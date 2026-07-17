"""slice-068 / SC-135 — materialize the product's own scope into the candidate backlog.

THE DEFECT this pins (spike-proven, not asserted): PRODUCT-sourced candidates = 0 across all 145
candidates ever minted in two REAL vaults. `/discover` mints exactly ONE product candidate, via
concept.json's `first_slice_candidate` — which fires once, at slice 1, and never again. Everything
downstream is exhaust (risks, code-review findings, reality-surprises, reflection residues). aivlc's
orchestrator/state-machine — its actual product — was never minted as a candidate at all, so `/slice`
structurally CANNOT pick it.

THE GUARANTEE (ADR-067, which SUPERSEDES ADR-066): the model's decomposition of concept.json crosses
into the vault EXACTLY ONCE as a persisted <vault>/product-scope.json whose item ids are minted by the
RECEIVER (id_allocator, in-lock), never by the model — B1 measured 22% cross-run key agreement, so a
model-emitted key would re-mint 78% of the backlog on every run. Materialization into candidates.json
is then a deterministic, idempotent, create-only pass keyed on candidate provenance
`source: [{type: "product-scope", ref: "PS-NNN"}]` across live ∪ archive.

Covers AC1 (source type + census flip), AC2 (idempotency), AC3 (survives the ship cycle), AC4 (absent
concept fails VISIBLY), plus the ratified critique constraints: C5 (the source[] normalizer's three
shapes), C6 (the identity guard lives in `persist` — and, since slice-073 / [[ADR-080]], the
_MANAGED_KIND entry ALSO guards the remove/set legs while its append leg refuses), C7 (--scope-file implies
--dry-run), C10 (the census classifier is explicit, with an `unclassified` tripwire), C12 (materialize
does NOT require concept.json), C13 (the `ps` kind is not half-audited), C14 (a refused item withholds
its dependents transitively).

AC5 (the aivlc reality replay) lives in test_product_scope_aivlc_replay.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aivlc-vault"
SCRIPT = "scripts/lib/product_scope.py"


# ── helpers ──────────────────────────────────────────────────────────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _items(n: int = 3) -> dict:
    """A minimal model-shaped decomposition: NO ids, depends_on in run-local labels."""
    return {
        "items": [
            {
                "label": "core-engine", "title": "build-core-engine",
                "description": "The deterministic core.", "user_visible_outcome": "It runs.",
                "depends_on": [],
                "assumptions": [{"id": "A1", "statement": "The core is expressible deterministically.",
                                 "blocking": True, "spike_status": "unproven"}],
                "verification_plan": "Drive one real run end-to-end.",
            },
            {
                "label": "cli-frontend", "title": "build-cli-frontend",
                "description": "The thin CLI.", "user_visible_outcome": "Operator drives it from a terminal.",
                "depends_on": ["core-engine"],
                "assumptions": [{"id": "A1", "statement": "A CLI carries every gate.",
                                 "blocking": True, "spike_status": "unproven"}],
                "verification_plan": "A real operator answers each gate through the CLI.",
            },
            {
                "label": "state-resume", "title": "add-state-resume",
                "description": "Resume an interrupted run.", "user_visible_outcome": "Resume works.",
                "depends_on": ["core-engine"],
                "assumptions": [{"id": "A1", "statement": "Stage state is fully externalizable.",
                                 "blocking": True, "spike_status": "unproven"}],
                "verification_plan": "Kill a run mid-DAG and resume.",
            },
        ][:n]
    }


@pytest.fixture
def pvault(tmp_path: Path) -> Path:
    """A purpose-built fixture vault: a real concept.json + an empty backlog.

    C15: the census flip is only observable HERE. It cannot be demonstrated on either real vault —
    THIS vault has no concept.json (AC4's target) and aivlc is READ-ONLY (AC5) — so pinning AC1's
    target to a tmp fixture vault is deliberate, not a shortcut.
    """
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "concept.json", json.loads((FIXTURES / "concept.json").read_text(encoding="utf-8")))
    _write(v / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture",
        "counters": {"sc": 0}, "candidates": [], "pick_log": [],
    })
    return v


def _run(run_script, vault: Path, *args):
    return run_script(SCRIPT, ["--vault", str(vault), *args])


def _persist(run_script, vault: Path, items: dict, tmp_path: Path, name: str = "items.json"):
    f = tmp_path / name
    _write(f, items)
    return _run(run_script, vault, "persist", "--items-file", str(f), "--json")


def _product_candidates(vault: Path) -> list[dict]:
    out = []
    for rel in ("candidates.json", "archive/candidates.json"):
        p = vault / rel
        if not p.exists():
            continue
        for c in _read(p).get("candidates", []):
            src = c.get("source")
            if isinstance(src, list) and any(
                isinstance(s, dict) and s.get("type") == "product-scope" for s in src
            ):
                out.append(c)
    return out


# ── AC1 — the source type exists, provenance survives, the census flips 0 -> >0 ──

def test_ac1_product_scope_source_type_persists_and_census_flips(run_script, pvault, tmp_path):
    """AC1: mint PRODUCT-sourced candidates; source.type == 'product-scope' SURVIVES minting; the
    census PRODUCT count goes 0 -> >0. Provenance IS the idempotency key, so losing it is not a
    cosmetic defect — it is the resurrection bug (D2)."""
    before = json.loads(_run(run_script, pvault, "census", "--json").stdout)
    assert before["counts"]["PRODUCT"] == 0, "the fixture vault must start with zero product candidates"

    r = _persist(run_script, pvault, _items(), tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["persisted"] == 3
    assert out["materialize"]["minted_count"] == 3

    prods = _product_candidates(pvault)
    assert len(prods) == 3
    for c in prods:
        refs = [s["ref"] for s in c["source"] if s.get("type") == "product-scope"]
        assert len(refs) == 1 and refs[0].startswith("PS-"), c["source"]

    after = json.loads(_run(run_script, pvault, "census", "--json").stdout)
    assert after["counts"]["PRODUCT"] == 3, "the census must SEE the product candidates it just minted"
    assert after["unclassified"] == []


def test_ac1_minted_candidate_is_visible_at_the_real_pick_surface(run_script, pvault, tmp_path):
    """C1 (blocker): PRESENT != PICKABLE-IN-PRACTICE. /slice injects `candidates_top.py --top 5`,
    which sorts by -priority.score; an absent score reads as 0.0 and sorts DEAD LAST. A product
    candidate with no priority block is this slice's own bug reproduced one level down. Assert the
    PRODUCTION invocation shape, not merely _classify == 'pickable' (BC-PROJ-10)."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0

    r = run_script("skills/slice/scripts/candidates_top.py", ["--vault", str(pvault), "--top", "5", "--json"])
    assert r.returncode == 0, r.stderr
    top = json.loads(r.stdout)["top"]
    assert [t["id"] for t in top], "nothing reached the pick surface"
    for t in top:
        assert isinstance(t["score"], (int, float)) and t["score"] > 0, f"scored 0.0 -> sorts last: {t}"
        assert t["effort"], f"unset effort sorts last on the tie-break: {t}"
        assert t["blast_radius"], f"unset blast_radius: {t}"


def test_ac1_product_candidate_carries_blocking_assumptions(run_script, pvault, tmp_path):
    """M-add-4: a candidate with assumptions: [] SKIPS /risk-spike step-0 — the crown-jewel gate —
    on exactly the least-understood work in the product. A finding-derived candidate is a PROVEN bug
    with nothing to spike; a product capability is UNPROVEN by definition."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    for c in _product_candidates(pvault):
        blocking = [a for a in c.get("assumptions") or []
                    if a.get("blocking") and a.get("spike_status") == "unproven"]
        assert blocking, f"{c['id']} presents NOTHING to /risk-spike step-0"
        assert c.get("verification_plan")


# ── AC2 — idempotency ────────────────────────────────────────────────────────────

def test_ac2_materialize_is_idempotent(run_script, pvault, tmp_path):
    """AC2 (must-not-defer): a materializer that re-mints on every run floods the backlog with
    duplicates — strictly WORSE than the absence it fixes. The dedup key is allocator-minted
    provenance, never a model-emitted title/scope-key (B1: 22% cross-run agreement)."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    first = _read(pvault / "candidates.json")["candidates"]

    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["minted_count"] == 0
    assert out["status"] == "nothing-to-mint"
    assert out["reason"], "a 0-mint must ALWAYS state its reason — never a silent no-op"

    second = _read(pvault / "candidates.json")["candidates"]
    assert [c["id"] for c in first] == [c["id"] for c in second]
    titles = [c["title"] for c in second]
    assert len(titles) == len(set(titles)), "duplicate titles minted"


def test_ac2_persist_is_create_only(run_script, pvault, tmp_path):
    """B1 constraint 3: the scope BOUNDARY drifts run-to-run (4 of 18 items appeared in exactly one
    run), so a re-decomposition is a SEMANTIC change to the product's scope — it must be a deliberate,
    user-visible act (`revise`), never a silent side effect of re-running the skill."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    r = _persist(run_script, pvault, _items(), tmp_path, name="items2.json")
    assert r.returncode == 4, "a second persist must REFUSE (create-only), not silently re-decompose"
    assert "revise" in (r.stdout + r.stderr)


# ── AC3 — survives the ship cycle ────────────────────────────────────────────────

def test_ac3_survives_the_ship_cycle(run_script, pvault, tmp_path):
    """AC3 (RESTATED at TRI-1, M-add-1): the ORIGINAL AC3 ('assert the NEXT tranche is now present')
    was UNFALSIFIABLE once spike D1 resolved to a FULL-DAG mint — every item is minted at tick 1, so
    there IS no next tranche and the assertion passes vacuously.

    This tests the property full-DAG CAN violate, and it is the one designer-crossdomain's k8s lens
    predicted: because /commit-slice MOVES a shipped candidate into archive/, a reconciler that lists
    only the LIVE file is a controller that lists only Running pods — it faithfully RESURRECTS
    everything it has ever completed. Simulate the REAL model-mediated ship->archive move, then assert
    (a) the shipped item is NOT re-minted, and (b) every unshipped product item is still pickable."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0

    live = _read(pvault / "candidates.json")
    shipped = next(c for c in live["candidates"] if c["title"] == "build-core-engine")

    # the REAL /commit-slice shape: append to archive/, THEN remove from live (never a gap).
    shipped_copy = dict(shipped, status="shipped", progress="complete")
    _write(pvault / "archive" / "candidates.json",
           {"_schema": "aisdlc/slice-candidates@1", "candidates": [shipped_copy]})
    live["candidates"] = [c for c in live["candidates"] if c["id"] != shipped["id"]]
    _write(pvault / "candidates.json", live)

    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["minted_count"] == 0, (
        "RESURRECTION: the shipped product item was re-minted — the observed-state list must span "
        f"live ∪ archive, not live alone. minted={out.get('minted')}"
    )

    live_ids = {c["id"] for c in _read(pvault / "candidates.json")["candidates"]}
    assert shipped["id"] not in live_ids

    r = run_script("skills/slice/scripts/candidates_top.py",
                   ["--vault", str(pvault), "--top", "0", "--json"])
    top_ids = {t["id"] for t in json.loads(r.stdout)["top"]}
    remaining = [c for c in _product_candidates(pvault) if c["id"] != shipped["id"]]
    assert len(remaining) == 2
    for c in remaining:
        assert c["id"] in top_ids, f"{c['id']} survived the ship cycle but is no longer pickable"


# ── AC4 — an absent concept.json fails VISIBLY ───────────────────────────────────

def test_ac4_absent_concept_fails_visibly(run_script, tmp_path):
    """AC4 (must-not-defer): a silent no-mint here would reproduce the exact invisible failure this
    slice exists to kill. The message must NAME the remedy."""
    v = tmp_path / "empty-vault"
    v.mkdir()
    r = _run(run_script, v, "decompose-context", "--json")
    assert r.returncode == 3, f"expected exit 3 (concept absent), got {r.returncode}"
    msg = r.stdout + r.stderr
    assert "/discover" in msg, "the failure must be ACTIONABLE — name the skill that fixes it"
    assert "concept.json" in msg


@pytest.mark.skipif(
    not (Path.home() / ".aisdlc" / "aisdlc-v2-a5c48e41").is_dir(),
    reason="this project's real vault is not on this host (CI); the tmp-vault case above carries the AC",
)
def test_ac4_against_this_projects_real_vault(run_script):
    """AC4's stated target verbatim: THIS vault, 68 slices deep, where concept.json is GENUINELY
    absent (skills/discover/SKILL.md:111 declares it an all-modes output — it was never written).
    Backfilling it is out_of_scope; the code must fail visibly instead. Skips off this laptop by
    design — the portable tmp-vault case above is the durable evidence (C9)."""
    real = Path.home() / ".aisdlc" / "aisdlc-v2-a5c48e41"
    assert not (real / "concept.json").exists(), "premise changed: this vault now HAS a concept.json"
    r = _run(run_script, real, "decompose-context", "--json")
    assert r.returncode == 3
    assert "/discover" in (r.stdout + r.stderr)


def test_ac4_materialize_without_scope_names_the_bootstrap(run_script, pvault):
    """C12: materialize's inputs are product-scope.json + candidates ∪ archive — concept.json is NOT
    among them. A vault with a concept but no persisted scope has not been BOOTSTRAPPED, and the
    message must name the act that bootstraps it (M-add-2's whole point: nothing in the pipeline ever
    invoked it, so the slice would have shipped INERT)."""
    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 4
    msg = r.stdout + r.stderr
    assert "/slice-candidates --product" in msg
    assert json.loads(r.stdout)["status"] == "no-scope"


def test_c12_materialize_does_not_require_concept(run_script, pvault, tmp_path):
    """C12: `nothing-ready` is DELETED (it was vocabulary from the DROPPED ready-frontier rule). A
    valid product-scope.json whose concept.json was moved/deleted still materializes — the missing
    concept is a WARNING (concept_missing: true), never a refusal, or AC3 continuity breaks."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    (pvault / "concept.json").unlink()

    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["concept_missing"] is True
    assert out["status"] != "nothing-ready", "dropped-frontier-rule vocabulary leaked into the contract"


# ── C5 — the source[] normalizer's three shapes (APED-1: executed, not reasoned about) ──

def test_c5_iter_sources_normalizes_three_shapes():
    """C5: the real malformed shape is a SCALAR STRING ('reflect'), not a list containing one. The
    natural 'tolerant' implementation — iterate `source`, coerce str elements — iterates the STRING'S
    CHARACTERS and yields SEVEN pseudo-sources. It does not crash and does not false-match, so BOTH
    of the MV2 spike's measured properties are BLIND to it — while it silently poisons the census,
    which is AC1's entire measurement spine."""
    from scripts.lib.product_scope import iter_sources

    assert list(iter_sources({"source": [{"type": "risk", "ref": "R-1"}]})) == [{"type": "risk", "ref": "R-1"}]

    scalar = list(iter_sources({"source": "reflect"}))
    assert scalar == [{"type": "reflect", "ref": None}], (
        f"a scalar string must yield ONE pseudo-source, never one per CHARACTER: {scalar}"
    )
    assert all(len(s["type"]) > 1 for s in scalar)

    assert list(iter_sources({"source": ["slice-007-discovered"]})) == [
        {"type": "slice-007-discovered", "ref": None}
    ]
    assert list(iter_sources({"source": {"type": "risk", "ref": "R-2"}})) == [{"type": "risk", "ref": "R-2"}]
    assert list(iter_sources({})) == []
    assert list(iter_sources({"source": None})) == []
    assert list(iter_sources({"source": 17})) == []


def test_c10_census_classifier_is_explicit_and_has_a_tripwire():
    """C10: source.type is an OPEN SET (artifact_lint.py:185). An unknown value silently absorbed into
    EXHAUST would reproduce the very invisibility this slice exists to kill. The 4th `unclassified`
    bucket LISTS the raw values, so a new source type is LOUD in the census."""
    from scripts.lib.product_scope import PRODUCT_SOURCES, classify_source_type

    assert "product-scope" in PRODUCT_SOURCES
    assert "concept-scope" in PRODUCT_SOURCES, "the SUPERSEDED value must never be miscounted (C2)"
    assert classify_source_type("product-scope") == "PRODUCT"
    assert classify_source_type("concept-scope") == "PRODUCT"
    assert classify_source_type("reality-surprise") == "EXHAUST"
    assert classify_source_type("user-directed") == "HUMAN"
    assert classify_source_type("slice-007-discovered") == "EXHAUST"
    assert classify_source_type("a-type-nobody-has-invented-yet") == "UNCLASSIFIED"


def test_c10_census_unclassified_is_empty_against_the_real_aivlc_taxonomy(run_script, tmp_path):
    """C10's tripwire, executed against REAL bytes: every source.type aivlc's 14 real candidates
    actually carry must classify. An unclassified value here means the census is under-reporting."""
    v = tmp_path / "replay"
    (v / "archive").mkdir(parents=True)
    for rel in ("candidates.json", "archive/candidates.json"):
        (v / rel).write_bytes((FIXTURES / rel).read_bytes())

    r = _run(run_script, v, "census", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["unclassified"] == [], f"unknown source types in the real taxonomy: {out['unclassified']}"
    assert out["no_source"] == 0, "a source-less candidate is invisible to the census — the other hole"
    assert out["counts"]["PRODUCT"] == 0, "aivlc's real backlog is 0% product — the defect, verbatim"
    assert out["total"] == 14


# ── C6 — the identity guard is on the path that actually RUNS ────────────────────

def test_c6_persist_rejects_a_model_supplied_id(run_script, pvault, tmp_path):
    """C6: ADR-066 §2 claimed _MANAGED_KIND was 'the load-bearing line' — but `persist` must REWRITE
    depends_on labels into minted PS ids inside ONE lock, and `vault_edit append` mints internally and
    returns nothing, so persist BYPASSES vault_edit entirely. The guard therefore lives in persist's
    own in-lock reject_supplied_id call (ADR-067 §3), and it is tested by driving PERSIST — not
    vault_edit — with a model-supplied id."""
    items = _items()
    items["items"][0]["id"] = "PS-999"  # the model tries to supply an identity
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode != 0, "a model-supplied PS id must be REJECTED"
    assert "minted in-lock" in (r.stdout + r.stderr)
    assert not (pvault / "product-scope.json").exists(), "the write must not have landed"


def test_c6_product_scope_is_registered_in_managed_kind_with_a_refusing_append_leg():
    """INVERTED by slice-073 / [[ADR-080]], which supersedes ADR-067 §3 on exactly this line.

    This test used to assert the entry's ABSENCE, pinning ADR-067 §3. A pinning test must move with
    the decision it pins, or the decision is not really made — so it now pins ADR-080's two halves
    together, because either one alone is a defect:

      * REGISTERED — without it, `vault_edit remove` and `set --path items` each delete a scope item
        at rc=0 with no record, walking around cmd_revise's omission gate entirely (the more
        DISCOVERABLE door: a model told "remove PS-002" finds the generic documented verb first).
      * APPEND REFUSES — registration alone would make mint-on-append hand a real PS id to an item
        with no assumptions, whose candidate then SKIPS /risk-spike step-0 (ADR-067 §5's bypass,
        reopened by one supported command). Today-unregistered that append crashes loudly; a silent,
        legitimate-looking, contract-free item would be strictly worse.

    ADR-067 §3's FIRST half is untouched and still tested by
    test_c6_persist_rejects_a_model_supplied_id: persist keeps its own in-lock reject_supplied_id and
    never routes through vault_edit.
    """
    from scripts.lib.vault_edit import _APPEND_REFUSED_KINDS, _MANAGED_KIND

    assert _MANAGED_KIND.get(("product-scope.json", "items")) == "ps"
    assert "ps" in _APPEND_REFUSED_KINDS, (
        "registering the kind without refusing its append leg turns a loud crash into a silent "
        "contract-free mint -- the two halves of ADR-080 ship together or not at all"
    )


def test_c6_vault_edit_alloc_mints_a_ps_id(run_script, pvault):
    """`vault_edit alloc --kind ps` — the established remedy for hand-authored pre-minting."""
    r = run_script("scripts/lib/vault_edit.py",
                   ["--vault", str(pvault), "alloc", "--file", "product-scope.json", "--kind", "ps"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "PS-001"


# ── C7 — --scope-file is a READ-ONLY surface ─────────────────────────────────────

def test_c7_scope_file_implies_dry_run(run_script, pvault, tmp_path):
    """C7 (security / trust boundary): `--scope-file` WITHOUT `--dry-run` would be a WRITE path that
    mints real, monotonic, non-revocable SC candidates from an ARBITRARY, non-allocator-keyed items
    file — re-opening the exact hole ADR-066/067 exists to close. Not hypothetical: --scope-file is
    the natural mechanism a Builder reaches for when writing the AC2/AC3 tests."""
    scope = tmp_path / "arbitrary-scope.json"
    _write(scope, {"items": [{"id": "PS-042", "title": "smuggled", "depends_on": []}]})

    r = _run(run_script, pvault, "materialize", "--scope-file", str(scope), "--json")
    assert r.returncode == 2, "--scope-file without --dry-run must be a USAGE error (fail-visible)"
    assert "--dry-run" in (r.stdout + r.stderr)
    assert not (pvault / "candidates.json").read_text(encoding="utf-8").count("smuggled")


def test_c7_dry_run_writes_nothing(run_script, pvault, tmp_path):
    scope = tmp_path / "scope.json"
    _write(scope, {"items": [{"id": "PS-001", "title": "would-be-minted", "description": "x",
                              "depends_on": [], "assumptions": [], "verification_plan": "x"}]})
    before = (pvault / "candidates.json").read_bytes()

    r = _run(run_script, pvault, "materialize", "--scope-file", str(scope), "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["would_mint"] == ["PS-001"]
    assert (pvault / "candidates.json").read_bytes() == before, "a dry run WROTE to the vault"


# ── C14 / D2 — the title-collision guard withholds dependents transitively ───────

def test_c14_refused_item_withholds_its_dependents_transitively(run_script, pvault, tmp_path):
    """D2's guard: 79/79 archived candidates retained source[] — but that is a SNAPSHOT, not an
    invariant (slice-050's lesson), and the archive copy stays model-mediated and unenforced. Because
    a LOST provenance fails SILENTLY (resurrecting a shipped item forever), materialize must fail
    LOUD: a candidate carrying the item's persisted title WITHOUT the expected provenance is REFUSED.

    C14: a refused item's dependents must be withheld TRANSITIVELY. A partially-minted DAG with a
    `dependencies` entry that maps to no SC id is worse than a deferred sub-tree — candidates_top's
    _unmet_deps tests membership in live ids, so a dangling dep is SILENTLY DROPPED."""
    # a hand-written candidate already carries the persisted title, with NO product-scope provenance
    _write(pvault / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture", "counters": {"sc": 7},
        "candidates": [{
            "id": "SC-007", "title": "build-core-engine", "status": "candidate",
            "progress": "not-started", "source": [{"type": "user-directed", "ref": "a human typed it"}],
            "priority": {"score": 3, "effort": "M"},
        }],
        "pick_log": [],
    })

    r = _persist(run_script, pvault, _items(), tmp_path)
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout)["materialize"]

    refused = {x["item"] for x in m["refused"]}
    assert len(refused) == 1, m["refused"]
    withheld = {x["item"] for x in m["withheld"]}
    assert len(withheld) == 2, f"both dependents of the refused root must be withheld: {m['withheld']}"
    assert m["minted_count"] == 0
    assert all(x["root_cause"] in refused for x in m["withheld"]), "the withheld sub-tree must name its root cause"

    for c in _read(pvault / "candidates.json")["candidates"]:
        for dep in c.get("dependencies") or []:
            assert dep is not None and dep.startswith("SC-"), f"dangling dependency minted: {c}"


def test_c14_acknowledge_overrides_the_refusal(run_script, pvault, tmp_path):
    """C8: a permanently-refused item must be ACTIONABLE, not a forever-warning."""
    _write(pvault / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture", "counters": {"sc": 7},
        "candidates": [{
            "id": "SC-007", "title": "build-core-engine", "status": "candidate",
            "progress": "not-started", "source": [{"type": "user-directed", "ref": "a human typed it"}],
            "priority": {"score": 3, "effort": "M"},
        }],
        "pick_log": [],
    })
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0

    scope = _read(pvault / "product-scope.json")
    ps_id = next(i["id"] for i in scope["items"] if i["title"] == "build-core-engine")

    r = _run(run_script, pvault, "materialize", "--acknowledge", ps_id, "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["minted_count"] == 3, out
    assert out["refused"] == []


# ── C8 — the scope-correction verb ───────────────────────────────────────────────

def test_c8_revise_preserves_minted_ids_and_never_re_mints(run_script, pvault, tmp_path):
    """C8: without a `revise` verb the FIRST decomposition — a coin-flip snapshot of a stochastic
    decomposer (22% key stability, 4-of-18 boundary drift) — is frozen PERMANENTLY, and ADR-066's
    promised 'human-adjudicated diff' has no verb to execute it."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    scope = _read(pvault / "product-scope.json")
    core = next(i for i in scope["items"] if i["decomposition_label"] == "core-engine")

    # slice-073: every KEPT item is re-stated. This test's subject is identity PRESERVATION, not
    # membership -- it previously omitted the other two persisted items, and the (then absent)
    # omission gate silently honoured that as a DELETION. Re-stating them keeps the subject and both
    # assertions below unchanged; the omission path is now pinned deliberately, by
    # tests/bugs/test_product_scope_revise_contract.py (SC-160).
    others = [dict(i) for i in scope["items"] if i["id"] != core["id"]]
    revised = {"items": [
        dict(core, description="The deterministic core (sharpened after a concept revision)."),
        *others,
        {"label": "telemetry", "title": "add-telemetry", "description": "See what each stage did.",
         "user_visible_outcome": "Operator sees per-stage activity.", "depends_on": [core["id"]],
         "assumptions": [{"id": "A1", "statement": "Per-stage activity is observable.",
                          "blocking": True, "spike_status": "unproven"}],
         "verification_plan": "Drive a real run; assert each stage reports."},
    ]}
    f = tmp_path / "revised.json"
    _write(f, revised)

    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)

    after = _read(pvault / "product-scope.json")
    kept = next(i for i in after["items"] if i["decomposition_label"] == "core-engine")
    assert kept["id"] == core["id"], "an already-minted PS id must be PRESERVED by id, never re-minted"
    assert kept["description"].endswith("concept revision).")
    assert out["materialize"]["minted_count"] == 1, "only the genuinely NEW item mints"

    prods = _product_candidates(pvault)
    assert len(prods) == 4
    assert len({c["title"] for c in prods}) == 4


def test_cr1_revise_rejects_a_REPEATED_minted_id(run_script, pvault, tmp_path):
    """code-review CR1 (blocker), reproduced by execution before the fix: `revise` rejected an INVENTED
    PS id but accepted a REPEATED one. Two items both carrying PS-001 collapsed onto ONE minted SC id --
    materialize's pass 1 does `minted_ids[it["id"]] = next_id(...)`, so the second write won, and pass 2
    then stamped BOTH candidate records with it. Observed: scope ['PS-001','PS-001'] -> candidates.json
    ids ['SC-003','SC-003'], exit 0, "minted 2 product candidate(s)".

    CITATION NOTE (slice-075): that accumulator was named `ps_to_sc` when this test was written and is
    now `minted_ids` -- an expression re-cite, not a semantics change. This guard is about two SCOPE
    ITEMS sharing one id (N:1); slice-075 made the OPPOSITE direction (one capability, many candidates)
    legal and left this refusal exactly as strict.

    This is the trust boundary's SECOND crossing (persist is guarded by in-lock reject_supplied_id;
    revise was not) — BC-PROJ-6 verbatim: 'a guard on ONE write path is bypassable through another'.
    Minting is irreversible, so an ambiguous identity must STOP."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    scope = _read(pvault / "product-scope.json")
    core = next(i for i in scope["items"] if i["decomposition_label"] == "core-engine")
    before = _read(pvault / "candidates.json")["candidates"]

    dup = tmp_path / "dup.json"
    _write(dup, {"items": [dict(core), dict(core, decomposition_label="core-engine-clone",
                                 title="build-core-engine-clone")]})   # same id, twice

    r = _run(run_script, pvault, "revise", "--items-file", str(dup), "--json")
    assert r.returncode == 2, "a REPEATED minted id must be REFUSED, not aliased onto one candidate"
    assert core["id"] in (r.stdout + r.stderr)

    after = _read(pvault / "candidates.json")["candidates"]
    assert [c["id"] for c in after] == [c["id"] for c in before], "the refused revise still wrote"
    assert len({c["id"] for c in after}) == len(after), "duplicate candidate ids minted"


def test_cr1_duplicate_labels_are_refused(run_script, pvault, tmp_path):
    """Same law one level down: label -> PS id is a MAPPING, so two items sharing a label would alias
    every depends_on edge pointing at either onto whichever was minted last — a corrupted DAG."""
    items = _items()
    items["items"][1]["label"] = "core-engine"          # collide with items[0]
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode == 2
    assert "core-engine" in (r.stdout + r.stderr)
    assert not (pvault / "product-scope.json").exists()


def test_cr3_an_unresolvable_depends_on_stops_instead_of_dropping(run_script, pvault, tmp_path):
    """code-review CR3 (major): _topo and the depends_on rewrite FILTERED unknown refs out (`if d in
    known`). A typo'd label was silently DROPPED and its dependent was promoted to a false DAG ROOT.

    Not cosmetic: PRODUCT_PRIORITY is a flat constant, so TOPOLOGICAL ORDER is the only intra-product
    ranking signal — a false root jumps the queue and surfaces unready work at the pick gate, which is a
    quieter version of the very bug this module exists to fix."""
    items = _items()
    items["items"][1]["depends_on"] = ["core-engien"]   # a typo, not a real label
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode == 2, "an unresolvable depends_on must STOP, never be silently dropped"
    msg = r.stdout + r.stderr
    assert "core-engien" in msg and "false root" in msg
    assert not (pvault / "product-scope.json").exists()


def test_m1_a_no_op_materialize_writes_nothing(run_script, pvault, tmp_path):
    """code-review m1: an already-materialized vault re-ran materialize and still rewrote
    candidates.json to bump `updated` — pointless churn on a file every parallel slice contends for.
    A no-op must be a TRUE no-op: byte-identical."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    before = (pvault / "candidates.json").read_bytes()

    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["minted_count"] == 0
    assert (pvault / "candidates.json").read_bytes() == before, "a 0-mint rewrote candidates.json"


def test_c8_revise_rejects_an_invented_id(run_script, pvault, tmp_path):
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    f = tmp_path / "bad.json"
    _write(f, {"items": [{"id": "PS-404", "label": "ghost", "title": "ghost", "depends_on": []}]})
    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json")
    assert r.returncode == 2
    assert "PS-404" in (r.stdout + r.stderr)


# ── C13 — the `ps` kind does not ship HALF-audited ───────────────────────────────

def test_c13_id_allocation_audit_catches_a_stale_ps_counter(run_script, pvault, tmp_path):
    """C13 (RPCD-1): counters_violations resolved each kind's counter with a hardcoded TWO-way
    ternary (`ship_counters if kind == 'ship' else counters`). counters.ps lives in product-scope.json
    — a THIRD holder the audit never loaded — so a naive `sources['ps'] = _scan(...)` would leave the
    counter-staleness arm SILENTLY NO-OPPING, and a hand-edited-down counters.ps (which RE-ISSUES an
    existing PS id) would sail through green. 'Not half-managed' must be TESTED, not asserted."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0

    clean = run_script("scripts/lib/id_allocation_audit.py", ["--vault", str(pvault), "--json"])
    assert json.loads(clean.stdout)["counters"] == []

    scope = _read(pvault / "product-scope.json")
    assert scope["counters"]["ps"] == 3
    scope["counters"]["ps"] = 1  # hand-edited BELOW the max existing PS id -> would re-issue PS-002
    _write(pvault / "product-scope.json", scope)

    r = run_script("scripts/lib/id_allocation_audit.py", ["--vault", str(pvault), "--json"])
    assert r.returncode == 1, "a stale counters.ps must be a VIOLATION, not a silent no-op"
    viol = json.loads(r.stdout)["counters"]
    assert any("ps" in v for v in viol), viol


def test_bc_proj_6_the_detective_backstop_catches_a_hand_authored_duplicate_ps_id(run_script, pvault,
                                                                                  tmp_path):
    """BC-PROJ-6: 'a guard on ONE write path is bypassable through another.'

    slice-073 / [[ADR-080]] CORRECTED THIS TEST'S STATED PREMISE — the test itself is unchanged and
    still passes, which is exactly why the stale premise was worth hunting down (FBCD-1: count the
    anchor at EVERY site, not the first two). It used to say the append path was unguarded 'because
    product-scope.json/items is deliberately NOT a _MANAGED_KIND (ADR-067 section 3)'. That is no
    longer true: the kind IS registered and `vault_edit append`/`remove`/`set --path items` now all
    REFUSE.

    What remains true — and is what this test actually exercises — is that a RAW FILE WRITE goes
    through no guard at all. The body below writes product-scope.json directly (the `vault_edit
    rewrite` CAS verb, deliberately out of scope per ADR-080 #6, is the same class). No preventive
    control can cover that, on ANY vault file, so the DETECTIVE control has to. This is that control,
    and it is why C13's fix mattered: id_allocation_audit must actually SEE `ps`.
    """
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0

    scope = _read(pvault / "product-scope.json")
    scope["items"].append(dict(scope["items"][0], id="PS-002"))   # a forged/colliding id, hand-written
    _write(pvault / "product-scope.json", scope)

    r = run_script("scripts/lib/id_allocation_audit.py", ["--vault", str(pvault), "--json"])
    assert r.returncode == 1, "a DUPLICATE PS id slipped past every control — preventive AND detective"
    viol = json.loads(r.stdout)["counters"]
    assert any("ps" in v and "DUPLICATE" in v for v in viol), viol


def test_bc_proj_7_the_naive_source_pattern_is_gone_from_build_backlog():
    """BC-PROJ-7 (anti-revert): centralizing a pattern is only real if something ENFORCES that no copy
    survives. Both of build_backlog's source[] loops crashed on real data; a future edit could
    re-inline `c.get("source") or []` and silently reintroduce the AttributeError."""
    import re

    src = (Path(__file__).resolve().parents[1] / "skills" / "slice-candidates" / "scripts"
           / "build_backlog.py").read_text(encoding="utf-8")
    # strip comments (whole-line AND trailing) -- the fixed lines deliberately QUOTE the old pattern in
    # a trailing comment, and a guard that trips over its own documentation is a guard nobody keeps
    code = [ln.split("#", 1)[0] for ln in src.splitlines()]
    naive = [ln for ln in code if re.search(r'for\s+\w+\s+in\s+.*\.get\(["\']source["\']\)', ln)]
    assert not naive, f"the naive source[] iteration is back in build_backlog.py: {naive}"
    assert "_iter_sources(c)" in src, "both loops must route through the shared selector"
    assert src.count("_iter_sources(c)") == 2, "BOTH arms (cmd_build live + _archive_scan) must route"


# ══════════════════════════════════════════════════════════════════════════════════
# slice-075 / SC-159 — a capability has MANY candidates ([[ADR-086]], supersedes ADR-085)
#
# THE DEFECT: `_plan`'s derived map was `{ref: id}`, so a capability's SECOND child silently overwrote
# the first, and the collapse winner became the answer to every question about that capability —
# including a dependent's `dependencies[]`, frozen into an append-only record with no un-mint.
#
# NOT SPECULATIVE. Every artifact in this slice's design record (3 blind designers, both step-0 spikes,
# the design spike, ADR-085, the first Critic) asserted N>1 was unreachable until an out-of-scope
# splitter landed. FALSE — `sc` is a registered vault_edit managed kind and _APPEND_REFUSED_KINDS is
# {"ps"}, so ONE `vault_edit append` grows a child set at rc=0. These fixtures therefore grow it THROUGH
# THAT REAL PATH (BC-PROJ-10: drive the AC with the PRODUCTION invocation shape, never hand-built JSON)
# — the one claim nobody executed is the one these tests execute.
# ══════════════════════════════════════════════════════════════════════════════════

VAULT_EDIT = "scripts/lib/vault_edit.py"


def _lib():
    """The module under test, imported for its pure helpers."""
    import sys as _sys
    root = str(Path(__file__).resolve().parents[1])
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from scripts.lib import product_scope
    return product_scope


def _append_child(run_script, vault: Path, tmp_path: Path, ps_ref: str, title: str, name: str):
    """Grow a capability's child set through the REAL production write path (M-add-1 / BC-PROJ-10).

    This is `vault_edit append` — the same in-lock, id-minting, managed-kind path /repro and /discover
    use. It mints the SC id itself (the payload carries NO id), which is precisely why N>1 is reachable
    today with no splitter. Hand-writing candidates.json would prove nothing about the real hazard.
    """
    f = tmp_path / name
    _write(f, {
        "title": title,
        "status": "candidate",
        "progress": "not-started",
        "source": [{"type": "product-scope", "ref": ps_ref}],
        "description": f"a second slice of {ps_ref}",
        "priority": {"score": 3, "severity": "medium", "effort": "M"},
    })
    r = run_script(VAULT_EDIT, ["append", "--vault", str(vault), "--file", "candidates.json",
                               "--array", "candidates", "--content-file", str(f)])
    assert r.returncode == 0, f"the real append path refused (rc={r.returncode}): {r.stderr}"
    return r


def _done(run_script, vault: Path, *extra):
    r = _run(run_script, vault, "done", "--json", *extra)
    return r, (json.loads(r.stdout) if r.stdout.strip() else None)


def _ps_id(vault: Path, label: str = "core-engine") -> str:
    scope = _read(vault / "product-scope.json")
    return next(i for i in scope["items"] if i["decomposition_label"] == label)["id"]


# ── AC1 — a capability carries N>1 children, and the relation resolves BOTH ways ──

def test_ac1_a_capability_carries_many_candidates_and_resolves_both_ways(run_script, pvault, tmp_path):
    """AC1: N>1 linked candidates, every one resolving back to its parent, and the reverse lookup
    returning ALL of them — not merely the collapse winner."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)

    first = [c for c in _read(pvault / "candidates.json")["candidates"]
             if any(s.get("ref") == ps for s in c["source"])]
    assert len(first) == 1, "precondition: the once-act mints exactly one child"

    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-2", "child2.json")
    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-3", "child3.json")

    kids = [c for c in _read(pvault / "candidates.json")["candidates"]
            if any(s.get("ref") == ps for s in c["source"])]
    assert len(kids) == 3, "N>1 children per capability must be REPRESENTABLE"

    m = _lib()
    # child -> parent is TOTAL: every one of the N resolves to the SAME parent
    assert {m.owner_ref(c) for c in kids} == {ps}, "every child must resolve back to its parent PS id"
    assert all(m.owner_refs(c) == [ps] for c in kids), "a well-formed child claims exactly ONE parent"

    # parent -> children is COMPLETE: the reverse lookup returns all N, not the first
    _r, out = _done(run_script, pvault, "--item", ps)
    got = out["items"][0]["children"]
    assert len(got) == 3, (
        "the reverse lookup must return ALL children — a scalar map here IS the bug this slice fixes")
    assert got == sorted(got, key=m._sc_sort_key), "children come back in canonical numeric order"


def test_ac1_children_are_sorted_numerically_not_lexicographically():
    """SC-9 must precede SC-10. Lexicographic ordering inverts them, and
    migrate-legacy-unpadded-ids-to-canonical-zero-pad is a LIVE candidate, so the mixed-pad corpus is
    real, not hypothetical."""
    m = _lib()
    assert sorted(["SC-10", "SC-9", "SC-002"], key=m._sc_sort_key) == ["SC-002", "SC-9", "SC-10"]
    # a malformed id ORDERS LAST but is never DROPPED — a child that VANISHES from its parent's set is
    # exactly the silent loss `done` exists to refuse
    assert sorted(["SC-10", "junk", "SC-9"], key=m._sc_sort_key) == ["SC-9", "SC-10", "junk"]


# ── AC2 — idempotence at N>1 holds BY CONSTRUCTION, not via the collapse ─────────

def test_ac2_materialize_mints_nothing_when_a_capability_has_many_children(run_script, pvault,
                                                                           tmp_path):
    """AC2, the sharpest one. Idempotence must survive N>1 — and hold because the KEY SET is unchanged,
    not because a dict collapse happens to hide the duplicates. The create-only test is KEY membership;
    the collapse corrupted the VALUE. Asserted at N>1 AND at N==1."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-2", "child2.json")

    before = _read(pvault / "candidates.json")
    ids_before = [c["id"] for c in before["candidates"]]
    counter_before = before.get("counters", {}).get("sc")
    bytes_before = (pvault / "candidates.json").read_bytes()

    r = _run(run_script, pvault, "materialize", "--json")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["minted_count"] == 0, "a fully-materialized scope must mint NOTHING at N>1"
    assert out["would_mint"] == []

    after = _read(pvault / "candidates.json")
    assert [c["id"] for c in after["candidates"]] == ids_before, "no new candidate id at N>1"
    assert after.get("counters", {}).get("sc") == counter_before, "the sc counter must not move"
    assert (pvault / "candidates.json").read_bytes() == bytes_before, (
        "a no-op materialize writes NOTHING — byte-identical, not merely equivalent")

    # the N>1 capability reports `already`, and is never re-minted, refused, or withheld
    entry = next(a for a in out["already_materialized"] if a["item"] == ps)
    assert "candidates" in entry and len(entry["candidates"]) == 2, (
        "at N>1 the already-report lists ALL children")
    assert "candidate" not in entry, (
        "at N>1 the scalar `candidate` must be OMITTED — emitting one would be the arbitrary "
        "winner-pick this slice exists to kill; a consumer must KeyError loudly instead")
    assert not out["refused"] and not out["withheld"]

    # ...and the N==1 siblings still report the byte-identical legacy scalar shape (AC5)
    others = [a for a in out["already_materialized"] if a["item"] != ps]
    assert others and all("candidate" in a and "candidates" not in a for a in others), (
        "at N==1 the already-entry shape is UNCHANGED from pre-slice-075")


def test_ac2_the_cr1_belt_is_key_membership_not_value_truthiness():
    """M3 / CC-001. The CR1 belt is `iid in ps_to_scs or iid in minted_ids` — literal KEY membership,
    matching the create-only test. A `.get()` form is value TRUTHINESS: the two agree only while no key
    can map to an empty list, so the single most natural refactor a reader makes to a multimap
    (pre-seeding `{it["id"]: [] for it in items}`) would silently DISABLE the belt while the create-only
    test kept refusing — two guards on one map with two membership semantics, on the module's
    most-defended invariant, against a substrate with no un-mint. This pins it against that refactor."""
    m = _lib()
    src = Path(m.__file__).read_text(encoding="utf-8")
    code = [ln.split("#", 1)[0] for ln in src.splitlines()]
    assert not [ln for ln in code if "ps_to_scs.get(iid)" in ln], (
        "the CR1 belt must never be value-truthiness (.get()) — it is KEY membership")
    assert any("if iid in ps_to_scs or iid in minted_ids:" in ln for ln in code), (
        "the belt must test BOTH the observed map and the mint accumulator")

    # the property itself: a key mapping to an EMPTY list is STILL membership -> still refuses.
    # This is exactly the case `.get()` would wave through.
    ps_to_scs, minted_ids = {"PS-001": []}, {}
    assert ("PS-001" in ps_to_scs or "PS-001" in minted_ids) is True


# ── AC3 — the census counts ALL N children as PRODUCT ────────────────────────────

def test_ac3_census_counts_every_child_of_a_capability_as_product(run_script, pvault, tmp_path):
    """AC3 needs NO production code: `classify_candidate` reads each candidate's OWN source type
    independently, so cardinality never entered into it. That makes this a REGRESSION assertion —
    proving the widening did not break a classifier that was already right. (Rewriting working code to
    fix a defect it does not have is exactly what spike constraint A1.4 forbids.)"""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    before = json.loads(_run(run_script, pvault, "census", "--json").stdout)["counts"]["PRODUCT"]

    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-2", "child2.json")
    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-3", "child3.json")

    out = json.loads(_run(run_script, pvault, "census", "--json").stdout)
    assert out["counts"]["PRODUCT"] == before + 2, "every one of the N children counts as PRODUCT"
    assert out["counts"]["UNCLASSIFIED"] == 0, "no child may land in UNCLASSIFIED"
    assert out["unclassified"] == []


# ── AC4 — `done` is decidable, 4-valued, and reads live UNION archive ────────────

def _ship(vault: Path, sc_id: str, status: str = "shipped"):
    """Move a candidate live -> archive, the way /commit-slice Step 6 does: it leaves the archive copy
    byte-identical apart from `status`, and REMOVES the live row. A child that has shipped exists ONLY
    in archive/candidates.json — which is why any predicate reading live alone is wrong by construction.
    """
    live_p, arch_p = vault / "candidates.json", vault / "archive" / "candidates.json"
    live = _read(live_p)
    row = next(c for c in live["candidates"] if c["id"] == sc_id)
    live["candidates"] = [c for c in live["candidates"] if c["id"] != sc_id]
    _write(live_p, live)
    arch = _read(arch_p) if arch_p.exists() else {"_schema": "aisdlc/slice-candidates@1",
                                                  "project": "fixture", "candidates": []}
    arch["candidates"].append(dict(row, status=status))
    _write(arch_p, arch)


def test_ac4_done_is_false_until_every_child_is_archived(run_script, pvault, tmp_path):
    """AC4: a capability with 3 children — 2 archived, 1 live — is NOT done; archive the third and it
    is. The predicate must read live UNION archive, since a shipped child lives only in the archive."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    first = next(c for c in _read(pvault / "candidates.json")["candidates"]
                 if any(s.get("ref") == ps for s in c["source"]))
    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-2", "child2.json")
    _append_child(run_script, pvault, tmp_path, ps, "build-core-engine-part-3", "child3.json")

    kids = [c["id"] for c in _read(pvault / "candidates.json")["candidates"]
            if any(s.get("ref") == ps for s in c["source"])]
    assert len(kids) == 3

    _r, out = _done(run_script, pvault, "--item", ps)
    assert out["items"][0]["state"] == "in-progress", "no child archived -> in-progress"

    _ship(pvault, kids[0])
    _ship(pvault, kids[1])
    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] == "in-progress", "SOME-but-not-all archived must NOT be done"
    assert e["pending"] == [kids[2]] and sorted(e["archived"]) == sorted(kids[:2])
    assert len(e["children"]) == 3, (
        "the archived children must still be SEEN — a live-only read would lose them entirely")

    _ship(pvault, kids[2])
    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] == "done", "every child archived -> done"
    assert e["pending"] == [] and len(e["archived"]) == 3
    assert e["archived_composition"] == {"shipped": 3, "rejected": 0}
    assert first["id"] in e["children"]


def test_ac4_done_is_never_a_bool_and_the_empty_set_is_not_done(run_script, pvault, tmp_path):
    """spike A2.4: `all([])` is vacuously TRUE, so a capability with zero children must never report
    done. Guarded BY TYPE — `no-children` is a distinct STATE, not a bool a caller can misread."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    live = _read(pvault / "candidates.json")
    live["candidates"] = [c for c in live["candidates"]
                          if not any(s.get("ref") == ps for s in c["source"])]
    _write(pvault / "candidates.json", live)

    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] == "no-children", "an empty child set is NOT a finished one"
    assert e["state"] is not True and e["state"] is not False, "state is a 4-valued enum, never a bool"
    assert e["children"] == []


