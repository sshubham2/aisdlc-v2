"""release_cut (slice-022 AC4) — the atomic uat->master release transaction.

Proves on REAL git (like spike-release-cut-atomicity) that:
  * it REFUSES on a dirty target tree (B2) without touching master;
  * a happy-path cut lands the merge + bump + changelog as exactly ONE first-parent
    commit and syncs uat back;
  * a uat-not-ahead run is a clean no-op (M2);
  * ANY pre-commit failure rolls back via the captured pre-merge SHA so master is
    byte-identical (M2/m3) and the tree is clean.

The git seam is REAL (run_git); only the bump + changelog steps are injected, so
the atomicity is tested honestly without the full changelog machinery.

TF-1: written FAILING before the impl.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib._git_default_branch import run_git  # noqa: E402

RC = ROOT / "skills" / "release" / "scripts" / "release_cut.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _load():
    spec = importlib.util.spec_from_file_location("release_cut", RC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_cut"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _set_version(repo, v):
    p = repo / ".claude-plugin" / "plugin.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": "ai-sdlc", "version": v}, indent=2) + "\n", encoding="utf-8")


def _repo(tmp_path, uat_ahead=True, integration="uat", genesis=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    _set_version(repo, "2.35.1")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 2.35.1\n- base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base 2.35.1")
    # slice-061: release-genesis marks an ai-sdlc-managed repo -> legacy `uat` resolves.
    if genesis:
        _git(repo, "tag", "release-genesis")
    _git(repo, "branch", integration)
    if uat_ahead:
        _git(repo, "checkout", integration)
        (repo / "feat.py").write_text("slice work\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"slice work on {integration}")
        _git(repo, "checkout", "master")
    return repo


def _master_sha(repo):
    return run_git(repo, "rev-parse", "master").stdout.strip()


def _fake_bump(repo, version, level):
    _set_version(Path(repo), version)
    return (True, version)


def _fake_changelog(repo, version):
    p = Path(repo) / "CHANGELOG.md"
    p.write_text(p.read_text(encoding="utf-8") + f"\n## {version}\n- release\n", encoding="utf-8")
    return (True, "ok")


def _failing_changelog(repo, version):
    return (False, "simulated changelog failure")


# ── happy path: one commit, version bumped, uat synced ───────────────────────
@gitok
def test_happy_path_one_commit(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=True)
    before = _master_sha(repo)
    r = rc.run_release_cut(repo, "2.36.0", git=run_git, bump=_fake_bump, changelog=_fake_changelog)
    assert r["action"] == "released", r
    assert r["exit_code"] == 0
    # exactly ONE first-parent commit advanced master
    fp = run_git(repo, "rev-list", "--first-parent", f"{before}..master").stdout.split()
    assert len(fp) == 1, fp
    # the release commit is a 2-parent merge touching plugin.json + CHANGELOG
    parents = run_git(repo, "rev-list", "--parents", "-n", "1", "master").stdout.split()
    assert len(parents) == 3, "release commit must be a 2-parent merge"
    names = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-m",
                    "--first-parent", "master").stdout.split()
    assert ".claude-plugin/plugin.json" in names and "CHANGELOG.md" in names
    ver = json.loads((repo / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert ver == "2.36.0"
    assert r.get("sync_back") == "ok"
    # uat now contains the release
    assert run_git(repo, "merge-base", "--is-ancestor", "master", "uat").returncode == 0


# ── slice-061 AC3: with aisdlc-uat present, the cut targets the namespaced branch ─
@gitok
def test_resolves_and_merges_aisdlc_uat(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=True, integration="aisdlc-uat")
    before = _master_sha(repo)
    r = rc.run_release_cut(repo, "2.36.0", git=run_git, bump=_fake_bump, changelog=_fake_changelog)
    assert r["action"] == "released", r
    assert r["source"] == "aisdlc-uat", r
    fp = run_git(repo, "rev-list", "--first-parent", f"{before}..master").stdout.split()
    assert len(fp) == 1, fp
    assert run_git(repo, "merge-base", "--is-ancestor", "master", "aisdlc-uat").returncode == 0


# ── B2: refuse on a dirty tree, master untouched ─────────────────────────────
@gitok
def test_refuse_on_dirty_tree(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=True)
    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")  # dirty the tree
    before = _master_sha(repo)
    r = rc.run_release_cut(repo, "2.36.0", git=run_git, bump=_fake_bump, changelog=_fake_changelog)
    assert r["action"] == "refuse-dirty", r
    assert r["exit_code"] != 0
    assert _master_sha(repo) == before  # master untouched


# ── atomic: a pre-commit failure rolls back to the captured SHA ──────────────
@gitok
def test_changelog_failure_rolls_back(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=True)
    before = _master_sha(repo)
    r = rc.run_release_cut(repo, "2.36.0", git=run_git, bump=_fake_bump, changelog=_failing_changelog)
    assert r["action"] == "changelog-failed", r
    assert r["exit_code"] != 0
    assert _master_sha(repo) == before, "master ref must be byte-identical after a rolled-back cut"
    # tree must be clean (not just the ref) -- the slice-009/spike lesson
    assert run_git(repo, "status", "--porcelain").stdout.strip() == "", "tree must be clean after rollback"
    # plugin.json restored to the pre-cut version
    ver = json.loads((repo / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert ver == "2.35.1"


# ── M2: uat-not-ahead -> clean no-op ─────────────────────────────────────────
@gitok
def test_uat_not_ahead_no_op(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=False)  # uat == master
    before = _master_sha(repo)
    r = rc.run_release_cut(repo, "2.36.0", git=run_git, bump=_fake_bump, changelog=_fake_changelog)
    assert r["action"] == "no-op", r
    assert r["exit_code"] == 0
    assert _master_sha(repo) == before


# ── CR1: a --level run uses the version the bump hook RESOLVES, not literal None ──
@gitok
def test_level_run_uses_resolved_version(tmp_path):
    rc = _load()
    repo = _repo(tmp_path, uat_ahead=True)

    def level_bump(repo_, version, lvl):
        # mirror bump_plugin_version --level: new_version is None, compute + return resolved
        assert version is None and lvl == "minor"
        _set_version(Path(repo_), "2.36.0")
        return (True, "2.36.0")

    seen = {}

    def cl(repo_, version):
        seen["version"] = version
        return _fake_changelog(repo_, version)

    r = rc.run_release_cut(repo, None, level="minor", git=run_git, bump=level_bump, changelog=cl)
    assert r["action"] == "released", r
    assert r["new_version"] == "2.36.0"
    assert seen["version"] == "2.36.0", "changelog must get the resolved version, not None"
    msg = run_git(repo, "log", "-1", "--format=%s").stdout
    assert "2.36.0" in msg and "None" not in msg, msg
