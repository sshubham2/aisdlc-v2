"""scripts/lib/active_slice.py — in-flight slice resolution (4.4 priority d).

Resolution precedence: git branch (slice/NNN-name) -> vault scan (non-terminal
preferred, then most-recent) -> None. The vault-scan tests pass a non-git repo_root
so `_git_branch` returns None and resolution falls through to the scan.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id


def _make_slice(vault, folder, *, stage=None, at=None):
    d = vault / "slices" / folder
    d.mkdir(parents=True)
    m = {}
    if stage is not None:
        m["stage"] = stage
    if at is not None:
        m["at"] = at
    if m:
        (d / "milestone.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def test_none_when_no_slices(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    assert resolve_active_slice(vault, repo_root=tmp_path) is None


def test_vault_scan_prefers_non_terminal(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-001-done", stage="complete", at="2026-01-01")
    _make_slice(vault, "slice-002-wip", stage="build", at="2026-01-02")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info is not None
    assert info["slice"] == "slice-002"
    assert info["source"] == "vault-scan"


def test_vault_scan_most_recent_among_non_terminal(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-003-a", stage="design", at="2026-03-01")
    _make_slice(vault, "slice-004-b", stage="design", at="2026-03-09")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["slice"] == "slice-004"


def test_all_terminal_falls_back_to_most_recent(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-005-a", stage="complete", at="2026-05-01")
    _make_slice(vault, "slice-006-b", stage="complete", at="2026-05-09")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["slice"] == "slice-006"


def test_archive_dir_excluded(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "slices" / "archive").mkdir(parents=True)
    _make_slice(vault, "slice-007-a", stage="build", at="2026-07-01")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["slice"] == "slice-007"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_branch_resolution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "tester")
    git("commit", "--allow-empty", "-m", "init")
    git("checkout", "-b", "slice/009-feature")

    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-009-feature", stage="build", at="2026-09-01")
    info = resolve_active_slice(vault, repo_root=repo)
    assert info is not None
    assert info["slice"] == "slice-009"
    assert info["source"] == "git-branch"


def test_folder_only_cli(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-010-x", stage="build", at="2026-10-01")
    r = run_script(
        "scripts/lib/active_slice.py",
        ["--vault", str(vault), "--repo-root", str(tmp_path), "--folder-only"],
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "slice-010-x"


# ── resolve_slice_by_id (archive-aware by-id lookup for /slice-story; B1) ─────────

def test_resolve_by_id_active(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-011-feature", stage="build")
    info = resolve_slice_by_id(vault, "slice-011")
    assert info is not None
    assert info["slice"] == "slice-011"
    assert info["source"] == "by-id-active"


def test_resolve_by_id_finds_archived(tmp_path):
    # a shipped slice lives under slices/archive/ — where /reflect moved it BEFORE
    # /commit-slice's on-ship auto-emit runs (the B1 case resolve_active_slice misses).
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-012-shipped", stage="complete")
    info = resolve_slice_by_id(vault, "slice-012")
    assert info is not None
    assert info["slice"] == "slice-012"
    assert info["source"] == "by-id-archive"
    assert "archive" in info["path"].replace("\\", "/")


def test_resolve_by_id_active_preferred_over_archive(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-013-active", stage="build")
    _make_slice(vault, "archive/slice-013-old", stage="complete")
    info = resolve_slice_by_id(vault, "slice-013")
    assert info["source"] == "by-id-active"  # slices/ searched before slices/archive/


def test_resolve_by_id_matches_full_folder_name(tmp_path):
    # the id is given as the canonical slice-NNN, but slice-NNN-<name> must also match
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-014-some-name", stage="complete")
    assert resolve_slice_by_id(vault, "slice-014")["slice"] == "slice-014"
    assert resolve_slice_by_id(vault, "slice-014-some-name")["slice"] == "slice-014"


def test_resolve_by_id_none_when_missing(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-015-x", stage="build")
    assert resolve_slice_by_id(vault, "slice-099") is None
    assert resolve_slice_by_id(vault, "not-a-slice") is None


def test_resolve_by_id_absolute_path(tmp_path):
    # M4: the returned path is absolute so the on-ship write target is cwd-independent.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-016-z", stage="complete")
    info = resolve_slice_by_id(vault, "slice-016")
    assert Path(info["path"]).is_absolute()


def test_slice_cli_resolves_archive(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-017-shipped", stage="complete")
    r = run_script(
        "scripts/lib/active_slice.py",
        ["--vault", str(vault), "--slice", "slice-017", "--path-only"],
    )
    assert r.returncode == 0
    assert r.stdout.strip().replace("\\", "/").endswith("slices/archive/slice-017-shipped")
