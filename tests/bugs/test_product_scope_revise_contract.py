"""slice-073 / SC-160 + SC-161 — `product_scope.py revise` contract defects.

Two defects in ONE contract (the revise crossing), each reproduced by execution — independently, by
the first Critic and DR-1, during the architect design tournament of 2026-07-16. Both arms are pinned
here because they are the SAME boundary: the guard that makes an item spikeable (arm b) is the guard
that makes it un-editable, and the whole-list replace that enables a legitimate correction (arm a) is
the one that silently destroys the scope of record.

ARM (a) — SILENT DROP (SC-160)
  Bug:      cmd_revise (scripts/lib/product_scope.py:783-862) is a whole-list REPLACE, not a delta.
            A revise payload that omits an already-materialized PS id deletes that scope item and
            exits 0 green. The computed `holder["dropped"]` (:847) is returned to stdout only (:860)
            and never persisted — product-scope.json carries no `revisions` key at all — so the
            deletion leaves ZERO durable trace.
  Expected: REFUSED with a non-zero exit naming the omitted id; product-scope.json unchanged.
  Actual:   exit 0, "items now: 1", PS-002 gone, no record anywhere.
  Why it matters: a model handed "revise the scope" emits a delta, because that is what the word
            means. product-scope.json is the PRODUCT'S SCOPE OF RECORD.

ARM (b) — POST-SPIKE FREEZE (SC-161)
  Bug:      _check_contract (:411-435) requires every item to carry >=1 blocking assumption whose
            spike_status is "unproven" (the default, :448). cmd_revise re-runs it over the FULL item
            list (:809), so a list containing even ONE item whose assumption has been proven is
            rejected wholesale.
  Expected: a full-list revise carrying a proven assumption succeeds; both items preserved.
  Actual:   exit 2, "scope item 'core-engine' carries no BLOCKING unproven assumption...".
  Why it matters: the requirement is deliberate and CORRECT at persist time (ADR-067 §5 — /risk-spike
            step-0 skips a candidate with no blocking unproven assumption, so an item without one
            would bypass the reality gate). It was simply never re-thought for revise, where the
            population necessarily includes items whose spikes have already run.

The fix must close arm (a) WITHOUT re-opening the step-0 bypass that arm (b)'s guard exists to
prevent. That regression (a FRESH persist carrying an unspikeable item is still refused) is an AC of
the slice, not of this repro.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aivlc-vault"
SCRIPT = "scripts/lib/product_scope.py"
SCOPE = "product-scope.json"


# ── helpers (mirroring tests/test_product_scope.py's conventions) ────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _two_items() -> dict:
    """A minimal model-shaped decomposition: NO ids (the receiver mints them), labels for depends_on."""
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
        ]
    }


@pytest.fixture
def pvault(tmp_path: Path) -> Path:
    """A purpose-built fixture vault: a real concept.json (persist requires it) + an empty backlog."""
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


def _persist_two(run_script, vault: Path, tmp_path: Path):
    f = tmp_path / "items.json"
    _write(f, _two_items())
    r = _run(run_script, vault, "persist", "--items-file", str(f), "--json")
    assert r.returncode == 0, f"fixture setup failed: {r.stderr}"
    scope = _read(vault / SCOPE)
    assert [i["id"] for i in scope["items"]] == ["PS-001", "PS-002"], scope["items"]
    return scope


def _revise(run_script, vault: Path, tmp_path: Path, items: list[dict], *extra):
    f = tmp_path / "revision.json"
    _write(f, {"items": items})
    return _run(run_script, vault, "revise", "--items-file", str(f), "--json", *extra)


def _vault_edit(run_script, vault: Path, *args):
    return run_script("scripts/lib/vault_edit.py", ["--vault", str(vault), *args])


# ── ARM (a) — SC-160: a delta-shaped revise silently deletes the omitted item ────

def test_revise_omitting_a_materialized_item_is_refused_and_leaves_the_scope_intact(
    run_script, pvault, tmp_path
):
    """A revise payload that omits PS-002 must be REFUSED naming PS-002 — not silently delete it.

    Today: exit 0, PS-002 erased from the product's scope of record, no durable trace.
    """
    scope_before = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    # The delta a model emits when told "revise the scope": re-state the item it means to keep.
    # PS-002 is not mentioned — not cut, just absent. Nothing in the contract says it must be here.
    r = _revise(run_script, pvault, tmp_path, [dict(scope_before["items"][0])])

    combined = r.stdout + r.stderr
    assert r.returncode != 0, (
        "revise ACCEPTED a payload omitting the already-materialized PS-002 and exited 0. "
        f"An omission must be refused, never treated as a deletion. stdout={r.stdout!r}"
    )
    assert "PS-002" in combined, (
        f"the refusal must NAME the omitted id so the caller can act on it; got: {combined!r}"
    )

    after = (pvault / SCOPE).read_text(encoding="utf-8")
    assert after == raw_before, (
        "a REFUSED revise must leave product-scope.json byte-identical; it was rewritten."
    )
    assert [i["id"] for i in _read(pvault / SCOPE)["items"]] == ["PS-001", "PS-002"]


# ── ARM (b) — SC-161: one proven assumption freezes the scope forever ────────────

def test_full_list_revise_succeeds_when_an_assumption_has_been_proven(
    run_script, pvault, tmp_path
):
    """A FULL-list revise carrying a spiked (proven) item must succeed — nothing is being dropped.

    Today: exit 2 via _check_contract's blocking-unproven requirement, which is correct at PERSIST
    time but was never re-thought for revise, where spikes have necessarily already run.
    """
    scope = _persist_two(run_script, pvault, tmp_path)
    items = [dict(i) for i in scope["items"]]

    # /risk-spike step-0 proved PS-001's blocking assumption. Carry that verdict back into the scope.
    items[0]["assumptions"] = [dict(a, spike_status="proven") for a in items[0]["assumptions"]]
    assert items[0]["assumptions"], "fixture invariant: assumptions must be NON-empty"

    # Nothing omitted: both ids re-emitted verbatim. This is the well-formed revise.
    r = _revise(run_script, pvault, tmp_path, items)

    assert r.returncode == 0, (
        "a full-list revise carrying a PROVEN assumption was refused — the scope is frozen the "
        f"moment any item is spiked. exit={r.returncode} stderr={r.stderr!r}"
    )
    out = _read(pvault / SCOPE)
    assert [i["id"] for i in out["items"]] == ["PS-001", "PS-002"], (
        f"both items must survive a full-list revise; got {out['items']!r}"
    )
    proven = [a["spike_status"] for a in out["items"][0]["assumptions"]]
    assert proven == ["proven"], f"the spike verdict must persist; got {proven!r}"


# ── AC1(b)/(c) + M2 — the SIBLING write paths (BC-PROJ-6) ───────────────────────
#
# These live here, beside the revise contract they belong to, rather than in
# tests/test_vault_edit.py: they are the SAME contract's other doors. A guard on
# cmd_revise that a model walks around via the generic, documented `vault_edit remove`
# is theatre, and the whole point of BC-PROJ-6 is that the surface -- not the function --
# is the unit of protection. Keeping them in one file makes that surface readable.


def test_vault_edit_remove_refuses_to_delete_a_scope_item(run_script, pvault, tmp_path):
    """AC1(b) / ADR-080 #1. Today: rc=0 and PS-002 is GONE, no record, no refusal.

    This is the MORE discoverable door: a model told "remove PS-002 from the scope" finds the
    generic documented `vault_edit remove` long before it finds `product_scope revise`.
    """
    _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    r = _vault_edit(run_script, pvault, "remove", "--file", SCOPE, "--array", "items", "--id", "PS-002")

    assert r.returncode != 0, (
        "vault_edit remove DELETED a product scope item at exit 0 -- the third silent-delete door, "
        f"walking straight around cmd_revise's omission gate. stdout={r.stdout!r}"
    )
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before, (
        "a refused remove must leave product-scope.json byte-identical"
    )
    assert [i["id"] for i in _read(pvault / SCOPE)["items"]] == ["PS-001", "PS-002"]


def test_vault_edit_set_path_items_refuses_to_replace_the_scope_array(run_script, pvault, tmp_path):
    """AC1(c) / ADR-080 #1. Executed WITHOUT the registration this is rc=0 and PS-002 is gone with
    no record -- a THIRD silent-delete door, as bad as `remove`. The registration closes it for FREE
    (first Critic M2(a): the design under-claimed its own fix and shipped no test for it -- this is
    that test)."""
    _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    r = _vault_edit(run_script, pvault, "set", "--file", SCOPE, "--path", "items", "--json", "[]")

    assert r.returncode != 0, (
        f"vault_edit set --path items EMPTIED the product's scope at exit 0. stdout={r.stdout!r}"
    )
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before
    assert [i["id"] for i in _read(pvault / SCOPE)["items"]] == ["PS-001", "PS-002"]


def test_vault_edit_append_refuses_to_mint_a_contract_free_scope_item(run_script, pvault, tmp_path):
    """M2 (first Critic major, RAISED to blocker by DR-1) / ADR-080 #2.

    The registration that closes `remove`/`set` OPENS `append`: mint-on-append would hand a real
    PS id to an item with NO assumptions, `_check_contract` never runs, and the item flows
    _plan:534 -> _candidate_from:508 -> a candidate with assumptions=[] -> /risk-spike step-0
    yields zero targets -> SKIP. That is ADR-067 section 5's bypass reopened by ONE supported
    command, and a REGRESSION: today (unregistered) the same append lands an item with no id and
    crashes LOUDLY at product_scope.py:490/:512. A loud crash traded for a silent, real-id,
    contract-free scope item would be a strictly worse world -- so the append leg must REFUSE.
    """
    _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    ghost = json.dumps({"title": "a capability with NO assumptions at all",
                        "decomposition_label": "ghost"})
    r = run_script("scripts/lib/vault_edit.py",
                   ["--vault", str(pvault), "append", "--file", SCOPE, "--array", "items", "--stdin"],
                   stdin=ghost)

    assert r.returncode != 0, (
        "vault_edit append MINTED a contract-free scope item -- a real PS id on an item with no "
        f"blocking assumption, which SKIPS /risk-spike step-0. stdout={r.stdout!r}"
    )
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before, (
        "a refused append must leave product-scope.json byte-identical (counters.ps included)"
    )
    after = _read(pvault / SCOPE)
    assert [i["id"] for i in after["items"]] == ["PS-001", "PS-002"]
    assert after["counters"]["ps"] == 2, "the allocator counter must not have been bumped"


# ── AC2 — the explicit cut escape + the append-only revisions[] ledger ──────────


def test_an_explicit_cut_removes_the_item_and_records_it_in_revisions(run_script, pvault, tmp_path):
    """AC2. revise is the ONLY intended path that removes an item, so a bare refusal without an
    escape would freeze the scope in the other direction. The cut is durable IN THE FILE -- prose on
    stdout is exactly the defect (:847 computes `dropped`, :860 throws it to stdout, nothing persists).
    """
    scope = _persist_two(run_script, pvault, tmp_path)

    r = _revise(run_script, pvault, tmp_path, [dict(scope["items"][0])],
                "--cut", "PS-002", "--reason", "descoped after the concept revision")

    assert r.returncode == 0, f"an explicit --cut must be ACCEPTED; exit={r.returncode} {r.stderr!r}"
    after = _read(pvault / SCOPE)
    assert [i["id"] for i in after["items"]] == ["PS-001"], "the cut item must be gone"

    revs = after.get("revisions")
    assert isinstance(revs, list) and len(revs) == 1, (
        f"exactly ONE revisions[] record must be appended; got {revs!r}"
    )
    rec = revs[0]
    assert rec["cut"] == ["PS-002"], f"the record must NAME the cut id; got {rec!r}"
    assert rec["reason"] == "descoped after the concept revision", rec
    assert rec["items_before"] == 2 and rec["items_after"] == 1, (
        f"the record must carry the resulting counts; got {rec!r}"
    )
    assert rec.get("at"), "the record must be timestamped"

    # it SURVIVES a re-read -- the ledger is the durable trace, not a return value
    assert _read(pvault / SCOPE)["revisions"][0]["cut"] == ["PS-002"]


def test_an_add_only_revise_records_the_added_ids_with_a_null_reason(run_script, pvault, tmp_path):
    """AC2, narrowed at TRI-1 (M3): `--reason` is required iff `--cut`. An ADD is self-describing --
    the item's own title/description IS the reason -- while a CUT destroys the only record of what
    was there. So an add-only revise records added ids + counts and tolerates reason=null."""
    scope = _persist_two(run_script, pvault, tmp_path)
    items = [dict(i) for i in scope["items"]]
    items.append({
        "label": "telemetry", "title": "add-telemetry", "description": "See what each stage did.",
        "user_visible_outcome": "Operator sees per-stage activity.", "depends_on": ["PS-001"],
        "assumptions": [{"id": "A1", "statement": "Per-stage activity is observable.",
                         "blocking": True, "spike_status": "unproven"}],
        "verification_plan": "Drive a real run; assert each stage reports.",
    })

    r = _revise(run_script, pvault, tmp_path, items)          # NO --reason, and none is required

    assert r.returncode == 0, f"an add-only revise must not require --reason; {r.stderr!r}"
    after = _read(pvault / SCOPE)
    assert [i["id"] for i in after["items"]] == ["PS-001", "PS-002", "PS-003"]

    revs = after["revisions"]
    assert len(revs) == 1, f"one record per membership change; got {revs!r}"
    assert revs[0]["added"] == ["PS-003"], f"the record must name the added id; got {revs[0]!r}"
    assert revs[0]["cut"] == []
    assert revs[0]["reason"] is None, "reason is OPTIONAL on the add path (null when not supplied)"
    assert revs[0]["items_before"] == 2 and revs[0]["items_after"] == 3


def test_revisions_history_is_append_only_across_two_revises(run_script, pvault, tmp_path):
    """must-not-defer: 'a revise may never rewrite or truncate prior history'. That IS the defect's
    own lesson -- create-only persist was mistaken for append-only history."""
    scope = _persist_two(run_script, pvault, tmp_path)

    r = _revise(run_script, pvault, tmp_path, [dict(scope["items"][0])],
                "--cut", "PS-002", "--reason", "first cut")
    assert r.returncode == 0, r.stderr
    first = _read(pvault / SCOPE)["revisions"][0]

    kept = [dict(_read(pvault / SCOPE)["items"][0])]
    kept.append({
        "label": "telemetry", "title": "add-telemetry", "description": "Later addition.",
        "user_visible_outcome": "Operator sees activity.", "depends_on": [],
        "assumptions": [{"id": "A1", "statement": "Activity is observable.",
                         "blocking": True, "spike_status": "unproven"}],
        "verification_plan": "Drive a real run.",
    })
    r2 = _revise(run_script, pvault, tmp_path, kept)
    assert r2.returncode == 0, r2.stderr

    revs = _read(pvault / SCOPE)["revisions"]
    assert len(revs) == 2, f"the second revise must APPEND, never replace; got {revs!r}"
    assert revs[0] == first, "the FIRST record must survive the second revise byte-for-byte"
    assert revs[0]["cut"] == ["PS-002"] and revs[1]["cut"] == []


def test_a_no_op_revise_appends_no_record(run_script, pvault, tmp_path):
    """The ledger records MEMBERSHIP changes. A revise that only edits a description is not a
    membership event, and a record for it would make the ledger noise rather than a retirement
    history -- which is what B1 makes it load-bearing as."""
    scope = _persist_two(run_script, pvault, tmp_path)
    items = [dict(i) for i in scope["items"]]
    items[0] = dict(items[0], description="The deterministic core (sharpened).")

    r = _revise(run_script, pvault, tmp_path, items)
    assert r.returncode == 0, r.stderr

    after = _read(pvault / SCOPE)
    assert after["items"][0]["description"].endswith("(sharpened).")
    assert "revisions" not in after or after["revisions"] == [], (
        f"a membership-preserving revise must not grow the ledger; got {after.get('revisions')!r}"
    )


def test_a_cut_requires_a_reason(run_script, pvault, tmp_path):
    """AC2 / M3's taste resolution, enforced rather than documented: a cut destroys the only record
    of what was there, so its `reason` is the record."""
    scope = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    r = _revise(run_script, pvault, tmp_path, [dict(scope["items"][0])], "--cut", "PS-002")

    assert r.returncode != 0, "a --cut without --reason must be refused"
    assert "--reason" in (r.stdout + r.stderr), "the refusal must name the remedy"
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before


def test_a_cut_naming_an_id_not_in_the_scope_is_refused(run_script, pvault, tmp_path):
    """A typo must STOP, not degrade into the very omission it authorizes (BC-PROJ-6)."""
    scope = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    r = _revise(run_script, pvault, tmp_path, [dict(i) for i in scope["items"]],
                "--cut", "PS-009", "--reason", "typo")

    assert r.returncode != 0, "a --cut naming an id the scope does not carry must be refused"
    assert "PS-009" in (r.stdout + r.stderr), "the refusal must name the offending id"
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before


def test_a_cut_contradicted_by_the_payload_keeping_it_is_refused(run_script, pvault, tmp_path):
    """Contradictory intent: PS-002 is simultaneously cut and re-stated as kept. Refuse rather than
    silently picking a winner -- an ambiguous identity must STOP (the module's own law)."""
    scope = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    r = _revise(run_script, pvault, tmp_path, [dict(i) for i in scope["items"]],
                "--cut", "PS-002", "--reason", "contradicted")

    assert r.returncode != 0, "an id both cut AND kept must be refused, never silently resolved"
    assert "PS-002" in (r.stdout + r.stderr)
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before


# ── AC3 — the step-0 bypass stays CLOSED at persist ─────────────────────────────


def test_a_fresh_persist_still_refuses_an_item_with_no_blocking_unproven_assumption(
    run_script, pvault, tmp_path
):
    """AC3 / must-not-defer #1 (ADR-067 section 5). Relaxing _check_contract for `revise` must NOT
    relax it for `persist`. This is the whole reason the guard exists: a candidate with no blocking
    unproven assumption SKIPS /risk-spike step-0, and a product capability is unproven BY DEFINITION.
    """
    payload = _two_items()
    payload["items"][0]["assumptions"] = [
        {"id": "A1", "statement": "Already known.", "blocking": True, "spike_status": "proven"}
    ]
    f = tmp_path / "unspikeable.json"
    _write(f, payload)

    r = _run(run_script, pvault, "persist", "--items-file", str(f), "--json")

    assert r.returncode == 2, (
        "a FRESH persist carrying an item with no blocking UNPROVEN assumption must still be "
        f"refused exit 2 -- the step-0 bypass must stay closed. exit={r.returncode}"
    )
    assert not (pvault / SCOPE).exists(), "the refused persist still created the scope file"


# ── AC4 — the refusal names the ACTUAL cause ───────────────────────────────────


def test_the_contract_refusal_never_claims_empty_assumptions_when_assumptions_exist(
    run_script, pvault, tmp_path
):
    """AC4. Today the refusal claims ``assumptions: []`` while assumptions is NON-empty -- maximally
    misleading at exactly the boundary the message exists to explain. Three distinct causes must be
    named distinctly: genuinely absent / present-but-none-blocking / present-but-all-proven."""
    payload = _two_items()
    # present, blocking, but ALL PROVEN -- the cause is NOT "assumptions: []"
    payload["items"][0]["assumptions"] = [
        {"id": "A1", "statement": "Already proven at step-0.", "blocking": True,
         "spike_status": "proven"}
    ]
    f = tmp_path / "proven.json"
    _write(f, payload)
    r = _run(run_script, pvault, "persist", "--items-file", str(f), "--json")
    combined = r.stdout + r.stderr

    assert r.returncode == 2, r.stdout
    assert "assumptions: []" not in combined, (
        "the refusal claimed `assumptions: []` against a NON-empty assumptions list -- that is the "
        f"AC4 defect verbatim. got: {combined!r}"
    )
    assert "core-engine" in combined, "the refusal must still name the offending item"

    # present, NON-empty, but none BLOCKING -- also not "assumptions: []"
    payload2 = _two_items()
    payload2["items"][0]["assumptions"] = [
        {"id": "A1", "statement": "A non-blocking nicety.", "blocking": False,
         "spike_status": "unproven"}
    ]
    f2 = tmp_path / "nonblocking.json"
    _write(f2, payload2)
    r2 = _run(run_script, pvault, "persist", "--items-file", str(f2), "--json")
    combined2 = r2.stdout + r2.stderr
    assert r2.returncode == 2
    assert "assumptions: []" not in combined2, (
        f"a present-but-none-blocking item was refused with an empty-list claim: {combined2!r}"
    )

    # the GENUINELY empty case MAY still say it
    payload3 = _two_items()
    payload3["items"][0]["assumptions"] = []
    f3 = tmp_path / "empty.json"
    _write(f3, payload3)
    r3 = _run(run_script, pvault, "persist", "--items-file", str(f3), "--json")
    assert r3.returncode == 2


# ── AC5 — back-compat with the LIVE, revisions-less file ───────────────────────


def test_a_no_op_revise_leaves_the_scope_byte_identical_apart_from_revised_at(
    run_script, pvault, tmp_path
):
    """AC5. `revisions` is written as `data['revisions'] = (data.get('revisions') or []) + [rec]`
    ONLY when the item set changed -- never `setdefault` -- so a no-op revise never grows the key.
    That is what makes this fall out rather than needing a migration."""
    scope = _persist_two(run_script, pvault, tmp_path)
    before = _read(pvault / SCOPE)
    assert "revisions" not in before, "fixture invariant: a freshly persisted scope has no ledger"

    r = _revise(run_script, pvault, tmp_path, [dict(i) for i in scope["items"]])
    assert r.returncode == 0, r.stderr

    after = _read(pvault / SCOPE)
    b = {k: v for k, v in before.items() if k != "revised_at"}
    a = {k: v for k, v in after.items() if k != "revised_at"}
    assert a == b, (
        "a no-op full-list revise must leave the scope identical apart from revised_at; "
        f"diff keys: {sorted(set(a) ^ set(b)) or [k for k in a if a[k] != b.get(k)]}"
    )
    assert "revisions" not in after, "a no-op revise must NOT grow the revisions key"


def test_a_legacy_scope_with_no_revisions_key_lints_clean_and_is_never_refused(
    run_script, pvault, tmp_path
):
    """AC5 / spike A1's two constraints, pinned together because they must ship ATOMICALLY: adding
    `revisions` to the canonical example WITHOUT the OPTIONAL_KEYS entry reds every live file.

    Deliberately a FIXTURE of the live shape, NOT a read of this host's real vault: a test that
    depends on the developer's vault is how SC-166's stale-premise sentinel went red, and conftest
    strips AI_SDLC_VAULT_ROOT for exactly this reason.
    """
    from scripts.lib.artifact_lint import _load_examples, lint_artifact

    _persist_two(run_script, pvault, tmp_path)
    legacy = _read(pvault / SCOPE)
    assert "revisions" not in legacy, "the live shape carries no revisions key -- that is the point"
    assert set(legacy) == {"_schema", "project", "concept_sha256", "decomposed_at", "counters",
                           "items"}, f"the live top-level shape drifted: {sorted(legacy)}"

    examples = _load_examples()
    violations = lint_artifact(legacy, "product-scope", examples["product-scope"], "legacy-shape")
    assert violations == [], (
        "the LIVE revisions-less product-scope.json must lint CLEAN once the canonical example "
        f"documents revisions[] -- add it to artifact_lint.OPTIONAL_KEYS. got: {violations}"
    )

    # and a reader must treat the absent key as an EMPTY HISTORY, never a refusal
    r = _revise(run_script, pvault, tmp_path, [dict(i) for i in legacy["items"]])
    assert r.returncode == 0, (
        f"a legacy file with no revisions key must never be refused; got {r.returncode}: {r.stderr!r}"
    )


# ── M-add-1 — the gate is BIDIRECTIONAL and IN-LOCK ────────────────────────────


def test_the_membership_gate_refuses_a_payload_id_absent_from_the_in_lock_scope():
    """M-add-1 (DR-1's missed finding). The invented-id guard at :800-806 tests the PRE-LOCK
    `existing` snapshot. That was safe only while nothing could remove an item -- `existing` was a
    SUBSET of `cur`. `--cut` falsifies that premise: post-cut, `existing` is neither a subset nor a
    superset of `cur`, so a payload composed before a parallel writer's cut can RESURRECT the cut id
    and silently re-adopt its shipped candidate (the exemption then waves it through as
    MATERIALIZED). Both directions must be re-checked IN-LOCK against `cur`.

    Unit-tested against the gate directly: the race cannot be driven through a subprocess CLI, and
    a test that could not actually observe `existing != cur` would be pinning the wrong thing.
    """
    from scripts.lib.product_scope import _Refuse, _check_membership

    cur = {"PS-001": {"id": "PS-001"}}                       # PS-002 was CUT by a parallel writer
    payload = [{"id": "PS-001"}, {"id": "PS-002"}]           # composed BEFORE that cut -- revives it

    with pytest.raises(_Refuse) as exc:
        _check_membership(payload, cur, [], "payload")
    assert "PS-002" in str(exc.value), "the refusal must name the resurrected id"
    assert exc.value.code == 2

    # the well-formed cases still pass cleanly
    _check_membership([{"id": "PS-001"}], cur, [], "payload")                  # exact
    _check_membership([{"id": "PS-001"}, {"label": "new"}], cur, [], "payload")  # add (no id)


def test_the_membership_gate_refuses_an_omission_and_names_every_unaccounted_id():
    """AC1(a) at the unit boundary: every id in the IN-LOCK `cur` is kept or explicitly cut."""
    from scripts.lib.product_scope import _Refuse, _check_membership

    cur = {"PS-001": {"id": "PS-001"}, "PS-002": {"id": "PS-002"}, "PS-003": {"id": "PS-003"}}

    with pytest.raises(_Refuse) as exc:
        _check_membership([{"id": "PS-001"}], cur, [], "payload")
    msg = str(exc.value)
    assert "PS-002" in msg and "PS-003" in msg, f"EVERY un-accounted id must be named; got {msg!r}"
    assert "--cut" in msg, "the refusal must name the remedy"

    # explicitly cut -> accepted
    _check_membership([{"id": "PS-001"}], cur, ["PS-002", "PS-003"], "payload")


def test_the_exemption_keys_on_materialized_not_on_carrying_a_minted_id(run_script, pvault, tmp_path):
    """ADR-079 section 1's ONE load-bearing decision, pinned (code-review CR2).

    The design tournament materially disagreed here and the DESIGN SPIKE settled it by executing the
    exploit: the id-keyed rule (`exempt = set(cur)` -- exempt anything carrying a minted id) REOPENS
    the ADR-067 section 5 bypass, because an item can carry a minted id while never having been
    MATERIALIZED. The correct rule is: exempt iff a candidate exists THROUGH WHICH /risk-spike step-0
    could actually have run.

    CR2 caught that nothing pinned it -- swapping the rejected rule back in left the whole suite
    green, so the slice's central decision was a comment, not a contract. This is that contract.

    THE REACHABLE GHOST STATE: materialize REFUSES a provenance-integrity collision (a candidate
    already carries the item's title without its product-scope provenance) and takes NO action. So
    PS-002 is persisted, carries a real minted id, and has NO candidate -- step-0 never ran on it and
    never could have. Under the id-keyed rule it would be EXEMPT and its all-proven assumptions would
    sail through. Under the materialized-keyed rule it is correctly REFUSED.
    """
    scope = _persist_two(run_script, pvault, tmp_path)

    # Manufacture the ghost: strip PS-002's candidate provenance so it is no longer materialized,
    # exactly as a refused-collision item would be. PS-001 keeps its candidate.
    cands = _read(pvault / "candidates.json")
    kept = []
    for c in cands["candidates"]:
        srcs = [s for s in (c.get("source") or []) if isinstance(s, dict)]
        if any(s.get("ref") == "PS-002" for s in srcs):
            continue                       # PS-002 has NO candidate -> never materialized
        kept.append(c)
    cands["candidates"] = kept
    _write(pvault / "candidates.json", cands)

    from scripts.lib.product_scope import owner_ref
    refs = {owner_ref(c) for c in kept}
    assert "PS-001" in refs and "PS-002" not in refs, f"fixture invariant broken: {refs}"

    # Both items claim their spikes are done. PS-001's claim is legitimate (it IS materialized);
    # PS-002's is not (no candidate ever existed, so step-0 could never have run on it).
    items = [dict(i) for i in scope["items"]]
    for it in items:
        it["assumptions"] = [dict(a, spike_status="proven") for a in it["assumptions"]]

    r = _revise(run_script, pvault, tmp_path, items)
    combined = r.stdout + r.stderr

    assert r.returncode != 0, (
        "the all-proven UNMATERIALIZED item PS-002 was EXEMPTED -- the exemption is keyed on carrying "
        "a minted id, which is the rule the design spike REJECTED by executing this exact exploit. It "
        "reopens ADR-067 section 5: an item /risk-spike step-0 never ran on walks past the reality "
        f"gate with nothing to prove. exit={r.returncode} stdout={r.stdout!r}"
    )
    assert "PS-002" in combined, f"the refusal must name the un-exempt item; got {combined!r}"

    # ...and the MATERIALIZED half is genuinely exempt -- i.e. the gate is not just refusing
    # everything, which would pass the assertion above for the wrong reason (AC3 would catch it, but
    # pin the discriminator HERE, where it is the subject).
    items_ok = [dict(i) for i in scope["items"]]
    items_ok[0]["assumptions"] = [dict(a, spike_status="proven") for a in items_ok[0]["assumptions"]]
    r2 = _revise(run_script, pvault, tmp_path, items_ok)
    assert r2.returncode == 0, (
        "the MATERIALIZED item PS-001's proven assumption must be exempt -- the discriminator must "
        f"distinguish, not blanket-refuse. exit={r2.returncode} stderr={r2.stderr!r}"
    )


# ── B1 — revisions[] IS the ps retirement history ──────────────────────────────


def test_a_cut_id_is_never_re_issued_even_with_a_zeroed_counter(run_script, pvault, tmp_path):
    """B1 (blocker). `--cut` makes a PS id RETIRED -- a lifecycle state that never existed for this
    kind -- which silently falsifies id_allocator.seed_max_for('ps')'s stated premise ('the live
    items[] IS the full history: an id is never retired'). After a cut, items[] is NOT the full
    history, so the self-heal FLOOR stops covering the retired id and a lowered counter re-issues it
    onto a LIVE shipped candidate.

    The full chain, executed with supported verbs only (the first Critic's reproduction):
    cut PS-002 -> `vault_edit set --path counters.ps --value 0` (rc=0: an unguarded write to an
    allocator counter on a managed file -- _cmd_set keys the managed refusal on the path's FIRST
    segment, 'counters', not 'items'; filed as its own candidate, NOT closed here) -> the next mint
    must still be PS-003, never PS-002.

    The fix is the ledger scan (the cc/cn/gs precedent at id_allocator.py:183-196, where a retired
    check's id is likewise never re-issued). It is defense-in-depth against the counters.ps hole,
    NOT a closure of it.
    """
    scope = _persist_two(run_script, pvault, tmp_path)

    r = _revise(run_script, pvault, tmp_path, [dict(scope["items"][0])],
                "--cut", "PS-002", "--reason", "retired -- superseded by the core")
    assert r.returncode == 0, f"setup: the cut must be accepted; {r.stderr!r}"
    assert _read(pvault / SCOPE)["revisions"][0]["cut"] == ["PS-002"]

    # the hand-edited-down counter this floor exists to survive
    z = _vault_edit(run_script, pvault, "set", "--file", SCOPE, "--path", "counters.ps", "--value", "0")
    assert z.returncode == 0, "setup: zeroing counters.ps is the (unguarded) chain the Critic executed"
    assert _read(pvault / SCOPE)["counters"]["ps"] == 0

    kept = [dict(_read(pvault / SCOPE)["items"][0])]
    kept.append({
        "label": "telemetry", "title": "add-telemetry", "description": "A brand-new capability.",
        "user_visible_outcome": "Operator sees per-stage activity.", "depends_on": [],
        "assumptions": [{"id": "A1", "statement": "Per-stage activity is observable.",
                         "blocking": True, "spike_status": "unproven"}],
        "verification_plan": "Drive a real run.",
    })
    r2 = _revise(run_script, pvault, tmp_path, kept)
    assert r2.returncode == 0, r2.stderr

    ids = [i["id"] for i in _read(pvault / SCOPE)["items"]]
    assert "PS-002" not in ids, (
        f"a RETIRED PS id was RE-ISSUED to a brand-new capability -- it now aliases the shipped "
        f"candidate that PS-002 minted. ids={ids}"
    )
    assert ids == ["PS-001", "PS-003"], f"the mint must skip the retired id; got {ids}"


def test_id_allocation_audit_sees_a_re_issued_cut_id_as_a_duplicate(run_script, pvault, tmp_path):
    """B1, the audit half. sources['ps'] scans items[] only, so a cut id is INVISIBLE to the audit
    whose docstring says it catches a hand-edited-down counter. Union the ledger's cut ids in, so a
    re-issue is a VISIBLE duplicate rather than a silent alias."""
    scope = _persist_two(run_script, pvault, tmp_path)
    r = _revise(run_script, pvault, tmp_path, [dict(scope["items"][0])],
                "--cut", "PS-002", "--reason", "retired")
    assert r.returncode == 0, r.stderr

    # hand-author the re-issue the audit must SEE (the state a lost floor would produce)
    data = _read(pvault / SCOPE)
    data["items"].append(dict(data["items"][0], id="PS-002", decomposition_label="resurrected",
                              title="resurrected-item"))
    _write(pvault / SCOPE, data)

    from scripts.lib.id_allocation_audit import counters_violations
    problems = counters_violations(pvault)

    assert any(p.startswith("ps:") and "DUPLICATE" in p for p in problems), (
        "a re-issued RETIRED PS id must be a VISIBLE duplicate to the audit whose docstring says it "
        f"catches a hand-edited-down counter; got {problems!r}"
    )


# ── m2 — the shrink-to-zero refusal names ITS OWN cause ────────────────────────


def test_a_revise_that_would_empty_the_scope_names_that_cause(run_script, pvault, tmp_path):
    """m2. Today `_load_items:342` raises 'must carry a non-empty `items` array' BEFORE any gate --
    so a deliberate shrink-to-zero is refused with a message about a MALFORMED PAYLOAD rather than
    about the shrink it actually attempted. That is AC4's own complaint, one function over."""
    _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    f = tmp_path / "empty.json"
    _write(f, {"items": []})
    r = _run(run_script, pvault, "revise", "--items-file", str(f), "--json",
             "--cut", "PS-001", "--cut", "PS-002", "--reason", "the whole product was descoped")
    combined = r.stdout + r.stderr

    assert r.returncode != 0, "a shrink-to-zero must be refused"
    assert "non-empty `items` array" not in combined, (
        "the refusal fell through to _load_items' PAYLOAD-SHAPE message -- it names the wrong "
        f"cause, which is AC4's complaint one function over. got: {combined!r}"
    )
    lowered = combined.lower()
    assert "zero" in lowered or "empty" in lowered or "every item" in lowered, (
        f"the refusal must NAME the shrink-to-zero it actually attempted; got {combined!r}"
    )
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before


# ══════════════════════════════════════════════════════════════════════════════════
# slice-076 / SC-162 / ADR-087 — enforce the two contract fields that give a product
# capability MEANING (verification_plan, user_visible_outcome) at the SINGLE
# _check_contract recognizer, delete the _candidate_from placeholder repair, recognize
# BOTH fields on the dry-run --scope-file replay, and validate the EFFECTIVE (item OR
# prev) value on revise so the check agrees with the persist merge (:1255/:1258).
# ══════════════════════════════════════════════════════════════════════════════════


def _persist_items(run_script, vault: Path, tmp_path: Path, payload: dict, name: str = "items.json"):
    f = tmp_path / name
    _write(f, payload)
    return _run(run_script, vault, "persist", "--items-file", str(f), "--json")


def _scope_file_item(**over) -> dict:
    """A single well-formed --scope-file item: title/label/blocking-unproven assumption + BOTH
    contract fields present, so a field-under-test refusal is isolated to the overridden field
    (title/label/assumption checks fire earlier and would mask it)."""
    it = {
        "id": "PS-001", "decomposition_label": "core-engine", "title": "would-be-minted",
        "description": "x", "user_visible_outcome": "It runs.", "depends_on": [],
        "assumptions": [{"id": "A1", "statement": "The core is expressible deterministically.",
                         "blocking": True, "spike_status": "unproven"}],
        "verification_plan": "Drive one real run end-to-end.",
    }
    it.update(over)
    return it


# ── AC1 — an empty/absent verification_plan is refused; the placeholder repair is gone ──


@pytest.mark.parametrize("bad", [{"verification_plan": ""}, "absent"])
def test_persist_refuses_an_empty_or_absent_verification_plan(run_script, pvault, tmp_path, bad):
    """AC1. Today `_check_contract` never looks at verification_plan and `_candidate_from:776-778`
    silently REPAIRS an empty one into a generated placeholder, so an item that declares no way to
    check itself still mints a PS id. The check must REFUSE it, fail-visibly, naming the field."""
    payload = _two_items()
    if bad == "absent":
        payload["items"][0].pop("verification_plan", None)
    else:
        payload["items"][0].update(bad)

    r = _persist_items(run_script, pvault, tmp_path, payload)
    combined = r.stdout + r.stderr

    assert r.returncode == 2, (
        "persist ACCEPTED an item with no verification_plan -- the field that states how the "
        f"capability is checked. exit={r.returncode} stdout={r.stdout!r}"
    )
    assert "verification_plan" in combined, f"the refusal must NAME the field; got {combined!r}"
    assert "core-engine" in combined, f"the refusal must NAME the offending item; got {combined!r}"
    assert not (pvault / SCOPE).exists(), "the refused persist still created the scope file"


def test_the_candidate_placeholder_repair_is_gone(run_script, pvault, tmp_path):
    """AC1. A COMPLETE decomposition still mints, and the minted candidate's verification_plan is the
    one the item SUPPLIED -- never the deleted `Prove the blocking assumption(s)...` placeholder that
    used to mask an empty plan (the parser differential: scope.json empty, candidate synthesised)."""
    r = _persist_items(run_script, pvault, tmp_path, _two_items())
    assert r.returncode == 0, r.stderr

    prods = []
    for rel in ("candidates.json", "archive/candidates.json"):
        p = pvault / rel
        if p.exists():
            prods += [c for c in _read(p).get("candidates", [])
                      if any(isinstance(s, dict) and s.get("type") == "product-scope"
                             for s in (c.get("source") or []))]
    assert prods, "no product candidate minted"
    for c in prods:
        vp = c.get("verification_plan")
        assert isinstance(vp, str) and vp.strip(), f"minted candidate has no verification_plan: {c}"
        assert not vp.startswith("Prove the blocking assumption(s)"), (
            f"the deleted placeholder repair re-appeared on a minted candidate: {vp!r}"
        )
    # the core-engine item's plan is carried verbatim
    core = next(c for c in prods if "core" in (c.get("title") or ""))
    assert core["verification_plan"] == "Drive one real run end-to-end."


# ── AC2 — an empty/absent user_visible_outcome is refused ──


@pytest.mark.parametrize("bad", [{"user_visible_outcome": ""}, "absent"])
def test_persist_refuses_an_empty_or_absent_user_visible_outcome(run_script, pvault, tmp_path, bad):
    """AC2. Today user_visible_outcome is read via a bare `.get()` (:764) and `_check_contract` never
    validates it -- so it is silently optional and a materialized candidate carries None. Refuse it."""
    payload = _two_items()
    if bad == "absent":
        payload["items"][0].pop("user_visible_outcome", None)
    else:
        payload["items"][0].update(bad)

    r = _persist_items(run_script, pvault, tmp_path, payload)
    combined = r.stdout + r.stderr

    assert r.returncode == 2, (
        "persist ACCEPTED an item with no user_visible_outcome -- silently optional. "
        f"exit={r.returncode} stdout={r.stdout!r}"
    )
    assert "user_visible_outcome" in combined, f"the refusal must NAME the field; got {combined!r}"
    assert "core-engine" in combined, f"the refusal must NAME the offending item; got {combined!r}"
    assert not (pvault / SCOPE).exists()


# ── AC3 — contract-complete items (the live PS-001..004 shape) are NOT stranded ──


def test_contract_complete_items_are_not_stranded_on_persist_or_revise(run_script, pvault, tmp_path):
    """AC3 / must-not-defer #2. The tightened contract must ACCEPT items carrying non-empty fields --
    the exact shape the 4 live PS items already have (measured: verification_plan len 309-490,
    user_visible_outcome len 220-277). The real vault is unreachable here (run_script strips
    AI_SDLC_VAULT_ROOT), so this mirrors the live shape with the fixture: both fields non-empty."""
    scope = _persist_two(run_script, pvault, tmp_path)
    assert all(i.get("verification_plan") and i.get("user_visible_outcome") for i in scope["items"])

    # a full-list revise of the complete items must not be refused (no strand)
    r = _revise(run_script, pvault, tmp_path, [dict(i) for i in scope["items"]])
    assert r.returncode == 0, (
        f"a full-list revise of contract-complete items was refused -- the live scope is stranded. "
        f"exit={r.returncode} stderr={r.stderr!r}"
    )
    out = _read(pvault / SCOPE)
    assert [i["id"] for i in out["items"]] == ["PS-001", "PS-002"]


# ── AC4 — the refusal is SHAPE-LEVEL honest (presence, not a testability guarantee) ──


def test_the_contract_refusal_is_labelled_shape_level_not_a_testability_guarantee(
    run_script, pvault, tmp_path
):
    """AC4 / A2 constraint. The check is presence/non-empty ONLY; a non-empty but meaningless plan
    ('TODO') still passes. The message must SAY so and must NOT imply it guarantees testability, or
    it oversells a shape check as a substance check."""
    payload = _two_items()
    payload["items"][0]["verification_plan"] = ""
    r = _persist_items(run_script, pvault, tmp_path, payload)
    combined = (r.stdout + r.stderr).lower()

    assert r.returncode == 2, r.stdout
    assert "non-empty" in combined, f"the message must claim non-empty PRESENCE; got {combined!r}"
    assert "shape-level" in combined, f"the message must label itself shape-level; got {combined!r}"
    assert "necessary, not sufficient" in combined or "not a" in combined, (
        f"the message must DISCLAIM a testability guarantee; got {combined!r}"
    )


# ── AC5 — enforcement at BOTH model->vault entry points (persist AND revise) ──


def test_both_entry_points_refuse_an_empty_contract_field(run_script, pvault, tmp_path):
    """AC5. `_check_contract` is the single choke point both persist (:1087) and revise (:1235) route
    through, so one placement must cover BOTH. Pin persist AND revise each refusing an empty field."""
    # persist leg — an empty user_visible_outcome on a fresh decomposition
    payload = _two_items()
    payload["items"][1]["user_visible_outcome"] = ""
    rp = _persist_items(run_script, pvault, tmp_path, payload, name="persist.json")
    assert rp.returncode == 2, f"persist leg accepted an empty field: {rp.stdout!r}"
    assert not (pvault / SCOPE).exists()

    # revise leg — persist a clean scope, then a revise that INTRODUCES a new item with an empty
    # field. (An empty field on a KEPT item legitimately inherits its non-empty `prev` via the
    # effective-value merge and is NOT stranded -- that is pinned separately. A NEW item has no prev,
    # so its empty field is genuinely empty and must be refused: that is where revise could introduce
    # an empty contract field.)
    scope = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")
    items = [dict(i) for i in scope["items"]]           # keep both -> membership passes
    items.append({
        "label": "newcap", "title": "add-newcap", "description": "A brand-new capability.",
        "user_visible_outcome": "The operator sees the new capability.", "depends_on": [],
        "assumptions": [{"id": "A1", "statement": "It is expressible.", "blocking": True,
                         "spike_status": "unproven"}],
        "verification_plan": "",                        # empty on a NEW item -> no prev -> refused
    })
    rr = _revise(run_script, pvault, tmp_path, items)
    combined = rr.stdout + rr.stderr
    assert rr.returncode != 0, f"revise leg introduced a new item with an empty field: {rr.stdout!r}"
    assert "verification_plan" in combined, f"the revise refusal must name the field; got {combined!r}"
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before, (
        "a refused revise must leave product-scope.json byte-identical"
    )


# ── m2 — whitespace-only and non-string are refused, not accepted/coerced ──


@pytest.mark.parametrize("field", ["verification_plan", "user_visible_outcome"])
def test_persist_refuses_a_whitespace_only_contract_field(run_script, pvault, tmp_path, field):
    """m2(a). A bare-truthiness check accepts '   ' (three spaces) -- exactly the 'no real way to
    check itself' the slice rejects. The predicate must strip: str(...).strip() before the test."""
    payload = _two_items()
    payload["items"][0][field] = "   "
    r = _persist_items(run_script, pvault, tmp_path, payload)
    combined = r.stdout + r.stderr
    assert r.returncode == 2, f"a whitespace-only {field} was accepted: {r.stdout!r}"
    assert field in combined, f"the refusal must name {field}; got {combined!r}"
    assert not (pvault / SCOPE).exists()


def test_persist_refuses_a_non_string_verification_plan_rather_than_coercing_it(
    run_script, pvault, tmp_path
):
    """m2(b). str(['a','b']) is a non-empty string, so a bare str()-coercion would PASS a list and
    persist it as a list, which a downstream string op then misreads (the parser differential the
    LangSec frame exists to prevent). The isinstance guard must REFUSE a non-string by name."""
    payload = _two_items()
    payload["items"][0]["verification_plan"] = ["step a", "step b"]
    r = _persist_items(run_script, pvault, tmp_path, payload)
    combined = r.stdout + r.stderr
    assert r.returncode == 2, f"a NON-STRING (list) verification_plan was coerced-and-accepted: {r.stdout!r}"
    assert "verification_plan" in combined, f"the refusal must name the field; got {combined!r}"
    assert not (pvault / SCOPE).exists()


# ── M-add-1 — the revise check validates the EFFECTIVE value and mirrors the :1258 merge ──


def test_revise_refuses_a_whitespace_field_even_when_prev_carries_a_real_one(
    run_script, pvault, tmp_path
):
    """M-add-1 (DR-1). Persist merge at :1258 uses BARE `or`, so a whitespace-only value on a kept
    item ('   ' is truthy) is NOT rescued by prev -- it is what gets WRITTEN, unstripped. A per-operand
    check (prev rescues) would PASS it while persist writes whitespace: the check/persist parser
    differential re-entering at the revise seam. The check must compute the EFFECTIVE value FIRST then
    strip -- so a whitespace item on a kept row is refused BEFORE persist writes it."""
    scope = _persist_two(run_script, pvault, tmp_path)
    raw_before = (pvault / SCOPE).read_text(encoding="utf-8")

    items = [dict(i) for i in scope["items"]]
    items[0]["verification_plan"] = "   "       # whitespace on a KEPT item; prev carries a real one
    assert scope["items"][0]["verification_plan"].strip(), "fixture invariant: prev IS non-empty"

    r = _revise(run_script, pvault, tmp_path, items)
    combined = r.stdout + r.stderr

    assert r.returncode != 0, (
        "revise ACCEPTED a whitespace-only verification_plan on a kept item because prev rescued it -- "
        f"but :1258 writes the item's whitespace, unstripped. exit={r.returncode} stdout={r.stdout!r}"
    )
    assert "verification_plan" in combined, f"the refusal must name the field; got {combined!r}"
    assert (pvault / SCOPE).read_text(encoding="utf-8") == raw_before, (
        "a refused revise must leave product-scope.json byte-identical"
    )


def test_revise_accepts_a_kept_item_that_inherits_its_field_from_prev(run_script, pvault, tmp_path):
    """M-add-1, the other direction. The effective-value check must NOT strand a legitimate partial
    revise: a kept item that OMITS verification_plan inherits prev's real one via :1258, so the
    effective value is non-empty and the revise must PASS (this is the slice-073 non-strand promise)."""
    scope = _persist_two(run_script, pvault, tmp_path)
    items = [dict(i) for i in scope["items"]]
    items[0].pop("verification_plan", None)     # omitted -> inherits prev's real plan
    r = _revise(run_script, pvault, tmp_path, items)
    assert r.returncode == 0, (
        f"a kept item omitting verification_plan (inheriting a real prev) was stranded. "
        f"exit={r.returncode} stderr={r.stderr!r}"
    )
    out = _read(pvault / SCOPE)
    kept = next(i for i in out["items"] if i["id"] == "PS-001")
    assert kept["verification_plan"] == scope["items"][0]["verification_plan"], "prev value must persist"


# ── m3 / M-add-2 — the dry-run --scope-file replay recognizes BOTH fields ──


@pytest.mark.parametrize("field", ["verification_plan", "user_visible_outcome"])
def test_dry_run_scope_file_replay_refuses_an_empty_contract_field(
    run_script, pvault, tmp_path, field
):
    """m3 (verification_plan) + M-add-2 (user_visible_outcome). `materialize --scope-file --dry-run`
    is a READ-ONLY replay that reaches _candidate_from WITHOUT the recognizer (_load_items runs only
    _check_identities). After deleting the placeholder, an empty verification_plan would yield a
    silent None candidate and an empty user_visible_outcome silently None -- the exact AC1/AC2 defects
    surviving on the replay surface. Running _check_contract on the dry-run items closes BOTH."""
    scope = tmp_path / "scope.json"
    _write(scope, {"items": [_scope_file_item(**{field: ""})]})

    r = _run(run_script, pvault, "materialize", "--scope-file", str(scope), "--dry-run", "--json")
    combined = r.stdout + r.stderr

    assert r.returncode == 2, (
        f"the dry-run replay MINTED (dry) a candidate from an item with an empty {field} -- the "
        f"replay path bypasses the recognizer. exit={r.returncode} stdout={r.stdout!r}"
    )
    assert field in combined, f"the dry-run refusal must name {field}; got {combined!r}"


def test_dry_run_scope_file_replay_still_recognizes_a_complete_item(run_script, pvault, tmp_path):
    """m3/M-add-2, the pass direction. A contract-complete --scope-file item must still dry-run
    cleanly (the recognizer accepts it), so the fix does not break the legitimate replay surface."""
    scope = tmp_path / "scope.json"
    _write(scope, {"items": [_scope_file_item()]})
    r = _run(run_script, pvault, "materialize", "--scope-file", str(scope), "--dry-run", "--json")
    assert r.returncode == 0, f"a complete item was refused on the dry-run replay: {r.stderr!r}"
    out = _read(pvault / SCOPE) if (pvault / SCOPE).exists() else None
    assert out is None, "a dry run must not write the scope file"
    assert json.loads(r.stdout)["dry_run"] is True
