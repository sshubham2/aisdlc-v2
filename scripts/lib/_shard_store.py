"""_shard_store.py — the append-only per-entry shard-log storage seam (slice-088 / SC-193).

The first cut of the vault-sharding program (ADR-105, corrected by ADR-106). A shared-mutable
append aggregate (gate-log first) becomes an append-only, per-entry immutable shard LOG that is
the source of truth, with the legacy flat path demoted to a DERIVED, local, git-ignored CACHE.
Sharding turns each append into a NEW-file write (atomic same-dir temp publish, no whole-file RMW), which is what makes
the vault shareable over git/S3 without the ``_vault_write`` whole-file-rewrite machinery.

Design record: event-sourcing / CQRS — an immutable event log (one positional entry per file,
ordered by a global monotonic ``seq`` minted at append) + a disposable materialized-view cache.

Load-bearing corrections pinned by ADR-106 (each earned by /critique + the DR-1 meta-Critic):
  * **B1** — a seq collision raises ``FileExistsError``, NEVER a silent overwrite. slice-090 /
    ADR-109 REFINES B1's MECHANISM (the invariant is unchanged): the shard is no longer created
    in place with ``os.O_EXCL`` (which exposed a 0-byte ``<seq>.json`` to the ADR-107 lock-free
    reader — SC-196) but PUBLISHED atomically — full bytes to a same-dir ``*.tmp`` → ``fsync`` →
    an under-lock ``if target.exists(): raise`` pre-check → ``os.replace``. So a future implementer
    must NOT re-derive ``O_EXCL`` from ADR-106 (superseded-in-mechanism-only). See ``_write_exclusive``.
  * **B2 / M-add-1** — the WHOLE gate-log resource serializes on ONE lock, the ``gate-log.json``
    sidecar (``_file_lock`` on the flat cache path). ``append_entry`` (shard write + cache regen)
    AND ``migrate`` both hold it, so the append is atomic and migrate mutually excludes both the
    flat and the sharded append paths. There is NO separate ``_seq.lock``.
  * **T1 (slice-088)** — ``_file_lock`` is NON-reentrant (a nested ``safe_write_text`` on the same
    sidecar deadlocks 15s → ``TimeoutError``, verified). So the in-lock cache publish uses the
    LOCK-FREE ``_atomic_replace_with_retry`` (the exact core of ``safe_write_text`` minus the
    re-lock). The outer append/migrate lock already provides mutual exclusion — this preserves
    every ADR-106 invariant (immutable per-entry shard · os.replace whole-file cache · one lock · atomic).
  * **M3** — ``migrate`` stages shards INSIDE the vault (same volume) and publishes via a SINGLE
    no-clobber ``os.rename`` (a pre-existing/partial ``gate-log/`` is a loud stop, never clobbered).
  * **M4** — the cache regen is INCREMENTAL on the hot path (append the new entry to the cached
    array in O(1)); a full O(N) ``derive`` is the recovery path only (torn/missing/stale cache).
  * **m2 / m3** — the sharding route predicate keys on the vault-RELATIVE POSIX path (SC-046), never
    a raw basename; ``.gitignore`` excludes the cache + ``<dir>/*.lock`` + ``<dir>/*.tmp`` cruft.
  * **INV3** — ``derive`` is FAIL-CLOSED: a torn shard, a non-int filename, or a duplicate seq
    RAISES (a ledger never silently skips / reorders / drops).

Leading-underscore module → auto-excluded from the PMI-1 inventory, like its siblings
``_vault_write`` / ``_vault_paths``.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/_shard_store.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib._vault_write import (  # noqa: E402 — after the sys.path bootstrap
    DuplicateAppendSuppressed,
    _atomic_replace_with_retry,
    _file_lock,
)

_O_BIN = getattr(os, "O_BINARY", 0)  # POSIX no-op; Windows: binary mode (no \n->\r\n)
_META_NAME = "_meta.json"
_EXCLUDE_SUFFIXES = (".lock", ".tmp")  # coordination cruft in the shard dir, never a shard (m2)

# The sharding allowlist (ADR-106 m3): keyed on the VAULT-RELATIVE POSIX path of the derived
# cache file + its array. Value = the shard-dir name (a sibling of the cache under the vault root).
# gate-log is the FIRST and — this slice — ONLY member; a later log adds exactly one line here.
# A raw basename is NEVER the key (SC-046: an archived/nested copy would collide with the live file).
_SHARDED: dict[tuple[str, str], str] = {
    ("gate-log.json", "entries"): "gate-log",
}


# ── allowlist / route predicate ─────────────────────────────────────────────────

def sharded_dir_name(rel_key: str, array: str) -> str | None:
    """The shard-dir name for an allowlisted (rel_key, array), or ``None`` if not sharded."""
    return _SHARDED.get((rel_key, array))


def is_sharded(vault_root: Path | str, rel_key: str, array: str) -> bool:
    """True iff (rel_key, array) is an allowlisted sharded aggregate AND its shard dir already
    exists under the vault root. BOTH halves are required: the allowlist alone would divert a
    PRE-migration append (dir absent → the flat path, the AC2 fallback); shard-dir-exists alone
    would let a stray same-named dir hijack a transactional file's append into sharding."""
    name = _SHARDED.get((rel_key, array))
    if name is None:
        return False
    return (Path(vault_root) / name).is_dir()


