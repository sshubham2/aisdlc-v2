---
name: pulse
description: "Scan the AI SDLC vault and produce a compact macro-state summary: active slice stage + next action, risk exposure, regression health, Critic calibration status, per-gate hit-rate (measurement spine), top lessons, candidate backlog snapshot, and recommended next action. Three modes: default (~60 lines), --brief (~20 lines), --full (~150 lines). Read-only — never modifies vault files."
when_to_use: "Trigger phrases: /pulse, 'where are we?', 'macro state', 'vault scan'. Use at session start, after time away, before major decisions, or when handing off. Replaces reading 5-6 vault files manually."
argument-hint: "[--brief | --full]"
context: fork
agent: general-purpose
allowed-tools: Read, Bash, Agent
---

# /pulse — Project Pulse / Macro State

You scan the AI SDLC vault and produce a compact structured summary.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Argument modes

- `/pulse` — balanced summary (~60 lines)
- `/pulse --brief` — one-screen (~20 lines) for quick orientation
- `/pulse --full` — comprehensive view (~150 lines); includes all deferred items, calibration history, full stranded-branch detail

## Prerequisite check

If `<vault>/` does not exist: project not opened yet — suggest `/triage` or `/adopt` and stop.

## Step 1 — Read vault state

**BRANCH-2 worktree detection (mandatory first):**

```!
git worktree list --porcelain 2>/dev/null
```

For each worktree on a `slice/NNN-<name>` branch (canonical path `<main-parent>/<main-name>-wt/slice-NNN-<name>`):
- Read that worktree's `<vault>/slices/slice-NNN-<name>/milestone.json` (or `slices/archive/` if archived).
- The worktree's milestone is authoritative for that slice during the BRANCH-2 window (pre-merge).
- Classify each: `IN_PROGRESS` | `BUILT_BUT_NOT_MERGED` | `MERGED` | `UNKNOWN`.

**Stranded-slice signal (R-26) — bare branches without worktrees:**

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json 2>/dev/null
```

Entries with `halt: true` (`klass` in `stranded-complete`, `orphaned`, `indeterminate`) → surface in **Drift & bypass** as `WARN: stranded: <branch> (<klass>)`. Informational entries (`in-progress`, `claimed-by-other`) are parallel-normal — do NOT warn. `branchless-in-flight` → one-line note only.

**Core vault state — injected at load time:**

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_snapshot.py" --vault "$AI_SDLC_VAULT_ROOT" \
    --files triage.json concept.json slices/_index.json candidates.json \
            shippability.json critic-calibration-log.json lessons-learned.json \
            drift-log.json 2>/dev/null
```

Active slice milestone (injected):

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_snapshot.py" --vault "$AI_SDLC_VAULT_ROOT" \
    --active-slice milestone.json risk-register.json slices/action-points.json 2>/dev/null
```

**Heavy-mode architecture phase — presence probe** (existence + counts only, NOT full content; these are the
`/heavy-architect` outputs, which the per-slice scan above does not cover — without this probe a Heavy project
sitting between `/heavy-architect` and the first `/slice` would look "pre-architecture" and falsely recommend
re-running `/heavy-architect`):

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_snapshot.py" --vault "$AI_SDLC_VAULT_ROOT" \
    --presence threat-model.json requirements.json non-functional.json \
               cost-estimation.json diagrams.json actors 2>/dev/null
```

The above injections pre-load vault JSON into the prompt context. If a file is absent the snapshot script omits it — state "not yet created" for any missing key.

Read the following JSON files if not fully covered by the injections above (skip gracefully if absent):

| File | Purpose |
|---|---|
| `<vault>/triage.json` | mode, classification, pipeline path, deferred steps |
| `<vault>/concept.json` | 1-line project description |
| `<vault>/risk-register.json` | open risks |
| `<vault>/slices/_index.json` | active slices, recent-10 |
| `<vault>/slices/action-points.json` | cross-slice action-points register |
| `<vault>/candidates.json` | live candidate backlog |
| `<vault>/shippability.json` | regression catalog count + last run |
| `<vault>/critic-calibration-log.json` | last calibration date, slices since |
| `<vault>/lessons-learned.json` | last 3-5 lessons |
| `<vault>/drift-log.json` | unresolved drift count |
| `<vault>/gate-log.json` | per-gate outcome log (Phase 0 measurement spine) — **read the file FULLY** here (do NOT rely on a truncated snapshot); aggregate per gate in Step 2 |
| `<vault>/threat-model.json` · `requirements.json` · `non-functional.json` · `cost-estimation.json` · `diagrams.json` · `actors/` | **Heavy-mode upfront architecture** — presence-probed above (existence + counts, not full-read). Their existence = `/heavy-architect` has run; their absence in Heavy mode = it has not. |

