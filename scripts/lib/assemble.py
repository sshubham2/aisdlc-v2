"""
assemble.py — composes diagnose-out/diagnosis.html from per-pass artifacts.

Output is a self-contained, single-file HTML report with:
- Sticky page header + dark-mode toggle
- Hero card with severity stat tiles
- Left sidebar: TOC + live progress counter (severity-wise addressed/not-addressed)
- Magazine-style executive summary (drop cap, serif body)
- Per-pass sections with severity-tinted finding cards (inline annotation form per card)
- Resolved-since-last-run footer

Owner opens, annotates inline, clicks "Save annotated HTML" to download a copy
with annotations baked into the embedded JSON state. Owner emails it back;
/slice-candidates extracts the JSON to drive the backlog.
"""

from __future__ import annotations

import argparse
import hashlib
import html as htmlmod
import json
import logging
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# Invoked as `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/assemble.py"`, so sys.path[0] is this
# scripts/lib/ dir, not the plugin root. Add the plugin root (parents[2]) so the sibling
# `from scripts.lib.finding_dedup import ...` resolves. No-op if already present.
# (Also covers the write_pass.py -> `from scripts.lib.assemble import ...` path, which runs this.)
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib.finding_dedup import dedupe_findings  # noqa: E402

log = logging.getLogger(__name__)

PASS_ORDER = [
    "01-intent",
    "02-architecture",
    "03a-dead-code",
    "03b-duplicates",
    "03c-size-outliers",
    "03d-half-wired",
    "03e-contradictions",
    "03f-layering",
    "03g-dead-config",
    "03h-test-coverage",
    "04-ai-bloat",
]

# Human-readable section labels for the sidebar TOC.
PASS_LABELS = {
    "01-intent": "What it does",
    "02-architecture": "Architecture",
    "03a-dead-code": "Dead code",
    "03b-duplicates": "Duplicates",
    "03c-size-outliers": "Size outliers",
    "03d-half-wired": "Half-wired features",
    "03e-contradictions": "Contradictions",
    "03f-layering": "Layering",
    "03g-dead-config": "Dead config",
    "03h-test-coverage": "Test coverage",
    "04-ai-bloat": "AI bloat",
}

OVERVIEW_PASS = "00-overview"

REQUIRED_FIELDS = (
    "id", "pass", "category", "severity", "blast_radius", "reversibility",
    "title", "description", "evidence", "suggested_action",
    "effort_estimate", "slice_candidate",
)

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
SEVERITY_ORDER = ["critical", "high", "medium", "low"]


# ---------------------------------------------------------------------------
# Per-pass signature extractors (slice-001 / B2)
# ---------------------------------------------------------------------------
#
# The schema's canonical ID recipe is F-<CAT>-<sha1(category +
# primary_evidence_path + signature)[:8]>. The `signature` is per-pass:
#   03a-dead-code:    function/class/module name being flagged → defaults to title
#   03b-duplicates:   lexicographically smallest path among the duplicates
#   03c-size-outliers: symbol → defaults to title
#   03d-half-wired:   feature concept → defaults to title
#   03e-contradictions: domain concept → defaults to title
#   03f-layering:     module pair → defaults to title
#   03g-dead-config:  config key → defaults to title
#   03h-test-coverage: capability → defaults to title
#   04-ai-bloat:      heuristic-specific → defaults to title
#
# When a subagent emits a malformed ID, normalize_finding() recomputes it
# using these extractors so the recipe stays deterministic across runs
# (preserves owner-annotation carryover per SKILL.md hard rule 4).

_DEFAULT_SIGNATURE = lambda f: str(f.get("title", ""))


def _smallest_evidence_path(f: dict) -> str:
    """For 03b-duplicates: lexicographically smallest path across evidence list."""
    ev = f.get("evidence") or []
    paths = [
        e.get("path", "") if isinstance(e, dict) else str(e)
        for e in ev
    ]
    paths = [p for p in paths if p]
    return sorted(paths)[0] if paths else ""


