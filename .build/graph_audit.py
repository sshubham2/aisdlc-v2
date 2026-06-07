# -*- coding: utf-8 -*-
"""Consistency audit of skill-graph.json — looks for dangling edges, orphans,
producer/consumer mismatches, stats drift, and classification inconsistencies.
Read-only. Prints findings grouped by severity."""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\sshub\aisdlc-v2"
G = json.load(open(os.path.join(ROOT, "skill-graph.json"), encoding="utf-8"))

skills = {n["id"] for n in G["nodes"]["skills"]}
agents = {n["id"] for n in G["nodes"]["agents"]}
tools  = {n["id"] for n in G["nodes"]["tools"]}
files  = {n["id"] for n in G["nodes"]["files"]}
file_nodes = {n["id"]: n for n in G["nodes"]["files"]}
skill_nodes = {n["id"]: n for n in G["nodes"]["skills"]}
all_nodes = skills | agents | tools | files

BLOCK, MAJOR, MINOR, INFO = [], [], [], []

# 1. dangling edges: every edge endpoint must resolve to a known node
EDGE_TARGET_SPACE = {
    "spawns": agents, "uses_tool": tools, "uses_harness": tools,
    "reads": files, "creates": files, "updates": files, "appends": files,
    "hands_off_to": skills,
}
for e in G["edges"]:
    if e["from"] not in skills:
        BLOCK.append(f"edge.from not a skill: {e}")
    space = EDGE_TARGET_SPACE.get(e["type"])
    if space is None:
        MAJOR.append(f"unknown edge type: {e}")
    elif e["to"] not in space:
        BLOCK.append(f"dangling {e['type']} edge -> '{e['to']}' (not in {('agents' if space is agents else 'tools' if space is tools else 'files' if space is files else 'skills')})")

# 2. stats drift: recompute every headline stat from the node/edge data
st = G["stats"]
recomputed = {
    "skills": len(skills),
    "skills_main_agent": sum(1 for n in G["nodes"]["skills"] if n["runs_in"] == "main-agent"),
    "skills_delegable": sum(1 for n in G["nodes"]["skills"] if n["runs_in"] == "delegable-to-subagent"),
    "named_agents_used": len(agents),
    "tools": len(tools),
    "file_nodes": len(files),
    "edges": len(G["edges"]),
    "md_to_json_targets": sum(1 for n in G["nodes"]["files"] if n["v2_format"] == "json"),
}
for k, v in recomputed.items():
    if st.get(k) != v:
        MAJOR.append(f"stats.{k} = {st.get(k)} but recomputed = {v}")

# 3. file producer/consumer sanity
for fid, fn in file_nodes.items():
    cb, eb, rb = fn["created_by"], fn["edited_by"], fn["read_by"]
    kind, fmt = fn["kind"], fn["v2_format"]
    # read or edited but never created — only OK for external/code/side-effect/directory targets
    if not cb and (rb or eb):
        if kind in ("external-artifact", "side-effect") or fid.startswith("./") or "tests/bugs" in fid or fid.endswith("/VERSION") or "CLAUDE.md" in fid:
            INFO.append(f"no-producer (expected, external/code): {fid}  read_by={rb} edited_by={eb}")
        else:
            MAJOR.append(f"vault file consumed but never created: {fid}  read_by={rb} edited_by={eb}")
    # created by >1 skill — usually a bug unless it's a shared opener (triage/adopt)
    if len(cb) > 1 and set(cb) - {"triage", "adopt"}:
        MINOR.append(f"created_by multiple skills: {fid} -> {cb}")
    # produced but never read and never edited downstream (dead output) — info only
    if cb and not rb and not eb and kind not in ("side-effect", "directory", "external-artifact"):
        INFO.append(f"write-only artifact (no reader): {fid} created_by={cb}")

# 4. edges vs inverse-link tables must agree (forward edge => inverse membership)
fwd_creates, fwd_reads, fwd_edits = {}, {}, {}
for e in G["edges"]:
    if e["to"] not in files: continue
    if e["type"] == "creates": fwd_creates.setdefault(e["to"], set()).add(e["from"])
    if e["type"] in ("updates", "appends"): fwd_edits.setdefault(e["to"], set()).add(e["from"])
    if e["type"] in ("reads", "updates"): fwd_reads.setdefault(e["to"], set()).add(e["from"])
for fid, fn in file_nodes.items():
    if set(fn["created_by"]) != fwd_creates.get(fid, set()):
        MAJOR.append(f"created_by/edge mismatch {fid}: table={fn['created_by']} edges={sorted(fwd_creates.get(fid,set()))}")
    if set(fn["edited_by"]) != fwd_edits.get(fid, set()):
        MAJOR.append(f"edited_by/edge mismatch {fid}: table={fn['edited_by']} edges={sorted(fwd_edits.get(fid,set()))}")
    if set(fn["read_by"]) != fwd_reads.get(fid, set()):
        MAJOR.append(f"read_by/edge mismatch {fid}: table={fn['read_by']} edges={sorted(fwd_reads.get(fid,set()))}")

# 5. runs_in <-> requires_full_context consistency
for n in G["nodes"]["skills"]:
    rfc, ri = n["requires_full_context"], n["runs_in"]
    if rfc and ri != "main-agent":
        MAJOR.append(f"{n['id']}: requires_full_context=True but runs_in={ri}")
    if not rfc and ri != "delegable-to-subagent":
        MAJOR.append(f"{n['id']}: requires_full_context=False but runs_in={ri}")

# 6. hands_off_to targets exist (already covered by dangling check, but report unreachable skills)
referenced = {e["to"] for e in G["edges"] if e["type"] == "hands_off_to"}
entrypoints = {"triage", "adopt", "diagnose", "query-design", "pulse", "bug-hunt"}
unreferenced = skills - referenced - entrypoints
if unreferenced:
    INFO.append(f"skills never handed-off-to (entrypoints/maintenance, expected): {sorted(unreferenced)}")

# 7. every tool node 'found' flag vs reality (skills/*/scripts or scripts/lib)
import glob as _g
real_tools = {os.path.splitext(os.path.basename(p))[0] for p in _g.glob(os.path.join(ROOT,"scripts","lib","*.py")) if not os.path.basename(p).startswith("__")}
real_tools |= {os.path.splitext(os.path.basename(p))[0] for p in _g.glob(os.path.join(ROOT,"skills","*","scripts","*.py"))}
for n in G["nodes"]["tools"]:
    if n["type"] == "methodology-tool":
        exists = n["id"] in real_tools
        if n.get("found") != exists:
            MAJOR.append(f"tool '{n['id']}' found={n.get('found')} but on-disk={exists}")

def dump(title, items):
    print(f"\n{'='*70}\n{title}: {len(items)}\n{'='*70}")
    for x in items: print(f"  - {x}")

dump("BLOCKERS (dangling refs / structural)", BLOCK)
dump("MAJOR (data inconsistencies)", MAJOR)
dump("MINOR (suspicious but maybe intentional)", MINOR)
dump("INFO (expected / contextual)", INFO)
print(f"\nSUMMARY: {len(BLOCK)} blockers, {len(MAJOR)} major, {len(MINOR)} minor, {len(INFO)} info")
