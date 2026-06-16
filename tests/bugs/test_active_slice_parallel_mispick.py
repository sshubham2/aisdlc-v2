"""
Bug (SC-23): under PARALLEL slices, scripts/lib/active_slice.resolve_active_slice()
silently resolves to the WRONG slice when an orchestration skill is invoked from the
master tree.

Live event (slice-010 reflect -- "4 parallel slices broke active_slice resolution"):
  - >=2 slices are genuinely in flight, each in its own worktree on its own
    slice/NNN-* branch.
  - An orchestration skill (/build-slice, /code-review, /validate-slice, ...) is run
    from the MAIN session tree (master), where NO slice/NNN-* branch is checked out.

resolve_active_slice() is branch-first (active_slice.py:104-110); from master no slice
branch matches, so it falls through to the vault-scan fallback (active_slice.py:112-128),
which sorts the non-terminal slices by (milestone.at desc, then NNN desc) and returns the
single most-recent one -- a CONFIDENT pick it cannot actually justify. So a skill invoked
for slice-X silently operates on slice-Y (the live run hit slice-013/011 instead of
slice-010).

Reproduction faithfulness: a non-git ``repo_root`` makes ``_git_branch`` return None, so
branch-first resolution finds nothing and the vault-scan fallback runs -- the SAME code
path an on-master checkout takes (a real "master"/"main" branch likewise matches no
slice). This mirrors the existing suite's non-git ``repo_root`` convention
(tests/test_active_slice.py).

Expected: when >=2 slices are genuinely active and the call site cannot disambiguate
          (master tree, no slice branch, no explicit id), the resolver must NOT silently
          return one confident slice. It should refuse / return None / flag ambiguity.
          The MECHANISM is left to the fix slice (HALT, require an explicit slice id, or
          bind each run to a session/worktree context) -- this test only asserts
          "not a silent single mis-pick".
Actual:   it returns slice-101-beta (the most-recently-updated), as if certain.

NOTE for the fix slice: this DIRECTLY CONTRADICTS the existing
tests/test_active_slice.py::test_vault_scan_most_recent_among_non_terminal, which encodes
the most-recent heuristic as correct. That heuristic is only safe for a SINGLE
genuinely-active slice; under real parallelism it mis-picks. The fix must reconcile the
two (e.g. distinguish "one active slice -> benign fallback" from ">=2 active -> ambiguous").

This test PASSES when the resolver stops silently mis-picking under parallel ambiguity.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Self-contained import bootstrap (robust when the row is run standalone, not only
# under tests/conftest.py): add the plugin root so `from scripts.lib ...` resolves.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.active_slice import resolve_active_slice


def _make_slice(vault: Path, folder: str, *, stage: str, at: str) -> None:
    """Create a slice folder with a milestone.json (the only file the resolver reads)."""
    d = vault / "slices" / folder
    d.mkdir(parents=True)
    (d / "milestone.json").write_text(
        json.dumps({"stage": stage, "at": at}), encoding="utf-8"
    )


def _is_non_silent(result, raised: bool) -> bool:
    """A non-silent (acceptable) outcome under parallel ambiguity, mechanism-agnostic:
      - the resolver raised (refuse-by-exception), OR
      - it returned None (refused / no confident answer), OR
      - it returned a mapping that marks itself unresolved/ambiguous rather than
        confidently naming one slice.
    A confident single pick (a dict naming a concrete slice via the silent fallback) is
    the BUG and is NOT acceptable here."""
    if raised or result is None:
        return True
    if isinstance(result, dict):
        if result.get("ambiguous") is True:
            return True
        if result.get("slice") in (None, ""):
            return True
        if str(result.get("source") or "").lower() in {"ambiguous", "halt", "unresolved"}:
            return True
    return False


def test_parallel_active_slices_not_silently_mispicked_from_master(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    # Two slices genuinely in flight (non-terminal), older alpha + newer beta --
    # reproducing the live slice-010 event.
    _make_slice(vault, "slice-100-alpha", stage="build", at="2026-06-15T07:00:00Z")
    _make_slice(vault, "slice-101-beta", stage="design", at="2026-06-15T08:00:00Z")

    # Invoked from a NON-slice context (the master tree): a non-git repo_root makes
    # _git_branch() return None, so branch-first resolution finds nothing and the
    # vault-scan fallback runs -- exactly the master-tree path.
    master_root = tmp_path / "main_tree"
    master_root.mkdir()

    raised = False
    try:
        result = resolve_active_slice(vault, repo_root=master_root)
    except Exception:  # refuse-by-raising on ambiguity is a valid non-silent outcome
        raised = True
        result = None

    assert _is_non_silent(result, raised), (
        f"resolve_active_slice silently mis-picked {result!r} from a non-slice 'master' "
        "context with 2 genuinely-active slices; expected it to refuse / return None / "
        "flag ambiguity (mechanism-agnostic) rather than confidently returning the "
        "most-recent slice (slice-101-beta)."
    )
