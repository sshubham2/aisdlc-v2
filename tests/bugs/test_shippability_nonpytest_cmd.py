"""
Bug: the shippability catalog tooling cannot handle a valid non-pytest
     `python -c "...;...;..."` machine_cmd row (the live slice-001 row-1 form).

Root cause (shared): both shippability_decoupling_audit (SCMD-1) and
shippability_runner (SRSC-1) parse a machine_cmd via _segments(), which did a
naive `machine_cmd.split(";")`. A single `python -c "import sys; a=1; b=2;
sys.exit(0)"` command carries semicolons INSIDE the quoted inline script, so the
split shreds the one command into fragments with unbalanced quotes.

Fix (slice-011): _segments() splits on TOP-LEVEL `;` only (quote/escape-aware,
matching shlex.split(posix=True)); the SCMD-1 grammar accepts a valid non-pytest
interpreter command (`<interp> -c "<quoted code>"` / `<interp> <script>.py`)
alongside the pytest form, while still rejecting narrative prose; and the runner
treats an unparseable segment as a per-ROW FAIL (exit 1), never a catalog abort
(exit 2).

This file is the BFRD-1 repro PLUS the slice-011 behavioural battery (AC1-AC4,
M1 prose-rejection, m1/m3 shlex-oracle agreement).
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

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
audit_mod = _load("skills/validate-slice/scripts/shippability_decoupling_audit.py",
                  "shippability_decoupling_audit")
runner_mod = _load("skills/validate-slice/scripts/shippability_runner.py",
                   "ai_sdlc_shippability_runner")


# A valid SINGLE command whose semicolons live INSIDE the quoted inline script.
# Exits 0 when parsed correctly; depends on nothing in the repo.
_VALID_NONPYTEST_ROW = (
    'python -c "import sys; a = 1; b = 2; sys.exit(0 if a + b == 3 else 1)"'
)


def _catalog_with(tmp_path: Path, machine_cmd: str) -> Path:
    cat = {
        "_schema": "aisdlc/shippability@1",
        "rows": [
            {
                "id": "SHIP-REPRO",
                "slice": "slice-x",
                "what": "a single machine_cmd row under test",
                "machine_cmd": machine_cmd,
                "added_at": "2026-01-01T00:00:00Z",
            }
        ],
    }
    p = tmp_path / "shippability.json"
    p.write_text(json.dumps(cat, indent=2), encoding="utf-8")
    return p


def _violations(tmp_path: Path, machine_cmd: str):
    return audit_mod.audit(_catalog_with(tmp_path, machine_cmd)).violations


# ── BFRD-1 repro: the live slice-001 row-1 form (AC1 + AC2) ──────────────────

def test_scmd1_audit_accepts_valid_nonpytest_inline_script(tmp_path: Path):
    """(AC1) SCMD-1 must NOT flag a valid single-command `python -c "...;..."` row."""
    result = audit_mod.audit(_catalog_with(tmp_path, _VALID_NONPYTEST_ROW))
    assert result.violations == [], (
        "SCMD-1 wrongly flagged a valid non-pytest inline-script row as prose: "
        + "; ".join(v.detail for v in result.violations)
    )


def test_runner_executes_valid_nonpytest_inline_script(tmp_path: Path):
    """(AC2) the runner must EXECUTE the row (PASS), not crash on inner-`;` quotes."""
    catalog = _catalog_with(tmp_path, _VALID_NONPYTEST_ROW)
    try:
        result = runner_mod.run_catalog(catalog, repo_root=REPO_ROOT)
    except ValueError as exc:  # the live bug: shlex.split "No closing quotation"
        pytest.fail(f"run_catalog crashed instead of executing the row: {exc!r}")
    assert result.failed == 0 and result.passed == 1, (
        f"expected the valid inline-script row to PASS, got "
        f"{result.passed} pass / {result.failed} fail: "
        + "; ".join(r.detail for r in result.rows if r.status == "FAIL")
    )


# ── _segments quote-aware top-level `;` split (AC3) ──────────────────────────

# (input, expected segments). The `;` inside a quoted span is NOT a separator;
# a backslash-escaped `;` outside quotes is NOT a separator; a trailing `;`
# yields no empty segment.
_SPLIT_CASES = [
    ('python -c "import sys; a=1; b=2; sys.exit(0)"',
     ['python -c "import sys; a=1; b=2; sys.exit(0)"']),
    ("python -c 'a=1; b=2'", ["python -c 'a=1; b=2'"]),
    ('python -c "a; b" ; pytest tests/y.py',
     ['python -c "a; b"', 'pytest tests/y.py']),
    ('pytest tests/a.py ; pytest tests/b.py',
     ['pytest tests/a.py', 'pytest tests/b.py']),
    ('pytest tests/x.py ;', ['pytest tests/x.py']),                 # trailing ;
    ('python -c "say \\"hi\\"; bye"', ['python -c "say \\"hi\\"; bye"']),  # escaped dq inside dq
    ('foo a\\; b', ['foo a\\; b']),                                 # backslash-escaped ; outside quotes
    ('pytest tests/x.py', ['pytest tests/x.py']),                   # no ; -> one segment
]


@pytest.mark.parametrize("cmd,expected", _SPLIT_CASES)
def test_segments_splits_top_level_semicolons_only(cmd, expected):
    """_segments splits on TOP-LEVEL `;` only — never on `;` inside quotes."""
    assert audit_mod._segments(cmd) == expected


@pytest.mark.parametrize("cmd,_expected", _SPLIT_CASES)
def test_every_segment_is_shlex_parseable(cmd, _expected):
    """m1/m3: every segment _segments returns must round-trip through
    shlex.split(posix=True) without raising — that shared quoting model is the
    coherence invariant the runner depends on (no unbalanced fragments)."""
    for seg in audit_mod._segments(cmd):
        shlex.split(seg, posix=True)  # must not raise ValueError


# ── SCMD-1 grammar: accept valid non-pytest commands (AC1), reject prose (M1) ─

_ACCEPTED = [
    'python -c "import sys; sys.exit(0)"',          # double-quoted -c
    "python -c 'a=1; b=2'",                          # single-quoted -c
    'python -m pytest tests/x.py -q',                # interpreter-anchored pytest form
    'python build.py',                               # bare script (interpreter-led -> portable)
    'python build.py --out dist/ data.json',         # script + flag/path-like args
]

# slice-046 / ADR-035: bare `pytest tests/...` (no interpreter prefix) is now NON-PORTABLE and
# REJECTED — it was in _ACCEPTED in slice-011; this is the reconciliation (moved, not silently
# deleted). The interpreter-anchored `python -m pytest ...` form above stays accepted.
_REJECTED_NONPORTABLE = [
    'pytest tests/x.py',
    'pytest tests/x.py -q',
    'pytest tests/bugs/test_webhook_sig.py::test_sig',
    'pytest tests/',
]

_REJECTED_PROSE = [
    'python -c just inspect it by hand',             # M1: unquoted -c free text
    'python3 the_plan.py and then review',           # M1: script + bare-word prose args
    'just run the regression by hand',               # plain prose
    'asserts an omitting design.json lints clean',   # plain prose (slice-001 style)
    'python -c "oops',                               # unterminated quote (malformed)
]


@pytest.mark.parametrize("cmd", _ACCEPTED)
def test_scmd1_accepts_valid_commands(tmp_path: Path, cmd):
    assert _violations(tmp_path, cmd) == [], f"valid command wrongly flagged: {cmd!r}"


@pytest.mark.parametrize("cmd", _REJECTED_PROSE)
def test_scmd1_rejects_prose_and_malformed(tmp_path: Path, cmd):
    vs = _violations(tmp_path, cmd)
    assert len(vs) >= 1, f"prose/malformed machine_cmd wrongly accepted: {cmd!r}"


# ── slice-046 / ADR-035: bare-pytest console-script is non-portable (AC2 reconciliation) ──

@pytest.mark.parametrize("cmd", _REJECTED_NONPORTABLE)
def test_scmd1_rejects_bare_pytest_nonportable(tmp_path: Path, cmd):
    """A bare `pytest tests/...` console-script is flagged as a `non-portable-command` violation
    (NOT silently accepted, NOT mislabelled prose). This is the slice-011 grammar reconciliation:
    the form moved from _ACCEPTED to rejected with a distinct, actionable violation kind."""
    vs = _violations(tmp_path, cmd)
    assert len(vs) >= 1, f"bare-pytest console-script wrongly accepted: {cmd!r}"
    assert any(v.kind == "non-portable-command" for v in vs), (
        f"bare pytest should be 'non-portable-command', got "
        f"{[v.kind for v in vs]} for {cmd!r}"
    )
    # the violation must distinguish it from prose and name the portable form (must_not_defer)
    assert any("interpreter-anchored" in v.detail or "ambient PATH" in v.detail for v in vs)


# ── runner: a genuinely malformed cmd FAILs the row, never aborts (M-must-not-defer) ─

def test_runner_fails_malformed_row_not_exit2(tmp_path: Path):
    """A genuinely unparseable segment (unterminated quote) must FAIL that ROW
    (exit-1 territory) and let the run continue — never raise / never the exit-2
    catalog-usage-error abort."""
    catalog = _catalog_with(tmp_path, 'python -c "unterminated')
    try:
        result = runner_mod.run_catalog(catalog, repo_root=REPO_ROOT)
    except ValueError as exc:
        pytest.fail(f"run_catalog raised instead of failing the row: {exc!r}")
    assert result.failed == 1 and result.passed == 0, (
        f"expected the malformed row to FAIL (not abort), got "
        f"{result.passed} pass / {result.failed} fail"
    )


def test_runner_runs_multicommand_top_level(tmp_path: Path):
    """AC3: a genuine multi-command row (top-level `;`) runs each segment; both
    pass -> row PASS. Uses python (portable) rather than a shell builtin."""
    cmd = ('python -c "import sys; sys.exit(0)" ; '
           'python -c "import sys; sys.exit(0)"')
    result = runner_mod.run_catalog(_catalog_with(tmp_path, cmd), repo_root=REPO_ROOT)
    assert result.failed == 0 and result.passed == 1, (
        "expected a 2-segment top-level multi-command row to PASS: "
        + "; ".join(r.detail for r in result.rows if r.status == "FAIL")
    )
