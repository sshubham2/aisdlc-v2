"""pr_flow.py — the non-interactive PR ladder (PUSHED -> PR_CREATED -> AUTOMERGE_ENABLED).

Every gh/git call routes through ONE injected ``runner(argv) -> CompletedProcess``
(the mock seam). These tests drive the pure executor with a ``FakeRunner`` and assert
the EXACT gh argv, the rung reached, and the degradation action — including the
must-not-defer safety floor (no forbidden flag ever reaches the runtime command set;
verify-the-outcome not the exit code; never enable auto-merge without merge rights).

Auto-merge-ONLY (direct-merge dropped at slice-008 TRI-1): the ladder never merges
locally and never deletes a branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import FakeRunner, cp, load_script  # noqa: E402

pr_flow = load_script("pr_flow")

BRANCH = "slice/008-automate-push-pr-merge"
DEFAULT = "master"
FORBIDDEN = {"--force", "--force-with-lease", "-D", "--no-verify"}


def make_runner(*, push=None, gh_version=None, remote=None, pr_create=None,
                pr_view_url=None, perm=None, merge=None, automerge_view=None):
    """Build a FakeRunner whose happy-path defaults walk the full ladder; pass an
    override (a CompletedProcess or an Exception) to bend one step."""
    def handler(argv):
        a = list(argv)
        if a[:2] == ["git", "push"]:
            return push if push is not None else cp(a, 0)
        if a == ["gh", "--version"]:
            return gh_version if gh_version is not None else cp(a, 0, "gh version 2.62.0\n")
        if a[:1] == ["git"] and "remote" in a and "get-url" in a:
            return remote if remote is not None else cp(a, 0, "https://github.com/sshubham2/aisdlc-v2.git\n")
        if a[:3] == ["gh", "pr", "create"]:
            return pr_create if pr_create is not None else cp(a, 0, "https://github.com/sshubham2/aisdlc-v2/pull/9\n")
        if a[:3] == ["gh", "pr", "view"] and "autoMergeRequest" in a:
            return automerge_view if automerge_view is not None else cp(a, 0, '{"enabledAt":"2026-06-14T00:00:00Z"}\n')
        if a[:3] == ["gh", "pr", "view"]:
            return pr_view_url if pr_view_url is not None else cp(a, 0, "https://github.com/sshubham2/aisdlc-v2/pull/9\n")
        if a[:2] == ["gh", "api"]:
            return perm if perm is not None else cp(a, 0, "true\n")
        if a[:3] == ["gh", "pr", "merge"]:
            return merge if merge is not None else cp(a, 0, "")
        return cp(a, 0)
    return FakeRunner(handler)


def _run(runner, **kw):
    kw.setdefault("branch", BRANCH)
    kw.setdefault("default", DEFAULT)
    return pr_flow.run_pr_flow(runner=runner, **kw)


def _assert_no_forbidden(runner):
    leaked = FORBIDDEN & set(runner.flat())
    assert not leaked, f"forbidden flag(s) reached the runtime command set: {leaked}"


# ── AC1: rebase->push parity + the no-forbidden-flag runtime floor ─────────────

def test_ac1_rebase_then_push_and_pcr_gate():
    runner = make_runner()
    v = _run(runner)
    # clean path proceeds through push
    assert runner.argv_contains("git", "push", "-u", "origin", BRANCH)
    # M1 belt-and-suspenders: no forbidden flag in ANY runtime argv
    _assert_no_forbidden(runner)
    # pr_flow does NOT rebase/merge/checkout — that REBASED rung is SKILL.md's shared
    # section (interactive PCR-2b); the ladder starts at the push.
    assert not runner.argv_contains("git", "rebase")
    assert not runner.argv_contains("git", "merge")
    assert not runner.argv_contains("git", "checkout")


# ── AC2: gh-present creates the PR; gh-absent / non-GitHub falls back ──────────

def test_ac2_gh_present_creates_pr_absent_prints_hint():
    # gh present + GitHub origin -> PR is created
    runner = make_runner()
    v = _run(runner)
    assert runner.argv_contains("gh", "pr", "create", "--base", DEFAULT)
    assert runner.argv_contains("gh", "pr", "create", "--head", BRANCH)
    # --fill is REQUIRED: gh pr create is otherwise interactive (no title/body) and
    # would hang/fail non-interactively. Pin it so it can't silently regress.
    assert runner.argv_contains("gh", "pr", "create", "--fill")
    assert v.rung_reached in ("PR_CREATED", "AUTOMERGE_ENABLED")
    assert v.pr_url and v.pr_url.startswith("https://github.com/")

    # gh ABSENT -> graceful printed-hint fallback, no traceback, no PR create
    import builtins
    absent = make_runner(gh_version=FileNotFoundError("gh"))
    v2 = _run(absent)
    assert v2.action == "fallback-hint"
    assert v2.rung_reached == "PUSHED"
    assert v2.internal_error is False
    assert not absent.argv_contains("gh", "pr", "create")

    # non-GitHub origin -> same printed-hint fallback
    nongh = make_runner(remote=cp([], 0, "https://gitlab.com/o/r.git\n"))
    v3 = _run(nongh)
    assert v3.action == "fallback-hint"
    assert v3.rung_reached == "PUSHED"
    assert not nongh.argv_contains("gh", "pr", "create")


# ── AC3: auto-merge only with merge rights; verify outcome; non-blocking ──────

def test_ac3_auto_merge_when_permitted_nonblocking():
    # permitted -> enable + VERIFY via autoMergeRequest non-null, return promptly
    runner = make_runner()
    v = _run(runner)
    assert runner.argv_contains("gh", "pr", "merge", "--auto")
    assert v.can_merge == "true"
    assert v.automerge_confirmed is True
    assert v.rung_reached == "AUTOMERGE_ENABLED"
    # non-blocking: never waits on / polls CI
    assert not runner.argv_contains("gh", "run", "watch")
    assert "--watch" not in runner.flat() and "watch" not in runner.flat()

    # permission UNKNOWN (empty .permissions.push) -> never enable auto-merge
    unknown = make_runner(perm=cp([], 0, "\n"))
    vu = _run(unknown)
    assert vu.can_merge == "unknown"
    assert not unknown.argv_contains("gh", "pr", "merge")
    assert vu.rung_reached == "PR_CREATED"

    # permission FALSE -> never enable auto-merge
    false = make_runner(perm=cp([], 0, "false\n"))
    vf = _run(false)
    assert vf.can_merge == "false"
    assert not false.argv_contains("gh", "pr", "merge")

    # silent false-success: enable exits 0 BUT autoMergeRequest is null -> NOT enabled
    silent = make_runner(automerge_view=cp([], 0, "null\n"))
    vs = _run(silent)
    assert vs.automerge_confirmed is False
    assert vs.action == "automerge-unverified"
    assert vs.rung_reached == "PR_CREATED"

    # enable fails (HTTP 422 / exit 1) -> EXPECTED graceful fallback, stderr surfaced
    unavail = make_runner(merge=cp([], 1, "", "failed to enable auto-merge: HTTP 422 (checks pending)"))
    vU = _run(unavail)
    assert vU.action == "automerge-unavailable"
    assert vU.rung_reached == "PR_CREATED"
    assert vU.automerge_confirmed is False
    assert "422" in vU.stderr


# ── AC5: never touches plugin.json; never does --merge's local merge ──────────

def test_ac5_no_version_bump_and_merge_unchanged():
    runner = make_runner()
    _run(runner)
    for call in runner.calls:
        assert not any("plugin.json" in tok for tok in call), f"pr_flow touched plugin.json: {call}"
    # --push must NOT perform --merge's local merge/checkout into default
    assert not runner.argv_contains("git", "merge")
    assert not runner.argv_contains("git", "checkout")
    _assert_no_forbidden(runner)


# ── m2: PR-create head failure (not pushed / no commits) -> halt at PUSHED ─────

def test_m2_pr_create_head_not_pushed_falls_back():
    for stderr in ("pull request create failed: GraphQL: No commits between master and the branch",
                   "aborted: you must first push the current branch to a remote"):
        runner = make_runner(pr_create=cp([], 1, "", stderr))
        v = _run(runner)
        assert v.action == "pr-create-failed"
        assert v.rung_reached == "PUSHED"
        assert stderr.split(":")[0] in v.stderr or stderr in v.stderr
        assert v.reason  # a manual-finish hint is present
        assert v.internal_error is False
        # never escalated to auto-merge after a failed create
        assert not runner.argv_contains("gh", "pr", "merge")


# ── m3: non-ff push rejected on a re-run -> halt at REBASED, never force ───────

def test_m3_non_ff_push_after_rebase_halts_at_rebased():
    runner = make_runner(push=cp([], 1, "",
                         "! [rejected]  slice/008 -> slice/008 (non-fast-forward)\nUpdates were rejected"))
    v = _run(runner)
    assert v.rung_reached == "REBASED"
    assert v.action == "push-rejected-nonff"
    assert "non-fast-forward" in v.stderr
    _assert_no_forbidden(runner)         # never force-pushes to resolve
    assert v.internal_error is False
    # ladder stopped: no gh calls after a failed push
    assert not runner.argv_contains("gh", "pr", "create")


# ── M-add-1: the PUSHED breadcrumb survives a post-push internal error ─────────

def test_madd1_push_breadcrumb_survives_postpush_exit1():
    emitted = []

    def handler(argv):
        a = list(argv)
        if a[:2] == ["git", "push"]:
            return cp(a, 0)               # push SUCCEEDS (irreversible)
        if a == ["gh", "--version"]:
            return RuntimeError("simulated post-push crash")   # then a crash
        return cp(a, 0)

    runner = FakeRunner(handler)
    v = _run(runner, emit=emitted.append)
    # the push breadcrumb was flushed BEFORE the crash
    assert any(e.get("rung") == "PUSHED" for e in emitted), f"no PUSHED breadcrumb: {emitted}"
    # the verdict still records the irreversible push, and signals an internal error
    assert v.rung_reached == "PUSHED"
    assert v.internal_error is True
    assert v.action == "internal-error"


# ── CLI: refuses to run without the human confirmation gate ───────────────────

def test_cli_requires_confirmed(run_script, tmp_path):
    r = run_script("skills/commit-slice/scripts/pr_flow.py",
                   ["--branch", BRANCH, "--default", DEFAULT, "--repo-root", str(tmp_path)])
    assert r.returncode == 2
    assert "confirm" in (r.stdout + r.stderr).lower()


# ── pure helper: GitHub remote parsing ────────────────────────────────────────

def test_parse_github_remote():
    assert pr_flow.parse_github_remote("https://github.com/o/r.git") == ("o", "r")
    assert pr_flow.parse_github_remote("git@github.com:o/r.git") == ("o", "r")
    assert pr_flow.parse_github_remote("https://github.com/o/r") == ("o", "r")
    assert pr_flow.parse_github_remote("https://gitlab.com/o/r.git") is None
    assert pr_flow.parse_github_remote("") is None
