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
    for mutating in ("product_scope.py\" persist", "product_scope.py\" materialize",
                     "product_scope.py\" revise"):
        assert mutating not in text, (
            f"/slice must never invoke a MUTATING product_scope verb ({mutating}) — it is a read-only "
            f"pick path (ADR-067 section 1)"
        )


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