# ── shard-file primitives ───────────────────────────────────────────────────────

def _shard_name(seq: int) -> str:
    """Offset-only filename ``<seq:06d>.json`` — the ``at`` timestamp lives in the body, NOT the
    filename (ISO-8601 ``:`` is illegal on Windows). Zero-pad width is COSMETIC — ordering keys on
    the parsed int, never the lexical name."""
    return f"{seq:06d}.json"


def _parse_seq(basename: str) -> int:
    """Parse the int seq of a shard filename; raise ``ValueError`` on a non-shard / non-int name
    (fail-visible — INV3: derive must RAISE on a foreign filename, never silently skip it)."""
    if not basename.endswith(".json"):
        raise ValueError(f"not a shard filename: {basename!r}")
    stem = basename[:-5]
    if not stem.isdigit():  # rejects 'abc', '-1', '', '1.2' → fail-visible
        raise ValueError(f"shard filename is not a zero-padded integer: {basename!r}")
    return int(stem)


def _shard_files(shard_dir: Path) -> list[str]:
    """Every shard filename in ``shard_dir`` (excludes ``_meta.json`` + ``*.lock``/``*.tmp`` cruft).
    A foreign ``*.json`` (e.g. ``abc.json``) IS returned so ``_parse_seq``/``derive`` fail-visibly
    on it (INV3), rather than being silently filtered out."""
    out: list[str] = []
    for fn in os.listdir(shard_dir):
        if fn == _META_NAME or fn.endswith(_EXCLUDE_SUFFIXES) or not fn.endswith(".json"):
            continue
        out.append(fn)
    return out


def _next_seq(shard_dir: Path) -> int:
    """The next global monotonic seq = max existing shard seq + 1 (self-healing; no separate
    counter file to drift). Empty dir → 0. Raises on a malformed existing name (fail-visible)."""
    mx = -1
    for fn in _shard_files(shard_dir):
        mx = max(mx, _parse_seq(fn))
    return mx + 1


