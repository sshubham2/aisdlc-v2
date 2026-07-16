"""residue_disposition.py — slice-072 / SC-137 / ADR-077.

The shared, PURE, non-interactive gate helper for residue leaving its owning slice. It
builds a residue-born candidate payload stamping the two provenance fields
(``ejected_from`` = the owning slice, ``ejection_reason`` = the recorded reason) and it is
FAIL-CLOSED + FAIL-VISIBLE by contract (Shingo control-type poka-yoke: refuse, don't warn):

  - fail-CLOSED: an empty / whitespace / missing ``ejection_reason`` -> ``ResidueError``
                 (no payload is ever returned reason-less). A capture is NEVER dropped:
                 the caller surfaces the error and re-prompts for a reason, it does not
                 silently discard the residue (reflect record-on-capture guarantee).
  - fail-VISIBLE: a missing / malformed residue source (item not a dict), an unclassifiable
                 item (no non-empty ``title``), a caller-supplied ``id`` (the allocator mints
                 SC-NNN in-lock, ADR-013), a missing / malformed owning slice, or a malformed
                 ``source`` field -> ``ResidueError``. Never swallowed.

The DECISION (whether to eject, and the reason text) is main-thread interactive in
skills/reflect (Step 2 record-on-capture) + skills/build-slice (Step 7 mint-split); this
helper only BUILDS + VALIDATES the payload. It does no I/O beyond emitting the payload; the
skill routes the payload to ``vault_edit append --file candidates.json`` (id omitted).

Mirrors the pure-helper idiom of tournament_convergence.py (slice-066) / verification_core.py
(slice-047): a pure ``build_eject_payload`` (no I/O, raises on any violation) + a thin ``--json``
CLI + a ``--self-test`` battery that drives the AC5 executable-layer check. Reason/error strings
are ASCII-only; the CLI reconfigures stdout to UTF-8 (Windows cp1252 rule).
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys

# --- shared-lib import bootstrap (a scripts/lib module: parents[2] == repo root) ---
_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402  (UTF8-STDOUT-1: the canonical house import)

# Lifecycle fields a fresh, unclaimed candidate carries — defaulted only when the caller
# omits them, so a residue capture is a valid backlog row the readers won't choke on.
_LIFECYCLE_DEFAULTS = {
    "status": "candidate",
    "progress": "not-started",
    "slice": None,
    "claimed_by": None,
    "started_at": None,
    "assumptions": [],
}


class ResidueError(ValueError):
    """A residue disposition cannot be recorded (fail-closed reason / fail-visible source).

    Raised for EVERY refusal so the caller must surface it — a residue item is never
    silently dropped and a reason-less payload is never emitted.
    """


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_source(source: object) -> None:
    """A ``source`` field, when present, must be a list of ``{type: ...}`` mappings."""
    if source is None:
        return
    if not isinstance(source, list):
        raise ResidueError(
            f"malformed residue source: `source` must be a list, got {type(source).__name__}")
    for i, entry in enumerate(source):
        if not isinstance(entry, dict) or not _nonempty_str(entry.get("type")):
            raise ResidueError(
                f"malformed residue source: source[{i}] must be a mapping with a non-empty `type`")


def build_eject_payload(item: object, ejected_from: object, ejection_reason: object) -> dict:
    """Build a residue-born candidate payload carrying ``ejected_from`` + ``ejection_reason``.

    Pure (no I/O); raises ``ResidueError`` on any violation. Returns a NEW dict (the input
    ``item`` is never mutated). Both provenance fields are ALWAYS present + non-empty on the
    returned payload — by construction the helper cannot build a reason-less residue candidate.
    """
    # fail-VISIBLE: the residue source must be a usable item.
    if not isinstance(item, dict):
        raise ResidueError(
            f"malformed residue source: item must be a mapping, got {type(item).__name__}")
    # fail-VISIBLE: the owning slice must be a non-empty string.
    if not _nonempty_str(ejected_from):
        raise ResidueError(
            "missing/malformed owning slice: `ejected_from` must be a non-empty string "
            f"(got {ejected_from!r})")
    # fail-CLOSED: the reason is required and must be non-empty (never a reason-less capture).
    if not _nonempty_str(ejection_reason):
        raise ResidueError(
            "fail-closed: `ejection_reason` is required and must be non-empty — a residue "
            "capture/eject is never recorded reason-less (surface + re-prompt, never drop)")
    # fail-VISIBLE: the allocator mints SC-NNN in-lock; a caller-supplied id is rejected (ADR-013).
    if "id" in item:
        raise ResidueError(
            "a residue candidate must OMIT `id` — the vault_edit allocator mints SC-NNN in-lock")
    # fail-VISIBLE: an unclassifiable item (no title) cannot become a backlog candidate.
    if not _nonempty_str(item.get("title")):
        raise ResidueError(
            "unclassifiable residue item: a non-empty `title` is required to mint a candidate")
    _validate_source(item.get("source"))

    payload = dict(item)
    for key, default in _LIFECYCLE_DEFAULTS.items():
        if key not in payload:
            # CR2: copy mutable defaults so payloads never ALIAS one shared module-level list
            # (e.g. `assumptions: []`) — a future consumer mutating one must not corrupt the others.
            payload[key] = copy.deepcopy(default) if isinstance(default, (list, dict)) else default
    payload["ejected_from"] = ejected_from.strip()
    payload["ejection_reason"] = ejection_reason.strip()
    return payload


# ── self-test battery (AC5 executable-layer smoke, callable without disk) ──────────────
def _self_test() -> list[str]:
    """Return a list of failure strings ([] = all green)."""
    fails: list[str] = []
    good_item = {"title": "t", "description": "d",
                 "source": [{"type": "reflection-discovered", "ref": "slice-072"}]}

    def _expect_raises(label: str, *args) -> None:
        try:
            build_eject_payload(*args)
        except ResidueError:
            return
        fails.append(f"expected ResidueError: {label}")

    # build path
    try:
        p = build_eject_payload(good_item, "slice-072", "  a genuine reason  ")
        if p.get("ejected_from") != "slice-072":
            fails.append("ejected_from not stamped")
        if p.get("ejection_reason") != "a genuine reason":
            fails.append("ejection_reason not stamped/stripped")
        if "id" in p:
            fails.append("payload leaked an id")
    except ResidueError as exc:
        fails.append(f"valid build unexpectedly raised: {exc}")

    # fail-closed reason
    for bad in ("", "   ", None):
        _expect_raises(f"reason={bad!r}", good_item, "slice-072", bad)
    # fail-visible source / classification / owner / id
    _expect_raises("item=None", None, "slice-072", "r")
    _expect_raises("no-title", {"description": "x"}, "slice-072", "r")
    _expect_raises("supplied-id", {"title": "t", "id": "SC-9"}, "slice-072", "r")
    _expect_raises("bad-owner", good_item, "", "r")
    _expect_raises("bad-source", {"title": "t", "source": "nope"}, "slice-072", "r")
    return fails


def _load_item(item_file: str | None) -> object:
    raw = pathlib.Path(item_file).read_text(encoding="utf-8") if item_file else sys.stdin.read()
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    _stdout.reconfigure_stdin_utf8()  # BC-PROJ-5: the item body (free-text title/reason) may be non-ASCII
    parser = argparse.ArgumentParser(
        prog="residue_disposition",
        description="Build a fail-closed residue-born candidate payload (ejected_from + ejection_reason).")
    parser.add_argument("--item-file", default=None,
                        help="path to a JSON item (partial candidate); default: read from stdin")
    parser.add_argument("--ejected-from", default=None, help="the owning slice being left (slice-NNN)")
    parser.add_argument("--ejection-reason", default=None, help="the recorded reason (required, non-empty)")
    parser.add_argument("--json", action="store_true", help="emit the payload as JSON on stdout")
    parser.add_argument("--self-test", action="store_true", help="run the built-in validation battery")
    args = parser.parse_args(argv)

    if args.self_test:
        fails = _self_test()
        if fails:
            for f in fails:
                sys.stderr.write(f"residue_disposition self-test FAIL: {f}\n")
            return 1
        print("residue_disposition: self-test OK")
        return 0

    try:
        item = _load_item(args.item_file)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"residue_disposition: cannot read item ({exc})\n")
        return 2
    try:
        payload = build_eject_payload(item, args.ejected_from, args.ejection_reason)
    except ResidueError as exc:
        sys.stderr.write(f"residue_disposition: {exc}\n")
        return 1

    if args.json:
        # BC-PROJ-3: the payload is read back (vault_edit append -> candidates.json), so a
        # non-ASCII ejection_reason must serialize as the literal char, never a \uXXXX escape.
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"residue_disposition: built candidate payload for ejected_from="
              f"{payload['ejected_from']!r} ({len(payload)} fields)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
