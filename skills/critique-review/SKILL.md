---
name: critique-review
description: "Meta-Critic (DR-1) review of the first Critic's critique.json for false positives, false negatives, and severity miscalibrations. Runs inline on the main agent thread; spawns a 'critique-review' subagent via the Agent tool. Reads mission-brief.json, design.json, critique.json, and project-frame; classifies each finding as VALID/SUSPICIOUS/SEVERITY-WRONG; surfaces missed findings; runs the structural audit; writes critique-review.json. Use AFTER /critique, BEFORE /critique Step 4.5 TRI-1."
when_to_use: "Trigger phrases: /critique-review, 'meta-review the critique', 'second-pass critique', 'review the Critic', 'dual review'. Recommended for: high-tier slices, slices touching auth/data-model/contracts, 3+ consecutive clean first-Critic reviews (calibration smell), or 5+ findings (severity-inflation check). Optional for low-tier minimal slices."
allowed-tools: Read, Grep, Glob, Bash, Write, Agent
---

# /critique-review — meta-Critic (DR-1)

You are running **inline on the main agent thread**. Your job is to read slice artifacts, spawn the
`critique-review` subagent via the Agent tool, run the structural audit, then hand off to `/critique`
Step 4.5 TRI-1. The subagent is adversarial toward the first Critic's review, not the design itself.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git config
> `aisdlc/vault-root`). The spawned subagent does NOT inherit the project CLAUDE.md — resolve it in context.

## Active slice + inputs — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --json
```

Read the following from the active slice folder (`<vault>/slices/slice-NNN-<name>/`):
- `mission-brief.json` — intent, ACs, must-not-defer, out-of-scope
- `design.json` — components touched, contracts, wiring matrix, ADR refs
- `critique.json` — first Critic's findings (primary input; **must exist**)
- Any `<vault>/decisions/ADR-*.json` referenced by this slice

If `critique.json` does not exist: write `critique-review.json` with
`"result":"PREREQUISITE-MISSING","message":"critique.json not found — run /critique first"` and stop.

## Project frame — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$AI_SDLC_VAULT_ROOT/slices/$(ls "$AI_SDLC_VAULT_ROOT/slices" | grep -v archive | tail -1)" 2>/dev/null || echo "(project-frame unavailable)"
```

Use the project frame to re-check whether the first Critic missed a direction-fit concern or flagged one that
contradicts a deliberate strategic choice. On empty/error: pass `(project-frame unavailable)` — advisory only, not a gate.

## Task

### Step 1 — Prerequisite check

Read `critique.json`. If it does not exist: write `critique-review.json` with
`"result":"PREREQUISITE-MISSING","message":"critique.json not found — run /critique first"` and stop.

If `critique.json` verdict is `accept` AND the slice `risk_tier` is `low` with no mandatory triggers
(auth/authz, API contracts, data model, security paths, methodology surface `skills/**`/`agents/**`/`scripts/**`):
note this to the user and proceed — the DR-1 pass still runs.

### Step 2 — Spawn the meta-Critic subagent

Invoke the `critique-review` subagent via the Agent tool, passing as context:
- The full contents of `mission-brief.json`, `design.json`, `critique.json`
- Any `decisions/ADR-*.json` referenced by the slice
- The injected project-frame text
- These instructions for the subagent:

> You are the **meta-Critic** (DR-1). Challenge the first Critic's findings for over-reach, under-reach,
> and severity miscalibration. You are adversarial toward the first Critic's review, not the design itself.
>
> **Classify every finding** in `critique.json` as exactly one of:
>
> | Classification | Meaning |
> |---|---|
> | `valid` | Accurate, specific, correctly calibrated. |
> | `suspicious` | Over-reach, mis-framed, or unsupported by the artifacts. State specifically why. |
> | `severity-wrong` | Real finding but severity is mis-set. State the correct severity and why. |
>
> **Specificity rule**: cite the original finding ID (e.g. `C1`, `B2`) AND one of: a specific design.json
> section, a mission-brief.json AC reference, or an ADR id.
> **Honesty rule**: `valid` when the first Critic was right. Do not manufacture SUSPICIOUS to justify this pass.
>
> **Surface missed findings**: independently apply the 8 review dimensions against mission-brief + design.
> Each missed finding must cite the dimension, state a concrete claim (link to a specific section or AC), and
> suggest a severity. If none: state "none" — do not manufacture.
>
> **Return** a JSON object matching `examples/critique-review.json` (schema: `aisdlc/critique-review@1`) with:
> `assessments[]` (finding, classification, note), `missed[]` (dimension, severity, claim), and `verdict`:
> `accept` (all valid, no missed), `adjust` (suspicious/severity-wrong; no missed changing go/no-go),
> `extend` (missed findings the Builder must address).

Await the subagent's return value. On error: surface it and stop.

### Step 3 — Run the structural audit

```bash
$PY ${CLAUDE_SKILL_DIR}/scripts/critique_review_audit.py "$AI_SDLC_VAULT_ROOT/slices/$(ls "$AI_SDLC_VAULT_ROOT/slices" | grep -v archive | tail -1)"
```

The audit validates: required sections present, verdict in `{accept, adjust, extend}`, `reviewed_by` and
`date` fields populated. If violations: surface them; do not write a malformed file.

### Step 4 — Write critique-review.json

Write `<vault>/slices/slice-NNN-<name>/critique-review.json`
(schema: `examples/critique-review.json`).

### Step 5 — Update milestone.json

Update `<vault>/slices/slice-NNN-<name>/milestone.json`: `stage: "critique-review"`,
`next_action: "/critique Step 4.5 TRI-1"`.

## Return

Return a 3-line summary:
1. `Verdict: <ACCEPT|ADJUST|EXTEND>`
2. `Assessments: <N valid, M suspicious, K severity-wrong>`
3. `Missed findings: <count> — run /critique Step 4.5 TRI-1 to reconcile both passes.`

The full review lives in `critique-review.json`.

## Critical rules

- **Read all three source files** (mission-brief.json, design.json, critique.json) before spawning the subagent.
  Never pass partial context.
- **Never skip the audit.** Malformed critique-review.json trips TRI-1 reconciliation at `/critique`.
- **Rubber-stamp smell.** 5+ consecutive `accept` verdicts when the first Critic flagged substantive
  findings each time is a calibration warning. Instruct the subagent to re-examine more aggressively.
- **No generic missed findings.** "The first Critic could be more thorough" is not a missed finding.
  Link to a specific design section or AC or drop it.
- **Project frame is advisory.** An unavailable project frame does not block this step.

## Pipeline position

- predecessor: `/critique` · successor: `/critique` (Step 4.5 TRI-1) · auto-advance: true
- on-clean-completion: the main thread advances to `/critique` Step 4.5 (TRI-1 user triage).
- user-input gates: none on this skill's own path — the TRI-1 HALT lives in `/critique` after handoff.
