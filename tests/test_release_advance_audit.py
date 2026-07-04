"""release_advance_audit (slice-022 AC4 teeth) — "master advanced ONLY via a
versioned release cut", proven over real git history.

The audit walks ``git rev-list --first-parent <GENESIS>..master`` from a DURABLE
recorded genesis (a ``release-genesis`` tag), NOT the live merge-base(uat,master)
which would advance each release and collapse the window (M1). Every first-parent
master advance since genesis must change ``.claude-plugin/plugin.json``'s version
line; an unbumped advance (or a split bump/changelog) is flagged. It also asserts
uat descends from the recorded genesis (M4). NO-OP PASS on a non-methodology repo.

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

AUDIT = ROOT / "scripts" / "lib" / "release_advance_audit.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _load():
    spec = importlib.util.spec_from_file_location("release_advance_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_advance_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _set_version(repo, v):
    p = repo / ".claude-plugin" / "plugin.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": "ai-sdlc", "version": v}, indent=2) + "\n", encoding="utf-8")


def _append_changelog(repo, line):
    p = repo / "CHANGELOG.md"
    prev = p.read_text(encoding="utf-8") if p.exists() else "# Changelog\n"
    p.write_text(prev + line + "\n", encoding="utf-8")


def _base_repo(tmp_path, tag_genesis=True):
    """A methodology repo at 2.35.1 on master, with a release-genesis tag at the base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    _set_version(repo, "2.35.1")
    _append_changelog(repo, "## 2.35.1\n- base")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base 2.35.1")
    if tag_genesis:
        _git(repo, "tag", "release-genesis")
    return repo


def _release_cut(repo, new_version, source="uat"):
    """Simulate a proper release: merge source -> master + bump + changelog in ONE commit."""
    _git(repo, "checkout", "master")
    _git(repo, "merge", "--no-ff", "--no-commit", source)
    _set_version(repo, new_version)
    _append_changelog(repo, f"## {new_version}\n- release")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"release {new_version}")


def _run(repo, *args):
    return subprocess.run([sys.executable, str(AUDIT), "--root", str(repo), *args],
                          capture_output=True, text=True)


# ── clean: a genuine release cut passes ──────────────────────────────────────
@gitok
def test_genuine_release_passes(tmp_path):
    repo = _base_repo(tmp_path)
    _git(repo, "branch", "uat")
    _git(repo, "checkout", "uat")
    (repo / "feat.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "slice work")
    _release_cut(repo, "2.36.0")
    r = _run(repo, "--json")
    assert r.returncode == 0, r.stdout + r.stderr


# ── flagged: a hand unbumped master advance ──────────────────────────────────
@gitok
def test_hand_unbumped_advance_flagged(tmp_path):
    repo = _base_repo(tmp_path)
    (repo / "hotfix.py").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "hand hotfix, no bump")
    r = _run(repo, "--json")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "version" in (r.stdout + r.stderr).lower()


# ── flagged: a split bump/changelog (changelog-only commit changes no version) ─
@gitok
def test_split_changelog_only_flagged(tmp_path):
    repo = _base_repo(tmp_path)
    _set_version(repo, "2.36.0")
    _git(repo, "add", ".claude-plugin/plugin.json")
    _git(repo, "commit", "-m", "bump only")
    _append_changelog(repo, "## 2.36.0\n- split")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-m", "changelog only")
    r = _run(repo, "--json")
    assert r.returncode == 1, r.stdout + r.stderr


# ── M1: the window does NOT collapse after a release ─────────────────────────
@gitok
def test_window_does_not_collapse_after_release(tmp_path):
    """After a genuine release (master advanced, merge-base would move forward),
    a SUBSEQUENT hand unbumped advance must STILL be flagged — proving the audit
    walks from the durable genesis, not the live merge-base."""
    repo = _base_repo(tmp_path)
    _git(repo, "branch", "uat")
    _git(repo, "checkout", "uat")
    (repo / "feat.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A"); _git(repo, "commit", "-m", "slice work")
    _release_cut(repo, "2.36.0")          # a real release advances master past genesis
    # now a hand unbumped advance on master AFTER the release
    _git(repo, "checkout", "master")
    (repo / "sneaky.py").write_text("z\n", encoding="utf-8")
    _git(repo, "add", "-A"); _git(repo, "commit", "-m", "sneaky unbumped advance after release")
    r = _run(repo, "--json")
    assert r.returncode == 1, "post-release unbumped advance must still be flagged (window must not collapse)"


# ── M4: uat must descend from the recorded genesis ───────────────────────────
@gitok
def test_uat_rooted_at_genesis_passes(tmp_path):
    repo = _base_repo(tmp_path)
    _git(repo, "branch", "uat")  # from master == genesis
    r = _run(repo, "--json")
    assert r.returncode == 0, r.stdout + r.stderr


@gitok
def test_uat_not_descending_from_genesis_flagged(tmp_path):
    repo = _base_repo(tmp_path, tag_genesis=False)
    # advance master, THEN tag genesis ahead of where uat will be rooted
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A"); _git(repo, "commit", "-m", "advance")
    _set_version(repo, "2.36.0")
    _git(repo, "commit", "-am", "bump 2.36.0")
    _git(repo, "tag", "release-genesis")        # genesis is HERE
    # uat rooted at the OLD base (does not descend from genesis)
    _git(repo, "branch", "uat", "HEAD~2")
    r = _run(repo, "--json")
    assert r.returncode == 1, "uat not descending from genesis must be flagged (M4)"


# ── slice-061 AC2: the M4 check resolves the namespaced integration branch ───
@gitok
def test_aisdlc_uat_rooted_at_genesis_passes(tmp_path):
    repo = _base_repo(tmp_path)
    _git(repo, "branch", "aisdlc-uat")  # from master == genesis
    r = _run(repo, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout).get("integration_branch_checked") == "aisdlc-uat"


@gitok
def test_aisdlc_uat_not_descending_from_genesis_flagged(tmp_path):
    repo = _base_repo(tmp_path, tag_genesis=False)
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A"); _git(repo, "commit", "-m", "advance")
    _set_version(repo, "2.36.0")
    _git(repo, "commit", "-am", "bump 2.36.0")
    _git(repo, "tag", "release-genesis")        # genesis is HERE
    _git(repo, "branch", "aisdlc-uat", "HEAD~2")  # rooted before genesis
    r = _run(repo, "--json")
    assert r.returncode == 1, "aisdlc-uat not descending from genesis must be flagged (M4)"
    kinds = [v["kind"] for v in json.loads(r.stdout)["violations"]]
    assert "integration-genesis-mismatch" in kinds, kinds


# ── NO-OP on a non-methodology repo ──────────────────────────────────────────
@gitok
def test_noop_on_non_methodology_repo(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A"); _git(repo, "commit", "-m", "x")
    r = _run(repo, "--json")
    assert r.returncode == 0, "non-methodology repo (no plugin.json) -> NO-OP PASS"


# ── genesis ref absent -> fail-visible exit 2 ────────────────────────────────
@gitok
def test_genesis_absent_exit2(tmp_path):
    repo = _base_repo(tmp_path, tag_genesis=False)
    r = _run(repo, "--json")
    assert r.returncode == 2, "missing genesis ref must fail-visible (exit 2)"
    assert "genesis" in (r.stdout + r.stderr).lower()
