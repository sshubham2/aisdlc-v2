"""slice-080 / SC-165 / [[ADR-091]] — AC4: the OPTIONAL area lens on the /slice pick surface.
slice-084 renamed the flag `--component` -> `--area` (component kept as a back-compat alias) and
SOURCE-SCOPED the lens (A1): it now filters to PRODUCT-sourced candidates only, so `--area unassigned`
means "a product capability with no area yet", never the pipeline-exhaust chores that also carry no
product-scope parent.

The lens FILTERS the pickable list to one area (blocked/in-flight stay global context — critique m2),
takes no lock / mints no id / writes no status (a lens is not ownership). Default-OFF is byte-identical
to today. The candidate->area join uses owner_refs (M-add-1): a candidate claiming TWO product-scope
parents lands in 'unassigned', never silently under its first parent.

(This file keeps its legacy name + function names for shippability-catalog node-id stability; it now
tests the `--area` canonical path plus the `--component` alias.)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOP = _REPO / "skills" / "slice" / "scripts" / "candidates_top.py"

# the pre-slice-080 default-OFF payload contract (m5: adding the lens must not perturb it)
_TOP_LEVEL_KEYS = {"action", "project", "counts", "top", "blocked", "in_flight"}
_TOP_ENTRY_KEYS = {"id", "title", "score", "effective_score", "path_class", "demote_reason",
                   "effort", "blast_radius", "deps_unmet", "couples_with"}


def _cand(cid, refs, *, score=5):
    return {"id": cid, "title": cid.lower(), "status": "candidate", "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None,
            "source": [{"type": t, "ref": r} for (t, r) in refs],
            "priority": {"score": score, "severity": "medium", "effort": "M"}}


def _write(vault, cands, scope_items):
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "t", "candidates": cands, "pick_log": []}),
        encoding="utf-8")
    (vault / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "t", "items": scope_items}), encoding="utf-8")


def _fixture(vault, *, field="area"):
    # `field` toggles the canonical `area` key vs the `component` back-compat alias, so one fixture
    # exercises both the rename and its alias.
    scope = [{"id": "PS-100", field: "payments"}, {"id": "PS-102", field: "billing"}]
    cands = [
        _cand("SC-A", [("product-scope", "PS-100")]),                       # payments (product-sourced)
        _cand("SC-B", [("product-scope", "PS-102")]),                       # billing  (product-sourced)
        _cand("SC-C", [("finding", "r-1")]),                               # NON-product chore (A1: excluded)
        _cand("SC-D", [("product-scope", "PS-100"), ("product-scope", "PS-102")]),  # ambiguous -> unassigned
        _cand("SC-E", [("product-scope", "PS-100")]),                       # payments (product-sourced)
    ]
    _write(vault, cands, scope)


def _run(vault, *args):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    return subprocess.run([sys.executable, str(_TOP), "--vault", str(vault), "--json", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _ids(vault, *args):
    cp = _run(vault, *args)
    assert cp.returncode == 0, cp.stderr
    return sorted(t["id"] for t in json.loads(cp.stdout)["top"]), cp


# ── AC4 — the lens filters pickable to one area ──

def test_component_filters_pickable(tmp_path):
    _fixture(tmp_path)
    payments, _ = _ids(tmp_path, "--area", "payments")
    assert payments == ["SC-A", "SC-E"], payments
    billing, _ = _ids(tmp_path, "--area", "billing")
    assert billing == ["SC-B"], billing


# ── M-add-1 — a two-parent candidate files in 'unassigned', not under parent #1 ──
# ── slice-084 A1 — a NON-product chore is EXCLUDED from the (product) area lens entirely ──

def test_two_parent_candidate_is_unassigned_not_first_parent(tmp_path):
    _fixture(tmp_path)
    payments, _ = _ids(tmp_path, "--area", "payments")
    assert "SC-D" not in payments, "the ambiguous two-parent candidate must NOT file under its first parent"
    unassigned, _ = _ids(tmp_path, "--area", "unassigned")
    # SC-D: product-sourced but ambiguous (2 parents) -> unassigned. SC-C: a non-product chore, so A1
    # source-scoping EXCLUDES it from the product area lens (the whole point of A1 — 'unassigned' means a
    # product capability with no area, never a pipeline chore).
    assert "SC-D" in unassigned, unassigned
    assert "SC-C" not in unassigned, "A1: a non-product-sourced chore must NOT appear in the area lens"
    # unassigned must NOT sweep in the single-parent (area-annotated) candidates
    assert "SC-A" not in unassigned and "SC-B" not in unassigned


# ── slice-084 A1 — the source-scoping is explicit: NO non-product candidate under ANY area value ──

def test_a1_area_lens_excludes_nonproduct_candidates(tmp_path):
    _fixture(tmp_path)
    for area in ("payments", "billing", "unassigned", "does-not-exist"):
        ids, _ = _ids(tmp_path, "--area", area)
        assert "SC-C" not in ids, f"the finding-sourced chore SC-C leaked into --area {area}"


# ── slice-084 — the --component alias yields the SAME result as --area ──

def test_component_alias_matches_area(tmp_path):
    _fixture(tmp_path)
    via_area, _ = _ids(tmp_path, "--area", "payments")
    via_component, _ = _ids(tmp_path, "--component", "payments")
    assert via_area == via_component == ["SC-A", "SC-E"], (via_area, via_component)


# ── slice-084 — a legacy `component:` scope field is still READ (back-compat alias on the data field) ──

def test_legacy_component_field_still_read(tmp_path):
    _fixture(tmp_path, field="component")                     # scope items carry the OLD `component` key
    payments, _ = _ids(tmp_path, "--area", "payments")
    assert payments == ["SC-A", "SC-E"], payments


# ── m2 — the lens filters ONLY pickable; an unknown area -> empty + explicit note ──

def test_unknown_component_is_empty_with_note(tmp_path):
    _fixture(tmp_path)
    cp = _run(tmp_path, "--area", "does-not-exist")
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["top"] == [], "unknown area must yield an empty list, never a silent full list"
    assert payload["area_lens"]["area"] == "does-not-exist"
    assert payload["area_lens"]["known"] is False


# ── m5 — default-OFF is byte-identical: no lens flag perturbs neither key-set nor value ──

def test_default_off_payload_is_unperturbed(tmp_path):
    _fixture(tmp_path)
    cp = _run(tmp_path)  # NO --area / --component
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert set(payload.keys()) == _TOP_LEVEL_KEYS, "default-OFF must add NO top-level key (no area_lens)"
    for entry in payload["top"]:
        assert set(entry.keys()) == _TOP_ENTRY_KEYS, f"default-OFF top entry perturbed: {set(entry)}"
    # golden: the same invocation twice is deterministic + identical
    cp2 = _run(tmp_path)
    assert cp.stdout == cp2.stdout, "default-OFF output must be deterministic + byte-identical"


# ── AC5 — the lens writes nothing (no lock/status) ──

def test_lens_writes_nothing(tmp_path):
    _fixture(tmp_path)

    def _sha():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (tmp_path / "candidates.json", tmp_path / "product-scope.json")}

    before = _sha()
    _run(tmp_path, "--area", "payments")
    _run(tmp_path, "--area", "unassigned")
    _run(tmp_path, "--component", "payments")                 # the alias is read-only too
    assert _sha() == before, "the area lens must mutate NO vault file (a lens is not ownership)"


# ── AC5 / M-add-4 — the lens path mints no id and holds no lock (source-level tripwire) ──

def test_lens_path_mints_no_id_takes_no_lock():
    src = _TOP.read_text(encoding="utf-8")
    assert "next_id" not in src, "the pick digest / lens must mint no id (AC5: no new id kind)"
    # call-syntax tripwires (a doc REFERENCE to vault_edit is fine; a CALL is not)
    for prim in ("safe_mutate_text(", "vault_edit.", ".write_text(", ".write_bytes(", "_vault_write"):
        assert prim not in src, f"the read-only pick digest / lens must hold no write/lock primitive: {prim}"
