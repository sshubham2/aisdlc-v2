"""product_priority.py — the ONE home for the product-priority path-class taxonomy + its
score-space ranking term + the demote-record builder (slice-077 / SC-138 / [[ADR-088]]).

WHY ONE MODULE: the pick surface (`candidates_top`) and the demote write path
(`demote_candidate`) must agree on what a candidate's path-class IS and what it weighs, or
producer and gate drift. This is that single source of truth.

THE TAXONOMY (3-valued, DERIVED — never a new stored classifier):
  off-path       explicitly demoted (both demote sibling fields present) — checked FIRST
  on-path        product-scope-sourced (product_scope.owner_ref is not None)
  unclassified   the additive-identity default (term-less legacy + everything else)

Precedence is LOCKED off-path > on-path (M-add-2): a candidate that is BOTH product-sourced
AND demoted resolves off-path, so its demote actually lowers its rank (a recorded-but-inert
demote is forbidden). The demote eligibility guard in `demote_candidate` makes that overlap
unreachable in practice, but the precedence is proven here belt-and-braces.

THE TERM (SCORE SPACE ONLY — there is no order_key space, M1): centered on unclassified = 0
so a term-less corpus is provably unchanged (adding a constant to every element of a total
order is an order-isomorphism — AC2 + AC4 by construction). on-path is term-INERT (0): the
on-path lift is already carried by SC-135's flat mint-time constant (product_scope.py:198),
NOT re-added here (single-counting; m1 reconciliation of ADR-088 vs ADR-067). Only an
explicit off-path demote moves rank (−4, a bounded compensatory penalty — never a
lexicographic dominator, so a NON-demoted critical still tops the board).

ERROR CONTRACT (M4): `path_class` RAISES `DemoteCoConstraintError` (naming the id) ONLY on a
demote co-constraint violation — exactly one of `demoted_at` / `demote_reason` truthy (a
half-written demote). It does NOT read `priority`, so a non-dict priority (SC-152/SC-153 carry
one TODAY) never reaches it — the score-space caller (`candidates_top`) reuses its own
`_priority` isinstance guard and fail-SAFEs the TERM to 0 on a non-dict priority, never a
raise. Truthiness-keyed, mirroring artifact_lint's presence-symmetric residue check: a stray
empty-string sibling is treated as ABSENT, not a violation.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- shared-lib import bootstrap (scripts/lib/X.py -> repo root) ---
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import product_scope

# The 3-valued path-class (derived, never stored).
ON_PATH = "on-path"
OFF_PATH = "off-path"
UNCLASSIFIED = "unclassified"

# Score-space term, centered on unclassified = 0. on-path term-inert (SC-135 flat-5 carries
# the lift); only an explicit demote moves rank. W=4 clears the exhaust band (on-path MEDIUM
# 5 > demoted-HIGH 3) yet leaves a default critical (9) untouched — bounded, compensatory.
_OFF_PATH_PENALTY = -4
_TERM = {ON_PATH: 0, UNCLASSIFIED: 0, OFF_PATH: _OFF_PATH_PENALTY}


class DemoteCoConstraintError(ValueError):
    """A half-written demote (exactly one of demoted_at / demote_reason truthy). Carries the
    offending candidate id so the caller can fail VISIBLE (name the id + exit 1), never a
    silent mis-rank and never a raw traceback that blinds the /slice injection."""

    def __init__(self, candidate_id, message: str):
        self.candidate_id = candidate_id
        super().__init__(message)


def _demote_fields(cand: dict) -> tuple[str, str]:
    """The two demote siblings, whitespace-stripped. Truthiness-keyed: `null` / `""` == absent."""
    at = str(cand.get("demoted_at") or "").strip()
    reason = str(cand.get("demote_reason") or "").strip()
    return at, reason


def path_class(cand: dict) -> str:
    """Derive the path-class. off-path (demoted) FIRST, then on-path (product-sourced), else
    unclassified. Raises DemoteCoConstraintError ONLY on a half-written demote (M4)."""
    at, reason = _demote_fields(cand)
    if bool(at) != bool(reason):
        present, missing = ("demoted_at", "demote_reason") if at else ("demote_reason", "demoted_at")
        cid = cand.get("id") or "?"
        raise DemoteCoConstraintError(
            cand.get("id"),
            f"candidate {cid!r} has `{present}` set but `{missing}` empty/absent — a demote is "
            f"presence-symmetric (`demoted_at` truthy <=> `demote_reason` non-empty)")
    if at and reason:
        return OFF_PATH  # off-path DOMINATES on-path (M-add-2)
    if product_scope.owner_ref(cand) is not None:
        return ON_PATH
    return UNCLASSIFIED


def product_term(path_class_value: str) -> int:
    """The bounded score-space term for a path-class. Unknown class -> 0 (neutral, never a raise
    — an unmodelled class must not move rank silently)."""
    return _TERM.get(path_class_value, 0)


def build_demote_record(reason: str, ts: str) -> dict:
    """Pure, fail-CLOSED builder for the metadata a demote writes: the two presence-symmetric
    sibling fields (so the co-constraint holds by construction) + the append-only history event.
    An empty/whitespace reason or timestamp RAISES (never a silent, reason-less demote)."""
    r = str(reason or "").strip()
    if not r:
        raise ValueError("demote reason must be a non-empty string (fail-closed)")
    t = str(ts or "").strip()
    if not t:
        raise ValueError("demote timestamp must be a non-empty string (fail-closed)")
    return {
        "demoted_at": t,
        "demote_reason": r,
        "history_event": {"event": "demoted", "by": "slice-candidates", "at": t, "ref": r},
    }
