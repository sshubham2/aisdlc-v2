"""Unit tests for scripts/lib/runnable_command.py (slice-046 / SC-081, AC1).

Covers the three-state verdict + the M1 requirement that not_a_command SUBSUMES slice-011's
prose-rejection grammar (interpreter-led prose stays rejected, not just bare-word prose).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.runnable_command import (
    NON_PORTABLE_CONSOLE_SCRIPT,
    NOT_A_COMMAND,
    PORTABLE,
    classify,
)

# Interpreter-anchored => portable. Mirrors the slice-011 _ACCEPTED forms MINUS bare pytest.
_PORTABLE = [
    "python -m pytest tests/x.py -q",
    "<interp> -m pytest tests/x.py",
    "python3 -m pytest tests/a.py::TestClass::test_method",
    'C:/Users/x/.venv/Scripts/python.exe -m pytest tests/x.py -q',
    "python -W error -m pytest tests/x.py",
    'python -c "import sys; sys.exit(0)"',
    "python -c 'a=1; b=2'",
    "python build.py",
    "python build.py --out dist/ data.json",
]

# Bare pytest console-script (no interpreter prefix) => non-portable (the flip this slice makes).
_NON_PORTABLE = [
    "pytest tests/x.py -q",
    "pytest tests/x.py",
    "pytest tests/",
    "pytest tests/bugs/test_webhook_sig.py::test_sig",
]

# Prose / unparseable => not_a_command. Includes slice-011's _REJECTED_PROSE so the validator
# provably subsumes _NONPYTEST_CMD_RE (critique M1) — interpreter-LED prose stays rejected.
_NOT_A_COMMAND = [
    "python -c just inspect it by hand",       # interp-led prose: -c without a quoted token
    "python3 the_plan.py and then review",     # interp-led prose: bare-word trailing args
    "just run the regression by hand",         # plain prose
    "asserts an omitting design.json lints clean",  # plain prose
    'python -c "oops',                         # unterminated quote (malformed)
    "",                                        # empty
    "   ",                                     # whitespace only
]


@pytest.mark.parametrize("cmd", _PORTABLE)
def test_classify_verdicts_portable(cmd):
    v = classify(cmd)
    assert v.klass == PORTABLE, f"expected portable, got {v.klass} ({v.reason}) for {cmd!r}"
    assert v.reason == "", "portable verdict carries no reason"
    assert v.is_portable


@pytest.mark.parametrize("cmd", _NON_PORTABLE)
def test_classify_verdicts_non_portable(cmd):
    v = classify(cmd)
    assert v.klass == NON_PORTABLE_CONSOLE_SCRIPT, (
        f"expected non_portable_console_script, got {v.klass} for {cmd!r}"
    )
    assert v.reason, "a non-portable verdict must carry a logged reason"
    assert "ambient PATH" in v.reason
    assert not v.is_portable


@pytest.mark.parametrize("cmd", _NOT_A_COMMAND)
def test_classify_verdicts_not_a_command(cmd):
    v = classify(cmd)
    assert v.klass == NOT_A_COMMAND, f"expected not_a_command, got {v.klass} for {cmd!r}"
    assert v.reason, "a not_a_command verdict must carry a logged reason"
    assert not v.is_portable


def test_classify_verdicts():
    """TF-1 anchor (test_first_plan AC1): one terminal assertion that all three verdict classes
    resolve correctly, so the AC has a single named PASSING row."""
    assert classify("python -m pytest tests/x.py -q").klass == PORTABLE
    assert classify("pytest tests/x.py -q").klass == NON_PORTABLE_CONSOLE_SCRIPT
    assert classify("python -c just inspect it by hand").klass == NOT_A_COMMAND
    assert classify("python build.py").klass == PORTABLE  # interpreter-led => stays portable


def test_classify_never_raises_on_garbage():
    """fail-visible: classify returns a verdict (never raises) on adversarial input."""
    for junk in ['"', "';drop", "\\", "pytest 'unbalanced", "<interp>", "-m pytest tests/x.py"]:
        v = classify(junk)
        assert v.klass in {PORTABLE, NON_PORTABLE_CONSOLE_SCRIPT, NOT_A_COMMAND}
