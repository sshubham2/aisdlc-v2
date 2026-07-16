"""slice-072 / SC-137 — AC3 + AC4 doc-guard: the reflect record-on-capture (:107/:110) and the
build-slice mint-split (:170) route their candidates.json writes through residue_disposition.

The wiring layer is SKILL.md prose + an interactive gate (not pytest-executable), so a region-
keyed doc-guard grep is the verification (repo norm: test_convergence_trigger_doc_sync.py /
test_git_gate_doc_guard.py). Each guard is NON-VACUOUS — it also pins the surrounding invariant
(the guaranteed captures, the mint-split prose, the unchanged deferrals path) so it fails if the
real wiring were removed rather than passing on an empty string.

TF-1: written FAILING before the reflect + build-slice SKILL.md edits.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFLECT = ROOT / "skills" / "reflect" / "SKILL.md"
BUILD = ROOT / "skills" / "build-slice" / "SKILL.md"
HELPER = "residue_disposition"


# ── AC3: reflect = record-on-capture ───────────────────────────────────────
def test_reflect_capture_routes_through_gate():
    text = REFLECT.read_text(encoding="utf-8")
    assert HELPER in text, \
        "reflect Step 2 Deferred/Discovered capture must route through residue_disposition (record-on-capture)"
    # non-vacuous: the GUARANTEED captures are still present (never regressed to a drop)
    assert "Discovered" in text and "Deferred" in text and "candidates.json" in text


def test_reflect_names_required_reason():
    text = REFLECT.read_text(encoding="utf-8").lower()
    assert "ejection_reason" in text, \
        "reflect must require a recorded ejection_reason on every capture (record-on-capture)"


def test_reflect_has_no_resolve_in_slice_branch():
    # ADR-077 / DR-1 M-add-1: NO resolve-in-slice option at reflect (the slice is archiving).
    text = REFLECT.read_text(encoding="utf-8").lower()
    assert "resolve-in-slice" not in text and "resolve in slice" not in text, \
        "reflect must NOT offer a resolve-in-slice branch (unactionable while archiving)"


# ── AC4: build-slice = the default-flip / mint-through-gate ─────────────────
def test_build_slice_mint_routes_through_gate():
    text = BUILD.read_text(encoding="utf-8")
    assert HELPER in text, \
        "build-slice mint-split (:170 'this is 2 slices: B as SC-NNN') must route through residue_disposition"
    # non-vacuous: the mint-split prose is still present
    assert "2 slices" in text and "SC-NNN" in text


def test_build_slice_deferrals_path_unchanged():
    # AC4: the build-log.json deferrals path (in-slice record, no candidate mint) stays as-is.
    text = BUILD.read_text(encoding="utf-8")
    assert "deferrals" in text and "build-log.json" in text
