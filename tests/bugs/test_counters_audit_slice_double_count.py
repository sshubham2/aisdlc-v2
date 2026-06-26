"""Repro for SC-045 (slice-041): id_allocation_audit slice-kind DOUBLE-COUNT false positive.

THE BUG this pins: `counters_violations()` assembles the `slice` kind by CONCATENATING four
sources without dedup — live candidate.slice, pick_log[].slice, the `slices/` folder glob, and
the `slices/archive/` folder glob. Every shipped slice legitimately appears in BOTH the pick_log
(`slice-NNN`) AND the archive folder (`slice-NNN-<name>`), so the SAME slice number lands in the
list twice and `nums.count(n) > 1` fires a phantom `slice: DUPLICATE` — the exact false positive
slice-019's duplicate detector was added to AVOID, which trains the reader to ignore it.

THE FIX must dedupe the *same* slice across sources WITHOUT blinding the detector to a *genuine*
collision (two genuinely-distinct slices that share a number).

AC1 test fails on HEAD (the false positive fires); AC2 test guards the real signal (must keep
firing after the fix).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]  # tests/bugs/ -> tests/ -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import id_allocation_audit  # noqa: E402


def _slice_dup_findings(vault: Path) -> list[str]:
    return [v for v in id_allocation_audit.counters_violations(vault)
            if v.startswith("slice: DUPLICATE")]


def test_slice_in_pick_log_and_archive_no_false_duplicate(tmp_path):
    """AC1: one slice present in BOTH pick_log AND slices/archive/ is ONE slice -> no DUPLICATE.

    Fails on HEAD: pick_log 'slice-005' (->5) + archive folder 'slice-005-foo' (->5) => count 2.
    """
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "candidates.json").write_text(json.dumps({
        "counters": {"slice": 5},
        "candidates": [],
        "pick_log": [{"candidate": "SC-100", "slice": "slice-005"}],
    }), encoding="utf-8")
    (vault / "slices" / "archive" / "slice-005-foo").mkdir(parents=True)

    assert _slice_dup_findings(vault) == [], (
        "the same slice appearing in pick_log and the archive folder must NOT be flagged as a "
        f"duplicate slice number; got {_slice_dup_findings(vault)!r}")


def test_two_distinct_slice_folders_same_number_still_flagged(tmp_path):
    """AC2 (regression guard): two GENUINELY distinct slice folders sharing a number STILL fires.

    Passes on HEAD; must keep passing after the fix (do not over-dedupe away the real signal).
    """
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "candidates.json").write_text(json.dumps({
        "counters": {"slice": 7},
        "candidates": [],
        "pick_log": [],
    }), encoding="utf-8")
    (vault / "slices" / "archive" / "slice-007-foo").mkdir(parents=True)
    (vault / "slices" / "archive" / "slice-007-bar").mkdir(parents=True)

    findings = _slice_dup_findings(vault)
    assert findings, "two distinct slice folders sharing number 7 must still fire slice: DUPLICATE"
    # m3: pin the exact DUPLICATE prefix up to the [N] payload -- the number is what consumers key
    # on (CLI, CI self-audit, SHIP-036) -- not just substring '7', so a reworded/reformatted leading
    # clause can't silently drift past. (m2: the trailing em-dash clause is descriptive prose, not
    # part of the matched contract, so it is deliberately NOT pinned here.)
    assert any(f.startswith("slice: DUPLICATE id number(s) [7]") for f in findings), findings


def test_in_flight_live_folder_and_candidate_slice_no_false_duplicate(tmp_path):
    """m2 (AC1): the parallel-slice NORMAL state -- a LIVE slices/<folder> plus a candidate.slice
    bare ref for the same number -- must NOT fire a false duplicate. This is the live-vault condition
    AC3 relies on (the live vault has it for slices 40/41/42); an implementer who deduped only
    pick_log<->archive would leave this phantom.
    """
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "candidates.json").write_text(json.dumps({
        "counters": {"slice": 41},
        "candidates": [{"id": "SC-099", "slice": "slice-041"}],
        "pick_log": [],
    }), encoding="utf-8")
    (vault / "slices" / "slice-041-foo").mkdir(parents=True)

    assert _slice_dup_findings(vault) == [], (
        "a live slice folder plus a candidate.slice bare ref for the same number is ONE slice in "
        f"flight, not a duplicate; got {_slice_dup_findings(vault)!r}")


def test_sc_kind_duplicate_still_fires_unchanged(tmp_path):
    """AC3 guard: the slice-kind fix must NOT touch the sc/adr/ship detectors. A genuine sc-number
    collision (two distinct candidates whose ids collapse to one number, e.g. SC-23 vs SC-023) must
    still fire sc: DUPLICATE -- the unchanged number-multiset path."""
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "candidates.json").write_text(json.dumps({
        "counters": {"sc": 23},
        "candidates": [{"id": "SC-23"}, {"id": "SC-023"}],
        "pick_log": [],
    }), encoding="utf-8")

    findings = id_allocation_audit.counters_violations(vault)
    assert any(f.startswith("sc: DUPLICATE id number(s) [23]") for f in findings), findings


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