def _write_exclusive(target: Path, element: Any) -> None:
    """Publish ONE immutable shard ATOMICALLY-to-lock-free-readers (slice-090 / ADR-109, supersedes
    ADR-106's O_EXCL mechanism; the B1 invariant is UNCHANGED).

    The old ``O_EXCL`` idiom created a 0-byte ``<seq>.json`` and THEN wrote its bytes as a separate
    step, so the ADR-107 lock-free reader could observe a partial ``<seq>.json`` and raise false
    corruption (SC-196). Instead, fully construct the shard in a same-dir reader-excluded temp, then
    install it with a single atomic rename (RCU-style safe publication): a lock-free listdir+read
    sees either the OLD state or the fully-committed new shard, never a half-built one.

    Steps: ``json.dumps`` (BEFORE any disk write — a non-serializable element never leaves a husk)
    → write the FULL bytes to ``<name>.<pid>.tmp`` (``O_CREAT|O_TRUNC`` private scratch; reader- and
    git-excluded via ``*.tmp``) → ``fsync`` the temp fd (M2: a crash then leaves a COMPLETE shard or
    none — never a present-but-zero-length ``<seq>.json`` that ``derive`` would raise on and that
    would sync to every replica) → B1 pre-check ``if target.exists(): raise FileExistsError`` (a seq
    collision is a loud stop, NEVER a silent overwrite; TOCTOU-safe — both callers hold
    ``gate-log.json.lock`` and the only concurrent actor is a lock-free reader that never creates a
    shard) → ``os.replace`` via ``_atomic_replace_with_retry`` (same-dir temp ⇒ same filesystem ⇒
    atomic on Windows + POSIX; reuses the Windows held-handle EPERM retry). ``os.replace`` CONSUMES
    the temp via the rename, so there is no post-publish unlink; ANY failure BEFORE the range (write
    / fsync / the B1 raise) unlinks the temp and re-raises — no orphaned ``*.tmp`` (must_not_defer)."""
    data = json.dumps(element, ensure_ascii=False).encode("utf-8")
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")  # same-dir, reader/git-excluded (*.tmp)
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BIN, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())  # M2: durable temp bytes BEFORE the atomic install (no 0-byte commit)
        if target.exists():  # B1 pre-check under the caller's lock — fail-visible, never a clobber
            raise FileExistsError(
                f"shard seq collision: {target} already exists — refusing to overwrite an "
                "existing shard (B1 fail-visible)")
        _atomic_replace_with_retry(tmp, target)  # atomic same-dir publish; CONSUMES the temp
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(str(tmp))  # clean the scratch on any pre-publish failure (no orphaned .tmp)
        raise


# ── projection (derive) — fail-closed ───────────────────────────────────────────

def derive(shard_dir: Path | str) -> dict:
    """Fold the shard log → the full document (the pure, total, deterministic projection).

    Order by PARSED INT ``seq`` (zero-pad width cosmetic). FAIL-CLOSED (INV3): a torn shard (bad
    JSON), a non-int filename, or a duplicate ``seq`` RAISES — a gate-log is a LEDGER, so it never
    silently skips / reorders / drops an entry. ``_meta.json`` supplies every non-``entries``
    top-level key (``rows[]``, ``_plugin_version``, …) verbatim, so the rebuild is parsed-equal to
    the original document."""
    shard_dir = Path(shard_dir)
    meta_path = shard_dir / _META_NAME
    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError(f"{meta_path} is not a JSON object")
    pairs: list[tuple[int, Any]] = []
    for fn in _shard_files(shard_dir):
        seq = _parse_seq(fn)  # non-int → ValueError (fail-visible)
        rec = json.loads((shard_dir / fn).read_text(encoding="utf-8"))  # torn → JSONDecodeError
        pairs.append((seq, rec))
    seqs = [s for s, _ in pairs]
    if len(set(seqs)) != len(seqs):
        raise RuntimeError(f"duplicate seq among shards in {shard_dir} — ledger integrity violated")
    pairs.sort(key=lambda p: p[0])  # ORDER BY PARSED INT (a lexical name sort would mis-order)
    doc = dict(meta)
    doc["entries"] = [rec for _, rec in pairs]
    return doc


def _recent_entries(shard_dir: Path, n: int) -> list:
    """The last ``n`` entries by seq order — a BOUNDED read for the dedup guard, never a full
    O(N) derive."""
    if n <= 0:
        return []
    seqs = sorted(_parse_seq(fn) for fn in _shard_files(shard_dir))
    return [json.loads((shard_dir / _shard_name(s)).read_text(encoding="utf-8")) for s in seqs[-n:]]


# ── read-side derive-on-missing (slice-089 / SC-194 / ADR-107) ────────────────────

