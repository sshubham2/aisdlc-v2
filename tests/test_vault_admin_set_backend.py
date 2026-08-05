"""slice-097 / SC-206 / ADR-121+123 — `vault_admin set-backend` / `set-base` consented actuators.

test_first tests for the two non-interactive actuators /setup's SKILL.md AskUserQuestion gates call:
  * set-backend --backend {local,git,s3} — validate-before-record via the boto3-FREE resolve_config,
    persist via _sync_config, read-back-verify, exit 0/2/3; NEVER imports boto3 (m5); WARN+persist on
    boto3 absence (m5); shadow-var WARN naming a real-env AISDLC_S3_* (m1); local persists no
    remote/bucket (AC2); refuse a secret / userinfo endpoint (AC4/M3).
  * set-base <dir> — write ~/.claude/ai-sdlc-vault-base, read-back-verify, base-dir writable check,
    exit 3 on mismatch, idempotent (AC1 lower half / m5).

Seams (mirroring the write-pin tests): monkeypatch ``va._git_common_dir`` (config location) and
``va._base_config_file`` (base file) onto tmp paths so no real repo / home file is touched.

TF-1: written FAILING before the set-backend / set-base subcommands exist.
"""
from __future__ import annotations

import argparse
import builtins
import json
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _sync_config as sc  # noqa: E402
from scripts.lib import vault_admin as va  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_s3_env(monkeypatch):
    """No real-env AISDLC_S3_* leaks into a validate/shadow test (a developer shell / slice-095 flow
    may have them set)."""
    for k in ("AISDLC_S3_BUCKET", "AISDLC_S3_ENDPOINT", "AISDLC_S3_REGION",
              "AISDLC_S3_PREFIX", "AISDLC_S3_PROJECT"):
        monkeypatch.delenv(k, raising=False)


def _common(tmp_path, monkeypatch):
    common = tmp_path / "proj" / ".git"
    common.mkdir(parents=True)
    monkeypatch.setattr(va, "_git_common_dir", lambda: str(common))
    return common


def _sb_args(**kw):
    base = dict(backend=None, s3_bucket=None, s3_endpoint=None, s3_region=None,
                s3_project=None, remote=None, vault=None)
    base.update(kw)
    return argparse.Namespace(**base)


# ── AC2 — backend persisted + round-trips; local persists no remote/bucket ──────────

def test_set_backend_local_persists_no_remote(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    assert va.cmd_set_backend(_sb_args(backend="local")) == 0
    got = sc.load(common_dir=common)
    assert got["backend"] == "local" and "s3" not in got and "git" not in got


def test_set_backend_s3_persists_and_readback(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="mybucket",
                                     s3_endpoint="https://minio.local:9000",
                                     s3_region="eu-west-1", s3_project="proj-x",
                                     vault=str(tmp_path / "vault")))
    assert rc == 0
    got = sc.load(common_dir=common)
    assert got["s3"] == {"bucket": "mybucket", "endpoint": "https://minio.local:9000",
                         "region": "eu-west-1", "project": "proj-x"}


