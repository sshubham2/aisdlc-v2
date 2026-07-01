"""Concurrent-write/append-safe vault writer (slice-093 / [[ADR-085]]).

The C2 concurrent-write-safety primitive (R-32): a shared MUTABLE vault
store turns loud git merge-conflicts into SILENT Windows lost-update /
atomic-rename-EPERM corruption (the ``.claude.json``/OneDrive class). This
module is the structural replacement for PCR-on-vault-files once the
slice-094 flip removes git-conflict resolution.

- ``safe_write_text``  — whole-file: sidecar-``.lock`` + temp-write +
  atomic ``os.replace`` + bounded retry on ``PermissionError``/EPERM.
- ``safe_append_text`` — append-only (ADRs, ``risk-register.md``,
  ``_index.md``, the PCR audit log): ``O_APPEND``/``FILE_APPEND_DATA``
  under the sidecar lock — non-clobbering, closes the read-modify-write
  lost-update window that whole-file atomic-replace does NOT.
- ``write_vault_root_config`` / ``read_vault_root_config`` — the
  per-project config API (production-called at the slice-094 flip).

The lock is acquired on a per-file SIDECAR ``<path>.lock`` — NEVER the
replace target (Windows ``msvcrt.locking``→``LockFileEx`` is *mandatory*,
so locking the target would block its own ``os.replace``).

Leading-underscore helper → auto-excluded from PMI-1 inventory. Imports
``scripts.lib._vault_paths._CONFIG_REL`` (the shared config-location SSoT,
m2); that does NOT break ``_vault_paths``'s own leaf-purity.
"""
from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from scripts.lib._vault_paths import _CONFIG_REL

_LOCK_SUFFIX = ".lock"
_EPERM_RETRIES = 6
_EPERM_BACKOFF_BASE = 0.05  # seconds; exponential 0.05, 0.10, 0.20, ...
_LOCK_TIMEOUT = 15.0  # seconds
_LOCK_POLL = 0.02  # seconds between non-blocking lock attempts


class StaleVaultBaseError(Exception):
    """Raised by ``safe_rewrite_text`` when the target changed since the caller
    read it (compare-and-swap base mismatch — R-32 skill-driven RMW; [[ADR-088]]).

    The RETRYABLE signal: the caller (skill prose via ``vault_edit rewrite``)
    should re-read the current file, re-apply its edit, and retry. NOT an
    ``OSError`` subclass on purpose — a generic ``except OSError`` write-failure
    handler must NOT swallow it (it is a concurrency signal, not a write failure);
    ``tools/vault_edit.py`` maps it to a DISTINCT exit code (3), separate from the
    usage-error exit (2)."""


class DuplicateAppendSuppressed(Exception):
    """Raised by a ``vault_edit append`` mutate when a BOUNDED, ``--stdin``-scoped
    duplicate is detected (SC-041 / slice-050; ADR-040 + ADR-043): the identical
    element was appended within the recent last-K window, so the append is
    suppressed as idempotent SUCCESS and the target is left UNTOUCHED.

    A DIRECT ``Exception`` subclass ON PURPOSE — NOT ``ValueError``/``OSError`` — so
    ``vault_edit._run_mutate``'s generic error handlers (which map ValueError /
    TimeoutError / OSError to exit 2) do NOT swallow it into a non-zero exit. A
    non-zero exit would re-trigger the harness retry this guard exists to absorb, so
    ``_cmd_append`` catches this in its OWN dedicated handler and maps it to exit 0 +
    a machine-readable stdout signal. Carries the target ``array`` name and the
    post-op ``count`` (unchanged — the append did not land) for that signal."""

    def __init__(self, *, array: str, count: int):
        super().__init__(f"duplicate append suppressed on array {array!r}")
        self.array = array
        self.count = count


