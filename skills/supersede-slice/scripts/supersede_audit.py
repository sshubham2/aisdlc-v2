"""Slice supersession audit (SUP-1) — v2 JSON-native.

Walks the project's active and archived slices and validates the
bidirectional consistency of slice supersession links.

**v2 port (md -> json).** v1 regex-parsed two markdown fields:

    mission-brief.md   ->   **Supersedes**: slice-NNN-<name>
    reflection.md      ->   ## Supersession / **Superseded by**: slice-NNN-<name>

v2 stores both ends as JSON fields in the per-slice artifacts (the shared
EXTERNAL vault under ``VAULT_ROOT``, NOT in the code repo):

    <vault>/slices/<active>/mission-brief.json       "supersedes": "<archived-id>" | null
    <vault>/slices/archive/<archived>/reflection.json "supersession": {
                                                          "superseded_by": "<active-id>",
                                                          "date": "<YYYY-MM-DD>",
                                                          "reason": "<one paragraph>"
                                                        } | null

So the audit reads JSON fields instead of regex-matching prose. The
``SupersessionLink`` / ``SUPViolation`` / ``AuditResult`` API + the
bidirectional consistency rules are unchanged from v1. The ``line`` field is
retained for API compatibility but is always 0 (JSON has no meaningful claim
line number; ``path`` alone localizes the violation).

Per SUP-1 (methodology-changelog.md v0.19.0). The rule's purpose: when a
shipped slice's design turns out wrong (reality contradicts the original
assumptions), the new fix slice doesn't just exist as "another slice"; it's
explicitly linked as the supersession of the old one. The audit catches:

  - Active slices claiming supersession of nonexistent archived slices
  - Archived slices marked superseded-by a slice that doesn't exist
    in active or archive
  - One-way links (active claims, archive doesn't acknowledge — or
    vice versa)

The audit is project-wide; not gated by Heavy mode.

Layout assumed (VAULT_ROOT-routed; the shared external store):
  <vault>/slices/slice-NNN-<name>/                  active slices
  <vault>/slices/archive/slice-NNN-<name>/          archived slices
  <vault>/slices/_index.json                        ignored

Usage:
    python supersede_audit.py
    python supersede_audit.py --root <project-root>
    python supersede_audit.py --json

Exit codes:
    0  clean (or no supersession links found)
    1  violations
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT


@dataclass(frozen=True)
class SupersessionLink:
    direction: str    # "supersedes" (active -> archived) | "superseded-by" (archived -> any)
    source: str       # slice id of the source
    target: str       # slice id of the target
    source_path: str  # file path that declared the link
    line: int         # retained for API compat; always 0 (JSON has no claim line)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SUPViolation:
    path: str
    line: int
    source: str       # slice id of the source slice
    target: str       # slice id the source claims
    kind: str         # "missing-target" | "one-way-link" | "revert-malformed"
    severity: str     # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    links: list[SupersessionLink] = field(default_factory=list)
    violations: list[SUPViolation] = field(default_factory=list)
    active_slices: list[str] = field(default_factory=list)
    archived_slices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "links": [l.to_dict() for l in self.links],
            "violations": [v.to_dict() for v in self.violations],
            "active_slices": list(self.active_slices),
            "archived_slices": list(self.archived_slices),
            "summary": {
                "link_count": len(self.links),
                "violation_count": len(self.violations),
            },
        }


def _list_active_slices(slices_dir: Path) -> list[Path]:
    if not slices_dir.exists():
        return []
    return [
        p for p in sorted(slices_dir.iterdir())
        if p.is_dir() and p.name.startswith("slice-") and p.name != "archive"
    ]


def _list_archived_slices(archive_dir: Path) -> list[Path]:
    if not archive_dir.exists():
        return []
    return [
        p for p in sorted(archive_dir.iterdir())
        if p.is_dir() and p.name.startswith("slice-")
    ]


def _load_json(path: Path) -> dict | None:
    """Best-effort JSON object load. Returns None on missing/unreadable/non-object/
    malformed — a malformed artifact simply yields no link (fail-open per-file; the
    audit never crashes on one bad file)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _find_supersedes(brief_path: Path) -> str | None:
    """Read the active mission-brief.json ``supersedes`` field (archived slice id),
    or None when absent/null/empty. v2 replacement for the v1 ``**Supersedes**:``
    markdown regex."""
    data = _load_json(brief_path)
    if data is None:
        return None
    target = data.get("supersedes")
    if isinstance(target, str) and target.strip():
        return target.strip()
    return None


