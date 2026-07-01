"""Guard the test_first handoff: the test_first slice-discipline's ``test_first_plan[]`` must be
named and explained at every place a builder reads about it, so it can never again surface only
as a surprise pre-finish FAIL (slice-011 / SC-023).

Updated for slice-051 / SC-062 / ADR-042: the plan is now PRODUCER-SCAFFOLDED. ``/slice`` (Step
5.3) lays down a PENDING ``test_first_plan[]`` stub (one row per AC) the moment test_first is
chosen; the builder then AUTHORS each row's ``test_path``/``test_function`` and walks it to
PASSING at build time. So the /slice Step 3b prose must (a) still name ``test_first_plan[]``,
(b) state the producer SCAFFOLDS the PENDING stub at /slice (NOT the stale 'no field written
here'), and (c) keep an accepted build-authoring phrase for the row bodies.

This is a doc-content guard over model-followed prose. The single AUTHORITATIVE source for
what the gate requires is ``SPECS['test_first']`` in ``scripts/lib/brief_variants_audit.py``
(array key ``test_first_plan``; ``--strict-pre-finish`` accepts only rows at status PASSING;
at least one row per acceptance criterion). The two SKILL.md prose sites (/slice Step 3b and
/build-slice) are DERIVED representations that must stay consistent with that canonical source;
this test is what enforces the consistency (slice-018: a property is only real where something
enforces it).

Written test-first (RED before the SKILL.md edits, GREEN after). Each function pairs a POSITIVE
invariant (the artifact is named + build-time authoring stated) with a NEGATIVE one (the stale
'no extra field' phrasing is gone), and keys on REGIONS spanning every site -- so it cannot pass
while the surprise survives at any one of them (the failure mode of an absence-only or
single-line test). Mirrors the doc-as-code pattern of tests/test_design_tournament_always_three.py.
"""
from __future__ import annotations


def _read(plugin_root, rel):
    return (plugin_root / rel).read_text(encoding="utf-8")


def _slice_step3b_region(plugin_root):
    """The Step 3b (slice-discipline variants) section of the /slice SKILL.md -- the
    producer-side handoff, spanning the variant-menu bullet, the chosen-variant bullet,
    and the 'Shapes' block."""
    text = _read(plugin_root, "skills/slice/SKILL.md")
    start = text.find("### Step 3b")
    assert start != -1, "skills/slice/SKILL.md has no '### Step 3b' section"
    end = text.find("## Step 4", start)
    assert end != -1, "could not bound the Step 3b region (no following '## Step 4')"
    return text[start:end]


def test_ac1_slice_step3b_handoff_explicit(plugin_root):
    """AC1 (+ its M2/M-add-2 breadth): ALL THREE test_first prose sites in /slice Step 3b
    name the build-authored test_first_plan[]; the stale 'no extra field' phrasing is gone."""
    region = _slice_step3b_region(plugin_root)
    low = region.lower()

    # POSITIVE: the build-time artifact is named, and the prose says who/when authors it.
    assert "test_first_plan" in region, (
        "/slice Step 3b must NAME the test_first_plan[] artifact (it was silent -- the "
        "slice-011 surprise)"
    )
    assert any(p in low for p in ("builder-authored", "authored at build", "authored by the builder at build")), (
        "/slice Step 3b must state test_first_plan[] is AUTHORED at BUILD time as a contiguous "
        "phrase (who + when) -- not merely the words 'build' and 'author' appearing apart"
    )

    # NEGATIVE: the misleading 'no extra field' line must be gone (globally unique phrase).
    assert "no extra field" not in low, (
        "the misleading 'no extra field' test_first wording must be removed -- it is the "
        "exact phrasing that hid the requirement"
    )

    # slice-051 (SC-062 / M-add-2): the producer now SCAFFOLDS the PENDING stub at /slice.
    # The prose must SAY so, and the stale 'no field is written here' contradiction must be
    # gone -- a build that reverted to 'the plan is written later' would silently re-open the
    # producer/gate gap this slice closed.
    assert "scaffold" in low, (
        "/slice Step 3b must state the producer SCAFFOLDS the PENDING test_first_plan stub at "
        "/slice (slice-051) -- not that the plan is authored only later"
    )
    assert "no field is written" not in low, (
        "the stale 'no field is written *here*' claim must be gone -- the producer now writes "
        "the PENDING stub at /slice (slice-051 / M-add-2)"
    )

    # M-add-2 (slice-038): the 'Shapes' block must document test_first_plan[] alongside the other two
    # variants (it documented architectural_layers/exploratory_charters but omitted this one).
    shapes_idx = region.find("Shapes")
    assert shapes_idx != -1, "Step 3b 'Shapes' block not found"
    shapes_block = region[shapes_idx:]
    assert "test_first_plan" in shapes_block, (
        "the Step 3b 'Shapes' block must document the test_first_plan[] shape alongside "
        "architectural_layers[]/exploratory_charters[] -- omitting it recreates the drift"
    )


