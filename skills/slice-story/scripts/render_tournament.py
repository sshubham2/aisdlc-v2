#!/usr/bin/env python3
"""Render a slice's DESIGN TOURNAMENT view — a LIVE LIBRARY for render_story.py, not dead code.

Status (read this first): since slice-043 (ADR-030) this module's primary role is as the
imported library `render_story.py` composes the tournament region from (`render_body()` +
`scoped_css()`); its standalone CLI is retained only for back-compat + characterization
tests. Details in the slice-043 paragraph below.

slice-039. The design tournament (`/design-slice`) runs three blind designers and persists their FULL
proposals verbatim to `<slice>/design-proposals.json`, but `design.json` keeps only a one-line summary +
a selected flag. This renderer turns the persisted detail into a readable, owner-facing page: a top
summary, the full per-designer detail, an honest expert-source badge, and a "which reviews ran" panel.

It is the TECHNICAL companion to story.html (the plain-language narrative). Unlike `render_story.py` it has
NO jargon tripwire -- designer names / `core`/`partial` / "the Critic" ARE the content here. It is
deterministic, stdlib-only, read-only.

slice-043 (ADR-030): the design-tournament detail is now COMPOSED INTO the one `story.html` -- `render_story`
imports `render_body()` (the inner fragment, no page chrome) + `scoped_css()` (its CSS namespaced under
`.tournament-scope`) and appends it as a second region of the single combined report. There is no separate
`tournament.html` from `/slice-story` anymore. The standalone `render()` / `main()` CLI (`--out tournament.html`)
is RETAINED for back-compat + the characterization tests (`render() == _page(render_body())`), but is no longer
wired into `/slice-story`.

Anti-hallucination (AC2 / ADR-026): each channeled expert's recorded `source` is classified OFFLINE by
`scripts/lib/expert_provenance.py` into "cites a source" | "self-attested" | "no source". The badge is
HONESTLY labelled -- a "cites a source" badge means a citable source is PRESENT, not that the expert was
confirmed real/live (M2). The provenance source-of-truth is `design-proposals.json`'s expert proposal (the
only artifact carrying `.source`), NOT the lossy `design.json.tournament.channeled_experts` (M1).

Security (M5): every value is HTML-escaped; an expert source is shown as escaped text, and only a single
clean http(s) URL is rendered as a link (with attribute-quoted, scheme-allowlisted href).

Usage:
    python render_tournament.py --slice-dir <slice folder> --gate-log <vault>/gate-log.json --out tournament.html

Exit codes:
    0  rendered (INCLUDING the honest "no design contest captured" page when design-proposals.json is absent)
    1  design-proposals.json is present but not valid JSON
    2  usage error (unreadable input / unwritable output)
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _shard_store  # noqa: E402
from scripts.lib import _stdout  # noqa: E402
from scripts.lib import expert_provenance as ep  # noqa: E402

_CANON_SLICE_RE = re.compile(r"^(slice-\d+)")
_VERDICT_CLASS = {ep.VERIFIED: "b-good", ep.SELF_ATTESTED: "b-warn", ep.UNVERIFIABLE: "b-grey"}
_DESIGNER_LABEL = {
    "designer-practice": "Practice (battle-tested patterns)",
    "designer-crossdomain": "Cross-domain (a pattern borrowed from another field)",
    "designer-expert": "Expert-channeled (a named expert's approach)",
}
_SELECTED_LABEL = {"core": "built on", "partial": "borrowed part of", "none": "set aside"}


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _canon_slice(slice_id: str) -> str:
    m = _CANON_SLICE_RE.match(str(slice_id or "").strip())
    return m.group(1) if m else str(slice_id or "").strip()


def _load_json(path: Path):
    """Tolerant load -> (data, error). Missing file -> (None, None); malformed -> (None, message)."""
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null"), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _is_single_clean_url(text: str) -> bool:
    if not text or any(ch.isspace() for ch in text):
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _render_source(source) -> str:
    """M5: escape always; render a single clean http(s) URL as a safe link, else as escaped text."""
    text = str(source or "").strip()
    if not text:
        return '<span class="muted">(no source recorded)</span>'
    if _is_single_clean_url(text):
        return (f'<a href="{html.escape(text, quote=True)}" rel="noopener noreferrer nofollow">'
                f"{_esc(text)}</a>")
    return _esc(text)


def _str_list(label: str, items) -> str:
    if not isinstance(items, list) or not items:
        return ""
    lis = "".join(f"<li>{_esc(str(it))}</li>" for it in items if str(it).strip())
    return f'<div class="field"><div class="field-label">{_esc(label)}</div><ul>{lis}</ul></div>' if lis else ""


def _kv_list(label: str, items, keys) -> str:
    if not isinstance(items, list) or not items:
        return ""
    rows = []
    for it in items:
        if not isinstance(it, dict):
            rows.append(f"<li>{_esc(str(it))}</li>")
            continue
        head = _esc(it.get(keys[0], "") or "")
        rest = " ".join(
            f'<span class="kv"><b>{_esc(k)}:</b> {_esc(it.get(k, "") or "")}</span>'
            for k in keys[1:] if it.get(k)
        )
        rows.append(f"<li>{head}{(' — ' + rest) if rest else ''}</li>")
    return (f'<div class="field"><div class="field-label">{_esc(label)}</div>'
            f'<ul>{"".join(rows)}</ul></div>') if rows else ""


def _render_invariants(transfer: dict) -> str:
    if not isinstance(transfer, dict):
        return ""
    parts = []
    for key in ("source_domain", "pattern", "rationale"):
        if transfer.get(key):
            parts.append(f'<div class="field"><div class="field-label">{_esc(key.replace("_", " "))}</div>'
                         f"<p>{_esc(transfer[key])}</p></div>")
    invs = transfer.get("invariants")
    if isinstance(invs, list) and invs:
        rows = []
        for inv in invs:
            if not isinstance(inv, dict):
                continue
            status = str(inv.get("status", "")).strip().lower()
            cls = {"holds": "b-good", "must-verify": "b-warn", "fails": "b-grey"}.get(status, "b-grey")
            rows.append(
                f'<li><span class="badge {cls}">{_esc(status or "?")}</span> '
                f'{_esc(inv.get("precondition", ""))}'
                + (f'<div class="muted">{_esc(inv.get("evidence", ""))}</div>' if inv.get("evidence") else "")
                + "</li>")
        if rows:
            parts.append('<div class="field"><div class="field-label">analogy invariants</div>'
                         f'<ul class="invariants">{"".join(rows)}</ul></div>')
    return "".join(parts)


def _render_experts(channeled_experts) -> str:
    rows = ep.classify_experts(channeled_experts)
    if not rows:
        return '<div class="field"><div class="field-label">named experts</div><p class="muted">(none recorded)</p></div>'
    lis = []
    for r in rows:
        cls = _VERDICT_CLASS.get(r["verdict"], "b-grey")
        lis.append(
            '<li class="expert">'
            f'<div class="expert-head"><span class="badge {cls}">{_esc(r["badge"])}</span>'
            f'<span class="expert-name">{_esc(r["name"])}</span></div>'
            f'<div class="muted">source: {_render_source(r["source"])}</div>'
            f'<div class="muted small">{_esc(r["reason"])}</div>'
            "</li>")
    return ('<div class="field"><div class="field-label">named experts (offline source check)</div>'
            f'<ul class="experts">{"".join(lis)}</ul></div>')


def _designer_detail(proposal: dict) -> str:
    designer = proposal.get("designer", "")
    parts = []
    if proposal.get("approach"):
        parts.append(f'<div class="field"><div class="field-label">approach</div><p>{_esc(proposal["approach"])}</p></div>')
    parts.append(_str_list("what's new", proposal.get("whats_new")))
    parts.append(_kv_list("components", proposal.get("components"), ["name", "responsibility", "lives_at"]))
    parts.append(_kv_list("key decisions", proposal.get("key_decisions"), ["decision", "reversibility", "rationale"]))
    parts.append(_str_list("risks", proposal.get("risks")))
    if designer == "designer-practice":
        parts.append(_kv_list("prior art (what it found in practice)", proposal.get("prior_art"),
                              ["pattern", "where", "authority"]))
        parts.append(_str_list("failure modes avoided", proposal.get("failure_modes_avoided")))
        if proposal.get("over_engineering_flag"):
            parts.append('<div class="field"><div class="field-label">over-engineering flag</div>'
                         f'<p>{_esc(proposal.get("over_engineering_note", "flagged"))}</p></div>')
    elif designer == "designer-crossdomain":
        parts.append(_render_invariants(proposal.get("cross_domain_transfer")))
    elif designer == "designer-expert":
        parts.append(_render_experts(proposal.get("channeled_experts")))
        if proposal.get("staleness_note"):
            parts.append('<div class="field"><div class="field-label">staleness note</div>'
                         f'<p>{_esc(proposal["staleness_note"])}</p></div>')
    return "".join(p for p in parts if p)


def _selected_map(design: dict) -> dict:
    out = {}
    tour = (design or {}).get("tournament") or {}
    for pr in tour.get("proposals") or []:
        if isinstance(pr, dict) and pr.get("designer"):
            out[pr["designer"]] = str(pr.get("selected", "")).strip().lower()
    return out


def _render_summary(proposals: list, design: dict) -> str:
    selected = _selected_map(design)
    rows = []
    for pr in proposals:
        if not isinstance(pr, dict):
            continue
        designer = pr.get("designer", "")
        label = _DESIGNER_LABEL.get(designer, designer)  # known label is safe static text; unknown id is escaped once below
        sel = selected.get(designer, "")
        sel_label = _SELECTED_LABEL.get(sel, sel or "—")
        one_line = _esc((pr.get("approach") or "")[:200])
        rows.append(
            f'<tr><td class="d-name">{_esc(label) if designer not in _DESIGNER_LABEL else label}</td>'
            f'<td><span class="sel sel-{sel or "none"}">{_esc(sel_label)}</span></td>'
            f"<td>{one_line}</td></tr>")
    tour = (design or {}).get("tournament") or {}
    rationale = tour.get("selection_rationale")
    rationale_html = (f'<p class="rationale"><b>Why this combination won:</b> {_esc(rationale)}</p>'
                      if rationale else "")
    if not rows:
        return ""
    return ('<section class="card"><h2>The three approaches, at a glance</h2>'
            '<table class="summary"><thead><tr><th>designer</th><th>used</th><th>approach</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{rationale_html}</section>')


def _render_reviews(slice_dir: Path, slice_id: str, gate_log_path: Path) -> str:
    """AC4 / M4: state which design reviews ran, sourced from artifact presence + the milestone marker +
    the vault-root gate-log filtered to THIS slice (canonical id)."""
    crit, _ = _load_json(slice_dir / "critique.json")
    crev, _ = _load_json(slice_dir / "critique-review.json")
    milestone, _ = _load_json(slice_dir / "milestone.json")

    # critique: present -> ran; else milestone marker {step:critique, done:"skipped"} -> skipped; else not run
    crit_skipped = False
    for step in ((milestone or {}).get("progress") or []):
        if isinstance(step, dict) and step.get("step") == "critique" and step.get("done") == "skipped":
            crit_skipped = True
    if isinstance(crit, dict):
        crit_line = (f'<b>Independent design review:</b> ran — verdict <i>{_esc(crit.get("verdict", "?"))}</i> '
                     f'<span class="src">(source: critique.json)</span>')
    elif crit_skipped:
        crit_line = ('<b>Independent design review:</b> deliberately skipped (low-risk, no mandatory trigger) '
                     '<span class="src">(source: milestone marker)</span>')
    else:
        crit_line = '<b>Independent design review:</b> did not run <span class="src">(source: no critique.json)</span>'

    if isinstance(crev, dict):
        crev_line = (f'<b>Second-pass meta-review:</b> ran — verdict <i>{_esc(crev.get("verdict", "?"))}</i> '
                     f'<span class="src">(source: critique-review.json)</span>')
    else:
        crev_line = ('<b>Second-pass meta-review:</b> not run — it is tier-driven (required only on higher-risk / '
                     'methodology work) <span class="src">(source: no critique-review.json)</span>')

    # gate-log rows for this slice
    canon = _canon_slice(slice_id)
    # slice-089/SC-194/AC5 (M1): derive-on-missing so the panel shows real rows on a synced/cloned
    # vault (git-ignored cache absent, shard log present) instead of silently empty. A torn cache/
    # shard RAISES (fail-visible) — caught by render()/render_story as a visible degrade, per
    # must_not_defer[0], never swallowed into [].
    if gate_log_path:
        entries = _shard_store.read_entries(gate_log_path.parent, gate_log_path.name, "entries")
    else:
        entries = []
    gate_rows = []
    for entry in entries:
        if isinstance(entry, dict) and _canon_slice(entry.get("slice", "")) == canon:
            if entry.get("kind") == "miss":  # a RECALL/miss row carries no verdict (gate_log two-kind schema)
                detail = "recall MISS — an issue this gate should have caught but missed"
            else:
                detail = (f'verdict {_esc(entry.get("verdict", "?"))}, '
                          f'reality contact <b>{_esc(entry.get("reality_contact", "?"))}</b>')
            gate_rows.append(f'<li><code>{_esc(entry.get("gate", "?"))}</code> — {detail}</li>')
    gate_html = (f'<div class="field"><div class="field-label">recorded gate log (this slice)</div>'
                 f'<ul>{"".join(gate_rows)}</ul></div>') if gate_rows else ""

    return ('<section class="card"><h2>Which reviews actually ran</h2>'
            f'<p>{crit_line}</p><p>{crev_line}</p>{gate_html}</section>')


def _no_contest_body(reason: str) -> str:
    return (f'<section class="card"><h2>No design contest was captured for this work</h2>'
            f'<p>{_esc(reason)}</p>'
            "<p class=\"muted\">A single-approach (low-risk) piece of work runs no three-designer contest, so "
            "there is no per-designer detail to show. This page is intentionally empty rather than inventing one.</p>"
            "</section>")


def _no_contest_page(slice_id: str, reason: str) -> str:
    return _page(slice_id, "Design tournament", _no_contest_body(reason))


def _footnotes() -> str:
    return (
        '<section class="card notes"><h2>How to read this page</h2>'
        '<p><b>The expert badge is honest about its limits.</b> "cites a source" means a citable external '
        "source is <i>present</i> next to the named expert — it does <b>not</b> mean the source was confirmed "
        "live or that the expert was proven real. A well-formed but fabricated link would still read "
        "\"cites a source\". The substantive check — actually resolving the link and matching it to the expert — "
        "is deliberately later work (a real source can return an error to an automated fetch). \"self-attested\" "
        "means the tool vouched from its own memory with no external source; \"no source\" means nothing was "
        "recorded. (m4 / ADR-026.)</p>"
        '<p><b>What this shows, and what it does not.</b> This page shows what each designer <i>found and '
        "proposed</i> — the sources it cited, the pattern it borrowed, the design it drafted. It does <b>not</b> "
        "show the literal search queries each designer ran; capturing those would change how the designers work, "
        "which is out of scope for this view. (M-add-1.)</p>"
        "</section>")


_CSS = """
:root{--ink:#1a1d23;--muted:#5b6470;--line:#e4e7ec;--bg:#fbfbfc;--card:#fff;--accent:#2563eb;
--good:#0f7b4f;--good-bg:#e6f4ed;--warn:#9a6700;--warn-bg:#fdf3d8;--grey-bg:#eef0f3;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:880px;margin:0 auto;padding:36px 22px 80px;}
header.top{border-bottom:2px solid var(--line);padding-bottom:18px;margin-bottom:10px;}
.eyebrow{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;}
h1{font-size:26px;line-height:1.2;margin:6px 0 4px;}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px;}
.chip{font-size:12px;font-weight:600;color:var(--muted);background:var(--grey-bg);border-radius:999px;padding:3px 10px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:18px 22px;margin:16px 0;}
.card h2{font-size:19px;margin:0 0 12px;}
.card.designer{border-left:4px solid var(--accent);}
table.summary{width:100%;border-collapse:collapse;font-size:14px;}
table.summary th,table.summary td{text-align:left;border-top:1px solid var(--line);padding:7px 8px;vertical-align:top;}
table.summary th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;border-top:none;}
.d-name{font-weight:600;white-space:nowrap;}
.sel{font-size:12px;font-weight:700;border-radius:5px;padding:2px 7px;white-space:nowrap;}
.sel-core{background:var(--good-bg);color:var(--good);}
.sel-partial{background:var(--warn-bg);color:var(--warn);}
.sel-none,.sel-{background:var(--grey-bg);color:var(--muted);}
.rationale{margin:12px 0 0;font-size:14px;color:var(--ink);}
.field{margin:12px 0;}
.field-label{font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin-bottom:3px;}
.field ul{margin:.2em 0;padding-left:1.2em;} .field li{margin:.2em 0;}
.kv{margin-right:10px;} .kv b{color:var(--muted);font-weight:600;}
.badge{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;border-radius:5px;padding:2px 7px;white-space:nowrap;}
.b-good{background:var(--good-bg);color:var(--good);}
.b-warn{background:var(--warn-bg);color:var(--warn);}
.b-grey{background:var(--grey-bg);color:var(--muted);}
ul.experts,ul.invariants{list-style:none;padding-left:0;}
ul.experts>li.expert{border-top:1px solid var(--line);padding:9px 0;}
ul.experts>li.expert:first-child{border-top:none;}
.expert-head{display:flex;gap:8px;align-items:baseline;}
.expert-name{font-weight:600;}
.muted{color:var(--muted);} .small{font-size:13px;}
.src{color:#9aa3af;font-size:12.5px;}
.notes p{font-size:14px;color:var(--muted);}
code{background:var(--grey-bg);border-radius:5px;padding:1px 5px;
font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
a{color:var(--accent);} a:hover{text-decoration:underline;}
footer.foot{margin-top:36px;padding-top:16px;border-top:1px solid var(--line);font-size:12.5px;color:#9aa3af;}
@media print{body{background:#fff}.card{break-inside:avoid;border-color:#ccc}}
"""

SCOPE_CLASS = "tournament-scope"
# Selectors NOT prefixed by scoped_css() -- reconciled into the COMPOSER's shared shell instead:
#   :root + *  -> hoisted (their values are shared/IDENTICAL with the story shell, verified at design spike)
#   body + h1  -> page chrome the fragment never emits (render_body excludes _page)
# Everything else (incl. the bare `code`/`a`/`a:hover` rules) is scoped as a descendant so it cannot bleed
# into the story half (M2).
_SHELL_HOISTED = {":root", "*", "body", "h1"}


def _scope_selector_list(selectors: str, scope: str) -> str:
    """Prefix each comma-separated selector with `.{scope} ` (descendant combinator)."""
    parts = [p.strip() for p in selectors.split(",") if p.strip()]
    return ", ".join(f".{scope} {p}" for p in parts)


def _scope_rules(css: str, scope: str) -> list[str]:
    """Prefix every top-level rule's selector under `.{scope}`, DROPPING the _SHELL_HOISTED rules.
    @media blocks are preserved: their condition stays and their inner rules are prefixed (an inner bare
    body/* is dropped). Tolerant brace scanner -- _CSS is plain (selectors hold no `{`, declarations hold
    no `}`, @-nesting is at most one @media level)."""
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            break
        head = css[i:brace].strip()
        if head.startswith("@media"):
            depth, j = 1, brace + 1
            while j < n and depth:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            inner_scoped = "".join(_scope_rules(css[brace + 1:j - 1], scope))
            if inner_scoped.strip():
                out.append(f"{head}{{{inner_scoped}}}")
            i = j
            continue
        close = css.find("}", brace)
        if close == -1:
            break
        decls = css[brace + 1:close].strip()
        parts = [p.strip() for p in head.split(",") if p.strip()]
        if parts and all(p in _SHELL_HOISTED for p in parts):
            i = close + 1  # drop -- reconciled into the shell
            continue
        out.append(f"{_scope_selector_list(head, scope)}{{{decls}}}")
        i = close + 1
    return out


def scoped_css(scope: str = SCOPE_CLASS) -> str:
    """Return _CSS with every selector namespaced under `.{scope}` so the tournament half cannot restyle
    the story half (M2). The _SHELL_HOISTED rules (:root/*/body/h1) are DROPPED -- the composer's own shell
    supplies the value-identical :root + *; body/h1 are page chrome the fragment never emits."""
    return "\n".join(_scope_rules(_CSS, scope))


def _page(slice_id: str, title: str, body: str) -> str:
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} — {_esc(slice_id)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header class="top">
<div class="eyebrow">Design tournament</div>
<h1>{_esc(title)}</h1>
<div class="chips"><span class="chip">{_esc(slice_id)}</span></div>
</header>
{body}
<footer class="foot">Generated {_esc(generated)} from this slice's own design records — a deterministic,
read-only view; nothing here is paraphrased or invented.</footer>
</div></body></html>
"""


def render_body(slice_dir: Path, gate_log_path: Path) -> tuple[str, int, str, str]:
    """Return (body_html, exit_code, slice_id, page_title) -- the tournament's INNER blocks with NO page
    chrome (no <!doctype>/<head>/<style>/footer), so a composer (render_story) can append them inside ONE
    shared document. On exit 1 body_html is the error message; on the empty path it is the no-contest body.
    render() wraps this in _page() to produce the standalone page (byte-identical to the pre-extraction output)."""
    design, _ = _load_json(slice_dir / "design.json")
    slice_id = _canon_slice((design or {}).get("slice") or slice_dir.name)
    title = str((design or {}).get("slice") or slice_dir.name)

    proposals_data, err = _load_json(slice_dir / "design-proposals.json")
    if err is not None:
        return f"design-proposals.json is not valid JSON: {err}", 1, slice_id, title
    if proposals_data is None:
        return _no_contest_body("No design-proposals.json was found for this work."), 0, slice_id, "Design tournament"

    proposals = proposals_data.get("proposals") if isinstance(proposals_data, dict) else None
    if not isinstance(proposals, list) or not proposals:
        return _no_contest_body("The design records hold no designer proposals to show."), 0, slice_id, "Design tournament"

    blocks = [_render_summary(proposals, design or {})]
    for pr in proposals:
        if not isinstance(pr, dict):
            continue
        designer = pr.get("designer", "")
        label = _DESIGNER_LABEL.get(designer, designer or "(unknown designer)")
        detail = _designer_detail(pr) or '<p class="muted">(no detail recorded for this designer)</p>'
        blocks.append(f'<section class="card designer"><h2>{_esc(label)}</h2>{detail}</section>')
    blocks.append(_render_reviews(slice_dir, slice_id, gate_log_path))
    blocks.append(_footnotes())
    return "\n".join(b for b in blocks if b), 0, slice_id, title


def render(slice_dir: Path, gate_log_path: Path) -> tuple[str, int]:
    """Return (html, exit_code). exit 1 only when design-proposals.json is present but malformed.
    Thin wrapper over render_body() + _page() so the standalone CLI output stays byte-identical."""
    body, code, slice_id, title = render_body(slice_dir, gate_log_path)
    if code != 0:
        return body, code
    return _page(slice_id, title, body), 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(prog="render_tournament",
                                     description="Render a slice's design tournament to tournament.html.")
    parser.add_argument("--slice-dir", type=Path, required=True,
                        help="the slice folder (holds design-proposals.json, design.json, critique*.json, milestone.json)")
    parser.add_argument("--gate-log", type=Path, default=None,
                        help="path to the vault-root gate-log.json (for the 'which reviews ran' panel)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output tournament.html path (default: tournament.html in the slice folder)")
    args = parser.parse_args(argv)

    slice_dir = args.slice_dir
    if not slice_dir.is_dir():
        sys.stderr.write(f"render_tournament: slice dir not found: {slice_dir}\n")
        return 2

    try:
        html_text, code = render(slice_dir, args.gate_log)
    except Exception as exc:  # never crash a read-only render; report and fail visibly
        sys.stderr.write(f"render_tournament: unexpected error: {exc}\n")
        return 2
    if code != 0:
        sys.stderr.write(f"render_tournament: {html_text}\n")
        return code

    out = args.out or (slice_dir / "tournament.html")
    try:
        out.write_text(html_text, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"render_tournament: cannot write {out}: {exc}\n")
        return 2
    print(f"render_tournament: wrote {out} ({len(html_text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
