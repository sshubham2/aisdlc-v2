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

Exit codes:
    0  rendered
    1  bad/empty input JSON
    2  usage error (unreadable input / unwritable output)
    3  JARGON-LEAK: pipeline jargon found in prose fields (DD-9 tripwire) — nothing
       written; re-translate the named fields or re-run with --allow-jargon
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
    so = data.get("signoff") or {}
    if isinstance(so, dict):
        for key in ("reality_approved", "model_approved", "not_yet"):
            for j, it in enumerate(so.get(key) or []):
                if isinstance(it, str):
                    hits += _jargon_hits(it, f"signoff.{key}[{j}]")
                elif isinstance(it, dict):
                    hits += _jargon_hits(it.get("what", ""), f"signoff.{key}[{j}].what")
                    hits += _jargon_hits(it.get("by", ""), f"signoff.{key}[{j}].by")
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


def _render_signoff(signoff: dict) -> str:
    """Render the 'Who has signed off' panel: reality-approved vs model-approved vs
    not-yet, so the reader sees at a glance whether reality or just a review said yes.
    Each list is optional; the whole panel is omitted when nothing applies."""
    if not isinstance(signoff, dict):
        return ""

    def _items(lst: list) -> str:
        rows = []
        for it in (lst or []):
            if isinstance(it, str):
                rows.append(f"<li>{_inline(it.strip())}</li>")
            elif isinstance(it, dict):
                what = _inline(str(it.get("what", "")).strip())
                by = it.get("by")
                ref = it.get("ref")
                by_html = f' <span class="so-by">— {html.escape(str(by), quote=False)}</span>' if by else ""
                ref_html = f'<span class="ref">{html.escape(str(ref), quote=False)}</span>' if ref else ""
                rows.append(f'<li>{what}{by_html}{ref_html}</li>')
        return "".join(rows)

    groups = (
        ("reality_approved", "Proven against reality", "so-reality"),
        ("model_approved", "Reviewed by the model", "so-model"),
        ("not_yet", "Not yet checked against reality", "so-notyet"),
    )
    cols = []
    for key, label, cls in groups:
        body = _items(signoff.get(key) or [])
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
@media print{body{background:#fff}.section,.tldr,.signoff{break-inside:avoid;border-color:#ccc}}
"""


def render(data: dict) -> str:
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

    signoff_html = _render_signoff(data.get("signoff") or {})

    sections_html = "\n".join(
        _render_section(s) for s in (data.get("sections") or []) if isinstance(s, dict)
    )
    glossary_html = _render_glossary(data.get("glossary") or [])

    headline_html = f'<p class="headline">{headline}</p>' if headline else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — slice story</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header class="top">
<div class="eyebrow">Slice story</div>
<h1>{title}</h1>
{headline_html}
{chips_html}
</header>
{tldr_html}
{signoff_html}
{sections_html}
{glossary_html}
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

    html_text = render(data)
    try:
        out.write_text(html_text, encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"render_story: cannot write output {out}: {e}\n")
        return 2

    # ASCII-only status line (Windows cp1252-safe).
    print(f"render_story: wrote {out} ({len(html_text)} bytes, {len(data.get('sections') or [])} sections)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
