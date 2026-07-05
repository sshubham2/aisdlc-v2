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
# slice-064/ADR-061: the batch-and-scatter core -- run the MERGEABLE plain-pytest
# rows in ONE pytest session (fixtures boot once), attribute back per-row, fall
# back to the UNCHANGED run_verification for any row the merge cannot attribute.
import catalog_merge


@dataclass(frozen=True)
class RowResult:
    row: str
    status: str          # "PASS" | "FAIL" | "ABSENT"
    index: int
    detail: str = ""
    subkind: str = ""    # slice-064: WHY (e.g. "timeout", "merged", "fallback"); additive -- existing JSON consumers ignore unknown keys

    def to_dict(self) -> dict:
        return {"row": self.row, "status": self.status,
                "index": self.index, "detail": self.detail,
                "subkind": self.subkind}


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


def _serial_verdict(cell: str, repo_root: Path, timeout: float | None):
    """Run ONE row via the UNCHANGED shared core (below-normal priority) and return
    (status, detail, subkind). This is the exact-verdict path: every STANDALONE row,
    every fallback re-run, and the whole legacy behavior when --no-merge is set."""
    v = run_verification(cell, repo_root, timeout=timeout, below_normal_priority=True)
    return v.status, v.reason, v.subkind


def run_catalog(catalog_path: Path, repo_root: Path | None = None,
                timeout: float | None = None, *,
                merge: bool = True, session_timeout: float | None = None) -> RunResult:
    """Execute every catalog row and report a three-valued PASS/FAIL/ABSENT verdict.

    slice-064/ADR-061: the MERGEABLE plain-pytest rows run in ONE pytest session
    (session fixtures boot once) instead of one cold-start subprocess each, and
    results are attributed back per-row by JUnit classname+name. Everything else is
    UNCHANGED: a non-pytest / multi-segment / flagged / `isolate:true` / not-all-
    present row runs STANDALONE through `run_verification` (which still owns the
    SC-021/ADR-021 ABSENT pre-check + every error path, slice-011). The merged path
    is an optimization over the trusted serial run: a merged session that times out,
    exits outside {0,1}, or yields no usable JUnit falls back to a whole-batch serial
    re-run, and any row the merge cannot attribute (0 matched nodes) re-runs
    standalone -- so the merge NEVER decides a verdict it cannot confidently attribute
    (must_not_defer #1: no silent PASS). `merge=False` (--no-merge) forces the exact
    legacy per-row path (the AC1 differential oracle + the session-unsafe escape).
    `timeout` bounds each per-row/segment run (unchanged); `session_timeout` bounds
    the merged session (caller-supplied -- see main(); integration_health_gate passes
    NEITHER so both stay None, preserving its m5 behavior byte-identically)."""
    result = RunResult()
    if repo_root is None:
        repo_root = _find_repo_root(catalog_path)

    # slice-064 / code-review CR1: bound EVERY serial run (standalone + fallback) by an
    # EFFECTIVE per-row timeout so a caller supplying ONLY --session-timeout (the
    # /validate-slice Step-6 shape) still bounds a hung row -- AC3 must hold through the
    # DEPLOYED wiring, not only when both --timeout and --session-timeout are passed.
    # integration_health_gate passes NEITHER (both None) -> eff_timeout stays None ->
    # unbounded, preserving its m5 (no false-REFUSE of a slow-but-passing merge).
    eff_timeout = timeout if timeout is not None else session_timeout

    # 1. Materialize rows in catalog order + classify each (index is the unique key).
    ordered: list[tuple[int, str]] = []              # (index, row_id), catalog order
    cells: dict[int, str] = {}
    verdicts: dict[int, tuple[str, str, str]] = {}   # index -> (status, detail, subkind)
    merge_specs: list = []                            # catalog_merge.RowSpec for the merged batch
    for index, row, row_id in _catalog_rows(catalog_path):
        ordered.append((index, row_id))
        cell = _machine_cmd_cell(row)
        cells[index] = cell
        if not cell:
            # SCMD-1 pre-catalog gate should have STOPped before us; defensively
            # record rather than crash (an empty cell is a FAIL, never a silent PASS).
            verdicts[index] = ("FAIL", "no machine_cmd field (SCMD-1 pre-catalog gate "
                               "should have caught this -- did Step 6 run "
                               "shippability_decoupling_audit?)", "empty-cmd")
            continue
        isolate = bool(row.get("isolate")) if isinstance(row, dict) else False
        kind, pairs = catalog_merge.classify(cell, repo_root, isolate=isolate)
        if merge and kind == "mergeable":
            merge_specs.append(catalog_merge.RowSpec(
                row_id=row_id, index=index, command=cell, pairs=pairs))
        else:
            verdicts[index] = _serial_verdict(cell, repo_root, eff_timeout)

    # 2. Run the MERGEABLE rows in ONE session; fall back to the exact serial path
    #    for anything the merge cannot confidently attribute.
    if merge and merge_specs:
        outcome = catalog_merge.run_merged_batch(
            merge_specs, repo_root, below_normal=True, session_timeout=session_timeout)
        if outcome.whole_batch_fallback:
            # m3: loud diagnostic -- the whole batch reverts to the trusted serial path.
            sys.stderr.write(f"shippability_runner: merged session fell back to "
                             f"per-row serial: {outcome.reason}\n")
            for s in merge_specs:
                verdicts[s.index] = _serial_verdict(cells[s.index], repo_root, eff_timeout)
        else:
            for s in merge_specs:
                if s.index in outcome.unresolved:
                    # this row matched 0 nodes in the merged run -> recover its exact
                    # verdict via the serial path (never a silent PASS).
                    sys.stderr.write(f"shippability_runner: row '{s.row_id}' "
                                     f"unattributable in merged run -> serial re-run\n")
                    verdicts[s.index] = _serial_verdict(cells[s.index], repo_root, eff_timeout)
                else:
                    status, detail = outcome.per_row[s.index]
                    verdicts[s.index] = (status, detail, "merged")

    # 3. Reassemble RowResults in CATALOG order, each carrying its ORIGINAL index
    #    (M-add-2: integration_health_gate + the REFUSE message read rows[] + index).
    for index, row_id in ordered:
        status, detail, subkind = verdicts[index]
        result.rows_run += 1
        if status == "ABSENT":
            result.absent += 1
        elif status == "PASS":
            result.passed += 1
        else:
            result.failed += 1
        result.rows.append(RowResult(row_id, status, index, detail, subkind))

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
                        help="Per-segment / per-row timeout in seconds (default: none). Bounds each "
                             "STANDALONE row and each fallback re-run.")
    parser.add_argument("--session-timeout", type=float, default=None,
                        help="slice-064: timeout in seconds for the ONE MERGED pytest session (default: "
                             "none). CALLER-SUPPLIED -- /validate-slice Step 6 passes a generous value "
                             "(AC3); integration_health_gate passes neither timeout so both stay None, "
                             "preserving its m5 (no false-REFUSE of a slow-but-passing merge). A merged "
                             "session that exceeds it falls back to per-row serial (bounded + attributed).")
    parser.add_argument("--no-merge", action="store_true",
                        help="slice-064: disable the merged-session fast path -- run EVERY row via the "
                             "exact per-row serial engine (the pre-064 behavior). The AC1 differential "
                             "oracle, and the escape hatch for a session-order-dependent suite where "
                             "merged verdicts could diverge from serial (ADR-061).")
    parser.add_argument("--repo-root", type=Path, default=None,
                        help="Checkout the catalog rows run against (default: resolved from the "
                             "invocation cwd via _find_repo_root). slice-059: an EXPLICIT target so a "
                             "caller (e.g. the /commit-slice integration-health gate) can point the "
                             "run at a specific worktree instead of ambient cwd. Default None preserves "
                             "the /validate-slice Step-6 behavior exactly.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    catalog_path: Path = args.catalog
    if not catalog_path.is_file():
        sys.stderr.write(f"usage error: catalog not found: {catalog_path}\n")
        return 2

    try:
        result = run_catalog(catalog_path, repo_root=args.repo_root, timeout=args.timeout,
                             merge=not args.no_merge, session_timeout=args.session_timeout)
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
