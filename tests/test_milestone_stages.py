"""Tests for the shared terminal-milestone-stage definition (slice-037 / ADR-024).

``milestone_stages.TERMINAL_STAGES`` is the ONE source of truth for "which milestone
stage means a slice's loop is finished", imported by ``active_slice``,
``pulse_worktree_resolver`` and ``stranded_slice_audit``. These tests enforce:

  - AC1: the canonical value (the superset {reflect, complete}).
  - AC3: all three resolvers reference the IDENTICAL object (not just equal copies),
    so the divergence this slice removed cannot silently re-form.
  - M2: 'reflect' is terminal for READING old records but is NOT a legal stage to WRITE
    (artifact_lint._MILESTONE_STAGES omits it) -- pinned both ways, so a future
    maintainer can't "reconcile" the gap by blessing 'reflect' as a writable stage.
  - m3 / M-add-1 (Option B): a behavioral regression pin for EACH of the three resolvers
    over stage='reflect' (the legacy done-marker) vs a non-terminal stage -- active_slice's
    widening and the pulse/stranded classifiers had no / thin coverage before this slice.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.lib import (
    active_slice,
    artifact_lint,
    milestone_stages,
    pulse_worktree_resolver as pulse,
    stranded_slice_audit as stranded,
)
from scripts.lib.active_slice import resolve_active_slice
from scripts.lib.pulse_worktree_resolver import (
    WorktreeInfo,
    WorktreeState,
    classify_worktree_state,
)


# ------------------------------------------------------------------ AC1: value
def test_terminal_stages_value():
    # The canonical superset: legacy 'reflect' (read-side only) + current 'complete'.
    assert milestone_stages.TERMINAL_STAGES == frozenset({"reflect", "complete"})
    assert isinstance(milestone_stages.TERMINAL_STAGES, frozenset)  # immutable -> safe to share


# -------------------------------------------------- AC3: single shared object
def test_all_three_resolvers_share_one_object():
    # IDENTITY, not just equality: a future re-hardcode would create a DISTINCT object
    # that fails `is` even if `==` still passes (the "enforce, don't hope" discipline).
    shared = milestone_stages.TERMINAL_STAGES
    assert active_slice._TERMINAL_STAGES is shared
    assert pulse._TERMINAL_STAGES is shared
    assert stranded._TERMINAL_STAGES is shared


def test_members_are_lowercase():
    # stranded normalizes with .strip().lower() before membership; a non-lowercase member
    # would be unreachable there yet matched by the raw-membership resolvers (a silent re-fork).
    assert all(s == s.lower() for s in milestone_stages.TERMINAL_STAGES)


# ------------------------------------ M2: terminal-to-READ vs legal-to-WRITE
def test_reflect_is_readable_terminal_but_not_a_writable_stage():
    # 'reflect' classifies legacy finished work correctly (READ-side) ...
    assert "reflect" in milestone_stages.TERMINAL_STAGES
    # ... but is NOT a legal milestone stage to WRITE: the authoritative legal-stage enum
    # omits it ('reflect' is a milestone STEP, not a STAGE). Do NOT add it to _MILESTONE_STAGES.
    assert "reflect" not in artifact_lint._MILESTONE_STAGES
    # 'complete' is legal in both worlds.
    assert "complete" in milestone_stages.TERMINAL_STAGES
    assert "complete" in artifact_lint._MILESTONE_STAGES


# ----------------------- behavioral regression pins (m3 + M-add-1 Option B) --
def _make_slice(vault: Path, folder: str, *, stage: str, at: str) -> Path:
    d = vault / "slices" / folder
    d.mkdir(parents=True)
    (d / "milestone.json").write_text(json.dumps({"stage": stage, "at": at}), encoding="utf-8")
    return d


def test_active_slice_treats_reflect_as_terminal(tmp_path):
    # m3: active_slice WIDENED {complete} -> {reflect, complete}. A legacy stage='reflect'
    # slice is now TERMINAL, so it no longer counts as a second in-flight slice. Under the
    # OLD {complete}-only set this vault (reflect + build) would be two non-terminal slices
    # => AMBIGUOUS; under the superset only 'build' is in-flight and it resolves cleanly.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-101-legacy-done", stage="reflect", at="2026-01-01")
    _make_slice(vault, "slice-102-wip", stage="build", at="2026-01-02")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info is not None
    assert info["slice"] == "slice-102"


def test_stranded_is_terminal_pin():
    # stranded._is_terminal over both terminal markers + a non-terminal stage.
    assert stranded._is_terminal("reflect", None) is True
    assert stranded._is_terminal("complete", None) is True
    assert stranded._is_terminal("build", None) is False


def _milestone(d: Path, stage: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "milestone.json"
    p.write_text(json.dumps({"stage": stage}), encoding="utf-8")
    return p


def _worktree_info(milestone_path: Path) -> WorktreeInfo:
    return WorktreeInfo(
        path=str(milestone_path.parent),
        branch="slice/101-x",
        head_sha="0" * 40,  # set -> classify skips the git rev-parse
        slice_num="101",
        slice_name="x",
        milestone_path=milestone_path,
    )


def test_pulse_treats_reflect_as_terminal(tmp_path):
    # M-add-1 (Option B): pulse classify over stage='reflect' (terminal) vs 'build'
    # (non-terminal), exercising _TERMINAL_STAGES through the real classify path.
    # 'build' (non-terminal) -> IN_PROGRESS, returned before any git. 'reflect' (terminal)
    # proceeds PAST the in-progress gate to ancestry, which on a non-git repo_root yields
    # UNKNOWN(merge-base-error) -- i.e. NOT IN_PROGRESS, proving 'reflect' is treated terminal.
    build_cls = classify_worktree_state(
        _worktree_info(_milestone(tmp_path / "b", "build")), default_branch="master", repo_root=tmp_path
    )
    reflect_cls = classify_worktree_state(
        _worktree_info(_milestone(tmp_path / "r", "reflect")), default_branch="master", repo_root=tmp_path
    )
    # 'build' (non-terminal) -> IN_PROGRESS at the terminal gate (pulse line 286).
    assert build_cls.state is WorktreeState.IN_PROGRESS
    assert "pre-terminal" in build_cls.reason
    # 'reflect' (terminal) PASSES the terminal gate to ancestry, which on a non-git repo_root
    # yields UNKNOWN(merge-base-error). Pin the specific reason (m1 hardening) so the test proves
    # 'reflect' TRANSITED the terminal gate, not merely that it landed on any non-IN_PROGRESS state.
    assert reflect_cls.state is not WorktreeState.IN_PROGRESS
    assert reflect_cls.reason == "merge-base-error"
    assert reflect_cls.milestone_stage == "reflect"
