---
name: critique
description: "Adversarial design review of the current slice by a separate Critic AI persona. Spawns a 'critique' subagent with 9 fixed attack dimensions, writes findings to critique.json, gates /build-slice behind user-owned TRI-1 triage. Tier-driven: runs when risk_tier is medium/high OR critic_required is true; skipped on a low-tier slice with no mandatory trigger (mode is not a per-slice cost lever — it only sets the default tier + Heavy's sign-off floor). BLOCKED verdict prevents auto-advance; CLEAN or NEEDS-FIXES proceed to /slice-story (the plain-language pre-build report), then /build-slice."
when_to_use: "Trigger phrases: /critique, 'critique this design', 'review the slice design', 'have the Critic review', 'adversarial review'. Use after /design-slice, before /build-slice. The forked adversarial review returns to the main thread for the interactive TRI-1 user triage gate."
argument-hint: "[slice-id] [--force]"
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

Active slice mission-brief (mode + risk tier + critic_required) — **run this `bash` block FIRST**: it
resolves the active slice in a BODY step that BINDS an explicit `/critique slice-NNN` `$ARG` (a
`!`-injection runs at skill-LOAD before `${ARGUMENTS}` binds, so it CANNOT — SC-064 / ADR-022). Use the
printed `risk_tier` / `critic_required` for the Gating decision below, and `$SDIR` (the resolved slice
folder) for the Step-1 reads.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi   # no 2>/dev/null — surface an AMBIGUOUS HALT (exit 4) instead of silently mis-resolving
$PY -c "import json,sys; f=sys.argv[1]; d=json.load(open(f+'/mission-brief.json',encoding='utf-8')) if f else {}; print(json.dumps({k:d.get(k) for k in ['slice','name','mode','risk_tier','critic_required']},indent=2))" "$SDIR" 2>/dev/null || echo "{}"
```

_(The full `design.json` is NOT pre-injected here — the Critic receives it verbatim in its agent prompt at Step 2,
and the orchestrator reads it directly in Step 1 if it needs a field. Pre-injecting it too would cross the same
JSON into context twice for no gain — 2.8.)_

Cross-slice action points:
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/slices/action-points.json'; print(open(f,encoding='utf-8').read() if os.path.exists(f) else '{}')" "$VAULT" 2>/dev/null || echo "{}"
```

Slice index (most-recent-10):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/slices/_index.json'; print(open(f,encoding='utf-8').read() if os.path.exists(f) else '{}')" "$VAULT" 2>/dev/null || echo "{}"
```

Project-calibrated overlay (learned from THIS project via `/critic-calibrate`; layered on the base `agents/critique.md`).
Loads three small sections only, never `runs[]`: `active_checks` (extra checks to APPLY — the Critic was missing these),
`calibration_notes` (dimensions to LIGHTEN — they've been low-signal here; weight lower, never a reality sign-off), and
`gate_skips` (model gates this project measured at precision < 0.2 over ≥ 8 runs and chose to stop spawning on
discretionary slices — 3.2; honored by the gating table below):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/critic-calibration-log.json'; d=json.load(open(f,encoding='utf-8')) if os.path.exists(f) else {}; print(json.dumps({'active_checks':d.get('active_checks',[]),'calibration_notes':d.get('calibration_notes',[]),'gate_skips':d.get('gate_skips',[])},indent=2))" "$VAULT" 2>/dev/null || echo "{}"
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

**Calibrated gate-skip (3.2).** If the injected overlay carries a user-accepted `gate_skips[]` entry with
`target_gate: "critique"` (a project where `/critic-calibrate` measured the first Critic at precision < 0.2 over
≥ 8 runs with zero real blockers caught), honor it: **SKIP** this slice's Critic when `critic_required == false`
AND `risk_tier != high` (for `action: "tier-gate-high-only"`, skip on any non-high tier; for `action: "skip"`,
same). A **compliance-mandatory** trigger (`critic_required: true` — auth/data-model/security/methodology/Heavy) or
a `high` risk tier **ALWAYS runs the Critic** regardless of any gate-skip — calibration can retire a model gate's
*discretionary* firing, never its compliance floor, and **never** the reality spine. On a gate-skip SKIP, take the
**On skip** path below and note `gate-skip <GS-NNN>` in the milestone `current_focus` so the spine shows why.

**`/critique --force`**: run regardless of tier or any gate-skip; record the reason in `critique.json`.

**On skip**: update `milestone.json` (`stage: "critique"`, `next_action: "/build-slice"`) and set the critique
entry in `progress[]` to exactly `{ "step": "critique", "done": "skipped" }` (the string `"skipped"`, not the
boolean `true`). This marker is what lets `/build-slice` accept the absence of `critique.json` instead of
deadlocking on "run /critique first". Print: _"Slice tier is `low`, no mandatory triggers. Skipping /critique —
Builder self-review applies. Re-run with `/critique --force` to override. (`/slice-story` is available any time
for a plain-language overview.)"_ Then HALT and prompt the user to run `/build-slice` when ready — do NOT spawn
the narrator on a skipped slice (there is no review to narrate).

## Prerequisite check

- Active slice folder found and `design.json` exists **and parses as JSON** → continue. (Existence alone is not
  enough — a malformed file passes an existence gate and then explodes mid-skill in the agent prompt. Check:
  `$PY -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" <slice>/design.json` — non-zero →
  STOP: _"design.json is corrupted (not valid JSON) — fix or regenerate it via /design-slice."_)
