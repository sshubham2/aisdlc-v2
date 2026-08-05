"""_vault_s3_sync.py — S3/MinIO object-store push/pull of the vault sync-set (slice-095 / SC-197 / ADR-119).

The engine behind `vault_admin sync push|pull --backend s3`. A STRUCTURAL SIBLING of
`_vault_git_sync.py` (NOT a StorageBackend ABC — ADR-119): same "sync the log, never the view"
contract, same 0-ok / 2-usage / 3-failure taxonomy (it REUSES that module's `SyncUsageError` /
`SyncFailure` classes so `cmd_sync`'s single except surface covers both engines), and the same
is_sharded-gated derived-cache invalidation (`_invalidate_derived_caches`, imported, not
re-implemented). SYNC model only (a local working copy pushed to / pulled from a bucket), NOT a
live-mounted backend and NOT a multi-writer merge/CRDT engine (that is SC-198).

Sync is an ADDITIVE immutable-log SET-UNION (Helland): the append-only seq-keyed shard log +
`_meta.json` + whole-file artifacts are shipped; the git-ignored derived `gate-log.json` cache and
all `*.lock`/`*.tmp`/`.source-repo` coordination cruft are excluded and rebuilt on the far side by
the EXISTING `_shard_store.read_entries` derive-on-missing. boto3 is an OPTIONAL dependency, imported
lazily only when an S3 backend is actually used; credentials are delegated entirely to boto3's
default chain — this module never reads, logs, or persists them.

Critic-accepted refinements folded in (critique.json + DR-1, all ratified):
  * B1      — a pull-side CONTIGUITY assertion (gapless 0..max shard seqs). A PRE-WRITE check over
    the local∪remote shard union refuses a gapped remote BEFORE any shard is written to the vault
    (so a failed pull never leaves a gapped on-disk set a direct read would derive short); a
    post-download assertion stays as belt-and-braces. Shards transfer in SEQ ORDER so a mere
    interruption leaves only a self-healing contiguous prefix, never a gap.
  * B2      — mutable whole-file artifacts are LAST-WRITER-WINS on push, announced with a LOUD
    warning; the immutable shards stay strict (PUT-if-missing, fork-refuse, never clobber).
  * B3      — the default prefix is MACHINE-INVARIANT (a hash of the git REMOTE URL, collision-safe
    across projects); an unresolvable identity (no remote AND no AISDLC_S3_PROJECT/PREFIX) REFUSES
    exit 2 rather than silently defaulting to a per-machine prefix that no-ops the cross-machine pull.
  * M1      — fork/update detection uses an explicit x-amz-meta CONTENT HASH, never ETag==MD5 (which
    breaks under SSE-KMS/SSE-C); when a remote object lacks the hash AND its ETag is not an MD5
    digest (an SSE signal), it FAILS VISIBLE rather than silently mis-detecting.
  * M2      — PUT-if-missing is a list-then-PUT; its TOCTOU window is the SC-198 multi-writer
    boundary (If-None-Match:* is AWS-only, unsupported by MinIO — no portable atomic if-missing),
    documented here, not closed.
  * M4      — every pull object key is resolved and REFUSED fail-visibly if it escapes the vault root
    ('..'/absolute — the S3-slip/zip-slip class), mirroring `cmd_import`'s `extractall(filter='data')`.
  * M5      — the push-side derived-cache exclude ITERATES `_shard_store._SHARDED` + is_sharded-gates
    each entry (never a hardcoded `gate-log.json`), staying a true twin of `_invalidate_derived_caches`.
  * M-add-1 — pull OVERWRITES a mutable artifact when the remote differs but REFUSES to clobber a
    locally-EDITED artifact without --force (a 3-way merge keyed on a node-local baseline, the git
    dirty-tree guard analogue); GET-if-missing applies ONLY to the immutable shards.
  * m1      — `sync push` announces the vault is transmitted UNREDACTED and names the S3 mitigation:
    a bucket with S3 Block Public Access ENABLED / no public-read ACL.

Leading-underscore module -> auto-excluded from the PMI-1 inventory (like `_vault_git_sync` /
`_shard_store` / `_vault_write`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/_vault_s3_sync.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store, _vault_paths  # noqa: E402 — after the sys.path bootstrap
from scripts.lib._vault_git_sync import (  # noqa: E402 — REUSE the shared taxonomy + invalidation
    SyncFailure,
    SyncUsageError,
    _invalidate_derived_caches,
)
from scripts.lib._vault_write import _atomic_replace_with_retry  # noqa: E402

# ── constants ──────────────────────────────────────────────────────────────────────

BASELINE_NAME = ".s3-sync-state.json"          # node-local last-synced artifact hashes (M-add-1)
CONTENT_HASH_META = "content-sha256"           # x-amz-meta-content-sha256 (M1 fork oracle, not ETag)
_DEFAULT_REGION = "us-east-1"
_CONNECT_TIMEOUT = 10                          # s — botocore fast-fail (the git engine's SSH BatchMode twin)
_READ_TIMEOUT = 60                             # s
_MAX_ATTEMPTS = 3                              # capped retries

# Sync-set excludes: coordination cruft + the node-local baseline. The derived cache is excluded
# SEPARATELY by iterating _shard_store._SHARDED (M5), never named here.
_EXCLUDE_SUFFIXES = (".lock", ".tmp")
_EXCLUDE_NAMES = {".source-repo", BASELINE_NAME}
_EXCLUDE_DIRS = {".git"}


@dataclass
class S3Config:
    bucket: str
    prefix: str
    endpoint_url: str | None
    region: str


# ── config resolution (AC3 / B3) ────────────────────────────────────────────────────

def resolve_config(vault: Path, *, bucket: str | None = None, endpoint_url: str | None = None,
                   prefix: str | None = None) -> S3Config:
    """Resolve the S3 connection config from CLI args over env. A missing bucket or an unresolvable
    machine-invariant prefix is a ``SyncUsageError`` (exit 2, actionable hint) BEFORE any network
    call or boto3 import."""
    bucket = (bucket or os.environ.get("AISDLC_S3_BUCKET") or "").strip()
    if not bucket:
        raise SyncUsageError(
            "no S3 bucket configured — set AISDLC_S3_BUCKET or pass --s3-bucket (the target "
            "object-store bucket for the vault sync-set).")
    endpoint_url = (endpoint_url or os.environ.get("AISDLC_S3_ENDPOINT") or "").strip() or None
    region = (os.environ.get("AISDLC_S3_REGION") or "").strip() or _DEFAULT_REGION
    prefix = (prefix or os.environ.get("AISDLC_S3_PREFIX") or "").strip() or _default_prefix(vault)
    if not prefix:
        raise SyncUsageError(
            "cannot resolve a machine-invariant S3 prefix — this project has no git remote to key on "
            "and neither AISDLC_S3_PROJECT nor AISDLC_S3_PREFIX is set. Set AISDLC_S3_PROJECT=<stable "
            "project id> (B3: a per-machine default prefix would push/pull DISJOINT prefixes across "
            "machines and silently 'reconstruct' an empty vault).")
    return S3Config(bucket=bucket, prefix=prefix.strip("/"), endpoint_url=endpoint_url, region=region)


def _sanitize_prefix(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._/-]+", "-", s).strip("/-") or "vault"


def _default_prefix(vault: Path) -> str | None:
    """A MACHINE-INVARIANT project identity (B3): an explicit AISDLC_S3_PROJECT, else a hash of the
    project's git REMOTE URL (same across machines that cloned it, distinct across projects). None
    when neither is resolvable — the caller then REFUSES rather than defaulting per-machine."""
    proj = (os.environ.get("AISDLC_S3_PROJECT") or "").strip()
    if proj:
        return _sanitize_prefix(proj)
    url = _project_remote_url()
    if not url:
        return None
    slug = _vault_paths._project_slug(_project_repo_name() or "vault")
    return f"{slug}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def _project_repo_name() -> str | None:
    common = _vault_paths.git_common_dir()
    return Path(common).parent.name if common else None


def _project_remote_url() -> str | None:
    """The project repo's git remote URL (origin / the sole remote), or None on ambiguity / no
    remote / not-a-git-tree. Machine-invariant — the same URL on every clone of the project."""
    common = _vault_paths.git_common_dir()
    if not common:
        return None
    repo = str(Path(common).parent)  # <repo>/.git -> <repo>
    try:
        names = subprocess.run(["git", "-C", repo, "remote"], capture_output=True, text=True,
                               timeout=15).stdout.split()
        if not names:
            return None
        name = "origin" if "origin" in names else (names[0] if len(names) == 1 else None)
        if name is None:  # multiple remotes, none 'origin' -> ambiguous, no stable identity
            return None
        url = subprocess.run(["git", "-C", repo, "remote", "get-url", name],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return url or None
    except (OSError, subprocess.SubprocessError):
        return None


# ── boto3 (lazy / optional) ──────────────────────────────────────────────────────────

def _import_boto3():
    """Lazy-import boto3 + botocore.config.Config; a ``SyncUsageError`` (exit-2 class) with an
    install hint if absent — never a bare ImportError traceback (AC3)."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise SyncUsageError(
            "boto3 is not installed — `pip install boto3` (an OPTIONAL dependency, required only for "
            "the S3 sync backend).") from exc
    return boto3, Config


