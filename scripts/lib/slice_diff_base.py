"""Resolve the slice diff base ref (SC-043 / slice-025).

The /build-slice pre-finish gate, /code-review, and /validate-slice derive the
slice diff base to scope the changed-files set to the current slice's own work.
The base MUST be the fork point of the slice branch against the LOCAL integration
branch (aisdlc-uat, legacy uat as back-compat, degrading to the released trunk) --
NOT ``origin/HEAD``. Slices branch
off the LOCAL integration branch, which in the dogfood flow is AHEAD of origin, so
``merge-base HEAD origin/HEAD`` resolves to a stale ancestor and the changed-files
set BALLOONS to include every prior-merged slice (mis-scoping BC-1 / LINT-MOCK /
the review diff onto already-merged files).

This computes ``git merge-base HEAD <resolve_integration_branch>`` -- the explicit,
reflog-independent fork point against the correct LOCAL base ref. (Explicit
merge-base, NOT ``--fork-point``: --fork-point reads the gc-volatile reflog and is
unreliable; the chosen base ref was the only bug, not the merge-base algorithm.)

Never aborts a gate: on every unresolvable path (no remote / fresh repo / branch
unresolvable / empty merge-base output / git unusable) it prints ``HEAD`` -- diffing
against HEAD matches the WT-ROOT-1 no-remote contract (uncommitted work since the
branch tip). Exits 0 on a REAL worktree (exits 2, empty stdout, when the worktree is bogus -- see
Exit codes); prints EXACTLY the base ref as the sole stdout line;
routes the resolved branch + every degrade reason to stderr ONLY, so a caller's
``base="$(...)"`` capture is always the clean base. ASCII-only output.

Reuses ``scripts/lib/_git_default_branch.py``: ``resolve_integration_branch`` (the
slice-022 canonical resolver -- degrades VISIBLY to stderr, None only when git is
unusable) and ``run_git`` (the shared UTF-8/errors=replace git runner -- BB-25).

Usage::

    $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_diff_base.py" --worktree <wt>
    # -> stdout: <base SHA> or HEAD   (the sole stdout line)

Exit codes (CLI)::

    0  base printed (a SHA, or the HEAD fallback) for a REAL git worktree root
    2  usage error, OR --worktree is not an existing git worktree root (EMPTY stdout).
       slice-069/M2: the old 'ALWAYS exits 0' contract turned an empty/bogus --worktree into
       `HEAD` -> `git diff HEAD...HEAD` -> an EMPTY DIFF -> a confident false 'no code changes'
       review. The HEAD fallback still stands for a REAL worktree whose base is unresolvable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}`. Shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" ...`, which puts scripts/lib
# (not the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib import
# ...` resolves. Mirrors _worktree_paths.py. No-op under `-m`.
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout
from scripts.lib._git_default_branch import resolve_integration_branch, run_git


def resolve_slice_diff_base(worktree: Path | str) -> str:
    """Return the slice diff base ref for ``worktree`` (a SHA, or ``"HEAD"``).

    The fork point of HEAD against the LOCAL integration branch
    (``resolve_integration_branch``). Falls back to ``"HEAD"`` -- never raising,
    never returning empty -- on EVERY unresolvable path (branch None / git unusable,
    merge-base non-zero, OR merge-base exit-0-but-EMPTY when HEAD shares no ancestor
    with the integration branch) so a gate is never aborted by base resolution.
    Every degrade reason is logged to stderr (visible, ASCII).
    """
    branch = resolve_integration_branch(worktree)
    if branch:
        mb = run_git(worktree, "merge-base", "HEAD", branch)
        base = mb.stdout.strip()
        if mb.returncode == 0 and base:
            return base
        print(
            f"slice_diff_base: merge-base HEAD {branch} did not resolve a base "
            f"(rc={mb.returncode}); falling back to HEAD",
            file=sys.stderr,
        )
    else:
        print(
            "slice_diff_base: integration branch unresolvable (git unusable); "
            "falling back to HEAD",
            file=sys.stderr,
        )
    return "HEAD"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slice_diff_base",
        description=(
            "Resolve the slice diff base: the fork point of HEAD against the LOCAL "
            "integration branch (never origin/HEAD). Prints the base ref (or HEAD) "
            "as the sole stdout line. Exits 0 on a real worktree; exits 2 (EMPTY stdout) when --worktree is not an existing git worktree ROOT -- slice-069/M2: the old 'always exits 0' contract turned a bogus worktree into `HEAD`, i.e. an EMPTY DIFF and a false 'no code changes' review."
        ),
    )
    p.add_argument(
        # NO `type=Path` (slice-069 / M2). argparse would convert the EMPTY STRING to `Path('')` ==
        # `WindowsPath('.')` -- truthy, and `is_dir()` True -- BEFORE any guard can see it, so the
        # empty capture this gate exists to catch would silently become "the current directory".
        # Keep the RAW string; validate it; convert afterwards.
        "--worktree", "--repo-root", dest="worktree", required=True,
        help="The slice worktree (HEAD = the slice branch). --repo-root is an accepted alias.",
    )
    return p


EXIT_BAD_WORKTREE = 2


def _is_git_worktree_root(worktree: Path) -> bool:
    """True only when `worktree` is a real, existing git worktree ROOT.

    `is_dir()` alone is NOT sufficient, and that is the whole point (slice-069 / M2): argparse's
    `type=Path` turns the EMPTY STRING into `Path('')` == `WindowsPath('.')`, which is TRUTHY and
    whose `is_dir()` is True. So `if not args.worktree:` and `if not args.worktree.is_dir():` are
    BOTH silent no-ops on the exact input this guard exists to catch."""
    try:
        if not worktree.is_dir():
            return False
        top = run_git(worktree, "rev-parse", "--show-toplevel")
        return top.returncode == 0 and bool(top.stdout.strip())
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    # FAIL-CLOSED on an unusable worktree (slice-069 / ADR-072; critique M2 + DR-1 M-add-2).
    #
    # This function's HEAD fallback is deliberately generous -- "never abort a gate" -- and that
    # generosity is exactly what converted an unusable worktree into a CLEAN EMPTY DIFF: with an
    # empty (or two-line, or otherwise bogus) --worktree, `git -C ""` silently operates on the MAIN
    # REPO, the integration branch fails to resolve, we print `HEAD`, and the caller runs
    # `git diff HEAD...HEAD` -> zero lines -> "no code changes" -> a confident false-green review.
    # The fallback stays for a REAL worktree whose base is genuinely unresolvable; it must NOT stand
    # in for a worktree that does not exist. Nothing is printed on stdout, so a `$( )` capture goes
    # EMPTY and the call site's guard fires.
    raw = "" if args.worktree is None else str(args.worktree)
    if not raw.strip() or not _is_git_worktree_root(Path(raw)):
        print(f"slice_diff_base: --worktree is not an existing git worktree root: "
              f"{raw!r} -- refusing to resolve a base (a bogus worktree here becomes an EMPTY DIFF, "
              f"i.e. a false 'no code changes' review).", file=sys.stderr)
        return EXIT_BAD_WORKTREE

    print(resolve_slice_diff_base(Path(raw)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
