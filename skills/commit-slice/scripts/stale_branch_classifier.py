"""Stale-branch classifier — parallel-aware stale-vs-active discriminator (v2).

Per slice-089 / [[ADR-081]]: the `/commit-slice` stale-slice-branch guardrail
(`--merge` Step 5b sub-step 1 + `--push` Step 5c pre-flight #2) historically
STOPped on ANY non-current `slice/*` branch — a single-slice-era heuristic that
false-positives under the PSQ-1 / PSQ-2 / BRANCH-2 parallel-slice model, where a
concurrent `slice/NNN` branch WITH a live worktree is the NORMAL state.

This module classifies the repo's local `slice/*` branches by **worktree-backing**:

- ``parallel_slices``: worktree-backed, non-current  -> active parallel slice (ALLOW)
- ``orphan_branches``: worktree-less, non-current     -> genuine orphan/stranded (REFUSE)
- ``verdict`` == "refuse" iff ``orphan_branches`` is non-empty, else "allow"

**v2 port (slice-089 verbatim, no behavior change).** This tool is purely
git-based — it inspects only ``git`` ref/worktree state, never the vault — so the
shared-external-vault redesign and the md->json conversion DO NOT touch it. The
only edits from v1 are the import bootstrap + the package rename
``tools`` -> ``scripts.lib`` (``_stdout`` + the reused ``_parse_worktree_porcelain``
parser). v1 imported these from ``tools``; v2 imports them from the shared
``scripts.lib`` leaf.

Worktree-backing is determined by parsing ``git worktree list --porcelain`` branch
lines DIRECTLY (reusing the raw ``pulse_worktree_resolver._parse_worktree_porcelain``),
NOT the higher-level ``detect_active_worktrees`` — whose slice-name-suffix filter
(``_SLICE_BRANCH_RE``) + prunable/detached/path-gone drops would misclassify a
worktree-backed branch with a non-canonical name (e.g. ``slice/077``) as an orphan
(slice-089 Critic B3). The classifier strips the ``refs/heads/`` prefix itself so the
porcelain ``branch`` value (raw refname) lands in the same short-form space as
``git for-each-ref --format='%(refname:short)'`` (meta-Critic B-add-1).

The current slice's own worktree is excluded by **path-equality** (``git rev-parse
--show-toplevel``), with current-branch exclusion as defense-in-depth — robust to a
Windows path-normalization mismatch (slice-089 Critic B1 + M-add-1).

Read-only — never mutates git state.

Per slice-090's documented cp1252 failure class, all git output is decoded UTF-8 with
``errors="replace"`` so it round-trips on a non-UTF-8 host (Windows cp1252). This now
routes through the single shared ``scripts.lib._git_default_branch.run_git`` (the runner
that historically lacked ``encoding=`` — that bug is fixed at the source), wrapped only
to map a missing git binary to ``_GitError``.

Cross-spec parity (CSP-1) with ``pulse_worktree_resolver`` / ``parallel_conflict_resolver``:
argparse ``--repo-root`` (parse-time ``default=Path(".")``, post-parse ``.resolve()``),
``--json``, JSON ``{"action": ...}`` stdout / ``{"action":..., "error":...}`` stderr,
exit codes 0 success / 1 runtime error / 2 malformed CLI args.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._git_default_branch import run_git
from scripts.lib.pulse_worktree_resolver import _parse_worktree_porcelain

__all__ = [
    "StaleBranchVerdict",
    "classify_stale_branches",
    "main",
]


_REFS_HEADS = "refs/heads/"
_SLICE_PREFIX = "slice/"

# Canonical BRANCH-2 slice-branch shape (mirrors
# pulse_worktree_resolver._SLICE_BRANCH_RE). A worktree-backed branch that
# does NOT match this shape (e.g. `slice/077`, no `-name` suffix) is still treated
# as backed (ALLOW) but flagged in `noncanonical_backed` so the consumer can surface
# a rename hint (per ADR-063 split-slice naming).
_SLICE_BRANCH_RE = re.compile(r"^slice/(\d{3})-(.+)$")


class _GitError(RuntimeError):
    """A git subprocess returned non-zero or the git binary is unavailable.

    Mapped to CLI exit 1 (runtime error) per the CSP-1 0/1/2 contract.
    """


@dataclass(frozen=True)
class StaleBranchVerdict:
    verdict: str  # "allow" | "refuse"
    current_branch: str | None
    parallel_slices: list[str]
    orphan_branches: list[str]
    noncanonical_backed: list[str]


# ----------------------------- private helpers -----------------------------


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Thin wrapper over the shared UTF-8-safe runner
    (``scripts.lib._git_default_branch.run_git``) that maps a missing git binary to
    ``_GitError`` (the CSP-1 exit-1 contract). The UTF-8 + ``errors="replace"`` decoding
    lives in the shared runner — no second runner definition here.
    """
    try:
        return run_git(repo_root, *args)
    except FileNotFoundError as exc:  # git binary not on PATH
        raise _GitError(f"git binary unavailable: {exc}") from exc


