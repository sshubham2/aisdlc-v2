---
name: heavy-architect
description: "Heavy-mode-only skill that produces the comprehensive upfront architecture vault. Writes only the human-authored irreducible files (threat-model.json, cost-estimation.json, requirements.json, non-functional.json, diagrams.json, actors/) rather than derived component/contract/schema files (left to /sync). For brownfield, seeds the component inventory from code-review-graph code-graph analysis. Trigger phrases: '/heavy-architect', 'create the architecture upfront', 'compliance architecture', 'regulated project architecture'."
when_to_use: "Run AFTER /discover and BEFORE /user-test and /slice (HIGH risks remain OPEN — they are retired in-loop by /risk-spike, downstream inside /slice). Heavy mode ONLY — abort if triage.json shows Standard or Minimal. Do NOT use per-slice; that is /design-slice. Use for compliance, regulated, or audit-grade projects that require comprehensive contracts and threat models before implementation begins."
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# /heavy-architect — Comprehensive Upfront Architecture Vault

Produces the Heavy-mode irreducible architecture vault. Does NOT pre-generate `components/`, `contracts/`, or
`schemas/` — those are code-derived and owned by `/sync`. Produces only what genuinely cannot be derived from code.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git-common-dir `aisdlc/vault-root`
> config). Vault artifacts are JSON (not `.md`). `./CLAUDE.md` is the only markdown exception.

## Prerequisite state — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --file triage.json --fields mode,project_id 2>/dev/null || echo "UNRESOLVABLE"
```

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/risk_register_audit.py" --json --filter-band high 2>/dev/null || echo "(no risk register yet)"
```

Spike outcomes (retire evidence for assumptions proven by `/risk-spike`):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --glob "spikes/spike-*.json" --fields id,assumption,result,decision,adrs_raised 2>/dev/null || echo "(no spikes yet)"
```

**ABORT if** the first injection printed `UNRESOLVABLE` (no vault / no `triage.json` — run `/triage` first) or
`triage.json` `mode` is not `"heavy"` — suggest `/triage --re-triage`. Tell the user clearly. (The abort keys on
the clean sentinel / the mode value, never on raw tool error text.)

**Do NOT abort on open HIGH risks.** Open HIGH-band risks (lowercase `band: "high"` in the register) are
**expected** here: in v2, `/risk-spike` is an in-loop gate that runs **inside `/slice`, downstream of this
skill**, so HIGH risks cannot be retired yet — aborting on them would deadlock Heavy mode (the retiring step
lives after this one). Instead, treat each open HIGH risk from the injection above as an **architecture driver**:
it must be visibly addressed by the threat model (Step 4) and the component decomposition (Step 2), so the
downstream spikes have a frame to prove. List them to the user as the risks this architecture must de-risk.

Spike results loaded above inform architecture decisions: proven assumptions strengthen design choices; failed assumptions (with fallback decisions) must be reflected in the component decomposition and threat model.

## Step 0: Brownfield seed (skip for greenfield)

If `/adopt` was run (brownfield — codebase exists), seed the component inventory from the code graph BEFORE writing
any files. Query via `code-review-graph` MCP tools:

1. If `.code-review-graph/` is absent or stale: `"${CRG:-code-review-graph}" build --repo .` (or `update`) from
   the project root — NEVER the bare name: on the documented Windows venv/pinned-`$PY` setup the entry point is
   off PATH and a bare probe false-negatives as missing, silently degrading this seed on exactly the
   compliance-grade projects Heavy serves.
2. Use `code-review-graph` MCP tools to list top-level modules, identify god-nodes, and cluster by community.
3. Produce a draft component list: each god-node or high-fan-in module → one component candidate.
4. Carry this draft into Step 2 (decomposition) as seed — it adds WHAT (from graph); you add WHY (from concept).

**If `/adopt` (Heavy) already reverse-engineered the vault:** its `fidelity`-marked artifacts are the seed of
record — read them and build on them; do NOT re-derive a competing decomposition from the raw graph. Where your
Step-2 decomposition would disagree with adopt's inventory, name the deviation to the user in the Step-1 scope
gate instead of silently double-authoring.

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
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" --glob "decisions/ADR-*.json" --fields id,status,title,decision,consequences 2>/dev/null || echo "(ADR read failed — check the vault, do not decide against unknown constraints)"
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

For each actor identified in `concept.json` (or discovered in the code graph), write `<vault>/actors/<actor>.json`
(one file per actor). Schema by example: `examples/actor.json` (the bundled example is flat — aggregate.py cannot
nest `examples/actors/`).

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

Every component row carries a `"fidelity"` field: `"estimated"` (the default — these numbers are
model-estimated, not quotes) or `"grounded"` ONLY when a figure traces to a real bill / published price /
vendor quote (name the source in a sibling `"source"` field). The compliance trail must stay honest about
which numbers are guesses — same discipline as `/adopt`'s `fidelity` marks on reverse-engineered artifacts.

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

## Step 9: Lint the written artifacts, then completion summary

Before the summary, lint everything just written (catches enum/shape misses NOW instead of as CSP-1 findings
at the next `/sync`; same closing discipline as `/reflect` pre-archive):

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --dir "$VAULT" --skip-unknown
```

Non-zero → fix the named violations in the files THIS run wrote and re-lint before presenting the summary
(pre-existing violations in files this run did not touch: report them, don't fix them here).

After all files are written and lint is clean for this run's files, present:

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

- **Predecessor**: `/discover` — Heavy mode only (HIGH risks remain OPEN; retired in-loop by `/risk-spike` downstream of this skill)
- **Successor**: `/user-test` (if B2C) OR `/slice` (otherwise)
- **Auto-advance**: NO — user confirms next step after the completion summary
- **User-input gates**: Step 1 scope confirmation (mandatory before any writes)
