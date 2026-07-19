"""
Bug (SC-182): `product_scope revise` accepts an item whose grouping label is the reserved sentinel
`unassigned`, writing it verbatim.

`_valid_area` (slice-081 / ADR-092; renamed from `_valid_component` in slice-084) rejects the reserved
sentinel, and slice-083 wired it into the shared `_load_items` seam so EVERY write path (persist / revise /
--scope-file replay) enforces it — closing the write-seam asymmetry where one path was guarded and another
was not. slice-084 renamed the field `component` -> `area` and keeps `component` as a back-compat alias:
the alias MUST NOT be a bypass — both keys normalize through `_valid_area` at the seam.

Expected: `revise` rejects the reserved value (exit 2), whether supplied as `area` OR the `component`
alias -- the sentinel rule holds across every write seam, single-sourced.
Actual (pre-fix): accepted, exit 0, `unassigned` persisted verbatim.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "scripts/lib/product_scope.py"

_ITEM = {
    "id": "PS-001", "decomposition_label": "core", "title": "build-core",
    "description": "the core", "user_visible_outcome": "it runs", "depends_on": [],
    "assumptions": [{"id": "A1", "statement": "expressible", "blocking": True,
                     "spike_status": "unproven"}],
    "verification_plan": "drive one run",
}


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _setup(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", {
        "_schema": "aisdlc/product-scope@1", "project": "fixture", "items": [dict(_ITEM)],
    })
    _write(v / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture",
        "counters": {"sc": 0}, "candidates": [], "pick_log": [],
    })
    return v


def _reserved_not_persisted(v: Path) -> None:
    persisted = json.loads((v / "product-scope.json").read_text(encoding="utf-8"))
    item = next(i for i in persisted["items"] if i["id"] == "PS-001")
    assert item.get("area") != "unassigned", "the reserved sentinel was written to the scope (area)"
    assert item.get("component") != "unassigned", "the reserved sentinel was written to the scope (component alias)"


def test_revise_rejects_reserved_unassigned_component(run_script, tmp_path):
    # canonical `area`
    v = _setup(tmp_path)
    rev = tmp_path / "rev.json"
    _write(rev, {"items": [dict(_ITEM, area="unassigned")]})
    r = run_script(SCRIPT, ["--vault", str(v), "revise", "--items-file", str(rev), "--json"])
    assert r.returncode == 2, (
        f"revise accepted the reserved 'unassigned' area (exit {r.returncode}); set-area rejects it -- "
        f"the guard must hold on every write seam. stderr={r.stderr}"
    )
    assert "unassigned" in r.stderr.lower(), r.stderr
    _reserved_not_persisted(v)


def test_revise_rejects_reserved_via_component_alias(run_script, tmp_path):
    # the back-compat `component` alias must ALSO be rejected -- an alias is not a bypass (slice-084)
    v = _setup(tmp_path)
    rev = tmp_path / "rev.json"
    _write(rev, {"items": [dict(_ITEM, component="unassigned")]})
    r = run_script(SCRIPT, ["--vault", str(v), "revise", "--items-file", str(rev), "--json"])
    assert r.returncode == 2, (
        f"revise accepted the reserved 'unassigned' via the component alias (exit {r.returncode}); "
        f"the alias must normalize through the same seam validator. stderr={r.stderr}"
    )
    assert "unassigned" in r.stderr.lower(), r.stderr
    _reserved_not_persisted(v)
