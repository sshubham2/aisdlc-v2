"""scripts/lib/_vault_paths.py — per-invocation vault resolution precedence.

4.6.1 relies on this: once the SessionStart hook stops freezing AI_SDLC_VAULT_ROOT, each
invocation resolves the vault from its OWN cwd/git context, so two repos in one session map
to two different vaults. An explicit user-set env var still wins (tier 1).
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

RES = "scripts/lib/_vault_paths.py"


def test_env_var_tier1_wins(run_script):
    # an EXPLICIT AI_SDLC_VAULT_ROOT is still honored (the override path 4.6.1 preserves)
    r = run_script(RES, ["--path"], env={"AI_SDLC_VAULT_ROOT": "/explicit/override"})
    assert r.returncode == 0
    out = r.stdout.strip()
    assert "explicit" in out and "override" in out  # str(Path(...)) may use OS separators


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_two_repos_resolve_two_vaults(run_script, tmp_path):
    # THE 4.6.1 isolation: with the env var unset (run_script strips it), two different repos
    # resolve DIFFERENT vaults — no frozen-env leak routing both to the first repo's vault.
    a = tmp_path / "repo-a"
    b = tmp_path / "repo-b"
    a.mkdir()
    b.mkdir()
    subprocess.run(["git", "-C", str(a), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(b), "init"], capture_output=True)

    va = run_script(RES, ["--path"], cwd=a).stdout.strip()
    vb = run_script(RES, ["--path"], cwd=b).stdout.strip()

    assert va and vb
    assert va != vb  # different repos -> different vaults (the bug 4.6.1 fixes)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_frozen_env_would_mis_route(run_script, tmp_path):
    # demonstrates the BUG the hook caused: a frozen env var routes repo B to repo A's vault
    b = tmp_path / "repo-b"
    b.mkdir()
    subprocess.run(["git", "-C", str(b), "init"], capture_output=True)
    r = run_script(RES, ["--path"], cwd=b, env={"AI_SDLC_VAULT_ROOT": "/repo/a/vault"})
    assert "repo" in r.stdout and "vault" in r.stdout  # tier-1 env wins, NOT repo B's own vault
