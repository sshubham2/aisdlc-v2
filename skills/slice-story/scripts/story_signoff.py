#!/usr/bin/env python3
"""story_signoff.py — derive story.html's signoff panel from the trust ledger (slice-086 / [[ADR-102]]).

SC-145. A per-consumer READ MODEL (CQRS / anti-corruption-layer framing, sibling of story_inputs.py):
/slice-story's "Who has signed off" panel used to be AUTHORED by the narrator (a low-integrity subject
grading itself). This module derives the three trust columns MECHANICALLY from the deterministic trust
ledger (scripts/lib/trust_ledger.compose — the reality_contact partition), so a model can never — even
accidentally — render a model-only pass as reality-proven.

Cross-domain safety frame (Biba integrity + reference monitor): reality_contact is the integrity LABEL,
assigned by a high-integrity mechanism (gate_log.GATE_CONTACT) keyed on gate identity; trust_ledger is the
trusted labeller; render_story is the reference monitor. The write-up channel (narrator -> panel) is severed
BY CONSTRUCTION: render_story reads a NEW `trust_signoff` key this module stamps, never the narrator's
`signoff`. There is no code path from a narrator characterization to the green column.

Classification (the three columns render_story._render_trust_signoff consumes):
  reality_approved  Proven against reality — a REALITY gate (high/medium contact) with a clean-POSITIVE
                    verdict ONLY (M1: contact alone is NOT proof; a no-go / partial / fail / warn is
                    reality-CONTACT, not reality-PROOF, and must NOT render green), plus a green regression.
  model_approved    Reviewed by the model — the low-contact model gates (code/critique/critique-review/
                    build-checks), whatever the verdict (a review is never a reality proof).
  not_yet           Not yet proven against reality — two clearly-worded kinds: (a) unchecked gaps
                    (not_checked lines, worded from the `reason` enum, never the engineer-facing text — M5),
                    and (b) reality-FLAGGED concerns (a non-positive reality-gate outcome, an un-eliminated
                    reality surprise, or a FAILED regression — m1/M-add-1), each marked distinctly so a
                    reality-discovered problem never reads as a mere unchecked gap.

Plain-English translation (AC2): a CLOSED gate->English table (drift-guarded set-equal to
gate_log.GATE_CONTACT minus the informational gates — m2) and an OPEN verdict->English table with a loud,
FAIL-VISIBLE pass-through for an unrecognized verdict (a later-added verdict degrades loudly, never invented
into false English — CONSTRAINT-2). No raw gate id or verdict enum reaches the panel for the known
vocabulary, BY CONSTRUCTION — so AC2 rests on this table, NOT on a jargon tripwire (M2/M4).

Fail-visible (AC4 / M-add-2): an absent slice (LedgerNotFound) OR an unreadable primary source (a
malformed/empty/missing gate-log.json — the source of the reality+model columns) yields a stamped
state='unavailable' block; render shows an explicit notice with NO green column and NO narrator fallback.
An empty-but-VALID gate-log ('no reviews ran yet') is NOT unavailable — the pre-build panel survives.

Exit: `inject` exits non-zero ONLY on an io failure (unreadable/unwritable story-sections.json) so the skill
can surface it; a compute problem still injects a fail-visible state='unavailable' block. `project` is
exit-0-always (debug view).
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

from scripts.lib import _stdout, trust_ledger  # noqa: E402
from scripts.lib.gate_log import GATE_CONTACT, INFORMATIONAL_GATES  # noqa: E402

# CR1/CR-parity: the provenance marker inject stamps onto the block; render_story renders the trust columns
# ONLY when the block carries this exact value, so an un-injected / narrator-authored block is never rendered.
INJECT_SOURCE = "story_signoff.inject"

# ── the label->English tables ────────────────────────────────────────────────────────────────
# CLOSED over the gate set (m2): every gate that can reach the trust columns has a plain name. The drift-guard
# below is set-equal to gate_log.GATE_CONTACT MINUS the informational gates (design-tournament never reaches
# reality_confirmed/model_only — trust_ledger buckets it to `informational`), so a gate added in gate_log
# without a plain name here fails LOUDLY at import, not silently with a jargon leak.
GATE_ENGLISH: dict[str, str] = {
    "risk-spike": "Feasibility check on the real environment",
    "validate-slice": "Reality test (real device or real data)",
    "drift-check": "Check that the notes still match the code",
    "code-review": "Code review by the model",
    "critique": "Design review by the model",
    "critique-review": "Second design review by the model",
    "build-checks": "Automated build safety checks",
}
_PROJECTED_GATES = set(GATE_CONTACT) - set(INFORMATIONAL_GATES)


def _check_gate_english_drift(english: dict, projected) -> None:
    """Raise LOUDLY if the closed gate->English table is not set-equal to the projected gate universe
    (BC-PROJ-12: a shippable guard, extracted so the negative arm — 'it fires on drift' — is testable and
    invokable, not a positive-only assertion). A gate added/removed in gate_log without a plain-English name
    would else silently leak a raw gate id into the panel (AC2)."""
    if set(english) != set(projected):
        raise RuntimeError(
            "story_signoff: GATE_ENGLISH is out of sync with gate_log.GATE_CONTACT minus the informational "
            f"gates (english={sorted(english)} vs projected={sorted(projected)}). A gate was added/removed "
            "in gate_log; give it a plain-English name here (or exclude it as informational)."
        )


_check_gate_english_drift(GATE_ENGLISH, _PROJECTED_GATES)  # loud drift guard at import

# OPEN over verdicts (AC2/CONSTRAINT-2): known verdicts render as plain English chosen so NO raw verdict-enum
# token is a substring of its phrase; an UNKNOWN verdict passes through flagged (loud, fail-visible).
VERDICT_ENGLISH: dict[str, str] = {
    "go": "confirmed it works",
    "conditional": "confirmed it works, with conditions",
    "no-go": "found it would not work",
    "pass": "succeeded",
    "partial": "succeeded only in part",
    "fail": "did not succeed",
    "clean": "found no problems",
    "needs-fixes": "asked for changes",
    "blocked": "raised a blocking concern",
    "findings": "raised some notes",
    "extend": "asked for another review",
    "accept": "agreed with the review",
    "warn": "flagged a mismatch",
}

# M1: which verdict makes a REALITY gate's row a genuine reality PROOF (green). Anything else on a reality gate
# (no-go / partial / fail / warn, or an unknown verdict) is reality-CONTACT, not proof -> the amber column.
# Only the high/medium-contact gates can reach reality_confirmed, so only they need an entry; the default (an
# empty set) fail-SAFELY denies green to any unlisted/unknown reality gate.
POSITIVE_VERDICTS: dict[str, set[str]] = {
    "risk-spike": {"go", "conditional"},
    "validate-slice": {"pass"},
    "drift-check": {"clean"},
}

# fixed English for the not_checked `reason` enum (M5: translate from STRUCTURE, never the engineer-facing text).
_REASON_ENGLISH: dict[str, str] = {
    "no-criteria": "Nothing has been checked against reality yet -- no acceptance checks were recorded.",
    "criterion-absent": "has not been checked against reality yet.",
    "criterion-not-pass": "was checked against reality but has not passed yet.",
    "low-contact": "was reviewed by the model only, so it isn't proven against reality yet.",
    "regression-not-run": "The regression test suite has not been run for this slice yet.",
    "regression-absent": "No regression test result was recorded for this slice.",
}
_FLAG = "Reality flagged a concern: "  # ASCII marker distinguishing (b) reality-found problems from (a) gaps


# ── translation helpers ──────────────────────────────────────────────────────────────────────

def _gate_en(gate: str | None) -> str:
    g = str(gate or "").strip()
    return GATE_ENGLISH.get(g) or (f"an unrecognized check ('{g}')" if g else "an unnamed check")


def _verdict_en(verdict: str | None) -> str:
    v = str(verdict or "").strip()
    if v in VERDICT_ENGLISH:
        return VERDICT_ENGLISH[v]
    # fail-visible pass-through: deliberately quote the raw token so a new verdict degrades LOUDLY.
    return f"reported an outcome recorded as '{v}'" if v else "reported no outcome"


def _gate_phrase(line: dict) -> str:
    return f"{_gate_en(line.get('gate'))} {_verdict_en(line.get('verdict'))}."


def _is_positive_reality(line: dict) -> bool:
    """A reality-confirmed row is a genuine reality PROOF only if its verdict is a clean positive for its
    gate (M1). Unknown gate or unknown/absent verdict -> NOT positive (fail-safe: never fabricate green)."""
    return str(line.get("verdict") or "") in POSITIVE_VERDICTS.get(str(line.get("gate") or ""), set())


# ── the projection (pure — the AC1/AC2/AC3 unit-test surface) ──────────────────────────────────

def _gate_log_unavailable(ledger: dict) -> bool:
    """The reality+model columns are derived from gate-log.json; if that primary source is unreadable
    (missing/malformed/empty) the columns would be spuriously empty and indistinguishable from 'no reviews
    ran yet' — so surface it as unavailable (M-add-2). An empty-but-VALID gate-log stays available."""
    for a in (ledger.get("availability") or []):
        if isinstance(a, dict) and a.get("source") == "gate-log.json":
            return a.get("status") != "ok"
    return True  # no gate-log availability entry at all -> cannot confirm the source -> unavailable


