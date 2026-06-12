---
name: design-slice
description: "Produces a just-enough per-slice design (design.json) via a tier-gated DESIGN TOURNAMENT: on medium/high/novel slices it spawns 2-3 BLIND designer subagents (practice, cross-domain, expert), then reality-grounds a single synthesis (CRG-fit / spike-ability / reversibility / simplest-that-works) with a composition-coherence pass; low/mechanical slices use a single inline flight (zero added cost). Queries code-review-graph for blast-radius, runs the project-frame synthesizer, tags every new ADR with reversibility. Empirically-decidable tournament disagreements + must-verify cross-domain invariants gate a post-synthesis /risk-spike --mode design before /critique."
when_to_use: "Trigger after /risk-spike (feasibility) passes, before /critique. Phrases: '/design-slice', 'design this slice', 'spec the current slice', 'design the slice'. Reads <vault>/slices/slice-NNN/mission-brief.json. Per-slice only — for upfront Heavy-mode vault use /heavy-architect."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion, Skill
---

# /design-slice — just-enough spec for one cut

Design ONLY what THIS slice needs to ship. Not full architecture. Output feeds `/critique`.

**Generate diversely · select against reality · review independently.** The brilliant design is a *generation*
event, sampled once today and lost. So on slices that warrant it, design-slice runs a **tournament**: 2–3 *blind*
designers generate independently (the ceiling), then a sighted synthesis selects against reality (the floor).

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git config `aisdlc/vault-root`).

## Live state — injected

Active slice mission brief:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice_brief.py" --vault "$AI_SDLC_VAULT_ROOT"
```

Nearest prior slice + relevant past reflections (lexical match via vault JSON — shared designer context):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/reflection_lookup.py" --vault "$AI_SDLC_VAULT_ROOT" --from-mission-brief
```

## Step 0 — graph context (before designing)

Query code-review-graph for blast-radius and reachability of the modules this slice will touch.
Use the CRG MCP tools (`impact-radius`, `review-context`, `search`) or the CLI:

```bash
"${CRG:-code-review-graph}" search "<module-or-concept>"
"${CRG:-code-review-graph}" impact-radius --node "<file-or-module>"
```

If `.code-review-graph/` is missing or stale: `"${CRG:-code-review-graph}" build` (or `update`).

For conceptual matches not found by CRG keyword search, fall back to:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/grep_vault.py" --vault "$AI_SDLC_VAULT_ROOT" --pattern "<concept>" --dir slices/archive
```

**Keep a short blast-radius summary** — it becomes part of the shared context every designer gets.

## Step 0.5 — project-frame synthesis (PFS-1)

Before designing, run the ephemeral project-frame so the design is direction-aware:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$VAULT/slices/<active-slice>"
```

Emits ≤40 lines: **Identity** / **Trajectory** / **Impact**. First line is an adversarial ATTACK-LENS preamble —
read it as a lens for where this slice fights the project's direction. **Advisory only, never a gate**: if the
tool fails, proceed with `(project-frame unavailable)` and design normally.

At this point `design.json` does not exist yet; the Impact section will be mission-brief-only (expected; ignore the stderr WARN).

Capture stdout — it is shared designer context too.

## Step 1 — the design tournament (tier-gated)

Read `risk_tier` from the injected mission-brief. Pick the tournament size — **the tournament is insurance; do
not buy platinum on a $5 slice:**

| Tier | Designers | Path |
|------|-----------|------|
| **low / mechanical** (typo, config, rename, obvious CRUD) | **1 (inline)** | **Single flight** — use the "Single flight" path just below, then skip Step 2 (synthesis) → Step 3. No agents spawned, no design spike. **Zero added cost.** |
| **medium** | **2 blind**: `designer-practice` + `designer-crossdomain` | Tournament |
| **high / novel / irreversible** | **3 blind**: + `designer-expert` | Tournament + mandatory coherence pass + design spike **iff** the synthesis left a `pending` decidable disagreement or a `must-verify` invariant (Step 8) |

**Escalate within medium → full 3** when the slice is genuinely *novel* (no similar prior slice in the injected
reflections) or locks an *irreversible* ADR — those are exactly the slices where the expert lens earns its cost.

### Single flight (low / mechanical only — today's behavior)

Compare mission-brief.json to existing vault + code-graph output and design inline. List ONLY what this slice
introduces (components, contracts, data-model deltas, new ADRs). **Do not list things this slice doesn't touch.**
Skip the cross-domain hunt — forcing an analogy on a trivial cut is noise. Go straight to Step 3. No `tournament`
block is written; no design spike runs.

### Tournament (medium / high) — spawn BLIND designers

