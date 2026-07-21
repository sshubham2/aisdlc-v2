"""_claim_coord.py — shared-vault-safe slice-claim coordination (slice-091 / SC-198 / ADR-113).

Today candidate CLAIMING is mutually-excluded only by a LOCAL file lock on ``candidates.json`` — it has
ZERO cross-machine semantics, so on a shared (git / S3 / MinIO) vault two developers picking concurrently
both mint the same slice (a git non-fast-forward reject at push, or a silent last-write-wins clobber on
object storage). This module is the OPT-IN, shared-remote-only coordination PRIMITIVE + SEAM that makes
the SECOND concurrent claim be REFUSED, never a silent duplicate.

THIN scope (ADR-112, corrected by ADR-113): ship the ``ClaimBackend`` seam + the single-shared-key
create-if-absent logic + a REFERENCE local backend only. The git + S3/MinIO PRODUCTION backends (and their
live double-pick regressions) are **SC-197**, implemented behind the SAME frozen seam.

The model — ONE authoritative object per candidate (``<vault>/claims/<candidate>/HELD.json``) whose ATOMIC
create IS the winner-decision (doorway-free: the winner mints immediately, no read-all). A losing create is
disambiguated by a read-back of the token (own token => WON self-retry / C2; a foreign token => LOST). The
seam is symmetric — ``create_if_absent`` + ``get`` + ``remove_if_owner`` (compare-and-delete) — so a
``--release`` compensation can tear a HELD down without orphaning it (M-add-1).

**Load-bearing correctness (B1 / ADR-113):** the reference ``LocalDirClaimBackend``'s create-if-absent is a
GENUINE atomic no-clobber ``os.link`` publish — full body written to a per-actor temp, then linked onto the
target, whose ``FileExistsError`` IS the loser. It does NOT reuse ``_shard_store._write_exclusive``, whose
O_EXCL was stripped by slice-090/ADR-109 and whose atomicity comes only from the gate-log lock (an unlocked
reuse silently double-picks under concurrency — both Critics executed 28/40 barrier-synced double-picks).
Body-complete-BEFORE-publish means a concurrent loser's read-back never observes a partial HELD (closes
SC-196). The AC1/AC2 proof RACES TWO barrier-synced CONCURRENT PROCESSES — never a sequential proxy.

Leading-underscore module → auto-excluded from the PMI-1 inventory, like ``_vault_write`` / ``_shard_store``.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/_claim_coord.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib._vault_paths import git_common_dir  # noqa: E402 — after the sys.path bootstrap
from scripts.lib._vault_write import _file_lock  # noqa: E402

_O_BIN = getattr(os, "O_BINARY", 0)  # POSIX no-op; Windows: binary mode (no \n->\r\n)

# Result statuses of create_if_absent (the frozen seam contract).
CREATED = "CREATED"          # this actor's atomic create won -> mint immediately
EXISTS = "EXISTS"            # a HELD already existed -> read-back its body (own token => WON, else LOST)
UNVERIFIABLE = "UNVERIFIABLE"  # cannot decide WON/LOST -> the caller REFUSES fail-closed

# The opt-in signal (mirrors the _vault_paths precedence: env override -> git-common-dir config -> None).
# Keyed on a DISTINCT signal, NOT the vault-root pin.
_ENV_BACKEND = "AI_SDLC_CLAIM_BACKEND"
_CONFIG_REL = "aisdlc/claim-backend"
_LOCAL_ALIASES = frozenset({"local", "localdir", "local-dir"})


class UnsupportedBackend(RuntimeError):
    """The claim-backend is CONFIGURED but not available in this build (git / S3 / MinIO = SC-197). The
    caller must FAIL CLOSED (never fall back to a local-only claim that could double-pick — AC4)."""


@dataclass(frozen=True)
class ClaimResult:
    """The outcome of a ``create_if_absent``. ``body`` carries the current HELD (for EXISTS) or the
    created body (for CREATED). ``kind`` splits UNVERIFIABLE for the caller's exit-code taxonomy (m1):
    ``transient`` (unreachable / timeout -> retryable exit 3) vs ``indeterminate`` (a read-back that
    cannot decide -> ambiguous exit 4)."""

    status: str
    body: dict | None = None
    kind: str = ""
    reason: str = ""


def claim_key(candidate: str) -> str:
    """The single authoritative claim key for a candidate. The candidate id is validated (``^SC-\\d+$``)
    by the CONFIGURED-branch caller BEFORE this composes a ``claims/`` path (m3), so no traversal reaches
    here."""
    return f"claims/{candidate}/HELD.json"


def _norm(value: object) -> str:
    """Comparison normal form: stripped + casefolded (emails are case-insensitive in practice; a
    case-different address must never read as a different owner — no false clobber-refusal)."""
    return value.strip().casefold() if isinstance(value, str) else ""


class ClaimBackend(ABC):
    """The FROZEN shared-claim seam (reversibility=expensive). SC-197's git + S3/MinIO backends
    implement this same interface — ``create_if_absent`` (atomic, authoritative), ``get`` (read-back),
    and ``remove_if_owner`` (compare-and-delete teardown) — so no expensive contract change is
    foreseeable there."""

    @abstractmethod
    def create_if_absent(self, key: str, body: dict) -> ClaimResult:
        """Atomically create the single authoritative HELD at ``key`` iff absent. CREATED (this actor
        won), EXISTS(current_body) (a HELD already existed), or UNVERIFIABLE (cannot decide)."""

    @abstractmethod
    def get(self, key: str) -> dict | None:
        """The current HELD body at ``key``, or ``None`` when absent/unreadable."""

    @abstractmethod
    def remove_if_owner(self, key: str, owner_email: str) -> bool:
        """COMPARE-AND-DELETE: remove the HELD ONLY if its recorded owner matches ``owner_email``
        (never a blind read-then-delete). ``True`` = removed; ``False`` = absent or foreign (no-op)."""


class LocalDirClaimBackend(ClaimBackend):
    """The REFERENCE backend: one HELD file per candidate under ``<vault_root>/claims/<candidate>/``.

    create_if_absent is a GENUINE atomic no-clobber ``os.link`` publish (B1 / ADR-113), so exactly one
    of N concurrent creators wins and the loser reads back a COMPLETE HELD (never a partial — SC-196).

    **Local-filesystem precondition (m5).** ``os.link`` create-if-absent is atomic on a LOCAL filesystem
    only; over NFS/SMB it is historically UNRELIABLE (NFSv3+ / recent kernels). The vault base is
    user-configurable (``~/.claude/ai-sdlc-vault-base``) and CAN point at a network share, which would
    silently weaken this guarantee — so cross-machine safety on a shared mount is a SC-197 concern, where
    the remote path uses S3 ``If-None-Match:*`` / a dedicated git ref, never O_EXCL/``os.link`` on a mount.
    """

    def __init__(self, vault_root: Path | str):
        self._root = Path(vault_root)

    def _target(self, key: str) -> Path:
        return self._root / key

    def _read(self, target: Path) -> dict | None:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # absent or torn -> None (the caller retries / fails closed)
        return data if isinstance(data, dict) else None

    def create_if_absent(self, key: str, body: dict) -> ClaimResult:
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        token = str(body.get("idempotency_token") or os.getpid())
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{token}.tmp")  # per-actor scratch (*.tmp: reader/git-excluded)
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BIN, 0o644)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())  # a crash leaves a COMPLETE HELD or none — never a 0-byte husk
            # ATOMIC no-clobber publish: os.link raises FileExistsError iff the target already exists,
            # on BOTH POSIX and Windows (os.rename overwrites on POSIX -> would silently double-pick).
            try:
                os.link(str(tmp), str(target))
            except FileExistsError:
                current = self._read(target)
                if current is None:
                    # the HELD vanished/torn between the failed link and the read-back (a concurrent
                    # remove_if_owner?) — one retry, else UNVERIFIABLE(indeterminate) fail-closed.
                    try:
                        os.link(str(tmp), str(target))
                        return ClaimResult(status=CREATED, body=body)
                    except FileExistsError:
                        current = self._read(target)
                    if current is None:
                        return ClaimResult(
                            status=UNVERIFIABLE, kind="indeterminate",
                            reason="the competing HELD read-back was absent/torn — cannot decide WON/LOST")
                return ClaimResult(status=EXISTS, body=current)
            return ClaimResult(status=CREATED, body=body)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(str(tmp))  # os.link left the target as a 2nd link; drop our scratch name

    def get(self, key: str) -> dict | None:
        return self._read(self._target(key))

    def remove_if_owner(self, key: str, owner_email: str) -> bool:
        target = self._target(key)
        # Serialize the compare-and-delete under the per-HELD sidecar lock. create_if_absent is
        # lock-free (os.link, create-only), which is safe: while this lock is held no lock-free op can
        # REPLACE the HELD (os.link never clobbers) and no other remover runs (they take this lock), so
        # the HELD we read is the HELD we delete — a genuine atomic compare-and-delete, not a TOCTOU.
        with _file_lock(target):
            current = self.get(key)  # m4: get() is the owner-check read-back (a real production caller)
            if current is None:
                return False  # already gone / torn -> idempotent no-op
            held_email = (current.get("actor") or {}).get("git_email")
            if _norm(held_email) != _norm(owner_email):
                return False  # foreign owner -> refuse (a stale --release cannot clobber another's HELD)
            with contextlib.suppress(FileNotFoundError):
                os.remove(str(target))
            return True


def _read_signal() -> str | None:
    """The opt-in claim-backend signal: ``$AI_SDLC_CLAIM_BACKEND`` (override / injection) →
    ``<git-common-dir>/aisdlc/claim-backend`` (the durable per-clone opt-in, read via the MEMOIZED
    git_common_dir() so NO per-claim git subprocess fires — M1) → ``None`` (unconfigured)."""
    env = (os.environ.get(_ENV_BACKEND) or "").strip()
    if env:
        return env
    common = git_common_dir()  # MEMOIZED — shares the one rev-parse VAULT_ROOT resolution already did
    if not common:
        return None
    cfg = Path(common) / _CONFIG_REL
    try:
        if not cfg.exists():
            return None
        text = cfg.read_text(encoding="utf-8-sig").strip()  # utf-8-sig strips a PowerShell-written BOM
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def coordination_backend(vault_root: Path | str) -> ClaimBackend | None:
    """Resolve the OPT-IN coordination backend for ``vault_root``, or ``None`` when unconfigured
    (→ today's local-only claim, byte-identical, zero added latency — AC3/AC4).

    ``local`` / ``localdir`` / ``local-dir`` → the reference ``LocalDirClaimBackend``. A backend named but
    NOT built this slice (``git`` / ``s3`` / ``minio`` = SC-197) raises ``UnsupportedBackend`` so the
    caller FAILS CLOSED — never a silent fall-back to a local-only claim that could double-pick (AC4)."""
    signal = _read_signal()
    if not signal:
        return None
    if signal.strip().lower() in _LOCAL_ALIASES:
        return LocalDirClaimBackend(Path(vault_root))
    raise UnsupportedBackend(
        f"claim-backend {signal!r} is configured but not available in this build — the git / S3 / MinIO "
        f"backends are SC-197. Refusing to fall back to a local-only claim that could double-pick "
        f"(fail-closed). Unset {_ENV_BACKEND} / the aisdlc/claim-backend config, or set it to 'local'.")