def _unavailable_block(reason: str) -> dict:
    return {"state": "unavailable", "derivation": "trust-ledger", "unavailable_reason": reason,
            "reality_approved": [], "model_approved": [], "not_yet": []}


def project_ledger_for_signoff(ledger: dict) -> dict:
    """Project a composed trust ledger into the three signoff columns. PURE + deterministic.

    Returns {state, derivation, reality_approved[], model_approved[], not_yet[]}; state='unavailable'
    (+ unavailable_reason) when the ledger is unusable for the trust columns (M-add-2). The `_source`
    provenance stamp is added by `inject`, never here — an un-injected projection never renders (mirror of
    story_inputs / product_shape)."""
    if not isinstance(ledger, dict):
        return _unavailable_block("the trust ledger could not be read.")
    if _gate_log_unavailable(ledger):
        return _unavailable_block(
            "the review record (gate-log.json) could not be read, so what has been proven against reality "
            "cannot be shown right now.")

    reality_approved: list[dict] = []
    model_approved: list[dict] = []
    not_yet: list[dict] = []

    # reality_confirmed (high/medium contact) — split by verdict polarity (M1).
    for line in (ledger.get("reality_confirmed") or []):
        if not isinstance(line, dict):
            continue
        if _is_positive_reality(line):
            reality_approved.append({"what": _gate_phrase(line)})
        else:
            not_yet.append({"what": _FLAG + _gate_phrase(line)})   # reality-CONTACT, not proof

    # model_only (low contact) — always a model review, never a reality proof.
    for line in (ledger.get("model_only") or []):
        if isinstance(line, dict):
            model_approved.append({"what": _gate_phrase(line)})

    # a GREEN regression is executed reality evidence (M-add-1 conscious decision -> reality column);
    # a FAILED regression is a reality-found concern; catalog-membership is a DEFINITION (excluded).
    for line in (ledger.get("shippability") or []):
        if not isinstance(line, dict):
            continue
        st = line.get("state")
        if st == "green":
            reality_approved.append({"what": "The project's regression test suite passed."})
        elif st == "failed":
            not_yet.append({"what": _FLAG + "a regression test failed and has not been cleared."})
        # 'not-run' already surfaces via a not_checked reason=regression-not-run line; skip here.

    # not_checked — unchecked GAPS, worded from the `reason` enum (M5: never the engineer-facing text).
    for line in (ledger.get("not_checked") or []):
        if not isinstance(line, dict):
            continue
        reason = str(line.get("reason") or "")
        ac = str(line.get("ac") or "").strip()
        item: dict = {}
        if reason in ("no-criteria", "regression-not-run", "regression-absent"):
            item["what"] = _REASON_ENGLISH[reason]
        elif reason in ("criterion-absent", "criterion-not-pass", "low-contact"):
            subject = ac or "This acceptance check"
            item["what"] = f"{subject} {_REASON_ENGLISH[reason]}"
            if ac:
                item["ref"] = ac
        else:
            # unknown reason -> fail-visible generic gap (never fabricate, never crash).
            item["what"] = "Something has not yet been checked against reality (details in the trust ledger)."
        not_yet.append(item)

    # reality_surprises — un-eliminated defeaters (m1). Aggregate to a COUNT; never render the free-form
    # note verbatim (M5: a surprise note commonly embeds a trace id -> would leak into the panel).
    surprises = ledger.get("reality_surprises") or []
    n_surprise = sum(1 for s in surprises if isinstance(s, dict))
    if n_surprise:
        tail = ("1 surprise that has not been resolved yet." if n_surprise == 1
                else f"{n_surprise} surprises that have not been resolved yet.")
        not_yet.append({"what": _FLAG + "reality testing raised " + tail})

    return {"state": "ok", "derivation": "trust-ledger",
            "reality_approved": reality_approved, "model_approved": model_approved, "not_yet": not_yet}


