#!/usr/bin/env python3
"""area_resolve.py — the SINGLE precedence site for a candidate's product AREA (slice-098 / SC-212;
[[ADR-124]] section 1 + [[ADR-125]] sections 1/7).

Until slice-098 an `area` existed only on a product-scope item (PS-NNN) and was joined to a candidate at
READ time through `owner_refs` (product_rollup.candidate_area). On the live vault 0 of 101 live candidates
carried an `owner_refs` at all, so the whole area axis described 4 shipped capabilities and was structurally
silent about every piece of queued work. This module is the read side of giving a candidate its OWN
optional `area`, and it exists as a SEPARATE module for one reason: spike-A1 constraint 1 -- "the capability
rollup MUST NEVER read the candidate-level area" -- is only mechanically checkable if the resolver and
product_rollup are separable (ADR-124 section 3). Co-located it degrades to a comment sitting on top of a
proximity hazard. CAND-AREA-1 (tests/test_cand_area_1.py) is the executable half of that separation: an AST
scan of product_rollup.py for a candidate-record read of the `area` key, with a mandatory negative control.

TWO AXES, ONE RESOLVER (the load-bearing distinction):
  * The CAPABILITY-progress rollup (product_rollup.compute_rollup) counts CAPABILITIES. It never calls into
    this module and never sees a candidate's own fields -- that is AC3, and it holds by CONSTRUCTION
    (accretion, not mutation: candidate_area is left byte-unchanged and used here as the derived arm).
  * The /slice PICK lens (candidates_top --area) answers "what should I pick next in area X" and DOES widen
    to candidate-asserted areas. Both of its paths route through THIS module, so no second precedence rule
    can appear (spike-A1 constraint 4).

PRECEDENCE -- ASSERTED BEATS DERIVED ([[ADR-124]] section 1, user-decided at the design fork; the tournament's
one disjoint split, designer-crossdomain having argued fallback-only). A candidate's own VALID `area` wins
over the value derived by joining through `owner_refs`, INCLUDING over the multi-parent ambiguity fallback.
A candidate with no own area keeps today's rule byte-for-byte: exactly one parent carrying an area resolves
to it; 0 parents or 2+ parents resolve to `unassigned`. Accepted cost, recorded in the ADR rather than fixed:
own-beats-derived can MASK a mis-parenting; it is mitigated by VISIBILITY (`source` is rendered at the pick
surface), never by refusal.

STRICT AT WRITE, TOTAL AT READ ([[ADR-124]] section 5). Every mediated write seam refuses an invalid area
(vault_edit's kind=='sc' guards, rc=2, file byte-identical), but `vault_edit rewrite` is a NAMED-OPEN leg
(SC-168, proven live 101 -> 100 rows at rc=0) and `/commit-slice` runs it on EVERY ship. So the compensating
control lives here: `own_area` judges a present value through the SAME product_scope._valid_area recognizer
the write seams use, inside a try/except, and a value that fails DEGRADES TO ABSENT. A rewrite-injected
invalid area therefore never surfaces in the lens and never crashes the /slice pick digest. The reject set is
IMPORTED, never re-implemented -- re-implementing it is precisely the SC-185 area-parity drift.

Nothing here writes, locks, or mints.
"""
from __future__ import annotations

import pathlib
import sys

# --- shared-lib import bootstrap (a bundled script cannot use `python -m`) ---
_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import product_rollup, product_scope

# Single-sourced from product_scope via product_rollup's own re-export, so the residual sentinel cannot
# drift between the write-seam validator (which REJECTS it) and the read-side catch-all bucket.
UNASSIGNED = product_rollup.UNASSIGNED

# `resolve()`'s second element. Kept as module constants so a caller renders/branches on a name rather
# than a bare string literal it could typo into a silently-never-true comparison.
SOURCE_CANDIDATE = "candidate"          # the candidate's own asserted area won
SOURCE_PRODUCT_SCOPE = "product-scope"  # derived through owner_refs from its single PS parent
SOURCE_RESIDUAL = "residual"            # no source at all -> the `unassigned` bucket


