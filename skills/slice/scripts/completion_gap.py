"""completion_gap.py — classify the /slice pick surface for APP COMPLETION (slice-102 / SC-232).

THE DEFECT. `/slice` had no notion of what is still MISSING for the app to be usable. Its only product
signal was a flat mint-time `score: 5` stamped by `product_scope.PRODUCT_PRIORITY`, and the pick-time
path-class term is deliberately INERT at 0 (`product_priority.product_term`). Because
`/slice-candidates --product` decomposes the product's scope EXACTLY ONCE by design — a re-decomposition
would re-mint ~78% duplicate sludge — an EXHAUSTED scope is the steady state of any project past its
initial capability list, and in that state the pipeline carries zero app-completion signal and falls
through to score-ranked pipeline exhaust BY CONSTRUCTION.

Every pre-existing backstop misses that state: the `PRODUCT == 0` census notice does not fire (PRODUCT
is non-zero), the completeness governor (`product_rollup._governor_line`) fires ONLY at 0-built, and
`/slice --area` has nothing to filter on while every capability is `unassigned`.

THE FRAME (designer-crossdomain, selected at synthesis). This is NOT a ranking defect — a ranking
function is a total order over a SET and cannot rank an ABSENT item, which is exactly why the on-path
term is inert and why SC-234 cannot fix it. It is a MISSING RESIDENCY CHECK, whose solved form is the
demand-paging page fault: trap at the ACCESS (not on a schedule), hand the fault to a handler at a
DIFFERENT authority level (the receiver mints identity, never the model), fill exactly ONE page (bulk
re-prefetch is thrashing), and resume by ADDRESS rather than by re-ranking (a score-5 mint measures
~11th of 117 on a real vault, outside any --top 5 window). Two of the analogy's invariants FAIL here and
are recorded as failing rather than assumed: the check is BYPASSABLE (an opt-in flag, not a layer below
the consumer — INV-1), and a fill is NOT reversible (there is no un-mint — INV-4, compensated by
refuse-BEFORE-mint in `product_scope add-item`, never by verify-after).

A PURE LIBRARY MODULE ([[ADR-146]] decision 1) — no CLI, no ``main()``, no exit codes of its own, so
there is no second exit table to collide with `candidates_top`'s shipped 0/1/2 contract.

IT IMPORTS NOTHING, and that is TRUE rather than asserted: `candidates_top` PROJECTS every input at the
call site ([[ADR-148]] d5/d6/d7/d8) — `ranked_rows` (carrying `unmet_deps`), an ALREADY area-filtered
`capabilities[]` plus an explicit `area_scope`, an ALREADY live-filtered `orphaned`, and
`done_definition` on every branch, including the `scope_present: false` branch that neither
`build_envelope` nor `_error_envelope` covers. `done_definition` is NEVER a literal here, so SC-233's
redefinition of what `done` MEANS cannot leave a stale copy behind.

FAIL-VISIBLE IS A PAYLOAD PROPERTY, NEVER AN EXIT CODE ([[ADR-146]] d2). On an undecidable input the
`verdict` key is OMITTED ENTIRELY and `{error, cause_kind}` is present, so a consumer branching on it
KeyErrors LOUDLY rather than reading a fabricated `scope-absent` — which would say "this project has no
product" and SUPPRESS the very gate this module adds (must-not-defer #1). The process still exits 0:
blanking the ranked digest — the surface the user needs in order to pick at all — because a
PRODUCT-SCOPE file is corrupt is strictly worse than the failure being reported.
"""
from __future__ import annotations

# ── the declared vocabularies (each enum declared ONCE, every member with a producer) ──────────

VERDICTS = ("product-work-available", "scope-exhausted", "scope-absent")

#: verdict -> its permitted `reason` discriminators. `empty-scope` sits under `scope-exhausted`, NOT
#: under `scope-absent` ([[ADR-146]] d9, restoring INV-8's `holds`): MEASURED, the fill seam ACCEPTS a
#: present-but-empty scope (`cur` is empty, membership passes vacuously, the item mints) and REFUSES an
#: ABSENT one with exit 4 `no-scope`. A present-but-empty file is MAPPED-BUT-NOT-RESIDENT, not UNMAPPED,
#: so the VERDICT alone keeps selecting the handler.
REASONS = {
    "product-work-available": ("unbuilt-present",),
    "scope-exhausted": ("all-built", "none-pickable", "empty-scope"),
    "scope-absent": ("absent-no-file",),
}

