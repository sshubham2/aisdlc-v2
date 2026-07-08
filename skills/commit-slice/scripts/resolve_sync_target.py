"""resolve_sync_target.py — AC4 cleanup-from-anywhere target resolver (slice-008).

``/commit-slice --sync-after-pr`` used to require you to ``cd`` into the slice
worktree (it cleaned up the CURRENT branch). This resolver lets it run from the MAIN
tree: it answers *which* slice to clean, then SKILL.md 5d re-runs the two-signal gate
and owns the (gated, safe) delete. **RESOLVE-ONLY — this script never deletes.**

Resolution precedence:
  1. **explicit** ``--slice slice-NNN`` -> ``active_slice.resolve_slice_by_id`` (archive
     aware); targets it regardless of worktree-backing (the escape hatch for cleaning a
     worktree-backed merged slice from the main tree).
  2. **on-branch** (HEAD is a ``slice/*`` branch) -> resolve self (back-compat with
     today's on-branch 5d; no vault lookup required).
  3. **auto-detect** (from the main tree): enumerate local ``slice/*`` refs; EXCLUDE any
     that are worktree-backed in a worktree other than the main tree — a live
     worktree-backed slice is IN-FLIGHT, never an auto cleanup target [M4] (the same
     parallel-vs-orphan split ``stale_branch_classifier`` draws); run the two-signal
     merged detection over the worktree-LESS survivors; exactly one merged -> auto-pick;
     >1 -> AMBIGUOUS (SKILL.md asks); zero -> none.

Merge-state classifier (slice-054 / ADR-051, supersedes the old two-signal AND):
  * Signal A — the remote branch is gone (``git ls-remote --exit-code origin <b>`` != 0)
    — now a TOPOLOGY DISCRIMINATOR, not a merge predicate.
  * Signal B — the slice commits are on ``origin/<default>``: Pass-1 ``git cherry`` has
    no ``+`` lines; Pass-2 fallback detects a squash-merge by file-set + tree equality.
  ``classify_merge_state`` -> one of {unmerged, merged-remote-absent (auto-delete ON),
  merged-remote-lingering (auto-delete OFF), in-flight-excluded (worktree-backed)}.
  ``is_merged`` is retained as a back-compat wrapper but is HONESTLY WIDENED (ADR-051):
  it is now True for ``merged-remote-lingering`` (remote present), where it once required
  the remote gone. Auto-detect (which consumes ``is_merged``) therefore now also finds a
  merged slice whose remote branch still lingers — the SC-018 auto-delete-OFF fix.

Authoritative remote-delete authorization (slice-054 / ADR-052):
  ``authorize_remote_delete`` is the SINGLE authorization function (B2/M1). The
  irreversible ``git push origin --delete`` is authorized ONLY by the AUTHORITATIVE gh PR
  merged-state (``gh pr view <branch> --json state,mergedAt`` == MERGED) as the PRIMARY
  factor; Signal B, a fresh remote-present check, not-worktree-backed, origin-only, and the
  slice-regex are conjunctive DEFENSE-IN-DEPTH — never the sole authorizer. gh absent or a
  non-GitHub origin FAILS CLOSED (authorized:false), mirroring pr_flow.py's degrade. It is
  called at classify-time to fill the resolver plan AND re-called by the actuator at
  point-of-use — ONE authorization home, no two AND-chains that can disagree.

All git/gh access routes through ONE injected ``runner(argv) -> CompletedProcess`` (the
real CLI binds it to ``subprocess.run(cwd=repo_root)``; tests inject a fake), so the
classifier, authorization, and M4 exclusion logic are unit-testable without a real
worktree, remote, or gh.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = Path(__file__).resolve().parents[3]  # <plugin>/skills/commit-slice/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._git_default_branch import resolve_integration_branch
from scripts.lib.active_slice import resolve_slice_by_id
from scripts.lib.pulse_worktree_resolver import _parse_worktree_porcelain

__all__ = [
    "resolve_target",
    "is_merged",
    "classify_merge_state",
    "authorize_remote_delete",
    "main",
    # merge-state enum
    "UNMERGED",
    "MERGED_REMOTE_ABSENT",
    "MERGED_REMOTE_LINGERING",
    "IN_FLIGHT_EXCLUDED",
]

Runner = Callable[[list], subprocess.CompletedProcess]

_SLICE_BRANCH_RE = re.compile(r"^slice/(\d+)-(.+)$")
_REFS_HEADS = "refs/heads/"
_SQUASH_PERF_BOUND = 500  # Pass-2 scan bound (mirrors SKILL.md 5d)

# --- merge-state enum (ADR-051) ---
UNMERGED = "unmerged"
MERGED_REMOTE_ABSENT = "merged-remote-absent"        # auto-delete ON (the classic path)
MERGED_REMOTE_LINGERING = "merged-remote-lingering"  # auto-delete OFF (SC-018)
IN_FLIGHT_EXCLUDED = "in-flight-excluded"            # worktree-backed -> never a cleanup target

# GitHub origin matcher (mirrors pr_flow.py's idiom; kept local to avoid a brittle
# sibling import — the actuator imports authorize_remote_delete from HERE, so the
# authorization composition still has exactly one home).
_GITHUB_RE = re.compile(r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?/?$")
_PR_STATE_MERGED = "MERGED"


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").rstrip("/")


def _branch_to_slice(branch: str) -> tuple[str | None, str | None]:
    """``slice/008-name`` -> (``slice-008``, ``slice-008-name``); else (None, None)."""
    m = _SLICE_BRANCH_RE.match(branch or "")
    if not m:
        return None, None
    return f"slice-{m.group(1)}", f"slice-{m.group(1)}-{m.group(2)}"


def _folder_to_branch(folder: str) -> str | None:
    """``slice-008-name`` -> ``slice/008-name``."""
    if not folder.startswith("slice-"):
        return None
    return "slice/" + folder[len("slice-"):]


# ----------------------------- two-signal detection -----------------------------


def _signal_a_remote_absent(runner: Runner, branch: str) -> bool:
    return runner(["git", "ls-remote", "--exit-code", "origin", branch]).returncode != 0


def _signal_b_on_default(runner: Runner, branch: str, default: str) -> bool:
    # Pass 1: cherry — no `+` lines means every slice commit is already on the default.
    cherry = runner(["git", "cherry", f"origin/{default}", branch])
    if cherry.returncode == 0 and not any(
        ln.startswith("+ ") for ln in (cherry.stdout or "").splitlines()
    ):
        return True
    # Pass 2: squash-merge fallback — the slice's file-set landed as one commit whose
    # tree at those paths equals the slice tip's tree.
    base = runner(["git", "merge-base", f"origin/{default}", branch])
    if base.returncode != 0 or not base.stdout.strip():
        return False
    base_sha = base.stdout.strip()
    files_cp = runner(["git", "diff", "--name-only", f"{base_sha}..{branch}"])
    files = [f for f in (files_cp.stdout or "").splitlines() if f.strip()]
    if not files:
        return False
    slice_tree = runner(["git", "rev-parse", f"{branch}^{{tree}}"]).stdout.strip()
    log_cp = runner(["git", "rev-list", f"{base_sha}..origin/{default}"])
    commits = [c for c in (log_cp.stdout or "").splitlines() if c.strip()]
    if len(commits) > _SQUASH_PERF_BOUND:
        return False
    fileset = set(files)
    for c in commits:
        touched_cp = runner(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", c])
        touched = {t for t in (touched_cp.stdout or "").splitlines() if t.strip()}
        if fileset <= touched:
            ctree = runner(["git", "rev-parse", f"{c}^{{tree}}"]).stdout.strip()
            if ctree and ctree == slice_tree:
                return True
    return False


def classify_merge_state(
    runner: Runner, branch: str, default: str, *, worktree_backed: bool = False
) -> str:
    """Classify the merge topology of ``branch`` (ADR-051). RESOLVE-ONLY (no delete).

    Signal A discriminates topology (remote absent vs lingering); Signal B is the merge
    signal. A worktree-backed slice is IN-FLIGHT and short-circuits to in-flight-excluded.
    NO gh call here — this stays cheap enough to run over every auto-detect survivor.
    """
    if worktree_backed:
        return IN_FLIGHT_EXCLUDED
    if not _signal_b_on_default(runner, branch, default):
        return UNMERGED
    return MERGED_REMOTE_ABSENT if _signal_a_remote_absent(runner, branch) else MERGED_REMOTE_LINGERING


def is_merged(runner: Runner, branch: str, default: str) -> bool:
    """Back-compat wrapper, HONESTLY WIDENED (ADR-051, supersedes ADR-047's two-signal AND).

    Now True for BOTH merged topologies — including ``merged-remote-lingering`` (Signal
    A=NO, remote present), where the old predicate required the remote gone. Consumed by
    ``resolve_target``'s auto-detect, so auto-detect now also picks a merged slice whose
    remote branch still lingers (the SC-018 auto-delete-OFF fix); the downstream irreversible
    remote delete is separately gated by ``authorize_remote_delete`` (gh MERGED primary).
    """
    return classify_merge_state(runner, branch, default) in (
        MERGED_REMOTE_ABSENT,
        MERGED_REMOTE_LINGERING,
    )


# ----------------------------- authoritative authorization (ADR-052) -----------------------------


def _gh_present(runner: Runner) -> bool:
    """True iff ``gh`` is invocable (a missing binary raises FileNotFoundError -> False)."""
    try:
        return runner(["gh", "--version"]).returncode == 0
    except FileNotFoundError:
        return False


def _parse_github_remote(url: str) -> tuple[str, str] | None:
    m = _GITHUB_RE.match((url or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _origin_github(runner: Runner) -> tuple[str, str] | None:
    """``(owner, repo)`` for a github.com origin, else None (non-GitHub / no origin)."""
    cp = runner(["git", "remote", "get-url", "origin"])
    if cp.returncode != 0:
        return None
    return _parse_github_remote(cp.stdout)


def _gh_pr_merged_state(runner: Runner, branch: str) -> tuple[str | None, str | None, object]:
    """Authoritative PR state via ``gh pr view <branch> --json number,state,mergedAt``.

    Returns ``(state, merged_at, pr_number)``; any failure / unparseable output -> all None
    (the caller treats a non-MERGED state as unauthorized, so this fails closed)."""
    cp = runner(["gh", "pr", "view", branch, "--json", "number,state,mergedAt"])
    if cp.returncode != 0:
        return None, None, None
    try:
        data = json.loads(cp.stdout or "{}")
    except (ValueError, TypeError):
        return None, None, None
    return data.get("state"), data.get("mergedAt"), data.get("number")


def authorize_remote_delete(
    runner: Runner,
    branch: str,
    default: str,
    *,
    remote: str = "origin",
    worktree_backed: bool = False,
) -> dict:
    """The SINGLE authorization function for the irreversible remote slice-branch delete
    (B2/M1, ADR-052). Returns ``{authorized: bool, evidence: dict, reason: str}``.

    PRIMARY factor: ``gh pr view <branch>`` state == MERGED (authoritative — GitHub's own
    merged-state, independent of local git topology). Conjunctive DEFENSE-IN-DEPTH: origin
    scope, slice-regex, not-worktree-backed, and Signal B. FAILS CLOSED (authorized:false,
    zero implication of a delete) when gh is absent OR origin is non-GitHub OR state != MERGED
    — there is NO non-gh fallback (M-add-2: an OPEN PR protects a slice in-flight in ANOTHER
    clone that local worktree state cannot see). ``remote_present`` is INFORMATIONAL (an
    already-absent remote is an idempotent no-op for the actuator, not an authz failure).
    """
    ev = {
        "pr_number": None,
        "pr_state": None,
        "merged_at": None,
        "remote_present": None,
        "worktree_backed": worktree_backed,
        "gh_present": None,
        "is_github": None,
        "origin_ok": None,
        "slice_ok": None,
        "signal_b": None,
    }

    def deny(reason: str) -> dict:
        return {"authorized": False, "evidence": ev, "reason": reason}

    ev["origin_ok"] = origin_ok = (remote == "origin")
    if not origin_ok:
        return deny(f"remote {remote!r} is not 'origin' — remote delete is origin-scoped only")
    ev["slice_ok"] = slice_ok = bool(_SLICE_BRANCH_RE.match(branch or ""))
    if not slice_ok:
        return deny(f"branch {branch!r} is not a slice/NNN- branch")
    if worktree_backed:
        return deny(f"{branch} is worktree-backed (in-flight in THIS clone) — never delete its remote")

    ev["gh_present"] = gh_present = _gh_present(runner)
    if not gh_present:
        return deny("gh CLI unavailable — cannot obtain authoritative PR merged-state; "
                    "FAIL CLOSED, no non-gh fallback (M-add-2)")
    owner_repo = _origin_github(runner)
    ev["is_github"] = is_github = owner_repo is not None
    if not is_github:
        return deny("origin is not a GitHub remote — no authoritative PR merged-state; "
                    "FAIL CLOSED, no non-gh fallback (M-add-2)")

    # PRIMARY: authoritative gh PR merged-state.
    state, merged_at, number = _gh_pr_merged_state(runner, branch)
    ev["pr_state"], ev["merged_at"], ev["pr_number"] = state, merged_at, number
    if state != _PR_STATE_MERGED:
        return deny(f"gh PR state is {state or 'unknown'} (not MERGED) — an OPEN PR means the "
                    f"slice may be in-flight (possibly in another clone); STOP, zero delete")

    # DEFENSE-IN-DEPTH: Signal B (Pass-2 tree-equality preserved — m2, never weakened).
    ev["signal_b"] = signal_b = _signal_b_on_default(runner, branch, default)
    if not signal_b:
        return deny("Signal B defense-in-depth failed — slice commits not found on "
                    f"origin/{default} despite a MERGED PR state; STOP")

    ev["remote_present"] = not _signal_a_remote_absent(runner, branch)  # informational
    return {
        "authorized": True,
        "evidence": ev,
        "reason": f"authorized: gh PR MERGED (#{number}, {merged_at}) + defense-in-depth",
    }


# ----------------------------- worktree-backing split -----------------------------


def _slice_branches(runner: Runner) -> list[str]:
    cp = runner(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/slice/"])
    return [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]


def _worktree_backed(runner: Runner, main_tree: str) -> dict[str, str]:
    """Map ``slice/* branch -> worktree path`` for every slice branch checked out in a
    worktree OTHER than the main tree (these are the in-flight slices to EXCLUDE)."""
    cp = runner(["git", "worktree", "list", "--porcelain"])
    main_norm = _norm(main_tree)
    backed: dict[str, str] = {}
    for block in _parse_worktree_porcelain(cp.stdout or ""):
        ref = block.get("branch", "")
        if not ref.startswith(_REFS_HEADS):
            continue
        short = ref[len(_REFS_HEADS):]
        if not short.startswith("slice/"):
            continue
        wt_path = _norm(block.get("worktree", ""))
        if wt_path == main_norm:
            continue  # a slice branch checked out in the main tree is not "other"
        backed[short] = block.get("worktree", "")
    return backed


# ----------------------------- resolution -----------------------------


def _plan(status, *, resolution=None, slice_id=None, branch=None, worktree_path=None,
          candidates=None, reason="") -> dict:
    return {
        "action": "resolve-sync-target",
        "status": status,
        "resolution": resolution,
        "slice": slice_id,
        "branch": branch,
        "worktree_path": worktree_path,
        "candidates": candidates or [],
        "reason": reason,
        # slice-054 (M1): the classification computed ONCE in Python and consumed by §5d
        # (which branches PURELY on `state`, no bash recompute). `remote_delete_authorized`
        # + `evidence` come from the single-sourced authorize_remote_delete.
        "state": None,
        "remote_delete_authorized": None,
        "remote_branch": None,
        "evidence": {},
    }


def _attach_state(plan: dict, runner: Runner, default: str, backed: dict) -> dict:
    """Enrich a RESOLVED plan with the merge-state classification + the single-sourced
    remote-delete authorization for its branch (M1/B2). The gh probe runs ONLY for the one
    resolved branch — never per auto-detect survivor. Non-resolved plans pass through."""
    if plan.get("status") != "resolved" or not plan.get("branch"):
        return plan
    branch = plan["branch"]
    wt_backed = branch in backed
    plan["state"] = classify_merge_state(runner, branch, default, worktree_backed=wt_backed)
    authz = authorize_remote_delete(runner, branch, default, worktree_backed=wt_backed)
    plan["remote_delete_authorized"] = authz["authorized"]
    plan["remote_branch"] = branch
    plan["evidence"] = authz["evidence"]
    return plan


def resolve_target(
    *,
    runner: Runner,
    default: str,
    main_tree: str,
    current_branch: str | None = None,
    explicit_slice: str | None = None,
    vault: str | None = None,
) -> dict:
    """Resolve which slice ``--sync-after-pr`` should clean. Resolve-only."""
    backed = _worktree_backed(runner, main_tree)

    # 1. explicit --slice (archive-aware; targets regardless of worktree-backing)
    if explicit_slice:
        if not vault:
            return _plan("none", reason=f"--slice {explicit_slice} needs a vault to resolve")
        info = resolve_slice_by_id(vault, explicit_slice)
        if not info:
            return _plan("none", reason=f"slice {explicit_slice} not found in the vault")
        branch = _folder_to_branch(info["folder"])
        return _attach_state(
            _plan("resolved", resolution="explicit", slice_id=info["slice"], branch=branch,
                  worktree_path=backed.get(branch)),
            runner, default, backed)

    # 2. on a slice branch -> resolve self (back-compat)
    if current_branch and _SLICE_BRANCH_RE.match(current_branch):
        slice_id, _folder = _branch_to_slice(current_branch)
        return _attach_state(
            _plan("resolved", resolution="on-branch", slice_id=slice_id, branch=current_branch,
                  worktree_path=backed.get(current_branch)),
            runner, default, backed)

    # 3. auto-detect from the main tree
    survivors = [b for b in _slice_branches(runner) if b not in backed]
    merged = [b for b in survivors if is_merged(runner, b, default)]
    if not merged:
        return _plan("none", candidates=survivors,
                     reason="no merged-and-not-in-flight slice found")
    if len(merged) > 1:
        return _plan("ambiguous", candidates=sorted(merged),
                     reason="multiple merged slices — pass --slice slice-NNN to choose")
    branch = merged[0]
    slice_id, _folder = _branch_to_slice(branch)
    return _attach_state(
        _plan("resolved", resolution="auto", slice_id=slice_id, branch=branch,
              worktree_path=backed.get(branch)),
        runner, default, backed)


# ----------------------------- CLI -----------------------------


def _real_runner(repo_root: str) -> Runner:
    def run(argv):
        return subprocess.run([str(a) for a in argv], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    return run


def _current_branch(runner: Runner) -> str | None:
    cp = runner(["git", "symbolic-ref", "--short", "HEAD"])
    return cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else None


def _main_tree(runner: Runner) -> str:
    cp = runner(["git", "worktree", "list", "--porcelain"])
    for block in _parse_worktree_porcelain(cp.stdout or ""):
        if "worktree" in block:
            return block["worktree"]
    return "."


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resolve_sync_target",
        description="Resolve which slice /commit-slice --sync-after-pr should clean "
                    "(resolve-only; never deletes).",
    )
    p.add_argument("--repo-root", default=".", help="git cwd (default: cwd)")
    p.add_argument("--slice", dest="explicit_slice", default=None,
                   help="explicit slice id (slice-NNN) to target")
    p.add_argument("--default", default=None, help="default branch (resolved if omitted)")
    p.add_argument("--vault", default=None, help="vault root (for --slice by-id resolution)")
    p.add_argument("--json", action="store_true", help="emit JSON (default: JSON)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    runner = _real_runner(args.repo_root)
    # slice-022/061: a slice merges to the INTEGRATION branch (aisdlc-uat), so merged-detection
    # targets origin/<integration>, not the released trunk.
    default = args.default or resolve_integration_branch(args.repo_root)
    if not default:
        sys.stderr.write("resolve_sync_target: could not resolve the integration branch.\n")
        return 2
    plan = resolve_target(
        runner=runner, default=default, main_tree=_main_tree(runner),
        current_branch=_current_branch(runner), explicit_slice=args.explicit_slice,
        vault=args.vault,
    )
    plan["integration_branch"] = default  # slice-022: observable resolution (must-not-defer)
    sys.stdout.write(json.dumps(plan) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
