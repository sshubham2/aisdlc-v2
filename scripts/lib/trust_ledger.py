"""trust_ledger.py — per-slice trust ledger: compose + render (SC-143 / PS-002).

Compose ONE human-facing "assurance case" for a slice so a reader can trust it
WITHOUT re-reading the diff — what reality confirmed, what only a model reviewed,
and what nobody checked — built MECHANICALLY from evidence that already exists as
STRUCTURE (zero model authorship): the slice's ``validation.json`` (per-criterion
result + reality_contact/reality_proxy, reality_surprises, shippability_regression),
the project ``gate-log.json`` rows (per-gate verdict + reality_contact), and the
project ``shippability.json`` catalog. Framed as a GSN/Assurance-2.0 assurance case:
reality-touching evidence (``gate_log.GATE_CONTACT`` high/medium) renders DISTINCTLY
from model-only evidence (low); a sub-claim with no reality evidence is a visible
NOT-CHECKED (undeveloped goal), never silently dropped; a recorded reality_surprise
is an un-eliminated DEFEATER that annotates the green headline. Every rendered line
carries required provenance ``source={file,locator}`` — the composer refuses to emit
a line without it.

SIBLING / TWIN of ``ship_receipt.py`` (m3): both read the SAME three sources
(validation.json + gate-log rows + shippability_regression), but for DIFFERENT
purposes with DELIBERATELY DIFFERENT loaders. ``ship_receipt`` is the opt-in,
CI-facing merge-gate RECORD emitted at ``/commit-slice`` AFTER the trust decision;
its ``_load`` collapses any error to ``{}`` (fine for a post-decision record). This
ledger is PRE-decision human honesty: it uses a FAIL-VISIBLE loader (below) so a
missing/malformed/empty source can never masquerade as a silent green (AC4). A future
change to how one twin reads a source should be mirror-checked against the other.
Green semantics diverge intentionally: ship_receipt treats failed+deferral_approved
as PASS (ship_receipt.py:147); this ledger reads a deferred-but-FAILED regression as
NOT-green (M4 honesty divergence).

Green anchor = ``validation.json`` as the AUTHORITATIVE per-slice reality record
(ADR-098, superseding the gate-log-row anchor of ADR-097): ``reads_fully_green`` is a
Kleene conjunction (UNKNOWN never absorbs to TRUE) — TRUE iff validation.json is
present+ok AND result==pass AND len(criteria)>=1 AND every criterion result==pass AND
the slice-level reality_contact is high/medium AND no shippability regression FAILED AND
every mission-brief acceptance-criterion has a reality-verified passing criterion (the
expected-universe anchor, ADR-097 invariant #1 / CR1 — an uncovered AC is an undeveloped
goal that cannot sit under a fully-green top claim; proven 0 false-alarms across 67 real
base-green slices). The gate-log validate-slice ROW is corroborating only; its absence is a
measurement-spine NOTE, never a red. Residual gaps ADR-098 deliberately keeps UN-blocking
(a shippability regression that did not run / is absent, an unreadable mission-brief, a
recorded reality_surprise) CAVEAT the headline (GREEN*) rather than block it — so a
one-glance green is never UNQUALIFIED while any gap exists (the M-add-1 mechanism).

Subcommands (mirrors ship_receipt's stdout/--out shape):
  compose --slice <slice-NNN> [--vault V] [--out PATH] [--format json|text|md]
          Compose trust-ledger.json (default --format json) and emit to stdout or --out.
  render  (--from <ledger.json> | --slice <slice-NNN> [--vault V]) [--format text|md]
          Deterministic view; a PURE function of the composed JSON (re-derives nothing).

Exit: 0 ok · 2 usage error / slice-not-found / unreadable --from ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# Invoked as `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/trust_ledger.py"`, which puts
# scripts/lib (NOT the plugin root) on sys.path[0]; add the plugin root so the
# `from scripts.lib ...` imports resolve. No-op under `-m scripts.lib.trust_ledger`.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/trust_ledger.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402
# The SINGLE source of truth for reality-contact classification + the proxy value-set +
# the informational-gate exclusion (ADR-097 point 3). Do NOT re-hardcode the contact map.
from scripts.lib.gate_log import (  # noqa: E402
    GATE_CONTACT,
    INFORMATIONAL_GATES,
    _PROXIES,
)
# Reuse the slice locator (active OR archive) from the CI-gate twin.
from scripts.lib.ship_receipt import _canon, _find_slice_dir  # noqa: E402

# gate_log._PROXIES is an UNORDERED set, so the strongest->weakest RANK is defined
# LOCALLY here. The set-equality guard below fails LOUDLY if a proxy is ever added or
# removed in gate_log without updating this rank — a silent mis-rank is worse than a crash.
PROXY_RANK = [
    "real-device",       # strongest
    "real-account",
    "real-sandbox",
    "staging",
    "local-real-data",
    "simulator",
    "docs-only",         # weakest
]
if set(PROXY_RANK) != set(_PROXIES):  # loud drift guard (also asserted in tests)
    raise RuntimeError(
        "trust_ledger: PROXY_RANK is out of sync with gate_log._PROXIES "
        f"(rank={sorted(PROXY_RANK)} vs proxies={sorted(_PROXIES)}). A proxy was "
        "added/removed in gate_log; update PROXY_RANK's strongest->weakest order."
    )

SCHEMA = "aisdlc/trust-ledger@1"
SCOPE_NOTE = ("trust from RECORDED evidence: validation.json (authoritative per-slice "
              "reality record), gate-log.json, shippability.json — verification leaving "
              "no structured trace is invisible to this mechanical composer")


class LedgerNotFound(Exception):
    """The requested slice folder does not exist (active or archive) — fail-visible."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _vault_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    from scripts.lib._vault_paths import VAULT_ROOT  # lazy (PEP 562): no git probe unless needed
    return VAULT_ROOT