#: Every member has exactly ONE producer, named here so the set cannot grow by accident ([[ADR-148]] d3).
#: `candidates-malformed` is deliberately RETIRED: `candidates_top` already exits 1 on malformed JSON
#: through its shipped contract, before this gate ever runs.
CAUSE_KINDS = (
    "candidates-absent",            # the backlog-load branch CONDITION (absent/zero-byte/missing/empty)
    "candidates-changed-mid-read",  # the two same-population captures of [[ADR-148]] d1
    "scope-changed-mid-read",       # the scope id-set check + product_rollup's own m4 guard
    "rollup-error",                 # compute_rollup's _error_envelope (corrupt scope OR a conservation breach)
    "scope-malformed",              # the _Refuse(1) branch of the caught orphaned derivation, AND a
                                    # present-but-structurally-wrong `items` (parseable JSON, wrong shape)
    "area-unresolvable",            # `--area` names an area with no capabilities to be complete ABOUT
)

MODES = ("product-pick", "route-add-item", "route-discover", "route-materialize",
         "route-coordinate", "route-repair", "route-rescope", "headline-only")

#: The modes that HALT `/slice` into a conversation. Every one of them offers the decline
#: (must-not-defer #3) and suppresses the completeness governor (M14), so exactly ONE instruction
#: reaches the pick surface.
HALTING_MODES = frozenset({"route-add-item", "route-discover", "route-materialize",
                           "route-coordinate", "route-repair", "route-rescope"})

#: MIXED `unbuilt[]` states route on the WORST state, ties broken by `capabilities[]` (rollup) order.
#: Ordered worst-first: "I cannot tell" outranks "it was killed" outranks "nothing was materialized"
#: outranks "someone is on it".
STATE_PRECEDENCE = ("unknown", "rejected_only", "no_children", "in_progress")

#: A killed capability needs a RE-DECISION, not a materialize/coordinate/repair (B2's 5th state).
ROUTE_BY_STATE = {
    "unknown": "route-repair",
    "rejected_only": "route-rescope",
    "no_children": "route-materialize",
    "in_progress": "route-coordinate",
}

#: The Step-5.7 gate row's `decision` enum ([[ADR-152]] d7, closing round-4 M2). `new-capability` is
#: RETIRED: the row is POST-CLAIM, and a user who follows ANY route-* mode — INCLUDING route-add-item —
#: leaves /slice WITHOUT claiming, so the member had no producer. MEASUREMENT RESIDUE, STATED: the
#: DECLINE branch stays measurable (it claims, then emits `decision: declined`); the ACCEPT branch is
#: NOT gate-log-measurable from /slice, so must-not-defer #6 is met for firing-vs-decline and NOT for
#: accept-rate. The durable record of an accept is the PS/SC mint itself.
GATE_DECISIONS = ("product-pick", "declined", "explicit-intent")

_ROUTE_TARGET = "/slice-candidates --add-item"


# ── helpers ───────────────────────────────────────────────────────────────────────────────────

def error_envelope(message: str, cause_kind: str) -> dict:
    """The verdict-LESS envelope. The `verdict` key is OMITTED, never set to a fourth value: a consumer
    branching on it must KeyError loudly rather than handle a state it may not know about.

    PUBLIC because the call site produces two of the five causes itself — `candidates-absent` is decided
    at `main()`'s backlog-load branch, BEFORE `classify` is reachable, and `scope-malformed` comes from
    the caught `_Refuse(1)` of the orphaned derivation.
    """
    assert cause_kind in CAUSE_KINDS, cause_kind      # the enum has one producer per member
    return {"error": message, "cause_kind": cause_kind}


