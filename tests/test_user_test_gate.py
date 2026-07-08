"""scripts/lib/user_test_gate.py — the real-user-validated firewall (slice-044 / SC-076).

Guards the load-bearing AC3 guardrail (heuristic output can NEVER count as real-user
validation) and the M5 main-thread enforcer (the self-report guardrails moved from
agent-prose to code). Per ADR-034 the predicate is ADAPTED to the PRODUCTION schema
(findings[] + participants>=1 + explicit source=='real-user'), NOT the spike's verbatim
real_user_findings shape — so this test asserts BOTH directions (M-add-1): a heuristic /
laundered / legacy session is NOT validated, AND a production-shaped genuine real-user
session IS validated (the test FAILS if the predicate cannot validate a real session,
closing B1's always-False false-green).
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.lib import user_test_gate as gate

_REPO = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO / "skills" / "user-test" / "examples" / "user-test.json"


def _production_real_session() -> dict:
    """A genuine real-user session in the PRODUCTION shape (built from the canonical
    example), with every real finding source-tagged per /user-test Step 5 (M1)."""
    return {
        "_schema": "aisdlc/user-test@1",
        "test": "checkout-flow",
        "mode": "prototype",
        "date": "2026-01-01T00:00:00Z",
        "participants": 1,
        "tasks": [{"task": "complete a purchase", "result": "stuck",
                   "observation": "user could not find the pay button"}],
        "findings": [{"kind": "stuck", "detail": "pay button below the fold",
                      "source": "real-user", "becomes_candidate": None, "becomes_risk": None}],
    }


# ── AC3 POSITIVE (M-add-1): a real session MUST validate ─────────────────────────────
def test_production_real_session_is_validated():
    assert gate.is_real_user_validated(_production_real_session()) is True


def test_example_shape_is_what_we_validate_against():
    # the positive fixture mirrors the canonical example's real-user keys (no drift)
    ex = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    assert "participants" in ex and "findings" in ex


# ── AC3 NEGATIVE: no shape of non-real data validates ────────────────────────────────
def test_heuristic_only_session_not_validated():
    session = {
        "participants": 0,
        "findings": [],
        "heuristic_walkthrough": {"source": "sim-agent", "color": "heuristic",
                                  "findings": [{"id": "H1", "kind": "confusion",
                                                "evidence_quote": "the term 'foo'"}]},
    }
    assert gate.is_real_user_validated(session) is False


def test_untagged_launder_not_validated():
    # sim findings copied into findings[] WITHOUT a real-user tag must NOT validate (M1)
    session = {"participants": 1,
               "findings": [{"kind": "confusion", "detail": "x", "source": "sim-agent"}]}
    assert gate.is_real_user_validated(session) is False


def test_untagged_legacy_real_not_validated():
    # a legacy session whose real findings lack the source tag reads as NOT-validated (M1, no default)
    session = {"participants": 1, "findings": [{"kind": "stuck", "detail": "x"}]}
    assert gate.is_real_user_validated(session) is False


def test_participants_zero_not_validated():
    session = {"participants": 0, "findings": [{"kind": "stuck", "source": "real-user"}]}
    assert gate.is_real_user_validated(session) is False


def test_predicate_is_blind_to_heuristic_walkthrough():
    # a rich heuristic section can never satisfy the gate
    session = {"participants": 1, "findings": [],
               "heuristic_walkthrough": {"findings": [{"id": "H1", "source": "real-user",
                                                       "evidence_quote": "q"}]}}
    assert gate.is_real_user_validated(session) is False


def test_missing_or_malformed_inputs_are_false_not_crash():
    assert gate.is_real_user_validated({}) is False
    assert gate.is_real_user_validated({"participants": "1", "findings": []}) is False
    assert gate.is_real_user_validated({"participants": 1, "findings": "nope"}) is False


# ── M5: ingest is the main-thread enforcer (not agent self-policing) ──────────────────
def test_ingest_drops_evidence_less_finding():
    raw = {"findings": [
        {"id": "H1", "kind": "confusion", "heuristic": "Match", "observation": "o",
         "evidence_quote": "the term 'reconcile'", "confidence": "medium",
         "predicts_interaction": False, "drafts_observation_question": "q1"},
        {"id": "H2", "kind": "confusion", "heuristic": "Match", "observation": "o2",
         "confidence": "high", "predicts_interaction": False, "drafts_observation_question": "q2"},
    ]}
    out = gate.ingest_heuristic_walkthrough(raw)
    assert [f["id"] for f in out["findings"]] == ["H1"]  # H2 dropped (A1.G1)


def test_ingest_forces_low_confidence_on_interaction_finding():
    raw = {"findings": [
        {"id": "H3", "kind": "broken-flow", "heuristic": "Error prevention", "observation": "o",
         "evidence_quote": "q", "confidence": "high", "predicts_interaction": True,
         "drafts_observation_question": "dq"},
    ]}
    out = gate.ingest_heuristic_walkthrough(raw)
    assert out["findings"][0]["confidence"] == "low"  # A1.G3


def test_ingest_sets_echo_caveat_only_when_ai_generated():
    on = gate.ingest_heuristic_walkthrough({"findings": []}, artifact_ai_generated=True)
    off = gate.ingest_heuristic_walkthrough({"findings": []}, artifact_ai_generated=False)
    assert on.get("echo_chamber_caveat")            # A1.G5
    assert not off.get("echo_chamber_caveat")


def test_ingest_ok_section_carries_firewall_markers():
    out = gate.ingest_heuristic_walkthrough({"findings": []})
    assert out["source"] == "sim-agent" and out["color"] == "heuristic"
    assert out["status"] == "ok"


# ── BC-PROJ-3: non-ASCII evidence quotes round-trip as the literal char ───────────────
_EM = "—"  # em-dash, as it appears copied from a real artifact's prose


def test_ingest_preserves_non_ascii_evidence_quote():
    raw = {"findings": [{"id": "H1", "kind": "confusion", "evidence_quote": f"Commit {_EM} now",
                         "confidence": "low", "predicts_interaction": False}]}
    out = gate.ingest_heuristic_walkthrough(raw)
    assert out["findings"][0]["evidence_quote"] == f"Commit {_EM} now"


def test_cli_ingest_emits_literal_non_ascii_not_escape(tmp_path):
    import subprocess
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"findings": [
        {"id": "H1", "kind": "confusion", "evidence_quote": f"Reconcile {_EM} entries",
         "confidence": "low", "predicts_interaction": False}]}), encoding="utf-8")
    script = _REPO / "scripts" / "lib" / "user_test_gate.py"
    proc = subprocess.run([__import__("sys").executable, str(script), "ingest", "--raw", str(raw)],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0
    assert _EM in proc.stdout            # literal em-dash present
    assert "\\u2014" not in proc.stdout  # NOT the ASCII escape
