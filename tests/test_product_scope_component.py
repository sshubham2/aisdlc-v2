"""slice-080 / SC-165 / [[ADR-091]] — AC1: the additive OPTIONAL `area` field on product-scope items.
(slice-084 renamed the field `component` -> `area`, keeping `component` as a back-compat input alias.)

A capability may be bound to a high-level product AREA via ONE optional scalar string. It is added to BOTH
`cmd_persist` and `cmd_revise` out_items whitelists so the register actually records it; items lacking
it stay valid. cmd_revise reads `it.get('area') or it.get('component') or prev...` (critique m1) — the
established out_items idiom — so a revise can SET/UPDATE and also PRESERVE the area, never silently drop a
revise-supplied one.

Also pins the AC5 no-stored-STATUS tripwire at the persist/revise seam: a model-supplied `status` /
`progress` on an item is DROPPED by the whitelist (progress is always computed, never stored). If a
future slice widens the whitelist to carry a status, this test fails loudly.

(Legacy file + function names retained for shippability-catalog node-id stability; the bodies now test the
canonical `area` / `set-area` / `--area` path, with dedicated `component`-alias coverage.)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aivlc-vault"
SCRIPT = "scripts/lib/product_scope.py"


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _assump():
    return [{"id": "A1", "statement": "It is expressible deterministically.",
             "blocking": True, "spike_status": "unproven"}]


@pytest.fixture
def pvault(tmp_path: Path) -> Path:
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


def _revise(run_script, vault: Path, items: dict, tmp_path: Path, name: str, *extra):
    f = tmp_path / name
    _write(f, items)
    return _run(run_script, vault, "revise", "--items-file", str(f), "--json", *extra)


def _scope_items(vault: Path) -> dict:
    return {i["id"]: i for i in _read(vault / "product-scope.json")["items"]}


# ── AC1: an area-bearing and an area-less item both persist; the field survives ──

def test_ac1_component_persists_and_absent_stays_valid(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "pay-core", "title": "build-pay-core", "description": "core",
         "user_visible_outcome": "captures", "depends_on": [], "area": "payments",
         "assumptions": _assump(), "verification_plan": "drive it"},
        {"label": "misc", "title": "build-misc", "description": "misc",
         "user_visible_outcome": "shows", "depends_on": [],
         "assumptions": _assump(), "verification_plan": "drive it"},
    ]}
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode == 0, r.stderr
    by_id = _scope_items(pvault)
    witha = [i for i in by_id.values() if i.get("area") == "payments"]
    assert len(witha) == 1, "the area-bearing item must record it"
    # the area-less item stays valid — area absent is legal, not a crash / not a refusal
    without = [i for i in by_id.values() if not i.get("area")]
    assert len(without) == 1


# ── AC1 / m1: revise can SET an area AND preserve a prev one (it.get or prev.get) ──

def test_ac1_revise_sets_and_preserves_component(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "area": "payments", "assumptions": _assump(),
         "verification_plan": "x"},
        {"label": "b", "title": "build-b", "description": "b", "user_visible_outcome": "b",
         "depends_on": [], "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    by_id = _scope_items(pvault)
    a_id = next(i for i, it in by_id.items() if it.get("area") == "payments")
    b_id = next(i for i, it in by_id.items() if not it.get("area"))

    # revise: leave A's area unset (must PRESERVE 'payments' via prev.get); SET B's to 'billing'
    rev = {"items": [
        {"id": a_id, "title": "build-a", "assumptions": _assump(), "verification_plan": "x"},
        {"id": b_id, "title": "build-b", "area": "billing", "assumptions": _assump(),
         "verification_plan": "x"},
    ]}
    r = _revise(run_script, pvault, rev, tmp_path, "rev.json")
    assert r.returncode == 0, r.stderr
    after = _scope_items(pvault)
    assert after[a_id].get("area") == "payments", "prev area must be preserved (m1 or-prev)"
    assert after[b_id].get("area") == "billing", "revise-supplied area must be recorded (m1)"


# ── AC5: no stored status/progress — the whitelist DROPS a model-supplied status ──

def test_ac5_persist_and_revise_drop_stored_status(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "area": "payments", "status": "done", "progress": "shipped",
         "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    it = next(iter(_scope_items(pvault).values()))
    assert "status" not in it, "a stored status must be DROPPED (progress is computed, never stored)"
    assert "progress" not in it, "a stored progress must be DROPPED"


# ── slice-084: the `component` INPUT alias — a persist payload keyed `component` still records `area` ──

def test_alias_component_input_field_records_area(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "component": "payments", "assumptions": _assump(),
         "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    it = next(iter(_scope_items(pvault).values()))
    assert it.get("area") == "payments", "a legacy `component` input must normalize into `area`"
    assert "component" not in it, "the persisted item must carry the canonical `area`, not the alias key"


# ── slice-084 (C1b): the optional code_components[] link persists and defaults to [] ──

def test_c1b_code_components_link_persists(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "area": "payments", "code_components": ["billing.py", "ledger.py", 7, ""],
         "assumptions": _assump(), "verification_plan": "x"},
        {"label": "b", "title": "build-b", "description": "b", "user_visible_outcome": "b",
         "depends_on": [], "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    by_id = _scope_items(pvault)
    a = next(i for i in by_id.values() if i.get("area") == "payments")
    b = next(i for i in by_id.values() if not i.get("area"))
    assert a["code_components"] == ["billing.py", "ledger.py"], "only real string ids are kept (7 and '' dropped)"
    assert b["code_components"] == [], "an item with no link defaults to an empty list (legal pre-code)"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# slice-081 / SC-183 / [[ADR-092]] — the area-annotation PRODUCER: `product_scope set-area`
# (slice-084 renamed set-component -> set-area, --component -> --area, both kept as aliases).
#
# The verb assigns ONE area to ONE already-materialized PS item via the atomic safe_mutate_text path
# (no re-materialize, no revisions[] entry — ADR-092). It validates the name at the WRITE seam (reject
# empty/whitespace + the reserved 'unassigned' sentinel), and every reject leaves product-scope.json
# byte-identical.
# ══════════════════════════════════════════════════════════════════════════════════════════════

import hashlib

ROLLUP = "scripts/lib/product_rollup.py"
TOP = "skills/slice/scripts/candidates_top.py"


def _setarea(run_script, vault, item, area, *extra):
    return _run(run_script, vault, "set-area", "--item", item, "--area", area, "--json", *extra)


def _setarea_alias(run_script, vault, item, area, *extra):
    # the deprecated set-component / --component alias, dispatching to the same handler (dest=area)
    return _run(run_script, vault, "set-component", "--item", item, "--component", area, "--json", *extra)


def _persist_two(run_script, pvault, tmp_path):
    """Persist two INDEPENDENT, un-annotated capabilities; return {decomposition_label: PS-id}."""
    items = {"items": [
        {"label": "pay-core", "title": "build-pay-core", "description": "core",
         "user_visible_outcome": "captures", "depends_on": [],
         "assumptions": _assump(), "verification_plan": "drive it"},
        {"label": "misc", "title": "build-misc", "description": "misc",
         "user_visible_outcome": "shows", "depends_on": [],
         "assumptions": _assump(), "verification_plan": "drive it"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    return {it["decomposition_label"]: pid for pid, it in _scope_items(pvault).items()}


def _cands_by_psref(vault: Path) -> dict:
    """{PS-ref: SC-id} for every materialized product-scope candidate in the live file."""
    out = {}
    for c in _read(vault / "candidates.json").get("candidates", []):
        for s in c.get("source") or []:
            if isinstance(s, dict) and s.get("type") == "product-scope" and s.get("ref"):
                out[s["ref"]] = c["id"]
    return out


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── AC1: the producer SETS the area and RECORDS it atomically (revised_at bumped) ──

def test_setcomp_ac1_sets_component_and_records(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    r = _setarea(run_script, pvault, ids["pay-core"], "payments")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["changed"] is True and out["area"] == "payments"
    by_id = _scope_items(pvault)
    assert by_id[ids["pay-core"]]["area"] == "payments", "the item must carry the assigned area"
    # 'recorded' = the atomic write bumped revised_at (no raw whole-file overwrite; safe_mutate_text path)
    assert _read(pvault / "product-scope.json").get("revised_at"), "the write must record revised_at"


# ── slice-084: the set-component / --component alias sets the SAME area and reports action=set-area ──

def test_setcomp_alias_verb_sets_area(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    r = _setarea_alias(run_script, pvault, ids["pay-core"], "payments")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["action"] == "set-area" and out["area"] == "payments", out
    assert _scope_items(pvault)[ids["pay-core"]]["area"] == "payments"


# ── AC2: end-to-end — the annotation de-inerts BOTH read-side consumers (rollup bucket + lens) ──

def test_setcomp_ac2_rollup_bucket_and_lens_filter_end_to_end(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    sc = _cands_by_psref(pvault)
    assert ids["pay-core"] in sc and ids["misc"] in sc, "persist must have materialized a candidate per item"
    assert _setarea(run_script, pvault, ids["pay-core"], "payments").returncode == 0

    # (a) product_rollup now carries a 'payments' area bucket (was: everything unassigned)
    env = json.loads(run_script(ROLLUP, ["--vault", str(pvault), "--json"]).stdout)
    names = {c["name"] for c in env["areas"]}
    assert "payments" in names, f"annotated area must be its own rollup bucket; got {names}"

    # (b) candidates_top --area payments POSITIVELY filters pickable to the annotated item's
    #     candidate, and the non-matching (misc) candidate is ABSENT (no vacuous empty-filter green — M3)
    cp = run_script(TOP, ["--vault", str(pvault), "--json", "--area", "payments"])
    assert cp.returncode == 0, cp.stderr
    top_ids = {t["id"] for t in json.loads(cp.stdout)["top"]}
    assert sc[ids["pay-core"]] in top_ids, "the annotated capability's candidate must be PRESENT under the lens"
    assert sc[ids["misc"]] not in top_ids, "a non-matching candidate must be ABSENT (filter is real, not vacuous)"


# ── AC3: a zero-annotation scope rolls up with EVERY capability in the reserved 'unassigned' bucket ──
#          (the pre-slice baseline; the UNASSIGNED re-point is value-preserving) ──

def test_setcomp_ac3_zero_annotation_rollup_is_all_unassigned(run_script, pvault, tmp_path):
    _persist_two(run_script, pvault, tmp_path)   # neither item annotated
    env = json.loads(run_script(ROLLUP, ["--vault", str(pvault), "--json"]).stdout)
    assert env["areas"] == [], "a zero-annotation scope must yield NO area buckets"
    assert env["unassigned"]["name"] == "unassigned"
    assert env["unassigned"]["total"] == 2, "every capability lands in the reserved 'unassigned' stratum"
    assert env["whole_app"]["total"] == 2


def test_setcomp_ac3_m1_reserved_sentinel_single_sourced():
    """m1: the write-validator and the read-side catch-all bucket share ONE definition — value-preserving,
    so slice-080's rollup byte-identity holds. Direct import (conftest bootstraps sys.path)."""
    from scripts.lib import product_scope, product_rollup
    assert product_scope.UNASSIGNED == "unassigned", "canonical sentinel must be the exact lowercase string"
    assert product_rollup.UNASSIGNED is product_scope.UNASSIGNED or \
        product_rollup.UNASSIGNED == product_scope.UNASSIGNED, "product_rollup must re-point to the canonical sentinel"


