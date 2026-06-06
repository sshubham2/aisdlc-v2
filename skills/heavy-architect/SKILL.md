---
name: heavy-architect
description: "Heavy-mode-only skill that produces the comprehensive upfront architecture vault. Writes only the human-authored irreducible files (threat-model.json, cost-estimation.json, requirements.json, non-functional.json, diagrams.json, actors/) rather than derived component/contract/schema files (left to /sync). For brownfield, seeds the component inventory from code-review-graph code-graph analysis. Trigger phrases: '/heavy-architect', 'create the architecture upfront', 'compliance architecture', 'regulated project architecture'."
when_to_use: "Run AFTER /discover (all HIGH risks retired) and BEFORE /user-test and /slice. Heavy mode ONLY — abort if triage.json shows Standard or Minimal. Do NOT use per-slice; that is /design-slice. Use for compliance, regulated, or audit-grade projects that require comprehensive contracts and threat models before implementation begins."
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# /heavy-architect — Comprehensive Upfront Architecture Vault

Produces the Heavy-mode irreducible architecture vault. Does NOT pre-generate `components/`, `contracts/`, or
`schemas/` — those are code-derived and owned by `/sync`. Produces only what genuinely cannot be derived from code.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git-common-dir `aisdlc/vault-root`
> config). Vault artifacts are JSON (not `.md`). `./CLAUDE.md` is the only markdown exception.

## Prerequisite state — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --vault "$AI_SDLC_VAULT_ROOT" --file triage.json --fields mode,project_id
```

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/risk_register_audit.py" "$AI_SDLC_VAULT_ROOT/risk-register.json" --json --filter-band high
```

