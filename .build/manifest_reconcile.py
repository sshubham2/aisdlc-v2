# -*- coding: utf-8 -*-
"""manifest_reconcile.py — bring the .build/manifests/batch*.json design record in line
with the corrected v2 SKILL.md + the real v2 script layer. Idempotent. Dry-run by default;
pass --apply to write. Re-run `aggregate.py` afterwards to regenerate skill.json + graph.

Transforms:
  1. REMOVE dropped methodology_tools (forward-sync x3, vault_flip_prose_inventory,
     critique_agent_drift_audit).
  2. REPLACE slice's v1 slice_queue_writer/claim tools with v2 candidates_top + claim_candidate.
  3. RETARGET methodology_tools[].invocation: `$PY -m tools.X` -> the v2 absolute-path form
     ($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/X.py" for shared, .../scripts/X.py for
     single-skill); `.md` -> `.json` in file args.
  4. PROSE `tools.X` -> `scripts.lib.X` (shared) / `X` (single-skill) in summary/purpose/note/marker.
  5. discover risk-register UPDATE: raw-write -> vault_edit-append (SVW-1 exemptions = {triage,adopt}).
Also REPORTS (for manual follow-up): leftover dropped-tool name mentions in prose, and
file_access paths that look like dropped forward-sync / ~/.claude targets.
"""
import argparse, glob, json, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"C:\Users\sshub\aisdlc-v2"
MAN = os.path.join(ROOT, ".build", "manifests")
LIB = os.path.join(ROOT, "scripts", "lib")

SHARED = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(LIB, "*.py"))
          if not os.path.basename(p).startswith("__")}
DROPPED = {"methodology_changelog_forward_sync", "ai_sdlc_version_forward_sync",
           "ai_sdlc_tools_version_forward_sync", "vault_flip_prose_inventory",
           "critique_agent_drift_audit"}
SLICE_QUEUE = {"slice_queue_writer", "slice_queue_claim"}

SLICE_REPLACEMENTS = [
    {"name": "candidates_top",
     "invocation": '$PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --vault "$AI_SDLC_VAULT_ROOT" --top 5',
     "purpose": "Rank the live candidates.json backlog (blocked-on-spike + unmet-deps flagged) for the Step 1 recommendation."},
    {"name": "claim_candidate",
     "invocation": '$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" --candidate <SC-NNN> --slice slice-NNN-<name>',
     "purpose": "SVW-1 atomic claim: set status=spiking / progress=spike / claimed_by / started_at / slice + history + pick_log."},
]

DROPPED_TOKEN_RE = re.compile(r"|".join(sorted(DROPPED | SLICE_QUEUE, key=len, reverse=True)))
LEFTOVER_RE = re.compile(r"forward.?sync|MCFS|critique_agent_drift", re.I)

# Stale file_access whole-entry drops (dropped forward-sync / ~/.claude parity targets).
DROP_PATHS = {"methodology-changelog.md"}

# Exact prose-clause removals for the dropped forward-sync / critic-drift model.
PROSE_REPLACEMENTS = [
    ("runs forward-sync gates, ", ""),
    ("Run forward-sync gates and build_checks_integrity tool", "Run build_checks_integrity"),
    ("List archived slice folders (ls -t); run critique_agent_drift_audit",
     "List archived slice folders (ls -t)"),
]


def apply_prose_replacements(obj):
    """Recursively apply the exact-string PROSE_REPLACEMENTS to every string value."""
    if isinstance(obj, str):
        for old, new in PROSE_REPLACEMENTS:
            obj = obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [apply_prose_replacements(v) for v in obj]
    if isinstance(obj, dict):
        return {k: apply_prose_replacements(v) for k, v in obj.items()}
    return obj


def tool_path(name):
    if name in SHARED:
        return '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/%s.py"' % name
    return '$PY "${CLAUDE_SKILL_DIR}/scripts/%s.py"' % name


