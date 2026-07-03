"""local_branch_delete.py -- the named ACTUATOR for the informed LOCAL slice-branch
force-delete (slice-057 / SC-092 / ADR-054).

This is the ONE and ONLY path that force-removes a local slice branch. It exists
because, on this project's squash-merge model, `/commit-slice` local cleanup
(`--sync-after-pr` 4a/4b) leaves the finished local branch behind: `git branch -d`
(the safe delete) legitimately refuses a squash-merged branch as "not fully merged"
because the squash rewrote history and severed local ancestry -- even though the PR
truly merged.

It is the LOCAL sibling of `remote_branch_delete.py` (slice-054) and mirrors its shape:

  * **DECIDE/ACTUATE split.** Authorization is NOT re-derived here as a second hand-written
    AND-chain -- the actuator RE-CALLS the single-sourced ``authorize_remote_delete`` from
    ``resolve_sync_target`` at point-of-use. The primary factor is the AUTHORITATIVE gh PR
    merged-state, independent of local topology (slice-054 B2/M1).
  * **FAIL SAFE / DENY BY DEFAULT (Saltzer & Schroeder).** The force branch-delete is
    permitted ONLY on a positive ``authorized:true`` verdict. Not-authorized (non-MERGED /
    gh-absent / non-GitHub / worktree-backed / non-origin / non-slice branch) => REFUSE:
    non-zero exit, ZERO force op. This preserves the slice-008 never-force floor for the
    genuinely-unmerged case.
  * **STRUCTURED pre-check, no stderr parsing (M3).** An already-absent local ref is told
    apart from a "not fully merged" refusal by ``git rev-parse --verify --quiet
    refs/heads/<branch>`` -- absent => idempotent no-op (zero gh call, zero force op), so an
    idempotent re-run never becomes a spurious failure.
  * **Fail-visible.** A force op that itself errors => STOP surfacing the reason (never
    swallowed).

Recoverability: unlike the irreversible remote ``push --delete``, a local branch delete is
RECOVERABLE -- the commits are on the integration branch + git reflog -- so no re-runnable
literal recovery command is emitted (the reason names what to inspect instead). The force
flag therefore appears EXACTLY ONCE in this file (the one blessed subprocess arg), which
``forbidden_flag_audit`` scans and permits via a line-scoped, single-use exception; every
other force flag in this file (or a copied sentinel in any other file) still FAILs.

All git/gh access routes through ONE injected ``runner(argv) -> CompletedProcess`` (the
real CLI binds it to ``subprocess.run(cwd=repo_root)``; tests inject a fake), so the
noop / safe-delete / refuse / force / fail-visible arms are unit-testable without a real
repo or gh.

Exit codes (CLI): 0 = safe-deleted OR force-deleted OR idempotent no-op ·
3 = REFUSED (not authorized -- STOP) · 4 = force op failed (fail-visible) · 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

# --- single-skill import bootstrap ---
_HERE = Path(__file__).resolve().parent                 # <plugin>/skills/commit-slice/scripts
_REPO = _HERE.parents[2]                                 # -> <plugin>
for _p in (str(_HERE), str(_REPO)):                      # _HERE: sibling resolve_sync_target import
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.lib import _stdout
from scripts.lib._git_default_branch import resolve_integration_branch
# Single authorization home: the actuator RE-USES the resolver's function -- one
# definition, one source of truth, never a second hand-written AND-chain (slice-054 B2).
from resolve_sync_target import (  # noqa: E402
    _main_tree,
    _worktree_backed,
    authorize_remote_delete,
)

__all__ = ["run_local_delete", "main"]

Runner = Callable[[list], subprocess.CompletedProcess]

# Result actions
NOOP_ABSENT = "noop-already-absent"
SAFE_DELETED = "safe-deleted"
FORCE_DELETED = "force-deleted"
REFUSED = "refused"
FORCE_DELETE_FAILED = "force-delete-failed"


def _exit_for(result: dict) -> int:
    """Map a run_local_delete result dict to the CLI exit code (single contract home)."""
    action = result.get("action")
    if action in (SAFE_DELETED, FORCE_DELETED, NOOP_ABSENT):
        return 0
    if action == REFUSED:
        return 3
    if action == FORCE_DELETE_FAILED:
        return 4
    return 2


def run_local_delete(
    *,
    runner: Runner,
    branch: str,
    default: str,
    remote: str = "origin",
    worktree_backed: bool = False,
) -> dict:
    """Actuate (or refuse) the informed local-branch force-delete. Returns a result dict with
    ``action`` in {noop-already-absent, safe-deleted, refused, force-deleted, force-delete-failed},
    plus ``deleted``, ``authorized``, ``reason``, and ``evidence``.

    Order is load-bearing: structured absence pre-check -> safe delete -> (on refusal only)
    authoritative authorization -> force op. The force op runs ONLY on ``authorized:true``.
    """
    # 1. Structured pre-check (M3): an already-absent local ref is an idempotent no-op --
    #    zero gh call, zero force op. This is what distinguishes it from a "not fully merged"
    #    refusal without fragile stderr parsing.
    present = runner(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if present.returncode != 0:
        return {
            "action": NOOP_ABSENT,
            "deleted": False,
            "authorized": None,
            "reason": f"local branch {branch} already absent -- idempotent no-op, zero force op",
            "evidence": {},
        }

    # 2. Safe delete first. A clean success short-circuits (no authorization needed, no force).
    safe = runner(["git", "branch", "-d", branch])
    if safe.returncode == 0:
        return {
            "action": SAFE_DELETED,
            "deleted": True,
            "authorized": None,
            "reason": f"safe-deleted local branch {branch} (fully merged)",
            "evidence": {},
        }

    # 3. Safe delete refused (the squash-merge "not fully merged" case). Authorize at
    #    point-of-use via the single-sourced authoritative gh-MERGED verdict. DENY by default.
    authz = authorize_remote_delete(
        runner, branch, default, remote=remote, worktree_backed=worktree_backed
    )
    ev = authz["evidence"]
    if not authz["authorized"]:
        return {
            "action": REFUSED,
            "deleted": False,
            "authorized": False,
            "reason": (
                f"safe-delete refused for {branch} and the force branch-delete is NOT authorized "
                f"({authz['reason']}). STOP -- inspect `git log {default}..{branch}`; the local branch "
                f"is untouched. Never force without an authoritative gh MERGED verdict."
            ),
            "evidence": ev,
        }

    # 4. Authorized. The ONE blessed force branch-delete (line-scoped audit exception).
    forced = runner(["git", "branch", "-D", branch])  # forbidden-flag-audit:allow=branch_force_delete
    if forced.returncode != 0:
        return {
            "action": FORCE_DELETE_FAILED,
            "deleted": False,
            "authorized": True,
            "reason": (
                f"the authorized force branch-delete of {branch} FAILED (rc={forced.returncode}): "
                f"{(forced.stderr or '').strip()}. NOT swallowed -- inspect and remove the local "
                f"branch manually once satisfied."
            ),
            "evidence": ev,
        }
    return {
        "action": FORCE_DELETED,
        "deleted": True,
        "authorized": True,
        "reason": (
            f"force branch-deleted {branch} (gh PR #{ev.get('pr_number')} MERGED "
            f"{ev.get('merged_at')})"
        ),
        "evidence": ev,
    }


# ----------------------------- CLI -----------------------------


def _real_runner(repo_root: str) -> Runner:
    def run(argv):
        return subprocess.run([str(a) for a in argv], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    return run


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local_branch_delete",
        description="Named actuator: the ONLY path that force-removes a local slice branch, "
                    "gated by the single-sourced authorize_remote_delete (gh PR MERGED primary; "
                    "deny-by-default). Safe delete is tried first; the force op runs only when "
                    "authorized.",
    )
    p.add_argument("--branch", required=True, help="the slice branch to delete (slice/NNN-name)")
    p.add_argument("--default", default=None, help="integration branch (resolved if omitted)")
    p.add_argument("--repo-root", default=".", help="git/gh cwd (default: cwd)")
    p.add_argument("--remote", default="origin", help="remote (origin-only enforced; default origin)")
    p.add_argument("--json", action="store_true", help="emit the result as JSON (default: JSON)")
    return p


def main(argv: list | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    runner = _real_runner(args.repo_root)

    default = args.default or resolve_integration_branch(args.repo_root)
    if not default:
        sys.stderr.write("local_branch_delete: could not resolve the integration branch.\n")
        return 2

    # Re-derive worktree-backing independently (defense-in-depth); never force a branch
    # checked out in a live worktree of THIS clone.
    backed = _worktree_backed(runner, _main_tree(runner))
    result = run_local_delete(
        runner=runner, branch=args.branch, default=default, remote=args.remote,
        worktree_backed=args.branch in backed,
    )
    sys.stdout.write(json.dumps(result) + "\n")
    return _exit_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
