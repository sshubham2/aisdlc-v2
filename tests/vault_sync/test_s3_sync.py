"""slice-095 / SC-197 / ADR-119 — S3/MinIO vault-sync backend (`vault_admin sync push|pull --backend s3`).

test_first tests for the S3 backend + the `_vault_s3_sync` engine, a structural sibling of
`_vault_git_sync`. Every round-trip AC is driven through the SHIPPED `vault_admin sync` CLI entry
(`vault_admin.main([...])`) so the exit taxonomy + backend dispatch are exercised for real. moto's
``@mock_aws`` patches botocore IN-PROCESS only, so the round-trip ACs call ``main()`` in-process
under the mock (a subprocess would escape the mock and hit real AWS); the pure config/usage-exit
cases that need no network stay subprocess-driven via ``run_script``.

moto + boto3 are HARD-imported (they live in requirements-dev.txt per critique M3), so a missing dep
ERRORS loudly here — it never silently importorskip-skips to green in CI. The env-gated real-MinIO
round-trip (``AISDLC_S3_TEST_ENDPOINT``) is the REALITY anchor (moto is a labeled simulation).

Critic-accepted findings, each with a dedicated row:
  * B1      — a pull-side CONTIGUITY assertion (gapless 0..max shard seqs) refuses a gapped set
              BEFORE the derived cache is invalidated/derived (never a silently-short log).
  * B2      — push overwrites mutable whole-file artifacts last-writer-wins with a LOUD warning;
              shards are strict (PUT-if-missing, no clobber).
  * B3      — the default prefix is machine-invariant (git-remote-URL hash); unresolvable -> exit 2.
  * M1      — fork detection uses an x-amz-meta content hash (never ETag==MD5); a non-MD5 ETag
              (SSE) where a hash is required fails VISIBLE.
  * M4      — an S3-slip pull key ('..'/absolute outside the vault root) is refused fail-visibly.
  * M5      — the push-side exclude iterates _shard_store._SHARDED (never a hardcoded gate-log.json).
  * M-add-1 — pull overwrites a mutable artifact when the remote differs but REFUSES to clobber a
              locally-edited artifact without --force; GET-if-missing only for immutable shards.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import boto3  # hard import (requirements-dev.txt; M3 — never importorskip-to-green)
import pytest
from moto import mock_aws

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store, vault_admin  # noqa: E402 — after the sys.path bootstrap
from scripts.lib import _vault_s3_sync  # noqa: E402

VAULT_ADMIN = "scripts/lib/vault_admin.py"
BUCKET = "aisdlc-test-bucket"
PREFIX = "testproj"
REGION = "us-east-1"


# ── fixtures / helpers ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _dummy_aws_creds(monkeypatch):
    """boto3 refuses to sign without SOME creds even under moto; inject throwaway ones. In a
    reality-anchor run (AISDLC_S3_TEST_ENDPOINT set) leave the AMBIENT real creds untouched — moto
    still accepts them for the mocked tests, and the real-MinIO test needs them. Always strip the
    AISDLC_S3_* config so the resolver never leaks a developer's real bucket/prefix into a moto test."""
    if not os.environ.get("AISDLC_S3_TEST_ENDPOINT"):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    for k in ("AISDLC_S3_BUCKET", "AISDLC_S3_ENDPOINT", "AISDLC_S3_REGION",
              "AISDLC_S3_PREFIX", "AISDLC_S3_PROJECT", "AI_SDLC_VAULT_ROOT"):
        monkeypatch.delenv(k, raising=False)


def _client():
    return boto3.client("s3", region_name=REGION)


def _make_bucket():
    _client().create_bucket(Bucket=BUCKET)


