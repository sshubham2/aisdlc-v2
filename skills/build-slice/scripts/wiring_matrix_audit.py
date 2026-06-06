"""Wiring matrix audit (WIRE-1) — v2 JSON.

Reads a slice's ``design.json`` ``wiring_matrix`` array and validates that every
new module declared in the slice has either:
  (a) a consumer entry point AND a consumer test, OR
  (b) an explicit exemption with rationale

Per WIRE-1 (methodology-changelog.md v0.9.0). The rule's purpose: prevent
dead-modules-with-green-tests by requiring consumer-side demand for every
producer (Freeman & Pryce, GOOS — "the consumer demand precedes the producer").

**v2 change from v1.** v1 parsed a markdown table under a ``## Wiring matrix``
heading in ``design.md`` and did markdown-table format validation (heading
present, header + separator + 4-cell rows). v2's design artifact is
``design.json`` with a structured ``wiring_matrix`` array — each entry is a JSON
object, so the markdown-table format checks (heading-found, separator-row,
column-count) DISSOLVE. What survives is the SEMANTIC rule: every row needs a
``module`` plus either a consumer (``consumer_entrypoint`` + ``consumer_test``)
or an ``exemption`` whose text carries ``rationale:``.

v2 ``wiring_matrix`` entry shape (schema by example
``skills/design-slice/examples/design.json``)::

    {
      "module": "presence.ts",                       (required, non-empty)
      "consumer_entrypoint": "doc-view subscribes",   (consumer half)
      "consumer_test": "tests/presence.test.ts",      (consumer half)
      "exemption": "internal helper — rationale: ..." (optional escape)
    }

A row is clean iff: ``module`` is non-empty AND
(both consumer cells non-empty) OR (``exemption`` present + contains
``rationale:``).

NFR-1 carry-over (v1) is GONE: v2 mtime carry-over keyed on a ``mission-brief.md``
file that no longer exists (it is ``mission-brief.json``) and a fixed v1 release
date; for a fresh v2 plugin every slice post-dates the rule, so the exemption is
inapplicable. The ``--no-carry-over`` flag is accepted (no-op) for CLI
compatibility with the v1 invocation string.

Usage:
    python wiring_matrix_audit.py <slice-folder>
    python wiring_matrix_audit.py <design.json>
    python wiring_matrix_audit.py --json <slice-folder>

Exit codes:
    0  clean
    1  violations
    2  usage error / unrecoverable failure
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402

# Sentinel cell values treated as empty.
_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})

# Substring required in an exemption value to qualify as a real exemption.
_RATIONALE_MARKER = "rationale:"


@dataclass(frozen=True)
class WireViolation:
    """A finding emitted by the audit."""
    path: str        # design.json path
    row_index: int   # 1-based entry index; 0 if file-level
    kind: str        # "no-matrix" | "format" | "missing-cells" | "missing-rationale"
    severity: str    # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _cell_is_empty(value: object) -> bool:
    """Is this value empty (per the 'n/a' conventions)?"""
    return str(value or "").strip().lower() in _EMPTY_SENTINELS


def _exemption_has_rationale(value: object) -> bool:
    """Does this exemption value contain the required 'rationale:' marker?"""
    return _RATIONALE_MARKER in str(value or "").lower()


def audit_design_file(design_path: Path) -> list[WireViolation]:
    """Audit a single design.json against WIRE-1 semantic rules."""
    if not design_path.exists():
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="no-matrix", severity="Important",
            message=f"design.json not found: {design_path}",
        )]

    try:
        text = design_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError) as exc:
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="format", severity="Important",
            message=f"cannot read design.json: {exc}",
        )]
    except json.JSONDecodeError as exc:
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="format", severity="Important",
            message=f"design.json is not valid JSON: {exc}",
        )]

    if not isinstance(data, dict):
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="format", severity="Important",
            message="design.json top-level is not a JSON object.",
        )]

    matrix = data.get("wiring_matrix")
    if matrix is None:
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="no-matrix", severity="Important",
            message=(
                "no `wiring_matrix` array in design.json. Per WIRE-1, every "
                "slice's design.json must include a wiring matrix (may be an "
                "empty array when the slice adds no new modules)."
            ),
        )]
    if not isinstance(matrix, list):
        return [WireViolation(
            path=str(design_path), row_index=0,
            kind="format", severity="Important",
            message="`wiring_matrix` is not a JSON array.",
        )]

    violations: list[WireViolation] = []
    for idx, entry in enumerate(matrix, start=1):
        if not isinstance(entry, dict):
            violations.append(WireViolation(
                path=str(design_path), row_index=idx,
                kind="format", severity="Important",
                message=f"wiring_matrix[{idx - 1}] is not a JSON object.",
            ))
            continue

        module = entry.get("module")
        entry_point = entry.get("consumer_entrypoint")
        consumer_test = entry.get("consumer_test")
        exemption = entry.get("exemption")

        if _cell_is_empty(module):
            violations.append(WireViolation(
                path=str(design_path), row_index=idx,
                kind="missing-cells", severity="Important",
                message=f"row {idx}: `module` is empty.",
            ))
            continue

        has_consumer = not _cell_is_empty(entry_point) and not _cell_is_empty(consumer_test)
        has_exemption = not _cell_is_empty(exemption)

        if not has_consumer and not has_exemption:
            violations.append(WireViolation(
                path=str(design_path), row_index=idx,
                kind="missing-cells", severity="Important",
                message=(
                    f"row {idx} ('{module}'): missing `consumer_entrypoint` or "
                    f"`consumer_test`. Either both must be filled, or carry an "
                    f"`exemption` with rationale (e.g. 'internal helper, no "
                    f"consumer demanded — rationale: ...')."
                ),
            ))
            continue

        if has_exemption and not _exemption_has_rationale(exemption):
            violations.append(WireViolation(
                path=str(design_path), row_index=idx,
                kind="missing-rationale", severity="Important",
                message=(
                    f"row {idx} ('{module}'): `exemption` present but missing "
                    f"'rationale:' substring. Per WIRE-1, exemptions must include "
                    f"explicit rationale."
                ),
            ))

    return violations


def _format_human(violations: list[WireViolation]) -> str:
    if not violations:
        return "No wiring matrix violations.\n"
    lines: list[str] = [f"{len(violations)} wiring matrix findings:\n\n"]
    for v in violations:
        lines.append(
            f"  [{v.severity}] {v.path} row {v.row_index} ({v.kind})\n"
            f"    {v.message}\n\n"
        )
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="wiring_matrix_audit",
        description="WIRE-1 wiring matrix audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Path to a slice folder (auto-finds design.json inside) OR a design.json file directly",
    )
    parser.add_argument("--json", action="store_true", help="Output findings as JSON")
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted for v1 CLI compatibility; no-op in v2 (no mtime carry-over).",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    design_path = target / "design.json" if target.is_dir() else target

    violations = audit_design_file(design_path)

    if args.json:
        sys.stdout.write(json.dumps({
            "violations": [v.to_dict() for v in violations],
            "summary": {"total": len(violations)},
        }, indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(violations))

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
