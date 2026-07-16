"""slice-072 / SC-137 — AC2 + m1 (APED-1): the artifact_lint presence-symmetric co-constraint.

`ejected_from` truthy <=> `ejection_reason` non-empty, keyed on a NON-EMPTY VALUE (truthiness),
NOT key-presence. NORMAL candidate rows OMIT both keys entirely (never null) and pass; a stray
`ejected_from: null` is treated as absent. The full execution-tested battery (m1) + `--self-check`
green on the updated canonical example (which now carries one ejected row).

TF-1: written FAILING before the artifact_lint co-constraint exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import artifact_lint  # noqa: E402

_EX = artifact_lint._load_examples()["slice-candidates"]
BASE = {"id": "SC-1", "title": "t", "status": "candidate", "progress": "not-started"}


def _lint(row):
    doc = {
        "_schema": "aisdlc/slice-candidates@1", "project": "x", "updated": "ts",
        "candidates": [row], "pick_log": [],
    }
    return artifact_lint.lint_artifact(doc, "slice-candidates", _EX, "t")


# ── m1 APED-1 battery ──────────────────────────────────────────────────────
def test_normal_row_omits_both_keys_passes():
    assert _lint(dict(BASE)) == []


def test_ejected_row_with_both_passes():
    assert _lint({**BASE, "ejected_from": "slice-072", "ejection_reason": "budget"}) == []


def test_ejected_from_with_empty_reason_fails():
    v = _lint({**BASE, "ejected_from": "slice-072", "ejection_reason": "   "})
    assert any("ejection_reason" in x for x in v), v


def test_reason_without_ejected_from_fails():
    v = _lint({**BASE, "ejection_reason": "orphan reason"})
    assert any("ejected_from" in x for x in v), v


def test_ejected_from_null_treated_as_absent_passes():
    assert _lint({**BASE, "ejected_from": None}) == []


def test_ejected_from_null_and_reason_null_passes():
    assert _lint({**BASE, "ejected_from": None, "ejection_reason": None}) == []


def test_empty_string_ejected_from_treated_as_absent_passes():
    assert _lint({**BASE, "ejected_from": "", "ejection_reason": ""}) == []


# ── AC2: --self-check green over the updated canonical example ──────────────
def test_self_check_green_over_canonical_examples():
    ex = artifact_lint._load_examples()
    viol = []
    for key, e in ex.items():
        viol.extend(artifact_lint.lint_artifact(e, key, e, f"example:{key}"))
    viol.extend(artifact_lint.coverage_gaps())
    viol.extend(artifact_lint.enum_path_resolves())
    assert viol == [], viol


def test_canonical_slice_candidates_example_carries_an_ejected_row():
    # AC2: the canonical example must exercise the ejected path (non-vacuous self-check).
    rows = _EX.get("candidates", [])
    ejected = [c for c in rows if str(c.get("ejected_from") or "").strip()]
    assert ejected, "the slice-candidates canonical example must carry >=1 ejected row"
    for c in ejected:
        assert str(c.get("ejection_reason") or "").strip(), "ejected example row needs a reason"
