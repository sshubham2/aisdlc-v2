"""Triage audit (TRI-1) — v2 JSON.

Audits the user-ratified triage object embedded in a slice's `critique.json`
(v1 parsed the `## Triage` markdown section of `critique.md`). In v2 the triage
is a JSON object — `critique.json.triage` — alongside the Critic's findings in
`critique.json.findings[]`.

It validates:
  - A `triage` object is present (per TRI-1, every critique must carry a
    user-ratified triage).
  - Required triage header fields present: `ratified_by`, `at`, `verdict`.
  - `verdict` is one of {clean, needs-fixes, blocked} (case-insensitive).
  - Every finding in `findings[]` has a disposition row in `triage.dispositions[]`.
  - Each disposition `action` is in the allowed vocabulary.
  - overridden / deferred / escalated dispositions carry a non-empty `rationale`.
  - The declared `verdict` is consistent with the disposition pattern:
        any escalated                     -> blocked
        any accepted-pending (no esc.)    -> needs-fixes
        otherwise                         -> clean

Per TRI-1 (methodology-changelog.md v0.11.0). The rule's purpose: make
Critic-Builder-User triage explicit and auditable so dispositions don't
disappear into Builder hand-waves and the user has formal authority over
the gate decision.

v2 shape (`critique.json`; schema by example `skills/critique/examples/critique.json`):

    {
      "_schema": "aisdlc/critique@1",
      "slice": "slice-021",
      "verdict": "needs-fixes",
      "findings": [
        {"id": "C1", "severity": "major", "claim": "...", "disposition": "..."}
      ],
      "triage": {
        "verdict": "needs-fixes",
        "ratified_by": "<user>",
        "at": "<ts>",
        "dispositions": [
          {"finding": "C1", "action": "accepted-pending", "rationale": "..."}
        ]
      }
    }

Disposition vocabulary (v2 lowercase):
  - accepted-fixed      agree with Critic; fix applied already
  - accepted-pending    agree with Critic; fix to apply during /build-slice
  - overridden          user disagrees with Critic — rationale required
  - deferred            known issue; later slice — rationale required
  - escalated           spike or redesign needed — rationale required

NFR-1 mtime carry-over was REMOVED (3.9 — it was dead for every post-install user).
`--no-carry-over` is still accepted as a no-op for CLI compatibility.

Usage:
    python triage_audit.py <slice-folder>
    python triage_audit.py <critique.json>
    python triage_audit.py --json <slice-folder>
    python triage_audit.py --no-carry-over <slice-folder>

Exit codes:
    0  clean
    1  triage violations
    2  usage error
"""
from __future__ import annotations

import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.lib import _stdout

# Required fields in the triage header block
_REQUIRED_HEADER_FIELDS: frozenset[str] = frozenset({"ratified_by", "at", "verdict"})

# Allowed verdict values (compared case-insensitively)
_ALLOWED_VERDICTS: frozenset[str] = frozenset({"clean", "needs-fixes", "blocked"})

# Allowed dispositions (v2 lowercase)
_ALLOWED_DISPOSITIONS: frozenset[str] = frozenset({
    "accepted-fixed", "accepted-pending", "overridden", "deferred", "escalated",
})

# Dispositions that REQUIRE a non-empty rationale
_RATIONALE_REQUIRED: frozenset[str] = frozenset({"overridden", "deferred", "escalated"})

# Sentinel rationale values treated as empty
_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class TriageViolation:
    """A finding emitted by the audit."""
    path: str
    finding_id: str  # may be "" for section-level errors
    kind: str        # "no-triage" | "missing-field" | "invalid-verdict" |
                     # "missing-row" | "invalid-disposition" |
                     # "missing-rationale" | "verdict-mismatch" | "format"
    severity: str    # always "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TriageResult:
    declared_verdict: str = ""
    expected_verdict: str = ""
    ratified_by: str = ""
    date: str = ""
    findings: list[str] = field(default_factory=list)
    dispositions: dict[str, str] = field(default_factory=dict)  # finding_id -> disposition
    violations: list[TriageViolation] = field(default_factory=list)
    carry_over_exempt: bool = False

    def to_dict(self) -> dict:
        return {
            "declared_verdict": self.declared_verdict,
            "expected_verdict": self.expected_verdict,
            "ratified_by": self.ratified_by,
            "date": self.date,
            "findings": list(self.findings),
            "dispositions": dict(self.dispositions),
            "violations": [v.to_dict() for v in self.violations],
            "carry_over_exempt": self.carry_over_exempt,
            "summary": {
                "violation_count": len(self.violations),
                "consistent": (
                    self.declared_verdict == self.expected_verdict
                    if self.declared_verdict and self.expected_verdict
                    else False
                ),
            },
        }


def _cell_is_empty(cell: str) -> bool:
    return str(cell).strip().lower() in _EMPTY_SENTINELS


def _expected_verdict(dispositions: dict[str, str]) -> str:
    """Compute the expected verdict from the disposition pattern."""
    if not dispositions:
        return "clean"
    values = set(dispositions.values())
    if "escalated" in values:
        return "blocked"
    if "accepted-pending" in values:
        return "needs-fixes"
    return "clean"


