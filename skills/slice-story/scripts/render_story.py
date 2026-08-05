#!/usr/bin/env python3
"""Render a slice STORY (story-sections.json) into one self-contained story.html.

Deterministic, dependency-free (stdlib only). The /slice-story sub-agent produces
the narrative as structured `story-sections.json`; this script owns the styling so
the look is consistent run-to-run and the agent never hand-writes HTML/CSS.

The input schema is `aisdlc/story-sections@1` (see
skills/slice-story/examples/story-sections.json). All fields are treated as
optional and rendered defensively — a partial story still produces a valid page.

The HTML is a single file with inline CSS: readable for a mixed technical /
non-technical audience, print-friendly, no external assets, no JavaScript.

Usage:
    python render_story.py --sections-file story-sections.json --out story.html
    cat story-sections.json | python render_story.py --out story.html
    python render_story.py --sections-file s.json            # writes story.html beside it
    # slice-043: compose the design-tournament detail INTO the one page (replaces the old separate tournament.html)
    python render_story.py --sections-file s.json --slice-dir <slice folder> --gate-log <vault>/gate-log.json --out story.html

Exit codes (the combined exit-code contract; render_story.main() is the single authority):
    0  rendered (story only, or story + composed tournament)
    1  bad/empty input JSON                       -> NOTHING written
    2  usage error (unreadable input / unwritable output) -> NOTHING written
    3  JARGON-LEAK: pipeline jargon found in prose fields (DD-9 tripwire) -> NOTHING
       written; re-translate the named fields or re-run with --allow-jargon
    4  rendered, but the composed design-tournament detail was UNAVAILABLE (malformed
       design-proposals.json / tournament render error): the story half + a visible
       "tournament view unavailable" notice IS written and delivered; the cause is on
       stderr (M1/M3). Only reachable with --slice-dir.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
# slice-043: also put THIS script's dir on the path so the composer can import its sibling render_tournament.
_SKILL_SCRIPTS = Path(__file__).resolve().parent
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from scripts.lib import _stdout  # noqa: E402

# ---------------------------------------------------------------- mini-markdown
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_CODE_RE = re.compile(r"`([^`]+?)`")


def _inline(text: str) -> str:
    """Escape, then apply a tiny safe inline subset: `code` and **bold**."""
    out = html.escape(text, quote=False)
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    return out


def _md(text: str) -> str:
    """Render a small markdown subset to HTML: paragraphs, `- ` bullet lists,
    single-newline line breaks, inline **bold** / `code`. No raw HTML passthrough."""
    if not text:
        return ""
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    parts: list[str] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""]
        if lines and all(ln.lstrip().startswith(("- ", "* ")) for ln in lines):
            lis = "".join(f"<li>{_inline(ln.lstrip()[2:])}</li>" for ln in lines)
            parts.append(f"<ul>{lis}</ul>")
        else:
            joined = "<br>".join(_inline(ln) for ln in lines)
            parts.append(f"<p>{joined}</p>")
    return "\n".join(parts)


# ------------------------------------------------------------ jargon tripwire
# DD-9: "no pipeline jargon ever reaches the page" was prompt-enforced only; this is
# the deterministic check. Banned tokens mirror agents/slice-story.md §Banned vocabulary.
# Scanned: prose fields only (`ref`/`badge` are the sanctioned homes for trace tags).
# Deliberately NOT matched: bare "overridden"/"escalated"/"deferred" (common English —
# too false-positive-prone; the narrator prompt still bans their pipeline sense).
_JARGON_RES = [
    # pipeline rule codes (TRI-1, SVW-1, WIRE-1, PCA-1, DR-1, CRD-1, …)
    re.compile(r"\b(?:TRI|SVW|WIRE|PCA|DR|CRD|CRP|WS|ETC|VAL|BCSG|SRSC|SCMD|PTFCD|DCE|NAW|STP|BCI|SUP|CAND|BC)-\d+\b"),
    # trace ids that belong in `ref` fields, never prose (AC1, C2, R-27, ADR-014, SC-031, M-add-2)
    re.compile(r"\b(?:AC\d+|C\d+|R-\d+|ADR-\d+|SC-\d+|BC-PROJ-\d+|M-add-\d+)\b"),
    # pipeline plumbing vocabulary
    re.compile(r"(?i)\b(?:blast[- ]radius|auto-advance|dispositions?|accepted-(?:pending|fixed)|"
               r"mission[- ]brief|slice loop|the Critic|the Builder|the vault)\b"),
    # slice-082 (M4/m2): a TRANSCRIPTION backstop for the capability-rollup vocabulary. The projection
    # (story_inputs.py) already strips these from the substrate render_story renders deterministically, so
    # this fires only on the OTHER leak path — the narrator transcribing them from design.json/ADR-093
    # embedded in its prompt. UNDERSCORE-EXACT identifiers + the EXACT done_definition phrase ONLY: never a
    # space/hyphen-broadened form, because the plain-language 'no children' / 'rejected only' the narrator
    # MUST be free to write are legitimate prose — a broadened regex would false-FAIL them (APED-1 cry-wolf).
    re.compile(r"\b(?:rejected_only|no_children|pulse_line|done_definition)\b"),
    re.compile(r"materialized candidate archived"),
]


def _jargon_hits(text: str, where: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if isinstance(text, str) and text:
        for rx in _JARGON_RES:
            for m in rx.finditer(text):
                hits.append((where, m.group(0)))
    return hits


def scan_jargon(data: dict) -> list[tuple[str, str]]:
    """Return (field-path, leaked-token) pairs across all PROSE fields."""
    hits: list[tuple[str, str]] = []
    hits += _jargon_hits(data.get("headline", ""), "headline")
    hits += _jargon_hits(data.get("tldr_md", ""), "tldr_md")
    # slice-082: the narrator's optional one-line product-shape framing is prose too (the COUNTS are the
    # deterministic product_shape block, not scanned — they carry no jargon by construction).
    hits += _jargon_hits(data.get("product_shape_framing", ""), "product_shape_framing")
    for i, sec in enumerate(data.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        base = f"sections[{i}]"
        hits += _jargon_hits(sec.get("heading", ""), f"{base}.heading")
        hits += _jargon_hits(sec.get("body_md", ""), f"{base}.body_md")
        hits += _jargon_hits(sec.get("tech_note_md", ""), f"{base}.tech_note_md")
        for j, it in enumerate(sec.get("items") or []):
            if isinstance(it, dict):
                hits += _jargon_hits(it.get("label", ""), f"{base}.items[{j}].label")
                hits += _jargon_hits(it.get("detail", ""), f"{base}.items[{j}].detail")
    # slice-086 (M2/M4): the signoff panel is no longer narrator prose — it is the DETERMINISTIC
    # `trust_signoff` block story_signoff.inject derives from the trust ledger (jargon-free by
    # construction: a CLOSED gate/verdict->English table, no raw gate id / verdict enum for the known
    # vocabulary). So scan_jargon no longer scans a `signoff` key (the narrator stops authoring it), and
    # AC2 rests on the closed table + a dedicated substring test, NOT on this tripwire (which never matched
    # gate ids / verdict enums anyway, and whose exit-3 would contradict the AC4 fail-visible pass-through).
    for j, g in enumerate(data.get("glossary") or []):
        if isinstance(g, dict):
            hits += _jargon_hits(g.get("plain", ""), f"glossary[{j}].plain")
    return hits


# ------------------------------------------------------------------- rendering
_STAGE_LABELS = {
    "pre-build": "Before building",
    "built": "Built",
    "reviewed": "Code reviewed",
    "validated": "Reality tested",
    "shipped": "Shipped",
}

_BADGE_CLASS = {
    "target": "b-target",
    "proven": "b-good",
    "changed-course": "b-warn",
    "fixed": "b-good",
    "noted": "b-grey",
    "deferred": "b-warn",
    "decision": "b-decision",
}


def _chip(text: str, cls: str = "chip") -> str:
    return f'<span class="{cls}">{html.escape(str(text), quote=False)}</span>'


def _render_items(items: list) -> str:
    if not items:
        return ""
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = _inline(str(it.get("label", "")).strip())
        detail = it.get("detail")
        ref = it.get("ref")
        badge = it.get("badge")
        badge_html = ""
        if badge:
            cls = _BADGE_CLASS.get(str(badge).strip().lower(), "b-grey")
            badge_html = f'<span class="badge {cls}">{html.escape(str(badge), quote=False)}</span>'
        ref_html = f'<span class="ref">{html.escape(str(ref), quote=False)}</span>' if ref else ""
        detail_html = f'<div class="item-detail">{_inline(str(detail))}</div>' if detail else ""
        rows.append(
            '<li class="item">'
            f'<div class="item-head">{badge_html}<span class="item-label">{label}</span>{ref_html}</div>'
            f"{detail_html}"
            "</li>"
        )
    return f'<ul class="items">{"".join(rows)}</ul>' if rows else ""


def _render_section(sec: dict) -> str:
    if not isinstance(sec, dict):
        return ""
    heading = html.escape(str(sec.get("heading", "")).strip(), quote=False)
    tone = str(sec.get("tone", "all")).strip().lower()
    body = _md(str(sec.get("body_md", "")))
    items = _render_items(sec.get("items") or [])
    tech = sec.get("tech_note_md")
    tech_html = ""
    if tech:
        tech_html = (
            '<div class="tech-note"><div class="tech-note-label">For the engineer</div>'
            f"{_md(str(tech))}</div>"
        )
    cls = "section tech" if tone == "tech" else "section"
    return (
        f'<section class="{cls}">'
        f"<h2>{heading}</h2>"
        f"{body}{items}{tech_html}"
        "</section>"
    )


def _render_glossary(glossary: list) -> str:
    if not glossary:
        return ""
    rows = []
    for g in glossary:
        if not isinstance(g, dict):
            continue
        term = html.escape(str(g.get("term", "")).strip(), quote=False)
        plain = _inline(str(g.get("plain", "")).strip())
        if term:
            rows.append(f"<dt>{term}</dt><dd>{plain}</dd>")
    if not rows:
        return ""
    return (
        '<section class="glossary"><h2>A few terms, in plain words</h2>'
        f'<dl>{"".join(rows)}</dl></section>'
    )


def _render_trust_signoff(block: dict) -> str:
    """Render the 'Who has signed off' panel from the DETERMINISTIC trust_signoff block
    (slice-086 / [[ADR-102]]). The block is derived from the trust ledger and stamped by
    story_signoff.inject on the MAIN THREAD; the narrator NEVER authors it. render reads ONLY
    this stamped block — there is no code path from the narrator's `signoff` to the panel
    (Biba no-write-up, severed by construction, not merely guarded).

    An absent / unstamped / state!='ok' block renders a visible 'trust classification
    unavailable' notice with NO green column and NO narrator fallback (AC4)."""
    if not isinstance(block, dict) or block.get("_source") != "story_signoff.inject":
        return ""  # not the main-thread-injected block -> render NOTHING (never a narrator characterization)
    if block.get("state") != "ok":
        reason = _inline(str(block.get("unavailable_reason")
                             or "the trust record could not be read.").strip())
        return (
            '<section class="signoff"><h2>Who has signed off</h2>'
            '<div class="signoff-grid"><div class="so-col so-notyet">'
            '<div class="so-label">Trust classification unavailable</div>'
            f"<ul><li>{reason}</li></ul></div></div>"
            '<p class="signoff-foot">This panel is derived mechanically from the recorded evidence; it could '
            "not be shown here, so nothing is being claimed as proven against reality.</p></section>"
        )

    def _items(lst: list) -> str:
        rows = []
        for it in (lst or []):
            if isinstance(it, str):
                what, ref = it.strip(), None
            elif isinstance(it, dict):
                what, ref = str(it.get("what", "")).strip(), it.get("ref")
            else:
                continue
            if not what:
                continue
            ref_html = f'<span class="ref">{html.escape(str(ref), quote=False)}</span>' if ref else ""
            rows.append(f"<li>{_inline(what)}{ref_html}</li>")
        return "".join(rows)

    groups = (
        ("reality_approved", "Proven against reality", "so-reality"),
        ("model_approved", "Reviewed by the model", "so-model"),
        ("not_yet", "Not yet proven against reality", "so-notyet"),
    )
    cols = []
    for key, label, cls in groups:
        body = _items(block.get(key) or [])
        if body:
            cols.append(
                f'<div class="so-col {cls}"><div class="so-label">{label}</div>'
                f"<ul>{body}</ul></div>"
            )
    if not cols:
        return ""
    return (
        '<section class="signoff"><h2>Who has signed off</h2>'
        f'<div class="signoff-grid">{"".join(cols)}</div>'
        '<p class="signoff-foot">Reality testing can say a hard no, so a reality sign-off is the '
        "strongest kind. A review is the model checking the plan or the code &mdash; valuable, but not "
        "the same as proving it against the real world.</p></section>"
    )