def own_area(cand) -> str | None:
    """The candidate's OWN asserted area as it will be MATCHED, or None when it has none.

    TOTAL: never raises, for any stored value including garbage a non-mediated path injected. A present
    value is routed through product_scope._valid_area -- the same recognizer the write seams use -- whose
    type-guard fires BEFORE `.strip()` (product_scope.py, the SC-185 / [[ADR-118]] fix), so a non-string
    (int / list / object) refuses cleanly instead of raising AttributeError. Anything the recognizer
    refuses (empty, whitespace-only, non-string, the reserved `unassigned` sentinel) DEGRADES TO ABSENT:
    the candidate falls back to its derived arm exactly as if it had never been annotated.

    Returns the STRIPPED value -- check == matched-value (slice-076), so a stored '  billing  ' matches
    `--area billing` and there is no check/match differential.
    """
    if not isinstance(cand, dict):
        return None
    raw = cand.get("area")
    if raw is None:
        return None
    try:
        return product_scope._valid_area(raw)
    except product_scope._Refuse:
        return None
    except Exception:                     # noqa: BLE001 -- totality is the contract; a read must never crash
        return None                       # the /slice pick digest (the named-open rewrite leg's control)


def has_area_source(cand) -> bool:
    """Does this candidate have a real, non-residual AREA SOURCE? -- the lens ADMISSION predicate.

    REPLACES [[ADR-091]] section 4's `owner_refs`-non-empty admission ([[ADR-125]] section 1). slice-084's A1
    source-scoping added that predicate deliberately: without it `--area unassigned` swept in the ~88
    pipeline-exhaust chores, "conflating the declared product capabilities with ~88 chores". Its RATIONALE
    survives here because `owner_refs`-non-empty was only ever a PROXY for "has a real area source", and the
    keystone is the reject set: `_valid_area` REFUSES the reserved `unassigned` sentinel at every write seam,
    so an ANNOTATED candidate can never resolve into the residual bucket the chore leak flowed through. An
    un-annotated chore has no source and stays out, exactly as A1 intended.
    """
    if own_area(cand) is not None:
        return True
    return bool(product_scope.owner_refs(cand))


def resolve(cand, area_map: dict[str, str]) -> tuple[str, str]:
    """(area, source) for ONE candidate -- the one and only precedence site.

    ASSERTED BEATS DERIVED: an own valid area wins over the owner_refs join, including over the 2+-parent
    ambiguity fallback ([[ADR-124]] section 1). With no own area the DERIVED arm is
    product_rollup.candidate_area, called UNCHANGED -- accretion, not mutation, so the capability path's one
    existing caller is unmoved by construction.

    `source` is SOURCE_CANDIDATE | SOURCE_PRODUCT_SCOPE | SOURCE_RESIDUAL. It is the VISIBILITY half of
    ADR-124 section 1's accepted masking cost: a candidate whose asserted area shadows its capability's is
    only ever distinguishable by this value, so the pick surface renders it (M6).
    """
    own = own_area(cand)
    if own is not None:
        return own, SOURCE_CANDIDATE
    derived = product_rollup.candidate_area(cand, area_map)
    if derived != UNASSIGNED:
        return derived, SOURCE_PRODUCT_SCOPE
    return UNASSIGNED, SOURCE_RESIDUAL


def asserted_areas(cands) -> set[str]:
    """Every distinct area candidates ASSERT for themselves -- the widening term for the lens's
    `known`/`areas` list ([[ADR-125]] section 1). Without it a freshly-annotated area reads `known: false`
    and the pick surface says "UNKNOWN area, 0 pickable" about an area that demonstrably exists.
    Malformed-tolerant by construction: own_area filters non-areas out."""
    out: set[str] = set()
    for c in cands or []:
        a = own_area(c)
        if a is not None:
            out.add(a)
    return out


def near_matches(name, known) -> list[str]:
    """Known areas that CASEFOLD-match `name` but are not byte-equal to it -- the split-bucket signal
    (critique m2). `_valid_area` normalizes only by `.strip()`, so 'Verification-Gates' and
    'verification-gates' are two DISTINCT buckets that both read `known: true`, silently splitting one
    area's picks in two. Advisory only: this REPORTS, it never refuses or rewrites (ADR-124 section 6's
    visibility-not-refusal stance). Deterministic order for a stable rendered line."""
    if not isinstance(name, str):
        return []
    fold = name.casefold()
    return sorted(k for k in (known or [])
                  if isinstance(k, str) and k != name and k.casefold() == fold)
