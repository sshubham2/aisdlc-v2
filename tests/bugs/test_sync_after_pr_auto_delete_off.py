"""slice-054 / SC-018 — harden `/commit-slice --sync-after-pr` for repos where GitHub
head-branch auto-delete is OFF (the remote slice branch lingers after a merge).

All git/gh access routes through ONE injected ``runner(argv) -> CompletedProcess`` fake,
so every AC is decidable without a real remote, worktree, or gh (mission-brief
verification_plan). Covers:

  AC1  classify_merge_state -> merged-remote-lingering (Signal A=NO + Signal B=YES),
       distinct from the unmerged STOP.
  AC2  authorize_remote_delete: gh PR MERGED is the PRIMARY authorizer; state!=MERGED /
       gh-absent / non-GitHub FAIL CLOSED; Pass-2 tree-equality defense-in-depth preserved;
       single-sourced (classifier plan and actuator agree by construction).
  AC3  the actuator issues EXACTLY `git push origin --delete <branch>`, reachable only
       when authorized; §5d confirmation renders the authoritative evidence (doc-guard).
  AC4  an OPEN PR STOPs (remote-in-flight-in-another-clone protection); a sibling
       worktree-backed slice's remote is never touched.
  AC5  the auto-delete-ON path (remote already absent) issues ZERO `git push --delete`.
  M3   auto-detect now picks a lingering-remote merged slice (is_merged widened, ADR-051).
  M2   the §5d auto-delete-ON region is byte-stable and issues no remote push (doc-guard).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/ -> _commit_slice_helpers
from _commit_slice_helpers import FakeRunner, cp, load_script  # noqa: E402

rst = load_script("resolve_sync_target")
act = load_script("remote_branch_delete")

DEFAULT = "uat"
MAIN_TREE = "C:/repo"
B = "slice/054-fix"
_SKILL = Path(__file__).resolve().parents[2] / "skills" / "commit-slice" / "SKILL.md"


def _porcelain(*entries) -> str:
    blocks = []
    for path, branch in entries:
        b = f"worktree {path}\nHEAD deadbeef\n"
        b += (f"branch refs/heads/{branch}\n" if branch else "detached\n")
        blocks.append(b)
    return "\n".join(blocks) + "\n"


def make_runner(
    *,
    slice_refs=(),
    worktrees=None,
    remote_present=(),          # branches whose remote head EXISTS (ls-remote rc 0)
    cherry_empty=(),            # branches where `git cherry` has no `+` lines (Signal B Pass-1)
    local_ref_absent=(),        # branches whose LOCAL ref is gone -> cherry/rev-parse rc 128 (CR1)
    gh_present=True,
    github_origin=True,
    pr_state=None,              # branch -> "MERGED" | "OPEN" | "CLOSED"
    pr_meta=None,               # branch -> {"number":.., "mergedAt":..}
    push_fails=False,
    # Pass-2 (squash) knobs — only exercised when cherry is NOT empty:
    merge_base_sha="",
    diff_files=(),
    rev_list_commits=(),
    commit_touched=None,        # commit -> set(files)
    commit_tree=None,           # commit -> tree-sha
    slice_tree="T-SLICE",
):
    remote_present = set(remote_present)
    cherry_empty = set(cherry_empty)
    local_ref_absent = set(local_ref_absent)
    pr_state = pr_state or {}
    pr_meta = pr_meta or {}
    commit_touched = commit_touched or {}
    commit_tree = commit_tree or {}
    wts = worktrees if worktrees is not None else ((MAIN_TREE, None),)

    def handler(argv):
        a = list(argv)
        # -- gh --
        if a[:2] == ["gh", "--version"]:
            if not gh_present:
                raise FileNotFoundError("gh")
            return cp(a, 0, "gh version 2.0.0")
        if a[:3] == ["gh", "pr", "view"]:
            branch = a[3]
            st = pr_state.get(branch)
            if st is None:
                return cp(a, 1, "", "no pull requests found")
            meta = pr_meta.get(branch, {})
            payload = {"number": meta.get("number", 1), "state": st,
                       "mergedAt": meta.get("mergedAt")}
            return cp(a, 0, json.dumps(payload))
        # -- git remote origin url --
        if a[:4] == ["git", "remote", "get-url", "origin"]:
            url = "git@github.com:owner/repo.git" if github_origin else "git@gitlab.com:o/r.git"
            return cp(a, 0, url)
        # -- refs / worktrees --
        if a[:2] == ["git", "for-each-ref"]:
            return cp(a, 0, "".join(f"{r}\n" for r in slice_refs))
        if a[:3] == ["git", "worktree", "list"]:
            return cp(a, 0, _porcelain(*wts))
        # -- Signal A / B --
        if a[:2] == ["git", "ls-remote"]:
            branch = a[-1]
            present = branch in remote_present
            return cp(a, 0 if present else 2, ("abc refs/heads/" + branch) if present else "")
        if a[:2] == ["git", "cherry"]:
            branch = a[-1]
            if branch in local_ref_absent:                       # deleted local ref (CR1)
                return cp(a, 128, "", f"fatal: unknown commit {branch}")
            return cp(a, 0, "" if branch in cherry_empty else "+ abc123\n")
        # -- Pass-2 squash detection --
        if a[:2] == ["git", "merge-base"]:
            return cp(a, 0 if merge_base_sha else 0, merge_base_sha + ("\n" if merge_base_sha else ""))
        if a[:3] == ["git", "diff", "--name-only"]:
            return cp(a, 0, "".join(f"{f}\n" for f in diff_files))
        if a[:2] == ["git", "rev-list"]:
            return cp(a, 0, "".join(f"{c}\n" for c in rev_list_commits))
        if a[:2] == ["git", "diff-tree"]:
            commit = a[-1]
            return cp(a, 0, "".join(f"{f}\n" for f in commit_touched.get(commit, set())))
        if a[:2] == ["git", "rev-parse"]:
            ref = a[2].rsplit("^", 1)[0]
            if ref == B or ref in slice_refs:
                return cp(a, 0, slice_tree)
            return cp(a, 0, commit_tree.get(ref, ""))
        # -- the destructive op (recorded) --
        if a[:2] == ["git", "push"]:
            return cp(a, 1 if push_fails else 0, "", "remote rejected" if push_fails else "")
        return cp(a, 0)

    return FakeRunner(handler)


def _resolve(runner, **kw):
    kw.setdefault("default", DEFAULT)
    kw.setdefault("main_tree", MAIN_TREE)
    return rst.resolve_target(runner=runner, **kw)


def _pushes(runner):
    return [c for c in runner.calls if c[:2] == ["git", "push"]]


# ── AC1 — classify the new topology, distinct from the unmerged STOP ──────────


def test_ac1_classifies_merged_remote_lingering():
    r = make_runner(remote_present={B}, cherry_empty={B})
    state = rst.classify_merge_state(r, B, DEFAULT)
    assert state == rst.MERGED_REMOTE_LINGERING
    assert state != rst.UNMERGED


def test_ac1_absent_and_unmerged_topologies_still_classify():
    # remote absent + merged -> the classic auto-delete-ON state
    r_absent = make_runner(remote_present=set(), cherry_empty={B})
    assert rst.classify_merge_state(r_absent, B, DEFAULT) == rst.MERGED_REMOTE_ABSENT
    # remote present but commits NOT on default (Pass-1 has `+`, Pass-2 empty base) -> unmerged
    r_unmerged = make_runner(remote_present={B}, cherry_empty=set())
    assert rst.classify_merge_state(r_unmerged, B, DEFAULT) == rst.UNMERGED
    # worktree-backed short-circuits to in-flight-excluded
    assert rst.classify_merge_state(r_absent, B, DEFAULT, worktree_backed=True) == rst.IN_FLIGHT_EXCLUDED


# ── AC2 — gh-primary authorization, fail-closed, defense-in-depth, single-sourced ──


def test_ac2a_state_not_merged_denies():
    r = make_runner(remote_present={B}, cherry_empty={B},
                    pr_state={B: "OPEN"}, pr_meta={B: {"number": 7}})
    authz = rst.authorize_remote_delete(r, B, DEFAULT)
    assert authz["authorized"] is False
    assert authz["evidence"]["pr_state"] == "OPEN"


def test_ac2b_gh_absent_or_non_github_fails_closed():
    r_nogh = make_runner(remote_present={B}, cherry_empty={B}, gh_present=False)
    a1 = rst.authorize_remote_delete(r_nogh, B, DEFAULT)
    assert a1["authorized"] is False and a1["evidence"]["gh_present"] is False
    assert not _pushes(r_nogh)  # zero push --delete implied (authorization only, but guard anyway)

    r_nongh = make_runner(remote_present={B}, cherry_empty={B}, github_origin=False,
                          pr_state={B: "MERGED"})
    a2 = rst.authorize_remote_delete(r_nongh, B, DEFAULT)
    assert a2["authorized"] is False and a2["evidence"]["is_github"] is False


def test_ac2c_pass2_tree_equality_preserved():
    # gh says MERGED, but the ONLY commit whose touched-set superset FILES has a MISMATCHED
    # tree -> Signal B defense-in-depth returns False -> NOT authorized (m2: gate preserved).
    r = make_runner(
        remote_present={B}, cherry_empty=set(),
        pr_state={B: "MERGED"}, pr_meta={B: {"number": 9, "mergedAt": "2026-07-01T00:00:00Z"}},
        merge_base_sha="BASE", diff_files=["f1.py"], rev_list_commits=["C1"],
        commit_touched={"C1": {"f1.py", "f2.py"}}, commit_tree={"C1": "T-OTHER"},
        slice_tree="T-SLICE",
    )
    authz = rst.authorize_remote_delete(r, B, DEFAULT)
    assert authz["authorized"] is False
    assert authz["evidence"]["signal_b"] is False


def test_ac2d_authorized_when_all_hold_and_single_sourced():
    meta = {"number": 11, "mergedAt": "2026-07-01T12:00:00Z"}
    direct = rst.authorize_remote_delete(
        make_runner(remote_present={B}, cherry_empty={B}, pr_state={B: "MERGED"}, pr_meta={B: meta}),
        B, DEFAULT)
    assert direct["authorized"] is True
    assert direct["evidence"]["pr_number"] == 11 and direct["evidence"]["pr_state"] == "MERGED"

    # The actuator derives its decision from the SAME authorize_remote_delete (imported, not
    # re-implemented): identical evidence + verdict by construction (B2 single-source).
    res = act.run_remote_delete(
        runner=make_runner(remote_present={B}, cherry_empty={B}, pr_state={B: "MERGED"}, pr_meta={B: meta}),
        branch=B, default=DEFAULT)
    assert res["authorized"] is True
    assert res["evidence"]["pr_number"] == direct["evidence"]["pr_number"]
    assert res["evidence"]["pr_state"] == direct["evidence"]["pr_state"]
    assert act.authorize_remote_delete.__module__ == "resolve_sync_target"  # imported, not redefined


# ── AC3 — named origin-scoped actuator; evidence-rendering confirmation (doc-guard) ──


def test_ac3_actuator_issues_exactly_origin_delete():
    r = make_runner(remote_present={B}, cherry_empty={B},
                    pr_state={B: "MERGED"}, pr_meta={B: {"number": 3, "mergedAt": "2026-07-01"}})
    res = act.run_remote_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == act.DELETED and res["deleted"] is True
    assert _pushes(r) == [["git", "push", "origin", "--delete", B]]  # EXACTLY one, origin-scoped


def test_ac3_skill_5d_confirmation_renders_evidence():
    text = _SKILL.read_text(encoding="utf-8")
    region = _region(text, "SYNC-5D-LINGERING")
    # pos: the confirmation renders the AUTHORITATIVE evidence + the exact op + a yes/no gate
    assert "evidence.pr_number" in region
    assert "evidence.merged_at" in region
    assert "lingers" in region
    assert "git push origin --delete" in region
    assert "(yes/no)" in region
    # neg: it is NOT the old rubber-stamp that relayed only the tool's own verdict
    assert "PR appears merged + remote-deleted" not in region


def test_ac3_push_failure_surfaces_literal_recovery_command():
    r = make_runner(remote_present={B}, cherry_empty={B}, push_fails=True,
                    pr_state={B: "MERGED"}, pr_meta={B: {"number": 4, "mergedAt": "2026-07-01"}})
    res = act.run_remote_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == act.PUSH_FAILED and res["deleted"] is False
    assert res["recovery_command"] == f"git push origin --delete {B}"  # M4 re-runnable literal


# ── AC4 — remote-in-flight boundary is gh PR-state; sibling remote never touched ──


def test_ac4_open_pr_stops_zero_delete():
    r = make_runner(remote_present={B}, cherry_empty={B},
                    pr_state={B: "OPEN"}, pr_meta={B: {"number": 5}})
    res = act.run_remote_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == act.REFUSED and res["authorized"] is False
    assert not _pushes(r)  # an in-flight PR (another clone) is protected — zero push --delete


def test_ac4_sibling_worktree_backed_remote_survives():
    target, sib = "slice/054-target", "slice/100-sib"
    r = make_runner(
        slice_refs=[target, sib],
        worktrees=[(MAIN_TREE, None), ("C:/repo-wt/slice-100-sib", sib)],
        remote_present={target, sib}, cherry_empty={target, sib},
        pr_state={target: "MERGED"}, pr_meta={target: {"number": 8, "mergedAt": "2026-07-01"}},
    )
    plan = _resolve(r, current_branch=DEFAULT)
    assert plan["status"] == "resolved" and plan["branch"] == target
    assert plan["state"] == rst.MERGED_REMOTE_LINGERING
    assert plan["remote_delete_authorized"] is True
    # the sibling is worktree-backed -> actuating it is REFUSED, its remote untouched
    res_sib = act.run_remote_delete(runner=r, branch=sib, default=DEFAULT, worktree_backed=True)
    assert res_sib["action"] == act.REFUSED
    assert not any("--delete" in c and sib in c for c in r.calls)


# ── AC5 — auto-delete-ON path issues ZERO remote push ─────────────────────────


def test_ac5_auto_delete_on_zero_remote_push():
    r = make_runner(remote_present=set(), cherry_empty={B},
                    pr_state={B: "MERGED"}, pr_meta={B: {"number": 2, "mergedAt": "2026-07-01"}})
    assert rst.classify_merge_state(r, B, DEFAULT) == rst.MERGED_REMOTE_ABSENT
    res = act.run_remote_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == act.NOOP_ABSENT and res["deleted"] is False
    assert not _pushes(r)  # remote already absent -> idempotent no-op, zero push --delete


# ── M3 — auto-detect now picks a lingering-remote merged slice (is_merged widened) ──


def test_m3_autodetect_picks_lingering_remote():
    r = make_runner(slice_refs=[B], worktrees=[(MAIN_TREE, None)],
                    remote_present={B}, cherry_empty={B},
                    pr_state={B: "MERGED"}, pr_meta={B: {"number": 1, "mergedAt": "2026-07-01"}})
    plan = _resolve(r, current_branch=DEFAULT)
    assert plan["status"] == "resolved" and plan["branch"] == B
    assert plan["state"] == rst.MERGED_REMOTE_LINGERING
    assert rst.is_merged(r, B, DEFAULT) is True  # widened: True though the remote is present


# ── M2 — the §5d auto-delete-ON region is byte-stable + issues no remote push ──


def test_m2_onpath_region_never_issues_remote_push():
    text = _SKILL.read_text(encoding="utf-8")
    region = _region(text, "SYNC-5D-ONPATH")
    # pos: the local-only safe-delete sequence is intact
    assert "pull --ff-only" in region
    assert "git branch -d" in region
    assert "safe-delete" in region
    # neg: this path NEVER issues the origin remote delete
    assert "push origin --delete" not in region


# ── CR1/CR2 (ADR-053) — 4b runs the actuator BEFORE the local delete; gh-gated -D ──


def test_cr1_4b_actuator_precedes_local_branch_delete():
    # SKILL.md bash sites are not statically executed, so pin the ordering by doc-guard
    # (BC-PROJ-7): in the 4b region, the actuator invocation must appear BEFORE any
    # `git branch -d/-D` — else the actuator's Signal-B re-verify reads a deleted local ref.
    region = _region(_SKILL.read_text(encoding="utf-8"), "SYNC-5D-4BORDER")
    i_actuator = region.find("remote_branch_delete.py")
    i_branch = region.find("git branch -d")
    assert i_actuator != -1, "4b must invoke remote_branch_delete.py"
    assert i_branch != -1, "4b must delete the local branch"
    assert i_actuator < i_branch, "the actuator must run BEFORE the local branch delete (CR1)"


def test_cr1_actuator_denies_when_local_ref_deleted():
    # The failure mode CR1 identified: if the local branch was already deleted, the
    # actuator's Signal-B re-verify (git cherry / rev-parse on the bare name) returns rc 128
    # -> Signal B False -> REFUSE, even with a MERGED PR. This is WHY §5d runs the actuator
    # BEFORE the local delete (pinned by test_cr1_4b_actuator_precedes_local_branch_delete).
    r = make_runner(remote_present={B}, local_ref_absent={B},
                    pr_state={B: "MERGED"}, pr_meta={B: {"number": 12, "mergedAt": "2026-07-01"}})
    res = act.run_remote_delete(runner=r, branch=B, default=DEFAULT)
    assert res["action"] == act.REFUSED and res["authorized"] is False
    assert res["evidence"]["signal_b"] is False
    assert not _pushes(r)


def test_cr2_4b_safe_delete_refusal_is_handled_without_force():
    # The 4b local delete must HANDLE a squash-merge safe-delete refusal (CR2) — with a STOP
    # hint, NOT a force-delete (the slice-008 never-force-delete floor is preserved; ADR-053).
    region = _region(_SKILL.read_text(encoding="utf-8"), "SYNC-5D-4BORDER")
    assert "git branch -d" in region          # safe-delete
    assert "git branch -D" not in region      # NEVER force-delete
    assert "squash" in region.lower()
    assert "refus" in region.lower()          # the refusal path is explicitly handled


# ── helper: extract a region-keyed doc-guard block from SKILL.md ──────────────


def _region(text: str, key: str) -> str:
    begin, end = f"{key}:BEGIN", f"{key}:END"
    i, j = text.find(begin), text.find(end)
    assert i != -1, f"region {key}:BEGIN marker missing from SKILL.md"
    assert j != -1 and j > i, f"region {key}:END marker missing/misordered in SKILL.md"
    return text[i:j]
