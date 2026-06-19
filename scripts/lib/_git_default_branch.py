"""Shared git default-branch resolution (v2; extracted from v1 branch_workflow_audit).

Both ``scripts.lib.pulse_worktree_resolver`` and ``scripts.lib.stranded_slice_audit``
need the repo's default branch. v1 imported ``_resolve_default_branch`` from the
single-skill ``branch_workflow_audit`` — a shared tool depending on a single-skill
tool (wrong direction). In v2 it is a proper shared leaf helper: single-skill tools
import FROM here, never the reverse. Stdlib only (subprocess/pathlib) — a leaf.
"""
from __future__ import annotations

import subprocess
import sys
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


def resolve_integration_branch(repo_root: Path | str, *, log: bool = True) -> str | None:
    """Resolve the INTEGRATION branch — where new slice work integrates (slice-022).

    Under the uat/master release model: slices branch from + merge to ``uat``;
    ``master`` is released-only (advanced only by the deliberate versioned release
    cut). This answers "which branch does new work integrate onto":

      * ``uat`` when ``refs/heads/uat`` verifies, else
      * degrade VISIBLY to ``resolve_default_branch`` (the released trunk) — the
        degrade is logged to stderr (must-not-defer: observable / why-degraded),
        never a silent fallback, and
      * ``None`` only when git itself is unusable (``resolve_default_branch`` whiffs).

    A SIBLING of ``resolve_default_branch`` — that function is UNCHANGED: its
    trunk-reasoning consumers (``pulse_worktree_resolver`` / ``stranded_slice_audit``
    / ``wt_root_audit``) must keep seeing the released trunk, not the integration
    branch. Read-only callers may use the degraded value; a WRITE caller that would
    advance the released trunk (``/commit-slice --merge``) must instead REFUSE on the
    degrade — see the ``--integration --write`` CLI guard below (M3).
    """
    probe = run_git(repo_root, "rev-parse", "--verify", "--quiet", "refs/heads/uat")
    if probe.returncode == 0:
        return "uat"
    default = resolve_default_branch(repo_root)
    if log and default is not None:
        print(
            f"integration-branch: uat absent, using default '{default}' "
            f"(read-only degrade; a write path must refuse here)",
            file=sys.stderr,
        )
    return default


def _main(argv: list[str] | None = None) -> int:
    """CLI seam for SKILL.md call sites.

    ``--integration``          -> print the integration branch (uat or the degraded
                                  default); exit 2 when git is unusable.
    ``--integration --write``  -> WRITE-path guard (M3): print ``uat`` and exit 0
                                  ONLY when uat exists; on a uat-absent degrade
                                  REFUSE (exit 3, nothing on stdout) rather than let
                                  a write fall back to the released trunk.
    (no flag)                  -> print ``resolve_default_branch`` (back-compat).
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="_git_default_branch",
        description="Resolve the default / integration git branch for the slice loop.",
    )
    ap.add_argument("--integration", action="store_true",
                    help="resolve the INTEGRATION branch (uat, else the degraded default)")
    ap.add_argument("--write", action="store_true",
                    help="WRITE-path guard (M3): refuse (exit 3) on a uat-absent degrade")
    ap.add_argument("--repo-root", default=".", help="repo root to inspect (default: cwd)")
    args = ap.parse_args(argv)

    if args.integration:
        branch = resolve_integration_branch(args.repo_root)
        if branch is None:
            print("resolve_integration_branch: git unusable; cannot resolve a branch.",
                  file=sys.stderr)
            return 2
        if args.write and branch != "uat":
            print(
                "integration-branch: uat absent; refusing to advance the released "
                "trunk via a write path -- establish uat first (a read-only caller "
                "may degrade, a write caller may not).",
                file=sys.stderr,
            )
            return 3
        print(branch)
        return 0

    default = resolve_default_branch(args.repo_root)
    if default is None:
        print("resolve_default_branch: git unusable; cannot resolve a branch.",
              file=sys.stderr)
        return 2
    print(default)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