def _norm_path(p: str) -> str:
    """Normalize a filesystem path for cross-surface equality: `\\`->`/`, strip a
    trailing separator. Both `git rev-parse --show-toplevel` and the porcelain
    `worktree` line emit forward slashes, but we normalize defensively (slice-089
    M-add-1); the current-branch belt is the load-bearing self-exclusion if this slips.
    """
    return p.replace("\\", "/").rstrip("/")


def _short_ref(branch_ref: str) -> str | None:
    """Strip a leading `refs/heads/` and return the short ref iff it is a `slice/*`
    branch; else None. (meta-Critic B-add-1: the porcelain parser returns the RAW
    refname `refs/heads/slice/...`, NOT the stripped short form.)
    """
    if not branch_ref.startswith(_REFS_HEADS):
        return None
    short = branch_ref[len(_REFS_HEADS):]
    return short if short.startswith(_SLICE_PREFIX) else None


# ----------------------------- library API -----------------------------


def classify_stale_branches(repo_root: Path) -> StaleBranchVerdict:
    """Classify the repo's local `slice/*` branches by worktree-backing.

    Raises _GitError on any git command failure / not-a-repo / git unavailable
    (CLI exit 1). Never silently defaults — fail-visible per R-7.
    """
    # 1. Resolve current branch + current worktree path (for self-exclusion).
    cb = _run_git(repo_root, "symbolic-ref", "--short", "HEAD")
    current_branch = cb.stdout.strip() if cb.returncode == 0 else None  # detached HEAD -> None

    tp = _run_git(repo_root, "rev-parse", "--show-toplevel")
    if tp.returncode != 0:
        raise _GitError(f"rev-parse --show-toplevel failed: {tp.stderr.strip()}")
    current_path = _norm_path(tp.stdout.strip())

    # 2. all_slice_refs: every local slice/* ref (short form), minus the current branch.
    fe = _run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/slice/")
    if fe.returncode != 0:
        raise _GitError(f"for-each-ref failed: {fe.stderr.strip()}")
    all_slice_refs = {ln.strip() for ln in fe.stdout.splitlines() if ln.strip()}
    if current_branch:
        all_slice_refs.discard(current_branch)

    # 3. backed: slice/* branches with a live worktree (short form), excluding the
    #    current slice's own worktree by path-equality + branch belt.
    wl = _run_git(repo_root, "worktree", "list", "--porcelain")
    if wl.returncode != 0:
        raise _GitError(f"worktree list --porcelain failed: {wl.stderr.strip()}")
    backed: set[str] = set()
    noncanonical_backed: list[str] = []
    for block in _parse_worktree_porcelain(wl.stdout):
        short = _short_ref(block.get("branch", ""))
        if short is None:
            continue  # not a slice/* branch (main worktree on default, detached, etc.)
        wt_path = _norm_path(block.get("worktree", ""))
        if wt_path == current_path:
            continue  # current slice's own worktree (path-equality self-exclusion)
        if current_branch and short == current_branch:
            continue  # defense-in-depth branch belt
        backed.add(short)
        if not _SLICE_BRANCH_RE.match(short):
            noncanonical_backed.append(short)

    # 4. set ops (both operands short-form — meta-Critic B-add-1).
    parallel_slices = sorted(all_slice_refs & backed)
    orphan_branches = sorted(all_slice_refs - backed)
    verdict = "refuse" if orphan_branches else "allow"

    return StaleBranchVerdict(
        verdict=verdict,
        current_branch=current_branch,
        parallel_slices=parallel_slices,
        orphan_branches=orphan_branches,
        noncanonical_backed=sorted(noncanonical_backed),
    )


# ----------------------------- CLI -----------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stale_branch_classifier",
        description=(
            "Stale-branch classifier (ADR-081) — classify local slice/* branches by "
            "worktree-backing for the /commit-slice stale-branch guardrail. Read-only."
        ),
    )
    parser.add_argument(
        "--repo-root",
        "--root",
        dest="repo_root",
        type=Path,
        default=Path("."),
        help="Repo root (default: cwd). Resolved post-parse. `--root` is an alias.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output (default: human-readable text).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exit 0 success, 1 runtime error, 2 malformed CLI args."""
    _stdout.reconfigure_stdout_utf8()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        v = classify_stale_branches(repo_root)
    except _GitError as exc:
        sys.stderr.write(
            json.dumps({"action": "classify-stale-branches", "error": str(exc)}) + "\n"
        )
        return 1
    payload = {
        "action": "classify-stale-branches",
        "verdict": v.verdict,
        "current_branch": v.current_branch,
        "parallel_slices": v.parallel_slices,
        "orphan_branches": v.orphan_branches,
        "noncanonical_backed": v.noncanonical_backed,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        sys.stdout.write(
            f"stale-branch-classifier -> verdict: {v.verdict}; "
            f"parallel={v.parallel_slices}; orphans={v.orphan_branches}"
            + (f"; noncanonical-backed={v.noncanonical_backed}" if v.noncanonical_backed else "")
            + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
