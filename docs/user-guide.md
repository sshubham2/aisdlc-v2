# ai-sdlc User Guide

A practical walkthrough of running the `ai-sdlc` pipeline on a project, from first install through a complete
per-slice loop and ongoing maintenance. Read the README first for philosophy, requirements, and configuration.

> All slash commands are in the `ai-sdlc` namespace. You can invoke `/triage` as either `/triage` (shorthand
> once the plugin is active in the conversation) or the fully-qualified `/ai-sdlc:triage`.

---

## 1. Install and set up

### 1a. Install the plugin

```bash
/plugin marketplace add sshubham2/aisdlc-v2
/plugin install ai-sdlc@ai-sdlc
```

For a local dev checkout:

```bash
claude --plugin-dir /path/to/aisdlc-v2
```

### 1b. Run the dependency doctor

Before anything else, run the one-time setup in the project directory you want to pipeline:

```
/ai-sdlc:setup
```

This installs PyYAML and `code-review-graph` (with visible pip progress), registers the CRG MCP server
in a gitignored `.mcp.json`, and builds the initial code graph. When it finishes:

1. **Restart Claude Code** — the MCP server only becomes available on the next launch.
2. **Approve the one-time trust prompt** for `code-review-graph` when Claude Code restarts.
3. Run `/mcp` to confirm the server is connected.

Optional flags:
- `--no-mcp` — skip MCP server registration
- `--no-graph` — skip the initial graph build
- `[repo-path]` — target a directory other than the current one

If a skill later reports `CRG_MISSING` or a broken interpreter, re-run `/ai-sdlc:setup` — it is idempotent.

---

## 2. Open a project (choose your entry point)

You have three entry paths depending on whether you are starting fresh, adopting an existing codebase, or
jumping straight to brownfield forensics.

### 2a. Greenfield — `/triage`

Use this when you are starting from scratch (or fewer than ~500 LOC of existing code).

```
/triage
/triage MINIMAL <one-sentence description>
/triage STANDARD <one-sentence description>
/triage HEAVY <one-sentence description>
```

`/triage` asks five one-at-a-time questions (what you're building, audience, biggest unknown, compliance,
team size), picks a **pipeline mode** (Minimal / Standard / Heavy), builds the initial risk register,
scaffolds the vault skeleton (`triage.json`, `risk-register.json`, `decisions/`, `spikes/`, `slices/`),
and writes a `./CLAUDE.md` in the project root. It hands off to `/discover` (or `/heavy-architect`
for Heavy mode).

**Mode quick-reference:**

| Mode | For |
|------|-----|
| Minimal | Solo dev, MVP, exploration, one-off scripts |
| Standard | B2C, small teams, product work — the default |
| Heavy | Compliance, enterprise, regulated, public APIs |

Mode sets the default risk tier for new slices (Minimal → `low`, Standard/Heavy → `medium`) and Heavy's
forced-Critic floor. It does **not** set the per-slice review cost — that is the risk tier you choose in
`/slice`.

### 2b. Brownfield — `/adopt`

Use this when code already exists. `/adopt` replaces `/triage` — do not run both.

```
/adopt
/adopt MINIMAL
/adopt STANDARD
/adopt HEAVY
```

`/adopt` scans the codebase with `code-review-graph`, offers to run `/diagnose` for forensic analysis,
conducts a structured interview (one question at a time), picks a mode, builds the risk register,
and reverse-engineers `concept.json` from code reality. After adoption, proceed directly to `/slice`
(or to `/discover` if the concept still needs sharpening).

### 2c. Brownfield forensics — `/diagnose` → `/slice-candidates`

For deeper brownfield analysis, run the forensic pipeline:

```
/diagnose
/diagnose /path/to/repo
/diagnose /path/to/repo --parallel
```

`/diagnose` runs 11 structured analysis passes and assembles a self-contained interactive
`diagnose-out/diagnosis.html`. Open it in a browser, annotate each finding (Confirmed / No + notes),
save the annotated file, and return it. Then:

