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
    """``git -C <repo_root> <args...>`` capturing stdout/stderr as UTF-8 text.

    THE single git runner shared across the helpers that shell out to git
    (``pulse_worktree_resolver``, ``stranded_slice_audit``, ``wt_root_audit``,
    ``stale_branch_classifier``). ``encoding="utf-8", errors="replace"`` is
    load-bearing (slice-090 / BB-25): without ``encoding=``, ``text=True`` decodes
    the child's pipe with ``locale.getpreferredencoding(False)`` — cp1252 on Windows —
    which mojibakes or raises ``UnicodeDecodeError`` on a non-ASCII branch / worktree /
    ref name; ``errors="replace"`` degrades a genuinely non-UTF-8 byte to U+FFFD
    instead of crashing the pipe-reader thread. A missing git binary raises
    ``FileNotFoundError`` — callers that need a softer failure wrap this.
    """
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def resolve_default_branch(repo_root: Path | str) -> str | None:
    """Resolve the repo's default branch.

    Tries, in order, until one resolves:
      1. ``git symbolic-ref refs/remotes/origin/HEAD`` → strip the
         ``refs/remotes/origin/`` prefix (authoritative — exists after a clone).
      2. ``git config init.defaultBranch`` (the user's declared intent).
      3. A conventional trunk ref that actually EXISTS — ``main`` then ``master``
         (a local-only repo with commits but no ``origin``).
      4. ``git symbolic-ref --short HEAD`` — the current / just-created branch, which IS
         the default at repo birth: a fresh ``git init`` with ``init.defaultBranch`` unset
         and no commits yet, where steps 1–3 all whiff and no ``main``/``master`` ref
         exists. This step is what stops BRANCH-1 / NAW-1 from spuriously failing on a
         user's very first slice (it returns the unborn branch name).
    Returns ``None`` only when git itself is unusable (not a repo / git binary missing).
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

    for candidate in ("main", "master"):
        probe = run_git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}")
        if probe.returncode == 0:
            return candidate

    head = run_git(repo_root, "symbolic-ref", "--short", "HEAD")
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()

    return None