_signature_extractors: dict[str, Callable[[dict], str]] = {
    "__default__": _DEFAULT_SIGNATURE,
    "01-intent": _DEFAULT_SIGNATURE,
    "02-architecture": _DEFAULT_SIGNATURE,
    "03a-dead-code": _DEFAULT_SIGNATURE,
    "03b-duplicates": _smallest_evidence_path,
    "03c-size-outliers": _DEFAULT_SIGNATURE,
    "03d-half-wired": _DEFAULT_SIGNATURE,
    "03e-contradictions": _DEFAULT_SIGNATURE,
    "03f-layering": _DEFAULT_SIGNATURE,
    "03g-dead-config": _DEFAULT_SIGNATURE,
    "03h-test-coverage": _DEFAULT_SIGNATURE,
    "04-ai-bloat": _DEFAULT_SIGNATURE,
}


# ---------------------------------------------------------------------------
# normalize_finding (slice-001 / B2 / M1)
# ---------------------------------------------------------------------------
#
# Ingest-time tolerance for common LLM-output mistakes. Called by
# write_pass.py before YAML write. NOT called by load_findings() — load
# remains strict so re-runs against existing diagnose-out/ stay
# deterministic (per critique M1).

_ID_SHAPE = re.compile(r"^F-[A-Z]+-[a-f0-9]{8}$")


def _category_short(category: str) -> str:
    """Map a full category name to its short uppercase form for IDs."""
    mapping = {
        "dead-code": "DEAD",
        "duplicate": "DUP",
        "size-outlier": "SIZE",
        "half-wired": "HALF",
        "contradiction": "CONTRA",
        "layering-violation": "LAYER",
        "dead-config": "CONFIG",
        "test-gap": "TEST",
        "ai-bloat": "BLOAT",
        "correctness-bug": "BUG",
        "security": "SEC",
    }
    return mapping.get(category, category.upper().replace("-", "")[:6] or "OTHER")


def _recompute_id(finding: dict, pass_name: str) -> str:
    """Rebuild a canonical-recipe ID from finding fields + pass-specific signature."""
    category = finding.get("category", "other")
    primary_path = ""
    ev = finding.get("evidence") or []
    if ev:
        first = ev[0]
        primary_path = first.get("path", "") if isinstance(first, dict) else str(first)
    extractor = _signature_extractors.get(pass_name) or _signature_extractors["__default__"]
    signature = extractor(finding)
    payload = f"{category}{primary_path}{signature}".encode("utf-8")
    # usedforsecurity=False: this is a content-addressing digest (a stable finding id),
    # NOT a cryptographic integrity check -- clears bandit B324 for the seeded security gate.
    digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:8]
    return f"F-{_category_short(category)}-{digest}"


def _normalize_evidence(evidence) -> list[dict]:
    """Convert evidence shapes (flat strings, partial dicts) to {path, lines, note}."""
    if not isinstance(evidence, list):
        return []
    out = []
    for entry in evidence:
        if isinstance(entry, dict):
            out.append({
                "path": str(entry.get("path", "")),
                "lines": str(entry.get("lines", "")),
                "note": str(entry.get("note", "")),
            })
        elif isinstance(entry, str):
            out.append({"path": entry, "lines": "", "note": ""})
        else:
            log.warning("dropping unrecognized evidence entry type: %r", type(entry))
    return out


def normalize_finding(raw: dict, pass_name: str) -> dict | None:
    """Coerce a subagent-emitted finding into schema shape.

    Returns the normalized finding dict, or None if the entry is
    irrecoverably malformed (no evidence at all after coercion).

    Coercions performed (per slice-001 AC #3 + B2):
    - Unwraps {finding: {...}} or {findings: [{...}]} dict shapes
    - Normalizes evidence: flat strings → {path, lines, note} dicts
    - Recomputes ID via _signature_extractors when shape doesn't match
      ^F-<CAT>-<8hex>$
    - Drops fields not in REQUIRED_FIELDS, with a warning per dropped key
    """
    # Unwrap dict shapes
    if isinstance(raw, dict):
        if "finding" in raw and isinstance(raw["finding"], dict):
            raw = raw["finding"]
        elif "findings" in raw and isinstance(raw["findings"], list) and raw["findings"]:
            # Caller passed the wrapper; take the first entry
            raw = raw["findings"][0]
    if not isinstance(raw, dict):
        log.warning("normalize_finding got non-dict input: %r", type(raw))
        return None

    # Drop unknown fields
    allowed = set(REQUIRED_FIELDS)
    extras = [k for k in raw.keys() if k not in allowed]
    for k in extras:
        log.warning("dropping unknown field %r from finding", k)
    finding = {k: v for k, v in raw.items() if k in allowed}

    # Normalize evidence shape
    finding["evidence"] = _normalize_evidence(finding.get("evidence"))
    if not finding["evidence"]:
        log.warning("finding has no usable evidence after normalization; rejecting")
        return None

    # ID validation + recompute
    current_id = finding.get("id", "")
    if not _ID_SHAPE.match(str(current_id)):
        finding["id"] = _recompute_id(finding, pass_name)
        log.warning(
            "recomputed malformed ID %r → %r for pass %s",
            current_id, finding["id"], pass_name,
        )

    # Set pass field if missing
    finding.setdefault("pass", pass_name)

    # BB-23: YAML 1.1 parses bare `no`/`yes` → bool, but the schema mandates the
    # string `yes|no|maybe`. Coerce back so the persisted YAML + embedded JSON stay
    # strings (the /slice-candidates contract does string equality on this field).
    if isinstance(finding.get("slice_candidate"), bool):
        finding["slice_candidate"] = "yes" if finding["slice_candidate"] else "no"

    return finding


