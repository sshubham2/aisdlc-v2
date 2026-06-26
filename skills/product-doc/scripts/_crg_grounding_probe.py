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
    --health        -> {"reachable": bool, "total_nodes": int, "last_updated": str|null,
                        "public_nodes": int, "embeddings_count": int}
                        (public_nodes = total - File - Test; slice-029 / ADR-019)
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


def _graph_db_path(repo_root: str) -> str | None:
    """Resolve the CRG graph db path for repo_root (slice-040): the conventional
    <repo>/.code-review-graph/graph.db (the CRG 2.3.5 location, spike-verified). Returns None if no
    db exists -- the caller then fails CLOSED (never constructs a GraphStore on a missing path, which
    would silently CREATE an empty db and look healthy-but-empty; m2)."""
    cand = pathlib.Path(repo_root) / ".code-review-graph" / "graph.db"
    return str(cand) if cand.exists() else None


def _names_set(repo_root: str):
    """Build the deterministic reality set for public_surface verification (slice-040):
    exact symbol names (every non-File/non-Test node kind -- Class/Function/Type; the INVERSE, not
    an allowlist, per ADR-019/slice-029 so a kind the tool adds (e.g. CRG's Type on TS repos) is not
    silently missed) + file stems, plus the ambiguous set (a name with >1 referent -- >1 symbol node,
    >1 file sharing a stem, or present as BOTH a symbol and a stem). EXACT enumeration via
    GraphStore.get_all_nodes/get_all_files -- never the fuzzy search_nodes (FTS5/LIKE) which would
    re-import the false-accept slice-015 killed."""
    from collections import Counter
    from code_review_graph.graph import GraphStore

    dbp = _graph_db_path(repo_root)
    if not dbp:
        return None  # health said >0 but the db path is unresolvable -> caller fails closed
    with contextlib.redirect_stdout(sys.stderr):  # CRG stdout noise -> stderr (strict JSON contract)
        store = GraphStore(dbp)
        nodes = list(store.get_all_nodes())
        files = list(store.get_all_files())

    def _attr(n, k):
        return n.get(k) if isinstance(n, dict) else getattr(n, k, None)

    sym = Counter(_attr(n, "name") for n in nodes
                  if _attr(n, "kind") not in ("Test", "File"))  # inverse, not allowlist (ADR-019)
    sym.pop(None, None)
    stem = Counter(pathlib.Path(str(f)).stem for f in files)
    ambiguous = ({k for k, c in sym.items() if c > 1}
                 | {k for k, c in stem.items() if c > 1}
                 | (set(sym) & set(stem)))
    return sorted(sym), sorted(stem), sorted(ambiguous)


def _stats(q, repo_root: str):
    try:
        with contextlib.redirect_stdout(sys.stderr):
            s = q.list_graph_stats(repo_root=repo_root)
    except Exception:
        return False, 0, None, 0, 0
    if not isinstance(s, dict):
        return False, 0, None, 0, 0
    total = int(s.get("total_nodes") or 0)
    by_kind = s.get("nodes_by_kind") or {}
    # public_nodes = total - File - Test (every non-container/non-test node = a public symbol;
    # robust to CRG's full kind set {File,Class,Function,Type,Test} -- slice-029 / ADR-019)
    files = int(by_kind.get("File") or 0)
    tests = int(by_kind.get("Test") or 0)
    public_nodes = max(0, total - files - tests)
    embeddings_count = int(s.get("embeddings_count") or 0)
    return (total > 0), total, s.get("last_updated"), public_nodes, embeddings_count


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--path")
    ap.add_argument("--symbol")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--names", action="store_true")  # slice-040: emit the public_surface reality set
    args = ap.parse_args(argv)

    try:
        import code_review_graph.tools.query as q
    except Exception:
        return 3  # CRG not installed/importable in this interpreter -> caller degrades (AC3)

    reachable, total, last_updated, public_nodes, embeddings_count = _stats(q, args.repo_root)

    if args.names:
        # slice-040: the public_surface reality set. set_ready (reachable) is the total_nodes>0
        # health gate above -- NOT 'get_all_nodes did not raise' (m2: GraphStore on a missing db
        # silently returns an empty set that looks healthy-but-empty). Build only when reachable.
        names: list[str] = []
        stems: list[str] = []
        ambiguous: list[str] = []
        if reachable:
            try:
                built = _names_set(args.repo_root)
                if built is None:
                    reachable = False  # db path unresolvable -> fail-closed (admit nothing)
                else:
                    names, stems, ambiguous = built
            except Exception:
                reachable = False  # CRG present but enumeration failed -> fail-closed
        json.dump({"reachable": reachable, "names": names, "stems": stems,
                   "ambiguous_names": ambiguous, "total_nodes": total},
                  sys.stdout, ensure_ascii=False)
        return 0

    if args.health or not args.path:
        json.dump({"reachable": reachable, "total_nodes": total,
                   "last_updated": last_updated, "public_nodes": public_nodes,
                   "embeddings_count": embeddings_count}, sys.stdout, ensure_ascii=False)  # BC-PROJ-3
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
