"""finding_dedup.py — cross-pass / cross-finder finding de-duplication (SHARED).

SKETCH v0 (not yet wired into the build). Shared because >1 skill needs it:
  - /diagnose  (call from assemble.py, AFTER load_findings(), BEFORE render)
  - /bug-hunt  (call after the finder fan-out, BEFORE adversarial verify)

## Why this exists

The per-pass content-ID recipe `F-<CAT>-sha1(category + primary_path + signature)`
(scripts/lib/assemble.py) CANNOT de-duplicate, for two structural reasons:

  1. `category` is *in the hash*. The same underlying issue surfaced by two passes
     (a god-file flagged by 03a dead-code AND 03c size AND 03f layering AND 04 bloat)
     always gets a different ID per pass, so it renders 3-4 times. The owner sees
     N cards for 1 problem.
  2. The signature defaults to the *title* for 9 of 11 passes, so wording drift
     across runs changes the ID -> carryover (Confirmed/Notes) silently breaks.

This module merges findings that point at the **same code location**, independent
of which pass/category/finder produced them, keyed on evidence (path + line span)
NOT on category or title.

## Contract

Operates on the finding schema (scripts/lib/finding.yaml): each
finding is a dict carrying at least `evidence: [{path, lines, note}]` and `severity`.

Merge is **additive and lossless on required fields**:
  - a *singleton* (merges with nothing) is returned UNCHANGED — same id, no new
    fields. The common case is untouched, so existing carryover keeps working.
  - a *cluster* (>=2 findings) collapses to one representative finding that GAINS
    optional provenance fields (`seen_by_passes`, `categories`, `merged_ids`,
    `merge_count`) and a stable `F-MRG-<8hex>` id derived from the cluster's
    canonical location. No REQUIRED_FIELD is removed, so a merged list still
    passes load_findings()'s required-field check, and the extra fields are
    ignored by consumers that don't know them (e.g. /slice-candidates).

Carryover note for the caller: when this is introduced, teach the carryover step
to match a merged finding by `id` OR any entry in its `merged_ids` (one-time
migration so prior per-pass annotations survive the first merged run).
"""
from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout

# Local copy (do NOT import from a single skill — lib must not depend on skills/).
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Line-number bucket for the stable cluster id: small line shifts between runs
# (a few lines added above) must NOT churn the merged id. 0 disables bucketing.
_LINE_BUCKET = 10

# A finding citing MORE than this many evidence paths (e.g. a duplicate-cluster
# finding listing a whole cluster) may ONLY link to others via a real line-range
# overlap — never via a bare whole-file match. Stops one wide finding from
# chaining unrelated per-file findings into a mega-cluster (over-merge guard).
_WIDE_FINDING_PATHS = 3

_RANGE_RE = re.compile(r"(\d+)\s*[-–]\s*(\d+)|(\d+)")


# ---------------------------------------------------------------------------
# Location extraction
# ---------------------------------------------------------------------------

def _norm_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return posixpath.normpath(p) if p else ""


def _parse_spans(lines: str) -> list[tuple[int, int]]:
    """'12-40, 88' -> [(12,40),(88,88)]. Empty/non-numeric -> [] (whole-file)."""
    spans: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(str(lines or "")):
        if m.group(1):
            a, b = int(m.group(1)), int(m.group(2))
        else:
            a = b = int(m.group(3))
        spans.append((min(a, b), max(a, b)))
    return spans


def _locations(f: dict) -> list[tuple[str, tuple[int, int] | None]]:
    """Evidence -> [(path, (start,end) | None)]. None span = whole-file."""
    locs: list[tuple[str, tuple[int, int] | None]] = []
    for e in f.get("evidence") or []:
        if isinstance(e, dict):
            path = _norm_path(e.get("path", ""))
            spans = _parse_spans(e.get("lines", ""))
        else:
            path, spans = _norm_path(str(e)), []
        if not path:
            continue
        if spans:
            locs.extend((path, s) for s in spans)
        else:
            locs.append((path, None))
    return locs


def _ranges_touch(a: tuple[int, int], b: tuple[int, int], gap: int) -> bool:
    return a[0] <= b[1] + gap and b[0] <= a[1] + gap


def _findings_link(fa: dict, fb: dict, gap: int) -> bool:
    """Two findings link iff they share an overlapping/adjacent code location."""
    la, lb = _locations(fa), _locations(fb)
    wide = len(la) > _WIDE_FINDING_PATHS or len(lb) > _WIDE_FINDING_PATHS
    for pa, ra in la:
        for pb, rb in lb:
            if pa != pb:
                continue
            if ra is None or rb is None:
                if wide:           # over-merge guard: wide findings need a real range
                    continue
                return True
            if _ranges_touch(ra, rb, gap):
                return True
    return False


# ---------------------------------------------------------------------------
# Union-Find
# ---------------------------------------------------------------------------

class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _cluster_groups(findings: list[dict], gap: int) -> list[list[dict]]:
    n = len(findings)
    dsu = _DSU(n)
    # O(n^2) is fine: finding counts are dozens-to-low-hundreds per run.
    for i in range(n):
        for j in range(i + 1, n):
            if _findings_link(findings[i], findings[j], gap):
                dsu.union(i, j)
    groups: dict[int, list[dict]] = {}
    for idx, f in enumerate(findings):
        groups.setdefault(dsu.find(idx), []).append(f)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def _sev_rank(f: dict) -> int:
    return SEVERITY_RANK.get(str(f.get("severity", "")).lower(), 0)


