---
name: risk-spike
description: "In-loop slice step-0 gate. Reads the picked candidate's blocking assumptions from candidates.json, spawns field-recon to survey current platform reality, then proves each assumption with throwaway code on real environments. All proven -> advances to /design-slice; any FAILED -> marks the candidate blocked and halts until a risk-free fallback is discussed and re-spiked. Records verdicts into candidates.json, risk-register.json, and spikes/spike-*.json."
when_to_use: "Trigger phrases: /risk-spike, 'spike the risks', 'prove the assumptions', 'run feasibility spike'. Auto-triggered by /slice after candidate claim; runs as step-0 of every slice. Also user-invokable directly with an optional candidate-id or risk-id argument to re-spike a specific assumption."
argument-hint: "[SC-NNN | R-NN | all]"
allowed-tools: Read, Write, Edit, Bash, Agent, AskUserQuestion, Skill
---

# /risk-spike — in-loop feasibility gate (slice step-0)

Step-0 of the per-slice loop. **No design happens until every blocking assumption is proven.**
Candidate picked by `/slice`; verdicts advance to `/design-slice` (all GO) or block the slice (any NO-GO).

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git `aisdlc/vault-root`).

## Active candidate — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" query --vault "$AI_SDLC_VAULT_ROOT" \
    --file candidates.json --array candidates --where status=spiking
```

If the injection returns empty and no argument was supplied, ask with `AskUserQuestion`:
"No spiking candidate found. Which candidate or risk should I spike? (SC-NNN / R-NN / all)"

## Step 1 — identify spike targets

- **From injected candidate**: extract `assumptions[]` where `blocking: true` and `spike_status != "proven"`.
- **From argument `SC-NNN`**: load that candidate's blocking assumptions.
- **From argument `R-NN`**: find the candidate whose `source.ref == R-NN`; use its blocking assumptions.
- **From argument `all`**: queue all candidates with `status in [candidate, spiking]` that have unproven blocking assumptions.

If there are zero unproven blocking assumptions: write a skip note, set `progress: design` in candidates.json,
advance to `/design-slice`.

## Step 2 — design each spike

For each assumption under test:

1. Check for prior spikes via CRG: `code-review-graph search "<technology> spike"` — skip re-spiking something already proven in a prior slice if the environment hasn't changed.
2. Produce a minimal test spec (10–30 lines). Must state:
   - **Real runtime** — not a local mock; actual platform / device / API
   - **Exact scopes / permissions / credentials** the real flow uses
   - **Expected outcome** — observable success
   - **Failure signal** — what failure looks like (do not conflate "didn't run" with "failed")

For multi-device / sync / sharing features: require 2+ instances. Single-instance tests do NOT validate
collaboration assumptions.

## Step 2.5 — field reconnaissance (per assumption)

Spawn the `field-recon` subagent via the Agent tool. Pass:
- **Target**: specific technology + version (e.g. `"Stripe webhook signature verification v2024"`)
- **Assumption under test**: one sentence
- **Use-case context**: what this project does with it

Write the agent's output to `<vault>/spikes/spike-<name>/field-recon.json`.

**Schema by example**: `examples/spike.json` (the `field-recon.json` sibling — same structure; `verdict` omitted, `method` = "web-survey").

**Asymmetric early-drop rule** (main thread decides; agent only recommends):

| Agent recommendation | Action |
|---|---|
| `drop` (official source contradicts assumption) | Flag to user. Default: skip Step 3, record NO-GO, go to Step 5. Ask only if there's reason the official doc is outdated. |
| `proceed-with-caveats` | Run Step 3; field-recon is a strong NO-GO prior. Target the specific concern surfaced. |
| `proceed` | Run Step 3 as planned. Even if docs confirm — **docs lie**. Empirical test is the point. |
| `inconclusive` | Run Step 3 normally. |

**Critical**: `drop` only on authoritative contradiction, never on a confirmation. Docs misrepresent
reality more often than they admit broken behavior (this rule's origin: Google Drive `drive.file` scope
docs claimed cross-account read; 8 sprints later, a spike would have caught it in 30 minutes).

If `field-recon.json` contains an authoritative contradiction, note in the spike doc that it is
**required reading for `/critique`** on any downstream slice touching the same technology.

## Step 3 — run the spike

If the target environment is available (connected device, local server, cloud account with credentials):

1. Write throwaway code to `<vault>/spikes/code/spike-<name>/`. Mark every file: `# THROWAWAY — not for production`.
2. Execute via Bash; capture output/logs.
3. Decide: **GO** / **NO-GO** / **CONDITIONAL**

   - **GO** — assumption holds; proceed with design
   - **NO-GO** — assumption fails; redesign required
   - **CONDITIONAL** — holds under specific constraints; design must respect them

   Be honest. Do NOT soften a NO-GO into CONDITIONAL because it's inconvenient.

If the environment is NOT available: stop, tell the user exactly what setup is needed, do not fabricate results.

## Step 4 — write spike artifact

Write `<vault>/spikes/spike-<name>.json`. **Schema by example**: `examples/spike.json`.

