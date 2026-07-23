"""_sync_config.py — per-project vault-sync backend config (slice-097 / SC-206 / ADR-121 + ADR-123).

The SINGLE source of truth for the durable, NON-SECRET sync-backend profile that lets /setup's
chosen backend reach a LATER `vault_admin sync` with zero manual env export. Structurally this is
`psql service=<name>` / `ssh <host>`: a one-time step records a named connection PROFILE at a
well-known machine-local path; the stateless client (`vault_admin sync`) re-reads it at call time and
resolves the connection, while the credential stays OUT-OF-BAND in a channel the client already
consults (boto3's default provider chain).

Location (ADR-121, in force): `<git-common-dir>/aisdlc/sync-backend.json` — resolved via
``_vault_paths`` by BOTH the writer (`vault_admin set-backend`) and the reader (`vault_admin
cmd_sync`), so write-location == read-location is STRUCTURAL across git worktrees. It is the sibling
of the tier-2 vault-root pin (``_CONFIG_REL = "aisdlc/vault-root"``), UNTRACKED (under ``.git/``, not
the working tree — the ``.mcp.json``-gitignored precedent), and NOT inside the vault.

Wiring (ADR-123): `cmd_sync` folds the file's non-secret fields into ``os.environ`` via
``setdefault`` before calling the SHIPPED ``_vault_s3_sync.resolve_config`` UNCHANGED — this leaf
owns only the file's location + schema + read/write/validate, never the resolution precedence.

Schema (all fields optional except ``backend``)::

    {"backend": "local" | "git" | "s3",
     "s3":  {"bucket": str, "endpoint": str, "region": str, "project": str},
     "git": {"remote": str}}

SECURITY (critique M3): credentials are NEVER persisted — S3 auth is delegated entirely to boto3's
default chain. This module REFUSES, on BOTH write and read, a file carrying a secret-shaped KEY
(``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_SESSION_TOKEN`` + the generic
``secret`` / ``token`` / ``password`` / ``credential`` key-name families) OR a credential embedded in
the endpoint-URL VALUE (userinfo ``user:pass@host`` — a key-NAME matcher would miss it). So a
credential cannot enter the file by key OR by value, and a hand-edited / legacy file is refused
symmetrically.

Robustness (critique m2): read with ``encoding='utf-8-sig'`` (tolerate a PowerShell Out-File BOM,
mirroring the sibling readers ``_vault_paths.py`` :183 / :234); write BOM-free AND atomically
(``safe_write_text`` — the vault-root-pin write discipline), so a ``cmd_sync`` read never sees a
torn write and a BOM'd hand-edit never degrades silently to 'unset'.

Leading-underscore module -> auto-excluded from the PMI-1 inventory (like ``_vault_write`` /
``_vault_paths``). No third-party import (never boto3) — a low leaf, sibling of ``_vault_write``.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.lib._vault_paths import git_common_dir
from scripts.lib._vault_write import safe_write_text

# The config path RELATIVE to the git common-dir — the sibling of ``_CONFIG_REL`` (aisdlc/vault-root).
_SYNC_CONFIG_REL = "aisdlc/sync-backend.json"

VALID_BACKENDS = ("local", "git", "s3")

# The non-secret s3 fields the file may carry, mapped to the env var ``resolve_config`` reads
# (ADR-123: ``cmd_sync`` ``os.environ.setdefault``s these before the unchanged resolve_config).
S3_FIELD_ENV = {
    "bucket": "AISDLC_S3_BUCKET",
    "endpoint": "AISDLC_S3_ENDPOINT",
    "region": "AISDLC_S3_REGION",
    "project": "AISDLC_S3_PROJECT",
}

# The secret-shaped-key matcher (M3, APED-1 — executed against an adversarial battery at build).
# Exact AWS credential env names + generic credential-bearing substrings (case-insensitive).
_SECRET_EXACT = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
_SECRET_SUBSTRINGS = ("secret", "token", "password", "credential")


class SyncConfigError(Exception):
    """A config INTEGRITY failure that must fail VISIBLE, never a silent mis-resolve: a
    secret-shaped key, a userinfo-bearing endpoint value, or (on the strict write path) an invalid
    backend. DISTINCT from the benign absent/empty/malformed case (which resolves to ``None`` = the
    git-default back-compat), so ``cmd_sync`` maps it to a usage exit (2) with a hint rather than
    treating it as 'no config'."""


# ── location ────────────────────────────────────────────────────────────────────────

def config_path(common_dir: str | Path | None = None) -> Path | None:
    """``<common_dir>/aisdlc/sync-backend.json`` — the config location. ``common_dir`` is the
    absolute git-common-dir (pass it so the writer and reader converge on the SAME anchor and tests
    can inject a tmp one); when ``None`` it is resolved via the memoized ``git_common_dir()``. Returns
    ``None`` when not a git work tree (no stable per-repo anchor)."""
    if common_dir is None:
        common_dir = git_common_dir()
    if not common_dir:
        return None
    return Path(common_dir) / _SYNC_CONFIG_REL


# ── secret defense (M3) ───────────────────────────────────────────────────────────────

def _key_is_secret(key: str) -> bool:
    k = str(key)
    if k.upper() in _SECRET_EXACT:
        return True
    kl = k.lower()
    return any(s in kl for s in _SECRET_SUBSTRINGS)


def endpoint_has_userinfo(value: str | None) -> bool:
    """True iff an S3 endpoint value smuggles a credential in its userinfo (``user:pass@host``) — the
    vector a key-NAME matcher misses (M3). Detects it with or without a scheme (``https://k:s@h``,
    ``//k:s@h``, bare ``k:s@h:9000``): the authority component is everything after ``//`` (or the
    whole leading segment when there is none), before the first ``/``; an ``@`` there is userinfo. A
    benign ``https://minio.local:9000`` has no ``@`` in its authority."""
    if not value:
        return False
    v = str(value).strip()
    for sep in ("://", "//"):
        if sep in v:
            authority = v.split(sep, 1)[1].split("/", 1)[0]
            return "@" in authority
    return "@" in v.split("/", 1)[0]


