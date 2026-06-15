"""risk_status.py — the ONE canonical risk-register status vocabulary (slice-010 / ADR-008).

Single source of truth for the `risks[].status` enum, imported by EVERY enforcer so the
vocabulary lives in exactly one place and can never silently drift across copies again:

  - scripts/lib/artifact_lint.py            — KNOWN_ENUMS ("risk-register", "risks[].status")
  - scripts/lib/risk_register_audit.py      — _ALLOWED_STATUSES
  - skills/build-slice/scripts/state_transition_pin_audit.py — Sub-form B regex alternation

The PRODUCERS of a status (skills/risk-spike/SKILL.md Step 5 writes {retired, blocking,
conditional}; /adopt writes {accepted}; triage/discover/validate-slice seed {open}) write a
SUBSET of this set. tests/test_risk_status_reconciliation.py enforces producer ⊆ this set —
including extracting the producer alphabet from the risk-spike SKILL.md prose so even that
text source can't drift.

Per ADR-008 two lint-only values that NO producer ever wrote (and that appear in no stored
register) were dropped from the historical union: `mitigated` and `closed`. Their intent is
covered by the retained `mitigating` and `retired`; see ADR-008 for the authoritative
rationale. `mitigating` is retained as a valid manual lifecycle state even though no
automation writes it today.

This module is a LEAF — it imports nothing from the package — so importing it can never form
a cycle (risk_register_audit imports it; state_transition_pin_audit imports both).
"""
from __future__ import annotations

# The canonical, enforced set of allowed risk-register `status` values.
RISK_STATUSES: frozenset[str] = frozenset(
    {"open", "accepted", "mitigating", "retired", "blocking", "conditional"}
)
