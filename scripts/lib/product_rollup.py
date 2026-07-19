#!/usr/bin/env python3
"""product_rollup.py — read-only capability-progress rollup + area-lens support (slice-080; slice-084 renamed component→area).

SC-165 / [[ADR-091]]. A DERIVED VIEW, never stored: it REUSES product_scope.cmd_done as the SINGLE
done-derivation (called in-process via a synthetic args(item=None) shim — DD1 spike proved it cleanly
library-callable, no refactor of high-fan-in cmd_done), joins each capability to its product-scope
`area`, and reports done/pending counts in CAPABILITIES (never slices) at whole-app + per-area
level. The module writes NOTHING.

Epidemiological point-prevalence discipline (the cross-domain frame, ADR-091): the product-scope items
are the POPULATION register (the denominator — the artifact itself, not a stored count); `done` is the
fixed CASE DEFINITION computed by cmd_done; `area` is the STRATIFIER with a mandatory explicit
'unassigned' stratum so no subject is ever dropped; and the strata CONSERVE (sum per-area ==
whole-app). Rates are recomputed every read, never cached.

Findings this pins (all accepted-pending at TRI-1, slice-080):
  * M1 — the FULL 4-state cmd_done output is mapped, not a lossy 3-key one: cmd_done's
    done|in-progress|no-children|unknown become the 5-way partition done|rejected_only|in_progress|
    no_children|unknown at BOTH whole-app and per-area levels, and the strata conserve to `total`.
  * M2 — cmd_done marks state='done' when every child is archived REGARDLESS of shipped vs rejected, so
    a rejected-only capability would read 'done'. The rollup re-buckets a 'done' capability whose
    archived children are all rejected (0 shipped) into `rejected_only`, so it never inflates the
    headline 'done' count. archived_composition is carried forward at every stratum.
  * M3 — a PRESENT-but-empty scope (items:[] => total==0) surfaces as a distinct `empty_scope` state
    ('0 capabilities decomposed yet'), NEVER the forbidden '0/0 done'.
  * M4 — cmd_done runs IN-PROCESS, so it cannot be exit-code-keyed like slice-078's subprocess. No
    product-scope is the COMMON case, so it is a clean {scope_present:false} (pre-checked existence +
    belt-and-braces catch of _Refuse.code==4), never a cry-wolf error; only a GENUINE compute failure
    routes to a fail-visible `error` riding stdout (exit-0-always).
  * m4 — product-scope.json is read twice non-atomically (cmd_done's read + the area-map read);
    a parallel revise between them is caught by an item-id-set mismatch that degrades to `error`,
    never a silently-miscounted rate.
  * m3 — stdout is reconfigured to UTF-8 + json.dumps(ensure_ascii=False): an `area` is a
    free user-authored string that may be non-ASCII (the Windows cp1252 default would raise/mojibake).
  * M-add-1 — candidate_area() (the /slice lens join) uses owner_refs (ambiguity-safe), NOT
    owner_ref: a candidate claiming TWO product-scope parents lands in 'unassigned' (matching cmd_done's
    'unknown'), never silently filed under its first parent.
  * M-add-2 — area ordering is a pinned TOTAL order: least-complete-first (completion ratio ASC),
    then most-central (|capabilities| DESC), then name ASC for determinism; each carries its `rank`.
  * M-add-3 — the envelope carries a pre-rendered `pulse_line` that ALWAYS names the done_definition
    ('materialized candidate archived'), so no /pulse mode (incl --brief) can emit a bare 'X/Y done'.

Contract — a stable stdout envelope, EXIT 0 ALWAYS:

    {"scope_present": bool,
     "unit": "capabilities", "done_definition": "materialized candidate archived",
     "empty_scope": bool,
     "whole_app": {done, rejected_only, in_progress, no_children, unknown, total, composition},
     "areas": [{name, ...counts, total, composition, rank, ratio}],
     "unassigned": {name, ...counts, total, composition},
     "pulse_line": str,
     "error"?: str}

Read-only: writes nothing (AC5 / AC2).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path
from types import SimpleNamespace

# --- shared-lib import bootstrap (a bundled script cannot use `python -m`) ---
_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout, product_scope
from scripts.lib._vault_paths import VAULT_ROOT

SCOPE_FILE = product_scope.SCOPE_FILE
# slice-081/ADR-092 (critique m1): single-source the reserved catch-all sentinel from product_scope, so
# the write-seam validator (product_scope._valid_area, which REJECTS it) and this read-side catch-all
# bucket cannot drift. Value-preserving (product_scope.UNASSIGNED == 'unassigned'), so slice-080's rollup
# byte-identity holds. Import-safe: product_rollup already depends on product_scope, never the reverse.
UNASSIGNED = product_scope.UNASSIGNED
UNIT = "capabilities"
DONE_DEFINITION = "materialized candidate archived"

# The 5-way partition. cmd_done is 4-valued (done | in-progress | no-children | unknown); the rollup
# splits 'done' into genuinely-shipped `done` vs `rejected_only` (M2). Every capability lands in exactly
# ONE, so the strata conserve to `total`.
_STATES = ("done", "rejected_only", "in_progress", "no_children", "unknown")


# ── the case-definition mapping (cmd_done 4-state -> the rollup 5-way partition) ──────────────

def _bucket(entry: dict) -> str:
    """One cmd_done entry -> one _STATES stratum. Total function; 'unknown' is the safe default."""
    state = entry.get("state")
    if state == "done":
        comp = entry.get("archived_composition") or {}
        shipped = comp.get("shipped") or 0
        rejected = comp.get("rejected") or 0
        if shipped > 0:
            return "done"                     # at least one child genuinely shipped
        if rejected > 0:
            return "rejected_only"            # killed, not delivered — must not inflate 'done' (M2)
        return "done"                         # archived, neither shipped/rejected — conservative, not rejected
    if state == "in-progress":
        return "in_progress"
    if state == "no-children":
        return "no_children"
    return "unknown"                          # 'unknown' or any unexpected state -> the safe stratum


def _new_strata() -> dict:
    d = {s: 0 for s in _STATES}
    d["total"] = 0
    d["composition"] = {"shipped": 0, "rejected": 0}
    return d


def _add(strata: dict, entry: dict, bucket: str) -> None:
    strata[bucket] += 1
    strata["total"] += 1
    comp = entry.get("archived_composition") or {}
    strata["composition"]["shipped"] += comp.get("shipped") or 0
    strata["composition"]["rejected"] += comp.get("rejected") or 0


# ── the stratifier (area) join ──────────────────────────────────────────────────────────

def ps_area_map(scope: dict) -> dict[str, str]:
    """{PS-id: area} for items carrying a NON-EMPTY STRING area (slice-084: renamed from ps_component_map;
    a legacy `component` key is still read as a back-compat alias). A missing/blank/non-string value
    simply omits the item from the map, so it lands in the mandatory 'unassigned' stratum — never a crash
    (must-not-defer: a malformed area degrades to unassigned)."""
    out: dict[str, str] = {}
    for it in scope.get("items") or []:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        area = it.get("area")
        if not (isinstance(area, str) and area.strip()):
            area = it.get("component")           # slice-084 back-compat alias
        if iid and isinstance(area, str) and area.strip():
            out[str(iid)] = area.strip()
    return out


def _scope_item_ids(scope: dict) -> set[str]:
    return {str(it.get("id")) for it in (scope.get("items") or [])
            if isinstance(it, dict) and it.get("id")}


def candidate_area(cand: dict, area_map: dict[str, str]) -> str:
    """The AREA a CANDIDATE belongs to, for the /slice lens (M-add-1; slice-084 renamed from
    candidate_component).

    Uses product_scope.owner_refs — the ambiguity-SAFE plural — mirroring cmd_done: a candidate claiming
    MORE THAN ONE product-scope parent is ambiguous and lands in 'unassigned' (never silently filed under
    its first parent, the winner-pick owner_ref's own docstring refuses everywhere else). A candidate
    with no product-scope parent, or whose single parent carries no area, is 'unassigned' too."""
    refs = product_scope.owner_refs(cand)
    if len(refs) != 1:                         # 0 parents, or ambiguous 2+ -> unassigned (== cmd_done 'unknown')
        return UNASSIGNED
    return area_map.get(refs[0], UNASSIGNED)


def read_area_map(vault: Path) -> dict[str, str]:
    """{PS-id: area}, read-only + absence-tolerant (no/malformed scope -> {}). For the lens
    (slice-084 renamed from read_component_map)."""
    p = Path(vault) / SCOPE_FILE
    if not p.exists():
        return {}
    try:
        scope = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return ps_area_map(scope if isinstance(scope, dict) else {})


# ── ordering (M-add-2) + conservation (M1 / AC5) ─────────────────────────────────────────────

def _ratio(st: dict) -> float:
    """Completion ratio: genuinely-shipped `done` over the full population of the stratum. total>=1
    for any real area (an area exists only if >=1 capability maps to it), so no /0."""
    return (st["done"] / st["total"]) if st["total"] else 0.0


def _order_areas(by_name: dict[str, dict]) -> list[dict]:
    """Pinned TOTAL order (M-add-2): least-complete-first (ratio ASC), then most-central
    (|capabilities| DESC), then name ASC for determinism. Each carries its 1-based `rank`."""
    ordered = sorted(by_name.items(), key=lambda kv: (_ratio(kv[1]), -kv[1]["total"], kv[0]))
    out: list[dict] = []
    for rank, (name, st) in enumerate(ordered, 1):
        out.append({"name": name, **st, "rank": rank, "ratio": round(_ratio(st), 4)})
    return out


def _conserves(whole: dict, area_list: list[dict], unassigned: dict) -> bool:
    """Denominator conservation: sum of per-area + unassigned == whole, per state AND total; and
    the whole's states sum to its own total. A breach is a compute error, never a silently-wrong rate."""
    for s in (*_STATES, "total"):
        if unassigned[s] + sum(c[s] for c in area_list) != whole[s]:
            return False
    return sum(whole[s] for s in _STATES) == whole["total"]


# ── envelope assembly ─────────────────────────────────────────────────────────────────────────

def _error_envelope(msg: str) -> dict:
    """Fail-visible: scope_present True so /pulse WARNs (never a silent no-line); the signal rides
    stdout (exit stays 0). must-not-defer: never silently render 0/0 or swallow a compute error."""
    return {"scope_present": True, "unit": UNIT, "done_definition": DONE_DEFINITION,
            "error": msg, "pulse_line": f"Product shape unavailable — {msg}"}


def _pulse_line(env: dict) -> str:
    """A pre-rendered headline that ALWAYS names the done_definition (M-add-3), so no /pulse mode can
    emit a bare 'X/Y done'. Empty when there is no product scope (the line is omitted upstream)."""
    if not env.get("scope_present"):
        return ""
    if env.get("empty_scope"):
        return "Product shape: scope present, 0 capabilities decomposed yet"
    w = env["whole_app"]
    done, total = w["done"], w["total"]
    extra = []
    if w["rejected_only"]:
        extra.append(f"{w['rejected_only']} rejected-only")
    not_done = total - done - w["rejected_only"]
    if not_done:
        extra.append(f"{not_done} unbuilt")
    suffix = f"; {', '.join(extra)}" if extra else ""
    return f"Whole app {done}/{total} capabilities done ({DONE_DEFINITION}{suffix})"


# ── the completeness governor (slice-084 B4) ──────────────────────────────────────────────────
#
# The tracker being HONEST (0/N) turned out not to be enough: slice-077 added a product-priority ranking
# term and the product STILL sat at 0/4 while seven consecutive slices built instrumentation around it.
# The governor is the VISIBLE nudge — a string emitted ONLY when the product's scope IS decomposed
# (total >= 1) but ZERO of its capabilities are built (done == 0). /pulse + /slice render it as a WARN and
# point at `/slice --area` to pick a real capability. The key is OMITTED once anything is built, or when
# the scope is empty/absent — so a healthy product carries no governor (byte-identical to pre-B4 there).
# It is descriptive only: it changes no ranking and blocks no pick (never a lexicographic dominator —
# spike-product-priority-a2's constraint that the product signal is WEIGHTED, never overriding).

def _governor_line(env: dict) -> str | None:
    w = env.get("whole_app") or {}
    total, done = int(w.get("total") or 0), int(w.get("done") or 0)
    if total >= 1 and done == 0:
        cap = "capability" if total == 1 else "capabilities"
        return (f"the product's scope is decomposed into {total} {cap}, but 0 are built — the pipeline is "
                f"shipping instrumentation while the product itself sits at 0/{total}. Pick a real "
                f"capability next: /slice --area <NAME> (a product-sourced candidate), not another chore.")
    return None


def build_envelope(done_result: dict, scope: dict) -> dict:
    """PURE: assemble the rollup envelope from a cmd_done result + the persisted scope. No IO."""
    items = done_result.get("items") or []
    area_map = ps_area_map(scope)
    scope_ids = _scope_item_ids(scope)
    done_ids = {str(e.get("item")) for e in items}
    # m4: cmd_done's scope read and the area-map read are non-atomic. If a parallel revise changed
    # the item id set between them, refuse a possibly-miscounted rollup rather than emit a false rate.
    if done_ids != scope_ids:
        return _error_envelope(
            f"product-scope item set changed between reads (cmd_done saw {len(done_ids)} item(s), "
            f"the area map saw {len(scope_ids)}); refusing a possibly-miscounted rollup")

    whole = _new_strata()
    by_area: dict[str, dict] = {}
    unassigned = _new_strata()
    for e in items:
        b = _bucket(e)
        area = area_map.get(str(e.get("item")))       # capability -> area: DIRECT + total-function
        _add(whole, e, b)
        _add(by_area.setdefault(area, _new_strata()) if area else unassigned, e, b)

    area_list = _order_areas(by_area)
    env = {
        "scope_present": True,
        "unit": UNIT,
        "done_definition": DONE_DEFINITION,
        "empty_scope": whole["total"] == 0,       # M3: present-but-empty is distinct, never '0/0 done'
        "whole_app": whole,
        "areas": area_list,
        "unassigned": {"name": UNASSIGNED, **unassigned},
    }
    if not _conserves(whole, area_list, unassigned):
        return _error_envelope("strata-sum conservation breached — refusing a silently-wrong rate")
    env["pulse_line"] = _pulse_line(env)
    gov = _governor_line(env)                     # slice-084 B4: present ONLY when scope decomposed but 0 built
    if gov:
        env["governor"] = gov
    return env


def compute_rollup(vault: Path) -> dict:
    """IO entry point: derive the rollup for a vault. Exit-0-always semantics (any error rides stdout)."""
    vault = Path(vault)
    # M4: no product-scope -> a CLEAN {scope_present:false}, NOT an error (this is the common /pulse
    # path). Pre-check existence so the in-process cmd_done never raises its _Refuse(4).
    if not (vault / SCOPE_FILE).exists():
        return {"scope_present": False, "pulse_line": ""}
    try:
        done_result = product_scope.cmd_done(vault, SimpleNamespace(item=None))
        scope = product_scope._scope(vault, required=True)
    except product_scope._Refuse as exc:
        # Belt-and-braces: a scope that vanished between the pre-check and the call is no-scope (code 4,
        # silent omit); any OTHER refusal is a genuine failure surfaced fail-visible.
        if getattr(exc, "code", None) == 4:
            return {"scope_present": False, "pulse_line": ""}
        return _error_envelope(f"product_scope refused ({getattr(exc, 'code', '?')}): {exc}")
    except Exception as exc:                     # genuine compute failure -> fail-visible, never a silent 0/0
        return _error_envelope(f"rollup compute failed: {exc}")
    return build_envelope(done_result, scope)


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────

def _render_text(env: dict) -> str:
    if not env.get("scope_present"):
        return "product rollup: no product-scope.json — nothing to roll up."
    if env.get("error"):
        return f"product rollup: ERROR — {env['error']}"
    lines = [env["pulse_line"]]
    if env.get("governor"):
        lines.append(f"  WARN (completeness governor): {env['governor']}")
    for c in env["areas"]:
        lines.append(
            f"  [{c['rank']}] {c['name']}: {c['done']}/{c['total']} done "
            f"({c['in_progress']} in-progress, {c['no_children']} no-children, "
            f"{c['rejected_only']} rejected-only, {c['unknown']} unknown)")
    u = env["unassigned"]
    if u["total"]:
        lines.append(f"  unassigned / cross-cutting: {u['done']}/{u['total']} done ({u['total']} caps)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()            # m3: an area name may be non-ASCII
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    ap.add_argument("--json", action="store_true", help="emit JSON (default: human-readable text)")
    args = ap.parse_args(argv)
    vault = Path(args.vault) if args.vault else VAULT_ROOT
    env = _error_envelope("could not resolve the vault root") if not vault else compute_rollup(Path(vault))
    print(json.dumps(env, ensure_ascii=False) if args.json else _render_text(env))  # m3: ensure_ascii=False
    return 0                                      # exit-0-always: the error rides stdout (mirrors slice-078)


if __name__ == "__main__":
    raise SystemExit(main())