**Active slice:** Read `<vault>/slices/slice-NNN-<name>/milestone.json` first (primary source: `stage`, `next_action`, progress checkboxes, `on_resume`). If `stage` is `build` or later and `build-log.json` exists, read the last ~15 lines of its `events` array — the events trace is the durable record; compare its latest timestamp to `milestone.json.at`. If events are newer, milestone is stale — flag it.

Do NOT read individual slice design/mission files (active slice excepted). Do NOT read ADRs. Do NOT descend into `slices/archive/`.

## Step 2 — Compute derived metrics

**Active slice stage + next action:** Read directly from `milestone.json` fields (`stage`, `next_action`, progress checkboxes, `on_resume`). If `milestone.json` is missing, derive stage from file existence as fallback (WARN: milestone absent) using the **canonical stage-derivation rule** (shared verbatim with `/archive` Step 3 — keep the two identical): check the **highest** stage first (first match wins); `critique.json` is **OPTIONAL** (a low-tier slice with no mandatory trigger skips it — 1.1), so `build-log.json` presence decides `build` regardless of whether `critique.json` exists:

| Highest-present file in `<vault>/slices/slice-NNN-<name>/` | Derived stage |
|---|---|
| `reflection.json` | `reflect` (complete) |
| `validation.json` | `validate` |
| `build-log.json` | `build` |
| `critique.json` (only when `build-log.json` is **absent**) | `critique` |
| `design.json` | `design` |
| `mission-brief.json` | `spike` (awaiting `/risk-spike`) |
| none (directory missing or empty) | `none` — slice not started |

**Regression health:**
- Shippability count from `shippability.json`.
- Slices since last full catalog run.
- If >3 slices since last full run AND catalog exists: flag with WARN.

**Critic calibration (CAL-1):**
- Slices since last `/critic-calibrate` (from `critic-calibration-log.json`).
- States: `within window` (0-9), `approaching` (10-14, info only), `recommended` (15-20, WARN), `overdue` (>20, WARN-WARN).
- First run: if log is empty AND <10 archived slices → "first calibration deferred" (not a warning).

**Candidate backlog snapshot:**
- Count of `not-started` + `in-flight` candidates from `candidates.json`.
- Top-priority candidate (highest `priority.score`, not blocked-on-spike).
- Count of `blocked-on-spike` candidates.

**Gate hit-rate (GATE-LOG — Phase 0 measurement spine):** From the FULL `<vault>/gate-log.json` `entries[]`
(skip this whole metric if the file is absent or empty), **first split rows by kind**: a row is a RECALL row
when `kind == "miss"` (Phase 0.2 recall half), otherwise it is a VERDICT row (one gate-run). **Only VERDICT
rows feed runs/raised/precision** — a miss row carries no `findings_count` and counting it as a run would
deflate `raised_rate`. Group by `gate` and compute per gate:
- `runs` = number of **verdict** rows.
- `raised` = verdict rows with `findings_count > 0`; `raised_rate` = `raised / runs`.
- `precision` = `Σ findings_real / (Σ findings_real + Σ findings_noise)` over verdict rows — ONLY when those
  fields are present (today: `critique`). Omit `precision` entirely when no row carries them (do NOT show 0%).
- `misses` = number of **recall** (`kind == "miss"`) rows for this gate; `recall` = `Σ findings_real /
  (Σ findings_real + misses)` — i.e. catches / (catches + misses) — ONLY when `findings_real` is present AND
  `(catches + misses) > 0`. Omit `recall` when uncomputable; still show `missed <misses>` whenever `misses > 0`
  (a miss is real signal even before recall is computable for that gate).
