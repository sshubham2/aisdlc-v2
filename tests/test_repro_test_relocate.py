"""Behavioral tests for scripts/lib/repro_test_relocate.py (slice-018, AC1).

Asserts the relocation helper targets ONLY the active slice's explicitly-named
repro test and NEVER sweeps a sibling slice's untracked tests/bugs/* into the
worktree -- the confused-deputy bug this slice fixes. These are BEHAVIORAL, not
import-error reds: a globbing / enumerate-and-grab implementation would relocate
the sibling and FAIL `test_relocate_targets_only_named_test_not_sibling`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]  # tests/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib._worktree_paths import canonical_worktree_path
from scripts.lib.repro_test_relocate import relocate_repro_test


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def two_sibling_repros(tmp_path):
    """A real git repo + a linked worktree, with TWO untracked tests/bugs/ files on main."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    slice_folder = "slice-999-fix-thing"
    wt = canonical_worktree_path(slice_folder, repo)
    _git(repo, "worktree", "add", str(wt), "-b", "slice/999-fix-thing", "main")
    bugs = repo / "tests" / "bugs"
    bugs.mkdir(parents=True)
    (bugs / "test_a.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    (bugs / "test_b.py").write_text("def test_b():\n    assert False\n", encoding="utf-8")
    return repo, wt, slice_folder


def test_relocate_targets_only_named_test_not_sibling(two_sibling_repros):
    repo, wt, slice_folder = two_sibling_repros
    rc = relocate_repro_test(slice_folder, repo, "tests/bugs/test_a.py")
    assert rc == 0
    # the named test moved into the worktree and off main
    assert (wt / "tests" / "bugs" / "test_a.py").exists()
    assert not (repo / "tests" / "bugs" / "test_a.py").exists()
    # the SIBLING is untouched: still on main, NEVER swept into the worktree
    assert (repo / "tests" / "bugs" / "test_b.py").exists()
    assert not (wt / "tests" / "bugs" / "test_b.py").exists()
    # the moved test is staged on the slice branch
    staged = _git(wt, "diff", "--cached", "--name-only").stdout
    assert "tests/bugs/test_a.py" in staged


def test_relocate_missing_named_source_fails_visibly(two_sibling_repros):
    repo, wt, slice_folder = two_sibling_repros
    # a grant naming a file that does not exist on main is a fail-visible error (exit 1),
    # never a silent success (must-not-defer: error paths fail visibly).
    rc = relocate_repro_test(slice_folder, repo, "tests/bugs/test_does_not_exist.py")
    assert rc == 1
    # the siblings are left completely untouched
    assert (repo / "tests" / "bugs" / "test_a.py").exists()
    assert (repo / "tests" / "bugs" / "test_b.py").exists()


def test_relocate_missing_worktree_fails_visibly(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    bugs = repo / "tests" / "bugs"
    bugs.mkdir(parents=True)
    (bugs / "test_a.py").write_text("def test_a():\n    assert False\n", encoding="utf-8")
    # no worktree was created for slice-999 -> exit 2 (usage / worktree absent), never a silent move
    rc = relocate_repro_test("slice-999-fix-thing", repo, "tests/bugs/test_a.py")
    assert rc == 2
    assert (repo / "tests" / "bugs" / "test_a.py").exists()


def test_relocate_rejects_path_traversal(two_sibling_repros):
    repo, wt, slice_folder = two_sibling_repros
    # a --test-path with '..' must be refused (exit 2), never resolved outside the tree (CR1 defense-in-depth)
    rc = relocate_repro_test(slice_folder, repo, "tests/bugs/../../escape.py")
    assert rc == 2
    # nothing was moved
    assert (repo / "tests" / "bugs" / "test_a.py").exists()
    assert (repo / "tests" / "bugs" / "test_b.py").exists()
