"""slice-082 / SC-184 / [[ADR-093]] — m4 doc-guard: the whole inject->prompt->section chain is wired.

AC1's 'assembles into the narrator's input' + M-add-1's 'numbers rendered deterministically' live in SKILL.md
markdown + the agent persona, which no unit test exercises (slice-063/078 lesson: a helper tests GREEN at the
CLI while the automated CONSUMER never reaches it). This region-keyed grep pins the four wiring points and is
NON-VACUOUS — each also asserts the surrounding discipline so it fails if the real wiring were removed rather
than passing on an empty string. Repo norm: test_residue_gate_doc_guard.py / test_convergence_trigger_doc_sync.py.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "slice-story" / "SKILL.md"
AGENT = ROOT / "agents" / "slice-story.md"
EXAMPLE = ROOT / "skills" / "slice-story" / "examples" / "story-sections.json"


def test_skill_step1b_fetches_the_projection():
    text = SKILL.read_text(encoding="utf-8")
    assert "story_inputs.py" in text and "project --vault" in text, \
        "SKILL Step-1b must fetch via `story_inputs.py project --vault` (single fetch+project, M2)"
    # non-vacuous: the M2 projection-shaped launch guard is present (never /pulse's envelope shape).
    assert '{"state":"error"' in text, "the || echo guard must emit the PROJECTION shape, not the envelope (M2)"


def test_skill_step2_prompt_embeds_the_product_shape_block():
    text = SKILL.read_text(encoding="utf-8")
    assert "# product shape" in text, \
        "the Step-2 narrator prompt must carry a `# product shape` block (AC1: assembled into narrator input)"
    # non-vacuous: the block instructs the narrator NOT to restate numbers (M-add-1).
    assert "do NOT restate these numbers" in text


def test_skill_step3b_injects_counts_deterministically():
    text = SKILL.read_text(encoding="utf-8")
    assert "story_inputs.py" in text and "inject" in text and "--sections-file" in text, \
        "SKILL Step-3b must inject the counts via `story_inputs.py inject --sections-file` (M-add-1, main-thread)"
    # non-vacuous: the injection is fail-visible (never a silent wrong count).
    assert "fail-visible" in text.lower() and "injection failed" in text.lower()


def test_agent_persona_carries_the_product_shape_section():
    text = AGENT.read_text(encoding="utf-8")
    assert "Where this fits in the product" in text, \
        "the narrator persona must reference the 'Where this fits in the product' section (chain endpoint)"
    # non-vacuous: it must state the numbers-are-not-the-narrator's discipline + the framing field.
    assert "product_shape_framing" in text
    assert "not" in text.lower() and "transcribe" in text.lower()


def test_example_artifact_documents_the_shape_and_framing():
    # AC4: the schema-by-example carries the new structured block + the optional framing.
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "product_shape" in text and "product_shape_framing" in text
