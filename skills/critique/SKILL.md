---
name: critique
description: "Adversarial design review of the current slice by a separate Critic AI persona. Spawns a 'critique' subagent with 9 fixed attack dimensions, writes findings to critique.json, gates /build-slice behind user-owned TRI-1 triage. Mandatory in Standard (medium/high tier) and Heavy modes; skippable on low-tier slices with no mandatory triggers. BLOCKED verdict prevents auto-advance; CLEAN or NEEDS-FIXES proceed to /build-slice."
when_to_use: "Trigger phrases: /critique, 'critique this design', 'review the slice design', 'have the Critic review', 'adversarial review'. Use after /design-slice, before /build-slice. The forked adversarial review returns to the main thread for the interactive TRI-1 user triage gate."
argument-hint: "[--force]"
allowed-tools: Read, Grep, Bash, Write, Edit, Agent, AskUserQuestion, Skill
---

# /critique — adversarial design review

Orchestrator runs in the **main thread** (interactive TRI-1 gate requires it). The adversarial heavy-lifting
is delegated to a forked `critique` subagent. Skill = orchestration; agent = work.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git config
> `aisdlc/vault-root`). Active slice = latest `<vault>/slices/slice-NNN-*/`.

## Live state — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --repo-root . --json 2>/dev/null || echo "{}"
```

Active slice mission-brief (mode + risk tier + critic_required):
```!
$PY -c "import json,glob,os; v=os.environ.get('AI_SDLC_VAULT_ROOT','architecture'); slices=sorted(glob.glob(f'{v}/slices/slice-*/mission-brief.json')); f=slices[-1] if slices else None; d=json.load(open(f)) if f else {}; print(json.dumps({k:d.get(k) for k in ['slice','name','mode','risk_tier','critic_required']},indent=2))" 2>/dev/null || echo "{}"
```

Active slice design.json (component contracts for Critic):
```!
$PY -c "import json,glob,os; v=os.environ.get('AI_SDLC_VAULT_ROOT','architecture'); slices=sorted(glob.glob(f'{v}/slices/slice-*/design.json')); f=slices[-1] if slices else None; print(open(f).read() if f else '{}')" 2>/dev/null || echo "{}"
```

Cross-slice action points:
```!
$PY -c "import json,os; v=os.environ.get('AI_SDLC_VAULT_ROOT','architecture'); f=f'{v}/slices/action-points.json'; print(open(f).read() if os.path.exists(f) else '{}')" 2>/dev/null || echo "{}"
```

Slice index (most-recent-10):
```!
$PY -c "import json,os; v=os.environ.get('AI_SDLC_VAULT_ROOT','architecture'); f=f'{v}/slices/_index.json'; print(open(f).read() if os.path.exists(f) else '{}')" 2>/dev/null || echo "{}"
```

## Mode/tier gating

Read `risk_tier` and `critic_required` from `mission-brief.json` and `milestone.json`.

| Mode    | Risk tier    | critic_required | Action                            |
|---------|-------------|-----------------|-----------------------------------|
| Heavy   | any          | any             | ALWAYS RUN + sign-off required    |
| Standard | medium/high | any             | ALWAYS RUN                        |
| Standard | low         | true            | RUN (mandatory trigger detected)  |
| Standard | low         | false           | **SKIP** — Builder self-review    |
| Minimal  | medium/high | any             | RUN (no sign-off)                 |
| Minimal  | low         | true            | RUN                               |
| Minimal  | low         | false           | **SKIP**                          |

**Mandatory triggers** (override low-tier to force `critic_required: true` even if `/slice` missed them):
auth/authz, new API contracts, data model/migrations, multi-device/sync, external integrations,
security-sensitive paths, methodology surface (`skills/**`, `agents/**`, `scripts/**`).

**`/critique --force`**: run regardless of tier; record the reason in `critique.json`.

**On skip**: update `milestone.json` (`stage: "critique"`, `next_action: "/build-slice"`, mark step done
as `skipped`). Print: _"Slice tier is `low`, no mandatory triggers. Skipping /critique — Builder self-review
applies. Re-run with `/critique --force` to override."_ Then invoke `/build-slice` via Skill.

## Prerequisite check

- Active slice folder found and `design.json` exists → continue.
- `design.json` missing → STOP: _"Run `/design-slice` first."_

## Step 1 — gather Critic context

Read (all from the active slice folder):
- `mission-brief.json` — intent, ACs, must-not-defer, out-of-scope, risk tier
- `design.json` — components touched, contracts, wiring matrix, ADR refs
- Any `decisions/ADR-*.json` created by this slice

Pattern-recognition inputs (query JSON vault directly; use code-review-graph / CRG for code-graph queries):
- `<vault>/slices/action-points.json` — curated cross-slice action-points register
- `<vault>/slices/_index.json` — most-recent-10 slice table
- Open individual `reflection.json` files **only** when action-points or _index point to a specific match.

Project-frame (PFS-1): run via Bash and capture stdout:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir <active-slice-folder>
```
On non-zero/empty output: pass `(project-frame unavailable)` — advisory, never a gate.

## Step 2 — spawn Critic subagent

Use the **Agent tool** with `subagent_type: "critique"`. The agent carries the adversarial persona,
9 dimensions, specificity/honesty rules — do NOT re-state them here. Pass only inputs:

```
Slice: slice-NNN-<name>
Mode: <Minimal | Standard | Heavy>
Risk tier: <low | medium | high>
Forced: <true | false>

# mission-brief.json
<full JSON contents>

# design.json
<full JSON contents>

# project-frame
<stdout of project_frame_synth, or "(project-frame unavailable)">

# New ADRs this slice
<full JSON of each ADR-NNN.json, or "none">

# Cross-slice action points
<contents of action-points.json, or "none">

# Specific archived reflections (only if directly relevant)
<contents, or "none">
```

**Await the real agent — never fabricate its output.**
The Agent tool may return an async acknowledgment; that is NOT the review. Wait for the actual
task-notification. Write `critique.json` ONLY from the agent's returned content (R-25: self-authoring
silently defeats the Builder↔Critic separation).

If findings look generic (no file refs): request a re-run — _"Findings must reference specific files /
ADRs / endpoints — re-attack with specificity."_ Note for `/critic-calibrate`.

## Step 3 — write critique.json

Write `<vault>/slices/slice-NNN-<name>/critique.json` (schema: `examples/critique.json`).

Required top-level fields: `_schema`, `slice`, `reviewed_by`, `verdict` (`clean|needs-fixes|blocked`),
`findings[]` (each: `id`, `dimension`, `severity`, `claim`, `fix`, `disposition`),
`dimensions_checked[]` (each: `dimension`, `result` — every dimension gets an entry, even `"none: <reason>"`),
`triage` (null until Step 4.5 ratification).

## Step 4 — Builder draft dispositions

For each finding (blocker → major → minor), propose a draft disposition:

| Disposition       | Meaning                                                          |
|-------------------|------------------------------------------------------------------|
| `accepted-fixed`  | Agreed + fix applied now (edit design.json / ADR before triage) |
| `accepted-pending`| Agreed; fix in `/build-slice` — state what will be done         |
| `overridden`      | Builder disagrees — must carry a specific rationale             |
| `deferred`        | Agreed in principle; punch to named slice/backlog target        |
| `escalated`       | Unknown blocks resolution — state what spike is needed          |

Per **TPHD-1**: when `accepted-fixed` edits rename test functions or AC row references in `mission-brief.json`
or `design.json`, harmonize the mission-brief test-first plan section in the same fix block.

Set `"disposition": <draft>` on each finding in `critique.json`. `"triage"` stays `null` until Step 4.5.

## Step 4.5 — TRI-1 user-owned triage (HALT gate)

**PCA-1: always halt here.** The Builder cannot self-ratify. Present via `AskUserQuestion`:

```
Critic findings for slice-NNN <name>:

  [Blocker] C1 <title>
    Critic: <issue summary>
    Builder draft: ACCEPTED-FIXED — <fix ref>
    Ratify? (Enter to accept, or: ACCEPTED-FIXED | ACCEPTED-PENDING | OVERRIDDEN | DEFERRED | ESCALATED + rationale)

  [Major] C2 <title>
    ...
  [Minor] C3 <title>
    ...
```

OVERRIDDEN / DEFERRED / ESCALATED MUST carry a non-empty rationale (triage audit refuses empty).

Once the user ratifies, compute **final verdict** mechanically:
- Any `escalated` → **BLOCKED**
- Else any `accepted-pending` → **NEEDS-FIXES**
- Else (only `accepted-fixed` / `overridden` / `deferred`) → **CLEAN**
- Zero findings → **CLEAN**

Update `critique.json` — write the `"triage"` object:
```json
"triage": {
  "ratified_by": "<user>",
  "at": "<iso-timestamp>",
  "verdict": "clean|needs-fixes|blocked",
  "dispositions": [
    { "finding": "C1", "action": "accepted-fixed", "rationale": "<ref>" }
  ]
}
```

Run triage audit:
```bash
$PY ${CLAUDE_SKILL_DIR}/scripts/triage_audit.py <active-slice-folder>
```

Audit refusal codes: `no-section`, `missing-field`, `invalid-verdict`, `missing-row`,
`invalid-disposition`, `missing-rationale`, `verdict-mismatch`. On any violation: surface to user,
correct, re-run. Do NOT bypass.

NFR-1 carry-over: slices with `mission-brief.json` mtime before 2026-05-06 are exempt
(`carry_over_exempt: true`; audit returns zero violations).

## Step 5 — gate decision + milestone update

After verdict:
- **CLEAN** → proceed to `/build-slice`
- **NEEDS-FIXES** → ACCEPTED-PENDING fixes applied during `/build-slice`; OVERRIDDEN/DEFERRED recorded
- **BLOCKED** (any ESCALATED) → **HALT (PCA-1)**. Do NOT auto-advance. Revise design (`/design-slice`),
  then re-run `/critique`.

**Heavy mode + BLOCKED**: human reviewer sign-off on the redesign required before triage may clear BLOCKED.

Update `<vault>/slices/slice-NNN-<name>/milestone.json` via `Edit`:
- `stage: "critique"`, `next_action: "/build-slice"` (or `"address blockers then re-run /critique"`)
- Mark `{ "step": "critique", "done": true }` (or `"skipped"`)
- `current_focus`: critique result summary (blocker + major counts)
- `on_resume`: next step (or address blockers first)

Use `Edit` (read-modify-write) — milestone.json is a shared file; route through `vault_edit` only if
parallel writers are possible (standard Edit is safe for the orchestrator's own update here).

## Step 5b — auto-advance

On CLEAN or NEEDS-FIXES: invoke `/critique-review` via the Skill tool (mandatory in Standard/Heavy for
methodology surfaces; advisory otherwise). After `/critique-review` returns, advance to `/build-slice`.

On BLOCKED: do NOT invoke any successor — HALT and surface instructions to the user.

## Critical rules

- USE the `Agent` tool (`subagent_type: "critique"`). Never self-review in the main thread.
- Do NOT re-state the 9 dimensions or adversarial stance in the agent prompt — those live in the agent file.
- Do NOT soften Critic findings. Dispute with rationale if wrong; never dilute severity.
- Do NOT bypass the TRI-1 gate (Step 4.5) — even in auto-advance mode.
- TRACK Critic accuracy in `/reflect`. Every 10–20 slices, run `/critic-calibrate`.
- "No issues found" on 3+ slices in a row is a smell — re-read more aggressively or escalate.

## Pipeline position

- predecessor: `/design-slice`
- successor: `/critique-review` (then `/build-slice`)
- auto-advance: true (CLEAN/NEEDS-FIXES only)
- user-input gates (halt auto-advance):
  - **Step 4.5 TRI-1** — always; user is final triage authority; Builder cannot self-ratify.
  - **Verdict BLOCKED** — halt; redesign via `/design-slice` then re-run `/critique`.
- on-clean-completion: invoke `/critique-review` via Skill, then `/build-slice`.
