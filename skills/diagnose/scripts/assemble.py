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
    digest = hashlib.sha1(payload).hexdigest()[:8]
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
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
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


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ============================================================
   Light theme (default)
   ============================================================ */
:root {
  --bg: #faf8f5;
  --bg-warm: #f4efe5;
  --bg-deep: #efe9dc;
  --surface: #ffffff;
  --surface-raised: #ffffff;
  --fg: #2d2a24;
  --fg-soft: #4a4640;
  --muted: #6b665d;
  --subtle: #8a8377;
  --border: #e5dfd0;
  --border-strong: #d4cab5;
  --border-subtle: #efe9dc;
  --accent: #cc785c;
  --accent-hover: #b3634a;
  --accent-soft: #fbeee5;
  --accent-subtle: #f5e2d6;

  --critical: #b5553e;
  --critical-bg: #fcecea;
  --critical-tint: #fdf5f3;
  --critical-border: #e8b8ad;
  --high: #c2410c;
  --high-bg: #fceedb;
  --high-tint: #fdf6ec;
  --high-border: #e8c69a;
  --medium: #997133;
  --medium-bg: #faf0d3;
  --medium-tint: #fdf8e8;
  --medium-border: #ddc796;
  --low: #6b665d;
  --low-bg: #efeae0;
  --low-tint: #f6f3eb;
  --low-border: #d4cab5;

  --success: #6b8e5a;
  --success-bg: #e9efe2;

  --new: #4a5f99;
  --persisting: #6b665d;
  --resolved: #6b8e5a;
  --code-bg: #f4efe5;

  --shadow-sm: 0 1px 2px rgba(45, 42, 36, 0.05);
  --shadow-md: 0 4px 8px rgba(45, 42, 36, 0.06), 0 2px 4px rgba(45, 42, 36, 0.04);
  --shadow-lg: 0 10px 20px rgba(45, 42, 36, 0.08), 0 4px 8px rgba(45, 42, 36, 0.04);
}

/* ============================================================
   Dark theme — warm dark, editorial
   ============================================================ */
[data-theme="dark"] {
  --bg: #1a1815;
  --bg-warm: #221f1b;
  --bg-deep: #2a2622;
  --surface: #232020;
  --surface-raised: #2a2724;
  --fg: #f0ebdf;
  --fg-soft: #d4cab5;
  --muted: #9a948a;
  --subtle: #7a746b;
  --border: #3a342c;
  --border-strong: #4a4339;
  --border-subtle: #2e2a25;
  --accent: #e89878;
  --accent-hover: #f0a584;
  --accent-soft: #3a2a22;
  --accent-subtle: #2a221d;

  --critical: #e87a62;
  --critical-bg: #3a2018;
  --critical-tint: #2a1814;
  --critical-border: #5a2e22;
  --high: #e89058;
  --high-bg: #3a2616;
  --high-tint: #2a1c12;
  --high-border: #5a3a20;
  --medium: #e0b870;
  --medium-bg: #332916;
  --medium-tint: #251f12;
  --medium-border: #4a3a20;
  --low: #b0aa9e;
  --low-bg: #2a2722;
  --low-tint: #221f1c;
  --low-border: #3a342c;

  --success: #8eb87a;
  --success-bg: #2a3322;

  --new: #7a92cc;
  --persisting: #9a948a;
  --resolved: #8eb87a;
  --code-bg: #2a2622;

  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.25);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.35), 0 2px 4px rgba(0, 0, 0, 0.2);
  --shadow-lg: 0 12px 28px rgba(0, 0, 0, 0.45), 0 4px 8px rgba(0, 0, 0, 0.25);
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html { scroll-behavior: smooth; }

body {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 16px;
  line-height: 1.6;
  color: var(--fg);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  font-feature-settings: "ss01", "cv11";
  transition: background-color 0.3s, color 0.3s;
}

h1, h2, h3, h4, h5, h6 {
  font-family: 'Fraunces', 'Iowan Old Style', Charter, Georgia, serif;
  font-weight: 500;
  line-height: 1.2;
  letter-spacing: -0.01em;
  color: var(--fg);
}

p { margin: 0 0 1em 0; }
strong { color: var(--fg); font-weight: 600; }