def test_set_backend_git_records_remote(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    assert va.cmd_set_backend(_sb_args(backend="git", remote="origin")) == 0
    assert sc.load(common_dir=common)["git"] == {"remote": "origin"}


def test_set_backend_not_git_tree_exit2(tmp_path, monkeypatch):
    monkeypatch.setattr(va, "_git_common_dir", lambda: None)
    assert va.cmd_set_backend(_sb_args(backend="local")) == 2


# ── slice-100 / M5 / ADR-131 add.3 — the claim register finally gets a PRODUCER ──────

def _claim_ready_vault(tmp_path: Path) -> Path:
    """A vault that can actually serve a git claim register: a git work tree with a remote."""
    import subprocess
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    subprocess.run(["git", "init", "-q"], cwd=str(vault), check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=str(vault), check=True)
    return vault


def test_set_backend_git_also_enables_claim_coordination(tmp_path, monkeypatch):
    """Choosing the git backend at /setup must ALSO write the claim-backend opt-in signal.

    The two files are different: set-backend writes `aisdlc/sync-backend.json`, while the claim
    backend reads `aisdlc/claim-backend` (a different schema). Before this, ZERO writers of the
    latter existed outside a test fixture -- so the register's own mitigation for its weakest joint
    ('a team that ran /setup once shares one register by construction') was simply FALSE: every peer
    silently took the uncoordinated `backend is None` path."""
    from scripts.lib import _claim_coord
    common = _common(tmp_path, monkeypatch)
    vault = _claim_ready_vault(tmp_path)
    assert va.cmd_set_backend(_sb_args(backend="git", remote="origin", vault=str(vault))) == 0
    signal = common / _claim_coord._CONFIG_REL
    assert signal.read_text(encoding="utf-8-sig").strip() == "git", \
        "picking the git backend must enable claim coordination, not just sync"
    # and the signal is the one _claim_coord actually READS (no BOM / trailing-whitespace drift):
    # write-location == read-location, proven end to end rather than by matching two path constants.
    from scripts.lib import _vault_paths
    monkeypatch.delenv("AI_SDLC_CLAIM_BACKEND", raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR", str(common), raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR_DONE", True, raising=False)
    assert _claim_coord._read_signal() == "git"


@pytest.mark.parametrize("with_remote", [False, True])
def test_set_backend_git_refuses_to_arm_an_unservable_register(tmp_path, monkeypatch, capsys,
                                                               with_remote):
    """CR1 (code-review BLOCKER): validate-before-record, as the `s3` branch already does.

    The vault lives OUTSIDE the repo at ~/.aisdlc/<slug>-<hash>/ and is NOT a git repo by default,
    while set-backend's own precondition check only covers the PROJECT repo's common-dir. Arming the
    signal anyway hard-blocks `/slice`: every claim then builds a GitClaimBackend whose first act is
    `_require_git_tree`, refuses, and there is no way to un-arm it. Both unservable shapes are
    covered: not a git work tree at all, and a git work tree with NO remote."""
    import subprocess
    from scripts.lib import _claim_coord
    common = _common(tmp_path, monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    if with_remote:  # a git tree, but no remote -> _resolve_remote refuses
        subprocess.run(["git", "init", "-q"], cwd=str(vault), check=True)

    assert va.cmd_set_backend(_sb_args(backend="git", remote="origin", vault=str(vault))) == 0, \
        "the sync-backend choice itself is still recorded -- only the register is withheld"
    assert not (common / _claim_coord._CONFIG_REL).exists(), \
        "an unservable claim register must NEVER be armed -- it hard-blocks every /slice"
    err = capsys.readouterr().err
    assert "claim coordination NOT enabled" in err, err
    assert "git -C" in err and "remote add origin" in err, f"the recovery must be actionable: {err}"
    assert sc.load(common_dir=common)["backend"] == "git"


def test_set_backend_non_git_disables_claim_coordination(tmp_path, monkeypatch, capsys):
    """CR1: the picker that ARMS the register is also the verb that DISARMS it.

    Without this there is no way to turn coordination off short of hand-deleting a file under
    `.git/` -- and an armed register that cannot be reached refuses every claim. The removal is
    announced, because silently disabling a collision guard would be its own hazard."""
    from scripts.lib import _claim_coord
    common = _common(tmp_path, monkeypatch)
    vault = _claim_ready_vault(tmp_path)
    assert va.cmd_set_backend(_sb_args(backend="git", remote="origin", vault=str(vault))) == 0
    signal = common / _claim_coord._CONFIG_REL
    assert signal.exists()

    assert va.cmd_set_backend(_sb_args(backend="local")) == 0
    assert not signal.exists(), "picking a non-git backend must disarm the claim register"
    assert "claim coordination disabled" in capsys.readouterr().out
    # idempotent: disabling twice is a clean no-op, not an error.
    assert va.cmd_set_backend(_sb_args(backend="local")) == 0


def test_set_backend_git_warns_when_claim_signal_unwritable(tmp_path, monkeypatch, capsys):
    """A claim signal that could NOT be written is announced with the exact manual recovery -- an
    unannounced failure here is precisely the silent double-pick this backend exists to prevent. It
    is non-fatal to the sync-backend choice, which already succeeded and read back clean."""
    common = _common(tmp_path, monkeypatch)
    vault = _claim_ready_vault(tmp_path)
    monkeypatch.setattr(va, "safe_write_text", _boom_on_claim_signal(va.safe_write_text))
    assert va.cmd_set_backend(_sb_args(backend="git", remote="origin", vault=str(vault))) == 0, \
        "the sync-backend choice itself is recorded and trustworthy -- do not fail it"
    err = capsys.readouterr().err
    assert "NOT coordinated" in err and "AI_SDLC_CLAIM_BACKEND=git" in err, err
    assert sc.load(common_dir=common)["backend"] == "git"


def _boom_on_claim_signal(real):
    def wrapper(path, text, *a, **kw):
        if str(path).endswith("claim-backend"):
            raise OSError("simulated: read-only .git directory")
        return real(path, text, *a, **kw)
    return wrapper


# ── AC3 validation — an s3 config with no bucket / unresolvable prefix is exit 2 ─────

def test_set_backend_s3_missing_bucket_exit2(tmp_path, monkeypatch):
    _common(tmp_path, monkeypatch)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_project="p", vault=str(tmp_path / "v")))
    assert rc == 2, "no bucket must be a usage error before recording"


def test_set_backend_s3_unresolvable_prefix_exit2(tmp_path, monkeypatch):
    _common(tmp_path, monkeypatch)
    # bucket present but NO project and (mocked) no git remote -> prefix unresolvable
    monkeypatch.setattr("scripts.lib._vault_s3_sync._project_remote_url", lambda: None)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="b", vault=str(tmp_path / "v")))
    assert rc == 2


# ── AC4 / M3 — no credential ever persisted ─────────────────────────────────────────

