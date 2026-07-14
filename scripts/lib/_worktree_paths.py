"""Shared worktree-create logic (BRANCH-3 / slice-099 / [[ADR-090]]).

Single source of truth for the canonical worktree path + slice branch name.
Used by:

- ``skills/slice/SKILL.md`` Step 5.5 (worktree-at-pick — BRANCH-3) via the CLI,
- ``skills/build-slice/SKILL.md`` ``### Branch state`` (the legacy/create path) via the CLI,
- ``tools/branch_workflow_audit.py`` (end-state validation) via import,

so the three surfaces compute the canonical path + branch identically and
cannot drift (slice-099 AC5 — single source of truth on the primary path).

Per **BRANCH-3** (``methodology-changelog.md`` v0.81.0; slice-099; [[ADR-090]];
partial-supersedes ADR-063 BRANCH-2 build-time worktree timing): the worktree
is created at ``/slice`` pick-time, not ``/build-slice``. The canonical path
convention itself is UNCHANGED from BRANCH-2 — ``<main-parent>/<main-name>-wt/
slice-NNN-<name>`` on ``slice/NNN-<name>`` — this module only centralizes its
computation. ``_SLICE_FOLDER_RE`` + ``slice_branch_name`` + the canonical-path
logic were moved verbatim out of ``branch_workflow_audit.py`` (which now imports
them, internal-audit use); behavior is byte-identical.

Underscore-prefixed (``_worktree_paths``) like ``_vault_paths`` / ``_vault_write``
/ ``_stdout`` — a shared helper, NOT a catalogued tool, so it carries no
``plugin.yaml`` / ``install_audit.py`` / INSTALL.md inventory count and no PMI-1
module-count bump (the MEPD precedent).

Usage::

    # Library API (imported by branch_workflow_audit.py)
    from tools._worktree_paths import (
        canonical_worktree_path, slice_branch_name,
    )

    # CLI (invoked by /slice + /build-slice prose to compute path + branch)
    python -m scripts.lib._worktree_paths --slice-folder slice-NNN-<name> --repo-root .
    # → stdout line 1: <canonical worktree path>
    #   stdout line 2: slice/NNN-<name>

Exit codes (CLI)::

    0  path + branch printed
    2  usage error (slice-folder name fails ``slice-NNN-<name>`` shape)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON
# hooks/MCP). Shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" ...`, which puts scripts/lib
# (not the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib import
# ...` resolves, mirroring the single-skill parents[3] bootstrap. No-op under `-m`.
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout

# Slice-folder pattern: `slice-NNN-<slice-name>` (zero-padded 3-digit number).
# Moved verbatim from branch_workflow_audit.py (slice-099 / BRANCH-3); that
# module now imports it (internal-audit use). The strict numeric-only accept is
# unchanged — the letter-suffixed split-slice diagnostic regex stays in
# branch_workflow_audit (it is audit-message-only, not a path-compute concern).
_SLICE_FOLDER_RE = re.compile(r"^slice-(\d{3})-(.+)$")


def slice_branch_name(slice_folder_name: str) -> str:
    """Compute ``slice/NNN-<slice-name>`` from a slice-folder NAME.

    Returns ``""`` when ``slice_folder_name`` does not match the strict
    ``slice-NNN-<name>`` shape (caller decides how to handle the miss — the
    audit emits its split-slice-aware diagnostic; the CLI exits 2).
    """
    match = _SLICE_FOLDER_RE.match(slice_folder_name)
    if not match:
        return ""
    number, name = match.group(1), match.group(2)
    return f"slice/{number}-{name}"


def canonical_worktree_path(slice_folder_name: str, main_repo_root: Path) -> Path:
    """Canonical sibling-dir worktree path for ``slice_folder_name``.

    ``<main-parent>/<main-name>-wt/<slice-folder-name>`` per the BRANCH-2
    convention ([[ADR-063]] §Decision), unchanged by BRANCH-3. For main repo
    ``C:\\Users\\sshub\\ai_sdlc`` + folder ``slice-099-create-worktree-at-slice-pick``
    → ``C:\\Users\\sshub\\ai_sdlc-wt\\slice-099-create-worktree-at-slice-pick``.
    """
    main_repo_root = Path(main_repo_root)
    return main_repo_root.parent / f"{main_repo_root.name}-wt" / slice_folder_name


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.lib._worktree_paths",
        description=(
            "Compute the canonical worktree path + slice branch for a slice "
            "folder name (BRANCH-3 / slice-099). Single source of truth shared "
            "by /slice, /build-slice, and branch_workflow_audit."
        ),
    )
    p.add_argument(
        "--slice-folder", required=True,
        help="Slice folder NAME, e.g. slice-099-create-worktree-at-slice-pick.",
    )
    p.add_argument(
        "--repo-root", type=Path, default=Path("."),
        help="Main repo root (default: cwd). The worktree is its sibling -wt dir.",
    )
    p.add_argument(
        "--print", dest="print_field", choices=("path", "branch", "both"), default="both",
        help="Which field to print. DEFAULT 'both' keeps the historical TWO-LINE stdout contract "
             "(path on line 1, branch on line 2) that ~12 SKILL.md call sites depend on. Use "
             "'path' when you want the worktree alone -- it is safer than `| head -1`, which "
             "MASKS this command's exit status (a pipeline's status is the LAST command's).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    branch = slice_branch_name(args.slice_folder)
    if not branch:
        print(
            f"_worktree_paths usage error: slice-folder name does not match "
            f"`slice-NNN-<name>`: {args.slice_folder}",
            file=sys.stderr,
        )
        return 2
    wt = canonical_worktree_path(args.slice_folder, args.repo_root.resolve())
    # Emit forward slashes (as_posix): a Windows "C:\..." path breaks `cd`/`git -C` in git-bash
    # (backslash = escape) and is inconsistent with the hook's forward-slash $AI_SDLC_VAULT_ROOT.
    # Forward slashes are accepted by both git-bash AND git on Windows. (CLI-only; the imported
    # canonical_worktree_path() Path API is unchanged, so branch_workflow_audit is unaffected.)
    #
    # TWO-LINE STDOUT CONTRACT (slice-069 / ADR-072). This command prints the PATH on line 1 and the
    # BRANCH on line 2. That contract was undocumented, and ~12 SKILL.md call sites silently depend
    # on it by piping through `| head -1`. ADR-069 mistook that pipe for a defensive mask and told
    # the builder to DROP it -- which would have handed every one of those sites a two-line string,
    # breaking `git -C "$wt"` for the OWNER and (via slice_diff_base's HEAD fallback) producing an
    # EMPTY DIFF: a false green, on the happy path, everywhere. Use `--print path` at a call site
    # that wants one value; the contract is now pinned by tests/test_worktree_paths_contract.py.
    if args.print_field == "path":
        print(wt.as_posix())
    elif args.print_field == "branch":
        print(branch)
    else:
        print(wt.as_posix())
        print(branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