def _boto_exceptions():
    from botocore.exceptions import (BotoCoreError, ClientError, NoCredentialsError,
                                     PartialCredentialsError)
    return BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError


def build_client(cfg: S3Config):
    """Construct a fast-fail boto3 S3 client (bounded connect/read timeouts + capped retries — the
    botocore analogue of the git engine's SSH BatchMode). ``endpoint_url`` unset = AWS S3, set =
    MinIO / S3-compatible."""
    boto3, Config = _import_boto3()
    conf = Config(connect_timeout=_CONNECT_TIMEOUT, read_timeout=_READ_TIMEOUT,
                  retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"})
    return boto3.client("s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region, config=conf)


def _map_boto_exc(exc: BaseException):
    """Map a boto3/botocore exception onto the shared taxonomy: credentials -> SyncUsageError (exit
    2); ClientError / transport -> SyncFailure (exit 3). A non-boto exception re-raises unchanged."""
    try:
        BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError = _boto_exceptions()
    except Exception:  # noqa: BLE001 — boto not importable: nothing to map, surface the original
        raise exc
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        raise SyncUsageError(
            f"AWS credentials are not configured — set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, "
            f"~/.aws, or an IAM role ({exc}).") from exc
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "?")
        raise SyncFailure(
            f"S3 request failed [{code}]: {exc} — check the bucket / endpoint / permissions.") from exc
    if isinstance(exc, BotoCoreError):
        raise SyncFailure(
            f"S3 transport failure: {exc} — endpoint unreachable / network / timeout.") from exc
    raise exc


def _run_guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (SyncUsageError, SyncFailure):
        raise
    except Exception as exc:  # noqa: BLE001 — map boto errors to the taxonomy, else re-raise
        _map_boto_exc(exc)


# ── content hashing / object I/O ─────────────────────────────────────────────────────

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _looks_like_md5(etag: str) -> bool:
    etag = (etag or "").strip('"').lower()
    return len(etag) == 32 and all(c in "0123456789abcdef" for c in etag)


def _key(cfg: S3Config, rel: str) -> str:
    return cfg.prefix.rstrip("/") + "/" + rel


def _put_object(client, cfg: S3Config, rel: str, body: bytes, digest: str) -> None:
    client.put_object(Bucket=cfg.bucket, Key=_key(cfg, rel), Body=body,
                      Metadata={CONTENT_HASH_META: digest})


def _get_bytes(client, cfg: S3Config, key: str) -> bytes:
    return client.get_object(Bucket=cfg.bucket, Key=key)["Body"].read()


def _list_remote(client, cfg: S3Config) -> dict:
    """{relposix: {'key', 'etag'}} for every object under the prefix (ListObjectsV2 paginator)."""
    out: dict = {}
    p = cfg.prefix.rstrip("/") + "/"
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=p):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(p):]
            if rel:  # skip a bare prefix "directory" placeholder
                out[rel] = {"key": obj["Key"], "etag": obj.get("ETag", "").strip('"')}
    return out


