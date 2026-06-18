"""scripts/lib/vault_edit.py — SVW-1 JSON vault-write CLI contracts (4.4 priority b).

Exit-code contract (incl. the item-1.7 fail-visible exits):
    0 success · 2 usage / missing-or-non-array / path-escape / clobber · 3 rewrite CAS conflict
Run via subprocess (the faithful path; the module freezes VAULT_ROOT at import, so
``--vault`` is the in-process-safe override the tests pass explicitly).
"""
from __future__ import annotations

import json

VE = "scripts/lib/vault_edit.py"


def _ve(run_script, vault, *args, stdin=None):
    return run_script(VE, ["--vault", str(vault), *args], stdin=stdin)


def test_append_then_count(run_script, vault):
    # a GENERIC (non-managed) file/array: tests the plain append+count path. (candidates.json /
    # shippability.json now mint their id in-lock and REJECT a caller-supplied one — slice-019 AC2,
    # covered by tests/test_id_allocation_concurrency.py — so the generic contract uses a generic file.)
    f = "generic.json"
    r = _ve(run_script, vault, "append", "--file", f, "--array", "items",
            "--json", '{"id": "C-1"}')
    assert r.returncode == 0, r.stderr
    r2 = _ve(run_script, vault, "count", "--file", f, "--array", "items")
    assert r2.returncode == 0
    assert r2.stdout.strip() == "1"
    data = json.loads((vault / f).read_text(encoding="utf-8"))
    assert data["items"] == [{"id": "C-1"}]


def test_append_list_extends(run_script, vault):
    f = "a.json"
    _ve(run_script, vault, "append", "--file", f, "--array", "xs", "--json", "[1, 2]")
    r = _ve(run_script, vault, "count", "--file", f, "--array", "xs")
    assert r.stdout.strip() == "2"


def test_count_non_array_field_exit2(run_script, vault):
    (vault / "d.json").write_text(json.dumps({"obj": {"k": 1}}))
    r = _ve(run_script, vault, "count", "--file", "d.json", "--array", "obj")
    assert r.returncode == 2


def test_count_missing_field_exit2(run_script, vault):
    (vault / "d.json").write_text(json.dumps({"other": [1]}))
    r = _ve(run_script, vault, "count", "--file", "d.json", "--array", "nope")
    assert r.returncode == 2


def test_count_missing_file_exit2(run_script, vault):
    r = _ve(run_script, vault, "count", "--file", "absent.json", "--array", "xs")
    assert r.returncode == 2


def test_list_missing_dir_exit2(run_script, vault):
    r = _ve(run_script, vault, "list", "--dir", "no-such-dir")
    assert r.returncode == 2


def test_list_empty_dir_exit0(run_script, vault):
    (vault / "empty").mkdir()
    r = _ve(run_script, vault, "list", "--dir", "empty")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_get_missing_path_exit2(run_script, vault):
    (vault / "g.json").write_text(json.dumps({"a": 1}))
    r = _ve(run_script, vault, "get", "--file", "g.json", "--path", ".nope")
    assert r.returncode == 2


def test_get_scalar_raw(run_script, vault):
    (vault / "g.json").write_text(json.dumps({"mode": "standard"}))
    r = _ve(run_script, vault, "get", "--file", "g.json", "--path", ".mode")
    assert r.returncode == 0
    assert r.stdout.strip() == "standard"


def test_path_escape_exit2(run_script, vault):
    r = _ve(run_script, vault, "get", "--file", "../escape.json", "--path", ".")
    assert r.returncode == 2


def test_rewrite_cas_conflict_exit3(run_script, vault, tmp_path):
    target = vault / "doc.json"
    target.write_bytes(b'{"v": 2}\n')
    base = tmp_path / "base"
    base.write_bytes(b'{"v": 1}\n')  # STALE
    content = tmp_path / "content"
    content.write_bytes(b'{"v": 3}\n')
    r = _ve(run_script, vault, "rewrite", "--file", "doc.json",
            "--base-file", str(base), "--content-file", str(content))
    assert r.returncode == 3
    assert target.read_bytes() == b'{"v": 2}\n'  # unchanged


