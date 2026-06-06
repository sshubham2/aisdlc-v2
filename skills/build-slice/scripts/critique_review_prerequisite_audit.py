"""Critique-review prerequisite audit (CRP-1) — v2 JSON.

Refuses ``/build-slice`` when a mandatory ``/critique-review`` (DR-1) was skipped
without a documented rationale. The first structural skip-detector for DR-1.

Per CRP-1 (methodology-changelog.md v0.40.0). CRP-1 is an **audit-enforced gate**
(its programmatic gate is this module), so per ADR-019's test-pinned naming note
it carries the bare ``CRP-1`` form (NO ``-D`` suffix).

**v2 changes from v1.**
- The slice artifacts are JSON: ``milestone.md`` -> ``milestone.json``,
  ``critique-review.md`` -> ``critique-review.json``. ``critic-required`` and the
  ``critique-review-skip`` escape-hatch are now JSON FIELDS of ``milestone.json``
  (not YAML frontmatter) — per ``skills/build-slice/SKILL.md`` Step 8, which keeps
  these as preserved milestone keys.
- Mode resolution reads ``<vault>/triage.json`` ``mode`` (was ``triage.md``
  frontmatter), falling back to the repo ``CLAUDE.md`` ``**Mode**:`` line.
- ``_vault_git.resolve_repo_root_for_slice`` is GONE in v2 (the slice folder lives
  in the EXTERNAL shared vault, which has no ``.git`` ancestor). The vault root is
  derived from the slice folder itself (``<vault>/slices/slice-NNN-<name>`` ->
  ``<vault>``), so ``triage.json`` is found without a git walk. ``CLAUDE.md`` is
  resolved from ``--root`` (default cwd) for the fallback.

Refuse condition (exit 1, ``mandatory-critique-review-absent``):
    mode in {STANDARD, HEAVY}
    AND milestone.json ``critic_required`` (or ``critic-required``) is truthy
    AND ``critique-review.json`` absent in the slice folder
    AND no ``critique-review-skip`` key in milestone.json

Accept (exit 0): ``critique-review.json`` present, OR a canonical
``critique-review-skip`` value present, OR mode == MINIMAL, OR not critic-required.

Malformed-skip (exit 1, Important, ``escape-hatch-malformed``):
``critique-review-skip`` present but value does NOT match ``^skip — rationale: .+``.

Usage:
    python critique_review_prerequisite_audit.py <slice-folder>
    python critique_review_prerequisite_audit.py --json <slice-folder>
    python critique_review_prerequisite_audit.py --root <repo-root> <slice-folder>

Exit codes:
    0  clean (accept)
    1  violations (mandatory review absent + unrationalised, or malformed skip)
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

# Canonical regex for the milestone.json `critique-review-skip` value. Same
# `rationale:` spirit as BRANCH-1's `BRANCH=skip — rationale:` (ADR-024); the
# em-dash `—` is required (NOT a hyphen), matching v1 byte-for-byte.
_SKIP_VALUE_RE = re.compile(r"^skip — rationale: .+")

# Modes for which a mandatory /critique-review is enforced.
_ENFORCED_MODES = {"STANDARD", "HEAVY"}


@dataclass(frozen=True)
class CRPViolation:
    kind: str       # "mandatory-critique-review-absent" | "escape-hatch-malformed" |
                    # "usage-error" | "mode-unresolvable"
    severity: str   # "Important" (all CRP-1 violations refuse)
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    slice_folder: str = ""
    repo_root: str = ""
    resolved_mode: str = ""
    critic_required: bool | None = None
    critique_review_present: bool = False
    skip_key_present: bool = False
    skip_rationale: str | None = None
    accepted_reason: str = ""
    violations: list[CRPViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": "CRP-1",
            "slice_folder": self.slice_folder,
            "repo_root": self.repo_root,
            "resolved_mode": self.resolved_mode,
            "critic_required": self.critic_required,
            "critique_review_present": self.critique_review_present,
            "skip_key_present": self.skip_key_present,
            "skip_rationale": self.skip_rationale,
            "accepted_reason": self.accepted_reason,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "violation_count": len(self.violations),
                "clean": not self.violations,
            },
        }


def _load_json(path: Path) -> dict | None:
    """Load a JSON object from `path`, or None on any failure / non-object."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _field(data: dict, *keys: str) -> object:
    """First present value among `keys` (tolerates underscore / hyphen variants)."""
    for k in keys:
        if k in data:
            return data[k]
    return None


def _resolve_vault_root(slice_folder: Path) -> Path | None:
    """Derive ``<vault>`` from a slice folder ``<vault>/slices/slice-NNN-<name>``.

    Returns the vault root (parent of ``slices/``) or None when the folder is not
    laid out under a ``slices/`` (active) or ``slices/archive/`` directory.
    """
    parent = slice_folder.parent
    if parent.name == "archive":
        parent = parent.parent
    if parent.name == "slices":
        return parent.parent
    return None


