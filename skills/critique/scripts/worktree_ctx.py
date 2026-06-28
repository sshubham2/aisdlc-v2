#!/usr/bin/env python3
"""slice-042 / ADR-029 (WT-CTX-1): resolve the active slice's WORKTREE for /critique's Step-2 prompt.

The forked `critique` subagent's file tools default to the MAIN repo root, so without an explicit pointer it
reads the main tree and false-flags the ADR-012-relocated repro test as 'missing' (slice-020 M1, the recurring
main-tree-vs-worktree vantage gap). This thin consumer REUSES
`scripts/lib/pulse_worktree_resolver.detect_active_worktrees` (it is NOT a 4th worktree resolver) and joins it
to the active slice by the resolver's OWN canonical full-folder string form -- NEVER a numeric slice_num compare
(M-add-2: the '042' vs '42' silent-regression guard; mirrors pulse_worktree_resolver._run_classify). It prints a
DATA block the orchestrator pastes into the Step-2 agent prompt: the resolved worktree path + the ADR-012
behavioral note + the repro-test listing (M-add-1), or 'Worktree: main tree' when the slice is not
worktree-backed (AC3 degrade; never raises).

NB (m2): /code-review's WT-ROOT-1 resolves the worktree via scripts/lib/_worktree_paths.py (a pure
convention-string computer with NO existence check, which would yield a path even when no worktree exists). We
DELIBERATELY diverge to --detect here because it lists ONLY git-registered worktrees, giving the 'main tree'
degrade for free, and /critique already calls --detect at SKILL.md:20. Do NOT 'harmonize' the two skills onto
_worktree_paths without restoring an existence check (it would defeat the AC3 degrade).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# skills/critique/scripts/worktree_ctx.py -> parents[3] = repo root (for `from scripts.lib import ...`)
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.lib import pulse_worktree_resolver  # noqa: E402
from scripts.lib import _stdout  # noqa: E402

NOTE = (
    "Repro tests (ADR-012) for THIS slice live under the WORKTREE shown above, NOT the main tree. Read/run "
    "them from there; do NOT flag a repro test 'missing' without checking <worktree>/tests/bugs/ first "
    "(this closes the slice-020 M1 main-tree-vantage false-flag)."
)


def _match(folder, worktrees):
    """Canonical join (M-add-2): the active slice's worktree is the one whose FULL FOLDER NAME
    f'slice-{slice_num}-{slice_name}' string-equals `folder`. NEVER a numeric/unpadded slice_num compare --
    a '42' vs '042' numeric compare would silently miss and degrade to 'main tree', recurring the false-flag
    while believed fixed (the resolver emits slice_num zero-padded; mirrors _run_classify)."""
    for w in worktrees:
        if f"slice-{w.slice_num}-{w.slice_name}" == folder:
            return w
    return None


def _folder(slice_dir):
    """The active slice's folder NAME from its path. m1 (code-review): `.strip()` FIRST to defuse the
    project's documented Windows trap -- a `SDIR=$(active_slice.py ... --path-only)` capture keeps a trailing
    `\\r` (active_slice.py prints `\\r\\n` on Windows; `$(...)` strips only the `\\n`), which would fail the
    canonical equality and SILENTLY degrade to 'main tree', recurring the slice-020 false-flag while believed
    fixed (the same silent-regression class M-add-2 guards, via CR contamination not zero-padding; memory
    worktree-add-crlf-path)."""
    return os.path.basename(os.path.normpath(str(slice_dir).strip()))


def resolve(slice_dir, repo_root="."):
    """Return {'worktree': <abs path str | None>, 'repro_tests': [<filename>, ...]}.

    NEVER raises (AC3): any failure (no slice_dir, --detect error, no match, junk repo) degrades to
    {'worktree': None, 'repro_tests': []} -> rendered as 'main tree'."""
    try:
        folder = _folder(slice_dir)
        worktrees = pulse_worktree_resolver.detect_active_worktrees(Path(repo_root))
        match = _match(folder, worktrees)
        if match is None:
            return {"worktree": None, "repro_tests": []}
        bugs = Path(match.path) / "tests" / "bugs"
        tests = sorted(p.name for p in bugs.glob("*.py")) if bugs.is_dir() else []
        return {"worktree": str(match.path), "repro_tests": tests}
    except Exception as e:  # AC3: never raise -- but m2 (code-review): make an UNEXPECTED breakage VISIBLE on
        # stderr (a future pulse_worktree_resolver API/field rename would otherwise degrade to 'main tree'
        # FOREVER and silently recur the false-flag). stdout (the pasted prompt block) stays clean.
        sys.stderr.write(f"worktree_ctx: degraded to 'main tree' on {type(e).__name__}: {e}\n")
        return {"worktree": None, "repro_tests": []}


def render(ctx):
    """Render the '# Active worktree (ADR-012)' DATA block the orchestrator pastes into the Step-2 prompt."""
    wt = ctx.get("worktree")
    if wt:
        lines = [f"Worktree: {wt}", NOTE]
        if ctx.get("repro_tests"):
            lines.append("Repro tests present in this worktree (tests/bugs/):")
            lines += [f"  {wt}/tests/bugs/{t}" for t in ctx["repro_tests"]]
        else:
            lines.append(f"  (no repro tests yet under {wt}/tests/bugs/)")
        return "\n".join(lines)
    return (
        "Worktree: main tree (no registered worktree for the active slice). Repro tests (ADR-012), if any, "
        "are under ./tests/bugs/ in the main tree."
    )


def main(argv=None):
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(description="Resolve the active slice worktree context for /critique Step-2.")
    ap.add_argument("--slice-dir", required=True, help="the active slice folder path (abs or basename)")
    ap.add_argument("--repo-root", default=".", help="repo root for git worktree detection")
    args = ap.parse_args(argv)
    print(render(resolve(args.slice_dir, args.repo_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
