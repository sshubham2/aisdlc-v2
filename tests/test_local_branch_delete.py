"""slice-057 / SC-092 — informed local-branch force-delete actuator.

`local_branch_delete.py` is the ONE and ONLY path that force-deletes a local slice
branch (`git branch -D`). Its force arm is DENIED by default and permitted ONLY by a
positive authoritative gh-MERGED verdict via the reused, single-sourced
`authorize_remote_delete` — mirroring the sibling `remote_branch_delete.py` (slice-054).

All git/gh access routes through ONE injected ``runner(argv) -> CompletedProcess`` fake
(the faithful unit seam), so every AC is decidable without a real repo or gh. The fake
models `git branch -d` returning NON-ZERO (the squash-merge refusal — CC-002) and the
structured `git rev-parse --verify` pre-check that distinguishes "already gone" from
"not fully merged" (M3), which a branch-name-unconditional fake could not see.

  AC1  squash-refuse (`-d` rc!=0) + gh MERGED -> `git branch -D` completes, no leftover.
  AC2  fail-closed: gh-absent / non-GitHub / PR-not-MERGED -> REFUSED, ZERO force-delete.
  AC4  the un-merged floor is preserved: no authoritative MERGED -> never force-deletes.
  M3   structured pre-check: absent ref -> noop (no gh, no force-delete); idempotent re-run
       never converts a clean absent-branch into a spurious force-delete failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import FakeRunner, cp, load_script  # noqa: E402

lbd = load_script("local_branch_delete")

DEFAULT = "uat"
MAIN = "C:/repo"
B = "slice/057-add-gh-gated-branch-force-cleanup"


def make_runner(
    *,
    ref_present=True,       # structured pre-check: local ref exists (M3)
    safe_delete_rc=1,       # `git branch -d` return code (1 = squash-merge refusal; 0 = safe-deleted)
    force_delete_rc=0,      # `git branch -D` return code (0 = deleted; !=0 = fail-visible)
    gh_present=True,
    github_origin=True,
    pr_state="MERGED",      # gh PR state; None -> "no pull requests found"
    pr_meta=None,
    cherry_empty=True,      # Signal-B Pass-1: slice commits already on origin/<default>
):
    pr_meta = pr_meta or {"number": 8, "mergedAt": "2026-07-03T04:00:00Z"}

    def handler(argv):
        a = list(argv)
        # -- structured local-ref pre-check (M3): refs/heads/<branch> present? --
        if a[:3] == ["git", "rev-parse", "--verify"]:
            return cp(a, 0 if ref_present else 1, "abc123" if ref_present else "",
                      "" if ref_present else "fatal: Needed a single revision")
        # -- safe delete --
        if a[:3] == ["git", "branch", "-d"]:
            err = "" if safe_delete_rc == 0 else "error: The branch '...' is not fully merged."
            return cp(a, safe_delete_rc, "", err)
        # -- force delete (the ONE blessed op) --
        if a[:3] == ["git", "branch", "-D"]:
            err = "" if force_delete_rc == 0 else "error: cannot force-delete"
            return cp(a, force_delete_rc, "", err)
        # -- authorize_remote_delete dependencies --
        if a[:2] == ["gh", "--version"]:
            if not gh_present:
                raise FileNotFoundError("gh")
            return cp(a, 0, "gh version 2.94.0")
        if a[:3] == ["gh", "pr", "view"]:
            if pr_state is None:
                return cp(a, 1, "", "no pull requests found")
            return cp(a, 0, json.dumps({"number": pr_meta["number"], "state": pr_state,
                                        "mergedAt": pr_meta["mergedAt"]}))
        if a[:4] == ["git", "remote", "get-url", "origin"]:
            url = "git@github.com:owner/repo.git" if github_origin else "git@gitlab.com:o/r.git"
            return cp(a, 0, url)
        if a[:2] == ["git", "cherry"]:
            return cp(a, 0, "" if cherry_empty else "+ abc123\n")
        if a[:2] == ["git", "ls-remote"]:
            return cp(a, 2, "")  # remote absent (4a merged-remote-absent) — informational only
        return cp(a, 0)

    return FakeRunner(handler)


def _force_calls(r):
    return [c for c in r.calls if c[:3] == ["git", "branch", "-D"]]


def _safe_calls(r):
    return [c for c in r.calls if c[:3] == ["git", "branch", "-d"]]


def _gh_calls(r):
    return [c for c in r.calls if c and c[0] == "gh"]


# ── AC1 — squash-refuse + gh MERGED -> informed force-delete completes ──────────

def test_force_delete_on_squash_merged_and_gh_merged():
    r = make_runner(ref_present=True, safe_delete_rc=1, pr_state="MERGED")
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT, worktree_backed=False)
    assert res["action"] == lbd.FORCE_DELETED
    assert res["deleted"] is True and res["authorized"] is True
    assert _force_calls(r) == [["git", "branch", "-D", B]], "the one blessed force-delete must be issued exactly once"
    # audit trail: reason carries the gh MERGED evidence (pr number)
    assert "8" in res["reason"] and "MERGED" in res["reason"].upper()
    assert lbd._exit_for(res) == 0


def test_safe_delete_succeeds_no_gh_no_force():
    # -d succeeds (fully merged) -> short-circuit, NO gh call, NO force-delete.
    r = make_runner(ref_present=True, safe_delete_rc=0)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.SAFE_DELETED and res["deleted"] is True
    assert _gh_calls(r) == [] and _force_calls(r) == []
    assert lbd._exit_for(res) == 0


# ── AC2 — fail-closed on every non-authorized input; ZERO force-delete ─────────

def test_fail_closed_gh_absent():
    r = make_runner(safe_delete_rc=1, gh_present=False)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.REFUSED and res["authorized"] is False
    assert _force_calls(r) == [], "must NEVER force-delete when gh is unavailable"
    assert res["reason"], "a STOP hint must be surfaced"
    assert lbd._exit_for(res) == 3


def test_fail_closed_non_github_origin():
    r = make_runner(safe_delete_rc=1, github_origin=False)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.REFUSED and res["authorized"] is False
    assert _force_calls(r) == []
    assert lbd._exit_for(res) == 3


def test_fail_closed_pr_not_merged():
    r = make_runner(safe_delete_rc=1, pr_state="OPEN")
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.REFUSED and res["authorized"] is False
    assert _force_calls(r) == []
    assert lbd._exit_for(res) == 3


def test_fail_closed_no_pr_at_all():
    # --merge local-only flow: no PR exists -> gh pr view rc!=0 -> fail-closed STOP.
    r = make_runner(safe_delete_rc=1, pr_state=None)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.REFUSED and res["authorized"] is False
    assert _force_calls(r) == []


# ── AC4 — the never-force-delete floor for the un-merged case is preserved ─────

def test_unmerged_never_force_deletes():
    # No authoritative MERGED evidence (PR closed-unmerged) -> STOP, no -D. The slice-008 floor.
    r = make_runner(safe_delete_rc=1, pr_state="CLOSED")
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.REFUSED
    assert _force_calls(r) == []


def test_worktree_backed_refused():
    # An in-flight (worktree-backed) branch is never force-deleted, even with a MERGED PR.
    r = make_runner(safe_delete_rc=1, pr_state="MERGED")
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT, worktree_backed=True)
    assert res["action"] == lbd.REFUSED and res["authorized"] is False
    assert _force_calls(r) == []


# ── M3 — structured pre-check: absent ref -> idempotent noop (no gh, no -D) ─────

def test_idempotent_absent_branch_is_noop():
    r = make_runner(ref_present=False)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.NOOP_ABSENT and res["deleted"] is False
    assert _gh_calls(r) == [], "an already-absent branch must NOT reach the gh gate"
    assert _safe_calls(r) == [] and _force_calls(r) == []
    assert lbd._exit_for(res) == 0


# ── fail-visible: a force-delete error is surfaced (exit 4), never swallowed ────

def test_force_delete_failure_is_fail_visible():
    r = make_runner(ref_present=True, safe_delete_rc=1, pr_state="MERGED", force_delete_rc=1)
    res = lbd.run_local_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == lbd.FORCE_DELETE_FAILED and res["deleted"] is False
    assert res["reason"], "a force-delete failure must be surfaced, not swallowed"
    assert lbd._exit_for(res) == 4
