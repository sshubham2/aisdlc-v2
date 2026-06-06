"""Exploratory-charter audit (ETC-1) — v2 JSON.

Reads a slice's `mission-brief.json` and validates:
  - The opt-in flag `variants.exploratory_charter` (v1: `**Exploratory-charter**:
    true` field in the markdown brief) gates the audit
  - When true, an `exploratory_charters` array must be present and non-empty
  - Each charter object carries `mission`, `timebox`, `status`, `findings`
  - `mission` is non-empty
  - `status` is one of {pending, in-progress, completed, deferred}
  - `findings` is non-empty when status is `completed` or `deferred`
    (a completed/deferred charter without captured findings defeats the
    discipline)
  - With --strict-pre-finish, `pending` and `in-progress` are violations;
    `completed` and `deferred` are both accepted (deferred is the escape hatch)

Per ETC-1. Charter-based exploratory testing (Bach / Kaner / Hendrickson):
each charter is a timeboxed mission ("Explore X using Y to find Z").

Default-off semantics: a brief without `variants.exploratory_charter: true` is
unaffected. ETC-1 is opt-in per slice.

**v2 change from v1.** The brief is JSON, not markdown. The boolean flag is
`variants.exploratory_charter` (was the `**Exploratory-charter**: true` field
line); the 5-column markdown table `# | Mission | Timebox | Status | Findings`
becomes the `exploratory_charters[]` array of objects. Statuses are lowercase
JSON tokens (`pending` / `in-progress` / `completed` / `deferred`) rather than
UPPER markdown cells. The NFR-1 carry-over mtime exemption now keys on
`mission-brief.json`. Audit semantics, violation kinds, exit codes, and
`--strict-pre-finish` are otherwise preserved.

v2 brief shape (the relevant fields of `mission-brief.json`):

    {
      "variants": {"exploratory_charter": true, ...},
      "exploratory_charters": [
        {"mission": "Explore HEIC upload edge cases", "timebox": "30m",
         "status": "completed", "findings": "corrupt files 500 instead of 415"}
      ]
    }

Usage:
    python exploratory_charter_audit.py <slice-folder>
    python exploratory_charter_audit.py <mission-brief.json>
    python exploratory_charter_audit.py <slice-folder> --strict-pre-finish
    python exploratory_charter_audit.py <slice-folder> --json
    python exploratory_charter_audit.py <slice-folder> --no-carry-over

Exit codes:
    0  clean (or default-off / carry-over exempt)
    1  violations
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

# Date this rule shipped. NFR-1 carry-over.
_ETC_1_RELEASE_DATE: date = date(2026, 5, 6)

# Allowed statuses (lowercase JSON tokens; v1 markdown cells were UPPER).
_ALLOWED_STATUSES: frozenset[str] = frozenset({
    "pending", "in-progress", "completed", "deferred",
})

# Statuses that REQUIRE non-empty findings (the whole point of the discipline).
_FINDINGS_REQUIRED: frozenset[str] = frozenset({"completed", "deferred"})

# Statuses accepted at strict-pre-finish (charter is "done" — completion or
# deliberate deferral).
_STRICT_ACCEPTED: frozenset[str] = frozenset({"completed", "deferred"})

_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class CharterRow:
    index: str
    mission: str
    timebox: str
    status: str
    findings: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ETCViolation:
    path: str
    charter_index: str  # "" for section-level errors
    kind: str           # "missing-section" | "empty-table" | "missing-mission" |
                        # "missing-findings" | "invalid-status" | "format" |
                        # "non-final-pre-finish"
    severity: str       # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    exploratory_charter_enabled: bool = False
    rows: list[CharterRow] = field(default_factory=list)
    violations: list[ETCViolation] = field(default_factory=list)
    carry_over_exempt: bool = False

    def to_dict(self) -> dict:
        return {
            "exploratory_charter_enabled": self.exploratory_charter_enabled,
            "rows": [r.to_dict() for r in self.rows],
            "violations": [v.to_dict() for v in self.violations],
            "carry_over_exempt": self.carry_over_exempt,
            "summary": {
                "row_count": len(self.rows),
                "by_status": {
                    s: sum(1 for r in self.rows if r.status == s)
                    for s in _ALLOWED_STATUSES
                },
                "violation_count": len(self.violations),
            },
        }


def _slice_is_carry_over(brief_path: Path) -> bool:
    if not brief_path.exists():
        return False
    mtime_date = datetime.fromtimestamp(brief_path.stat().st_mtime).date()
    return mtime_date < _ETC_1_RELEASE_DATE


def _cell_is_empty(cell: str) -> bool:
    return cell.strip().lower() in _EMPTY_SENTINELS


def audit_brief_file(
    brief_path: Path,
    strict_pre_finish: bool = False,
    skip_if_carry_over: bool = True,
) -> AuditResult:
    """Audit a mission-brief.json against ETC-1."""
    result = AuditResult()

    if not brief_path.exists():
        return result

    if skip_if_carry_over and _slice_is_carry_over(brief_path):
        result.carry_over_exempt = True
        return result

    try:
        data = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.violations.append(ETCViolation(
            path=str(brief_path), charter_index="", kind="format",
            severity="Important",
            message=f"mission-brief.json is not readable/valid JSON: {exc}",
        ))
        return result
    if not isinstance(data, dict):
        result.violations.append(ETCViolation(
            path=str(brief_path), charter_index="", kind="format",
            severity="Important",
            message="mission-brief.json top level is not a JSON object.",
        ))
        return result

    variants = data.get("variants") if isinstance(data.get("variants"), dict) else {}
    enabled = bool(variants.get("exploratory_charter", False))
    result.exploratory_charter_enabled = enabled

    if not enabled:
        return result

    charters = data.get("exploratory_charters")
    if charters is None:
        result.violations.append(ETCViolation(
            path=str(brief_path), charter_index="", kind="missing-section",
            severity="Important",
            message=(
                "`variants.exploratory_charter` is true but no "
                "`exploratory_charters` array was found. Per ETC-1, when the "
                "flag is true the brief must list at least one charter "
                "(mission, timebox, status, findings)."
            ),
        ))
        return result

    if not isinstance(charters, list):
        result.violations.append(ETCViolation(
            path=str(brief_path), charter_index="", kind="format",
            severity="Important",
            message="`exploratory_charters` is not a JSON array.",
        ))
        return result

    if not charters:
        result.violations.append(ETCViolation(
            path=str(brief_path), charter_index="", kind="empty-table",
            severity="Important",
            message=(
                "`exploratory_charters` has no entries. Per ETC-1, list at "
                "least one charter or set exploratory_charter to false."
            ),
        ))
        return result

    for idx, entry in enumerate(charters, start=1):
        index_cell = str(idx)
        if not isinstance(entry, dict):
            result.violations.append(ETCViolation(
                path=str(brief_path), charter_index=index_cell, kind="format",
                severity="Important",
                message=f"charter {idx} is not a JSON object.",
            ))
            continue

        mission = str(entry.get("mission", "")).strip()
        timebox = str(entry.get("timebox", "")).strip()
        status_raw = str(entry.get("status", "")).strip()
        findings = str(entry.get("findings", "")).strip()

        if not any(str(v).strip() for v in entry.values()):  # BB-26: skip a WHOLLY-empty entry (index_cell is a synthetic counter, never empty)
            continue

        if _cell_is_empty(mission):
            result.violations.append(ETCViolation(
                path=str(brief_path), charter_index=index_cell,
                kind="missing-mission", severity="Important",
                message=(
                    f"charter {idx}: `mission` is empty. Per ETC-1, every "
                    f"charter must declare what to explore (e.g., 'Explore "
                    f"HEIC upload edge cases using corrupted files to find "
                    f"error-handling gaps')."
                ),
            ))
            continue

        status = status_raw.lower().strip()
        if status not in _ALLOWED_STATUSES:
            result.violations.append(ETCViolation(
                path=str(brief_path), charter_index=index_cell,
                kind="invalid-status", severity="Important",
                message=(
                    f"charter {idx}: status '{status_raw}' not in "
                    f"{sorted(_ALLOWED_STATUSES)}."
                ),
            ))
            continue

        if status in _FINDINGS_REQUIRED and _cell_is_empty(findings):
            result.violations.append(ETCViolation(
                path=str(brief_path), charter_index=index_cell,
                kind="missing-findings", severity="Important",
                message=(
                    f"charter {idx}: status '{status}' requires non-empty "
                    f"`findings`. Per ETC-1, a completed charter without "
                    f"captured findings defeats the discipline; a deferred "
                    f"charter without rationale is hand-waved."
                ),
            ))
            continue

        result.rows.append(CharterRow(
            index=index_cell, mission=mission, timebox=timebox,
            status=status, findings=findings,
        ))

    if strict_pre_finish:
        for row in result.rows:
            if row.status not in _STRICT_ACCEPTED:
                result.violations.append(ETCViolation(
                    path=str(brief_path), charter_index=row.index,
                    kind="non-final-pre-finish", severity="Important",
                    message=(
                        f"charter '{row.mission[:60]}' status is {row.status}; "
                        f"--strict-pre-finish requires completed or deferred. "
                        f"Either run the charter and record findings, or "
                        f"deliberately defer with rationale."
                    ),
                ))

    return result


def _format_human(result: AuditResult) -> str:
    if result.carry_over_exempt:
        return (
            "Exploratory-charter audit: slice is carry-over exempt "
            "(mission-brief.json predates ETC-1 release).\n"
        )
    if not result.exploratory_charter_enabled:
        return (
            "Exploratory-charter audit: not enabled "
            "(`variants.exploratory_charter` absent or false).\n"
        )

    if not result.violations:
        by_status = {
            s: sum(1 for r in result.rows if r.status == s)
            for s in _ALLOWED_STATUSES
        }
        return (
            f"Exploratory-charter audit: clean. {len(result.rows)} "
            f"charter(s) — completed={by_status['completed']}, "
            f"in-progress={by_status['in-progress']}, "
            f"deferred={by_status['deferred']}, "
            f"pending={by_status['pending']}.\n"
        )

    out: list[str] = [
        f"{len(result.violations)} exploratory-charter violation(s):\n\n"
    ]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind}) "
            f"{f'charter #{v.charter_index}' if v.charter_index else ''}\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="exploratory_charter_audit",
        description="ETC-1 charter-based exploratory testing audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds mission-brief.json inside) OR a mission-brief.json file",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--strict-pre-finish", action="store_true",
        help=(
            "Refuse pending / in-progress charters (use at /validate-slice "
            "ETC-1 gate); completed and deferred are both accepted"
        ),
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Disable mtime-based carry-over exemption",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    brief_path = target / "mission-brief.json" if target.is_dir() else target

    result = audit_brief_file(
        brief_path,
        strict_pre_finish=args.strict_pre_finish,
        skip_if_carry_over=not args.no_carry_over,
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