def _ip_clause(st: dict) -> str:
    """' (N in progress)' when a stratum has in-flight work (m3), else ''. Plain, jargon-free."""
    ip = int(st.get("in_progress") or 0)
    return f" ({ip} in progress)" if ip else ""


def _render_product_shape(shape: dict, framing: str = "") -> str:
    """slice-082 / [[ADR-093]]: the DETERMINISTIC 'Where this fits in the product' section. render_story
    OWNS the numbers (M-add-1 — they are read from the projected substrate, never transcribed by the
    narrator); the narrator supplies only the optional one-line plain-language `framing`. The section is
    OMITTED when there is no product scope, and renders an honest note on the empty / all-unassigned / error
    states (AC3 graceful degrade — it never crashes and never silently drops a fail-visible state)."""
    if not isinstance(shape, dict):
        return ""
    # CR1 belt-and-braces: render ONLY the deterministic, main-thread-INJECTED block (stamped by
    # story_inputs.inject). A product_shape a narrator authored against its persona rule carries no such
    # stamp, so its LLM-authored counts are never rendered — the M-add-1 'numbers out of the LLM's hands'
    # guarantee holds even if the inject step failed and left an un-overwritten narrator block.
    if shape.get("_source") != "story_inputs.inject":
        return ""
    state = shape.get("state")
    if not state or state == "no_scope":
        return ""                                      # no product scope -> section omitted entirely
    unit = str(shape.get("unit") or "capabilities")
    framing_html = _md(str(framing)) if isinstance(framing, str) and framing.strip() else ""

    def _wrap(inner: str) -> str:
        return (f'<section class="section product-shape"><h2>Where this fits in the product</h2>'
                f"{framing_html}{inner}</section>")

    if state == "error":
        why = html.escape(str(shape.get("error") or "the product view is unavailable"), quote=False)
        return _wrap(f'<p class="ps-note">Where this slice sits in the wider product could not be shown '
                     f"right now — {why}.</p>")
    if state == "empty_scope":
        note = html.escape(str(shape.get("note") or "No capabilities have been broken out yet."), quote=False)
        return _wrap(f'<p class="ps-note">{note}</p>')

    # populated | degenerate_unassigned — the substrate guarantees whole_app here (M3 branch order).
    w = shape.get("whole_app") or {}
    wd, wt = int(w.get("done") or 0), int(w.get("total") or 0)
    whole = (f'<p class="ps-whole">Across the whole product, '
             f"<strong>{wd} of {wt} {html.escape(unit, quote=False)}</strong> are built{_ip_clause(w)}.</p>")

    body = whole
    areas = shape.get("areas") or []
    if areas:
        rows = []
        for c in areas:
            if not isinstance(c, dict):
                continue
            name = html.escape(str(c.get("name", "")).strip(), quote=False)
            cd, ct = int(c.get("done") or 0), int(c.get("total") or 0)
            rows.append(f'<li><span class="ps-comp">{name}</span> — {cd} of {ct} built{_ip_clause(c)}</li>')
        if rows:
            body += f'<p class="ps-sub">By area:</p><ul class="ps-list">{"".join(rows)}</ul>'
        u = shape.get("unassigned") or {}
        if int(u.get("total") or 0):                   # cross-cutting caps not filed under any area
            ud, ut = int(u.get("done") or 0), int(u.get("total") or 0)
            body += (f'<p class="ps-unassigned">Not yet grouped into an area: '
                     f"{ud} of {ut} built{_ip_clause(u)}.</p>")
    else:
        # degenerate_unassigned: the honest note (m1 — the COMMON live case until SC-183). Never 'no progress'.
        note = html.escape(str(shape.get("note") or ""), quote=False)
        if note:
            body += f'<p class="ps-note">{note}</p>'
    return _wrap(body)


