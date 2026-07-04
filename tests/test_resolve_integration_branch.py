"""resolve_integration_branch (slice-022 AC1) — the integration-branch resolver.

`resolve_integration_branch(repo_root)` is the single fact "which branch does new
slice work integrate onto" under the uat/master release model: 'uat' when that
branch exists, else it degrades VISIBLY to the resolved default branch (logging
why), and returns None only when git is unusable. The `--integration` CLI exposes
it to SKILL.md call sites; `--integration --write` is the WRITE-path guard (M3):
it REFUSES (non-zero) when uat is absent rather than letting a write fall back to
the released trunk.

TF-1: these are written FAILING before the impl exists.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib._git_default_branch import resolve_integration_branch  # noqa: E402

CLI = ROOT / "scripts" / "lib" / "_git_default_branch.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp_path, with_uat=False, with_aisdlc_uat=False, genesis=True):
    """A test repo. ``genesis`` tags ``release-genesis`` at the base — i.e. an ai-sdlc-managed
    repo (the M2 discriminator). ``with_uat`` alone + ``genesis=True`` is the legacy back-compat
    state; ``with_uat`` + ``genesis=False`` is a FRESH host whose own `uat` must NOT be claimed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "base")
    if genesis:
        _git(repo, "tag", "release-genesis")
    if with_uat:
        _git(repo, "branch", "uat")
    if with_aisdlc_uat:
        _git(repo, "branch", "aisdlc-uat")
    return repo


def _cli(repo, *args):
    return subprocess.run([sys.executable, str(CLI), "--repo-root", str(repo), *args],
                          capture_output=True, text=True)


# ── the function ─────────────────────────────────────────────────────────────
@gitok
def test_aisdlc_uat_present_returns_aisdlc_uat(tmp_path):
    # slice-061 AC1: the namespaced integration branch wins (self-identifying by name).
    repo = _repo(tmp_path, with_aisdlc_uat=True)
    assert resolve_integration_branch(repo) == "aisdlc-uat"


@gitok
def test_both_present_prefers_aisdlc_uat(tmp_path):
    # slice-061 AC1: aisdlc-uat takes precedence over the legacy uat during the transition.
    repo = _repo(tmp_path, with_uat=True, with_aisdlc_uat=True)
    assert resolve_integration_branch(repo) == "aisdlc-uat"


@gitok
def test_legacy_uat_present_returns_uat(tmp_path):
    # Back-compat: an ai-sdlc-managed repo (release-genesis tag) still resolves legacy uat.
    repo = _repo(tmp_path, with_uat=True)  # genesis=True by default
    assert resolve_integration_branch(repo) == "uat"


@gitok
def test_fresh_host_uat_without_genesis_degrades(tmp_path):
    # M2 / M-add-2: a FRESH host with its own `uat` and NO release-genesis tag must NOT
    # be claimed as the integration branch -> the legacy arm is rejected -> degrade to trunk.
    repo = _repo(tmp_path, with_uat=True, genesis=False)
    assert resolve_integration_branch(repo) == "master"


@gitok
def test_uat_absent_degrades_to_default(tmp_path):
    repo = _repo(tmp_path, with_uat=False)
    assert resolve_integration_branch(repo) == "master"


@gitok
def test_uat_absent_logs_visible_degrade(tmp_path, capsys):
    repo = _repo(tmp_path, with_uat=False)
    resolve_integration_branch(repo)
    err = capsys.readouterr().err
    assert "uat" in err and ("absent" in err.lower() or "default" in err.lower())


def test_git_unusable_returns_none(tmp_path, monkeypatch):
    # "git unusable" = resolve_default_branch itself whiffs (binary missing / all
    # probes fail incl. no global init.defaultBranch). Simulate the leaf returning
    # None and assert we propagate None (never invent a branch).
    import scripts.lib._git_default_branch as mod
    notgit = tmp_path / "plain"
    notgit.mkdir()
    monkeypatch.setattr(mod, "resolve_default_branch", lambda r: None)
    assert mod.resolve_integration_branch(notgit) is None


