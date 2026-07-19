"""slice-082 / SC-184 / [[ADR-093]] — story_inputs.project_rollup_for_story (the AC1 projection surface).

Proves the pure projection over every rollup state, the M3 branch-order safety (product_rollup's error /
no-scope returns OMIT whole_app, so a naive read would KeyError — the projection must NOT crash), the m3
in_progress carry, and count fidelity (M-add-1: the substrate the renderer will render deterministically
carries the envelope's exact numbers). Also covers the exit-0-always `project` CLI + the `inject` verb.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import product_rollup  # noqa: E402

SCRIPT = ROOT / "skills" / "slice-story" / "scripts" / "story_inputs.py"

# import the module under test via its own bootstrap
sys.path.insert(0, str(ROOT / "skills" / "slice-story" / "scripts"))
import story_inputs  # noqa: E402

project = story_inputs.project_rollup_for_story


# ── fixture envelopes (mirror product_rollup's real shapes) ─────────────────────────────────

def _stratum(done=0, rejected_only=0, in_progress=0, no_children=0, unknown=0):
    total = done + rejected_only + in_progress + no_children + unknown
    return {"done": done, "rejected_only": rejected_only, "in_progress": in_progress,
            "no_children": no_children, "unknown": unknown, "total": total,
            "composition": {"shipped": done, "rejected": rejected_only}}


def _populated_env():
    """Two named components + a non-empty unassigned bucket — the AC2 synthetic (m1: only reachable via a
    synthetic fixture; the real vault is all-unassigned until SC-183)."""
    comps = [
        {"name": "auth", "rank": 1, **_stratum(done=2, in_progress=1, no_children=4)},      # total 7
        {"name": "billing", "rank": 2, **_stratum(done=3, in_progress=2)},                   # total 5
    ]
    whole = _stratum(done=6, in_progress=3, no_children=4, unknown=3)                         # total 16
    unassigned = {"name": "unassigned", **_stratum(done=1, unknown=3)}                        # total 4
    return {"scope_present": True, "unit": "capabilities",
            "done_definition": "materialized candidate archived", "empty_scope": False,
            "whole_app": whole, "areas": comps, "unassigned": unassigned,
            "pulse_line": "Whole app 6/16 capabilities done (materialized candidate archived; ...)"}


def _degenerate_env():
    """Every capability unassigned (components:[]) — the COMMON live case (slice-080 reflection)."""
    whole = _stratum(done=0, in_progress=4)
    return {"scope_present": True, "unit": "capabilities",
            "done_definition": "materialized candidate archived", "empty_scope": False,
            "whole_app": whole, "areas": [],
            "unassigned": {"name": "unassigned", **_stratum(done=0, in_progress=4)},
            "pulse_line": "Whole app 0/4 capabilities done (...)"}


# ── AC1 / M-add-1: populated projection carries the exact numbers, drops presentation fields ──

def test_populated_projection_shape_and_fidelity():
    sub = project(_populated_env())
    assert sub["state"] == "populated"
    assert sub["unit"] == "capabilities"
    # M-add-1 count fidelity: the substrate mirrors the envelope's numbers exactly.
    assert sub["whole_app"] == {"done": 6, "in_progress": 3, "total": 16}
    assert [c["name"] for c in sub["areas"]] == ["auth", "billing"]
    assert sub["areas"][0] == {"name": "auth", "done": 2, "in_progress": 1, "total": 7, "rank": 1}
    assert sub["areas"][1] == {"name": "billing", "done": 3, "in_progress": 2, "total": 5, "rank": 2}
    assert sub["unassigned"] == {"done": 1, "in_progress": 0, "total": 4}
    # ADR-093: /pulse's presentation fields are DROPPED at source (never reach the story).
    blob = json.dumps(sub)
    assert "pulse_line" not in blob and "done_definition" not in blob
    assert "materialized candidate archived" not in blob


def test_m3_in_progress_is_carried():
    # m3: an in-progress component must be distinguishable from an untouched one.
    sub = project(_populated_env())
    assert sub["areas"][0]["in_progress"] == 1
    assert sub["whole_app"]["in_progress"] == 3


def test_degenerate_unassigned_has_honest_note():
    sub = project(_degenerate_env())
    assert sub["state"] == "degenerate_unassigned"
    assert sub["areas"] == []
    assert sub["whole_app"] == {"done": 0, "in_progress": 4, "total": 4}
    assert sub.get("note") and "unassigned" in sub["note"].lower()


# ── M3: the degenerate envelopes that OMIT whole_app must never KeyError ─────────────────────

def test_error_envelope_projects_to_error_no_crash():
    # product_rollup._error_envelope OMITS whole_app/components/unassigned — a naive read crashes.
    raw = product_rollup._error_envelope("strata-sum conservation breached")
    assert "whole_app" not in raw                              # premise: the key really is absent
    sub = project(raw)                                         # must not raise
    assert sub == {"state": "error", "error": "strata-sum conservation breached"}


def test_no_scope_return_projects_to_no_scope_no_crash():
    # product_rollup's no-scope return is {"scope_present": False, "pulse_line": ""} — no whole_app.
    raw = {"scope_present": False, "pulse_line": ""}
    sub = project(raw)                                         # must not raise
    assert sub == {"state": "no_scope"}


def test_empty_scope_is_distinct_present_with_note():
    raw = {"scope_present": True, "empty_scope": True, "unit": "capabilities",
           "whole_app": _stratum(), "areas": [], "unassigned": {"name": "unassigned", **_stratum()}}
    sub = project(raw)
    assert sub["state"] == "empty_scope"
    assert sub.get("note")
    assert "whole_app" not in sub                              # a '0/0' block is never surfaced


def test_branch_order_error_wins_over_scope_present():
    # An envelope with BOTH error and scope_present must classify as error (fail-visible), not read whole_app.
    raw = {"scope_present": True, "error": "compute failed", "pulse_line": "x"}
    assert project(raw) == {"state": "error", "error": "compute failed"}


def test_malformed_env_degrades_to_error():
    assert project(None)["state"] == "error"
    assert project([1, 2, 3])["state"] == "error"


def test_partial_populated_env_never_crashes():
    # whole_app present but a component missing counts -> total-function defaults, no crash.
    raw = {"scope_present": True, "empty_scope": False, "whole_app": {"done": 1, "total": 2},
           "areas": [{"name": "x", "rank": 1}], "unassigned": {}}
    sub = project(raw)
    assert sub["areas"][0] == {"name": "x", "done": 0, "in_progress": 0, "total": 0, "rank": 1}
    assert sub["whole_app"] == {"done": 1, "in_progress": 0, "total": 2}


# ── the CLI (exit-0-always project; inject writes the substrate) ─────────────────────────────

def _write_vault(tmp_path: Path, scope_items) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "product-scope.json").write_text(
        json.dumps({"_schema": "aisdlc/product-scope@1", "project": "fx", "items": scope_items}),
        encoding="utf-8")
    return v


def test_cli_project_exit0_always_on_no_scope(tmp_path):
    v = tmp_path / "empty"
    v.mkdir()
    cp = subprocess.run([sys.executable, str(SCRIPT), "project", "--vault", str(v), "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0
    assert json.loads(cp.stdout)["state"] == "no_scope"


def test_cli_project_error_rides_stdout_exit0(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "product-scope.json").write_text("not json", encoding="utf-8")
    cp = subprocess.run([sys.executable, str(SCRIPT), "project", "--vault", str(v), "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0                                  # exit-0-always
    assert json.loads(cp.stdout)["state"] == "error"


def test_cli_inject_writes_product_shape(tmp_path):
    v = _write_vault(tmp_path, [])                             # empty scope
    sf = tmp_path / "story-sections.json"
    sf.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "slice-082",
                              "sections": [{"heading": "x", "body_md": "y"}]}), encoding="utf-8")
    cp = subprocess.run([sys.executable, str(SCRIPT), "inject", "--sections-file", str(sf),
                         "--vault", str(v)], capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr
    data = json.loads(sf.read_text(encoding="utf-8"))
    assert data["product_shape"]["state"] == "empty_scope"
    assert data["product_shape"]["_source"] == "story_inputs.inject"   # CR1 provenance stamp
    assert data["sections"]                                    # narrator content preserved


def test_cli_inject_unreadable_sections_is_fail_visible(tmp_path):
    v = _write_vault(tmp_path, [])
    cp = subprocess.run([sys.executable, str(SCRIPT), "inject",
                         "--sections-file", str(tmp_path / "missing.json"), "--vault", str(v)],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 2                                  # io failure surfaces (never a silent drop)
    assert "cannot read" in cp.stderr


# ── BC-PROJ-3: the serialize leg — a non-ASCII component name round-trips VERBATIM ───────────

def _ps_item(iid, area):
    return {"id": iid, "title": iid.lower(), "area": area,
            "assumptions": [{"id": "A1", "statement": "x", "blocking": True, "spike_status": "unproven"}]}


def _write_vault_nonascii(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "archive").mkdir(parents=True)
    (v / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "fx",
         "items": [_ps_item("PS-1", "café")]}, ensure_ascii=False), encoding="utf-8")
    for rel in ("candidates.json", "archive/candidates.json"):
        (v / rel).write_text(json.dumps(
            {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": []}), encoding="utf-8")
    return v


def test_project_cli_preserves_non_ascii_component_literal(tmp_path):
    v = _write_vault_nonascii(tmp_path)
    cp = subprocess.run([sys.executable, str(SCRIPT), "project", "--vault", str(v), "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr
    assert "café" in cp.stdout and "caf\\u00e9" not in cp.stdout   # literal char, not an escape
    assert json.loads(cp.stdout)["areas"][0]["name"] == "café"


def test_inject_writes_non_ascii_component_verbatim(tmp_path):
    v = _write_vault_nonascii(tmp_path)
    sf = tmp_path / "story-sections.json"
    sf.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "s",
                              "sections": [{"heading": "x", "body_md": "y"}]}), encoding="utf-8")
    cp = subprocess.run([sys.executable, str(SCRIPT), "inject", "--sections-file", str(sf),
                         "--vault", str(v)], capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr
    raw = sf.read_text(encoding="utf-8")
    assert "café" in raw and "caf\\u00e9" not in raw              # written verbatim (ensure_ascii=False)
