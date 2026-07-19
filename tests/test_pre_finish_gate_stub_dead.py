"""AC4 + M6: STUB-DEAD-1's non-zero exit FOLDS into the pre-finish gate FAIL verdict.

AC4's deciding assertion is the run_gate FOLD, not the detector in isolation (M6 / CC-002): run_gate
runs ~12 checks and each would independently FAIL/SKIP on a synthetic worktree, masking attribution.
So this isolates STUB-DEAD-1 — every OTHER check is monkeypatched to PASS while STUB-DEAD-1 runs the
REAL detector — and asserts gate=='FAIL' AND the STUB-DEAD-1 CheckResult.status=='FAIL' AND
main()==1 on a planted defect, with a clean variant proving STUB-DEAD-1 itself flips to PASS. A
source doc-guard pins the wiring (STUB-DEAD-1 threaded with --base, inside run_gate, before the fold
— M-add-3).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "build-slice" / "scripts"))

import pre_finish_gate  # noqa: E402

_GATE_SRC = _ROOT / "skills" / "build-slice" / "scripts" / "pre_finish_gate.py"


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {args} failed: {r.stderr or r.stdout}"
    return r.stdout.strip()


def _worktree(tmp_path, new_body: str):
    """A real git worktree with a committed base and one uncommitted new.py holding `new_body`.
    Returns (work, base_sha)."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "uat")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    _git(work, "config", "commit.gpgsign", "false")
    (work / "base.py").write_text("# base\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    base = _git(work, "rev-parse", "HEAD")
    (work / "new.py").write_text(new_body, encoding="utf-8")
    return work, base


def _args(tmp_path, work, base):
    slice_dir = tmp_path / "vault" / "slices" / "slice-x"
    return argparse.Namespace(
        slice=str(slice_dir), worktree=str(work),
        changed_files=[], changed_test_files=[], changed_from_git=base,
        ack_critical="", seam_allowlist=None, test_first=False, strict=False, json=True)


def _isolate_to_stub_dead(monkeypatch):
    """Monkeypatch _run so every check but STUB-DEAD-1 returns PASS; STUB-DEAD-1 runs for real."""
    real_run = pre_finish_gate._run

    def fake_run(name, argv, cwd):
        if name == "STUB-DEAD-1":
            return real_run(name, argv, cwd)
        return pre_finish_gate.CheckResult(name=name, status="PASS", exit_code=0, summary="(isolated)")

    monkeypatch.setattr(pre_finish_gate, "_run", fake_run)


def test_ac4_gate_folds_stub_dead_fail(tmp_path, monkeypatch):
    """A planted stub → STUB-DEAD-1 CheckResult FAIL → gate=='FAIL' → main() returns 1."""
    work, base = _worktree(tmp_path, 'def todo():\n    raise NotImplementedError("TODO")\n')
    _isolate_to_stub_dead(monkeypatch)

    gate, results = pre_finish_gate.run_gate(_args(tmp_path, work, base))
    sd = next(r for r in results if r.name == "STUB-DEAD-1")
    assert sd.status == "FAIL", f"STUB-DEAD-1 must be the failing check: {sd}"
    assert sd.exit_code == 1
    assert gate == "FAIL", "a STUB-DEAD-1 FAIL must fold into a gate FAIL"

    rc = pre_finish_gate.main(
        ["--slice", str(tmp_path / "vault" / "slices" / "slice-x"),
         "--worktree", str(work), "--changed-from-git", base, "--json"])
    assert rc == 1, "main() must return 1 when the gate FAILs on STUB-DEAD-1"


def test_ac4_clean_diff_stub_dead_passes(tmp_path, monkeypatch):
    """The deciding opposite outcome: a clean diff → STUB-DEAD-1 PASS → gate PASS → main()==0."""
    work, base = _worktree(tmp_path, "def real():\n    return 1\n")
    _isolate_to_stub_dead(monkeypatch)

    gate, results = pre_finish_gate.run_gate(_args(tmp_path, work, base))
    sd = next(r for r in results if r.name == "STUB-DEAD-1")
    assert sd.status == "PASS", f"clean diff must PASS STUB-DEAD-1: {sd.summary}"
    assert gate == "PASS"

    rc = pre_finish_gate.main(
        ["--slice", str(tmp_path / "vault" / "slices" / "slice-x"),
         "--worktree", str(work), "--changed-from-git", base, "--json"])
    assert rc == 0


def test_run_gate_wires_stub_dead_with_base_before_fold():
    """Doc-guard (M-add-3): the check is appended inside run_gate, invokes stub_dead_audit.py,
    threads the gate's already-resolved base via --base, and sits BEFORE the gate FAIL fold."""
    src = _GATE_SRC.read_text(encoding="utf-8")
    assert '"STUB-DEAD-1"' in src
    assert "stub_dead_audit.py" in src
    # threads the gate's already-resolved base (M-add-3); read via getattr so a hand-built
    # Namespace without the field self-resolves instead of AttributeError-ing (SHIP-031/043 regression).
    assert 'getattr(args, "changed_from_git"' in src, "must source the base from changed_from_git"
    assert '"--base", base_ref' in src, "must thread the resolved base to STUB-DEAD-1 (M-add-3)"
    idx_run_gate = src.index("def run_gate(")
    idx_check = src.index('"STUB-DEAD-1"', idx_run_gate)
    idx_fold = src.index('gate = "FAIL" if any(')
    assert idx_run_gate < idx_check < idx_fold, "STUB-DEAD-1 must wire inside run_gate, before the fold"
