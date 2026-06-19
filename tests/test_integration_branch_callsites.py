"""slice-022 AC3 / m1 — the script call sites RESOLVE the integration branch (uat).

These are the genuine red->green tests for the pr_flow.py:293 + resolve_sync_target.py:256
swap (resolve_default_branch -> resolve_integration_branch): against a real repo with a
`uat` branch and NO explicit --default, each script must resolve `uat`, not the released
trunk. Pre-swap (resolve_default_branch) these assert-uat cases FAIL (they resolve master) —
that is the red->green genuineness the m1 finding asked for (demonstrated live via a git
stash of the swapped scripts during build).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRF = ROOT / "skills" / "commit-slice" / "scripts" / "pr_flow.py"
RST = ROOT / "skills" / "commit-slice" / "scripts" / "resolve_sync_target.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp_path, with_uat):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    if with_uat:
        _git(repo, "branch", "uat")
    return repo


# ── resolve_sync_target resolves uat ─────────────────────────────────────────
@gitok
def test_resolve_sync_target_resolves_uat_when_present(tmp_path):
    repo = _repo(tmp_path, with_uat=True)
    r = subprocess.run([sys.executable, str(RST), "--repo-root", str(repo), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout.strip().splitlines()[-1])
    assert plan["integration_branch"] == "uat"


@gitok
def test_resolve_sync_target_degrades_to_default_when_uat_absent(tmp_path):
    repo = _repo(tmp_path, with_uat=False)
    r = subprocess.run([sys.executable, str(RST), "--repo-root", str(repo), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout.strip().splitlines()[-1])
    assert plan["integration_branch"] == "master"


# ── pr_flow bases the PR on uat ──────────────────────────────────────────────
@gitok
def test_pr_flow_bases_pr_on_uat_when_present(tmp_path):
    """With a working origin but no gh, pr_flow pushes then degrades to the
    `gh pr create --base <resolved>` hint; with uat present that base must be uat."""
    repo = _repo(tmp_path, with_uat=True)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "master")
    _git(repo, "push", "origin", "uat")
    _git(repo, "checkout", "-b", "slice/001-x")
    (repo / "f.txt").write_text("slice\n", encoding="utf-8")
    _git(repo, "commit", "-am", "slice edit")
    r = subprocess.run([sys.executable, str(PRF), "--confirmed", "--branch", "slice/001-x",
                        "--repo-root", str(repo), "--json"], capture_output=True, text=True)
    out = r.stdout + r.stderr
    assert "base uat" in out, out
    assert "base master" not in out, "PR must base on the integration branch (uat), not master"
