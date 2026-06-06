"""Drift-check enforcement audit (DCE-1) — v2 JSON.

Refuses ``/build-slice`` Step 6 when no ``/drift-check`` was run for the active
slice — closing the silent-skip gap.

Per DCE-1 (methodology-changelog.md v0.76.0; ADR-073). DCE-1 is an
**audit-enforced gate** (bare ``DCE-1`` form, no ``-D`` suffix).

Scope honesty (ADR-073): this is a *was-it-MARKED* gate, not a was-it-RUN gate.
It enforces that a slice-referencing entry exists in ``<vault>/drift-log.json``.
What it structurally closes is the silent-skip hole (a slice finishing with NO
drift-check trace at all). The semantic vault-vs-code reading remains Claude's
judgement via ``/drift-check``.

**v2 changes from v1.**
- ``drift-log.md`` (append-only markdown, scanned ``**Trigger**:`` lines) ->
  ``<vault>/drift-log.json`` (``entries[]`` array; each entry's ``trigger`` field
  names the slice, e.g. ``"slice-021"``). The line-anchor + ``**Trigger**:`` scan
  DISSOLVES: the marker is now "an entry whose ``trigger`` references the slice
  number", read structurally. The slice-anchored regex (``\bslice[- ]?0*<N>\b``)
  is retained but applied to the ``trigger`` field value only, so a number merely
  mentioned in a ``finding`` / ``resolution`` body can never false-ACCEPT.
- ``milestone.md`` frontmatter -> ``milestone.json`` ``drift-check-skip`` field.
- Mode reads ``<vault>/triage.json`` ``mode`` (vault derived from the slice
  folder), CLAUDE.md ``**Mode**:`` fallback. ``_vault_git`` git-walk is GONE.

Refuse condition (exit 1, ``drift-check-not-run``):
    mode in {STANDARD, HEAVY}
    AND no drift-log.json entry's ``trigger`` references the slice number
    AND no ``drift-check-skip`` key in milestone.json

Accept (exit 0): a slice-referencing entry present, OR canonical
``drift-check-skip`` value present, OR mode == MINIMAL.

Usage:
    python drift_check_audit.py <slice-folder>
    python drift_check_audit.py --json <slice-folder>
    python drift_check_audit.py --root <repo-root> <slice-folder>

Exit codes:
    0  clean (accept)
    1  violations (drift-check marker absent + unrationalised, or malformed skip)
    2  usage error (slice-folder missing, milestone.json missing, mode unresolvable)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402

# Canonical regex for the milestone.json `drift-check-skip` value. Byte-faithful
# clone of CRP-1's `_SKIP_VALUE_RE` (em-dash `—`, NOT a hyphen).
_SKIP_VALUE_RE = re.compile(r"^skip — rationale: .+")

# Modes for which a mandatory /drift-check is enforced. MINIMAL skips by default.
_ENFORCED_MODES = {"STANDARD", "HEAVY"}

_SLICE_FOLDER_RE = re.compile(r"^slice-(\d{3})-(.+)$")


@dataclass(frozen=True)
class DCEViolation:
    kind: str       # "drift-check-not-run" | "escape-hatch-malformed" |
                    # "usage-error" | "mode-unresolvable"
    severity: str   # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    slice_folder: str = ""
    repo_root: str = ""
    slice_number: str = ""
    resolved_mode: str = ""
    drift_marker_present: bool = False
    skip_key_present: bool = False
    skip_rationale: str | None = None
    accepted_reason: str = ""
    violations: list[DCEViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": "DCE-1",
            "slice_folder": self.slice_folder,
            "repo_root": self.repo_root,
            "slice_number": self.slice_number,
            "resolved_mode": self.resolved_mode,
            "drift_marker_present": self.drift_marker_present,
            "skip_key_present": self.skip_key_present,
            "skip_rationale": self.skip_rationale,
            "accepted_reason": self.accepted_reason,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "violation_count": len(self.violations),
                "clean": not self.violations,
            },
        }


def _load_json(path: Path) -> object | None:
    """Load JSON from `path`, or None on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return None


def _field(data: dict, *keys: str) -> object:
    for k in keys:
        if k in data:
            return data[k]
    return None


def _resolve_vault_root(slice_folder: Path) -> Path | None:
    """Derive ``<vault>`` from ``<vault>/slices/slice-NNN-<name>``."""
    parent = slice_folder.parent
    if parent.name == "archive":
        parent = parent.parent
    if parent.name == "slices":
        return parent.parent
    return None


