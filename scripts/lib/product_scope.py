"""product_scope — make the product's OWN scope appear in the candidate backlog (slice-068 / SC-135).

THE DEFECT (spike-proven, not asserted). `spike-product-priority-a1` classified every candidate ever
minted across two REAL vaults: aivlc (a real product, 11 shipped slices) — 14 candidates, 0 PRODUCT.
aisdlc-v2 (this pipeline, 68 shipped slices) — 131 candidates, 0 PRODUCT. **PRODUCT-sourced candidates
= 0. In both vaults. Across all 145.** The product's footprint in the backlog is not low-ranked, it is
ABSENT: `/discover` mints exactly ONE product candidate via concept.json's `first_slice_candidate`,
which fires once, at slice 1, and never again; everything after it is exhaust (risks, findings,
reality-surprises, reflection residues). aivlc's orchestrator/state-machine — its actual product —
was never minted as a candidate at all, so `/slice` structurally CANNOT pick it.

SC-135 was opened as "add a product-priority term to the ranking". The step-0 spike FALSIFIED that
premise: a ranking function is a total order over a SET — it cannot rank an ABSENT item. Materialization
is the 0->1; ranking is the 1->best (split out as SC-138). This module is the 0->1.

THE ARCHITECTURE IS FORCED BY REALITY, not chosen (spike B1, BINDING). Two INDEPENDENT, BLIND model
decompositions of aivlc's real concept.json agreed on only 2 of 9 scope keys (22%); 5 of 7
semantically-identical items drifted their key, INCLUDING THE ORCHESTRATOR ITSELF. A model-emitted key
therefore CANNOT be a cross-run dedup key — it would re-mint 7 of 9 items on every run (78% duplicate
sludge, which the must-not-defer calls strictly WORSE than the absence it fixes). So:

    DECOMPOSE ONCE, PERSIST, THEN MINT DETERMINISTICALLY.

  * `decompose-context` hands concept.json to the model (which is the only thing that can read prose).
  * `persist` crosses the model's decomposition into the vault EXACTLY ONCE, minting a PS-NNN per item
    from the in-lock id_allocator — the RECEIVER owns identity, never the producer. The model's own
    run-local labels are an intra-call correlation id and are discarded as an identity the moment the
    lock closes. Then it materializes, in the same act (ADR-067 §1).
  * `materialize` is a deterministic, idempotent, CREATE-ONLY pass keyed on candidate provenance
    (`source: [{type: "product-scope", ref: "PS-NNN"}]`) across live ∪ archive.
  * `revise` is the deliberate, user-gated scope correction (preserves minted ids BY id, never
    re-mints). It replaces the WHOLE item list, so it REFUSES a payload that silently omits a live
    item — that omission used to DELETE it at exit 0 with no trace, on the product's scope of record
    (slice-073 / SC-160). Removing an item is an explicit act: `--cut PS-NNN --reason '<why>'`,
    recorded in the append-only `revisions[]` ledger.
  * `census` re-runs the classification that DETECTED this defect, so it can never become invisible again.

AUTHORITATIVE DESIGN RECORD: **ADR-067** (SUPERSEDES ADR-066 — where they disagree, ADR-067 wins).
Load-bearing decisions it changed, all of which live in this file:

  1. Materialize IN THE ONCE-ACT; /slice stays READ-ONLY. A full-DAG mint (spike D1) mints every item on
     the first tick, so a level-triggered reconciler had exactly one non-trivial run in its life. It was
     VESTIGIAL, and it made AC3 unfalsifiable. Removing the /slice mutation DISSOLVES the whole
     read-your-own-writes / injection-ordering hazard rather than managing it.
  2. The identity guard lives HERE, in `persist`'s own in-lock `reject_supplied_id`. persist must
     REWRITE the model's depends_on labels into minted ids inside ONE lock, and `vault_edit append`
     mints internally and returns nothing to the caller, so persist bypasses vault_edit entirely.
     STILL TRUE. But ADR-067 went on to conclude that `product-scope.json`/`items` therefore needed
     NO `vault_edit._MANAGED_KIND` entry at all, and [[ADR-080]] (slice-073) SUPERSEDES that half:
     one _MANAGED_KIND entry drives FOUR legs, and the argument was only ever made about `append`.
     `remove` and `set --path` were each deleting a scope item at rc=0, with no record — around
     every guard in this module. The kind IS registered now; its `append` leg REFUSES outright
     (a raw append would mint a real id onto an item with no assumptions, which SKIPS /risk-spike
     step-0); persist's own guard is unchanged and is still the one that runs.
  3. `product-scope` SUPERSEDES the never-emitted `concept-scope` (schemas/slice-candidates.example.json).
     The census PRODUCT set accepts BOTH so a legacy value is never miscounted.
  4. Product candidates carry BLOCKING assumptions. `/risk-spike` step-0 SKIPS a candidate with zero
     unproven blocking assumptions — so `assumptions: []` would walk the least-understood work in the
     product straight past the pipeline's crown-jewel gate. A finding-derived candidate is a PROVEN bug
     with nothing to spike; a product capability is UNPROVEN by definition and has everything to spike.

TWO WRITES, NOT ONE TRANSACTION (stated, not glossed). product-scope.json and candidates.json have
SEPARATE SVW-1 locks and the vault has no cross-file transaction, so the once-act is ONE command
performing TWO sequential locked writes. A crash between them leaves the scope persisted and its
candidates unminted — which is recoverable by re-running `materialize`, and is exactly why `materialize`
survives as a standalone idempotent verb rather than being folded into `persist`.

MINTING IS NOT CHEAPLY REVERSIBLE (the cross-domain transfer's sharpest edge). A level-triggered k8s
reconciler is forgiving because a wrong action is corrected on the next tick; here a mint burns a
MONOTONIC SC id and there is no un-mint. So: every ambiguity biases toward NOT minting (a title
collision without the expected provenance is REFUSED, not resolved); `--dry-run` is a first-class mode
and IS the read-only replay mechanism; and a 0-mint ALWAYS states its reason — "never silently mints
nothing" applies to the SUCCESS path too, not only to the absent-concept path.

CLI (exit codes are distinct so a caller can tell "not set up yet" from "broken"; every path also emits
a sibling `status` field, so a consumer branches on structured state and never on the exit code alone):

    product_scope.py [--vault ROOT] decompose-context [--json]
    product_scope.py [--vault ROOT] persist --items-file PATH [--json]
    product_scope.py [--vault ROOT] materialize [--dry-run] [--scope-file PATH]
                                                [--acknowledge PS-NNN ...] [--json]
    product_scope.py [--vault ROOT] revise --items-file PATH [--cut PS-NNN ...]
                                           [--reason TEXT] [--json]
    product_scope.py [--vault ROOT] census [--json]
    product_scope.py [--vault ROOT] done [--item PS-NNN] [--json]

    0  ran (minted N >= 0; a 0-mint states its reason)
    1  runtime error (fail-visible)
    2  usage error (incl. a model-supplied id, an invented PS id, --scope-file without --dry-run,
       a `done --item` naming an id this scope does not carry)
    3  concept.json ABSENT           -> actionable message naming /discover            [decompose-context, persist]
    4  product-scope.json ABSENT     -> actionable message naming /slice-candidates --product   [materialize, revise, done]
       product-scope.json ALREADY EXISTS -> create-only refusal naming `revise`        [persist]

A CAPABILITY HAS MANY CANDIDATES (slice-075 / [[ADR-086]], which SUPERSEDES ADR-085). "How many slices
did this capability take" used to be inexpressible: `_plan`'s derived `ps_to_sc` map was `{ref: id}`, so
a second child silently overwrote the first and whichever won became the answer to every question about
that capability -- including a dependent's `dependencies[]`, frozen into an append-only record with no
un-mint. The map is now a MULTIMAP (`ps_to_scs: dict[str, list[str]]`) whose KEY SET is unchanged by
construction, so the create-only/dedupe contract is untouched; a dependent depends on ALL children of
each `depends_on` item; and `done` answers the resulting question, 4-valued and never cached.

NOT SPECULATIVE -- N>1 IS REACHABLE TODAY, on a shipping path, and this fixes a LIVE arbitrary-winner
bug. `sc` is a registered `vault_edit._MANAGED_KIND` and `_APPEND_REFUSED_KINDS` is `{"ps"}`
(vault_edit.py:197), so ONE `vault_edit append --file candidates.json --array candidates` carrying
`source: [{type: "product-scope", ref: "PS-001"}]` returns rc=0 and grows a capability's child set -- no
splitter required. Every artifact in this slice's design record (all three blind designers, both step-0
spikes, the design spike, ADR-085, and the first Critic) asserted the opposite; it was the one claim
nobody executed. Who is ALLOWED to create children is a separate question, filed as SC-177: this module
MODELS N>1 correctly, it does not police the producer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout, id_allocator
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text

SCOPE_FILE = "product-scope.json"
SCOPE_SCHEMA = "aisdlc/product-scope@1"
CANDIDATES_SCHEMA = "aisdlc/slice-candidates@1"

# ── the candidate source taxonomy (C10: an OPEN SET, so the classifier must be EXPLICIT) ─────────
#
# `candidates[].source[].type` is an artifact_lint open-set exemption (artifact_lint.py:185:
# "extensible source taxonomy ... grows over time"). The two real vaults carry 28 DISTINCT, almost
# entirely disjoint values. A census with a hardcoded map WILL meet an unknown one, and an unknown
# value silently absorbed into EXHAUST would reproduce the exact invisibility this module exists to
# kill — the "0" could return without anyone seeing it. So every bucket is a named constant, and
# anything unrecognized lands in an explicitly-emitted `unclassified` bucket that LISTS the raw values.
# That list is the measurement spine's own tripwire as the taxonomy grows.

#: The type this module emits. Written ONLY here, as a module-level constant — never by a model,
#: never by SKILL.md prose. It is THE idempotency key (see `owner_ref`).
PRODUCT_SOURCE_TYPE = "product-scope"

#: SUPERSEDED by PRODUCT_SOURCE_TYPE (ADR-067 §4). `concept-scope` was declared in
#: schemas/slice-candidates.example.json but emitted by ZERO code paths and carried by ZERO of the 146
#: candidates in either real vault. The census still ACCEPTS it, so a hand-written legacy candidate can
#: never be silently miscounted as non-product.
SUPERSEDED_SOURCE_TYPE = "concept-scope"

PRODUCT_SOURCES = frozenset({PRODUCT_SOURCE_TYPE, SUPERSEDED_SOURCE_TYPE})

#: A human typed this candidate in.
HUMAN_SOURCES = frozenset({"user-directed", "user-request", "user-idea", "analysis"})

#: EXHAUST — the pipeline generated this candidate by running. This is what 100% of both real backlogs
#: is made of, and why the product never gets built.
EXHAUST_SOURCES = frozenset({
    "risk", "reality-surprise", "reflection", "reflection-deferred", "reflection-discovered",
    "reflect", "reflect-deferred", "discovered", "deferred", "slice", "slice-descope", "split",
    "spun-off", "adr", "dogfood-incident", "exploratory-charter", "external-review",
    "code-review", "code-review-finding", "critique-finding", "design-review-finding",
    "critic-calibrate-finding", "verified-finding", "finding", "diagnose-finding", "bug-hunt-finding",
})

#: Per-slice EXHAUST variants the vault genuinely produces (e.g. the real aivlc row
#: `"source": "slice-007-discovered"`). A named pattern, not a literal, because the slice number varies
#: — but still explicit, and anything outside it still lands in `unclassified`.
_EXHAUST_PATTERNS = (re.compile(r"^slice-\d+-(discovered|deferred|descope)$"),)

# ── the mint-time priority block (C1, blocker) ────────────────────────────────────────────────────
#
# PRESENT != PICKABLE-IN-PRACTICE. /slice injects `candidates_top.py --top 5`, which sorts by
# -priority.score and reads an ABSENT score as 0.0 — so a candidate with no priority block is minted
# into the file and ranks DEAD LAST, invisible at the pick gate. That is this module's own bug, one
# level down. Measured on the real backlog when the design was reviewed: 63 pickable, the 5th-ranked
# scored 3, histogram {6:1, 5:2, 3:4, 2:33, 1:23} — an unscored product candidate ranked 64th of 64.
#
# WHY A MID-BAND CONSTANT AND NOT A HIGH ONE (spike-product-priority-a2, constraint 2): the product
# signal must be WEIGHTED, never a lexicographic dominator. A dominating score would bury a CRITICAL
# off-path auth-bypass bug BENEATH a LOW-value on-path CLI-help-text item. Score 5 clears both real
# backlogs' exhaust band (aivlc's live max is 4) while a genuinely critical bug scoring 8-10 still
# outranks it.
#
# ONE MECHANISM, DECLARED NOW (TRI-1's C1 caveat): this mint-time constant IS the product-priority
# signal. SC-138's rank-time term must key off `source.type == "product-scope"` and REPLACE this
# constant — it must NOT stack on top of it, or the two double-count into exactly the dominator
# spike-a2 forbids.
#
# RESIDUAL, STATED HONESTLY: with a flat constant, product items are ordered among themselves by
# candidates_top's tie-break (effort, then id) — and ids follow the TOPOLOGICAL order this module mints
# in, so the DAG roots (the critical path) surface first. For aivlc's real 9-item decomposition that
# puts the orchestrator inside the --top 5 window (AC5 asserts it). A guarantee for arbitrarily LARGE
# decompositions is a ranking property, and ranking is SC-138.
PRODUCT_PRIORITY = {"score": 5, "severity": "medium", "effort": "L"}


class _Refuse(Exception):
    """A fail-VISIBLE refusal carrying its exit code and sibling status."""

    def __init__(self, code: int, status: str, message: str):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


# ── the selector — ONE function, shared by the materializer, the census, and build_backlog ───────

def iter_sources(cand) -> list[dict]:
    """Normalize a candidate's `source` into a list of {type, ref} dicts. Malformed-TOLERANT.

    This is a REQUIREMENT, not defensiveness: the real aivlc vault carries `source` as a bare STRING on
    two rows where the schema says list-of-dicts (SC-014 "reflect" LIVE; SC-009 "slice-007-discovered"
    ARCHIVED), and both crash `s.get("type")`. A selector that dies on one malformed object never
    reconciles the rest.

    THE TRAP (critique C5): the real malformed shape is a SCALAR string, not a list containing one. The
    natural "tolerant" implementation — iterate `source`, coerce str elements — iterates the STRING'S
    CHARACTERS and yields SEVEN pseudo-sources ({'type':'r'}, {'type':'e'}, ...). It does not raise and
    it does not false-match, so BOTH properties the design spike measured are BLIND to it — while it
    silently poisons the census, which is the whole measurement spine. So a scalar string is NEVER
    iterated; it yields exactly ONE pseudo-source.

    Four shapes, explicitly:
      list-of-dicts   -> yielded as-is
      SCALAR string   -> ONE {"type": <the whole string>, "ref": None}
      list of strings -> one pseudo-source PER ELEMENT
      anything else   -> nothing
    """
    src = cand.get("source")
    if isinstance(src, str):
        s = src.strip()
        return [{"type": s, "ref": None}] if s else []
    if isinstance(src, dict):
        return [src]
    if not isinstance(src, list):
        return []
    out: list[dict] = []
    for s in src:
        if isinstance(s, dict):
            out.append(s)
        elif isinstance(s, str) and s.strip():
            out.append({"type": s.strip(), "ref": None})
    return out


def owner_ref(cand) -> str | None:
    """The PS id this candidate is the materialization of, else None — THE idempotency key.

    Deliberately keyed on PRODUCT_SOURCE_TYPE alone, not on PRODUCT_SOURCES: a legacy `concept-scope`
    row's ref is a prose anchor (`concept#payments`), not an allocator-minted PS id, so it can never be
    a materialization witness. The census counts it; the reconciler does not trust it.

    CARDINALITY (slice-075 / [[ADR-086]]): this is child -> parent, inherently N:1, and the step-0 spike
    proved it already correct at N>1 — it is UNCHANGED. It returns the FIRST ref, which is exactly right
    for a well-formed child (one parent) and is why `owner_refs` exists: a child claiming TWO parents is
    an AMBIGUOUS identity, and picking its first ref would be the silent winner-pick this module refuses
    everywhere else. The mint path reads `owner_refs` and REFUSES; this singular form stays the map's
    key-builder, over a set the refusal has already vetted.
    """
    for s in iter_sources(cand):
        if s.get("type") == PRODUCT_SOURCE_TYPE and s.get("ref"):
            return str(s["ref"])
    return None


def owner_refs(cand) -> list[str]:
    """EVERY distinct PS id this candidate claims, in source order (usually 0 or 1) — slice-075.

    The plural sibling of `owner_ref`, added so that a child claiming TWO parents is EXPRESSIBLE and can
    therefore be REFUSED (mission-brief must-not-defer #1: widening the relation must not widen the
    silence). `owner_ref` alone cannot see it — it returns the first ref and walks on.

    Reachable, not hypothetical: `sc` is a registered `vault_edit` managed kind and _APPEND_REFUSED_KINDS
    is `{"ps"}` (vault_edit.py:197), so ONE `vault_edit append` writes a model-supplied `source[]` of any
    shape at rc=0. Verified zero such rows exist today — this is a forward guard, not a retrofit.
    """
    out: list[str] = []
    for s in iter_sources(cand):
        if s.get("type") == PRODUCT_SOURCE_TYPE and s.get("ref"):
            ref = str(s["ref"])
            if ref not in out:
                out.append(ref)
    return out


def children_by_parent(observed: list[dict]) -> tuple[dict[str, list[str]], dict[str, list[dict]]]:
    """Derive (children, ambiguous) from the candidates' OWN provenance — the SSOT for the whole relation.

    Returns TWO maps, both PS-ref-keyed:
      children:  ref -> ALL its candidate ids, deduped by candidate id, in canonical numeric order.
      ambiguous: ref -> [{candidate, claims}] for every child that claims MORE THAN ONE parent.

    THE ONE place the capability -> candidates relation is derived, shared by the mint path (`_plan`) and
    the read path (`cmd_done`) so the two can never disagree about who a capability's children are, NOR
    about which are ambiguous (slice-051's producer/gate SSOT lesson). The mint path used to compute
    ambiguity itself while the read path did not compute it at ALL -- code-review CR1/CR2: `cmd_done`
    then filed a two-parent child under its FIRST parent only and reported the SECOND parent `done` with
    a live child still claiming it, and `_plan`'s withhold loop minted a dependent of an ambiguous parent
    with the ambiguous child frozen into its append-only `dependencies[]`. Both were the SAME hole: an
    ambiguity guard on one path is not a guard on the other. Deriving BOTH maps HERE closes it once.

    FILED UNDER EVERY CLAIMED PARENT (`owner_refs`, not `owner_ref`). A child claiming PS-001 AND PS-002
    is a child of both as far as the reverse lookup is concerned; filing it under the FIRST only is the
    silent winner-pick this module refuses everywhere else. For a well-formed child (exactly one parent)
    `owner_refs == [owner_ref]`, so this is byte-identical to the singular form for the entire real
    corpus -- the widening changes behaviour ONLY where an ambiguous child exists, which is exactly where
    the old form was silently wrong. Consumers must treat an `ambiguous` ref as unresolved (refuse / report
    `unknown`), never conclude over it.

    THE DEDUPE IS LOAD-BEARING (the half the design's own frame missed). The join is specified as a G-Set
    union -- idempotent by construction -- but `_observed` is a LIST CONCAT, and list concat is not
    idempotent: /commit-slice's live->archive move is two writes with no cross-file transaction, so a
    child caught mid-move is in BOTH files and would be counted TWICE. The pre-slice-075 scalar map was
    accidentally immune (a dict overwrite collapses the duplicate); the widening exposes it, so the
    dedupe (`cid not in kids`) ships WITH the widening. A doubled child fans out into a dependent's
    `dependencies[]`, frozen into an append-only record with no un-mint -- caught by executing the
    torn-read fixture (AC4), not by reasoning. Live-first order is preserved from `_observed` before
    sorting, so the dedupe keeps the LIVE row's identity when a child is mid-move.
    """
    children: dict[str, list[str]] = {}
    ambiguous: dict[str, list[dict]] = {}
    for c in observed:
        refs = owner_refs(c)
        cid = str(c.get("id"))
        for ref in refs:
            kids = children.setdefault(ref, [])
            if cid not in kids:                  # SET union, not list concat -- see the docstring
                kids.append(cid)
        if len(refs) > 1:                        # a child of two parents: ambiguous to BOTH
            for ref in refs:
                ambiguous.setdefault(ref, []).append({"candidate": cid, "claims": refs})
    for ref in children:
        children[ref].sort(key=_sc_sort_key)
    return children, ambiguous


def _sc_sort_key(sc_id) -> tuple[int, str]:
    """Canonical child order: NUMERIC SC suffix, then the raw id as a total-order tie-break.

    Numeric, not lexicographic: lexicographic puts SC-10 before SC-9, and
    `migrate-legacy-unpadded-ids-to-canonical-zero-pad` is a LIVE pending candidate, so the mixed-pad
    corpus is real. Reuses id_allocator.parse_num (the module that OWNS id shape) rather than a local
    regex — one place to change when the pad changes. An unparseable id sorts last but is never dropped:
    a malformed child that VANISHES from its parent's child set is exactly the silent loss `done` refuses
    to make, so it stays visible and merely orders last.
    """
    n = id_allocator.parse_num("sc", sc_id)
    return (n if n is not None else 10**9, str(sc_id))


def classify_source_type(t: str) -> str:
    """PRODUCT | HUMAN | EXHAUST | UNCLASSIFIED for ONE raw source.type value."""
    t = (t or "").strip()
    if t in PRODUCT_SOURCES:
        return "PRODUCT"
    if t in HUMAN_SOURCES:
        return "HUMAN"
    if t in EXHAUST_SOURCES or any(p.match(t) for p in _EXHAUST_PATTERNS):
        return "EXHAUST"
    return "UNCLASSIFIED"


def classify_candidate(cand) -> tuple[str, list[str]]:
    """(bucket, unknown_type_values). PRODUCT wins over HUMAN wins over EXHAUST."""
    types = [str(s.get("type") or "").strip() for s in iter_sources(cand)]
    types = [t for t in types if t]
    buckets = {classify_source_type(t) for t in types}
    unknown = [t for t in types if classify_source_type(t) == "UNCLASSIFIED"]
    for b in ("PRODUCT", "HUMAN", "EXHAUST"):
        if b in buckets:
            return b, unknown
    return "UNCLASSIFIED", unknown


# ── vault I/O ────────────────────────────────────────────────────────────────────────────────────

def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _load_json(p: Path) -> dict:
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _Refuse(1, "malformed", f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _Refuse(1, "malformed", f"{p} top-level is not a JSON object")
    return data


def _concept(vault: Path, *, required: bool) -> tuple[dict, str | None]:
    """(concept, sha256). Exit 3 with an ACTIONABLE message when required and absent (AC4)."""
    p = vault / "concept.json"
    if not p.exists():
        if not required:
            return {}, None
        raise _Refuse(
            3, "no-concept",
            f"concept.json is ABSENT at {p}. The product's scope cannot be decomposed from a concept "
            f"that does not exist. Run /discover to write it, then re-run /slice-candidates --product.",
        )
    return _load_json(p), hashlib.sha256(p.read_bytes()).hexdigest()


def _scope(vault: Path, *, required: bool) -> dict:
    """The persisted decomposition. Exit 4 naming the BOOTSTRAP act when required and absent.

    That message matters more than it looks: nothing in the pipeline used to invoke the decompose act
    at all, so product-scope.json would never have existed, materialize would have exit-4'd forever,
    and the backlog would have stayed 100% exhaust — this module's own bug, one level up (M-add-2).
    """
    p = vault / SCOPE_FILE
    if not p.exists():
        if not required:
            return {}
        raise _Refuse(
            4, "no-scope",
            f"{SCOPE_FILE} is ABSENT at {p} — the product's scope has never been decomposed, so there "
            f"is nothing to materialize. Run /slice-candidates --product (after /discover has written "
            f"concept.json) to decompose it once and mint its candidates.",
        )
    return _load_json(p)


def _observed(vault: Path, live: list[dict]) -> list[dict]:
    """Every candidate the reconciler must SEE: live UNION archive.

    THE ARCHIVE HALF IS LOAD-BEARING (spike D2, and the failure designer-crossdomain's k8s lens
    predicted): /commit-slice MOVES a shipped candidate out of candidates.json into
    archive/candidates.json. A reconciler that lists only the live file is a controller that lists only
    Running pods — it would faithfully RESURRECT every product item it has ever completed, forever.
    AC2 (run twice, nothing shipped between) cannot catch that; only AC3 can.
    """
    arch = _load_json(vault / "archive" / "candidates.json").get("candidates") or []
    return list(live) + [c for c in arch if isinstance(c, dict)]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the decomposition contract (the model -> vault trust boundary) ───────────────────────────────

def _label(item: dict) -> str:
    return str(item.get("decomposition_label") or item.get("label") or item.get("title") or "").strip()


def _load_items(path: str, *, allow_empty: bool = False) -> list[dict]:
    """The payload reader. `allow_empty` is REVISE-only (slice-073 / critique m2).

    An empty `items` array means two different things to the two verbs, and conflating them named
    the wrong cause: to `persist` it is a MALFORMED payload (there is nothing to decompose), but to
    `revise` it is a deliberate SHRINK-TO-ZERO attempt. Refusing the latter with "must carry a
    non-empty `items` array" describes the payload's shape rather than the act, which is AC4's own
    complaint one function over. So revise loads it and refuses it BY NAME (cmd_revise).
    """
    p = Path(path)
    if not p.is_file():
        raise _Refuse(2, "usage", f"--items-file not found: {p}")
    data = _load_json(p)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or (not items and not allow_empty):
        raise _Refuse(2, "usage", f"{p} must carry a non-empty `items` array (the decomposition).")
    for it in items:
        if not isinstance(it, dict):
            raise _Refuse(2, "usage", f"{p}: every item must be a JSON object; got {type(it).__name__}")
    _check_identities(items, p)
    return items


def _check_identities(items: list[dict], where) -> None:
    """No two items may share an id, and no two may share a label. Fail-VISIBLE (code-review CR1).

    persist is protected by reject_supplied_id (no item may carry an id at all), but `revise` MUST
    accept ids -- that is how an already-minted item is preserved across a revision -- and it only
    rejected an INVENTED id. A REPEATED one passed trivially: two items both carrying PS-001 collapsed
    onto ONE minted SC id in materialize's pass 1 (`minted_ids[it['id']] = next_id(...)` -- the second
    write wins), and pass 2 then stamped BOTH candidate records with that same id. Reproduced: scope
    ['PS-001','PS-001'] -> candidates ['SC-003','SC-003'], exit 0, "minted 2".

    CITATION NOTE (slice-075): the accumulator that line names was called `ps_to_sc` when this guard was
    written; the expression is re-cited above to match the code. This guard's SUBSTANCE is NOT touched by
    slice-075 and must not be "fixed": it is about two SCOPE ITEMS sharing one id (N:1 -- two capabilities
    claiming one identity), which is the OPPOSITE direction from the 1:N relation that slice made legal.
    One capability may now have many candidates; one identity may still never name two capabilities.

    This is the trust boundary's SECOND crossing, and BC-PROJ-6 is exactly about that: a guard on one
    write path is bypassable through another. Minting is irreversible here, so an ambiguous identity
    must STOP, never mint.

    Duplicate LABELS are rejected for the same reason one level down: label -> PS id is a mapping, so
    two items sharing a label would silently alias every depends_on edge pointing at either of them
    onto whichever was minted last -- a corrupted DAG rather than a duplicated id.
    """
    for key, human in (("id", "id"), (None, "label")):
        seen: dict[str, int] = {}
        for i, it in enumerate(items):
            val = str(it.get("id") or "").strip() if key else _label(it)
            if not val:
                continue
            if val in seen:
                raise _Refuse(
                    2, "usage",
                    f"{where}: items[{seen[val]}] and items[{i}] share the same {human} {val!r}. Each "
                    f"scope item is one product capability with ONE identity -- a repeated {human} "
                    f"would alias two items onto a single minted candidate (or a single dependency "
                    f"edge). Give each item a distinct {human}.",
                )
            seen[val] = i


def _check_membership(items: list[dict], cur: dict, cut, where) -> None:
    """The revise membership gate — BIDIRECTIONAL, and evaluated IN-LOCK against `cur` (slice-073).

    THE DEFECT THIS CLOSES (SC-160). `cmd_revise` was a whole-list REPLACE, not a delta: a payload
    that omitted an already-materialized PS id deleted that scope item and exited 0 GREEN. The
    computed `dropped` was thrown to stdout and nowhere else, so the deletion left ZERO durable
    trace on the PRODUCT'S SCOPE OF RECORD. A model handed "revise the scope" emits a delta, because
    that is what the word means.

    WHY IN-LOCK, AND WHY BOTH DIRECTIONS (critique-review M-add-1). The sibling invented-id guard
    tested the PRE-LOCK snapshot (`existing`), which was safe only while nothing could remove an
    item -- `existing` was then necessarily a SUBSET of `cur`. `--cut` FALSIFIES that premise: post-
    cut, `existing` is neither a subset nor a superset of `cur`. So a payload composed before a
    parallel writer's cut could RESURRECT the cut id -- and the spike-exemption, keyed on
    MATERIALIZED, would then wave the revived item through as already-spiked and re-adopt its
    shipped candidate. Both directions are therefore re-checked HERE, against the authoritative
    in-lock read. The pre-lock check survives only as a cheap early refusal, never as the only one
    (the in-file precedent is persist: create-only checked at :712, RE-CHECKED under the lock).

    Four properties, each fail-VISIBLE and each naming the offending id(s):
      1. every `--cut` id EXISTS in `cur`      -- a typo must STOP, not degrade into the very
                                                  omission it authorizes (BC-PROJ-6)
      2. every payload id EXISTS in `cur`      -- no invented id, no resurrected one
      3. no id is BOTH cut and kept            -- contradictory intent must STOP, never be resolved
      4. every id in `cur` is kept or cut      -- THE omission gate (SC-160)

    A raise propagates out of the caller's mutate closure and `safe_mutate_text` leaves the target
    UNTOUCHED -- no temp written, no replace (_vault_write.py:319-322). That is what makes the
    refusal byte-identical STRUCTURALLY, rather than by re-writing identical bytes.
    """
    cur_ids = {str(k) for k in cur}
    payload_ids = [str(it["id"]) for it in items if isinstance(it, dict) and it.get("id") is not None]
    cut_ids = [str(c).strip() for c in (cut or []) if str(c).strip()]

    unknown_cut = sorted({c for c in cut_ids if c not in cur_ids})
    if unknown_cut:
        raise _Refuse(
            2, "usage",
            f"{where}: --cut names {', '.join(unknown_cut)}, which this scope does not carry "
            f"(live ids: {', '.join(sorted(cur_ids)) or 'none'}). A cut must name a REAL item -- a "
            f"typo'd id would otherwise be accepted as authorization to drop whatever it was meant "
            f"to name, which is the very omission this gate exists to refuse. Check the id and "
            f"re-run.",
        )

    invented = sorted({i for i in payload_ids if i not in cur_ids})
    if invented:
        raise _Refuse(
            2, "usage",
            f"{where}: scope item(s) carry id(s) {', '.join(invented)}, which this scope does not "
            f"currently carry. `revise` may REUSE an allocator-minted PS id that is STILL IN THE "
            f"SCOPE (that is how an item is preserved across a revision) but may never INVENT one, "
            f"and may never RESURRECT one that was cut -- a cut id's candidate may already be "
            f"shipped, so re-adopting it would silently alias two capabilities onto one record. "
            f"Omit `id` to add a NEW item (identity is minted by the receiver).",
        )

    both = sorted({c for c in cut_ids if c in payload_ids})
    if both:
        raise _Refuse(
            2, "usage",
            f"{where}: {', '.join(both)} is both --cut AND re-stated as kept in the payload. That "
            f"is contradictory intent; refusing rather than silently picking a winner (an ambiguous "
            f"identity must STOP -- minting/retiring is not reversible here). Either drop the "
            f"--cut or remove the item from the payload.",
        )

    accounted = set(payload_ids) | set(cut_ids)
    missing = sorted(i for i in cur_ids if i not in accounted)
    if missing:
        raise _Refuse(
            2, "usage",
            f"{where}: this revision does not account for {', '.join(missing)}, which the product's "
            f"scope currently carries. `revise` replaces the WHOLE item list, so an omitted item "
            f"would be DELETED from the scope of record -- silently, and with no trace. Re-state "
            f"every item you mean to KEEP (carrying its minted `id` verbatim), or, to remove one "
            f"deliberately, re-run with `--cut {missing[0]} --reason '<why>'` (repeatable). Nothing "
            f"was written.",
        )


def _check_deps(items: list[dict], where) -> None:
    """Every depends_on reference must resolve to an item IN THIS BATCH. Fail-VISIBLE (code-review CR3).

    Both `_topo` and persist's/revise's depends_on rewrite used to FILTER unknown references out
    (`if d in known`). A typo'd or stale label was therefore silently DROPPED, and the item that
    depended on it was promoted to a false DAG ROOT. That is not cosmetic: PRODUCT_PRIORITY is a flat
    constant, so topological order is the ONLY intra-product ranking signal -- a false root jumps the
    queue and surfaces unready work at the pick gate, which is a quieter version of the very bug this
    module exists to fix.
    """
    known = {str(it["id"]).strip() for it in items if it.get("id")}
    known |= {_label(it) for it in items if _label(it)}
    for it in items:
        for d in it.get("depends_on") or []:
            if str(d).strip() not in known:
                raise _Refuse(
                    2, "usage",
                    f"{where}: scope item {(it.get('id') or _label(it))!r} declares depends_on "
                    f"{str(d)!r}, which is not an item in this decomposition. A dependency that does "
                    f"not resolve cannot be silently dropped -- it would make this item a false root of "
                    f"the DAG and surface it as ready work before its blocker exists. Reference another "
                    f"item's label (or, in `revise`, an already-minted PS id being kept), or remove it.",
                )


_CONTRACT_FIELD_MEANING = {
    "verification_plan": "how the capability will be CHECKED against reality",
    "user_visible_outcome": "what the user VISIBLY gets when it works",
}


def _nonempty_contract_str(value: object) -> bool:
    """Shape-level membership for a contract field: a non-empty string after `.strip()`.

    The `isinstance(str)` guard is deliberate (slice-076 / critique m2). A bare `str(value or '')`
    coercion would let a NON-string pass -- `str(['a','b'])` is a truthy ``"['a', 'b']"`` -- and the
    value would then persist as a list a downstream string op misreads. That is the parser
    differential the LangSec frame (ADR-087) exists to eliminate, so a non-string REFUSES here rather
    than coercing. `.strip()` refuses whitespace-only ('   '), the 'no real way to check itself' the
    slice rejects."""
    return isinstance(value, str) and bool(value.strip())


def _check_contract(
    items: list[dict],
    *,
    exempt_spiked: frozenset[str] = frozenset(),
    prev_by_id: dict[str, dict] | None = None,
) -> None:
    """The model->vault decomposition contract. TWO-PHASE (slice-073 / ADR-079 §1).

    Every scope item must carry a title, a label, and >=1 BLOCKING assumption (ADR-067 §5). The
    requirement is enforced HERE, at the crossing — not downstream — because /risk-spike step-0 SKIPS
    a candidate with zero unproven blocking assumptions. Without it, aivlc's orchestrator/state-machine
    (the largest, least-understood item in that product, and the very thing this module exists to
    surface) would enter the loop with NOTHING TO PROVE.

    THE DEFECT THIS FIXES (SC-161): the `unproven` demand was made of EVERY item on EVERY crossing,
    and `revise` re-runs the contract over the FULL list — so a list containing even ONE item whose
    spike had already run was rejected WHOLESALE. The scope froze the moment any item was proven,
    which is precisely when a revision becomes most necessary. The requirement was correct at persist
    time and was simply never re-thought for revise, where the population NECESSARILY includes spiked
    items.

    So the demand splits:

      Phase 1 — >=1 BLOCKING assumption, ANY spike_status. Demanded of EVERY item on BOTH verbs,
                ALWAYS. This is what stops a revise from ERASING assumptions[] to slip an item past
                step-0: it cannot be waived by having been spiked, because the assumption itself must
                still be there.
      Phase 2 — that a blocking assumption is still `unproven`. Demanded of NEW items; WAIVED only
                for ids in `exempt_spiked`.

    `exempt_spiked` is supplied BY THE CALLER and this function performs NO lookup — persist (:721)
    passes nothing and therefore keeps the strict pre-slice-073 behaviour EXACTLY, which is the
    must-not-defer: relaxing revise must not relax persist. That is belt-and-braces even if the
    parameter were ever mispassed, because persist's own `reject_supplied_id` bans ids outright, so
    every persist item is new by construction and could never match an exempt id anyway.

    AC4 — THE REFUSAL NAMES THE ACTUAL CAUSE. The old message claimed ``assumptions: []`` against a
    NON-empty list, which is maximally misleading at exactly the boundary the message exists to
    explain. Three distinct causes, named distinctly: genuinely absent / present-but-none-blocking /
    present-but-all-proven-and-not-exempt.

    slice-076 / ADR-087 — TWO CONTRACT-COMPLETENESS FIELDS. `verification_plan` and
    `user_visible_outcome` are the fields that give a capability MEANING; both are demanded here in
    the Phase-1 band (never waived by `exempt_spiked` -- a meaning-giving field is orthogonal to
    spike status). The check is SHAPE-LEVEL only: a non-empty string, NOT a runnable/testable plan
    (a placeholder like 'TODO' still passes) -- the message says so and disclaims a testability
    guarantee (AC4 / A2). On REVISE the value validated is the EFFECTIVE one the persist merge
    (:1328/:1331) will actually WRITE -- `it.get(field) or prev.get(field)`, via the caller-supplied
    `prev_by_id` map (mirroring `exempt_spiked`: persist passes none -> strict, effective == item;
    revise passes the in-lock `cur`). So the check and the write agree on the same value, and neither
    a whitespace-only nor a non-string field can slip past the check to be persisted.
    """
    for it in items:
        if not str(it.get("title") or "").strip():
            raise _Refuse(2, "usage", f"scope item {_label(it) or '<unnamed>'} has no `title`.")
        if not _label(it):
            raise _Refuse(2, "usage", f"scope item {it.get('title')!r} has no `label` to key depends_on on.")

        iid = str(it.get("id") or "").strip()
        named = f"{_label(it)!r}" + (f" ({iid})" if iid else "")
        assumptions = [a for a in it.get("assumptions") or [] if isinstance(a, dict)]
        blocking = [a for a in assumptions if a.get("blocking")]

        # Phase 1 — a BLOCKING assumption must EXIST. Never waived, on either verb.
        if not blocking:
            if not assumptions:
                cause = ("carries `assumptions: []`")            # the genuinely-empty case
            else:
                cause = (f"carries {len(assumptions)} assumption(s), but NONE is marked "
                         f"`blocking: true`")
            raise _Refuse(
                2, "usage",
                f"scope item {named} {cause}, so it has no BLOCKING assumption. A product capability "
                f"is UNPROVEN by definition -- a candidate with nothing blocking SKIPS /risk-spike "
                f"step-0, the pipeline's reality gate, on exactly the least-understood work in the "
                f"product (ADR-067 section 5). Add a blocking assumption and re-run.",
            )

        # Phase 1 (slice-076 / ADR-087) — the two contract-completeness fields must be PRESENT and
        # non-empty, ALWAYS, on both verbs. Validated on the EFFECTIVE value (item OR prev) so the
        # check agrees with the persist merge at :1328/:1331; on persist `prev_by_id` is None, so
        # `prev` is {} and the effective value is the item's own field (strict).
        prev = (prev_by_id or {}).get(iid) or {}
        for field, meaning in _CONTRACT_FIELD_MEANING.items():
            effective = it.get(field) or prev.get(field)   # mirrors the bare `or` of :1328/:1331
            if not _nonempty_contract_str(effective):
                raise _Refuse(
                    2, "usage",
                    f"scope item {named} has no non-empty `{field}` -- the field that states "
                    f"{meaning}. A capability that declares neither how it is checked nor what the "
                    f"user gets is untestable prose, so the crossing refuses it. NOTE: this is a "
                    f"SHAPE-LEVEL check -- it requires a non-empty string, NOT a runnable or testable "
                    f"plan; presence is necessary, not sufficient (a placeholder like 'TODO' still "
                    f"passes). Supply a non-empty `{field}` and re-run.",
                )

        # Phase 2 — it must still be UNPROVEN, unless this item has already been through step-0.
        if iid and iid in exempt_spiked:
            continue
        if not any((a.get("spike_status") or "unproven") == "unproven" for a in blocking):
            raise _Refuse(
                2, "usage",
                f"scope item {named} carries {len(blocking)} blocking assumption(s), but every one "
                f"is already `spike_status: proven` -- and this item is not an already-materialized "
                f"one whose spike could have run (it has no candidate). A NEW product capability is "
                f"UNPROVEN by definition; declaring it proven at the crossing would walk it past "
                f"/risk-spike step-0 with nothing to prove (ADR-067 section 5). If the work really "
                f"is understood, say so with a blocking assumption step-0 can CHECK; if this item "
                f"was already spiked, revise it (its exemption keys on being materialized, not on "
                f"carrying an id).",
            )


def _normalize_assumptions(item: dict) -> list[dict]:
    out = []
    for i, a in enumerate(item.get("assumptions") or [], 1):
        if not isinstance(a, dict):
            continue
        out.append({
            "id": str(a.get("id") or f"A{i}"),
            "statement": str(a.get("statement") or "").strip(),
            "risk_ref": a.get("risk_ref"),
            "blocking": bool(a.get("blocking", True)),
            "spike_status": str(a.get("spike_status") or "unproven"),
            "spike_ref": a.get("spike_ref"),
            "spike_evidence": a.get("spike_evidence"),
            "fallback": a.get("fallback"),
        })
    return out


def _topo(items: list[dict], dep_key) -> list[dict]:
    """Kahn's, stable in input order. Roots first — so the ids this module mints follow the product's
    CRITICAL PATH, which is the free gift B1 handed us (both blind decompositions emitted a depends_on
    DAG unprompted). A cycle is fail-visible, never silently linearized."""
    keys = [dep_key(it) for it in items]
    known = set(keys)
    deps = {k: [d for d in (it.get("depends_on") or []) if d in known]
            for k, it in zip(keys, items)}
    by_key = dict(zip(keys, items))
    out, done = [], set()
    while len(out) < len(items):
        ready = [k for k in keys if k not in done and all(d in done for d in deps[k])]
        if not ready:
            cyc = sorted(k for k in keys if k not in done)
            raise _Refuse(2, "usage", f"the decomposition's depends_on graph has a CYCLE among: {cyc}")
        for k in ready:
            out.append(by_key[k])
            done.add(k)
    return out


# ── materialize — the deterministic, idempotent, create-only mint ────────────────────────────────

def _require_contract_field(item: dict, field: str) -> str:
    """Read a contract field the single recognizer (`_check_contract`) has already guaranteed
    non-empty. Fail-VISIBLE belt-and-braces (slice-076 / ADR-087): NOT a second recognizer -- it
    converts a would-be SILENT bug (a `None` `user_visible_outcome`, or the `KeyError`/placeholder
    the deleted repair used to mask an empty `verification_plan`) into a labelled `_Refuse` naming
    the item, should an unrecognized item ever reach materialize."""
    value = item.get(field)
    if not _nonempty_contract_str(value):
        raise _Refuse(
            2, "usage",
            f"materialize reached scope item {(item.get('id') or _label(item))!r} with an empty or "
            f"non-string `{field}` -- the contract recognizer (_check_contract) should have refused "
            f"it upstream. Refusing to mint a candidate from an unrecognized item.",
        )
    return value


def _candidate_from(item: dict, sc_id: str, dep_sc: list[str], ts: str) -> dict:
    """The FULL candidate record (M-add-4), mirroring build_backlog._candidate_from field-for-field."""
    blocking = [a for a in item.get("assumptions") or [] if a.get("blocking")]
    return {
        "id": sc_id,
        "title": item["title"],
        "status": "candidate",
        "progress": "not-started",
        "slice": None,
        "claimed_by": None,
        "started_at": None,
        "source": [{"type": PRODUCT_SOURCE_TYPE, "ref": item["id"]}],
        "description": item.get("description") or item["title"],
        "rationale": (
            f"The PRODUCT's own scope ({item['id']}), decomposed once from concept.json and "
            f"materialized so /slice can pick it at all. {len(blocking)} blocking assumption(s) "
            f"unproven -- /risk-spike step-0 gates this candidate."
        ),
        "user_visible_outcome": _require_contract_field(item, "user_visible_outcome"),
        "dependencies": dep_sc,
        "priority": {
            "score": PRODUCT_PRIORITY["score"],
            "severity": PRODUCT_PRIORITY["severity"],
            "effort": PRODUCT_PRIORITY["effort"],
            # A product item has no CODE blast radius yet (nothing is built). Keyed on the item so it is
            # unique per candidate: a shared literal would make candidates_top report all N product
            # items as coupling with each other -- true but useless noise at the pick gate.
            "blast_radius": f"product-scope: {_label(item)}",
        },
        "assumptions": _normalize_assumptions(item),
        # slice-076 / ADR-087: the placeholder-substitution repair is DELETED. An empty plan is
        # refused at the recognizer (`_check_contract`), never masked here into a synthesised string
        # while scope.json stays empty (the parser differential the LangSec frame exists to close).
        "verification_plan": _require_contract_field(item, "verification_plan"),
        "history": [{"event": "created", "by": "slice-candidates", "at": ts, "ref": item["id"]}],
    }


def _plan(items: list[dict], observed: list[dict], acknowledge: set[str]) -> dict:
    """Decide, WITHOUT writing, what materialize would do. Computed inside the caller's lock.

    Bias EVERY ambiguity toward NOT minting: a mint burns a monotonic SC id and there is no un-mint, so
    the self-correcting safety net a level-triggered reconciler normally relies on is ABSENT here.

    Returns `ps_to_scs: dict[str, list[str]]` -- the capability -> ALL-its-children multimap, derived
    from the children's own `source[].ref` over live u archive (slice-075 / [[ADR-086]]). It is
    READ-ONLY to the caller: pass 1's newly-minted ids accumulate in a SEPARATE scalar-valued `minted`
    map, and keeping those two apart BY TYPE is what makes both a list-valued candidate id and a
    type-heterogeneous map unrepresentable rather than merely guarded. Do not merge them back.
    """
    # THE DERIVED MULTIMAP (slice-075 / [[ADR-086]] §2). `dict[ref] = id` became
    # `setdefault(ref, []).append(id)` over the SAME iteration under the SAME `owner_ref` guard, so key
    # insertion happens on exactly the same iterations -- the KEY SET is identical to the old scalar
    # map's BY CONSTRUCTION, which is what preserves the create-only test at :684 (KEY membership) and
    # with it spike constraint A1.1: no re-mint, against a substrate with no un-mint.
    #
    # It is READ-ONLY once returned. Pass 1's minted ids accumulate in a SEPARATE `minted: dict[str,str]`
    # ([[ADR-086]] §3) -- conflating the two is what hid :819/:824 through an entire design + review.
    # The capability -> candidates relation AND its ambiguities come from ONE shared derivation, so the
    # mint path and the read path (`cmd_done`) can never disagree (code-review CR1/CR2/CR3). N children
    # per capability is LEGAL; a child claiming N parents is an ambiguous identity that STOPS -- widening
    # the relation must not widen the silence (must-not-defer #1).
    ps_to_scs, ambiguous = children_by_parent(observed)

    # candidates carrying a title but NO product-scope provenance -- the D2 collision surface
    unowned_titles: dict[str, str] = {}
    for c in observed:
        if owner_ref(c) is None:
            t = str(c.get("title") or "").strip()
            if t:
                unowned_titles.setdefault(t, str(c.get("id")))

    already, refused, to_mint = [], [], []
    for it in items:
        iid = it.get("id") or _label(it)
        # BEFORE the create-only test, deliberately: an ambiguously-parented item's own map entry is the
        # thing in doubt, so reporting it `already` would launder the ambiguity as a clean no-op. Refuse
        # the ITEM, not the VERB (the module's D2 pattern, :688-703) -- an offending row in the
        # append-only archive would otherwise block EVERY capability's materialize forever.
        if it.get("id") and it["id"] in ambiguous:
            rows = ambiguous[it["id"]]
            refused.append({
                "item": it["id"],
                "title": str(it.get("title") or "").strip(),
                "colliding_candidate": rows[0]["candidate"],
                "ambiguous_children": rows,
                "reason": (
                    f"candidate(s) {', '.join(r['candidate'] for r in rows)} each claim MORE THAN ONE "
                    f"product-scope parent ({'; '.join(', '.join(r['claims']) for r in rows)}). A "
                    f"capability may have many candidates, but a candidate belongs to exactly ONE "
                    f"capability -- with two parents there is no fact of the matter about whose child "
                    f"it is, so this item's child set, its dependents' dependencies[], and its `done` "
                    f"are all undefined. Refusing to mint (a mint is not reversible). REMEDY: correct "
                    f"the child's provenance to a single parent -- `vault_edit.py update --file "
                    f"candidates.json --array candidates --id {rows[0]['candidate']} --set "
                    f"'source=[{{\"type\": \"product-scope\", \"ref\": \"<the ONE real parent>\"}}]'` "
                    f"(use --file archive/candidates.json if the row has shipped) -- then re-run. There "
                    f"is deliberately no --acknowledge for this: acknowledging would mint UNDER the "
                    f"ambiguity, and every ambiguity here biases toward NOT minting."
                ),
            })
            continue
        if it.get("id") and it["id"] in ps_to_scs:
            kids = ps_to_scs[it["id"]]
            # AC5: at N==1 this entry is BYTE-IDENTICAL to the pre-slice-075 shape. At N>1 the scalar
            # `candidate` is OMITTED rather than filled with a winner -- at N>1 "the candidate" has no
            # referent, and emitting one would be the arbitrary pick this slice exists to kill. A
            # consumer reading ['candidate'] then KeyErrors LOUDLY instead of silently believing a
            # coin-flip. (Verified: `already_materialized` has ONE producer (_finish) and ZERO consumers
            # repo-wide -- no test, script, or SKILL.md reads it.)
            already.append({"item": it["id"], "candidate": kids[0]} if len(kids) == 1
                           else {"item": it["id"], "candidates": list(kids)})
            continue
        title = str(it.get("title") or "").strip()
        if title in unowned_titles and iid not in acknowledge:
            # D2's fail-LOUD guard. Provenance empirically SURVIVES the model-mediated ship->archive
            # move -- retained on EVERY archived candidate measured to date (76/76 as of slice-075) --
            # but that is a SNAPSHOT, not an invariant (slice-050's lesson), and the archive copy stays
            # model-mediated and unenforced. The claim is stated as its SHAPE ("every one measured so
            # far") rather than as a bare figure precisely so it cannot rot: the number moves with the
            # corpus, the point does not. (The figure read "79/79" until slice-075 counted: it was 76.)
            # A LOST provenance fails SILENTLY -- it resurrects a shipped item forever. So convert the
            # silent resurrection into a loud stop. Keying on the TITLE is safe here precisely because
            # the title now comes from the PERSISTED scope list, not from the model (which B1 proved
            # drifts 78% run-to-run).
            refused.append({
                "item": iid, "title": title, "colliding_candidate": unowned_titles[title],
                "reason": (f"a candidate ({unowned_titles[title]}) already carries this scope item's "
                           f"title but does NOT carry its product-scope provenance. Minting would "
                           f"either duplicate it or resurrect a shipped item. If they are genuinely "
                           f"different, re-run with --acknowledge {iid}."),
            })
            continue
        to_mint.append(it)

    # C14: a refused item's dependents are withheld TRANSITIVELY. A partially-minted DAG whose
    # `dependencies` entry maps to no SC id is worse than a deferred sub-tree: candidates_top._unmet_deps
    # tests membership in LIVE ids, so a dangling dep is SILENTLY DROPPED -- the quieter failure.
    blocked_root = {r["item"]: r["item"] for r in refused}
    withheld: list[dict] = []
    changed = True
    while changed:
        changed = False
        for it in list(to_mint):
            key = _label(it)
            iid = it.get("id") or key
            for d in it.get("depends_on") or []:
                # a dep is satisfiable iff it is already materialized OR is being minted in this batch --
                # UNLESS the parent is AMBIGUOUS (code-review CR2). An ambiguous parent is REFUSED and its
                # child set is undefined (the refusal reason literally says so), so a materialized-but-
                # ambiguous `d` must NOT count as satisfied: fan-out would otherwise freeze the ambiguous
                # child into this dependent's append-only `dependencies[]`. Fall through to withhold it,
                # rooted at the ambiguous parent -- exactly the C14 transitive-withhold this loop exists for.
                if d in ps_to_scs and d not in ambiguous:   # KEY membership -- semantics unchanged at N>1
                    continue
                if any((m.get("id") or _label(m)) == d or _label(m) == d for m in to_mint):
                    continue
                root = blocked_root.get(d, d)
                withheld.append({"item": iid, "unresolved_dependency": d, "root_cause": root})
                blocked_root[iid] = root
                to_mint.remove(it)
                changed = True
                break

    scope_ids = {it.get("id") or _label(it) for it in items}
    orphaned = [{"candidate": c.get("id"), "ref": ref} for c in observed
                if (ref := owner_ref(c)) and ref not in scope_ids]

    return {"to_mint": to_mint, "already": already, "refused": refused,
            "withheld": withheld, "orphaned": orphaned, "ps_to_scs": ps_to_scs}


def _reason(plan: dict, minted: int) -> str:
    """A 0-mint ALWAYS states its reason. 'Never silently mints nothing' is not only about the
    absent-concept path -- a silent, reasonless success is the same invisible failure, wearing green."""
    if minted:
        return f"minted {minted} product candidate(s)."
    bits = []
    if plan["already"]:
        bits.append(f"{len(plan['already'])} scope item(s) already materialized")
    if plan["refused"]:
        bits.append(f"{len(plan['refused'])} REFUSED (provenance-integrity collision)")
    if plan["withheld"]:
        bits.append(f"{len(plan['withheld'])} withheld behind a refused dependency")
    if not bits:
        bits.append("the persisted scope carries no items")
    return "minted 0: " + "; ".join(bits) + "."


def _materialize(vault: Path, items: list[dict], *, dry_run: bool, acknowledge: set[str],
                 ts: str) -> dict:
    summary: dict = {}

    def _finish(plan: dict, minted: list[dict], concept_missing: bool) -> dict:
        status = "ok" if minted else ("nothing-to-mint" if not plan["refused"] and not plan["withheld"]
                                      else "blocked")
        return {
            "action": "materialize",
            "status": status,          # ok | nothing-to-mint | blocked   ('nothing-ready' is DELETED --
                                       # it was vocabulary from the ready-frontier rule spike D1 dropped)
            "dry_run": dry_run,
            "minted": [m["id"] for m in minted],
            "minted_count": len(minted),
            "would_mint": [it.get("id") or _label(it) for it in plan["to_mint"]],
            "already_materialized": plan["already"],
            "refused": plan["refused"],
            "withheld": plan["withheld"],
            "orphaned": plan["orphaned"],
            "concept_missing": concept_missing,
            "reason": _reason(plan, len(minted)),
        }

    # concept.json is NOT an input to materialize (C12): its declared inputs are product-scope.json and
    # candidates ∪ archive. It is read ONLY for the drift warning, and its absence is a WARNING, never a
    # refusal -- else a vault whose concept was moved could no longer materialize, breaking AC3.
    concept_missing = not (vault / "concept.json").exists()

    if dry_run:
        live = _load_json(vault / "candidates.json").get("candidates") or []
        plan = _plan(items, _observed(vault, live), acknowledge)
        return _finish(plan, [], concept_missing)

    def mutate(text: str) -> str:
        data = json.loads(text) if text.strip() else {
            "_schema": CANDIDATES_SCHEMA, "project": vault.name, "candidates": [], "pick_log": [],
        }
        if not isinstance(data, dict):
            raise _Refuse(1, "malformed", "candidates.json top-level is not a JSON object")
        cands = data.setdefault("candidates", [])
        if not isinstance(cands, list):
            raise _Refuse(1, "malformed", "candidates.json 'candidates' is not an array")

        # THE DEDUP SET IS COMPUTED INSIDE THE LOCK, over live ∪ archive -- the proven build_backlog
        # shape (build_backlog.py:451-470). A read-then-mint across the lock boundary would let two
        # parallel /slice sessions each observe an empty backlog and both mint the same scope item.
        plan = _plan(items, _observed(vault, cands), acknowledge)

        if not plan["to_mint"]:
            # Nothing to mint -> write NOTHING. Returning the text unchanged keeps candidates.json
            # byte-identical, so a re-run of an already-materialized vault is a true no-op rather than
            # a churned `updated` timestamp on a file every parallel slice is contending for.
            summary.update(_finish(plan, [], concept_missing))
            if text.strip():
                return text

        # TWO STRUCTURES, SPLIT BY TYPE ([[ADR-086]] §3 -- the type split IS the control here; the
        # rename is only a readability aid, since a global find/replace would rewrite both sites and
        # compile clean). `ps_to_scs` is the OBSERVED children, list-valued, READ-ONLY from here on.
        # `minted` is pass 1's accumulator: PS id -> the ONE new SC id, scalar BY TYPE.
        #
        # Conflating them (as the pre-slice-075 code did, and as this slice's own design did until
        # review) is not a style problem, it is the defect's cause. A single map must then hold BOTH a
        # list (observed) and a str (just-minted) -- and the fan-out at pass 2 would iterate a minted
        # id's CHARACTERS, yielding dependencies == ['-','0','2','C','S'] on the FIRST full-DAG mint
        # (every product bootstrap with a depends_on edge; the real vault's PS-004 is exactly this
        # shape). That is slice-068's per-character pseudo-source trap, in this file, which
        # iter_sources' own docstring documents at length. With the split, both that and a list-valued
        # candidate id are UNREPRESENTABLE rather than merely guarded against.
        ps_to_scs: dict[str, list[str]] = plan["ps_to_scs"]
        minted_ids: dict[str, str] = {}
        seed = id_allocator.seed_max_for(vault, "sc", data)
        for it in plan["to_mint"]:                       # pass 1: mint ids, in topological order
            iid = it["id"]
            # CR1 belt: identity ambiguity NEVER mints. LITERAL key membership (`in`), matching :684 --
            # never `.get()`, which is value TRUTHINESS and would silently stop refusing the moment any
            # future refactor pre-seeds keys with empty lists (`{it["id"]: [] for it in items}` is the
            # single most natural thing a reader does to a multimap). Two guards on one map with two
            # membership semantics is the CC-001 twin pattern; this one sits on the module's
            # most-defended invariant, so it stays byte-identical in meaning to what it replaced.
            if iid in ps_to_scs or iid in minted_ids:
                raise _Refuse(1, "malformed",
                              f"scope item id {iid!r} appears twice in the batch -- refusing to mint, "
                              f"since both records would collapse onto one candidate id.")
            minted_ids[iid] = id_allocator.next_id(data, "sc", seed_max=seed)

        minted = []
        for it in plan["to_mint"]:                       # pass 2: build records; deps now all resolvable
            # FAN-OUT ([[ADR-086]] §4): a dependent depends on ALL children of each depends_on item --
            # DERIVED from the existing consumer, not chosen. candidates_top._unmet_deps (:166-168) is
            # `[d for d in dependencies if d in live_ids]`: live == unmet, archived == met. So "depends
            # on every child of PS-X" IS "blocked until PS-X is done" -- one predicate at two call sites.
            #
            # depends_on ORDER is preserved OUTER and only each parent's children are sorted WITHIN.
            # NOT a sorted union: today's line is a comprehension in depends_on order, so a sorted union
            # REORDERS dependencies[] at N==1 for any item with >=2 edges (PS-003 -> ['SC-002','SC-001']
            # becomes ['SC-001','SC-002']) -- an AC5 break, into an append-only backlog with no un-mint,
            # invisible on today's corpus only because PS-004 is the sole item with a depends_on and it
            # has exactly one edge. This shape is byte-identical at N==1 BY CONSTRUCTION.
            # No dedupe: the two-parent refusal makes a shared child unrepresentable.
            deps: list[str] = []
            for d in it.get("depends_on") or []:
                if d in minted_ids:
                    deps.append(minted_ids[d])
                else:
                    deps.extend(sorted(ps_to_scs.get(d, []), key=_sc_sort_key))
            rec = _candidate_from(it, minted_ids[it["id"]], deps, ts)
            cands.append(rec)
            minted.append(rec)

        data["updated"] = ts
        if not data.get("project"):
            data["project"] = vault.name
        summary.update(_finish(plan, minted, concept_missing))
        return _dump(data)

    safe_mutate_text(vault / "candidates.json", mutate)
    return summary


# ── verbs ────────────────────────────────────────────────────────────────────────────────────────

def cmd_decompose_context(vault: Path, args) -> dict:
    """Hand concept.json to the model — the only thing in the pipeline that can read prose.

    build_backlog.py is deterministic and CANNOT interpret a concept's `what` narrative, which is why
    the decomposition lives in the SKILL (a model context) and only its RESULT is crossed into the vault.
    """
    concept, sha = _concept(vault, required=True)
    scope = _scope(vault, required=False)
    return {
        "action": "decompose-context",
        "status": "ok",
        "concept": concept,
        "concept_sha256": sha,
        "already_decomposed": bool(scope.get("items")),
        "existing_items": [{"id": i.get("id"), "title": i.get("title")} for i in scope.get("items") or []],
    }


def cmd_persist(vault: Path, args) -> dict:
    """THE ONCE-ACT. Cross the model's decomposition into the vault exactly once, then materialize."""
    _, sha = _concept(vault, required=True)
    items_in = _load_items(args.items_file)
    if (vault / SCOPE_FILE).exists() and _scope(vault, required=False).get("items"):
        raise _Refuse(
            4, "already-decomposed",
            f"{SCOPE_FILE} already carries a decomposition. persist is CREATE-ONLY: the scope BOUNDARY "
            f"itself drifts between model runs (B1 measured 4 of 18 items produced by exactly one of two "
            f"blind runs), so a re-decomposition is a SEMANTIC change to the product's scope and must be "
            f"a deliberate, user-visible act -- never a side effect of re-running the skill. Use "
            f"`revise --items-file` to extend or correct the scope (it preserves already-minted PS ids).",
        )
    _check_contract(items_in)
    _check_deps(items_in, args.items_file)      # CR3: an unresolvable depends_on STOPS, never drops
    ts = _now()
    holder: dict = {}

    def mutate(text: str) -> str:
        # FIRST STATEMENT, INSIDE THE LOCK (ADR-067 section 3). THE identity guard: the model may never
        # supply a cross-run identity. B1 measured 22% key agreement across two blind decompositions --
        # 5 of 7 semantically-identical items drifted, INCLUDING the orchestrator -- so a model-keyed
        # dedup would re-mint 78% of the backlog every run.
        id_allocator.reject_supplied_id("ps", items_in)

        data = json.loads(text) if text.strip() else {}
        if data.get("items"):                       # create-only, re-checked under the lock
            raise _Refuse(4, "already-decomposed",
                          f"{SCOPE_FILE} was decomposed by a parallel writer; use `revise`.")
        ordered = _topo(items_in, _label)
        data["_schema"] = SCOPE_SCHEMA
        data["project"] = vault.name
        data["concept_sha256"] = sha
        data["decomposed_at"] = ts

        seed = id_allocator.seed_max_for(vault, "ps", data)
        label_to_ps: dict[str, str] = {}
        out_items: list[dict] = []
        for it in ordered:
            ps = id_allocator.next_id(data, "ps", seed_max=seed)
            label_to_ps[_label(it)] = ps
            out_items.append({
                "id": ps,
                "decomposition_label": _label(it),
                "title": str(it["title"]).strip(),
                "description": it.get("description") or it["title"],
                "user_visible_outcome": it.get("user_visible_outcome"),
                "depends_on": [],                  # rewritten below, once every label has an id
                "assumptions": _normalize_assumptions(it),
                "verification_plan": it.get("verification_plan"),
            })
        for oi, src in zip(out_items, ordered):
            # The model's label was an INTRA-CALL correlation id only. It is discarded as an identity
            # the moment this lock closes -- a model key is allowed WITHIN one run, never ACROSS runs.
            oi["depends_on"] = [label_to_ps[d] for d in (src.get("depends_on") or []) if d in label_to_ps]

        data["items"] = out_items
        holder["items"] = out_items
        return _dump(data)

    safe_mutate_text(vault / SCOPE_FILE, mutate)
    items = holder["items"]
    # SECOND locked write, on a DIFFERENT file. Stated plainly: this is not one transaction. A crash
    # here leaves the scope persisted and its candidates unminted -- recoverable by re-running
    # `materialize`, which is exactly why materialize survives as a standalone idempotent verb.
    mat = _materialize(vault, items, dry_run=False, acknowledge=set(), ts=ts)
    return {
        "action": "persist", "status": "ok",
        "persisted": len(items),
        "minted_ids": [i["id"] for i in items],
        "concept_sha256": sha,
        "materialize": mat,
    }


def cmd_revise(vault: Path, args) -> dict:
    """The scope-correction verb (C8) — explicit, user-gated, in-lock.

    Without it the FIRST decomposition (a coin-flip snapshot of a stochastic decomposer: 22% key
    stability, 4-of-18 boundary drift) would be frozen PERMANENTLY — persist is create-only and a minted
    SC id cannot be un-minted — and ADR-066's promised "human-adjudicated diff" would have no verb able
    to execute it.

    Already-minted PS ids are preserved BY ID, never re-minted. An item carrying an id the receiver never
    minted is REJECTED: the model may reuse an identity the vault gave it, never invent one.
    """
    scope = _scope(vault, required=True)
    existing = {str(i.get("id")): i for i in scope.get("items") or [] if isinstance(i, dict)}
    items_in = _load_items(args.items_file, allow_empty=True)
    cut_ids = [str(c).strip() for c in (getattr(args, "cut", None) or []) if str(c).strip()]
    reason = getattr(args, "reason", None)
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else None

    # --reason is required IFF --cut (critique M3, ratified at TRI-1 for crossdomain+expert over
    # practice's uniform requirement). An ADD is self-describing: the item's own title/description
    # IS its reason, so demanding one taxes the benign extend path. A CUT destroys the only record
    # of what was there, so its reason IS the record -- the ledger entry is worthless without it.
    if cut_ids and not reason:
        raise _Refuse(
            2, "usage",
            f"--cut {' '.join(cut_ids)} requires --reason '<why>'. A cut removes a capability from "
            f"the product's scope of record; the reason is the only thing that survives it, and the "
            f"revisions[] entry exists to carry exactly that. (An ADD needs no --reason -- the "
            f"item's own title and description are self-describing.)",
        )
    if not items_in:
        # m2: NAME the act, not the payload's shape. `_load_items` would otherwise refuse this with
        # "must carry a non-empty `items` array" -- a message about a MALFORMED payload, when what
        # was actually attempted is a deliberate shrink-to-zero. That is AC4's own complaint (a
        # refusal naming the wrong cause) one function over, which is why it is worth the branch.
        raise _Refuse(
            2, "usage",
            f"this revision would leave the product's scope with ZERO items (it carries no `items` "
            f"and cuts {', '.join(cut_ids) if cut_ids else 'nothing'}). A product with no scope is "
            f"not a revision of it -- refusing. If the product really has been descoped entirely, "
            f"that is a deliberate RE-DECOMPOSITION, not a revise: remove {SCOPE_FILE} and re-run "
            f"/slice-candidates --product against the revised concept.json. Nothing was written.",
        )

    # A CHEAP EARLY REFUSAL ONLY -- never the only one (critique-review M-add-1). `existing` is a
    # PRE-LOCK snapshot; once `--cut` can retire an id it is neither a subset nor a superset of the
    # in-lock `cur`, so the authoritative re-check lives inside the mutate closure below. Keeping
    # this one costs nothing and refuses the common typo before taking the lock. The in-file
    # precedent is persist: create-only is checked at :712 and RE-CHECKED under the lock at :734.
    for it in items_in:
        iid = it.get("id")
        if iid is not None and str(iid) not in existing:
            raise _Refuse(
                2, "usage",
                f"scope item carries id {iid!r}, which this vault's scope does not carry. `revise` "
                f"may REUSE an allocator-minted PS id that is still in the scope (that is how an "
                f"item is preserved across a revision) but may never INVENT one, and may never "
                f"RESURRECT a cut one -- identity is minted by the receiver. Omit `id` for a new item.",
            )
    # CR1: rejecting an INVENTED id is not enough -- a REPEATED one aliases two items onto one minted
    # candidate. _load_items already ran _check_identities; this is the boundary that made it necessary.
    _check_deps(items_in, args.items_file)      # CR3: a dep dropped from the revision STOPS, never drops
    ts = _now()
    holder: dict = {}

    def mutate(text: str) -> str:
        data = json.loads(text) if text.strip() else {}
        cur = {str(i.get("id")): i for i in data.get("items") or [] if isinstance(i, dict)}

        # THE AUTHORITATIVE GATES, against the IN-LOCK read (slice-073). Both were previously
        # evaluated against the STALE pre-lock snapshot, or not at all:
        #   _check_membership -- SC-160's omission gate + M-add-1's resurrection guard, bidirectional
        #   _check_contract   -- relaxed (Phase 2 only) for items whose spike has ALREADY run
        #                        (SC-161), keyed on the population this gate just verified
        _check_membership(items_in, cur, cut_ids, args.items_file)

        # THE EXEMPT SET keys on MATERIALIZED, not on minted-`id` presence (ADR-079 section 1). The
        # design spike REJECTED the id-keyed rule by executing its exploit: an item can carry a
        # minted id while never having been materialized (materialize REFUSES a provenance
        # collision, and WITHHOLDS its dependents transitively), so id-presence would exempt an item
        # /risk-spike step-0 could never have run on -- reopening the ADR-067 section 5 bypass.
        # Exempt iff a candidate exists THROUGH WHICH step-0 could actually have run. The CALLER
        # does the lookup; _check_contract performs none.
        live = _load_json(vault / "candidates.json").get("candidates") or []
        exempt = {ref for c in _observed(vault, [c for c in live if isinstance(c, dict)])
                  if (ref := owner_ref(c))} & set(cur)
        # slice-076 / ADR-087 + M-add-1: pass the in-lock `cur` as `prev_by_id` so the two
        # contract-completeness fields are validated on the EFFECTIVE value (item OR prev) the persist
        # merge at :1328/:1331 will write -- the check and the write agree on the same value.
        _check_contract(items_in, exempt_spiked=frozenset(exempt), prev_by_id=cur)

        ordered = _topo(items_in, lambda it: str(it.get("id") or _label(it)))
        key_to_ps: dict[str, str] = {}
        seed = id_allocator.seed_max_for(vault, "ps", data)
        for it in ordered:
            iid = str(it["id"]) if it.get("id") else id_allocator.next_id(data, "ps", seed_max=seed)
            key_to_ps[str(it.get("id") or _label(it))] = iid
            key_to_ps.setdefault(_label(it), iid)

        out_items = []
        for it in ordered:
            key = str(it.get("id") or _label(it))
            ps = key_to_ps[key]
            prev = cur.get(ps) or {}
            out_items.append({
                "id": ps,
                "decomposition_label": _label(it) or prev.get("decomposition_label"),
                "title": str(it["title"]).strip(),
                "description": it.get("description") or prev.get("description") or it["title"],
                "user_visible_outcome": it.get("user_visible_outcome") or prev.get("user_visible_outcome"),
                "depends_on": [key_to_ps[d] for d in (it.get("depends_on") or []) if d in key_to_ps],
                "assumptions": _normalize_assumptions(it),
                "verification_plan": it.get("verification_plan") or prev.get("verification_plan"),
            })

        data["_schema"] = SCOPE_SCHEMA
        data.setdefault("project", vault.name)
        data["revised_at"] = ts
        data["items"] = out_items

        # ── the revisions[] ledger (slice-073 / ADR-078) ──────────────────────────────────────
        # THE DEFECT, verbatim: `dropped` was computed here and thrown to STDOUT, so a deletion from
        # the PRODUCT'S SCOPE OF RECORD left zero durable trace. A membership change is now written
        # IN THE FILE, inside this same lock. Append-only: prior entries are never rewritten or
        # truncated -- that is the defect's own lesson (create-only `persist` was mistaken for
        # append-only HISTORY).
        #
        # `data["revisions"] = (data.get("revisions") or []) + [rec]`, never `setdefault`, and ONLY
        # when the item set actually changed -- so a no-op / description-only revise never grows the
        # key and the LIVE revisions-less file stays byte-identical apart from revised_at (AC5).
        # An absent key is a legal, PERMANENT state meaning "empty history"; there is no migration.
        #
        # B1 (blocker) makes this ledger LOAD-BEARING beyond its audit value: `cut` retires a PS id,
        # which falsifies id_allocator.seed_max_for('ps')'s previous premise that the live items[] IS
        # the full history. revisions[].cut IS the retirement history the allocator's floor now scans
        # (id_allocator.py:180-190) -- so this record is not documentation, it is the thing that
        # stops a retired id being re-issued onto a shipped candidate.
        kept_ids = {o["id"] for o in out_items}
        dropped = [i for i in cur if i not in kept_ids]
        added = [o["id"] for o in out_items if o["id"] not in cur]
        if dropped or added:
            data["revisions"] = (data.get("revisions") or []) + [{
                "at": ts,
                "cut": dropped,
                "added": added,
                # required iff `cut` (M3's taste call, ratified at TRI-1): an ADD is self-describing
                # -- the item's own title/description IS the reason -- while a CUT destroys the only
                # record of what was there. Null on an add-only revise.
                "reason": reason,
                "items_before": len(cur),
                "items_after": len(out_items),
            }]

        holder["items"] = out_items
        holder["dropped"] = dropped
        return _dump(data)

    safe_mutate_text(vault / SCOPE_FILE, mutate)
    items = holder["items"]
    mat = _materialize(vault, items, dry_run=False, acknowledge=set(), ts=ts)
    return {
        "action": "revise", "status": "ok",
        "items": len(items),
        "preserved": [i["id"] for i in items if str(i["id"]) in existing],
        # A CUT scope item leaves its candidate UNTOUCHED (the backlog is append-only and the
        # candidate may already be shipped). materialize reports it as `orphaned` and takes NO action
        # -- the owner-deletion cascade the k8s frame wanted is not even expressible. Unchanged by
        # slice-073, and deliberately so (out_of_scope).
        #
        # This field is now a REPORT of a durable fact, not the only record of it: every id here also
        # appears in product-scope.json's revisions[].cut. Reporting a deletion ONLY here was the
        # defect (:847/:860).
        "cut": holder["dropped"],
        "dropped": holder["dropped"],   # retained: the pre-slice-073 key, for any existing consumer
        "materialize": mat,
    }


def cmd_materialize(vault: Path, args) -> dict:
    if args.scope_file and not args.dry_run:
        # C7 (trust boundary). `--scope-file` WITHOUT `--dry-run` would be a WRITE path minting real,
        # monotonic, NON-REVOCABLE candidates from an arbitrary, non-allocator-keyed items file --
        # re-opening the exact hole ADR-067 exists to close. The only WRITE-path scope source is the
        # vault's own persisted product-scope.json.
        raise _Refuse(
            2, "usage",
            "--scope-file implies --dry-run: it is a READ-ONLY replay surface. Minting from an "
            "arbitrary items file would let a caller supply cross-run identities into an append-only, "
            "non-revocable backlog. Pass --dry-run, or run `persist`/`revise` to write.",
        )
    if args.scope_file:
        items = _load_items(args.scope_file)
        # slice-076 / ADR-087 (m3 + M-add-2): the dry-run --scope-file replay reaches _candidate_from
        # WITHOUT the write-path recognizer (_load_items runs only _check_identities). Recognize the
        # replayed items here -- strict/no-prev, exactly like persist -- so an empty verification_plan
        # or user_visible_outcome refuses BY NAME instead of leaking a silent None / bare index onto
        # the read surface. (The WRITE path via --scope-file is already refused above, C7.)
        _check_contract(items)
    else:
        items = [i for i in _scope(vault, required=True).get("items") or [] if isinstance(i, dict)]
    return _materialize(vault, items, dry_run=bool(args.dry_run),
                        acknowledge=set(args.acknowledge or []), ts=_now())


def _torn_provenance(observed: list[dict], scope_ids: set[str]) -> dict[str, list[str]]:
    """PS ref -> the candidate ids whose provenance is TORN. The tombstone (slice-075 / [[ADR-086]] §6).

    THE FAILURE IT DETECTS. `done` quantifies over "every linked candidate is archived", and a child is
    linked by its own `source[].ref`. If a child LOSES that ref it does not fail loudly -- it VANISHES
    from its parent's child set, and the parent then reports `done` with an unaccounted-for child still
    in flight. That is the G-Set absence/deletion ambiguity: "removed" and "never existed" are the same
    observation. So keep a second, independent witness and let disagreement force `unknown`.

    THE WITNESS IS FREE. `_candidate_from` already writes the PS id into `history[0].ref` as well as
    `source[].ref` -- no new field, no new producer, no migration. The substrate already carries it.

    THE DISCRIMINATOR IS NARROW ON PURPOSE (critique M4). It keys on the PRODUCER's own shape --
    `event == "created" AND by == "slice-candidates" AND ref in the LIVE scope's ids` -- never on the
    ref's TEXT. Measured on the real backlog: `history[].ref` is free-form prose carrying 149 DISTINCT
    values across 98/168 candidates, including 'slice-058', 'slice-023 ADR-015 deferred', and one
    396-character narrative paragraph. A `^PS-\\d+$` regex over ALL of history[] would fire on any actor
    that ever appends PS-shaped prose -- and the damage is STICKY: history[] is append-only with no
    un-write, so a false `unknown` could never be cleared. Scanning the created-event ONLY matches what
    the producer writes and makes a later free-text append harmless; requiring LIVE-scope membership
    means a cut or legacy PS id cannot resurrect an `unknown`. (slice-051's producer/gate SSOT lesson:
    the detector reuses the producer's own key shape.)

    IT IS A HEURISTIC, NOT A GUARANTEE -- said plainly, because this slice turns on not overselling a
    proof. `history[]` rides the same model-mediated /commit-slice Step-6 copy as `source[]` and is
    writable through the same unrefused `vault_edit append` path, so it is forgeable and BOTH witnesses
    can be lost together -- correlated loss is not closed and is not pretended covered. It is strictly
    ADDITIVE: worst case it equals the status quo; best case it converts a SILENT loss into a loud
    `unknown`. Measured today: 4/4 real PRODUCT children carry BOTH witnesses, and source-loss has
    occurred in 0 of 76 real archive moves -- so this defends a failure never yet OBSERVED. Insurance
    against a reachable hypothetical, not a fix for a live defect.
    """
    torn: dict[str, list[str]] = {}
    for c in observed:
        claimed = set(owner_refs(c))
        for h in c.get("history") or []:
            if not isinstance(h, dict):
                continue
            if h.get("event") != "created" or h.get("by") != "slice-candidates":
                continue
            ref = str(h.get("ref") or "").strip()
            if ref and ref in scope_ids and ref not in claimed:
                torn.setdefault(ref, []).append(str(c.get("id")))
    return torn


def cmd_done(vault: Path, args) -> dict:
    """Is this capability finished? A PURE, UNCACHED, 4-valued read (slice-075 / [[ADR-086]] §5).

    FORM 2 ONLY -- "every linked candidate is archived" -- per spike A2. FORM 1 (a reality-witness link
    from a validation back to the capability's verification_plan) has ZERO producers today:
    validation.json keys on slice + AC ids and never on a PS id. Claiming it would mean inventing a
    witness that does not exist, so `done` scopes to what the artifacts can actually decide.

    FOUR-VALUED, NEVER A BOOL. `done | in-progress | no-children | unknown` are distinct STATES, so "I
    cannot tell" and "there is nothing to tell" can never be read as a falsy `done` by a caller writing
    `if result:`. The empty case is guarded BY TYPE rather than by remembering that `all([])` is
    vacuously true (spike A2.4).

    NO CACHE, NO LATCH -- load-bearing; do not optimise away. `done` is NOT monotone: the child set can
    GROW, so a `true` can legitimately become `false`. Not theoretical -- `sc` is a registered vault_edit
    managed kind and _APPEND_REFUSED_KINDS is `{"ps"}` (vault_edit.py:197), so ONE `vault_edit append`
    carrying a product-scope source grows a child set at rc=0 TODAY, no splitter involved (proven by
    execution). A latched `done: true` would be wrong by construction, now -- not after some future
    slice. Recomputing every call is what makes the lock-free two-file read correct, not merely cheap.

    THE JOIN IS CONSERVATIVE AND ORDER-BIASED, because the two-file read is NOT atomic (two files, two
    SVW-1 locks, no cross-file transaction, and this verb deliberately takes no lock). Read LIVE first,
    then archive: a child interleaved by /commit-slice's move appears in BOTH and counts LIVE =>
    done=false -- a false NEGATIVE, the safe direction. Archive-first would make it appear in NEITHER --
    it would VANISH, and the parent would report `done` with a child in flight.
    """
    scope = _scope(vault, required=True)
    items = [i for i in scope.get("items") or [] if isinstance(i, dict)]
    scope_ids = {str(it.get("id")) for it in items if it.get("id")}

    if args.item and str(args.item) not in scope_ids:
        # Mirrors _check_membership's posture: an id this scope does not carry is a USAGE error, never
        # an empty result -- a typo must not read as "that capability has no children".
        raise _Refuse(
            2, "usage",
            f"--item {args.item} names a scope item this product does not carry (live ids: "
            f"{', '.join(sorted(scope_ids)) or 'none'}). Read {SCOPE_FILE} for the real ids.",
        )

    live = [c for c in (_load_json(vault / "candidates.json").get("candidates") or [])
            if isinstance(c, dict)]
    arch = [c for c in (_load_json(vault / "archive" / "candidates.json").get("candidates") or [])
            if isinstance(c, dict)]
    live_ids = {str(c.get("id")) for c in live}
    arch_by_id = {str(c.get("id")): c for c in arch}

    observed = _observed(vault, live)          # live-first, deliberately (see the join note above)
    torn = _torn_provenance(observed, scope_ids)

    # ONE shared derivation with the mint path -- and it now also carries AMBIGUITY (code-review CR1).
    # Without it, a child claiming two parents was filed under its FIRST parent only, and the SECOND
    # parent reported `done` with a live child still claiming it: a false `done`, the one thing the
    # error_model forbids. An ambiguous parent joins the torn case -- `unknown`, never `done`.
    children, ambiguous = children_by_parent(observed)

    out_items = []
    for it in items:
        iid = str(it.get("id"))
        if args.item and iid != str(args.item):
            continue
        kids = children.get(iid, [])
        archived = [k for k in kids if k in arch_by_id and k not in live_ids]
        pending = [k for k in kids if k not in archived]
        composition = {"shipped": 0, "rejected": 0}
        for k in archived:
            st = str(arch_by_id[k].get("status") or "").strip()
            if st in composition:
                composition[st] += 1

        amb_kids = sorted({r["candidate"] for r in ambiguous.get(iid, [])}, key=_sc_sort_key)
        if amb_kids or iid in torn:
            # every ambiguity resolves to the SAFE side -- never a false `done`
            state = "unknown"
        elif not kids:
            state = "no-children"
        elif not pending:
            state = "done"
        else:
            state = "in-progress"

        entry = {
            "item": iid,
            "title": it.get("title"),
            "state": state,                # done | in-progress | no-children | unknown -- NEVER a bool
            "children": kids,
            "archived": archived,
            "pending": pending,
            # FORM 2 counts a REJECTED child as archived, so a rejected-only capability reports `done`.
            # Bound by spike constraint A2.1 (FORM 2 is the only decidable form), so it is SURFACED
            # rather than laundered: the real archive carries 73 `shipped` + 3 `rejected`.
            "archived_composition": composition,
        }
        if state == "unknown":
            reasons = []
            if amb_kids:
                entry["ambiguous_children"] = amb_kids
                reasons.append(
                    f"candidate(s) {', '.join(amb_kids)} claim MORE THAN ONE product-scope parent, so "
                    f"whether they belong to {iid} has no fact of the matter -- this capability's "
                    f"child set is undefined and it cannot be called done. REMEDY: correct each child's "
                    f"`source[]` to a single parent (`vault_edit.py update --file candidates.json "
                    f"--array candidates --id <SC-NNN> --set 'source=[...one product-scope ref...]'`; "
                    f"use --file archive/candidates.json if it has shipped), then re-run."
                )
            if iid in torn:
                entry["torn_provenance"] = torn[iid]
                reasons.append(
                    f"candidate(s) {', '.join(torn[iid])} were CREATED as this capability's children "
                    f"(history[].ref == {iid}) but no longer carry its product-scope source[]. Their "
                    f"state cannot be accounted for. REMEDY: restore the child's "
                    f"`source: [{{\"type\": \"product-scope\", \"ref\": \"{iid}\"}}]`, or -- if it was "
                    f"deliberately re-parented -- correct its history so the two witnesses agree."
                )
            entry["reason"] = " ".join(reasons)
        out_items.append(entry)

    return {
        "action": "done",
        "status": "ok",
        "vault": str(vault),
        "items": out_items,
        "counts": {s: sum(1 for e in out_items if e["state"] == s)
                   for s in ("done", "in-progress", "no-children", "unknown")},
    }


def cmd_census(vault: Path, args) -> dict:
    """Re-run the classification that DETECTED this defect — shipped, testable, repeatable.

    AC1's "0 -> >0" is not a one-off spike assertion; it is a command, so the PRODUCT count can never
    silently return to 0 without someone being able to see it.
    """
    live = _load_json(vault / "candidates.json").get("candidates") or []
    observed = _observed(vault, [c for c in live if isinstance(c, dict)])

    counts = {"PRODUCT": 0, "HUMAN": 0, "EXHAUST": 0, "UNCLASSIFIED": 0}
    unknown: dict[str, int] = {}
    no_source = 0
    for c in observed:
        bucket, unk = classify_candidate(c)
        counts[bucket] += 1
        for t in unk:
            unknown[t] = unknown.get(t, 0) + 1
        if not iter_sources(c):
            no_source += 1

    total = len(observed)
    return {
        "action": "census",
        "status": "ok",
        "vault": str(vault),
        "total": total,
        "counts": counts,
        "product_share": round(counts["PRODUCT"] / total, 4) if total else 0.0,
        # The tripwire. An unknown source type is LOUD here rather than silently absorbed into EXHAUST
        # -- which is how the "0" stayed invisible for 145 candidates in the first place.
        "unclassified": [{"type": t, "count": n} for t, n in sorted(unknown.items())],
        "no_source": no_source,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    # --vault / --json are accepted in EITHER position (before the verb on the top parser, or after it
    # on each subparser). The subparser copies use SUPPRESS so an omitted flag never clobbers a value
    # the top parser already captured — the vault_edit idiom.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", default=argparse.SUPPRESS,
                        help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="emit the JSON summary (default: human-readable text)")

    p = argparse.ArgumentParser(
        prog="product_scope", parents=[common],
        description="Materialize the product's own scope into <vault>/candidates.json (slice-068).",
    )
    sub = p.add_subparsers(dest="verb", required=True)

    sub.add_parser("decompose-context", parents=[common],
                   help="emit concept.json for the model to decompose (exit 3 when it is absent)")

    pe = sub.add_parser("persist", parents=[common],
                        help="THE ONCE-ACT: cross the decomposition in, mint ids, materialize")
    pe.add_argument("--items-file", required=True,
                    help="the model's decomposition: {items:[{label,title,description,depends_on,"
                         "assumptions,verification_plan}]} -- NO ids (identity is minted in-lock)")

    m = sub.add_parser("materialize", parents=[common],
                       help="idempotently mint candidates from the persisted scope")
    m.add_argument("--dry-run", action="store_true", help="plan only; write nothing")
    m.add_argument("--scope-file", default=None,
                   help="replay an alternate items file. IMPLIES --dry-run (read-only surface).")
    m.add_argument("--acknowledge", action="append", default=[], metavar="PS-NNN",
                   help="override the provenance-integrity refusal for this scope item (repeatable)")

    r = sub.add_parser("revise", parents=[common],
                       help="explicit scope correction (preserves minted PS ids by id)")
    r.add_argument("--items-file", required=True)
    # slice-073 / ADR-078. The cut escape is on the CLI, not in the payload -- the design spike
    # FORCED that (spike-cut-escape): a payload-carried cut is a claim made by the same untrusted,
    # stochastic producer whose omissions are the defect, so it could not be told apart from the
    # accident it exists to distinguish. The flag is a SECOND, deliberate, human act. Shape mirrors
    # `materialize --acknowledge PS-NNN` field-for-field -- the in-module precedent for a repeatable,
    # CLI-side, per-id override of a refusal.
    r.add_argument("--cut", action="append", default=[], metavar="PS-NNN",
                   help="deliberately REMOVE this already-minted scope item (repeatable). "
                        "Requires --reason. Recorded in product-scope.json's append-only revisions[].")
    r.add_argument("--reason", default=None,
                   help="why the --cut item(s) are being removed. REQUIRED with --cut (an added "
                        "item is self-describing; a cut destroys the only record of what was there).")

    sub.add_parser("census", parents=[common],
                   help="classify live u archive into PRODUCT / EXHAUST / HUMAN / unclassified")

    dn = sub.add_parser("done", parents=[common],
                        help="is a capability finished? 4-valued, read-only (done | in-progress | "
                             "no-children | unknown)")
    dn.add_argument("--item", default=None, metavar="PS-NNN",
                    help="report only this scope item (default: every item)")
    return p


_DISPATCH = {
    "decompose-context": cmd_decompose_context,
    "persist": cmd_persist,
    "materialize": cmd_materialize,
    "revise": cmd_revise,
    "census": cmd_census,
    "done": cmd_done,
}


def _text(out: dict) -> str:
    a = out.get("action")
    if a == "done":
        lines = [f"CAPABILITY STATE ({out['vault']}) -- live u archive, computed fresh (never cached)"]
        for e in out["items"]:
            lines.append(f"  {e['item']} [{e['state'].upper()}] {e['title'] or ''}".rstrip())
            if e["children"]:
                comp = e["archived_composition"]
                lines.append(f"      children: {len(e['children'])}  "
                             f"archived: {len(e['archived'])} ({comp['shipped']} shipped, "
                             f"{comp['rejected']} rejected)  pending: {len(e['pending'])}")
                if e["pending"]:
                    lines.append(f"      still in flight: {', '.join(e['pending'])}")
            if e["state"] == "unknown":
                lines.append(f"      {e['reason']}")
            elif e["state"] == "no-children":
                lines.append("      no candidates link to this capability -- nothing has been "
                             "materialized for it, so it is NOT done (an empty set is not a finished "
                             "one).")
        c = out["counts"]
        lines.append(f"  => done {c['done']} | in-progress {c['in-progress']} | "
                     f"no-children {c['no-children']} | unknown {c['unknown']}")
        return "\n".join(lines)
    if a == "census":
        c = out["counts"]
        lines = [f"CENSUS ({out['vault']}) -- {out['total']} candidates, live u archive",
                 f"  PRODUCT ...... {c['PRODUCT']:4d}  ({out['product_share']:.1%})",
                 f"  HUMAN ........ {c['HUMAN']:4d}",
                 f"  EXHAUST ...... {c['EXHAUST']:4d}",
                 f"  UNCLASSIFIED . {c['UNCLASSIFIED']:4d}"]
        if out["unclassified"]:
            lines.append("  unknown source types (the taxonomy grew -- classify them):")
            lines += [f"      {u['type']!r} x{u['count']}" for u in out["unclassified"]]
        if not c["PRODUCT"]:
            lines.append("  => the product's own scope is ABSENT from this backlog. Run /discover, "
                         "then /slice-candidates --product.")
        return "\n".join(lines)
    if a in ("persist", "revise"):
        m = out["materialize"]
        return (f"{a}: {out.get('persisted', out.get('items'))} scope item(s) persisted "
                f"({', '.join(out['materialize']['minted']) or 'no new candidates'}).\n  {m['reason']}")
    if a == "materialize":
        head = "materialize (DRY RUN -- nothing written)" if out["dry_run"] else "materialize"
        lines = [f"{head}: {out['reason']}"]
        if out["dry_run"] and out["would_mint"]:
            lines.append(f"  would mint: {', '.join(out['would_mint'])}")
        for r in out["refused"]:
            lines.append(f"  REFUSED {r['item']}: {r['reason']}")
        for w in out["withheld"]:
            lines.append(f"  withheld {w['item']} (behind {w['root_cause']})")
        if out["concept_missing"]:
            lines.append("  WARNING: concept.json is absent -- the persisted scope cannot be checked "
                         "for drift against it.")
        return "\n".join(lines)
    return json.dumps(out, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    as_json = getattr(args, "json", False)
    vault = Path(getattr(args, "vault", None) or VAULT_ROOT)
    try:
        out = _DISPATCH[args.verb](vault, args)
    except _Refuse as exc:
        if as_json:
            print(json.dumps({"action": args.verb, "status": exc.status, "error": exc.message},
                             ensure_ascii=False))
        sys.stderr.write(f"product_scope {args.verb}: {exc.message}\n")
        return exc.code
    except ValueError as exc:                      # id_allocator's guards, fail-VISIBLE
        if as_json:
            print(json.dumps({"action": args.verb, "status": "rejected", "error": str(exc)},
                             ensure_ascii=False))
        sys.stderr.write(f"product_scope {args.verb}: {exc}\n")
        return 2
    print(json.dumps(out, ensure_ascii=False) if as_json else _text(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