def _load_source(path: Path) -> tuple[dict | None, str]:
    """Fail-visible loader: return (data, status in {ok, missing, malformed, empty}).

    Unlike ship_receipt._load (error -> {}), this PRESERVES the failure kind so a
    missing/malformed/absent source surfaces as an explicit availability state and can
    never masquerade as a silent green (AC4). NOTE: 'empty' catches an empty FILE only —
    a populated dict whose criteria==[] is 'ok' here, and the M2 vacuous-truth guard in
    _compute_green handles the zero-criteria case separately.
    """
    try:
        if not path.is_file():
            return None, "missing"
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "malformed"
    if not raw.strip():
        return None, "empty"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "malformed"
    if not isinstance(data, dict):
        return None, "malformed"
    return data, "ok"


def _line(text: str, file: str, locator: str, **extra) -> dict:
    """Build one ledger line, enforcing the provenance invariant.

    Every trust line MUST cite the source row it was composed from; a line without a
    non-empty {file, locator} is REFUSED (provenance is a structural contract, not a
    nicety). ``extra`` values that are None are dropped (matches the vault's omit-empty
    convention).
    """
    if not file or not locator:
        raise ValueError(
            f"trust_ledger: refusing a line without provenance (text={text!r}, "
            f"file={file!r}, locator={locator!r})")
    d: dict = {"text": str(text), "source": {"file": str(file), "locator": str(locator)}}
    for k, v in extra.items():
        if v is not None:
            d[k] = v
    return d


