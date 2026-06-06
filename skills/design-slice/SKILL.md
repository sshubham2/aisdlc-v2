---
name: design-slice
description: "Produces a just-enough per-slice design (design.json). Queries code-review-graph for blast-radius and reachability, runs the project-frame synthesizer, identifies only what is new for this slice, asks <=4 clarifying questions when genuinely ambiguous, and tags every new ADR with reversibility. In Heavy mode also updates threat-model.json and cost-estimation.json. Completes by updating milestone.json and auto-advancing to /critique."
when_to_use: "Trigger after /risk-spike passes, before /critique. Phrases: '/design-slice', 'design this slice', 'spec the current slice', 'design the slice'. Reads <vault>/slices/slice-NNN/mission-brief.json. Per-slice only — for upfront Heavy-mode vault use /heavy-architect."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Skill
---

# /design-slice — just-enough spec for one cut

Design ONLY what THIS slice needs to ship. Not full architecture. Output feeds `/critique`.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git config `aisdlc/vault-root`).

## Live state — injected

Active slice mission brief:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice_brief.py" --vault "$AI_SDLC_VAULT_ROOT"
```

Relevant past reflections (keyword-matched via vault JSON):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/reflection_lookup.py" --vault "$AI_SDLC_VAULT_ROOT" --from-mission-brief
```

## Step 0 — graph context (before designing)

Query code-review-graph for blast-radius and reachability of the modules this slice will touch.
Use the CRG MCP tools (`impact-radius`, `review-context`, `search`) or the CLI:

```bash
code-review-graph search "<module-or-concept>"
code-review-graph impact-radius --node "<file-or-module>"
```

If `.code-review-graph/` is missing or stale: `code-review-graph build` (or `update`).

For conceptual matches not found by CRG keyword search, fall back to:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/grep_vault.py" --vault "$AI_SDLC_VAULT_ROOT" --pattern "<concept>" --dir slices/archive
```

## Step 0.5 — project-frame synthesis (PFS-1)

Before writing any design, run the ephemeral project-frame so the design is direction-aware:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$AI_SDLC_VAULT_ROOT/slices/<active-slice>"
```

Emits ≤40 lines: **Identity** / **Trajectory** / **Impact**. First line is an adversarial ATTACK-LENS preamble —
read it as a lens for where this slice fights the project's direction. **Advisory only, never a gate**: if the
tool fails, proceed with `(project-frame unavailable)` and design normally.

At this point `design.json` does not exist yet; the Impact section will be mission-brief-only (expected; ignore the stderr WARN).

Use Trajectory to sanity-check design choices against the deliberate project direction. Carry tension into decisions.

## Step 1 — identify what's new for this slice

Compare mission-brief.json to existing vault + code-graph output. List only what this slice introduces:

- New components (files to create or modify)
- New contracts (endpoints, events, schemas)
- New data model fields / entities
- New decisions that lock with this slice (ADRs)

**Do not list things this slice doesn't touch.** The vault grows with the system, not ahead of it.

## Step 2 — clarifying questions (≤4, only on real ambiguity)

If the mission brief is genuinely ambiguous on a design-level question, ask via `AskUserQuestion`.
HALT auto-advance until answered.

**Legitimate questions** (design forks, irreversible choices):
- "Should receipts be stored as DB blobs or in object storage?"
- "Is this entity editable after creation, or append-only?"
- "Who can read this — owner only, or household members?"