def test_rewrite_cas_match_exit0(run_script, vault, tmp_path):
    target = vault / "doc.json"
    target.write_bytes(b'{"v": 2}\n')
    base = tmp_path / "base"
    base.write_bytes(b'{"v": 2}\n')
    content = tmp_path / "content"
    content.write_bytes(b'{"v": 3}\n')
    r = _ve(run_script, vault, "rewrite", "--file", "doc.json",
            "--base-file", str(base), "--content-file", str(content))
    assert r.returncode == 0, r.stderr
    assert b'"v": 3' in target.read_bytes()


def test_move_no_clobber_exit2(run_script, vault):
    (vault / "src.json").write_text("{}")
    (vault / "dst.json").write_text("{}")
    r = _ve(run_script, vault, "move", "--from", "src.json", "--to", "dst.json")
    assert r.returncode == 2


def test_move_success(run_script, vault):
    (vault / "src.json").write_text("{}")
    r = _ve(run_script, vault, "move", "--from", "src.json", "--to", "moved.json")
    assert r.returncode == 0
    assert (vault / "moved.json").exists()
    assert not (vault / "src.json").exists()


def test_update_set(run_script, vault):
    f = "rec.json"
    _ve(run_script, vault, "append", "--file", f, "--array", "rows",
        "--json", '{"id": "R1", "status": "open"}')
    r = _ve(run_script, vault, "update", "--file", f, "--array", "rows",
            "--id", "R1", "--set", "status=closed")
    assert r.returncode == 0, r.stderr
    data = json.loads((vault / f).read_text())
    assert data["rows"][0]["status"] == "closed"


def test_update_missing_id_exit2(run_script, vault):
    f = "rec.json"
    _ve(run_script, vault, "append", "--file", f, "--array", "rows", "--json", '{"id": "R1"}')
    r = _ve(run_script, vault, "update", "--file", f, "--array", "rows",
            "--id", "NOPE", "--set", "x=1")
    assert r.returncode == 2


def test_update_rejects_managed_id_reassign(run_script, vault):
    # CR1 (slice-019/AC2): a candidates.json row's id is minted in-lock; `update --set id=...`
    # would re-assign a managed id OUT OF BAND, bypassing the allocator -> rejected (exit 2).
    # A non-id field update on the same managed row still works (the guard is surgical).
    f = "candidates.json"
    r = _ve(run_script, vault, "append", "--file", f, "--array", "candidates",
            "--json", '{"title": "t", "status": "candidate"}')
    assert r.returncode == 0, r.stderr
    minted = json.loads((vault / f).read_text(encoding="utf-8"))["candidates"][0]["id"]

    r2 = _ve(run_script, vault, "update", "--file", f, "--array", "candidates",
             "--id", minted, "--set", "id=SC-099")
    assert r2.returncode == 2, "update --set id= on a managed kind must be rejected"
    assert "id" in r2.stderr.lower()

    r3 = _ve(run_script, vault, "update", "--file", f, "--array", "candidates",
             "--id", minted, "--set", "status=spiking")
    assert r3.returncode == 0, r3.stderr
    data = json.loads((vault / f).read_text(encoding="utf-8"))
    assert data["candidates"][0]["status"] == "spiking"
    assert data["candidates"][0]["id"] == minted  # id unchanged by the rejected attempt


def test_append_create_stamps_plugin_version(run_script, vault):
    # 4.5: a brand-new file created via append is stamped with _plugin_version
    f = "new.json"
    r = _ve(run_script, vault, "append", "--file", f, "--array", "xs", "--json", "[1]")
    assert r.returncode == 0, r.stderr
    data = json.loads((vault / f).read_text())
    assert data.get("_plugin_version")  # present + non-empty


def test_append_existing_not_restamped(run_script, vault):
    # only CREATE stamps; appending to an existing un-stamped file does not add it
    f = "pre.json"
    (vault / f).write_text(json.dumps({"xs": [1]}))  # pre-existing, no _plugin_version
    r = _ve(run_script, vault, "append", "--file", f, "--array", "xs", "--json", "2")
    assert r.returncode == 0, r.stderr
    data = json.loads((vault / f).read_text())
    assert "_plugin_version" not in data
    assert data["xs"] == [1, 2]