def read_entries(vault_root: Path | str, rel_key: str = "gate-log.json",
                 array: str = "entries") -> list:
    """THE single derive-on-missing read path for a (possibly sharded) aggregate's array.

    slice-088 demoted the flat ``gate-log.json`` to a git-ignored, node-local, DERIVED cache with
    the shard log as the source of truth. On a cloned/synced/replica vault the cache is ABSENT (the
    ``.gitignore`` line keeps it un-synced), so a naive ``json.load(cache).get(array, [])`` silently
    reads ZERO rows even though the shard log holds them. This composes the EXISTING
    ``is_sharded()``/``sharded_dir_name()``/``derive()`` — NO second projection path.

    CQRS/read-model recovery, RECOVERY HALF ONLY (ADR-107): read-only + lock-free — the derive
    branch returns the in-memory projection and NEVER writes the cache back / acquires the lock.
    Read-repair (Dynamo/Cassandra anti-entropy: persist the rebuilt snapshot) is DELIBERATELY not
    imported — it would race the single writer's lock-free in-lock cache publish (ADR-106 B2 / the
    non-reentrant sidecar lock, slice-088 T1). The next writer regenerates the durable cache.

    Resolution — source-of-truth-keyed, fail-visible:
      * FAST PATH: serve the local cache ONLY when it parses to a dict whose ``array`` is a LIST
        (M2). A JSON-valid-but-listless cache (``{}`` / ``{"entries": null}``) is NOT served — it
        falls through to derive, so it can never silently return ``[]`` while shards hold rows.
      * RECOVERY: cache absent / torn / listless AND the shard dir is present -> ``derive()[array]``
        (never ``[]``). A torn-or-listless-but-PRESENT cache that heals from shards emits a stderr
        WARNING (M-add-1 / DR-1) so genuine local cache corruption + the O(N)-derive-per-read cost
        are visible-but-non-fatal (must_not_defer[3]); a plain cache-ABSENT derive (a fresh
        clone/replica with no local cache) is the EXPECTED path and does NOT warn.
      * FAIL: torn cache with NO shard dir -> RAISE; a torn shard -> ``derive()`` RAISES, propagated.
      * EMPTY: neither a servable cache NOR a shard dir -> ``[]`` (a legitimate empty log; an
        empty-but-present shard dir also correctly derives to ``[]``).

    m2: ``array`` is guarded to ``'entries'`` — ``derive()`` hardcodes ``doc['entries']`` (see
    ``derive`` above), so the param only serves the sole ``_SHARDED`` member today; the guard
    prevents illusory generality until ``derive()`` is parameterized.
    """
    if array != "entries":
        raise ValueError(
            f"read_entries: array={array!r} is unsupported — derive() hardcodes 'entries', so the "
            "read path serves only the single sharded aggregate (gate-log/entries) today; "
            "parameterize derive() before adding a second aggregate array.")
    vault_root = Path(vault_root)
    cache_path = vault_root / rel_key

    # FAST PATH — serve the local cache only when its `array` is a LIST (M2).
    cache_torn = False
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache_torn = True  # unparseable -> not servable; try to recover from shards
        else:
            if isinstance(cached, dict) and isinstance(cached.get(array), list):
                return cached[array]
            cache_torn = True  # JSON-valid but list-less -> not servable; fall through to derive

    # RECOVERY — the aggregate is sharded (allowlisted AND its shard dir exists) -> derive.
    if is_sharded(vault_root, rel_key, array):
        doc = derive(vault_root / sharded_dir_name(rel_key, array))  # RAISES on a torn/dup shard
        if cache_torn:  # a torn/listless-but-PRESENT cache was healed from the shard log (M-add-1)
            sys.stderr.write(
                f"_shard_store.read_entries: WARNING — the local cache {cache_path} was "
                "unreadable/listless; healed from the shard log (read-only, no write-back). Expected "
                "on a synced/replica vault, but this masks genuine local cache corruption and costs "
                "an O(N) derive per read until the next writer regenerates the cache.\n")
        return doc.get(array, [])

    # FAIL / EMPTY — no shard dir to recover from.
    if cache_torn:  # torn cache AND no shards -> genuine failure, never a silent []
        raise ValueError(
            f"read_entries: {cache_path} is present but unreadable/not-a-list and there is no shard "
            "dir to recover from — refusing to silently return [] (fail-visible).")
    return []  # neither a servable cache nor a shard dir -> legitimate empty log


