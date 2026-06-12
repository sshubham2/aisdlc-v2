# -*- coding: utf-8 -*-
"""Rollout #3 (unified candidates.json) + #4 (in-loop risk-spike), applied to source manifests.
#3: one <vault>/candidates.json replaces backlog.md + slice-queue.md + risk-register-as-candidate;
    shipped candidates move to <vault>/archive/candidates.json (live/archive split).
#4: /risk-spike becomes the blocking slice step-0, driven by each candidate's assumptions[].
Re-run aggregate.py afterwards."""
import json, os
MAN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".build", "manifests")  # portable (4.8)
ROUTE = "<!-- route: tools.vault_edit append -->"
CAND  = "<vault>/candidates.json"
CARCH = "<vault>/archive/candidates.json"

def fa(path, kind, access, mech, purpose, fmt="json", modes=None, marker=None):
    return {"path":path,"kind":kind,"access":access,"mechanism":mech,"safety_marker":marker,
            "modes":modes or ["minimal","standard","heavy"],"format":fmt,"purpose":purpose}

batches = {i: json.load(open(os.path.join(MAN,f"batch{i}.json"),encoding="utf-8")) for i in range(8)}
M = {m["name"]: m for i in batches for m in batches[i]}     # refs into the batch lists
def rm(m, pred): m["file_access"]=[e for e in m["file_access"] if not pred(e)]
def add(m, e): m["file_access"].append(e)

# --- slice-candidates: backlog.md -> candidates.json (create) ---
m=M["slice-candidates"]
rm(m, lambda e: e["path"]=="diagnose-out/backlog.md")
add(m, fa(CAND,"content-file","create","vault_edit-append","Write confirmed-finding candidates (DAG-ordered) into the unified candidates backlog (replaces diagnose-out/backlog.md).",marker=ROUTE))
for s in m.get("local_scripts",[]):
    if s["name"]=="build_backlog.py":
        s["purpose"]+="  [v2: emits <vault>/candidates.json instead of backlog.md — code change owed]"

# --- slice: read+claim candidates.json; drop backlog.md + slice-queue.md; hand to risk-spike ---
m=M["slice"]
rm(m, lambda e: e["path"]=="diagnose-out/backlog.md")
rm(m, lambda e: e["path"]=="<vault>/slice-queue.md")
add(m, fa(CAND,"content-file","read","read","Primary candidate source — pick the next cut from the unified backlog (replaces backlog.md + slice-queue.md + risk-register-as-candidate)."))
add(m, fa(CAND,"content-file","update","vault_edit-append","Claim the picked candidate: status=spiking, progress=spike, claimed_by {git_user,git_email}, started_at; append to pick_log.",marker=ROUTE))
for e in m["file_access"]:
    if e["path"]=="<vault>/risk-register.md" and e["access"]=="read":
        e["purpose"]="BFRD-1 bug-class signal only; candidate selection now reads candidates.json (not the risk ledger)."
m["hands_off_to"]=["risk-spike","repro"]
m["summary"]=m["summary"].replace(
    "gathering candidates from the risk register, action-points register, diagnose-out backlog, concept scope, and code-review-graph queries, then scores and ranks them.",
    "reading the ranked candidate backlog from the unified candidates.json.").replace(
    "regenerates slice-queue.md with pick-log, and auto-advances to /design-slice.",
    "claims the picked candidate in candidates.json (status, claimed_by, started_at, pick_log), and auto-advances to /risk-spike (the in-loop spike gate).")

# --- discover: materialize first candidate; drop risk-spike from hand-off ---
m=M["discover"]
add(m, fa(CAND,"content-file","create","vault_edit-append","Materialize the named first slice candidate into the unified candidates backlog.",marker=ROUTE))
m["hands_off_to"]=[h for h in m["hands_off_to"] if h!="risk-spike"]

# --- risk-spike: REWRITE as in-loop blocking step-0 ---
m=M["risk-spike"]
m["summary"]=("First step of the slice loop (slice step-0 gate). Reads the picked candidate's blocking assumptions "
              "from candidates.json and proves each with throwaway code on real environments; spawns the field-recon "
              "subagent to survey current platform reality first. All assumptions proven -> advances to /design-slice; "
              "any FAILED -> marks the candidate blocked and halts the slice until a risk-free fallback is discussed and "
              "re-spiked. Records spike verdicts back into candidates.json and the risk ledger.")
m["context_rationale"]=("Interactive blocking gate: writes and runs throwaway proof code, needs credentials/device access, "
                        "and on failure requires a human discussion of a risk-free fallback before re-spiking — depends on the live slice session.")
add(m, fa(CAND,"content-file","read","read","Read the active candidate's blocking assumptions[] to prove."))
add(m, fa(CAND,"content-file","update","vault_edit-append","Record each assumption's spike_status (proven/failed), spike_ref, spike_evidence; on FAILED set candidate status=blocked + fallback; on all-proven set status=active, progress=design.",marker=ROUTE))
m["hands_off_to"]=["design-slice"]

# --- reflect: append discovered/deferred candidates + move shipped to archive ---
m=M["reflect"]
add(m, fa(CAND,"content-file","append","vault_edit-append","Append Discovered/Deferred items as new candidates.",marker=ROUTE))
add(m, fa(CAND,"content-file","update","vault_edit-append","Mark the shipped candidate complete, then MOVE it out of the live backlog (live/archive split).",marker=ROUTE))
add(m, fa(CARCH,"content-file","append","vault_edit-append","Destination for the shipped candidate moved out of candidates.json.",marker=ROUTE))

# --- validate-slice: reality-surprise candidates ---
add(M["validate-slice"], fa(CAND,"content-file","append","vault_edit-append","Append reality-surprise findings that spawn future work as new candidates.",marker=ROUTE))

# --- pulse: surface the backlog ---
add(M["pulse"], fa(CAND,"content-file","read","read","Surface the live candidate backlog (counts, top-priority, blocked-on-spike) in the macro-state summary."))

# --- triage / adopt: risk-spike is no longer a pre-pipeline step ---
for nm in ("triage","adopt"):
    M[nm]["hands_off_to"]=[h for h in M[nm]["hands_off_to"] if h!="risk-spike"]

for i in range(8):
    json.dump(batches[i], open(os.path.join(MAN,f"batch{i}.json"),"w",encoding="utf-8"), indent=2, ensure_ascii=False)
    open(os.path.join(MAN,f"batch{i}.json"),"a",encoding="utf-8").write("\n")
print("Rollout #3 + #4 applied to manifests.")
