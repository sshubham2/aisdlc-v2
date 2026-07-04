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

# slice-061 (SC-114): the pipeline's integration branch, namespaced so it cannot collide
# with a host project's own `uat` once the plugin is installed elsewhere. The name lives
# HERE, as a single constant, and every caller routes through resolve_integration_branch().
INTEGRATION_BRANCH = "aisdlc-uat"
LEGACY_INTEGRATION_BRANCH = "uat"
# release-genesis marks an ai-sdlc-managed repo (established with `uat` at slice-022). It is
# the discriminator (M2 / M-add-2) that keeps the legacy-`uat` back-compat arm from silently
# claiming a FRESH host's own `uat` branch: legacy `uat` is accepted ONLY when this tag exists.
GENESIS_TAG = "release-genesis"

# Once-per-process guard for the legacy-`uat` resolution note (m4 / M2): legacy `uat` is a
# VALID resolution, not a degrade, so a caller that resolves repeatedly WITHIN one process
# emits the concise back-compat nudge only once. (The SKILL call sites are one-shot
# subprocesses, so each invocation still prints it once — acceptable: it is a single
# stderr line, never on stdout, so captured base-SHAs / branch names are unaffected.)
_LEGACY_NOTE_EMITTED = False


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


def _branch_exists(repo_root: Path | str, name: str) -> bool:
    return run_git(repo_root, "rev-parse", "--verify", "--quiet",
                   f"refs/heads/{name}").returncode == 0


def _genesis_tag_exists(repo_root: Path | str) -> bool:
    """True iff a ``release-genesis`` tag exists — i.e. this is an ai-sdlc-managed repo."""
    return run_git(repo_root, "rev-parse", "--verify", "--quiet",
                   f"refs/tags/{GENESIS_TAG}").returncode == 0


def existing_integration_branch(repo_root: Path | str) -> str | None:
    """The pipeline's integration branch NAME, or ``None`` when none exists here.

    NON-degrading ordered probe — the SINGLE precedence point (slice-061), reused by
    ``resolve_integration_branch()`` (the degrading wrapper), the ``--integration
    --write`` guard, and ``release_advance_audit`` so the three sites cannot drift:

      1. ``aisdlc-uat`` when ``refs/heads/aisdlc-uat`` verifies (namespaced → ours by
         name — no host-collision risk, so no discriminator needed), else
      2. legacy ``uat`` when ``refs/heads/uat`` verifies AND a ``release-genesis`` tag
         exists (M2 / M-add-2: the tag proves this is an ai-sdlc-managed repo; a FRESH
         host whose own trunk/branch is coincidentally named ``uat`` has NO genesis tag,
         so the legacy arm is rejected → the write guard refuses rather than silently
         merging slice work into the host's branch — the exact collision this slice
         exists to prevent), else
      3. ``None`` — no integration branch here (git-unusable also whiffs to ``None``;
         callers that must distinguish the two re-probe ``resolve_default_branch``).

    Returns a NAME or ``None`` — it NEVER degrades to the released trunk (that is the
    wrapper's job). Keying the write guard on this (``None`` → refuse) rather than on
    name-equality (``branch != "uat"``) is load-bearing: name-equality would wrongly
    refuse a real ``aisdlc-uat`` ref and mis-handle a host trunk literally named ``uat``.
    """
    if _branch_exists(repo_root, INTEGRATION_BRANCH):
        return INTEGRATION_BRANCH
    if _branch_exists(repo_root, LEGACY_INTEGRATION_BRANCH) and _genesis_tag_exists(repo_root):
        return LEGACY_INTEGRATION_BRANCH
    return None


