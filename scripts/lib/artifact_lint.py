"""artifact_lint.py — enforce schema-by-example on vault JSON artifacts (3.18.7).

Driven DIRECTLY by schemas/artifact-examples.json: each artifact type's canonical
example defines (a) its `_schema` tag and (b) its required top-level keys — every
non-`_`-prefixed key whose value is NOT an optional-marker object (a dict carrying a
`_note`, the convention used for tournament / cross_domain_transfer). This lints an
artifact against that shape plus a small KNOWN-ENUMS table:
  - it MUST carry a `_schema` tag;
  - it MUST have every required top-level key the canonical example has;
  - known enum fields (mode / risk_tier / verdict / result / dispositions[].action …)
    must hold an allowed value.

Converts schema-by-example from decorative to ENFORCED — the `--self-check` mode lints
the canonical examples themselves, so a bad enum in artifact-examples.json (the 1.4
`action: fix-now` bug) fails CI before it ships.

Modes:
  --self-check         lint every canonical example in artifact-examples.json (CI).
  <file> [<file>...]   lint given vault artifact JSON files (type inferred from `_schema`,
                       or forced with --type <key>).
  --dir <d> --type <k> lint every *.json in <d> as artifact type <k>.

Exit: 0 clean · 1 violations · 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # scripts/lib/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib.risk_status import RISK_STATUSES

_EXAMPLES_PATH = _REPO / "schemas" / "artifact-examples.json"

# (artifact_key | "*", dotted-path) -> allowed values. Path supports `a.b` and the
# list-of-dicts hop `a[].b`. "*" applies to every artifact type. Kept deliberately
# small — the load-bearing enums (incl. the 1.4 triage `action` case).
_PIPELINE_MODE = frozenset({"minimal", "standard", "heavy"})
# slice-013 (ADR-009): shared enum value-sets reused across artifacts.
_SEVERITY = frozenset({"blocker", "major", "minor"})
_REVERSIBILITY = frozenset({"cheap", "expensive", "irreversible"})
_REALITY_CONTACT = frozenset({"high", "medium", "low"})
_MILESTONE_STAGES = frozenset(
    {"spike", "design", "critique", "critique-review", "build", "code-review", "validate", "complete"})
_MILESTONE_STEPS = frozenset(
    {"spike", "design", "critique", "critique-review", "build", "code-review", "validate", "reflect"})
KNOWN_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    # `mode` is the pipeline mode ONLY on these artifacts; changelog.mode (merge/push/
    # none) and user-test.mode (prototype/mockup/working-slice) are different fields.
    ("triage", "mode"): _PIPELINE_MODE,
    ("concept", "mode"): _PIPELINE_MODE,
    ("mission-brief", "mode"): _PIPELINE_MODE,
    ("slice-index", "mode"): _PIPELINE_MODE,
    ("mission-brief", "risk_tier"): frozenset({"low", "medium", "high"}),
    ("critique", "verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    ("critique", "triage.verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    ("critique", "triage.dispositions[].action"):
        frozenset({"accepted-fixed", "accepted-pending", "overridden", "deferred", "escalated"}),
    ("critique-review", "verdict"): frozenset({"accept", "adjust", "extend"}),
    ("validation", "result"): frozenset({"pass", "fail", "partial"}),
    ("validation", "criteria[].result"): frozenset({"pass", "fail", "partial"}),
    ("spike", "verdict"): frozenset({"go", "no-go", "conditional"}),
    # slice-004 (ADR-002): assumption-level spike fields. spike_status stays the BINARY
    # gate — `conditional` is deliberately NOT allowed here; the ternary verdict lives in
    # the sibling spike_verdict. Legacy rows lack both fields (absent/None passes).
    ("slice-candidates", "candidates[].assumptions[].spike_status"):
        frozenset({"unproven", "proving", "proven", "failed"}),
    ("slice-candidates", "candidates[].assumptions[].spike_verdict"):
        frozenset({"go", "no-go", "conditional"}),
    # a no-go assumption never passes through into a design's assumptions_proven
    ("design", "assumptions_proven[].verdict"): frozenset({"go", "conditional"}),
    # slice-013 (ADR-009): the dead ("code-review", "verdict") row was REMOVED — the
    # code-review artifact's outcome field is `result` (UPPERCASE {CLEAN, FINDINGS, BLOCKED},
    # off the lowercase convention -> ENUM_EXCLUSIONS, not enforced). enum_path_resolves()
    # guards against such dead rows.
    # Canonical risk-status set sourced from the ONE shared definition (slice-010 / ADR-008) —
    # NOT a hand-kept literal. Reconciles with risk_register_audit._ALLOWED_STATUSES (same import).
    ("risk-register", "risks[].status"): RISK_STATUSES,
    ("mission-brief", "architectural_layers[].status"): frozenset({"pending", "exercised"}),
    ("mission-brief", "exploratory_charters[].status"):
        frozenset({"pending", "in-progress", "completed", "deferred"}),
    # ── slice-013 (ADR-009): documented schema-by-example enums, each value-set verified
    # superset-safe against a live-vault scan of 100 typed artifacts. Documented-but-NOT-
    # enforced fields live in ENUM_EXCLUSIONS (with a category + rationale) below.
    ("build-log", "result"): frozenset({"shipped", "in-progress"}),
    ("build-log", "gates[].status"): frozenset({"pass", "fail", "n/a"}),
    ("concept", "constraints.stack[].reversibility"): _REVERSIBILITY,
    ("risk-register", "risks[].reversibility"): _REVERSIBILITY,
    ("adr", "reversibility"): _REVERSIBILITY,
    ("critique", "findings[].severity"): _SEVERITY,
    ("code-review", "findings[].severity"): _SEVERITY,
    # code-review's own triage action set (distinct from critique's 5-value disposition)
    ("code-review", "triage.dispositions[].action"): frozenset({"fixed", "overridden"}),
    # pinned to critique_review_audit.py:87 _ALLOWED_CLASSIFICATIONS
    ("critique-review", "assessments[].classification"):
        frozenset({"valid", "suspicious", "severity-wrong"}),
    ("drift-log", "entries[].category"):
        frozenset({"drift", "unspecified-code", "stale-claim", "stale-doc"}),
    ("design", "cross_domain_transfer.invariants[].status"):
        frozenset({"holds", "must-verify", "fails"}),
    ("design", "tournament.proposals[].selected"): frozenset({"core", "partial", "none"}),
    ("design", "tournament.approach_divergence[].divergence"):
        frozenset({"identical", "overlapping", "disjoint"}),
    ("design", "tournament.decidable_disagreements[].verdict"):
        frozenset({"pending", "go", "no-go"}),
    ("critic-calibration-log", "gate_skips[].action"):
        frozenset({"skip", "tier-gate-high-only"}),
    ("changelog", "mode"): frozenset({"merge", "push", "none"}),
    ("user-test", "mode"): frozenset({"prototype", "mockup", "working-slice"}),
    # slice-044: the OPTIONAL heuristic_walkthrough pre-flight sibling. kind classifies the
    # walkthrough finding; confidence is the model's self-rated strength (interaction-predicting
    # findings are force-set 'low' by ingest_heuristic_walkthrough -- A1.G3). Real-user findings[]
    # have no enforced kind enum; these live ONLY under the heuristic_walkthrough sibling.
    ("user-test", "heuristic_walkthrough.findings[].kind"):
        frozenset({"confusion", "dead-end", "ambiguous-instruction", "broken-flow"}),
    ("user-test", "heuristic_walkthrough.findings[].confidence"):
        frozenset({"low", "medium", "high"}),
    ("milestone", "stage"): _MILESTONE_STAGES,
    ("milestone", "progress[].step"): _MILESTONE_STEPS,
    ("validation", "reality_contact"): _REALITY_CONTACT,
    ("doc-manifest", "docs[].kind"):
        frozenset({"readme", "changelog", "api-reference", "user-guide"}),
    # slice-015: the grounding verifier's per-token drop reason. crg_reachable / graph_stale /
    # public_surface_verified are BOOLEAN (not enums) and deliberately NOT registered here.
    ("doc-manifest", "docs[].grounding_unverified[].reason"):
        frozenset({"source-unavailable", "symbol-absent", "ambiguous-match",
                   "malformed", "file-absent", "not-indexed"}),
    # slice-040 (M2): the public_surface verifier's per-entry drop reason. The public_surface_unverified
    # sibling annotates the FULL snapshot (M-add-1). Same 6-value set; registered so a bad value can't
    # ship silently (slice-013: an enum is only real where the linter enforces it). public_surface_verified
    # stays BOOLEAN (not registered), like crg_reachable / graph_stale.
    ("doc-manifest", "public_surface_unverified[].reason"):
        frozenset({"source-unavailable", "symbol-absent", "ambiguous-match",
                   "malformed", "file-absent", "not-indexed"}),
}

# Top-level keys that appear in a canonical example but are genuinely OPTIONAL on a
# real artifact (array-shaped optionals can't carry the dict-with-`_note` marker). Keyed
# by artifact type. e.g. the variant blocks a mission-brief carries only when opted in.
OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "mission-brief": frozenset({"architectural_layers", "exploratory_charters"}),
    # slice-001: the spike->design evidence cross-ref is array-shaped, so it NEEDS this
    # entry — without it every design.json that omits the block fails lint (critique B1).
    "design": frozenset({"assumptions_proven"}),
    # slice-004: structured constraints[] on a spike artifact (non-empty iff
    # verdict=conditional) — array-shaped optional; legacy spike files lack it.
    "spike": frozenset({"constraints"}),
    # slice-044: `preflight_used` is a bare bool (can't carry a `_note` marker) present
    # only when the heuristic pre-flight ran; a real-user-only session omits it. The
    # `heuristic_walkthrough` sibling is made optional by its own `_note` marker in the
    # example (so the dead-row guard still verifies its nested enum paths).
    "user-test": frozenset({"preflight_used"}),
    # slice-073: `revisions` is the append-only scope-change ledger, written by
    # `product_scope revise` ONLY when the item set actually changes. Array-shaped, so it
    # cannot carry the dict-with-`_note` optional marker — this entry is the only
    # mechanism. It MUST ship in the SAME change that adds revisions[] to the
    # `product-scope` canonical example (spike A1, constraint 2): the example edit alone
    # reds every live, revisions-less product-scope.json. An absent key is a legal,
    # PERMANENT state meaning "empty history", never a state to migrate away from.
    "product-scope": frozenset({"revisions"}),
}

# ── slice-013 (ADR-009): documented-enum coverage ════════════════════════════════════
# ENUM_EXCLUSIONS: documented/enum-shaped fields deliberately NOT enforced, each with a
# category + a written rationale so the gap is VISIBLE, never silent. Categories:
#   uppercase       — off the lowercase-enum convention (_conventions.md:23); a separate
#                     normalization slice owns these.
#   annotated       — the canonical token is a PREFIX; live writers append a ` - <note>`
#                     free-text suffix, so strict membership would false-reject.
#   owned-elsewhere — another in-flight slice / a derived field owns this surface.
#   open-set        — an extensible / off-convention set, not a closed enum.
ENUM_EXCLUSIONS: dict[tuple[str, str], dict[str, str]] = {
    ("code-review", "result"): {"category": "uppercase",
        "rationale": "UPPERCASE {CLEAN, FINDINGS, BLOCKED}, off the lowercase-enum convention; a lowercase-normalization slice owns this."},
    ("reflection", "critic_calibration[].verdict"): {"category": "uppercase",
        "rationale": "UPPERCASE {VALIDATED, NOT-YET}, off the lowercase-enum convention; normalize separately."},
    ("critique", "findings[].disposition"): {"category": "annotated",
        "rationale": "canonical token is a prefix; live writers append ` - <rationale>` (19 live records) so strict membership false-rejects. triage.dispositions[].action is the clean enforced sibling."},
    ("reflection", "discovered[].becomes"): {"category": "annotated",
        "rationale": "canonical token is a prefix; live writers append ` (<context>)` so strict membership false-rejects."},
    ("risk-register", "risks[].likelihood"): {"category": "owned-elsewhere",
        "rationale": "risk-register enum surface owned by the risk work (slice-010/SC-006); reconcile likelihood/impact there."},
    ("risk-register", "risks[].impact"): {"category": "owned-elsewhere",
        "rationale": "risk-register enum surface owned by the risk work (slice-010/SC-006); reconcile likelihood/impact there."},
    ("risk-register", "risks[].band"): {"category": "owned-elsewhere",
        "rationale": "band is DERIVED by risk_register_audit (computed, not hand-set); live uses 'moderate' vs the _note's 'med' — reconcile with the risk work."},
    ("slice-candidates", "candidates[].source[].type"): {"category": "open-set",
        "rationale": "extensible source taxonomy (risk, reality-surprise, exploratory-charter, user-directed, external-review, …); grows over time."},
    ("slice-candidates", "candidates[].status"): {"category": "open-set",
        "rationale": "lifecycle set spans /slice + claim_candidate + commit-slice + validate-slice; pin the full set in a focused pass to avoid false-rejecting a lifecycle value."},
    ("slice-candidates", "candidates[].progress"): {"category": "open-set",
        "rationale": "loop-stage set spans the pipeline writers; pin in a focused pass to avoid false-rejecting a stage value."},
    ("slice-candidates", "candidates[].priority.effort"): {"category": "open-set",
        "rationale": "single-letter sizing codes {S, M, L} are UPPERCASE, off the lowercase-enum convention."},
    ("changelog", "type"): {"category": "open-set",
        "rationale": "open conventional-commit type set (feat/fix/chore/docs/refactor/…); pin a canonical subset only if needed."},
    ("adr", "status"): {"category": "open-set",
        "rationale": "set not pinned beyond 'accepted'; supersession is tracked via superseded_by, not a status value."},
}

# DOCUMENTED_ENUMS: the curated SWEEP record — every documented enum field this slice is
# responsible for, mapped to WHERE it is documented. coverage_gaps() asserts each is
# enforced (KNOWN_ENUMS) or excluded (ENUM_EXCLUSIONS); a newly-documented enum added here
# that is neither fails the gate. (Pre-slice-013 enforced enums — triage.mode etc. — are
# documented + enforced by earlier slices and not re-listed; enum_path_resolves() still
# dead-row-guards ALL KNOWN_ENUMS. risk-register.risks[].status is enforced + owned by
# slice-010 and intentionally omitted from this sweep.)
DOCUMENTED_ENUMS: dict[tuple[str, str], str] = {
    # enforced (KNOWN_ENUMS) — where documented:
    ("build-log", "result"): "code-review/SKILL.md:25 (verify result: shipped); live scan",
    ("build-log", "gates[].status"): "build-log example gates[].status",
    ("concept", "constraints.stack[].reversibility"): "design-slice Step 4 + ADR _note (reversibility)",
    ("risk-register", "risks[].reversibility"): "design-slice Step 4 + ADR _note (reversibility)",
    ("adr", "reversibility"): "adr example + design-slice Step 4 reversibility",
    ("critique", "findings[].severity"): "critique/code-review severity convention",
    ("code-review", "findings[].severity"): "critique/code-review severity convention",
    ("code-review", "triage.dispositions[].action"): "code-review _note (fixed | overridden)",
    ("critique-review", "assessments[].classification"): "critique_review_audit.py:87 _ALLOWED_CLASSIFICATIONS",
    ("drift-log", "entries[].category"): "drift-log _note (drift | unspecified-code | stale-claim | stale-doc)",
    ("design", "cross_domain_transfer.invariants[].status"): "design cross_domain_transfer _note (holds | must-verify | fails)",
    ("design", "tournament.proposals[].selected"): "design tournament _note (selected: core|partial|none)",
    ("design", "tournament.approach_divergence[].divergence"): "design tournament _note (identical|overlapping|disjoint)",
    ("design", "tournament.decidable_disagreements[].verdict"): "design tournament _note (reality fills go/no-go)",
    ("critic-calibration-log", "gate_skips[].action"): "critic-calibration-log _note (action = skip | tier-gate-high-only)",
    ("changelog", "mode"): "artifact_lint KNOWN_ENUMS comment (changelog.mode merge/push/none)",
    ("user-test", "mode"): "artifact_lint KNOWN_ENUMS comment (user-test.mode prototype/mockup/working-slice)",
    ("user-test", "heuristic_walkthrough.findings[].kind"): "user-test example heuristic_walkthrough.findings[].kind (slice-044 pre-flight)",
    ("user-test", "heuristic_walkthrough.findings[].confidence"): "user-test example heuristic_walkthrough.findings[].confidence (slice-044 pre-flight)",
    ("milestone", "stage"): "schemas/_conventions.md L-1 stage state-machine",
    ("milestone", "progress[].step"): "milestone example progress[].step + _conventions L-1",
    ("validation", "reality_contact"): "gate-log _note (reality_contact high|medium|low)",
    ("doc-manifest", "docs[].kind"): "doc-manifest example docs[].kind",
    ("doc-manifest", "docs[].grounding_unverified[].reason"): "doc-manifest example docs[].grounding_unverified[].reason (slice-015 grounding verifier)",
    # excluded (ENUM_EXCLUSIONS) — documented but deliberately not enforced:
    ("code-review", "result"): "code-review _note (result CLEAN/FINDINGS) — uppercase",
    ("reflection", "critic_calibration[].verdict"): "reflect skill verdict vocabulary — uppercase",
    ("critique", "findings[].disposition"): "critique _note disposition enum — annotated suffix",
    ("reflection", "discovered[].becomes"): "reflection example discovered[].becomes — annotated suffix",
    ("risk-register", "risks[].likelihood"): "risk-register _note (low/med/high) — risk-work owned",
    ("risk-register", "risks[].impact"): "risk-register _note (low/med/high) — risk-work owned",
    ("risk-register", "risks[].band"): "risk-register _note band — derived/risk-work owned",
    ("slice-candidates", "candidates[].source[].type"): "slice-candidates.example.json — open set",
    ("slice-candidates", "candidates[].status"): "slice-candidates.example.json:5-6 — lifecycle set",
    ("slice-candidates", "candidates[].progress"): "slice-candidates.example.json:5-6 — loop-stage set",
    ("slice-candidates", "candidates[].priority.effort"): "slice-candidates effort S/M/L — uppercase",
    ("changelog", "type"): "changelog example type — open conventional-commit set",
    ("adr", "status"): "adr example status — not pinned beyond 'accepted'",
}


def _path_in_example(example: dict, path: str, optional: frozenset) -> bool:
    """True if `path` (dotted, with `[]` list hops) resolves to a present field on ANY
    element of the canonical `example`, OR its first segment is an OPTIONAL key
    (array-shaped optionals like architectural_layers are absent from the base example).
    A list hop descends into EVERY element — an enum field legitimately present on only
    some rows (e.g. assumptions[].spike_verdict, set only on a proven assumption) is NOT
    a dead row; a field present on NO element / nowhere in the shape IS."""
    parts = path.split(".")
    first = parts[0][:-2] if parts[0].endswith("[]") else parts[0]
    if first in optional:
        return True
    frontier = [example]
    for part in parts:
        is_list = part.endswith("[]")
        name = part[:-2] if is_list else part
        nxt: list = []
        for node in frontier:
            if isinstance(node, dict) and name in node:
                val = node[name]
                if is_list:
                    if isinstance(val, list):
                        nxt.extend(val)
                else:
                    nxt.append(val)
        if not nxt:
            return False
        frontier = nxt
    return True


def coverage_gaps() -> list[str]:
    """slice-013 (ADR-009) registry-completeness guard: every DOCUMENTED_ENUMS entry must
    be enforced (KNOWN_ENUMS) or explicitly excluded (ENUM_EXCLUSIONS), and no exclusion may
    exist outside the registry. The reliable replacement for the structurally-unsound _note
    prose-scanner (critique B2 / M-add-1)."""
    gaps: list[str] = []
    for key in sorted(DOCUMENTED_ENUMS):
        if key not in KNOWN_ENUMS and key not in ENUM_EXCLUSIONS:
            gaps.append(f"documented enum {key} is neither enforced (KNOWN_ENUMS) nor "
                        f"excluded (ENUM_EXCLUSIONS) — register-or-exclude (ADR-009)")
    for key in sorted(ENUM_EXCLUSIONS):
        if key not in DOCUMENTED_ENUMS:
            gaps.append(f"orphan exclusion {key}: in ENUM_EXCLUSIONS but not DOCUMENTED_ENUMS")
    return gaps


def enum_path_resolves() -> list[str]:
    """slice-013 (ADR-009) structural dead-row guard: every enforced/documented
    (artifact, path) must resolve to a real field in that artifact's canonical example
    (or an OPTIONAL_KEYS array field). Catches a rule pointing at a renamed/nonexistent
    field — e.g. the removed dead ("code-review", "verdict") row (the field is `result`)."""
    examples = _load_examples()
    bad: list[str] = []
    for (art, path) in sorted(set(KNOWN_ENUMS) | set(DOCUMENTED_ENUMS)):
        if art == "*":
            continue
        ex = examples.get(art)
        if ex is None:
            bad.append(f"enum row ({art}, {path}): unknown artifact type (no canonical example)")
        elif not _path_in_example(ex, path, OPTIONAL_KEYS.get(art, frozenset())):
            bad.append(f"dead enum row ({art}, {path}): field not in the {art} canonical example")
    return bad


def _load_examples() -> dict:
    with open(_EXAMPLES_PATH, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _required_keys(example: dict, key: str) -> list[str]:
    """Non-`_` top-level keys, excluding optional-marker objects (dict with `_note`) and
    keys listed as optional for this artifact type (OPTIONAL_KEYS — array optionals)."""
    optional = OPTIONAL_KEYS.get(key, frozenset())
    return [k for k, v in example.items()
            if not k.startswith("_")
            and k not in optional
            and not (isinstance(v, dict) and "_note" in v)]


def _walk(data, dotted: str) -> list:
    """Resolve a dotted path with `[]` list hops -> the list of leaf values present."""
    if not dotted:
        return [data]
    head, _, rest = dotted.partition(".")
    is_list = head.endswith("[]")
    head = head[:-2] if is_list else head
    if not isinstance(data, dict) or head not in data:
        return []
    nxt = data[head]
    if is_list:
        if not isinstance(nxt, list):
            return []
        out: list = []
        for item in nxt:
            out.extend(_walk(item, rest))
        return out
    return _walk(nxt, rest)


# slice-004 (ADR-002): per-row verdict<->constraints co-constraint. The flat _walk
# above cannot deliver this — it FLATTENS list hops into leaf values, discarding which
# row a value came from, so a record with one conditional-without-constraints row and
# one go-with-constraints row would hide BOTH problems from any count-based pairing.
# This check walks list ELEMENTS instead (row identity preserved).
# (artifact_key, list-parent path; "" = the top-level object) -> (verdict_field,
# constraints_field). Rules per element:
#   verdict == "conditional"  => constraints MUST be a non-empty LIST (type-checked:
#                                a malformed vault_edit --set can store a bare string);
#   any other verdict present => a non-empty constraints list is a STALE LEAK (writers
#                                re-set constraints=[] on non-conditional writes).
CO_CONSTRAINTS: dict[tuple[str, str], tuple[str, str]] = {
    ("slice-candidates", "candidates[].assumptions[]"): ("spike_verdict", "spike_constraints"),
    ("design", "assumptions_proven[]"): ("verdict", "constraints"),
    ("spike", ""): ("verdict", "constraints"),
}

# Review sweep 2026-07: per-row REQUIRED-NON-EMPTY fields. /validate-slice's evidence
# discipline ("command + output pasted; 'it worked' without evidence is not a PASS")
# was prose-only — this makes it mechanical: each row at the listed path must carry a
# non-empty string in the named field. (artifact_key, list path) -> field.
ROW_REQUIRED_NONEMPTY: dict[tuple[str, str], str] = {
    ("validation", "criteria[]"): "evidence",
}

# slice-072 (ADR-077): presence-triggered SYMMETRIC per-row check — distinct from the
# value-triggered CO_CONSTRAINTS above. For each row at the list path, if EITHER field
# carries a NON-EMPTY VALUE the OTHER must too (all-or-nothing sibling provenance).
# Keyed on VALUE truthiness, NEVER key-presence: a NORMAL candidate that OMITS both keys
# passes (both falsy), and a stray `field: null` / `""` is treated as absent (m1).
# slice-077 (M3): a (artifact_key, list path) maps to a LIST of field-pairs, so MULTIPLE
# co-constraints coexist under ONE key without clobbering each other (a single-tuple dict
# would let a second entry silently last-write-win over the first). candidates[] carries
# BOTH the residue pair (slice-072) AND the demote pair (slice-077 / ADR-088).
PRESENCE_SYMMETRIC: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("slice-candidates", "candidates[]"): [
        ("ejected_from", "ejection_reason"),   # slice-072 residue provenance (MUST keep firing)
        ("demoted_at", "demote_reason"),       # slice-077 demote provenance
    ],
}

# The kind of provenance a sibling-pair records, for a precise violation message.
_PRESENCE_KIND: dict[tuple[str, str], str] = {
    ("ejected_from", "ejection_reason"): "residue",
    ("demoted_at", "demote_reason"): "demote",
}


def _presence_symmetric_violations(data: dict, key: str, label: str) -> list[str]:
    v: list[str] = []
    for (ak, parent), pairs in PRESENCE_SYMMETRIC.items():
        if ak != key:
            continue
        loc = parent or "<top-level>"
        for (fa, fb) in pairs:
            kind = _PRESENCE_KIND.get((fa, fb), "sibling")
            for row in _walk_elements(data, parent):
                a = str(row.get(fa) or "").strip()
                b = str(row.get(fb) or "").strip()
                if bool(a) == bool(b):
                    continue  # both present (ok) or both absent (normal row, ok)
                present, missing = (fa, fb) if a else (fb, fa)
                rid = row.get("id") or "?"
                v.append(f"{label}: {loc} row {rid!r} has `{present}` set but `{missing}` "
                         f"empty/absent — {kind} provenance is presence-symmetric "
                         f"(`{fa}` truthy <=> `{fb}` non-empty)")
    return v


def _row_required_violations(data: dict, key: str, label: str) -> list[str]:
    v: list[str] = []
    for (ak, parent), fld in ROW_REQUIRED_NONEMPTY.items():
        if ak != key:
            continue
        for row in _walk_elements(data, parent):
            if not str(row.get(fld) or "").strip():
                rid = row.get("id") or "?"
                v.append(f"{label}: {parent} row {rid!r} must carry a non-empty `{fld}` "
                         f"— 'it worked' without evidence is not a PASS (the evidence "
                         f"discipline is mechanical, not prose)")
    return v


def _walk_elements(data, dotted: str) -> list:
    """Like _walk, but returns the list ELEMENTS (dicts) at an `a[].b[]`-style path —
    row identity preserved. "" resolves to the top-level object itself."""
    if not dotted:
        return [data] if isinstance(data, dict) else []
    head, _, rest = dotted.partition(".")
    is_list = head.endswith("[]")
    head = head[:-2] if is_list else head
    if not isinstance(data, dict) or head not in data:
        return []
    nxt = data[head]
    if not is_list:
        return _walk_elements(nxt, rest)
    if not isinstance(nxt, list):
        return []
    if not rest:
        return [item for item in nxt if isinstance(item, dict)]
    out: list = []
    for item in nxt:
        out.extend(_walk_elements(item, rest))
    return out


def _co_constraint_violations(data: dict, key: str, label: str) -> list[str]:
    v: list[str] = []
    for (ak, parent), (vf, cf) in CO_CONSTRAINTS.items():
        if ak != key:
            continue
        loc = parent or "<top-level>"
        for row in _walk_elements(data, parent):
            verdict = row.get(vf)
            cons = row.get(cf)
            if verdict == "conditional":
                if not isinstance(cons, list) or not cons:
                    v.append(f"{label}: {loc} row with {vf}='conditional' must carry a "
                             f"non-empty list `{cf}` (got {type(cons).__name__})")
            elif verdict is not None:
                if isinstance(cons, list) and cons:
                    v.append(f"{label}: {loc} row with {vf}={verdict!r} carries non-empty "
                             f"`{cf}` -- stale constraints must be cleared to []")
    return v


def lint_artifact(data: dict, key: str, example: dict, label: str) -> list[str]:
    """Return a list of violation strings ([] = clean)."""
    v: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: top level is not a JSON object"]
    if not data.get("_schema"):
        v.append(f"{label}: missing required `_schema` tag")
    for rk in _required_keys(example, key):
        if rk not in data:
            v.append(f"{label}: missing required key `{rk}` (per the {key} example)")
    for (ak, path), allowed in KNOWN_ENUMS.items():
        if ak not in ("*", key):
            continue
        for val in _walk(data, path):
            if val is not None and val not in allowed:
                v.append(f"{label}: `{path}` = {val!r} not in {sorted(allowed)}")
    v.extend(_co_constraint_violations(data, key, label))
    v.extend(_row_required_violations(data, key, label))
    v.extend(_presence_symmetric_violations(data, key, label))
    return v


def _type_for(data: dict, examples: dict, forced: str | None) -> str | None:
    if forced:
        return forced if forced in examples else None
    schema = (data.get("_schema") or "") if isinstance(data, dict) else ""
    # `_schema` is "aisdlc/<key>@N" -> <key>
    if schema.startswith("aisdlc/"):
        key = schema[len("aisdlc/"):].split("@")[0]
        if key in examples:
            return key
    return None


# ── 4.5 artifact version-skew detection (reader side) ────────────────────────────

def _plugin_version() -> str | None:
    """The running plugin's version from .claude-plugin/plugin.json (or None)."""
    try:
        with open(_REPO / ".claude-plugin" / "plugin.json", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _ver_tuple(v) -> tuple:
    """'2.22.4' -> (2, 22, 4); leading-digit-tolerant, missing parts -> 0."""
    out = []
    for part in str(v).split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _schema_major(schema) -> int | None:
    """The integer N from an `aisdlc/<key>@N` schema tag, or None."""
    if not isinstance(schema, str) or "@" not in schema:
        return None
    digits = ""
    for ch in schema.split("@", 1)[1].strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def schema_skew(data: dict, key: str, example: dict, plugin_ver: str | None) -> list[str]:
    """Non-fatal version-skew WARNings (4.5): the artifact's `_schema` major is NEWER than the
    one this plugin's canonical example defines (a vault written by a newer plugin), or its
    `_plugin_version` is newer than the running plugin. Older-than-current is the benign
    archived-artifact case and is NOT warned."""
    warns: list[str] = []
    if not isinstance(data, dict):
        return warns
    got = _schema_major(data.get("_schema"))
    known = _schema_major(example.get("_schema"))
    if got is not None and known is not None and got > known:
        warns.append(f"`_schema` is {key}@{got} but this plugin knows {key}@{known} — artifact "
                     f"written by a NEWER plugin; upgrade the plugin (vault/plugin skew).")
    stamped = data.get("_plugin_version")
    if stamped and plugin_ver and _ver_tuple(stamped) > _ver_tuple(plugin_ver):
        warns.append(f"`_plugin_version` {stamped} is newer than the running plugin {plugin_ver} "
                     f"— artifact written by a newer plugin (vault/plugin skew).")
    return warns


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="artifact_lint",
        description="Enforce schema-by-example (required keys + known enums) on vault JSON artifacts (3.18.7).")
    p.add_argument("files", nargs="*", type=Path, help="artifact JSON file(s) to lint")
    p.add_argument("--self-check", action="store_true",
                   help="lint every canonical example in artifact-examples.json (CI)")
    p.add_argument("--dir", type=Path, default=None, help="lint every *.json in this dir")
    p.add_argument("--type", default=None, help="force the artifact type (example key)")
    p.add_argument("--skip-unknown", action="store_true",
                   help="skip (don't fail) files whose artifact type can't be determined "
                        "— for a dir sweep that includes files this lint doesn't model")
    p.add_argument("--co-constraint-gate", action="store_true",
                   help="slice-072 (ADR-077): legacy-TOLERANT residue-provenance gate. Over the "
                        "given file(s) ONLY the presence-symmetric co-constraint "
                        "(ejected_from <=> ejection_reason) HARD-fails; every other finding "
                        "(required keys, enum drift like the legacy spike_status='pending' rows) "
                        "is downgraded to a non-fatal warning. Run this over candidates.json so a "
                        "reason-less eject is blocked without stranding the write on unrelated drift.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    examples = _load_examples()
    violations: list[str] = []
    warnings: list[str] = []          # 4.5 version-skew (non-fatal)
    plugin_ver = _plugin_version()
    checked = 0

    if args.self_check:
        for key, ex in examples.items():
            checked += 1
            violations.extend(lint_artifact(ex, key, ex, f"example:{key}"))
        # slice-013 (ADR-009): the documented-enum coverage + dead-row guards run in the
        # existing CI self-check (no separate flag) — this is their CI home.
        violations.extend(coverage_gaps())
        violations.extend(enum_path_resolves())
    else:
        targets = list(args.files)
        if args.dir is not None:
            targets.extend(sorted(args.dir.glob("*.json")))
        if not targets:
            sys.stderr.write("artifact_lint: pass file(s), --dir, or --self-check\n")
            return 2
        for f in targets:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                violations.append(f"{f}: unreadable / invalid JSON ({exc})")
                continue
            key = _type_for(data, examples, args.type)
            if key is None:
                if args.skip_unknown:
                    continue  # a dir sweep hits files this lint doesn't model — skip, don't fail
                violations.append(f"{f}: cannot determine artifact type "
                                  f"(no recognized `_schema`; pass --type)")
                continue
            checked += 1
            if args.co_constraint_gate:
                # slice-072 (ADR-077): only the residue-provenance co-constraint hard-fails;
                # everything else (required keys, enum drift) degrades to a warning so a
                # legitimate eject is never stranded on unrelated pre-existing candidates.json drift.
                all_v = lint_artifact(data, key, examples[key], str(f))
                hard = _presence_symmetric_violations(data, key, str(f))
                hard_set = set(hard)
                violations.extend(hard)
                warnings.extend(v for v in all_v if v not in hard_set)
            else:
                violations.extend(lint_artifact(data, key, examples[key], str(f)))
            for w in schema_skew(data, key, examples[key], plugin_ver):
                warnings.append(f"{f}: {w}")

    if args.json:
        print(json.dumps({"checked": checked, "violations": violations, "warnings": warnings}, indent=2))
    else:
        if violations:
            print(f"artifact_lint: {len(violations)} violation(s) over {checked} artifact(s):")
            for vi in violations:
                print(f"  - {vi}")
        elif not warnings:
            print(f"artifact_lint: clean. {checked} artifact(s) conform to schema-by-example.")
        if warnings:  # non-fatal: surfaced, but never fails the gate
            print(f"artifact_lint: {len(warnings)} version-skew warning(s) (non-fatal):")
            for w in warnings:
                print(f"  ! {w}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