def _iter_keys(obj):
    """Yield every mapping key anywhere in a nested JSON structure (defense-in-depth: a secret keyed
    under a nested object is caught too)."""
    if isinstance(obj, dict):
        for k, val in obj.items():
            yield k
            yield from _iter_keys(val)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_keys(it)


def _assert_no_secrets(data: dict) -> None:
    """Raise ``SyncConfigError`` if ``data`` carries a secret-shaped KEY anywhere, or an
    endpoint VALUE bearing userinfo. Enforced on BOTH the write (``save``) and the read (``load``)."""
    for k in _iter_keys(data):
        if _key_is_secret(k):
            raise SyncConfigError(
                f"refusing a sync-backend config carrying a secret-shaped key {k!r} — credentials "
                "are NEVER persisted here; S3 auth is delegated to boto3's default chain "
                "(AWS_ACCESS_KEY_ID / ~/.aws / an IAM role). Remove the key.")
    endpoint = ((data.get("s3") or {}).get("endpoint") if isinstance(data, dict) else None)
    if endpoint_has_userinfo(endpoint):
        raise SyncConfigError(
            "refusing an S3 endpoint that embeds credentials in its URL userinfo "
            "(user:pass@host) — that persists a secret to the config file. Use a bare endpoint "
            "(https://host:port) and let boto3's default chain supply credentials.")


# ── normalization ─────────────────────────────────────────────────────────────────────

def _clean_str(v) -> str:
    return str(v).strip() if v is not None else ""


def _normalize(raw: dict, *, strict: bool) -> dict | None:
    """Canonicalize a raw config to the known schema (drop unknown keys; keep only non-empty
    fields). ``strict`` (the ``save`` path) RAISES ``SyncConfigError`` on a missing/invalid backend;
    non-strict (the ``load`` path) returns ``None`` (a malformed/legacy file resolves to the git
    default rather than failing a sync)."""
    if not isinstance(raw, dict):
        if strict:
            raise SyncConfigError("sync-backend config must be a JSON object.")
        return None
    backend = _clean_str(raw.get("backend"))
    if backend not in VALID_BACKENDS:
        if strict:
            raise SyncConfigError(
                f"backend must be one of {VALID_BACKENDS!r} (got {backend!r}).")
        return None
    out: dict = {"backend": backend}
    if backend == "s3":
        s3 = raw.get("s3") or {}
        s3_out = {f: _clean_str(s3.get(f)) for f in S3_FIELD_ENV if _clean_str(s3.get(f))}
        if strict and not s3_out.get("bucket"):
            raise SyncConfigError("an s3 backend config must carry a non-empty s3.bucket.")
        if s3_out:
            out["s3"] = s3_out
    elif backend == "git":
        remote = _clean_str((raw.get("git") or {}).get("remote"))
        if remote:
            out["git"] = {"remote": remote}
    return out


# ── read / write ───────────────────────────────────────────────────────────────────────

def load(common_dir: str | Path | None = None, *, warn=lambda _m: None) -> dict | None:
    """Read the persisted sync-backend config, or ``None`` when it is absent / empty / malformed
    (the git-default back-compat — a missing profile is normal). Read with ``utf-8-sig`` so a
    PowerShell BOM does not degrade it to 'unset' (m2). Malformed JSON calls ``warn(msg)`` and
    returns ``None`` (never a silent mis-resolve). A secret-shaped key or a userinfo-bearing endpoint
    RAISES ``SyncConfigError`` (M3: refuse to load, fail-visible — an integrity problem, not 'no
    config')."""
    p = config_path(common_dir)
    if p is None or not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        warn(f"sync-backend config at {p} is unreadable ({exc}); treating as unset.")
        return None
    if not text:
        return None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        warn(f"sync-backend config at {p} is not valid JSON ({exc}); treating as unset "
             "(the backend falls back to git).")
        return None
    _assert_no_secrets(raw)  # M3 — raises on a secret key / userinfo endpoint (fail-visible)
    return _normalize(raw, strict=False)


def save(cfg: dict, common_dir: str | Path | None = None) -> Path:
    """Validate + persist a sync-backend config, BOM-free and atomically (``safe_write_text`` — the
    vault-root-pin write discipline), and return the written path. RAISES ``SyncConfigError`` on an
    invalid backend, a missing s3.bucket, a secret-shaped key, or a userinfo-bearing endpoint (M3),
    WITHOUT writing a file. The caller (``vault_admin set-backend``) read-back-verifies."""
    p = config_path(common_dir)
    if p is None:
        raise SyncConfigError(
            "not in a git work tree — no git-common-dir to anchor the sync-backend config "
            "(run `git init`, or pin AI_SDLC_VAULT_ROOT).")
    # M3: refuse on the RAW input FIRST — normalization would drop an unknown secret-shaped top-level
    # key before it could be caught, laundering the very leak this guard exists to stop.
    _assert_no_secrets(cfg if isinstance(cfg, dict) else {})
    normalized = _normalize(cfg, strict=True)
    _assert_no_secrets(normalized)  # belt-and-braces (endpoint userinfo survives normalization)
    safe_write_text(p, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return p
