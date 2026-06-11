"""Pulse Worktree Resolver — v2 worktree detection + state classification.

Library API + CLI for `/pulse` worktree augmentation and a building block for
`scripts.lib.stranded_slice_audit`: detect non-main worktrees on
`slice/NNN-<name>` branches and classify each worktree's HEAD-vs-default state.

**v2 changes from v1 (slice-077 / ADR-070):**
- Slice state is read from the ONE shared EXTERNAL vault, not a per-worktree
  in-tree copy. The milestone file is `milestone.json` (v2 JSON artifact), read
  via the absolute `VAULT_ROOT` (so it resolves to the shared store regardless of
  which worktree invoked the audit) — `stage` is a JSON field, not YAML frontmatter.
- The terminal "built, ready to merge" set is `{reflect, complete}` (v2 loop ends
  at `/reflect` → stage `complete`); any other stage is mid-build → IN_PROGRESS.
- `_resolve_default_branch` now comes from the shared `scripts.lib._git_default_branch`
  leaf (v1 imported it from the single-skill `branch_workflow_audit`).
- The v1 `should_suppress_vault_forward_population_flag` helper (compared installed
  `~/.claude/...` surfaces to a worktree copy) is REMOVED — it belongs to the v1
  forward-sync / `~/.claude` install-parity model, which is part of the deferred
  v2 packaging redesign (the plugin is the distribution unit). Re-add when that
  decision lands.

Read-only — never mutates worktree/git state.

4-state taxonomy:
- IN_PROGRESS:          milestone `stage` NOT in {reflect, complete}; still building.
- BUILT_BUT_NOT_MERGED: stage in {reflect, complete} AND HEAD is NOT an ancestor of <default>.
- MERGED:               HEAD IS an ancestor of <default>.
- UNKNOWN:              fail-closed on any parse failure.
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
from typing import Any, Iterable, Mapping

# --- plugin-root import bootstrap (BB-17) — a skill invokes this by ABSOLUTE PATH from
# the user's CWD, where `scripts.lib` is not importable; add the plugin root, mirroring
# the sibling shared libs (active_slice, stranded_slice_audit, …). No-op under `-m`. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout
from scripts.lib._git_default_branch import resolve_default_branch as _resolve_default_branch
from scripts.lib._git_default_branch import run_git as _run_git
from scripts.lib._vault_paths import VAULT_ROOT

__all__ = [
    "WorktreeState",
    "WorktreeInfo",
    "WorktreeStateClassification",
    "detect_active_worktrees",
    "classify_worktree_state",
    "augment_pulse_state_dict",
    "main",
]


# Canonical slice-branch shape `slice/NNN-<name>` (zero-padded 3-digit number).
_SLICE_BRANCH_RE = re.compile(r"^slice/(\d{3})-(.+)$")

# Terminal "built, ready to merge" stages (v2 loop ends at /reflect -> `complete`).
_TERMINAL_STAGES = frozenset({"reflect", "complete"})

# UNKNOWN-reason enumeration (fail-closed classification paths).
_UNKNOWN_REASONS = (
    "fresh-worktree-no-milestone",
    "milestone-missing-in-active-and-archive",
    "milestone-json-malformed",
    "detached-head",
    "dirty-worktree",
    "merge-base-error",
    "head-unresolvable",
    "slice-folder-name-drift",
)

# Per-UNKNOWN-reason WARN templates (the `skills/pulse/SKILL.md` consumer reads this
# constant and does `.get(reason, <fallback>)`; the `{slice}` placeholder is filled
# by the consumer).
_UNKNOWN_REASON_WARN_TEMPLATES: Mapping[str, str] = {
    "fresh-worktree-no-milestone": (
        "WARN: worktree {slice} has no milestone.json yet (fresh scaffold) — state "
        "UNKNOWN; run /slice in the worktree or verify the slice folder."
    ),
    "milestone-missing-in-active-and-archive": (
        "WARN: worktree {slice} milestone.json is absent from both the active and "
        "archive paths — state UNKNOWN; verify the slice folder name."
    ),
    "milestone-json-malformed": (
        "WARN: worktree {slice} milestone.json is malformed (no parseable `stage`) — "
        "state UNKNOWN; repair the JSON."
    ),
    "detached-head": (
        "WARN: worktree {slice} is on a detached HEAD — state UNKNOWN; check out the "
        "slice/NNN-<name> branch."
    ),
    "dirty-worktree": (
        "WARN: worktree {slice} has a dirty working tree — state UNKNOWN; commit or "
        "inspect before relying on classification."
    ),
    "merge-base-error": (
        "WARN: worktree {slice} `git merge-base --is-ancestor` errored — state UNKNOWN; "
        "HEAD-vs-default ancestry is indeterminate."
    ),
    "head-unresolvable": (
        "WARN: worktree {slice} HEAD could not be resolved — state UNKNOWN; the worktree "
        "may be empty or corrupt."
    ),
    "slice-folder-name-drift": (
        "WARN: worktree {slice} branch name and slice folder name disagree — state "
        "UNKNOWN; reconcile the folder/branch naming."
    ),
}


class WorktreeState(Enum):
    IN_PROGRESS = "IN_PROGRESS"
    BUILT_BUT_NOT_MERGED = "BUILT_BUT_NOT_MERGED"
    MERGED = "MERGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str
    head_sha: str
    slice_num: str
    slice_name: str
    milestone_path: Path | None


@dataclass(frozen=True)
class WorktreeStateClassification:
    state: WorktreeState
    reason: str
    milestone_stage: str = ""


# ----------------------------- private helpers -----------------------------
# `_run_git` is the shared UTF-8-safe runner imported above (scripts.lib._git_default_branch.run_git).


def _parse_worktree_porcelain(output: str) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` into one dict per block. The main
    worktree is the first block; lines are `key value` or a bare flag (`bare`,
    `detached`, `prunable`)."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if current:
                blocks.append(current)
                current = {}
            continue
        if " " in line:
            key, _, value = line.partition(" ")
            current[key] = value
        else:
            current[line] = ""
    if current:
        blocks.append(current)
    return blocks


def _resolve_milestone_path(scan_root: Path, slice_num: str, slice_name: str) -> Path | None:
    """Resolve `milestone.json` for a slice — active OR archived — in the SHARED
    external vault. Returns None if neither exists.

    `VAULT_ROOT` is absolute (the external store), so `scan_root / VAULT_ROOT`
    collapses to `VAULT_ROOT` — the read is the same shared vault regardless of
    which worktree invoked it (v2: one vault, not per-branch copies). `scan_root`
    is retained for signature compatibility / a hypothetical relative VAULT_ROOT.
    """
    folder = f"slice-{slice_num}-{slice_name}"
    active = scan_root / VAULT_ROOT / "slices" / folder / "milestone.json"
    if active.is_file():
        return active
    archive = scan_root / VAULT_ROOT / "slices" / "archive" / folder / "milestone.json"
    if archive.is_file():
        return archive
    return None


def _parse_milestone_stage(milestone_path: Path) -> str | None:
    """Read the `stage` field from milestone.json. Returns None on any failure
    (missing/unreadable file, malformed JSON, missing/empty `stage`)."""
    try:
        text = milestone_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    stage = data.get("stage")
    if not isinstance(stage, str) or not stage.strip():
        return None
    return stage.strip()


# ----------------------------- library API -----------------------------


def detect_active_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """Detect non-main `slice/NNN-<name>` worktrees registered with the repo.

    Runs `git worktree list --porcelain`; filters the main worktree, prunable
    worktrees, non-`slice/*` branches, and missing paths. Milestone is resolved
    from the shared external vault. Returns [] on git failure / none registered.
    """
    result = _run_git(repo_root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return []
    blocks = _parse_worktree_porcelain(result.stdout)
    if blocks and "bare" in blocks[0]:
        sys.stderr.write("WARN: bare repo detected; no active worktrees applicable\n")
        return []
    candidates = blocks[1:] if blocks else []  # first block is the main worktree
    out: list[WorktreeInfo] = []
    for block in candidates:
        if "prunable" in block:
            continue
        branch_ref = block.get("branch", "")
        if not branch_ref.startswith("refs/heads/"):
            continue
        branch = branch_ref[len("refs/heads/"):]
        m = _SLICE_BRANCH_RE.match(branch)
        if not m:
            continue
        slice_num, slice_name = m.group(1), m.group(2)
        wt_path_str = block.get("worktree", "")
        if not wt_path_str:
            continue
        wt_path = Path(wt_path_str)
        if not wt_path.exists():
            continue
        head_sha = block.get("HEAD", "")
        milestone_path = _resolve_milestone_path(wt_path, slice_num, slice_name)
        out.append(WorktreeInfo(
            path=str(wt_path).replace("\\", "/"),
            branch=branch,
            head_sha=head_sha,
            slice_num=slice_num,
            slice_name=slice_name,
            milestone_path=milestone_path,
        ))
    return out


def classify_worktree_state(
    worktree: WorktreeInfo,
    default_branch: str,
    repo_root: Path,
) -> WorktreeStateClassification:
    """Classify a worktree's HEAD-vs-default state into one of 4 WorktreeState
    values. Fail-closed: UNKNOWN with a specific reason on any parse failure."""
    if worktree.milestone_path is None:
        return WorktreeStateClassification(WorktreeState.UNKNOWN, "fresh-worktree-no-milestone")
    if not worktree.milestone_path.is_file():
        return WorktreeStateClassification(WorktreeState.UNKNOWN, "milestone-missing-in-active-and-archive")

    stage = _parse_milestone_stage(worktree.milestone_path)
    if stage is None:
        return WorktreeStateClassification(WorktreeState.UNKNOWN, "milestone-json-malformed")

    head_sha = worktree.head_sha
    if not head_sha:
        rev = _run_git(Path(worktree.path), "rev-parse", "HEAD")
        if rev.returncode != 0 or not rev.stdout.strip():
            return WorktreeStateClassification(WorktreeState.UNKNOWN, "head-unresolvable", stage)
        head_sha = rev.stdout.strip()

    # stage NOT terminal -> IN_PROGRESS (mid-build; ancestry irrelevant).
    if stage not in _TERMINAL_STAGES:
        return WorktreeStateClassification(
            WorktreeState.IN_PROGRESS, f"milestone stage={stage}; pre-terminal", stage,
        )

    # terminal stage -> ancestry disambiguates MERGED vs BUILT_BUT_NOT_MERGED.
    anc = _run_git(repo_root, "merge-base", "--is-ancestor", head_sha, default_branch)
    if anc.returncode == 0:
        return WorktreeStateClassification(
            WorktreeState.MERGED, f"HEAD {head_sha[:8]} reachable from {default_branch}", stage,
        )
    if anc.returncode != 1:
        return WorktreeStateClassification(WorktreeState.UNKNOWN, "merge-base-error", stage)
    return WorktreeStateClassification(
        WorktreeState.BUILT_BUT_NOT_MERGED,
        f"milestone terminal (stage={stage}); HEAD not ancestor of {default_branch}", stage,
    )


def augment_pulse_state_dict(
    base_state_dict: dict[str, Any],
    detected_worktrees: list[WorktreeInfo],
    classifications: list[WorktreeStateClassification],
) -> dict[str, Any]:
    """Augment the /pulse state-dict with worktree fields + a resolved
    `recommended_next_action_override` (BUILT_BUT_NOT_MERGED → commit-slice;
    else IN_PROGRESS → continue per milestone)."""
    augmented = dict(base_state_dict)
    augmented["worktrees"] = list(detected_worktrees)
    augmented["worktree_classifications"] = list(classifications)
    override: str | None = None
    for info, cls in zip(detected_worktrees, classifications):
        if cls.state == WorktreeState.BUILT_BUT_NOT_MERGED:
            override = f"cd {info.path} && /commit-slice --merge"
            break
        if cls.state == WorktreeState.IN_PROGRESS:
            override = f"cd {info.path} && continue per worktree milestone.json next_action"
            # don't break — a later BUILT_BUT_NOT_MERGED takes priority
    augmented["recommended_next_action_override"] = override
    return augmented


# ----------------------------- CLI -----------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.lib.pulse_worktree_resolver",
        description="Detect slice worktrees and classify worktree state (read-only).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detect", action="store_true",
                      help="Detect non-main slice worktrees; emit list of WorktreeInfo.")
    mode.add_argument("--classify", metavar="slice-NNN-<name>",
                      help="Classify the named slice's worktree state (slice folder name).")
    parser.add_argument("--json", action="store_true", help="Emit JSON (default: text).")
    parser.add_argument("--repo-root", type=Path, default=Path("."),
                        help="Repo root (default: cwd). Resolved post-parse.")
    return parser


def _worktree_info_to_dict(info: WorktreeInfo) -> dict[str, Any]:
    return {
        "path": info.path, "branch": info.branch, "head_sha": info.head_sha,
        "slice_num": info.slice_num, "slice_name": info.slice_name,
        "milestone_path": str(info.milestone_path) if info.milestone_path else None,
    }


def _classification_to_dict(cls: WorktreeStateClassification) -> dict[str, Any]:
    return {"state": cls.state.value, "reason": cls.reason, "milestone_stage": cls.milestone_stage}


def _emit_error(action: str, message: str) -> None:
    sys.stderr.write(json.dumps({"action": action, "error": message}) + "\n")


def _run_detect(repo_root: Path, json_mode: bool) -> int:
    worktrees = detect_active_worktrees(repo_root)
    if json_mode:
        sys.stdout.write(json.dumps(
            {"action": "detect", "worktrees": [_worktree_info_to_dict(w) for w in worktrees]}) + "\n")
    else:
        sys.stdout.write(f"Detected {len(worktrees)} slice worktree(s).\n")
        for w in worktrees:
            sys.stdout.write(f"  {w.branch} @ {w.path} (HEAD {w.head_sha[:8]})\n")
    return 0


def _run_classify(slice_arg: str, repo_root: Path, json_mode: bool) -> int:
    default_branch = _resolve_default_branch(repo_root)
    if default_branch is None:
        _emit_error("classify", "default-branch-unresolvable: neither origin/HEAD nor init.defaultBranch resolved")
        return 1
    worktrees = detect_active_worktrees(repo_root)
    matching = [w for w in worktrees if f"slice-{w.slice_num}-{w.slice_name}" == slice_arg]
    if not matching:
        _emit_error("classify", f"no worktree found for {slice_arg!r}")
        return 1
    cls = classify_worktree_state(matching[0], default_branch, repo_root)
    if json_mode:
        sys.stdout.write(json.dumps(
            {"action": "classify", "slice": slice_arg, "classification": _classification_to_dict(cls)}) + "\n")
    else:
        sys.stdout.write(f"{slice_arg}: {cls.state.value} — {cls.reason}\n")
    return 0  # BB-18: UNKNOWN is a valid computed classification (carried in the payload), not a runtime error


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.detect:
        return _run_detect(repo_root, args.json)
    return _run_classify(args.classify, repo_root, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
