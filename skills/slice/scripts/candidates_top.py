"""candidates_top.py — ranked digest of the live slice backlog for /slice (v2, NEW).

Single-skill injection tool for `/slice` Step "Live state". Reads the unified
`<vault>/candidates.json` backlog and prints the top-N PICKABLE candidates, ranked
by priority, with blocked-on-spike + unmet-dependency flags + blast-radius **coupling**
(other live candidates touching the same code area — surfaced at pick time, not just in
the DAG topo-sort; overlap with an in-flight slice is a conflict risk; roadmap Theme 4) —
so /slice can "recommend the next cut" without re-running a multi-source fan-out (rollout #3: the
single candidates.json IS the pre-ranked, pre-materialized source of truth, replacing
v1's scattered backlog.md / risk-register-as-candidate-source / slice-queue.md).

NET-NEW in v2 (v1 had no unified candidates backlog). Read-only — never mutates the
vault; claiming the pick is `claim_candidate.py`'s job.

Classification (LIVE-file statuses, per schemas/slice-candidates.example.json):

  candidate | deferred  -> PICKABLE      (ranked into the main list)
  blocked              -> blocked-on-spike (own section; needs a fallback re-spike,
                          NOT a fresh pick). Also any candidate carrying a blocking
                          assumption whose spike_status == "failed".
  spiking | active     -> in-flight      (claimed; parallel slices are normal — not
                          re-picked, surfaced as a one-line consult)

A dependency is UNMET iff it is still LIVE in candidates.json — a shipped dependency
is MOVED out to archive/candidates.json, so its ABSENCE from the live file == satisfied.

A `reserved` soft HOLD older than 24h is additionally flagged [STALE HOLD] (aged from
`started_at`): an abandoned pre-claim pick has no TTL and would otherwise stay invisible
to every other picker forever; /slice offers the same-owner `--release`.

Vault root: `--vault ROOT` overrides `$AI_SDLC_VAULT_ROOT` / the computed default
(mirrors vault_edit's `_root`). Exit 0 success (INCLUDING the normal "no backlog yet"
early state — never breaks the injection), 1 runtime error (malformed/unexpected JSON),
2 usage error (bad CLI args).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout, product_priority
from scripts.lib._vault_paths import VAULT_ROOT

_PICKABLE = {"candidate", "deferred"}
_BLOCKED = {"blocked"}
_IN_FLIGHT = {"spiking", "active", "reserved"}  # slice-027: a `reserved` soft HOLD is claimed-in-intent (in-flight), never re-pickable
# effort -> sort rank (smaller cut first on a score tie). Unknown effort sorts last.
_EFFORT_RANK = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}
# A `reserved` soft HOLD older than this is flagged STALE: an abandoned pre-claim pick has no
# TTL and no cleanup sweep, so without this flag it stays invisible to other pickers forever.
_STALE_HOLD_HOURS = 24.0


# ── helpers ─────────────────────────────────────────────────────────────────────

def _root(vault_arg: str | None) -> Path:
    """The vault root: ``--vault`` when given, else the resolved ``VAULT_ROOT``."""
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _priority(cand: dict) -> dict:
    pr = cand.get("priority")
    return pr if isinstance(pr, dict) else {}


def _score(cand: dict) -> float:
    s = _priority(cand).get("score")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else 0.0


def _effort(cand: dict) -> str:
    return str(_priority(cand).get("effort") or "").strip()


def _effort_rank(cand: dict) -> int:
    return _EFFORT_RANK.get(_effort(cand).upper(), 9)


# ── slice-077 (SC-138 / ADR-088): the product-priority path-class term at the pick surface ──

def _effective_score(cand: dict) -> float:
    """Base severity score + the product-priority path-class term (score space). REUSES `_priority`'s
    isinstance guard so a non-dict priority (the live SC-152/SC-153 shape) fail-SAFEs to term 0 and
    NEVER raises (M4). A demote CO-CONSTRAINT violation still raises DemoteCoConstraintError (caught in
    main -> fail-visible + exit 1); a non-dict priority alone does not — the two are independent."""
    base = _score(cand)
    pc = product_priority.path_class(cand)          # raises ONLY on a half-written demote
    if not isinstance(cand.get("priority"), dict):
        return base                                 # non-dict priority -> term 0 (fail-safe)
    return base + product_priority.product_term(pc)


def _demote_reason(cand: dict) -> str:
    return str(cand.get("demote_reason") or "").strip()


def _is_off_path(cand: dict) -> bool:
    """True iff this candidate is explicitly demoted. Safe AFTER a clean sort (no surviving
    co-constraint violation), which is the only place the formatters run."""
    return product_priority.path_class(cand) == product_priority.OFF_PATH


def _blast(cand: dict) -> str:
    return str(_priority(cand).get("blast_radius") or "").strip()


def _blast_segments(cand: dict) -> set[str]:
    """Normalized blast-radius directory segments. build_backlog joins touched dirs with
    ', ' (e.g. 'src/api, src/db'); two candidates sharing a segment touch the same code area.
    Used to surface file-overlap coupling at slice-pick time (roadmap Theme 4)."""
    out: set[str] = set()
    for seg in re.split(r"\s*,\s*", _blast(cand)):
        s = seg.strip().strip("/").replace("\\", "/").lower()
        if s and s not in ("(isolated)", "isolated"):
            out.add(s)
    return out


def _coupling(cand: dict, all_live: list[dict]) -> list[dict]:
    """Other LIVE candidates that share a blast-radius segment with ``cand`` — file-overlap
    coupling the DAG topo-sort hid. An overlap with an IN-FLIGHT candidate is a parallel-slice
    conflict risk (same files, two worktrees); surface it first. Read-only, candidates.json-only
    (no CRG re-query — reuses the coupling build_backlog already computed into blast_radius)."""
    mine = _blast_segments(cand)
    if not mine:
        return []
    cid = cand.get("id")
    out: list[dict] = []
    for other in all_live:
        if other is cand or other.get("id") == cid:
            continue
        shared = mine & _blast_segments(other)
        if shared:
            out.append({"id": other.get("id"), "shared": sorted(shared),
                        "in_flight": _classify(other) == "in_flight"})
    out.sort(key=lambda d: (not d["in_flight"], str(d["id"])))  # in-flight (conflict risk) first
    return out


def _hold_age_hours(cand: dict) -> float | None:
    """Age in hours of a `reserved` soft HOLD (None unless reserved with a parseable started_at)."""
    if cand.get("status") != "reserved":
        return None
    raw = cand.get("started_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0)


def _fmt_age(hours: float) -> str:
    return f"{hours / 24:.0f}d" if hours >= 48 else f"{hours:.0f}h"


def _failed_blocking_assumption(cand: dict) -> dict | None:
    """The first blocking assumption whose spike has FAILED (None if none)."""
    for a in cand.get("assumptions") or []:
        if isinstance(a, dict) and a.get("blocking") and a.get("spike_status") == "failed":
            return a
    return None


def _classify(cand: dict) -> str:
    """pickable | blocked | in_flight | other (shipped/rejected — not in a live file)."""
    st = cand.get("status")
    if st in _BLOCKED or _failed_blocking_assumption(cand) is not None:
        return "blocked"
    if st in _IN_FLIGHT:
        return "in_flight"
    if st in _PICKABLE:
        return "pickable"
    return "other"


def _unmet_deps(cand: dict, live_ids: set[str]) -> list[str]:
    """Dependencies still LIVE in candidates.json (== not yet shipped/archived)."""
    return [d for d in (cand.get("dependencies") or []) if d in live_ids]


# ── the completion gate (slice-102 / SC-232) ─────────────────────────────────────
#
# `/slice` had no notion of what is still MISSING for the app to be usable, so on an EXHAUSTED product
# scope -- the steady state of every project past its initial capability list, because the decomposition
# is a ONCE-act by design -- it fell through to score-ranked pipeline exhaust BY CONSTRUCTION. This is
# the residency check that was missing (completion_gap.py carries the frame). It is an OPT-IN flag, not
# a layer below the consumer: DD1 measured that an ALWAYS-present banner breaks slice-080/m5's
# test-pinned contract that the default-OFF payload adds NO top-level key, while the opt-in shape
# `--area`/`area_lens` already uses is GO 5/5. So the gate is BYPASSABLE BY DESIGN (INV-1 `fails`,
# recorded honestly rather than claimed away), and `/slice`'s injection always passes the flag with a
# doc-guard pinning that it does.


def _fmt_gap_banner(gap: dict) -> list[str]:
    """The TEXT header half of the gate. Renders the verdict, the HONEST headline, the route and the
    always-offered decline."""
    out = ["", "COMPLETION GAP (app completeness at the pick surface):"]
    if "verdict" not in gap:
        # FAIL-VISIBLE, and never a silent degrade to `scope-absent` (must-not-defer #1).
        out.append(f"  UNDECIDABLE ({gap['cause_kind']}): {gap['error']}")
        out.append("  The ranked list below is still correct -- only the completeness verdict is "
                   "withheld. Fix the named file and re-run /slice.")
        return out
    rec = gap["recommendation"]
    out.append(f"  verdict: {gap['verdict']} ({gap['reason']})")
    out.append(f"  {gap['headline']}")
    if gap["dangling"]:
        out.append(f"  dangling owner_ref(s) -- reported, never retracted: "
                   f"{', '.join(str(d['candidate']) for d in gap['dangling'])}")
    out.append(f"  -> {rec['mode']}: {rec['rationale']}")
    if rec["offer_decline"]:
        out.append("  Declining is always offered and returns the ordinary ranked pick below.")
    return out


def _fmt_pick_row(row: dict) -> list[str]:
    meta = f"score {row['score']:g}  effort {row['effort'] or '?'}"
    if row["blast_radius"]:
        meta += f"  blast: {row['blast_radius']}"
    if row["deps_unmet"]:
        # the gate must not STRIP a pick-time warning the ordinary digest already shows ([[ADR-148]] d5)
        meta += f"  [deps-unmet: {', '.join(row['deps_unmet'])}]"
    return ["",
            f"Completion pick (rank {row['rank']} of {row['of']}) -- hoisted by the completion gate:",
            f"  {row['id']}  {row['title']}",
            f"       {meta}"]


def _project_ranked_rows(ranked: list[tuple[dict, list[str]]], sources: dict) -> list[dict]:
    """THE PROJECTION, at the render point. It is what makes completion_gap's "imports nothing" TRUE
    rather than asserted: the classifier receives plain dicts and never reaches back into this module,
    `product_scope`, or `product_priority`.

    `unmet_deps` is the SECOND element of the `(candidate, unmet_deps)` tuples this list already holds.
    Dropping it would let the gate hoist a pick whose prerequisite has not shipped, stripping the
    `[deps-unmet: ...]` marker the ordinary digest renders.
    """
    from scripts.lib import product_scope
    return [{
        "id": c.get("id"),
        "title": c.get("title"),
        "score": _score(c),
        "effective_score": _effective_score(c),
        "effort": _effort(c) or None,
        "rank": i,
        # membership for `pickable_product[]` is `owner_ref is not None`, NEVER path_class ([[ADR-148]]
        # d9): path_class returns OFF_PATH for a DEMOTED candidate before it ever tests owner_ref, and
        # it is per-candidate, so blocked/in-flight product rows are `on-path` while belonging to
        # neither array.
        "owner_ref": product_scope.owner_ref(c),
        "area_source": sources.get(str(c.get("id"))),
        "unmet_deps": list(unmet),
    } for i, (c, unmet) in enumerate(ranked, 1)]


def _live_capture(cands) -> dict:
    """ONE population, as a `{id: status}` map. BOTH mid-read captures use THIS shape over THIS file --
    [[ADR-148]] d1, replacing ADR-146 d13's pair, which compared live-only against live UNION archive
    (measured 119 vs 224 on a QUIESCENT vault: the gate would have refused on every run, shipping
    INERT). One population, two reads, genuinely independent."""
    return {str(c.get("id")): c.get("status") for c in (cands or []) if isinstance(c, dict)}


def _scope_ids(vault: Path) -> set[str]:
    """The scope's id set. An unreadable scope yields an EMPTY set here -- reporting it is the ROLLUP's
    job (cause_kind `rollup-error`), not this capture's."""
    p = vault / "product-scope.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(i.get("id")) for i in (data.get("items") or [])
            if isinstance(i, dict) and i.get("id")}


