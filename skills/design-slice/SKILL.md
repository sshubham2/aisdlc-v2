---
name: design-slice
description: "Produces a just-enough per-slice design (design.json) via a DESIGN TOURNAMENT that runs on EVERY slice regardless of risk_tier: it spawns all 3 BLIND designer subagents (practice, cross-domain, expert), then reality-grounds a single synthesis (CRG-fit / spike-ability / reversibility / simplest-that-works) with a composition-coherence pass. Queries code-review-graph for blast-radius, runs the project-frame synthesizer, tags every new ADR with reversibility. Empirically-decidable tournament disagreements + must-verify cross-domain invariants gate a post-synthesis /risk-spike --mode design before /critique."
when_to_use: "Trigger after /risk-spike (feasibility) passes, before /critique. Phrases: '/design-slice', 'design this slice', 'spec the current slice', 'design the slice'. Reads <vault>/slices/slice-NNN/mission-brief.json. Per-slice only — for upfront Heavy-mode vault use /heavy-architect."
argument-hint: "[slice-id]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion, Skill
---

# /design-slice — just-enough spec for one cut

Design ONLY what THIS slice needs to ship. Not full architecture. Output feeds `/critique`.

**Generate diversely · select against reality · review independently.** The brilliant design is a *generation*
event, sampled once today and lost. So design-slice runs a **tournament** on EVERY slice: all 3 *blind*
designers generate independently (the ceiling), then a sighted synthesis selects against reality (the floor).

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git config `aisdlc/vault-root`).

## Resolve the active slice (run this FIRST)

Run the `bash` block below **first** and read the printed brief — it resolves the active slice in a BODY
step that BINDS an explicit `/design-slice slice-NNN` `$ARG`. A `!`-injection runs at skill-LOAD *before*
`${ARGUMENTS}` binds, so it CANNOT resolve a named slice (SC-064 / ADR-022). Use the resolved slice's
**folder** for the `<active-slice>` placeholder in Step 0.5 and the Step-1 reads.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice_brief.py" --vault "$VAULT" --slice "$ARG" --repo-root .; else $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice_brief.py" --vault "$VAULT" --repo-root .; fi   # exit 0 for absent/AMBIGUOUS (they degrade to a VISIBLE note, never a load-abort — ADR-022). EXIT 5 = OWNERSHIP REFUSED (slice-069): this block is a BODY block, so it CAN and DOES fail closed there.
```

> **OWNERSHIP REFUSED (exit 5) — STOP (slice-069 / ADR-072).** This command is a **bare invocation**: its
> output is read by YOU, not captured into a shell variable, so there is no `$var` for a shell guard to
> test. That makes the prose the guard. If the block prints `OWNERSHIP REFUSED` (or exits 5), the slice
> you were about to design belongs to **another git identity** — and `/design-slice` **writes**
> `design.json` and new ADRs into that slice's folder. **STOP. Do not reconstruct the path from the slice
> id and write anyway. Do not set `AI_SDLC_ALLOW_FOREIGN_SLICE` yourself.** Report the named owner to the
> user and let them decide. (This is a collision guard against an honest mistake, not a permission
> system — which is exactly why quietly routing around it defeats its entire purpose.)

## Prior-lesson recall — resolve in a BODY step (feeds the shared designer context)

Graded prior-lesson recall (`reflection_lookup.py`) — the past slices + reflections most relevant to THIS
mission, surfaced by the default `tfidf-cosine` scorer. **Run this as a BODY step, NOT a `!`-injection** (same
`${ARGUMENTS}`-binding rule as the block above): it passes `--slice` when an explicit slice arg was given,
degrading to `--from-mission-brief` (branch/cwd resolution) only when none was. Capture its stdout as the
"Nearest prior slice + relevant reflections" block in the Step-1 designer context.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"; if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/reflection_lookup.py" --vault "$VAULT" --slice "$ARG" --scorer tfidf-cosine; else $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/reflection_lookup.py" --vault "$VAULT" --from-mission-brief --scorer tfidf-cosine; fi
```

## Step 0 — graph context (before designing)

Query code-review-graph for blast-radius and reachability of the modules this slice will touch, via the CRG
**MCP tools** (you are in a live-MCP context here): `mcp__code-review-graph__semantic_search_nodes_tool` for
`<module-or-concept>`, and `mcp__code-review-graph__get_impact_radius_tool` for the impacted set of
`<file-or-module>`. (CRG 2.3.x has no `search`/`impact-radius` CLI verb — those capabilities are MCP-only.)

