# -*- coding: utf-8 -*-
"""Aggregate the 30 staged skill manifests into per-skill skill.json + a global skill-graph.json.
Forward edges (reads/writes/uses/spawns) come from each manifest; inverse edges
(created_by/edited_by/read_by/validated_by) are COMPUTED centrally so they are globally consistent."""
import json, os, re, html, glob, sys, shutil

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"C:\Users\sshub\aisdlc-v2"
MAN  = os.path.join(ROOT, ".build", "manifests")
SKILLS_DIR = os.path.join(ROOT, "skills")
GRAPH_OUT = os.path.join(ROOT, "skill-graph.json")

VALID_AGENTS = {"critique","critic-calibrate","critique-review","diagnose-narrator","field-recon","code-review","slice-story","designer-practice","designer-crossdomain","designer-expert","product-doc"}
# v2 tool locations: shared scripts/lib/ (invoked $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/X.py")
# + each skill's own scripts/ (single-skill, $PY "${CLAUDE_SKILL_DIR}/scripts/X.py"). Replaces the
# v1 temp/tools/ location: a methodology tool is "found" iff it exists in the v2 tree.
SHARED_TOOLS = {os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(os.path.join(ROOT,"scripts","lib","*.py"))
                if not os.path.basename(p).startswith("__")}
SKILL_TOOLS = {}  # tool basename -> owning skill (single-skill scripts/)
for _p in glob.glob(os.path.join(ROOT,"skills","*","scripts","*.py")):
    SKILL_TOOLS.setdefault(os.path.splitext(os.path.basename(_p))[0],
                           os.path.basename(os.path.dirname(os.path.dirname(_p))))
TOOL_MODULES = SHARED_TOOLS | set(SKILL_TOOLS)  # the v2 "found" set
def tool_path_for(nm, skill):
    if nm in SHARED_TOOLS: return f"scripts/lib/{nm}.py"
    if nm in SKILL_TOOLS:  return f"skills/{SKILL_TOOLS[nm]}/scripts/{nm}.py"
    return None
with open(os.path.join(ROOT,"schemas","artifact-examples.json"),encoding="utf-8") as _fh:
    EXAMPLES = {k:v for k,v in json.load(_fh).items() if not k.startswith("_")}
def artifact_key(path):
    bn = path.rstrip('/').split('/')[-1]
    for ext in (".json",".md"):
        if bn.endswith(ext): bn = bn[:-len(ext)]; break
    if bn == "*":  # collapsed glob node -> singular parent dir
        parent = path.rstrip('/').split('/')[-2]
        return {"components":"component","contracts":"contract","schemas":"schema",
                "actors":"actor","user-tests":"user-test"}.get(parent, parent)
    if bn.startswith("ADR"): return "adr"
    if bn.startswith("spike-"): return "spike"
    if bn == "candidates": return "slice-candidates"
    # the live index and the full archive catalog share a basename but are distinct
    # nodes with distinct shapes (3.18.1) — route the archive one to its own example.
    if bn == "_index":
        return "slice-archive-index" if "archive/_index" in path else "slice-index"
    return bn

def unescape(x):
    if isinstance(x, str): return html.unescape(x)
    if isinstance(x, list): return [unescape(v) for v in x]
    if isinstance(x, dict): return {k: unescape(v) for k,v in x.items()}
    return x

# ---- load manifests ----
manifests = []
for i in range(0,11):
    with open(os.path.join(MAN, f"batch{i}.json"), encoding="utf-8") as fh:
        manifests += unescape(json.load(fh))
by_name = {m["name"]: m for m in manifests}
assert len(manifests) == 30, f"expected 30 skills, got {len(manifests)}"

