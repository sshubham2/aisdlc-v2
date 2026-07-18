"""slice-078 / SC-163 — region-keyed doc-guard for the /pulse orphan surface (M-add-1).

The rendered orphan line is Haiku LLM control-flow, unreachable by a harness (CC-002). So the
achievable andon cord is a REGION-KEYED guard over skills/pulse/SKILL.md that pins ALL THREE wiring
links — a plain string-presence guard would pass GREEN with the M2 render-wiring bug present and stay
green if a refactor drops the threading (a vacuous guard launders a green):

  link 1 — the Step-1 adapter `!`-injection block is present, WITH its `|| echo` launch-failure
           fallback (M3), so a launch failure is fail-visible past the injection's 2>/dev/null;
  link 2 — BOTH the default `## Candidates` template AND the `--brief` template carry the distinct
           out-of-scope orphan slot + a WARN slot (M2 / M-add-2 / AC4);
  link 3 — Step-2 prose forwards the {orphaned, error} envelope into the Step-2 computed-state dict
           (mirror of the stranded-audit inject -> dict -> template chain) so default/--full render
           it, not just --brief.

Only the Haiku fill step itself remains unasserted (by design).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "pulse" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    assert i != -1, f"anchor not found in SKILL.md: {start!r}"
    j = text.find(end, i + len(start))
    assert j != -1, f"closing anchor not found after {start!r}: {end!r}"
    return text[i:j]


# region slicers (fail loudly if the SKILL.md section headers move) ──────────────────

def _step1_region(text: str) -> str:
    return _between(text, "## Step 1 — Read vault state", "## Step 2 — Compute derived metrics")


def _step2_candidates_region(text: str) -> str:
    return _between(text, "**Candidate backlog snapshot:**", "**Retired-risk freshness")


def _default_template_region(text: str) -> str:
    return _between(text, "### Default output template", "### Brief mode (`--brief`)")


def _brief_template_region(text: str) -> str:
    return _between(text, "### Brief mode (`--brief`)", "### Full mode (`--full`)")


# ── link 1 — Step-1 injection present WITH the || echo launch fallback (M3) ──────────

def test_link1_step1_injection_with_launch_fallback():
    region = _step1_region(_text())
    assert "orphaned_candidates.py" in region, "Step-1 must inject the orphaned-candidates adapter"
    assert "|| echo" in region, "the injection must carry a || echo launch-failure fallback (M3)"
    assert '"error"' in region, "the || echo fallback must supply a fail-visible error envelope (M3)"


# ── link 2 / AC4 — the distinct out-of-scope orphan + WARN slot in BOTH templates ────

def test_ac4_out_of_scope_label_in_both_templates():
    text = _text()
    default_region = _default_template_region(text)
    brief_region = _brief_template_region(text)
    # distinct label (NOT the stranded git-branch 'orphaned' klass)
    assert "Out-of-scope (parent capability cut)" in default_region, \
        "the default ## Candidates template needs the distinct out-of-scope orphan slot (AC4/M2)"
    assert "Out-of-scope" in brief_region, \
        "the --brief template needs an out-of-scope orphan slot (M-add-2) so a picker sees it too"
    # a WARN slot for the fail-visible error path, in both modes
    assert "WARN" in default_region, "default template needs a WARN slot for the orphan-surface error"
    assert "WARN" in brief_region, "--brief template needs a WARN slot for the orphan-surface error"


# ── link 3 — Step-2 threads the {orphaned, error} envelope into the render dict ──────

def test_link3_step2_threads_envelope_into_render_dict():
    region = _step2_candidates_region(_text())
    assert "orphaned" in region, "Step-2 must describe the orphaned envelope"
    assert "computed-state dict" in region, \
        "Step-2 must THREAD the envelope into the Step-2 computed-state dict (link 3), not only --brief"


# ── the whole cord: all three links must hold together (M-add-1) ─────────────────────

def test_doc_guard_all_three_links_hold():
    text = _text()
    step1 = _step1_region(text)
    step2 = _step2_candidates_region(text)
    default_region = _default_template_region(text)
    brief_region = _brief_template_region(text)
    assert "orphaned_candidates.py" in step1 and "|| echo" in step1          # link 1
    assert "Out-of-scope (parent capability cut)" in default_region          # link 2 (default)
    assert "Out-of-scope" in brief_region                                    # link 2 (--brief)
    assert "computed-state dict" in step2 and "orphaned" in step2            # link 3