If `.code-review-graph/` is missing or stale: `"${CRG:-code-review-graph}" build` (or `update`). If BOTH CRG
and the `grep_vault.py` fallback below are unavailable/fail, proceed with the advisory note
`(blast-radius context unavailable)` — the designers work without prior-art context; never a gate.
**Record the degradation, don't just tolerate it**: carry it to Step 5 — set
`tournament.crg_context: "unavailable"` in `design.json` (omit the field when context was available)
AND add `--note "crg-context:unavailable"` to the Step-5 design-tournament gate-log row, so `/pulse`
can surface "N recent slices designed without CRG context" instead of the degradation staying silent.

For conceptual matches not found by CRG keyword search, fall back to:
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: AI_SDLC_VAULT_ROOT is NOT exported -- resolve per block (vars don't persist across bash blocks)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/grep_vault.py" --vault "$VAULT" --pattern "<concept>" --dir slices/archive
```

**Keep a short blast-radius summary** — it becomes part of the shared context every designer gets.

## Step 0.5 — project-frame synthesis (PFS-1)

Before designing, run the ephemeral project-frame so the design is direction-aware:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --slice-dir "$VAULT/slices/<active-slice>"
```

Emits ≤40 lines: **Identity** / **Trajectory** / **Impact**. First line is an adversarial ATTACK-LENS preamble —
read it as a lens for where this slice fights the project's direction. **Advisory only, never a gate**: if the
tool fails, proceed with `(project-frame unavailable)` and design normally.

At this point `design.json` does not exist yet; the Impact section will be mission-brief-only — expected, and reported
as a plain `Design: mission-brief-only` line, not as a degrade. A `WARN: project-frame degraded` on stderr means a
REQUIRED vault source is genuinely missing: read it, do not wave it through.

Capture stdout — it is shared designer context too.

## Step 1 — the design tournament (always 3 blind designers)

Read `risk_tier` from the mission-brief resolved by the body step above — you still record it in `tournament.tier`, and it still drives
the downstream `/critique` + `/critique-review` gates and the Step-8 design-spike triggers. **But the tournament
runs on EVERY slice regardless of risk_tier — generation breadth is always maximal: spawn all 3 blind designers
(`designer-practice` + `designer-crossdomain` + `designer-expert`) feeding the Step-2 reality-grounded synthesis.**
There is no single-flight short-circuit and no tier-scaled designer count — tier no longer sizes the tournament
(ADR-018). The cost guard is the Step-2 synthesis + `/reduce` (a forced cross-domain analogy is discarded at
selection via `transfer_found: false`, never adopted), and the retained `approach_divergence` measurement (Step 2
item 5) keeps the always-3 cost honest.

### The tournament — spawn the 3 BLIND designers

Spawn all three designers **in a single message (parallel)** via the **Agent tool**
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
<the body-resolved reflection_lookup output (from the Prior-lesson recall BODY step above)>

# CRG blast-radius / reachability
<your Step 0 summary>
```

**Await the real agents — never fabricate a designer's output.** Each returns one `aisdlc/design-proposal@1`
JSON object. If a designer errors or returns null, synthesize from those that returned; if **all** fail, design
inline from the mission-brief + the Step-0 context and note that all designers failed.

**Persist the raw proposals BEFORE synthesizing (tournament path only).** Raw-Write
`<vault>/slices/slice-NNN-<name>/design-proposals.json`:
```json
{ "_schema": "aisdlc/design-proposals@1", "slice": "slice-NNN", "at": "<ts>",
  "proposals": [ <each designer's returned design-proposal object, verbatim> ] }
```
The tournament is the most expensive generation step in the loop, and `design.json` keeps only one-line
summaries — without this file, a design-spike **NO-GO re-synthesis** (Step 8) after a compaction/restart has
nothing to re-synthesize from. Per-slice artifact, single writer → raw-write is correct.

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
     → add to `tournament.decidable_disagreements` with `verdict: "pending"` (each entry is
     `{question, verdict: "pending", spike: "pending"}` — the Step-8 design spike overwrites both, filling the
     go/no-go `verdict` and the `spike: spike-<name>` ref) **only when the losing answer would
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
   premise, and it is **advisory only — a monitor, not a controller (ADR-018):** keep MEASURING it into
   `tournament.approach_divergence` and the design-tournament gate-log row every slice, but it **never auto-drops a
   designer** — generation is always 3. If a project's `designer-practice ~ designer-expert` pair comes back
   `identical`/`overlapping` on **most slices**, that is surfaced for **human review** of the always-3 policy via
   `/pulse --full`; it does not change the spawn set on its own. `/pulse --full` surfaces the cross-slice aggregate
   from the design-tournament gate-log row (Step 5).

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

For each decision this slice locks: first MINT the number IN-LOCK — `VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"; ADR=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --vault "$VAULT" --file candidates.json --kind adr)` (prints `ADR-NNN`, bumps `counters.adr`; NEVER hand-pick the number) — then create `<vault>/decisions/$ADR.json` (schema: `examples/adr.json`) with `id` = `$ADR`. Key
fields: `id`, `title`, `status: "accepted"`, `reversibility` (cheap|expensive|irreversible),
`supersedes`/`superseded_by` (null if n/a), `slice`, `date`, `context`, `decision`, `consequences`.

**SVW-1 note**: ADR files are new-file creates (`write_semantics: create`) — raw-write is correct. Do NOT
overwrite an existing ADR — always write a NEW file (supersede via `supersedes`).

**ADR-append seal (ADR-023 / SC-019):** immediately after writing the new `$ADR.json`, baseline it (SCOPED to
that id) so the append-only gate carries its content fingerprint, then VERIFY the decisions set is clean:
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # adr_append_only_audit EXITS 2 on an empty --vault (no computed fallback) -- resolve $VAULT here or the VERIFY below false-STOPs on every mint (4.6.1)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/adr_append_only_audit.py" --vault "$VAULT" --seal "$ADR"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/adr_append_only_audit.py" --vault "$VAULT"
```
`--seal "$ADR"` is SCOPED to the just-minted id ONLY -- never blanket (a blanket re-seal on every mint would
re-open the trust window and launder an unrelated unsealed edit). **Treat ANY non-zero VERIFY here as STOP** and
surface it, do NOT seal over it: **exit 1** = a PRIOR ADR was edited in place out-of-band; **exit 4** = a prior
sealed ADR was DELETED from disk out-of-band (ADR-049). With precedence 2>4>1>3>0 a co-occurring deletion+edit
returns the scalar 4, so inspect `--json result['tampered']` too before acting -- a masked tamper is still listed
there. (A project that already had ADRs *before* adopting this gate runs `adr_append_only_audit.py --vault
"$VAULT" --backfill` ONCE to baseline them — resolve `$VAULT` as above.)

Reversibility tags:
- **cheap** — 1-hour change: UI tokens, log format, library swap
- **expensive** — framework, DB engine, contract shape with multiple consumers: lock only if THIS slice needs it
- **irreversible** — identity model, tenant model, primary entity shape: lock only after a spike confirmed it

ADRs are append-only — supersede with a new ADR, never edit one in place. This is **enforced** by the
`adr-append-only` gate (`scripts/lib/adr_append_only_audit.py`, ADR-023): the scoped `--seal` above records the
new ADR's fingerprint, and the `/build-slice` pre-finish gate (ADR-APPEND-1) fails if any sealed ADR's immutable
content later changes.

## Step 5 — write design.json

Create `<vault>/slices/slice-NNN-<name>/design.json` (schema: `examples/design.json`).

Key fields:
- `whats_new` / `whats_reused` — what this slice adds vs relies on (reference file paths, not prose)
- `components_touched` + `components_detail` (`name`, `responsibility`, `lives_at`, `key_interactions`)
- `contracts` (`name`, `kind`, `auth_model`, `error_cases`, `notes`) · `data_model_deltas` · `wiring_matrix` (WIRE-1)
- `adrs` — ADR ids locked by this slice · `auth_model` · `error_model`
- `assumptions_proven` — **only when the claimed candidate has spiked assumptions**: a PURE PASS-THROUGH of the
  candidate's assumptions where `spike_status == "proven"`, one row per assumption —
  `{assumption: <id>, statement, spike_ref, verdict: <spike_verdict>, constraints: <spike_constraints>}`
  (field-for-field from the candidate row, no renames, no other source; the spike FILE under `<vault>/spikes/`
  stays the full-evidence authority). A `no-go` assumption never passes through; a CONDITIONAL verdict's
  constraints are design inputs (ADR-002) — surface them where the design reads. **Legacy rows without
  `spike_verdict`**: omit `verdict`/`constraints` — absent = unknown, never default-fill. **Omit the whole
  block** when no assumption is proven (absent = "no spiked assumptions", never an error). `artifact_lint` is
  the shape AUTHORITY (verdict enum + the conditional⇒non-empty-constraints co-constraint) — don't re-derive
  the co-constraints here or in downstream prose.
- `cross_domain_transfer` — **only if the selected design imports a cross-domain pattern** (from
  `designer-crossdomain`): `source_domain`, `pattern`, `rationale`, `invariants[]` (each
  `{precondition, status: holds|must-verify|fails, evidence}`). Omit when no transfer was selected. The
  `must-verify` invariants are what the design spike and `/critique` check.
- `tournament` — **present on every slice** (the 3-designer tournament always runs): `tier`, `designers[]`,
  `proposals[]` (`designer`, `approach`, `selected: core|partial|none`), `channeled_experts[]`,
  `selection_rationale`, `coherence_check`, `decidable_disagreements[]`, `taste_disagreements[]`,
  `approach_divergence[]` (3.3 — per designer-pair `{pair, divergence: identical|overlapping|disjoint}`),
  `crg_context: "unavailable"` **only when Step 0 degraded** (both CRG and the grep_vault fallback failed;
  omit when blast-radius context was available — absent = available).
- `at` — ISO-8601 timestamp

**Gate-log the divergence (every slice — 3.3).** After writing design.json, append one *informational*
`design-tournament` gate-log row so "diverse at generation" is measurable across slices (the tournament runs on
every slice, so this row is always written):

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per block
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate design-tournament --slice slice-NNN-<name> \
    --verdict <most-divergent pair: identical|overlapping|disjoint> --findings-count 0 \
    --approach-divergence "practice~crossdomain:<d>; practice~expert:<d>; crossdomain~expert:<d>" \
    --mode <minimal|standard|heavy> --tier <low|medium|high> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$VAULT" --file gate-log.json --array entries --stdin
# Add --note "crg-context:unavailable" (before the pipe) when Step 0 degraded — the row is
# how /pulse sees that the tournament ran without blast-radius context.
```

This row raises no findings (it is informational, not a verdict/finding gate — `/pulse` excludes it from the
quiet/lighten math); `/pulse --full` reads it to surface the project's expert-lens cost over time.

**Wiring matrix (WIRE-1)**: every new module declares a consumer entry point AND a consumer test, or carries an
explicit `exemption` with substring `"rationale:"`. build-slice treats null-exemption + empty consumer fields as a failure.

## Step 6 — Heavy mode extras

Standard / Minimal: skip. Heavy only:
- Update `<vault>/threat-model.json` if this slice changes the attack surface (schema:
  `schemas/artifact-examples.json` → `"threat-model"`).
- Update `<vault>/cost-estimation.json` if it changes the infrastructure footprint (schema:
  `schemas/artifact-examples.json` → `"cost-estimation"`).
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
- `next_action`: `"/risk-spike --mode design"` if Step 8 fires; else `"/critique"` — ALWAYS `/critique`, even when
  `critic_required: false` (Step 9 routes through `/critique`, whose skip path writes the `done: "skipped"` progress
  marker `/build-slice`'s prerequisite requires; pointing `next_action` straight at `/build-slice` strands a resume)
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
  specific composition failed). Re-run Step 2 with the failed branch excluded, sourcing the full proposals from
  `<slice>/design-proposals.json` (written in Step 1 — do NOT rely on conversation memory; after a
  compaction/restart that file is the only complete record). Do NOT re-spawn the designers.
  **Bound the loop to ≤2 re-synthesis rounds:** each round drops the failed branch, so it converges fast; if a
  2nd design spike still returns NO-GO, HALT and surface to the user — a persistent NO-GO is a feasibility
  problem, not a composition one (reconsider the candidate / discuss a fallback), not another silent re-synthesis.

**Step 8 is condition-gated, never tier-gated:** a slice with no pending decidable disagreement and no
`must-verify` invariant has nothing for reality to adjudicate — it skips Step 8 and goes straight to Step 9.

## Step 9 — confirm and auto-advance

Report:
```
Design complete — slice NNN. Tournament: 3 designers. Wrote: design.json,
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
- Running the post-synthesis design spike (Step 8) when nothing is pending — it gates on decidable disagreements / must-verify invariants, never on tier.
- Speculative interfaces / pre-defined "phase 2" contracts / ADRs for trivial choices.

## Pipeline position

- predecessor: `/risk-spike` (feasibility / step-0) · auto-advance: true
- successor: `/risk-spike --mode design` (Step 8, conditional) → then `/critique`; OR `/critique` directly when
  Step 8 doesn't fire; OR `/build-slice` if `critic_required: false`.
- on-clean-completion: write `design.json` (+`tournament`/`cross_domain_transfer` when applicable) + ADRs +
  `milestone.json`, then invoke the design spike or `/critique` via the Skill tool.
- user-input gates (halt auto-advance): Step 3 clarifying questions when a real, undecidable design fork exists (≤4).
  No plan-mode here — plan mode belongs to `/build-slice`.
