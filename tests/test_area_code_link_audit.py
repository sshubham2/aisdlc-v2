"""slice-084 C1c — area_code_link_audit.py: the area<->code-component link backstop for /drift-check.

Reconciles a product-scope item's OPTIONAL `code_components[]` (the product AREA axis) against the Heavy
`components/*.json` inventory (the code axis). Proves it flags a STALE LINK when a declared link resolves
to no real component, and DEGRADES CLEANLY (no-scope / no-links / no-code-inventory) everywhere else — the
whole reason the link field is optional + empty-legal.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import area_code_link_audit as acl  # noqa: E402

SCRIPT = ROOT / "scripts" / "lib" / "area_code_link_audit.py"


def _scope(vault: Path, items: list) -> None:
    (vault / "product-scope.json").write_text(
        json.dumps({"_schema": "aisdlc/product-scope@1", "project": "fx", "items": items}),
        encoding="utf-8")


def _component(vault: Path, name: str) -> None:
    d = vault / "components"
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(
        json.dumps({"_schema": "aisdlc/component@1", "name": name}), encoding="utf-8")


def _item(iid, area=None, code_components=None):
    it = {"id": iid, "title": iid.lower()}
    if area is not None:
        it["area"] = area
    if code_components is not None:
        it["code_components"] = code_components
    return it


# ── degrade states — all exit-0, each with a distinct status ──

def test_no_scope_degrades_clean(tmp_path):
    assert acl.audit(tmp_path)["status"] == "no-scope"


def test_no_links_degrades_clean(tmp_path):
    _scope(tmp_path, [_item("PS-1", "payments")])              # area, but no code_components
    res = acl.audit(tmp_path)
    assert res["status"] == "no-links" and res["findings"] == []


def test_links_without_inventory_skip(tmp_path):
    _scope(tmp_path, [_item("PS-1", "payments", ["orders"])])  # a link, but no components/ dir
    res = acl.audit(tmp_path)
    assert res["status"] == "no-code-inventory" and res["findings"] == []


# ── the real check — resolves vs stale ──

def test_resolving_link_is_clean(tmp_path):
    _scope(tmp_path, [_item("PS-1", "payments", ["orders"])])
    _component(tmp_path, "orders")
    res = acl.audit(tmp_path)
    assert res["status"] == "clean" and res["findings"] == []


def test_stale_link_is_flagged(tmp_path):
    _scope(tmp_path, [_item("PS-1", "payments", ["orders", "ghost"])])
    _component(tmp_path, "orders")                             # 'ghost' has no component file
    res = acl.audit(tmp_path)
    assert res["status"] == "findings"
    assert len(res["findings"]) == 1
    f = res["findings"][0]
    assert f["code_component"] == "ghost" and f["area"] == "payments" and f["item"] == "PS-1"


def test_legacy_component_field_used_for_area_label(tmp_path):
    # the `component` alias key still supplies the item's area label in the finding
    _scope(tmp_path, [{"id": "PS-1", "title": "x", "component": "legacy-area",
                       "code_components": ["ghost"]}])
    _component(tmp_path, "orders")
    res = acl.audit(tmp_path)
    assert res["findings"][0]["area"] == "legacy-area"


def test_component_file_stem_resolves_without_name_field(tmp_path):
    # a components/*.json lacking a `name` falls back to the filename stem
    _scope(tmp_path, [_item("PS-1", "payments", ["billing"])])
    d = tmp_path / "components"
    d.mkdir()
    (d / "billing.json").write_text(json.dumps({"_schema": "aisdlc/component@1"}), encoding="utf-8")
    assert acl.audit(tmp_path)["status"] == "clean"


# ── CLI exit codes: 0 clean/degrade, 1 stale link ──

def test_cli_exit_codes(tmp_path):
    _scope(tmp_path, [_item("PS-1", "payments", ["orders"])])
    _component(tmp_path, "orders")
    cp = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--json"],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr

    _scope(tmp_path, [_item("PS-1", "payments", ["ghost"])])   # now stale
    cp2 = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(tmp_path), "--json"],
                         capture_output=True, text=True, encoding="utf-8")
    assert cp2.returncode == 1
    assert json.loads(cp2.stdout)["findings"][0]["code_component"] == "ghost"
