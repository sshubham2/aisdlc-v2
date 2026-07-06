"""pre_finish_gate hardening (review sweep 2026-07):

1. An invalid --worktree is a HARD exit-2 usage error IN THE SCRIPT — it used to
   silently fall back to Path.cwd(), i.e. a green gate that audited the WRONG tree,
   with the only guard living in the SKILL's bash prose.
2. --changed-from-git derives the changed/changed-test lists inside the gate
   (removing the cross-bash-block model-memory transcription), is mutually
   exclusive with the explicit lists, and fails visibly on a bad git base.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "build-slice" / "scripts"))

import pre_finish_gate  # noqa: E402


def test_invalid_worktree_exit2_no_cwd_fallback(tmp_path, capsys):
    rc = pre_finish_gate.main([
        "--slice", str(tmp_path), "--worktree", str(tmp_path / "no-such-dir")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "refusing" in err.lower()


def test_run_gate_refuses_invalid_worktree_directly(tmp_path):
    # the API-level belt: run_gate itself raises (no silent cwd fallback for direct callers)
    import argparse
    args = argparse.Namespace(
        slice=str(tmp_path), worktree=str(tmp_path / "gone"),
        changed_files=[], changed_test_files=[], ack_critical="",
        seam_allowlist=None, test_first=False, strict=False)
    try:
        pre_finish_gate.run_gate(args)
    except ValueError as exc:
        assert "not a directory" in str(exc)
    else:
        raise AssertionError("run_gate must refuse an invalid --worktree")


def test_changed_from_git_mutually_exclusive(tmp_path, capsys):
    rc = pre_finish_gate.main([
        "--slice", str(tmp_path), "--worktree", str(tmp_path),
        "--changed-from-git", "HEAD", "--changed-files", "a.py"])
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err


def _git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def test_derive_changed_lists_diff_and_untracked(tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "base")
    # committed change + untracked source + untracked test files in the documented layouts
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "change")
    (repo / "new.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_new.py").write_text("", encoding="utf-8")
    (repo / "util_test.py").write_text("", encoding="utf-8")

    changed, tests = pre_finish_gate._derive_changed(repo, "HEAD~1")
    assert set(changed) == {"app.py", "new.py", "tests/test_new.py", "util_test.py"}
    assert set(tests) == {"tests/test_new.py", "util_test.py"}


def test_derive_changed_bad_base_raises(tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git(repo, "init", "-q")
    try:
        pre_finish_gate._derive_changed(repo, "no-such-ref")
    except ValueError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("a bad --changed-from-git base must raise, not return empty lists")