# ---------------------------------------------------------------------------
# Loading inputs
# ---------------------------------------------------------------------------


def _yaml_error_context(text: str, mark) -> str:
    """Render ±2 lines around a yaml problem_mark for human-readable errors."""
    if mark is None:
        return ""
    lines = text.splitlines()
    line_num = mark.line  # 0-indexed
    start = max(0, line_num - 2)
    end = min(len(lines), line_num + 3)
    snippet = []
    for i in range(start, end):
        marker = ">>" if i == line_num else "  "
        snippet.append(f"{marker} {i + 1:4d} | {lines[i]}")
    return "\n".join(snippet)


def load_findings(findings_dir: Path) -> list[dict]:
    """Load findings from <dir>/*.yaml. Strict: no normalization here (M1)."""
    by_id: dict[str, dict] = {}
    if not findings_dir.exists():
        return []
    for path in sorted(findings_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text) or []
        except yaml.YAMLError as exc:
            # M2: gracefully fall back when problem_mark is absent
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                context = _yaml_error_context(text, mark)
                msg = (
                    f"YAML parse failure in {path} at line {mark.line + 1}, "
                    f"column {mark.column + 1}: {exc}\n{context}"
                )
            else:
                msg = f"YAML parse failure in {path} (line/column unknown): {exc}"
            print(msg, file=sys.stderr)
            raise SystemExit(msg)
        if not isinstance(data, list):
            raise SystemExit(f"{path}: top level must be a YAML list")
        for entry in data:
            if not isinstance(entry, dict):  # BB-21: clear exit-1 error, not an AttributeError on entry.get
                raise SystemExit(
                    f"{path}: every finding must be a YAML mapping, got "
                    f"{type(entry).__name__}"
                )
            missing = [f for f in REQUIRED_FIELDS if f not in entry]
            if missing:
                raise SystemExit(
                    f"{path}: finding missing fields {missing}: "
                    f"{entry.get('id', '<no-id>')}"
                )
            by_id[entry["id"]] = entry
    return sorted(
        by_id.values(),
        key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["id"]),
    )