# ── AC4: the write seam REJECTS empty/whitespace + the reserved sentinel, byte-identical on reject ──

def test_setcomp_ac4_rejects_empty_and_reserved_byte_identical(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    scope_p = pvault / "product-scope.json"
    before = _sha(scope_p)
    for bad in ("", "   ", "unassigned", "Unassigned", "UNASSIGNED", " unassigned "):
        r = _setarea(run_script, pvault, ids["pay-core"], bad)
        assert r.returncode == 2, f"area {bad!r} must be rejected fail-visibly (exit 2), got {r.returncode}"
        assert _sha(scope_p) == before, f"a rejected write ({bad!r}) must leave product-scope.json byte-identical"


def test_setcomp_ac4_unknown_item_rejected_byte_identical(run_script, pvault, tmp_path):
    _persist_two(run_script, pvault, tmp_path)
    scope_p = pvault / "product-scope.json"
    before = _sha(scope_p)
    r = _setarea(run_script, pvault, "PS-999", "payments")   # id this scope does not carry
    assert r.returncode == 2, r.stderr
    assert _sha(scope_p) == before, "an unknown --item must leave the scope byte-identical (in-lock refusal)"


def test_setcomp_m1_scope_absent_exits_4(run_script, tmp_path):
    """M1: scope-absent reuses _scope(required=True) -> exit 4 (module taxonomy), NOT exit 2."""
    v = tmp_path / "empty-vault"
    v.mkdir()
    r = _setarea(run_script, v, "PS-001", "payments")
    assert r.returncode == 4, f"scope-absent must be exit 4 (no-scope), got {r.returncode}: {r.stderr}"


# ── m2: a no-op re-annotation is byte-identical (changed:false, no revised_at churn) ──

def test_setcomp_m2_noop_reannotate_byte_identical(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    assert _setarea(run_script, pvault, ids["pay-core"], "payments").returncode == 0
    scope_p = pvault / "product-scope.json"
    after_first = _sha(scope_p)
    revised_first = _read(scope_p).get("revised_at")
    r = _setarea(run_script, pvault, ids["pay-core"], "payments")   # same value → no-op
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["changed"] is False, "a same-value re-annotate must report changed:false"
    assert _sha(scope_p) == after_first, "a no-op must leave product-scope.json byte-identical"
    assert _read(scope_p).get("revised_at") == revised_first, "a no-op must NOT churn revised_at"


# ── m4: set-area preserves every OTHER item byte-for-byte (genuine in-place RMW, not a rebuild) ──

def test_setcomp_m4_preserves_other_items(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    misc_before = _scope_items(pvault)[ids["misc"]]
    assert _setarea(run_script, pvault, ids["pay-core"], "payments").returncode == 0
    misc_after = _scope_items(pvault)[ids["misc"]]
    assert misc_after == misc_before, "an untouched capability must be byte-identical after set-area"


# ── m5: a reassignment ECHOES the prior area so the change is visible in the command result ──

def test_setcomp_m5_reassignment_echoes_previous(run_script, pvault, tmp_path):
    ids = _persist_two(run_script, pvault, tmp_path)
    first = json.loads(_setarea(run_script, pvault, ids["pay-core"], "payments").stdout)
    assert first["previous"] in (None, "", "unassigned") or "previous" in first
    second = json.loads(_setarea(run_script, pvault, ids["pay-core"], "billing").stdout)
    assert second["changed"] is True and second["area"] == "billing"
    assert second["previous"] == "payments", "a reassignment must echo the prior area value (m5)"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# slice-083 / SC-182 / [[ADR-096]] — the reserved-area guard now holds on EVERY write seam.
#
# _valid_area (slice-081, renamed slice-084) was wired only into set-area; slice-083 routes persist /
# revise / --scope-file replay through it at the shared _load_items boundary, single-sourced. These tests
# lock the two accepted-pending Critic findings: M2 (raw-truthy whitespace lock) and m3 (persist reserved).
# ══════════════════════════════════════════════════════════════════════════════════════════════


def test_sc182_revise_rejects_reserved_component_every_write_seam(run_script, pvault, tmp_path):
    """AC1 parity at the revise seam: a reserved 'unassigned' area rejects (exit 2), single-sourced
    through _valid_area -- the guard is no longer set-area-only."""
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    a_id = next(iter(_scope_items(pvault)))
    rev = {"items": [{"id": a_id, "title": "build-a", "area": "unassigned",
                      "assumptions": _assump(), "verification_plan": "x"}]}
    r = _revise(run_script, pvault, rev, tmp_path, "rev.json")
    assert r.returncode == 2, f"revise must reject the reserved area (exit 2), got {r.returncode}: {r.stderr}"
    assert "unassigned" in r.stderr.lower(), r.stderr
    assert _scope_items(pvault)[a_id].get("area") in (None, ""), "the reserved name must never persist"


def test_sc182_m2_revise_rejects_whitespace_only_component_locks_raw_truthy(run_script, pvault, tmp_path):
    """M2: the write-seam gate keys on RAW truthiness (isinstance-str + value, NOT value.strip()). A
    whitespace-only area is truthy -> routed to _valid_area -> stripped to '' -> rejected (exit 2). This
    LOCKS the raw-truthy semantics: a future .strip()-based gate would skip validation and silently
    persist '   ' -- and this test would go red (slice-076 precedent)."""
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    a_id = next(iter(_scope_items(pvault)))
    rev = {"items": [{"id": a_id, "title": "build-a", "area": "   ",
                      "assumptions": _assump(), "verification_plan": "x"}]}
    r = _revise(run_script, pvault, rev, tmp_path, "rev.json")
    assert r.returncode == 2, f"revise must reject a whitespace-only area (exit 2), got {r.returncode}: {r.stderr}"
    assert "area" in r.stderr.lower(), r.stderr
    assert _scope_items(pvault)[a_id].get("area") in (None, ""), "whitespace must never persist"


def test_sc182_m3_persist_rejects_reserved_component_scope_byte_absent(run_script, pvault, tmp_path):
    """m3: a reserved 'unassigned' area in a persist items-file rejects PRE-LOCK (exit 2) via the shared
    _load_items choke point, and product-scope.json is never created (byte-ABSENT)."""
    scope_p = pvault / "product-scope.json"
    assert not scope_p.exists(), "precondition: no scope yet (persist is create-only)"
    items = {"items": [
        {"label": "pay-core", "title": "build-pay-core", "description": "core",
         "user_visible_outcome": "captures", "depends_on": [], "area": "unassigned",
         "assumptions": _assump(), "verification_plan": "drive it"},
    ]}
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode == 2, f"persist must reject the reserved area (exit 2), got {r.returncode}: {r.stderr}"
    assert "unassigned" in r.stderr.lower(), r.stderr
    assert not scope_p.exists(), "a pre-lock reject must leave product-scope.json byte-ABSENT (never created)"


def test_sc182_persist_and_revise_strip_a_supplied_component(run_script, pvault, tmp_path):
    """The introduced value is NORMALIZED (stripped) on the write paths -- check == persisted value, no
    differential. A ' payments ' area persists as 'payments' (via _valid_area's strip return)."""
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "area": "  payments  ", "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    it = next(iter(_scope_items(pvault).values()))
    assert it.get("area") == "payments", "a supplied area must persist stripped (check == write)"