# ── cache publish (LOCK-FREE — caller already holds the lock) ────────────────────

def _publish_cache_locked(cache_path: Path, doc: Any) -> None:
    """Publish the whole-file derived cache via the LOCK-FREE atomic replace. MUST be called while
    already holding ``gate-log.json.lock`` — ``safe_write_text`` would re-acquire that same
    non-reentrant sidecar and self-deadlock (slice-088 T1). The atomicity (``os.replace`` — no
    partial read) is identical; only the redundant re-lock is dropped (the outer lock excludes)."""
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    tmp = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.cache.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")  # LF-faithful, no text-mode CRLF drift
    _atomic_replace_with_retry(tmp, cache_path)


def _regenerate_cache_locked(shard_dir: Path, cache_path: Path, new_entries: list,
                             prior_count: int) -> None:
    """Regenerate the derived cache (caller holds the lock). M4 INCREMENTAL fast-path: if the
    on-disk cache is consistent with the pre-append state (it parses, and its ``entries`` count ==
    the prior shard count), append the new entries in O(1); otherwise a full O(N) ``derive``
    self-heals a torn / missing / stale cache (the recovery path only)."""
    doc: Any = None
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (isinstance(cached, dict) and isinstance(cached.get("entries"), list)
                    and len(cached["entries"]) == prior_count):
                cached["entries"].extend(new_entries)
                doc = cached
    except (ValueError, OSError):
        doc = None  # torn / unreadable cache → full derive (self-heal)
    if doc is None:
        doc = derive(shard_dir)
    _publish_cache_locked(cache_path, doc)


# ── append (single-lock, atomic) ────────────────────────────────────────────────

def append_entry(
    vault_root: Path | str,
    rel_key: str,
    array: str,
    element: Any,
    *,
    dedup_check: Callable[[list], bool] | None = None,
    dedup_tail: int = 0,
) -> int:
    """Append ``element`` (a dict/scalar → ONE shard; a list → one shard per item, extend
    semantics) to the sharded aggregate identified by (rel_key, array).

    ATOMIC under the SINGLE ``gate-log.json.lock`` (B2 / M-add-1): the shard write + cache regen
    are one unit, and ``migrate`` — holding the same lock — is mutually excluded, so a parallel
    append can neither be lost during migration nor produce a duplicate shard.

    ``dedup_check`` (optional): called UNDER the lock with the last ``dedup_tail`` entries in seq
    order; returning True SUPPRESSES the append — raise ``DuplicateAppendSuppressed`` with the
    UNCHANGED count (the faithful shard-path twin of vault_edit's ``--stdin`` bounded dedup, M2).

    Returns the number of entries appended."""
    vault_root = Path(vault_root)
    name = _SHARDED.get((rel_key, array))
    if name is None:
        raise ValueError(f"{rel_key!r}/{array!r} is not a sharded aggregate")
    shard_dir = vault_root / name
    cache_path = vault_root / rel_key
    shard_dir.mkdir(parents=True, exist_ok=True)
    with _file_lock(cache_path):  # the SINGLE lock: <vault>/gate-log.json.lock
        if dedup_check is not None and dedup_check(_recent_entries(shard_dir, dedup_tail)):
            raise DuplicateAppendSuppressed(array=array, count=len(_shard_files(shard_dir)))
        items = element if isinstance(element, list) else [element]
        prior = len(_shard_files(shard_dir))
        seq = _next_seq(shard_dir)
        for it in items:
            _write_exclusive(shard_dir / _shard_name(seq), it)  # atomic publish — seq collision fails-visible (B1)
            seq += 1
        _regenerate_cache_locked(shard_dir, cache_path, items, prior)
        return len(items)


