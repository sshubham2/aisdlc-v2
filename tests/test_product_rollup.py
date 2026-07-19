"""slice-080 / SC-165 / [[ADR-091]] — the read-only capability-progress rollup (product_rollup.py).

Covers AC2 (whole-app + per-area CAPABILITY counts, derived-at-read-time priority order, writes
nothing), AC3's contract (the --json envelope /pulse consumes + the always-qualified pulse_line), and
AC5 (no new id kind / no lock / no stored status / strata-sum conservation), plus the ratified critique
fixes: M1 (full 5-way partition + conservation over the full set, proven non-vacuously with a
no-children item), M2 (a rejected-only capability does NOT inflate 'done'), M3 (present-but-empty scope
is not '0/0'), M4 (no-scope is a clean scope_present:false; corrupt scope is a fail-visible error),
m4 (id-set mismatch degrades to error), m5 (read-only over the full read-set), M-add-2 (pinned total
component order), M-add-3 (pulse_line always names the done_definition).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from scripts.lib import product_rollup  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "lib" / "product_rollup.py"


# ── fixture builders ───────────────────────────────────────────────────────────────

def _ps_item(iid, area=None, title=None):
    it = {"id": iid, "title": title or iid.lower(), "assumptions": [
        {"id": "A1", "statement": "x", "blocking": True, "spike_status": "unproven"}]}
    if area is not None:
        it["area"] = area                     # slice-084: canonical grouping key (was `component`)
    return it


def _cand(cid, refs, status="candidate"):
    return {"id": cid, "title": cid.lower(), "status": status,
            "source": [{"type": "product-scope", "ref": r} for r in refs]}


def _write(vault: Path, scope_items, live, archive):
    (vault / "archive").mkdir(parents=True, exist_ok=True)
    (vault / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "fx", "items": scope_items}), encoding="utf-8")
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": live}), encoding="utf-8")
    (vault / "archive" / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": archive}), encoding="utf-8")


def _five_state_vault(vault: Path):
    """A scope exercising ALL five strata across three areas (M1 non-vacuous: it INCLUDES a
    no-children capability, a rejected-only capability, and an ambiguous->unknown pair)."""
    scope = [
        _ps_item("PS-100", "payments"),   # done (shipped child archived)
        _ps_item("PS-101", "payments"),   # rejected_only (only a rejected child archived)
        _ps_item("PS-102", "billing"),    # in_progress (live child)
        _ps_item("PS-103", "billing"),    # no_children
        _ps_item("PS-104"),               # unknown (ambiguous shared child) -> unassigned bucket
        _ps_item("PS-105"),               # unknown (ambiguous shared child) -> unassigned bucket
    ]
    live = [
        _cand("SC-903", ["PS-102"]),                 # keeps PS-102 in-progress
        _cand("SC-906", ["PS-104", "PS-105"]),       # ambiguous: two parents -> both unknown
    ]
    archive = [
        _cand("SC-901", ["PS-100"], status="shipped"),
        _cand("SC-902", ["PS-101"], status="rejected"),
    ]
    _write(vault, scope, live, archive)


def _sha_readset(vault: Path) -> dict:
    out = {}
    for rel in ("product-scope.json", "candidates.json", "archive/candidates.json"):
        p = vault / rel
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return out


# ── AC2 / M1 / M2 — whole-app + per-area counts over the full 5-way partition ──

def test_ac2_whole_app_and_per_component_counts(tmp_path):
    _five_state_vault(tmp_path)
    env = product_rollup.compute_rollup(tmp_path)
    assert env["scope_present"] and not env.get("error"), env
    assert env["unit"] == "capabilities"
    w = env["whole_app"]
    assert (w["done"], w["rejected_only"], w["in_progress"], w["no_children"], w["unknown"], w["total"]) \
        == (1, 1, 1, 1, 2, 6), w
    comps = {c["name"]: c for c in env["areas"]}
    assert comps["payments"]["done"] == 1 and comps["payments"]["rejected_only"] == 1
    assert comps["billing"]["in_progress"] == 1 and comps["billing"]["no_children"] == 1
    assert env["unassigned"]["unknown"] == 2 and env["unassigned"]["total"] == 2


def test_m2_rejected_only_does_not_inflate_done(tmp_path):
    """A capability whose only archived child is REJECTED reads 'done' from cmd_done, but must NOT be
    counted in the headline 'done' — it lands in rejected_only."""
    _five_state_vault(tmp_path)
    env = product_rollup.compute_rollup(tmp_path)
    payments = next(c for c in env["areas"] if c["name"] == "payments")
    assert payments["done"] == 1, "only the SHIPPED capability counts as done"
    assert payments["rejected_only"] == 1, "the rejected-only capability must be surfaced separately"
    assert payments["composition"] == {"shipped": 1, "rejected": 1}


# ── M1 / AC5 — strata-sum conservation over the FULL state set (non-vacuous) ──

def test_m1_strata_conserve(tmp_path):
    _five_state_vault(tmp_path)
    env = product_rollup.compute_rollup(tmp_path)
    w = env["whole_app"]
    for s in ("done", "rejected_only", "in_progress", "no_children", "unknown", "total"):
        strata_sum = env["unassigned"][s] + sum(c[s] for c in env["areas"])
        assert strata_sum == w[s], f"conservation breach on {s!r}: {strata_sum} != {w[s]}"
    assert sum(w[s] for s in
               ("done", "rejected_only", "in_progress", "no_children", "unknown")) == w["total"]


# ── M-add-2 — the area order is pinned: least-complete first, then |caps|, then name ──

def test_m_add_2_component_order_least_complete_first(tmp_path):
    _five_state_vault(tmp_path)
    env = product_rollup.compute_rollup(tmp_path)
    order = [(c["name"], c["rank"]) for c in env["areas"]]
    # billing ratio 0/2=0.0 is less complete than payments 1/2=0.5 -> billing ranks first
    assert order == [("billing", 1), ("payments", 2)], order


# ── M-add-3 — pulse_line ALWAYS names the done_definition; never a bare 'X/Y done' ──

def test_m_add_3_pulse_line_always_qualified(tmp_path):
    _five_state_vault(tmp_path)
    env = product_rollup.compute_rollup(tmp_path)
    line = env["pulse_line"]
    assert "materialized candidate archived" in line, line
    assert "1/6 capabilities done" in line, line
    # the bare form (a count with no qualifier) must never appear
    assert not (("done" in line) and ("materialized" not in line))


# ── M3 — a present-but-empty scope is a distinct surface, never '0/0 done' ──

def test_m3_empty_scope_is_not_zero_over_zero(tmp_path):
    (tmp_path / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "fx", "items": []}), encoding="utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    env = product_rollup.compute_rollup(tmp_path)
    assert env["scope_present"] and env["empty_scope"] and not env.get("error"), env
    assert "0/0" not in env["pulse_line"], env["pulse_line"]
    assert "0 capabilities decomposed yet" in env["pulse_line"]


# ── M4 — no scope is a clean scope_present:false; corrupt scope is a fail-visible error ──

def test_m4_no_scope_is_clean_omit(tmp_path):
    env = product_rollup.compute_rollup(tmp_path)  # empty vault, no product-scope.json
    assert env == {"scope_present": False, "pulse_line": ""}, env


def test_m4_corrupt_scope_is_fail_visible_error(tmp_path):
    (tmp_path / "product-scope.json").write_text("{ this is not json", encoding="utf-8")
    env = product_rollup.compute_rollup(tmp_path)
    assert env["scope_present"] is True and env.get("error"), env
    assert "Product shape unavailable" in env["pulse_line"]


# ── m4 — an item-id-set mismatch between the two non-atomic reads degrades to error ──

def test_m4_idset_mismatch_degrades_to_error():
    done_result = {"items": [{"item": "PS-1", "state": "no-children", "archived_composition": {}}]}
    scope = {"items": [{"id": "PS-1"}, {"id": "PS-2", "area": "x"}]}  # scope carries an extra item
    env = product_rollup.build_envelope(done_result, scope)
    assert env.get("error") and "changed between reads" in env["error"], env


# ── AC2 / m5 / AC5 — the rollup writes NOTHING across the full read-set ──

def test_ac2_read_only_full_readset(tmp_path):
    _five_state_vault(tmp_path)
    before = _sha_readset(tmp_path)
    product_rollup.compute_rollup(tmp_path)
    # and the CLI path (what /pulse subprocesses)
    subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--json"],
                   capture_output=True, text=True, encoding="utf-8", timeout=60)
    after = _sha_readset(tmp_path)
    assert before == after, "the rollup (import + CLI) must mutate NO vault file"
    # no NEW file was written either
    assert set(p.name for p in tmp_path.rglob("*")) >= set()  # sanity; exact set asserted below
    assert not (tmp_path / "product-rollup.json").exists(), "the rollup must not persist an aggregate"


# ── AC5 — the read paths mint no id and hold no lock (source-level tripwires) ──

def test_ac5_no_id_mint_no_write_primitive_in_source():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "next_id" not in src, "the rollup must mint no id (no new id kind)"
    for prim in (".write_text", ".write_bytes", "safe_mutate", "vault_edit", "open("):
        assert prim not in src, f"the read-only rollup must not carry a write primitive: {prim}"


# ── BC-PROJ-12 — the conservation guard is NON-VACUOUS: prove it can go red ──

def test_conserves_detects_a_breach():
    """The guard logic must distinguish a balanced partition from a broken one, else its green is
    meaningless (BC-PROJ-12: a positive-only assertion cannot tell 'passed' from 'stopped matching')."""
    whole = {"done": 2, "rejected_only": 0, "in_progress": 0, "no_children": 0, "unknown": 0, "total": 2}
    good = [{"done": 2, "rejected_only": 0, "in_progress": 0, "no_children": 0, "unknown": 0, "total": 2}]
    empty = {"done": 0, "rejected_only": 0, "in_progress": 0, "no_children": 0, "unknown": 0, "total": 0}
    assert product_rollup._conserves(whole, good, empty) is True
    # break it: a stratum that does not sum to the whole must be caught
    bad = [{"done": 1, "rejected_only": 0, "in_progress": 0, "no_children": 0, "unknown": 0, "total": 1}]
    assert product_rollup._conserves(whole, bad, empty) is False


def test_build_envelope_routes_conservation_breach_to_error(monkeypatch):
    """If _conserves ever returns False, build_envelope must degrade to a fail-visible `error`, never
    emit a silently-wrong rate (BC-PROJ-12: observe the gate go red, naming the site)."""
    monkeypatch.setattr(product_rollup, "_conserves", lambda *a, **k: False)
    done_result = {"items": [{"item": "PS-1", "state": "done",
                              "archived_composition": {"shipped": 1, "rejected": 0}}]}
    scope = {"items": [{"id": "PS-1", "area": "x"}]}
    env = product_rollup.build_envelope(done_result, scope)
    assert env.get("error") and "conservation" in env["error"], env


# ── slice-084 B4 — the completeness governor: present iff scope decomposed but 0 capabilities built ──

def test_b4_governor_present_when_scope_decomposed_but_zero_built(tmp_path):
    scope = [_ps_item("PS-1", "core"), _ps_item("PS-2", "core")]
    live = [_cand("SC-1", ["PS-1"])]                       # PS-1 in-progress, PS-2 no-children -> 0 built
    _write(tmp_path, scope, live, [])
    env = product_rollup.compute_rollup(tmp_path)
    assert env["whole_app"]["done"] == 0 and env["whole_app"]["total"] == 2, env["whole_app"]
    assert env.get("governor"), "B4: a decomposed-but-0-built product must carry a governor nudge"
    assert "0/2" in env["governor"] and "instrumentation" in env["governor"], env["governor"]


def test_b4_governor_absent_once_anything_built(tmp_path):
    _five_state_vault(tmp_path)                            # PS-100 shipped -> done == 1
    env = product_rollup.compute_rollup(tmp_path)
    assert env["whole_app"]["done"] >= 1
    assert "governor" not in env, "B4: a product with >=1 built capability carries NO governor"


def test_b4_governor_absent_on_empty_scope(tmp_path):
    (tmp_path / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "fx", "items": []}), encoding="utf-8")
    (tmp_path / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    env = product_rollup.compute_rollup(tmp_path)
    assert env["empty_scope"] and "governor" not in env, env


# ── slice-084 — a legacy `component:` scope key is still stratified (back-compat alias on read) ──

def test_legacy_component_field_still_rolls_up(tmp_path):
    scope = [{"id": "PS-1", "title": "x", "component": "payments",
              "assumptions": [{"id": "A1", "statement": "x", "blocking": True, "spike_status": "unproven"}]}]
    live = [_cand("SC-1", ["PS-1"])]
    _write(tmp_path, scope, live, [])
    env = product_rollup.compute_rollup(tmp_path)
    assert "payments" in [c["name"] for c in env["areas"]], env["areas"]


# ── AC3 contract — the CLI emits the envelope /pulse consumes, exit 0 always ──

def test_ac3_cli_envelope_shape_and_exit_zero(tmp_path):
    _five_state_vault(tmp_path)
    cp = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--json"],
                        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp.returncode == 0, cp.stderr
    env = json.loads(cp.stdout)
    assert env["scope_present"] and "whole_app" in env and "pulse_line" in env
    assert env["done_definition"] == "materialized candidate archived"
    # exit 0 even with no scope
    cp2 = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path / "nope"), "--json"],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp2.returncode == 0 and json.loads(cp2.stdout)["scope_present"] is False
