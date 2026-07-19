"""
Bug (SC-182): `product_scope revise` accepts an item whose `component` is the reserved sentinel
`unassigned`, writing it verbatim.

`_valid_component` (slice-081 / ADR-092) rejects the reserved sentinel, but it is wired ONLY into
`cmd_set_component`. `cmd_revise` writes `it.get("component") or prev.get("component")` (:1351)
without validation, so a capability annotated `unassigned` collides with the rollup's reserved
catch-all bucket -- two distinct `unassigned` surfaces, conflated candidates in the `--component
unassigned` lens. This is the write-seam asymmetry: one write path is guarded, another is not.

Expected: `revise` rejects the reserved component (exit 2), same shape as `set-component` -- the
`_valid_component` sentinel rule holds across every write seam, single-sourced.
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


def test_revise_rejects_reserved_unassigned_component(run_script, tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", {
        "_schema": "aisdlc/product-scope@1", "project": "fixture", "items": [dict(_ITEM)],
    })
    _write(v / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture",
        "counters": {"sc": 0}, "candidates": [], "pick_log": [],
    })
    rev = tmp_path / "rev.json"
    _write(rev, {"items": [dict(_ITEM, component="unassigned")]})

    r = run_script(SCRIPT, ["--vault", str(v), "revise", "--items-file", str(rev), "--json"])

    assert r.returncode == 2, (
        f"revise accepted the reserved 'unassigned' component (exit {r.returncode}); "
        f"set-component rejects it -- the guard must hold on every write seam. stderr={r.stderr}"
    )
    assert "unassigned" in r.stderr.lower(), r.stderr
    # And the reserved name must NOT have been persisted.
    persisted = json.loads((v / "product-scope.json").read_text(encoding="utf-8"))
    comp = next(i for i in persisted["items"] if i["id"] == "PS-001").get("component")
    assert comp != "unassigned", "the reserved sentinel was written to the scope"
