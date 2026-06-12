"""scripts/lib/vault_admin.py — vault lifecycle helpers (4.7 pin / sibling / list / uninstall)."""
from __future__ import annotations

import argparse

from scripts.lib import vault_admin as va


def test_slug_of():
    assert va._slug_of("aisdlc-v2-a5c48e41") == "aisdlc-v2"
    assert va._slug_of("myproj-deadbeef") == "myproj"
    assert va._slug_of("noseparator") == "noseparator"


def test_siblings_same_slug_different_hash(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    me = base / "proj-aaaa1111"
    me.mkdir()
    (base / "proj-bbbb2222").mkdir()   # same slug, different hash -> sibling
    (base / "other-cccc3333").mkdir()  # different slug -> not a sibling
    sibs = va._siblings(me)
    names = {p.name for p in sibs}
    assert names == {"proj-bbbb2222"}


def test_vault_rows_status(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    live_src = tmp_path / "live-repo"
    live_src.mkdir()
    # live: back-ref to an existing repo
    (base / "a-1111").mkdir()
    (base / "a-1111" / va._SOURCE_MARKER).write_text(str(live_src) + "\n", encoding="utf-8")
    # orphan: back-ref to a missing repo
    (base / "b-2222").mkdir()
    (base / "b-2222" / va._SOURCE_MARKER).write_text(str(tmp_path / "gone") + "\n", encoding="utf-8")
    # unknown: no back-ref (pre-4.7)
    (base / "c-3333").mkdir()

    rows = {name: status for status, name, _ in va.vault_rows(base)}
    assert rows == {"a-1111": "live", "b-2222": "ORPHAN", "c-3333": "?"}


def test_uninstall_refuses_without_yes(tmp_path, monkeypatch):
    base = tmp_path / "base"
    (base / "v-1234").mkdir(parents=True)
    monkeypatch.setattr(va, "resolve_base", lambda: base)
    rc = va.cmd_uninstall(argparse.Namespace(name="v-1234", yes=False))
    assert rc == 2
    assert (base / "v-1234").is_dir()  # not deleted


def test_uninstall_deletes_with_yes(tmp_path, monkeypatch):
    base = tmp_path / "base"
    (base / "v-1234").mkdir(parents=True)
    monkeypatch.setattr(va, "resolve_base", lambda: base)
    rc = va.cmd_uninstall(argparse.Namespace(name="v-1234", yes=True))
    assert rc == 0
    assert not (base / "v-1234").exists()


def test_uninstall_unknown_exit2(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(va, "resolve_base", lambda: base)
    rc = va.cmd_uninstall(argparse.Namespace(name="nope-0000", yes=True))
    assert rc == 2
