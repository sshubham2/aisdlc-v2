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
import shlex
import subprocess
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
from shippability_decoupling_audit import (  # canonical, NOT re-derived
    _catalog_rows,
    _machine_cmd_cell,
    _segments,
)
from shippability_path_audit import _extract_test_tokens, _find_repo_root

# Tokens that introduce the canonical interpreter placeholder. SCMD-1 permits
# `<interp>` (the SKILL.md-prose convention), a bare `python`, or an absolute
# `.../python.exe`. The runner normalizes them to the live interpreter so the
# catalog never embeds a machine-specific path.
_INTERP_TOKENS = frozenset({"<interp>", "python", "python.exe", "python3"})


def _normalize_interp(tokens: list[str]) -> list[str]:
    """Replace a leading interpreter token with the live interpreter.

    `<interp> -m pytest ...` / `python -m pytest ...` /
    `C:/.../python.exe -m pytest ...` all become
    `<sys.executable> -m pytest ...`. A bare `pytest ...` segment (no leading
    interpreter token — the v2 example form) is left as-is and runs via the
    `pytest` console entry point on PATH."""
    if not tokens:
        return tokens
    head = tokens[0]
    is_interp = (
        head in _INTERP_TOKENS
        or head.endswith("python")
        or head.endswith("python.exe")
        or head.endswith("python3")
    )
    if is_interp:
        return [sys.executable, *tokens[1:]]
    return tokens


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
            # record rather than crash.
            result.failed += 1
            result.rows.append(RowResult(
                row_id, "FAIL", index,
                "no machine_cmd field (SCMD-1 pre-catalog gate should have "
                "caught this — did Step 6 run shippability_decoupling_audit?)"))
            continue

        # SC-021: pre-flight ABSENT classification (decided by FILE EXISTENCE,
        # never the pytest exit code — exit 4 is ambiguous; ADR-021). If the row
        # cites >=1 tests/...py token AND every cited token is absent on this
        # checkout, record ABSENT (distinct, counted, not run, not a regression).
        # A row with a present token, or with NO extractable test token (e.g. a
        # `python -c` row), falls through to normal execution so a real failure
        # still FAILs.
        test_tokens = [tok for tok, _sel in _extract_test_tokens(cell)]
        if test_tokens and all(not (repo_root / tok).exists() for tok in test_tokens):
            result.absent += 1
            result.rows.append(RowResult(
                row_id, "ABSENT", index,
                "test file(s) not on this checkout — not a regression "
                "(a sibling slice's not-yet-merged repro): "
                + ", ".join(test_tokens)))
            continue

        row_ok = True
        fail_detail = ""
        for seg in _segments(cell):  # CANONICAL per-;-segment strip
            try:
                argv = _normalize_interp(shlex.split(seg, posix=True))
            except ValueError as exc:
                # A genuinely malformed segment (e.g. an unterminated quote).
                # Fail THIS row (a regression -> exit 1) and keep the run going;
                # NEVER let the ValueError bubble to main()'s handler, which
                # would misreport it as an exit-2 catalog usage-error and abort
                # the whole run (slice-011: the catalog-abort bug). exit 2 stays
                # reserved for a missing/invalid catalog FILE.
                row_ok = False
                fail_detail = f"segment is not a parseable command ({exc}): {seg!r}"
                break
            if not argv:
                continue
            try:
                proc = subprocess.run(
                    argv, cwd=str(repo_root),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",  # BB-25: avoid cp1252 reader-thread UnicodeDecodeError
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                row_ok = False
                fail_detail = f"segment timed out after {timeout}s: {seg!r}"
                break
            except OSError as exc:
                row_ok = False
                fail_detail = f"segment could not be executed ({exc}): {seg!r}"
                break
            if proc.returncode != 0:
                row_ok = False
                tail = (proc.stdout or "")[-500:] + (proc.stderr or "")[-500:]
                fail_detail = (f"segment exited {proc.returncode}: {seg!r}\n"
                               f"{tail.strip()}")
                break  # row already failed; no need to run later segments

        if row_ok:
            result.passed += 1
            result.rows.append(RowResult(row_id, "PASS", index))
        else:
            result.failed += 1
            result.rows.append(RowResult(row_id, "FAIL", index, fail_detail))

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