def test_ac4_a_child_in_BOTH_files_counts_live_the_safe_direction(run_script, pvault, tmp_path):
    """The two-file read is NOT atomic (two files, two locks, no cross-file transaction, no lock taken
    here). A child interleaved by /commit-slice's move appears in BOTH files; the conservative join
    counts it LIVE -> done=false, a false NEGATIVE. Archive-first would make it appear in NEITHER — it
    would VANISH and the parent would report `done` with a child still in flight."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    kid = next(c for c in _read(pvault / "candidates.json")["candidates"]
               if any(s.get("ref") == ps for s in c["source"]))

    # the torn state: appended to archive, not yet removed from live
    arch_p = pvault / "archive" / "candidates.json"
    _write(arch_p, {"_schema": "aisdlc/slice-candidates@1", "project": "fixture",
                    "candidates": [dict(kid, status="shipped")]})

    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] == "in-progress", "a torn row must count LIVE — never report a premature done"
    assert e["archived"] == [] and e["pending"] == [kid["id"]]
    assert e["children"] == [kid["id"]], (
        "a torn child is ONE child, not two: _observed is a LIST CONCAT, so a row present in BOTH "
        "files arrives twice. The join is specified as a G-Set union (idempotent) — list concat is "
        "not. The pre-slice-075 scalar map was accidentally immune (dict overwrite); the widening is "
        "what exposes it, so the dedupe ships with the widening.")


def test_ac4_a_torn_child_is_not_double_counted_in_a_dependents_dependencies(run_script, pvault,
                                                                            tmp_path):
    """The torn-read duplicate's REAL blast radius, and why the dedupe is not cosmetic: an undeduped
    child fans out into a dependent's `dependencies[]`, which is frozen at mint into an append-only
    record with NO un-mint. A wrong count is recoverable; a poisoned backlog row is not."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps_core = _ps_id(pvault, "core-engine")
    kid = next(c for c in _read(pvault / "candidates.json")["candidates"]
               if any(s.get("ref") == ps_core for s in c["source"]))

    # tear it: present in archive AND still live (the /commit-slice mid-move state)
    _write(pvault / "archive" / "candidates.json",
           {"_schema": "aisdlc/slice-candidates@1", "project": "fixture",
            "candidates": [dict(kid, status="shipped")]})

    items = _items(2)
    items["items"] = [dict(i, id=_ps_id(pvault, i["label"])) for i in items["items"]]
    items["items"].append({
        "label": "torn-dependent", "title": "build-torn-dependent",
        "description": "minted while its parent's child is mid-move.",
        "user_visible_outcome": "It waits.", "depends_on": [ps_core],
        "assumptions": [{"id": "A1", "statement": "It composes.", "blocking": True,
                         "spike_status": "unproven"}],
        "verification_plan": "Drive it.",
    })
    f = tmp_path / "torn.json"
    _write(f, items)
    assert _run(run_script, pvault, "revise", "--items-file", str(f), "--json").returncode == 0

    dep = next(c for c in _read(pvault / "candidates.json")["candidates"]
               if c["title"] == "build-torn-dependent")
    assert dep["dependencies"] == [kid["id"]], (
        f"a torn parent-child must appear ONCE in a dependent's dependencies[], got "
        f"{dep['dependencies']} — a duplicate here is permanent (append-only, no un-mint)")


