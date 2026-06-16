"""active_slice.py — resolve the in-flight slice + emit it for injection (v2, NEW).

Shared library + CLI. The single canonical answer to "which slice is the skill
working on right now?", so every skill that injects active-slice context
(`/reflect`, `/critique-review`, `/design-slice`, `/validate-slice`) resolves it
the SAME way and cannot drift.

Resolution precedence (`resolve_active_slice`) — TRI-STATE (slice-014 hardening):
  1. **git branch** of the worktree (`git -C <repo_root> symbolic-ref --short HEAD`):
     a `slice/NNN-<name>` branch -> the `slice-NNN-<name>` folder, iff it exists in
     the vault. This is the robust per-worktree CAPABILITY (parallel slices each have
     their own branch + worktree). Mirrors `pulse_worktree_resolver`'s branch shape.
  2. **vault scan** fallback (detached HEAD / not in a slice worktree / non-git):
     among `<vault>/slices/slice-*` (excluding `archive/`):
       - 0 non-terminal slices, all terminal -> resolve the most-recent (degenerate);
       - EXACTLY 1 non-terminal slice -> resolve it (`source="vault-scan"`) ALWAYS,
         even from a non-git cwd — the 99% happy path has NO new friction;
       - **>=2 non-terminal slices and no branch capability -> AMBIGUOUS** (a distinct
         sentinel `{slice:None, source:"ambiguous", candidates:[...]}`), NEVER a
         confident recency pick. This is the fail-closed core: a skill run for slice-X
         from the master tree with several slices in flight must refuse, not guess
         (slice-014 / SC-23 / ADR-010). Recency (`milestone.at`) is demoted to ORDERING
         the candidate list only — it never decides between live slices.
  3. None when the vault has no slices.

`is_main_worktree(repo_root)` classifies the call site (main tree vs linked worktree vs
indeterminate) for the HALT message; the ambiguous TRIGGER itself is the >=2-non-terminal
count after branch-first fails, so a non-git cwd never refuses the 1-slice happy path.

Read-only. Library API `resolve_active_slice(vault, repo_root=".") -> dict | None`
returns `{slice, folder, path, stage, next_action, current_focus, source, exists}` on a
clean resolve, the AMBIGUOUS sentinel (with `candidates[]`) under parallel ambiguity, or
`None` when the vault has no slices.

CLI: `--vault ROOT [--repo-root .] [--json]`. Exit 0 on a clean resolve / benign absent
slice; **exit 4 on AMBIGUOUS** (a distinct, NON-retryable code — exit 3 is the reserved
retryable-CAS-conflict signal, ADR-010/B3) with EMPTY `--path-only`/`--folder-only` stdout
and a fail-visible HALT on stderr naming the candidate slices + the `--slice` remedy.
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
# (NOTE: /reflect writes stage 'complete' directly -- no stage named 'reflect' is ever
# written, so {complete} is the in-flight boundary. The 3-resolver divergence with
# pulse_worktree_resolver/stranded_slice_audit {reflect,complete} is filed as SC-027.)
_TERMINAL_STAGES = frozenset({"complete"})

# AMBIGUOUS CLI exit code (slice-014 / B3): distinct + NON-retryable. Exit 3 is RESERVED
# for the retryable CAS-write-conflict signal (vault_edit rewrite); reusing it here would
# make a retry-muscle-memory consumer loop forever on an ambiguity that retry can't fix.
EXIT_AMBIGUOUS = 4


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


def _git_rev_parse(repo_root: str | Path, *flags: str) -> str | None:
    """`git -C <root> rev-parse <flags>` -> stripped str, or None (non-git / error).
    BB-19: capture BYTES + decode in the main thread (a non-UTF-8 path would otherwise
    raise an uncaught UnicodeDecodeError in the subprocess reader thread)."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", *flags], capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    return cp.stdout.decode("utf-8", "replace").strip() or None