def resolve_integration_branch(repo_root: Path | str, *, log: bool = True) -> str | None:
    """Resolve the INTEGRATION branch — where new slice work integrates (slice-022/061).

    Under the release model: slices branch from + merge to the integration branch;
    ``master`` is released-only (advanced only by the deliberate versioned release
    cut). This answers "which branch does new work integrate onto":

      * ``aisdlc-uat`` when it exists (the namespaced name; slice-061), else
      * legacy ``uat`` when it exists in an ai-sdlc-managed repo (release-genesis tag
        present) — a VALID back-compat resolution, NOT a degrade; announced once per
        process on stderr (m4: legacy `uat` is correct, so frequent read-only callers
        stay quiet after the first note), else
      * degrade VISIBLY to ``resolve_default_branch`` (the released trunk) — the
        degrade is logged to stderr (must-not-defer: observable / why-degraded),
        never a silent fallback, and
      * ``None`` only when git itself is unusable (``resolve_default_branch`` whiffs).

    A SIBLING of ``resolve_default_branch`` — that function is UNCHANGED: its
    trunk-reasoning consumers (``pulse_worktree_resolver`` / ``stranded_slice_audit``
    / ``wt_root_audit``) must keep seeing the released trunk, not the integration
    branch. Read-only callers may use the degraded value; a WRITE caller that would
    advance the released trunk (``/commit-slice --merge``) must instead REFUSE on the
    degrade — see the ``--integration --write`` CLI guard below (M3 / M-add-2).
    """
    global _LEGACY_NOTE_EMITTED
    src = existing_integration_branch(repo_root)
    if src == INTEGRATION_BRANCH:
        return src
    if src == LEGACY_INTEGRATION_BRANCH:
        if log and not _LEGACY_NOTE_EMITTED:
            _LEGACY_NOTE_EMITTED = True
            print(
                f"integration-branch: resolving the legacy '{LEGACY_INTEGRATION_BRANCH}' "
                f"integration branch (pre-'{INTEGRATION_BRANCH}' back-compat; a VALID "
                f"resolution, not a degrade). Rename to '{INTEGRATION_BRANCH}' when convenient.",
                file=sys.stderr,
            )
        return src
    # src is None → no integration branch here → degrade VISIBLY to the released trunk.
    default = resolve_default_branch(repo_root)
    if log and default is not None:
        print(
            f"integration-branch: {INTEGRATION_BRANCH}/{LEGACY_INTEGRATION_BRANCH} both absent, "
            f"using default '{default}' (read-only degrade; a write path must refuse here)",
            file=sys.stderr,
        )
    return default


def _main(argv: list[str] | None = None) -> int:
    """CLI seam for SKILL.md call sites.

    ``--integration``          -> print the integration branch (aisdlc-uat / legacy
                                  uat, or the degraded default); exit 2 when git is
                                  unusable.
    ``--integration --write``  -> WRITE-path guard (M3 / M-add-2): print the integration
                                  branch and exit 0 ONLY when one EXISTS (source-keyed
                                  via ``existing_integration_branch``); on a trunk-degrade
                                  (no aisdlc-uat AND no ai-sdlc-managed uat) REFUSE (exit
                                  3, nothing on stdout) rather than let a write fall back
                                  to the released trunk; exit 2 when git is unusable.
    (no flag)                  -> print ``resolve_default_branch`` (back-compat).
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="_git_default_branch",
        description="Resolve the default / integration git branch for the slice loop.",
    )
    ap.add_argument("--integration", action="store_true",
                    help="resolve the INTEGRATION branch (aisdlc-uat / legacy uat, else the degraded default)")
    ap.add_argument("--write", action="store_true",
                    help="WRITE-path guard (M3 / M-add-2): refuse (exit 3) on a trunk-degrade (no integration branch)")
    ap.add_argument("--repo-root", default=".", help="repo root to inspect (default: cwd)")
    args = ap.parse_args(argv)

    if args.integration:
        if args.write:
            # WRITE-path guard: key on resolution SOURCE, not name-equality (M-add-2).
            src = existing_integration_branch(args.repo_root)
            if src is not None:
                print(src)
                return 0
            # src is None: distinguish a real trunk-degrade (exit 3) from git-unusable (exit 2).
            if resolve_default_branch(args.repo_root) is None:
                print("resolve_integration_branch: git unusable; cannot resolve a branch.",
                      file=sys.stderr)
                return 2
            print(
                f"integration-branch: no {INTEGRATION_BRANCH}/{LEGACY_INTEGRATION_BRANCH} "
                f"integration branch; refusing to advance the released trunk via a write path "
                f"-- establish {INTEGRATION_BRANCH} first (a read-only caller may degrade, a "
                f"write caller may not).",
                file=sys.stderr,
            )
            return 3
        branch = resolve_integration_branch(args.repo_root)
        if branch is None:
            print("resolve_integration_branch: git unusable; cannot resolve a branch.",
                  file=sys.stderr)
            return 2
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