def derive_for_slice(vault: Path, slice_arg: str) -> dict:
    """IO entry: compose the ledger then project. LedgerNotFound (absent slice) -> unavailable (AC4)."""
    try:
        ledger = trust_ledger.compose(Path(vault), slice_arg)
    except trust_ledger.LedgerNotFound:
        return _unavailable_block(
            f"the trust ledger for {slice_arg!r} could not be found, so what has been proven against "
            "reality cannot be shown.")
    return project_ledger_for_signoff(ledger)


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────

def _render_text(block: dict) -> str:
    if block.get("state") != "ok":
        return f"trust signoff: UNAVAILABLE -- {block.get('unavailable_reason')}"
    lines = []
    for key, label in (("reality_approved", "Proven against reality"),
                       ("model_approved", "Reviewed by the model"),
                       ("not_yet", "Not yet proven against reality")):
        lines.append(f"[{label}]")
        for it in block.get(key) or []:
            lines.append(f"  - {it.get('what')}")
        if not (block.get(key) or []):
            lines.append("  (none)")
    return "\n".join(lines)


def _resolve_vault(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    try:
        from scripts.lib._vault_paths import VAULT_ROOT
        return VAULT_ROOT
    except Exception:
        return None


def _cmd_project(args) -> int:
    vault = _resolve_vault(args.vault)
    if vault is None:
        block = _unavailable_block("could not resolve the vault root.")
    else:
        block = derive_for_slice(vault, args.slice)
    print(json.dumps(block, ensure_ascii=False) if args.json else _render_text(block))
    return 0  # exit-0-always: a compute problem rides stdout as a fail-visible unavailable block


def _cmd_inject(args) -> int:
    """Compose+project on the MAIN THREAD and write the stamped `trust_signoff` block into story-sections.json.
    io failure -> non-zero (the skill surfaces it); a compute problem still injects the unavailable block."""
    sf = Path(args.sections_file)
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"story_signoff inject: cannot read {sf}: {e}\n")
        return 2
    if not isinstance(data, dict):
        sys.stderr.write(f"story_signoff inject: {sf} is not a JSON object\n")
        return 2
    vault = _resolve_vault(args.vault)
    block = derive_for_slice(vault, args.slice) if vault is not None \
        else _unavailable_block("could not resolve the vault root.")
    # stamp provenance so render_story renders ONLY this main-thread-derived block (severs the narrator channel
    # by construction); UNCONDITIONALLY overwrite any narrator-authored `trust_signoff` (mirror story_inputs).
    block["_source"] = INJECT_SOURCE
    data["trust_signoff"] = block
    try:
        sf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"story_signoff inject: cannot write {sf}: {e}\n")
        return 2
    # must-not-defer #4: log the derivation source so the classification is auditable.
    print(f"story_signoff inject: trust_signoff derived from {block.get('derivation')} "
          f"(state={block.get('state')}) -> {sf}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(prog="story_signoff", description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_proj = sub.add_parser("project", help="compose+project the trust signoff, print it (exit-0-always)")
    p_proj.add_argument("--slice", required=True, help="slice folder name or canonical slice-NNN")
    p_proj.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    p_proj.add_argument("--json", action="store_true", help="emit JSON (default: human-readable text)")
    p_proj.set_defaults(func=_cmd_project)

    p_inj = sub.add_parser("inject", help="derive+write the trust_signoff block into a story-sections.json")
    p_inj.add_argument("--sections-file", required=True, help="path to the story-sections.json to inject into")
    p_inj.add_argument("--slice", required=True, help="slice folder name or canonical slice-NNN")
    p_inj.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    p_inj.set_defaults(func=_cmd_inject)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
