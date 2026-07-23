"""slice-097 / SC-206 / ADR-123 — the M2 end-to-end proof (CC-002 reach/level).

AC3 says "a subsequent `vault_admin sync --backend s3` resolve_config succeeds against the persisted
config". An isolated resolve_config(...) unit assertion is a SHALLOWER proxy (slice-063 BC-PROJ-9
laundered-green: a helper GREEN at CLI while the consumer never reaches it). So this drives the REAL
path end-to-end: `vault_admin.main(['sync','push','--backend','s3'])` IN-PROCESS under moto @mock_aws
with a persisted sync-backend.json and NO env / NO --s3-* args — proving cmd_sync loads the file,
os.environ.setdefault-folds it (ADR-123), and the SHIPPED resolve_config picks it up so the object
lands under the FILE-sourced bucket + prefix. Plus the m4 back-compat characterization: no config +
no flag => the git backend (never s3).

moto's @mock_aws patches botocore IN-PROCESS only, so this MUST call main() in-process (a subprocess
would escape the mock). boto3 + moto are hard-imported (requirements-dev.txt; never importorskip-to-green).

TF-1: written FAILING before the cmd_sync setdefault fold exists.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3  # hard import (M3 — never importorskip-to-green)
import pytest
from moto import mock_aws

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _sync_config, _vault_s3_sync, vault_admin as va  # noqa: E402

BUCKET = "filesourced-bucket"
PROJECT = "fileproj"          # -> the S3 key prefix (machine-invariant, B3)
REGION = "us-east-1"

_S3_ENVS = ("AISDLC_S3_BUCKET", "AISDLC_S3_ENDPOINT", "AISDLC_S3_REGION",
            "AISDLC_S3_PREFIX", "AISDLC_S3_PROJECT")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Dummy AWS creds for moto; strip every AISDLC_S3_* so the ONLY config source is the persisted
    file. cmd_sync's setdefault fold mutates os.environ (no restore, by ADR-123's one-shot-CLI
    design), so pop the folded vars on teardown to keep test isolation."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    for k in _S3_ENVS:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in _S3_ENVS:
        os.environ.pop(k, None)


def _common(tmp_path, monkeypatch) -> Path:
    common = tmp_path / "proj" / ".git"
    common.mkdir(parents=True)
    monkeypatch.setattr(va, "_git_common_dir", lambda: str(common))
    return common


def _vault_with_artifact(tmp_path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    return vault


def _keys():
    r = boto3.client("s3", region_name=REGION).list_objects_v2(Bucket=BUCKET, Prefix=PROJECT + "/")
    return sorted(o["Key"] for o in r.get("Contents", []))


# ── AC3 / M2 — the persisted file resolves + pushes end-to-end (no env, no args) ────

@mock_aws
def test_persisted_s3_config_resolves_and_pushes_under_moto(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    vault = _vault_with_artifact(tmp_path)
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    # persist the picker's choice (the ONLY config source — no env, no CLI --s3-* args)
    _sync_config.save({"backend": "s3", "s3": {"bucket": BUCKET, "region": REGION,
                                               "project": PROJECT}}, common_dir=common)

    rc = va.main(["sync", "push", "--vault", str(vault), "--backend", "s3"])
    assert rc == 0, "cmd_sync must load the file, setdefault-fold it, and push via the shipped engine"
    keys = _keys()
    assert f"{PROJECT}/candidates.json" in keys, \
        f"the object must land under the FILE-sourced bucket + prefix (got {keys})"


@mock_aws
def test_backend_resolves_from_file_without_explicit_flag(tmp_path, monkeypatch):
    """AC2: a later `sync` (no --backend) reads the recorded backend — s3 here, not the git default."""
    common = _common(tmp_path, monkeypatch)
    vault = _vault_with_artifact(tmp_path)
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    _sync_config.save({"backend": "s3", "s3": {"bucket": BUCKET, "project": PROJECT}},
                      common_dir=common)
    assert va.main(["sync", "push", "--vault", str(vault)]) == 0  # NO --backend flag
    assert f"{PROJECT}/candidates.json" in _keys()


# ── M-add-2 — the file-sourced REGION reaches S3Config (region has zero prior wiring) ──

def test_file_sourced_region_reaches_s3config(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    vault = _vault_with_artifact(tmp_path)
    _sync_config.save({"backend": "s3", "s3": {"bucket": BUCKET, "region": "eu-west-1",
                                               "project": PROJECT}}, common_dir=common)
    cfg_file = _sync_config.load(common)
    # exercise the REAL production helpers (the ADR-123 setdefault seam) — assert INSIDE the scope
    with va._scoped_env_setdefault(va._s3_env_pairs(cfg_file["s3"])):
        s3cfg = _vault_s3_sync.resolve_config(vault)
        assert s3cfg.region == "eu-west-1", "a file-sourced region must reach S3Config (M-add-2)"
        assert s3cfg.bucket == BUCKET
        assert s3cfg.prefix == PROJECT


# ── m4 — back-compat characterization: no config + no flag => git (never s3) ─────────

def test_no_config_defaults_to_git(tmp_path, monkeypatch):
    _common(tmp_path, monkeypatch)  # a git tree, but NO sync-backend.json written
    vault = _vault_with_artifact(tmp_path)
    called = {}

    def _fake_git_push(v, *, remote_arg=None, log):
        called["git"] = True
        return {"action": "pushed", "remote": remote_arg}

    monkeypatch.setattr(va._vault_git_sync, "sync_push", _fake_git_push)
    # if it wrongly took the s3 path it would hit resolve_config/boto3, not the git engine
    assert va.main(["sync", "push", "--vault", str(vault)]) == 0
    assert called.get("git"), "no config + no --backend must resolve the GIT backend (back-compat)"


def test_local_backend_is_visible_noop(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    vault = _vault_with_artifact(tmp_path)
    _sync_config.save({"backend": "local"}, common_dir=common)
    assert va.main(["sync", "push", "--vault", str(vault)]) == 0  # local = no-op, never touches a remote