def _scope_shape_error(vault: Path) -> str | None:
    """A present-but-STRUCTURALLY-WRONG scope, named rather than degraded (must-not-defer #1).

    THE SILENT DEGRADE THIS CLOSES (code-review m1). `product-scope.json` can be perfectly valid JSON
    and still be the wrong SHAPE -- `items` a dict, a string, or a list of non-dicts. Every reader then
    yields ZERO capabilities without raising: `_scope_item_ids` and `cmd_done` both filter non-dicts, so
    they AGREE on the empty set, `build_envelope`'s id-set guard passes, and the rollup reports
    `total: 0`. The gate would map that to `empty-scope` -> `route-add-item` and tell the user "every
    declared capability is built" about a file it could not read. A corrupt scope must never read as a
    FINISHED product.
    """
    p = vault / "product-scope.json"
    if not p.exists():
        return None                       # a genuinely absent scope is the NORMAL scope-absent verdict
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                       # unparseable -> compute_rollup owns it (rollup-error)
    if not isinstance(data, dict):
        return f"{p} top-level is not a JSON object"
    items = data.get("items")
    if items is None:
        return None                       # legal: a scope file that carries no items key yet
    if not isinstance(items, list):
        return f"{p} `items` is {type(items).__name__}, not an array"
    bad = [i for i, it in enumerate(items) if not isinstance(it, dict)]
    if bad:
        return (f"{p} `items` carries {len(bad)} non-object entr(y/ies) at index {bad[:5]} -- every "
                f"reader silently drops them, so the product would read as SMALLER than it is")
    return None


