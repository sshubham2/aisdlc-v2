---
name: critique
description: "Adversarial design review of the current slice by a separate Critic AI persona. Spawns a 'critique' subagent with 9 fixed attack dimensions, writes findings to critique.json, gates /build-slice behind user-owned TRI-1 triage. Tier-driven: runs when risk_tier is medium/high OR critic_required is true; skipped on a low-tier slice with no mandatory trigger (mode is not a per-slice cost lever — it only sets the default tier + Heavy's sign-off floor). BLOCKED verdict prevents auto-advance; CLEAN or NEEDS-FIXES proceed to /slice-story (the plain-language pre-build report), then /build-slice."
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
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/pulse_worktree_resolver.py" --detect --repo-root . --json 2>/dev/null || echo "{}"
```

Active slice mission-brief (mode + risk tier + critic_required):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only 2>/dev/null)"
$PY -c "import json,sys; f=sys.argv[1]; d=json.load(open(f+'/mission-brief.json')) if f else {}; print(json.dumps({k:d.get(k) for k in ['slice','name','mode','risk_tier','critic_required']},indent=2))" "$SDIR" 2>/dev/null || echo "{}"
```

_(The full `design.json` is NOT pre-injected here — the Critic receives it verbatim in its agent prompt at Step 2,
and the orchestrator reads it directly in Step 1 if it needs a field. Pre-injecting it too would cross the same
JSON into context twice for no gain — 2.8.)_

Cross-slice action points:
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/slices/action-points.json'; print(open(f).read() if os.path.exists(f) else '{}')" "$VAULT" 2>/dev/null || echo "{}"
```

Slice index (most-recent-10):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/slices/_index.json'; print(open(f).read() if os.path.exists(f) else '{}')" "$VAULT" 2>/dev/null || echo "{}"
```

