"""The two-line stdout contract, and the fail-closed diff base (slice-069 / ADR-072).

This file exists because a SEALED ADR told the builder to do the wrong thing.

ADR-069 (C) instructed: *"the `| head -1` pipe that masks `_worktree_paths.py`'s honest exit-2 is
removed"*. It is not a mask. `_worktree_paths.py` prints TWO lines — the worktree PATH on line 1 and
the BRANCH on line 2 — and the pipe SELECTS the path. Roughly a dozen SKILL.md call sites silently
depend on that, through an entirely undocumented positional contract. Had the instruction been
followed, every one of those sites would have handed `git -C "$wt"` a two-line string on the OWNER's
happy path, and `slice_diff_base.py` — which used to exit 0 and print `HEAD` for ANY unusable
worktree — would have turned that into `git diff HEAD...HEAD`: an EMPTY DIFF, i.e. a confident
"no code changes" review. The remedy for a false green would have manufactured a new one.

So: the contract is pinned here, and the fail-open that made it lethal is closed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WT_PATHS = REPO / "scripts" / "lib" / "_worktree_paths.py"
DIFF_BASE = REPO / "scripts" / "lib" / "slice_diff_base.py"
SLICE_FOLDER = "slice-042-thread-worktree-ctx-into-critic-prompt"   # any well-formed name


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


# ── _worktree_paths.py: the two-line stdout contract ~12 call sites depend on ────────────────────

def test_default_stdout_is_exactly_two_lines_path_then_branch() -> None:
    cp = _run(WT_PATHS, "--slice-folder", SLICE_FOLDER, "--repo-root", str(REPO))
    assert cp.returncode == 0
    lines = cp.stdout.strip().splitlines()
    assert len(lines) == 2, f"the two-line contract is BROKEN: {lines!r}"
    assert lines[0].endswith(SLICE_FOLDER), lines[0]      # line 1 = the worktree PATH
    assert lines[1] == "slice/042-thread-worktree-ctx-into-critic-prompt", lines[1]   # line 2 = BRANCH


def test_print_path_emits_the_path_alone() -> None:
    """The safe way to take one value: it keeps the command's EXIT STATUS, which `| head -1` destroys
    (a pipeline's status is the LAST command's — so `head`'s 0 masks this script's honest 2)."""
    cp = _run(WT_PATHS, "--slice-folder", SLICE_FOLDER, "--repo-root", str(REPO), "--print", "path")
    assert cp.returncode == 0
    assert len(cp.stdout.strip().splitlines()) == 1
    assert cp.stdout.strip().endswith(SLICE_FOLDER)


def test_print_branch_emits_the_branch_alone() -> None:
    cp = _run(WT_PATHS, "--slice-folder", SLICE_FOLDER, "--repo-root", str(REPO), "--print", "branch")
    assert cp.returncode == 0
    assert cp.stdout.strip() == "slice/042-thread-worktree-ctx-into-critic-prompt"


def test_a_bad_slice_folder_still_exits_nonzero_with_empty_stdout() -> None:
    cp = _run(WT_PATHS, "--slice-folder", "", "--repo-root", str(REPO), "--print", "path")
    assert cp.returncode != 0
    assert cp.stdout.strip() == ""          # so a `$( )` capture goes EMPTY and the guard fires


# ── slice_diff_base.py: an unusable worktree must NEVER become a clean empty diff ────────────────

def test_empty_worktree_is_refused_not_silently_the_cwd() -> None:
    """THE trap: argparse's `type=Path` turned '' into `Path('.')` — truthy, `is_dir()` True — so the
    guard ran against the CURRENT DIRECTORY and passed. The arg is now validated as a RAW STRING."""
    cp = _run(DIFF_BASE, "--worktree", "")
    assert cp.returncode != 0, "an empty --worktree resolved a base -- the fail-open is back"
    assert cp.stdout.strip() == ""


def test_two_line_worktree_is_refused() -> None:
    """Exactly what ADR-069's 'drop the pipe' instruction would have produced at ~12 call sites."""
    cp = _run(DIFF_BASE, "--worktree", "C:/nowhere\nslice/042-x")
    assert cp.returncode != 0
    assert cp.stdout.strip() == ""


def test_nonexistent_worktree_is_refused() -> None:
    cp = _run(DIFF_BASE, "--worktree", str(REPO / "definitely-not-a-worktree"))
    assert cp.returncode != 0
    assert cp.stdout.strip() == ""


def test_a_real_worktree_still_resolves_a_base(tmp_path: Path) -> None:
    """No regression: the generous HEAD fallback is intact for a REAL worktree — it just may no
    longer stand in for a worktree that does not exist."""
    r = tmp_path / "repo"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, check=True)
    run("init")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (r / "f.txt").write_text("x", encoding="utf-8")
    run("add", "-A")
    run("commit", "-m", "init")
    cp = _run(DIFF_BASE, "--worktree", str(r))
    assert cp.returncode == 0
    assert cp.stdout.strip()          # a SHA, or the HEAD fallback — but never empty