code {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  background: var(--accent-subtle);
  color: var(--fg);
  padding: 0.12em 0.45em;
  border-radius: 4px;
  font-size: 0.88em;
  font-weight: 500;
}

pre {
  background: var(--bg-deep);
  color: var(--fg);
  padding: 16px 20px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 1em 0;
  font-size: 14px;
  line-height: 1.65;
  border: 1px solid var(--border);
}
pre code { background: transparent; color: inherit; padding: 0; font-weight: 400; }

a { color: var(--accent); text-decoration: none; transition: color 0.15s; }
a:hover { color: var(--accent-hover); text-decoration: underline; }

ul, ol { padding-left: 1.5em; margin: 0.5em 0 1em 0; }
li + li { margin-top: 0.4em; }

button {
  font-family: 'DM Sans', sans-serif;
  background: var(--accent);
  color: white;
  border: 0;
  padding: 9px 18px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, transform 0.1s, box-shadow 0.15s;
  box-shadow: var(--shadow-sm);
}
button:hover {
  background: var(--accent-hover);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
button:active { transform: translateY(0); }
button.icon-btn {
  background: transparent;
  color: var(--muted);
  padding: 6px 10px;
  box-shadow: none;
  font-size: 18px;
  line-height: 1;
}
button.icon-btn:hover {
  background: var(--bg-warm);
  color: var(--accent);
  transform: none;
  box-shadow: none;
}

/* ============================================================
   Layout grid — page-header / sidebar / main
   ============================================================ */

.page-grid {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  grid-template-areas:
    "header header"
    "sidebar main";
  min-height: 100vh;
}

header.page-header {
  grid-area: header;
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(250, 248, 245, 0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  padding: 12px 28px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  transition: background 0.3s;
}
[data-theme="dark"] header.page-header {
  background: rgba(26, 24, 21, 0.92);
}
header.page-header h1 {
  margin: 0;
  font-family: 'Fraunces', serif;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  flex: 0 0 auto;
  color: var(--fg);
}
header.page-header h1 .accent { color: var(--accent); }
header.page-header .meta {
  color: var(--muted);
  font-size: 13px;
  flex: 1 1 auto;
  letter-spacing: 0.01em;
}
header.page-header .actions { display: flex; align-items: center; gap: 12px; }
#save-status { font-size: 13px; color: var(--muted); font-weight: 500; }
#save-status.dirty { color: var(--high); }
#save-status.saved { color: var(--resolved); }

/* ============================================================
   Sidebar — sticky TOC + live progress counter
   ============================================================ */

aside.sidebar {
  grid-area: sidebar;
  position: sticky;
  top: 56px;
  align-self: start;
  height: calc(100vh - 56px);
  overflow-y: auto;
  background: var(--bg-warm);
  border-right: 1px solid var(--border);
  padding: 24px 22px;
  transition: background 0.3s, border-color 0.3s;
}
aside.sidebar::-webkit-scrollbar { width: 6px; }
aside.sidebar::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 3px; }

aside.sidebar h3 {
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--accent);
  margin: 18px 0 10px 0;
}
aside.sidebar h3:first-child { margin-top: 0; }

/* Progress counter — top of sidebar, live updates */
.progress-block {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px 18px;
  margin-bottom: 22px;
}
.progress-overall {
  font-family: 'Fraunces', serif;
  font-size: 17px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 4px;
  line-height: 1.25;
}
.progress-overall .total {
  font-size: 28px;
  color: var(--accent);
  font-weight: 700;
}
.progress-overall .of { color: var(--muted); font-weight: 500; }
.progress-sub {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 14px;
  letter-spacing: 0.02em;
}
.progress-bar {
  height: 6px;
  background: var(--bg-deep);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 14px;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-hover));
  border-radius: 3px;
  transition: width 0.35s ease;
  width: 0%;
}
.sev-progress-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sev-progress {
  display: grid;
  grid-template-columns: max-content 1fr max-content;
  gap: 8px;
  align-items: center;
  font-family: 'DM Sans', sans-serif;
  font-size: 12px;
}
.sev-progress .sev-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.sev-progress.critical .sev-dot { background: var(--critical); }
.sev-progress.high .sev-dot     { background: var(--high); }
.sev-progress.medium .sev-dot   { background: var(--medium); }
.sev-progress.low .sev-dot      { background: var(--low); }
.sev-progress .sev-name {
  font-weight: 600;
  color: var(--fg-soft);
  letter-spacing: 0.02em;
}
.sev-progress .sev-track {
  height: 4px;
  background: var(--bg-deep);
  border-radius: 2px;
  overflow: hidden;
}
.sev-progress .sev-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.35s ease;
  width: 0%;
}
.sev-progress.critical .sev-fill { background: var(--critical); }
.sev-progress.high .sev-fill     { background: var(--high); }
.sev-progress.medium .sev-fill   { background: var(--medium); }
.sev-progress.low .sev-fill      { background: var(--low); }
.sev-progress .sev-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
  white-space: nowrap;
  letter-spacing: 0.02em;
}