def _resolve_mode(slice_folder: Path, repo_root: Path) -> str | None:
    """Resolve pipeline mode.

    Primary: ``<vault>/triage.json`` ``mode`` (vault derived from the slice
    folder). Fallback: ``<repo_root>/CLAUDE.md`` ``**Mode**:`` line. Returns an
    uppercased mode string, or None if unresolvable.
    """
    vault = _resolve_vault_root(slice_folder)
    if vault is not None:
        triage = _load_json(vault / "triage.json")
        if triage:
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


def audit(slice_folder: Path, repo_root: Path | None = None) -> AuditResult:
    """Run the CRP-1 audit against a slice folder."""
    slice_folder = Path(slice_folder).resolve()
    if not slice_folder.exists():
        return AuditResult(
            slice_folder=str(slice_folder),
            violations=[CRPViolation(
                kind="usage-error", severity="Important",
                message=f"slice folder not found: {slice_folder}",
            )],
        )

    repo_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd()
    result = AuditResult(slice_folder=str(slice_folder), repo_root=str(repo_root))

    milestone_path = slice_folder / "milestone.json"
    if not milestone_path.exists():
        result.violations.append(CRPViolation(
            kind="usage-error", severity="Important",
            message=f"milestone.json not found in slice folder: {slice_folder}",
        ))
        return result

    milestone = _load_json(milestone_path)
    if milestone is None:
        result.violations.append(CRPViolation(
            kind="usage-error", severity="Important",
            message="milestone.json is missing or not a valid JSON object (cannot read critic-required).",
        ))
        return result

    # Resolve mode.
    mode = _resolve_mode(slice_folder, repo_root)
    if mode is None:
        result.violations.append(CRPViolation(
            kind="mode-unresolvable", severity="Important",
            message=(
                "cannot resolve pipeline mode from <vault>/triage.json `mode` "
                "or CLAUDE.md `**Mode**:` line"
            ),
        ))
        return result
    result.resolved_mode = mode

    # Read critic-required (tolerate JSON bool or string).
    cr_raw = _field(milestone, "critic_required", "critic-required")
    if isinstance(cr_raw, bool):
        critic_required = cr_raw
    else:
        critic_required = str(cr_raw or "").strip().lower() == "true"
    result.critic_required = critic_required

    # critique-review.json presence.
    result.critique_review_present = (slice_folder / "critique-review.json").exists()

    # Escape-hatch: critique-review-skip key.
    skip_raw = _field(milestone, "critique-review-skip", "critique_review_skip")
    skip_val = str(skip_raw).strip() if skip_raw is not None else None
    result.skip_key_present = skip_val is not None
    if skip_val is not None:
        if _SKIP_VALUE_RE.match(skip_val):
            result.skip_rationale = skip_val
        else:
            result.violations.append(CRPViolation(
                kind="escape-hatch-malformed", severity="Important",
                message=(
                    f"milestone.json `critique-review-skip` present but value "
                    f"{skip_val!r} does not match canonical shape "
                    f"`skip — rationale: <text>` (per ADR-024)."
                ),
            ))
            return result

    # Acceptance paths.
    if result.critique_review_present:
        result.accepted_reason = "critique-review.json present"
        return result
    if result.skip_rationale is not None:
        result.accepted_reason = f"documented skip — {result.skip_rationale}"
        return result
    if mode not in _ENFORCED_MODES:
        result.accepted_reason = f"mode {mode} does not enforce mandatory /critique-review"
        return result
    if not critic_required:
        result.accepted_reason = "critic-required is not true (no mandatory-Critic trigger)"
        return result

    # Refuse: mandatory /critique-review absent + unrationalised.
    result.violations.append(CRPViolation(
        kind="mandatory-critique-review-absent", severity="Important",
        message=(
            f"mandatory /critique-review is absent and unrationalised. Conditions "
            f"held: mode={mode} (in {{STANDARD, HEAVY}}); critic-required=true; "
            f"critique-review.json absent; no `critique-review-skip` milestone.json key. "
            f"Run `/critique-review` for this slice, OR document a deliberate skip by "
            f"adding `\"critique-review-skip\": \"skip — rationale: <text>\"` to "
            f"milestone.json (per ADR-024)."
        ),
    ))
    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="critique_review_prerequisite_audit",
        description="CRP-1 audit: refuse /build-slice on skipped mandatory /critique-review (v2 JSON).",
    )
    parser.add_argument("slice_folder", type=Path, help="Path to active slice folder.")
    parser.add_argument("--root", type=Path, default=None,
                        help="Repo root for the CLAUDE.md mode fallback (default: cwd).")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        result = audit(slice_folder=args.slice_folder, repo_root=args.root)
    except Exception as e:  # noqa: BLE001 — top-level CLI guard
        print(f"critique_review_prerequisite_audit: error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        if result.violations:
            for v in result.violations:
                print(f"[{v.severity}] {v.kind}: {v.message}")
        else:
            print(f"CRP-1 audit: clean. Accepted: {result.accepted_reason}.")

    usage_kinds = {"usage-error", "mode-unresolvable"}
    if any(v.kind in usage_kinds for v in result.violations):
        return 2
    if result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
