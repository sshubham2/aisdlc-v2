"""Bug: the gate-log shard write is NOT atomic to lock-free readers (SC-196 / slice-090).

scripts/lib/_shard_store._write_exclusive creates ``<seq>.json`` with ``os.O_EXCL`` and THEN
writes its bytes as a SEPARATE second step (``os.open`` create, then ``fh.write``). slice-089
(SC-194 / ADR-107) made ``read_entries()`` LOCK-FREE (derive-on-missing), so a concurrent reader
can now observe the shard directory in that mid-write window: it sees a freshly-created 0-byte
``<seq>.json`` and ``derive()`` -- which does ``json.loads(read_text())`` over every ``<seq>.json``
returned by ``_shard_files`` -- raises ``JSONDecodeError``. That is FALSE corruption: the writer is
mid-flight, not corrupt, and a re-read after the write completes is clean.

Expected: a lock-free ``read_entries()``/``derive()`` during an in-progress shard write reads the
          prior COMMITTED state -- a partial shard is never observable.
Actual:   ``read_entries()`` raises ``json.JSONDecodeError`` on the 0-byte ``<seq>.json``.

Deterministic repro (no real thread race): monkeypatch ``os.open`` so that, at the exact moment
``_write_exclusive`` CREATES a file under the shard dir but BEFORE its bytes are written, a
lock-free ``read_entries()`` runs against the same vault. The interposition keys on
(O_CREAT flag + parent == shard dir + not the sidecar lock / _meta.json), so it fires on the 0-byte
``<seq>.json`` create TODAY (RED) and on the 0-byte ``<seq>.json.<pid>.tmp`` create AFTER the fix
(GREEN) -- letting the SAME test flip from raising to clean once the write publishes atomically
(``_shard_files`` already excludes ``*.tmp``). It must NOT require ``O_EXCL``: the fix (ADR-109)
creates its temp with ``O_CREAT|O_TRUNC`` (no ``O_EXCL``), so post-fix there is ZERO ``O_EXCL``
open under the shard dir -- an O_EXCL-keyed interposition would never fire and the test would die a
stale death at its own guard.

Fix (slice-090, ADR-109): write the full bytes to a same-dir ``<seq>.json.<pid>.tmp`` -> fsync ->
an under-lock ``if target.exists(): raise`` B1 pre-check -> atomically publish via ``os.replace``.
A lock-free reader thus sees the OLD committed state or the fully-written new shard, never a
0-byte partial. AC1 = this test reads clean; AC2 = at the mid-write instant no in-flight
``<seq>.json`` is visible while its ``*.tmp`` is.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import _shard_store as S  # noqa: E402

REL, ARRAY = "gate-log.json", "entries"


def test_lockfree_read_during_shard_write_reads_clean(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    shard_dir = vault / "gate-log"

    # 1) Commit a first shard so there is a PRIOR clean state to read, then drop the derived cache
    #    so the lock-free reader takes the derive-on-missing path (the reachable condition: cache
    #    absent on a synced/replica vault -- slice-089's whole premise).
    S.append_entry(vault, REL, ARRAY, {"n": 0})
    (vault / REL).unlink()  # remove the derived cache -> read_entries() must derive from shards

    observed: dict = {}
    real_open = os.open

    def spy_open(path, flags, mode=0o777, **kw):
        fd = real_open(path, flags, mode, **kw)
        p = Path(os.fspath(path))
        # Interpose ONCE, at the shard-write CREATE, BEFORE bytes land. Keyed on
        # (O_CREAT + parent == shard_dir + not the sidecar lock / _meta.json) -- NOT O_EXCL, so it
        # fires on <seq>.json today AND on <seq>.json.<pid>.tmp after the fix (which drops O_EXCL).
        if ("reader" not in observed
                and (flags & os.O_CREAT)
                and p.parent == shard_dir
                and p.name != S._META_NAME
                and not p.name.endswith(".lock")):
            # Snapshot the shard dir at the mid-write instant (AC2): the parseable shard set
            # (_shard_files excludes *.tmp) must NOT yet contain the in-flight <seq>.json, and the
            # fix must have staged a *.tmp. Captured here, asserted after the append completes.
            committed = sorted(S._shard_files(shard_dir))
            tmps = sorted(fn for fn in os.listdir(shard_dir) if fn.endswith(".tmp"))
            observed["interpose"] = (committed, tmps)
            try:
                rows = S.read_entries(vault, REL, ARRAY)  # LOCK-FREE -- must not deadlock the writer
                observed["reader"] = ("clean", rows)
            except BaseException as exc:  # noqa: BLE001 -- capture the exact failure signature
                observed["reader"] = ("raised", type(exc).__name__)
        return fd

    monkeypatch.setattr(os, "open", spy_open)

    # 2) A second append opens a new shard with O_EXCL -> spy_open fires the reader while that
    #    shard is still 0 bytes (the writer holds the lock; the reader is lock-free by design).
    S.append_entry(vault, REL, ARRAY, {"n": 1})

    assert observed.get("reader") is not None, (
        "interposition never fired -- the shard write did not go through an O_CREAT create under the "
        "shard dir (repro harness is stale, not a genuine pass)")
    status, detail = observed["reader"]
    assert status == "clean", (
        f"a lock-free reader observed a PARTIAL shard mid-write and raised {detail!r} (false "
        "corruption); expected a clean derive of the prior committed state. The shard write must "
        "publish atomically to readers (same-dir temp + atomic rename).")
    # AC1: the clean read is the prior COMMITTED state -- never empty, never torn.
    assert [r.get("n") for r in detail] == [0], detail
    # AC2: at the mid-write instant the in-flight <seq>.json (000001.json) is NOT yet a visible
    # shard, and the bytes are staged in a same-dir *.tmp -- the atomic-publication guarantee.
    committed_at, tmps_at = observed["interpose"]
    assert committed_at == ["000000.json"], (
        "AC2: a lock-free reader must not see the in-flight <seq>.json until it is atomically "
        f"published; at the mid-write instant the parseable shard set was {committed_at}")
    assert tmps_at, (
        "AC2: the fix must stage the shard bytes in a same-dir *.tmp before the atomic publish "
        "(none present at the mid-write instant -- the write is not going through a temp)")
