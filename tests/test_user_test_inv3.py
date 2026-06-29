"""M6 / INV-3 — the flow-complete BASE protocol must survive in /user-test Step 3.

The design spike (spike-design-inv3-blinded-confirmation) proved the incorporation-bias trap is
REAL on the actual sim output: naive "one drafted question per sim finding" covers ZERO of the
real-only dimensions (task-completion / interaction-dynamics / motivational-dropout) and steers the
real session onto only what the model already saw. The guard is that drafted questions ADD to a
flow-complete base that is ALWAYS present. That guard is SKILL.md control flow a pytest cannot
execute (M7), so this test pins the load-bearing STRUCTURE: Step 3 must keep the three real-only
base dimensions and frame the pre-flight questions as a weaker, secondary candidate block. A future
edit that drops the base (re-opening the trap) fails here.

Plus the AC1 automated portion (M-add-2): a representative agent fixture, once ingested, conforms to
schema-by-example (the agent-output PROPERTY — calibrated findings from a real run — is proven by the
manual dry-run / mid-slice smoke, not a pytest, which cannot spawn a real agent).
"""
from __future__ import annotations

from pathlib import Path

from scripts.lib import artifact_lint
from scripts.lib import user_test_gate as gate

_SKILL = (Path(__file__).resolve().parents[1] / "skills" / "user-test" / "SKILL.md").read_text(encoding="utf-8")
_LOWER = _SKILL.lower()


def test_step3_keeps_all_three_real_only_base_dimensions():
    for dim in ("task-completion", "interaction-dynamics", "motivational-dropout"):
        assert dim in _LOWER, f"Step 3 base protocol dropped the '{dim}' dimension (INV-3 regression)"


def test_base_is_marked_always_present():
    # the base must be non-negotiable even when the pre-flight ran (the guard against replacement)
    assert "always present" in _LOWER or "non-negotiable" in _LOWER
    assert "inv-3" in _LOWER  # the guard is named so it is not silently removed


def test_preflight_questions_are_a_weaker_secondary_candidate_block():
    assert "candidate additions" in _LOWER
    assert "disconfirm" in _LOWER  # framed as hypotheses to disconfirm, not findings


def test_representative_fixture_conforms_after_ingest():
    # AC1 (automated portion): a representative multi-finding agent return, once ingested + stored,
    # conforms to schema-by-example. (Real-agent calibration is proven by the smoke, not here — M-add-2.)
    raw = {
        "_schema": "aisdlc/heuristic-walkthrough@1", "status": "ok",
        "disclaimed_scopes": ["cross-screen-state", "efficiency", "motivational-dropout"],
        "findings": [
            {"id": "H1", "kind": "confusion", "heuristic": "Match between system and the real world",
             "observation": "jargon label", "evidence_quote": "Reconcile selected entries",
             "confidence": "high", "predicts_interaction": False, "drafts_observation_question": "q1"},
            {"id": "H2", "kind": "broken-flow", "heuristic": "Visibility of system status",
             "observation": "silent commit", "evidence_quote": "refreshes to an empty grid",
             "confidence": "high", "predicts_interaction": True, "drafts_observation_question": "q2"},
        ],
    }
    section = gate.ingest_heuristic_walkthrough(raw, artifact_ai_generated=True)
    session = {
        "_schema": "aisdlc/user-test@1", "test": "rep", "mode": "mockup", "date": "<ts>",
        "participants": 0, "tasks": [], "findings": [], "preflight_used": True,
        "heuristic_walkthrough": section,
    }
    ex = artifact_lint._load_examples()["user-test"]
    assert artifact_lint.lint_artifact(session, "user-test", ex, "rep") == []
    # the interaction finding was downgraded by the enforcer
    assert section["findings"][1]["confidence"] == "low"
