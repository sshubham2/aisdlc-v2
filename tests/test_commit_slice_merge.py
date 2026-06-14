"""M2 — characterize the shared rebase section so the extraction can't silently
regress `--merge`.

slice-008 lifts the PSQ-3 rebase + PCR-2b conflict gate out of `--merge` 5b into a
SHARED SKILL.md section that BOTH `--merge` (5b) and the new `--push` (5c) reference.
The design's whole rationale for keeping the rebase in PROSE (not extracting a
slice_rebase.py helper) is that extraction risks regressing `--merge`'s proven hot
path — but that rationale needs a backing test. The load-bearing behavior the shared
section relies on is ``parallel_conflict_resolver.py``'s conflict CLASSIFICATION,
invoked UNCHANGED by both modes:

  * a real rebase conflict -> ``conflict_class: HARD`` (enter the PCR-2b hand-resolve
    gate; v2 NEVER auto-resolves), and
  * no rebase in progress -> ``conflict_class: UNKNOWN`` (fail-closed -> SOAD-1 block).

If a future edit to the shared section changed which classifier verb runs (or the
classifier's contract drifted), this test fails — that is the regression backstop.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import load_script  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = "skills/commit-slice/scripts/parallel_conflict_resolver.py"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _make_conflict_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "slice/001-x")
    (repo / "f.txt").write_text("slice change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "slice edit")
    _git(repo, "checkout", "master")
    (repo / "f.txt").write_text("master change\n", encoding="utf-8")
    _git(repo, "commit", "-am", "master edit")
    _git(repo, "checkout", "slice/001-x")
    # rebase onto master -> conflict on f.txt (left in-progress for the classifier)
    subprocess.run(["git", "-C", str(repo), "rebase", "master"], capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_m2_merge_characterization_shared_rebase_section(run_script, tmp_path):
    repo = tmp_path / "repo"
    _make_conflict_repo(repo)

    # mid-rebase conflict -> HARD (PCR-2b gate engages; nothing is auto-resolved)
    r = run_script(RESOLVER, ["--classify", "--json", "--repo-root", str(repo)])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["conflict_class"] == "HARD"

    # the U-file is still unmerged — the classifier did NOT resolve it for us
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True)
    assert any(line.startswith(("UU", "AA", "DD")) for line in status.stdout.splitlines()), \
        "expected an unmerged path mid-rebase (classifier must not auto-resolve)"

    subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)

    # no rebase in progress -> UNKNOWN (fail-closed)
    r2 = run_script(RESOLVER, ["--classify", "--json", "--repo-root", str(repo)])
    assert json.loads(r2.stdout)["conflict_class"] == "UNKNOWN"