def _reread_live(vault: Path) -> list:
    """The SECOND explicit read of candidates.json for the cross-check. A file that vanished or turned
    unparseable between the captures yields an EMPTY population, which the cross-check then REPORTS as
    a delta -- visible, never swallowed."""
    p = vault / "candidates.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return (data.get("candidates") or []) if isinstance(data, dict) else []


def _derive_completion_gap(vault: Path, cands: list, ranked: list, area, explicit_intent: bool,
                           sources: dict, area_known: bool | None = None) -> dict:
    """Build the whole `completion_gap` payload. ALL IO and ALL projection live HERE, at the call site.

    Read-only by construction: `/slice`'s PICK PHASE takes no lock and mutates no vault file
    ([[ADR-067]] section 1 as scoped by [[ADR-080]] + [[ADR-152]]).
    """
    import completion_gap as gap_lib
    from scripts.lib import product_rollup, product_scope

    cand_a = _live_capture(cands)          # capture A -- the backlog read this function was handed
    scope_a = _scope_ids(vault)

    shape = _scope_shape_error(vault)
    if shape:
        return gap_lib.error_envelope(
            f"the product scope is present but structurally invalid -- {shape}. Every reader drops the "
            f"unreadable entries silently, so the completeness verdict would describe a product this "
            f"file does not actually declare. Fix the file and re-run.", "scope-malformed")

    env = dict(product_rollup.compute_rollup(vault))
    # `done_definition` is supplied by the CALLER on EVERY branch, including `scope_present: false`,
    # which neither build_envelope nor _error_envelope covers. That keeps the constant single-sourced,
    # so SC-233's redefinition of what `done` MEANS cannot leave a stale copy in the classifier.
    env.setdefault("done_definition", product_rollup.DONE_DEFINITION)

    # THE ORPHANED DERIVATION IS GUARDED (round-3 M-add-1 / [[ADR-148]] d4). `cmd_materialize` resolves
    # items via `_scope(required=True)` and RAISES `_Refuse(4, no-scope)` on the branch this design
    # declares NORMAL, and `_Refuse(1, malformed)` on a corrupt one. `main()` has no handler and the
    # `!`-injection carries no `||` fallback, so an escaping exception would render a traceback exactly
    # where the ranked digest belongs.
    try:
        plan = product_scope.cmd_materialize(
            vault, SimpleNamespace(scope_file=None, dry_run=True, acknowledge=[]))
        orphaned = plan.get("orphaned") or []
    except product_scope._Refuse as exc:
        if getattr(exc, "code", None) != 4:
            return gap_lib.error_envelope(
                f"the product scope could not be read while deriving dangling owner_refs: {exc}",
                "scope-malformed")
        orphaned = []                       # no scope -> no orphans; continue to the NORMAL verdict
    except Exception as exc:                # a genuine compute failure is still fail-VISIBLE
        return gap_lib.error_envelope(
            f"the dangling-owner_ref derivation failed: {exc}", "scope-malformed")

    # live-filter the orphans HERE: `cands` is the only input carrying the live set ([[ADR-148]] d7),
    # so this is the only place the filter can honestly happen.
    live_ids = {str(c.get("id")) for c in cands if isinstance(c, dict)}
    orphaned = [{"candidate": o.get("candidate"), "ref": o.get("ref")} for o in orphaned
                if str(o.get("candidate")) in live_ids]

    # capture B -- the SAME populations, read again at the END of the derivation
    delta = gap_lib.cross_check(scope_a, _scope_ids(vault))
    if delta:
        return gap_lib.error_envelope(
            f"the product-scope item set changed while the completion gate was reading it ({delta}); "
            f"refusing a possibly-miscounted verdict", "scope-changed-mid-read")
    delta = gap_lib.cross_check(cand_a, _live_capture(_reread_live(vault)))
    if delta:
        return gap_lib.error_envelope(
            f"candidates.json changed while the completion gate was reading it ({delta}); refusing a "
            f"possibly-miscounted verdict", "candidates-changed-mid-read")

    if area is not None and env.get("capabilities") is not None:
        # the WHOLE population is scoped: done/total are COUNTED FROM this filtered array, never read
        # from `whole_app`, which is never area-scoped ([[ADR-148]] d6)
        scoped = [cap for cap in env["capabilities"] if cap["area"] == area]
        # THE TYPO TRAP (code-review M2). A MISSPELLED `--area` filters `capabilities[]` to empty; the
        # classifier maps `total == 0` to `empty-scope`, and the route table sends `empty-scope` to
        # `route-add-item` -- so a typo would tell the user "every declared capability is built" and
        # point them at the ONE irreversible act in this module. `area_lens.known` was computed sixty
        # lines earlier and never consulted. It is now, and an area with nothing to be complete ABOUT
        # is UNDECIDABLE, never FINISHED. Covers the known-but-capability-less area too (an area only a
        # candidate asserts): the same wrong answer, from a spelling that is not a typo.
        if not scoped:
            why = ("is not a known area on this vault -- check the spelling"
                   if area_known is False else
                   "carries no product capabilities, so there is nothing for it to be complete about")
            return gap_lib.error_envelope(
                f"--area {area!r} {why}. Refusing a completeness verdict rather than reporting an "
                f"empty filter as a finished product.", "area-unresolvable")
        env["capabilities"] = scoped
    env["area_scope"] = area

    ranked_rows = _project_ranked_rows(ranked, sources)
    payload = gap_lib.classify(env, orphaned, ranked_rows)
    if "verdict" not in payload:
        return payload
    rec = gap_lib.recommend(payload, ranked_rows, explicit_intent=explicit_intent)
    payload["recommendation"] = rec
    payload["suppress_governor"] = gap_lib.suppress_governor(rec["mode"])
    if rec["pick_id"] is not None:
        row = next((r for r in ranked_rows if r["id"] == rec["pick_id"]), None)
        cand = next((c for c, _ in ranked if c.get("id") == rec["pick_id"]), None)
        if row is not None and cand is not None:
            payload["pick_row"] = {
                "id": row["id"], "title": row["title"], "score": row["score"],
                "effective_score": row["effective_score"], "effort": row["effort"],
                "blast_radius": _blast(cand) or None, "deps_unmet": row["unmet_deps"],
                "rank": row["rank"], "of": len(ranked_rows),
            }
    return payload


