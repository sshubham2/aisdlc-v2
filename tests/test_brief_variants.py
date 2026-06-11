"""scripts/lib/brief_variants_audit.py — the merged TF-1/WS-1/ETC-1 variant audit (3.7).

PASS/FAIL fixtures per variant + the WS-1 --execute reality run. Exit-code parity with
the three pre-merge audits was verified across 23 cases at merge time; these tests lock
the merged behavior in.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.lib.brief_variants_audit import SPECS, audit


def _brief(tmp_path, data) -> Path:
    p = tmp_path / "mission-brief.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _kinds(result):
    return {v.kind for v in result.violations}


# ── test_first (TF-1) ─────────────────────────────────────────────────────────────

def test_tf_off_is_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": False}}), SPECS["test_first"])
    assert not r.enabled and not r.violations


def test_tf_missing_plan(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True}}), SPECS["test_first"])
    assert "missing-section" in _kinds(r)


def test_tf_valid_non_strict_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "acceptance_criteria": [{"id": "AC1"}],
              "test_first_plan": [{"ac": "1", "status": "PENDING"}]}), SPECS["test_first"])
    assert not r.violations


def test_tf_ac_without_row(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "acceptance_criteria": [{"id": "AC1"}, {"id": "AC2"}],
              "test_first_plan": [{"ac": "1", "status": "PENDING"}]}), SPECS["test_first"])
    assert "ac-without-row" in _kinds(r)


def test_tf_bad_status(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "test_first_plan": [{"ac": "1", "status": "DONE"}]}), SPECS["test_first"])
    assert "invalid-status" in _kinds(r)


def test_tf_strict_non_passing(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "test_first_plan": [{"ac": "1", "status": "PENDING"}]}),
              SPECS["test_first"], strict=True)
    assert "non-passing-pre-finish" in _kinds(r)


def test_tf_strict_ptfcd_missing_file(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "test_first_plan": [{"ac": "1", "status": "PASSING",
                                   "test_path": "no_such_test.py", "test_function": "test_x"}]}),
              SPECS["test_first"], strict=True, root=tmp_path)
    assert "missing-test-path-file" in _kinds(r)


def test_tf_strict_ptffd_missing_function(tmp_path):
    (tmp_path / "real_test.py").write_text("def test_other():\n    pass\n", encoding="utf-8")
    r = audit(_brief(tmp_path, {"variants": {"test_first": True},
              "test_first_plan": [{"ac": "1", "status": "PASSING",
                                   "test_path": "real_test.py", "test_function": "test_missing"}]}),
              SPECS["test_first"], strict=True, root=tmp_path)
    assert "missing-test-function" in _kinds(r)


# ── walking_skeleton (WS-1) ───────────────────────────────────────────────────────

def test_ws_off_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": False}}), SPECS["walking_skeleton"])
    assert not r.enabled and not r.violations


def test_ws_missing_array(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True}}), SPECS["walking_skeleton"])
    assert "missing-section" in _kinds(r)


def test_ws_valid_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "API", "verification": "curl x", "status": "exercised"}]}),
              SPECS["walking_skeleton"])
    assert not r.violations


def test_ws_missing_verification(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "API", "verification": "", "status": "exercised"}]}),
              SPECS["walking_skeleton"])
    assert "missing-verification" in _kinds(r)


def test_ws_strict_non_exercised(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "API", "verification": "x", "status": "pending"}]}),
              SPECS["walking_skeleton"], strict=True)
    assert "non-exercised-pre-finish" in _kinds(r)


# ── walking_skeleton --execute (3.1 reality run) ──────────────────────────────────

@pytest.mark.skipif(shutil.which("python") is None, reason="python not on PATH")
def test_ws_execute_pass(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "A", "verification": "python --version",
                                        "status": "exercised"}]}),
              SPECS["walking_skeleton"], execute=True, root=tmp_path)
    assert not r.violations
    assert any(e.get("verified") is True for e in r.executions)


@pytest.mark.skipif(shutil.which("python") is None, reason="python not on PATH")
def test_ws_execute_fail(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "A", "verification": 'python -c "raise SystemExit(1)"',
                                        "status": "exercised"}]}),
              SPECS["walking_skeleton"], execute=True, root=tmp_path)
    assert "verification-failed" in _kinds(r)


def test_ws_execute_prose_is_advisory(tmp_path):
    # prose verification (command not found) degrades to a non-gating advisory, never a hard fail
    r = audit(_brief(tmp_path, {"variants": {"walking_skeleton": True},
              "architectural_layers": [{"layer": "A", "verification": "this is prose not a command",
                                        "status": "exercised"}]}),
              SPECS["walking_skeleton"], execute=True, root=tmp_path)
    assert not r.violations
    assert r.advisories


# ── exploratory_charter (ETC-1) ───────────────────────────────────────────────────

def test_etc_off_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": False}}), SPECS["exploratory_charter"])
    assert not r.enabled and not r.violations


def test_etc_valid_clean(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": True},
              "exploratory_charters": [{"mission": "m", "status": "completed", "findings": "f"}]}),
              SPECS["exploratory_charter"])
    assert not r.violations


def test_etc_missing_findings_when_completed(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": True},
              "exploratory_charters": [{"mission": "m", "status": "completed", "findings": ""}]}),
              SPECS["exploratory_charter"])
    assert "missing-findings" in _kinds(r)


def test_etc_missing_mission(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": True},
              "exploratory_charters": [{"mission": "", "status": "completed", "findings": "f"}]}),
              SPECS["exploratory_charter"])
    assert "missing-mission" in _kinds(r)


def test_etc_deferred_needs_findings(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": True},
              "exploratory_charters": [{"mission": "m", "status": "deferred", "findings": ""}]}),
              SPECS["exploratory_charter"])
    assert "missing-findings" in _kinds(r)


def test_etc_strict_pending(tmp_path):
    r = audit(_brief(tmp_path, {"variants": {"exploratory_charter": True},
              "exploratory_charters": [{"mission": "m", "status": "pending"}]}),
              SPECS["exploratory_charter"], strict=True)
    assert "non-final-pre-finish" in _kinds(r)


# ── CLI contracts (exit codes) ────────────────────────────────────────────────────

def test_cli_missing_target_exit2(run_script, tmp_path):
    r = run_script("scripts/lib/brief_variants_audit.py",
                   [str(tmp_path / "nope"), "--variant", "test_first"])
    assert r.returncode == 2


def test_cli_unknown_variant_exit2(run_script, tmp_path):
    p = _brief(tmp_path, {"variants": {}})
    r = run_script("scripts/lib/brief_variants_audit.py", [str(p), "--variant", "bogus"])
    assert r.returncode == 2  # argparse choices rejects -> exit 2


def test_cli_off_exit0(run_script, tmp_path):
    p = _brief(tmp_path, {"variants": {"walking_skeleton": False}})
    r = run_script("scripts/lib/brief_variants_audit.py", [str(p), "--variant", "walking_skeleton"])
    assert r.returncode == 0
