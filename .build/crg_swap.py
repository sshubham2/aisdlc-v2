# -*- coding: utf-8 -*-
"""Rollout #2: replace graphify with code-review-graph (CRG) in the source manifests.
- CRG is the code graph (build/update + MCP query tools + claude-code install).
- graphify's vault-graph and multimodal-ingest uses are DROPPED (vault is queried as JSON;
  external refs become plain JSON fields).
Transforms .build/manifests/batch*.json in place (structural: external_tools + file_access).
Prose stragglers (summary/harness purposes) are reported for a follow-up pass."""
import json, os, re
ROOT = r"C:\Users\sshub\aisdlc-v2"
MAN  = os.path.join(ROOT, ".build", "manifests")

# skills whose ONLY graphify use was vault-graph / ingest -> drop CRG entirely
CRG_DROP = {"discover", "archive", "critique"}
# per-skill CRG roles (install / build / query)
ROLES = {
    "triage": {"install","build"}, "adopt": {"install","build","query"},
    "reflect": {"build"}, "sync": {"build"}, "heavy-architect": {"build","query"},
    "diagnose": {"build","query"}, "design-slice": {"build","query"}, "reduce": {"build","query"},
    "slice": {"query"}, "build-slice": {"query"}, "repro": {"query"}, "query-design": {"query"},
    "drift-check": {"query"}, "risk-spike": {"query"}, "slice-candidates": {"query"},
}

def crg_entry(name):
    roles = ROLES[name]; parts=[]; inv=[]
    if "install" in roles:
        parts.append("installs the Claude Code MCP integration (30 MCP tools + slash commands + git hooks)")
        inv.append("code-review-graph install --platform claude-code")
    if "build" in roles:
        parts.append("builds/refreshes the code graph"); inv.append("code-review-graph build|update")
    if "query" in roles:
        parts.append("reachability / blast-radius / keyword+semantic search via CRG MCP tools")
        inv.append("CRG MCP tools (impact-radius, review-context, search)")
    return {"name":"code-review-graph","type":"external-package","invocation":" | ".join(inv),
            "purpose":"Code graph via code-review-graph (CRG) — " + "; ".join(parts) +
                      ". Replaces graphify; vault-graph & multimodal-ingest uses dropped.",
            "found_in_temp":False,
            "note":"External pip package (github.com/tirth8205/code-review-graph), MCP-native; "
                   "graph stored in .code-review-graph/. Replaces graphify."}

def fix_file_access(fa):
    out=[]; seen=set()
    for e in fa:
        p=e["path"]
        # drop vault-graph + multimodal-ingest artifacts entirely
        if "vault-graph" in p or p.startswith("./raw") or p.startswith("raw/") or "/raw/" in p:
            continue
        e=dict(e)
        if p.startswith("graphify integration"):
            e["path"]="code-review-graph integration (MCP tools + git hooks + .code-review-graph/ store)"
            e["mechanism"]="code-review-graph"
        elif p.startswith("diagnose-out/graphify-out"):
            e["path"]="diagnose-out/.code-review-graph/"; e["mechanism"]="code-review-graph"
            e["kind"]="external-artifact" if e["access"]=="read" else "side-effect"; e["format"]="other"
        elif "graphify-out" in p:
            e["path"]=".code-review-graph/"; e["mechanism"]="code-review-graph"
            e["kind"]="external-artifact" if e["access"]=="read" else "side-effect"; e["format"]="other"
        elif e.get("mechanism")=="graphify":
            e["mechanism"]="code-review-graph"
        key=(e["path"], e["access"])
        if key in seen: continue
        seen.add(key); out.append(e)
    return out

stragglers={}
for i in range(8):
    fp=os.path.join(MAN, f"batch{i}.json")
    data=json.load(open(fp, encoding="utf-8"))
    for m in data:
        nm=m["name"]
        # external_tools: drop any graphify* entry; add the consolidated CRG entry if the skill keeps it
        m["external_tools"]=[t for t in m.get("external_tools",[]) if "graphify" not in t.get("name","").lower()]
        if nm in ROLES:
            m["external_tools"].append(crg_entry(nm))
        # file_access structural rewrite
        m["file_access"]=fix_file_access(m.get("file_access",[]))
        # report prose stragglers still mentioning graphify (summary / rationale / harness / local_scripts)
        blob=json.dumps({k:m.get(k) for k in ("summary","context_rationale","harness_tools","local_scripts")})
        if "graphify" in blob.lower():
            stragglers.setdefault(nm,0)
            stragglers[nm]=blob.lower().count("graphify")
    json.dump(data, open(fp,"w",encoding="utf-8"), indent=2, ensure_ascii=False)
    open(fp,"a",encoding="utf-8").write("\n")

print("CRG structural swap applied to manifests.")
print("CRG users:", sorted(ROLES)); print("CRG dropped (vault/ingest-only):", sorted(CRG_DROP))
print("\nProse stragglers still mentioning 'graphify' (summary/rationale/harness/local_scripts):")
for k,v in sorted(stragglers.items()): print(f"  {k}: {v}")
