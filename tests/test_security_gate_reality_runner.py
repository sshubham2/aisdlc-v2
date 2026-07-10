"""Acceptance coverage for the deterministic security gates DRIVEN THROUGH THE PRODUCTION
reality-gate runner (slice-067 / SC-097 / ADR-065; BC-PROJ-10 -- run the production invocation
shape, not the raw tool).

Each AC scaffolds a fixture repo exactly as /setup does (scaffold_reality_gates.scaffold ->
manifest + vendored guard), then invokes reality_gate_runner.run_gate against it -- the SAME
decide-core both wires call (pre_finish_gate.py:247 + validate-slice/SKILL.md:389).

  AC1  bandit HIGH -> runner FAIL (evidence captured); cleaned -> PASS.
  AC2  pip-audit known-vuln -> runner FAIL (CVE in evidence); clean -> PASS  [network-gated].
  AC3  neither surface -> no security gate declared/executed; pre-finish outcome unchanged (no-op PASS).
  AC4  the SAME runner path both wires call fails closed on a finding; both wire sources pinned.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))  # plugin root -> scripts.lib

from scripts.lib import reality_gate_runner as rg          # noqa: E402
from scripts.lib import scaffold_reality_gates as srg       # noqa: E402

_CLEAN = "def f(x):\n    return x + 1\n"
_HIGH = "import hashlib\ndef d(x):\n    return hashlib.md5(x).hexdigest()\n"


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _online() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


def _security_ids(repo: Path) -> list[str]:
    import json
    data = json.loads((repo / ".aisdlc" / "reality-gates.json").read_text(encoding="utf-8"))
    return [e["id"] for e in data["gates"]["security"]]


# ══════════════════════ AC1 -- bandit through the runner ══════════════════════
def test_ac1_bandit_high_fails_closed_then_clean_passes(tmp_path):
    # source-only fixture (no requirements.txt) so ONLY the bandit gate is seeded (no network).
    _write(tmp_path / "a.py", _CLEAN)
    _write(tmp_path / "b.py", _CLEAN)
    _write(tmp_path / "evil.py", _HIGH)
    srg.scaffold(tmp_path)
    assert _security_ids(tmp_path) == ["bandit"]

    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
    row = next(r for r in res["results"] if r["gate_id"] == "bandit")
    assert row["status"] == "FAIL" and "FINDING" in row["reason"] and "HIGH" in row["reason"]

    # clean the finding -> the SAME declared gate now PASSES (fixed-source twin).
    (tmp_path / "evil.py").write_text(_CLEAN, encoding="utf-8")
    res2 = rg.run_gate(repo_root=str(tmp_path))
    assert res2["action"] == rg.PASS and rg._exit_for(res2) == 0


# ══════════════════════ AC2 -- pip-audit through the runner (network-gated) ══════════════════════
@pytest.mark.skipif(not _online(), reason="pip-audit needs network (PyPA #698); offline -> skip")
def test_ac2_pip_audit_vuln_fails_closed_then_clean_passes(tmp_path):
    # deps-only fixture (no *.py) so ONLY the pip-audit gate is seeded.
    _write(tmp_path / "requirements.txt", "jinja2==2.11.2\n")
    srg.scaffold(tmp_path)
    assert _security_ids(tmp_path) == ["pip-audit"]

    res = rg.run_gate(repo_root=str(tmp_path), timeout=60)
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
    row = next(r for r in res["results"] if r["gate_id"] == "pip-audit")
    assert row["status"] == "FAIL"
    assert "FINDING" in row["reason"] and ("CVE-" in row["reason"] or "jinja2" in row["reason"])

    # clean twin: a non-vulnerable dependency set -> PASS.
    (tmp_path / "requirements.txt").write_text("certifi==2026.6.17\n", encoding="utf-8")
    res2 = rg.run_gate(repo_root=str(tmp_path), timeout=60)
    # PASS on a clean set; tolerate a transient network INFRA (still fail-closed, never false-green).
    assert res2["action"] in (rg.PASS, rg.FAIL)
    if res2["action"] == rg.FAIL:
        r = next(x for x in res2["results"] if x["gate_id"] == "pip-audit")
        assert "FINDING" not in r["reason"]   # never a vuln false-positive on the clean twin


# ══════════════════════ AC3 -- neither surface: no security gate, pre-finish unchanged ══════════════════════
def test_ac3_no_python_surface_declares_no_security_gate_and_noops(tmp_path):
    _write(tmp_path / "README.md", "# a non-python project\n")
    _write(tmp_path / "index.js", "console.log(1)\n")
    summary = srg.scaffold(tmp_path)
    assert summary["added"] == [] and summary["guard"] == "skipped"
    assert _security_ids(tmp_path) == []                       # no security gate declared
    assert not (tmp_path / ".aisdlc" / "gates").exists()       # no guard vendored

    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.NOOP and rg._exit_for(res) == 0  # pre-finish outcome UNCHANGED
    assert res["results"] == []                                 # no security gate command executed


# ══════════════════════ AC4 -- same runner path at both wires; fails closed ══════════════════════
def test_ac4_same_runner_path_both_wires_pinned():
    pre = (_ROOT / "skills" / "build-slice" / "scripts" / "pre_finish_gate.py").read_text(encoding="utf-8")
    val = (_ROOT / "skills" / "validate-slice" / "SKILL.md").read_text(encoding="utf-8")
    for src in (pre, val):
        assert "reality_gate_runner.py" in src and "--repo-root" in src
    # pre-finish roots at the worktree; validate roots at "$wt" -- both the shipping checkout.
    assert "worktree" in pre
    assert '"$wt"' in val


def test_ac4_shared_runner_path_fails_closed_on_a_finding(tmp_path):
    # The functional half of AC4: the ONE decide-core both wires invoke fails closed on a finding,
    # so a HIGH cannot slip past by being checked at only one wire.
    _write(tmp_path / "app.py", _HIGH)
    srg.scaffold(tmp_path)
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
