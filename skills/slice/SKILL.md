---
name: slice
description: "Define + claim the next thinnest vertical cut. Reads the ranked candidates.json backlog, recommends the next cut, claims it (worktree + branch + pick-log), writes mission-brief.json + milestone.json, then auto-advances to /risk-spike (the in-loop spike gate). Use after /reflect (or after /discover for slice 1)."
when_to_use: "Trigger phrases: /slice, 'define next slice', 'next slice', 'what should we build next'. First step of the per-slice loop."
argument-hint: "[optional slice description or hint]"
allowed-tools: Read, Grep, Glob, Bash, Write, AskUserQuestion
---

# /slice — define + claim the next cut

First step of the per-slice loop. A slice = one thin vertical cut, ≤1 day of AI work, big enough to retire risk or
ship user-visible value. You pick a candidate from the unified backlog, claim it, scaffold it, then auto-advance to
`/risk-spike`. **Candidate selection comes from `<vault>/candidates.json`** (pre-ranked, pre-materialized from
risks / diagnose findings / reflections / concept scope) — you do NOT re-run a multi-source fan-out.

## Live state — injected

Top live candidates (ranked; blocked-on-spike flagged):
```!
$PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --vault "$AI_SDLC_VAULT_ROOT" --top 5
```

Stranded-slice consult (R-26 — never define a slice on top of genuinely-stranded prior work):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json
```

- `status: divergent` (any entry `halt: true`) → **HALT** and present an `AskUserQuestion` gate naming the branch(es):
  resume via `/commit-slice`, continue its `/build-slice`, or proceed anyway (always offered).
- `status: clean` with informational entries → one-line note, **proceed** (parallel slices are normal).
- exit 2 (git unavailable) → surface stderr and continue (fail-visible, never silent).

## Step 1 — recommend (don't just list)
The injected candidates are already scored (priority.score, effort, blast_radius, blocked-on-spike). Present a
**ranked recommendation** with a 🏆 top pick and a one-line "why this one", plus 2–4 alternatives. If `/slice
"<description>"` was invoked, evaluate that intent against the ranking — say so if it isn't strongest. If the user
says "you pick" / autonomous → take #1.

**Candidate selection is a user-input gate**: HALT for the pick unless explicit intent was supplied or the user said
"you pick".

## Step 2 — bug-fix prelude (BFRD-1)
If the chosen candidate is a bug fix, a **failing repro test must exist first**. Treat it as a bug fix when ANY of:
- name matches `fix-*` / `*-fix` / `hotfix-*` / `harden-*-bug`; OR
- **`source.type` is `bug-hunt-finding`** — ALWAYS a defect (these come from /bug-hunt's correctness/security sweep,
  so the repro gate is mandatory); OR
- `source.type`/category otherwise identifies a defect — e.g. a `diagnose-finding` of category `correctness-bug` or
  `security` (a `diagnose-finding` of `dead-code`/`duplicate`/etc. is cleanup, NOT a bug — do not force repro); OR
- the description identifies a defect.

Check `<vault>/shippability.json` for a row whose `machine_cmd` targets `tests/bugs/*` (one that `/repro` — or
`/bug-hunt`'s own `/repro→/slice` handoff — may already have written; if so, BFRD-1 is satisfied, proceed). If absent
→ distill a one-line repro description, confirm it via an `AskUserQuestion` (Confirm / Modify / Not-a-bug-cancel). On
Confirm/Modify → invoke `/repro <desc>` once via Skill, re-check; on "Not a bug" → fail-closed (re-scope). One AC must
assert "the failing repro test passes at slice end". (`/repro` runs here before the worktree exists, so it writes the
failing test to the MAIN tree; **Step 5 relocates it into `$wt`** on the slice branch — WT-ROOT-1.)

## Step 3 — define the slice
- **Name**: verb-object (`add-receipt-upload`, `fix-thumbnail-orientation`). Never `phase-N` / vague nouns.
- **Risk tier**: `low | medium | high` (Step 3a). **Acceptance criteria** ≤5, testable. **Verification plan** per AC.
  **Must-not-defer** (auth/validation/error paths/logging — EVERY slice). **Out of scope**. Mid-slice smoke gate.

### Step 3a — risk tier (controls /critique)
`low` = pure CSS/copy/docs/test-only; `medium` = default; `high` = novel domain / first integration / extra scrutiny.
**Always set `critic_required: true`** (even if tier=low) when the slice touches: auth/authz, new API contracts, data
model/migrations, multi-device/sync, external integrations, security paths, or the methodology surface
(`skills/**`, `agents/**`, `scripts/**`). Tell the user when low-tier still triggers the Critic and why.

## Step 4 — scope check
≤5 ACs, ≤1 day, system stays shippable. If it exceeds → split.

## Step 5 — claim + scaffold (BRANCH-3: worktree at pick)
Once the candidate is settled AND scope passes, in order:
1. Compute paths: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder slice-NNN-<name> --repo-root .` (line 1 = wt path, line 2 = `slice/NNN-<name>`). `NNN = max(existing incl. archive) + 1`.
2. `git -C <main> worktree add <wt_path> -b slice/NNN-<name> <default>`. Failure → STOP, surface (nothing else ran).
3. Write the scaffold to the **external vault store**:
   - `<vault>/slices/slice-NNN-<name>/mission-brief.json` (schema: `examples/mission-brief.json`)
   - `<vault>/slices/slice-NNN-<name>/milestone.json` (schema: `examples/milestone.json`; `stage: "spike"`, `next_action: "/risk-spike"`)
   Failure → STOP, roll back the worktree (`git -C <main> worktree remove <wt_path>`).
4. **Claim the candidate** (fail-visible on unset git identity):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" \
       --candidate <SC-NNN> --slice slice-NNN-<name>
   ```
   This routes through `vault_edit` (SVW-1): sets the candidate `status: spiking`, `progress: spike`,
   `claimed_by {git_user, git_email}`, `started_at`, and appends the `pick_log` entry.
5. **Relocate any pre-worktree repro test into `$wt` (WT-ROOT-1 / repro fix)** — a `/repro` run before the worktree
   existed (BFRD-1 Step 2, or standalone) wrote the failing test to the MAIN tree. Move any untracked `tests/bugs/*`
   into the worktree and stage it on the slice branch, so the repro test + the coming fix co-locate (and the main
   tree stays clean — the WT-ROOT-1 audit will check this at build/validate):
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder slice-NNN-<name> --repo-root "$repo_root" | head -1)"
   for f in $(git -C "$repo_root" ls-files --others --exclude-standard -- 'tests/bugs/*' 2>/dev/null); do
     mkdir -p "$wt/$(dirname "$f")"; mv "$repo_root/$f" "$wt/$f"; git -C "$wt" add "$f"
   done
   ```
   (No bug-fix repro → the loop is a no-op. The fix slice will make this test pass; `/validate-slice` runs it from `$wt`.)

## Critical rules
- ASK before deciding the slice (unless explicit intent / "you pick"). ENFORCE scope (>1 day → split).
- VERB-OBJECT names only. INCLUDE must-not-defer EVERY slice. Do NOT design here — that's `/design-slice`.

## Pipeline position
- predecessor: `/reflect` (or `/discover` for slice 1) · successor: **`/risk-spike`** · auto-advance: true
- on-clean-completion: once the scaffold is written, the candidate is claimed, and a candidate was settled,
  invoke **`/risk-spike`** via the Skill tool — its in-loop spike gate must pass before `/design-slice`.
- user-input gates (halt auto-advance): candidate selection (Step 1); BFRD-1 confirm (Step 2).