/* TOC list */
.toc-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.toc-list li {
  margin: 0;
  border-radius: 6px;
  transition: background 0.12s;
}
.toc-list li:hover { background: var(--bg-deep); }
.toc-list a {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 10px;
  font-size: 13.5px;
  color: var(--fg-soft);
  text-decoration: none;
  font-weight: 500;
  letter-spacing: 0.01em;
}
.toc-list a:hover { color: var(--accent); text-decoration: none; }
.toc-list a.empty { opacity: 0.45; }
.toc-list .toc-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  color: var(--muted);
  background: var(--bg-deep);
  padding: 1px 6px;
  border-radius: 8px;
  font-weight: 500;
}

/* ============================================================
   Main column
   ============================================================ */

main {
  grid-area: main;
  padding: 32px 48px 96px;
  max-width: 1100px;
  width: 100%;
}
section { margin-bottom: 40px; }

/* ============================================================
   Hero card — first impression
   ============================================================ */

section.hero { margin: 0 0 36px 0; }
.hero-card {
  background: linear-gradient(135deg, var(--surface) 0%, var(--bg-warm) 100%);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 36px 40px;
  box-shadow: var(--shadow-md);
  position: relative;
  overflow: hidden;
}
.hero-card::before {
  content: "";
  position: absolute;
  top: -60px;
  right: -60px;
  width: 240px;
  height: 240px;
  background: radial-gradient(circle, var(--accent-subtle) 0%, transparent 70%);
  pointer-events: none;
}
.hero-eyebrow {
  font-family: 'DM Sans', sans-serif;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--accent);
  margin-bottom: 14px;
  position: relative;
}
.hero-headline {
  font-family: 'Fraunces', serif;
  font-size: 42px;
  font-weight: 500;
  line-height: 1.08;
  letter-spacing: -0.025em;
  color: var(--fg);
  margin-bottom: 14px;
  max-width: 22ch;
  position: relative;
}
.hero-headline strong {
  font-weight: 700;
  color: var(--accent);
}
.hero-sub {
  font-size: 16px;
  color: var(--muted);
  max-width: 60ch;
  margin-bottom: 28px;
  line-height: 1.6;
  position: relative;
}

.stat-tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  position: relative;
}
.stat-tile {
  background: var(--surface);
  border: 1.5px solid var(--border);
  border-radius: 12px;
  padding: 18px 16px 14px;
  text-align: center;
  text-decoration: none;
  color: var(--fg);
  transition: transform 0.18s, box-shadow 0.18s, border-color 0.18s;
  display: block;
}
.stat-tile:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-lg);
  text-decoration: none;
}
.stat-tile.critical { border-color: var(--critical-border); background: var(--critical-tint); }
.stat-tile.critical:hover { border-color: var(--critical); }
.stat-tile.critical .stat-num { color: var(--critical); }
.stat-tile.high { border-color: var(--high-border); background: var(--high-tint); }
.stat-tile.high:hover { border-color: var(--high); }
.stat-tile.high .stat-num { color: var(--high); }
.stat-tile.medium { border-color: var(--medium-border); background: var(--medium-tint); }
.stat-tile.medium:hover { border-color: var(--medium); }
.stat-tile.medium .stat-num { color: var(--medium); }
.stat-tile.low { border-color: var(--low-border); background: var(--low-tint); }
.stat-tile.low:hover { border-color: var(--low); }
.stat-tile.low .stat-num { color: var(--low); }
.stat-tile.zero { opacity: 0.55; }
.stat-num {
  font-family: 'Fraunces', serif;
  font-size: 44px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.03em;
  display: block;
  margin-bottom: 4px;
}
.stat-label {
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
}
.stat-tile.critical .stat-label { color: var(--critical); }
.stat-tile.high .stat-label { color: var(--high); }
.stat-tile.medium .stat-label { color: var(--medium); }
.stat-tile.low .stat-label { color: var(--low); }