def test_ac4_done_is_pure_and_writes_nothing(run_script, pvault, tmp_path):
    """spike A2.5: running the predicate leaves the vault byte-identical. It takes no lock precisely
    because it writes nothing."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    snap = {p.name: p.read_bytes() for p in pvault.glob("*.json")}
    r, _out = _done(run_script, pvault)
    assert r.returncode == 0
    assert {p.name: p.read_bytes() for p in pvault.glob("*.json")} == snap, (
        "`done` must be a PURE function of the files it reads")


def test_ac4_done_refuses_an_item_the_scope_does_not_carry(run_script, pvault, tmp_path):
    """A typo'd id is a USAGE error, never an empty result — it must not read as `no-children`."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    r = _run(run_script, pvault, "done", "--item", "PS-999", "--json")
    assert r.returncode == 2
    assert json.loads(r.stdout)["status"] == "usage"


def test_ac4_done_without_a_scope_names_the_bootstrap_act(run_script, pvault):
    """Reuses _scope(required=True): exit 4, naming the act that bootstraps it."""
    r = _run(run_script, pvault, "done", "--json")
    assert r.returncode == 4
    assert json.loads(r.stdout)["status"] == "no-scope"
    assert "/slice-candidates --product" in (r.stdout + r.stderr)


# ── AC5 — the N==1 path is byte-identical ────────────────────────────────────────

