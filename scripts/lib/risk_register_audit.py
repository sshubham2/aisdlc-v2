"""Risk register audit (RR-1) — v2 JSON.

Loads `<vault>/risk-register.json` (the v2 JSON artifact; v1 parsed the
`risk-register.md` H2-structured markdown) and:
  - Validates required fields per risk (id, title, likelihood, impact, status)
  - Validates allowed values per field
  - Detects duplicate risk IDs
  - Computes canonical Score = Likelihood * Impact (low=1, medium=2, high=3 -> 1..9)
    and Band (1-2 low, 3-4 medium, 6-9 high), and REJECTS a stored score/band that
    disagrees (triage writes them; "set them correctly or the audit rejects")
  - Sorts and filters for downstream consumers (/slice, /pulse)

v2 shape (the `risks[]` array of `<vault>/risk-register.json`; schema by example
`skills/*/examples/risk-register.json`):

    {
      "_schema": "aisdlc/risk-register@1",
      "risks": [
        {
          "id": "R-1", "title": "...",
          "likelihood": "low|medium|high", "impact": "low|medium|high",
          "status": "open|mitigating|retired|accepted|blocking|conditional",
          "reversibility": "cheap|expensive|irreversible",   (optional)
          "score": 1..9, "band": "low|medium|high",          (validated vs computed)
          "mitigation": "<text or spike ref>",               (optional)
          "discovered": {"phase": "...", "at": "..."},        (optional)
          "notes": "<free text>"                              (optional)
        }
      ]
    }

v2 status set is the union of v1 lifecycle states + the in-loop /risk-spike
verdicts (`/risk-spike` maps GO->retired, NO-GO->blocking, CONDITIONAL->conditional).

Usage:
    python -m scripts.lib.risk_register_audit <register.json>
    python -m scripts.lib.risk_register_audit <register.json> --json
    python -m scripts.lib.risk_register_audit <register.json> --filter-status open
    python -m scripts.lib.risk_register_audit <register.json> --filter-band high
    python -m scripts.lib.risk_register_audit <register.json> --sort score --top 3

Exit codes:
    0  clean (or empty/absent register)
    1  violations
    2  usage error (malformed JSON)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON
# hooks/MCP). Shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" ...`, which puts scripts/lib
# (not the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib import
# ...` resolves, mirroring the single-skill parents[3] bootstrap. No-op under `-m`.
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

_REQUIRED_FIELDS: frozenset[str] = frozenset({"id", "title", "likelihood", "impact", "status"})

_ALLOWED_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})
# v1 lifecycle states + the in-loop /risk-spike verdicts (retired/blocking/conditional).
_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"open", "mitigating", "retired", "accepted", "blocking", "conditional"}
)
_ALLOWED_REVERSIBILITY: frozenset[str] = frozenset({"cheap", "expensive", "irreversible"})

_LEVEL_NUMERIC = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Risk:
    id: str
    title: str
    likelihood: str
    impact: str
    status: str
    score: int
    band: str
    reversibility: str
    mitigation: str
    discovered: Any
    notes: str
    index: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RiskViolation:
    path: str
    index: int
    risk_id: str
    kind: str    # missing-field | invalid-value | duplicate-id | score-mismatch | band-mismatch | format
    severity: str  # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    risks: list[Risk] = field(default_factory=list)
    violations: list[RiskViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        by_band: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
        by_status: dict[str, int] = {s: 0 for s in sorted(_ALLOWED_STATUSES)}
        for r in self.risks:
            by_band[r.band] += 1
            by_status[r.status] = by_status.get(r.status, 0) + 1
        open_high = [r.to_dict() for r in self.risks if r.status == "open" and r.band == "high"]
        return {
            "risks": [r.to_dict() for r in self.risks],
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "total": len(self.risks),
                "by_band": by_band,
                "by_status": by_status,
                "open_high_count": len(open_high),
                "open_high": open_high,
                "violation_count": len(self.violations),
            },
        }


def _band_for_score(score: int) -> str:
    if score <= 2:
        return "low"
    if score <= 4:
        return "medium"
    return "high"


def _parse_risks(data: Any, path: str) -> tuple[list[Risk], list[RiskViolation]]:
    """Parse + validate the `risks[]` array of a loaded risk-register.json."""
    risks: list[Risk] = []
    violations: list[RiskViolation] = []

    raw = data.get("risks") if isinstance(data, dict) else None
    if raw is None:
        return risks, violations  # no risks key -> empty register (silent)
    if not isinstance(raw, list):
        violations.append(RiskViolation(
            path=path, index=-1, risk_id="", kind="format", severity="Important",
            message="`risks` is not a JSON array.",
        ))
        return risks, violations

    seen_ids: dict[str, int] = {}
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            violations.append(RiskViolation(
                path=path, index=idx, risk_id="", kind="format", severity="Important",
                message=f"risks[{idx}] is not a JSON object.",
            ))
            continue

        risk_id = str(entry.get("id", "")).strip()

        # Required-field check
        missing = sorted(f for f in _REQUIRED_FIELDS if not str(entry.get(f, "")).strip())
        if missing:
            violations.append(RiskViolation(
                path=path, index=idx, risk_id=risk_id, kind="missing-field", severity="Important",
                message=(f"risks[{idx}] (id={risk_id or '?'}): missing required field(s): "
                         f"{', '.join(missing)}."),
            ))
            continue

        # Duplicate ID check
        if risk_id in seen_ids:
            violations.append(RiskViolation(
                path=path, index=idx, risk_id=risk_id, kind="duplicate-id", severity="Important",
                message=(f"risk {risk_id} declared again at risks[{idx}] "
                         f"(first at risks[{seen_ids[risk_id]}]). IDs must be unique."),
            ))
            continue
        seen_ids[risk_id] = idx

        likelihood = str(entry["likelihood"]).strip().lower()
        impact = str(entry["impact"]).strip().lower()
        status = str(entry["status"]).strip().lower()

        had_invalid = False
        for fname, fval, allowed in (
            ("likelihood", likelihood, _ALLOWED_LEVELS),
            ("impact", impact, _ALLOWED_LEVELS),
            ("status", status, _ALLOWED_STATUSES),
        ):
            if fval not in allowed:
                violations.append(RiskViolation(
                    path=path, index=idx, risk_id=risk_id, kind="invalid-value", severity="Important",
                    message=f"risk {risk_id}: {fname} '{fval}' not in {sorted(allowed)}.",
                ))
                had_invalid = True
        if had_invalid:
            continue

        reversibility = str(entry.get("reversibility", "")).strip().lower()
        if reversibility and reversibility not in _ALLOWED_REVERSIBILITY:
            violations.append(RiskViolation(
                path=path, index=idx, risk_id=risk_id, kind="invalid-value", severity="Important",
                message=f"risk {risk_id}: reversibility '{reversibility}' not in {sorted(_ALLOWED_REVERSIBILITY)}.",
            ))
            continue

        score = _LEVEL_NUMERIC[likelihood] * _LEVEL_NUMERIC[impact]
        band = _band_for_score(score)

        # Stored score/band must agree with the canonical computation (triage writes
        # them; "set them correctly or the audit rejects"). Absent -> the canonical
        # value is used with no violation.
        stored_score = entry.get("score")
        if stored_score is not None and stored_score != score:
            violations.append(RiskViolation(
                path=path, index=idx, risk_id=risk_id, kind="score-mismatch", severity="Important",
                message=(f"risk {risk_id}: stored score {stored_score} != computed {score} "
                         f"({likelihood}x{impact})."),
            ))
            continue
        stored_band = entry.get("band")
        if stored_band is not None and str(stored_band).strip().lower() != band:
            violations.append(RiskViolation(
                path=path, index=idx, risk_id=risk_id, kind="band-mismatch", severity="Important",
                message=f"risk {risk_id}: stored band '{stored_band}' != computed '{band}' (score {score}).",
            ))
            continue

        risks.append(Risk(
            id=risk_id,
            title=str(entry["title"]).strip(),
            likelihood=likelihood,
            impact=impact,
            status=status,
            score=score,
            band=band,
            reversibility=reversibility,
            mitigation=str(entry.get("mitigation", "")),
            discovered=entry.get("discovered", ""),
            notes=str(entry.get("notes", "")),
            index=idx,
        ))

    return risks, violations


def audit_register(register_path: Path) -> AuditResult:
    """Audit a risk-register.json file. A missing file is silent (empty result)."""
    result = AuditResult()
    if not register_path.exists():
        return result
    try:
        text = register_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError) as exc:
        result.violations.append(RiskViolation(
            path=str(register_path), index=-1, risk_id="", kind="format", severity="Important",
            message=f"cannot read register: {exc}",
        ))
        return result
    except json.JSONDecodeError as exc:
        result.violations.append(RiskViolation(
            path=str(register_path), index=-1, risk_id="", kind="format", severity="Important",
            message=f"risk-register.json is not valid JSON: {exc}",
        ))
        return result
    risks, violations = _parse_risks(data, str(register_path))
    result.risks = risks
    result.violations = violations
    return result


def filter_and_sort(
    result: AuditResult,
    filter_status: str | None = None,
    filter_band: str | None = None,
    sort_by: str = "score",
    top: int | None = None,
) -> list[Risk]:
    """Apply filter + sort to the audit result; return list of risks."""
    risks = list(result.risks)
    if filter_status:
        risks = [r for r in risks if r.status == filter_status]
    if filter_band:
        risks = [r for r in risks if r.band == filter_band]

    if sort_by == "score":
        risks.sort(key=lambda r: (-r.score, r.id))
    elif sort_by == "band":
        band_order = {"high": 0, "medium": 1, "low": 2}
        risks.sort(key=lambda r: (band_order.get(r.band, 99), -r.score, r.id))
    elif sort_by == "id":
        risks.sort(key=lambda r: r.id)

    if top is not None and top > 0:
        risks = risks[:top]
    return risks


def _format_human(result: AuditResult, view: list[Risk]) -> str:
    if result.violations:
        out: list[str] = [f"{len(result.violations)} risk-register violation(s):\n\n"]
        for v in result.violations:
            loc = f"risks[{v.index}]" if v.index >= 0 else v.path
            out.append(
                f"  [{v.severity}] {v.path} {loc} ({v.kind}) "
                f"{f'risk {v.risk_id}' if v.risk_id else ''}\n    {v.message}\n\n"
            )
        return "".join(out)

    if not view:
        return "Risk register: 0 risks (or filter excluded all).\n"

    lines: list[str] = [f"Risk register: {len(view)} risk(s):\n\n"]
    for r in view:
        lines.append(
            f"  [{r.band:>6}] {r.id} score={r.score} ({r.likelihood}x{r.impact}) "
            f"status={r.status} - {r.title}\n"
        )
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="risk_register_audit",
        description="RR-1 risk register audit + scoring (v2 JSON)",
    )
    parser.add_argument(
        "register", type=Path, nargs="?",
        default=VAULT_ROOT / "risk-register.json",
        help="Path to risk-register.json (default: <vault>/risk-register.json)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--filter-status", choices=sorted(_ALLOWED_STATUSES),
                        help="Show only risks with this status")
    parser.add_argument("--filter-band", choices=["low", "medium", "high"],
                        help="Show only risks in this band")
    parser.add_argument("--sort", choices=["score", "band", "id"], default="score",
                        help="Sort order (default: score)")
    parser.add_argument("--top", type=int, default=None,
                        help="Limit output to first N risks after sort")
    args = parser.parse_args(argv)

    result = audit_register(args.register)
    view = filter_and_sort(
        result,
        filter_status=args.filter_status,
        filter_band=args.filter_band,
        sort_by=args.sort,
        top=args.top,
    )

    if args.json:
        out = result.to_dict()
        out["risks"] = [r.to_dict() for r in view]  # consumed key reflects filter/sort/top
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result, view))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
