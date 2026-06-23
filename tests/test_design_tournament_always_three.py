"""Guard ADR-018: the design tournament runs ALL THREE blind designers on every
slice, regardless of risk_tier (slice-028 / SC-060).

This is a doc-content guard over model-followed prose: the tournament SIZE is
driven by the instructions in ``skills/design-slice/SKILL.md`` (and the designer
agent registry), not by runtime code, so the honest enforcement is to assert the
shipped instruction files carry the always-3 invariant and no surviving
single-flight / tier-scaled-SIZE short-circuit.

Written test-first (RED before the SKILL.md edits, GREEN after). It asserts the
POSITIVE invariant (an explicit all-3 / every-tier instruction is present) AND the
ABSENCE of the removed machinery -- so it cannot pass while a single-flight path
survives (the failure mode of an absence-only test). It deliberately does NOT key
on the bare word "tier": tier legitimately survives (it still drives /critique +
/critique-review and is recorded in tournament.tier) -- only the generation SIZE
decouples from it.
"""
from __future__ import annotations

import re


def _read(plugin_root, rel):
    return (plugin_root / rel).read_text(encoding="utf-8")


def test_design_tournament_always_runs_three(plugin_root):
    """AC1/AC2/AC4: Step 1 spawns all 3 blind designers on every tier; the
    single-flight short-circuit and the auto-drop actuation are gone; the
    approach_divergence MEASUREMENT survives."""
    skill = _read(plugin_root, "skills/design-slice/SKILL.md")
    low = skill.lower()

    # POSITIVE: an explicit "all three, every slice/tier" instruction must be present.
    assert ("all 3" in low) or ("all three" in low), \
        "design-slice SKILL.md must state all 3 designers run"
    assert any(p in low for p in ("every slice", "every tier", "regardless of risk_tier", "on every")), \
        "design-slice SKILL.md must state the tournament runs on every slice/tier"

    # NEGATIVE: the tier->SIZE machinery and single-flight short-circuit must be gone.
    banned = [
        "### Single flight",        # the removed single-flight section heading
        "1 (inline)",               # the low/mechanical = 1 tier-table row
        "2 blind",                  # the medium = 2 tier-table row
        "Escalate within medium",   # the medium->3 escalation rule
        "drop to 2 designers",      # the approach_divergence auto-drop actuation (AC2)
        "single inline flight",     # the frontmatter / prose single-flight claim
    ]
    for token in banned:
        assert token not in skill, (
            f"design-slice SKILL.md still contains removed single-flight/size "
            f"machinery: {token!r}"
        )

    # MEASUREMENT survives (AC2 must-not-defer: only the auto-drop dies).
    assert "approach_divergence" in skill, (
        "the approach_divergence MEASUREMENT must survive -- only the auto-drop "
        "(actuation) is removed, not the divergence measurement/gate-log"
    )


def test_designer_personas_not_tier_scoped(plugin_root):
    """M-add-1 (ADR-018): the designer-agent frontmatter descriptions must not
    tier-scope WHO /design-slice spawns -- under always-3 every designer runs on
    every slice."""
    for agent in ("designer-practice", "designer-crossdomain", "designer-expert"):
        text = _read(plugin_root, f"agents/{agent}.md")
        desc = next((ln for ln in text.splitlines() if ln.startswith("description:")), "")
        assert desc, f"agents/{agent}.md has no frontmatter description line"
        for scope in ("medium/high/novel", "high/novel/irreversible"):
            assert scope not in desc, (
                f"agents/{agent}.md description still tier-scopes the spawn: "
                f"{scope!r} -- under always-3 it runs on every slice"
            )


def test_no_tier_gated_size_claims_in_docs(plugin_root):
    """AC3: CLAUDE.md and the design-slice frontmatter no longer claim a tier-gated
    tournament SIZE / single inline flight -- always-3 is the documented policy."""
    claude = _read(plugin_root, "CLAUDE.md")
    for token in ("tier-gated design tournament", "single inline flight"):
        assert token not in claude, \
            f"CLAUDE.md still claims a tier-gated tournament size: {token!r}"

    skill = _read(plugin_root, "skills/design-slice/SKILL.md")
    desc = next((ln for ln in skill.splitlines() if ln.startswith("description:")), "")
    assert desc, "design-slice SKILL.md has no frontmatter description line"
    for token in ("tier-gated", "single inline flight"):
        assert token not in desc, \
            f"design-slice SKILL.md frontmatter still tier-gates the tournament size: {token!r}"
