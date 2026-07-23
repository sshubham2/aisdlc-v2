"""slice-097 / SC-206 — AC5: /setup's base+backend surface is additive, READ-ONLY, and fail-visible.

setup.py --check must (a) surface the resolved vault base + this-repo vault path + the current sync
backend, (b) write NOTHING (git status stays clean), and (c) NOT run the picker steps (they live in
SKILL.md as consented AskUserQuestion — m3, AC5 structural-by-ordering). The consented persist is a
SEPARATE vault_admin actuator with the exit-3 taxonomy, so it never aborts the deps install.

TF-1: the base/backend surface lines are written FAILING before the setup.py edit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_PY = REPO_ROOT / "skills" / "setup" / "scripts" / "setup.py"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


@pytest.fixture
def tmp_git_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    if _git(repo, "init").returncode != 0:
        pytest.skip("git not available")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _run_check(repo):
    """Run `setup.py --check` with cwd=repo (so git-common-dir resolves the tmp repo) and a stripped
    AISDLC_S3_* env (never resolve a developer's real config)."""
    import os
    env = dict(os.environ)
    for k in ("AISDLC_S3_BUCKET", "AISDLC_S3_ENDPOINT", "AISDLC_S3_REGION",
              "AISDLC_S3_PREFIX", "AISDLC_S3_PROJECT", "AI_SDLC_VAULT_ROOT"):
        env.pop(k, None)
    return subprocess.run([sys.executable, str(SETUP_PY), "--check", str(repo)],
                          cwd=str(repo), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def test_check_surfaces_base_vault_and_backend(tmp_git_repo):
    r = _run_check(tmp_git_repo)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "vault base" in out
    assert "vault (repo)" in out
    assert "sync backend" in out
    # a fresh repo has no persisted config -> the surface names the git-default back-compat
    assert "not configured" in out


def test_check_writes_nothing(tmp_git_repo):
    """AC5: --check makes NO changes (git status clean) AND runs no persist/picker."""
    before = _git(tmp_git_repo, "status", "--porcelain").stdout
    _run_check(tmp_git_repo)
    after = _git(tmp_git_repo, "status", "--porcelain").stdout
    assert before == after == "", f"--check must not modify the tree (before={before!r} after={after!r})"
    # the config file the picker would write must NOT exist after a read-only --check
    assert not (tmp_git_repo / ".git" / "aisdlc" / "sync-backend.json").exists()
