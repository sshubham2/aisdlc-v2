"""Walking-skeleton slice audit (WS-1) — v2 JSON.

Reads a slice's `mission-brief.json` and validates:
  - The opt-in flag `variants.walking_skeleton` (v1: `**Walking-skeleton**: true`
    field in the markdown brief) gates the audit
  - When true, an `architectural_layers` array must be present and non-empty
    (a walking skeleton with no layers is meaningless — that's a standard slice)
  - Each layer object carries `layer`, `component`, `verification`, `status`
  - Each layer's `verification` is non-empty
  - Each layer's `status` is one of {pending, exercised}
  - With --strict-pre-finish, any non-`exercised` layer is a violation
    (used at /validate-slice Step 5 WS-1 gate)

Per WS-1. The walking-skeleton discipline (Cockburn): the smallest possible
end-to-end implementation that exercises every architectural layer.

Default-off semantics: a brief without `variants.walking_skeleton: true` is
unaffected. WS-1 is opt-in per slice.

**v2 change from v1.** The brief is JSON, not markdown. The boolean flag is
`variants.walking_skeleton` (was the `**Walking-skeleton**: true` field line);
the 5-column markdown table `# | Layer | Component | Verification | Status`
becomes the `architectural_layers[]` array of objects. Statuses are lowercase
JSON tokens (`pending` / `exercised`) rather than UPPER markdown cells. The
NFR-1 carry-over mtime exemption now keys on `mission-brief.json`. Audit
semantics, violation kinds, exit codes, and `--strict-pre-finish` are otherwise
preserved.

v2 brief shape (the relevant fields of `mission-brief.json`):

    {
      "variants": {"walking_skeleton": true, ...},
      "architectural_layers": [
        {"layer": "API", "component": "routes.py",
         "verification": "curl returns 200", "status": "exercised"}
      ]
    }

Usage:
    python walking_skeleton_audit.py <slice-folder>
    python walking_skeleton_audit.py <mission-brief.json>
    python walking_skeleton_audit.py <slice-folder> --strict-pre-finish
    python walking_skeleton_audit.py <slice-folder> --json
    python walking_skeleton_audit.py <slice-folder> --no-carry-over

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
_WS_1_RELEASE_DATE: date = date(2026, 5, 6)

# Allowed statuses (lowercase JSON tokens; v1 markdown cells were UPPER).
_ALLOWED_STATUSES: frozenset[str] = frozenset({"pending", "exercised"})

_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class LayerRow:
    index: str       # the layer's 1-based position (free-form string)
    layer: str       # name of the architectural layer
    component: str   # the component / file / module touched
    verification: str  # how this layer's exercise is verified
    status: str      # "pending" | "exercised"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WSViolation:
    path: str
    layer_index: str  # "" for section-level errors
    kind: str         # "missing-section" | "empty-table" | "missing-verification" |
                      # "invalid-status" | "format" | "non-exercised-pre-finish"
    severity: str     # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    walking_skeleton_enabled: bool = False
    rows: list[LayerRow] = field(default_factory=list)
    violations: list[WSViolation] = field(default_factory=list)
    carry_over_exempt: bool = False

    def to_dict(self) -> dict:
        return {
            "walking_skeleton_enabled": self.walking_skeleton_enabled,
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
    return mtime_date < _WS_1_RELEASE_DATE


def _cell_is_empty(cell: str) -> bool:
    return cell.strip().lower() in _EMPTY_SENTINELS


def audit_brief_file(
    brief_path: Path,
    strict_pre_finish: bool = False,
    skip_if_carry_over: bool = True,
) -> AuditResult:
    """Audit a mission-brief.json against WS-1."""
    result = AuditResult()

    if not brief_path.exists():
        return result

    if skip_if_carry_over and _slice_is_carry_over(brief_path):
        result.carry_over_exempt = True
        return result

    try:
        data = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message=f"mission-brief.json is not readable/valid JSON: {exc}",
        ))
        return result
    if not isinstance(data, dict):
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message="mission-brief.json top level is not a JSON object.",
        ))
        return result

    variants = data.get("variants") if isinstance(data.get("variants"), dict) else {}
    enabled = bool(variants.get("walking_skeleton", False))
    result.walking_skeleton_enabled = enabled

    if not enabled:
        return result  # default-off; nothing else to check

    layers = data.get("architectural_layers")
    if layers is None:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="missing-section",
            severity="Important",
            message=(
                "`variants.walking_skeleton` is true but no "
                "`architectural_layers` array was found. Per WS-1, when "
                "walking-skeleton is enabled the brief must list every "
                "architectural layer the slice touches end-to-end."
            ),
        ))
        return result

    if not isinstance(layers, list):
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message="`architectural_layers` is not a JSON array.",
        ))
        return result

    if not layers:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="empty-table",
            severity="Important",
            message=(
                "`architectural_layers` has no entries. A walking skeleton "
                "with zero layers is meaningless — that's a standard slice. "
                "Per WS-1, list every architectural layer the slice touches."
            ),
        ))
        return result

    for idx, entry in enumerate(layers, start=1):
        index_cell = str(idx)
        if not isinstance(entry, dict):
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell, kind="format",
                severity="Important",
                message=f"layer {idx} is not a JSON object.",
            ))
            continue

        layer = str(entry.get("layer", "")).strip()
        component = str(entry.get("component", "")).strip()
        verification = str(entry.get("verification", "")).strip()
        status_raw = str(entry.get("status", "")).strip()

        if _cell_is_empty(index_cell) and _cell_is_empty(layer):
            continue  # blank entry — skip silently

        if _cell_is_empty(verification):
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell,
                kind="missing-verification", severity="Important",
                message=(
                    f"layer {idx} ('{layer}'): `verification` is empty. Per "
                    f"WS-1, every layer must declare HOW its exercise is "
                    f"verified at runtime."
                ),
            ))
            continue

        status = status_raw.lower().strip()
        if status not in _ALLOWED_STATUSES:
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell,
                kind="invalid-status", severity="Important",
                message=(
                    f"layer {idx} ('{layer}'): status '{status_raw}' not in "
                    f"{sorted(_ALLOWED_STATUSES)}."
                ),
            ))
            continue

        result.rows.append(LayerRow(
            index=index_cell, layer=layer, component=component,
            verification=verification, status=status,
        ))

    if strict_pre_finish:
        for row in result.rows:
            if row.status != "exercised":
                result.violations.append(WSViolation(
                    path=str(brief_path), layer_index=row.index,
                    kind="non-exercised-pre-finish", severity="Important",
                    message=(
                        f"layer '{row.layer}' status is {row.status}; "
                        f"--strict-pre-finish requires exercised. The "
                        f"walking-skeleton hasn't actually reached this layer "
                        f"yet — fix the implementation or remove "
                        f"--strict-pre-finish (only used at /validate-slice "
                        f"WS-1 gate)."
                    ),
                ))

    return result


def _format_human(result: AuditResult) -> str:
    if result.carry_over_exempt:
        return (
            "Walking-skeleton audit: slice is carry-over exempt "
            "(mission-brief.json predates WS-1 release).\n"
        )
    if not result.walking_skeleton_enabled:
        return (
            "Walking-skeleton audit: not enabled "
            "(`variants.walking_skeleton` absent or false).\n"
        )

    if not result.violations:
        by_status = {
            s: sum(1 for r in result.rows if r.status == s)
            for s in _ALLOWED_STATUSES
        }
        return (
            f"Walking-skeleton audit: clean. {len(result.rows)} layer(s) — "
            f"exercised={by_status['exercised']}, "
            f"pending={by_status['pending']}.\n"
        )

    out: list[str] = [
        f"{len(result.violations)} walking-skeleton violation(s):\n\n"
    ]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind}) "
            f"{f'layer #{v.layer_index}' if v.layer_index else ''}\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="walking_skeleton_audit",
        description="WS-1 walking-skeleton slice variant audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds mission-brief.json inside) OR a mission-brief.json file",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--strict-pre-finish", action="store_true",
        help="Refuse non-exercised layers (use at /validate-slice WS-1 gate)",
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
