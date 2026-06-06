"""SRSC-1 — the canonical /validate-slice Step-6 shippability-catalog runner — v2 JSON.

The single canonical executor for the shippability regression catalog: reads
`<vault>/shippability.json`, and for every `rows[].machine_cmd` runs each
`;`-separated segment from the repo root, normalizing the leading interpreter
token to the live interpreter. Every segment of a row must exit 0 for the row to
PASS; the first failing segment fails the row but the run continues so the full
regression picture is reported.

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
PASS/FAIL semantics are preserved verbatim. v2 machine_cmd may be a bare
`pytest tests/... -q` (the example form); `_normalize_interp` only rewrites a
leading interpreter token, so a bare-`pytest` segment runs pytest directly via
the resolved console entry point on PATH.

Usage:
    python shippability_runner.py <vault>/shippability.json
    python shippability_runner.py <vault>/shippability.json --json

Exit codes:
    0  every data row PASSED (or empty / zero-row catalog)
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
from shippability_path_audit import _find_repo_root

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
    status: str          # "PASS" | "FAIL"
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
    rows: list[RowResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows_run": self.rows_run,
            "passed": self.passed,
            "failed": self.failed,
            "rows": [r.to_dict() for r in self.rows],
            "summary": {"failed_count": self.failed},
        }


def run_catalog(catalog_path: Path, repo_root: Path | None = None,
                timeout: float | None = None) -> RunResult:
    """Execute every catalog row's machine_cmd, per-segment.

    Each row's command is split via the CANONICAL `_segments()` (split on `;`,
    then strip backticks + ws PER segment). Every segment of a row must exit 0
    for the row to PASS; the first failing segment fails the row but the run
    continues so the full regression picture is reported."""
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

        row_ok = True
        fail_detail = ""
        for seg in _segments(cell):  # CANONICAL per-;-segment strip
            argv = _normalize_interp(shlex.split(seg, posix=True))
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
            f"{r.passed} PASS, {r.failed} FAIL\n")
    if not r.failed:
        return head
    out = [head, "\nFAILED:\n"]
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