def _cluster_id(group: list[dict]) -> str:
    """Stable id from the cluster's canonical (smallest) bucketed location."""
    keys: list[tuple[str, int]] = []
    for f in group:
        for path, rng in _locations(f):
            start = rng[0] if rng else 0
            keys.append((path, start // _LINE_BUCKET if _LINE_BUCKET else start))
    canonical = min(keys) if keys else ("", 0)
    digest = hashlib.sha1(f"{canonical[0]}:{canonical[1]}".encode("utf-8")).hexdigest()[:8]
    return f"F-MRG-{digest}"


def _union_evidence(group: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for f in group:
        for e in f.get("evidence") or []:
            if isinstance(e, dict):
                key = (str(e.get("path", "")), str(e.get("lines", "")), str(e.get("note", "")))
                ev = {"path": e.get("path", ""), "lines": e.get("lines", ""), "note": e.get("note", "")}
            else:
                key = (str(e), "", "")
                ev = {"path": str(e), "lines": "", "note": ""}
            if key not in seen:
                seen.add(key)
                out.append(ev)
    return out


def _merge(group: list[dict]) -> dict:
    if len(group) == 1:
        return group[0]  # singleton: return UNCHANGED (preserves existing id + carryover)

    # Representative: highest severity, then most evidence, then smallest id (stable).
    rep = sorted(group, key=lambda f: (-_sev_rank(f), -len(f.get("evidence") or []), str(f.get("id", ""))))[0]
    merged = dict(rep)

    merged["id"] = _cluster_id(group)
    merged["severity"] = max(group, key=_sev_rank).get("severity", rep.get("severity"))
    merged["evidence"] = _union_evidence(group)
    merged["merged_ids"] = sorted({str(f.get("id", "")) for f in group if f.get("id")})
    merged["seen_by_passes"] = sorted({str(f.get("pass", "")) for f in group if f.get("pass")})
    merged["categories"] = sorted({str(f.get("category", "")) for f in group if f.get("category")})
    merged["merge_count"] = len(group)

    # Visible provenance even when the renderer only knows schema fields.
    note = (
        f"\n\n_Merged from {len(group)} findings "
        f"(passes: {', '.join(merged['seen_by_passes']) or 'n/a'}; "
        f"categories: {', '.join(merged['categories']) or 'n/a'}). "
        f"Constituent IDs: {', '.join(merged['merged_ids'])}._"
    )
    merged["description"] = str(rep.get("description", "")).rstrip() + note
    return merged


def dedupe_findings(findings: list[dict], gap: int = 3) -> tuple[list[dict], list[dict]]:
    """Merge findings that share a code location. Returns (merged, merge_report).

    `gap` — line-distance within which two ranges are considered touching.
    merge_report — one record per cluster that actually merged (>=2 findings),
    for the caller to log: {id, merged_ids, seen_by_passes, categories, title}.
    """
    groups = _cluster_groups(findings, gap)
    merged: list[dict] = []
    report: list[dict] = []
    for g in groups:
        m = _merge(g)
        merged.append(m)
        if len(g) > 1:
            report.append({
                "id": m["id"],
                "merged_ids": m["merged_ids"],
                "seen_by_passes": m["seen_by_passes"],
                "categories": m["categories"],
                "title": m.get("title", ""),
            })
    merged.sort(key=lambda f: (-_sev_rank(f), str(f.get("id", ""))))
    return merged, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_input(in_path: Path | None, findings_dir: Path | None) -> list[dict]:
    import json
    import yaml  # local: yaml is only needed in CLI mode

    findings: list[dict] = []
    if findings_dir is not None:
        for p in sorted(findings_dir.glob("*.yaml")):
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
            if isinstance(data, list):
                findings.extend(x for x in data if isinstance(x, dict))
    elif in_path is not None:
        text = in_path.read_text(encoding="utf-8")
        data = json.loads(text) if in_path.suffix == ".json" else (yaml.safe_load(text) or [])
        if isinstance(data, dict) and "findings" in data:
            data = data["findings"]
        if isinstance(data, list):
            findings = [x for x in data if isinstance(x, dict)]
    return findings


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(description="Merge findings that share a code location (cross-pass dedup).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--in", dest="in_path", type=Path, help="A .json or .yaml file: a list of findings (or {findings:[...]}).")
    src.add_argument("--findings-dir", type=Path, help="A directory of <pass>.yaml finding files to concat + merge.")
    ap.add_argument("--out", type=Path, help="Write merged findings here (default: stdout).")
    ap.add_argument("--format", choices=["yaml", "json"], default="yaml")
    ap.add_argument("--gap", type=int, default=3, help="Line gap within which ranges are 'touching' (default 3).")
    ap.add_argument("--report", action="store_true", help="Print a human merge report to stderr.")
    args = ap.parse_args(argv)

    import json
    import yaml

    findings = _load_input(args.in_path, args.findings_dir)
    merged, report = dedupe_findings(findings, gap=args.gap)

    if args.format == "json":
        rendered = json.dumps(merged, indent=2, default=str)
    else:
        rendered = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True) if merged else "[]\n"

    if args.out:
        args.out.write_text(rendered if rendered.endswith("\n") else rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    if args.report:
        print(f"[finding_dedup] {len(findings)} in -> {len(merged)} out "
              f"({len(findings) - len(merged)} collapsed across {len(report)} clusters)", file=sys.stderr)
        for r in report:
            print(f"  {r['id']} <- {r['merged_ids']} "
                  f"(passes: {','.join(r['seen_by_passes']) or '-'}) :: {r['title']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