def _remote_matches_local(client, cfg: S3Config, key: str, etag: str, local_path: Path,
                          local_sha256: str) -> bool:
    """True iff the remote object's content equals the local file, decided via the x-amz-meta
    content hash (M1), NEVER ETag==MD5. When the remote lacks the hash AND its ETag is not an MD5
    digest (SSE), FAIL VISIBLE rather than silently mis-detect a fork/update."""
    meta = client.head_object(Bucket=cfg.bucket, Key=key).get("Metadata", {})
    remote_sha = meta.get(CONTENT_HASH_META)
    if remote_sha is not None:
        return remote_sha == local_sha256
    if _looks_like_md5(etag):
        local_md5 = hashlib.md5(Path(local_path).read_bytes(), usedforsecurity=False).hexdigest()
        return etag.strip('"').lower() == local_md5
    raise SyncFailure(
        f"remote object {key} lacks the {CONTENT_HASH_META} metadata and its ETag {etag!r} is not an "
        "MD5 digest (server-side encryption?) — cannot verify fork/update safety fail-closed (M1); "
        "refusing rather than silently mis-detecting.")


# ── sync-set enumeration + classification (M5) ───────────────────────────────────────

def _excluded_derived_caches(vault: Path) -> set[str]:
    """The vault-relative derived-cache paths to EXCLUDE from the sync-set — iterate
    `_shard_store._SHARDED` + is_sharded-gate each (M5, the true twin of `_invalidate_derived_caches`;
    NEVER a hardcoded gate-log.json). On a NON-sharded vault the flat file is the SOURCE OF TRUTH and
    is NOT excluded (it must sync)."""
    return {rel_key for (rel_key, array), _name in _shard_store._SHARDED.items()
            if _shard_store.is_sharded(vault, rel_key, array)}


