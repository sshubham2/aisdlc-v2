---
name: critique-review
description: "Meta-Critic (DR-1) review of the first Critic's critique.json for false positives, false negatives, and severity miscalibrations. Runs inline on the main agent thread; spawns a 'critique-review' subagent via the Agent tool. Reads mission-brief.json, design.json, critique.json, and project-frame; classifies each finding as VALID/SUSPICIOUS/SEVERITY-WRONG; surfaces missed findings; runs the structural audit; writes critique-review.json. Use AFTER /critique, BEFORE /critique Step 4.5 TRI-1."
when_to_use: "Trigger phrases: /critique-review, 'meta-review the critique', 'second-pass critique', 'review the Critic', 'dual review'. Tier-driven (NOT mode-gated) — MANDATORY (CRP-1-enforced) when risk_tier=high OR critic_required is true (auth/data-model/contracts/security/methodology surface; Heavy forces it everywhere) OR first-Critic findings >=5; ADVISORY on a 3+ consecutive-clean calibration smell. See the canonical trigger table in /critique Step 3.5. Not required otherwise."
argument-hint: "[slice-id]"
allowed-tools: Read, Grep, Glob, Bash, Write, Agent
---

# /critique-review — meta-Critic (DR-1)

You are running **inline on the main agent thread**. Your job is to read slice artifacts, spawn the
`critique-review` subagent via the Agent tool, run the structural audit, then hand off to `/critique`
Step 4.5 TRI-1. The subagent is adversarial toward the first Critic's review, not the design itself.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git config
> `aisdlc/vault-root`). The spawned subagent does NOT inherit the project CLAUDE.md — resolve it in context.

## Step 0 — resolve the active slice (run this FIRST, BEFORE the Step-2 spawn)

Run this `bash` block first — it resolves the active slice in a BODY step that BINDS an explicit
`/critique-review slice-NNN` `$ARG`. A `!`-injection runs at skill-LOAD *before* `${ARGUMENTS}` binds, so
it CANNOT resolve a named slice (SC-064 / ADR-022). Read the printed JSON; use the resolved slice
**folder** for the Step-1 reads AND the Step-2 agent inputs — the spawn below MUST consume THIS
body-resolved slice, never a load-time injection (SC-064 M3 — the composition seam).
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"   # an explicit /critique-review slice-NNN resolves THAT slice; shape-guarded so a non-slice arg (or none) falls to the slice-014 --repo-root HALT path
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --json
else
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --json
fi
```

Read the following from the active slice folder (`<vault>/slices/slice-NNN-<name>/`):
- `mission-brief.json` — intent, ACs, must-not-defer, out-of-scope
- `design.json` — components touched, contracts, wiring matrix, ADR refs
- `critique.json` — first Critic's findings (primary input; **must exist**)
- Any `<vault>/decisions/ADR-*.json` referenced by this slice

If `critique.json` does not exist: write `critique-review.json` with
`"result":"PREREQUISITE-MISSING","message":"critique.json not found — run /critique first"` and stop.

## Project frame — run this `bash` block

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"
else
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"
fi
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$SDIR" 2>/dev/null || echo "(project-frame unavailable)"
```

Use the project frame to re-check whether the first Critic missed a direction-fit concern or flagged one that
contradicts a deliberate strategic choice. On empty/error: pass `(project-frame unavailable)` — advisory only, not a gate.

## Task

### Step 1 — Prerequisite check

Read `critique.json`. If it does not exist: write `critique-review.json` with
`"result":"PREREQUISITE-MISSING","message":"critique.json not found — run /critique first"` and stop.

If `critique.json` verdict is `clean` AND the slice `risk_tier` is `low` with no mandatory triggers
(auth/authz, API contracts, data model, security paths, methodology surface `skills/**`/`agents/**`/`scripts/**`):
note this to the user and proceed — the DR-1 pass still runs.

### Step 2 — Spawn the meta-Critic subagent

