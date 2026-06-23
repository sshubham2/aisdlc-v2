"""harvest_degrade.py -- deterministic prose-doc harvest-degrade classifier
(slice-029 / SC-013). Decides whether /product-doc's prose-doc half (README /
API-reference / user-guide) can ground interface facts on the CRG public surface,
from code_review_graph.tools.query.list_graph_stats COUNTS -- no MCP, no embedding
provider, no model-eyeballed harvest count (ADR-019, after critique v1 BLOCKED the
original MCP-only premise).

classify(total_nodes, public_nodes, embeddings_count) -> {degraded, cause, message}
  cause: graph-unavailable | embeddings-absent | genuinely-empty | None (harvestable)
  - any non-int input / total<=0 -> graph-unavailable (FAIL-CLOSED -- a stale cached
    probe that omits the new keys must never produce a silent pass)
  - public>0 & embeddings==0 -> embeddings-absent (the public surface EXISTS but the
    semantic harvest cannot retrieve it; remedy: code-review-graph embed)
  - public<=0 -> genuinely-empty (no public symbols; NOT degraded)
  - public>0 & embeddings>0 -> harvestable (cause None; NOT degraded)

CLI: harvest_degrade.py --repo-root <dir>  -> ONE JSON line
  {degraded, cause, total_nodes, public_nodes, embeddings_count, message}
  obtains the counts from ONE _crg_grounding_probe.py --health subprocess. A missing /
  non-int key (stale cached probe) is read as None -> graph-unavailable (fail-closed).
  exit 0 always; the degrade lives in the JSON, not the exit code.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

_PROBE = pathlib.Path(__file__).resolve().parent / "_crg_grounding_probe.py"

# Messages are GENERIC (cause + remedy only) -- NO machine-local paths / counts (m3 banner hygiene).
# The CLI JSON carries the counts separately for the console signal.
_MESSAGES = {
    "graph-unavailable": "Code map unavailable -- /product-doc cannot ground interface facts. "
                         "Build the code map: code-review-graph build.",
    "embeddings-absent": "Code map has public symbols but no embeddings -- the semantic harvest "
                         "cannot retrieve them, so prose docs would omit interface facts. "
                         "Build embeddings: code-review-graph embed (or install the embedding "
                         "provider). Re-run, or proceed with degraded (interface-light) docs.",
    "genuinely-empty": "No public symbols in the code map -- nothing to ground (not a degrade).",
    None: "Code map is harvestable.",
}


def _is_count(x) -> bool:
    # bool is a subclass of int but is NOT a valid node count
    return isinstance(x, int) and not isinstance(x, bool)


def classify(total_nodes, public_nodes, embeddings_count) -> dict:
    """Pure, deterministic, FAIL-CLOSED. Returns {degraded, cause, message}."""
    if not (_is_count(total_nodes) and _is_count(public_nodes) and _is_count(embeddings_count)):
        cause = "graph-unavailable"          # missing / non-int (stale probe) -> never a silent pass
    elif total_nodes <= 0:
        cause = "graph-unavailable"
    elif public_nodes <= 0:
        cause = "genuinely-empty"
    elif embeddings_count <= 0:
        cause = "embeddings-absent"
    else:
        cause = None
    degraded = cause in ("graph-unavailable", "embeddings-absent")
    return {"degraded": degraded, "cause": cause, "message": _MESSAGES[cause]}


def _probe_health(repo_root: str) -> dict | None:
    """Run _crg_grounding_probe.py --health. None on any failure (-> fail-closed)."""
    try:
        p = subprocess.run([sys.executable, str(_PROBE), "--repo-root", repo_root, "--health"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    except Exception:
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    args = ap.parse_args(argv)

    health = _probe_health(args.repo_root)
    # absent probe / absent keys -> None -> classify() fails closed to graph-unavailable
    total = health.get("total_nodes") if isinstance(health, dict) else None
    public = health.get("public_nodes") if isinstance(health, dict) else None
    emb = health.get("embeddings_count") if isinstance(health, dict) else None

    v = classify(total, public, emb)
    out = {"degraded": v["degraded"], "cause": v["cause"],
           "total_nodes": total, "public_nodes": public, "embeddings_count": emb,
           "message": v["message"]}
    json.dump(out, sys.stdout, ensure_ascii=False)  # BC-PROJ-3 (cp1252 serialize leg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
