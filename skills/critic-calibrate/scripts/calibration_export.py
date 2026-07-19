"""calibration_export.py -- upstream Critic-calibration DECLASSIFIER (slice-087, SC-144).

Makes the UPSTREAM half of the calibration loop executable: it turns a project's
``<vault>/critic-calibration-log.json`` into ONE maintainer-ready, safe-by-default
payload the user can send upstream so recurring, evidence-backed Critic checks reach
the next plugin version -- WITHOUT leaking project-private content.

DESIGN (ADR-103 default-deny declassifier + ADR-104 array split):
  * calibration_notes / gate_skips -> a MACHINE STRUCTURAL FLOOR. Emit only an
    enumerated allowlist of project-agnostic fields, and validate each enum VALUE
    against a CLOSED vocabulary (the log is LLM-written, so a field NAME is not a
    safety guarantee -- Saltzer-Schroeder economy of mechanism + Denning
    declassification). Free-text bodies (note/rationale) are WITHHELD and reduced to
    a recurrence COUNT. Unknown fields default-deny (dropped + counted).
  * active_checks -> NOT machine-read from the log at all. Their check text is both
    the payload's whole value AND an unclosable free-prose leak surface, so only the
    human-confirmed, genericized text from the Step-5 ``--approved-checks`` staging
    file enters the payload (in-loop human declassification). A non-authoritative
    structural BACKSTOP refuses any confirmed text still carrying a path / slice-NNN /
    CC-NNN / SHIP-NNN / GS-NNN / CN-NNN token (an un-genericized forward).
  * runs[] is OUT of scope entirely (nothing from it is emitted).

secret_scrub.redact() runs LAST on the final serialized markdown as a credential
defense-in-depth tripwire (AC2) -- NEVER the primary control (it is credential-only;
the A1 spike proved paths/code survive it). It is imported INSIDE main() under
try/except so an unavailable scrub yields the fail-closed manifest, not a traceback.

FAIL-CLOSED: missing / malformed / empty log, an all-withheld (hollow) projection, a
backstop hit, or a secret_scrub import/run failure -> exit non-zero with NOTHING on
stdout. The redaction manifest (types + counts only, never values) goes to stderr.

CLI:
  $PY calibration_export.py [--vault DIR | --log FILE] [--approved-checks FILE] [--out FILE]

Exit: 0 emitted (safe payload on stdout/--out) · 1 refused (fail-closed) · 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# --- single-skill import bootstrap (mirror commit-slice/scripts/*.py) ---
_HERE = Path(__file__).resolve().parent          # <plugin>/skills/critic-calibrate/scripts
_REPO = _HERE.parents[2]                          # -> <plugin>
for _p in (str(_HERE), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.lib import _stdout  # noqa: E402

# --- closed value-vocabularies (the log is LLM-written -> validate VALUES, not just names) ---
TARGET_GATE_VOCAB = frozenset({"critique", "critique-review", "code-review"})
SIGNAL_VOCAB = frozenset({"low-precision", "quiet"})
ACTION_VOCAB = frozenset({"skip", "tier-gate-high-only"})
DIMENSION_MIN, DIMENSION_MAX = 1, 9  # the 9 fixed critique dimensions

# --- EMIT_SCHEMA: the small, static, auditable allowlist for the STRUCTURAL FLOOR ---
# Safety comes from this table's structure (economy of mechanism), NOT from scrubbing prose.
# `anchor` is REQUIRED: an entry without a valid anchor value is dropped whole (it is
# meaningless without its gate). `enum`/`numeric` fields emit only a validated value.
# `count_from` -> an integer recurrence count (the slice-ids themselves are NEVER emitted).
# Every field NOT named here (incl. the free-text `withheld` bodies) is dropped by default-deny.
EMIT_SCHEMA: dict[str, dict] = {
    "calibration_notes": {
        "anchor": "target_gate",
        "enum": {"target_gate": "gate", "signal": "signal", "target_dimension": "dimension"},
        "numeric": {"window": "int", "precision": "unit_float"},
        "count_from": "evidence",
        "withheld": ("note", "id", "confirmed_at", "category"),
    },
    "gate_skips": {
        "anchor": "target_gate",
        "enum": {"target_gate": "gate", "action": "action"},
        "numeric": {"precision": "unit_float", "runs_observed": "int", "real_blockers_caught": "int"},
        "count_from": "evidence",
        "withheld": ("rationale", "id", "user_accepted_at"),
    },
}

# --- M2 structural backstop: un-genericized private tokens in human-confirmed check text ---
# NOT a scrub (it refuses, it does not strip) and NON-authoritative (the human gate is the real
# control) -- it is a fail-visible safety net. Tuned to NOT trip on slash-joined GENERIC prose
# ("validator/executor/resolver", "code-half + data-half"): a POSIX path needs a leading slash,
# a source file needs a real extension, and id tokens are anchored.
_BACKSTOP_PATTERNS: dict[str, "re.Pattern[str]"] = {
    "slice-id": re.compile(r"\bslice-\d+\b", re.IGNORECASE),
    "cc-id": re.compile(r"\bCC-\d+\b"),
    "ship-id": re.compile(r"\bSHIP-\d+\b"),
    "gs-id": re.compile(r"\bGS-\d+\b"),
    "cn-id": re.compile(r"\bCN-\d+\b"),
    "windows-path": re.compile(r"[A-Za-z]:[\\/]"),
    "posix-path": re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]+"),
    "source-file": re.compile(
        r"\b[\w.-]+\.(?:py|json|jsonl|md|js|ts|tsx|sh|ya?ml|toml|ini|cfg|txt|html|csv)\b",
        re.IGNORECASE,
    ),
    # CR3: a relative source PATH with no leading slash and no extension (e.g. `src/handlers/auth`)
    # is a real leak the generic pattern above misses. Anchor on a known code-dir ROOT so a
    # slash-joined GENERIC word-list ("validator/executor/resolver") -- no such root -- does NOT trip.
    "relative-source-path": re.compile(
        r"(?<![\w./-])(?:src|lib|tests?|scripts|skills|app|pkg|internal|cmd|api|dist|build|node_modules)"
        r"/[\w-]+(?:/[\w.-]+)*",
        re.IGNORECASE,
    ),
}


class Refuse(Exception):
    """Fail-closed refusal: carries a stderr-safe reason (never a leaked value)."""


def _load_secret_scrub():
    """Import the credential scrubber INSIDE main() (m3). A monkeypatch/import failure here is
    caught by main() and turned into the fail-closed manifest, never a module-top traceback."""
    from scripts.lib import secret_scrub
    return secret_scrub


# --- value validators (return (ok, normalized_value)) ---
def _v_gate(x): return (x in TARGET_GATE_VOCAB, x)
def _v_signal(x): return (x in SIGNAL_VOCAB, x)
def _v_action(x): return (x in ACTION_VOCAB, x)


def _v_dimension(x):
    m = re.match(r"\s*(\d{1,2})", str(x))
    if not m:
        return (False, None)
    n = int(m.group(1))
    return (DIMENSION_MIN <= n <= DIMENSION_MAX, n)


def _v_int(x):
    try:
        if isinstance(x, bool):
            return (False, None)
        return (True, int(x))
    except (TypeError, ValueError):
        return (False, None)


def _v_unit_float(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return (False, None)
    return (0.0 <= f <= 1.0, f)


_VALIDATORS = {
    "gate": _v_gate, "signal": _v_signal, "action": _v_action,
    "dimension": _v_dimension, "int": _v_int, "unit_float": _v_unit_float,
}


def project_floor(log: dict) -> tuple[dict, dict]:
    """Project calibration_notes + gate_skips through the EMIT_SCHEMA allowlist.

    Returns (digest, manifest). digest = {"calibration_notes": [...], "gate_skips": [...]}
    of emit-only dicts. manifest counts (types only) drive the stderr redaction record.
    active_checks and runs[] are NOT touched here (ADR-104 / m1)."""
    digest: dict[str, list] = {"calibration_notes": [], "gate_skips": []}
    manifest = {
        "calibration_notes": {"emitted": 0, "dropped_entries": 0,
                              "out_of_vocab_values": 0, "dropped_unknown_fields": 0},
        "gate_skips": {"emitted": 0, "dropped_entries": 0,
                       "out_of_vocab_values": 0, "dropped_unknown_fields": 0},
    }
    for array, spec in EMIT_SCHEMA.items():
        known = ({spec["anchor"]} | set(spec["enum"]) | set(spec["numeric"])
                 | {spec["count_from"]} | set(spec["withheld"]))
        for entry in log.get(array, []) or []:
            if not isinstance(entry, dict):
                manifest[array]["dropped_entries"] += 1
                continue
            # anchor: an entry without a valid gate value is meaningless -> drop whole.
            ok, gate = _VALIDATORS[spec["enum"][spec["anchor"]]](entry.get(spec["anchor"]))
            if not ok:
                manifest[array]["dropped_entries"] += 1
                continue
            emit: dict = {}
            for field, vkey in spec["enum"].items():
                if field not in entry:
                    continue
                vok, val = _VALIDATORS[vkey](entry[field])
                if vok:
                    emit[field] = val
                else:
                    manifest[array]["out_of_vocab_values"] += 1
            for field, vkey in spec["numeric"].items():
                if field not in entry:
                    continue
                vok, val = _VALIDATORS[vkey](entry[field])
                if vok:
                    emit[field] = val
                else:
                    manifest[array]["out_of_vocab_values"] += 1
            ev = entry.get(spec["count_from"])
            if isinstance(ev, list):
                emit["recurrence_count"] = len(set(map(str, ev)))
            # default-deny: anything not declared in the schema is an unknown field -> dropped.
            for field in entry:
                if field not in known:
                    manifest[array]["dropped_unknown_fields"] += 1
            digest[array].append(emit)
            manifest[array]["emitted"] += 1
    return digest, manifest


def load_approved_checks(path: "Path | None") -> list[dict]:
    """Read the Step-5 --approved-checks staging file: a JSON array of {text, recurrence_count}.
    Returns [] when no path is given. Raises Refuse on a malformed/unreadable staging file
    (it is written by our own gate; a broken one is a fail-closed condition, never a blind emit)."""
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refuse(f"--approved-checks unreadable/malformed: {type(exc).__name__}")
    if not isinstance(raw, list):
        raise Refuse("--approved-checks must be a JSON array of {text, recurrence_count}")
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise Refuse("--approved-checks entry missing a non-empty string 'text'")
        rc = item.get("recurrence_count")
        cnt = int(rc) if isinstance(rc, int) and not isinstance(rc, bool) else None
        out.append({"text": item["text"].strip(), "recurrence_count": cnt})
    return out


def backstop_hits(text: str) -> list[str]:
    """Return the sorted kinds of un-genericized private tokens found in confirmed check text
    (empty list = clean). A non-empty result REFUSES the export (M2)."""
    return sorted(k for k, pat in _BACKSTOP_PATTERNS.items() if pat.search(text or ""))


def serialize_markdown(checks: list[dict], digest: dict) -> str:
    """Render the ONE pinned maintainer-ready issue-template (m2). Free-text bodies never
    reach here (withheld upstream); confirmed check text is human-declassified."""
    lines: list[str] = []
    lines.append("# AI-SDLC Critic calibration -- upstream digest")
    lines.append("")
    lines.append("_Machine-declassified export (default-deny). Free-text bodies are withheld by "
                 "construction; the proposed checks below were human-reviewed and genericized at "
                 "the source vault. Safe-by-default -- NOT a claim that arbitrary prose was proven "
                 "generic; the credential pass is defense-in-depth only._")
    lines.append("")

    lines.append("## Proposed generic Critic checks")
    lines.append("")
    if checks:
        for c in checks:
            cnt = c.get("recurrence_count")
            prefix = (f"**Recurred across {cnt} distinct slices.** " if isinstance(cnt, int) else "")
            lines.append(f"- {prefix}{c['text']}")
    else:
        lines.append("_None submitted._")
    lines.append("")

    lines.append("## Calibration-notes digest (structural)")
    lines.append("")
    notes = digest.get("calibration_notes", [])
    if notes:
        for n in notes:
            lines.append("- " + _fmt_struct(n))
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Gate-skip digest (structural)")
    lines.append("")
    skips = digest.get("gate_skips", [])
    if skips:
        for s in skips:
            lines.append("- " + _fmt_struct(s))
    else:
        lines.append("_None._")
    lines.append("")
    return "\n".join(lines)


def _fmt_struct(entry: dict) -> str:
    """Render one structural emit dict as a compact, id-free line."""
    order = ["target_gate", "target_dimension", "signal", "action",
             "precision", "window", "runs_observed", "real_blockers_caught"]
    label = {"target_gate": "gate", "target_dimension": "dimension"}
    parts = []
    for k in order:
        if k in entry:
            parts.append(f"{label.get(k, k)}={entry[k]}")
    if "recurrence_count" in entry:
        parts.append(f"recurred_across={entry['recurrence_count']} slices")
    return " · ".join(parts)


def _write_manifest(err, digest_manifest: dict, checks: int,
                    creds: "list[str] | None" = None, reason: str = "") -> None:
    """Redaction manifest to stderr: TYPES + COUNTS only, never values or withheld content."""
    err.write("calibration_export: redaction manifest (types/counts only)\n")
    for array, m in digest_manifest.items():
        err.write(f"  {array}: " + " ".join(f"{k}={v}" for k, v in m.items()) + "\n")
    err.write(f"  active_checks: confirmed={checks} (human-declassified; log never machine-read)\n")
    err.write("  runs: out-of-scope (not emitted)\n")
    if creds:
        err.write(f"  credentials_redacted (types): {', '.join(creds)}\n")
    if reason:
        err.write(f"  REFUSE: {reason}\n")


def _resolve_log_path(args) -> "Path | None":
    if args.log:
        return args.log
    if args.vault:
        return args.vault / "critic-calibration-log.json"
    root = os.environ.get("AI_SDLC_VAULT_ROOT")
    if not root:
        try:
            from scripts.lib._vault_paths import VAULT_ROOT
            root = str(VAULT_ROOT) if VAULT_ROOT else None
        except Exception:
            root = None
    return Path(root) / "critic-calibration-log.json" if root else None


def main(argv: "list[str] | None" = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="calibration_export",
        description="Declassify <vault>/critic-calibration-log.json into a safe upstream payload.")
    ap.add_argument("--vault", type=Path, default=None, help="vault dir (reads its critic-calibration-log.json)")
    ap.add_argument("--log", type=Path, default=None, help="explicit path to critic-calibration-log.json")
    ap.add_argument("--approved-checks", dest="approved", type=Path, default=None,
                    help="Step-5 staging file: JSON array of {text, recurrence_count}")
    ap.add_argument("--out", type=Path, default=None, help="write the payload here (default: stdout)")
    args = ap.parse_args(argv)

    # empty per-array manifest so an early refuse still prints a coherent record
    empty_manifest = {a: {"emitted": 0, "dropped_entries": 0,
                          "out_of_vocab_values": 0, "dropped_unknown_fields": 0}
                      for a in EMIT_SCHEMA}
    try:
        log_path = _resolve_log_path(args)
        if log_path is None:
            raise Refuse("cannot resolve the vault / calibration log path")
        if not log_path.is_file():
            raise Refuse(f"calibration log not found: {log_path.name}")
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
        except ValueError:
            raise Refuse("calibration log is not valid JSON")
        if not isinstance(log, dict):
            raise Refuse("calibration log is not a JSON object")

        digest, manifest = project_floor(log)
        checks = load_approved_checks(args.approved)

        # M2 backstop: any un-genericized private token in confirmed text refuses the whole export.
        for c in checks:
            hits = backstop_hits(c["text"])
            if hits:
                raise Refuse(f"confirmed check carries un-genericized token(s): {', '.join(hits)}")

        # hollow guard: nothing structural AND no confirmed checks -> refuse (never a hollow payload).
        has_struct = any(digest[a] for a in EMIT_SCHEMA)
        if not has_struct and not checks:
            raise Refuse("nothing to emit -- empty/hollow projection and no confirmed checks")

        payload = serialize_markdown(checks, digest)

        # credential defense-in-depth LAST, on the final serialized string (m3: import here).
        try:
            scrub = _load_secret_scrub()
            redacted, cred_types = scrub.redact(payload)
            residual = scrub.scan(redacted)
        except Exception as exc:  # noqa: BLE001 -- any scrub failure is fail-closed by design
            raise Refuse(f"secret_scrub unavailable/failed ({type(exc).__name__}); refusing to emit")
        if residual:
            raise Refuse(f"credential survived redaction ({len(residual)}); refusing to emit")

        # emit ONLY after every gate passed.
        if args.out:
            args.out.write_text(redacted, encoding="utf-8", newline="")
        else:
            sys.stdout.write(redacted)
            if not redacted.endswith("\n"):
                sys.stdout.write("\n")

        _write_manifest(sys.stderr, manifest, len(checks), creds=cred_types)

        # single-shot staging: remove after a successful emit (best-effort; never blocks success).
        if args.approved is not None:
            try:
                args.approved.unlink()
            except OSError:
                sys.stderr.write("  note: could not remove --approved-checks staging file\n")
        return 0

    except Refuse as r:
        _write_manifest(sys.stderr, empty_manifest, 0, reason=str(r))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