def _shard_dir_names() -> set[str]:
    """The allowlisted shard-dir names (independent of local existence — a remote shard is
    ``<shard-dir>/<seq>.json`` even when the local dir does not exist yet on a fresh pull)."""
    return set(_shard_store._SHARDED.values())


def _is_shard_rel(rel: str, shard_dirs: set[str]) -> bool:
    """True iff ``rel`` is an immutable seq-keyed shard ``<shard-dir>/<seq>.json`` (``_meta.json`` is
    a mutable artifact, not a shard)."""
    parts = rel.split("/")
    if len(parts) != 2:
        return False
    d, fn = parts
    if d not in shard_dirs or fn == _shard_store._META_NAME:
        return False
    try:
        _shard_store._parse_seq(fn)
        return True
    except ValueError:
        return False


def _shard_seq(rel: str) -> int:
    return _shard_store._parse_seq(rel.split("/", 1)[1])


def _iter_local_syncset(vault: Path):
    """Yield ``(relposix, abspath)`` for every file in the sync-set — excluding .git/, *.lock/*.tmp,
    .source-repo, the node-local baseline, and the is_sharded-gated derived caches (M5)."""
    vault = Path(vault)
    excluded = _excluded_derived_caches(vault)
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            ap = Path(root) / fn
            rel = ap.relative_to(vault).as_posix()
            if rel in excluded or fn in _EXCLUDE_NAMES or fn.endswith(_EXCLUDE_SUFFIXES):
                continue
            yield rel, ap


