"""scripts/lib/_vault_write.py — R-32 concurrency-safe write primitives (4.4 priority a).

Covers the lock + atomic-replace + CAS + append + EOL-preservation contracts and a
lost-update stress under concurrent threads (each `_file_lock` opens its own handle,
so the lock serialises across threads on both msvcrt and fcntl).
"""
from __future__ import annotations

import json
import threading

import pytest

from scripts.lib import _vault_write as vw
from scripts.lib._vault_write import (
    StaleVaultBaseError,
    read_vault_root_config,
    safe_append_text,
    safe_mutate_text,
    safe_rewrite_text,
    safe_write_text,
    write_vault_root_config,
)


# ── EOL helpers ──────────────────────────────────────────────────────────────────

def test_normalize_eol_crlf_to_lf():
    assert vw._normalize_eol(b"a\r\nb\r\n") == b"a\nb\n"


def test_normalize_eol_preserves_trailing_newline():
    # a content change that differs ONLY by a trailing newline is a GENUINE diff
    assert vw._normalize_eol(b"a") != vw._normalize_eol(b"a\n")


def test_detect_eol():
    assert vw._detect_eol(b"a\r\nb") == b"\r\n"
    assert vw._detect_eol(b"a\nb") == b"\n"


# ── safe_write_text ──────────────────────────────────────────────────────────────

def test_safe_write_roundtrip(tmp_path):
    p = tmp_path / "f.txt"
    safe_write_text(p, "hello\nworld\n")
    assert p.read_text(encoding="utf-8") == "hello\nworld\n"


def test_safe_write_lf_faithful_no_crlf_translation(tmp_path):
    # newline="" — must not translate \n -> os.linesep (EOL-DRIFT-1 / ADR-033)
    p = tmp_path / "f.txt"
    safe_write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"


def test_safe_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "sub" / "deep" / "f.txt"
    safe_write_text(p, "x")
    assert p.read_text() == "x"


# ── safe_append_text ─────────────────────────────────────────────────────────────

def test_safe_append_concatenates(tmp_path):
    p = tmp_path / "log.txt"
    safe_append_text(p, "one\n")
    safe_append_text(p, "two\n")
    assert p.read_bytes() == b"one\ntwo\n"


def test_safe_append_creates_when_absent(tmp_path):
    p = tmp_path / "new.txt"
    safe_append_text(p, "first\n")
    assert p.read_text() == "first\n"


def test_safe_append_no_crlf_translation(tmp_path):
    p = tmp_path / "log.txt"
    safe_append_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"


# ── safe_rewrite_text (compare-and-swap) ─────────────────────────────────────────

def test_rewrite_cas_match_writes(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_bytes(b"base\n")
    safe_rewrite_text(p, "new\n", expected_base=b"base\n")
    assert p.read_bytes() == b"new\n"


def test_rewrite_cas_mismatch_raises_and_preserves(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_bytes(b"current\n")
    with pytest.raises(StaleVaultBaseError):
        safe_rewrite_text(p, "new\n", expected_base=b"STALE\n")
    assert p.read_bytes() == b"current\n"  # untouched on conflict


def test_rewrite_cas_eol_normalized_compare_and_preserving_write(tmp_path):
    # CRLF on disk, LF base -> representation flip is EQUAL (no false conflict);
    # the write re-applies the target's CRLF (no 309KB CRLF->LF churn).
    p = tmp_path / "doc.txt"
    p.write_bytes(b"a\r\nb\r\n")
    safe_rewrite_text(p, "x\ny\n", expected_base=b"a\nb\n")
    assert p.read_bytes() == b"x\r\ny\r\n"


def test_rewrite_trailing_newline_is_genuine_conflict(tmp_path):
    # M-add-1: a base that differs only by a missing trailing newline is a real
    # mismatch, never silently matched away.
    p = tmp_path / "doc.txt"
    p.write_bytes(b"a\n")
    with pytest.raises(StaleVaultBaseError):
        safe_rewrite_text(p, "z\n", expected_base=b"a")


# ── safe_mutate_text ─────────────────────────────────────────────────────────────

def test_mutate_sees_current_and_writes(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text("1")
    safe_mutate_text(p, lambda cur: cur + "2")
    safe_mutate_text(p, lambda cur: cur + "3")
    assert p.read_text() == "123"


def test_mutate_absent_file_sees_empty_string(tmp_path):
    p = tmp_path / "m.txt"
    safe_mutate_text(p, lambda cur: f"[{cur}]")
    assert p.read_text() == "[]"


def test_mutate_raising_leaves_target_untouched(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text("orig")

    def boom(_cur):
        raise ValueError("malformed")

    with pytest.raises(ValueError):
        safe_mutate_text(p, boom)
    assert p.read_text() == "orig"


# ── vault-root config (the tier-2 pin API) ───────────────────────────────────────

def test_vault_root_config_roundtrip(tmp_path):
    common = tmp_path / "common"
    common.mkdir()
    vault = tmp_path / "the-vault"
    cfg = write_vault_root_config(common, vault)
    assert cfg.exists()
    assert read_vault_root_config(common) == str(vault)


def test_read_vault_root_config_absent_returns_none(tmp_path):
    assert read_vault_root_config(tmp_path) is None


# ── concurrency: no lost update ──────────────────────────────────────────────────

def test_concurrent_appends_no_lost_update(tmp_path):
    p = tmp_path / "concurrent.log"
    n = 12
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        safe_append_text(p, f"line-{i}\n")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = p.read_text().splitlines()
    assert sorted(lines) == sorted(f"line-{i}" for i in range(n))


def test_concurrent_mutate_converges(tmp_path):
    # serialized read-modify-write: N mutators each append one item -> all present
    p = tmp_path / "arr.json"
    p.write_text(json.dumps({"items": []}))
    n = 10
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()

        def mut(cur):
            d = json.loads(cur) if cur.strip() else {"items": []}
            d["items"].append(i)
            return json.dumps(d)

        safe_mutate_text(p, mut)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    d = json.loads(p.read_text())
    assert sorted(d["items"]) == list(range(n))