def test_ac2_build_slice_authoring_before_gate(plugin_root):
    """AC2 (+ M-add-1): /build-slice tells the builder to author test_first_plan[] at BUILD
    START -- reachable BEFORE the Step-6 pre-finish gate -- and documents the
    --strict-pre-finish requirement; the TPHD-1 pre-flight no longer presumes a pre-existing
    plan table."""
    text = _read(plugin_root, "skills/build-slice/SKILL.md")

    # POSITIVE: the artifact is named and the strict gate is documented.
    assert "test_first_plan" in text, (
        "/build-slice must NAME test_first_plan[] (the build-authored plan the TF-1 gate "
        "requires)"
    )
    assert "strict-pre-finish" in text, (
        "/build-slice must document the brief_variants_audit --strict-pre-finish requirement "
        "(every row PASSING)"
    )

    # M-add-1 (positional): the build-START authoring directive must live in the build's
    # AUTHORING FLOW (Step 1 'Load context' .. before the Step-6 pre-finish gate) -- NOT in the
    # line-41 pre-flight (which sits BEFORE Step 1), and NOT only at the Step-6 gate (too late).
    # Keying on the *first* test_first_plan token would vacuously pass on the line-41 mention
    # alone (code-review M1); instead require the build-start authoring directive INSIDE the
    # Step-1..Step-6 region, so removing the Step-1 directive FAILS this even though the Step-4
    # walk note still says 'test_first_plan'.
    s1 = text.find("## Step 1")
    s6 = text.find("## Step 6")
    assert -1 < s1 < s6, "/build-slice Step 1 / Step 6 headings not found in order"
    flow = text[s1:s6]
    low_flow = flow.lower()
    assert "test_first_plan" in flow, (
        "the test_first_plan authoring directive must appear in the /build-slice authoring flow "
        "(Step 1..Step 4), not only the line-41 pre-flight or the Step-6 gate (M-add-1)"
    )
    assert any(p in low_flow for p in ("build start", "build-start", "at build start", "start of the build")), (
        "the directive must tell the builder to DRAFT test_first_plan[] at BUILD START -- "
        "authoring in the build flow, not discovered at the pre-finish gate (M-add-1)"
    )


def test_ac3_handoff_guard_spans_both_files(plugin_root):
    """AC3: the guard is non-vacuous and region-keyed across BOTH sides of the handoff -- the
    producer (/slice) AND the consumer (/build-slice) must both name test_first_plan[], so the
    consistency cannot quietly hold on one side while the other drops it."""
    region = _slice_step3b_region(plugin_root)
    build = _read(plugin_root, "skills/build-slice/SKILL.md")
    assert "test_first_plan" in region, "/slice Step 3b dropped test_first_plan"
    assert "test_first_plan" in build, "/build-slice dropped test_first_plan"
    # the stale phrase must not reappear on the producer side (the regression this guards).
    assert "no extra field" not in region.lower(), (
        "the 'no extra field' surprise must not reappear in /slice Step 3b"
    )
