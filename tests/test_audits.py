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
pca_audit = _load("skills/build-slice/scripts/pipeline_chain_audit.py", "ai_sdlc_pca_audit")


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


def test_triage_audit_orphan_disposition_fails(tmp_path):
    # A disposition naming a nonexistent finding id (typo'd C7 for C1) must be refused,
    # not silently accepted; the real finding is separately flagged missing-row.
    c = _valid_critique()
    c["triage"]["dispositions"][0]["finding"] = "C7"
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1
    r = triage_audit.audit_critique_file(p)
    assert any(v.kind == "orphan-row" for v in r.violations)


def _deferred_blocker_critique():
    c = _valid_critique()
    c["findings"] = [{"id": "B1", "severity": "blocker", "claim": "x", "disposition": "deferred"}]
    c["verdict"] = "clean"
    c["triage"] = {
        "verdict": "clean", "ratified_by": "user", "at": "2026-01-01",
        "dispositions": [
            {"finding": "B1", "action": "deferred", "rationale": "punt to SC-031 (backlog)"}
        ],
        "deferred_blockers": ["B1"],
    }
    return c


def test_triage_audit_deferred_blocker_qualified_passes(tmp_path):
    p = tmp_path / "critique.json"
    _write(p, _deferred_blocker_critique())
    assert triage_audit.main([str(p)]) == 0


def test_triage_audit_deferred_blocker_no_target_fails(tmp_path):
    # DD-15: "later" is not a target — the rationale must name slice-NNN or SC-NNN.
    c = _deferred_blocker_critique()
    c["triage"]["dispositions"][0]["rationale"] = "later"
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1
    r = triage_audit.audit_critique_file(p)
    assert any(v.kind == "deferred-blocker" for v in r.violations)


def test_triage_audit_deferred_blocker_unlisted_fails(tmp_path):
    # DD-15: the deferred blocker id must appear in triage.deferred_blockers[].
    c = _deferred_blocker_critique()
    del c["triage"]["deferred_blockers"]
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1


def test_triage_audit_deferred_blockers_mismatch_fails(tmp_path):
    # DD-15 converse: a deferred_blockers entry that is not a deferred blocker finding.
    c = _valid_critique()
    c["triage"]["deferred_blockers"] = ["C1"]  # C1 is a major, accepted-pending
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 1


def test_triage_audit_deferred_major_needs_no_qualification(tmp_path):
    # DD-15 scopes to BLOCKER severity only — a deferred major with a plain rationale passes.
    c = _valid_critique()
    c["triage"]["dispositions"][0]["action"] = "deferred"
    c["triage"]["dispositions"][0]["rationale"] = "not worth it this slice"
    c["triage"]["verdict"] = "clean"
    c["verdict"] = "clean"
    p = tmp_path / "critique.json"
    _write(p, c)
    assert triage_audit.main([str(p)]) == 0


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


# ── pipeline_chain_audit (PCA-1) — Option-1 membership matcher ────────────────────

def test_pca_real_tree_is_clean():
    # the shipped 10-skill chain conforms: the membership matcher accepts
    # design-slice's conditional design-spike and critique's slice-story +
    # in-loop critique-review listed alongside the canonical edge.
    result = pca_audit.audit(repo_root=PLUGIN_ROOT)
    assert not result.violations, [v.message for v in result.violations]


def test_pca_matcher_accepts_canonical_among_alternatives():
    toks = pca_audit._all_cmds("/risk-spike --mode design (conditional) -> then /critique")
    assert "/critique" in toks
    toks2 = pca_audit._all_cmds(
        "/slice-story when >=1 finding else /build-slice; the /critique-review runs in-loop")
    assert "/critique-review" in toks2


def test_pca_matcher_still_fails_when_canonical_absent():
    # the relaxation must NOT accept a successor that omits the canonical edge
    assert "/critique" not in pca_audit._all_cmds("/build-slice directly")


# ── deterministic plugin self-audit (the green member of plugin_self_audits) ──────

def test_artifact_lint_self_check_subprocess(run_script):
    r = run_script("scripts/lib/artifact_lint.py", ["--self-check"])
    assert r.returncode == 0, r.stdout + r.stderr