def _normalize_eol(data: bytes) -> bytes:
    """CRLF→LF ONLY. Trailing newlines and every other byte are preserved verbatim
    (critique-review M-add-1 / [[ADR-088]]): a content change that differs only in
    a trailing newline is a GENUINE difference (never normalized away → never a
    silent CAS match/overwrite); a pure CRLF↔LF representation flip is equal (→ no
    false-conflict on the CRLF ``_index.md``/``risk-register.md``)."""
    return data.replace(b"\r\n", b"\n")


def _detect_eol(data: bytes) -> bytes:
    """The target's dominant line ending: CRLF if ANY ``\\r\\n`` is present, else
    LF. Used so ``safe_rewrite_text`` PRESERVES a CRLF target's EOL (no 309KB
    CRLF→LF churn — critique B1). A mixed-EOL file normalizes to the CRLF-dominant
    form on rewrite — a minor, acceptable one-time cleanup; the EOL-normalized
    compare is unaffected (documented residual)."""
    return b"\r\n" if b"\r\n" in data else b"\n"


def _atomic_replace_with_retry(tmp: Path, path: Path) -> None:
    """``os.replace(tmp, path)`` with bounded exponential-backoff retry on
    ``PermissionError`` (Windows EPERM when a handle is held by OneDrive / AV /
    indexer). On budget exhaustion: unlink the temp + raise a typed
    ``PermissionError`` naming the held-handle cause (never silently corrupts).
    Shared by ``safe_write_text`` + ``safe_rewrite_text`` (the replace step is
    identical; only the temp-content differs)."""
    last_exc: BaseException | None = None
    for attempt in range(_EPERM_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:  # WinError 5 — a handle is held
            last_exc = exc
            time.sleep(_EPERM_BACKOFF_BASE * (2**attempt))
    with contextlib.suppress(OSError):
        tmp.unlink()
    raise PermissionError(
        f"could not atomically replace {path} after {_EPERM_RETRIES} attempts — "
        f"a handle is held by another process (OneDrive / antivirus / Search "
        f"indexer?). Last error: {last_exc}"
    )


@contextlib.contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """Hold an exclusive lock on the SIDECAR ``<target>.lock`` (NEVER the target
    itself) for the duration of the block. Cross-platform: ``msvcrt.locking``
    (Windows; mandatory byte-range) / ``fcntl.flock`` (POSIX; advisory). Blocks
    up to ``_LOCK_TIMEOUT`` polling non-blocking attempts, then ``TimeoutError``.

    Lifecycle (3.19.4): the ``.lock`` sidecar is intentionally IMMORTAL — created on
    first use, NEVER deleted at unlock. Unlinking it would race a concurrent locker
    that already holds the old inode (the classic unlink-race), so it is left in place
    as a zero-content coordination file. Stale ``.lock`` sidecars are harmless; an
    OFFLINE maintenance sweep (when no writer is active — e.g. an ``/archive`` cleanup)
    is the only safe time to remove them.
    """
    lockpath = target.with_name(target.name + _LOCK_SUFFIX)
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lockpath, "a+")  # noqa: SIM115 — released in finally
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timeout acquiring lock {lockpath}")
                    time.sleep(_LOCK_POLL)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timeout acquiring lock {lockpath}")
                    time.sleep(_LOCK_POLL)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def safe_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Whole-file write: lock the sidecar → write a temp file → atomic
    ``os.replace`` onto the target, with bounded exponential-backoff retry on
    ``PermissionError`` (Windows EPERM when a handle is held by OneDrive / AV /
    indexer).

    CONCURRENCY guarantee, not a crash-durability one (3.19.4): the atomic
    ``os.replace`` means no concurrent reader ever observes a half-written target —
    a reader sees either the old file or the new one, never a truncation. It does
    NOT fsync the temp, so a crash / power-loss in the write→replace window can lose
    the just-written content (the prior target survives intact); it never corrupts
    the target into a partial state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        # newline="" — LF-faithful, matching the canonical vault writers
        # (slice_queue_writer.py:819 / slice_queue_claim.py:535). Without it,
        # Path.write_text translates \n -> os.linesep (CRLF on Windows) —
        # EOL-DRIFT-1 / ADR-033, which would corrupt every routed vault file. (slice-094 B1)
        tmp.write_text(text, encoding=encoding, newline="")
        _atomic_replace_with_retry(tmp, path)  # shared replace+EPERM-retry (slice-097)