def _classify_gate_rows(gl: dict | None, canon: str) -> tuple[list, list, list, list]:
    """Partition THIS slice's gate-log rows into (reality_confirmed, model_only,
    informational, known_escapes).

    m1: read the canonical ``entries`` array, NOT the stray top-level ``rows``.
    M3: kind=='miss' RECALL rows are NEVER trust-affirming -> known_escapes; design-
        tournament (INFORMATIONAL_GATES) rows carry a verdict but no pass/fail meaning
        -> informational (excluded from the trust sections); non-verdict rows are skipped
        (mirrors ship_receipt.py:107).
    A slice can carry MULTIPLE rows per gate (slice-064 has 2 risk-spike rows); each is
    rendered with its ``at`` so duplicates stay legible.
    """
    reality_confirmed: list = []
    model_only: list = []
    informational: list = []
    known_escapes: list = []
    entries = (gl or {}).get("entries") or []  # m1: canonical array, ignore stray `rows`
    for row in entries:
        if not isinstance(row, dict):
            continue
        if str(row.get("slice", "")) != canon:
            continue
        gate = str(row.get("gate", ""))
        at = row.get("at", "")
        loc = f"entries[slice={canon},gate={gate},at={at}]"
        contact = row.get("reality_contact")
        if row.get("kind") == "miss":
            sev = row.get("severity", "?")
            caught = row.get("caught_by", "?")
            ref = row.get("ref", "")
            txt = f"{gate}: MISSED a {sev} issue (caught by {caught})"
            if ref:
                txt += f" -- {ref}"
            known_escapes.append(_line(txt, "gate-log.json", loc, reality_contact=contact))
            continue
        if "verdict" not in row:
            continue  # non-verdict, non-miss row -> not a trust row (ship_receipt.py:107 mirror)
        verdict = row.get("verdict")
        fc = row.get("findings_count")
        if gate in INFORMATIONAL_GATES:
            div = row.get("approach_divergence")
            txt = f"{gate}: {verdict} (informational; findings={fc if isinstance(fc, int) else 0})"
            if div:
                txt += f", divergence={div}"
            informational.append(_line(txt, "gate-log.json", loc, reality_contact=contact))
            continue
        if not contact:
            contact = GATE_CONTACT.get(gate)
        fc_txt = f" ({fc} finding{'s' if fc != 1 else ''})" if isinstance(fc, int) else ""
        txt = f"{gate}: {verdict}{fc_txt}"
        line = _line(txt, "gate-log.json", loc, reality_contact=contact, at=at or None,
                     reality_proxy=row.get("reality_proxy"))
        if contact in ("high", "medium"):
            reality_confirmed.append(line)
        elif contact == "low":
            model_only.append(line)
        else:
            # Unknown/absent contact on a verdict row: fail-visible, NEVER a trust-affirming
            # line — surface it as informational with the anomaly flagged.
            line["text"] = f"{txt} [unclassified reality_contact={contact!r}]"
            informational.append(line)
    return reality_confirmed, model_only, informational, known_escapes


def _compute_green(val_status: str, val: dict | None, slice_contact,
                   crit_list, sr, surprises, uncovered_acs, brief_ok: bool) -> tuple[bool, list[str]]:
    """reads_fully_green — Kleene conjunction over validation.json (ADR-098) PLUS the
    expected-universe AC-coverage anchor (ADR-097 invariant #1; CR1).

    UNKNOWN/absent never absorbs to TRUE. Returns (green, caveats):
      * BLOCKS green (-> False) on: validation unavailable / result!=pass / empty-or-absent
        criteria (M2) / any non-pass criterion / slice-level reality_contact not high|medium /
        a FAILED shippability regression (M4) / OR an EXPECTED mission-brief AC with no
        reality-verified passing criterion (CR1 — an undeveloped goal cannot sit under a
        fully-green top claim; proven 0 false-alarms across 67 real base-green slices).
      * CAVEATS green (GREEN*, never an UNQUALIFIED green) — but does NOT block — on the
        gaps ADR-098 deliberately keeps un-blocking: a non-empty reality_surprises (M-add-1),
        a shippability regression that did NOT run / is absent (ADR-098 pt 3), and an
        unreadable mission-brief (the expected AC universe is unverifiable). This mirrors the
        M-add-1 mechanism so the one-glance headline is honest whenever a residual gap exists.
    """
    caveats: list[str] = []
    if val_status != "ok" or not isinstance(val, dict):
        return False, caveats  # validation unavailable -> UNKNOWN -> not green (falsification)
    if str(val.get("result", "")).lower() != "pass":
        return False, caveats
    if not isinstance(crit_list, list) or len(crit_list) < 1:  # M2: empty/absent criteria != vacuous green
        return False, caveats
    for c in crit_list:
        if not isinstance(c, dict) or str(c.get("result", "")).lower() != "pass":
            return False, caveats  # any non-pass / malformed criterion -> not green
    if slice_contact not in ("high", "medium"):
        return False, caveats  # model-only / absent slice-level contact -> not green
    if isinstance(sr, dict):
        failed = sr.get("failed_rows") or []
        nfailed = len(failed) if isinstance(failed, (list, tuple)) else (failed if isinstance(failed, int) else 0)
        if nfailed > 0:
            return False, caveats  # M4: deferred-but-FAILED still reads not-green (pre-decision honesty)
    if brief_ok and uncovered_acs:
        return False, caveats  # CR1: an expected AC with no reality-verified pass -> undeveloped goal -> not green
    # Green — annotate the headline for every residual gap so it is never an UNQUALIFIED green.
    if isinstance(surprises, list) and len(surprises) > 0:
        caveats.append(f"green with {len(surprises)} recorded reality surprise(s)")  # M-add-1
    if not isinstance(sr, dict):
        caveats.append("green but no shippability regression recorded")              # ADR-098 pt 3
    elif not bool(sr.get("ran")):
        caveats.append("green but the shippability regression did not run")          # ADR-098 pt 3
    if not brief_ok:
        caveats.append("green but the mission-brief is unavailable -- AC coverage is unverifiable")  # CR1
    return True, caveats