/* ============================================================
   Executive summary — magazine layout
   ============================================================ */

section.exec-summary {
  background: var(--surface);
  padding: 40px 48px 32px;
  border-radius: 14px;
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
  margin-bottom: 40px;
}
section.exec-summary > h2 {
  font-size: 30px;
  font-weight: 500;
  margin-bottom: 22px;
  padding-bottom: 14px;
  border-bottom: 2px solid var(--accent-subtle);
  letter-spacing: -0.02em;
}
section.exec-summary h3 {
  font-size: 22px;
  margin-top: 32px;
  margin-bottom: 12px;
  letter-spacing: -0.01em;
}
section.exec-summary h4 {
  font-family: 'DM Sans', sans-serif;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent);
  margin-top: 24px;
  margin-bottom: 8px;
}
section.exec-summary p {
  font-family: 'Fraunces', 'Iowan Old Style', Charter, Georgia, serif;
  font-size: 18px;
  line-height: 1.7;
  color: var(--fg-soft);
  font-weight: 400;
  max-width: 64ch;
  margin-bottom: 1.1em;
}
section.exec-summary p:first-of-type::first-letter {
  font-family: 'Fraunces', serif;
  float: left;
  font-size: 4em;
  line-height: 0.92;
  font-weight: 600;
  color: var(--accent);
  padding: 0.06em 0.1em 0 0;
  margin-right: 0.04em;
}
section.exec-summary blockquote {
  border-left: 4px solid var(--accent);
  background: var(--accent-subtle);
  padding: 16px 22px;
  margin: 22px 0;
  border-radius: 0 8px 8px 0;
  font-style: italic;
  color: var(--fg);
  font-family: 'Fraunces', serif;
  font-size: 17px;
}
section.exec-summary ul, section.exec-summary ol {
  font-family: 'DM Sans', sans-serif;
  font-size: 16px;
  max-width: 64ch;
}
section.exec-summary strong { color: var(--accent-hover); font-weight: 700; }

/* Pass-summary fallback */
.pass-summary {
  font-size: 16px;
  margin-bottom: 18px;
  padding-left: 14px;
  border-left: 2px solid var(--accent-subtle);
}
.pass-summary .pass-name {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-subtle);
  padding: 2px 8px;
  border-radius: 4px;
  margin-bottom: 6px;
  letter-spacing: 0.04em;
}
.pass-summary p { font-size: 15.5px; line-height: 1.65; color: var(--fg-soft); margin: 4px 0; }

/* ============================================================
   Hint banner
   ============================================================ */

.hint {
  background: var(--accent-subtle);
  border-left: 4px solid var(--accent);
  padding: 14px 20px;
  border-radius: 0 8px 8px 0;
  font-size: 15px;
  color: var(--fg-soft);
  margin: 0 0 36px 0;
  line-height: 1.55;
}
.hint strong { color: var(--accent); }

/* ============================================================
   Pass sections
   ============================================================ */

section.pass-section {
  margin-top: 56px;
  scroll-margin-top: 70px;
}
section.pass-section:first-of-type { margin-top: 0; }
section.pass-section .section-eyebrow {
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--accent);
  margin-bottom: 8px;
}
section.pass-section h1,
section.pass-section h2 {
  font-size: 30px;
  font-weight: 500;
  margin-top: 0;
  margin-bottom: 18px;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--accent-subtle);
  letter-spacing: -0.018em;
  color: var(--fg);
}
section.pass-section h3 {
  font-size: 22px;
  margin-top: 32px;
  margin-bottom: 12px;
  color: var(--fg);
}
section.pass-section h4 {
  font-family: 'DM Sans', sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: 26px;
  margin-bottom: 8px;
}
section.pass-section p {
  font-size: 16px;
  line-height: 1.7;
  color: var(--fg-soft);
  margin-bottom: 1em;
}
section.pass-section .md-table {
  border-collapse: collapse;
  margin: 18px 0;
  font-size: 14px;
  width: 100%;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}
