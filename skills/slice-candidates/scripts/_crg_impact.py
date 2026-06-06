"""_crg_impact.py — best-effort code-review-graph blast-radius for build_backlog.

Invoked as a SUBPROCESS by build_backlog._crg_prober (NEVER imported) on purpose:
code-review-graph pulls heavy deps and logs to stderr/stdout, and build_backlog's
own stdout is a strict JSON contract — so CRG runs in a child whose only stdout is
the single JSON line this script prints. Any leakage CRG writes to stdout is
redirected to stderr while the query runs.

Pinned against code-review-graph 2.3.x: there is **no** `blast-radius` CLI verb —
the per-file blast radius is the Python MCP-tool impl
``code_review_graph.tools.query.get_impact_radius(changed_files=[...], repo_root=...)``,
whose ``impacted_files`` key holds the coupled files (returned as ABSOLUTE paths, so
we normalize them to repo-relative forward-slash here to match build_backlog's
repo-relative evidence paths). The graph DB is selected via the ``CRG_DATA_DIR`` env
override (build_backlog passes --crg-graph's diagnose-out/.code-review-graph/ dir).

Contract:
  Usage:  <py> _crg_impact.py <evidence_path> <repo_root> [<crg_data_dir>]
  stdout: {"impacted_files": ["<repo-relative/forward-slash>", ...]}   (exit 0)
  failure (CRG absent, graph missing/broken, query error): nothing on stdout, exit 1
          → the caller marks CRG degraded and falls back to shared-evidence coupling.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path


def _norm_rel(p: str, root: Path) -> str:
    """Absolute (or relative) impacted-file path -> repo-relative, forward-slash."""
    try:
        pp = Path(p)
        rel = os.path.relpath(pp, root) if pp.is_absolute() else str(pp)
    except (ValueError, OSError):
        rel = str(p)
    rel = rel.replace("\\", "/")
    return rel[2:] if rel.startswith("./") else rel


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        return 2
    evidence, repo_root = argv[0], argv[1]
    data_dir = argv[2] if len(argv) > 2 else ""
    if data_dir:
        # Verbatim override of the graph DB location (get_data_dir() honors this).
        os.environ["CRG_DATA_DIR"] = data_dir

    try:
        from code_review_graph.tools.query import get_impact_radius
    except Exception:
        return 1  # CRG not installed in this interpreter → caller degrades

    try:
        # Keep any stray CRG stdout noise off our JSON channel.
        with contextlib.redirect_stdout(sys.stderr):
            result = get_impact_radius(
                changed_files=[evidence],
                repo_root=repo_root,
                max_depth=2,
                detail_level="standard",
            )
    except Exception:
        return 1  # broken/empty graph, unreadable file, etc. → caller degrades

    if not isinstance(result, dict) or result.get("status") != "ok":
        return 1

    root = Path(repo_root)
    rels = sorted({_norm_rel(str(p), root) for p in (result.get("impacted_files") or [])})
    json.dump({"impacted_files": rels}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