def is_main_worktree(repo_root: str | Path = ".") -> bool | None:
    """True if `repo_root` is the MAIN git worktree, False if a LINKED worktree, None if
    INDETERMINATE (non-git / git unavailable). Compares the RESOLVED absolute paths of
    `--absolute-git-dir` vs `--git-common-dir` in Python: they are EQUAL in the main tree
    and DIFFER in a linked worktree (whose git-dir is `<common>/worktrees/<id>`).

    The Python `pathlib.resolve()` compare is load-bearing (spike-proven,
    spike-design-active-slice): a raw bash string compare false-negatives because
    `--git-common-dir` is RELATIVE ('.git') in the main tree, and git emits `C:/...`
    while a bash `pwd` emits MSYS `/c/...`. Used only to ENRICH the ambiguity HALT
    message — the ambiguous trigger is the >=2-non-terminal count (so a non-git cwd
    never refuses the 1-slice happy path)."""
    gd = _git_rev_parse(repo_root, "--absolute-git-dir")
    cd = _git_rev_parse(repo_root, "--git-common-dir")
    if gd is None or cd is None:
        return None  # indeterminate: non-git cwd or git unavailable
    try:
        gd_r = Path(gd).resolve()
        cd_p = Path(cd)
        cd_r = (cd_p if cd_p.is_absolute() else (Path(repo_root) / cd_p)).resolve()
    except (OSError, ValueError):
        return None
    return gd_r == cd_r


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


def _candidate_entry(folder: Path) -> dict:
    """A single AMBIGUOUS candidate entry — M2 contract `{slice, folder, stage, at}`.
    Carries enough for a human/CLI to disambiguate (the id + worktree folder + stage +
    recency)."""
    m = _read_milestone(folder)
    fm = _SLICE_FOLDER_RE.match(folder.name)
    return {
        "slice": f"slice-{fm.group(1)}" if fm else folder.name,
        "folder": folder.name,
        "stage": m.get("stage"),
        "at": m.get("at"),
    }


def _ambiguous(candidates: list[dict]) -> dict:
    """The distinct AMBIGUOUS sentinel — same stable key set as `_slice_info` (so a
    `--json` consumer indexing those keys never KeyErrors), with every resolved field
    None, `source="ambiguous"`, and the `candidates[]` list (M2 / M-add-2). MUST stay
    distinct from the benign `source="none"` (no slices) result so a consumer can tell
    'refuse, name the candidates' from 'nothing to do'."""
    return {
        "slice": None,
        "folder": None,
        "path": None,
        "stage": None,
        "next_action": None,
        "current_focus": None,
        "source": "ambiguous",
        "exists": False,
        "candidates": candidates,
    }


def resolve_active_slice(vault: str | Path, repo_root: str | Path = ".") -> dict | None:
    """The in-flight slice (or the AMBIGUOUS sentinel, or None). See module docstring."""
    slices_dir = Path(vault) / "slices"

    # 1. git branch of the worktree = the per-slice CAPABILITY (unchanged happy path)
    branch = _git_branch(repo_root)
    if branch:
        mb = _SLICE_BRANCH_RE.match(branch)
        if mb:
            folder = slices_dir / f"slice-{mb.group(1)}-{mb.group(2)}"
            if folder.is_dir():
                return _slice_info(folder, "git-branch")

    # 2. vault scan: branch-first did not resolve a capability
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

    # >=2 genuinely-active slices + no branch capability -> FAIL-CLOSED ambiguous.
    # Recency only ORDERS the candidate list here; it never decides (Lamport: wall-clock
    # recency is not a valid ordering across concurrent actors).
    if len(non_terminal) >= 2:
        non_terminal.sort(key=lambda s: (s[2], s[3]), reverse=True)  # at desc, then NNN desc
        return _ambiguous([_candidate_entry(s[0]) for s in non_terminal])

    # exactly one non-terminal (the 99% happy path, even from a non-git cwd), OR
    # all-terminal degenerate fallback -> resolve the (most-recent) slice.
    pool = non_terminal if non_terminal else scored
    pool.sort(key=lambda s: (s[2], s[3]), reverse=True)  # at desc, then NNN desc
    return _slice_info(pool[0][0], "vault-scan")


