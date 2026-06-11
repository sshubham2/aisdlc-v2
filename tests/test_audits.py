"""Per-slice gate audits — triage_audit (TRI-1) + critique_review_audit (DR-1)
PASS/FAIL fixtures (4.4 priority c), plus a deterministic plugin-self-audit smoke.

The audit scripts live under skills/<name>/scripts/ (not an importable package), so
they're loaded by path via importlib; each self-bootstraps the plugin root onto
sys.path for its `from scripts.lib import ...` lines.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec so @dataclass can resolve __module__
    spec.loader.exec_module(mod)
    return mod


triage_audit = _load("skills/critique/scripts/triage_audit.py", "ai_sdlc_triage_audit")
cr_audit = _load("skills/critique-review/scripts/critique_review_audit.py",
                 "ai_sdlc_critique_review_audit")


def _write(p, obj):
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


# ── triage_audit (TRI-1) ─────────────────────────────────────────────────────────

def _valid_critique():
    return {
        "_schema": "aisdlc/critique@1",
        "slice": "slice-001",
        "verdict": "needs-fixes",
        "findings": [{"id": "C1", "severity": "major", "claim": "x", "disposition": "y"}],
        "triage": {
            "verdict": "needs-fixes", "ratified_by": "user", "at": "2026-01-01",
            "dispositions": [
                {"finding": "C1", "action": "accepted-pending", "rationale": "fix in build"}
            ],
        },
    }


def test_triage_audit_pass(tmp_path):
    p = tmp_path / "critique.json"
    _write(p, _valid_critique())
    assert triage_audit.main([str(p)]) == 0


def test_triage_audit_bad_disposition_fails(tmp_path):
    c = _valid_critique()
    c["triage"]["dispositions"][0]["action"] = "fix-now"
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1


def test_triage_audit_missing_disposition_row_fails(tmp_path):
    c = _valid_critique()
    c["findings"].append({"id": "C2", "severity": "minor", "claim": "z", "disposition": "w"})
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1


def test_triage_audit_verdict_mismatch_fails(tmp_path):
    c = _valid_critique()
    # accepted-pending present -> expected needs-fixes; declaring clean is inconsistent
    c["triage"]["verdict"] = "clean"
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1


# ── critique_review_audit (DR-1) ─────────────────────────────────────────────────

def _valid_cr():
    return {
        "_schema": "aisdlc/critique-review@1",
        "slice": "slice-001",
        "reviewed_by": "critique-review agent",
        "verdict": "adjust",
        "date": "2026-01-01",
        "assessments": [{"finding": "C1", "classification": "valid"}],
        "missed": [],
    }


def test_critique_review_audit_pass(tmp_path):
    p = tmp_path / "critique-review.json"
    _write(p, _valid_cr())
    assert cr_audit.main([str(p)]) == 0


def test_critique_review_audit_bad_verdict_fails(tmp_path):
    c = _valid_cr()
    c["verdict"] = "totally-wrong"
    p = tmp_path / "critique-review.json"
    _write(p, c)
    assert cr_audit.main([str(p)]) == 1


def test_critique_review_audit_bad_classification_fails(tmp_path):
    c = _valid_cr()
    c["assessments"][0]["classification"] = "bogus"
    p = tmp_path / "critique-review.json"
    _write(p, c)
    assert cr_audit.main([str(p)]) == 1


def test_critique_review_audit_missing_field_fails(tmp_path):
    c = _valid_cr()
    del c["missed"]
    p = tmp_path / "critique-review.json"
    _write(p, c)
    assert cr_audit.main([str(p)]) == 1


# ── deterministic plugin self-audit (the green member of plugin_self_audits) ──────

def test_artifact_lint_self_check_subprocess(run_script):
    r = run_script("scripts/lib/artifact_lint.py", ["--self-check"])
    assert r.returncode == 0, r.stdout + r.stderr
