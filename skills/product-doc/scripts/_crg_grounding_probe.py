"""_crg_grounding_probe.py — deterministic CRG node/symbol resolution for the
/product-doc grounding verifier (slice-015).

Invoked as a SUBPROCESS by grounding_verify.py (NEVER imported): code-review-graph
pulls heavy deps and logs to stdout/stderr, while grounding_verify's stdout is a
strict JSON contract — so CRG runs in a child whose only stdout is the single JSON
line this script prints (CRG's own stdout noise is redirected to stderr). It mirrors
the ISOLATION contract of skills/slice-candidates/scripts/_crg_impact.py — but NOT
its `status != "ok" -> exit 1` logic: query_graph returns status='ok' with empty
results on a MISSING graph and never raises, so graph-absence is detected via an
explicit list_graph_stats(total_nodes>0) health check, not via an exception (M1).

Resolution is DETERMINISTIC (query_graph file_summary / list_graph_stats — no
embedding model, no MCP server). Pinned against code-review-graph 2.3.5.

Contract:
  Usage:
    <py> _crg_grounding_probe.py --repo-root <dir> --health
    <py> _crg_grounding_probe.py --repo-root <dir> --path <repo-rel-path> [--symbol <name>]
  stdout: ONE JSON line.
    --health        -> {"reachable": bool, "total_nodes": int, "last_updated": str|null}
    --path          -> {"reachable": bool, "file_resolved": bool, "symbol_present": bool|null,
                        "ambiguous": bool, "total_nodes": int, "last_updated": str|null}
  CRG not importable -> exit 3, empty stdout (caller maps to unreachable / AC3).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import sys

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402


def _norm_repo_rel(target: str, repo_root: str) -> str:
    """Normalize a token path to repo-relative forward-slash (m3 — absolute/backslash
    targets silently return 0 on CRG 2.3.5, so normalize before querying)."""
    t = target.replace("\\", "/").strip()
    if t.startswith("./"):
        t = t[2:]
    p = pathlib.Path(t)
    if p.is_absolute():
        try:
            t = os.path.relpath(str(p), repo_root).replace("\\", "/")
        except (ValueError, OSError):
            pass
    return t


def _stats(q, repo_root: str):
    try:
        with contextlib.redirect_stdout(sys.stderr):
            s = q.list_graph_stats(repo_root=repo_root)
    except Exception:
        return False, 0, None
    if not isinstance(s, dict):
        return False, 0, None
    total = s.get("total_nodes") or 0
    return (total > 0), int(total), s.get("last_updated")


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--path")
    ap.add_argument("--symbol")
    ap.add_argument("--health", action="store_true")
    args = ap.parse_args(argv)

    try:
        import code_review_graph.tools.query as q
    except Exception:
        return 3  # CRG not installed/importable in this interpreter -> caller degrades (AC3)

    reachable, total, last_updated = _stats(q, args.repo_root)

    if args.health or not args.path:
        json.dump({"reachable": reachable, "total_nodes": total,
                   "last_updated": last_updated}, sys.stdout)
        return 0

    file_resolved = False
    symbol_present = None
    ambiguous = False
    if reachable:
        target = _norm_repo_rel(args.path, args.repo_root)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                r = q.query_graph(pattern="file_summary", target=target,
                                  repo_root=args.repo_root, detail_level="standard")
        except Exception:
            r = None
        results = (r.get("results") or []) if isinstance(r, dict) else []
        file_nodes = [n for n in results if isinstance(n, dict) and n.get("kind") == "File"]
        distinct_files = {n.get("name") for n in file_nodes}
        ambiguous = len(distinct_files) > 1
        file_resolved = len(file_nodes) >= 1 and not ambiguous
        if args.symbol is not None:
            # m1: the File node's (absolute) name must NOT satisfy symbol membership
            symbols = {n.get("name") for n in results if isinstance(n, dict) and n.get("kind") != "File"}
            symbol_present = args.symbol in symbols

    json.dump({"reachable": reachable, "file_resolved": file_resolved,
               "symbol_present": symbol_present, "ambiguous": ambiguous,
               "total_nodes": total, "last_updated": last_updated}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