# ── existing_integration_branch (the non-degrading precedence leaf) ───────────
@gitok
def test_existing_integration_branch_precedence(tmp_path):
    import scripts.lib._git_default_branch as mod

    def _sub(name):
        d = tmp_path / name
        d.mkdir()
        return d

    assert mod.existing_integration_branch(_repo(_sub("a"), with_aisdlc_uat=True)) == "aisdlc-uat"
    assert mod.existing_integration_branch(_repo(_sub("b"), with_uat=True)) == "uat"
    # fresh host: uat, no genesis -> NOT an integration branch (never degrades to trunk here)
    assert mod.existing_integration_branch(_repo(_sub("c"), with_uat=True, genesis=False)) is None
    # neither branch -> None
    assert mod.existing_integration_branch(_repo(_sub("d"), with_uat=False)) is None


@gitok
def test_legacy_uat_note_once_per_process(tmp_path, capsys, monkeypatch):
    # m4 / M2: legacy uat is a VALID resolution (not a degrade) -> the note fires at most
    # ONCE per process so frequent read-only callers stay quiet during the transition window.
    import scripts.lib._git_default_branch as mod
    monkeypatch.setattr(mod, "_LEGACY_NOTE_EMITTED", False)
    repo = _repo(tmp_path, with_uat=True)
    mod.resolve_integration_branch(repo)
    first = capsys.readouterr().err
    mod.resolve_integration_branch(repo)
    second = capsys.readouterr().err
    assert "legacy" in first.lower() and "uat" in first
    assert second.strip() == "", f"legacy note must fire once per process, got: {second!r}"


# ── the CLI ──────────────────────────────────────────────────────────────────
@gitok
def test_cli_integration_prints_uat(tmp_path):
    repo = _repo(tmp_path, with_uat=True)
    r = _cli(repo, "--integration")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "uat"


@gitok
def test_cli_integration_degrades_to_default(tmp_path):
    repo = _repo(tmp_path, with_uat=False)
    r = _cli(repo, "--integration")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "master"


@gitok
def test_cli_write_refuses_when_uat_absent(tmp_path):
    """M3: the WRITE path must REFUSE (non-zero) on a uat-absent degrade, never fall back to master."""
    repo = _repo(tmp_path, with_uat=False)
    r = _cli(repo, "--integration", "--write")
    assert r.returncode != 0
    assert "uat" in r.stderr.lower() and "refus" in r.stderr.lower()
    assert r.stdout.strip() == ""  # no master leaked on stdout


@gitok
def test_cli_write_ok_when_uat_present(tmp_path):
    repo = _repo(tmp_path, with_uat=True)
    r = _cli(repo, "--integration", "--write")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "uat"


@gitok
def test_cli_integration_prints_aisdlc_uat(tmp_path):
    repo = _repo(tmp_path, with_aisdlc_uat=True)
    r = _cli(repo, "--integration")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "aisdlc-uat"


@gitok
def test_cli_write_ok_when_aisdlc_uat_present(tmp_path):
    repo = _repo(tmp_path, with_aisdlc_uat=True)
    r = _cli(repo, "--integration", "--write")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "aisdlc-uat"


@gitok
def test_cli_write_refuses_on_fresh_host_uat(tmp_path):
    """M-add-2: a host's own `uat` with NO release-genesis tag is a trunk-degrade for the
    WRITE guard -> REFUSE (exit 3), never merge slice work into the host's branch."""
    repo = _repo(tmp_path, with_uat=True, genesis=False)
    r = _cli(repo, "--integration", "--write")
    assert r.returncode == 3, (r.stdout, r.stderr)
    assert "aisdlc-uat" in r.stderr and "refus" in r.stderr.lower()
    assert r.stdout.strip() == ""  # no branch leaked on stdout


@gitok
def test_cli_integration_git_unusable_exit2(tmp_path):
    # neutralize the global/system git config so init.defaultBranch can't resolve,
    # then point at a non-repo dir -> every probe whiffs -> None -> CLI exit 2.
    notgit = tmp_path / "plain"
    notgit.mkdir()
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_SYSTEM": str(empty)}
    r = subprocess.run([sys.executable, str(CLI), "--repo-root", str(notgit), "--integration"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2, (r.stdout, r.stderr)
