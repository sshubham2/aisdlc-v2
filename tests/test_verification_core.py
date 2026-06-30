"""Unit coverage for the shared verification execution core (slice-047 / ADR-038).

AC1: run_verification is a TOTAL three-valued (PASS/FAIL/ABSENT) fail-closed engine.
AC5: a portable interpreter-anchored command genuinely runs and is recorded as a
     real PASS (reality contact = True).
Also pins the m2/M2 collision resolution: ABSENT is decided STRICTLY pre-execution
by cited-test-file existence, while a not-runnable command (FileNotFoundError) is a
FAIL tagged `not-runnable` -- the two are never conflated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plugin root -> scripts.lib

from scripts.lib.verification_core import (
    ExecVerdict,
    _extract_test_tokens,
    _normalize_interp,
    _segments,
    run_verification,
)


# ── ExecVerdict: illegal states unrepresentable (meta-Critic backstop) ──
def test_execverdict_rejects_a_fourth_status():
    ExecVerdict("PASS"); ExecVerdict("FAIL"); ExecVerdict("ABSENT")  # the only three
    import pytest
    with pytest.raises(ValueError):
        ExecVerdict("MAYBE")  # a 4th status can never be constructed


# ── relocated helpers behave (single source of truth) ──
def test_normalize_interp_anchors_python_leaves_bare_pytest():
    assert _normalize_interp(["python", "-m", "pytest", "x"])[0] == sys.executable
    assert _normalize_interp(["python3", "-m", "pytest"])[0] == sys.executable
    # bare pytest is NOT normalized (portability enforced upstream, ADR-038)
    assert _normalize_interp(["pytest", "tests/x.py"]) == ["pytest", "tests/x.py"]


def test_segments_is_quote_aware():
    assert _segments('python -c "import sys; a=1"; pytest tests/x.py') == \
        ['python -c "import sys; a=1"', "pytest tests/x.py"]


def test_extract_test_tokens_after_pytest_kw():
    assert _extract_test_tokens("python -m pytest tests/x.py::T::m") == [("tests/x.py", "::T::m")]
    assert _extract_test_tokens("curl localhost/health") == []


# ── AC1: the three-valued, fail-closed verdict ──
def test_run_verification_is_three_valued_fail_closed(tmp_path: Path):
    # PASS: a portable interpreter-anchored command that exits 0.
    assert run_verification('python -c "raise SystemExit(0)"', tmp_path).status == "PASS"

    # FAIL (exited-nonzero): a command that runs but exits non-zero.
    v = run_verification('python -c "raise SystemExit(1)"', tmp_path)
    assert v.status == "FAIL" and v.subkind == "exited-nonzero"

    # FAIL (not-runnable): a command whose program is not on PATH -> the genuinely
    # UNDECIDABLE class (tagged so the WS consumer can route it to a loud advisory).
    v = run_verification("totally-not-a-real-binary-xyz123 arg", tmp_path)
    assert v.status == "FAIL" and v.subkind == "not-runnable"

    # FAIL (unparseable): an unbalanced quote -> shlex ValueError, NEVER bubbles.
    v = run_verification('python -c "unterminated', tmp_path)
    assert v.status == "FAIL" and v.subkind == "unparseable"

    # ABSENT: cites a tests/...py token that is absent on repo_root. Decided
    # pre-execution by FILE existence, so it lands ABSENT even though bare pytest
    # would also be not-runnable here -- proving the m2/M2 separation.
    v = run_verification("pytest tests/this_is_absent_here.py", tmp_path)
    assert v.status == "ABSENT" and v.subkind == "absent-tests"


def test_absent_pre_check_fires_before_not_runnable(tmp_path: Path):
    # A cited-but-absent test token -> ABSENT (NOT a not-runnable FAIL), even on a
    # checkout where the pytest console-script is off PATH. This is the exact
    # carve-out (ADR-021) the WS consumer must NOT conflate with a phantom.
    absent = run_verification("pytest tests/bugs/never_here.py", tmp_path)
    assert absent.status == "ABSENT"
    # A PRESENT cited token does NOT short-circuit to ABSENT -> it runs (and here
    # fails not-runnable because bare pytest is off PATH), proving the existence
    # gate is real, not a blanket 'any pytest -> ABSENT'.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "present.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    present = run_verification("pytest tests/present.py", tmp_path)
    assert present.status != "ABSENT"


# ── AC5: the portable form makes genuine reality contact ──
def test_portable_form_makes_reality_contact(tmp_path: Path):
    # The unified core must ACTUALLY run a portable interpreter-anchored command
    # (not just classify it) and record a real PASS. Proof of reality contact: the
    # subprocess writes a marker file into repo_root (cwd), which we then observe.
    cmd = 'python -c "import pathlib; pathlib.Path(\'reality.marker\').write_text(\'contacted\')"'
    verdict = run_verification(cmd, tmp_path)
    assert verdict.status == "PASS"
    marker = tmp_path / "reality.marker"
    assert marker.exists() and marker.read_text() == "contacted"  # reality-contact = True