- `reality_contact` = the rows' `reality_contact` (constant per gate): `high` / `medium` / `low`.
- `last` = the most-recent **verdict** row's `verdict` (+ `slice`).
Order the gates by `reality_contact` **high → medium → low** (reality-touching gates read first — Theme 2
seed). Mark a gate `(quiet)` when `runs >= 5` AND `raised_rate == 0` — it has flagged nothing for many slices
(a future lighten candidate, Phase 4/5). This is descriptive only — pulse changes nothing.
**Exclude the `design-tournament` gate from this whole section** (the precision/raised/quiet math): it is
INFORMATIONAL (3.3) — it raises no findings by design, so a zero raised_rate is expected, never a lighten signal.
Its rows carry `approach_divergence`, not a verdict; report them separately in `--full` (see below).

**Reality-approved vs model-approved (active slice — Phase 1.2):** From the active slice's gate-log rows
(filter on the **canonical** `slice == slice-NNN` — gate-log rows store the canonical id, NOT the
`slice-NNN-<name>` folder name, so match on the `slice-NNN` prefix), split the gates that returned a
**passing** verdict by what signed off:
- **Reality-approved** — `reality_contact` in {high, medium} with a pass-class verdict (`go`/`conditional`,
  `pass`, or drift `clean`): the spike / real-device validation / code-vs-claims check said yes against
  something that is *not the model*.
- **Model-approved** — `reality_contact == low` with a pass-class verdict (`clean`/`accept`, or code-review
  `clean`): a review signed off, but reality has not.
- **Pending reality** — reality gates (risk-spike, validate-slice) this slice has **not yet** recorded a pass
  (e.g. `validate-slice` absent pre-build). Name them so it is clear reality has not signed off yet.
Trust a green exactly as much as it touches something that is not the model — so a slice with only
model-approvals is NOT the same as one reality has signed off on, even if every box is checked.

**Cross-domain transfer validity ratio (Phase 2.3 — the number that decides the Phase 3 tournament):** From
`gate-log.json` rows with `cross_domain == true`, restricted to **reality gates** (`reality_contact` in
{high, medium}): `held` = rows with a pass-class verdict (`go`/`conditional`, `pass`); `total` = all such rows.
Ratio = `held / total` — "when a design imported a borrowed pattern, how often did REALITY confirm its
preconditions held?" (Part I §6's empirical measure of how much to trust the latent space). Report it WITH the
sample size (`held/total`); it is a **soft, model-tagged signal** (a row is tagged only when the slice's design
self-declared a transfer), so present it as directional evidence, not a hard metric. Omit when `total == 0`.

**Recommended next action — override precedence (resolve HERE; pass resolved value to Step 3):**

| Condition | Resolved next-action |
|---|---|
| Any worktree `BUILT_BUT_NOT_MERGED` | `cd <wt-path> && /commit-slice --merge` |
| Any worktree `IN_PROGRESS` (no BUILT_BUT_NOT_MERGED) | `cd <wt-path>` + worktree milestone `next_action` |
| Any worktree `MERGED` (worktree still exists post-merge) | WARN: CLEANUP-CANDIDATE — `git worktree remove <wt-path>` (merged branch leaked; run regardless of CAL-1 state) |
| Any worktree `UNKNOWN` (classification indeterminate) | WARN: unknown worktree state on `<branch>` — inspect manually before continuing; surface the sub-reason (no milestone found / divergent commit history / missing branch) |
| No BRANCH-2 override + CAL-1 overdue (>20 slices) | `/critic-calibrate` |
| **Pre-slice** (no worktree, `slices/_index.json` absent/no active entry, no `slices/slice-NNN/milestone.json`) + `triage.json` **absent** | `/triage` (greenfield) or `/adopt` (existing codebase) — project not classified yet |
| **Pre-slice** + `triage.json` present + `concept.json` **absent** (discover/adopt not run) | `/discover` — discovery (concept + actors + first candidates) gates everything after triage, in **every** mode. In Heavy, discover runs **before** `/heavy-architect` — do NOT skip to architecture. |
| **Pre-slice** + `concept.json` present + Heavy mode + architecture **absent** (`threat-model.json` absent per the probe) | `/heavy-architect` |
| **Pre-slice** + `concept.json` present + Heavy mode + architecture **present** (`threat-model.json` exists per the probe) | `/user-test` (if the concept is B2C) or `/slice` — **architecture is DONE; never re-recommend `/heavy-architect`** |
| **Pre-slice** + `concept.json` present + Standard / Minimal mode | `/slice` (or `/slice-candidates` / `/discover` if `candidates.json` is empty/absent) |
| Otherwise (an active slice exists) | Active-slice `milestone.json` `next_action` field |