def _resolve_mode(slice_folder: Path, repo_root: Path) -> str | None:
    """Primary: <vault>/triage.json `mode`. Fallback: CLAUDE.md `**Mode**:`."""
    vault = _resolve_vault_root(slice_folder)
    if vault is not None:
        triage = _load_json(vault / "triage.json")
        if isinstance(triage, dict):
            val = triage.get("mode")
            if isinstance(val, str) and val.strip():
                return val.strip().upper()

    claude_md = repo_root / "CLAUDE.md"
    if claude_md.exists():
        m = re.search(
            r"^\*\*Mode\*\*\s*:\s*([A-Za-z]+)",
            claude_md.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if m:
            return m.group(1).strip().upper()
    return None


def _drift_marker_present(slice_folder: Path, slice_number: str) -> bool:
    """True iff a drift-log.json entry's ``trigger`` references the slice number.

    The slice-anchored regex (``\\bslice[- ]?0*<N>\\b``, left `\\b` per slice-081
    /code-review m1) is applied to the ``trigger`` field VALUE only. A number
    appearing in a ``finding`` / ``resolution`` body never matches — the
    structural ``trigger``-field read is v2's replacement for v1's
    ``**Trigger**:`` line-anchor. A missing drift-log.json is "no marker" (a repo
    that never ran /drift-check is refused, not errored).
    """
    vault = _resolve_vault_root(slice_folder)
    if vault is None:
        return False
    data = _load_json(vault / "drift-log.json")
    if not isinstance(data, dict):
        return False
    entries = data.get("entries")
    if not isinstance(entries, list):
        return False
    n = int(slice_number)  # strip zero-padding: "081" -> 81
    slice_re = re.compile(rf"\bslice[- ]?0*{n}\b")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        trigger = entry.get("trigger")
        if isinstance(trigger, str) and slice_re.search(trigger):
            return True
    return False


def audit(slice_folder: Path, repo_root: Path | None = None) -> AuditResult:
    """Run the DCE-1 audit against a slice folder."""
    slice_folder = Path(slice_folder).resolve()
    if not slice_folder.exists():
        return AuditResult(
            slice_folder=str(slice_folder),
            violations=[DCEViolation(
                kind="usage-error", severity="Important",
                message=f"slice folder not found: {slice_folder}",
            )],
        )

    m = _SLICE_FOLDER_RE.match(slice_folder.name)
    if not m:
        return AuditResult(
            slice_folder=str(slice_folder),
            violations=[DCEViolation(
                kind="usage-error", severity="Important",
                message=(
                    f"slice folder name {slice_folder.name!r} does not match "
                    f"the canonical `slice-NNN-<name>` shape."
                ),
            )],
        )
    slice_number = m.group(1)

    repo_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd()
    result = AuditResult(
        slice_folder=str(slice_folder),
        repo_root=str(repo_root),
        slice_number=slice_number,
    )

    milestone_path = slice_folder / "milestone.json"
    if not milestone_path.exists():
        result.violations.append(DCEViolation(
            kind="usage-error", severity="Important",
            message=f"milestone.json not found in slice folder: {slice_folder}",
        ))
        return result

    milestone = _load_json(milestone_path)
    if not isinstance(milestone, dict):
        result.violations.append(DCEViolation(
            kind="usage-error", severity="Important",
            message="milestone.json is missing or not a valid JSON object (cannot read drift-check-skip).",
        ))
        return result

    # Resolve mode.
    mode = _resolve_mode(slice_folder, repo_root)
    if mode is None:
        result.violations.append(DCEViolation(
            kind="mode-unresolvable", severity="Important",
            message=(
                "cannot resolve pipeline mode from <vault>/triage.json `mode` "
                "or CLAUDE.md `**Mode**:` line"
            ),
        ))
        return result
    result.resolved_mode = mode

    # Escape-hatch: drift-check-skip key (structural, not body-scanned).
    skip_raw = _field(milestone, "drift-check-skip", "drift_check_skip")
    skip_val = str(skip_raw).strip() if skip_raw is not None else None
    result.skip_key_present = skip_val is not None
    if skip_val is not None:
        if _SKIP_VALUE_RE.match(skip_val):
            result.skip_rationale = skip_val
        else:
            result.violations.append(DCEViolation(
                kind="escape-hatch-malformed", severity="Important",
                message=(
                    f"milestone.json `drift-check-skip` present but value "
                    f"{skip_val!r} does not match canonical shape "
                    f"`skip — rationale: <text>` (per ADR-073)."
                ),
            ))
            return result

    # drift-log marker presence (structural trigger-field read).
    result.drift_marker_present = _drift_marker_present(slice_folder, slice_number)

    # Acceptance paths.
    if result.drift_marker_present:
        result.accepted_reason = (
            f"drift-log.json has an entry whose `trigger` references slice-{slice_number}"
        )
        return result
    if result.skip_rationale is not None:
        result.accepted_reason = f"documented skip — {result.skip_rationale}"
        return result
    if mode not in _ENFORCED_MODES:
        result.accepted_reason = f"mode {mode} does not enforce mandatory /drift-check"
        return result

    # Refuse: mandatory /drift-check absent + unrationalised.
    result.violations.append(DCEViolation(
        kind="drift-check-not-run", severity="Important",
        message=(
            f"no /drift-check marker for slice-{slice_number}. Conditions held: "
            f"mode={mode} (in {{STANDARD, HEAVY}}); no drift-log.json entry's "
            f"`trigger` references slice-{slice_number}; no `drift-check-skip` "
            f"milestone.json key. Run `/drift-check` (full mode) for this slice — "
            f"it appends an entry with `\"trigger\": \"slice-{slice_number}\"` to "
            f"<vault>/drift-log.json — OR document a deliberate skip by adding "
            f"`\"drift-check-skip\": \"skip — rationale: <text>\"` to "
            f"milestone.json (per ADR-073)."
        ),
    ))
    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="drift_check_audit",
        description="DCE-1 audit: refuse /build-slice when no /drift-check was run for the slice (v2 JSON).",
    )
    parser.add_argument("slice_folder", type=Path, help="Path to active slice folder.")
    parser.add_argument("--root", type=Path, default=None,
                        help="Repo root for the CLAUDE.md mode fallback (default: cwd).")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        result = audit(slice_folder=args.slice_folder, repo_root=args.root)
    except Exception as e:  # noqa: BLE001 — top-level CLI guard
        print(f"drift_check_audit: error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        if result.violations:
            for v in result.violations:
                print(f"[{v.severity}] {v.kind}: {v.message}")
        else:
            print(f"DCE-1 audit: clean. Accepted: {result.accepted_reason}.")

    usage_kinds = {"usage-error", "mode-unresolvable"}
    if any(v.kind in usage_kinds for v in result.violations):
        return 2
    if result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