def parse_prior_state(prior_html: Path) -> dict:
    if not prior_html.exists():
        return {}
    text = prior_html.read_text(encoding="utf-8")
    m = re.search(
        r'<script\s+type="application/json"\s+id="diagnose-data">(.*?)</script>',
        text,
        re.DOTALL,
    )
    if not m:
        return {}
    raw = m.group(1).strip()
    raw = raw.replace("<\\/script>", "</script>")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def read_optional(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").rstrip() + "\n"
    return ""


# ---------------------------------------------------------------------------
# Markdown → HTML (constrained subset)
# ---------------------------------------------------------------------------

# BB-22: a URL is safe in an href only if it carries no scheme other than
# http(s)/mailto (or is a fragment/relative path). Anything with a `:` that isn't
# one of those (javascript:, data:, vbscript:) is rejected — the report opens in
# the owner's browser and the markdown is produced from untrusted target code.
_SAFE_URL_RE = re.compile(r"^(?:https?:|mailto:|#|/|\.{1,2}/|[^:]*$)", re.IGNORECASE)


def md_to_html(text: str) -> str:
    if not text or not text.strip():
        return ""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    in_list = False
    in_code = False
    in_table = False
    table_rows: list[list[str]] = []
    para_buf: list[str] = []

    def inline(s: str) -> str:
        s = htmlmod.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
        def _safe_link(mm: "re.Match[str]") -> str:
            label, url = mm.group(1), mm.group(2)
            if _SAFE_URL_RE.match(url):  # BB-22: reject javascript:/data: schemes
                return f'<a href="{url}">{label}</a>'
            return label  # unsafe scheme → render the link text only
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _safe_link, s)
        return s

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            joined = " ".join(p.strip() for p in para_buf)
            out.append(f"<p>{inline(joined)}</p>")
            para_buf = []

    def flush_table() -> None:
        nonlocal table_rows, in_table
        if not table_rows:
            in_table = False
            return
        header = table_rows[0]
        body = table_rows[2:] if len(table_rows) > 2 else []
        out.append('<table class="md-table">')
        out.append("<thead><tr>")
        for cell in header:
            out.append(f"<th>{inline(cell)}</th>")
        out.append("</tr></thead>")
        if body:
            out.append("<tbody>")
            for row in body:
                out.append("<tr>")
                for cell in row:
                    out.append(f"<td>{inline(cell)}</td>")
                out.append("</tr>")
            out.append("</tbody>")
        out.append("</table>")
        table_rows = []
        in_table = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush_para()
            close_list()
            if in_table:
                flush_table()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append('<pre><code class="code-block">')
                in_code = True
            i += 1
            continue
        if in_code:
            out.append(htmlmod.escape(line))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            flush_para()
            close_list()
            if in_table:
                flush_table()
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if re.match(r"^---+$", line.strip()):
            flush_para()
            close_list()
            if in_table:
                flush_table()
            out.append("<hr>")
            i += 1
            continue
        if line.strip().startswith("|") and line.strip().endswith("|"):
            flush_para()
            close_list()
            in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            table_rows.append(cells)
            i += 1
            continue
        else:
            if in_table:
                flush_table()
        m = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if m:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(m.group(2))}</li>")
            i += 1
            continue
        else:
            close_list()
        if not line.strip():
            flush_para()
            i += 1
            continue
        para_buf.append(line)
        i += 1

    flush_para()
    close_list()
    if in_table:
        flush_table()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


_ASSET_DIR = Path(__file__).resolve().parent


def _read_asset(name: str) -> str:
    """Read a sibling static asset (assemble.css / assemble.js) — extracted from
    this module in 3.19.6 and inlined verbatim at render. Universal-newline read
    normalizes any CRLF checkout back to the LF value the inline <style>/<script>
    expects, so the rendered HTML is byte-identical across platforms."""
    return (_ASSET_DIR / name).read_text(encoding="utf-8")


CSS = _read_asset("assemble.css")


# ---------------------------------------------------------------------------
# Client-side JS — annotation form + save-and-download + dark mode + progress
# ---------------------------------------------------------------------------


JS_TEMPLATE = _read_asset("assemble.js")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def esc(v) -> str:
    return htmlmod.escape(safe_str(v))


def severity_class(s) -> str:
    s = safe_str(s)
    return f"sev-{s}" if s in {"critical", "high", "medium", "low"} else ""


def card_severity_class(s) -> str:
    s = safe_str(s)
    return f"finding-card--{s}" if s in {"critical", "high", "medium", "low"} else ""