- `design.json` missing → STOP: _"Run `/design-slice` first."_

## Step 1 — gather Critic context

Read (all from the active slice folder):
- `mission-brief.json` — intent, ACs, must-not-defer, out-of-scope, risk tier
- `design.json` — components touched, contracts, wiring matrix, ADR refs
- Any `decisions/ADR-*.json` created by this slice

Pattern-recognition inputs (query JSON vault directly; use code-review-graph / CRG for code-graph queries):
- `<vault>/slices/action-points.json` — curated cross-slice action-points register
- `<vault>/slices/_index.json` — most-recent-10 slice table
- `<vault>/critic-calibration-log.json` → **`active_checks[]` + `calibration_notes[]` + `gate_skips[]` ONLY** (all
  injected above). `active_checks` are extra dimensions to APPLY; `calibration_notes` (Phase 4.1) are dimensions this
  project found low-signal — hand both to the Critic in Step 2 (apply / weight LIGHTER, never skip). `gate_skips`
  (3.2) are consumed *earlier*, by the **gating decision** (whether to run the Critic at all — see the gating
  section) — NOT passed to the agent. NEVER read `runs[]` (it grows unboundedly). Absent file/keys → no overlay
  (silent no-op). Note: a calibration_note or gate_skip can only ever target a model-on-model gate — NEVER the
  reality gates.
- Open individual `reflection.json` files **only** when action-points or _index point to a specific match.

Project-frame (PFS-1): run via Bash and capture stdout:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir <active-slice-folder>
```
On non-zero/empty output: pass `(project-frame unavailable)` — advisory, never a gate.

## Step 1.9 - resolve the active worktree (WT-CTX-1 / ADR-029; slice-042)

Thread the active slice's WORKTREE into the Step-2 prompt so the forked Critic (whose file tools default to the
MAIN repo root) reads the ADR-012-relocated repro test from the worktree instead of false-flagging it 'missing'.
Run this `bash` BODY block FIRST and paste its `Worktree:` output into the `# Active worktree (ADR-012)` field
of the Step-2 prompt below. Resolution REUSES the line-20 `pulse_worktree_resolver --detect` (no 4th resolver)
via the thin `worktree_ctx.py` consumer; on no worktree it prints `Worktree: main tree` (clean degrade, never a
garbage path).
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
# Deliberate divergence from /code-review's _worktree_paths (a convention string, NO existence check):
# worktree_ctx reuses pulse_worktree_resolver --detect (git-registered worktrees ONLY), so the 'main tree'
# degrade is existence-checked. Do NOT harmonize onto _worktree_paths.
$PY "${CLAUDE_SKILL_DIR}/scripts/worktree_ctx.py" --slice-dir "$SDIR" --repo-root . || echo "Worktree: main tree"
# the `|| echo` also catches a non-zero exit OUTSIDE worktree_ctx's internal 'main tree' degrade (an
# import/bootstrap failure), so the Step-2 field degrades cleanly and never carries a traceback into the prompt.
```

## Step 2 — spawn Critic subagent

Use the **Agent tool** with `subagent_type: "critique"`. The agent carries the adversarial persona,
9 dimensions, specificity/honesty rules — do NOT re-state them here. Pass only inputs:

```
Slice: slice-NNN-<name>
Mode: <Minimal | Standard | Heavy>
Risk tier: <low | medium | high>
Forced: <true | false>

