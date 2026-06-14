"""resolve_sync_target.py — AC4 cleanup-from-anywhere target resolver.

RESOLVE-ONLY (never deletes). Three resolution paths:
  * explicit ``--slice`` arg  -> active_slice.resolve_slice_by_id (archive-aware)
  * on a slice/* branch       -> resolve self (back-compat with today's on-branch 5d)
  * else from the MAIN tree   -> enumerate local slice/* refs, EXCLUDE worktree-backed
                                 in-flight slices [M4], two-signal-merged over survivors;
                                 exactly one -> auto-pick; >1 -> AMBIGUOUS; 0 -> none.

All git access routes through ONE injected ``runner(argv) -> CompletedProcess`` so the
M4 exclusion + ambiguity logic is unit-testable without real worktrees/origin.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import FakeRunner, cp, load_script  # noqa: E402

rst = load_script("resolve_sync_target")

DEFAULT = "master"
MAIN_TREE = "C:/repo"


def _porcelain(*entries) -> str:
    """Build `git worktree list --porcelain` text. entries = (path, branch_or_None)."""
    blocks = []
    for path, branch in entries:
        b = f"worktree {path}\nHEAD deadbeef\n"
        b += (f"branch refs/heads/{branch}\n" if branch else "detached\n")
        blocks.append(b)
    return "\n".join(blocks) + "\n"


def make_runner(*, slice_refs=(), worktrees=(), merged=()):
    """slice_refs: local slice/* branch names. worktrees: (path, branch) porcelain rows.
    merged: branch names that two-signal as merged (remote absent + cherry empty)."""
    merged = set(merged)

    def handler(argv):
        a = list(argv)
        if a[:2] == ["git", "for-each-ref"]:
            return cp(a, 0, "".join(f"{r}\n" for r in slice_refs))
        if a[:3] == ["git", "worktree", "list"]:
            rows = worktrees or ((MAIN_TREE, None),)
            return cp(a, 0, _porcelain(*rows))
        if a[:2] == ["git", "ls-remote"]:
            branch = a[-1]
            # --exit-code: present (rc 0) when NOT merged; absent (rc 2) when merged.
            return cp(a, 2 if branch in merged else 0, "" if branch in merged else "abc refs/heads/" + branch)
        if a[:2] == ["git", "cherry"]:
            branch = a[-1]
            return cp(a, 0, "" if branch in merged else "+ abc123\n")
        return cp(a, 0)
    return FakeRunner(handler)


def _resolve(runner, **kw):
    kw.setdefault("default", DEFAULT)
    kw.setdefault("main_tree", MAIN_TREE)
    return rst.resolve_target(runner=runner, **kw)


# ── is_merged: two-signal AND ─────────────────────────────────────────────────

def test_is_merged_both_signals():
    r = make_runner(merged={"slice/008-x"})
    assert rst.is_merged(r, "slice/008-x", DEFAULT) is True


def test_not_merged_remote_present():
    r = make_runner()  # nothing merged -> ls-remote returns rc 0 (remote present)
    assert rst.is_merged(r, "slice/008-x", DEFAULT) is False


# ── AC4: resolve from the main tree / explicit / on-branch / ambiguous ─────────

def test_ac4_sync_after_pr_from_main_tree_resolves_slice():
    # (a) single worktree-less merged slice -> auto-resolved
    r = make_runner(slice_refs=["slice/008-automate"], worktrees=[(MAIN_TREE, None)],
                    merged={"slice/008-automate"})
    plan = _resolve(r, current_branch=DEFAULT)
    assert plan["status"] == "resolved"
    assert plan["resolution"] == "auto"
    assert plan["branch"] == "slice/008-automate"
    assert plan["slice"] == "slice-008"

    # (b) explicit arg targets that slice via the archive-aware by-id lookup
    def _mk_vault(tmp):
        v = tmp / "vault"
        (v / "slices" / "slice-005-foo").mkdir(parents=True)
        return v

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        vault = _mk_vault(Path(td))
        r2 = make_runner(worktrees=[(MAIN_TREE, None)])
        plan2 = _resolve(r2, current_branch=DEFAULT, explicit_slice="slice-005", vault=str(vault))
        assert plan2["status"] == "resolved"
        assert plan2["resolution"] == "explicit"
        assert plan2["slice"] == "slice-005"
        assert plan2["branch"] == "slice/005-foo"

    # (c) on a slice branch -> resolves self (back-compat), no vault needed
    r3 = make_runner(worktrees=[(MAIN_TREE, None)])
    plan3 = _resolve(r3, current_branch="slice/007-bar")
    assert plan3["status"] == "resolved"
    assert plan3["resolution"] == "on-branch"
    assert plan3["slice"] == "slice-007"
    assert plan3["branch"] == "slice/007-bar"

    # (d) two merged worktree-less slices -> AMBIGUOUS (the human disambiguates)
    r4 = make_runner(slice_refs=["slice/008-a", "slice/009-b"], worktrees=[(MAIN_TREE, None)],
                     merged={"slice/008-a", "slice/009-b"})
    plan4 = _resolve(r4, current_branch=DEFAULT)
    assert plan4["status"] == "ambiguous"
    assert set(plan4["candidates"]) == {"slice/008-a", "slice/009-b"}


def test_explicit_slice_not_found_resolves_none(tmp_path):
    vault = tmp_path / "vault"
    (vault / "slices").mkdir(parents=True)
    r = make_runner(worktrees=[(MAIN_TREE, None)])
    plan = _resolve(r, current_branch=DEFAULT, explicit_slice="slice-099", vault=str(vault))
    assert plan["status"] == "none"


def test_no_merged_slice_resolves_none():
    # one slice ref, worktree-less, but NOT merged -> nothing to clean
    r = make_runner(slice_refs=["slice/008-x"], worktrees=[(MAIN_TREE, None)], merged=set())
    plan = _resolve(r, current_branch=DEFAULT)
    assert plan["status"] == "none"


# ── M4: worktree-backed in-flight slices are NEVER auto-picked ─────────────────

def test_m4_excludes_inflight_worktree_backed_slice():
    # slice/100-a is merged BUT worktree-backed (in-flight) -> excluded.
    # slice/200-b is merged AND worktree-less -> the cleanup target.
    r = make_runner(
        slice_refs=["slice/100-a", "slice/200-b"],
        worktrees=[(MAIN_TREE, None), ("C:/repo-wt/slice-100-a", "slice/100-a")],
        merged={"slice/100-a", "slice/200-b"},
    )
    plan = _resolve(r, current_branch=DEFAULT)
    assert plan["status"] == "resolved"
    assert plan["branch"] == "slice/200-b"        # the worktree-less one
    assert plan["slice"] == "slice-200"
    # the in-flight worktree-backed slice is never the auto-pick
    assert plan["branch"] != "slice/100-a"


# ── CLI smoke (real git, no origin): a repo with no slice branches -> none ─────

def test_cli_no_slice_branches_none(run_script, tmp_path):
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-b", "master")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "tester")
    git("commit", "--allow-empty", "-m", "init")

    r = run_script("skills/commit-slice/scripts/resolve_sync_target.py",
                   ["--repo-root", str(repo), "--json"])
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["status"] == "none"