def pill_severity_class(s) -> str:
    s = safe_str(s)
    return f"pill {s}" if s in {"critical", "high", "medium", "low"} else "pill"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_finding_card(f: dict, anno: dict, status: str) -> str:
    """Self-contained card with inline annotation form. data-severity drives live progress."""
    fid = esc(f["id"])
    title = esc(f["title"])
    sev_raw = safe_str(f.get("severity", "")).lower()
    sev_pill = pill_severity_class(sev_raw)
    card_sev = card_severity_class(sev_raw)
    blast = esc(f.get("blast_radius", ""))
    rev = esc(f.get("reversibility", ""))
    eff = esc(f.get("effort_estimate", ""))
    sc = esc(f.get("slice_candidate", ""))
    desc = esc(safe_str(f.get("description", "")).rstrip()).replace("\n", "<br>")
    sugg = esc(safe_str(f.get("suggested_action", "")).rstrip()).replace("\n", "<br>")

    status_badge = ""
    if status == "NEW":
        status_badge = '<span class="badge new">New</span>'
    elif status == "PERSISTING":
        status_badge = '<span class="badge persisting">Persisting</span>'

    persisting_class = " finding-card--persisting" if status == "PERSISTING" else ""

    ev_items = []
    for e in f.get("evidence", []) or []:
        path = esc(e.get("path", ""))
        lines = esc(e.get("lines", ""))
        note = esc(e.get("note", ""))
        loc = f"{path}:{lines}".rstrip(":")
        ev_items.append(f"<li><code>{loc}</code> — {note}</li>")
    ev_count = len(ev_items)
    ev_html = ""
    if ev_items:
        ev_label = "Evidence" if ev_count == 1 else f"Evidence ({ev_count} items)"
        ev_html = (
            f'<details class="finding-evidence">'
            f"<summary>{ev_label}</summary>"
            f'<ul>{"".join(ev_items)}</ul>'
            f"</details>"
        )

    confirmed = esc(anno.get("confirmed", ""))
    notes = esc(anno.get("notes", ""))
    confirmed_options = ""
    for val in ("", "yes", "no", "defer"):
        sel = " selected" if val == confirmed else ""
        label = "—" if val == "" else val
        confirmed_options += f'<option value="{val}"{sel}>{label}</option>'

    sev_for_class = sev_raw if sev_raw in {"critical", "high", "medium", "low"} else "low"

    return (
        f'<article class="finding-card {card_sev}{persisting_class}" '
        f'id="finding-{fid}" data-finding-id="{fid}" data-severity="{sev_for_class}">'
        f'<div class="finding-id-line">'
        f'<span class="finding-id">{fid}</span>'
        f'<span class="{sev_pill}">{esc(sev_raw)}</span>'
        f"{status_badge}"
        f"</div>"
        f'<h4 class="finding-title">{title}</h4>'
        f'<div class="finding-meta">'
        f'<span>Blast: {blast}</span><span class="sep">·</span>'
        f'<span>Reversibility: {rev}</span><span class="sep">·</span>'
        f'<span>Effort: {eff}</span><span class="sep">·</span>'
        f'<span>Slice candidate: {sc}</span>'
        f"</div>"
        f'<div class="finding-body">'
        f"<p>{desc}</p>"
        f"{ev_html}"
        f'<p class="finding-action"><strong>Suggested action:</strong> {sugg}</p>'
        f"</div>"
        f'<div class="finding-annotation">'
        f'<label for="conf-{fid}">Confirmed</label>'
        f'<select class="confirmed" id="conf-{fid}">{confirmed_options}</select>'
        f'<label for="notes-{fid}">Notes</label>'
        f'<textarea class="notes" id="notes-{fid}" rows="2" '
        f'placeholder="Optional context, scope adjustments, or rationale">{notes}</textarea>'
        f"</div>"
        f"</article>"
    )


def render_hero(findings: list[dict]) -> str:
    sev_counts = Counter(safe_str(f.get("severity", "")).lower() for f in findings)
    total = len(findings)
    crit = sev_counts.get("critical", 0)
    high = sev_counts.get("high", 0)
    med = sev_counts.get("medium", 0)
    low = sev_counts.get("low", 0)

    by_sev: dict[str, list[dict]] = {s: [] for s in SEVERITY_ORDER}
    for f in findings:
        s = safe_str(f.get("severity", "")).lower()
        if s in by_sev:
            by_sev[s].append(f)

    if total == 0:
        eyebrow = "Forensic analysis · All clear"
        headline = "No findings."
        sub = "All forensic passes ran clean. The codebase shows no issues across the dimensions checked."
    else:
        eyebrow = "Forensic codebase analysis"
        if crit > 0:
            headline = (
                f"<strong>{crit}</strong> critical finding{'s' if crit != 1 else ''} "
                f"need attention &mdash; plus {total - crit} more."
            )
        elif high >= 3:
            headline = f"<strong>{high}</strong> high-severity findings to triage."
        else:
            headline = f"<strong>{total}</strong> finding{'s' if total != 1 else ''} to review."
        sub = (
            "Each finding below has its own card with evidence, suggested action, and an inline annotation form. "
            "Click any severity tile to jump to the first finding of that level. "
            "When done, hit <strong>Save annotated HTML</strong> at the top to download a copy."
        )

    def tile(sev: str, n: int, label: str) -> str:
        zero_class = " zero" if n == 0 else ""
        if n > 0 and by_sev[sev]:
            first_id = esc(by_sev[sev][0]["id"])
            return (
                f'<a class="stat-tile {sev}{zero_class}" href="#finding-{first_id}">'
                f'<span class="stat-num">{n}</span>'
                f'<span class="stat-label">{label}</span>'
                f"</a>"
            )
        else:
            return (
                f'<div class="stat-tile {sev}{zero_class}">'
                f'<span class="stat-num">{n}</span>'
                f'<span class="stat-label">{label}</span>'
                f"</div>"
            )

    return f"""
<section class="hero">
  <div class="hero-card">
    <div class="hero-eyebrow">{eyebrow}</div>
    <h2 class="hero-headline">{headline}</h2>
    <p class="hero-sub">{sub}</p>
    <div class="stat-tiles">
      {tile("critical", crit, "Critical")}
      {tile("high", high, "High")}
      {tile("medium", med, "Medium")}
      {tile("low", low, "Low")}
    </div>
  </div>
</section>
"""


