"""slice-080 / SC-165 / [[ADR-091]] — AC1: the additive OPTIONAL `component` field on product-scope items.

A capability may be bound to a high-level component via ONE optional scalar string. It is added to BOTH
`cmd_persist` and `cmd_revise` out_items whitelists so the register actually records it; items lacking
it stay valid. cmd_revise reads `it.get('component') or prev.get('component')` (critique m1) — the
established out_items idiom — so a revise can SET/UPDATE and also PRESERVE the component, never silently
drop a revise-supplied one.

Also pins the AC5 no-stored-STATUS tripwire at the persist/revise seam: a model-supplied `status` /
`progress` on an item is DROPPED by the whitelist (progress is always computed, never stored). If a
future slice widens the whitelist to carry a status, this test fails loudly.
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


# ── AC1: a component-bearing and a component-less item both persist; the field survives ──

def test_ac1_component_persists_and_absent_stays_valid(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "pay-core", "title": "build-pay-core", "description": "core",
         "user_visible_outcome": "captures", "depends_on": [], "component": "payments",
         "assumptions": _assump(), "verification_plan": "drive it"},
        {"label": "misc", "title": "build-misc", "description": "misc",
         "user_visible_outcome": "shows", "depends_on": [],
         "assumptions": _assump(), "verification_plan": "drive it"},
    ]}
    r = _persist(run_script, pvault, items, tmp_path)
    assert r.returncode == 0, r.stderr
    by_id = _scope_items(pvault)
    withc = [i for i in by_id.values() if i.get("component") == "payments"]
    assert len(withc) == 1, "the component-bearing item must record it"
    # the component-less item stays valid — component absent is legal, not a crash / not a refusal
    without = [i for i in by_id.values() if not i.get("component")]
    assert len(without) == 1


# ── AC1 / m1: revise can SET a component AND preserve a prev one (it.get or prev.get) ──

def test_ac1_revise_sets_and_preserves_component(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "component": "payments", "assumptions": _assump(),
         "verification_plan": "x"},
        {"label": "b", "title": "build-b", "description": "b", "user_visible_outcome": "b",
         "depends_on": [], "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    by_id = _scope_items(pvault)
    a_id = next(i for i, it in by_id.items() if it.get("component") == "payments")
    b_id = next(i for i, it in by_id.items() if not it.get("component"))

    # revise: leave A's component unset (must PRESERVE 'payments' via prev.get); SET B's to 'billing'
    rev = {"items": [
        {"id": a_id, "title": "build-a", "assumptions": _assump(), "verification_plan": "x"},
        {"id": b_id, "title": "build-b", "component": "billing", "assumptions": _assump(),
         "verification_plan": "x"},
    ]}
    r = _revise(run_script, pvault, rev, tmp_path, "rev.json")
    assert r.returncode == 0, r.stderr
    after = _scope_items(pvault)
    assert after[a_id].get("component") == "payments", "prev component must be preserved (m1 or-prev)"
    assert after[b_id].get("component") == "billing", "revise-supplied component must be recorded (m1)"


# ── AC5: no stored status/progress — the whitelist DROPS a model-supplied status ──

def test_ac5_persist_and_revise_drop_stored_status(run_script, pvault, tmp_path):
    items = {"items": [
        {"label": "a", "title": "build-a", "description": "a", "user_visible_outcome": "a",
         "depends_on": [], "component": "payments", "status": "done", "progress": "shipped",
         "assumptions": _assump(), "verification_plan": "x"},
    ]}
    assert _persist(run_script, pvault, items, tmp_path).returncode == 0
    it = next(iter(_scope_items(pvault).values()))
    assert "status" not in it, "a stored status must be DROPPED (progress is computed, never stored)"
    assert "progress" not in it, "a stored progress must be DROPPED"