This chain is exhaustive for the pre-slice phase and covers **both** entry paths: greenfield (`/triage` → `/discover`)
and brownfield (`/adopt` — which writes `triage.json` + `concept.json` + `candidates.json` up front, so it lands in
the concept-present rows and is **never** told to re-run `/triage` or `/discover`). The phase signals are purely
file-existence (`triage.json` → `concept.json` → `threat-model.json` → `slices/`), so each phase is detected from
what its predecessor produced — not inferred.

**Fail-safe (avoid confusion):** if NO row matches, or the signals are contradictory (an unexpected partial vault —
e.g. `concept.json` present but `triage.json` absent), do **not** improvise a slash command. State the observed
phase files and recommend `/pulse --full` or a manual check. A wrong "run X next" is worse than "here's what I see."

## Step 3 — Render via Haiku dispatch

Dispatch to a Haiku subagent (COST-1: read-only summarization, no synthesis required):

```python
# Use the Agent tool:
# subagent_type: "general-purpose"
# model: haiku
# Pass:
#   - mode_arg: "brief" | "default" | "full"
#   - All computed state from Steps 1+2 as a structured dict
#   - The output template for the requested mode (below)
```

The Haiku agent fills the template and returns the summary text. Main thread prints it. The Haiku agent does NOT re-run override resolution — it consumes the `recommended_next_action_override` key already resolved in Step 2.

### Default output template

```
# Project Pulse — <YYYY-MM-DD>

## Identity
**Project**: <name from concept.json, or dir name>
**Mode**: <Minimal | Standard | Heavy>
**Opened**: <triage date> (<days>d ago)

## Slices
**Total**: <N> shipped (<N-1> archived, 1 active)

### Active
**<slice-NNN-name>** (stage: **<stage>**)
- Intent: <one-line from mission-brief>
- Next action: **<resolved next_action>**
- On resume: <on_resume field if set>
[If build stage with newer events:]
- Recent events (last 5): <from build-log.json events tail>
- WARN: Recent FINDING/ERROR not in milestone — verify before continuing

### Recently shipped (last 3)
- <slice-NNN-name> (<N> slices ago) — <one-line>

## Architecture (Heavy mode only — OMIT this whole section in Standard/Minimal)
[From the architecture presence probe. Render only when Mode is Heavy:]
- threat-model: <present (N) | absent> · requirements: <present (N) | absent> · non-functional: <present (N) | absent>
- cost-estimation: <present | absent> · diagrams: <present | absent> · actors: <present (N files) | absent>
- Phase: <gate on concept.json so the order triage → discover → heavy-architect is respected:
  • `concept.json` absent → "discovery not done yet — architecture comes after /discover"
  • `concept.json` present + `threat-model.json` absent → "pending — /heavy-architect not yet run"
  • `threat-model.json` present → "complete — core architecture artifacts exist">
  (Do NOT name a slash command here — the single Recommended-next-action line below is authoritative.)

## Candidates
- Backlog: <N not-started + in-flight>  (blocked-on-spike: <N>)
- Top priority: <SC-NNN — description>

## Risk exposure
- **Active HIGH**: <N> — <R-NN (label), ...>
- Pending spikes: <N>

## Regression health
- Shippability catalog: **<N> critical paths**
- Last full run: <slice-NNN> (<N> slices ago) — <PASS/FAIL>
[If >3 slices: WARN]

## Critic calibration
- Last run: <N> slices ago
- Status: **<within window | approaching | recommended | overdue>**
[If overdue: WARN-WARN + "Run /critic-calibrate first"]

## Drift & bypass
- Unresolved drift: <N>
[Stranded branches: WARN per entry with halt: true]

## Gate hit-rate (measurement spine)
[From gate-log.json; OMIT this whole section if the file is absent/empty. One line per gate, ordered
high → low reality-contact:]
- <gate> [<reality_contact>]: <runs> runs · raised <raised>/<runs> (<raised_rate as %>)[· precision <p>%][· recall <r>%][· missed <misses>][ · quiet]
- ...
[If gate-log.json absent:] - not yet recorded — no gate has run since the measurement spine was added.

[Active-slice sign-off (Phase 1.2) — from this slice's gate-log rows; omit if the slice has no rows yet:]
- **Reality-approved**: <gates passed at high/med contact, e.g. `risk-spike (go)`>  | _none yet_
- **Model-approved**: <gates passed at low contact, e.g. `critique (clean), code-review (clean)`>  | _none yet_
- **Pending reality**: <reality gates not yet passed, e.g. `validate-slice (not run — pre-build)`>

[Cross-domain transfer validity (Phase 2.3) — omit if no cross-domain slice has reached a reality gate yet:]
- Cross-domain transfers confirmed by reality: <held>/<total> (<ratio %>) — soft signal; the number that decides whether the design tournament (Phase 3) is worth building.

## Top lessons (last 5)
- <lesson>
- ...

## Recommended next action
**<resolved override or stage-derived next-action>**

[Queued-after (from deferred / candidates top-2):]
1. <item>
2. <item>
```

