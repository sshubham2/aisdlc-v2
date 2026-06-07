"""wt_root_audit.py — WT-ROOT-1 enforcement (shared).

Backstops the WT-ROOT-1 contract: all slice code lives in the slice WORKTREE, and the
MAIN working tree stays clean (no edits leaked out of `$wt`). Two harness facts make the
contract easy to violate by accident, which is why a backstop is needed:
  - `Edit`/`Write`/`Read` take ABSOLUTE paths — a bash `cd` does not redirect them; and
  - skill ```bash blocks are FRESH shells — a `cd` does not persist to the next block.
So build-slice / code-review / validate-slice must root every code op at `$wt`; if they
slip and edit the main tree instead, this audit catches it.

Used by /build-slice (Step 6 pre-finish) and /validate-slice.

Given `--worktree <path>`:
  1. Resolve the MAIN working tree = first `git worktree list --porcelain` entry.
  2. If `--worktree` does not exist OR resolves to the main tree -> WORKTREE=skip legacy
     mode: WT-ROOT-1 is N/A (build legitimately happens in main); exit 0 with a note.
  3. Else the MAIN tree MUST be clean: `git -C <main> status --porcelain` empty. Non-empty
     -> exit 1 and list the leaked paths (slice code edited in main instead of `$wt`).
  4. Advisory: the worktree HEAD should be a `slice/*` branch.

Exit: 0 clean / N/A  ·  1 WT-ROOT-1 violation  ·  2 usage / git error.
"""
from __future__ import annotations

import argparse
import subprocess
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


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _main_tree(ref_dir: str) -> str | None:
    """First `git worktree list --porcelain` entry = the main working tree."""
    r = _git(["worktree", "list", "--porcelain"], cwd=ref_dir)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def audit(worktree: str) -> int:
    wt = Path(worktree)
    ref = str(wt) if wt.exists() else "."
    main_tree = _main_tree(ref)
    if main_tree is None:
        print("WT-ROOT-1: `git worktree list` failed (not a git repo?).", file=sys.stderr)
        return 2

    main_real = str(Path(main_tree).resolve())
    wt_real = str(wt.resolve()) if wt.exists() else ""

    # WORKTREE=skip / no separate worktree -> contract N/A (escape hatch is the build-slice DEVIATION log).
    if not wt.exists() or wt_real == main_real:
        print(f"WT-ROOT-1: N/A — no separate slice worktree (WORKTREE=skip). main={main_real}")
        return 0

    st = _git(["-C", main_tree, "status", "--porcelain"])
    if st.returncode != 0:
        print(f"WT-ROOT-1: `git status` on main tree failed: {st.stderr.strip()}", file=sys.stderr)
        return 2
    dirty = [ln for ln in st.stdout.splitlines() if ln.strip()]

    br = _git(["-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"])
    branch = br.stdout.strip() if br.returncode == 0 else "?"

    if dirty:
        print(
            f"WT-ROOT-1 VIOLATION: main tree {main_real} has {len(dirty)} uncommitted change(s) — "
            f"slice code must live in the worktree ({wt_real}, branch {branch}), NOT main:",
            file=sys.stderr,
        )
        for ln in dirty[:50]:
            print(f"  {ln}", file=sys.stderr)
        if len(dirty) > 50:
            print(f"  ... +{len(dirty) - 50} more", file=sys.stderr)
        return 1

    if not branch.startswith("slice/"):
        print(f"WT-ROOT-1: main tree clean, but worktree HEAD '{branch}' is not a slice/* branch (advisory).")
        return 0

    print(f"WT-ROOT-1 OK: main tree clean; slice code isolated in worktree {wt_real} (branch {branch}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(description="WT-ROOT-1 enforcement: assert slice code stays in the worktree, main tree clean.")
    ap.add_argument("--worktree", required=True, help="Path to the slice worktree ($wt). If absent/==main -> WORKTREE=skip N/A.")
    args = ap.parse_args(argv)
    return audit(args.worktree)


if __name__ == "__main__":
    sys.exit(main())