def _compute_weakest_proxy(crit_list, reality_confirmed: list) -> dict:
    """Weakest reality_proxy, SCOPED to sources that actually recorded one (m2).

    Considers per-criterion proxies + reality-confirmed gate-row proxies. Absence is
    labelled, never silently ranked as strong: a single scalar over a mostly-absent field
    would over/under-state contact strength (reality_proxy is ~23% populated).
    """
    recorded: list[str] = []
    total = 0
    if isinstance(crit_list, list):
        for c in crit_list:
            if isinstance(c, dict):
                total += 1
                p = c.get("reality_proxy")
                if p and str(p).lower() in _PROXIES:
                    recorded.append(str(p).lower())
    for line in reality_confirmed:
        total += 1
        p = line.get("reality_proxy")
        if p and str(p).lower() in _PROXIES:
            recorded.append(str(p).lower())
    if recorded:
        weakest = max(recorded, key=lambda p: PROXY_RANK.index(p))  # highest rank index == weakest
        coverage = f"weakest of {len(recorded)} recorded; {total - len(recorded)} recorded none"
    else:
        weakest = None
        coverage = f"no reality_proxy recorded ({total} source(s) considered)"
    return {"value": weakest, "recorded": len(recorded),
            "absent": total - len(recorded), "coverage": coverage}