Spike outcomes (retire evidence for assumptions proven by `/risk-spike`):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --vault "$AI_SDLC_VAULT_ROOT" --glob "spikes/spike-*.json" --fields id,assumption,result,decision,adrs_raised
```

**ABORT if** `triage.json` `mode` is not `"heavy"` — suggest `/triage --re-triage`. Tell the user clearly.

**ABORT if** any risk in `risk-register.json` has `band: "HIGH"` and `status != "retired"` — list them and block.

Spike results loaded above inform architecture decisions: proven assumptions strengthen design choices; failed assumptions (with fallback decisions) must be reflected in the component decomposition and threat model.

## Step 0: Brownfield seed (skip for greenfield)

If `/adopt` was run (brownfield — codebase exists), seed the component inventory from the code graph BEFORE writing
any files. Query via `code-review-graph` MCP tools:

1. If `.code-review-graph/` is absent or stale: `code-review-graph build` (or `update`) from the project root.
2. Use `code-review-graph` MCP tools to list top-level modules, identify god-nodes, and cluster by community.
3. Produce a draft component list: each god-node or high-fan-in module → one component candidate.
4. Carry this draft into Step 2 (decomposition) as seed — it adds WHAT (from graph); you add WHY (from concept).

For greenfield: skip; generate from `concept.json` and actors in Step 2.

## Step 1: Scope confirmation (user-input gate)

Read `<vault>/concept.json` and `<vault>/actors/*.json`. Present a planned outline to the user:

> "I'll produce the Heavy-mode comprehensive vault. Planned output:
> - Actors: <N> actor files (from concept.json and any discovered above)
> - Threat model: STRIDE per component — threat-model.json
> - Cost estimation: per-component infra at 1K/10K/100K users — cost-estimation.json
> - Requirements: functional by actor — requirements.json
> - Non-functionals + compliance mapping — non-functional.json
> - System overview + sequence diagrams — diagrams.json
>
> DO NOT include: components/, contracts/, schemas/ (code-derived → /sync generates on-demand).
> Confirm to proceed, or scope down."

**HALT** here with `AskUserQuestion`. Do not write any files until confirmed.

## Pre-Step 2: Existing architectural decisions — injected

Read all existing ADRs before decomposing components; they constrain what you may and may not decide:

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --vault "$AI_SDLC_VAULT_ROOT" --glob "decisions/ADR-*.json" --fields id,status,title,decision,consequences
```

If the vault has no ADRs yet, this injection returns an empty list — proceed normally.

## Step 2: Component decomposition (internal — no files)

From `concept.json`, actors, the brownfield seed (Step 0 if applicable), and the ADRs loaded above, identify components. For each:

- **Single responsibility** — one sentence. If you can't, decompose further.
- **Public surface** — what other components / external clients see.
- **Internal state** — what it owns (data, side effects).
- **Dependencies** — other components, external services, third-party integrations.
- **Failure modes** — blast radius, cascade risk.

Default to modular monolith unless scale/team/ops clearly warrant distribution. Document the choice in an ADR if
the decision is non-obvious.

## Step 3: Actor files

For each actor identified in `concept.json` (or discovered in the code graph), write `<vault>/actors/<actor>.json`.
Schema by example: `examples/actors/example-actor.json`.

## Step 4: Threat model (STRIDE per component)

For each component, walk all six STRIDE categories. For each finding, document mitigation, status, and refs.

Write `<vault>/threat-model.json`. Schema by example: `examples/threat-model.json`.

STRIDE checklist per component:
- **S**poofing — impersonation of this component or its users
- **T**ampering — data in transit / at rest
- **R**epudiation — actions deniable without audit trail
- **I**nformation disclosure — data leaks on compromise
- **D**enial of service — what triggers DOS, blast radius
- **E**levation of privilege — low-privilege actor escalation paths

DO NOT skip this step — in Heavy mode, threat model is a compliance requirement.

## Step 5: Cost estimation

For each component, estimate infrastructure costs at three scales: 1K / 10K / 100K users. Categories:
compute, storage (DB + object + CDN), network (egress, inter-AZ), third-party services (auth, analytics,
monitoring). Include total monthly + per-user cost per scale.

Write `<vault>/cost-estimation.json`. Schema by example: `examples/cost-estimation.json`.

## Step 6: Requirements

Write `<vault>/requirements.json` — functional requirements by actor. Schema by example: `examples/requirements.json`.

Each item: `{ "id": "REQ-N", "actor": "<slug>", "statement": "<declarative>", "status": "planned",
"implementation_ref": null, "verification_ref": null }`.

## Step 7: Non-functional requirements + compliance mapping

Write `<vault>/non-functional.json` — NFRs and compliance constraints. Schema by example: `examples/non-functional.json`.

Each item: `{ "id": "NFR-N", "kind": "latency|uptime|throughput|security|compliance|...",
"target": "<measurable target>", "status": "planned|met|violated", "verification_ref": null,
"compliance": ["HIPAA"|"PCI"|"SOC2"|"GDPR"|...] }`.

Include at minimum: latency targets (p95), uptime SLA, data retention policy, and all relevant compliance frameworks
identified in `concept.json` / `triage.json`.

## Step 8: Diagrams

Write `<vault>/diagrams.json` — Mermaid diagram strings as JSON fields (not rendered files).
Schema by example: `examples/diagrams.json`.

## Step 9: Completion summary

After all files are written, present:

- Files written (list with paths)
- Component count, actor count, REQ-N range, NFR-N range, threat count
- ADR refs that informed decisions (from `decisions/ADR-*.json`)
- Next step: `/user-test mockup` (if B2C) OR `/slice` to start the build loop

## Critical rules

- VERIFY Heavy mode first (`triage.json` `mode == "heavy"`). Abort otherwise.
- CONFIRM scope (Step 1) before writing any files. Heavy users sometimes want a subset.
- DECOMPOSE: every component must have a one-sentence responsibility. Can't write it → split the component.
- DO NOT create `components/`, `contracts/`, `schemas/` — those are code-derived, owned by `/sync`.
- Use `code-review-graph` (CRG) for all brownfield code-graph queries; vault queries read JSON directly.
- DO NOT use `[[wikilinks]]` — cross-references are JSON `id` fields per schema conventions.
- Threat model and cost estimation are mandatory in Heavy mode — not optional.
- All vault writes are JSON, not markdown. Schema tags (`_schema`) on every file.
- Single-skill scripts: `$PY "${CLAUDE_SKILL_DIR}/scripts/<x>.py"`. Shared lib: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<x>.py"` (skill commands run in the user's CWD and cannot use `python -m`/`${CLAUDE_PLUGIN_ROOT}`).
- SVW-1: shared append-mutated files route through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"`. These outputs are
  `raw-write` (first create), so direct `Write` is correct here — no append-safety concern.

## Pipeline position

- **Predecessor**: `/discover` (all HIGH risks retired) — Heavy mode only
- **Successor**: `/user-test` (if B2C) OR `/slice` (otherwise)
- **Auto-advance**: NO — user confirms next step after the completion summary
- **User-input gates**: Step 1 scope confirmation (mandatory before any writes)