def fix_inv(inv):
    if not inv:
        return inv
    inv = re.sub(r"(?:\$PY|python)\s+-m\s+tools\.(\w+)", lambda m: tool_path(m.group(1)), inv)
    inv = re.sub(r"\.md\b", ".json", inv)
    return inv


def fix_prose(s):
    if not s:
        return s
    return re.sub(r"\btools\.(\w+)",
                  lambda m: f"scripts.lib.{m.group(1)}" if m.group(1) in SHARED else m.group(1), s)


def reconcile(apply):
    stats = {"removed": 0, "replaced": 0, "inv": 0, "prose": 0, "discover": 0, "dropped_fa": 0}
    leftover, fwd_paths = [], []
    for f in sorted(glob.glob(os.path.join(MAN, "batch*.json"))):
        data = json.load(open(f, encoding="utf-8"))
        for idx, m in enumerate(data):
            s = m["name"]
            # 1+2: filter/replace methodology_tools
            mts = m.get("methodology_tools", [])
            kept = []
            for t in mts:
                if t["name"] in DROPPED:
                    stats["removed"] += 1
                    continue
                if t["name"] in SLICE_QUEUE:
                    continue  # replaced below
                kept.append(t)
            if any(t["name"] in SLICE_QUEUE for t in mts) and s == "slice":
                existing = {t["name"] for t in kept}
                for rep in SLICE_REPLACEMENTS:
                    if rep["name"] not in existing:
                        kept.append(dict(rep)); stats["replaced"] += 1
            # 3: invocation retarget + .md->.json
            for t in kept:
                old = t.get("invocation", "")
                new = fix_inv(old)
                if new != old:
                    t["invocation"] = new; stats["inv"] += 1
                pp = fix_prose(t.get("purpose", ""))
                if pp != t.get("purpose", ""):
                    t["purpose"] = pp; stats["prose"] += 1
            m["methodology_tools"] = kept
            # drop stale forward-sync / ~/.claude-parity file_access entries
            fa_before = len(m.get("file_access", []))
            m["file_access"] = [fa for fa in m.get("file_access", []) if fa["path"] not in DROP_PATHS]
            stats["dropped_fa"] += fa_before - len(m["file_access"])
            # 4: prose fields
            for key in ("summary", "context_rationale"):
                v = fix_prose(m.get(key, ""))
                if v != m.get(key, ""):
                    m[key] = v; stats["prose"] += 1
            for et in m.get("external_tools", []):
                for key in ("purpose", "note"):
                    v = fix_prose(et.get(key, ""))
                    if v != et.get(key, ""):
                        et[key] = v; stats["prose"] += 1
            for h in m.get("harness_tools", []):
                v = fix_prose(h.get("purpose", ""))
                if v != h.get("purpose", ""):
                    h["purpose"] = v; stats["prose"] += 1
            for fa in m.get("file_access", []):
                for key in ("purpose", "safety_marker"):
                    v = fix_prose(fa.get(key) or "")
                    if v != (fa.get(key) or ""):
                        fa[key] = v; stats["prose"] += 1
                # 5: discover risk-register update -> vault_edit-append
                if s == "discover" and "risk-register" in fa["path"] and fa["access"] == "update":
                    if fa.get("mechanism") != "vault_edit-append":
                        fa["mechanism"] = "vault_edit-append"
                        fa["safety_marker"] = "<!-- route: scripts.lib.vault_edit append -->"
                        stats["discover"] += 1
            # exact-string prose-clause removals; reassign the final manifest
            data[idx] = apply_prose_replacements(m)
            blob = json.dumps(data[idx], ensure_ascii=False)
            for mt in set(DROPPED_TOKEN_RE.findall(blob)) | set(LEFTOVER_RE.findall(blob)):
                leftover.append(f"{s}: {mt}")
        if apply:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False); fh.write("\n")
    print(("APPLIED" if apply else "DRY-RUN") + " — " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if leftover:
        print("\nLEFTOVER dropped/forward-sync mentions (manual review):")
        for x in sorted(set(leftover)):
            print("  " + x)
    else:
        print("leftover dropped/forward-sync mentions: none")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    reconcile(ap.parse_args().apply)