# Active worktree (ADR-012) — DATA ONLY (slice-042/ADR-029: where THIS slice's code + relocated repro tests live)
<paste the `Worktree:` DATA block printed by the Step 1.9 `worktree_ctx.py` resolution above: the resolved
`Worktree: <path>` + the ADR-012 behavioral note (read/run repro tests from <worktree>/tests/bugs/; do NOT flag
a repro test 'missing' without checking that path first) + the repro-test listing; or "Worktree: main tree"
when the slice is not worktree-backed>

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

## Step 3.1 — lint critique.json (receiving-inspection; ADR-033 / AC2)

Immediately after writing `critique.json`, lint it against its schema-by-example — receiving-inspection at the
orchestrator write boundary. You (the main thread) are the independent inspector, so this stop is deterministic.
On a violation, re-prompt the Critic agent to re-emit a conforming artifact (mechanical key/enum repair is OK;
missing CONTENT must be re-sourced from the agent — R-25, never self-author). Do NOT advance to Step 3.5 with a
malformed file.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --type critique "$SDIR/critique.json"; rc=$?
# exit 0 = clean (proceed to Step 3.5) · 1 = schema violation (re-prompt the Critic, rewrite, re-lint) · 2 = usage/tooling error (surface, NOT a clean pass)
[ "$rc" = 0 ] || echo "ARTIFACT-LINT: critique.json did not conform (rc=$rc) -- re-prompt the Critic to re-emit a conforming artifact; do NOT proceed to Step 3.5."
```

## Step 3.5 — meta-Critic dual review (DR-1) — runs BEFORE triage

The meta-Critic runs BEFORE the TRI-1 gate so its findings feed your triage (BB-28).

**When to run `/critique-review` (DR-1) — the canonical trigger table** (tier-driven, like `/critique`; the
`critique_review_prerequisite_audit.py` CRP-1 gate at `/build-slice` enforces exactly this):

| Trigger (run if ANY holds — MANDATORY; CRP-1 refuses `/build-slice` if absent + unrationalised) | Source |
|---|---|
| `risk_tier == high` | mission-brief.json |
| `critic_required == true` — auth/authz · API contracts · data-model/migrations · security · methodology surface (`skills/**`/`agents/**`/`scripts/**`); Heavy forces this on every slice | mission-brief.json |
| first-Critic `findings` count ≥ 5 (severity-inflation check) | critique.json |
| **full tournament convergence** — the design tournament's `approach_divergence` has NO designer pair classified `disjoint` (machine id `full-tournament-convergence`; slice-066 / ADR-064) | design.json |

Convergence is a **multi-clause predicate over an array**, not a scalar you can eyeball — so **compute it, don't
read it by eye** (a mis-read here skips DR-1, and CRP-1 only catches it later at `/build-slice`, running DR-1
*after* triage instead of before). The SAME `tournament_convergence` helper CRP-1 enforces with reports the
verdict, so both homes share ONE computation:
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/tournament_convergence.py" --slice "$SDIR" --json
# is_full_convergence:true -> the convergence trigger HOLDS (DR-1 mandatory). state:indeterminate
# (absent/malformed design.json) -> NO trigger, fail-visible, NEVER read as convergent.
```

**Advisory (recommended, not refused):** 3+ consecutive `clean` first-Critic verdicts (calibration smell) —
`/critic-calibrate` handles under-firing empirically across slices. Run it, or skip with a marker.

**Calibrated gate-skip (3.2):** a user-accepted `gate_skips[]` entry with `target_gate: "critique-review"`
suppresses only this **advisory** trigger (stop running critique-review on the 3-clean smell). The MANDATORY
triggers in the table above (`high` tier, `critic_required`, findings ≥ 5) ALWAYS hold regardless — CRP-1 enforces
them at `/build-slice`, and a gate-skip never removes a compliance/quality-floor trigger or touches the reality spine.

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

Prefer `AskUserQuestion` **structured options** (the fixed five-disposition vocabulary as options; "Other" for a
custom disposition + rationale) over free-text transcription — transcription typos are exactly what
`triage_audit` then bounces (`invalid-disposition` / `orphan-row`).

**Anti-alert-fatigue (Theme 5) — surface the novel, batch the routine.** A gate that makes the user ratify ten
look-alike findings one-by-one trains the rubber-stamp reflex, and a rubber-stamped gate is theater. So shape the
presentation by signal, not by uniform list:
- **Blockers + majors: individually**, each with its own ratify line (above). These are never batched.
- **Minors: batched.** Present them as ONE group — *"N minors, all drafted `<disposition>` — accept all as drafted? (Enter = yes, or name any id to review/override)."* Don't force a keystroke per minor.
- **Tag each finding NOVEL vs RECURRING.** A finding is RECURRING if this dimension+claim shape was already raised-and-accepted in a recent slice (check the injected `active_checks[]` overlay + the recent reflections' calibration — NEVER read `runs[]`; it grows unboundedly, per Step 1). **Lead with the NOVEL findings** — that is where the user's attention is worth spending; recurring ones can ride the batch.
- **Rubber-stamp awareness.** If the user ratifies *every* draft disposition unchanged (no override / no severity change), that wholesale-accept is itself a signal — set `"rubber_stamp": true` in the triage object (omit otherwise — structured, so `/critic-calibrate` can count it without text-mining) and note it in the triage `notes`. It feeds `/critic-calibrate`'s lighten analysis: a model-on-model gate whose findings are always accepted-as-drafted with zero pushback over several slices is a candidate to lighten (never the reality spine). This is descriptive, not a block — the user still owns the verdict.

Once the user ratifies, compute **final verdict** mechanically:
- Any `escalated` → **BLOCKED**
- Else any `accepted-pending` → **NEEDS-FIXES**
- Else (only `accepted-fixed` / `overridden` / `deferred`) → **CLEAN**
- Zero findings → **CLEAN**

**Deferred BLOCKER qualification (DD-15).** A `deferred` disposition on a **blocker**-severity finding is the
user knowingly building on top of an unresolved blocker — legitimate, but never an unqualified green:
- its rationale MUST name the concrete deferral target (a slice id or `SC-NNN` backlog candidate — "later" is
  not a target; re-ask if missing);
- list the ids in the triage object as `"deferred_blockers": ["C1", …]` (omit when none);
- the Step 5 milestone `current_focus` MUST carry the qualifier, e.g. `"CLEAN — 1 deferred blocker (C1 → SC-031)"`,
  so `/pulse` and a resume never render this as a plain clean.
(`overridden` blockers need no qualifier — the user judged the finding not-real, which is what the gate-log
precision row records.)

DD-15 is enforced MECHANICALLY by `triage_audit.py` (refusal kind `deferred-blocker`), not just by this prose:
a deferred blocker whose rationale lacks a `slice-NNN`/`SC-NNN` target, or that is missing from
`deferred_blockers[]` (or a `deferred_blockers[]` entry that isn't actually a deferred blocker), fails the audit.

Update `critique.json` — write the `"triage"` object:
```json
"triage": {
  "ratified_by": "<user>",
  "at": "<iso-timestamp>",
  "verdict": "clean|needs-fixes|blocked",
  "dispositions": [
    { "finding": "C1", "action": "accepted-fixed", "rationale": "<ref>" }
  ],
  "deferred_blockers": ["C1"],
  "rubber_stamp": true
}
```
(`deferred_blockers` — DD-15, present only when a blocker was deferred; `rubber_stamp` — present only on a
wholesale unchanged-accept; omit both otherwise, per the omit-empty convention.)

Run triage audit:
```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/triage_audit.py" <active-slice-folder>
```

Audit refusal codes: `no-triage`, `format`, `missing-field`, `invalid-verdict`, `missing-row`,
`orphan-row` (a disposition naming a nonexistent finding id), `invalid-disposition`,
`missing-rationale`, `deferred-blocker` (the DD-15 qualification, enforced mechanically),
`verdict-mismatch`. On any violation: surface to user, correct, re-run. Do NOT bypass.

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
# derive VAULT (4.6.1) + --out/--content-file (never pipe+--stdin: the double-apply-under-contention hazard)
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-cr-row.XXXXXX")"
"$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate critique --slice <slice-NNN-name> \
    --verdict <clean|needs-fixes|blocked> --findings-count <N> \
    --findings-real <R> --findings-noise <noise> \
    --mode <minimal|standard|heavy> --tier <low|medium|high> --out "$T/row.json" \
  && "$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$VAULT" --file gate-log.json --array entries --content-file "$T/row.json"; rc=$?
[ "$rc" = 0 ] || echo "STOP: critique gate-row append failed (rc=$rc) -- surface, never fire-and-forget (must-not-defer)" >&2
rm -rf "$T"
```

(This row is the **first Critic's** — it counts ONLY first-Critic findings, never the meta-Critic's `M-add-*`.)

**Then emit the `/critique-review` (DR-1) meta-Critic's OWN row — HERE, post-TRI-1 (ADR-045), NOT at
critique-review Step 5b.** The meta row's `findings_real`/`findings_noise` are only knowable once TRI-1 has
ratified the `M-add-*` dispositions, so it is emitted at this settlement point, exactly ONCE, and **only when a
meta-Critic actually ran this slice** — `triage_precision.py` returns the flags when `critique-review.json`
exists (and no `critique-review-skip` marker), or **nothing** when DR-1 was skipped, so a DR-1-skipped slice
emits ZERO rows (no phantom `count=0` row that would inflate `/pulse` runs — M-add-1). It classifies ONLY the
`^M-add-` dispositions via the shared SSOT rule (same real/noise sets as the first-Critic row above), degrading
to count-only (never hard-raising to block the append) on a stray disposition:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
cr_args="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/triage_precision.py" --critique-review-args --slice-dir "$SDIR")"; cr_rc=$?
if [ "$cr_rc" != 0 ]; then
  # CR1: a NON-ZERO exit (usage error / unreadable critique-review.json) must NOT masquerade as "DR-1 skipped".
  echo "STOP: triage_precision --critique-review-args failed (rc=$cr_rc) -- cannot compute the DR-1 gate row; surface, never silently skip (must-not-defer: emission is fail-visible)." >&2
elif [ -n "$cr_args" ]; then
  # slice-026 portable per-run temp dir: $PY's gettempdir() so the git-bash write + the two
  # Windows-Python tools (gate_log --out, vault_edit --content-file) resolve the SAME real path.
  TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
  T="$(mktemp -d "$TMPD/aisdlc-dr1row.XXXXXX")"
  # $cr_args word-splits into gate_log flags (--verdict V --findings-count N [--findings-real R --findings-noise K]); ASCII-only, safe unquoted
  "$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" --gate critique-review --slice <slice-NNN-name> $cr_args --mode <minimal|standard|heavy> --tier <low|medium|high> --out "$T/row.json" \
    && "$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$VAULT" --file gate-log.json --array entries --content-file "$T/row.json"; rc=$?
  [ "$rc" = 0 ] || echo "STOP: critique-review gate-row append failed (rc=$rc) -- surface, never fire-and-forget (must-not-defer)" >&2
  rm -rf "$T"
else
  echo "critique-review gate row: SKIPPED -- the meta-Critic (DR-1) did not run this slice (ADR-045/M-add-1: no phantom row)."
fi
```

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
  the user to run `/build-slice` when ready (tip: starting it in a fresh session — /clear first — is cheaper and
  just as safe; all resume state lives in the vault).

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