section.pass-section .md-table th, section.pass-section .md-table td {
  padding: 10px 14px;
  vertical-align: top;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}
section.pass-section .md-table tr:last-child td { border-bottom: none; }
section.pass-section .md-table th {
  background: var(--bg-warm);
  font-family: 'DM Sans', sans-serif;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}

/* ============================================================
   Finding cards
   ============================================================ */

.finding-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px 28px;
  margin: 22px 0;
  scroll-margin-top: 78px;
  position: relative;
  transition: transform 0.18s, box-shadow 0.18s;
  box-shadow: var(--shadow-sm);
}
.finding-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
.finding-card::before {
  content: "";
  position: absolute;
  top: 0; left: 0;
  width: 4px; height: 100%;
  border-radius: 12px 0 0 12px;
  background: var(--low-border);
}
.finding-card--critical { background: var(--critical-tint); border-color: var(--critical-border); }
.finding-card--critical::before { background: var(--critical); width: 6px; }
.finding-card--high { background: var(--high-tint); border-color: var(--high-border); }
.finding-card--high::before { background: var(--high); width: 5px; }
.finding-card--medium { background: var(--medium-tint); border-color: var(--medium-border); }
.finding-card--medium::before { background: var(--medium); }
.finding-card--low { background: var(--low-tint); border-color: var(--low-border); }
.finding-card--low::before { background: var(--low); }
.finding-card--persisting { opacity: 0.94; }
.finding-card.is-reviewed {
  border-style: solid;
  box-shadow: 0 0 0 1.5px var(--success), var(--shadow-sm);
}
.finding-card.is-reviewed::after {
  content: "✓";
  position: absolute;
  top: 14px;
  right: 18px;
  font-size: 18px;
  color: var(--success);
  font-weight: 700;
}

.finding-id-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.finding-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  background: rgba(255, 255, 255, 0.6);
  padding: 3px 8px;
  border-radius: 5px;
  color: var(--muted);
  letter-spacing: 0.02em;
}
[data-theme="dark"] .finding-id { background: rgba(255, 255, 255, 0.05); }

.finding-title {
  font-family: 'Fraunces', serif;
  font-size: 22px;
  font-weight: 500;
  margin: 6px 0 10px 0;
  line-height: 1.3;
  letter-spacing: -0.01em;
  color: var(--fg);
}
.finding-card--critical .finding-title { font-size: 25px; font-weight: 600; }
.finding-card--high .finding-title { font-size: 23px; }

.finding-meta {
  font-family: 'DM Sans', sans-serif;
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 16px;
  font-weight: 500;
}
.finding-meta span { white-space: nowrap; }
.finding-meta .sep { margin: 0 10px; color: var(--border-strong); }

.finding-body p {
  font-size: 16px;
  line-height: 1.7;
  color: var(--fg-soft);
  margin: 0 0 14px 0;
}

details.finding-evidence {
  margin: 16px 0;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 8px;
  padding: 4px 16px;
  font-size: 14px;
  border: 1px solid rgba(0, 0, 0, 0.04);
}
[data-theme="dark"] details.finding-evidence {
  background: rgba(255, 255, 255, 0.03);
  border-color: rgba(255, 255, 255, 0.05);
}
details.finding-evidence summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--muted);
  list-style: none;
  padding: 8px 0;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
details.finding-evidence summary::-webkit-details-marker { display: none; }
details.finding-evidence summary::before {
  content: "▸";
  margin-right: 8px;
  font-size: 0.9em;
  color: var(--subtle);
  display: inline-block;
  transition: transform 0.18s;
}
details.finding-evidence[open] summary::before { transform: rotate(90deg); }
details.finding-evidence ul {
  margin: 6px 0 12px 0;
  padding: 0;
  list-style: none;
}
details.finding-evidence li {
  margin: 7px 0;
  padding-left: 14px;
  border-left: 2px solid var(--border);
  font-size: 14px;
  line-height: 1.55;
}

