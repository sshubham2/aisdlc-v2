"""Integration coverage for the REALITY-GATES check wired into the /build-slice pre-finish
gate (slice-062 / SC-095 / ADR-059).

AC3: the pre-finish gate runs the declared set and BLOCKS finish on any gate failure.
M2 (the slice-059 --json exit-2->FAIL dogfood class): the argv the gate passes to
reality_gate_runner is PINNED and the runner's exit maps correctly to a CheckResult, so an
absent/empty manifest never FAILs the gate (no-op safety, must_not_defer #4) while a failing
or malformed manifest does. run_gate FAILs the whole gate on any FAIL via its existing
`any(r.status=='FAIL')` fold (pre_finish_gate.py) -- this test pins the REALITY-GATES row's
verdict, which that fold then aggregates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "build-slice" / "scripts"))

import pre_finish_gate  # noqa: E402

_RUNNER = _ROOT / "scripts" / "lib" / "reality_gate_runner.py"
_PASS = 'python -c "pass"'
_FAIL = 'python -c "import sys; sys.exit(1)"'


def _reality_gates_argv(repo: Path) -> list:
    """The EXACT argv shape run_gate builds for the REALITY-GATES check (M2: pinned)."""
    return [sys.executable, str(_RUNNER), "--repo-root", str(repo), "--json"]


def _write_manifest(root: Path, obj) -> None:
    d = root / ".aisdlc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "reality-gates.json").write_text(
        obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def _check(repo: Path):
    return pre_finish_gate._run("REALITY-GATES", _reality_gates_argv(repo), repo)


# ── no-op safety: absent / empty manifest -> PASS (gate outcome unchanged) ────
def test_absent_manifest_check_passes(tmp_path):
    assert _check(tmp_path).status == "PASS"          # no .aisdlc/ at all


def test_empty_manifest_check_passes(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [], "nfr": [], "ops": []}})
    assert _check(tmp_path).status == "PASS"


# ── a failing declared gate -> REALITY-GATES FAIL -> gate blocks ─────────────
def test_failing_declared_gate_check_fails(tmp_path):
    _write_manifest(tmp_path, {"gates": {"nfr": [{"id": "budget", "command": _FAIL}]}})
    r = _check(tmp_path)
    assert r.status == "FAIL"
    assert r.exit_code == 1


def test_malformed_manifest_check_fails(tmp_path):
    _write_manifest(tmp_path, "{ not valid json ")
    r = _check(tmp_path)
    assert r.status == "FAIL"
    assert r.exit_code == 3                            # REFUSE (fail-closed) -> CheckResult FAIL


def test_passing_declared_gate_check_passes(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [{"id": "ok", "command": _PASS}]}})
    assert _check(tmp_path).status == "PASS"


# ── doc-guard (M2): run_gate WIRES the check with the pinned argv ────────────
def test_run_gate_wires_reality_gates_with_pinned_argv():
    src = (_ROOT / "skills" / "build-slice" / "scripts" / "pre_finish_gate.py").read_text(encoding="utf-8")
    # the check must be appended inside run_gate, invoke the shared-lib runner, and pass
    # --repo-root explicitly + --json (the accepted no-op) -- the slice-059 dogfood shape.
    assert '"REALITY-GATES"' in src
    assert 'reality_gate_runner.py' in src
    assert '"--repo-root", worktree' in src
    assert '"--json"' in src
    idx_run_gate = src.index("def run_gate(")
    idx_check = src.index('"REALITY-GATES"')
    idx_fold = src.index('gate = "FAIL" if any(')       # the real gate-level FAIL fold
    assert idx_run_gate < idx_check < idx_fold        # inside run_gate, before the FAIL fold
