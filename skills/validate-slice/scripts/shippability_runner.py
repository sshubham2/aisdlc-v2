"""SRSC-1 — the canonical /validate-slice Step-6 shippability-catalog runner — v2 JSON.

The single canonical executor for the shippability regression catalog: reads
`<vault>/shippability.json`, and for every `rows[].machine_cmd` runs each
`;`-separated segment from the repo root, normalizing the leading interpreter
token to the live interpreter. Every segment of a row must exit 0 for the row to
PASS; the first failing segment fails the row but the run continues so the full
regression picture is reported.

**Three-valued row verdict (SC-021 / ADR-021).** A row whose cited `tests/...py`
test file(s) are ABSENT from the current checkout (a sibling slice's not-yet-merged
repro under parallel slices) is recorded as a distinct third verdict ``ABSENT`` —
NOT a regression. ABSENT is decided PRE-EXECUTION by a filesystem existence check
on the row's own cited test path(s) (reusing
``shippability_path_audit._extract_test_tokens``), NOT by the pytest exit code:
pytest exit 4 conflates absent-file / phantom-citation / CLI-usage-error, so a
present-file phantom citation must stay a FAIL. A row with >=1 present test token,
or with no extractable test token (e.g. a ``python -c`` row), is executed normally
so a present-but-failing test still FAILs. ABSENT rows are counted (``RunResult.absent``)
and surfaced distinctly — never silently dropped, never folded into PASS — and never
contribute to the failing exit code.

It does NOT re-derive the split/strip — it REUSES
`shippability_decoupling_audit._segments()` (the SCMD-1 canonical per-`;`-segment
backtick+whitespace strip) plus that audit's JSON catalog-row /
machine_cmd-field parsing (`_catalog_rows`, `_machine_cmd_cell`).

Pre-condition (documented, NOT re-validated here): Step 6 runs the SCMD-1
pre-catalog gate (`shippability_decoupling_audit.py`) as a hard STOP BEFORE this
runner. The runner trusts that gate for cell well-formedness and focuses solely
on correct per-segment execution.

**v2 change from v1.** Catalog is JSON (`rows[].machine_cmd`), not a markdown
table. The interpreter-normalization, per-segment execution, exit-code +
PASS/FAIL semantics are preserved verbatim. The canonical machine_cmd is now
interpreter-anchored (`<interp> -m pytest tests/...`); a bare `pytest`
console-script is rejected by SCMD-1 (ADR-035, slice-046) before it can reach
this runner, so `_normalize_interp` only ever rewrites a leading interpreter
token to the resolved interpreter (`sys.executable`).

Usage:
    python shippability_runner.py <vault>/shippability.json
    python shippability_runner.py <vault>/shippability.json --json

Exit codes:
    0  every data row PASSED or ABSENT (no regression; ABSENT = test not on this checkout)
    1  >=1 data row FAILED (a regression — blocks /reflect)
    2  usage error (catalog missing/unreadable, or not valid JSON)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SCRIPTS = pathlib.Path(__file__).resolve().parent     # sibling-script imports
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from shippability_decoupling_audit import _catalog_rows, _machine_cmd_cell  # canonical, NOT re-derived
from shippability_path_audit import _find_repo_root
# slice-047/ADR-038: the SHARED fail-closed execution core. run_verification owns
# what run_catalog used to inline (interp-normalization B1, the ABSENT pre-check
# m2/ADR-021, canonical _segments M3.2, every error path slice-011) and returns a
# three-valued ExecVerdict; run_catalog now just maps that verdict to a RowResult.
from scripts.lib.verification_core import run_verification


@dataclass(frozen=True)
class RowResult:
    row: str
    status: str          # "PASS" | "FAIL" | "ABSENT"
    index: int
    detail: str = ""

    def to_dict(self) -> dict:
        return {"row": self.row, "status": self.status,
                "index": self.index, "detail": self.detail}


@dataclass
class RunResult:
    rows_run: int = 0
    passed: int = 0
    failed: int = 0
    absent: int = 0       # SC-021: rows whose cited test file(s) are not on this checkout
    rows: list[RowResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows_run": self.rows_run,
            "passed": self.passed,
            "failed": self.failed,
            "absent": self.absent,
            "rows": [r.to_dict() for r in self.rows],
            "summary": {"failed_count": self.failed, "absent_count": self.absent},
        }


def run_catalog(catalog_path: Path, repo_root: Path | None = None,
                timeout: float | None = None) -> RunResult:
    """Execute every catalog row's machine_cmd, per-segment.

    Each row's command is split via the CANONICAL `_segments()` (split on `;`,
    then strip backticks + ws PER segment). Every segment of a row must exit 0
    for the row to PASS; the first failing segment fails the row but the run
    continues so the full regression picture is reported.

    SC-021/ADR-021: a row whose cited `tests/...py` token(s) are ALL absent on
    `repo_root` (the current checkout) is classified ABSENT pre-execution and is
    NOT run — a sibling slice's not-yet-merged repro is unobservable here, not a
    regression. A row with a present token, or no extractable test token, runs
    normally so a present-but-failing test still FAILs."""
    result = RunResult()
    if repo_root is None:
        repo_root = _find_repo_root(catalog_path)

    for index, row, row_id in _catalog_rows(catalog_path):
        result.rows_run += 1
        cell = _machine_cmd_cell(row)
        if not cell:
            # SCMD-1 pre-catalog gate should have STOPped before us; defensively
            # record rather than crash. (Guarded HERE, before the shared core, so
            # an empty cell is a FAIL — never silently PASS through run_verification.)
            result.failed += 1
            result.rows.append(RowResult(
                row_id, "FAIL", index,
                "no machine_cmd field (SCMD-1 pre-catalog gate should have "
                "caught this — did Step 6 run shippability_decoupling_audit?)"))
            continue

        # slice-047/ADR-038: delegate the per-segment execution to the SHARED core.
        # It owns the SC-021/ADR-021 ABSENT pre-check (cited tokens all-absent on
        # repo_root -> ABSENT, by FILE existence, never the pytest exit code), the
        # interp-normalization, the canonical _segments split, and every error
        # path (shlex ValueError / OSError / TimeoutExpired / not-runnable / non-
        # zero exit -> FAIL, never bubbling as an exit-2 catalog abort). run_catalog
        # maps the three-valued verdict to a RowResult + counts, UNCHANGED.
        verdict = run_verification(cell, repo_root, timeout=timeout)
        if verdict.status == "ABSENT":
            result.absent += 1
            result.rows.append(RowResult(row_id, "ABSENT", index, verdict.reason))
        elif verdict.status == "PASS":
            result.passed += 1
            result.rows.append(RowResult(row_id, "PASS", index))
        else:  # FAIL (any subkind)
            result.failed += 1
            result.rows.append(RowResult(row_id, "FAIL", index, verdict.reason))

    return result


def _format_human(r: RunResult) -> str:
    head = (f"Shippability catalog run: {r.rows_run} row(s), "
            f"{r.passed} PASS, {r.failed} FAIL, {r.absent} ABSENT\n")
    out = [head]
    # SC-021: surface ABSENT rows distinctly (never silently swallowed) BEFORE the
    # early-return, so a 0-FAIL run still reports a test that was not on this
    # checkout — information, not a regression.
    if r.absent:
        out.append("\nABSENT (test not on this checkout — not a regression):\n")
        for row in r.rows:
            if row.status == "ABSENT":
                out.append(f"  {row.row} (shippability.json rows[{row.index}])\n"
                           f"    {row.detail}\n")
    if not r.failed:
        return "".join(out)
    out.append("\nFAILED:\n")
    for row in r.rows:
        if row.status == "FAIL":
            out.append(f"  {row.row} (shippability.json rows[{row.index}])\n"
                       f"    {row.detail}\n\n")
    out.append("Cannot proceed to /reflect. Fix the regression, OR get user "
               "approval to defer the fix to a new slice.\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="shippability_runner",
        description="SRSC-1 canonical Step-6 shippability-catalog runner "
                    "(v2 JSON; per-;-segment backtick-strip; reuses SCMD-1 _segments())",
    )
    parser.add_argument("catalog", type=Path, nargs="?",
                        default=VAULT_ROOT / "shippability.json",
                        help="Path to shippability.json (default: <vault>/shippability.json)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Per-segment timeout in seconds (default: none)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    catalog_path: Path = args.catalog
    if not catalog_path.is_file():
        sys.stderr.write(f"usage error: catalog not found: {catalog_path}\n")
        return 2

    try:
        result = run_catalog(catalog_path, timeout=args.timeout)
    except OSError as exc:
        sys.stderr.write(f"usage error: cannot read catalog: {exc}\n")
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"usage error: catalog is not valid shippability.json: {exc}\n")
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_format_human(result), end="")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
