"""scripts/lib/vault_admin.py — vault lifecycle helpers (4.7 pin / sibling / list / uninstall)."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pytest

from scripts.lib import vault_admin as va

_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _fake_common(tmp_path):
    """A tmp ``<root>/.git`` common-dir for write-pin tests (no real git needed)."""
    root = tmp_path / "proj"
    root.mkdir()
    common = root / ".git"
    common.mkdir()
    return str(common)


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


# ── slice-058 / SC-107 / ADR-055 — write-pin hardening (AC3) ──────────────────

def test_write_pin_writes_verifies_no_bom_idempotent(tmp_path, monkeypatch):
    # AC3: pin written, UTF-8 without BOM, read-back-verified, idempotent.
    common = _fake_common(tmp_path)
    monkeypatch.setattr(va, "_git_common_dir", lambda: common)
    vault = tmp_path / "vault"
    assert va.cmd_write_pin(argparse.Namespace(vault=str(vault))) == 0
    pin = Path(common) / va._CONFIG_REL
    raw = pin.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")               # no BOM
    assert raw.decode("utf-8").strip() == str(vault).strip()  # read-back content
    assert va.cmd_write_pin(argparse.Namespace(vault=str(vault))) == 0
    assert pin.read_bytes() == raw                            # idempotent — identical bytes


def test_write_pin_genuine_failure_exit3_visible(tmp_path, monkeypatch, capsys):
    # AC3/M1: a genuine write failure is fail-VISIBLE with the DISTINCT exit 3 (not 2).
    monkeypatch.setattr(va, "_git_common_dir", lambda: _fake_common(tmp_path))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(va, "write_vault_root_config", boom)
    assert va.cmd_write_pin(argparse.Namespace(vault=str(tmp_path / "vault"))) == 3
    assert "FAILED" in capsys.readouterr().err


def test_write_pin_readback_mismatch_exit3(tmp_path, monkeypatch, capsys):
    # AC3: the write is only 'done' when an independent read confirms it.
    monkeypatch.setattr(va, "_git_common_dir", lambda: _fake_common(tmp_path))
    monkeypatch.setattr(va, "read_vault_root_config", lambda c: "/some/other/path")
    assert va.cmd_write_pin(argparse.Namespace(vault=str(tmp_path / "vault"))) == 3
    assert "read-back" in capsys.readouterr().err.lower()


def test_write_pin_not_git_tree_exit2(monkeypatch):
    # M1: not-a-git-tree stays the BENIGN exit 2, distinct from the genuine-failure exit 3.
    monkeypatch.setattr(va, "_git_common_dir", lambda: None)
    assert va.cmd_write_pin(argparse.Namespace(vault=None)) == 2


# ── slice-058 — git-init consented actuator (AC1 / M2 / m2) ───────────────────

@_GIT
def test_git_init_creates_repo_and_verifies_root(tmp_path):
    # AC1: accepting the gate runs `git init` in the project root.
    root = tmp_path / "fresh"
    root.mkdir()
    assert va.cmd_git_init(argparse.Namespace(root=str(root))) == 0
    assert (root / ".git").is_dir()


@_GIT
def test_git_init_canonical_root_variant_passes(tmp_path):
    # M2: a separator/case-variant root must PASS the _canonical parent==root re-verify
    # (a raw string compare would false-STOP a legitimate init on Windows).
    root = tmp_path / "proj"
    root.mkdir()
    assert va.cmd_git_init(argparse.Namespace(root=str(root) + "/")) == 0


def test_git_init_not_a_directory_exit3(tmp_path, capsys):
    assert va.cmd_git_init(argparse.Namespace(root=str(tmp_path / "nope"))) == 3
    assert "not a directory" in capsys.readouterr().err.lower()


@_GIT
def test_git_init_root_mismatch_fail_closed(tmp_path, monkeypatch, capsys):
    # M2/fail-closed: if the resolved common-dir's parent != the intended root (a nested/
    # ancestor bind), STOP with exit 3 — never pin the wrong home.
    root = tmp_path / "proj"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(va, "_git_common_dir_at", lambda r: str(elsewhere / ".git"))
    assert va.cmd_git_init(argparse.Namespace(root=str(root))) == 3
    assert "does not match" in capsys.readouterr().err.lower()


# ── slice-089 / SC-194 (AC3 / m1): read-entries CLI derive-on-missing + exit-3 taxonomy ──

def test_read_entries_derives_on_missing_cache(run_script, vault, tmp_path):
    """AC3: the read-entries CLI (the SKILL.md shell entry point) derives on a cache-absent sharded
    vault, byte-identical to the cache-present run. m1: a torn log with no shards -> exit 3 (genuine
    failure), NOT the usage exit 2."""
    import json as _json
    from scripts.lib import _shard_store as S
    (vault / "gate-log.json").write_text(_json.dumps({"entries": [
        {"gate": "critique", "n": 0}, {"gate": "code-review", "n": 1}]}), encoding="utf-8")
    r0 = run_script("scripts/lib/vault_admin.py", ["read-entries", "--vault", vault])
    assert r0.returncode == 0, r0.stderr
    base = _json.loads(r0.stdout)
    assert base == [{"gate": "critique", "n": 0}, {"gate": "code-review", "n": 1}]

    S.migrate(vault, "gate-log.json", "entries")
    (vault / "gate-log.json").unlink()  # synced/cloned vault: cache gone, shards present
    r1 = run_script("scripts/lib/vault_admin.py", ["read-entries", "--vault", vault])
    assert r1.returncode == 0, r1.stderr
    assert _json.loads(r1.stdout) == base, "read-entries must derive the same rows from shards"

    # torn cache + no shards -> genuine failure exit 3 (m1: not the usage exit 2).
    bad = tmp_path / "bad"; bad.mkdir()
    (bad / "gate-log.json").write_text('{"entries": [trunc', encoding="utf-8")
    r2 = run_script("scripts/lib/vault_admin.py", ["read-entries", "--vault", bad])
    assert r2.returncode == 3, f"expected exit 3 (genuine failure), got {r2.returncode}: {r2.stderr}"