_CSS = """
:root{--ink:#1a1d23;--muted:#5b6470;--line:#e4e7ec;--bg:#fbfbfc;--card:#fff;
--accent:#2563eb;--good:#0f7b4f;--good-bg:#e6f4ed;--warn:#9a6700;--warn-bg:#fdf3d8;
--grey-bg:#eef0f3;--decision:#6d28d9;--decision-bg:#f0e9fb;--target-bg:#e7eefc;--tech-bg:#f5f7fa;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:40px 24px 80px;}
header.top{border-bottom:2px solid var(--line);padding-bottom:22px;margin-bottom:8px;}
.eyebrow{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;}
h1{font-size:30px;line-height:1.2;margin:6px 0 8px;}
.headline{font-size:19px;color:var(--muted);margin:0 0 16px;font-weight:400;}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}
.chip{font-size:12.5px;font-weight:600;color:var(--muted);background:var(--grey-bg);
border-radius:999px;padding:3px 11px;}
.chip.stage{background:var(--target-bg);color:var(--accent);}
.tldr{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:10px;padding:16px 20px;margin:26px 0;}
.tldr .lbl{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);
font-weight:700;margin-bottom:4px;}
.tldr p{margin:.3em 0;}
.signoff{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin:18px 0;}
.signoff h2{font-size:20px;margin:0 0 14px;}
.signoff-grid{display:flex;flex-wrap:wrap;gap:14px;}
.so-col{flex:1 1 220px;border-radius:9px;padding:12px 15px;}
.so-reality{background:var(--good-bg);} .so-model{background:var(--grey-bg);} .so-notyet{background:var(--warn-bg);}
.so-label{font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}
.so-reality .so-label{color:var(--good);} .so-model .so-label{color:var(--muted);} .so-notyet .so-label{color:var(--warn);}
.so-col ul{margin:0;padding-left:1.1em;} .so-col li{margin:.4em 0;font-size:14.5px;}
.so-by{color:var(--muted);font-size:13px;} .signoff-foot{color:var(--muted);font-size:13.5px;margin:14px 0 0;}
.section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:22px 24px;margin:18px 0;}
.section.tech{background:var(--tech-bg);}
.product-shape{border-left:4px solid var(--accent);}
.product-shape .ps-whole{font-size:16.5px;margin:.2em 0 .4em;}
.product-shape .ps-sub{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
color:var(--muted);margin:.6em 0 .3em;}
.product-shape .ps-list{list-style:none;padding-left:0;margin:.2em 0;}
.product-shape .ps-list>li{border-top:1px solid var(--line);padding:7px 0;}
.product-shape .ps-list>li:first-child{border-top:none;}
.product-shape .ps-comp{font-weight:600;}
.product-shape .ps-unassigned,.product-shape .ps-note{color:var(--muted);font-size:14.5px;margin:.5em 0 0;}
.section h2,.glossary h2{font-size:20px;margin:0 0 12px;}
.section p{margin:.6em 0;}
.section code,.tldr code{background:var(--grey-bg);border-radius:5px;padding:1px 5px;
font:13.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
ul{margin:.5em 0;padding-left:1.3em;} li{margin:.25em 0;}
ul.items{list-style:none;padding-left:0;margin:.6em 0;}
ul.items>li.item{border-top:1px solid var(--line);padding:11px 0;}
ul.items>li.item:first-child{border-top:none;}
.item-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;}
.item-label{font-weight:600;}
.item-detail{color:var(--muted);font-size:14.5px;margin-top:2px;}
.badge{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
border-radius:5px;padding:2px 7px;white-space:nowrap;}
.b-target{background:var(--target-bg);color:var(--accent);}
.b-good{background:var(--good-bg);color:var(--good);}
.b-warn{background:var(--warn-bg);color:var(--warn);}
.b-grey{background:var(--grey-bg);color:var(--muted);}
.b-decision{background:var(--decision-bg);color:var(--decision);}
.ref{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#9aa3af;
margin-left:auto;align-self:center;}
.tech-note{background:var(--tech-bg);border:1px dashed var(--line);border-radius:9px;
padding:12px 16px;margin-top:14px;}
.section.tech .tech-note{background:#eef1f5;}
.tech-note-label{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;
color:var(--muted);font-weight:700;margin-bottom:4px;}
.glossary{margin-top:30px;}
.glossary dl{margin:0;} .glossary dt{font-weight:700;margin-top:10px;}
.glossary dd{margin:2px 0 0;color:var(--muted);}
footer.foot{margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
font-size:13px;color:#9aa3af;}
.tournament-scope{margin-top:36px;padding-top:6px;border-top:2px solid var(--line);}
.region-heading{font-size:23px;line-height:1.25;margin:22px 0 2px;}
.region-sub{color:var(--muted);font-size:15px;margin:0 0 6px;}
@media print{body{background:#fff}.section,.tldr,.signoff{break-inside:avoid;border-color:#ccc}.tournament-scope{break-inside:auto}}
"""


