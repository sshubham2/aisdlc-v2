# -*- coding: utf-8 -*-
"""Rollout #1 (structural): rename vault artifacts .md -> .json in the source manifests.
- First html.unescape every manifest string (normalizes the &lt;vault&gt; vs <vault> split so the
  rename fires uniformly across all batches).
- Renames file_access paths that are vault artifacts (start with <vault>/ and end .md) -> .json.
- Migrates adopt's leftover brownfield <vault>/backlog.md -> <vault>/candidates.json (vault_edit-append).
- Prose-renames known vault-artifact basenames .md -> .json in summary/rationale/purposes.
- Does NOT touch ./CLAUDE.md, README.md, methodology-changelog.md, diagnose-out/* (md), tests/*, VERSION.
Idempotent; re-run aggregate.py afterwards."""
import json, os, re, html
MAN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".build", "manifests")  # portable (4.8)
ROUTE = "<!-- route: tools.vault_edit append -->"

ART = ["triage","concept","risk-register","mission-brief","design","build-log","validation","reflection",
       "milestone","shippability","lessons-learned","drift-log","sync-log","_index","build-checks","critique",
       "critique-review","code-review","requirements","threat-model","non-functional","nfrs","cost-estimation",
       "diagrams","action-points","critic-calibration-log","parallel-conflict-resolution-log","changelog"]

def unescape(x):
    if isinstance(x, str):  return html.unescape(x)
    if isinstance(x, list): return [unescape(v) for v in x]
    if isinstance(x, dict): return {k: unescape(v) for k, v in x.items()}
    return x

def prose(t):
    if not t: return t
    for b in ART:
        t = re.sub(r'(?<![\w-])'+re.escape(b)+r'\.md\b', b+'.json', t)
    t = re.sub(r'(?<![\w-])(ADR[-\w<>*]*)\.md\b', r'\1.json', t)
    return t

for i in range(8):
    fp = os.path.join(MAN, f"batch{i}.json")
    data = unescape(json.load(open(fp, encoding="utf-8")))
    for m in data:
        for key in ("summary","context_rationale"):
            if key in m: m[key] = prose(m[key])
        for grp in ("harness_tools","local_scripts"):
            for x in m.get(grp, []):
                if "purpose" in x: x["purpose"] = prose(x["purpose"])
        for e in m.get("file_access", []):
            p = e["path"]
            if p.startswith("<vault>/") and p.endswith(".md"):
                p = p[:-3] + ".json"
            if p.split("/")[-1] == "backlog.json":          # adopt brownfield backlog -> unified candidates
                p = "<vault>/candidates.json"; e["mechanism"] = "vault_edit-append"; e["safety_marker"] = ROUTE
            e["path"] = p
            if e["path"].endswith(".json") and e.get("format") == "markdown":
                e["format"] = "json"
            if "purpose" in e: e["purpose"] = prose(e["purpose"])
    json.dump(data, open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(fp, "a", encoding="utf-8").write("\n")
print("md->json rename applied (manifests normalized to literal <vault>; adopt backlog migrated).")
