"""repro_test_relocate.py — capability-scoped relocation of ONE repro test (slice-018 / [[ADR-012]]).

Relocate exactly ONE explicitly-named pre-worktree repro test from the MAIN tree
into the slice WORKTREE, scoped by an explicit ``--test-path`` grant. It NEVER
enumerates ``tests/bugs/`` — it acts only on the one file it was handed, so it
physically cannot sweep a sibling slice's untracked test (the confused-deputy bug
ADR-012 fixes; the old ``git ls-files --others -- 'tests/bugs/*'`` glob could).

Used by ``skills/slice/SKILL.md`` Step 5 on the STANDALONE path (a ``/repro`` run
before the worktree existed wrote the failing test to the main tree). The in-loop
path writes the test INTO the worktree via ``/repro --target-root`` and does NOT
call this helper.

Mirrors the ``wt_root_audit.py`` / ``_worktree_paths.py`` idiom: plugin-root
bootstrap, ``_stdout`` UTF-8, the shared ``run_git`` runner, and forward-slash
(``as_posix``) paths so a Windows backslash path never breaks ``git -C`` in git-bash.

Exit codes:
    0  relocated (or already at dest) — logs ``moved X -> $wt/X``
    1  fail-visible error: the named ``--test-path`` source is missing on main, OR
       the move / ``git add`` failed, OR ``tests/bugs/`` is git-ignored in the
       worktree (the stage would silently no-op)
    2  usage error: bad ``--slice-folder`` shape, or the slice worktree is absent
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout
from scripts.lib._git_default_branch import run_git
from scripts.lib._worktree_paths import canonical_worktree_path, slice_branch_name


def _norm_relpath(test_path: str) -> str:
    """Normalize a repo-relative test path to a forward-slash POSIX relpath."""
    return Path(test_path.strip().strip('"').strip("'")).as_posix()


def relocate_repro_test(slice_folder: str, repo_root: Path | str, test_path: str) -> int:
    """Move ONE named repro test from ``repo_root`` into its slice worktree, then stage it.

    Returns 0 on success (or an idempotent no-op when the test is already relocated),
    1 on a fail-visible error (missing named source, git-ignored, move/stage failure),
    2 on a usage error (bad slice-folder shape, or the worktree does not exist).
    """
    repo_root = Path(repo_root).resolve()
    branch = slice_branch_name(slice_folder)
    if not branch:
        sys.stderr.write(
            f"repro_test_relocate: --slice-folder {slice_folder!r} is not a slice-NNN-<name> folder\n")
        return 2

    wt = canonical_worktree_path(slice_folder, repo_root)
    if not wt.is_dir():
        sys.stderr.write(
            f"repro_test_relocate: slice worktree does not exist: {wt.as_posix()} "
            f"(create it with /slice before relocating)\n")
        return 2

    rel = _norm_relpath(test_path)
    if not rel or rel == ".":
        sys.stderr.write("repro_test_relocate: --test-path is empty\n")
        return 2
    # CR1 (defense-in-depth): the grant is a repo-relative path INSIDE the tree. Reject an
    # absolute path or any `..` segment so a malformed --test-path can never escape `$wt`/main
    # (the wired caller already scopes to tests/bugs/, but the public CLI must not trust its input).
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        sys.stderr.write(
            f"repro_test_relocate: --test-path must be a repo-relative path without '..': {test_path!r}\n")
        return 2

    src = repo_root / rel
    dest = wt / rel

    # Idempotent no-op: the named test is already in $wt and gone from main -> nothing to do.
    if dest.exists() and not src.exists():
        run_git(wt, "add", rel)  # CR2: ensure the already-relocated test is staged (idempotent)
        print(f"repro_test_relocate: already relocated — {rel} is in the worktree, absent on main (no-op)")
        return 0

    if not src.exists():
        sys.stderr.write(
            f"repro_test_relocate: named test not found on the main tree: {src.as_posix()} "
            f"(the grant names a file that is not there — nothing to relocate)\n")
        return 1

    # m2: a git-ignored tests/bugs/ would make `git add` silently no-op -> fail visibly instead.
    ign = run_git(wt, "check-ignore", "-q", rel)
    if ign.returncode == 0:
        sys.stderr.write(
            f"repro_test_relocate: {rel} is git-ignored in the worktree — `git add` would silently "
            f"do nothing. Un-ignore tests/bugs/ (or the path) and retry.\n")
        return 1

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    except OSError as exc:
        sys.stderr.write(
            f"repro_test_relocate: move {src.as_posix()} -> {dest.as_posix()} failed: {exc}\n")
        return 1

    add = run_git(wt, "add", rel)
    if add.returncode != 0:
        sys.stderr.write(
            f"repro_test_relocate: `git -C {wt.as_posix()} add {rel}` failed: {add.stderr.strip()}\n")
        return 1

    print(f"repro_test_relocate: moved {src.as_posix()} -> {dest.as_posix()} (staged on {branch})")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="repro_test_relocate",
        description="Relocate ONE explicitly-named repro test from the main tree into its slice "
                    "worktree (ADR-012; capability-scoped, never enumerates tests/bugs/).")
    p.add_argument("--slice-folder", required=True, metavar="slice-NNN-name",
                   help="the slice folder name; the worktree path is derived from it")
    p.add_argument("--repo-root", type=Path, default=Path("."),
                   help="main repo root (default: cwd)")
    p.add_argument("--test-path", required=True, metavar="tests/bugs/test_x.py",
                   help="the ONE repo-relative test path to relocate (the explicit capability grant)")
    args = p.parse_args(argv)
    return relocate_repro_test(args.slice_folder, args.repo_root, args.test_path)


if __name__ == "__main__":
    sys.exit(main())