def safe_append_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Append-only write: lock the sidecar → ``O_APPEND``/``FILE_APPEND_DATA``
    open → write. Non-clobbering; closes the read-modify-write lost-update
    window that a whole-file atomic-replace does NOT (the append-log class:
    ADRs, ``risk-register.md``, ``_index.md``, the PCR audit log)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    with _file_lock(path):
        # m2 (/code-review): EPERM-retry on os.open — symmetric with
        # safe_write_text's os.replace retry. On Windows a held handle
        # (AV/OneDrive mid-scan, no FILE_SHARE_WRITE) can EPERM the open too.
        last_exc: BaseException | None = None
        fd = -1
        for attempt in range(_EPERM_RETRIES):
            try:
                # os.O_BINARY — Windows os.open defaults to TEXT mode, which
                # translates \n -> \r\n on os.write (EOL-DRIFT-1 / ADR-033).
                # getattr(..., 0) is a POSIX no-op (O_BINARY is Windows-only). (slice-094 B1)
                fd = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0),
                    0o644,
                )
                break
            except PermissionError as exc:
                last_exc = exc
                time.sleep(_EPERM_BACKOFF_BASE * (2**attempt))
        else:
            raise PermissionError(
                f"safe_append_text: could not open {path} for append after "
                f"{_EPERM_RETRIES} attempts — a handle is held by another process "
                f"(OneDrive / antivirus / Search indexer?). Last error: {last_exc}"
            )
        try:
            # os.write may SHORT-write (large appends, interrupted syscalls) — loop to
            # completion or raise, so the tail of an append is never silently dropped (3.19.4).
            written = 0
            while written < len(data):
                n = os.write(fd, data[written:])
                if n <= 0:
                    raise OSError(
                        f"safe_append_text: short write to {path} "
                        f"({written}/{len(data)} bytes) — append truncated")
                written += n
        finally:
            os.close(fd)