# ── .gitignore symmetry (m2 / M-add-2) ──────────────────────────────────────────

def _gitignore_entries(rel_key: str, name: str) -> list[str]:
    """The lines the sharding adds to ``<vault>/.gitignore``: the derived cache (local/unsynced —
    it must never become a shared-mutable file) + the shard dir's coordination cruft (never truth)."""
    return [f"/{rel_key}", f"{name}/*.lock", f"{name}/*.tmp"]


def _gitignore_set(vault_root: Path, rel_key: str, name: str, *, present: bool,
                   log: Callable[[str], None]) -> None:
    """Idempotently ADD (present=True, forward migrate) or REMOVE (present=False, --reverse) the
    sharding entries from ``<vault>/.gitignore``. Symmetric over ALL side effects (M-add-2): the
    reverse path is the exact mirror of forward, so a rolled-back vault's flat file is NOT
    git-ignored (else a clone would read the whole log empty — the M1 silent-loss, reintroduced)."""
    gi = Path(vault_root) / ".gitignore"
    want = _gitignore_entries(rel_key, name)
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    changed = False
    if present:
        for w in want:
            if w not in lines:
                lines.append(w)
                changed = True
    else:
        kept = [ln for ln in lines if ln not in want]
        if len(kept) != len(lines):
            lines, changed = kept, True
    if changed:
        text = "\n".join(lines) + ("\n" if lines else "")
        gi.write_text(text, encoding="utf-8", newline="")
        log(f".gitignore: {'added' if present else 'removed'} sharding entries {want}")