def resolve_slice_by_id(vault: str | Path, slice_id: str) -> dict | None:
    """Resolve a slice folder BY ID across BOTH ``slices/`` AND ``slices/archive/``
    (active first, then archive), matching on the NNN number so ``slice-5``,
    ``slice-005`` and ``slice-005-enrich-slice-story`` all resolve. Returns
    ``_slice_info`` with an **absolute** path (``.resolve()``-d, so the write target
    is cwd-independent — M4), or ``None`` when no folder matches.

    Unlike ``resolve_active_slice`` (which deliberately EXCLUDES ``archive/`` — that
    exclusion is load-bearing for /reflect, /critique, /design-slice), this is the
    archive-AWARE lookup ``/slice-story`` uses to target a slice that may already be
    shipped + archived: ``/commit-slice``'s on-ship auto-emit runs AFTER ``/reflect``
    moved the folder to ``archive/`` (B1). Read-only; no git branch needed."""
    m = re.match(r"^\s*slice-0*(\d+)(?:-.*)?\s*$", str(slice_id))
    if not m:
        return None
    num = int(m.group(1))
    slices_dir = Path(vault) / "slices"
    for base, source in ((slices_dir, "by-id-active"), (slices_dir / "archive", "by-id-archive")):
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            fm = _SLICE_FOLDER_RE.match(p.name)
            if p.is_dir() and fm and int(fm.group(1)) == num:
                return _slice_info(p.resolve(), source)
    return None


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
    p.add_argument("--slice", default=None, metavar="slice-NNN",
                   help="resolve THIS slice by id across slices/ AND slices/archive/ "
                        "(archive-aware; for /slice-story targeting a possibly-shipped "
                        "slice), instead of resolving the active slice")
    p.add_argument("--json", action="store_true",
                   help="emit the info dict as JSON (default: one-line text)")
    p.add_argument("--path-only", action="store_true",
                   help="print ONLY the resolved slice folder path (empty if none/ambiguous) — for shell capture")
    p.add_argument("--folder-only", action="store_true",
                   help="print ONLY the resolved slice folder NAME (basename; empty if none/ambiguous) — for "
                        "`_worktree_paths.py --slice-folder` and other name-keyed call sites")
    return p


def _is_ambiguous(info) -> bool:
    return isinstance(info, dict) and info.get("source") == "ambiguous"


def _emit_ambiguous_halt(info: dict, repo_root: str | Path) -> None:
    """Fail-visible HALT to STDERR (never stdout): name the candidate slices + the
    disambiguation remedy, classified by call-site (AC5 / M-add-1). is_main_worktree
    only enriches the wording."""
    cands = info.get("candidates", []) or []
    ids = ", ".join(str(c.get("slice")) for c in cands)
    where = is_main_worktree(repo_root)
    site = ("the MAIN tree" if where is True
            else "a non-git directory" if where is None
            else "a linked worktree with no slice branch checked out")
    print(f"AMBIGUOUS active slice: {len(cands)} slices in flight ({ids}) and no slice was "
          f"designated from {site}. This call refuses to guess. Disambiguate by EITHER passing "
          f"--slice <slice-NNN>, OR working from the slice's own worktree.", file=sys.stderr)
    for c in cands:
        print(f"  - {c.get('slice')}  (worktree: {c.get('folder')}, stage={c.get('stage')})",
              file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    if args.slice:  # archive-aware by-id lookup (/slice-story on a possibly-shipped slice)
        info = resolve_slice_by_id(_root(args.vault), args.slice)
    else:
        info = resolve_active_slice(_root(args.vault), args.repo_root)

    ambiguous = _is_ambiguous(info)

    # Observability (must_not_defer #3) — STDERR ONLY, so the machine-mode stdout
    # (--path-only/--folder-only/--json) stays EXACTLY the value with no contamination (m3).
    if ambiguous:
        _emit_ambiguous_halt(info, args.repo_root)
    elif isinstance(info, dict) and info.get("slice"):
        print(f"active-slice resolved: {info['folder']} via {info['source']}", file=sys.stderr)

    if args.path_only:  # BB-10: single-value capture for SKILL.md sub-shells
        print(info["path"] if (info and not ambiguous and info.get("path")) else "")
        return EXIT_AMBIGUOUS if ambiguous else 0
    if args.folder_only:  # NAME (basename) for `_worktree_paths.py --slice-folder` and peers
        print(info["folder"] if (info and not ambiguous and info.get("folder")) else "")
        return EXIT_AMBIGUOUS if ambiguous else 0
    if args.json:
        if ambiguous or info:
            print(json.dumps(info, ensure_ascii=False))  # full ambiguous payload OR resolved info
        else:
            print(json.dumps({"slice": None, "source": "none", "exists": False}, ensure_ascii=False))
        return EXIT_AMBIGUOUS if ambiguous else 0

    # human text mode
    if ambiguous:
        cands = info.get("candidates", []) or []
        ids = ", ".join(str(c.get("slice")) for c in cands)
        print(f"active slice: AMBIGUOUS — {len(cands)} in flight ({ids}); pass --slice <slice-NNN> "
              f"or work from the slice worktree")
        return EXIT_AMBIGUOUS
    if isinstance(info, dict) and info.get("slice"):
        print(f"active slice: {info['folder']} (stage={info['stage']}, "
              f"next={info['next_action']}, via {info['source']})")
    else:
        print("active slice: none (no slices in the vault yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