def _superseded_by_from(data: dict | None) -> str | None:
    """``supersession.superseded_by`` from an already-loaded reflection dict (or None)."""
    if not isinstance(data, dict):
        return None
    sup = data.get("supersession")
    if not isinstance(sup, dict):
        return None
    source = sup.get("superseded_by")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return None


def _find_superseded_by(reflection_path: Path) -> str | None:
    """Read the archived reflection.json ``supersession.superseded_by`` field
    (active slice id), or None when the ``supersession`` field is null/absent or
    lacks a ``superseded_by``. v2 replacement for the v1 ``## Supersession`` /
    ``**Superseded by**:`` markdown regex."""
    return _superseded_by_from(_load_json(reflection_path))


# slice-003: the supersession block's OPTIONAL revert object {commit?, pr?, note?}.
# Strict-reject of unknown keys is a DELIBERATE departure from the vault's
# tolerate-extras norm (artifact_lint never rejects unknowns): revert is a small,
# closed, HUMAN-TYPED write object where a typo'd key (`comit`) means silently
# losing the revert ref — exactly the data loss this audit exists to prevent.
# Extending revert with a new member is therefore a deliberate audit change, not
# an accident. (critique M1 — documented conscious decision.)
_REVERT_KEYS = frozenset({"commit", "pr", "note"})


def _validate_revert(reflection_path: Path, slice_id: str, sup, result: AuditResult) -> None:
    """STANDALONE revert-shape pass (slice-003, DR-1 M-add-1): runs for EVERY archived
    reflection whose supersession block is a dict — INDEPENDENT of superseded_by/link
    completeness, so a malformed revert on a half-written record still fires. Owns the
    isinstance guard (critique M2: a bare-string/list revert refuses, never crashes).
    Absent revert = valid (every legacy record passes unchanged)."""
    if not isinstance(sup, dict) or "revert" not in sup:
        return
    revert = sup["revert"]
    if revert is None:
        return  # null-as-absent, matching the file's own `supersession: null` convention (code-review M2)
    tgt = sup.get("superseded_by")
    target = tgt.strip() if isinstance(tgt, str) else ""
    remedy = f"Fix the `revert` object in {slice_id}'s reflection.json (or remove the key entirely)."

    def _viol(message: str) -> None:
        result.violations.append(SUPViolation(
            path=str(reflection_path), line=0,
            source=slice_id, target=target,
            kind="revert-malformed", severity="Important",
            message=f"archived slice {slice_id}: {message} {remedy}",
        ))

    if not isinstance(revert, dict):
        _viol(f"`revert` is not an object (got {type(revert).__name__}); expected "
              f"{{\"commit\"?, \"pr\"?, \"note\"?}} with at least one non-empty member.")
        return
    if not revert:
        _viol("`revert` has no members; when present it needs at least one of "
              "\"commit\" / \"pr\" / \"note\" (non-empty strings) — or omit the field entirely.")
        return
    for key in sorted(set(revert) - _REVERT_KEYS):
        _viol(f"`revert.{key}` is an unknown key (allowed: commit, pr, note). Unknown keys "
              f"are refused deliberately — a typo here silently loses the revert ref.")
    for key in sorted(set(revert) & _REVERT_KEYS):
        val = revert[key]
        if not isinstance(val, str) or not val.strip():
            _viol(f"`revert.{key}` is empty or not a string; every present member must be a "
                  f"non-empty string (commit sha / PR url / prose note).")
    # NOTE: a non-empty dict with zero known keys necessarily hit the unknown-key loop above,
    # so a separate "no known member" branch is unreachable (code-review m1: removed as dead).