```
/slice-candidates
/slice-candidates /path/to/diagnose-out
/slice-candidates --obo
```

`/slice-candidates` reads the annotated HTML, detects file-overlap coupling via CRG blast-radius queries,
topo-sorts findings into a DAG-ordered backlog, and appends candidates to `<vault>/candidates.json`.
Use `--obo` to walk through findings one at a time interactively instead of batch-processing.

For whole-codebase defect-finding (security + correctness sweep), use:

```
/bug-hunt
/bug-hunt /path/to/repo
/bug-hunt /path/to/repo --report
/bug-hunt /path/to/repo --top N
/bug-hunt /path/to/repo --since <git-ref>
```

`--report` renders an owner-facing HTML; `--top N` caps the risk-ranked work-list; `--since <ref>` scopes
to code changed since a git ref.

> **Note — `/bug-hunt` is `v0.1` (build-wired, not yet battle-tested).** It is registered and runnable, but
> less proven than the rest of the pipeline; treat its findings as leads to verify, not verdicts.

---

## 3. Discovery — `/discover`

After `/triage` (or after `/adopt` if concept still needs sharpening), run discovery:

```
/discover
```

`/discover` conducts a structured one-topic-at-a-time conversation covering:
1. **WHAT** — concept, scope, explicit non-goals
2. **WHO** — actors with their top 2–3 actions, needs, and boundaries
3. **CONSTRAINTS** — stack/infra/team, each tech decision reversibility-tagged (`cheap | expensive | irreversible`)
4. **HIGH-risk items** — how each high-band risk maps to a slice candidate

At the end it names a first slice candidate, confirms via `AskUserQuestion`, and writes `concept.json`,
updates `risk-register.json`, and appends the first entry to `candidates.json`.

For Standard B2C projects with UX uncertainty, `/discover` will recommend running `/user-test` before
moving to the slice loop.

**Optional:** `/user-test mockup|prototype|slice` — real-user validation gate. Prepares the artifact,
generates observation questions, and appends new risks to `risk-register.json` from what behavior revealed.

**Heavy mode only:** run `/heavy-architect` after `/discover` and before `/slice` to produce the
comprehensive upfront architecture vault (threat model, cost estimation, requirements, non-functional
constraints, diagrams).

---

## 4. The per-slice loop

Each slice is one thin vertical cut — no more than ~1 day of AI work — that retires a risk or ships
user-visible value. The full loop:

```
/slice → /risk-spike → /design-slice → [/risk-spike --mode design] →
/critique [+ /critique-review] → /slice-story → /build-slice →
/code-review → /validate-slice → /reflect → /commit-slice
```

### 4a. Pick a candidate — `/slice`

```
/slice
/slice "<description or hint>"
```

`/slice` reads the pre-ranked `candidates.json` backlog, presents a ranked recommendation with a top pick
and 2–4 alternatives (coupling with in-flight slices is surfaced), and waits for your pick (or takes #1
if you say "you pick"). It then defines the slice (name, risk tier, acceptance criteria ≤5, verification
plan, must-not-defer items, out-of-scope), writes `mission-brief.json` + `milestone.json`, claims the
candidate with a worktree + branch, and auto-advances to `/risk-spike`.

**Risk tier (the per-slice cost lever):**
- `low` — pure CSS/copy/docs/test-only or a genuinely small bug-fix / small feature
- `medium` — a normal change (Standard/Heavy default)
- `high` — novel domain, first integration, irreversible, needs extra scrutiny

For bug-fix candidates (source type `bug-hunt-finding` or name matching `fix-*`), `/slice` requires a
failing repro test via `/repro` before proceeding.

### 4b. Feasibility spike — `/risk-spike` (default mode)

```
/risk-spike
/risk-spike SC-NNN
/risk-spike R-NN
/risk-spike all
```

Step-0 of the slice loop. **No design happens until every blocking assumption is proven.** `/risk-spike`
reads the picked candidate's `assumptions[]` where `blocking: true`, spawns a `field-recon` subagent,
and proves each with throwaway code on the real environment. Verdicts:

- All **GO** → advances to `/design-slice`
- Any **NO-GO** → candidate is blocked until a fallback is re-spiked
- **conditional** → advances with `spike_constraints[]` recorded as first-class data

If there are zero unproven blocking assumptions, the spike is recorded as skipped and the loop advances
automatically.

### 4c. Design tournament — `/design-slice`

```
/design-slice
```

Runs on **every** slice regardless of tier: spawns all 3 **blind** designer subagents (practice, cross-domain,
expert), then reality-grounds a single synthesis using CRG blast-radius, spike evidence, reversibility,
and a simplest-that-works heuristic (ADR-018 — generation breadth is always maximal; tier still drives whether
`/critique` runs, not the tournament size). Writes `design.json`.

Empirically-decidable tournament disagreements gate a post-synthesis design spike:

### 4d. Design spike — `/risk-spike --mode design`

```
/risk-spike --mode design
```

Post-synthesis, conditional. Reads `design.json`'s `decidable_disagreements` and `must-verify cross-domain
invariants` and lets reality adjudicate. GO → proceeds to `/critique`; NO-GO → back to `/design-slice`
to re-synthesize.

### 4e. Adversarial design review — `/critique` [+ `/critique-review`]

```
/critique
/critique --force
```

Tier-driven (NOT mode-gated). Runs on `medium`/`high` slices or when `critic_required: true`; skipped on
`low`-tier slices with no mandatory trigger. Spawns a forked `critique` subagent with 9 fixed attack
dimensions, writes findings to `critique.json`, then returns to the main thread for the interactive
**TRI-1 user triage gate** (you classify each finding: accept, defer, reject with rationale).

For `high`-tier slices or when first-Critic findings are ≥ 5, the **meta-Critic** runs:

```
/critique-review
```

`/critique-review` spawns a second adversarial subagent that reviews the first Critic's `critique.json`
for false positives, false negatives, and severity miscalibrations. Its findings feed back into TRI-1.

### 4f. Plain-language report — `/slice-story`

```
/slice-story
/slice-story slice-NNN
```

Auto-invoked after `/critique` when the review surfaced ≥ 1 finding to narrate; also user-invokable any
time. A forked `slice-story` narrator subagent translates every pipeline artifact into plain English for a
mixed technical / non-technical audience (no pipeline jargon on the page), then `render_story.py` assembles
a standalone `story.html`. The file is **delivered straight to you via `SendUserFile`** — it reaches your
phone over Remote Control. You then approve to proceed to `/build-slice`.

### 4g. Build — `/build-slice`

```
/build-slice
```

Plan-mode execution: the Builder explores code with CRG queries and targeted Reads, drafts a task sequence,
and **halts for your explicit approval** before writing any code. Execution proceeds task-by-task with
per-task verification, a mandatory mid-slice smoke gate, and a multi-audit pre-finish gate (including
`/drift-check --fast`). Writes `build-log.json` and updates `milestone.json`.

### 4h. Code review — `/code-review`

```
/code-review
```

Runs in a forked `code-review` agent context. Reviews the just-built diff along 9 fixed dimensions
(identical to the design Critic's dimensions), producing `path:line` findings as `code-review.json`.
Blocker findings must be dispositioned before `/validate-slice` proceeds.

### 4i. Reality validation — `/validate-slice`

```
/validate-slice
```

Runs forked. Executes per-criterion PASS/FAIL/PARTIAL checks on the **real environment** (real device, real
user, real data — not just tests passing). Runs VAL-1/WS-1/ETC-1 layered audits and the shippability
catalog regression check. Auto-advances to `/reflect` only on aggregate Result: PASS.

### 4j. Reflect — `/reflect`

```
/reflect
```

Captures what the slice taught you across four categories: validated, corrected, discovered, and deferred.
Writes `reflection.json`, appends to `lessons-learned.json` and `shippability.json`, tracks Critic
calibration per TRI-1, and auto-archives the slice.

### 4k. Commit — `/commit-slice`

```
/commit-slice
/commit-slice --merge
/commit-slice --push
/commit-slice --sync-after-pr
```

User-invoked only — never auto-advanced into. Generates an audit-grade conventional commit message from
vault artifacts (mission-brief, build-log, validation, reflection, critique, ADRs, shippability). Modes:

- `--merge` — solo-dev local merge + safe branch delete
- `--push` — push slice branch + display PR hint
- `--sync-after-pr` — post-PR local cleanup
- No flag — generate and show the commit message only

If the repo carries the `aisdlc-merge-gate.yml` CI workflow, `/commit-slice` also emits
`.aisdlc/receipts/<slice-NNN>.json` into the commit as a ship receipt.

`/commit-slice` also auto-emits the refreshed `slice-story` into the archived slice folder on ship
(best-effort, non-blocking).

---

## 5. Orientation commands

### `/pulse` — macro state at a glance

```
/pulse
/pulse --brief
/pulse --full
```

Read-only scan of the vault. Reports: active slice stage + next action, risk exposure, regression health,
Critic calibration status, per-gate hit-rate from the measurement spine, top lessons, candidate backlog
snapshot, and recommended next action.

- Default (~60 lines) — balanced summary
- `--brief` (~20 lines) — quick orientation
- `--full` (~150 lines) — complete picture

Run at session start, after time away, before major decisions, or when handing off.

### `/query-design` — grounded codebase Q&A

```
/query-design
/query-design "how does X work in this repo?"
```

Read-only, grounded Q&A conversation about the existing codebase. Every answer cites specific
file/line/symbol evidence — no recall, no guessing. Never writes files. If the conversation surfaces
a concrete actionable finding, offers (but never forces) a handoff to `/slice`.

---

## 6. Maintenance

### `/drift-check` — vault vs code sync audit

```
/drift-check
/drift-check --fast
/drift-check --status
/drift-check --resolve
/drift-check [path]
```

Audits vault claims against code reality. Four finding categories: DRIFT (blocker), UNSPECIFIED CODE
(major), STALE CLAIM (major), STALE DOC (major — a `/product-doc`-generated doc that no longer matches
the code surface it documented).

- `--fast` — scope to changed files since last commit; target <2s (used by `/build-slice` pre-finish gate)
- `--status` — read-only current-state view: fold the append-only `drift-log.json` into accepted/open/resolved state; no detection, no vault writes
- `--resolve` — interactive walk-through of existing findings
- `[path]` — scope to one component/contract folder

Run before starting a new slice, after external changes, or any time the vault feels out of sync.

### `/reduce` — complexity budget enforcer

```
/reduce
/reduce --force
```

Audits vault and codebase for over-engineering, dead claims, speculative generality, god-nodes, and
cross-slice incoherence. Produces a ranked reduction-candidate report. If you confirm, proposes a
simplification slice. Run when `/reflect` auto-suggests it (component-count threshold), when
`drift-log` accumulates >5 unresolved entries, or periodically in Heavy mode (~every 5 slices).

### `/archive` — slice archival and index maintenance

```
/archive
/archive --index-only
```

Archives completed slices (those with `reflection.json`) from `slices/` to `slices/archive/` and
regenerates both `_index.json` files. In normal flow `/reflect` auto-archives; use `/archive` for batch
cleanup, after interrupted `/reflect` runs, after manual slice moves, or after a fresh clone
(`--index-only` to rebuild indexes without moving files).

### `/supersede-slice` — mark a shipped slice obsolete

```
/supersede-slice <archived-slice-id>
```

Establishes a formal bidirectional supersession link between an archived slice whose design has been
contradicted by reality and the active slice that fixes it. Appends a supersession block to the archived
`reflection.json` and sets the `Supersedes` field in the active `mission-brief.json`. Use when an
archived slice's claims would otherwise stand as live assertions while a new active slice corrects them.

### `/critic-calibrate` — close the Critic feedback loop

```
/critic-calibrate
/critic-calibrate --window N
```

Mines "Missed by Critic" entries from the last N archived reflections (default: 15), classifies
blind-spot patterns, and produces 0–3 evidence-backed Critic check proposals for your review. Accepted
proposals are persisted to `critic-calibration-log.json` in the vault; `/critique` reads them before
every review. Never edits the plugin's shipped agent prompt — a project overlay is used instead,
preserving upgradability. Run every 10–20 slices, after repeated Critic misses, or after a serious
post-ship bug.

### `/product-doc` — grounded documentation

```
/product-doc
/product-doc --docs readme,changelog,api,guide
```

Generates and maintains README / CHANGELOG / API-reference / user-guide grounded in code reality
(CRG public surface + vault). Default: all four. Pass `--docs <comma-list>` to produce only a subset
(`readme`, `changelog`, `api`, `guide`). CHANGELOG is assembled deterministically from per-slice
`changelog.json` records. Writes docs to the code repo and a `doc-manifest.json` provenance record
to the vault so `/drift-check` can flag docs that drift from code.

### `/sync` (Heavy mode only) — bidirectional vault-code reconciliation

```
/sync
/sync --dry-run
/sync --regen-only
/sync --check-only
/sync [path]
```

Heavy mode only — Standard/Minimal users use `/drift-check` instead. Regenerates code-derived vault
files (components, contracts, schemas from AST/OpenAPI/type defs), detects drift in human-authored
vault files, runs cross-spec parity audit, and appends a dated sync record. Run every 5–10 slices,
after major refactors, or before a release.

---

## 7. Bug fixes

For non-trivial bugs, always establish a failing repro test before defining a fix slice:

```
/repro <issue description>
```

`/repro` parses the issue, queries CRG for context in the buggy code area, writes a runnable failing
test under `tests/bugs/`, confirms it actually fails, and appends a shippability row so the bug can
never silently return. Then run `/slice "fix <issue>"` — the AC already includes "the failing repro
test passes at slice end".

---

## 8. Vault admin

The vault is plain JSON outside your repo. Key commands for vault lifecycle:

```bash
# Identify which vault is active and which resolution tier won
python3 /path/to/aisdlc-v2/scripts/lib/_vault_paths.py