# ── formatting ───────────────────────────────────────────────────────────────────

def _fmt_text(project: str, ranked: list[tuple[dict, list[str]]],
              blocked: list[dict], in_flight: list[dict], top: int,
              all_live: list[dict], area_lens: dict | None = None,
              completion_gap: dict | None = None) -> str:
    lines: list[str] = []
    lines.append(
        f"CANDIDATES (live backlog: {project}) — "
        f"{len(ranked)} pickable, {len(blocked)} blocked-on-spike, "
        f"{len(in_flight)} in-flight"
    )
    if area_lens is not None:
        # slice-080/ADR-091 (slice-084 renamed component→area): the lens filters ONLY pickable (m2);
        # blocked/in-flight stay global. slice-098/ADR-125: the population is now every candidate with an
        # AREA SOURCE — its own asserted `area` or a product-scope parent — not product-sourced only.
        name = area_lens["area"]
        if area_lens["known"]:
            lines.append(f"  [area lens: {name} — pickable filtered to this area (candidates that "
                         f"assert it, plus product capabilities bound to it); blocked/in-flight "
                         f"shown globally]")
        else:
            known = ", ".join(area_lens.get("areas") or []) or "none"
            lines.append(f"  [area lens: {name} — UNKNOWN area, 0 pickable. "
                         f"Known: {known}]")
        near = area_lens.get("near_matches") or []
        if near:
            # critique m2: a case/spacing variant of a REAL area splits one area's picks across two
            # buckets, both of which read as known. Surfaced at the pick surface, never silently filtered.
            lines.append(f"       WARN: {name!r} case-matches known area(s) "
                         f"{', '.join(repr(n) for n in near)} but is not byte-equal — the picks for "
                         f"this area are SPLIT across both spellings. Re-annotate to one spelling.")

    # slice-102 / SC-232: the completion-gap banner rides the TEXT header, not only --json. This
    # module's own note above pins the premise: EVERY production invocation of this digest is text-mode
    # (the /slice `!`-injection), so a verdict that existed only in --json would be a verdict the pick
    # surface never shows -- and a gate whose reasoning is invisible trains the user to click through
    # it (must-not-defer #5).
    if completion_gap is not None:
        lines.extend(_fmt_gap_banner(completion_gap))

    lines.append("")
    if ranked:
        shown = ranked[:top] if top > 0 else ranked
        lines.append(f"Top picks (ranked, {len(shown)} of {len(ranked)}):")
        for i, (cand, unmet) in enumerate(shown, 1):
            cid = cand.get("id", "?")
            title = cand.get("title", "")
            if _is_off_path(cand):
                # M5: surface the override so the ordering is explicable + auditable at pick time.
                meta = (f"score {_score(cand):g} -> {_effective_score(cand):g} "
                        f"[demoted: {_demote_reason(cand)}]  effort {_effort(cand) or '?'}")
            else:
                meta = f"score {_score(cand):g}  effort {_effort(cand) or '?'}"
            if _blast(cand):
                meta += f"  blast: {_blast(cand)}"
            if unmet:
                meta += f"  [deps-unmet: {', '.join(unmet)}]"
            if area_lens is not None:
                # M6 — render the resolved area SOURCE on the TEXT path. Every production invocation of
                # this digest is text-mode (the /slice `!`-injection), so a provenance that exists only
                # in --json is a provenance the pick surface never shows. `candidate` = the candidate
                # asserted this area itself and it OVERRODE any derived value ([[ADR-124]] section 1);
                # `product-scope` = derived through its single PS parent.
                src = (area_lens.get("sources") or {}).get(str(cid))
                if src:
                    meta += f"  area: {area_lens['area']} (via {src})"
            lines.append(f"  {i}. {cid}  {title}")
            lines.append(f"       {meta}")
            if cand.get("description"):
                lines.append(f"       {cand['description']}")
            if cand.get("user_visible_outcome"):
                lines.append(f"       -> {cand['user_visible_outcome']}")
            coup = _coupling(cand, all_live)
            if coup:
                shown_coup = [
                    f"{c['id']} ({', '.join(c['shared'])})"
                    + (" [IN-FLIGHT: conflict risk]" if c["in_flight"] else "")
                    for c in coup[:5]
                ]
                lines.append(f"       couples-with: {'; '.join(shown_coup)}")
    else:
        lines.append("No pickable candidates (none in {candidate, deferred}).")

    # The hoisted completion pick, in its OWN labelled section carrying its REAL rank. It is NEVER
    # renumbered into `Top picks`, whose documented meaning as a PREFIX of the ranking is preserved --
    # and it must be hoisted at all because a score-5 / effort-L product mint measures ~11th of 117 on
    # a real vault, outside any --top 5 window (INV-3: one filled page restores RESIDENCY, not RANK).
    if completion_gap is not None and completion_gap.get("pick_row"):
        lines.extend(_fmt_pick_row(completion_gap["pick_row"]))

    if blocked:
        lines.append("")
        lines.append("Blocked on spike (needs a fallback re-spike, NOT a fresh pick):")
        for cand in blocked:
            a = _failed_blocking_assumption(cand) or {}
            ev = a.get("spike_evidence") or ""
            line = f"  {cand.get('id', '?')}  {cand.get('title', '')}"
            if ev:
                line += f"  — {ev}"
            lines.append(line)
            if a.get("fallback"):
                lines.append(f"       fallback: {a['fallback']}")

    if in_flight:
        lines.append("")
        lines.append("In-flight (claimed; parallel slices are normal — do not re-pick):")
        for cand in in_flight:
            who = (cand.get("claimed_by") or {}).get("git_user") or "?"
            # slice-027 / M-add-1: a `reserved` hold has minted no slice number yet -- show 'held'
            # rather than a bare '?' so the In-flight row reads coherently (slice=null is expected).
            slc = cand.get("slice") or ("held" if cand.get("status") == "reserved" else "?")
            prog = cand.get("progress") or "?"
            lines.append(
                f"  {cand.get('id', '?')}  {cand.get('title', '')}  [{slc}, {prog}, by {who}]"
            )
            age = _hold_age_hours(cand)
            if age is not None and age >= _STALE_HOLD_HOURS:
                lines.append(
                    f"       [STALE HOLD: reserved {_fmt_age(age)} ago, never claimed -- if abandoned, "
                    f"release it: claim_candidate.py --candidate {cand.get('id', '?')} --release]"
                )

    return "\n".join(lines) + "\n"