def render_progress_block(findings: list[dict]) -> str:
    """Live progress counter for the sidebar. Numbers populated from JS."""
    total = len(findings)
    sev_counts = Counter(safe_str(f.get("severity", "")).lower() for f in findings)

    sev_rows = []
    for sev in SEVERITY_ORDER:
        n = sev_counts.get(sev, 0)
        sev_rows.append(
            f'<div class="sev-progress {sev}">'
            f'<span class="sev-dot"></span>'
            f'<div>'
            f'<div class="sev-name">{sev.capitalize()}</div>'
            f'<div class="sev-track"><div class="sev-fill" id="sev-fill-{sev}"></div></div>'
            f'</div>'
            f'<span class="sev-count" id="sev-count-{sev}">0 / {n}</span>'
            f'</div>'
        )

    return (
        '<div class="progress-block">'
        f'<div class="progress-overall">'
        f'<span class="total" id="progress-overall-num">0</span>'
        f'<span class="of"> / <span id="progress-overall-total">{total}</span></span>'
        f'</div>'
        '<div class="progress-sub">findings reviewed</div>'
        '<div class="progress-bar"><div class="progress-fill" id="progress-overall-fill"></div></div>'
        '<div class="sev-progress-list">'
        + "".join(sev_rows) +
        '</div>'
        '</div>'
    )


def render_sidebar(findings: list[dict], findings_by_pass: dict, has_resolved: bool) -> str:
    """TOC + live progress counter."""
    parts = ['<aside class="sidebar" id="sidebar">']
    parts.append(render_progress_block(findings))

    parts.append('<h3>Sections</h3>')
    parts.append('<ul class="toc-list">')
    parts.append(
        '<li><a href="#exec-summary"><span>Executive summary</span></a></li>'
    )
    extra_passes = [p for p in sorted(findings_by_pass) if p not in PASS_ORDER]
    for p in PASS_ORDER + extra_passes:  # BB-06: include off-PASS_ORDER passes in the TOC
        items = findings_by_pass.get(p, [])
        n = len(items)
        label = PASS_LABELS.get(p, p)
        empty_class = " empty" if n == 0 else ""
        parts.append(
            f'<li><a class="{empty_class.strip()}" href="#pass-{p}">'
            f'<span>{label}</span>'
            f'<span class="toc-count">{n}</span>'
            f'</a></li>'
        )
    if has_resolved:
        parts.append('<li><a href="#resolved"><span>Resolved</span></a></li>')
    parts.append('</ul>')
    parts.append('</aside>')
    parts.append('<div class="sidebar-overlay" id="sidebar-overlay"></div>')
    return "\n".join(parts)