def cross_check(before, after) -> str | None:
    """None when the two captures agree; else a message NAMING the delta.

    THE ROUND-3 BLOCKER this shape exists to prevent ([[ADR-148]] d1). [[ADR-146]] d13 named two capture
    points that measured DIFFERENT POPULATIONS — `candidates_top`'s live `cands` read against
    `product_scope._observed`'s live UNION archive. Reproduced independently by both reviewers at 119 vs
    224 on a QUIESCENT vault, so the gate would have emitted `candidates-changed-mid-read` on EVERY run
    and shipped INERT. The corrected pair is ONE population captured TWICE from the SAME file.

    Total over both shapes the caller uses: a `{id: status}` mapping (the candidate axis) and an id SET
    (the scope axis).
    """
    if before == after:
        return None
    b_keys, a_keys = set(before), set(after)
    added, removed = sorted(a_keys - b_keys), sorted(b_keys - a_keys)
    changed = []
    if isinstance(before, dict) and isinstance(after, dict):
        changed = sorted(k for k in b_keys & a_keys if before[k] != after[k])
    bits = []
    if added:
        bits.append(f"added {added}")
    if removed:
        bits.append(f"removed {removed}")
    if changed:
        bits.append(f"status changed for {changed}")
    return "; ".join(bits) or "the two captures differ"


def _worst_state(unbuilt) -> str:
    """The WORST `bucket` present, in the fixed precedence; ties broken by capabilities[] order because
    `unbuilt[]` preserves the rollup's own iteration order."""
    present = {c["bucket"] for c in unbuilt}
    for state in STATE_PRECEDENCE:
        if state in present:
            return state
    return "unknown"      # total function: an unrecognized bucket routes to the SAFE handler


def _headline(verdict, reason, done, total, done_definition, unbuilt, area_scope) -> str:
    """AC5 — HONEST about what `done` means, and naming the unbuilt capabilities from the SAME array
    `unbuilt[]` is derived from, so the headline and the list cannot disagree."""
    if verdict == "scope-absent":
        return ("No product scope has been decomposed yet, so nothing here records what this app is "
                "FOR — every pickable candidate is pipeline exhaust by construction.")
    where = f"Area {area_scope!r}" if area_scope else "Whole app"
    if reason == "empty-scope":
        head = f"{where}: product scope present, 0 capabilities decomposed yet"
    else:
        head = f"{where} {done}/{total} capabilities done ({done_definition})"
    if unbuilt:
        named = "; ".join(f"{c['id']} {c['title'] or ''}".rstrip() for c in unbuilt)
        head += f" -- {len(unbuilt)} unbuilt: {named}"
    return head


def suppress_governor(mode: str) -> bool:
    """True whenever the mode HALTS (M14). `product_rollup`'s completeness governor and this gate would
    otherwise deliver two contradictory instructions to the same surface at `done == 0`; the governor is
    the descriptive one, so it yields. Widening the governor's own firing condition is SC-233's work and
    is deliberately out of scope here — this only stops the two from colliding."""
    return mode in HALTING_MODES


def emits_gate_row(mode: str) -> bool:
    """The Step-5.7 row is POST-CLAIM ([[ADR-147]]), so a route-* mode legitimately emits NOTHING — the
    user left /slice without claiming, and the row's ABSENCE is the honest record."""
    return mode not in HALTING_MODES


# ── the classifier ────────────────────────────────────────────────────────────────────────────