def safe_rewrite_text(
    path: Path | str, text: str, *, expected_base: bytes, encoding: str = "utf-8"
) -> None:
    """Compare-and-swap whole-file rewrite — the R-32 skill-driven read-modify-write
    safe channel ([[ADR-088]] / slice-097). Under the sidecar lock: read the current
    target bytes; if they no longer match ``expected_base`` (the bytes the caller
    read before composing ``text``), raise ``StaleVaultBaseError`` so the caller
    re-reads + re-applies + retries; else write ``text`` and atomically replace.

    Two contracts close the slice-097 /critique findings:

    - **EOL-NORMALIZED compare, not byte-exact (critique B1).** The shared-aggregate
      RMW targets (``_index.md`` 309KB, ``risk-register.md`` 137KB) are CRLF on
      Windows checkouts (``.gitattributes`` does not normalize ``architecture/**``),
      while the caller's base is typically LF. A byte-exact compare would conflict on
      EVERY attempt → livelock → the channel unusable on the very files R-32 exists
      for. The compare normalizes CRLF→LF on both sides (``_normalize_eol``), so
      representation is immaterial; genuine content changes still differ.
    - **EOL-PRESERVING write (critique B1).** ``text`` is re-applied to the target's
      DETECTED EOL (``_detect_eol``) — a CRLF target stays CRLF — so a rewrite never
      churns 309KB CRLF→LF (EOL corruption on an unguarded surface).

    Lost-update safety: the LLM's read+edit happens OUTSIDE the lock, but the
    under-lock re-read + CAS compare converts a stale-base overwrite from a SILENT
    lost-update into a DETECTED ``StaleVaultBaseError``. Concurrent writers converge:
    A commits; B's compare fails → B re-reads (sees A's change) → re-applies →
    commits. A trailing-newline truncation by a concurrent writer is a genuine
    mismatch (``_normalize_eol`` preserves trailing bytes — M-add-1), never a silent
    match."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        current = path.read_bytes() if path.exists() else b""
        if _normalize_eol(current) != _normalize_eol(expected_base):
            raise StaleVaultBaseError(
                f"safe_rewrite_text: {path} changed since it was read (a parallel "
                f"slice/session wrote it) — re-read and re-apply (CAS base mismatch)."
            )
        eol = _detect_eol(current) if current else _detect_eol(expected_base)
        data = text.encode(encoding)
        if eol == b"\r\n":
            # text is authored LF; re-apply the target's CRLF. normalize-then-expand
            # is idempotent for existing CRLF and preserves a lone bare \r (a lone
            # \r-before-\n stays \r\r\n, never doubled to \r\r\r\n) — the slice-097
            # /code-review m2 lone-CR edge, verified across the EOL payload battery.
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.rw.tmp")
        tmp.write_bytes(data)  # bytes — EOL already applied; no text-mode translation
        _atomic_replace_with_retry(tmp, path)


def safe_mutate_text(
    path: Path | str,
    mutate: Callable[[str], str],
    *,
    encoding: str = "utf-8",
) -> None:
    """Locked read-modify-write — the SVW-1 safe channel for STRUCTURED mutation
    of a shared-aggregate vault file (the v2 JSON-native ``vault_edit append`` /
    ``update``). Under the sidecar lock: read the current text (``""`` when
    absent) → call ``mutate(current_text) -> new_text`` → atomically replace.

    Why this is lost-update-safe WITHOUT a CAS base: the lock SERIALIZES
    concurrent mutators, and each mutator re-reads the latest on-disk content
    INSIDE the lock before applying its change. So two parallel slices appending
    to ``risk-register.json`` converge (A commits; B's mutate runs on A's result).
    This is the JSON analogue of ``safe_append_text``'s ``O_APPEND`` — which is
    lock-free but can only concatenate bytes, never insert into a JSON array.

    EOL is preserved exactly as ``safe_rewrite_text`` (a CRLF target stays CRLF —
    no churn). ``mutate`` raising (malformed JSON, missing id, a non-array target
    field, …) propagates with the lock released and the target UNTOUCHED (no temp
    written, no replace) — the ``vault_edit`` CLI maps it to a fail-visible exit.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        current = path.read_bytes() if path.exists() else b""
        # mutate sees decoded text; for JSON callers CRLF is insignificant
        # whitespace (json.loads ignores it) and the re-emitted text is LF, then
        # the target's detected EOL is re-applied below.
        new_text = mutate(current.decode(encoding) if current else "")
        eol = _detect_eol(current) if current else b"\n"
        data = new_text.encode(encoding)
        if eol == b"\r\n":
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.mut.tmp")
        tmp.write_bytes(data)  # bytes — EOL already applied; no text-mode translation
        _atomic_replace_with_retry(tmp, path)


def write_vault_root_config(common_dir: Path | str, vault_path: Path | str) -> Path:
    """Write the per-project vault-root config at
    ``<common_dir>/aisdlc/vault-root`` (a single line: the absolute vault path)
    via ``safe_write_text``. Returns the config path. (Production-called at the
    slice-094 flip; test-exercised in slice-093.)"""
    cfg = Path(common_dir) / _CONFIG_REL
    safe_write_text(cfg, str(vault_path).strip() + "\n")
    return cfg


def read_vault_root_config(common_dir: Path | str) -> str | None:
    """Read the per-project vault-root config — the writer-side mirror of the
    inline reader in ``_vault_paths._read_common_dir_config`` (m2 parity, pinned
    by ``test_inline_and_helper_config_readers_agree``). Returns the stripped
    path, or ``None`` if absent/empty."""
    cfg = Path(common_dir) / _CONFIG_REL
    try:
        if not cfg.exists():
            return None
        text = cfg.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None
