# -*- coding: utf-8 -*-
"""Rollout #2 follow-up: clean graphify prose stragglers in the manifests.
Operates ONLY on summary / context_rationale / harness_tools.purpose / local_scripts.purpose.
Leaves external_tools (incl. the intentional 'Replaces graphify' provenance notes) untouched."""
import json, os, re
MAN = r"C:\Users\sshub\aisdlc-v2\.build\manifests"

# exact sentence rewrites for DROPPED features (vault-graph build, multimodal ingest, vault query)
KNOWN = [
    ("Ends by building the vault graph with graphify vault.", ""),
    (" and rebuilds the vault graph via graphify vault after each run", ""),
    ("optionally enriches the graphify graph with external references",
     "optionally records external references as JSON fields"),
    ("Step 4.5 graphify ingest + graph-rebuild commands and dropping local files into ./raw/.",
     "Step 4.5: record external references as JSON fields (multimodal ingest dropped in the graphify->CRG swap)."),
    ("Run project_frame_synth and triage_audit via $PY; run graphify query",
     "Run project_frame_synth and triage_audit via $PY (past-lessons lookup now queries the JSON archive directly)"),
]

def gen(t):
    t = t.replace("graphify-backed", "code-review-graph-backed")
    t = re.sub(r'graphify code (?:build|\.)', 'code-review-graph build', t)
    t = re.sub(r'graphify-out/[A-Za-z_]+\.(?:md|json)', '.code-review-graph/', t)
    t = re.sub(r'\bgraphify\b', 'code-review-graph', t)
    return re.sub(r'\s{2,}', ' ', t).strip()

def fix(t):
    if not t: return t
    for a, b in KNOWN: t = t.replace(a, b)
    return gen(t)

for i in range(8):
    fp = os.path.join(MAN, f"batch{i}.json")
    data = json.load(open(fp, encoding="utf-8"))
    for m in data:
        if "summary" in m: m["summary"] = fix(m["summary"])
        if "context_rationale" in m: m["context_rationale"] = fix(m["context_rationale"])
        for h in m.get("harness_tools", []):
            if "purpose" in h: h["purpose"] = fix(h["purpose"])
        for s in m.get("local_scripts", []):
            if "purpose" in s: s["purpose"] = fix(s["purpose"])
        for fa in m.get("file_access", []):
            if "purpose" in fa: fa["purpose"] = fix(fa["purpose"])
    json.dump(data, open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(fp, "a", encoding="utf-8").write("\n")
print("Prose cleaned.")
