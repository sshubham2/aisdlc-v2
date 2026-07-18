#!/usr/bin/env python3
"""story_inputs.py — project the product_rollup envelope into slice-story's own read model (slice-082).

SC-184 / [[ADR-093]]. A per-consumer READ MODEL (CQRS / anti-corruption-layer framing): /slice-story
consumes the capability-progress rollup (scripts/lib/product_rollup.py, shipped slice-080 / ADR-091) but
its presentation contract is the exact INVERSE of /pulse's — 'translate, never transcribe', zero pipeline
jargon on the page. So instead of reusing /pulse's presentation-shaped envelope, this helper PROJECTS the
envelope down to a jargon-stripped numeric substrate, DROPPING /pulse's presentation fields (pulse_line,
done_definition). The projection is pure + unit-testable; the numbers never pass through the narrator.

Findings this pins (all accepted-pending at TRI-1, slice-082):
  * M2 — a SINGLE fetch+project invocation (this module's `project`/`inject` do product_rollup.compute_rollup
    THEN project in one call), and the launch-failure `|| echo` guard emits the PROJECTION shape
    ({"state":"error",...}), never /pulse's envelope shape (which has no `state` key the degrade reads).
  * M3 — product_rollup's error envelope and no-scope return OMIT whole_app/components/unassigned (keys
    ABSENT). project_rollup_for_story branches error -> no_scope -> empty_scope BEFORE it ever reads
    whole_app, so a degenerate envelope can never KeyError (must-not-defer: never crash the narrator/renderer).
  * m3 — the substrate carries `in_progress` per stratum (not just done/total), so an actively-in-progress
    component is distinguishable from an untouched one in a section whose point is per-component PROGRESS.
  * M-add-1 — the substrate is the AUTHORITATIVE numeric block render_story renders DETERMINISTICALLY; the
    `inject` verb writes it into story-sections.json on the MAIN THREAD (never via the sonnet narrator), so
    the counts a stakeholder reads are code-rendered, not LLM-transcribed. The narrator authors only prose.

The substrate (or the degrade states) — this is the contract render_story._render_product_shape consumes:

    populated              {state, unit, whole_app:{done,in_progress,total},
                            components:[{name,done,in_progress,total,rank}], unassigned:{done,in_progress,total}}
    degenerate_unassigned  as populated but components:[] + a `note` (every capability unassigned — the COMMON
                            live case until SC-183 annotates components; framed honestly, never 'no progress')
    empty_scope            {state, unit, note}          (scope present, 0 capabilities decomposed yet)
    no_scope               {state}                       (render omits the section entirely)
    error                  {state, error}                (fail-visible note; render never errors)

Exit-0-always for `project` (any compute error rides stdout as {"state":"error",...}); `inject` exits
non-zero ONLY on an io failure (unreadable/unwritable story-sections.json) so the skill can surface it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- single-skill import bootstrap (a bundled script cannot use `python -m`) ---
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout, product_rollup  # noqa: E402

UNIT = "capabilities"
# CR1: the provenance marker inject stamps onto product_shape; render_story renders a block ONLY when it
# carries this exact value, so narrator-authored counts (which never carry it) are never rendered.
INJECT_SOURCE = "story_inputs.inject"
_EMPTY_NOTE = "A product shape is defined, but no capabilities have been broken out into it yet."
_UNASSIGNED_NOTE = ("Progress isn't broken down by area yet — every capability is still "
                    "unassigned to a component.")


# ── the projection (pure — the whole AC1 unit-test surface) ──────────────────────────────────

def _stratum(src: dict) -> dict:
    """One rollup stratum -> the jargon-free {done, in_progress, total} the story renders. Total-function:
    a missing/None count reads as 0, so a partial envelope never raises (must-not-defer: never crash)."""
    src = src if isinstance(src, dict) else {}
    return {
        "done": int(src.get("done") or 0),
        "in_progress": int(src.get("in_progress") or 0),  # m3: distinguish in-flight from untouched
        "total": int(src.get("total") or 0),
    }


def project_rollup_for_story(env: dict) -> dict:
    """Project product_rollup's envelope down to slice-story's jargon-stripped numeric substrate.

    M3 BRANCH ORDER is load-bearing: error -> no_scope -> empty_scope are each recognised BEFORE any
    read of whole_app/components/unassigned, because product_rollup's `error` and no-scope returns OMIT
    those keys entirely (a naive env['whole_app'] would KeyError and crash the narrator). DROPS /pulse's
    presentation fields (pulse_line, done_definition) at source (M-add-1 / ADR-093)."""
    if not isinstance(env, dict):
        return {"state": "error", "error": "malformed rollup envelope (not an object)"}
    # (1) genuine compute/launch failure — fail-visible, never a silent empty section (must-not-defer).
    if env.get("error"):
        return {"state": "error", "error": str(env.get("error"))}
    # (2) no product scope at all — the section is omitted downstream (not an error).
    if not env.get("scope_present"):
        return {"state": "no_scope"}
    # (3) scope present but nothing decomposed — a distinct honest note, never '0/0 done'.
    if env.get("empty_scope"):
        return {"state": "empty_scope", "unit": env.get("unit") or UNIT, "note": _EMPTY_NOTE}
    # (4) populated — NOW it is safe to read whole_app/components/unassigned.
    components = [
        {"name": str(c.get("name", "")), **_stratum(c), "rank": int(c.get("rank") or 0)}
        for c in (env.get("components") or []) if isinstance(c, dict)
    ]
    sub = {
        "state": "populated" if components else "degenerate_unassigned",
        "unit": env.get("unit") or UNIT,
        "whole_app": _stratum(env.get("whole_app") or {}),
        "components": components,
        "unassigned": _stratum(env.get("unassigned") or {}),
    }
    if not components:
        # m1 / the COMMON live case (component join deferred to SC-183): frame it honestly.
        sub["note"] = _UNASSIGNED_NOTE
    return sub


def compute_and_project(vault: Path) -> dict:
    """IO entry: derive the rollup for a vault (product_rollup's exit-0-always compute) then project."""
    return project_rollup_for_story(product_rollup.compute_rollup(Path(vault)))


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────

def _render_text(sub: dict) -> str:
    state = sub.get("state")
    if state == "no_scope":
        return "product shape: no product-scope.json — the story omits the 'where this fits' section."
    if state == "error":
        return f"product shape: ERROR — {sub.get('error')}"
    if state == "empty_scope":
        return f"product shape: {sub.get('note')}"
    w = sub["whole_app"]
    lines = [f"whole app: {w['done']}/{w['total']} built ({w['in_progress']} in progress)"]
    for c in sub["components"]:
        lines.append(f"  [{c['rank']}] {c['name']}: {c['done']}/{c['total']} built "
                     f"({c['in_progress']} in progress)")
    if state == "degenerate_unassigned":
        lines.append(f"  {sub.get('note')}")
    return "\n".join(lines)


def _cmd_project(args) -> int:
    vault = Path(args.vault) if args.vault else product_rollup.VAULT_ROOT
    if not vault:
        sub = {"state": "error", "error": "could not resolve the vault root"}
    else:
        sub = compute_and_project(Path(vault))
    print(json.dumps(sub, ensure_ascii=False) if args.json else _render_text(sub))
    return 0                                       # exit-0-always: any compute error rides stdout


def _cmd_inject(args) -> int:
    """Deterministically merge the projected substrate into story-sections.json (M-add-1: the counts are
    written on the MAIN THREAD, never authored by the narrator). io failure -> non-zero so the skill surfaces
    it (never a silent drop); a compute error still injects the fail-visible {state:'error'} substrate."""
    sf = Path(args.sections_file)
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"story_inputs inject: cannot read {sf}: {e}\n")
        return 2
    if not isinstance(data, dict):
        sys.stderr.write(f"story_inputs inject: {sf} is not a JSON object\n")
        return 2
    vault = Path(args.vault) if args.vault else product_rollup.VAULT_ROOT
    sub = compute_and_project(Path(vault)) if vault else {"state": "error",
                                                          "error": "could not resolve the vault root"}
    # CR1: stamp deterministic provenance so render_story renders ONLY main-thread-injected counts. If this
    # inject step ever fails, an UNSTAMPED product_shape a narrator might have authored (against its persona
    # rule) will NOT render as authoritative counts — the M-add-1 guarantee no longer rests on inject
    # succeeding AND the narrator obeying its prompt.
    sub["_source"] = INJECT_SOURCE
    data["product_shape"] = sub
    try:
        sf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"story_inputs inject: cannot write {sf}: {e}\n")
        return 2
    print(f"story_inputs inject: product_shape state={sub.get('state')} -> {sf}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()              # a component name may be non-ASCII
    ap = argparse.ArgumentParser(prog="story_inputs", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_proj = sub.add_parser("project", help="fetch+project the rollup, print the substrate (exit-0-always)")
    p_proj.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    p_proj.add_argument("--json", action="store_true", help="emit JSON (default: human-readable text)")
    p_proj.set_defaults(func=_cmd_project)

    p_inj = sub.add_parser("inject", help="merge the projected substrate into a story-sections.json (main-thread)")
    p_inj.add_argument("--sections-file", required=True, help="path to the story-sections.json to inject into")
    p_inj.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    p_inj.set_defaults(func=_cmd_inject)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
