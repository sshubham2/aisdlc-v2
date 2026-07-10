"""slice-066 / SC-119 — AC3 + critique M5: keep the DR-1 trigger set in sync across
its prose homes so the 3->4 cardinality change cannot drift stale (slice-051 SSOT,
slice-065 BC-PROJ-10). A region-keyed paired doc-guard binding THREE homes:

  1. skills/critique/SKILL.md Step 3.5 canonical trigger table (the canonical home)
  2. the critique_review_prerequisite_audit.py MODULE DOCSTRING (the CRP-1 enforcer)
  3. skills/critique-review/SKILL.md when_to_use (the third home critique M5 flagged)

Each must name the convergence trigger; the critique/SKILL.md home must ALSO invoke the
shared tournament_convergence helper (critique M1 -- one computation, no model-eyeball twin).
The guard is non-vacuous: it also pins the existing findings>=5 trigger, so it fails if
the real trigger table were removed rather than passing on an empty string.

TF-1: written FAILING before the doc edits (the trigger is not yet documented anywhere).
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRITIQUE_SKILL = ROOT / "skills" / "critique" / "SKILL.md"
CRITIQUE_REVIEW_SKILL = ROOT / "skills" / "critique-review" / "SKILL.md"
CRP_AUDIT = ROOT / "skills" / "build-slice" / "scripts" / "critique_review_prerequisite_audit.py"

_TRIGGER = "full-tournament-convergence"


def test_critique_skill_step35_names_convergence_trigger():
    text = CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert _TRIGGER in text, "critique/SKILL.md Step 3.5 must name the convergence trigger"
    assert "approach_divergence" in text
    # non-vacuous: the existing triggers are still in the same table (fails if the real table were removed)
    assert "risk_tier == high" in text and "findings" in text


def test_critique_skill_invokes_the_shared_helper():
    # critique M1: Step 3.5 must COMPUTE convergence via the shared helper, not eyeball it.
    text = CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert "tournament_convergence" in text, \
        "critique/SKILL.md Step 3.5 must invoke tournament_convergence (SSOT, no eyeball twin)"


def test_crp_audit_docstring_names_convergence_trigger():
    module = ast.parse(CRP_AUDIT.read_text(encoding="utf-8"))
    doc = ast.get_docstring(module) or ""
    assert _TRIGGER in doc, \
        "the CRP-1 module docstring must name full-tournament-convergence (kept in sync with the SKILL table)"


def test_critique_review_skill_names_convergence_trigger():
    # critique M5: the third home must not go stale at 3-of-4.
    text = CRITIQUE_REVIEW_SKILL.read_text(encoding="utf-8").lower()
    assert "converg" in text, \
        "critique-review/SKILL.md when_to_use must reflect the convergence trigger (or drop the enumeration for the pointer)"
