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

Two-signal merged detection (reused, faithful to SKILL.md 5d):
  * Signal A — the remote branch is gone (``git ls-remote --exit-code origin <b>`` != 0).
  * Signal B — the slice commits are on ``origin/<default>``: Pass-1 ``git cherry`` has
    no ``+`` lines; Pass-2 fallback detects a squash-merge by file-set + tree equality.
  Both signals required. (As today, this assumes head-branch auto-delete is ON;
  hardening the auto-delete-OFF case is SC-018, deferred at slice-008 TRI-1.)

All git access routes through ONE injected ``runner(argv) -> CompletedProcess`` (the
real CLI binds it to ``subprocess.run(cwd=repo_root)``; tests inject a fake), so the
M4 exclusion + ambiguity logic is unit-testable without real worktrees or a remote.
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
from scripts.lib._git_default_branch import resolve_default_branch
from scripts.lib.active_slice import resolve_slice_by_id
from scripts.lib.pulse_worktree_resolver import _parse_worktree_porcelain

__all__ = ["resolve_target", "is_merged", "main"]

Runner = Callable[[list], subprocess.CompletedProcess]

_SLICE_BRANCH_RE = re.compile(r"^slice/(\d+)-(.+)$")
_REFS_HEADS = "refs/heads/"
_SQUASH_PERF_BOUND = 500  # Pass-2 scan bound (mirrors SKILL.md 5d)


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


def is_merged(runner: Runner, branch: str, default: str) -> bool:
    """Two-signal AND: remote branch gone (A) AND slice commits on origin/<default> (B)."""
    return _signal_a_remote_absent(runner, branch) and _signal_b_on_default(runner, branch, default)


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
    }


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
        return _plan("resolved", resolution="explicit", slice_id=info["slice"], branch=branch,
                     worktree_path=backed.get(branch))

    # 2. on a slice branch -> resolve self (back-compat)
    if current_branch and _SLICE_BRANCH_RE.match(current_branch):
        slice_id, _folder = _branch_to_slice(current_branch)
        return _plan("resolved", resolution="on-branch", slice_id=slice_id, branch=current_branch,
                     worktree_path=backed.get(current_branch))

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
    return _plan("resolved", resolution="auto", slice_id=slice_id, branch=branch,
                 worktree_path=backed.get(branch))


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
    default = args.default or resolve_default_branch(args.repo_root)
    if not default:
        sys.stderr.write("resolve_sync_target: could not resolve the default branch.\n")
        return 2
    plan = resolve_target(
        runner=runner, default=default, main_tree=_main_tree(runner),
        current_branch=_current_branch(runner), explicit_slice=args.explicit_slice,
        vault=args.vault,
    )
    sys.stdout.write(json.dumps(plan) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
