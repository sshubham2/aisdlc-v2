"""
Bug (SC-021 / slice-033): the shippability regression gate
(skills/validate-slice/scripts/shippability_runner.py, run at /validate-slice
Step 6) treats a catalog row whose machine_cmd targets a tests/bugs/*.py test
file that is ABSENT from the current checkout as a real regression.

Under parallel slices the shared <vault>/shippability.json carries rows whose
repro tests live only in a SIBLING slice's not-yet-merged worktree. Run from any
other worktree, that row's `python -m pytest tests/bugs/<sibling>.py` exits
non-zero (file-not-found; pytest exit 4); run_catalog counts it in
`result.failed`, so the whole catalog FAILs (CLI exit 1) -- a FALSE regression
that blocks a clean slice (observed live at slice-011: 12 rows / 10 PASS /
2 FAIL, the 2 FAILs being other slices' uncommitted repro tests).

Expected (post-fix): an absent-test row is reported DISTINCTLY (not counted as a
regression) -- result.failed stays 0 when the only "failure" is a test file that
is not on this checkout -- while a row whose test file IS present and fails still
counts as a regression (scoping must NEVER mask a real regression).

Actual (today): the absent-test row is counted as result.failed == 1 -> the gate
reports a regression that does not exist.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
for _p in (REPO_ROOT, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec so @dataclass can resolve __module__
    spec.loader.exec_module(mod)
    return mod


# Load the audit FIRST under its canonical name so the runner's
# `from shippability_decoupling_audit import ...` reuses this same module.
_load("skills/validate-slice/scripts/shippability_decoupling_audit.py",
      "shippability_decoupling_audit")
runner_mod = _load("skills/validate-slice/scripts/shippability_runner.py",
                   "ai_sdlc_shippability_runner")


def _write_catalog(repo_root: Path, rows: list[dict]) -> Path:
    """Write a shippability.json under an isolated repo_root and return its path."""
    catalog = {"_schema": "aisdlc/shippability@1", "rows": rows}
    p = repo_root / "shippability.json"
    p.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    return p


def _write_test_file(repo_root: Path, rel: str, passes: bool) -> None:
    """Create a trivial standalone pytest file (collectable with no conftest)."""
    f = repo_root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    body = "def test_x():\n    assert True\n" if passes else "def test_x():\n    assert False\n"
    f.write_text(body, encoding="utf-8")


# ── the SC-021 repro: an absent-test row must NOT be a regression ─────────────

def test_absent_test_row_not_counted_as_regression(tmp_path: Path):
    """REPRO (RED today): a catalog row whose tests/bugs/*.py file is ABSENT from
    the checkout must not be counted as a regression. A sibling PRESENT-and-passing
    row must still PASS (so the fix scopes by FILE EXISTENCE, not by dropping every
    pytest-shaped row)."""
    repo = tmp_path
    _write_test_file(repo, "tests/bugs/test_present_pass.py", passes=True)
    catalog = _write_catalog(repo, [
        {  # the sibling slice's not-yet-merged repro -- absent on THIS checkout
            "id": "SHIP-ABSENT",
            "slice": "sibling-slice",
            "kind": "test",
            "description": "a sibling slice's repro test, not on this checkout",
            "machine_cmd": "python -m pytest tests/bugs/test_absent_sibling.py -q",
            "added": "2026-01-01T00:00:00Z",
        },
        {  # a present, passing row -- must still run and PASS
            "id": "SHIP-PRESENT",
            "slice": "this-slice",
            "kind": "test",
            "description": "a present, passing repro test",
            "machine_cmd": "python -m pytest tests/bugs/test_present_pass.py -q",
            "added": "2026-01-01T00:00:00Z",
        },
    ])

    result = runner_mod.run_catalog(catalog, repo_root=repo)

    assert result.failed == 0, (
        "absent-test row was counted as a regression (false regression): "
        + "; ".join(f"{r.row}:{r.detail}" for r in result.rows if r.status == "FAIL")
    )
    assert result.passed >= 1, (
        "the present, passing row must still run and PASS -- the fix must scope by "
        "file existence, not drop every pytest-shaped row"
    )


# ── guard: a PRESENT test that fails is still a real regression ───────────────

def test_present_failing_test_still_regresses(tmp_path: Path):
    """GUARD (GREEN today and post-fix): a row whose test file IS present and FAILS
    must still count as a regression -- scoping out absent tests must never mask a
    genuine failure."""
    repo = tmp_path
    _write_test_file(repo, "tests/bugs/test_present_fail.py", passes=False)
    catalog = _write_catalog(repo, [
        {
            "id": "SHIP-REGRESSION",
            "slice": "this-slice",
            "kind": "test",
            "description": "a present test that genuinely fails",
            "machine_cmd": "python -m pytest tests/bugs/test_present_fail.py -q",
            "added": "2026-01-01T00:00:00Z",
        },
    ])

    result = runner_mod.run_catalog(catalog, repo_root=repo)

    assert result.failed >= 1, (
        "a present-but-failing test must still FAIL the gate (real regression must "
        "not be masked by absent-test scoping)"
    )


# ── AC3 (C2): the absent row must be surfaced DISTINCTLY, never silently dropped ──

def test_absent_row_surfaced_distinctly_not_silently_dropped(tmp_path: Path):
    """AC3 (RED today): an absent-test row must be a DISTINCT, counted, non-FAIL
    status -- not silently dropped, not folded into PASS, not counted in failed.

    This pins the three-valued fix against a BAD silent-drop fix (a `continue`
    before appending any RowResult, no ABSENT status, no RunResult.absent), which
    would ALSO make test_absent_test_row_not_counted_as_regression pass (failed==0)
    -- exactly the SC-058 drop-before-run filter the design rejects. Without this
    test, AC3's 'no silent swallow' has no guard.
    """
    repo = tmp_path
    _write_test_file(repo, "tests/bugs/test_present_pass.py", passes=True)
    catalog = _write_catalog(repo, [
        {
            "id": "SHIP-ABSENT",
            "slice": "sibling-slice",
            "kind": "test",
            "description": "a sibling slice's repro test, not on this checkout",
            "machine_cmd": "python -m pytest tests/bugs/test_absent_sibling.py -q",
            "added": "2026-01-01T00:00:00Z",
        },
        {
            "id": "SHIP-PRESENT",
            "slice": "this-slice",
            "kind": "test",
            "description": "a present, passing repro test",
            "machine_cmd": "python -m pytest tests/bugs/test_present_pass.py -q",
            "added": "2026-01-01T00:00:00Z",
        },
    ])

    result = runner_mod.run_catalog(catalog, repo_root=repo)

    # distinct + counted: exactly one ABSENT row, tallied separately, never dropped
    assert result.absent == 1, "the absent-test row must be counted in a distinct `absent` tally"
    absent_rows = [r for r in result.rows if r.status == "ABSENT"]
    assert len(absent_rows) == 1, "exactly one row must carry status ABSENT (not silently dropped, not folded into PASS)"
    assert absent_rows[0].status not in ("PASS", "FAIL")
    # never folded into the failure verdict (AC1) nor the pass count
    assert result.failed == 0, "an absent row must not count as a regression"
    # the JSON contract surfaces it too -- no silent swallow for downstream consumers (AC3)
    d = result.to_dict()
    assert d.get("absent") == 1, "to_dict() must surface the absent count"
    assert any(r.get("status") == "ABSENT" for r in d.get("rows", [])), \
        "to_dict() rows[] must include the ABSENT row"
