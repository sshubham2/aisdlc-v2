"""remote_branch_delete.py — the named ACTUATOR for the lingering remote slice-branch
delete (slice-054 / SC-018 / ADR-052).

This is the ONE and ONLY path that issues ``git push origin --delete <slice-branch>``.
It exists because ``/commit-slice --sync-after-pr`` must clean up a MERGED slice whose
remote head branch still lingers on repos where GitHub head-branch auto-delete is OFF —
an irreversible, shared-remote destructive op that the old local ``git branch -d``
backstop (git refuses an unmerged branch) does NOT cover.

Load-bearing invariants (from the dual-Critic B1/B2/M-add-1/M-add-2/M4, ADR-052):
  * **DECIDE/ACTUATE split.** Authorization is NOT re-derived here as a second hand-written
    AND-chain — the actuator RE-CALLS the single-sourced ``authorize_remote_delete`` from
    ``resolve_sync_target`` at point-of-use (B2). The primary factor is the AUTHORITATIVE gh
    PR merged-state, genuinely independent of the local topology, so this re-verify adds real
    assurance (not just a TOCTOU guard on the same weak factor).
  * **FAIL CLOSED, no fallback.** Not-authorized (non-MERGED / gh-absent / non-GitHub /
    worktree-backed / non-origin / non-slice branch) => REFUSE: non-zero exit, ZERO git call
    (M-add-2 — an OPEN PR protects a slice in-flight in another clone this host cannot see).
  * **Idempotent.** Remote branch already absent => no-op, exit 0.
  * **Fail-visible.** A ``git push origin --delete`` non-zero => STOP printing the EXACT
    literal recovery command ``git push origin --delete <branch>`` (M4 — the branch is
    known here, so the residual is re-runnable even though auto-detect enumerates LOCAL refs).
  * **Origin-scoped.** The push is issued against ``origin`` only; the actuator hard-refuses
    any other remote (enforced inside ``authorize_remote_delete``).

All git/gh access routes through ONE injected ``runner(argv) -> CompletedProcess`` (the
real CLI binds it to ``subprocess.run(cwd=repo_root)``; tests inject a fake), so the
refuse / no-op / delete / fail-visible arms are unit-testable without a real remote or gh.

Exit codes (CLI): 0 = deleted OR idempotent no-op · 3 = REFUSED (not authorized) ·
4 = push failed (fail-visible, recovery command printed) · 2 = usage.
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
# Single authorization home (B2): the actuator RE-USES the resolver's function — one
# definition, one source of truth, never a second hand-written AND-chain.
from resolve_sync_target import (  # noqa: E402
    _main_tree,
    _worktree_backed,
    authorize_remote_delete,
)

__all__ = ["run_remote_delete", "main"]

Runner = Callable[[list], subprocess.CompletedProcess]

# Result actions
REFUSED = "refused"
NOOP_ABSENT = "noop-already-absent"
DELETED = "deleted"
PUSH_FAILED = "push-failed"


def run_remote_delete(
    *,
    runner: Runner,
    branch: str,
    default: str,
    remote: str = "origin",
    worktree_backed: bool = False,
) -> dict:
    """Actuate (or refuse) the lingering remote-branch delete. Returns a result dict with
    ``action`` in {refused, noop-already-absent, deleted, push-failed}, plus ``deleted``,
    ``authorized``, ``reason``, ``evidence``, and (on failure) ``recovery_command``."""
    authz = authorize_remote_delete(
        runner, branch, default, remote=remote, worktree_backed=worktree_backed
    )
    ev = authz["evidence"]
    if not authz["authorized"]:
        return {
            "action": REFUSED,
            "deleted": False,
            "authorized": False,
            "reason": authz["reason"],
            "evidence": ev,
        }

    # Authorized. An already-absent remote is a safe idempotent no-op (this is also the
    # auto-delete-ON topology, where §5d never routes here — but defend it anyway; AC5).
    if not ev.get("remote_present"):
        return {
            "action": NOOP_ABSENT,
            "deleted": False,
            "authorized": True,
            "reason": f"remote branch {branch} already absent — idempotent no-op, zero push --delete",
            "evidence": ev,
        }

    recovery = f"git push {remote} --delete {branch}"
    push = runner(["git", "push", remote, "--delete", branch])
    if push.returncode != 0:
        return {
            "action": PUSH_FAILED,
            "deleted": False,
            "authorized": True,
            "reason": (f"`{recovery}` FAILED (rc={push.returncode}): "
                       f"{(push.stderr or '').strip()}. Recover by re-running the EXACT command below."),
            "recovery_command": recovery,
            "evidence": ev,
        }
    return {
        "action": DELETED,
        "deleted": True,
        "authorized": True,
        "reason": f"deleted remote branch {branch} (gh PR #{ev.get('pr_number')} MERGED {ev.get('merged_at')})",
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
        prog="remote_branch_delete",
        description="Named actuator: the ONLY path that issues git push origin --delete "
                    "<slice-branch>, gated by the single-sourced authorize_remote_delete "
                    "(gh PR MERGED primary; fail-closed).",
    )
    p.add_argument("--branch", required=True, help="the slice branch to delete (slice/NNN-name)")
    p.add_argument("--default", default=None, help="integration branch (resolved if omitted)")
    p.add_argument("--repo-root", default=".", help="git/gh cwd (default: cwd)")
    p.add_argument("--remote", default="origin", help="remote (origin-only enforced; default origin)")
    p.add_argument("--json", action="store_true", help="emit the result as JSON (default: JSON)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    runner = _real_runner(args.repo_root)

    default = args.default or resolve_integration_branch(args.repo_root)
    if not default:
        sys.stderr.write("remote_branch_delete: could not resolve the integration branch.\n")
        return 2

    # Re-derive worktree-backing independently (defense-in-depth); never delete a branch
    # checked out in a live worktree of THIS clone.
    backed = _worktree_backed(runner, _main_tree(runner))
    result = run_remote_delete(
        runner=runner, branch=args.branch, default=default, remote=args.remote,
        worktree_backed=args.branch in backed,
    )
    sys.stdout.write(json.dumps(result) + "\n")
    if result["action"] == DELETED or result["action"] == NOOP_ABSENT:
        return 0
    if result["action"] == REFUSED:
        return 3
    return 4  # PUSH_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