def classify(rollup_envelope: dict, orphaned: list, ranked_rows: list) -> dict:
    """Classify the pick surface. NO IO, NO imports, and NO `explicit_intent` (round-2 m5: the verdict
    is a property of the VAULT, never of how `/slice` was invoked).

    ``rollup_envelope`` — `product_rollup`'s envelope, with `capabilities[]` ALREADY area-filtered, an
    explicit `area_scope` (`<NAME>` or None), and `done_definition` supplied on EVERY branch by the
    caller. `done`/`total` are COUNTED FROM `capabilities[]`, never read from `whole_app` (which is
    never area-scoped).
    ``orphaned``  — `_plan()`'s `{candidate, ref}` rows, ALREADY live-filtered by the caller (the only
    input carrying the live set, so the only place the filter can honestly happen).
    ``ranked_rows`` — the projected pick surface, carrying `owner_ref` and `unmet_deps`.
    """
    done_definition = rollup_envelope["done_definition"]   # KeyErrors LOUDLY if the caller forgot

    if rollup_envelope.get("error"):
        return error_envelope(str(rollup_envelope["error"]), "rollup-error")

    area_scope = rollup_envelope.get("area_scope")

    dangling = [{"candidate": o["candidate"], "ref": o["ref"]} for o in orphaned]
    # INV-9 (a stale mapping is DETECTED and never counted as resident). A dangling candidate points at
    # a capability the scope no longer carries -- `revise --cut` retires a PS id but leaves its
    # materialized candidate untouched, and the owner-deletion cascade "is not even expressible" -- so
    # it is a PERMANENT orphan. `dangling[]` single-sources it: the row is REPORTED (the backlog is
    # append-only; never retract a candidate, per ADR-089's mark-WITHOUT-sweep) and never counted as
    # available PRODUCT work, because the capability it claims to advance does not exist.
    _dangling_ids = {str(d["candidate"]) for d in dangling}

    # `pickable_product[]` membership is `owner_ref is not None`, NEVER `path_class == 'on-path'`
    # ([[ADR-148]] d9). path_class returns OFF_PATH for a DEMOTED candidate BEFORE it ever tests
    # owner_ref, and it is per-candidate, so BLOCKED and IN-FLIGHT product rows are `on-path` too while
    # belonging to neither array. `ranked_rows` is already the PICKABLE population, so membership here
    # is exactly "product-sourced AND pickable AND not dangling".
    pickable_product = [{"id": r["id"], "title": r["title"], "score": r["score"],
                         "effective_score": r["effective_score"], "rank": r["rank"]}
                        for r in ranked_rows
                        if r["owner_ref"] is not None and str(r["id"]) not in _dangling_ids]

    if not rollup_envelope.get("scope_present"):
        return {"verdict": "scope-absent", "reason": "absent-no-file",
                "done": 0, "total": 0, "done_definition": done_definition,
                "unbuilt": [], "pickable_product": pickable_product, "dangling": dangling,
                "area_scope": area_scope,
                "headline": _headline("scope-absent", "absent-no-file", 0, 0, done_definition, [],
                                      area_scope)}

    capabilities = rollup_envelope["capabilities"]
    total = len(capabilities)
    done = sum(1 for c in capabilities if c["bucket"] == "done")
    # THE MEMBERSHIP PREDICATE (round-2 B2): the rollup's OWN non-done set. Carrying both `state`
    # (cmd_done's four-valued) and `bucket` (the rollup's five-way stratum) is what makes a
    # `rejected_only` capability representable instead of falling out of `done` AND out of `unbuilt`.
    unbuilt = [{"id": c["item"], "title": c["title"], "state": c["state"], "bucket": c["bucket"]}
               for c in capabilities if c["bucket"] != "done"]

    if total == 0:
        verdict, reason = "scope-exhausted", "empty-scope"
    elif not unbuilt:
        # `all-built` can NEVER fire while done < total: `unbuilt` is empty iff every capability
        # buckets `done`, which is exactly `done == total`.
        verdict, reason = "scope-exhausted", "all-built"
    elif not pickable_product:
        verdict, reason = "scope-exhausted", "none-pickable"
    else:
        verdict, reason = "product-work-available", "unbuilt-present"

    return {"verdict": verdict, "reason": reason,
            "done": done, "total": total, "done_definition": done_definition,
            "unbuilt": unbuilt, "pickable_product": pickable_product, "dangling": dangling,
            "area_scope": area_scope,
            "headline": _headline(verdict, reason, done, total, done_definition, unbuilt,
                                  area_scope)}


# ── the recommendation (the declared route table) ─────────────────────────────────────────────

def _pick_id(envelope: dict, ranked_rows: list) -> str | None:
    """The highest-ranked PICKABLE PRODUCT candidate whose `unmet_deps` is EMPTY, falling back to the
    highest-ranked one WITH unmet deps only when none is clear ([[ADR-148]] d5).

    Two product candidates carry the SAME score and are split on EFFORT, so a small dependent otherwise
    outranks its own still-live prerequisite and the gate recommends blocked work. Membership comes from
    the envelope's `pickable_product[]`, so there is exactly ONE membership predicate.
    """
    member = {r["id"] for r in envelope["pickable_product"]}
    rows = sorted((r for r in ranked_rows if r["id"] in member), key=lambda r: r["rank"])
    if not rows:
        return None
    for r in rows:
        if not r["unmet_deps"]:
            return r["id"]
    return rows[0]["id"]