def compose(vault: Path, slice_arg: str) -> dict:
    """Mechanically compose a slice's trust-ledger.json dict. Pure/deterministic (save
    the ``at`` timestamp), read-only, ZERO model authorship — every field is a source
    enum/bool/int/id. Raises LedgerNotFound if the slice folder is absent."""
    vault = Path(vault)
    sdir = _find_slice_dir(vault, slice_arg)
    if sdir is None:
        raise LedgerNotFound(
            f"trust_ledger: slice {slice_arg!r} not found under {vault}/slices[/archive]")
    canon = _canon(sdir.name) or sdir.name

    val, val_status = _load_source(sdir / "validation.json")
    brief, brief_status = _load_source(sdir / "mission-brief.json")
    gl, gl_status = _load_source(vault / "gate-log.json")
    ship_cat, ship_status = _load_source(vault / "shippability.json")

    _reasons = {"missing": "file absent", "malformed": "invalid JSON / not an object",
                "empty": "empty file", "ok": ""}
    availability = [
        {"source": name, "status": st, "reason": _reasons.get(st, st)}
        for name, st in (("validation.json", val_status), ("mission-brief.json", brief_status),
                         ("gate-log.json", gl_status), ("shippability.json", ship_status))
    ]

    reality_confirmed, model_only, informational, known_escapes = _classify_gate_rows(gl, canon)

    not_checked: list = []
    reality_surprises: list = []
    shippability: list = []

    slice_contact = val.get("reality_contact") if val_status == "ok" and isinstance(val, dict) else None
    crit_list = val.get("criteria") if val_status == "ok" and isinstance(val, dict) else None
    crit_by_id: dict[str, dict] = {}
    if isinstance(crit_list, list):
        for c in crit_list:
            if isinstance(c, dict) and c.get("id"):
                crit_by_id[str(c["id"])] = c

    # Zero-criteria (M2): a validation.json that verified NOTHING is an undeveloped goal.
    if val_status == "ok" and (not isinstance(crit_list, list) or len(crit_list) == 0):
        not_checked.append(_line("no criteria recorded in validation.json (nothing was reality-checked)",
                                 "validation.json", "criteria", reason="no-criteria"))

    # Every mission-brief AC lacking a reality-verified pass criterion -> not_checked. An AC
    # whose criterion is absent or not-pass is an EXPECTED-universe gap that blocks green (CR1);
    # the low-contact case is already blocked globally by the slice-level contact conjunct.
    uncovered_acs: list[str] = []
    acs = brief.get("acceptance_criteria") if brief_status == "ok" and isinstance(brief, dict) else None
    if isinstance(acs, list):
        for i, ac in enumerate(acs):
            if not isinstance(ac, dict):
                continue
            acid = str(ac.get("id", f"AC{i + 1}"))
            c = crit_by_id.get(acid)
            if c is None:
                uncovered_acs.append(acid)
                not_checked.append(_line(f"{acid}: no validation criterion recorded",
                                         "mission-brief.json", f"acceptance_criteria[{i}]",
                                         reason="criterion-absent"))
            elif str(c.get("result", "")).lower() != "pass":
                uncovered_acs.append(acid)
                not_checked.append(_line(
                    f"{acid}: criterion result={str(c.get('result', '')).lower() or 'unknown'} "
                    "(not a reality-verified pass)", "validation.json", f"criteria[id={acid}]",
                    reason="criterion-not-pass"))
            elif slice_contact not in ("high", "medium"):
                not_checked.append(_line(
                    f"{acid}: criterion passed but slice reality_contact={slice_contact or 'absent'} "
                    "(model-only, not reality-verified)", "validation.json", "reality_contact",
                    reason="low-contact"))

    # reality_surprises = un-eliminated defeaters (M-add-1).
    surprises = val.get("reality_surprises") if val_status == "ok" and isinstance(val, dict) else None
    if isinstance(surprises, list):
        for i, s in enumerate(surprises):
            if isinstance(s, dict):
                txt = s.get("note") or s.get("text") or json.dumps(s, ensure_ascii=False, sort_keys=True)
            else:
                txt = str(s)
            reality_surprises.append(_line(f"reality surprise: {txt}", "validation.json",
                                           f"reality_surprises[{i}]", reason="un-eliminated-defeater"))

    # Shippability regression STATE from the per-slice validation record (M4), NOT the
    # project catalog (whose rows are DEFINITIONS with no result).
    sr = val.get("shippability_regression") if val_status == "ok" and isinstance(val, dict) else None
    if isinstance(sr, dict):
        ran = bool(sr.get("ran"))
        failed = sr.get("failed_rows") or []
        nfailed = len(failed) if isinstance(failed, (list, tuple)) else (failed if isinstance(failed, int) else 0)
        deferral = sr.get("deferral") or {}
        if not ran:
            shippability.append(_line("shippability regression did NOT run", "validation.json",
                                      "shippability_regression", state="not-run"))
            not_checked.append(_line("shippability regression not run", "validation.json",
                                     "shippability_regression", reason="regression-not-run"))
        elif nfailed > 0:
            deferred = bool(deferral.get("approved"))
            txt = f"shippability regression FAILED ({nfailed} row(s))"
            if deferred:
                txt += " [deferral approved -- still NOT-green here: pre-decision honesty vs ship_receipt]"
            shippability.append(_line(txt, "validation.json", "shippability_regression", state="failed"))
        else:
            shippability.append(_line("shippability regression: ran, 0 failed", "validation.json",
                                      "shippability_regression", state="green"))
    elif val_status == "ok":
        not_checked.append(_line("no shippability regression recorded", "validation.json",
                                 "shippability_regression", reason="regression-absent"))

    # Corroborating catalog membership (M4): labelled a DEFINITION, never checked/green.
    if ship_status == "ok" and isinstance(ship_cat, dict):
        rows = ship_cat.get("rows") or []
        mine = [r for r in rows if isinstance(r, dict) and _canon(str(r.get("slice", ""))) == canon]
        if mine:
            shippability.append(_line(
                f"{len(mine)} shippability catalog row(s) reference this slice "
                "(catalog membership -- a DEFINITION, not a pass/fail)", "shippability.json", "rows",
                state="catalog-membership"))

    reads_fully_green, green_caveats = _compute_green(
        val_status, val, slice_contact, crit_list, sr, surprises,
        uncovered_acs, brief_status == "ok")
    weakest_proxy = _compute_weakest_proxy(crit_list, reality_confirmed)

    return {
        "_schema": SCHEMA,
        "_composed_by": "trust_ledger.py",
        "slice": canon,
        "slice_folder": sdir.name,
        "reads_fully_green": reads_fully_green,
        "green_caveats": green_caveats,
        "weakest_proxy": weakest_proxy,
        "reality_confirmed": reality_confirmed,
        "model_only": model_only,
        "not_checked": not_checked,
        "reality_surprises": reality_surprises,
        "known_escapes": known_escapes,
        "informational": informational,
        "shippability": shippability,
        "availability": availability,
        "scope_note": SCOPE_NOTE,
        "at": _now_iso(),
    }


