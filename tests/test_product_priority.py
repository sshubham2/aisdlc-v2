"""product_priority.py — the path-class taxonomy + score-space term (slice-077 / SC-138 / ADR-088).

Covers AC2 (term-less -> unclassified), M-add-2 (off-path DOMINATES on-path precedence, locked),
and M4 (demote co-constraint XOR raises; a non-dict priority NEVER raises — SC-152/153 fail-safe).
Pure library — imported directly (no CLI).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import product_priority as pp


# ── builders ──────────────────────────────────────────────────────────────────────

def _plain(cid="SC-100", score=5):
    """A normal term-less candidate: no product-scope source, no demote fields."""
    return {"id": cid, "title": cid.lower(), "status": "candidate",
            "source": [{"type": "finding", "ref": "f-1"}],
            "priority": {"score": score, "severity": "medium", "effort": "M"}}


def _product(cid="SC-101", score=5):
    """An on-path (product-scope-sourced) candidate."""
    c = _plain(cid, score)
    c["source"] = [{"type": "product-scope", "ref": "PS-004"}]
    return c


def _demoted(cand, reason="good enough for now", ts="2026-07-18T00:00:00Z"):
    cand = dict(cand)
    cand["demoted_at"] = ts
    cand["demote_reason"] = reason
    return cand


# ── AC2: term-less legacy -> the MIDDLE (unclassified) tier ─────────────────────────

def test_termless_candidate_is_unclassified_not_offpath_not_onpath():
    """AC2: a candidate carrying no product-priority field resolves to `unclassified`,
    asserted directly — never off-path, never on-path."""
    pc = pp.path_class(_plain())
    assert pc == pp.UNCLASSIFIED
    assert pc != pp.OFF_PATH
    assert pc != pp.ON_PATH
    assert pp.product_term(pc) == 0


def test_product_term_map_values():
    """The term is centered on unclassified=0; on-path is term-inert; only off-path moves."""
    assert pp.product_term(pp.ON_PATH) == 0
    assert pp.product_term(pp.UNCLASSIFIED) == 0
    assert pp.product_term(pp.OFF_PATH) == -4


# ── on-path (product-sourced) ───────────────────────────────────────────────────────

def test_product_sourced_is_onpath_but_rank_inert():
    pc = pp.path_class(_product())
    assert pc == pp.ON_PATH
    assert pp.product_term(pc) == 0  # on-path is carried by SC-135's mint flat-5, not this term


# ── M-add-2: off-path DOMINATES on-path (locked precedence) ─────────────────────────

def test_demoted_product_candidate_resolves_offpath_not_onpath():
    """M-add-2: a candidate that is BOTH product-sourced AND demoted must resolve OFF-PATH
    (else the demote is silently inert). off-path is checked FIRST."""
    pc = pp.path_class(_demoted(_product()))
    assert pc == pp.OFF_PATH
    assert pp.product_term(pc) == -4


def test_demoted_plain_candidate_is_offpath():
    pc = pp.path_class(_demoted(_plain()))
    assert pc == pp.OFF_PATH
    assert pp.product_term(pc) == -4


# ── M4: the demote co-constraint (demoted_at XOR demote_reason) raises, named ────────

def test_coconstraint_violation_demoted_at_only_raises():
    bad = dict(_plain())
    bad["demoted_at"] = "2026-07-18T00:00:00Z"  # reason absent
    with pytest.raises(pp.DemoteCoConstraintError) as exc:
        pp.path_class(bad)
    assert exc.value.candidate_id == bad["id"]


def test_coconstraint_violation_reason_only_raises():
    bad = dict(_plain())
    bad["demote_reason"] = "orphaned reason"  # demoted_at absent
    with pytest.raises(pp.DemoteCoConstraintError):
        pp.path_class(bad)


def test_blank_sibling_is_treated_as_absent_not_a_violation():
    """A stray empty-string sibling is absent (truthiness-keyed), not a half-write."""
    c = dict(_plain())
    c["demoted_at"] = ""
    c["demote_reason"] = ""
    assert pp.path_class(c) == pp.UNCLASSIFIED


# ── M4: a NON-DICT priority never raises (SC-152/153 live fail-safe) ─────────────────

def test_nondict_priority_does_not_raise():
    """SC-152/SC-153 carry a non-dict priority today; path_class reads provenance + demote
    siblings, not priority, so it classifies cleanly and NEVER raises on those rows."""
    c = _plain()
    c["priority"] = ["SC-152", "SC-153"]  # the real non-dict shape
    assert pp.path_class(c) == pp.UNCLASSIFIED  # not demoted, not product-sourced


# ── build_demote_record: pure, fail-closed ─────────────────────────────────────────

def test_build_demote_record_fails_closed_on_empty_reason():
    with pytest.raises(ValueError):
        pp.build_demote_record("   ", "2026-07-18T00:00:00Z")


def test_build_demote_record_shape():
    rec = pp.build_demote_record("good enough for now", "2026-07-18T00:00:00Z")
    assert rec["demoted_at"] == "2026-07-18T00:00:00Z"
    assert rec["demote_reason"] == "good enough for now"
    ev = rec["history_event"]
    assert ev["event"] == "demoted" and ev["by"] == "slice-candidates"
    assert ev["ref"] == "good enough for now"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