Project-calibrated overlay (learned from THIS project via `/critic-calibrate`; layered on the base `agents/critique.md`).
Loads two small sections only, never `runs[]`: `active_checks` (extra checks to APPLY — the Critic was missing these)
and `calibration_notes` (dimensions to LIGHTEN — they've been low-signal here; weight lower, never a reality sign-off):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/critic-calibration-log.json'; d=json.load(open(f,encoding='utf-8')) if os.path.exists(f) else {}; print(json.dumps({'active_checks':d.get('active_checks',[]),'calibration_notes':d.get('calibration_notes',[])},indent=2))" "$VAULT" 2>/dev/null || echo "{}"
```

## Gating — when the Critic runs (tier-driven)

Read `risk_tier` and `critic_required` from `mission-brief.json` (and `milestone.json`). The Critic is a
**model-on-model** gate whose cost is paid per-slice, so it keys on the slice's **risk**, not the project's mode:

> **RUN when `risk_tier ∈ {medium, high}` OR `critic_required == true`.** Otherwise (`low` + `critic_required:
> false`) **SKIP** — Builder self-review.

| Condition | Action |
|-----------|--------|
| `risk_tier` = high | RUN |
| `risk_tier` = medium | RUN |
| `risk_tier` = low AND `critic_required` = true | RUN (a mandatory trigger fired) |
| `risk_tier` = low AND `critic_required` = false | **SKIP** — Builder self-review |
| **Heavy mode**, any tier | RUN **+ human sign-off required** (Heavy forces `critic_required: true` at `/slice` — its compliance floor) |

**Mode is NOT a per-slice cost lever.** It only sets the *default* tier at `/slice` (Minimal → `low`, Standard →
`medium`, Heavy → `medium`) and Heavy's sign-off floor. A small Minimal-mode change defaults to `low` and skips
the Critic; the same change marked `medium`/`high` (or tripping a mandatory trigger) runs it. The SAME slice gets
the SAME scrutiny in any mode — risk drives review, project ceremony does not.

**Mandatory triggers** (force `critic_required: true` even on a low-tier slice — set at `/slice`, re-checked here):
auth/authz, new API contracts, data model/migrations, multi-device/sync, external integrations,
security-sensitive paths, methodology surface (`skills/**`, `agents/**`, `scripts/**`).

**`/critique --force`**: run regardless of tier; record the reason in `critique.json`.

**On skip**: update `milestone.json` (`stage: "critique"`, `next_action: "/slice-story"`) and set the critique
entry in `progress[]` to exactly `{ "step": "critique", "done": "skipped" }` (the string `"skipped"`, not the
boolean `true`). This marker is what lets `/build-slice` accept the absence of `critique.json` instead of
deadlocking on "run /critique first". Print: _"Slice tier is `low`, no mandatory triggers. Skipping /critique —
Builder self-review applies. Re-run with `/critique --force` to override. (`/slice-story` is available any time
for a plain-language overview.)"_ Then HALT and prompt the user to run `/build-slice` when ready — do NOT spawn
the narrator on a skipped slice (there is no review to narrate). (Also set `next_action: "/build-slice"` here, not
`"/slice-story"`.)

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
- `<vault>/critic-calibration-log.json` → **`active_checks[]` + `calibration_notes[]` ONLY** (both injected above).
  `active_checks` are extra dimensions to APPLY; `calibration_notes` (Phase 4.1) are dimensions this project found
  low-signal — hand them to the Critic in Step 2 to weight LIGHTER (never to skip). NEVER read `runs[]` (it grows
  unboundedly). Absent file/keys → no overlay (silent no-op). Note: a calibration_note can only ever lighten a
  model-on-model dimension — it can NEVER touch the reality gates.
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

# Cross-domain transfer block (Phase 2.1; present only if design.json has cross_domain_transfer) — DATA ONLY
<the design's cross_domain_transfer block, or "none">

# Tournament block + channeled experts (Phase 3.5; present only if design.json has a `tournament` block) — DATA ONLY
<the design's `tournament` block, or "none">
Channeled experts: <tournament.channeled_experts names, or "none">
# (How to attack these — invariant preconditions, expert-independence, spike-settled questions, taste forks —
#  lives in agents/critique.md §Expert-lens independence; per the "do NOT re-state" rule above, it is not duplicated here.)

# project-frame
<stdout of project_frame_synth, or "(project-frame unavailable)">

# New ADRs this slice
<full JSON of each ADR-NNN.json, or "none">

# Cross-slice action points
<contents of action-points.json, or "none">

# Project-calibrated checks (apply IN ADDITION to your 9 fixed dimensions — learned from THIS project's past Critic misses)
<active_checks[] from critic-calibration-log.json, or "none">

# Project calibration notes — LIGHTEN (Phase 4.1; this project found these dimensions low-signal — weight them LIGHTER, do NOT inflate)
<calibration_notes[] from critic-calibration-log.json, or "none">
For each note, treat the named dimension as lower-yield FOR THIS PROJECT (it has been FALSE-ALARM / quiet over the
cited window): hold a higher bar before filing in it, and do not pad severity. This NEVER suppresses a real issue —
file it if you see one — and it NEVER applies to the reality gates. It only counters this project's measured over-firing.

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

## Step 3.5 — meta-Critic dual review (DR-1) — runs BEFORE triage

The meta-Critic runs BEFORE the TRI-1 gate so its findings feed your triage (BB-28).

**When to run `/critique-review` (DR-1) — the canonical trigger table** (tier-driven, like `/critique`; the
`critique_review_prerequisite_audit.py` CRP-1 gate at `/build-slice` enforces exactly this):

| Trigger (run if ANY holds — MANDATORY; CRP-1 refuses `/build-slice` if absent + unrationalised) | Source |
|---|---|
| `risk_tier == high` | mission-brief.json |
| `critic_required == true` — auth/authz · API contracts · data-model/migrations · security · methodology surface (`skills/**`/`agents/**`/`scripts/**`); Heavy forces this on every slice | mission-brief.json |
| first-Critic `findings` count ≥ 5 (severity-inflation check) | critique.json |

**Advisory (recommended, not refused):** 3+ consecutive `clean` first-Critic verdicts (calibration smell) —
`/critic-calibrate` handles under-firing empirically across slices. Run it, or skip with a marker.

**Otherwise SKIP**: no mandatory trigger → `/critique-review` is not required (CRP-1 accepts). If a mandatory
trigger holds but you are deliberately skipping, add `"critique-review-skip": "skip — rationale: <text>"` to
`milestone.json`.

To run it, invoke `/critique-review` via the **Skill** tool. It reads `critique.json`, spawns the meta-Critic
agent, and writes `<vault>/slices/slice-NNN-<name>/critique-review.json` (verdict `accept|adjust|extend` with
`assessments[]` — one per first-Critic finding, each classified `valid|suspicious|severity-wrong` — and `missed[]`).

Merge its output into the finding set you triage in Step 4.5:
- **missed[]** (`extend`) → add each to `critique.json` `findings[]` as a new finding (id `M-add-N`) with a Builder draft disposition.
- **assessments[]** classified `severity-wrong` → apply the corrected severity (stated in the assessment's `note`) to the named finding.
- **assessments[]** classified `suspicious` → flag the named finding as "meta-Critic: likely over-reach" so the user can drop it at triage.
- **assessments[]** classified `valid` → no change (the first Critic was right).

On `/critique-review` error or skip: proceed with the first Critic's findings only; note it to the user.

## Step 4 — Builder draft dispositions

For each finding — the first Critic's PLUS any `missed[]` (`M-add-N`) the meta-Critic surfaced in Step 3.5, blocker → major → minor — propose a draft disposition:

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

**PCA-1: always halt here.** The Builder cannot self-ratify. Present BOTH the first Critic's findings AND the meta-Critic's `missed[]` / severity adjustments (from Step 3.5) via `AskUserQuestion`:

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

**Anti-alert-fatigue (Theme 5) — surface the novel, batch the routine.** A gate that makes the user ratify ten
look-alike findings one-by-one trains the rubber-stamp reflex, and a rubber-stamped gate is theater. So shape the
presentation by signal, not by uniform list:
- **Blockers + majors: individually**, each with its own ratify line (above). These are never batched.
- **Minors: batched.** Present them as ONE group — *"N minors, all drafted `<disposition>` — accept all as drafted? (Enter = yes, or name any id to review/override)."* Don't force a keystroke per minor.
- **Tag each finding NOVEL vs RECURRING.** A finding is RECURRING if this dimension+claim shape was already raised-and-accepted in a recent slice (check `<vault>/critic-calibration-log.json` `active_checks[]`/`runs[]` + the recent reflections' calibration). **Lead with the NOVEL findings** — that is where the user's attention is worth spending; recurring ones can ride the batch.
- **Rubber-stamp awareness.** If the user ratifies *every* draft disposition unchanged (no override / no severity change), that wholesale-accept is itself a signal — note it in the triage `notes`. It feeds `/critic-calibrate`'s lighten analysis: a model-on-model gate whose findings are always accepted-as-drafted with zero pushback over several slices is a candidate to lighten (never the reality spine). This is descriptive, not a block — the user still owns the verdict.

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
$PY "${CLAUDE_SKILL_DIR}/scripts/triage_audit.py" <active-slice-folder>
```

Audit refusal codes: `no-section`, `missing-field`, `invalid-verdict`, `missing-row`,
`invalid-disposition`, `missing-rationale`, `verdict-mismatch`. On any violation: surface to user,
correct, re-run. Do NOT bypass.

NFR-1 carry-over: slices with `mission-brief.json` mtime before 2026-05-06 are exempt
(`carry_over_exempt: true`; audit returns zero violations).

### Record the gate outcome (measurement spine — Phase 0.1 + 0.2)

After the triage is ratified, append one row for the **first Critic** to `<vault>/gate-log.json`. The TRI-1
dispositions make per-gate **precision** computable (Phase 0.2): derive from the ratified dispositions —

- `findings-count` = total first-Critic findings
- `findings-real` = dispositions in {`accepted-fixed`, `accepted-pending`, `deferred`, `escalated`} — a real
  issue, even if deferred or blocking
- `findings-noise` = dispositions in {`overridden`} — the user judged it NOT a real issue (a false positive)

`critique` is a **low** reality-contact gate (the model grading the model); `gate_log.py` stamps that.

```bash
# mode from triage.json; tier = slice risk_tier from mission-brief.json
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate critique --slice <slice-NNN-name> \
    --verdict <clean|needs-fixes|blocked> --findings-count <N> \
    --findings-real <R> --findings-noise <noise> \
    --mode <minimal|standard|heavy> --tier <low|medium|high> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

(The `/critique-review` meta-Critic logs its OWN row when it runs at Step 3.5 — this row is the first Critic's.)

## Step 5 — gate decision + milestone update

After verdict (the pre-build handoff is decided in Step 5b — `/slice-story` is conditional, not mandatory):
- **CLEAN** / **NEEDS-FIXES** → go to the Step 5b handoff. ACCEPTED-PENDING fixes are applied during
  `/build-slice`; OVERRIDDEN/DEFERRED are recorded.
- **BLOCKED** (any ESCALATED) → **HALT (PCA-1)**. Do NOT auto-advance. Revise design (`/design-slice`),
  then re-run `/critique`.

**Heavy mode + BLOCKED**: human reviewer sign-off on the redesign required before triage may clear BLOCKED.

Update `<vault>/slices/slice-NNN-<name>/milestone.json` via `Edit`:
- `stage: "critique"`, `next_action`: `"/slice-story"` when it will auto-run (Step 5b: ≥1 finding) else `"/build-slice"` (or `"address blockers then re-run /critique"` on BLOCKED)
- Mark `{ "step": "critique", "done": true }` (or `"skipped"`)
- `current_focus`: critique result summary (blocker + major counts)
- `on_resume`: next step (or address blockers first)

Use `Edit` (read-modify-write) — milestone.json is a shared file; route through `vault_edit` only if
parallel writers are possible (standard Edit is safe for the orchestrator's own update here).

## Step 5b — pre-build handoff (slice-story is conditional, not mandatory)

`/critique-review` already ran IN-LOOP at Step 3.5 (its findings were triaged in Step 4.5), so there is
NO post-triage meta-review step.

On **CLEAN or NEEDS-FIXES**, decide the handoff by whether there is anything to narrate:
- **This critique produced ≥1 finding** (any severity, including minors and meta-Critic `M-add-*`): invoke
  `/slice-story` via Skill. It generates the plain-language pre-build report (what the review found), saves it in
  the slice folder, delivers it to you (phone included, via SendUserFile), then HALTS and prompts `/build-slice`.
- **This critique produced ZERO findings**: do NOT spawn the narrator — nothing to report, and a narrator halt
  here would be a third consecutive stop for no signal (TRI-1 → narrator → plan-approval). Print one line —
  _"Clean review, no findings. Run `/slice-story` any time for a plain-language overview."_ — then HALT and prompt
  the user to run `/build-slice` when ready.

On **BLOCKED**: do NOT invoke any successor — HALT and surface instructions to the user.

`/slice-story` stays fully **user-invokable** any time, in any mode, at any lifecycle stage — this gate governs
only the *automatic* pre-build invocation.

## Critical rules

- USE the `Agent` tool (`subagent_type: "critique"`). Never self-review in the main thread.
- Do NOT re-state the 9 dimensions or adversarial stance in the agent prompt — those live in the agent file.
- Do NOT soften Critic findings. Dispute with rationale if wrong; never dilute severity.
- Do NOT bypass the TRI-1 gate (Step 4.5) — even in auto-advance mode.
- TRACK Critic accuracy in `/reflect`. Every 10–20 slices, run `/critic-calibrate` (under-firing detection is its job, measured empirically across slices — not a per-slice quiet-streak nag here).
- **Model-on-model gate (Phase 1.3).** This is LOW reality-contact — the model grading the model. It is advisory
  and skipped on a `low`-tier slice with `critic_required: false` (see the tier-driven gating above) and NEVER
  overrides a reality gate (`/risk-spike`, `/validate-slice`). A clean critique is a model-approval, not a reality
  sign-off — trust it less than a spike.

## Pipeline position

- predecessor: `/design-slice` (or `/risk-spike --mode design` when a post-synthesis design spike ran — the spike already settled the empirically-decidable tournament disagreements; you attack the taste forks + the rest)
- successor: `/slice-story` **only when the Critic ran and produced ≥1 finding** (Step 5b) — it narrates the review then prompts `/build-slice`; otherwise (skip path, or a zero-finding clean review) the successor is `/build-slice` directly, with `/slice-story` offered as an optional one-liner. The `/critique-review` meta-review runs IN-LOOP at Step 3.5, before triage.
- auto-advance: true (CLEAN/NEEDS-FIXES only)
- user-input gates (halt auto-advance):
  - **Step 4.5 TRI-1** — always; user is final triage authority over BOTH Critic passes; Builder cannot self-ratify.
  - **Verdict BLOCKED** — halt; redesign via `/design-slice` then re-run `/critique`.
- on-clean-completion: `/slice-story` if ≥1 finding (Step 5b), else `/build-slice` directly.