def _src(line: dict) -> str:
    s = line.get("source") or {}
    return f"{s.get('file', '?')}:{s.get('locator', '?')}"


def render(ledger: dict, fmt: str = "text") -> str:
    """Deterministic text/markdown view — a PURE function of the composed JSON (re-derives
    nothing, so the view can never disagree with the mechanical composition). The
    NOT-CHECKED / un-eliminated-defeaters section leads first (skim-first, anti-ceremony);
    reality-confirmed renders visually distinct from model-only.
    """
    md = (fmt == "md")
    out: list[str] = []

    def h(title: str) -> None:
        out.append("")
        out.append(f"## {title}" if md else f"== {title} ==")

    slice_id = ledger.get("slice", "?")
    green = ledger.get("reads_fully_green")
    caveats = ledger.get("green_caveats") or []
    if green and caveats:
        headline = f"TRUST LEDGER {slice_id}: GREEN* -- {'; '.join(caveats)}"
    elif green:
        headline = f"TRUST LEDGER {slice_id}: READS FULLY GREEN"
    else:
        headline = f"TRUST LEDGER {slice_id}: NOT FULLY GREEN"
    out.append(f"# {headline}" if md else headline)
    out.append(f"scope: {ledger.get('scope_note', '')}")

    unavail = [a for a in (ledger.get("availability") or []) if a.get("status") != "ok"]
    if unavail:
        out.append("")
        out.append("!! SOURCE AVAILABILITY (a source is unavailable -- this ledger cannot read fully green):")
        for a in unavail:
            out.append(f"   - {a.get('source')}: {a.get('status')} ({a.get('reason', '')})")

    h("Not checked / un-eliminated defeaters")
    nc = ledger.get("not_checked") or []
    rs = ledger.get("reality_surprises") or []
    if not nc and not rs:
        out.append("   (none)")
    for line in rs:
        out.append(f"   [defeater]    {line.get('text', '')}   <- {_src(line)}")
    for line in nc:
        out.append(f"   [not-checked] {line.get('text', '')}   <- {_src(line)}")

    h("Reality-confirmed (evidence touched something that is NOT the model)")
    rc = ledger.get("reality_confirmed") or []
    if not rc:
        out.append("   (none)")
    for line in rc:
        proxy = f" [proxy: {line['reality_proxy']}]" if line.get("reality_proxy") else ""
        out.append(f"   [{line.get('reality_contact', '?')}] {line.get('text', '')}{proxy}   <- {_src(line)}")

    h("Model-only (a model graded a model -- NOT reality)")
    mo = ledger.get("model_only") or []
    if not mo:
        out.append("   (none)")
    for line in mo:
        out.append(f"   [{line.get('reality_contact', '?')}] {line.get('text', '')}   <- {_src(line)}")

    h("Shippability regression")
    sh = ledger.get("shippability") or []
    if not sh:
        out.append("   (none recorded)")
    for line in sh:
        out.append(f"   {line.get('text', '')}   <- {_src(line)}")

    wp = ledger.get("weakest_proxy") or {}
    h("Weakest reality proxy")
    out.append(f"   {wp.get('value') or 'none'}   ({wp.get('coverage', '')})")

    ke = ledger.get("known_escapes") or []
    if ke:
        h("Known escapes (a gate MISSED this -- recall data, NEVER trust-affirming)")
        for line in ke:
            out.append(f"   {line.get('text', '')}   <- {_src(line)}")

    info = ledger.get("informational") or []
    if info:
        h("Informational (no verdict of pass/fail)")
        for line in info:
            out.append(f"   {line.get('text', '')}   <- {_src(line)}")

    return "\n".join(out) + "\n"


