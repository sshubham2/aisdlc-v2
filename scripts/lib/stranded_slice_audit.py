"""Stranded-slice detector — v2 (redesigned for the shared external vault).

Read-only git-vs-vault CLASSIFIER. Enumerates every unmerged local
``slice/NNN-<name>`` branch and classifies each into a divergence model, halting
``/slice`` only on genuine divergence (STRANDED-COMPLETE / ORPHANED / INDETERMINATE).
A healthy in-flight parallel slice classifies as IN-PROGRESS → informational.

**v2 redesign from v1 (slice-087 / ADR-079).** v1 assumed an IN-TREE vault copied
per branch, so it read a bare branch's slice state via ``git show <branch>:architecture/…``
(gated by ``vault_is_external``) and read claims from ``slice-queue.md``. v2 has ONE
EXTERNAL vault shared by every worktree of the repo (keyed on the git common-dir), so:

- **slice state is read DIRECTLY from the shared vault** — ``<vault>/slices/slice-NNN-<name>/
  milestone.json`` (``.stage``) and ``<vault>/slices/archive/slice-NNN-<name>/`` — NOT from
  per-branch git trees. The ``vault_is_external`` guard + the ``git ls-tree``/``git show``
  reads are GONE (the vault is never in the code repo's git).
- **claims come from ``candidates.json``** (`candidates[].claimed_by {git_user,git_email}` +
  `slice`), NOT the deleted ``slice-queue.md``.

Classification precedence (first match wins):
  1. CLAIMED-BY-OTHER  — a ``candidates.json`` claim for the branch's slice by a foreign git
     identity (cross-session courtesy; never halt my /slice). Inert in solo-dev.
  2. IN-PROGRESS       — live worktree classify==IN_PROGRESS, or a non-terminal milestone in
     the shared vault (healthy parallel work).
  3. STRANDED-COMPLETE — git-unmerged AND vault says DONE (archived, or terminal milestone). HALT.
  4. ORPHANED          — git-unmerged AND no vault story anywhere. HALT.
  5. INDETERMINATE     — could not classify (merge-base error). HALT (fail-closed).
  + BRANCHLESS-IN-FLIGHT — a shared-vault slice folder with a non-terminal milestone and no
    ``slice/NNN-*`` ref (the pre-/build-slice scaffold). INFORMATIONAL, never a halt.

Advisory, never blocking: exit 0 on any successful run; exit 2 only on usage failure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
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
from scripts.lib._git_default_branch import resolve_default_branch as _resolve_default_branch
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.pulse_worktree_resolver import (
    WorktreeState,
    classify_worktree_state,
    detect_active_worktrees,
)

__all__ = [
    "DivergenceClass",
    "StrandedEntry",
    "classify_branches",
    "compute_status",
    "main",
]

# Canonical slice-branch shape `slice/NNN-<name>` and slice-folder `slice-NNN-<name>`.
_SLICE_BRANCH_RE = re.compile(r"^slice/(\d{3})-(.+)$")
_SLICE_FOLDER_RE = re.compile(r"^slice-(\d{3})-(.+)$")

# Terminal "built/done" milestone stages (v2 loop ends at /reflect -> `complete`).
_TERMINAL_STAGES = frozenset({"reflect", "complete"})


class _UsageError(Exception):
    """Hard input failure -> exit 2 (git unavailable / default unresolvable / not a repo)."""


class DivergenceClass(Enum):
    STRANDED_COMPLETE = "stranded-complete"
    ORPHANED = "orphaned"
    IN_PROGRESS = "in-progress"
    CLAIMED_BY_OTHER = "claimed-by-other"
    INDETERMINATE = "indeterminate"
    BRANCHLESS_IN_FLIGHT = "branchless-in-flight"


_HALT_CLASSES = {
    DivergenceClass.STRANDED_COMPLETE,
    DivergenceClass.ORPHANED,
    DivergenceClass.INDETERMINATE,
}


@dataclass(frozen=True)
class StrandedEntry:
    branch: str
    worktree_path: str | None
    klass: DivergenceClass
    halt: bool
    vault_state: str
    claimed_by: str | None
    ahead: int | None
    dirty: bool
    reason: str


# ----------------------------- private helpers -----------------------------


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False)


def _read_git_identity(repo_root: Path) -> str | None:
    """``"<name> <email>"`` or None. Non-fatal: unknown identity → CLAIMED-BY-OTHER
    simply never fires."""
    try:
        name = _run_git(repo_root, "config", "user.name")
        email = _run_git(repo_root, "config", "user.email")
    except FileNotFoundError:
        return None
    if name.returncode != 0 or email.returncode != 0:
        return None
    n, e = name.stdout.strip(), email.stdout.strip()
    if not n or not e:
        return None
    return f"{n} {e}"


def _load_claims(repo_root: Path) -> dict[str, str]:
    """Map slice-folder id (`slice-NNN-<name>`) -> claimed_by `"<name> <email>"` from the
    shared vault's ``candidates.json``. Non-fatal: absent/unparseable → no claims (the
    CLAIMED-BY-OTHER class never fires). v2 replacement for v1's ``slice-queue.md`` reader."""
    cand = repo_root / VAULT_ROOT / "candidates.json"  # absolute VAULT_ROOT → the shared vault
    if not cand.is_file():
        return {}
    try:
        data = json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    claims: dict[str, str] = {}
    for c in data.get("candidates", []) if isinstance(data, dict) else []:
        if not isinstance(c, dict):
            continue
        slc = c.get("slice")
        cb = c.get("claimed_by")
        if isinstance(slc, str) and slc and isinstance(cb, dict) and (cb.get("git_user") or cb.get("git_email")):
            claims[slc] = f"{cb.get('git_user', '')} {cb.get('git_email', '')}".strip()
    return claims