def test_ac5_a_single_candidate_capability_is_byte_identical(run_script, pvault, tmp_path):
    """AC5: the minted RECORD and the plan JSON for a single-candidate PS item are unchanged.

    Captured against the pre-change code and diffed here (the committed expectation below IS that
    capture — the pre-change values were recorded before the edit landed)."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    rec = next(c for c in _read(pvault / "candidates.json")["candidates"]
               if c["title"] == "build-cli-frontend")

    # the minted record's shape and its dependencies[] — the fields the widening could have moved
    assert list(rec.keys()) == [
        "id", "title", "status", "progress", "slice", "claimed_by", "started_at", "source",
        "description", "rationale", "user_visible_outcome", "dependencies", "priority",
        "assumptions", "verification_plan", "history",
    ], "the minted record's field set/order must be unchanged"
    assert isinstance(rec["id"], str), "the candidate id is a SCALAR — never a list (M2's nested-list)"
    assert rec["dependencies"] == [_ps_id(pvault) and next(
        c["id"] for c in _read(pvault / "candidates.json")["candidates"]
        if c["title"] == "build-core-engine")]
    assert rec["source"] == [{"type": "product-scope", "ref": _ps_id(pvault, "cli-frontend")}]

    out = json.loads(_run(run_script, pvault, "materialize", "--dry-run", "--json").stdout)
    assert all("candidate" in a and "candidates" not in a for a in out["already_materialized"]), (
        "at N==1 every already-entry keeps the legacy scalar shape, byte-identically")


def test_ac5_two_depends_on_edges_preserve_depends_on_ORDER(run_script, pvault, tmp_path):
    """M-add-3, and the fixture WITHOUT which AC5 cannot see the break.

    Today's line is a comprehension in DEPENDS_ON order. ADR-085 specified a 'sorted union', which
    REORDERS dependencies[] at N==1 for any item with >=2 edges — an AC5 violation with zero multi-child
    items involved, writing a reordered array into an append-only backlog with no un-mint. It passes on
    the real vault only because PS-004 is the sole item with a depends_on and it has exactly ONE edge:
    byte-identical BY LUCK OF THE CORPUS. So: build the shape the corpus lacks.

    `dep-b` depends_on ['state-resume', 'core-engine'] — deliberately NOT in mint order, so a sorted
    union would visibly reorder it."""
    items = _items()
    items["items"].append({
        "label": "dep-b", "title": "build-dep-b", "description": "two edges, deliberately unsorted.",
        "user_visible_outcome": "It composes both.",
        "depends_on": ["state-resume", "core-engine"],
        "assumptions": [{"id": "A1", "statement": "Both compose.", "blocking": True,
                         "spike_status": "unproven"}],
        "verification_plan": "Drive both.",
    })
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0

    by_title = {c["title"]: c for c in _read(pvault / "candidates.json")["candidates"]}
    core, resume = by_title["build-core-engine"]["id"], by_title["add-state-resume"]["id"]
    deps = by_title["build-dep-b"]["dependencies"]

    assert deps == [resume, core], (
        f"depends_on ORDER must be preserved: expected [{resume}, {core}] (state-resume first, as "
        f"declared), got {deps}. A sorted union would emit {sorted([resume, core])} — an AC5 break at "
        f"N==1, into an append-only record.")
    assert deps != sorted(deps), "the fixture must actually DISCRIMINATE the two rules"


def test_ac5_an_intra_batch_depends_on_edge_resolves_to_a_scalar_id(run_script, pvault, tmp_path):
    """M-add-2: the PRIMARY path — the full-DAG first mint, where every item mints on the first tick and
    a dependent's parent was minted in the SAME batch (the module carries a topological sort for exactly
    this; the real vault's PS-004 is this shape).

    Transcribing the design literally left the mint-write scalar in a list-valued map, so the fan-out
    iterated a minted id's CHARACTERS -> dependencies == ['-','0','2','C','S'] (slice-068's per-character
    pseudo-source trap, in this very file). The accumulator split makes it unrepresentable BY TYPE. This
    test is the execution that proves it."""
    assert _persist(run_script, pvault, _items(), tmp_path).returncode == 0
    by_title = {c["title"]: c for c in _read(pvault / "candidates.json")["candidates"]}
    core = by_title["build-core-engine"]["id"]

    for title in ("build-cli-frontend", "add-state-resume"):
        deps = by_title[title]["dependencies"]
        assert deps == [core], f"{title}: intra-batch dep must be the parent's scalar id, got {deps}"
        assert all(len(d) > 1 for d in deps), (
            f"{title}: dependencies[] contains single CHARACTERS {deps} — the minted id was iterated "
            f"as a string. This is the type-heterogeneous-map failure the accumulator split prevents.")


def test_ac5_a_dependent_depends_on_ALL_children_of_its_parent(run_script, pvault, tmp_path):
    """The fan-out rule itself ([[ADR-086]] §4), DERIVED from the existing consumer rather than chosen:
    candidates_top._unmet_deps is `[d for d in dependencies if d in live_ids]` — live == unmet, archived
    == met. So 'depends on every child of PS-X' IS 'blocked until PS-X is done'.

    Pre-slice-075 this emitted ONE ARBITRARY child (the collapse winner — and, proven at triage, the
    winner was the APPENDED row, not the minted one)."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0   # core-engine + cli
    ps_core = _ps_id(pvault, "core-engine")
    _append_child(run_script, pvault, tmp_path, ps_core, "build-core-engine-part-2", "child2.json")

    kids = sorted([c["id"] for c in _read(pvault / "candidates.json")["candidates"]
                   if any(s.get("ref") == ps_core for s in c["source"])],
                  key=_lib()._sc_sort_key)
    assert len(kids) == 2

    # a NEW dependent of core-engine, minted now that core-engine has TWO children
    items = _items(2)
    items["items"] = [dict(i, id=_ps_id(pvault, i["label"])) for i in items["items"]]
    items["items"].append({
        "label": "late-dependent", "title": "build-late-dependent",
        "description": "minted after the parent grew a second child.",
        "user_visible_outcome": "It waits for ALL of core-engine.",
        "depends_on": [ps_core],
        "assumptions": [{"id": "A1", "statement": "It composes.", "blocking": True,
                         "spike_status": "unproven"}],
        "verification_plan": "Drive it.",
    })
    f = tmp_path / "revised.json"
    _write(f, items)
    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json")
    assert r.returncode == 0, r.stderr

    dep = next(c for c in _read(pvault / "candidates.json")["candidates"]
               if c["title"] == "build-late-dependent")
    assert dep["dependencies"] == kids, (
        f"a dependent must depend on ALL {len(kids)} children of its parent, numeric-sorted within — "
        f"expected {kids}, got {dep['dependencies']}. One arbitrary child IS the bug.")


