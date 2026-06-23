"""Resolve the slice diff base ref (SC-043 / slice-025).

The /build-slice pre-finish gate, /code-review, and /validate-slice derive the
slice diff base to scope the changed-files set to the current slice's own work.
The base MUST be the fork point of the slice branch against the LOCAL integration
branch (uat, degrading to the released trunk) -- NOT ``origin/HEAD``. Slices branch
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
branch tip). ALWAYS exits 0; prints EXACTLY the base ref as the sole stdout line;
routes the resolved branch + every degrade reason to stderr ONLY, so a caller's
``base="$(...)"`` capture is always the clean base. ASCII-only output.

Reuses ``scripts/lib/_git_default_branch.py``: ``resolve_integration_branch`` (the
slice-022 canonical resolver -- degrades VISIBLY to stderr, None only when git is
unusable) and ``run_git`` (the shared UTF-8/errors=replace git runner -- BB-25).

Usage::

    $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_diff_base.py" --worktree <wt>
    # -> stdout: <base SHA> or HEAD   (the sole stdout line)

Exit codes (CLI)::

    0  base printed (a SHA, or the HEAD fallback -- ALWAYS 0 once args parse)
    2  usage error (missing --worktree / --repo-root)
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
            "as the sole stdout line; ALWAYS exits 0 once args parse."
        ),
    )
    p.add_argument(
        "--worktree", "--repo-root", dest="worktree", required=True, type=Path,
        help="The slice worktree (HEAD = the slice branch). --repo-root is an accepted alias.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    print(resolve_slice_diff_base(args.worktree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