def render_resolved_section(resolved_ids: list[str], prior_anno: dict) -> str:
    if not resolved_ids:
        return ""
    rows = []
    for fid in resolved_ids:
        anno = prior_anno.get(fid, {})
        rows.append(
            f'<tr><td class="id-col">{esc(fid)}</td>'
            f"<td>{esc(anno.get('confirmed', ''))}</td>"
            f"<td>{esc(anno.get('notes', ''))}</td></tr>"
        )
    return (
        '<section class="resolved" id="resolved">'
        "<h2>Resolved since last run</h2>"
        '<table class="resolved-table">'
        "<thead><tr><th>ID</th><th>Prior Confirmed</th><th>Prior Notes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Main assemble
# ---------------------------------------------------------------------------


def assemble(out_dir: Path) -> None:
    sections_dir = out_dir / "sections"
    findings_dir = out_dir / "findings"
    summary_dir = out_dir / "summary"
    diagnosis_path = out_dir / "diagnosis.html"
    prev_path = out_dir / "diagnosis.prev.html"

    for d in (sections_dir, findings_dir, summary_dir):
        if not d.exists():
            raise SystemExit(f"Required directory missing: {d}")

    findings = load_findings(findings_dir)
    # Cross-pass de-duplication (shared finding_dedup): collapse findings that point at
    # the same code location, independent of pass/category — the per-pass content-ID
    # recipe can't, because `category` is in the hash. Singletons pass through unchanged
    # (same id, no new fields); clusters become one F-MRG-* finding carrying merged_ids.
    findings, merge_report = dedupe_findings(findings)

    prior_state = parse_prior_state(diagnosis_path)
    prior_anno: dict[str, dict] = prior_state.get("annotations", {}) or {}
    prior_finding_ids: set[str] = set()
    for f in prior_state.get("findings", []) or []:
        if isinstance(f, dict) and "id" in f:
            prior_finding_ids.add(f["id"])
    for fid in prior_state.get("resolved_finding_ids", []) or []:
        prior_finding_ids.add(fid)

    # A prior finding is RESOLVED only if neither its id NOR (post-merge) any id it was
    # folded into is present now — so "current" includes every merged constituent id.
    current_ids: set[str] = set()
    for f in findings:
        current_ids.add(f["id"])
        current_ids.update(f.get("merged_ids", []) or [])
    resolved_ids = sorted(prior_finding_ids - current_ids)

    # 3.19.2: a CURRENT finding PERSISTS from the prior run if its id — or any constituent
    # id it was merged from — was seen last run (prior_finding_ids). This is INDEPENDENT of
    # whether the owner annotated it: carried_anno (below) is annotation carryover ONLY, not
    # the persistence signal. Keying status off carried_anno mislabeled every UNANNOTATED
    # persisting finding as NEW and inflated the `new` count in the verdict line.
    def _is_persisting(f: dict) -> bool:
        if f["id"] in prior_finding_ids:
            return True
        return any(mid in prior_finding_ids for mid in (f.get("merged_ids") or []))

    findings_by_pass: dict[str, list[dict]] = {p: [] for p in PASS_ORDER}
    for f in findings:
        findings_by_pass.setdefault(f["pass"], []).append(f)

    # Owner-annotation carryover. Prefer an exact id match; fall back to any constituent
    # id a merged finding was folded from. This is the one-time migration when cross-pass
    # dedup is first introduced: a merged finding's new F-MRG id isn't in the prior run,
    # but its merged_ids are — so the owner's Confirmed/Notes survive the first merged run,
    # re-keyed under the stable F-MRG id for all subsequent runs.
    carried_anno: dict[str, dict] = {}
    for f in findings:
        if f["id"] in prior_anno:
            carried_anno[f["id"]] = prior_anno[f["id"]]
            continue
        for mid in f.get("merged_ids", []) or []:
            if mid in prior_anno:
                carried_anno[f["id"]] = prior_anno[mid]
                break

    # ----- Executive summary -----
    overview_md = read_optional(sections_dir / f"{OVERVIEW_PASS}.md")
    if overview_md.strip():
        exec_summary_html = md_to_html(overview_md)
    else:
        exec_blocks_html = []
        for p in PASS_ORDER:
            path = summary_dir / f"{p}.md"
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                rendered = md_to_html(text)
                exec_blocks_html.append(
                    f'<div class="pass-summary">'
                    f'<span class="pass-name">{esc(p)}</span>'
                    f"{rendered}</div>"
                )
        exec_summary_html = "\n".join(exec_blocks_html) if exec_blocks_html else (
            "<p><em>No executive summary produced.</em></p>"
        )

    # ----- Stats / verdict -----
    sev_counts = Counter(f["severity"] for f in findings)
    persisting_count = sum(1 for f in findings if _is_persisting(f))  # 3.19.2: not len(carried_anno)
    new_count = len(findings) - persisting_count
    verdict_line = (
        f"{len(findings)} findings — "
        f"{sev_counts.get('critical', 0)} critical, "
        f"{sev_counts.get('high', 0)} high, "
        f"{sev_counts.get('medium', 0)} medium, "
        f"{sev_counts.get('low', 0)} low. "
        f"{new_count} new, {persisting_count} persisting, "
        f"{len(resolved_ids)} resolved since last run."
    )

    # ----- Per-pass sections, each wrapped with id="pass-XXX" for TOC anchors -----
    sections_html_parts: list[str] = []
    # BB-06: also render passes NOT in PASS_ORDER (a subagent typo in `pass:` must not
    # silently drop a finding that IS still counted in the hero/verdict + embedded JSON).
    extra_passes = [p for p in sorted(findings_by_pass) if p not in PASS_ORDER]
    for p in PASS_ORDER + extra_passes:
        section_md = read_optional(sections_dir / f"{p}.md")
        items = findings_by_pass.get(p, [])
        if not section_md.strip() and not items:
            continue
        label = PASS_LABELS.get(p, p)
        sections_html_parts.append(
            f'<section class="pass-section" id="pass-{p}">'
            f'<div class="section-eyebrow">Pass · {esc(p)}</div>'
        )
        if section_md.strip():
            sections_html_parts.append(md_to_html(section_md))
        else:
            sections_html_parts.append(f"<h2>{esc(label)}</h2>")
        for f in items:
            anno = carried_anno.get(f["id"], {})  # annotation carryover only (may be empty)
            status = "PERSISTING" if _is_persisting(f) else "NEW"  # 3.19.2: persistence != annotation
            sections_html_parts.append(render_finding_card(f, anno, status))
        sections_html_parts.append("</section>")
    sections_html = "\n".join(sections_html_parts)

    hero_html = render_hero(findings)
    sidebar_html = render_sidebar(findings, findings_by_pass, bool(resolved_ids))
    resolved_html = render_resolved_section(resolved_ids, prior_anno)

    # ----- Embedded JSON state -----
    state = {
        "version": 2,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "findings": findings,
        "annotations": carried_anno,
        "resolved_finding_ids": resolved_ids,
    }
    json_state = json.dumps(state, indent=2, default=str)
    json_state_safe = json_state.replace("</", "<\\/")

    generated = state["generated"]
    meta = (
        f"Generated {esc(generated)} &nbsp;•&nbsp; "
        f"{esc(verdict_line)}"
    )

    hint_html = ""
    if findings:
        hint_html = (
            '<p class="hint">'
            "💡 Annotate findings inline below — set <strong>Confirmed</strong> "
            "to <code>yes</code>, <code>no</code>, or <code>defer</code> on each card, "
            "and add free-form <strong>Notes</strong>. The sidebar tracks your progress live. "
            "When done, click <strong>Save annotated HTML</strong> at the top to download a copy."
            "</p>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codebase Diagnosis</title>
<style>{CSS}</style>
</head>
<body>
<div class="page-grid">
<header class="page-header">
  <button class="icon-btn sidebar-toggle" id="sidebar-toggle" type="button" aria-label="Toggle sidebar">☰</button>
  <h1>Codebase <span class="accent">Diagnosis</span></h1>
  <div class="meta">{meta}</div>
  <div class="actions">
    <button class="icon-btn" id="theme-toggle" type="button" aria-label="Toggle dark mode">☾</button>
    <button id="save-btn" type="button">Save annotated HTML</button>
    <span id="save-status"></span>
  </div>
</header>

{sidebar_html}

<main>
{hero_html}

<section class="exec-summary" id="exec-summary">
<h2>Executive summary</h2>
{exec_summary_html}
</section>

{hint_html}

{sections_html}

{resolved_html}
</main>
</div>

<script type="application/json" id="diagnose-data">{json_state_safe}</script>
<script>{JS_TEMPLATE}</script>
</body>
</html>
"""

    if diagnosis_path.exists():
        shutil.copy2(diagnosis_path, prev_path)
    diagnosis_path.write_text(html, encoding="utf-8")

    print(verdict_line)
    if merge_report:
        collapsed = sum(len(r["merged_ids"]) for r in merge_report) - len(merge_report)
        print(
            f"De-duplicated {collapsed} finding(s) into {len(merge_report)} merged "
            f"cluster(s) across passes."
        )
    print(f"Wrote: {diagnosis_path}")
    if prev_path.exists():
        print(f"Rotated prior to: {prev_path}")


def main() -> None:
    # UTF8-STDOUT-1: UTF-8 stdout/stderr so non-ASCII YAML-error snippets + vault
    # paths don't raise UnicodeEncodeError on a cp1252 console.
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(description="Assemble diagnose-out/diagnosis.html")
    ap.add_argument("--out", required=True, help="Path to diagnose-out directory")
    args = ap.parse_args()
    assemble(Path(args.out).resolve())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"assemble.py failed: {exc}", file=sys.stderr)
        sys.exit(2)