def _git_retrack_best_effort(vault_root: Path, rel_key: str, log: Callable[[str], None]) -> None:
    """After --reverse un-ignores the flat file, re-track it if the vault is a git work tree
    (the mirror of forward's ignore). Best-effort: a non-git vault / missing git is a no-op, never
    a failure — the load-bearing symmetry is the .gitignore line removal, which is unconditional."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(vault_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return
        subprocess.run(["git", "-C", str(vault_root), "add", rel_key],
                       capture_output=True, text=True, timeout=15)
        log(f"git: re-tracked {rel_key} (un-ignored on --reverse)")
    except (OSError, subprocess.SubprocessError):
        return


# ── migrate (single-lock, fail-closed, reversible, idempotent) ───────────────────

def migrate(vault_root: Path | str, rel_key: str, array: str, *, reverse: bool = False,
            log: Callable[[str], None] | None = None) -> dict:
    """Convert the flat aggregate ⇄ the shard store, HOLDING the single ``gate-log.json.lock``
    across the WHOLE read → build → verify → publish (B2 / M-add-1), fail-closed + idempotent.

    Forward: explode the flat cache's ``entries[]`` (offset = array index → insertion order) plus
    every non-``entries`` top-level key (``rows[]`` incl. the unique legacy row) into a staging dir
    INSIDE the vault (same volume — M3), VERIFY ``derive(staging) == original`` (parsed-equal)
    BEFORE publishing via a single no-clobber ``os.rename``, then regen the cache + add the
    ``.gitignore`` entries. Re-run is a no-op (self-reconciling). Any error leaves the flat file
    intact (staging is discarded).

    Reverse: rebuild + VERIFY the flat file from the shards, THEN remove the shard dir and the
    ``.gitignore`` entries (symmetric — M-add-2). Fail-closed: the shard dir is kept if the flat
    readback does not match.

    ``log`` records each action (auditable, must-not-defer #4); shard-write failures RAISE
    (fail-visible), never silent. Returns a small status dict."""
    vault_root = Path(vault_root)
    name = _SHARDED.get((rel_key, array))
    if name is None:
        raise ValueError(f"{rel_key!r}/{array!r} is not a sharded aggregate")
    shard_dir = vault_root / name
    cache_path = vault_root / rel_key
    _log = log or (lambda _m: None)

    with _file_lock(cache_path):  # the SAME single lock the append path takes
        if reverse:
            return _migrate_reverse_locked(vault_root, rel_key, name, shard_dir, cache_path, _log)

        # FORWARD ------------------------------------------------------------------
        if shard_dir.is_dir() and (shard_dir / _META_NAME).exists():
            # Already migrated → self-reconcile the cache + .gitignore (idempotent, content-equal).
            _regenerate_cache_locked(shard_dir, cache_path, [], len(_shard_files(shard_dir)))
            _gitignore_set(vault_root, rel_key, name, present=True, log=_log)
            _log(f"migrate: {shard_dir} already published — no-op (reconciled cache + .gitignore)")
            return {"action": "noop", "reason": "already-sharded", "entries": len(_shard_files(shard_dir))}
        if not cache_path.exists():
            raise FileNotFoundError(f"migrate: no flat file at {cache_path} to convert")
        original = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(original, dict):
            raise ValueError(f"migrate: {cache_path} top-level is not a JSON object")
        entries = original.get(array, [])
        if not isinstance(entries, list):
            raise ValueError(f"migrate: {cache_path} field {array!r} is not a JSON array")

        staging = vault_root / f".{name}.migrating.{os.getpid()}"  # INSIDE the vault (same volume, M3)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            meta = {k: v for k, v in original.items() if k != array}  # rows[] + scalars, verbatim
            (staging / _META_NAME).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            for i, entry in enumerate(entries):
                _write_exclusive(staging / _shard_name(i), entry)
            if derive(staging) != original:  # VERIFY before publish (parsed-equal, incl. rows[])
                raise RuntimeError("migrate: round-trip verify FAILED (derive(staging) != original) — "
                                   "aborting; flat file untouched")
            if shard_dir.exists():  # no-clobber (M3): a pre-existing/partial dir is a loud stop
                raise FileExistsError(f"migrate: publish target {shard_dir} already exists — refusing "
                                      "to clobber a pre-existing/partial shard dir (fail-closed)")
            os.rename(str(staging), str(shard_dir))  # single atomic same-volume publish
            _log(f"migrate: built + verified {len(entries)} shards, published → {shard_dir}")
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)  # discard staging; flat file untouched
            raise
        # post-publish (shards are truth + verified): regen cache + add .gitignore, still under lock
        _regenerate_cache_locked(shard_dir, cache_path, [], len(entries))
        _gitignore_set(vault_root, rel_key, name, present=True, log=_log)
        _log(f"migrate: regenerated cache {cache_path} + added .gitignore entries")
        return {"action": "migrated", "entries": len(entries)}


def _migrate_reverse_locked(vault_root: Path, rel_key: str, name: str, shard_dir: Path,
                            cache_path: Path, log: Callable[[str], None]) -> dict:
    """--reverse body (caller holds the lock). Rebuild + verify the flat file, THEN tear down the
    shard dir + un-ignore the cache (symmetric — M-add-2). Fail-closed: shards kept on a mismatch."""
    if not shard_dir.is_dir():
        # Already flat — still ensure the .gitignore is symmetric (mirror of forward).
        _gitignore_set(vault_root, rel_key, name, present=False, log=log)
        _git_retrack_best_effort(vault_root, rel_key, log)
        log(f"migrate --reverse: no shard dir at {shard_dir} — already flat (no-op)")
        return {"action": "noop", "reason": "not-sharded"}
    doc = derive(shard_dir)  # rebuild (fail-closed on any torn/dup shard) BEFORE removing anything
    _publish_cache_locked(cache_path, doc)
    readback = json.loads(cache_path.read_text(encoding="utf-8"))
    if readback != doc:  # successive self-inspection — the write is 'done' only when a read confirms it
        raise RuntimeError("migrate --reverse: flat readback != derived doc — aborting; shard dir kept "
                           "(fail-closed, no data lost)")
    shutil.rmtree(shard_dir)
    _gitignore_set(vault_root, rel_key, name, present=False, log=log)  # mirror: remove the cache ignore
    _git_retrack_best_effort(vault_root, rel_key, log)
    log(f"migrate --reverse: restored flat {cache_path}, removed {shard_dir}, un-ignored the cache")
    return {"action": "reversed", "entries": len(doc.get("entries", []))}
