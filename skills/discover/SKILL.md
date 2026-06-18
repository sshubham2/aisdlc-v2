---
name: discover
description: "Discovery step of the AI SDLC pipeline. Interactive concept + users + tech-constraints exploration in one unified loop, one topic at a time: WHAT (concept, scope, non-goals), WHO (actors with top actions, needs, boundaries), CONSTRAINTS (stack/infra/team, reversibility-tagged). Addresses HIGH-risk items, optionally records external references as JSON fields, and names the first slice candidate. Writes mode-scoped outputs: concept.json (all modes); tech-decision ADRs (Standard+Heavy); requirements.json + per-actor files (Heavy)."
when_to_use: "Trigger phrases: /discover, 'discover the project', 'explore concept and users', 'AI SDLC discovery'. Runs after /triage. Do NOT confuse with generic research — this is the pipeline step that gates entry to the per-slice loop."
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# /discover — Concept + Users + Constraints

Discovery step of the AI SDLC pipeline. Output: enough understanding of WHAT, WHO, and CONSTRAINTS to name the first slice candidate and populate `candidates.json`.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir `aisdlc/vault-root` config).

## Live vault state — injected

Triage context (mode, audience, existing risks):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --vault "$AI_SDLC_VAULT_ROOT" triage.json 2>/dev/null || echo '{"_missing":true}'
```

HIGH-risk items for Step 4:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --vault "$AI_SDLC_VAULT_ROOT" risk-register.json 2>/dev/null || echo '{"risks":[]}'
```

## Prerequisite check

If the injected `triage.json` has `_missing: true`: stop — "Run `/triage` first to set the project mode and risk register."

Extract: `mode` (Minimal / Standard / Heavy), audience, and any existing risks. The injected `risk-register.json` provides HIGH-risk items for Step 4.

Acknowledge triage context briefly at the start. Do NOT re-ask what triage already established.

## Step 1 — WHAT (concept)

Ask (one question, then stop and engage):

> "What's the core thing this app does for users? What's deliberately out of scope?"

Engage with the answer:
- Push back on scope bloat
- Surface obvious edge cases
- Confirm explicit non-goals as a list

## Step 2 — WHO (actors)

Ask (one question, then stop and engage):

> "Who are the primary users? Are there secondary roles (admin, support, integrator)?"

For each actor capture: top 2–3 actions, what they need from the system, boundaries (what they CANNOT do).

In **Heavy mode**: do a deeper walkthrough per actor — first-time use, heavy load, error case, waiting, collaboration, audit/history.

## Step 3 — CONSTRAINTS (tech + infra + team)

Ask (one question, then stop and engage):

> "Tech stack constraints? Existing infra? Team experience? Deployment target?"

Engage honestly:
- If the user picks something that doesn't fit: push back with reasoning
- If open: suggest options with trade-offs
- Tag each tech decision with reversibility: `cheap | expensive | irreversible`

## Step 4 — Address HIGH-risk items

For each HIGH-risk item from the injected `risk-register.json` (band == "high"):

> "Is this risk addressed by an assumption on the first slice candidate, or does it need its own candidate first?"

- Either way: the risk stays **open** in the register. Do NOT resolve or close it here.
- Note which candidate(s) will retire each HIGH risk when the per-slice loop begins.
- `/risk-spike` is an **in-loop gate** inside `/slice` — it is not a pre-pipeline step. Discover never hands off to it.

Do NOT remove risks from the register here — only annotate `notes`.

## Step 4.5 — External references

Ask once (do not push if the user skips):

> "Any external references that shape this project's design? (docs, specs, API references, research papers.) I'll record them as reference fields in concept.json."

For each reference provided, record it as a `references[]` entry in `concept.json` — plain JSON fields, no multimodal ingest.

```bash
# External references are JSON fields only — no code-graph ingest:
# concept.json -> "references": [{"label": "Stripe docs", "url": "https://..."}]
```

Skip entirely if no references supplied.

## Step 5 — Name the first slice candidate

Close with a proposal (do NOT skip this — the conversation must inform it):

> "Based on this, the first slice should be **\<verb-object name\>**, which retires risks **\<R-NN, …\>** and produces **\<user-visible outcome\>**."

