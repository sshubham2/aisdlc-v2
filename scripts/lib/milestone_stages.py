"""milestone_stages.py — the ONE canonical "terminal milestone stage" vocabulary (slice-037 / ADR-024).

Single source of truth for which milestone ``stage`` values mean a slice's loop is FINISHED,
imported by every resolver that classifies in-flight vs terminal so the definition lives in
exactly one place and can never silently drift across copies again:

  - scripts/lib/active_slice.py            — in-flight resolution (terminal slices are excluded)
  - scripts/lib/pulse_worktree_resolver.py — IN_PROGRESS vs MERGED / BUILT_BUT_NOT_MERGED
  - scripts/lib/stranded_slice_audit.py    — _is_terminal() divergence classification

Before this module the three carried private copies that had DRIFTED: active_slice used
``{complete}``; pulse/stranded used ``{reflect, complete}``. The /design-slice live-vault scan
settled the canonical value to the SUPERSET ``{reflect, complete}``: a real ARCHIVED, fully
completed slice (slices/archive/slice-015-verify-product-doc-grounding) records milestone stage
``reflect`` — the loop's terminal/done marker BEFORE the convention switched to writing
``complete`` directly. So genuinely-terminal slices exist under BOTH values; the superset
classifies them all correctly and CORRECTS active_slice's prior ``{complete}``-only
mis-classification of a legacy ``reflect`` done-slice as still-in-flight. See ADR-024.

READ-side vs WRITE-side (ADR-024 / slice-037 critique M2 — load-bearing):
  ``reflect`` is included here so OLD records READ correctly (a legacy done-slice classifies as
  terminal). It is deliberately NOT a *writable* stage: the authoritative legal-stage enum
  ``artifact_lint._MILESTONE_STAGES`` does NOT contain ``reflect`` (it lives in
  ``_MILESTONE_STEPS`` — a different axis), so nothing may NEWLY write stage ``reflect``. Do NOT
  "reconcile" that asymmetry by adding ``reflect`` to ``artifact_lint._MILESTONE_STAGES`` — that
  would bless an illegal write-stage and defeat this slice. tests/test_milestone_stages.py pins
  both halves of the rule.

This module is a LEAF — it imports nothing from the package — so importing it can never form a
cycle (active_slice/pulse/stranded import it; stranded already imports pulse). Consumers bind it
module-locally as ``_TERMINAL_STAGES`` to keep their membership checks byte-identical; never
rebind or augment it per call site (``_TERMINAL_STAGES |= {...}``) — that would re-fork the
single source this module exists to prevent.
"""
from __future__ import annotations

# The canonical, shared set of terminal (loop-finished) milestone ``stage`` values.
# Superset {reflect, complete}: 'reflect' is the legacy done-marker (READ-side only — see the
# module docstring + ADR-024), 'complete' is the current one. frozenset -> immutable, so it is
# safe to share by reference across every importer.
TERMINAL_STAGES: frozenset[str] = frozenset({"reflect", "complete"})
