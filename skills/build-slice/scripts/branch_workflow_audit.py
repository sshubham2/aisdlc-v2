"""Branch workflow audit (BRANCH-1) — v2 JSON.

Validates that the current git branch matches the active slice's
``slice/NNN-<slice-name>`` pattern, with a documented ``BRANCH=skip`` /
``WORKTREE=skip`` escape-hatch via ``build-log.json`` events.

Per BRANCH-1 (methodology-changelog.md v0.35.0). Fires at ``/build-slice`` Step 6
pre-finish gate to catch a slice that bypassed the branch-create discipline OR
ran on the wrong branch.

**v2 changes from v1.**
- ``build-log.md`` Events (markdown lines) -> ``build-log.json`` ``events``. The
  ``BRANCH=skip`` / ``WORKTREE=skip`` escape-hatch lines are matched inside event
  strings (per ``skills/build-slice/SKILL.md`` Step 4: each event is
  ``<YYYY-MM-DD HH:MM> <CATEGORY>: <description>``) AND inside object-event
  ``note`` fields (the ``{at, note}`` example shape), so both encodings work.
- ``_resolve_default_branch`` is NO LONGER defined locally — imported (with
  ``run_git``) from the shared ``scripts.lib._git_default_branch`` leaf (pulse /
  stranded already share it from there). ``slice_branch_name`` /
  ``canonical_worktree_path`` are imported from ``scripts.lib._worktree_paths``
  (single source of truth, BRANCH-3 / slice-099).
- ``_vault_git.resolve_repo_root_for_slice`` is GONE; the repo root is ``--root``
  (default cwd) — BRANCH-1 inspects the live git repo, not the external vault.
  The slice FOLDER is used only to read ``build-log.json`` + derive the expected
  branch from the folder name.

Usage:
    python branch_workflow_audit.py <slice-folder>
    python branch_workflow_audit.py --json <slice-folder>
    python branch_workflow_audit.py --root <repo-root> <slice-folder>

Exit codes:
    0  clean (current branch matches active slice OR canonical escape-hatch present)
    1  violations (on default branch + no escape-hatch, or branch-mismatch, or stale-slice-branch)
    2  usage error (slice-folder missing, git unavailable, default-branch-unresolvable)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402
from scripts.lib._git_default_branch import (  # noqa: E402
    # slice-022: BRANCH-1 keys on the branch slices are CUT FROM and must not sit on.
    # Under the uat/master model that is the INTEGRATION branch (uat), not the released
    # trunk -- so the on-default-branch check fires when work is parked on uat.
    resolve_integration_branch as _resolve_default_branch,
    run_git as _run_git,
)
from scripts.lib._worktree_paths import (  # noqa: E402
    _SLICE_FOLDER_RE,
    canonical_worktree_path as _shared_canonical_worktree_path,
    slice_branch_name as _shared_slice_branch_name,
)

# Canonical regex for the `BRANCH=skip` escape-hatch line in build-log.json events.
# The leading `- ` markdown bullet is OPTIONAL (v1 build-log.md lines carried it;
# v2 plain-string events do not — per skills/build-slice/SKILL.md Step 4 the event
# is `<YYYY-MM-DD HH:MM> <CATEGORY>: <description>`).
_BRANCH_SKIP_LINE_RE = re.compile(
    r"^(?:- )?\d{4}-\d{2}-\d{2} \d{2}:\d{2} (?:DEVIATION: )?BRANCH=skip\b.+rationale: .+",
)

# Canonical regex for the `WORKTREE=skip` escape-hatch line (BRANCH-2; ADR-063).
_WORKTREE_SKIP_LINE_RE = re.compile(
    r"^(?:- )?\d{4}-\d{2}-\d{2} \d{2}:\d{2} (?:DEVIATION: )?WORKTREE=skip\b.+rationale: .+",
)

# Diagnostic-only split-slice folder shape (slice-043 / ADR-046 / R-6).
_SPLIT_SLICE_FOLDER_RE = re.compile(r"^slice-(\d{3})([A-Z]+)-(.+)$")


@dataclass(frozen=True)
class BranchViolation:
    kind: str       # "on-default-branch" | "slice-branch-mismatch" |
                    # "escape-hatch-malformed" | "default-branch-unresolvable" |
                    # "stale-slice-branch" | "usage-error" | "worktree-skip-malformed"
    severity: str   # "Important" (refuses) or "Warning" (for stale-slice-branch)
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    slice_folder: str = ""
    repo_root: str = ""
    expected_branch: str = ""
    actual_branch: str = ""
    resolved_default_branch: str = ""
    escape_hatch_used: bool = False
    escape_hatch_rationale: str | None = None
    worktree_skip_used: bool = False
    worktree_skip_rationale: str | None = None
    violations: list[BranchViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": "BRANCH-1",
            "slice_folder": self.slice_folder,
            "repo_root": self.repo_root,
            "expected_branch": self.expected_branch,
            "actual_branch": self.actual_branch,
            "resolved_default_branch": self.resolved_default_branch,
            "escape_hatch_used": self.escape_hatch_used,
            "escape_hatch_rationale": self.escape_hatch_rationale,
            "worktree_skip_used": self.worktree_skip_used,
            "worktree_skip_rationale": self.worktree_skip_rationale,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "violation_count": len([v for v in self.violations if v.severity == "Important"]),
                "warning_count": len([v for v in self.violations if v.severity == "Warning"]),
                "clean": all(v.severity != "Important" for v in self.violations),
            },
        }


def _current_branch(repo_root: Path) -> str | None:
    """Return current branch name or None if detached HEAD or error."""
    result = _run_git(repo_root, "branch", "--show-current")
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch if branch else None


def _slice_branch_name(slice_folder: Path) -> str:
    """Compute expected ``slice/NNN-<slice-name>`` from slice-folder name (shared helper)."""
    return _shared_slice_branch_name(slice_folder.name)


def _build_log_event_strings(slice_folder: Path) -> list[str]:
    """Collect candidate escape-hatch lines from build-log.json events.

    v2 events may be plain strings (``<date time> CATEGORY: ...``) or objects with
    a ``note`` field (the ``{at, note}`` example shape). Both are scanned: object
    events contribute their ``note`` (optionally prefixed with ``at`` so an
    ``at``-only timestamp + ``BRANCH=skip`` note still matches the date-anchored
    regex). Returns [] on any read/parse failure (no escape-hatch found).
    """
    build_log = slice_folder / "build-log.json"
    if not build_log.exists():
        return []
    try:
        data = json.loads(build_log.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    out: list[str] = []
    for ev in events:
        if isinstance(ev, str):
            out.append(ev)
        elif isinstance(ev, dict):
            note = ev.get("note")
            at = ev.get("at")
            if isinstance(note, str):
                out.append(note)
                if isinstance(at, str) and at.strip():
                    out.append(f"{at} {note}")
    return out


def _scan_skip(events: list[str], canonical_re: re.Pattern[str], keyword: str,
               malformed_kind: str, adr_note: str) -> tuple[bool, str | None, BranchViolation | None]:
    """Scan event strings for a canonical ``<keyword>`` escape-hatch line.

    Returns (skip_used, rationale, malformed_violation). A line containing
    ``<keyword>`` but not matching the canonical shape (HH:MM + rationale:) yields
    a malformed violation.
    """
    for line in events:
        if canonical_re.match(line.strip()):
            idx = line.find("rationale:")
            rationale = line[idx + len("rationale:"):].strip() if idx >= 0 else None
            return True, rationale, None
    if any(keyword in line for line in events):
        return False, None, BranchViolation(
            kind=malformed_kind, severity="Important",
            message=(
                f"build-log.json events contains `{keyword}` but doesn't conform to "
                f"canonical shape. Required: `<YYYY-MM-DD HH:MM> DEVIATION: {keyword} — "
                f"rationale: <text>` per skills/build-slice/SKILL.md ({adr_note})."
            ),
        )
    return False, None, None


def _check_stale_slice_branches(repo_root: Path, current_branch: str) -> list[BranchViolation]:
    """Detect stale ``slice/*`` branches (artefact of prior ``--merge`` recovery)."""
    result = _run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/slice/")
    if result.returncode != 0:
        return []
    branches = [b.strip() for b in result.stdout.splitlines() if b.strip()]
    stale = [b for b in branches if b != current_branch]
    if not stale:
        return []
    return [BranchViolation(
        kind="stale-slice-branch", severity="Warning",
        message=(
            f"Stale `slice/*` branches present (artefact of prior `--merge` "
            f"conflict-recovery): {stale}. Inspect with `git log <default>..<branch>` "
            f"and `git branch -d` each after verifying merged."
        ),
    )]


def _paths_equivalent(a: Path, b: Path) -> bool:
    """Compare two paths tolerating Windows case-insensitivity + symlink/junction targets."""
    try:
        a_resolved = a.resolve(strict=False)
        b_resolved = b.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if a_resolved.exists() and b_resolved.exists():
        try:
            return a_resolved.samefile(b_resolved)
        except (OSError, FileNotFoundError):
            pass
    a_norm = os.path.normcase(os.path.realpath(str(a_resolved)))
    b_norm = os.path.normcase(os.path.realpath(str(b_resolved)))
    return a_norm == b_norm


def _slice_branch_in_worktree(repo_root: Path, slice_branch: str) -> Path | None:
    """Locate which worktree (if any) has ``slice_branch`` checked out."""
    result = _run_git(repo_root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return None
    current_wt: Path | None = None
    target_ref = f"branch refs/heads/{slice_branch}"
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_wt = Path(line[len("worktree "):])
        elif line == target_ref and current_wt is not None:
            return current_wt
    return None


def audit(slice_folder: Path, repo_root: Path | None = None) -> AuditResult:
    """Run the BRANCH-1 audit against a slice folder."""
    slice_folder = Path(slice_folder).resolve()
    if not slice_folder.exists():
        return AuditResult(
            slice_folder=str(slice_folder),
            violations=[BranchViolation(
                kind="usage-error", severity="Important",
                message=f"slice folder not found: {slice_folder}",
            )],
        )

    repo_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd()
    # Confirm the repo root is a git work tree (the external vault has no .git).
    rp = _run_git(repo_root, "rev-parse", "--git-dir")
    if rp.returncode != 0:
        return AuditResult(
            slice_folder=str(slice_folder),
            repo_root=str(repo_root),
            violations=[BranchViolation(
                kind="usage-error", severity="Important",
                message=(
                    f"--root {repo_root} is not a git repository. Pass the code "
                    f"worktree root via --root (the slice folder lives in the "
                    f"external vault, which has no .git)."
                ),
            )],
        )

    result = AuditResult(slice_folder=str(slice_folder), repo_root=str(repo_root))

    # Compute expected slice branch from folder name.
    expected = _slice_branch_name(slice_folder)
    if not expected:
        split_match = _SPLIT_SLICE_FOLDER_RE.match(slice_folder.name)
        if split_match:
            digits, letters, rest = split_match.groups()
            message = (
                f"split-slice follow-up folder name not accepted: {slice_folder.name!r}. "
                f"Per ADR-046 / BRANCH-1, split-slice follow-up folders are numeric "
                f"`slice-NNN-`; the `NNNx` letter (here `{digits}{letters}`) is a prose "
                f"lineage label only. Rename to the next free numeric slice number."
            )
        else:
            message = (
                f"slice folder name does not match `slice-NNN-<name>` pattern: "
                f"{slice_folder.name}"
            )
        result.violations.append(BranchViolation(kind="usage-error", severity="Important", message=message))
        return result
    result.expected_branch = expected

    # Get current branch.
    current = _current_branch(repo_root)
    if current is None:
        result.violations.append(BranchViolation(
            kind="usage-error", severity="Important",
            message="cannot resolve current branch (detached HEAD or git error)",
        ))
        return result
    result.actual_branch = current

    # Resolve the INTEGRATION branch (slice-022): the branch slices are cut from and
    # must not sit on (uat when present, else the released default trunk).
    default = _resolve_default_branch(repo_root)
    if default is None:
        result.violations.append(BranchViolation(
            kind="default-branch-unresolvable", severity="Important",
            message=(
                "cannot resolve repo default branch — `git symbolic-ref "
                "refs/remotes/origin/HEAD`, `git config init.defaultBranch`, a `main`/`master` "
                "ref, AND `git symbolic-ref --short HEAD` all failed (is this a git repo on a "
                "branch?). `git init` + an initial commit, or set "
                "`git config init.defaultBranch <name>`, then retry."
            ),
        ))
        return result
    result.resolved_default_branch = default

    # Stale slice branches (warning class — doesn't refuse).
    result.violations.extend(_check_stale_slice_branches(repo_root, current))

    # Escape-hatch scan (build-log.json events).
    events = _build_log_event_strings(slice_folder)

    escape_hatch, rationale, malformed = _scan_skip(
        events, _BRANCH_SKIP_LINE_RE, "BRANCH=skip", "escape-hatch-malformed", "Step 7c")
    if malformed:
        result.violations.append(malformed)
        return result
    result.escape_hatch_used = escape_hatch
    result.escape_hatch_rationale = rationale

    worktree_skip_used, worktree_skip_rationale, worktree_skip_malformed = _scan_skip(
        events, _WORKTREE_SKIP_LINE_RE, "WORKTREE=skip", "worktree-skip-malformed", "Step 7c / BRANCH-2 / ADR-063")
    result.worktree_skip_used = worktree_skip_used
    result.worktree_skip_rationale = worktree_skip_rationale
    if worktree_skip_malformed is not None:
        result.violations.append(worktree_skip_malformed)
        return result

    combined_skip = escape_hatch or worktree_skip_used

    # Main-tree mode: detect "forgot to cd into the worktree".
    if not combined_skip and current == default:
        wt_path = _slice_branch_in_worktree(repo_root, expected)
        if wt_path is not None and not _paths_equivalent(wt_path, repo_root):
            result.violations.append(BranchViolation(
                kind="slice-branch-mismatch", severity="Important",
                message=(
                    f"Slice branch `{expected}` is checked out in worktree `{wt_path}` but "
                    f"your cwd/--root is `{repo_root}`. Did you forget to `cd {wt_path}`? "
                    f"Run the audit with --root {wt_path}; or document `WORKTREE=skip — "
                    f"rationale: <text>` in build-log.json events."
                ),
            ))
            return result

    # Branch-state logic.
    if current == default:
        if not combined_skip:
            result.violations.append(BranchViolation(
                kind="on-default-branch", severity="Important",
                message=(
                    f"active-slice work occurred on default branch '{default}' with no canonical "
                    f"`BRANCH=skip — rationale: <text>` or `WORKTREE=skip — rationale: <text>` "
                    f"escape-hatch in build-log.json events. Expected branch: '{expected}'. "
                    f"Either switch to '{expected}' OR document escape-hatch per "
                    f"skills/build-slice/SKILL.md Step 7c canonical shape."
                ),
            ))
    elif current.startswith("slice/"):
        if current != expected:
            result.violations.append(BranchViolation(
                kind="slice-branch-mismatch", severity="Important",
                message=(
                    f"current branch '{current}' does not match active slice's expected branch "
                    f"'{expected}'. Did you forget to switch back after a prior slice's `--merge`?"
                ),
            ))
    else:
        result.violations.append(BranchViolation(
            kind="slice-branch-mismatch", severity="Important",
            message=(
                f"current branch '{current}' is neither the default branch '{default}' "
                f"nor the active slice's expected branch '{expected}'. "
                f"Switch to '{expected}' or document escape-hatch."
            ),
        ))

    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        description="BRANCH-1 audit: branch-per-slice workflow validation (v2 JSON).")
    parser.add_argument("slice_folder", type=Path, help="Path to active slice folder.")
    parser.add_argument("--root", type=Path, default=None,
                        help="Code worktree root to inspect (default: cwd).")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        result = audit(slice_folder=args.slice_folder, repo_root=args.root)
    except Exception as e:  # noqa: BLE001
        print(f"branch_workflow_audit: error: {e}", file=sys.stderr)
        return 2

    important = [v for v in result.violations if v.severity == "Important"]
    warnings = [v for v in result.violations if v.severity == "Warning"]

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for v in important:
            print(f"[{v.severity}] {v.kind}: {v.message}")
        for v in warnings:
            print(f"[{v.severity}] {v.kind}: {v.message}")
        if not result.violations:
            print(f"Branch workflow audit: clean. On branch '{result.actual_branch}' "
                  f"(matches expected '{result.expected_branch}').")
        elif not important:
            print(f"Branch workflow audit: clean (with {len(warnings)} warning(s)). "
                  f"On branch '{result.actual_branch}'.")

    usage_kinds = {"usage-error", "default-branch-unresolvable"}
    if any(v.kind in usage_kinds for v in important):
        return 2
    if important:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