Spawn the tier-appropriate designers **in a single message (parallel)** via the **Agent tool**
(`subagent_type: "designer-practice"` / `"designer-crossdomain"` / `"designer-expert"`). The personas carry their
own epistemology, procedure, and output schema — **do NOT re-state them here.** Pass every designer the **same**
context block, and **nothing else** — they must not see each other's output (blind = the diversity you're paying for):

```
Slice: slice-NNN-<name>
Mode: <Minimal | Standard | Heavy>   Risk tier: <low | medium | high>

# mission-brief.json
<full JSON contents>

# project-frame (PFS-1)
<stdout of project_frame_synth, or "(project-frame unavailable)">

# Nearest prior slice + relevant reflections
<the injected reflection_lookup output>

# CRG blast-radius / reachability
<your Step 0 summary>
```

**Await the real agents — never fabricate a designer's output.** Each returns one `aisdlc/design-proposal@1`
JSON object. If a designer errors or returns null, synthesize from those that returned; if **all** fail, fall
back to the "Single flight" inline design above and note it.

## Step 2 — reality-grounded synthesis (tournament path; sighted)

You now hold 2–3 independent proposals. Compose **one** design — this is the hard 20%, not the fan-out.

1. **Select against reality, not taste.** For each contested component/decision, choose by **CRG-fit**
   (does it match how the real code is shaped? verify with `impact-radius`), **spike-ability** (can we prove it?),
   **reversibility** (cheap > expensive > irreversible — see Step 4), and **simplest-that-works**. Never pick the
   "most elegant" — taste regresses to the popular/over-engineered answer. Honor `designer-practice`'s
   `over_engineering_flag` and `designer-crossdomain`'s `transfer_found: false` as real signals.
2. **Composition / coherence pass (guard the Frankenstein architecture).** Selection ≠ composition. Verify the
   pieces you chose from different proposals do **not** assume *contradictory invariants* — consistency model
   (strong vs eventual), error-handling contract, concurrency/ownership model. If two chosen pieces conflict,
   keep one and redesign the seam; record it in `coherence_check`.
3. **Classify the disagreements.**
   - **Empirically decidable** (does the API support X? is it fast enough? does the integration actually work?)
     → add to `tournament.decidable_disagreements` with `verdict: "pending"` **only when the losing answer would
     force a re-synthesis** — a different component boundary, contract, or data model. A cheap question whose
     answer doesn't change the design (it just needs to hold) is NOT a decidable disagreement; let `/build-slice`'s
     smoke gate settle it. **Reality adjudicates the material ones** at the post-synthesis design spike (Step 8) —
     not you, not the Critic.
   - **Taste** (boundary placement, naming, layering) → add to `tournament.taste_disagreements`; these fall
     through to `/critique` + the user.
4. **Carry the provenance.** Take `designer-crossdomain`'s `cross_domain_transfer` block into `design.json` when
   its pattern is (partly) selected — its `must-verify` invariants are design-spike + `/critique` targets. Take
   `designer-expert`'s `channeled_experts` into `tournament.channeled_experts` (the `/critique` independence guard
   reads it so the Critic is a *different* expert — Phase 3.5).