.finding-action {
  background: rgba(255, 255, 255, 0.55);
  border-left: 3px solid var(--accent);
  padding: 14px 18px;
  border-radius: 0 8px 8px 0;
  margin-top: 18px;
  font-size: 15.5px;
  line-height: 1.6;
  color: var(--fg);
}
[data-theme="dark"] .finding-action { background: rgba(255, 255, 255, 0.03); }
.finding-action strong { color: var(--accent); font-weight: 700; }

.finding-annotation {
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 10px 16px;
  align-items: start;
}
[data-theme="dark"] .finding-annotation {
  border-top-color: rgba(255, 255, 255, 0.06);
}
.finding-annotation label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  color: var(--muted);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding-top: 9px;
}
.finding-annotation select.confirmed,
.finding-annotation textarea.notes {
  font: inherit;
  font-family: 'DM Sans', sans-serif;
  font-size: 14px;
  border: 1.5px solid var(--border-strong);
  border-radius: 7px;
  background: var(--surface);
  color: var(--fg);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.finding-annotation select.confirmed {
  font-weight: 500;
  padding: 7px 12px;
  cursor: pointer;
}
.finding-annotation textarea.notes {
  width: 100%;
  min-height: 42px;
  padding: 8px 12px;
  resize: vertical;
}
.finding-annotation select.confirmed:hover,
.finding-annotation textarea.notes:hover { border-color: var(--accent); }
.finding-annotation select.confirmed:focus,
.finding-annotation textarea.notes:focus {
  outline: 0;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-subtle);
}

/* Severity pills + status badges */
.pill, .badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 11px;
  border-radius: 12px;
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.pill.critical { background: var(--critical-bg); color: var(--critical); border: 1px solid var(--critical-border); }
.pill.high     { background: var(--high-bg);     color: var(--high);     border: 1px solid var(--high-border); }
.pill.medium   { background: var(--medium-bg);   color: var(--medium);   border: 1px solid var(--medium-border); }
.pill.low      { background: var(--low-bg);      color: var(--low);      border: 1px solid var(--low-border); }
.badge { color: white; }
.badge.new { background: var(--new); }
.badge.persisting { background: var(--persisting); }
.badge.resolved { background: var(--resolved); }

/* Severity inline text colors (rare) */
.sev-critical { color: var(--critical); font-weight: 600; }
.sev-high     { color: var(--high); font-weight: 600; }
.sev-medium   { color: var(--medium); font-weight: 600; }
.sev-low      { color: var(--low); }

/* ============================================================
   Resolved (footer-style minor section)
   ============================================================ */

section.resolved {
  margin-top: 64px;
  padding-top: 32px;
  border-top: 1px solid var(--border);
  opacity: 0.85;
}
section.resolved h2 {
  font-size: 22px;
  font-weight: 500;
  color: var(--muted);
  margin-bottom: 14px;
}
.resolved-table {
  border-collapse: collapse;
  width: 100%;
  font-size: 14px;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}
.resolved-table th, .resolved-table td {
  padding: 10px 14px;
  vertical-align: top;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
}
.resolved-table tr:last-child td { border-bottom: none; }
.resolved-table th {
  background: var(--bg-warm);
  font-family: 'DM Sans', sans-serif;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.resolved-table .id-col {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  white-space: nowrap;
  color: var(--muted);
}

/* ============================================================
   Mobile sidebar — hidden by default, toggled
   ============================================================ */

.sidebar-toggle {
  display: none;
  background: transparent;
  color: var(--fg);
  padding: 6px 10px;
  font-size: 20px;
  box-shadow: none;
}

@media (max-width: 1100px) {
  .page-grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: "header" "main";
  }
  aside.sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: 300px;
    z-index: 60;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    box-shadow: var(--shadow-lg);
  }
  aside.sidebar.open { transform: translateX(0); }
  .sidebar-toggle { display: inline-flex; align-items: center; }
  .sidebar-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    z-index: 55;
  }
  aside.sidebar.open ~ .sidebar-overlay { display: block; }
  main { padding: 24px 24px 80px; }
}