def audit_critique_file(
    critique_path: Path,
    skip_if_carry_over: bool = True,
) -> TriageResult:
    """Audit a critique.json file's embedded triage object against TRI-1."""
    result = TriageResult()

    if not critique_path.exists():
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="no-triage", severity="Important",
            message=f"critique.json not found: {critique_path}",
        ))
        return result

    try:
        text = critique_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError) as exc:
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="", kind="format",
            severity="Important", message=f"cannot read critique.json: {exc}",
        ))
        return result
    except json.JSONDecodeError as exc:
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="", kind="format",
            severity="Important", message=f"critique.json is not valid JSON: {exc}",
        ))
        return result

    if not isinstance(data, dict):
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="", kind="format",
            severity="Important", message="critique.json top level is not a JSON object.",
        ))
        return result

    # Findings declared by the Critic (the body the triage must cover).
    findings: list[str] = []
    seen_fids: set[str] = set()
    raw_findings = data.get("findings")
    if isinstance(raw_findings, list):
        for entry in raw_findings:
            if not isinstance(entry, dict):
                continue
            fid = str(entry.get("id", "")).strip()
            if fid and fid not in seen_fids:
                seen_fids.add(fid)
                findings.append(fid)
    result.findings = findings

    triage = data.get("triage")
    if not isinstance(triage, dict):
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="no-triage", severity="Important",
            message=(
                "no `triage` object found in critique.json. Per TRI-1, every "
                "critique must include a user-ratified triage (ratified_by / at / "
                "verdict + per-finding dispositions)."
            ),
        ))
        return result

    # Required header fields
    present_fields = {k for k in _REQUIRED_HEADER_FIELDS
                      if str(triage.get(k, "")).strip()}
    missing_header = _REQUIRED_HEADER_FIELDS - present_fields
    if missing_header:
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="missing-field", severity="Important",
            message=(
                f"triage object missing required field(s): "
                f"{', '.join(sorted(missing_header))}. Required: "
                f"{', '.join(sorted(_REQUIRED_HEADER_FIELDS))}."
            ),
        ))

    result.ratified_by = str(triage.get("ratified_by", "")).strip()
    result.date = str(triage.get("at", "")).strip()
    declared = str(triage.get("verdict", "")).strip().lower()
    result.declared_verdict = declared

    if declared and declared not in _ALLOWED_VERDICTS:
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="invalid-verdict", severity="Important",
            message=(
                f"triage verdict '{declared}' not allowed. Use one of: "
                f"{', '.join(sorted(_ALLOWED_VERDICTS))}."
            ),
        ))

    # Parse disposition rows
    raw_disp = triage.get("dispositions")
    if raw_disp is None:
        raw_disp = []
    if not isinstance(raw_disp, list):
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="format", severity="Important",
            message="triage.dispositions is not a JSON array.",
        ))
        return result

    seen_ids: set[str] = set()
    for idx, row in enumerate(raw_disp):
        if not isinstance(row, dict):
            result.violations.append(TriageViolation(
                path=str(critique_path), finding_id="",
                kind="format", severity="Important",
                message=f"triage.dispositions[{idx}] is not a JSON object.",
            ))
            continue

        fid = str(row.get("finding", "")).strip()
        if not fid:
            continue
        seen_ids.add(fid)

        action = str(row.get("action", "")).strip().lower()
        if action not in _ALLOWED_DISPOSITIONS:
            result.violations.append(TriageViolation(
                path=str(critique_path), finding_id=fid,
                kind="invalid-disposition", severity="Important",
                message=(
                    f"finding {fid}: disposition '{action}' not allowed. "
                    f"Use one of: {', '.join(sorted(_ALLOWED_DISPOSITIONS))}."
                ),
            ))
            continue

        result.dispositions[fid] = action

        if action in _RATIONALE_REQUIRED and _cell_is_empty(row.get("rationale", "")):
            result.violations.append(TriageViolation(
                path=str(critique_path), finding_id=fid,
                kind="missing-rationale", severity="Important",
                message=(
                    f"finding {fid}: disposition '{action}' requires a "
                    f"non-empty rationale."
                ),
            ))

    # Findings without a disposition row
    for fid in findings:
        if fid not in seen_ids:
            result.violations.append(TriageViolation(
                path=str(critique_path), finding_id=fid,
                kind="missing-row", severity="Important",
                message=(
                    f"finding {fid} declared in findings[] but has no triage "
                    f"disposition. Per TRI-1, every finding must have a disposition."
                ),
            ))

    # Verdict consistency
    expected = _expected_verdict(result.dispositions)
    result.expected_verdict = expected
    if declared and declared in _ALLOWED_VERDICTS and declared != expected:
        result.violations.append(TriageViolation(
            path=str(critique_path), finding_id="",
            kind="verdict-mismatch", severity="Important",
            message=(
                f"declared triage verdict '{declared}' does not match the "
                f"disposition pattern. Expected: '{expected}'. Pattern: any "
                f"escalated -> blocked; else any accepted-pending -> needs-fixes; "
                f"else clean."
            ),
        ))

    return result


def _format_human(result: TriageResult) -> str:
    if result.carry_over_exempt:
        return (
            "Triage audit: slice is carry-over exempt "
            "(mission-brief.json predates TRI-1 release).\n"
        )

    if not result.violations:
        if result.declared_verdict:
            return (
                f"Triage audit: clean. Verdict: {result.declared_verdict} "
                f"({len(result.findings)} finding(s); "
                f"ratified by {result.ratified_by or 'unknown'}).\n"
            )
        return "Triage audit: clean (no findings).\n"

    out: list[str] = [f"{len(result.violations)} triage violation(s):\n\n"]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind}) "
            f"{f'finding {v.finding_id}' if v.finding_id else ''}\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="triage_audit",
        description="TRI-1 triage audit — user-owned triage discipline (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds critique.json inside) OR a critique.json file",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output result as JSON (machine-readable)",
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Disable mtime-based carry-over exemption",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    critique_path = target / "critique.json" if target.is_dir() else target

    result = audit_critique_file(
        critique_path,
        skip_if_carry_over=not args.no_carry_over,
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