5. **Measure the divergence (3.3 — measure "diverse at generation", don't just assert it).** For each designer
   **pair**, classify how different their proposals actually were: `identical` (same approach modulo wording),
   `overlapping` (shared core, differing details), or `disjoint` (materially different approaches). Record one
   entry per pair into `tournament.approach_divergence`. This is the empirical check on the tournament's whole
   premise — and the **decision rule** it feeds: if a project's `designer-practice ~ designer-expert` pair comes
   back `identical`/`overlapping` on **most high-tier slices**, the expert lens is converging on practice and not
   earning its spawn cost → **drop to 2 designers** (the medium-tier default) for this project. `/pulse --full`
   surfaces the cross-slice aggregate from the design-tournament gate-log row (Step 5).

The synthesized design is "what's new for this slice." **Thin-vault discipline**: reference code locations, don't
duplicate them; design ONLY what this slice ships. The vault grows with the system, not ahead of it.

## Step 3 — clarifying questions (≤4, only on real ambiguity)

If the design is genuinely ambiguous on a design-level question (a design fork the tournament couldn't settle and
the spike can't decide — i.e. a *taste* fork with product impact), ask via `AskUserQuestion`. HALT auto-advance
until answered.

**Legitimate** (design forks, irreversible choices): "Receipts as DB blobs or object storage?" · "Editable after
creation, or append-only?" · "Who can read this — owner only, or household?"
**Bad** (implementation, bikeshed): "How should we handle errors?" · "What testing framework?"

If the design is clear, skip this step.

## Step 4 — tag every new ADR with reversibility

For each decision this slice locks, create `<vault>/decisions/ADR-NNN.json` (schema: `examples/adr.json`). Key
fields: `id`, `title`, `status: "accepted"`, `reversibility` (cheap|expensive|irreversible),
`supersedes`/`superseded_by` (null if n/a), `slice`, `date`, `context`, `decision`, `consequences`.

**SVW-1 note**: ADR files are new-file creates (`write_semantics: create`) — raw-write is correct. Do NOT
overwrite an existing ADR — always write a NEW file (supersede via `supersedes`).

Reversibility tags:
- **cheap** — 1-hour change: UI tokens, log format, library swap
- **expensive** — framework, DB engine, contract shape with multiple consumers: lock only if THIS slice needs it
- **irreversible** — identity model, tenant model, primary entity shape: lock only after a spike confirmed it

ADRs are append-only — supersede with a new ADR, never edit one in place.

## Step 5 — write design.json

Create `<vault>/slices/slice-NNN-<name>/design.json` (schema: `examples/design.json`).

Key fields:
- `whats_new` / `whats_reused` — what this slice adds vs relies on (reference file paths, not prose)
- `components_touched` + `components_detail` (`name`, `responsibility`, `lives_at`, `key_interactions`)
- `contracts` (`name`, `kind`, `auth_model`, `error_cases`, `notes`) · `data_model_deltas` · `wiring_matrix` (WIRE-1)
- `adrs` — ADR ids locked by this slice · `auth_model` · `error_model`
- `cross_domain_transfer` — **only if the selected design imports a cross-domain pattern** (from
  `designer-crossdomain`): `source_domain`, `pattern`, `rationale`, `invariants[]` (each
  `{precondition, status: holds|must-verify|fails, evidence}`). Omit when no transfer was selected. The
  `must-verify` invariants are what the design spike and `/critique` check.
- `tournament` — **only on the tournament path** (omit on single-flight low-tier slices): `tier`, `designers[]`,
  `proposals[]` (`designer`, `approach`, `selected: core|partial|none`), `channeled_experts[]`,
  `selection_rationale`, `coherence_check`, `decidable_disagreements[]`, `taste_disagreements[]`,
  `approach_divergence[]` (3.3 — per designer-pair `{pair, divergence: identical|overlapping|disjoint}`).
- `at` — ISO-8601 timestamp

**Gate-log the divergence (tournament path only — 3.3).** After writing design.json, append one *informational*
`design-tournament` gate-log row so "diverse at generation" is measurable across slices (skip on single-flight
low-tier slices — no tournament ran):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate design-tournament --slice slice-NNN-<name> \
    --verdict <most-divergent pair: identical|overlapping|disjoint> --findings-count 0 \
    --approach-divergence "practice~crossdomain:<d>; practice~expert:<d>; crossdomain~expert:<d>" \
    --mode <minimal|standard|heavy> --tier <medium|high> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

This row raises no findings (it is informational, not a verdict/finding gate — `/pulse` excludes it from the
quiet/lighten math); `/pulse --full` reads it to surface the project's expert-lens cost over time.

**Wiring matrix (WIRE-1)**: every new module declares a consumer entry point AND a consumer test, or carries an
explicit `exemption` with substring `"rationale:"`. build-slice treats null-exemption + empty consumer fields as a failure.

## Step 6 — Heavy mode extras

Standard / Minimal: skip. Heavy only:
- Update `<vault>/threat-model.json` if this slice changes the attack surface.
- Update `<vault>/cost-estimation.json` if it changes the infrastructure footprint.
- Update `<vault>/components/<name>.json` + `<vault>/contracts/<name>.json` for substantively changed
  components/contracts (schema: `examples/component.json`, `examples/contract.json`).
- **Expert-lens vocabulary annotation (audit-tier toggle — roadmap Theme 7; strictly OFF the generation path).**
  The design was generated freely (the tournament) and selected against reality (Step 2) — that is **settled and
  is NOT revisited here**. For a regulated / audit-grade slice ONLY, additionally *describe* the chosen decisions in
  a recognized vocabulary so a human auditor can follow the reasoning in familiar terms: name the pattern ("this is
  the Outbox pattern", "optimistic concurrency", "a CQRS read-model"), and cite any standard/clause it satisfies, in
  a `design.json.vocabulary_annotation[]` block — `[{decision_ref, named_as, source, note}]`. This is **describe-
  after, never constrain-before**: it annotates what was already chosen, it does NOT change the choice, and it must
  **never** become a reason to reject a sound design for not matching a textbook label (that would re-introduce the
  authority-grounding the pipeline rejects — see CLAUDE.md philosophy). Generate freely → select against reality →
  *then* annotate for legibility. Omit entirely unless the slice is audit-tier; it is a Heavy toggle, not a default.

Do NOT produce component/contract files in Standard or Minimal mode — code is the source of truth.

## Step 7 — update milestone.json

Update `<vault>/slices/slice-NNN-<name>/milestone.json` (schema: `examples/milestone.json`):
- `stage: "design"`; progress step `"design"` → `done: true`
- `next_action`: `"/risk-spike --mode design"` if Step 8 fires; else `"/critique"`; else `"/build-slice"` if `critic_required: false`
- `current_focus`: what the design introduces (+ tournament tier if one ran)
- `on_resume`: matches `next_action`
- `updated_by: "design-slice"`

If scope expanded during design to touch a mandatory Critic trigger (auth, contracts, data model, multi-device/sync,
external integration, methodology surface `skills/**`/`agents/**`/`scripts/**`): set `critic_required: true` and tell
the user: "Scope expanded to touch X; Critic is now mandatory."

## Step 8 — design-spike gate (post-synthesis; conditional)

The tournament's empirically-decidable disagreements are settled by **reality**, not by the Critic. Run the
post-synthesis **design spike** when EITHER of these has something real to adjudicate (otherwise skip straight to
Step 9 → `/critique`):
- `tournament.decidable_disagreements` has any `verdict: "pending"`, OR
- the selected `cross_domain_transfer` has any invariant `status: "must-verify"`.

**Tier/irreversibility alone does NOT trigger a spike.** A high-tier or irreversible slice with nothing pending
has nothing for reality to decide — spiking it would be an empty round-trip (it would just write a skip note and
forward). The two target conditions above are the whole gate.

To run it, invoke **`/risk-spike --mode design`** via the Skill tool. It spikes the chosen composition on the real
environment, writes verdicts back into `design.json`'s `decidable_disagreements` + the `cross_domain_transfer`
invariants, logs a **high** reality-contact gate row, and then:
- **GO** (composition holds) → it advances to `/critique`.
- **NO-GO** (a decidable disagreement or invariant failed against reality) → it hands back to `/design-slice` to
  **re-synthesize** with the loser dropped (the *premise* already passed the step-0 feasibility spike — only this
  specific composition failed). Re-run Step 2 with the failed branch excluded.

**Single-flight low-tier slices never reach Step 8.**

## Step 9 — confirm and auto-advance

Report:
```
Design complete — slice NNN. Tournament: <none (single-flight) | 2 designers | 3 designers>. Wrote: design.json,
ADR-NNN (count), milestone.json (stage: design). Next: <design spike | /critique | /build-slice>.
```

Then advance — **do not wait for the user** unless Step 3 clarifying questions were asked (those already halted):
- Step 8 fires → invoke `/risk-spike --mode design` via Skill.
- Else → invoke `/critique` via Skill (it self-skips per its own tier gate when the slice is `low`-tier with
  `critic_required: false`, and advances onward).

## Critical rules

- DESIGN diversely on tournament slices; **never let designers see each other** (blind, or you lose the diversity).
- SELECT against reality (CRG-fit / spike-ability / reversibility / simplest-that-works), **never taste**.
- DO NOT skip the composition/coherence pass on the 3-designer path — selection ≠ composition.
- DO NOT spec components this slice doesn't touch; DO NOT define contracts for endpoints it doesn't add.
- DO NOT write test specs — ACs in mission-brief.json are enough.
- DO NOT duplicate code in design.json — REFERENCE code locations.
- DO NOT create component/contract/schema files in Standard or Minimal mode.
- TAG every new ADR with reversibility. ADRs are append-only (supersede, never edit).
- DECIDABLE disagreements go to the spike (reality), TASTE disagreements go to `/critique` (model + user).
- ASK clarifying questions only about real, undecidable design-fork ambiguity. Hard cap: ≤4.

## Anti-patterns

- Designers anchoring on each other (sequential or shared-context-leaking spawns) — that's not a tournament.
- Selecting the "most elegant" proposal — taste regresses to popular/over-engineered.
- Frankenstein composition: gluing pieces with contradictory consistency/error/concurrency models.
- Running the full 3-designer tournament + design spike on a typo (tier-gate exists for this).
- Speculative interfaces / pre-defined "phase 2" contracts / ADRs for trivial choices.

## Pipeline position

- predecessor: `/risk-spike` (feasibility / step-0) · auto-advance: true
- successor: `/risk-spike --mode design` (Step 8, conditional) → then `/critique`; OR `/critique` directly when
  Step 8 doesn't fire; OR `/build-slice` if `critic_required: false`.
- on-clean-completion: write `design.json` (+`tournament`/`cross_domain_transfer` when applicable) + ADRs +
  `milestone.json`, then invoke the design spike or `/critique` via the Skill tool.
- user-input gates (halt auto-advance): Step 3 clarifying questions when a real, undecidable design fork exists (≤4).
  No plan-mode here — plan mode belongs to `/build-slice`.