def test_set_backend_refuses_endpoint_userinfo_exit2(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="b", s3_project="p",
                                     s3_endpoint="https://AKIA:secret@minio.local:9000",
                                     vault=str(tmp_path / "v")))
    assert rc == 2
    assert not sc.config_path(common).exists(), "a rejected s3 config must not be persisted"


def test_no_credential_written_to_config(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="b", s3_endpoint="https://minio.local:9000",
                                s3_region="us-east-1", s3_project="p", vault=str(tmp_path / "v")))
    raw = sc.config_path(common).read_text(encoding="utf-8").lower()
    for pat in ("access_key", "secret", "session_token", "password", "@minio"):
        assert pat not in raw, f"config must not contain {pat!r}"


# ── m1 — shadow-var WARN when a real-env AISDLC_S3_* would shadow the freshly-picked file ──

def test_set_backend_warns_on_shadowing_env(tmp_path, monkeypatch, capsys):
    _common(tmp_path, monkeypatch)
    monkeypatch.setenv("AISDLC_S3_BUCKET", "stale-shadow-bucket")
    va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="fresh", s3_project="p",
                                vault=str(tmp_path / "v")))
    err = capsys.readouterr().err
    assert "AISDLC_S3_BUCKET" in err and "shadow" in err.lower()


# ── m5 — boto3 absence WARNs + surfaces the pip hint but STILL PERSISTS (exit 0) ─────

def test_set_backend_s3_persists_when_boto3_absent(tmp_path, monkeypatch, capsys):
    common = _common(tmp_path, monkeypatch)
    monkeypatch.setattr(va, "_boto3_available", lambda: False)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="b", s3_project="p",
                                     vault=str(tmp_path / "v")))
    assert rc == 0, "boto3 absence must never block persisting the s3 choice (never force-install)"
    assert sc.load(common_dir=common)["backend"] == "s3"
    out = capsys.readouterr()
    assert "boto3" in (out.err + out.out) and "pip install" in (out.err + out.out)


def test_set_backend_s3_never_imports_boto3(tmp_path, monkeypatch):
    """m5 structural: validate-before-record is boto3-FREE (resolve_config), so set-backend must
    succeed even if `import boto3` raises — proving it never reaches build_client / an import."""
    _common(tmp_path, monkeypatch)
    real_import = builtins.__import__

    def _no_boto3(name, *a, **k):
        if name == "boto3" or name.startswith("boto3.") or name == "botocore":
            raise ImportError("boto3 blocked for this test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_boto3)
    rc = va.cmd_set_backend(_sb_args(backend="s3", s3_bucket="b", s3_project="p",
                                     vault=str(tmp_path / "v")))
    assert rc == 0


# ── AC5 — persist failure is fail-visible exit 3 ────────────────────────────────────

def test_set_backend_readback_mismatch_exit3(tmp_path, monkeypatch, capsys):
    _common(tmp_path, monkeypatch)
    monkeypatch.setattr(va._sync_config, "load", lambda *a, **k: {"backend": "git"})  # lie on read-back
    rc = va.cmd_set_backend(_sb_args(backend="local"))
    assert rc == 3
    assert "read-back" in capsys.readouterr().err.lower()


# ── AC1 (lower half) / m5 — set-base read-back-verify + writability + idempotent ─────

def test_set_base_writes_readback_verifies_and_is_idempotent(tmp_path, monkeypatch):
    cfgfile = tmp_path / "home" / ".claude" / "ai-sdlc-vault-base"
    monkeypatch.setattr(va, "_base_config_file", lambda: cfgfile)
    base_dir = tmp_path / "custom-base"
    rc = va.cmd_set_base(argparse.Namespace(dir=str(base_dir)))
    assert rc == 0
    assert cfgfile.read_text(encoding="utf-8-sig").strip() == str(base_dir)
    assert base_dir.is_dir(), "the base dir must be created (writability confirmed)"
    # idempotent re-run
    assert va.cmd_set_base(argparse.Namespace(dir=str(base_dir))) == 0


def test_set_base_unwritable_dir_exit3(tmp_path, monkeypatch):
    cfgfile = tmp_path / ".claude" / "ai-sdlc-vault-base"
    monkeypatch.setattr(va, "_base_config_file", lambda: cfgfile)
    # a base path whose PARENT is a FILE -> mkdir(parents=True) raises -> exit 3
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    rc = va.cmd_set_base(argparse.Namespace(dir=str(afile / "under-a-file")))
    assert rc == 3


# ── argparse wiring (main dispatch) ─────────────────────────────────────────────────

def test_main_dispatches_set_backend_local(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    assert va.main(["set-backend", "--backend", "local"]) == 0
    assert sc.load(common_dir=common)["backend"] == "local"


def test_backend_choices_include_local():
    """m4: the picker offers exactly {local, git, s3}."""
    import scripts.lib.vault_admin as m
    p = m  # smoke: main builds the parser; assert 'local' is accepted for set-backend
    # driven via main() above; here assert an unknown backend is rejected by argparse (SystemExit)
    with pytest.raises(SystemExit):
        m.main(["set-backend", "--backend", "nope"])
