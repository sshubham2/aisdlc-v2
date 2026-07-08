"""Region-keyed doc-guard for the REALITY-GATES wire in /validate-slice (slice-062 / SC-095 /
ADR-059).

AC4 / M-add-1: /validate-slice must run the declared reality gates against the REAL worktree
checkout with an EXPLICIT --repo-root "$wt" (never ambient cwd -> the false-green the sibling
shippability_runner idiom would cause) AND enforce a FAIL as a hard block (invoked != enforced:
a declared-gate FAIL / malformed manifest makes the aggregate Result FAIL and the fork returns
blocked). A SKILL.md bash gate has no other testable surface, so this pins the wiring TEXT
(slice-059 lesson: the doc-guard checks it is WIRED; the pre_finish/runner tests check it RUNS).
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "skills" / "validate-slice" / "SKILL.md"


def _src() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_validate_slice_invokes_the_reality_gate_runner():
    assert "reality_gate_runner.py" in _src()


def test_reality_gate_wire_passes_repo_root_wt_explicitly():
    src = _src()
    # the exact explicit-checkout invocation (M-add-1): --repo-root "$wt", NOT ambient cwd.
    assert 'reality_gate_runner.py" --repo-root "$wt"' in src


def test_reality_gate_wire_blocks_on_fail():
    src = _src()
    # structural enforcement (the fork cannot AskUserQuestion): a non-zero exit blocks + returns blocked.
    assert "rgc=$?" in src
    assert 'rgc" -eq 0' in src
    assert "return blocked to the main thread" in src
    assert "Result: FAIL" in src


def test_reality_gate_wire_is_in_step_6_before_step_7():
    src = _src()
    idx_wire = src.index("reality_gate_runner.py")
    idx_step7 = src.index("## Step 7")
    idx_catalog = src.index("shippability_runner.py")
    assert idx_catalog < idx_wire < idx_step7   # after the catalog run, still inside Step 6