def cmd_compose(args: argparse.Namespace) -> int:
    vault = _vault_root(args.vault)
    try:
        ledger = compose(vault, args.slice)
    except LedgerNotFound as e:
        sys.stderr.write(str(e) + "\n")
        return 2
    if args.format == "json":
        payload = json.dumps(ledger, indent=2, ensure_ascii=False)
    else:
        payload = render(ledger, args.format)
    if args.out:
        try:
            Path(args.out).write_text(payload + ("" if payload.endswith("\n") else "\n"), encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"trust_ledger: cannot write --out {args.out}: {e}\n")
            return 2
        print(args.out)
    else:
        print(payload)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    if args.from_path:
        ledger, status = _load_source(Path(args.from_path))
        if status != "ok" or ledger is None:
            sys.stderr.write(f"trust_ledger render: cannot read ledger {args.from_path!r} ({status})\n")
            return 2
    elif args.slice:
        vault = _vault_root(args.vault)
        try:
            ledger = compose(vault, args.slice)
        except LedgerNotFound as e:
            sys.stderr.write(str(e) + "\n")
            return 2
    else:
        sys.stderr.write("trust_ledger render: pass --from <ledger.json> or --slice <slice-NNN>\n")
        return 2
    fmt = args.format if args.format in ("text", "md") else "text"
    print(render(ledger, fmt))
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="trust_ledger",
                                description="Per-slice trust ledger: compose + render (SC-143/PS-002).")
    sub = p.add_subparsers(dest="cmd", required=True)

    cp = sub.add_parser("compose", help="compose trust-ledger.json from a slice's vault evidence")
    cp.add_argument("--slice", required=True, help="slice folder name or canonical slice-NNN")
    cp.add_argument("--vault", default=None, help="vault root (default: resolved for this repo)")
    cp.add_argument("--out", default=None, help="write to this path (default: stdout)")
    cp.add_argument("--format", default="json", choices=("json", "text", "md"),
                    help="json (default, the load-bearing artifact) | text | md (rendered view)")

    rp = sub.add_parser("render", help="render a composed ledger deterministically (pure view)")
    rp.add_argument("--from", dest="from_path", default=None, help="an existing trust-ledger.json to render")
    rp.add_argument("--slice", default=None, help="compose+render this slice (if --from omitted)")
    rp.add_argument("--vault", default=None, help="vault root (with --slice)")
    rp.add_argument("--format", default="text", choices=("text", "md"), help="text (default) | md")

    args = p.parse_args(argv)
    return {"compose": cmd_compose, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