# ── baseline (node-local last-synced artifact hashes — M-add-1) ──────────────────────

def _load_baseline(vault: Path) -> dict:
    p = Path(vault) / BASELINE_NAME
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("artifacts", {}) if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def _save_baseline(vault: Path, artifacts: dict) -> None:
    p = Path(vault) / BASELINE_NAME
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")  # *.tmp -> sync-excluded
    tmp.write_text(json.dumps({"artifacts": artifacts}, ensure_ascii=False, indent=2),
                   encoding="utf-8", newline="")
    _atomic_replace_with_retry(tmp, p)


def _atomic_write_bytes(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.dl.tmp")  # *.tmp -> sync-excluded
    with open(tmp, "wb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    _atomic_replace_with_retry(tmp, target)


def _planned_shard_seqs(vault: Path, remote: dict, shard_dirs: set[str]) -> dict:
    """{shard_dir_name: set of seqs after this pull} = the local existing shards ∪ the remote shards.
    Used for the B1 PRE-WRITE contiguity check so a gapped remote refuses before any on-disk write."""
    by_dir: dict[str, set[int]] = {}
    for dname in shard_dirs:
        d = Path(vault) / dname
        if d.is_dir():
            for fn in _shard_store._shard_files(d):
                by_dir.setdefault(dname, set()).add(_shard_store._parse_seq(fn))
    for rel in remote:
        if _is_shard_rel(rel, shard_dirs):
            dname, fn = rel.split("/", 1)
            by_dir.setdefault(dname, set()).add(_shard_store._parse_seq(fn))
    return by_dir


def _assert_contiguous(shard_dir: Path) -> None:
    """B1: the shard seq set must be a gapless ``0..max`` BEFORE the derived cache is
    invalidated/derived — a gap would make `_shard_store.derive` read a silently-short log. Empty is
    vacuously contiguous. (A foreign shard filename raises via ``_parse_seq`` — INV3, fail-visible.)"""
    seqs = sorted(_shard_store._parse_seq(fn) for fn in _shard_store._shard_files(shard_dir))
    if not seqs:
        return
    expected = list(range(0, seqs[-1] + 1))
    if seqs != expected:
        missing = sorted(set(expected) - set(seqs))
        raise SyncFailure(
            f"refusing pull: the shard set in {shard_dir} is NOT contiguous (gapless 0..{seqs[-1]}) — "
            f"missing seq(s) {missing[:10]}. A gapped log derives a silently-short document; failing "
            "visible BEFORE the derived cache is materialized (B1 / spike-095-design).")


# ── push ─────────────────────────────────────────────────────────────────────────────

def sync_push(vault: Path, *, cfg: S3Config, client, force: bool = False, log) -> dict:
    return _run_guarded(_sync_push_impl, Path(vault), cfg=cfg, client=client, force=force, log=log)


def _sync_push_impl(vault: Path, *, cfg: S3Config, client, force: bool, log) -> dict:
    shard_dirs = _shard_dir_names()
    local = list(_iter_local_syncset(vault))
    local_shards = {rel for rel, _ in local if _is_shard_rel(rel, shard_dirs)}
    remote = _list_remote(client, cfg)
    remote_shards = {rel for rel in remote if _is_shard_rel(rel, shard_dirs)}

    # DATA-LOSS GUARD (shards-strict / pull-first, the git non-ff twin): the remote holds shards this
    # vault lacks -> refuse; a divergent push would fork the append-only log.
    missing = sorted(remote_shards - local_shards, key=_shard_seq)
    if missing:
        raise SyncFailure(
            f"refusing to push: the remote holds {len(missing)} shard(s) this vault lacks "
            f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}) — pull first (the "
            "append-only log's pull-first guard; a divergent push would fork it).")

    log("note: `sync push --backend s3` transmits the ENTIRE vault UNREDACTED to the bucket — use a "
        "bucket with S3 Block Public Access ENABLED and no public-read ACL (a public bucket exposes "
        "the whole vault; a secret pushed once persists).")

    put = skipped = 0
    # SHARDS in SEQ ORDER (B1: an interruption leaves only a contiguous prefix). PUT-if-missing;
    # a same-seq/different-content overlap is a FORK (M1, via the content hash). The list-then-PUT
    # TOCTOU window is the SC-198 multi-writer boundary (M2 — no portable atomic if-missing).
    for rel in sorted(local_shards, key=_shard_seq):
        ap = vault / rel
        digest = _sha256_file(ap)
        if rel in remote:
            if not _remote_matches_local(client, cfg, remote[rel]["key"], remote[rel]["etag"], ap, digest):
                raise SyncFailure(
                    f"FORK: shard {rel} already exists remotely with DIFFERENT content — an "
                    "append-only-log fork (concurrent divergent writers). Resolution is multi-writer "
                    "merge (SC-198), out of scope; refusing to overwrite an immutable shard.")
            skipped += 1
            continue
        _put_object(client, cfg, rel, ap.read_bytes(), digest)
        put += 1

    # MUTABLE whole-file artifacts: LAST-WRITER-WINS overwrite (B2) + a LOUD warning on a real clobber.
    artifacts: dict = {}
    warned = False
    for rel, ap in sorted((r, a) for r, a in local if r not in local_shards):
        digest = _sha256_file(ap)
        artifacts[rel] = digest
        if rel in remote:
            if _remote_matches_local(client, cfg, remote[rel]["key"], remote[rel]["etag"], ap, digest):
                continue  # identical — nothing to upload
            if not warned:
                log("WARNING: whole-file artifacts are LAST-WRITER-WINS over S3 (single-writer sync) "
                    "— a concurrent remote edit is OVERWRITTEN; multi-writer merge is SC-198.")
                warned = True
        _put_object(client, cfg, rel, ap.read_bytes(), digest)
        put += 1

    _save_baseline(vault, artifacts)  # record the just-synced artifact hashes (M-add-1 baseline)
    log(f"pushed to s3://{cfg.bucket}/{cfg.prefix}: {put} object(s) uploaded, {skipped} shard(s) "
        "already present.")
    return {"action": "pushed", "bucket": cfg.bucket, "prefix": cfg.prefix,
            "uploaded": put, "skipped": skipped}


# ── pull ─────────────────────────────────────────────────────────────────────────────

def sync_pull(vault: Path, *, cfg: S3Config, client, force: bool = False, log) -> dict:
    return _run_guarded(_sync_pull_impl, Path(vault), cfg=cfg, client=client, force=force, log=log)


def _sync_pull_impl(vault: Path, *, cfg: S3Config, client, force: bool, log) -> dict:
    remote = _list_remote(client, cfg)
    if not remote:
        raise SyncFailure(
            f"no objects under s3://{cfg.bucket}/{cfg.prefix} — nothing to pull. Check the bucket / "
            "prefix (a wrong prefix silently finds an empty vault).")
    shard_dirs = _shard_dir_names()
    baseline = _load_baseline(vault)

    # M4: resolve + path-contain EVERY target BEFORE writing anything (S3-slip guard; mirror of
    # cmd_import's extractall(filter='data')).
    vault_real = Path(vault).resolve()
    targets: list[tuple[str, Path, bool]] = []
    for rel in sorted(remote):
        target = vault / rel
        try:
            target.resolve().relative_to(vault_real)
        except (ValueError, OSError) as exc:
            raise SyncFailure(
                f"refusing pull: object key {rel!r} resolves OUTSIDE the vault root {vault_real} "
                "(S3-slip / path traversal) — mirror of cmd_import filter='data'.") from exc
        targets.append((rel, target, _is_shard_rel(rel, shard_dirs)))

    # B1 (pre-write): assert the POST-PULL shard set (local existing ∪ remote) is gapless 0..max
    # BEFORE writing ANY shard, so an adversarially-GAPPED remote refuses fail-visibly WITHOUT
    # leaving a partial gapped set on disk (which a direct read would derive short — `derive` has no
    # contiguity check). A normal interruption leaves a self-healing contiguous PREFIX (seq-ordered
    # transfer), which the remote list is NOT gapped about, so this only rejects a genuinely gapped
    # remote — never a resumable prefix. The post-download assertion below stays (belt-and-braces).
    for dname, seqs in _planned_shard_seqs(vault, remote, shard_dirs).items():
        ss = sorted(seqs)
        if ss and ss != list(range(ss[-1] + 1)):
            missing = sorted(set(range(ss[-1] + 1)) - set(ss))
            raise SyncFailure(
                f"refusing pull: the remote shard set for {dname} is NOT contiguous (gapless "
                f"0..{ss[-1]}) — missing seq(s) {missing[:10]}. A gapped log derives a silently-short "
                "document; refusing BEFORE any shard is written to the vault (B1 / spike-095-design).")

    # SHARDS first, in SEQ ORDER (B1). GET-if-missing; a same-key/different-content overlap is a FORK.
    for rel, target, is_shard in sorted((t for t in targets if t[2]), key=lambda t: _shard_seq(t[0])):
        if target.exists():
            if not _remote_matches_local(client, cfg, remote[rel]["key"], remote[rel]["etag"],
                                         target, _sha256_file(target)):
                raise SyncFailure(
                    f"FORK: local shard {rel} differs from the remote — an append-only fork "
                    "(resolution SC-198); refusing to overwrite an immutable shard.")
            continue  # already present + identical
        _atomic_write_bytes(target, _get_bytes(client, cfg, remote[rel]["key"]))

    # B1: CONTIGUITY assertion per shard dir BEFORE invalidating/deriving the cache.
    for dname in shard_dirs:
        d = vault / dname
        if d.is_dir():
            _assert_contiguous(d)

    # MUTABLE artifacts: 3-way merge on the node-local baseline (M-add-1). GET-if-missing; overwrite
    # when the remote changed since the last sync; REFUSE to clobber a local edit without --force.
    new_baseline = dict(baseline)
    for rel, target, is_shard in sorted(t for t in targets if not t[2]):
        body = _get_bytes(client, cfg, remote[rel]["key"])
        rhash = _sha256_bytes(body)
        if not target.exists():
            _atomic_write_bytes(target, body)
            new_baseline[rel] = rhash
            continue
        lhash = _sha256_file(target)
        if lhash == rhash:
            new_baseline[rel] = rhash
            continue
        base = baseline.get(rel)
        if base == rhash:
            continue  # remote unchanged since last sync, local ahead -> keep local (nothing to bring)
        if base == lhash:
            _atomic_write_bytes(target, body)  # local unchanged, remote changed -> overwrite (AC2)
            new_baseline[rel] = rhash
            continue
        if not force:  # both changed (or no baseline) -> would clobber a local edit
            raise SyncFailure(
                f"refusing pull: local artifact {rel} was edited since the last sync AND the remote "
                "also changed — pulling would clobber your un-pushed edit. Re-run with --force to "
                "take the remote (M-add-1 local-edit guard; multi-writer merge is SC-198).")
        _atomic_write_bytes(target, body)
        new_baseline[rel] = rhash

    _invalidate_derived_caches(vault, log=log)  # is_sharded-gated; imported from the git engine
    _save_baseline(vault, new_baseline)
    log(f"pulled s3://{cfg.bucket}/{cfg.prefix}: {len(remote)} object(s) reconciled.")
    return {"action": "pulled", "bucket": cfg.bucket, "prefix": cfg.prefix, "objects": len(remote)}
