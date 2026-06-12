"""active_slice.py — resolve the in-flight slice + emit it for injection (v2, NEW).

Shared library + CLI. The single canonical answer to "which slice is the skill
working on right now?", so every skill that injects active-slice context
(`/reflect`, `/critique-review`, `/design-slice`, `/validate-slice`) resolves it
the SAME way and cannot drift.

Resolution precedence (`resolve_active_slice`):
  1. **git branch** of the worktree (`git -C <repo_root> rev-parse --abbrev-ref HEAD`):
     a `slice/NNN-<name>` branch -> the `slice-NNN-<name>` folder, iff it exists in
     the vault. This is the robust per-worktree answer (parallel slices each have
     their own branch + worktree). Mirrors `pulse_worktree_resolver`'s branch shape.
  2. **vault scan** fallback (detached HEAD / not in a slice worktree / non-git):
     among `<vault>/slices/slice-*` (excluding `archive/`), prefer a NON-terminal
     milestone (`stage != "complete"`); within that pool pick the most recently
     updated (`milestone.at` desc, then highest NNN).
  3. None when the vault has no slices.

Read-only. Library API `resolve_active_slice(vault, repo_root=".") -> dict | None`
returns `{slice, folder, path, stage, next_action, current_focus, source, exists}`.

CLI: `--vault ROOT [--repo-root .] [--json]`. Exit 0 always (an absent active slice
is a normal early state, not an error — the consumer decides what to do); `--json`
emits the info dict (or `{"slice": null, ...}`), else a one-line summary.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md;
# a skill runs in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m`
# or `${CLAUDE_PLUGIN_ROOT}`). Add the plugin root so `from scripts.lib import …` resolves.
# No-op under `-m scripts.lib.active_slice` from the plugin root. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/active_slice.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

_SLICE_BRANCH_RE = re.compile(r"^slice/(\d+)-(.+)$")
_SLICE_FOLDER_RE = re.compile(r"^slice-(\d+)-(.+)$")
# Terminal stage(s): the v2 loop ends at /reflect -> milestone stage "complete".
_TERMINAL_STAGES = frozenset({"complete"})


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _git_branch(repo_root: str | Path) -> str | None:
    """Current branch via `symbolic-ref --short HEAD` — returns the branch name even
    for an UNBORN branch (a just-created `git worktree add -b slice/NNN` before its
    first commit), and exits non-zero on a detached HEAD (the correct 'not on a
    branch' signal). `rev-parse --abbrev-ref HEAD` would wrongly return "HEAD" there."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,  # BB-19: capture BYTES + decode in the MAIN thread —
        )                          # text=True+encoding= would raise an UNCAUGHT UnicodeDecodeError
    except (OSError, subprocess.SubprocessError):  # in the subprocess reader thread on a non-UTF-8 ref name
        return None
    if cp.returncode != 0:
        return None
    return cp.stdout.decode("utf-8", "replace").strip() or None


def _read_milestone(folder: Path) -> dict:
    p = folder / "milestone.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _slice_info(folder: Path, source: str) -> dict:
    m = _read_milestone(folder)
    fm = _SLICE_FOLDER_RE.match(folder.name)
    return {
        "slice": f"slice-{fm.group(1)}" if fm else folder.name,
        "folder": folder.name,
        "path": str(folder),
        "stage": m.get("stage"),
        "next_action": m.get("next_action"),
        "current_focus": m.get("current_focus"),
        "source": source,
        "exists": folder.is_dir(),
    }


def resolve_active_slice(vault: str | Path, repo_root: str | Path = ".") -> dict | None:
    """The in-flight slice (or None). See module docstring for precedence."""
    slices_dir = Path(vault) / "slices"

    # 1. git branch of the worktree
    branch = _git_branch(repo_root)
    if branch:
        mb = _SLICE_BRANCH_RE.match(branch)
        if mb:
            folder = slices_dir / f"slice-{mb.group(1)}-{mb.group(2)}"
            if folder.is_dir():
                return _slice_info(folder, "git-branch")

    # 2. vault scan: non-terminal first, then most-recent
    if not slices_dir.is_dir():
        return None
    folders = [p for p in slices_dir.iterdir()
               if p.is_dir() and p.name != "archive" and _SLICE_FOLDER_RE.match(p.name)]
    if not folders:
        return None
    scored = []
    for p in folders:
        m = _read_milestone(p)
        num = int(_SLICE_FOLDER_RE.match(p.name).group(1))
        terminal = m.get("stage") in _TERMINAL_STAGES
        scored.append((p, terminal, str(m.get("at") or ""), num))
    non_terminal = [s for s in scored if not s[1]]
    pool = non_terminal if non_terminal else scored
    pool.sort(key=lambda s: (s[2], s[3]), reverse=True)  # at desc, then NNN desc
    return _slice_info(pool[0][0], "vault-scan")


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="active_slice",
        description="Resolve the in-flight slice and emit it for skill injection. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--repo-root", "--root", dest="repo_root", default=".",
                   help="worktree/repo root for git-branch resolution (default: cwd)")
    p.add_argument("--json", action="store_true",
                   help="emit the info dict as JSON (default: one-line text)")
    p.add_argument("--path-only", action="store_true",
                   help="print ONLY the resolved slice folder path (empty if none) — for shell capture")
    p.add_argument("--folder-only", action="store_true",
                   help="print ONLY the resolved slice folder NAME (basename; empty if none) — for "
                        "`_worktree_paths.py --slice-folder` and other name-keyed call sites")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    info = resolve_active_slice(_root(args.vault), args.repo_root)
    if args.path_only:  # BB-10: single-value capture for SKILL.md sub-shells (consistent with --json)
        print(info["path"] if info else "")
        return 0
    if args.folder_only:  # NAME (basename) for `_worktree_paths.py --slice-folder` and peers
        print(info["folder"] if info else "")
        return 0
    if args.json:
        print(json.dumps(info if info else {"slice": None, "source": "none", "exists": False},
                         ensure_ascii=False))
    elif info:
        print(f"active slice: {info['folder']} (stage={info['stage']}, "
              f"next={info['next_action']}, via {info['source']})")
    else:
        print("active slice: none (no slices in the vault yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