**Bad questions** (implementation, bikeshed):
- "How should we handle errors?" (Builder's job)
- "What testing framework?" (not a slice-level decision)

If the brief is clear, skip this step entirely.

## Step 3 — tag every new ADR with reversibility

For each decision this slice locks, create `<vault>/decisions/ADR-NNN.json`
(schema: `examples/adr.json`). Key fields: `id`, `title`, `status: "accepted"`,
`reversibility` (cheap|expensive|irreversible), `supersedes` / `superseded_by` (null if not applicable),
`slice`, `date`, `context` (markdown string: why now), `decision` (markdown string: chosen option),
`consequences` (markdown string: downstream effects, affected components).

**SVW-1 note**: ADR files are new-file creates (`write_semantics: create`) — raw-write is correct here.
SVW-1's vault_edit requirement governs in-place append/mutation of shared files, not first-create writes.
Do NOT overwrite an existing ADR — always write a NEW file (supersede via `supersedes` field).

Reversibility tags:
- **cheap** — 1-hour change: UI tokens, log format, library swap
- **expensive** — framework, DB engine, contract shape with multiple consumers: lock only if THIS slice needs it
- **irreversible** — identity model, tenant model, primary entity shape: lock only after /risk-spike confirmed

ADRs are append-only — supersede with a new ADR, never edit an existing one.

## Step 4 — write design.json

Create `<vault>/slices/slice-NNN-<name>/design.json` (schema: `examples/design.json`).

Key fields to populate:
- `whats_new` / `whats_reused` — what this slice adds vs. what it relies on (reference file paths, not prose)
- `components_touched` — array of component names; `components_detail` — one entry per component: `name`, `responsibility`, `lives_at`, `key_interactions`
- `contracts` — new endpoints/events: `name`, `kind` (rest|sse|event|grpc), `auth_model`, `error_cases`, `notes` (code ref)
- `data_model_deltas` — new entities/fields: `entity`, `defined_in`, `whats_new`, `validation` (constraint ref)
- `wiring_matrix` — see WIRE-1 below
- `adrs` — list of ADR ids locked by this slice
- `auth_model`, `error_model` — authorization approach and new error codes introduced
- `at` — ISO-8601 timestamp

**Thin vault discipline** — reference code locations, do not duplicate them. Request/response schemas live in code
(`ReceiptUploadRequest` in `src/api/receipts.py`); reference the file. Do not enumerate every method on a class.

**Wiring matrix (WIRE-1)**: every new module must declare a consumer entry point AND a consumer test, or carry an
explicit `exemption` with substring `"rationale:"` + reason. The build-slice audit treats null-exemption + empty
consumer fields as a failure.

## Step 5 — Heavy mode extras

Standard / Minimal mode: skip this step entirely.

Heavy mode only:
- Update `<vault>/threat-model.json` (schema: `examples/threat-model.json`) if this slice changes the attack surface.
- Update `<vault>/cost-estimation.json` if this slice changes the infrastructure footprint.
- Update `<vault>/components/<name>.json` and `<vault>/contracts/<name>.json` for substantively changed
  components/contracts (schema: `examples/component.json`, `examples/contract.json`).

Do NOT produce component/contract files in Standard or Minimal mode. Code is the source of truth; the slice's
`design.json` references code locations and that is enough.

## Step 6 — update milestone.json

Update `<vault>/slices/slice-NNN-<name>/milestone.json` (schema: `examples/milestone.json`):

- `stage: "design"`
- progress step `"design"` → `done: true`
- `next_action: "/critique"` (or `"/build-slice"` if `critic_required: false`)
- `current_focus`: brief summary of what the design introduces
- `on_resume`: `"design-slice complete; next step /critique"` when `critic_required: true`;
  `"design-slice complete; next step /build-slice"` when `critic_required: false`
- `updated_by: "design-slice"`

If the slice's scope expanded during design to touch a mandatory Critic trigger (auth, contracts, data model,
multi-device/sync, external integration, methodology surface `skills/**`/`agents/**`/`scripts/**`): set
`critic_required: true` in the milestone and tell the user: "Scope expanded to touch X; Critic is now mandatory."

## Step 7 — confirm and auto-advance

Report to the user:
```
Design complete — slice NNN. Wrote: design.json, ADR-NNN (count), milestone.json updated (stage: design).
```

Then invoke `/critique` via the `Skill` tool — **do not wait for the user** unless Step 2 clarifying questions
were asked (those already halted; answers are already in context now).

If `critic_required: false`, invoke `/critique` anyway — it self-skips per its own mode/tier gate and advances onward.

## Critical rules

- DO NOT spec components this slice doesn't touch.
- DO NOT define contracts for endpoints this slice doesn't add.
- DO NOT write test specs — ACs in mission-brief.json are enough.
- DO NOT lock decisions this slice doesn't need — defer them.
- DO NOT duplicate code in design.json — REFERENCE code locations.
- DO NOT create component/contract/schema files in Standard or Minimal mode.
- ASK clarifying questions only about real design-fork ambiguity. Hard cap: ≤4 questions.
- TAG every new ADR with reversibility. No untagged decisions.
- ADRs are append-only. To change a decision, write a new ADR with `supersedes`.

## Anti-patterns

- Speculative interfaces ("this might be reused later")
- Pre-defined contracts for "phase 2" features
- ADRs for trivial choices (naming conventions, log levels)
- Component files written in Standard mode and never used

## Pipeline position

- predecessor: `/risk-spike` · successor: `/critique` · auto-advance: true
- on-clean-completion: write `design.json` + ADRs + `milestone.json`, then invoke `/critique` via Skill tool.
- user-input gates (halt auto-advance): Step 2 clarifying questions when real design ambiguity exists (≤4).
  No plan-mode here — plan mode belongs to `/build-slice`.
