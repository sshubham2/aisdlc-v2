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
    materialize's pass 1 does `ps_to_sc[it["id"]] = next_id(...)`, so the second write won, and pass 2
    then stamped BOTH candidate records with it. Observed: scope ['PS-001','PS-001'] -> candidates.json
    ids ['SC-003','SC-003'], exit 0, "minted 2 product candidate(s)".

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
