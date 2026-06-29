"""AC4 / M7 — the pre-flight degrade path is a PYTHON seam, tested directly.

AC4 ('if the sim agent is unavailable or fails, /user-test proceeds with the normal
real-user flow unchanged') is SKILL.md control flow a pytest cannot execute (M7). What
the pytest CAN and MUST exercise is the Python seam the skill relies on:
`ingest_heuristic_walkthrough()` must tolerate a missing / empty / malformed agent return
and yield a DEFINED skip-with-note section (status=='skipped', findings==[]), never a
crash — so the skill can safely fall through to its normal Step-3 real-user flow. The
skip-to-Step-3 markdown branch itself is verified by inspection (M7).

These FAIL before impl (the module does not exist), satisfying the WRITTEN-FAILING bar.
"""
from __future__ import annotations

import pytest

from scripts.lib import user_test_gate as gate


def _is_defined_skip(section: dict) -> bool:
    return (isinstance(section, dict)
            and section.get("status") == "skipped"
            and section.get("findings") == []
            and isinstance(section.get("note"), str) and section["note"].strip() != ""
            and section.get("source") == "sim-agent" and section.get("color") == "heuristic")


@pytest.mark.parametrize("raw", [
    None,
    "not-a-dict",
    123,
    [],
    {},                                  # dict but no findings list -> degrade
    {"foo": "bar"},                      # unrelated keys
    {"findings": "not-a-list"},          # malformed findings
    {"findings": None},
    {"status": "skipped"},               # field-recon graceful-skip contract
    {"status": "skipped", "note": "WebSearch unavailable"},
])
def test_degrade_inputs_yield_defined_skip(raw):
    section = gate.ingest_heuristic_walkthrough(raw)
    assert _is_defined_skip(section), f"expected a defined skip-with-note, got {section!r}"


def test_agent_skipped_note_is_carried_through():
    section = gate.ingest_heuristic_walkthrough({"status": "skipped", "note": "no inputs"})
    assert "no inputs" in section["note"]


def test_malformed_finding_elements_are_skipped_not_crashed():
    # a findings list containing junk elements must not crash; valid ones survive
    raw = {"findings": [
        "junk", 42, None,
        {"id": "H1", "evidence_quote": "q", "kind": "confusion"},  # the one valid finding
    ]}
    section = gate.ingest_heuristic_walkthrough(raw)
    assert section["status"] == "ok"
    assert [f["id"] for f in section["findings"]] == ["H1"]
