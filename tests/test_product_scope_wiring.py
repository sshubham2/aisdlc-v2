"""WIRE-1 — the bootstrap act must actually be INVOKED by something. slice-068 / SC-135 (M-add-2).

THE PREMORTEM this guard exists to prevent, in the meta-Critic's own words: "this slice ships and six
months later the product STILL is not getting built, because NO VAULT EVER RAN
`/slice-candidates --product`: product-scope.json never existed, materialize exit-4'd on every tick, and
the backlog stayed 100% exhaust. THE SLICE'S OWN BUG, ONE LEVEL UP."

That was not hypothetical. `/discover` wrote concept.json and handed off to `/slice`; nothing in the
pipeline invoked the decompose act at all. The design's own `tournament.taste_disagreements[0]` flagged
the wiring question and routed it verbatim "for /critique + the user" — and the first Critic's 15
findings never answered it. A perfectly correct product_scope.py would have shipped INERT.

So this guard drives the CONSUMER (the hand-off in the SKILL.md the pipeline actually executes), not the
CLI — BC-PROJ-10, and slice-063's recorded lesson: "a shared-helper fix can test GREEN at the CLI while
the automated CONSUMER never reaches it."

It is non-vacuous: it also pins surrounding structure, so it fails if the sections were removed rather
than passing against an empty string.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCOVER = ROOT / "skills" / "discover" / "SKILL.md"
ADOPT = ROOT / "skills" / "adopt" / "SKILL.md"
SLICE = ROOT / "skills" / "slice" / "SKILL.md"
SLICE_CANDIDATES = ROOT / "skills" / "slice-candidates" / "SKILL.md"

_BOOTSTRAP = "/slice-candidates --product"

#: The ADR-067 section-1 enumeration (as scoped by [[ADR-080]] + [[ADR-152]]). This is the ONLY
#: executable guard on that invariant — there is no _DISPATCH-completeness test — so a slice that
#: changes the CARDINALITY of `product_scope`'s mutating-verb set MUST extend it (FBCD-1, slice-081 m3).
#:
#: slice-102 grew the set 5 -> 6 with `add-item`, banned **UNCONDITIONALLY**. There is deliberately no
#: mode-awareness, no continuation-joining and no invocation scoping: under [[ADR-152]] `/slice` ROUTES
#: to `/slice-candidates --add-item` and has no reason to name the verb at all, so the check stays the
#: flat membership scan it has always been ([[ADR-153]] d1, superseding ADR-151's mode-aware parser,
#: which DR-1 broke on its first adversarial read).
#:
#: PRE-EXISTING GAP, RECORDED AND NOT FIXED ([[ADR-153]] d4): persist/materialize/revise are enumerated
#: in the QUOTE form only, while set-area/set-component carry both. `add-item` is added in BOTH forms so
#: the new verb does not inherit that gap; closing it for the older three is someone else's cut.
_MUTATING_VERBS = (
    "product_scope.py\" persist", "product_scope.py\" materialize",
    "product_scope.py\" revise",
    "product_scope.py\" add-item", "product_scope.py add-item",
    "product_scope.py\" set-area", "product_scope.py set-area",
    "product_scope.py\" set-component", "product_scope.py set-component",
)


def _mutating_hits(text: str) -> list[str]:
    """THE guard's predicate, single-sourced so the GREEN and RED directions exercise the SAME check.

    STRENGTH, STATED HONESTLY ([[ADR-153]] d2): this is a substring scan over ONE file's text. It
    cannot distinguish printing from executing, it is defeated by holding the script path in a
    variable, and it does NOT scan `skills/slice-candidates/SKILL.md`, where the verb legitimately
    lives. It is a cardinality-drift guard on ADR-067 section 1's subject file — not a general
    "nothing mutates the vault from /slice" enforcement, and it must not be described as one.

    A THIRD LIMITATION, FOUND BY CONSTRUCTION WHILE WIRING slice-102 AND RECORDED RATHER THAN PATCHED:
    the scan requires the verb to be ADJACENT to the script path, so any flag between them — the very
    shape `/slice-candidates` ships (`product_scope.py" --vault "$VAULT" add-item`) — slips past it.
    Not fixed here on purpose: [[ADR-153]] d1 keeps this a FLAT membership scan with no predicate
    change, because the alternative (a parser) is what DR-1 broke on its first adversarial read in
    round 4. Widening it is a separate, deliberate cut — and the honest reading is that this guard
    catches DRIFT in a counted set, not a determined bypass.
    """
    return [v for v in _MUTATING_VERBS if v in text]


def test_discover_hands_off_to_the_bootstrap():
    """The primary discovery path. Without it, nothing ever runs the decompose act."""
    text = DISCOVER.read_text(encoding="utf-8")
    assert _BOOTSTRAP in text, "discover/SKILL.md must HAND OFF to the bootstrap act"
    assert "hands_off_to" in text and "slice-candidates" in text
    # non-vacuous: the concept.json write it hands off FROM is still there
    assert "concept.json" in text


def test_adopt_carries_the_handoff_line():
    """Brownfield: a project adopting the pipeline otherwise inherits a backlog of everything WRONG with
    its code and nothing about what its product is FOR."""
    text = ADOPT.read_text(encoding="utf-8")
    assert _BOOTSTRAP in text
    assert "hands-off-to" in text


def test_slice_is_read_only_and_carries_the_backstop_notice():
    """ADR-067 section 1: /slice takes NO lock and mutates NO vault file — the entire read-your-own-writes
    and injection-ordering hazard is DISSOLVED rather than managed. The census call is the read-only
    backstop, and the notice is deliberately NOT suppressible."""
    text = SLICE.read_text(encoding="utf-8")
    assert "product_scope.py\" census" in text or "product_scope.py census" in text, (
        "/slice must consult the census as a read-only backstop"
    )
    assert "READ-ONLY" in text
    # slice-081 (m3 / FBCD-1): the mutating-verb set grew 3->4 with `set-component` (slice-084 renamed it
    # to `set-area`, keeping `set-component` as an alias — BOTH are mutating and enumerated), then 5->6
    # with slice-102's `add-item`. A slice that changes the cardinality of a counted set must extend that
    # set's enumeration (this is the ONLY enumeration guard -- there is no _DISPATCH-completeness test).
    hits = _mutating_hits(text)
    assert not hits, (
        f"/slice must never invoke a MUTATING product_scope verb ({hits}) — its PICK PHASE is a "
        f"read-only path (ADR-067 section 1, as scoped by ADR-080 + ADR-152)"
    )


def test_slice_readonly_guard_rejects_a_bare_add_item_invocation():
    """slice-102 / AC6 — BOTH DIRECTIONS, against REAL `skills/slice/SKILL.md` text.

    THE FBCD-1 GAP THIS CLOSES, FOUND BY EXECUTION: the shipped guard walked a FIXED tuple that did NOT
    contain `add-item`, so a `/slice` naming the new verb would have shipped **green by omission** —
    measured by running the SHIPPED test body against real patched SKILL.md copies. Green by omission is
    the defect the guard's own comment names, not a passing grade.

    THE RED DIRECTION IS ASSERTED, NOT ASSUMED ([[ADR-153]] d3). A guard only ever run on the passing
    case is the failure mode this slice has already paid for TWICE: round-3's B1 (a cross-check reasoned
    about rather than executed, which would have shipped the gate inert) and the DD3 spike's own T2
    (reporting PASS on a defective run). Both directions use the SAME predicate — `_mutating_hits` — so
    this cannot pass against a reimplementation that has drifted from the shipped check.
    """
    text = SLICE.read_text(encoding="utf-8")
    assert _mutating_hits(text) == [], "GREEN: the shipped file must pass"

    for injected in (
        '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" add-item --item-file "$F"',
        "$PY ${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py add-item --item-file $F",
        '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" add-item --dry-run --item-file "$F"',
    ):
        patched = text + "\n```bash\n" + injected + "\n```\n"
        assert _mutating_hits(patched), (
            f"the guard did NOT fire on a bare add-item invocation: {injected}")

    # both FORMS are enumerated, so the new verb does not inherit the pre-existing quote-only gap
    assert 'product_scope.py" add-item' in _MUTATING_VERBS
    assert "product_scope.py add-item" in _MUTATING_VERBS
    # and the mode-aware carve-out is RETIRED with the printed command ([[ADR-153]] supersedes ADR-151):
    # `--dry-run` earns no exemption, because /slice has no reason to name the verb at all.
    assert not any("dry-run" in v for v in _MUTATING_VERBS)


def test_slice_retires_the_stale_pre_materialized_claim():
    """/slice's opener CLAIMED candidates.json was 'pre-materialized from risks / diagnose findings /
    reflections / concept scope'. The last clause was FALSE in every vault ever built — this slice makes
    it true, and the prose must no longer assert it unconditionally."""
    text = SLICE.read_text(encoding="utf-8")
    assert "pre-materialized from\nrisks / diagnose findings / reflections / concept scope" not in text
    assert "reflections / concept scope) — you do NOT re-run" not in text, (
        "the stale unconditional claim survived"
    )


def test_slice_candidates_documents_the_product_mode_contract():
    """The rules a model must follow at the trust boundary. Each is load-bearing, so each is pinned."""
    text = SLICE_CANDIDATES.read_text(encoding="utf-8")
    assert "--product" in text
    assert "PRODUCT=1" in text, "the Step-0 flag parse must recognize --product (one parsing mechanism)"
    assert "NEVER emit an `id`" in text, "identity is minted by the receiver, never the model"
    assert "blocking, unproven assumption" in text, "else the candidate skips /risk-spike step-0"
    assert "depends_on" in text
    assert "product-scope" in text and "concept-scope" in text, "the supersession must be stated"


def test_cr2_every_bash_var_the_product_mode_uses_is_assigned_in_its_own_block():
    """code-review CR2 (blocker): the --product bootstrap COULD NOT EXECUTE as written. `$ITEMS` was
    used twice and assigned ZERO times, there was no temp-dir derivation, and `allowed-tools` had no
    `Write`, so the model could not author the items file at all.

    Why nothing caught it: this file's other guards only GREP SKILL.md for strings (they assert the
    hand-off is *mentioned*), and `cross_block_audit` is blind by construction to a variable that is
    NEVER assigned anywhere — it hunts a var assigned in one block and used in the NEXT. So a skill can
    be fully 'wired' by every existing check and still be dead on the first command.

    This closes that hole: every `$VAR` a bash block uses must be assigned IN that block, or be ambient
    (supplied by the SessionStart hook / the harness). That is BC-PROJ-2's real invariant, enforced.
    """
    import re

    # supplied by the SessionStart hook ($PY/$CRG), the harness (${CLAUDE_SKILL_DIR}, ${ARGUMENTS}),
    # the vault resolver, or the shell itself
    AMBIENT = {"PY", "CRG", "CLAUDE_SKILL_DIR", "ARGUMENTS", "AI_SDLC_VAULT_ROOT", "HOME", "PATH", "?"}

    text = SLICE_CANDIDATES.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
    assert blocks, "no bash blocks found — the guard would pass vacuously"

    offenders = []
    for block in blocks:
        # NOT line-anchored: real blocks assign inline after `;` / `then` / `&&`
        # (`DIAGNOSE_OUT=""; OBO=0; PRODUCT=0`). A line-anchored matcher is the exact false-positive
        # this repo's CI cross-block audit already carries a note about — don't reinvent it.
        assigned = set(re.findall(r"(?<![\w$-])(\w+)=(?!=)", block))
        assigned |= set(re.findall(r"\bfor\s+(\w+)\s+in\b", block))
        used = set(re.findall(r"\$\{?(\w+)", block))
        unresolved = {v for v in used - assigned - AMBIENT if not v.isdigit()}
        if unresolved:
            offenders.append((sorted(unresolved), block.strip().splitlines()[0][:60]))

    assert not offenders, (
        "a bash block uses a variable that is never assigned in it — the command cannot run: "
        f"{offenders}"
    )


def test_cr2_product_mode_can_author_its_items_file():
    """The decomposition is a JSON file the MODEL must write. Without `Write` in allowed-tools there is
    no way to produce it (and a bash heredoc mangles the apostrophes real item prose contains)."""
    fm = SLICE_CANDIDATES.read_text(encoding="utf-8").split("---")[1]
    tools = [t.strip() for t in
             next(ln for ln in fm.splitlines() if ln.startswith("allowed-tools:"))
             .split(":", 1)[1].split(",")]
    assert "Write" in tools, f"the model cannot author the items file: allowed-tools = {tools}"

    text = SLICE_CANDIDATES.read_text(encoding="utf-8")
    assert "aisdlc-product-scope-items.json" in text, "the items path must be a FIXED, re-derivable name"
    assert text.count("aisdlc-product-scope-items.json") >= 3, (
        "every block touching the items file must re-derive its path (vars do not persist across blocks)"
    )


def test_slice_candidates_surfaces_set_component_after_materialize():
    """slice-081 (M-add-1 / DR-1) — the POSITIVE-wiring twin of m3. This slice's whole reason to exist is
    de-inerting slice-080's lens; test_product_scope_wiring.py's own premortem (BC-PROJ-10) is that 'a
    perfectly correct product_scope.py would have shipped INERT because no consumer invoked it.' m3 pins
    the NEGATIVE wiring (/slice must NOT invoke set-component); this pins its POSITIVE twin: the
    --product flow must actually SURFACE `set-component` as the post-materialize enabler, or the producer
    ships and the lens stays inert on the real vault.

    Mirrors test_slice_candidates_documents_the_product_mode_contract: a string-in-SKILL guard on the
    hand-off the pipeline actually executes (the CONSUMER), not the CLI shape (which the unit tests cover).
    """
    text = SLICE_CANDIDATES.read_text(encoding="utf-8")
    assert "set-area" in text, (
        "slice-candidates/SKILL.md --product flow must SURFACE `set-area` (slice-084 renamed set-component "
        "-> set-area), or the producer ships INERT"
    )
    # positioned as the post-materialize enabler: it appears AFTER the materialize verb is introduced
    assert text.index("materialize") < text.rindex("set-area"), (
        "set-area must be documented as the step AFTER materialize (it annotates already-materialized "
        "capabilities)"
    )
    # non-vacuous: still the --product section, and it names what the annotation de-inerts (the product area)
    assert "--product" in text and "area" in text


def test_the_design_manifest_records_the_wiring():
    """The manifest is the SOURCE of the generated design record; a hand-off documented only in prose
    would vanish from the graph on the next aggregate.py run."""
    b0 = json.loads((ROOT / ".build" / "manifests" / "batch0.json").read_text(encoding="utf-8"))
    discover = next(s for s in b0 if s["name"] == "discover")
    assert "slice-candidates" in discover["hands_off_to"]

    b1 = json.loads((ROOT / ".build" / "manifests" / "batch1.json").read_text(encoding="utf-8"))
    sc = next(s for s in b1 if s["name"] == "slice-candidates")
    paths = {f["path"] for f in sc["file_access"]}
    assert "<vault>/concept.json" in paths
    assert "<vault>/product-scope.json" in paths
    assert any(t["name"] == "scripts.lib.product_scope" for t in sc["methodology_tools"])
