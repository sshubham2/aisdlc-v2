"""slice-052 / ADR-045 — the critique-review gate-log row is emitted at /critique Step 4.5,
NOT at critique-review Step 5b, and by EXACTLY ONE writer.

A regression test that exercises triage_precision.py in isolation does not guard the SKILL.md
SITES that wire it (slice-025: 'a property is only real where something enforces it'). These
grep-the-sites assertions make the emission-placement + single-writer + M-add-1 guard durable:
a future edit that re-adds the Step-5b append, drops the guard, or introduces a second writer
turns this suite RED.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.lib.triage_precision import REAL_DISPOSITIONS

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CRITIQUE = (PLUGIN_ROOT / "skills" / "critique" / "SKILL.md").read_text(encoding="utf-8")
CRITIQUE_REVIEW = (PLUGIN_ROOT / "skills" / "critique-review" / "SKILL.md").read_text(encoding="utf-8")
CRITIC_CALIBRATE = (PLUGIN_ROOT / "skills" / "critic-calibrate" / "SKILL.md").read_text(encoding="utf-8")
PULSE = (PLUGIN_ROOT / "skills" / "pulse" / "SKILL.md").read_text(encoding="utf-8")

EMIT = "--gate critique-review"


def test_step5b_no_longer_emits_the_critique_review_gate_row():
    # ADR-045: the pre-TRI-1 count-only emission was deleted from critique-review/SKILL.md.
    assert EMIT not in CRITIQUE_REVIEW
    assert "gate_log.py" not in CRITIQUE_REVIEW


def test_critique_step45_emits_the_guarded_critique_review_row():
    assert EMIT in CRITIQUE                       # emitted at /critique Step 4.5
    assert "triage_precision.py" in CRITIQUE      # via the SSOT helper
    assert "--critique-review-args" in CRITIQUE
    assert '[ -n "$cr_args" ]' in CRITIQUE        # M-add-1 guard: emit ONLY when the helper returned flags


def test_exactly_one_writer_of_the_critique_review_row():
    # single-writer invariant (AC2): only /critique Step 4.5 appends a gate: critique-review row.
    emitters = sorted(
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in (PLUGIN_ROOT / "skills").rglob("SKILL.md")
        if EMIT in p.read_text(encoding="utf-8")
    )
    assert emitters == ["skills/critique/SKILL.md"], f"unexpected critique-review emitters: {emitters}"


# ---- BC-PROJ-7: the consumers CALL the shipped precision helper (a revert to inline prose is caught) ----

def test_consumers_reference_the_shipped_precision_helper():
    for name, txt in (("critic-calibrate", CRITIC_CALIBRATE), ("pulse", PULSE)):
        assert "triage_precision.py" in txt and "--gate-precision" in txt, (
            f"{name}/SKILL.md must compute precision/recall via the shipped triage_precision "
            f"--gate-precision helper (M3/AC4), not re-derive it inline")


# ---- BC-PROJ-4: prose copies of the real-set must not silently diverge from the SSOT ----

def _documented_real_set(text: str, anchor: str) -> set[str]:
    m = re.search(anchor + r"[^{]*\{([^}]+)\}", text.replace("`", ""))
    assert m, f"real-set anchor not found (moved?): {anchor!r}"
    return {t.strip() for t in m.group(1).split(",")}


def test_documented_real_sets_match_the_ssot():
    # first-Critic emission prose (critique Step 4.5) — left inline per M4, so guard it against drift
    assert _documented_real_set(CRITIQUE, r"findings-real = dispositions in") == set(REAL_DISPOSITIONS)
    # /pulse DR-1 unique-catch prose (reads critique.json directly; the M1-confirmed working path)
    assert _documented_real_set(PULSE, r"ratified disposition is in") == set(REAL_DISPOSITIONS)
