"""Coverage for /setup's consented config-commit actuator (skills/setup/scripts/setup.py:
commit_scaffold + _uncommitted_scaffold).

/setup scaffolds <repo>/.aisdlc/reality-gates.json and appends ignore lines to <repo>/.gitignore
-- repo-tracked config that is MEANT to be committed (M-add-2: it must travel to teammates + CI),
but which the installer left uncommitted, dirtying the main tree and tripping WT-ROOT-1 on the
first /build-slice. `commit_scaffold` closes that loop. The load-bearing invariants under test:
  * it commits ONLY the two ai-sdlc config files -- never the user's OTHER staged work (pathspec-scoped);
  * it is idempotent (a re-run makes no new commit);
  * it is a guarded, visible no-op on a non-git repo / detached HEAD (never a surprise commit).
`.mcp.json` is out of scope (machine-specific + gitignored -> must NOT be committed).
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_SETUP_PY = Path(__file__).resolve().parents[1] / "skills" / "setup" / "scripts" / "setup.py"
_spec = importlib.util.spec_from_file_location("aisdlc_setup_skill", _SETUP_PY)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)  # setup.py self-bootstraps plugin root onto sys.path for _stdout


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "init")


def _scaffold_files(tmp_path):
    """Simulate what a /setup run leaves behind: an untracked reality-gates manifest + a
    .gitignore modified with ai-sdlc's ignore lines."""
    (tmp_path / ".aisdlc").mkdir(exist_ok=True)
    (tmp_path / ".aisdlc" / "reality-gates.json").write_text('{"gates":{}}\n', encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".mcp.json\n.code-review-graph/\n", encoding="utf-8")


def test_commits_only_ai_sdlc_config_not_other_staged_work(tmp_path):
    _init_repo(tmp_path)
    _scaffold_files(tmp_path)
    # the user's OWN staged work-in-progress MUST survive untouched
    (tmp_path / "user_wip.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "user_wip.py")

    assert setup.commit_scaffold(str(tmp_path)) == 0

    committed = _git(tmp_path, "show", "--stat", "--format=", "HEAD").stdout
    assert ".aisdlc/reality-gates.json" in committed
    assert ".gitignore" in committed
    assert "user_wip.py" not in committed          # pathspec-scoped: user's work NOT swept in

    st = _git(tmp_path, "status", "--porcelain").stdout
    assert "user_wip.py" in st                      # still staged, still uncommitted
    assert "reality-gates.json" not in st           # ai-sdlc config now tracked + clean


def test_uncommitted_scaffold_lists_untracked_and_modified_then_empties(tmp_path):
    _init_repo(tmp_path)
    _scaffold_files(tmp_path)
    assert set(setup._uncommitted_scaffold(str(tmp_path))) == {
        ".aisdlc/reality-gates.json", ".gitignore"}
    setup.commit_scaffold(str(tmp_path))
    assert setup._uncommitted_scaffold(str(tmp_path)) == []   # nothing left after commit


def test_idempotent_second_run_makes_no_new_commit(tmp_path):
    _init_repo(tmp_path)
    _scaffold_files(tmp_path)
    assert setup.commit_scaffold(str(tmp_path)) == 0
    head1 = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert setup.commit_scaffold(str(tmp_path)) == 0          # no-op
    head2 = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert head1 == head2


def test_non_git_repo_is_a_visible_noop(tmp_path):
    _scaffold_files(tmp_path)                       # no `git init`
    assert setup.commit_scaffold(str(tmp_path)) == 0


def test_detached_head_is_a_guarded_noop(tmp_path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "--detach", "HEAD")
    _scaffold_files(tmp_path)
    assert setup.commit_scaffold(str(tmp_path)) == 0
    # nothing committed on a detached HEAD -> the config is still uncommitted (git collapses
    # the untracked dir to `?? .aisdlc/`, so key on that rather than the file name).
    assert setup._uncommitted_scaffold(str(tmp_path)) == [".aisdlc/reality-gates.json", ".gitignore"]