### Brief mode (`--brief`)

```
# <project> — <slice-NNN> (<stage>) | Mode: <mode>
**Next**: <next-action>
HIGH risks: <N> (<R-NN, ...>)  |  Shippability: <N> paths, last OK <N> slices ago
Calibrate: <N> slices since last (<state>)  |  Drift: <clean | N unresolved>
Candidates: <N> live (<N> blocked-on-spike)
Recent lessons: <3 one-liners>
```

### Full mode (`--full`)

Balanced view plus:
- All active HIGH risks with detail
- All deferred items from last 5 reflections
- **Supersession links** — for any reflection read above whose `supersession` block is set, show
  `slice-NNN superseded by slice-MMM — <reason>` (this is the only surface for supersession; `/supersede-slice`
  no longer stamps `_index.json` — 3.12). No extra file reads beyond the reflections already loaded.
- All cross-slice action-points from `action-points.json`
- Full shippability catalog listing
- Critic calibration history (all past runs)
- **Designer divergence (3.3)** — from the `design-tournament` gate-log rows, the per-pair `approach_divergence`
  distribution (`identical` / `overlapping` / `disjoint`). Flag when `designer-practice ~ designer-expert` is
  `identical`/`overlapping` on **most high-tier slices**: the expert lens is converging on practice and not earning
  its spawn cost → note "consider dropping to 2 designers (medium-tier default)". Omit if no tournament has run.
- Stranded slice branches in detail (every `halt: true` entry with its class)
- Full gate-log history: every row, newest first — verdict rows (gate · slice · verdict · findings_count · reality_contact) and recall rows (gate · slice · `miss` · severity · caught_by)

## Critical rules

- **READ-ONLY.** Never write or edit vault files.
- **Derive, don't fabricate — never claim absence you didn't probe.** Report a file as absent/missing ONLY when it shows `absent` in an injection or presence probe above. Do NOT assert a vault file is missing (e.g. "no threat-model.json / requirements.json confirmed") from its mere absence in your input set — that is a hallucination, not a filesystem check. The Heavy-mode architecture artifacts are covered ONLY by the presence probe; if it shows `present`, the architecture phase is DONE and you must NOT recommend `/heavy-architect`.
- **Be specific about next action.** Name the slash command, not "continue".
- **Flags**: warn on overdue calibration, shippability gap >3 slices, unresolved drift, stranded branches.
- **Brief mode**: no tables, terse one-liners. Token budget: `--brief` <500; default <2k; `--full` <5k.
- **Worktree vault-forward false positive** (ADR-070): if any `BUILT_BUT_NOT_MERGED` worktree's installed surfaces are content-equal to the worktree's copies (modulo EOL), master-vs-installed divergence is the EXPECTED BUILT_BUT_NOT_MERGED state — suppress the drift flag and emit an info note instead.

## Pipeline position

- predecessor: any skill (orientation tool, not pipeline-gated)
- successor: whatever `Recommended next action` says — user decides
- auto-advance: false (read-only; no writes; user acts on output)
- user-input gates: none
