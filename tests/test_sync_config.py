"""slice-097 / SC-206 / ADR-121+123 — `scripts/lib/_sync_config.py` (the per-project sync-backend
config leaf: <git-common-dir>/aisdlc/sync-backend.json).

test_first tests for the config SSoT: round-trip read/write, BOM tolerance (m2), atomic BOM-free
write (m2), the M3 security contract (secret-shaped KEY refusal + endpoint-userinfo refusal on BOTH
write and read), the location seam (git_common_dir-anchored, sibling of the vault-root pin), and the
back-compat absent/empty/malformed -> None (git default) contract.

TF-1: written FAILING before _sync_config.py exists.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _sync_config as sc  # noqa: E402 — after the sys.path bootstrap


def _common(tmp_path: Path) -> Path:
    """A tmp <root>/.git common-dir (no real git needed) — the config lands at
    <common>/aisdlc/sync-backend.json, the sibling of the vault-root pin."""
    common = tmp_path / "proj" / ".git"
    common.mkdir(parents=True)
    return common


# ── location seam ──────────────────────────────────────────────────────────────────

def test_config_path_is_sibling_of_vault_root_pin(tmp_path):
    common = _common(tmp_path)
    p = sc.config_path(common)
    assert p == common / "aisdlc" / "sync-backend.json"
    # same aisdlc/ namespace as the tier-2 vault-root pin (_CONFIG_REL = aisdlc/vault-root)
    from scripts.lib._vault_paths import _CONFIG_REL
    assert p.parent == (common / _CONFIG_REL).parent


def test_config_path_none_when_not_a_git_tree(monkeypatch):
    monkeypatch.setattr(sc, "git_common_dir", lambda: None)
    assert sc.config_path(None) is None


# ── AC2 — backend_picker_persist_and_readback (round-trip) ──────────────────────────

def test_backend_picker_persist_and_readback(tmp_path):
    common = _common(tmp_path)
    cfg = {"backend": "s3", "s3": {"bucket": "b", "endpoint": "https://minio.local:9000",
                                   "region": "eu-west-1", "project": "proj-x"}}
    sc.save(cfg, common_dir=common)
    got = sc.load(common_dir=common)
    assert got["backend"] == "s3"
    assert got["s3"] == {"bucket": "b", "endpoint": "https://minio.local:9000",
                         "region": "eu-west-1", "project": "proj-x"}


def test_local_persists_no_remote_or_bucket(tmp_path):
    common = _common(tmp_path)
    sc.save({"backend": "local"}, common_dir=common)
    got = sc.load(common_dir=common)
    assert got["backend"] == "local"
    assert "s3" not in got and "git" not in got


def test_git_records_remote(tmp_path):
    common = _common(tmp_path)
    sc.save({"backend": "git", "git": {"remote": "origin"}}, common_dir=common)
    assert sc.load(common_dir=common)["git"] == {"remote": "origin"}


def test_absent_config_loads_none(tmp_path):
    assert sc.load(common_dir=_common(tmp_path)) is None


def test_empty_config_loads_none(tmp_path):
    common = _common(tmp_path)
    p = sc.config_path(common)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("   ", encoding="utf-8")
    assert sc.load(common_dir=common) is None


def test_malformed_json_warns_and_loads_none(tmp_path):
    common = _common(tmp_path)
    p = sc.config_path(common)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    warned = []
    assert sc.load(common_dir=common, warn=warned.append) is None
    assert warned, "a malformed config must WARN, never silently mis-resolve"


# ── m2 — BOM tolerance + BOM-free atomic write ──────────────────────────────────────

def test_bom_prefixed_config_is_read(tmp_path):
    """A PowerShell Out-File BOM must not degrade the config to 'unset' (sibling readers
    _vault_paths.py:183/:234 already use utf-8-sig)."""
    common = _common(tmp_path)
    p = sc.config_path(common)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"backend": "git", "git": {"remote": "origin"}})
    p.write_text("﻿" + body, encoding="utf-8")  # explicit BOM
    got = sc.load(common_dir=common)
    assert got is not None and got["backend"] == "git"


def test_written_file_is_bom_free(tmp_path):
    common = _common(tmp_path)
    sc.save({"backend": "local"}, common_dir=common)
    raw = sc.config_path(common).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the written config must be BOM-free"


# ── M3 — secret-shaped KEY refusal (write AND read) ─────────────────────────────────

@pytest.mark.parametrize("bad_key", [
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "secret", "my_token", "password", "aws_credential",
])
def test_save_refuses_secret_shaped_key(tmp_path, bad_key):
    common = _common(tmp_path)
    cfg = {"backend": "s3", "s3": {"bucket": "b", "project": "p"}, bad_key: "leak"}
    with pytest.raises(sc.SyncConfigError):
        sc.save(cfg, common_dir=common)
    assert not sc.config_path(common).exists(), "a rejected save must not leave a file"


def test_load_refuses_secret_shaped_key(tmp_path):
    common = _common(tmp_path)
    p = sc.config_path(common)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"backend": "s3", "s3": {"bucket": "b", "project": "p"},
                             "AWS_SECRET_ACCESS_KEY": "leak"}), encoding="utf-8")
    with pytest.raises(sc.SyncConfigError):
        sc.load(common_dir=common)


# ── M3 — endpoint-URL userinfo refusal (write AND read) ─────────────────────────────

@pytest.mark.parametrize("endpoint", [
    "https://AKIAEXAMPLE:secretkey@minio.local:9000",
    "//user:pass@host:9000",
    "user:pass@host:9000",
])
def test_save_refuses_endpoint_userinfo(tmp_path, endpoint):
    common = _common(tmp_path)
    cfg = {"backend": "s3", "s3": {"bucket": "b", "endpoint": endpoint, "project": "p"}}
    with pytest.raises(sc.SyncConfigError):
        sc.save(cfg, common_dir=common)


def test_load_refuses_endpoint_userinfo(tmp_path):
    common = _common(tmp_path)
    p = sc.config_path(common)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"backend": "s3", "s3": {
        "bucket": "b", "endpoint": "https://k:s@minio.local:9000", "project": "p"}}),
        encoding="utf-8")
    with pytest.raises(sc.SyncConfigError):
        sc.load(common_dir=common)


def test_benign_endpoint_is_accepted(tmp_path):
    common = _common(tmp_path)
    cfg = {"backend": "s3", "s3": {"bucket": "b", "endpoint": "https://minio.local:9000",
                                   "project": "p"}}
    sc.save(cfg, common_dir=common)  # no raise
    assert sc.load(common_dir=common)["s3"]["endpoint"] == "https://minio.local:9000"