def _fmt_json(project: str, ranked: list[tuple[dict, list[str]]],
              blocked: list[dict], in_flight: list[dict], top: int,
              all_live: list[dict], area_lens: dict | None = None,
              completion_gap: dict | None = None) -> str:
    shown = ranked[:top] if top > 0 else ranked
    payload = {
        "action": "candidates-top",
        "project": project,
        "counts": {
            "pickable": len(ranked),
            "blocked": len(blocked),
            "in_flight": len(in_flight),
        },
        "top": [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "score": _score(c),
                "effective_score": _effective_score(c),   # M5: the sort key's actual value
                "path_class": product_priority.path_class(c),
                "demote_reason": _demote_reason(c) or None,
                "effort": _effort(c) or None,
                "blast_radius": _blast(c) or None,
                "deps_unmet": unmet,
                "couples_with": _coupling(c, all_live),
            }
            for c, unmet in shown
        ],
        "blocked": [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "evidence": (_failed_blocking_assumption(c) or {}).get("spike_evidence"),
                "fallback": (_failed_blocking_assumption(c) or {}).get("fallback"),
            }
            for c in blocked
        ],
        "in_flight": [_in_flight_entry(c) for c in in_flight],
    }
    if area_lens is not None:
        # slice-080/ADR-091 (slice-084 renamed component→area): added ONLY when the lens is active, so
        # the default-OFF payload is byte-identical to today (critique m5).
        payload["area_lens"] = area_lens
    if completion_gap is not None:
        # slice-102 / SC-232, the SAME additive discipline: present ONLY under --completion-gap, so
        # `test_default_off_payload_is_unperturbed`'s contract (no new top-level key) still holds.
        payload["completion_gap"] = completion_gap
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _in_flight_entry(c: dict) -> dict:
    age = _hold_age_hours(c)
    return {
        "id": c.get("id"),
        "title": c.get("title"),
        "slice": c.get("slice"),
        "progress": c.get("progress"),
        "by": (c.get("claimed_by") or {}).get("git_user"),
        "stale_hold": age is not None and age >= _STALE_HOLD_HOURS,
        "held_hours": round(age, 1) if age is not None else None,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="candidates_top",
        description="Ranked digest of the live slice backlog (<vault>/candidates.json) "
                    "for /slice. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--top", type=int, default=5,
                   help="how many ranked picks to show (<=0 = all; default 5)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON (default: human-readable text for prompt injection)")
    p.add_argument("--area", dest="area", default=None, metavar="NAME",
                   help="OPTIONAL area lens (slice-080/ADR-091; slice-084 renamed from --component; "
                        "slice-098/ADR-125 widened the population): filter the PICKABLE list to every "
                        "candidate with an AREA SOURCE resolving to this area — one that ASSERTS the "
                        "area itself (its own `area` field, which OVERRIDES any derived value) or a "
                        "PRODUCT capability bound to it via owner_refs. Use 'unassigned' for product "
                        "capabilities with no area yet; an annotated candidate can never land there "
                        "(the write seams refuse the sentinel), and an UN-annotated chore has no area "
                        "source and stays out entirely. Blocked/in-flight stay global context. "
                        "Read-only — takes no lock, mints no id, writes no status; default-OFF is "
                        "byte-identical.")
    p.add_argument("--component", dest="area", default=None, metavar="NAME",
                   help="deprecated alias of --area (slice-084)")
    p.add_argument("--completion-gap", dest="completion_gap", action="store_true",
                   help="OPT-IN completion gate (slice-102/SC-232): classify the pick surface for APP "
                        "COMPLETION -- product-work-available | scope-exhausted | scope-absent -- and "
                        "emit the verdict, the honest done/total headline, the unbuilt capability list "
                        "and a routed recommendation, in BOTH the text header and --json. Hoists the "
                        "recommended product pick into its own labelled section even when it falls "
                        "outside --top N. Read-only: takes no lock, mints no id, writes nothing. "
                        "Default-OFF is byte-identical.")
    p.add_argument("--explicit-intent", dest="explicit_intent", action="store_true",
                   help="the user already named the work (`/slice \"<description>\"`), so the gate "
                        "renders its headline and raises NO question. The alert-fatigue carve-out; "
                        "only meaningful with --completion-gap.")
    return p