def recommend(envelope: dict, ranked_rows: list, *, explicit_intent: bool) -> dict:
    """The route table, keyed on (verdict, reason, worst-unbuilt-state, explicit_intent).

    Returns `{mode, pick_id, offer_decline, rationale}`. `offer_decline` is a RETURNED FIELD asserted on
    the RENDERED payload — that is what makes must-not-defer #3 (`the halt must stay OVERRIDABLE`)
    ENFORCEABLE rather than a sentence in prose. A gate that cannot be declined is a lock on the user's
    own backlog.

    Raises KeyError on an error envelope, deliberately: there is no verdict to route on.
    """
    verdict = envelope["verdict"]
    reason = envelope["reason"]

    if explicit_intent:
        # The alert-fatigue carve-out. INV-7 (`faults are RARE`) FAILS here on the HUMAN axis and is
        # measured rather than feared: once exhausted the verdict fires on EVERY pick. The user who
        # already named the work gets the headline and no question. Deliberately NO snooze/TTL — a
        # suppressible completion gate reinstates the exact "nobody is told" state this pipeline paid
        # 145 candidates to learn about.
        return _rec("headline-only", None, False,
                    f"Explicit intent supplied; rendering the completion headline only. {envelope['headline']}")

    if verdict == "product-work-available":
        pick = _pick_id(envelope, ranked_rows)
        return _rec("product-pick", pick, False,
                    f"The product still has unbuilt capabilities and {pick} is the highest-ranked "
                    f"pickable one with no unmet dependency. Selected BY IDENTITY, never by re-ranking.")

    if verdict == "scope-absent":
        # NEVER offers the mint: `add-item` exits 4 `no-scope` here (measured). The remedy is the BULK
        # map, which must not be replaced by a one-item fill or the project never gets a real scope.
        return _rec("route-discover", None, True,
                    "This project has no decomposed product scope at all, so the backlog can only ever "
                    "be pipeline exhaust. Run /discover (if concept.json is missing), then "
                    "/slice-candidates --product to decompose the product's scope ONCE.")

    # verdict == scope-exhausted
    if not envelope["unbuilt"]:
        # THE ONLY branch that reaches the irreversible mint -- and `/slice` ROUTES rather than
        # performing it ([[ADR-152]]). offer-mint was the only halting mode that did not route to a
        # user-invoked skill; making it symmetric with the other five dissolved three blockers.
        return _rec("route-add-item", None, True,
                    f"Every declared capability is built, so there is no app-completion work left to "
                    f"pick -- the ranked list below is pipeline exhaust. To add the next capability, "
                    f"run {_ROUTE_TARGET} (it elicits, previews and confirms before anything is "
                    f"minted). Declining returns the ordinary ranked pick.")

    worst = _worst_state(envelope["unbuilt"])
    mode = ROUTE_BY_STATE.get(worst, "route-repair")
    return _rec(mode, None, True, _NONE_PICKABLE_RATIONALE[mode])


_NONE_PICKABLE_RATIONALE = {
    "route-materialize": ("The product still has unbuilt capabilities, but NONE has a pickable "
                          "candidate: at least one capability has no children at all. Run "
                          "`product_scope materialize` (idempotent) to mint them."),
    "route-coordinate": ("The product still has unbuilt capabilities, but every one of them is "
                         "already IN FLIGHT in another slice. Coordinate with its owner rather than "
                         "starting a parallel cut on the same capability."),
    "route-repair": ("The product still has unbuilt capabilities, but at least one is in an UNKNOWN "
                     "state -- a child claims two parents, or its provenance is torn. Its child set "
                     "is undefined, so it cannot be called done or picked. Repair the provenance "
                     "(`product_scope done` names the offending candidates), then re-run /slice."),
    "route-rescope": ("The product still has unbuilt capabilities, but at least one was KILLED -- its "
                      "only archived children were rejected. A killed capability needs a re-decision, "
                      "not a materialize: revise the scope (`product_scope revise --cut ... --reason`) "
                      "or add the capability that replaces it."),
}


def _rec(mode: str, pick_id, offer_decline: bool, rationale: str) -> dict:
    return {"mode": mode, "pick_id": pick_id, "offer_decline": offer_decline,
            "rationale": rationale}