Key fields: `name`, `candidate`, `assumption` (id), `date`, `assumption_under_test`, `method`,
`verdict` (`go`/`no-go`/`conditional`), `evidence`, `fallback` (required on NO-GO), `risk_ref`.

## Step 5 — update vault artifacts

All writes route through `vault_edit` (SVW-1) for append/CAS files:

**Update `<vault>/risk-register.json`** — for each linked risk:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --vault "$AI_SDLC_VAULT_ROOT" \
    --file risk-register.json --array risks --id <R-NN> \
    --set status=<retired|blocking|conditional> \
    --set notes="spike <name>: <one-line rationale>"
```

Status mapping: GO → `retired`; NO-GO → `blocking`; CONDITIONAL → `conditional`.

**Update `<vault>/candidates.json`** — for each assumption:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --vault "$AI_SDLC_VAULT_ROOT" \
    --file candidates.json --array candidates --id <SC-NNN> \
    --assumption <A-id> \
    --set spike_status=<proven|failed> \
    --set spike_ref=spike-<name> \
    --set spike_evidence="<one-line summary>"
```

On all assumptions proven — set `status: active`, `progress: design`, append history entry:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --vault "$AI_SDLC_VAULT_ROOT" \
    --file candidates.json --array candidates --id <SC-NNN> \
    --set status=active \
    --set progress=design \
    --append history '{"event":"spike-passed","by":"risk-spike","at":"<ts>"}'
```

On any failed assumption — set `status: blocked`, `progress: blocked`, record `fallback` (required — discuss
with user if not already known), append history entry:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --vault "$AI_SDLC_VAULT_ROOT" \
    --file candidates.json --array candidates --id <SC-NNN> \
    --set status=blocked \
    --set progress=blocked \
    --set fallback="<agreed fallback description>" \
    --append history '{"event":"spike-blocked","reason":"assumption <A-id> failed","by":"risk-spike","at":"<ts>"}'
```

**Schema by example**: `examples/slice-candidates.json`, `examples/risk-register.json`.

## Step 6 — gate decision

**All assumptions proven (GO / CONDITIONAL):**
- Write `<vault>/slices/slice-NNN-<name>/milestone.json`: set `stage: "design"`, `next_action: "/design-slice"`.
  Schema by example: `examples/milestone.json` (from the slice skill's bundled examples).
- Auto-advance to `/design-slice` via Skill tool.

**Any assumption FAILED (NO-GO):**
- Candidate is now `status: blocked`. DO NOT advance to `/design-slice`.
- Present `AskUserQuestion`: "Assumption <A-id> failed. What fallback should we try? Options: (a) propose an alternative approach and re-spike, (b) deprioritize this candidate and pick a different one via /slice."
- On fallback selected: record `fallback` in candidates.json, await re-spike or `/slice`.

## Step 6b — record gate outcome (measurement spine)

One row per slice into `<vault>/gate-log.json` (roadmap Theme 8 / plan Phase 0). `risk-spike` is a
**high** reality-contact gate (throwaway code on the real environment) — `gate_log.py` stamps that:

```bash
# verdict: go = all proven · no-go = any FAILED · conditional = any CONDITIONAL with none failed
# findings-count: number of assumptions that came back NO-GO (FAILED)
# --cross-domain (Phase 2.3): set ONLY when re-spiking a cross-domain-transfer invariant — i.e. design.json
# already exists and carries a cross_domain_transfer (absent on the normal step-0 spike, before any design).
SLICE_DIR="$AI_SDLC_VAULT_ROOT/slices/<slice-NNN-name>"
CD=""; [ -f "$SLICE_DIR/design.json" ] && $PY -c "import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get('cross_domain_transfer') else 1)" "$SLICE_DIR/design.json" 2>/dev/null && CD="--cross-domain"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate risk-spike --slice <slice-NNN-name> \
    --verdict <go|no-go|conditional> --findings-count <N failed> $CD \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

## Critical rules

- **NEVER fabricate spike results.** If you can't run it, say so and stop.
- **NEVER skip multi-device validation** for sync/sharing/collaboration features.
- **NEVER soften a NO-GO.** Redesign is cheaper than wasted slices.
- **NEVER reuse spike code in production.** It's throwaway. Mark it clearly.
- **OAuth / cross-account scopes**: test with TWO different accounts on the actual scope. Most failures appear only in the second account.
- **Payment gateways**: use real sandbox, not mocked.
- **Push notifications**: test actual delivery, not mock dispatch.
- **Reality spine (Phase 1.3).** This is a HIGH reality-contact gate — it proves assumptions with throwaway code
  on the real environment, so it can say a hard *no*. It is **mandatory at every tier** (it never gets lightened
  for low-tier slices), and no model-on-model gate (`/critique`, `/code-review`) may override a NO-GO here.
  Reality > code-graph > model-critic.

## Pipeline position

- predecessor: `/slice` (auto-invoked after candidate claim) · successor: `/design-slice`
- auto-advance: YES — all assumptions proven → Skill `/design-slice` immediately
- user-input gates:
  - No spiking candidate in context → `AskUserQuestion` for target (Step 1)
  - Any NO-GO → `AskUserQuestion` for fallback decision (Step 6)
  - Environment unavailable → halt and tell the user what's needed (Step 3)