Use the **Agent tool** with `subagent_type: "critique-review"`. The agent carries the meta-Critic persona, its
decorrelated method (premortem + independent re-derivation), the review dimensions, the classification vocabulary,
and the output schema — do NOT re-state them here (they live in `agents/critique-review.md`). Pass only inputs:

```
Slice: slice-NNN-<name>

# mission-brief.json
<full JSON contents>

# design.json
<full JSON contents>

# critique.json (the first Critic's findings — your primary input)
<full JSON contents>

# New ADRs this slice
<full JSON of each decisions/ADR-NNN.json, or "none">

# project-frame
<the injected project-frame text, or "(project-frame unavailable)">
```

Await the subagent's return value. On error: surface it and stop.

### Step 3 — Run the structural audit

```bash
ARG="${ARGUMENTS[0]:-}"
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --path-only)"
else
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --path-only)"
fi
$PY "${CLAUDE_SKILL_DIR}/scripts/critique_review_audit.py" "$SDIR"
```

The audit validates: required sections present, verdict in `{accept, adjust, extend}`, `reviewed_by` and
`date` fields populated. If violations: surface them; do not write a malformed file.

### Step 4 — Write critique-review.json

Write `<vault>/slices/slice-NNN-<name>/critique-review.json`
(schema: `examples/critique-review.json`).

### Step 4.1 — lint critique-review.json (receiving-inspection; ADR-033 / AC2)

After writing it, lint `critique-review.json` against its schema-by-example — run this ALONGSIDE the Step-3
structural audit (`critique_review_audit.py`), not instead of it: the example-driven lint adds the `_schema` tag +
`first_critic_verdict` / `summary` / `notes` key-presence + enum enforcement the hand-maintained audit omits, and
auto-tracks future schema changes (drift-proof; TRI-1 m3). On a violation, re-prompt the meta-Critic to re-emit a
conforming artifact (R-25 — never self-author). Do NOT hand off to TRI-1 with a malformed file.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --type critique-review "$SDIR/critique-review.json"; rc=$?
# exit 0 = clean · 1 = schema violation (re-prompt + rewrite + re-lint) · 2 = usage/tooling error (surface, NOT a clean pass)
[ "$rc" = 0 ] || echo "ARTIFACT-LINT: critique-review.json did not conform (rc=$rc) -- re-prompt the meta-Critic; do NOT hand off to TRI-1."
```

### Step 5 — Update milestone.json

Update `<vault>/slices/slice-NNN-<name>/milestone.json`: `stage: "critique-review"`,
`next_action: "/critique Step 4.5 TRI-1"`.

### Step 5b — the gate outcome is recorded at /critique Step 4.5 (ADR-045), NOT here

The critique-review (DR-1) gate-log row is **no longer emitted here.** It was previously written at this point —
BEFORE TRI-1 — so it could only carry `--findings-count` and never the `findings_real`/`findings_noise` split (the
ratified dispositions do not exist yet at Step 5b, which blinded `/critic-calibrate` 1e precision + `/pulse`). Per
**ADR-045 (slice-052)** the row is now emitted at **`/critique` Step 4.5**, post-TRI-1, where `triage_precision.py`
classifies the ratified `^M-add-` dispositions — exactly ONCE, and ONLY when the meta-Critic ran (a DR-1-skipped
slice emits ZERO rows; no phantom `count=0` row). The meta-Critic is still a **low** reality-contact gate. Do NOT
re-add a gate-log append here — `/critique` Step 4.5 is the single writer; a second writer would double-emit.

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
- **Model-on-model gate (Phase 1.3).** LOW reality-contact — a model reviewing a model's review. Advisory, and
  optional on low-tier slices (see the Step 1 prerequisite). Never overrides a reality gate (`/risk-spike`,
  `/validate-slice`); it sharpens the first Critic, it does not sign off against reality.

## Pipeline position

- predecessor: `/critique` · successor: `/critique` (Step 4.5 TRI-1) · auto-advance: true
- on-clean-completion: the main thread advances to `/critique` Step 4.5 (TRI-1 user triage).
- user-input gates: none on this skill's own path — the TRI-1 HALT lives in `/critique` after handoff.
