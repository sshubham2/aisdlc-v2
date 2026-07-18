"""slice-080 / SC-165 / [[ADR-091]] — AC4: the OPTIONAL --component lens on the /slice pick surface.

The lens FILTERS the pickable list to one component (blocked/in-flight stay global context — critique
m2), takes no lock / mints no id / writes no status (a lens is not ownership). Default-OFF is
byte-identical to today. The candidate->component join uses owner_refs (M-add-1): a candidate claiming
TWO product-scope parents lands in 'unassigned', never silently under its first parent.
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

# the pre-slice-080 default-OFF payload contract (m5: adding --component must not perturb it)
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


def _fixture(vault):
    scope = [{"id": "PS-100", "component": "payments"}, {"id": "PS-102", "component": "billing"}]
    cands = [
        _cand("SC-A", [("product-scope", "PS-100")]),                       # payments
        _cand("SC-B", [("product-scope", "PS-102")]),                       # billing
        _cand("SC-C", [("finding", "r-1")]),                               # unassigned (no product parent)
        _cand("SC-D", [("product-scope", "PS-100"), ("product-scope", "PS-102")]),  # ambiguous -> unassigned
        _cand("SC-E", [("product-scope", "PS-100")]),                       # payments
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


# ── AC4 — the lens filters pickable to a component ──

def test_component_filters_pickable(tmp_path):
    _fixture(tmp_path)
    payments, _ = _ids(tmp_path, "--component", "payments")
    assert payments == ["SC-A", "SC-E"], payments
    billing, _ = _ids(tmp_path, "--component", "billing")
    assert billing == ["SC-B"], billing


# ── M-add-1 — a two-parent candidate files in 'unassigned', not under parent #1 ──

def test_two_parent_candidate_is_unassigned_not_first_parent(tmp_path):
    _fixture(tmp_path)
    payments, _ = _ids(tmp_path, "--component", "payments")
    assert "SC-D" not in payments, "the ambiguous two-parent candidate must NOT file under its first parent"
    unassigned, _ = _ids(tmp_path, "--component", "unassigned")
    assert "SC-C" in unassigned and "SC-D" in unassigned, unassigned
    # unassigned must NOT sweep in the single-parent candidates
    assert "SC-A" not in unassigned and "SC-B" not in unassigned


# ── m2 — the lens filters ONLY pickable; an unknown component -> empty + explicit note ──

def test_unknown_component_is_empty_with_note(tmp_path):
    _fixture(tmp_path)
    cp = _run(tmp_path, "--component", "does-not-exist")
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["top"] == [], "unknown component must yield an empty list, never a silent full list"
    assert payload["component_lens"]["component"] == "does-not-exist"
    assert payload["component_lens"]["known"] is False


# ── m5 — default-OFF is byte-identical: no --component perturbs neither key-set nor value ──

def test_default_off_payload_is_unperturbed(tmp_path):
    _fixture(tmp_path)
    cp = _run(tmp_path)  # NO --component
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert set(payload.keys()) == _TOP_LEVEL_KEYS, "default-OFF must add NO top-level key (no component_lens)"
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
    _run(tmp_path, "--component", "payments")
    _run(tmp_path, "--component", "unassigned")
    assert _sha() == before, "the component lens must mutate NO vault file (a lens is not ownership)"


# ── AC5 / M-add-4 — the lens path mints no id and holds no lock (source-level tripwire) ──

def test_lens_path_mints_no_id_takes_no_lock():
    src = _TOP.read_text(encoding="utf-8")
    assert "next_id" not in src, "the pick digest / lens must mint no id (AC5: no new id kind)"
    # call-syntax tripwires (a doc REFERENCE to vault_edit is fine; a CALL is not)
    for prim in ("safe_mutate_text(", "vault_edit.", ".write_text(", ".write_bytes(", "_vault_write"):
        assert prim not in src, f"the read-only pick digest / lens must hold no write/lock primitive: {prim}"