def run_audit(project_root: Path) -> AuditResult:
    """Run SUP-1 audit across active + archived slices."""
    result = AuditResult()

    slices_dir = project_root / VAULT_ROOT / "slices"  # VAULT_ROOT-routed (shared external vault)
    archive_dir = slices_dir / "archive"

    active_paths = _list_active_slices(slices_dir)
    archived_paths = _list_archived_slices(archive_dir)
    result.active_slices = [p.name for p in active_paths]
    result.archived_slices = [p.name for p in archived_paths]

    all_known_ids: set[str] = set(result.active_slices) | set(result.archived_slices)

    # Walk active slices for `supersedes` claims (mission-brief.json).
    forward_claims: dict[str, tuple[str, str]] = {}
    # source_id -> (target_id, source_path)
    for slice_dir in active_paths:
        brief = slice_dir / "mission-brief.json"
        target = _find_supersedes(brief)
        if target is None:
            continue
        result.links.append(SupersessionLink(
            direction="supersedes",
            source=slice_dir.name, target=target,
            source_path=str(brief), line=0,
        ))
        forward_claims[slice_dir.name] = (target, str(brief))

        if target not in all_known_ids:
            result.violations.append(SUPViolation(
                path=str(brief), line=0,
                source=slice_dir.name, target=target,
                kind="missing-target", severity="Important",
                message=(
                    f"slice {slice_dir.name} declares "
                    f"`\"supersedes\": \"{target}\"` but no slice with that id "
                    f"exists in active or archive. Either fix the target "
                    f"id or remove the claim."
                ),
            ))

    # Walk archived slices for `supersession.superseded_by` acknowledgments (reflection.json).
    backward_acks: dict[str, tuple[str, str]] = {}
    # archived_id -> (source_id, ack_path)
    for slice_dir in archived_paths:
        reflection = slice_dir / "reflection.json"
        # slice-003: revert validation runs FIRST, before the link gate's `continue` —
        # orthogonal to superseded_by completeness (DR-1 M-add-1). Single parse: the
        # loaded dict feeds both the revert pass and the link extraction (code-review m3).
        data = _load_json(reflection)
        if data is not None:
            _validate_revert(reflection, slice_dir.name, data.get("supersession"), result)
        source = _superseded_by_from(data)
        if source is None:
            continue
        result.links.append(SupersessionLink(
            direction="superseded-by",
            source=slice_dir.name, target=source,
            source_path=str(reflection), line=0,
        ))
        backward_acks[slice_dir.name] = (source, str(reflection))

        if source not in all_known_ids:
            result.violations.append(SUPViolation(
                path=str(reflection), line=0,
                source=slice_dir.name, target=source,
                kind="missing-target", severity="Important",
                message=(
                    f"archived slice {slice_dir.name} declares "
                    f"`\"superseded_by\": \"{source}\"` but no slice with that "
                    f"id exists in active or archive. Either fix the id or "
                    f"remove the claim."
                ),
            ))

    # Validate bidirectional consistency:
    # if active A claims supersedes B, archived B should have superseded-by A
    for source_id, (target_id, source_path) in forward_claims.items():
        if target_id not in result.archived_slices:
            continue  # already flagged above as missing-target
        ack = backward_acks.get(target_id)
        if ack is None or ack[0] != source_id:
            result.violations.append(SUPViolation(
                path=source_path, line=0,
                source=source_id, target=target_id,
                kind="one-way-link", severity="Important",
                message=(
                    f"slice {source_id} claims `\"supersedes\": \"{target_id}\"` "
                    f"but archived slice {target_id}'s reflection.json does "
                    f"NOT acknowledge it via `\"supersession\": "
                    f"{{\"superseded_by\": \"{source_id}\", ...}}`. "
                    f"Per SUP-1, both ends of the link must agree. Run "
                    f"`/supersede-slice {target_id}` to set the "
                    f"`supersession` field in its reflection.json."
                ),
            ))

    # Reverse direction: archived B says superseded-by A, but A doesn't claim it
    for target_id, (source_id, ack_path) in backward_acks.items():
        # Source might be active or archived
        if source_id in result.active_slices:
            forward = forward_claims.get(source_id)
            if forward is None or forward[0] != target_id:
                result.violations.append(SUPViolation(
                    path=ack_path, line=0,
                    source=target_id, target=source_id,
                    kind="one-way-link", severity="Important",
                    message=(
                        f"archived slice {target_id} declares "
                        f"`\"superseded_by\": \"{source_id}\"` but slice "
                        f"{source_id}'s mission-brief.json does NOT have "
                        f"`\"supersedes\": \"{target_id}\"`. Set the field to "
                        f"close the bidirectional link."
                    ),
                ))

    return result


def _format_human(result: AuditResult) -> str:
    if not result.violations:
        if not result.links:
            return (
                f"SUP-1 supersession audit: clean. No supersession links "
                f"found ({len(result.active_slices)} active + "
                f"{len(result.archived_slices)} archived slices walked).\n"
            )
        return (
            f"SUP-1 supersession audit: clean. {len(result.links)} link(s) "
            f"validated across "
            f"{len(result.active_slices)} active + "
            f"{len(result.archived_slices)} archived slices.\n"
        )

    out: list[str] = [f"{len(result.violations)} supersession violation(s):\n\n"]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind}) "
            f"{v.source} -> {v.target}\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="supersede_audit",
        description="SUP-1 slice supersession audit (v2 JSON-native)",
    )
    parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root (default: cwd)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    result = run_audit(project_root=args.root)

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