def _compose_tournament_region(body: str) -> str:
    """Wrap the tournament body fragment in the .tournament-scope region (so scoped_css() applies) with an
    introducing region heading (m4 -- a clear story/tournament boundary for visual + screen-reader/outline users)."""
    return (
        '<section class="tournament-scope">'
        '<h2 class="region-heading">The design tournament behind this slice</h2>'
        '<p class="region-sub">The technical record of how this slice\'s approach was chosen — '
        'the competing proposals, the source badges, and which reviews ran.</p>'
        f'{body}</section>'
    )


def _compose_tournament_unavailable() -> str:
    """The honest-degradation notice (M1/M3): the readable story IS delivered; the technical half could not
    be rendered. Uses the STORY shell's own classes (it sits OUTSIDE .tournament-scope)."""
    return (
        '<section class="section"><h2>The design tournament behind this slice</h2>'
        '<p>The technical design-tournament detail could not be rendered for this slice (its source records '
        'were unreadable). The plain-language report above is complete and unaffected.</p></section>'
    )


def render(data: dict, *, tournament_section: str = "", tournament_css: str = "") -> str:
    title = html.escape(str(data.get("title") or data.get("slice") or "Slice story"), quote=False)
    slice_id = html.escape(str(data.get("slice", "")).strip(), quote=False)
    headline = _inline(str(data.get("headline", "")).strip())
    stage_raw = str(data.get("stage", "")).strip().lower()
    stage_label = _STAGE_LABELS.get(stage_raw, stage_raw.replace("-", " ").title() if stage_raw else "")
    mode = str(data.get("mode", "")).strip()
    tier = str(data.get("risk_tier", "")).strip()
    generated = str(data.get("generated_at") or "").strip()
    if not generated or generated == "<ts>":
        generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    chips = []
    if stage_label:
        chips.append(_chip(stage_label, "chip stage"))
    if slice_id:
        chips.append(_chip(slice_id))
    if mode:
        chips.append(_chip(f"{mode} mode"))
    if tier:
        chips.append(_chip(f"{tier} risk"))
    chips_html = f'<div class="chips">{"".join(chips)}</div>' if chips else ""

    tldr = data.get("tldr_md")
    tldr_html = ""
    if tldr:
        tldr_html = f'<div class="tldr"><div class="lbl">In short</div>{_md(str(tldr))}</div>'

    # slice-086 (ADR-102): the panel is derived from the trust ledger and injected on the main thread
    # (data['trust_signoff']); the narrator's `signoff` key is NEVER read (the write-up channel is severed).
    signoff_html = _render_trust_signoff(data.get("trust_signoff") or {})
    # slice-082: the deterministic 'Where this fits in the product' section (counts owned by the renderer,
    # M-add-1). Sits after the sign-off panel as orienting context, before the narrative sections.
    product_shape_html = _render_product_shape(
        data.get("product_shape") or {}, data.get("product_shape_framing") or "")

    sections_html = "\n".join(
        _render_section(s) for s in (data.get("sections") or []) if isinstance(s, dict)
    )
    glossary_html = _render_glossary(data.get("glossary") or [])

    headline_html = f'<p class="headline">{headline}</p>' if headline else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — slice story</title>