# ── the tombstone — a HEURISTIC, and deliberately narrow (critique M4) ───────────

def test_torn_provenance_forces_unknown_never_done(run_script, pvault, tmp_path):
    """A child stripped of its source[] VANISHES from its parent's child set — the parent would then
    report `done` with an unaccounted-for child in flight. history[].ref is the second witness (free:
    _candidate_from already writes it); disagreement forces `unknown`, never `done`."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    live = _read(pvault / "candidates.json")
    kid = next(c for c in live["candidates"] if any(s.get("ref") == ps for s in c["source"]))

    # strip the source[] witness but leave history[] intact — the exact silent-loss shape
    kid["source"] = [{"type": "reflection", "ref": None}]
    _write(pvault / "candidates.json", live)

    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] == "unknown", (
        "a stripped child must force `unknown` — WITHOUT the tombstone this reports `no-children`, "
        "which is indistinguishable from 'never materialized'")
    assert e["torn_provenance"] == [kid["id"]]
    assert kid["id"] in e["reason"] and "REMEDY" in e["reason"]


def test_torn_provenance_ignores_free_prose_history_refs(run_script, pvault, tmp_path):
    """M4, the false-positive the narrow discriminator exists to avoid — and the reason it keys on the
    PRODUCER's shape rather than the ref's TEXT.

    MEASURED on the real backlog: history[].ref is free-form prose carrying 149 DISTINCT values across
    98/168 candidates ('slice-058', 'slice-023 ADR-015 deferred', one 396-character paragraph). A
    `^PS-\\d+$` regex over ALL of history[] would fire on any actor that ever appends PS-shaped prose —
    and the damage is STICKY: history[] is append-only with no un-write, so a false `unknown` could
    never be cleared. This is a non-product candidate whose history mentions PS-001 in prose."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps = _ps_id(pvault)
    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-900", "title": "an unrelated exhaust candidate", "status": "candidate",
        "progress": "not-started", "source": [{"type": "reflection", "ref": None}],
        "description": "no product-scope provenance at all",
        "history": [
            {"event": "created", "by": "reflect", "at": "2026-01-01T00:00:00Z",
             "ref": f"spun out of {ps} work — see {ps} for the background"},
            {"event": "note", "by": "slice-candidates", "at": "2026-01-02T00:00:00Z", "ref": ps},
        ],
    })
    _write(pvault / "candidates.json", live)

    _r, out = _done(run_script, pvault, "--item", ps)
    e = out["items"][0]
    assert e["state"] != "unknown", (
        "free prose mentioning a PS id must NOT poison a capability — the discriminator is "
        "event=='created' AND by=='slice-candidates' AND ref in the LIVE scope ids, not a regex")
    assert "SC-900" not in e["children"], "a candidate with no product-scope source is not a child"