# ---- canonicalize a path into a stable graph-node id ----
def canon(p):
    p = html.unescape(p).strip()
    # unify per-slice folder ids
    p = re.sub(r'slice-NNN-<[^>]*>', 'slice-NNN', p)
    p = re.sub(r'slices/<active>', 'slices/slice-NNN', p)
    p = re.sub(r'slices/slice-NNN-[^/<]+', 'slices/slice-NNN', p)
    # the literal '*' wildcard form (drift-check reads slices/*/design.json across all
    # active slices) names the SAME per-slice artifact type -> fold onto the slice-NNN node
    p = re.sub(r'slices/\*/', 'slices/slice-NNN/', p)
    # merge archived per-slice artifacts back onto their active path (they move on archival)
    p = p.replace('slices/archive/slice-NNN/', 'slices/slice-NNN/')
    # ADR collection -> single node (decisions/ dir, decisions files, or any ADR-* file incl. slice-folder drafts)
    if re.search(r'(^|/)decisions/', p) or re.search(r'(^|/)ADR[-_]', p):
        return '<vault>/decisions/ADR-*.json'
    # glob-collapse the typed collections (match .md or .json -> *.json in v2)
    p = re.sub(r'(<vault>/components/)[^/]+\.(?:md|json)', r'\1*.json', p)
    p = re.sub(r'(<vault>/contracts/)[^/]+\.(?:md|json)', r'\1*.json', p)
    p = re.sub(r'(<vault>/schemas/)[^/]+\.(?:md|json)', r'\1*.json', p)
    p = re.sub(r'(<vault>/actors/)[^/]+\.(?:md|json)', r'\1*.json', p)
    p = re.sub(r'(<vault>/user-tests/)[^/]+\.(?:md|json)', r'\1*.json', p)
    p = re.sub(r'spike-<[^>]*>', 'spike-*', p)
    p = re.sub(r'tests/bugs/[^/]+\.py', 'tests/bugs/*.py', p)
    return p

# ---- pass 1: global file-access map ----
creates, updates, appends, reads = {}, {}, {}, {}
node_kinds, node_formats = {}, {}
for m in manifests:
    s = m["name"]
    for fa in m.get("file_access", []):
        node = canon(fa["path"])
        node_kinds.setdefault(node, set()).add(fa.get("kind","content-file"))
        node_formats.setdefault(node, set()).add(fa.get("format","markdown"))
        acc = fa["access"]
        {"create":creates,"update":updates,"append":appends,"read":reads}[acc].setdefault(node,set()).add(s)

KIND_PRI = ["content-file","enforcement-file","external-artifact","side-effect","directory"]
FMT_PRI  = ["html","markdown","json","other","directory"]
def node_kind(n):
    ks = node_kinds.get(n,{"content-file"})
    for k in KIND_PRI:
        if k in ks: return k
    return "content-file"
def node_fmt(n):
    fs = node_formats.get(n,{"markdown"})
    for f in FMT_PRI:
        if f in fs: return f
    return "markdown"
def v2_format(n):
    if n.endswith("CLAUDE.md"): return "markdown"
    k = node_kind(n)
    if k == "side-effect": return "n/a"
    if k == "directory":   return "directory"
    f = node_fmt(n)
    if f == "html": return "html"
    if f == "json": return "json"
    if f == "markdown":
        # VAULT content files follow the v2 md->json conversion convention; repo markdown
        # DELIVERABLES (README/CHANGELOG/docs/*, written by /product-doc) stay markdown by nature.
        return "json" if n.startswith("<vault>/") else "markdown"
    return "other"

def created_by(n): return sorted(creates.get(n,set()))
def edited_by(n):  return sorted(updates.get(n,set()) | appends.get(n,set()))
def read_by(n):    return sorted(reads.get(n,set()) | updates.get(n,set()))  # update = read-modify-write

# validated_by: methodology tool whose invocation names the file's basename
def basename(n): return n.rstrip("/").split("/")[-1]
tool_invocations = []  # (tool_name, invocation)
for m in manifests:
    for t in m.get("methodology_tools", []):
        tool_invocations.append((t["name"], t.get("invocation","")))
def validated_by(n):
    bn = basename(n)
    if "*" in bn or not bn.endswith((".md",".json",".yaml")): return []
    out = sorted({tn for (tn,inv) in tool_invocations if bn in inv})
    return out

# ---- pass 2: emit skill.json per skill ----
def merge_entries(entries):
    """group file_access entries by canon path; return dict path->merged."""
    g = {}
    for fa in entries:
        n = canon(fa["path"])
        d = g.setdefault(n, {"accesses":set(),"modes":set(),"mechs":set(),"purpose":fa.get("purpose","")})
        d["accesses"].add(fa["access"])
        d["modes"].update(fa.get("modes", ["minimal","standard","heavy"]))
        d["mechs"].add(fa.get("mechanism","read"))
        if not d["purpose"]: d["purpose"] = fa.get("purpose","")
    return g

MODE_ORDER = ["minimal","standard","heavy"]
def order_modes(ms): return [x for x in MODE_ORDER if x in ms]

def pick_write_mech(mechs):
    if "vault_edit-append" in mechs: return "vault_edit-append"
    for m in ("raw-write","code-review-graph","mkdir"):
        if m in mechs: return m
    return sorted(mechs)[0] if mechs else "raw-write"