def _emit_no_backlog(path: Path, as_json: bool, completion_gap: dict | None = None) -> int:
    if as_json:
        payload = {
            "action": "candidates-top", "project": None, "note": "no-backlog",
            "counts": {"pickable": 0, "blocked": 0, "in_flight": 0},
            "top": [], "blocked": [], "in_flight": [],
        }
        if completion_gap is not None:
            payload["completion_gap"] = completion_gap
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(
            f"No candidates.json yet at {path} — run /discover (slice 1) or "
            f"/slice-candidates (brownfield) to populate the backlog."
        )
        if completion_gap is not None:
            print("\n".join(_fmt_gap_banner(completion_gap)))
    return 0


def _backlog_absent_envelope(path: Path, why: str) -> dict:
    """An absent or EMPTY candidates.json is an ERROR, never an empty pick surface (must-not-defer #7).

    Keyed on the CONDITION at main()'s backlog-load branch -- absent OR zero-byte OR `candidates`
    missing OR an empty list -- and NEVER on the `note: "no-backlog"` marker, which does not fire on
    `{"candidates": []}` at all (executed: rc 0, no note key). This branch does NOT call classify: with
    no backlog there is no pick surface to classify, and fabricating one would be the silent degrade
    the gate exists to prevent (wiring matrix row 1 / [[ADR-148]] d10).
    """
    import completion_gap as gap_lib
    return gap_lib.error_envelope(
        f"{path} {why}. The completion verdict is UNDECIDABLE without a backlog to classify -- an "
        f"empty pick surface is not 'nothing to do', it is a broken vault. Run /discover (slice 1) or "
        f"/slice-candidates to populate it.", "candidates-absent")


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success (incl. empty/absent backlog), 1 runtime error, 2 usage error."""
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    vault = _root(args.vault)
    path = vault / "candidates.json"

    # THE BACKLOG-LOAD BRANCH (slice-102). Under --completion-gap each of the four negative states below
    # emits the verdict-LESS `candidates-absent` envelope alongside the digest's own shipped rendering.
    # The exit code is UNTOUCHED (0/1/2): blanking the ranked digest -- the surface the user needs in
    # order to pick at all -- would be strictly worse than the failure being reported.
    def _absent(why: str) -> dict | None:
        return _backlog_absent_envelope(path, why) if args.completion_gap else None

    if not path.exists():
        return _emit_no_backlog(path, args.json, _absent("does not exist"))
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return _emit_no_backlog(path, args.json, _absent("is zero-byte"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"candidates_top: {path} is not valid JSON: {exc}\n")
        return 1
    if not isinstance(data, dict):
        sys.stderr.write(f"candidates_top: {path} top-level is not a JSON object\n")
        return 1

    cands = data.get("candidates")
    gap_absent = None
    if cands is None:
        cands = []
        gap_absent = _absent("carries no `candidates` key")
    if not isinstance(cands, list):
        sys.stderr.write(f"candidates_top: {path} 'candidates' is not a JSON array\n")
        return 1
    if not cands and gap_absent is None:
        # `{"candidates": []}` does NOT reach _emit_no_backlog (executed: rc 0, no `note` key), which
        # is precisely why the condition is tested HERE rather than on that marker.
        gap_absent = _absent("carries an EMPTY `candidates` array")

    project = data.get("project") or "<unnamed>"
    live_ids = {c.get("id") for c in cands if isinstance(c, dict)}

    pickable: list[dict] = []
    blocked: list[dict] = []
    in_flight: list[dict] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        bucket = _classify(c)
        if bucket == "pickable":
            pickable.append(c)
        elif bucket == "blocked":
            blocked.append(c)
        elif bucket == "in_flight":
            in_flight.append(c)

    # coupling spans EVERY live candidate — snapshot it BEFORE the lens filter so the conflict signal
    # stays global even when the pickable list is narrowed to one component (slice-080 m2).
    all_live = pickable + blocked + in_flight

    # slice-080/ADR-091 (slice-084 renamed component→area): the OPTIONAL --area lens filters ONLY
    # pickable (blocked/in-flight stay global). Read-only: it reads product-scope.json for the
    # {PS-id: area} map and joins each candidate via owner_refs (ambiguous multi-parent -> 'unassigned'
    # — M-add-1). Default-OFF (arg None) leaves everything untouched: no import, no filter, no payload key.
    #
    # slice-084 A1 (source-scoping): the area lens is a PRODUCT view, so it restricts to product-sourced
    # candidates FIRST (owner_refs non-empty). Without this, `--area unassigned` returned the pipeline-
    # exhaust chores too — every one of which maps to 'unassigned' because it carries no product-scope
    # source — conflating the declared product capabilities with ~88 chores. The lens now answers "which
    # product capability comes next in area X", never "everything the pipeline happens to have queued".
    # slice-098 / SC-212 ([[ADR-125]] section 1): the admission predicate widens from `owner_refs`
    # non-empty to `has_area_source` — an own valid `area` OR a product-scope parent. slice-084 A1's
    # anti-conflation rationale SURVIVES: `owner_refs`-non-empty was only ever a PROXY for "has a real
    # area source", and `_valid_area` REFUSES the reserved `unassigned` sentinel at every write seam, so
    # an annotated candidate can never resolve into the residual bucket the ~88-chore leak flowed through.
    # An un-annotated chore still has no source and still stays out. ALL area logic is delegated to
    # area_resolve so no second precedence rule can appear here (spike-A1 constraint 4).
    area_lens = None
    if args.area is not None:
        from scripts.lib import area_resolve, product_rollup
        area_map = product_rollup.read_area_map(_root(args.vault))
        # `known` unions the PS areas with the areas candidates ASSERT for themselves — without the
        # widening a freshly-annotated area reads `known: false` and the surface says "UNKNOWN area"
        # about an area that demonstrably exists.
        known = (set(area_map.values()) | {product_rollup.UNASSIGNED}
                 | area_resolve.asserted_areas(all_live))
        resolved = {}
        kept = []
        for c in pickable:
            if not area_resolve.has_area_source(c):
                continue
            area, source = area_resolve.resolve(c, area_map)
            if area == args.area:
                kept.append(c)
                resolved[str(c.get("id"))] = source
        pickable = kept
        area_lens = {"area": args.area, "known": args.area in known,
                     "areas": sorted(known),
                     # critique m2 — the read-time half of the split-bucket signal: a known area that
                     # casefold-matches the request without being byte-equal. Advisory, never a filter.
                     "near_matches": area_resolve.near_matches(args.area, known),
                     # M6 — per-pick provenance {id: candidate|product-scope}. The ONLY thing that
                     # distinguishes a candidate whose asserted area SHADOWS its capability's, which is
                     # ADR-124 section 1's accepted masking cost mitigated "by visibility, not refusal".
                     "sources": resolved}

    # Rank pickable: highest EFFECTIVE score (severity + product-priority term, slice-077) first,
    # then smaller effort, then id (stable). A demote co-constraint violation raises here and is
    # CAUGHT below -> fail-visible message naming the id + exit 1 (M4), never a raw traceback that
    # blinds the /slice injection (a non-dict priority stays injection-safe: it fail-safes to term 0).
    try:
        pickable.sort(key=lambda c: (-_effective_score(c), _effort_rank(c), str(c.get("id"))))
        ranked = [(c, _unmet_deps(c, live_ids)) for c in pickable]
        blocked.sort(key=lambda c: str(c.get("id")))
        in_flight.sort(key=lambda c: str(c.get("id")))
        # THE RENDER POINT (slice-102). classify() is called exactly ONCE, HERE, with `ranked_rows`
        # projected from the very list the digest below is rendered from -- so the gate's verdict and
        # the list the user reads can never come from two different populations.
        gap = None
        if args.completion_gap:
            gap = gap_absent if gap_absent is not None else _derive_completion_gap(
                vault, cands, ranked, args.area, args.explicit_intent,
                (area_lens or {}).get("sources") or {},
                area_known=(area_lens or {}).get("known"))
        fmt = _fmt_json if args.json else _fmt_text
        out = fmt(project, ranked, blocked, in_flight, args.top, all_live, area_lens, gap)
    except product_priority.DemoteCoConstraintError as exc:
        sys.stderr.write(
            f"candidates_top: candidate {exc.candidate_id!r} has a half-written demote ({exc}) — "
            f"fix the candidate's demoted_at/demote_reason pair; refusing to emit a mis-ranked backlog.\n")
        return 1
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