def test_torn_provenance_ignores_a_ref_outside_the_live_scope(run_script, pvault, tmp_path):
    """LIVE-scope membership, not a regex: a CUT or legacy PS id must not resurrect a sticky `unknown`
    on a capability that no longer exists."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-901", "title": "orphan of a cut capability", "status": "candidate",
        "progress": "not-started", "source": [{"type": "reflection", "ref": None}],
        "history": [{"event": "created", "by": "slice-candidates", "at": "2026-01-01T00:00:00Z",
                     "ref": "PS-404"}],       # never in this scope
    })
    _write(pvault / "candidates.json", live)

    r, out = _done(run_script, pvault)
    assert r.returncode == 0
    assert out["counts"]["unknown"] == 0, "a ref outside the live scope must not force `unknown`"


# ── the two-parent refusal — an ambiguous identity STOPS (must-not-defer #1) ─────

def test_two_parent_child_refuses_the_ITEM_and_mints_nothing_for_it(run_script, pvault, tmp_path):
    """must-not-defer #1: N candidates per capability is now legal, but a candidate claiming TWO parents
    is an AMBIGUOUS identity and must refuse LOUDLY, minting nothing — widening the relation must not
    widen the silence. owner_ref alone cannot see it (it takes the first ref and walks on), which is
    why owner_refs exists.

    Shaped per M-add-4 to the module's own D2 pattern: refuse the affected ITEM, not the whole VERB —
    a single offending row in the append-only archive would otherwise block EVERY capability's
    materialize forever, with no un-write to fix it."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps_a, ps_b = _ps_id(pvault, "core-engine"), _ps_id(pvault, "cli-frontend")

    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-902", "title": "a child claiming two parents", "status": "candidate",
        "progress": "not-started",
        "source": [{"type": "product-scope", "ref": ps_a},
                   {"type": "product-scope", "ref": ps_b}],
    })
    _write(pvault / "candidates.json", live)

    m = _lib()
    assert m.owner_refs(live["candidates"][-1]) == [ps_a, ps_b], "the plural form SEES both claims"
    assert m.owner_ref(live["candidates"][-1]) == ps_a, "the singular form would silently pick the first"

    before = (pvault / "candidates.json").read_bytes()
    r = _run(run_script, pvault, "materialize", "--json")
    out = json.loads(r.stdout)

    refused_items = {x["item"] for x in out["refused"]}
    assert refused_items == {ps_a, ps_b}, "BOTH claimed parents are ambiguous and must refuse"
    assert out["minted_count"] == 0
    assert (pvault / "candidates.json").read_bytes() == before, "a refusal mints NOTHING"

    reason = next(x for x in out["refused"] if x["item"] == ps_a)["reason"]
    assert "SC-902" in reason, "the refusal names the offending CANDIDATE"
    assert ps_a in reason and ps_b in reason, "...and BOTH claimed parents"
    assert "REMEDY" in reason and "vault_edit" in reason, (
        "...and a FILLABLE remedy naming the act — the module's own convention is that every refusal "
        "names the ACT and the remedy, never just the payload shape")
    # an ambiguous parent must NOT be laundered as a clean no-op
    assert ps_a not in {a["item"] for a in out["already_materialized"]}