def _milestone_stage_and_next(path: Path) -> tuple[str | None, str | None]:
    """Read `(stage, next_action)` from a milestone.json. None/None on any failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    stage = data.get("stage")
    nxt = data.get("next_action")
    return (stage if isinstance(stage, str) else None), (nxt if isinstance(nxt, str) else None)


def _is_terminal(stage: str | None, next_action: str | None) -> bool:
    if stage and stage.strip().lower() in _TERMINAL_STAGES:
        return True
    if next_action and re.search(r"\bcommit\b", next_action.lower()):
        return True
    return False


def _worktree_dirty(wt_path: str) -> bool:
    res = _run_git(Path(wt_path), "status", "--porcelain")
    return res.returncode == 0 and bool(res.stdout.strip())


def _entry(
    branch: str, wt: str | None, klass: DivergenceClass, vault_state: str,
    claimed_by: str | None, ahead: int | None, reason: str, dirty: bool = False,
) -> StrandedEntry:
    return StrandedEntry(
        branch=branch, worktree_path=wt, klass=klass, halt=klass in _HALT_CLASSES,
        vault_state=vault_state, claimed_by=claimed_by, ahead=ahead, dirty=dirty, reason=reason,
    )


def _bare_slice_branches(repo_root: Path, worktree_branches: set[str]) -> list[tuple[str, str, str]]:
    """Local ``slice/NNN-<name>`` branches WITHOUT a live worktree. The
    ``refs/heads/slice/`` ref-glob is load-bearing — ``recovery/*`` is structurally excluded."""
    res = _run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/slice/")
    if res.returncode != 0:
        return []
    out: list[tuple[str, str, str]] = []
    for line in res.stdout.splitlines():
        branch = line.strip()
        if not branch or branch in worktree_branches:
            continue
        m = _SLICE_BRANCH_RE.match(branch)
        if not m:
            continue
        out.append((branch, m.group(1), m.group(2)))
    return out


def _classify_bare_branch(
    repo_root: Path, branch: str, num: str, name: str, default: str,
    claims: dict[str, str], my_identity: str | None,
) -> StrandedEntry | None:
    # Merged? (ancestor of default → integrated → not stranded).
    anc = _run_git(repo_root, "merge-base", "--is-ancestor", branch, default)
    if anc.returncode == 0:
        return None
    if anc.returncode != 1:
        return _entry(branch, None, DivergenceClass.INDETERMINATE, "none", None, None,
                      f"merge-base --is-ancestor errored (rc={anc.returncode})")

    ahead: int | None = None
    if _run_git(repo_root, "merge-base", branch, default).returncode == 0:
        cnt = _run_git(repo_root, "rev-list", "--count", f"{default}..{branch}")
        if cnt.returncode == 0 and cnt.stdout.strip().isdigit():
            ahead = int(cnt.stdout.strip())

    slice_id = f"slice-{num}-{name}"
    claimed_by = claims.get(slice_id)

    # Precedence #1: foreign claim.
    if claimed_by and my_identity and claimed_by.strip() != my_identity:
        return _entry(branch, None, DivergenceClass.CLAIMED_BY_OTHER, f"claimed-by:{claimed_by}",
                      claimed_by, ahead, f"{slice_id} claimed by {claimed_by!r} (!= my identity)")

    # v2: read the ONE shared external vault directly (no git-tree reads).
    archive_dir = repo_root / VAULT_ROOT / "slices" / "archive" / slice_id
    milestone = repo_root / VAULT_ROOT / "slices" / slice_id / "milestone.json"
    if archive_dir.is_dir():
        return _entry(branch, None, DivergenceClass.STRANDED_COMPLETE, "archived", claimed_by, ahead,
                      "archived in the shared vault, branch unmerged")
    if milestone.is_file():
        stage, nxt = _milestone_stage_and_next(milestone)
        if _is_terminal(stage, nxt):
            return _entry(branch, None, DivergenceClass.STRANDED_COMPLETE, f"milestone:{stage}",
                          claimed_by, ahead, f"shared-vault milestone terminal (stage={stage}), branch unmerged")
        return _entry(branch, None, DivergenceClass.IN_PROGRESS, f"milestone:{stage}",
                      claimed_by, ahead, f"shared-vault milestone in-flight (stage={stage})")

    return _entry(branch, None, DivergenceClass.ORPHANED, "none", claimed_by, ahead,
                  "unmerged branch with no vault story (no slice folder in the shared vault, no claim)")


def _branchless_in_flight_slices(repo_root: Path, seen_keys: set[str]) -> list[StrandedEntry]:
    """Shared-vault ``slice-NNN-<name>/`` folders with a non-terminal milestone and NO matching
    ``slice/NNN-*`` ref — the normal pre-``/build-slice`` scaffold. One informational
    BRANCHLESS_IN_FLIGHT entry each (never a halt). Subordinate to the branch passes: emits
    ONLY for keys absent from ``seen_keys``. Fail-open per-folder."""
    out: list[StrandedEntry] = []
    slices_dir = repo_root / VAULT_ROOT / "slices"
    if not slices_dir.is_dir():
        return out
    for child in sorted(slices_dir.iterdir()):
        if not child.is_dir() or child.name == "archive":
            continue
        m = _SLICE_FOLDER_RE.match(child.name)
        if not m:
            continue
        num, name = m.group(1), m.group(2)
        key = f"{num}-{name}"
        if key in seen_keys:
            continue
        ms_path = child / "milestone.json"
        if not ms_path.is_file():
            continue
        stage, nxt = _milestone_stage_and_next(ms_path)
        if stage is None:
            continue
        if _is_terminal(stage, nxt):
            continue
        out.append(_entry(
            f"slice-{num}-{name}", None, DivergenceClass.BRANCHLESS_IN_FLIGHT, f"folder:{stage}",
            None, None,
            f"branchless in-flight slice (folder slice-{num}-{name}, stage={stage}, "
            f"no slice/{num}-* branch) — informational, parallel-safe",
        ))
    return out


# ----------------------------- library API -----------------------------


def classify_branches(repo_root: Path | str) -> list[StrandedEntry]:
    """Classify every unmerged ``slice/*`` branch into a divergence class.

    Raises ``_UsageError`` (→ exit 2) on hard input failure: git unavailable, not a git repo,
    or the default branch cannot be resolved. Never raises on a per-branch failure (→ per-entry
    INDETERMINATE)."""
    repo_root = Path(repo_root)
    try:
        rp = _run_git(repo_root, "rev-parse", "--git-dir")
    except FileNotFoundError as exc:
        raise _UsageError(f"git binary not available on PATH: {exc}") from exc
    if rp.returncode != 0:
        raise _UsageError(f"not a git repository: {repo_root}")
    default = _resolve_default_branch(repo_root)
    if not default:
        raise _UsageError("could not resolve default branch (no origin/HEAD, no init.defaultBranch)")

    my_identity = _read_git_identity(repo_root)
    claims = _load_claims(repo_root)

    entries: list[StrandedEntry] = []
    worktree_branches: set[str] = set()

    # --- worktree'd branches (reuse pulse detect + classify) ---
    for wt in detect_active_worktrees(repo_root):
        worktree_branches.add(wt.branch)
        slice_id = f"slice-{wt.slice_num}-{wt.slice_name}"
        claimed_by = claims.get(slice_id)
        dirty = _worktree_dirty(wt.path)
        if claimed_by and my_identity and claimed_by.strip() != my_identity:
            entries.append(_entry(
                wt.branch, wt.path, DivergenceClass.CLAIMED_BY_OTHER, f"claimed-by:{claimed_by}",
                claimed_by, None, f"{slice_id} claimed by {claimed_by!r}", dirty))
            continue
        cls = classify_worktree_state(wt, default, repo_root)
        if cls.state is WorktreeState.MERGED:
            continue
        if cls.state is WorktreeState.IN_PROGRESS:
            klass = DivergenceClass.IN_PROGRESS
        elif cls.state is WorktreeState.BUILT_BUT_NOT_MERGED:
            klass = DivergenceClass.STRANDED_COMPLETE
        else:
            klass = DivergenceClass.INDETERMINATE
        entries.append(_entry(
            wt.branch, wt.path, klass, f"worktree:{cls.state.value}:{cls.milestone_stage}",
            claimed_by, None, cls.reason, dirty))

    # --- bare branches (no live worktree) ---
    bare_tuples = _bare_slice_branches(repo_root, worktree_branches)
    for branch, num, name in bare_tuples:
        entry = _classify_bare_branch(repo_root, branch, num, name, default, claims, my_identity)
        if entry is not None:
            entries.append(entry)

    # --- branchless in-flight slices: shared-vault folders with no slice/* ref ---
    seen_keys = (
        {b[len("slice/"):] for b in worktree_branches}
        | {f"{num}-{name}" for (_b, num, name) in bare_tuples}
    )
    entries.extend(_branchless_in_flight_slices(repo_root, seen_keys))

    return entries


def compute_status(entries: list[StrandedEntry]) -> str:
    """``divergent`` iff >=1 halt-worthy entry; else ``clean``."""
    return "divergent" if any(e.halt for e in entries) else "clean"


def _entry_to_dict(e: StrandedEntry) -> dict[str, object]:
    return {
        "branch": e.branch, "worktree_path": e.worktree_path, "klass": e.klass.value,
        "halt": e.halt, "vault_state": e.vault_state, "claimed_by": e.claimed_by,
        "ahead": e.ahead, "dirty": e.dirty, "reason": e.reason,
    }


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="stranded-slice-audit",
        description="Classify unmerged slice/* branches into the v2 divergence model (shared vault).",
    )
    parser.add_argument("--repo-root", "--root", dest="repo_root", default=Path("."), type=Path,
                        help="repository root to inspect (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    try:
        entries = classify_branches(repo_root)
    except _UsageError as exc:
        sys.stderr.write(f"stranded-slice-audit: {exc}\n")
        return 2

    status = compute_status(entries)
    if args.json:
        print(json.dumps(
            {"action": "audit", "status": status, "entries": [_entry_to_dict(e) for e in entries]}, indent=2))
    else:
        n_div = sum(1 for e in entries if e.halt)
        print(f"stranded-slice-audit → status: {status} ({n_div} divergent)")
        for e in entries:
            tag = "HALT" if e.halt else "info"
            wt = f" wt={e.worktree_path}" if e.worktree_path else ""
            print(f"  {tag}  {e.branch}  [{e.klass.value}]{wt}  {e.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