Good first slices: exercise the riskiest external dependency; produce something a real user can touch; cover <20% of final scope.

Anti-patterns: "set up the database", "build the login page" (unless auth IS the risk), "implement basic CRUD".

This is an **AskUserQuestion gate**: confirm the candidate name before writing.

## Step 6 — Write outputs

Write all outputs using the schemas shown in `examples/`. All artifacts are JSON.

### All modes — `<vault>/concept.json`

Schema by example: `examples/concept.json` (write_semantics: create, raw-write). Key fields: `_schema`, `mode`, `what`, `non_goals[]`, `actors[]` (name/role/top_actions/needs/cannot), `constraints` (stack[]/infra/team), `references[]`, `first_slice_candidate`.

### All modes — update `<vault>/risk-register.json`

Schema by example: `examples/risk-register.json`. Route through `vault_edit` (SVW-1) — **never raw whole-file overwrite**:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" \
    --file risk-register.json --array risks --json '[<new-risk-objects>]'
```

Add any new discovery-phase risks found during the conversation (band = high/med/low as appropriate). This is a shared append-mutated file; `vault_edit` append is mandatory to prevent the write-race `raw-write` would reintroduce.

### All modes — append to `<vault>/candidates.json`

Schema by example: `examples/slice-candidates.json`. Materialize the named first slice candidate:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" \
    --file candidates.json --array candidates --json '<new-candidate-object>'
```

Candidate `id`: OMIT it — the allocator mints `SC-NNN` in-lock (`vault_edit append` on `candidates.json`/`candidates` rejects a caller-supplied id and fills it). `status: "candidate"`, `progress: "not-started"`, `source: [{"type":"risk","ref":"<R-NN>"}]`, `history: [{"event":"created","by":"discover","at":"<ts>"}]`.

### Standard + Heavy — `<vault>/decisions/ADR-NNN.json`

Schema by example: `examples/adr.json`. One ADR per non-trivial tech decision. ADRs are **append-only** — never edit in place; supersede with a new ADR. Mint the ADR number IN-LOCK — `ADR=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --vault "$AI_SDLC_VAULT_ROOT" --file candidates.json --kind adr)` (prints `ADR-NNN`, bumps `counters.adr`) — and name the file `<vault>/decisions/$ADR.json`. Never hand-pick the number. Reference each ADR from `concept.json` `constraints.stack[].adr`.

### Heavy mode only — `<vault>/requirements.json`

Schema by example: `examples/requirements.json`. Functional + non-functional requirements by actor.

### Heavy mode only — `<vault>/actors/<actor>.json`

One file per actor. Includes role-play walkthrough fields: `first_time_use`, `heavy_load`, `error_case`, `waiting`, `collaboration`, `audit_history` (markdown-valued strings).

## Critical rules

- ONE topic at a time. Stop after each area. Engage with answers; do not rush to the next area.
- Do NOT invent actors, scope, or constraints. Ask.
- Do NOT skip Area 1 (WHAT) even if "obvious from /triage" — concept depth matters.
- Do NOT propose a slice candidate until Step 5. The conversation must inform it.
- Standard B2C: recommend `/user-test` after `/discover` and before `/slice` (UX uncertainty → validate mockup first).
- External references = JSON fields in `concept.json` only; no code-graph ingest.
- Candidate backlog is `<vault>/candidates.json` (NOT `backlog.md` / `slice-queue.md`).
- risk-register is the RISK LEDGER — add risks, do not move candidates there.
- SVW-1: shared append-mutated files (`risk-register.json`, `candidates.json`) route through `vault_edit`, never raw whole-file overwrite.

## Pipeline position

- **Predecessor**: `/triage` (or `/adopt` for brownfield onboarding)
- **Successor**: `/user-test` (Standard B2C with UX uncertainty) OR `/slice` (otherwise)
- **Auto-advance**: NO — this skill ends with an explicit hand-off prompt; the user chooses the next step
- **User-input gates**: each of Steps 1–3 (one-topic-at-a-time conversation); Step 5 candidate confirmation (AskUserQuestion)
- `hands_off_to`: `user-test`, `slice`