# List all vaults on this machine + orphan status
$PY scripts/lib/vault_admin.py list

# Export this project's vault to a tarball
$PY scripts/lib/vault_admin.py export

# Import a vault on another machine
$PY scripts/lib/vault_admin.py import <archive>.tgz

# Pin the vault to this repo (tier-2 git config pin)
$PY scripts/lib/vault_admin.py write-pin

# Delete an orphaned vault
$PY scripts/lib/vault_admin.py uninstall <name> --yes
```

The `export` / `import` / `write-pin` flow is the recommended way to hand off the design record to a
new teammate or restore it on a new machine.

---

## 9. Typical first session (greenfield)

```
# Install once
/plugin marketplace add sshubham2/aisdlc-v2
/plugin install ai-sdlc@ai-sdlc

# In the project directory — one-time setup
/ai-sdlc:setup
# <restart Claude Code, approve MCP trust prompt>

# Open the project
/triage STANDARD "A task management API for small teams"

# Discovery
/discover

# Slice loop — repeat until done
/slice
# (risk-spike auto-runs)
# (design-slice auto-runs)
# (risk-spike --mode design, if tournament disagreements)
/critique
# (slice-story auto-runs if findings)
/build-slice
# (code-review auto-runs)
/validate-slice
/reflect
/commit-slice --push
```

## 10. Typical first session (brownfield)

```
# In the existing project directory
/ai-sdlc:setup
# <restart>

/adopt STANDARD
# /adopt offers /diagnose — accept for a thorough adoption
# Annotate diagnose-out/diagnosis.html, save, return
# /slice-candidates runs automatically

# Slice loop as above
/slice
...
```
