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
  * `revise` is the deliberate, user-gated scope correction (preserves minted ids BY id, never re-mints).
  * `census` re-runs the classification that DETECTED this defect, so it can never become invisible again.

AUTHORITATIVE DESIGN RECORD: **ADR-067** (SUPERSEDES ADR-066 — where they disagree, ADR-067 wins).
Load-bearing decisions it changed, all of which live in this file:

  1. Materialize IN THE ONCE-ACT; /slice stays READ-ONLY. A full-DAG mint (spike D1) mints every item on
     the first tick, so a level-triggered reconciler had exactly one non-trivial run in its life. It was
     VESTIGIAL, and it made AC3 unfalsifiable. Removing the /slice mutation DISSOLVES the whole
     read-your-own-writes / injection-ordering hazard rather than managing it.
  2. The identity guard lives HERE, in `persist`'s own in-lock `reject_supplied_id` — NOT in
     vault_edit._MANAGED_KIND. persist must REWRITE the model's depends_on labels into minted ids inside
     ONE lock, and `vault_edit append` mints internally and returns nothing to the caller, so persist
     bypasses vault_edit entirely; a _MANAGED_KIND entry would have guarded a path no writer takes. (This
     also follows the repo's own documented convention for cross-referencing appenders —
     vault_edit.py:137-140, the risk-register risks[] precedent.)
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
    product_scope.py [--vault ROOT] revise --items-file PATH [--json]
    product_scope.py [--vault ROOT] census [--json]

    0  ran (minted N >= 0; a 0-mint states its reason)
    1  runtime error (fail-visible)
    2  usage error (incl. a model-supplied id, an invented PS id, --scope-file without --dry-run)
    3  concept.json ABSENT           -> actionable message naming /discover            [decompose-context, persist]
    4  product-scope.json ABSENT     -> actionable message naming /slice-candidates --product   [materialize, revise]
       product-scope.json ALREADY EXISTS -> create-only refusal naming `revise`        [persist]
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
    """
    for s in iter_sources(cand):
        if s.get("type") == PRODUCT_SOURCE_TYPE and s.get("ref"):
            return str(s["ref"])
    return None


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


def _load_items(path: str) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        raise _Refuse(2, "usage", f"--items-file not found: {p}")
    data = _load_json(p)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
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
    onto ONE minted SC id in materialize's pass 1 (`ps_to_sc[it['id']] = next_id(...)` -- the second
    write wins), and pass 2 then stamped BOTH candidate records with that same id. Reproduced: scope
    ['PS-001','PS-001'] -> candidates ['SC-003','SC-003'], exit 0, "minted 2".

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


def _check_contract(items: list[dict]) -> None:
    """Every scope item must carry a title, a label, and >=1 BLOCKING unproven assumption (ADR-067 §5).

    The assumption requirement is enforced HERE, at the crossing — not downstream — because /risk-spike
    step-0 SKIPS a candidate with zero unproven blocking assumptions. Without this, aivlc's
    orchestrator/state-machine (the largest, least-understood, never-before-attempted item in that
    product, and the very thing this module exists to surface) would enter the loop with NOTHING TO
    PROVE and walk straight into the design tournament on an unspiked premise.
    """
    for it in items:
        if not str(it.get("title") or "").strip():
            raise _Refuse(2, "usage", f"scope item {_label(it) or '<unnamed>'} has no `title`.")
        if not _label(it):
            raise _Refuse(2, "usage", f"scope item {it.get('title')!r} has no `label` to key depends_on on.")
        blocking = [a for a in it.get("assumptions") or []
                    if isinstance(a, dict) and a.get("blocking")
                    and (a.get("spike_status") or "unproven") == "unproven"]
        if not blocking:
            raise _Refuse(
                2, "usage",
                f"scope item {_label(it)!r} carries no BLOCKING unproven assumption. A product "
                f"capability is UNPROVEN by definition -- with `assumptions: []` this candidate would "
                f"SKIP /risk-spike step-0, the pipeline's reality gate, on exactly the least-understood "
                f"work in the product (ADR-067 section 5). Add assumptions[] and re-run.",
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
        "user_visible_outcome": item.get("user_visible_outcome"),
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
        "verification_plan": item.get("verification_plan")
            or f"Prove the blocking assumption(s) at /risk-spike step-0, then verify "
               f"{item.get('user_visible_outcome') or 'the stated outcome'} against reality.",
        "history": [{"event": "created", "by": "slice-candidates", "at": ts, "ref": item["id"]}],
    }


def _plan(items: list[dict], observed: list[dict], acknowledge: set[str]) -> dict:
    """Decide, WITHOUT writing, what materialize would do. Computed inside the caller's lock.

    Bias EVERY ambiguity toward NOT minting: a mint burns a monotonic SC id and there is no un-mint, so
    the self-correcting safety net a level-triggered reconciler normally relies on is ABSENT here.
    """
    ps_to_sc = {ref: c.get("id") for c in observed if (ref := owner_ref(c))}
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
        if it.get("id") and it["id"] in ps_to_sc:
            already.append({"item": it["id"], "candidate": ps_to_sc[it["id"]]})
            continue
        title = str(it.get("title") or "").strip()
        if title in unowned_titles and iid not in acknowledge:
            # D2's fail-LOUD guard. Provenance empirically SURVIVES the model-mediated ship->archive
            # move (79/79 archived candidates retained source[]), but 79/79 is a SNAPSHOT, not an
            # invariant (slice-050's lesson), and that archive copy stays model-mediated and unenforced.
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
                # a dep is satisfiable iff it is already materialized OR is being minted in this batch
                if d in ps_to_sc:
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
            "withheld": withheld, "orphaned": orphaned, "ps_to_sc": ps_to_sc}


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

        ps_to_sc = dict(plan["ps_to_sc"])
        seed = id_allocator.seed_max_for(vault, "sc", data)
        for it in plan["to_mint"]:                       # pass 1: mint ids, in topological order
            iid = it["id"]
            if iid in ps_to_sc:                          # CR1 belt: identity ambiguity NEVER mints
                raise _Refuse(1, "malformed",
                              f"scope item id {iid!r} appears twice in the batch -- refusing to mint, "
                              f"since both records would collapse onto one candidate id.")
            ps_to_sc[iid] = id_allocator.next_id(data, "sc", seed_max=seed)

        minted = []
        for it in plan["to_mint"]:                       # pass 2: build records; deps now all resolvable
            deps = [ps_to_sc[d] for d in (it.get("depends_on") or []) if d in ps_to_sc]
            rec = _candidate_from(it, ps_to_sc[it["id"]], deps, ts)
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
    items_in = _load_items(args.items_file)

    for it in items_in:
        iid = it.get("id")
        if iid is not None and str(iid) not in existing:
            raise _Refuse(
                2, "usage",
                f"scope item carries id {iid!r}, which this vault never minted. `revise` may REUSE an "
                f"allocator-minted PS id (that is how an item is preserved across a revision) but may "
                f"never INVENT one -- identity is minted by the receiver. Omit `id` for a new item.",
            )
    # CR1: rejecting an INVENTED id is not enough -- a REPEATED one aliases two items onto one minted
    # candidate. _load_items already ran _check_identities; this is the boundary that made it necessary.
    _check_contract(items_in)
    _check_deps(items_in, args.items_file)      # CR3: a dep dropped from the revision STOPS, never drops
    ts = _now()
    holder: dict = {}

    def mutate(text: str) -> str:
        data = json.loads(text) if text.strip() else {}
        cur = {str(i.get("id")): i for i in data.get("items") or [] if isinstance(i, dict)}
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
        holder["items"] = out_items
        holder["dropped"] = [i for i in cur if i not in {o["id"] for o in out_items}]
        return _dump(data)

    safe_mutate_text(vault / SCOPE_FILE, mutate)
    items = holder["items"]
    mat = _materialize(vault, items, dry_run=False, acknowledge=set(), ts=ts)
    return {
        "action": "revise", "status": "ok",
        "items": len(items),
        "preserved": [i["id"] for i in items if str(i["id"]) in existing],
        # A scope item DROPPED from a revised concept leaves its candidate UNTOUCHED (the backlog is
        # append-only and the candidate may already be shipped). materialize reports it as `orphaned`
        # and takes NO action -- the owner-deletion cascade the k8s frame wanted is not even expressible.
        "dropped": holder["dropped"],
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
    else:
        items = [i for i in _scope(vault, required=True).get("items") or [] if isinstance(i, dict)]
    return _materialize(vault, items, dry_run=bool(args.dry_run),
                        acknowledge=set(args.acknowledge or []), ts=_now())


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

    sub.add_parser("census", parents=[common],
                   help="classify live u archive into PRODUCT / EXHAUST / HUMAN / unclassified")
    return p


_DISPATCH = {
    "decompose-context": cmd_decompose_context,
    "persist": cmd_persist,
    "materialize": cmd_materialize,
    "revise": cmd_revise,
    "census": cmd_census,
}


def _text(out: dict) -> str:
    a = out.get("action")
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
