"""Shared git default-branch resolution (v2; extracted from v1 branch_workflow_audit).

Both ``scripts.lib.pulse_worktree_resolver`` and ``scripts.lib.stranded_slice_audit``
need the repo's default branch. v1 imported ``_resolve_default_branch`` from the
single-skill ``branch_workflow_audit`` — a shared tool depending on a single-skill
tool (wrong direction). In v2 it is a proper shared leaf helper: single-skill tools
import FROM here, never the reverse. Stdlib only (subprocess/pathlib) — a leaf.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(repo_root: Path | str, *args: str) -> subprocess.CompletedProcess[str]:
    """``git -C <repo_root> <args...>`` capturing stdout/stderr as text."""
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def resolve_default_branch(repo_root: Path | str) -> str | None:
    """Resolve the repo's default branch.

    Primary: ``git symbolic-ref refs/remotes/origin/HEAD`` → strip the
    ``refs/remotes/origin/`` prefix. Fallback: ``git config init.defaultBranch``.
    Returns ``None`` if neither resolves (caller maps to a usage error).
    """
    result = run_git(repo_root, "symbolic-ref", "refs/remotes/origin/HEAD")
    if result.returncode == 0:
        ref = result.stdout.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref[len(prefix):]
    result = run_git(repo_root, "config", "init.defaultBranch")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None
