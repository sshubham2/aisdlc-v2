"""M2 / M-add-3 — the heuristic pre-flight must not break artifact_lint on user-test files.

M2:      a real-user-ONLY session (no pre-flight) must still lint clean — heuristic_walkthrough
         and preflight_used must be OPTIONAL, not falsely required.
M-add-3: a pre-flight-then-declined session (must-not-defer #3: log which sessions used the
         pre-flight) must be CONFORMANT — the Step-2.5 write uses empty real-user placeholders
         (participants:0, tasks:[], findings:[]) so the required keys are present, and is NOT
         real-user-validated.
Plus: a bad heuristic kind/confidence enum is still caught (the enum is real, not decorative).
"""
from __future__ import annotations

from scripts.lib import artifact_lint
from scripts.lib import user_test_gate as gate

_EX = artifact_lint._load_examples()["user-test"]


def _lint(data: dict) -> list[str]:
    return artifact_lint.lint_artifact(data, "user-test", _EX, "test")


def test_real_user_only_session_lints_clean():
    # no pre-flight ran: heuristic_walkthrough + preflight_used absent
    session = {
        "_schema": "aisdlc/user-test@1",
        "test": "t", "mode": "prototype", "date": "<ts>", "participants": 1,
        "tasks": [{"task": "x", "result": "stuck", "observation": "y"}],
        "findings": [{"kind": "stuck", "detail": "z", "source": "real-user"}],
    }
    assert _lint(session) == []


def test_preflight_only_declined_session_is_conformant_and_not_validated():
    # pre-flight ran, real session declined -> empty real-user placeholders + heuristic section
    section = gate.ingest_heuristic_walkthrough(
        {"findings": [{"id": "H1", "kind": "confusion", "confidence": "low",
                       "evidence_quote": "the word 'reconcile'"}]})
    session = {
        "_schema": "aisdlc/user-test@1",
        "test": "t", "mode": "mockup", "date": "<ts>",
        "participants": 0, "tasks": [], "findings": [],
        "preflight_used": True,
        "heuristic_walkthrough": section,
    }
    assert _lint(session) == []
    assert gate.is_real_user_validated(session) is False


def test_preflight_skip_section_is_conformant():
    # agent failed -> a skip section is stored; the artifact still conforms
    section = gate.ingest_heuristic_walkthrough(None)
    session = {
        "_schema": "aisdlc/user-test@1",
        "test": "t", "mode": "mockup", "date": "<ts>",
        "participants": 0, "tasks": [], "findings": [],
        "preflight_used": True, "heuristic_walkthrough": section,
    }
    assert _lint(session) == []


def test_bad_heuristic_kind_is_caught():
    session = {
        "_schema": "aisdlc/user-test@1",
        "test": "t", "mode": "mockup", "date": "<ts>",
        "participants": 0, "tasks": [], "findings": [],
        "preflight_used": True,
        "heuristic_walkthrough": {"source": "sim-agent", "color": "heuristic", "status": "ok",
                                  "findings": [{"id": "H1", "kind": "not-a-real-kind",
                                                "confidence": "low", "evidence_quote": "q"}]},
    }
    violations = _lint(session)
    assert any("kind" in v for v in violations)
