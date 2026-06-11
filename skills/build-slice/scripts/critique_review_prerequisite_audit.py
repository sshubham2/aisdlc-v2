"""Critique-review prerequisite audit (CRP-1) — v2 JSON, tier-driven.

Refuses ``/build-slice`` when a MANDATORY ``/critique-review`` (DR-1) was skipped
without a documented rationale. The structural skip-detector for DR-1.

Per CRP-1 (methodology-changelog.md v0.40.0). CRP-1 is an **audit-enforced gate**
(its programmatic gate is this module), so per ADR-019's test-pinned naming note
it carries the bare ``CRP-1`` form (NO ``-D`` suffix).

**Tier-driven trigger (remediation-plan 2.1 + 2.3).** ``/critique-review`` is no longer
gated on pipeline *mode* — the meta-Critic is a model-on-model cost paid per slice, so
it keys on the slice's RISK, exactly like ``/critique`` itself. It is MANDATORY when ANY
of these hold (the canonical table also lives in ``skills/critique/SKILL.md`` Step 3.5):

  - ``risk_tier == high``                              (mission-brief.json)
  - ``critic_required == true``                        (mission-brief.json / milestone.json)
        i.e. the slice trips a mandatory trigger — auth/authz, API contracts,
        data-model/migrations, security, or a methodology surface. In Heavy mode
        ``/slice`` forces ``critic_required: true`` on every slice, so Heavy's
        compliance floor falls out of this row with no separate mode check.
  - first-Critic ``findings`` count >= 5               (critique.json — severity-inflation check)

(The "3+ consecutive clean first-Critic verdicts" calibration smell is ADVISORY — it is
documented in the SKILL table and handled empirically by ``/critic-calibrate``, not
hard-refused here, so this audit stays deterministic and free of gate-log coupling.)

Refuse condition (exit 1, ``mandatory-critique-review-absent``):
    a mandatory trigger holds
    AND ``critique-review.json`` absent in the slice folder
    AND no ``critique-review-skip`` key in milestone.json

Accept (exit 0): ``critique-review.json`` present, OR a canonical
``critique-review-skip`` value present, OR no mandatory trigger holds.

Malformed-skip (exit 1, Important, ``escape-hatch-malformed``):
``critique-review-skip`` present but value does NOT match ``^skip — rationale: .+``.

Usage:
    python critique_review_prerequisite_audit.py <slice-folder>
    python critique_review_prerequisite_audit.py --json <slice-folder>

Exit codes:
    0  clean (accept)
    1  violations (mandatory review absent + unrationalised, or malformed skip)
    2  usage error (slice-folder missing, milestone.json missing)
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
# `rationale:` spirit as BRANCH-1's `BRANCH=skip — rationale:` (ADR-024). 3.8:
# the separator is now hyphen-TOLERANT — em-dash `—`, en-dash `–`, or a plain
# hyphen `-` all pass (typography, not dishonesty, must never block the gate).
_SKIP_VALUE_RE = re.compile(r"^skip\s*[—–-]\s*rationale:\s*.+")

# first-Critic findings count at or above which DR-1 becomes mandatory.
_FINDINGS_MANDATORY_THRESHOLD = 5


@dataclass(frozen=True)
class CRPViolation:
    kind: str       # "mandatory-critique-review-absent" | "escape-hatch-malformed" |
                    # "usage-error"
    severity: str   # "Important" (all CRP-1 violations refuse)
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    slice_folder: str = ""
    repo_root: str = ""
    risk_tier: str = ""
    critic_required: bool | None = None
    findings_count: int = 0
    mandatory_triggers: list[str] = field(default_factory=list)
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
            "risk_tier": self.risk_tier,
            "critic_required": self.critic_required,
            "findings_count": self.findings_count,
            "mandatory_triggers": list(self.mandatory_triggers),
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


def _read_risk_tier(slice_folder: Path) -> str:
    """Lowercased ``risk_tier`` from mission-brief.json, or "" if unreadable."""
    mb = _load_json(slice_folder / "mission-brief.json")
    if mb:
        val = mb.get("risk_tier")
        if isinstance(val, str):
            return val.strip().lower()
    return ""


def _read_findings_count(slice_folder: Path) -> int:
    """Length of critique.json ``findings[]`` (0 if absent/unreadable — e.g. the
    Critic was skipped on a low-tier slice)."""
    cj = _load_json(slice_folder / "critique.json")
    if cj:
        findings = cj.get("findings")
        if isinstance(findings, list):
            return len(findings)
    return 0


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

    # Read critic-required (tolerate JSON bool or string). mission-brief.json wins
    # if it carries the flag; milestone.json is the fallback.
    cr_raw = _field(milestone, "critic_required", "critic-required")
    mb = _load_json(slice_folder / "mission-brief.json")
    if mb is not None and _field(mb, "critic_required", "critic-required") is not None:
        cr_raw = _field(mb, "critic_required", "critic-required")
    if isinstance(cr_raw, bool):
        critic_required = cr_raw
    else:
        critic_required = str(cr_raw or "").strip().lower() == "true"
    result.critic_required = critic_required

    result.risk_tier = _read_risk_tier(slice_folder)
    result.findings_count = _read_findings_count(slice_folder)

    # Mandatory-trigger evaluation (the canonical tier-driven table).
    triggers: list[str] = []
    if result.risk_tier == "high":
        triggers.append("risk_tier=high")
    if critic_required:
        triggers.append("critic_required=true")
    if result.findings_count >= _FINDINGS_MANDATORY_THRESHOLD:
        triggers.append(f"findings={result.findings_count}>={_FINDINGS_MANDATORY_THRESHOLD}")
    result.mandatory_triggers = triggers

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
    if not triggers:
        result.accepted_reason = (
            f"no mandatory DR-1 trigger (risk_tier={result.risk_tier or '?'} != high; "
            f"critic_required={critic_required}; findings={result.findings_count} < "
            f"{_FINDINGS_MANDATORY_THRESHOLD})"
        )
        return result

    # Refuse: mandatory /critique-review absent + unrationalised.
    result.violations.append(CRPViolation(
        kind="mandatory-critique-review-absent", severity="Important",
        message=(
            f"mandatory /critique-review is absent and unrationalised. Mandatory "
            f"trigger(s) held: {', '.join(triggers)}; critique-review.json absent; "
            f"no `critique-review-skip` milestone.json key. Run `/critique-review` "
            f"for this slice, OR document a deliberate skip by adding "
            f"`\"critique-review-skip\": \"skip — rationale: <text>\"` to "
            f"milestone.json (per ADR-024)."
        ),
    ))
    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="critique_review_prerequisite_audit",
        description="CRP-1 audit: refuse /build-slice on skipped mandatory /critique-review (v2 JSON, tier-driven).",
    )
    parser.add_argument("slice_folder", type=Path, help="Path to active slice folder.")
    parser.add_argument("--root", type=Path, default=None,
                        help="Repo root (accepted for CLI compatibility; no longer used).")
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

    usage_kinds = {"usage-error"}
    if any(v.kind in usage_kinds for v in result.violations):
        return 2
    if result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