@media (max-width: 720px) {
  body { font-size: 15px; }
  main { padding: 20px 16px 60px; }
  .hero-card { padding: 26px 22px; }
  .hero-headline { font-size: 30px; }
  section.exec-summary { padding: 26px 22px; }
  section.exec-summary > h2 { font-size: 24px; }
  section.exec-summary p { font-size: 17px; }
  section.exec-summary p:first-of-type::first-letter { font-size: 3.4em; }
  .stat-tiles { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-num { font-size: 36px; }
  .finding-card { padding: 18px 20px; }
  .finding-title { font-size: 19px; }
  .finding-card--critical .finding-title { font-size: 21px; }
  .finding-annotation { grid-template-columns: 1fr; }
  .finding-annotation label { padding-top: 0; }
  header.page-header { padding: 12px 18px; gap: 10px; }
  header.page-header h1 { font-size: 19px; }
  header.page-header .meta { display: none; }
}

@media print {
  header.page-header, button, #save-status, .finding-annotation, aside.sidebar, .sidebar-overlay { display: none !important; }
  .page-grid { grid-template-columns: 1fr; }
  body { background: white; }
  .finding-card, .hero-card, section.exec-summary { break-inside: avoid; box-shadow: none; }
  .stat-tile:hover, .finding-card:hover { transform: none; }
}
"""


# ---------------------------------------------------------------------------
# Client-side JS — annotation form + save-and-download + dark mode + progress
# ---------------------------------------------------------------------------


JS_TEMPLATE = """
(function () {
  // ===== Theme toggle =====
  const THEME_KEY = 'diagnose:theme';
  function applyTheme(t) {
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
  }
  let theme = 'light';
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'dark' || stored === 'light') theme = stored;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) theme = 'dark';
  } catch (e) {}
  applyTheme(theme);
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      theme = theme === 'dark' ? 'light' : 'dark';
      applyTheme(theme);
      try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    });
  }

  // ===== Sidebar toggle (mobile) =====
  const sidebarBtn = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  function closeSidebar() { if (sidebar) sidebar.classList.remove('open'); }
  if (sidebarBtn && sidebar) {
    sidebarBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
  }
  if (overlay) overlay.addEventListener('click', closeSidebar);
  document.querySelectorAll('aside.sidebar a').forEach(a => a.addEventListener('click', () => {
    if (window.innerWidth <= 1100) closeSidebar();
  }));

  // ===== Embedded data =====
  const dataEl = document.getElementById('diagnose-data');
  if (!dataEl) return;
  let raw = dataEl.textContent.trim();
  raw = raw.replace(/<\\\\\\//g, '</');
  let data;
  try { data = JSON.parse(raw); } catch (e) { console.error('Bad embedded JSON', e); return; }
  data.annotations = data.annotations || {};

  const localKey = 'diagnose:' + (data.generated || 'unknown');

  function setStatus(text, cls) {
    const s = document.getElementById('save-status');
    if (s) { s.textContent = text; s.className = cls || ''; }
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
  }

  function applyAnnotation(id, anno) {
    const el = document.querySelector('[data-finding-id="' + cssEscape(id) + '"]');
    if (!el) return;
    const sel = el.querySelector('select.confirmed');
    const txt = el.querySelector('textarea.notes');
    if (sel) sel.value = anno.confirmed || '';
    if (txt) txt.value = anno.notes || '';
  }

  Object.entries(data.annotations).forEach(([id, anno]) => applyAnnotation(id, anno));

  try {
    const stored = localStorage.getItem(localKey);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (parsed && parsed.annotations && Object.keys(parsed.annotations).length) {
        Object.entries(parsed.annotations).forEach(([id, anno]) => applyAnnotation(id, anno));
        setStatus('Restored unsaved draft from this browser. Click Save annotated HTML to commit.', 'dirty');
      }
    }
  } catch (e) {}

  function collect() {
    const out = {};
    document.querySelectorAll('[data-finding-id]').forEach(el => {
      const id = el.dataset.findingId;
      const sel = el.querySelector('select.confirmed');
      const txt = el.querySelector('textarea.notes');
      const conf = sel ? sel.value : '';
      const notes = txt ? txt.value : '';
      if (conf || notes) out[id] = { confirmed: conf, notes: notes };
    });
    return out;
  }

  // ===== Live progress counter =====
  function updateProgress() {
    const totals = { all: 0, critical: 0, high: 0, medium: 0, low: 0 };
    const reviewed = { all: 0, critical: 0, high: 0, medium: 0, low: 0 };
    document.querySelectorAll('[data-finding-id]').forEach(el => {
      const sev = (el.dataset.severity || 'low').toLowerCase();
      if (totals[sev] === undefined) return;
      totals[sev]++; totals.all++;
      const sel = el.querySelector('select.confirmed');
      const isReviewed = sel && sel.value && sel.value !== '';
      if (isReviewed) {
        reviewed[sev]++; reviewed.all++;
        el.classList.add('is-reviewed');
      } else {
        el.classList.remove('is-reviewed');
      }
    });
    const overallNum = document.getElementById('progress-overall-num');
    const overallTotal = document.getElementById('progress-overall-total');
    const overallFill = document.getElementById('progress-overall-fill');
    if (overallNum) overallNum.textContent = reviewed.all;
    if (overallTotal) overallTotal.textContent = totals.all;
    if (overallFill) {
      const pct = totals.all > 0 ? (reviewed.all / totals.all) * 100 : 0;
      overallFill.style.width = pct.toFixed(1) + '%';
    }
    ['critical', 'high', 'medium', 'low'].forEach(sev => {
      const fill = document.getElementById('sev-fill-' + sev);
      const count = document.getElementById('sev-count-' + sev);
      if (count) count.textContent = reviewed[sev] + ' / ' + totals[sev];
      if (fill) {
        const pct = totals[sev] > 0 ? (reviewed[sev] / totals[sev]) * 100 : 0;
        fill.style.width = pct.toFixed(1) + '%';
      }
    });
  }
  updateProgress();

  let dirty = false;
  let saveTimer = null;
  function markDirty() {
    dirty = true;
    setStatus('Unsaved changes', 'dirty');
    updateProgress();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(localKey, JSON.stringify({ annotations: collect() }));
      } catch (e) {}
    }, 500);
  }

  document.querySelectorAll('select.confirmed, textarea.notes').forEach(el => {
    el.addEventListener('change', markDirty);
    el.addEventListener('input', markDirty);
  });

  const saveBtn = document.getElementById('save-btn');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      data.annotations = collect();
      const json = JSON.stringify(data, null, 2).replace(/<\\//g, '<\\\\/');
      dataEl.textContent = json;

      const html = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
      const blob = new Blob([html], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'diagnosis.html';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      try { localStorage.removeItem(localKey); } catch (e) {}
      dirty = false;
      setStatus('Saved (downloaded). Replace the original with the downloaded copy and send back.', 'saved');
    });
  }

  window.addEventListener('beforeunload', e => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
})();
"""


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
    for p in PASS_ORDER:
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
    prior_state = parse_prior_state(diagnosis_path)
    prior_anno: dict[str, dict] = prior_state.get("annotations", {}) or {}
    prior_finding_ids: set[str] = set()
    for f in prior_state.get("findings", []) or []:
        if isinstance(f, dict) and "id" in f:
            prior_finding_ids.add(f["id"])
    for fid in prior_state.get("resolved_finding_ids", []) or []:
        prior_finding_ids.add(fid)

    current_ids = {f["id"] for f in findings}
    resolved_ids = sorted(prior_finding_ids - current_ids)

    findings_by_pass: dict[str, list[dict]] = {p: [] for p in PASS_ORDER}
    for f in findings:
        findings_by_pass.setdefault(f["pass"], []).append(f)

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
    new_count = sum(1 for f in findings if f["id"] not in prior_anno)
    persisting_count = len(findings) - new_count
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
    for p in PASS_ORDER:
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
            anno = prior_anno.get(f["id"], {})
            status = "PERSISTING" if f["id"] in prior_anno else "NEW"
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
        "annotations": {
            f["id"]: prior_anno[f["id"]]
            for f in findings
            if f["id"] in prior_anno
        },
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
    print(f"Wrote: {diagnosis_path}")
    if prev_path.exists():
        print(f"Rotated prior to: {prev_path}")


def main() -> None:
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