def test_two_parent_refusal_does_not_block_unaffected_capabilities(run_script, pvault, tmp_path):
    """The D2 pattern's whole point (M-add-4): per-ITEM, not per-VERB. An unaffected, not-yet-minted
    capability still mints while an ambiguous sibling refuses.

    The ambiguous parent is `cli-frontend`; the newly-added capability `state-resume` depends on
    `core-engine` (NOT the ambiguous one), so it is genuinely unaffected. (A dependent OF the ambiguous
    parent is correctly withheld — that is code-review CR2, covered by its own test; conflating the two
    is exactly the bug CR2 fixed.)"""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps_b = _ps_id(pvault, "cli-frontend")

    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-903", "title": "ambiguous child", "status": "candidate", "progress": "not-started",
        "source": [{"type": "product-scope", "ref": ps_b},
                   {"type": "product-scope", "ref": "PS-404"}],
    })
    _write(pvault / "candidates.json", live)

    # add a THIRD capability (state-resume) that depends on core-engine, NOT the ambiguous cli-frontend
    items = _items(3)
    items["items"] = [dict(i, id=_ps_id(pvault, i["label"])) if i["label"] in ("core-engine",
                                                                              "cli-frontend") else i
                      for i in items["items"]]
    f = tmp_path / "grown.json"
    _write(f, items)
    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json")
    assert r.returncode == 0, r.stderr
    out = r.stdout and json.loads(r.stdout)["materialize"]

    assert ps_b in {x["item"] for x in out["refused"]}, "the ambiguous capability refuses"
    assert out["minted_count"] == 1, (
        "...and the unaffected NEW capability still mints — a per-VERB refusal would strand the "
        "whole product behind one bad row")
    assert any(c["title"] == "add-state-resume"
               for c in _read(pvault / "candidates.json")["candidates"])


