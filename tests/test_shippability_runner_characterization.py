"""Characterization suite for shippability_runner.run_catalog (slice-047 / M-add-2).

The slice-047 unification extracts run_catalog's per-segment execution into the
shared scripts.lib.verification_core. The project-frame attack-lens (and
must_not_defer #1) demand the unification NOT weaken the mature reality spine. A
happy-path byte-compare would miss the load-bearing carve-outs, so this suite pins
the FULL PASS/FAIL/ABSENT branch matrix of run_catalog against the rewired core:

  * no-token command, exit 0      -> PASS
  * no-token command, exit != 0   -> FAIL
  * malformed segment (bad quote) -> FAIL (this row), run COMPLETES (slice-011:
                                     never an exit-2 catalog abort)
  * all cited test tokens absent  -> ABSENT (by FILE existence, ADR-021/SC-021)
  * a PRESENT cited token that FAILS -> FAIL, NOT ABSENT (a phantom-present test
                                     must still be a real regression)
  * MIXED present + absent tokens -> RUNs (not short-circuited to ABSENT)

If a future refactor regresses any branch (e.g. mis-classifying a present-failing
test as benign ABSENT), this suite goes red.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
for _p in (REPO_ROOT, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("skills/validate-slice/scripts/shippability_decoupling_audit.py",
      "shippability_decoupling_audit")
_runner = _load("skills/validate-slice/scripts/shippability_runner.py",
                "ai_sdlc_shippability_runner_char")
run_catalog = _runner.run_catalog


def _write_catalog(repo_root: Path, rows: list[dict]) -> Path:
    p = repo_root / "shippability.json"
    p.write_text(json.dumps({"_schema": "aisdlc/shippability@1", "rows": rows},
                            indent=2), encoding="utf-8")
    return p


def _row(rid: str, cmd: str) -> dict:
    return {"id": rid, "slice": "char", "kind": "test",
            "description": rid, "machine_cmd": cmd, "added": "2026-01-01T00:00:00Z"}


def test_run_catalog_full_branch_matrix(tmp_path: Path):
    repo = tmp_path
    (repo / "tests").mkdir()
    (repo / "tests" / "present_fail.py").write_text(
        "def test_x():\n    assert False\n", encoding="utf-8")
    (repo / "tests" / "present_pass.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")

    catalog = _write_catalog(repo, [
        _row("PASS-NOTOKEN", 'python -c "raise SystemExit(0)"'),
        _row("FAIL-NONZERO", 'python -c "raise SystemExit(1)"'),
        _row("FAIL-MALFORMED", 'python -c "unterminated'),
        _row("ABSENT-ALL", "python -m pytest tests/absent_only.py -q"),
        _row("FAIL-PRESENT-PHANTOM", "python -m pytest tests/present_fail.py -q"),
        _row("RUN-MIXED", "python -m pytest tests/present_pass.py tests/absent_mixed.py -q"),
    ])

    result = run_catalog(catalog, repo_root=repo)
    by_id = {r.row: r.status for r in result.rows}

    # every row produced a result -- the malformed row did NOT abort the run (slice-011)
    assert result.rows_run == 6
    assert len(result.rows) == 6
    assert set(by_id) == {"PASS-NOTOKEN", "FAIL-NONZERO", "FAIL-MALFORMED",
                          "ABSENT-ALL", "FAIL-PRESENT-PHANTOM", "RUN-MIXED"}

    # per-branch verdicts (the spine's classification, preserved)
    assert by_id["PASS-NOTOKEN"] == "PASS"
    assert by_id["FAIL-NONZERO"] == "FAIL"
    assert by_id["FAIL-MALFORMED"] == "FAIL"          # parsed-bad -> row FAIL, not exit-2
    assert by_id["ABSENT-ALL"] == "ABSENT"            # all tokens absent -> ABSENT carve-out
    assert by_id["FAIL-PRESENT-PHANTOM"] == "FAIL"    # present token that fails -> real regression
    assert by_id["RUN-MIXED"] != "ABSENT"             # one present token -> RUNs, not short-circuited

    # the count aggregation is exact + nothing is lost
    assert result.passed == 1
    assert result.absent == 1
    assert result.failed == 4
    assert result.passed + result.failed + result.absent == result.rows_run