<style>{_CSS}{tournament_css}</style></head>
<body><div class="wrap">
<header class="top">
<div class="eyebrow">Slice story</div>
<h1>{title}</h1>
{headline_html}
{chips_html}
</header>
{tldr_html}
{signoff_html}
{product_shape_html}
{sections_html}
{glossary_html}
{tournament_section}
<footer class="foot">
Generated {html.escape(generated, quote=False)} from this slice's own working notes — written to be read by
anyone, technical or not. Small grey tags (e.g. <code>AC1</code>, <code>ADR-014</code>) are trace references
for engineers who want to dig into the source notes.
</footer>
</div></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(prog="render_story", description="Render story-sections.json to story.html.")
    parser.add_argument("--sections-file", type=Path, default=None,
                        help="Path to story-sections.json (default: read stdin).")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output story.html path (default: story.html beside the input, or ./story.html).")
    parser.add_argument("--allow-jargon", action="store_true",
                        help="Render despite JARGON-LEAK findings (after a failed re-translate; the leak is "
                             "reported on stderr but does not block).")
    parser.add_argument("--slice-dir", type=Path, default=None,
                        help="slice-043: the slice folder (design-proposals.json/design.json/critique*.json/"
                             "milestone.json). When given, the design-tournament detail is COMPOSED into this one "
                             "page as a second region (replacing the former separate tournament.html).")
    parser.add_argument("--gate-log", type=Path, default=None,
                        help="path to the vault-root gate-log.json (for the composed tournament's 'which reviews ran' panel)")
    args = parser.parse_args(argv)

    try:
        if args.sections_file:
            raw = args.sections_file.read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
    except OSError as e:
        sys.stderr.write(f"render_story: cannot read input: {e}\n")
        return 2

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.stderr.write(f"render_story: input is not valid JSON: {e}\n")
        return 1
    if not isinstance(data, dict) or not data:
        sys.stderr.write("render_story: input JSON is empty or not an object.\n")
        return 1

    leaks = scan_jargon(data)
    if leaks:
        for where, token in leaks:
            sys.stderr.write(f"JARGON-LEAK: {where}: {token!r}\n")
        if not args.allow_jargon:
            sys.stderr.write(
                f"render_story: {len(leaks)} pipeline-jargon leak(s) in prose fields — ask the narrator to "
                "re-translate the named fields (refs belong in `ref`, not prose), or re-run with --allow-jargon.\n"
            )
            return 3

    out = args.out
    if out is None:
        out = (args.sections_file.parent / "story.html") if args.sections_file else Path("story.html")

    # slice-043: compose the design-tournament detail INTO this one page when --slice-dir is given.
    # The story has already passed (valid JSON + jargon-clean here); a tournament-render problem must NOT
    # sink the keystone story deliverable (M1) -- it degrades to the story half + a visible notice and a
    # DISTINCT exit code 4 (M3), so the orchestrator can still deliver story.html.
    tournament_section = ""
    tournament_css = ""
    tournament_degraded = False
    if args.slice_dir is not None:
        import render_tournament as _rt  # sibling on the _SKILL_SCRIPTS path (slice-043)
        try:
            t_body, t_code, _t_slice, _t_title = _rt.render_body(args.slice_dir, args.gate_log)
        except Exception as exc:  # a read-only tournament render must never crash the story delivery (M1)
            sys.stderr.write(f"render_story: tournament view unavailable (render error: {exc})\n")
            t_body, t_code = "", 1
        if t_code == 0:
            tournament_css = _rt.scoped_css()
            tournament_section = _compose_tournament_region(t_body)
        else:
            sys.stderr.write(f"render_story: tournament view unavailable ({t_body or 'render error'})\n")
            tournament_section = _compose_tournament_unavailable()
            tournament_degraded = True

    html_text = render(data, tournament_section=tournament_section, tournament_css=tournament_css)
    try:
        out.write_text(html_text, encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"render_story: cannot write output {out}: {e}\n")
        return 2

    # ASCII-only status line (Windows cp1252-safe).
    composed = "" if args.slice_dir is None else (
        ", tournament composed" if not tournament_degraded else ", tournament UNAVAILABLE (exit 4)")
    print(f"render_story: wrote {out} ({len(html_text)} bytes, {len(data.get('sections') or [])} sections{composed})")
    return 4 if tournament_degraded else 0


if __name__ == "__main__":
    sys.exit(main())