emitted = {}
for m in manifests:
    s = m["name"]
    fa = m.get("file_access", [])
    reads_g  = merge_entries([e for e in fa if e["access"]=="read"])
    writes_g = merge_entries([e for e in fa if e["access"]!="read"])

    reads_out = []
    for n, d in reads_g.items():
        reads_out.append({
            "path": n, "kind": node_kind(n),
            "produced_by": created_by(n),
            "purpose": d["purpose"],
        })
    outputs_out = []
    example_keys = set()
    for n, d in writes_g.items():
        k = node_kind(n)
        out = {
            "path": n, "kind": k,
            "v1_format": ("directory" if k=="directory" else node_fmt(n)),
            "v2_format": v2_format(n),
            "write_semantics": "+".join(sorted(d["accesses"])),
            "write_mechanism": pick_write_mech(d["mechs"]),
            "modes": order_modes(d["modes"]),
            "created_by": created_by(n),
            "edited_by": edited_by(n),
            "read_by": read_by(n),
            "validated_by": validated_by(n),
            "purpose": d["purpose"],
        }
        # reference the bundled example file (skills/<name>/examples/<artifact>.json) for json artifacts
        if out["v2_format"] == "json":
            ak = artifact_key(n); ex = EXAMPLES.get(ak)
            if ex:
                out["example_schema"] = ex.get("_schema", f"aisdlc/{ak}@1")
                out["example"] = f"examples/{ak}.json"
                example_keys.add(ak)
        outputs_out.append(out)

    # tools: methodology (dedup by name) + external
    tools_out, seen_t = [], set()
    for t in m.get("methodology_tools", []):
        nm = t["name"]
        if nm in seen_t: continue
        seen_t.add(nm)
        path = tool_path_for(nm, s)
        tools_out.append({"name":nm,"type":"methodology-tool","invocation":t.get("invocation",""),
                          "purpose":t.get("purpose",""),"found":path is not None,
                          **({"path":path} if path else {})})
    for t in m.get("external_tools", []):
        nm = t["name"]
        if nm in seen_t: continue
        seen_t.add(nm)
        tools_out.append({"name":nm,"type":"external-package","invocation":t.get("invocation",""),
                          "purpose":t.get("purpose",""),"found":False,
                          "note":t.get("note","External pip package, intentionally not bundled in the plugin.")})

    # missing references
    missing = []
    for t in tools_out:
        if not t["found"]:
            missing.append({"ref":t["name"],"kind":"tool","found":False,
                            "expected": t["type"]=="external-package",
                            "reason": ("External pip package, intentionally not bundled in the plugin." if t["type"]=="external-package"
                                       else "Methodology tool referenced by skill but no scripts/lib or skills/*/scripts module found — verify.")})
    # named agents only
    agents_out = [a for a in m.get("agents", []) if a.get("name") in VALID_AGENTS]

    skill_json = {
        "name": s,
        "user_invokable": m.get("user_invokable", True),
        "requires_full_context": m.get("requires_full_context", True),
        "runs_in": m.get("runs_in","main-agent"),
        "context_rationale": m.get("context_rationale",""),
        "summary": m.get("summary",""),
        "reads": reads_out,
        "outputs": outputs_out,
        "agents": agents_out,
        "tools": tools_out,
        "harness_tools": m.get("harness_tools", []),
        "hands_off_to": m.get("hands_off_to", []),
        "source": m.get("source", f"temp/skills/{s}/SKILL.md"),
        "missing_references": missing,
    }
    emitted[s] = skill_json
    d = os.path.join(SKILLS_DIR, s)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d,"skill.json"),"w",encoding="utf-8") as fh:
        json.dump(skill_json, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # bundle output examples per the Claude Code skills convention: skills/<name>/examples/<artifact>.json
    ex_dir = os.path.join(d, "examples")
    if os.path.isdir(ex_dir): shutil.rmtree(ex_dir)
    if example_keys:
        os.makedirs(ex_dir, exist_ok=True)
        for ak in sorted(example_keys):
            with open(os.path.join(ex_dir, f"{ak}.json"), "w", encoding="utf-8") as fh:
                json.dump(EXAMPLES[ak], fh, indent=2, ensure_ascii=False); fh.write("\n")

# ---- pass 3: build graph.json ----
def spawns_generic(m):
    if any(a.get("name") not in VALID_AGENTS for a in m.get("agents",[])): return True
    for h in m.get("harness_tools",[]):
        if h.get("name")=="Agent" and re.search(r"haiku|general-purpose|pass subagent|analysis-pass|10\+1", h.get("purpose",""), re.I):
            return True
    return False

all_file_nodes = set(node_kinds)
edges = []
used_agents, used_tools = set(), {}
for m in manifests:
    s = m["name"]
    for a in m.get("agents",[]):
        if a.get("name") in VALID_AGENTS:
            used_agents.add(a["name"]); edges.append({"from":s,"to":a["name"],"type":"spawns"})
    for t in m.get("methodology_tools",[]):
        used_tools[t["name"]] = "methodology-tool"; edges.append({"from":s,"to":t["name"],"type":"uses_tool"})
    for t in m.get("external_tools",[]):
        used_tools[t["name"]] = "external-package"; edges.append({"from":s,"to":t["name"],"type":"uses_tool"})
    for h in m.get("harness_tools",[]):
        used_tools[h["name"]] = "harness"; edges.append({"from":s,"to":h["name"],"type":"uses_harness"})
    seen = set()
    for fa in m.get("file_access",[]):
        n = canon(fa["path"]); key=(n,fa["access"])
        if key in seen: continue
        seen.add(key)
        et = {"read":"reads","create":"creates","update":"updates","append":"appends"}[fa["access"]]
        edges.append({"from":s,"to":n,"type":et})
    for nxt in m.get("hands_off_to",[]):
        edges.append({"from":s,"to":nxt,"type":"hands_off_to"})

graph = {
    "generated_from": "temp/skills/*/SKILL.md (AI SDLC v1, 26 skills) + skills/bug-hunt/SKILL.md + skills/setup/SKILL.md + skills/slice-story/SKILL.md + skills/product-doc/SKILL.md (v2-native, 30 total)",
    "note": "Forward edges from each skill manifest; file inverse-edges (created_by/edited_by/read_by) computed globally. update access = read-modify-write (counts as both read and edit).",
    "stats": {
        "skills": len(manifests),
        "skills_main_agent": sum(1 for m in manifests if m.get("runs_in")=="main-agent"),
        "skills_delegable": sum(1 for m in manifests if m.get("runs_in")=="delegable-to-subagent"),
        "named_agents_used": len(used_agents),
        "tools": len(used_tools),
        "file_nodes": len(all_file_nodes),
        "edges": len(edges),
        "md_to_json_targets": sum(1 for n in all_file_nodes if v2_format(n)=="json"),
    },
    "nodes": {
        "skills": [{"id":m["name"],"runs_in":m.get("runs_in"),"requires_full_context":m.get("requires_full_context"),
                    "user_invokable":m.get("user_invokable",True),"spawns_generic_subagent":spawns_generic(m)}
                   for m in manifests],
        "agents": [{"id":a} for a in sorted(used_agents)],
        "tools": [{"id":t,"type":ty,"found":(ty!="external-package" and ty!="harness" and t in TOOL_MODULES)}
                  for t,ty in sorted(used_tools.items())],
        "files": [{"id":n,"kind":node_kind(n),"v2_format":v2_format(n),
                   "created_by":created_by(n),"edited_by":edited_by(n),"read_by":read_by(n),
                   "validated_by":validated_by(n)} for n in sorted(all_file_nodes)],
    },
    "edges": edges,
}
with open(GRAPH_OUT,"w",encoding="utf-8") as fh:
    json.dump(graph, fh, indent=2, ensure_ascii=False); fh.write("\n")

# ---- validate every emitted file ----
bad = []
for s in emitted:
    p = os.path.join(SKILLS_DIR,s,"skill.json")
    try: json.load(open(p,encoding="utf-8"))
    except Exception as e: bad.append((p,str(e)))
try: json.load(open(GRAPH_OUT,encoding="utf-8"))
except Exception as e: bad.append((GRAPH_OUT,str(e)))

print("=== AGGREGATION COMPLETE ===")
print(f"skills emitted : {len(emitted)}")
print(f"file nodes     : {len(all_file_nodes)}")
print(f"md->json targets: {graph['stats']['md_to_json_targets']}")
print(f"edges          : {len(edges)}")
print(f"main-agent / delegable : {graph['stats']['skills_main_agent']} / {graph['stats']['skills_delegable']}")
print(f"named agents used: {sorted(used_agents)}")
# surface any real missing tool refs
realmiss = sorted({mr['ref'] for s in emitted for mr in emitted[s]['missing_references'] if not mr['expected']})
print(f"REAL missing tool refs (not in scripts/lib or skills/*/scripts): {realmiss if realmiss else 'none'}")
print(f"invalid JSON files: {bad if bad else 'none'}")
# risk-register sanity
rr = '<vault>/risk-register.json'
print(f"\nrisk-register.json  created_by={created_by(rr)}")
print(f"risk-register.json  edited_by={edited_by(rr)}")
print(f"risk-register.json  read_by={read_by(rr)}")
print(f"risk-register.json  validated_by={validated_by(rr)}")