# ── code-review CR1/CR2 — the ambiguity guard reaches the READ path and the WITHHOLD loop ──
#
# The mint-path two-parent refusal alone was not enough: `cmd_done` (read path) and `_plan`'s
# transitive-withhold loop each had their OWN view of who a capability's children are, and neither
# consulted the ambiguity. Both are now derived from the shared children_by_parent, so an ambiguous
# child is seen everywhere at once (CR3's SSOT point).

def test_cr1_done_reports_unknown_not_done_for_an_ambiguous_child(run_script, pvault, tmp_path):
    """code-review CR1 (blocker), execution-proven: a child claiming TWO parents was filed under its
    FIRST parent only, so the SECOND parent reported `done` (or no-children) while a LIVE child still
    claimed it — a false `done`, the one thing done's error_model forbids. Both claimed parents must
    now report `unknown`."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps_a, ps_b = _ps_id(pvault, "core-engine"), _ps_id(pvault, "cli-frontend")

    # ship PS-B's own legitimate child, so WITHOUT the fix PS-B would look `done`
    kid_b = next(c for c in _read(pvault / "candidates.json")["candidates"]
                 if any(s.get("ref") == ps_b for s in c["source"]))
    _ship(pvault, kid_b["id"])

    # now add a LIVE child claiming BOTH PS-A and PS-B
    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-902", "title": "a child claiming two parents", "status": "candidate",
        "progress": "not-started",
        "source": [{"type": "product-scope", "ref": ps_a},
                   {"type": "product-scope", "ref": ps_b}],
    })
    _write(pvault / "candidates.json", live)

    _r, out = _done(run_script, pvault)
    by_item = {e["item"]: e for e in out["items"]}
    assert by_item[ps_b]["state"] == "unknown", (
        "PS-B has a LIVE child (SC-902) claiming it — it must NOT report `done`; the ambiguity is the "
        "safe-side `unknown`")
    assert by_item[ps_a]["state"] == "unknown", "PS-A equally has an ambiguous child"
    assert "SC-902" in by_item[ps_b]["ambiguous_children"]
    assert "SC-902" in by_item[ps_b]["reason"] and "REMEDY" in by_item[ps_b]["reason"]
    assert out["counts"]["done"] == 0, "no capability may be `done` while an ambiguous child is live"


def test_cr2_dependent_of_an_ambiguous_materialized_parent_is_withheld(run_script, pvault, tmp_path):
    """code-review CR2 (blocker), execution-proven: `_plan`'s withhold loop treated a materialized
    parent as satisfied via `d in ps_to_scs` WITHOUT consulting `ambiguous`, so a dependent of an
    ambiguous-but-already-materialized parent minted with the ambiguous child frozen into its
    append-only `dependencies[]` (`['SC-001','SC-902']`) — the very deps the refusal reason calls
    'undefined'. The dependent must instead be WITHHELD, rooted at the ambiguous parent."""
    assert _persist(run_script, pvault, _items(2), tmp_path).returncode == 0
    ps_a = _ps_id(pvault, "core-engine")

    # make PS-A ambiguous: a second live child claiming PS-A and a non-scope parent
    live = _read(pvault / "candidates.json")
    live["candidates"].append({
        "id": "SC-902", "title": "ambiguous child of core", "status": "candidate",
        "progress": "not-started",
        "source": [{"type": "product-scope", "ref": ps_a},
                   {"type": "product-scope", "ref": "PS-404"}],
    })
    _write(pvault / "candidates.json", live)

    # a NEW dependent of the (now ambiguous, already-materialized) PS-A
    items = _items(2)
    items["items"] = [dict(i, id=_ps_id(pvault, i["label"])) for i in items["items"]]
    items["items"].append({
        "label": "dependent-of-ambiguous", "title": "build-dependent-of-ambiguous",
        "description": "depends on an ambiguous parent.", "user_visible_outcome": "It waits.",
        "depends_on": [ps_a],
        "assumptions": [{"id": "A1", "statement": "It composes.", "blocking": True,
                         "spike_status": "unproven"}],
        "verification_plan": "Drive it.",
    })
    f = tmp_path / "dep.json"
    _write(f, items)
    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["materialize"]

    assert ps_a in {x["item"] for x in out["refused"]}, "the ambiguous parent refuses"
    withheld_items = {w["item"] for w in out["withheld"]}
    assert any("dependent-of-ambiguous" in w or "build-dependent-of-ambiguous" == w
               for w in withheld_items) or not any(
        c["title"] == "build-dependent-of-ambiguous"
        for c in _read(pvault / "candidates.json")["candidates"]), (
        "the dependent of an ambiguous parent must be WITHHELD, never minted with a frozen ambiguous dep")
    # the decisive assertion: the dependent must NOT exist with an ambiguous child in its deps
    dep = next((c for c in _read(pvault / "candidates.json")["candidates"]
                if c["title"] == "build-dependent-of-ambiguous"), None)
    assert dep is None, (
        "the dependent minted anyway — its dependencies[] would freeze the ambiguous SC-902, which the "
        "refusal itself declares undefined, into an append-only record with no un-mint")