def _make_sharded_vault(path: Path, n: int) -> Path:
    """A faithfully-SHARDED vault (the real migrate path): a flat gate-log.json exploded into the
    per-entry shard log + a derived cache, plus two whole-file artifacts and ambient cruft that must
    NOT sync."""
    path.mkdir(parents=True, exist_ok=True)
    entries = [{"gate": "g", "slice": f"s{i:03d}", "verdict": "clean", "n": i} for i in range(n)]
    (path / "gate-log.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    _shard_store.migrate(path, "gate-log.json", "entries")  # shards + derived cache + .gitignore
    (path / "gate-log.json.lock").write_text("", encoding="utf-8")     # cruft (excluded)
    (path / ".source-repo").write_text("/some/source/repo\n", encoding="utf-8")  # excluded
    (path / "candidates.json").write_text(
        json.dumps({"counters": {"sc": 1}, "candidates": []}), encoding="utf-8")   # artifact
    (path / "risk-register.json").write_text(
        json.dumps({"risks": []}), encoding="utf-8")                                # artifact
    return path


def _push(vault, *, backend="s3", extra=()):
    return vault_admin.main(["sync", "push", "--vault", str(vault), "--backend", backend,
                             "--s3-bucket", BUCKET, "--s3-prefix", PREFIX, *extra])


def _pull(vault, *, backend="s3", extra=()):
    return vault_admin.main(["sync", "pull", "--vault", str(vault), "--backend", backend,
                             "--s3-bucket", BUCKET, "--s3-prefix", PREFIX, *extra])


def _keys():
    r = _client().list_objects_v2(Bucket=BUCKET, Prefix=PREFIX + "/")
    return sorted(o["Key"][len(PREFIX) + 1:] for o in r.get("Contents", []))


def _read_entries(vault) -> list:
    return _shard_store.read_entries(vault, "gate-log.json", "entries")


# ── AC1 — push uploads the sync-set and excludes the derived cache + cruft (M5) ─────

@mock_aws
def test_push_uploads_syncset_and_excludes_cache_and_cruft(tmp_path):
    _make_bucket()
    vault = _make_sharded_vault(tmp_path / "A", 5)
    assert _push(vault) == 0
    keys = _keys()
    # the append-only shard log synced (reconstruction source)
    assert any(k.startswith("gate-log/") and k.endswith(".json") and k != "gate-log/_meta.json"
               for k in keys), keys
    assert "gate-log/_meta.json" in keys
    # whole-file artifacts synced
    assert "candidates.json" in keys and "risk-register.json" in keys, keys
    # M5 / AC1: the derived cache + cruft are NOT uploaded
    assert "gate-log.json" not in keys, "the derived cache must not sync (M5 iterate _SHARDED)"
    assert not any(k.endswith((".lock", ".tmp")) for k in keys), keys
    assert ".source-repo" not in keys
    assert _vault_s3_sync.BASELINE_NAME not in keys, "node-local S3 sync state must not sync"


# ── AC2 — pull into fresh AND existing vaults reconstructs + invalidates the cache ──

@mock_aws
def test_pull_into_fresh_vault_reconstructs_full_vault(tmp_path):
    _make_bucket()
    src = _make_sharded_vault(tmp_path / "A", 6)
    assert _push(src) == 0

    dst = tmp_path / "fresh"
    dst.mkdir()
    assert _pull(dst) == 0
    rows = _read_entries(dst)
    assert len(rows) == 6, f"all 6 rows reconstruct via derive-on-missing: {len(rows)}"
    assert (dst / "gate-log" / "_meta.json").exists()
    assert (dst / "candidates.json").exists() and (dst / "risk-register.json").exists()


@mock_aws
def test_pull_into_existing_vault_invalidates_stale_cache(tmp_path):
    _make_bucket()
    A = _make_sharded_vault(tmp_path / "A", 4)
    assert _push(A) == 0
    B = tmp_path / "B"
    B.mkdir()
    assert _pull(B) == 0
    for i in range(4):
        _shard_store.append_entry(B, "gate-log.json", "entries",
                                  {"gate": "g", "slice": f"B{i:03d}", "verdict": "clean"})
    assert _push(B) == 0
    # A pulls: its stale 4-row derived cache must be invalidated so it re-derives 8.
    assert _pull(A) == 0
    assert len(_read_entries(A)) == 8, "A must not serve its stale 4-row cache after pull"


@mock_aws
def test_pull_contiguity_assertion_refuses_gapped_shard_set_before_derive(tmp_path):
    """B1: a gapped remote shard set (missing a middle seq) is refused fail-visibly BEFORE the
    derived cache is materialized — never a silently-short log."""
    _make_bucket()
    src = _make_sharded_vault(tmp_path / "A", 5)
    assert _push(src) == 0
    _client().delete_object(Bucket=BUCKET, Key=f"{PREFIX}/gate-log/{2:06d}.json")

    dst = tmp_path / "gapped"
    dst.mkdir()
    assert _pull(dst) == 3, "a gapped shard set is exit 3, never a silent short log"
    assert not (dst / "gate-log.json").exists(), "no short-log cache may be derived on a gapped pull"
    # B1 pre-write hardening (code-review minor): a gapped remote refuses BEFORE any shard is written,
    # so no partial/gapped shard set is left on disk for a direct read to derive short.
    written = list((dst / "gate-log").glob("*.json")) if (dst / "gate-log").is_dir() else []
    assert not written, f"no partial gapped shard set may be left on disk: {[p.name for p in written]}"


# ── AC3 — config / optional-dep / ClientError fail closed onto 0/2/3 ────────────────

def test_config_bucket_unset_is_usage_exit2(tmp_path, run_script):
    """A missing bucket is a usage error (exit 2) BEFORE any network call — subprocess-driven
    (no moto needed; proves the shipped script's exit taxonomy end-to-end)."""
    vault = _make_sharded_vault(tmp_path / "v", 2)
    r = run_script(VAULT_ADMIN, ["sync", "push", "--vault", str(vault), "--backend", "s3",
                                 "--s3-prefix", PREFIX])
    assert r.returncode == 2, f"{r.returncode}: {r.stderr}"
    assert "bucket" in r.stderr.lower()


def test_config_unresolvable_prefix_is_usage_exit2(tmp_path, run_script):
    """B3: no git remote AND no AISDLC_S3_PROJECT/PREFIX -> refuse exit 2 (never a per-machine
    default that silently no-ops the cross-machine pull)."""
    vault = _make_sharded_vault(tmp_path / "v", 2)
    env = {"AISDLC_S3_BUCKET": BUCKET, "AISDLC_S3_PREFIX": "", "AISDLC_S3_PROJECT": ""}
    r = run_script(VAULT_ADMIN, ["sync", "push", "--vault", str(vault), "--backend", "s3"],
                   env=env, cwd=str(tmp_path))  # tmp_path is not a git repo -> no remote
    assert r.returncode == 2, f"{r.returncode}: {r.stderr}"
    assert "prefix" in r.stderr.lower() or "project" in r.stderr.lower()


def test_client_build_fails_closed_without_boto3(monkeypatch):
    """AC3: boto3 absent -> a clean SyncUsageError (exit-2 class) with an install hint, never an
    ImportError traceback."""
    def _boom():
        raise _vault_s3_sync.SyncUsageError(
            "boto3 is not installed — `pip install boto3` to use the S3 sync backend.")
    monkeypatch.setattr(_vault_s3_sync, "_import_boto3", _boom)
    cfg = _vault_s3_sync.S3Config(bucket=BUCKET, prefix=PREFIX, endpoint_url=None, region=REGION)
    with pytest.raises(_vault_s3_sync.SyncUsageError) as ei:
        _vault_s3_sync.build_client(cfg)
    assert "boto3" in str(ei.value)


@mock_aws
def test_client_error_missing_bucket_maps_to_exit3(tmp_path):
    """AC3: a boto3 ClientError (the bucket does not exist) -> exit 3 (genuine failure), no bare
    traceback."""
    vault = _make_sharded_vault(tmp_path / "A", 3)  # deliberately do NOT create the bucket
    assert _push(vault) == 3


# ── AC4 — shards-strict data loss + artifact update-preserve + S3-slip + fork ───────

@mock_aws
def test_shards_strict_push_refuses_when_remote_has_shards_local_lacks(tmp_path):
    """AC4: push refuses (pull-first) when the remote holds shards the local lacks — the git
    non-ff twin; no silent clobber/divergence of the append-only log."""
    _make_bucket()
    A = _make_sharded_vault(tmp_path / "A", 5)
    assert _push(A) == 0
    B = tmp_path / "B"
    B.mkdir()
    assert _pull(B) == 0
    for i in range(2):  # B advances the remote to 7
        _shard_store.append_entry(B, "gate-log.json", "entries",
                                  {"gate": "g", "slice": f"B{i:03d}", "verdict": "clean"})
    assert _push(B) == 0
    # A (still at 5) tries to push -> remote has shards 5,6 A lacks -> refuse
    assert _push(A) == 3, "push must refuse when the remote is ahead (pull-first guard)"


@mock_aws
def test_two_established_vault_artifact_update_preserves_local_edits(tmp_path):
    """M-add-1: A updates candidates.json and pushes; B has an un-pushed local edit to a DIFFERENT
    artifact. B pull observes A's update AND keeps B's un-pushed edit (neither is silently lost)."""
    _make_bucket()
    A = _make_sharded_vault(tmp_path / "A", 4)
    assert _push(A) == 0
    B = tmp_path / "B"
    B.mkdir()
    assert _pull(B) == 0

    (A / "candidates.json").write_text(json.dumps({"counters": {"sc": 9}, "candidates": ["x"]}),
                                       encoding="utf-8")
    assert _push(A) == 0
    (B / "risk-register.json").write_text(json.dumps({"risks": ["local-only"]}), encoding="utf-8")

    assert _pull(B) == 0
    assert json.load(open(B / "candidates.json"))["counters"]["sc"] == 9, "B observes A's update"
    assert json.load(open(B / "risk-register.json"))["risks"] == ["local-only"], \
        "B's un-pushed edit must not be silently lost"


@mock_aws
def test_pull_refuses_to_clobber_conflicting_local_edit_without_force(tmp_path):
    """M-add-1: when BOTH sides changed the SAME artifact, pull refuses (exit 3) without --force,
    and --force overwrites to the remote."""
    _make_bucket()
    A = _make_sharded_vault(tmp_path / "A", 3)
    assert _push(A) == 0
    B = tmp_path / "B"
    B.mkdir()
    assert _pull(B) == 0
    (A / "candidates.json").write_text(json.dumps({"counters": {"sc": 1}, "v": "A"}), encoding="utf-8")
    assert _push(A) == 0
    (B / "candidates.json").write_text(json.dumps({"counters": {"sc": 1}, "v": "B"}), encoding="utf-8")
    assert _pull(B) == 3, "a conflicting local artifact edit is refused without --force"
    assert json.load(open(B / "candidates.json"))["v"] == "B", "local edit intact after the refusal"
    assert _pull(B, extra=("--force",)) == 0
    assert json.load(open(B / "candidates.json"))["v"] == "A", "--force overwrites to the remote"


@mock_aws
def test_pull_s3slip_key_outside_vault_is_refused(tmp_path):
    """M4: a downloaded object whose key resolves outside the vault root ('..'/absolute) is refused
    fail-visibly and NEVER written outside the vault (mirrors cmd_import filter='data')."""
    _make_bucket()
    src = _make_sharded_vault(tmp_path / "A", 3)
    assert _push(src) == 0
    _client().put_object(Bucket=BUCKET, Key=f"{PREFIX}/../evil.json", Body=b"pwned")

    dst = tmp_path / "victim"
    dst.mkdir()
    assert _pull(dst) == 3, "an escaping key must be refused (exit 3)"
    assert not (dst.parent / "evil.json").exists(), "nothing may be written outside the vault root"


@mock_aws
def test_push_refuses_same_seq_different_content_fork_via_content_hash(tmp_path):
    """M1: a same-seq shard whose remote CONTENT differs from local is a FORK — refused fail-visibly
    via the x-amz-meta content hash (never ETag==MD5)."""
    _make_bucket()
    A = _make_sharded_vault(tmp_path / "A", 3)
    assert _push(A) == 0
    forged = json.dumps({"gate": "g", "slice": "FORK", "verdict": "clean", "n": 1}).encode()
    _client().put_object(
        Bucket=BUCKET, Key=f"{PREFIX}/gate-log/{1:06d}.json", Body=forged,
        Metadata={_vault_s3_sync.CONTENT_HASH_META: hashlib.sha256(forged).hexdigest()})
    assert _push(A) == 3, "a same-seq different-content shard is a fork -> exit 3"


# ── AC5 — moto round-trip (simulation) + env-gated real MinIO (reality anchor) ──────

@mock_aws
def test_moto_roundtrip_reconstructs_full_vault_via_shipped_cli(tmp_path):
    """AC5 walking-skeleton: push -> fresh-vault pull reconstructs the vault (shards + _meta +
    whole-file artifacts). moto is a labeled SIMULATION (the reality anchor is the MinIO test)."""
    _make_bucket()
    src = _make_sharded_vault(tmp_path / "src", 7)
    assert _push(src) == 0
    dst = tmp_path / "dst"
    dst.mkdir()
    assert _pull(dst) == 0
    assert len(_read_entries(dst)) == 7
    assert json.load(open(dst / "candidates.json")) == json.load(open(src / "candidates.json"))
    src_shards = {p.name for p in (src / "gate-log").glob("*.json")}
    dst_shards = {p.name for p in (dst / "gate-log").glob("*.json")}
    assert src_shards == dst_shards, "the full shard set + _meta round-trips"


@pytest.mark.skipif(not os.environ.get("AISDLC_S3_TEST_ENDPOINT"),
                    reason="reality anchor: set AISDLC_S3_TEST_ENDPOINT (+bucket/creds) to run against real MinIO")
def test_real_minio_roundtrip(tmp_path):
    """The REALITY anchor (not moto): an env-gated round-trip against a real MinIO/S3 endpoint.
    Skipped by default; run with AISDLC_S3_TEST_ENDPOINT/AISDLC_S3_TEST_BUCKET set (spike-095 proved
    real MinIO with 24 objects)."""
    endpoint = os.environ["AISDLC_S3_TEST_ENDPOINT"]
    bucket = os.environ.get("AISDLC_S3_TEST_BUCKET", "aisdlc-vault-sync-test")
    prefix = f"it-{os.getpid()}"
    src = _make_sharded_vault(tmp_path / "src", 5)
    cfg = _vault_s3_sync.S3Config(bucket=bucket, prefix=prefix, endpoint_url=endpoint, region=REGION)
    client = _vault_s3_sync.build_client(cfg)
    _vault_s3_sync.sync_push(src, cfg=cfg, client=client, log=lambda m: None)
    dst = tmp_path / "dst"
    dst.mkdir()
    _vault_s3_sync.sync_pull(dst, cfg=cfg, client=client, log=lambda m: None)
    assert len(_read_entries(dst)) == 5
