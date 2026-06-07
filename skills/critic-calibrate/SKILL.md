---
name: critic-calibrate
description: "Meta-skill that mines 'Missed by Critic' + 'Critic calibration' entries from the last N archived reflections, classifies blind-spot patterns (>=3-distinct-slices threshold), and produces 0-3 evidence-backed Critic checks reviewed one-at-a-time. Accepted checks are persisted to the project's vault overlay (active_checks[] in critic-calibration-log.json), which /critique reads before every review — it NEVER edits the plugin's agents/critique.md (that base is the maintainer's, shipped per plugin version; a project edit would be lost on upgrade and dirty the repo). Every run is appended to runs[] (audit history, never overwritten); the user is prompted to mail the log to the maintainer to fold recurring checks into the next plugin version. Spawns a 'critic-calibrate' subagent for the analysis."
when_to_use: "Trigger phrases: /critic-calibrate, 'calibrate the Critic', 'improve Critic prompt based on misses', 'analyze Critic blind spots'. Run every 10-20 slices as a routine calibration pass, after repeated misses in the same Critic category, or after a serious post-ship bug. Runs in all pipeline modes (minimal/standard/heavy). No predecessor or successor — standalone maintenance skill."
argument-hint: "[--window N]  (default: 15 reflections)"
allowed-tools: Read, Bash, Agent, AskUserQuestion
---

# /critic-calibrate — Close the Critic Feedback Loop

Mine Critic miss patterns from archived reflections, produce targeted prompt-update proposals, present them for human review, log results.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config).

## Prerequisite check

Archive must have >= 5 slices (can't find patterns in fewer). Run this Bash gate:

```bash
count=$(ls -t "${AI_SDLC_VAULT_ROOT}/slices/archive/" 2>/dev/null | wc -l)
if [ "$count" -lt 5 ]; then
  echo "INSUFFICIENT_ARCHIVE count=$count"
fi
```

If output contains `INSUFFICIENT_ARCHIVE`: tell the user to return after more slices accumulate, and stop.

## Step 1 — gather inputs

Collect the four inputs the subagent needs.

**1a. Archived reflections (window)**

Parse `--window N` from invocation args; default N=15.

List the last N archived slice folders:
```bash
ls -t "${AI_SDLC_VAULT_ROOT}/slices/archive/" | head -N
```

For each folder, read `<vault>/slices/archive/<folder>/reflection.json`. Extract the `critic_calibration` and `missed_by_critic` fields. Concatenate into one block tagged by slice id.

**1b. Base Critic prompt**

Read the plugin's base Critic prompt at `${CLAUDE_SKILL_DIR}/../../agents/critique.md` in full — for **context only**,
so the agent proposes checks NET-NEW to it (the base already covers many dimensions). This file is the plugin's base,
shipped per version; this skill **never writes it** — project-specific checks layer on top via the vault overlay
(Step 4). Resolves for both a plugin-cache install and a dev checkout.

**1c. Past calibration log + active overlay**

Read `<vault>/critic-calibration-log.json` if it exists (schema: `examples/critic-calibration-log.json`):
- `active_checks[]` — the checks ALREADY live in this project's overlay. Pass them so the agent proposes only
  NET-NEW checks (never a duplicate of an active check or of the base `agents/critique.md`). Note the highest
  existing `CC-NNN` id (Step 4a assigns the next one).
- `runs[]` — past runs for the effectiveness pass. Missing file or empty `runs` → pass `"no prior runs"`.

**1d. Effectiveness data**

For each prior accepted proposal in the log: count miss instances in that category within the current window vs the equivalent window before the proposal was applied. Include this as a structured block in the agent prompt.

## Step 2 — invoke the critic-calibrate subagent

Use the Agent tool with `subagent_type: "critic-calibrate"`. Pass all four inputs as the prompt body:

```
Window: last <N> archived reflections
Slice range: slice-<first> through slice-<last>

# Reflections in window
<extracted critic_calibration + missed_by_critic fields, tagged by slice>

# Current Critic prompt (from the plugin's agents/critique.md)
<full file contents>

# Active project checks (already in this project's overlay — propose only NET-NEW; never duplicate these or the base prompt)
<contents of active_checks[], or "none">

# Past calibration log (runs)
<runs[] of critic-calibration-log.json, or "no prior runs">

# Effectiveness data
<for each prior accepted proposal: category, run date, miss count before, miss count after>
```

The subagent returns: pattern summary, effectiveness section, 0-3 proposals (each a specific, imperative Critic check), "watching but not proposing" list.

**Zero-proposal is a valid result.** If the agent returns no proposals, skip to Step 4 (log the run). Do not push the agent to find something.

## Step 3 — present proposals one-at-a-time (user gate)

Present each proposal as a separate `AskUserQuestion` gate — do NOT bundle. For each:

1. Show the pattern (evidence: slice numbers + miss count).
2. Show the relevant excerpt from `agents/critique.md` the proposal targets.
3. Show the exact proposed text change.
4. Explain why this addition would have caught the observed misses.
5. Wait: **accept / modify / reject**.

Capture the user's decision (including any modification text) for the log.

## Step 4 — persist accepted checks to the vault overlay

This skill **NEVER edits `agents/critique.md`** (the plugin's base Critic — owned by the maintainer and shipped per
plugin version). A project-side edit would be **lost on the next plugin upgrade** AND would dirty the code repo on
`master`, breaking the slice-worktree contract (the main tree must stay clean). Accepted checks instead live in the
**EXTERNAL vault**, where `/critique` reads them before every review and they survive plugin upgrades.

Two writes to `<vault>/critic-calibration-log.json` (both SVW-1 via `vault_edit`; neither overwrites prior data):

**4a. Upsert each accepted/modified proposal into `active_checks[]`** — the small, deduped overlay `/critique` reads.
Assign a stable `id` = next `CC-NNN` after the highest existing (you read `active_checks` in Step 1c). Append **only**
checks whose `id`/text is not already present (dedup using that Step-1c read) so a re-run never duplicates or clobbers
an existing check:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" \
    --file critic-calibration-log.json --array active_checks --json '{
      "id": "CC-NNN", "check": "<imperative, specific check>",
      "category": "<pattern name>", "evidence": ["slice-NNN", ...], "added_at": "<ISO-8601>"
    }'
```

A **rejected** proposal that names an already-active check → retire it (remove that one element). A plain new
rejection adds nothing. Retire is the ONLY case that mutates an existing element; everything else is append-only.

**4b. Append the full run to `runs[]`** — the append-only audit history (every run, even zero-proposal):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" \
    --file critic-calibration-log.json --array runs --json '{
      "at": "<ISO-8601 timestamp>", "window": <N>,
      "patterns": [ { "category": "<name>", "slices": ["slice-NNN", ...] } ],
      "proposals": [ { "text": "<proposed check>", "decision": "accepted|modified|rejected", "check_id": "CC-NNN|null" } ]
    }'
```

Schema by example: `examples/critic-calibration-log.json`. Always append the run — even zero-proposal runs (empty
`proposals`, `patterns` populated with what was observed). `active_checks[]` stays small (it is all `/critique`
loads); `runs[]` grows but `/critique` never reads it.

## Step 5 — suggest mailing the log to the maintainer

Accepted checks are now live for THIS project via the overlay. To improve the **base Critic for every project** in the
next plugin version, the maintainer needs the log. Print the suggestion + the absolute path to attach:

```bash
echo "Mail your calibration log to the maintainer (s2.shubh2@gmail.com) so recurring checks can be folded into the"
echo "base agents/critique.md in the next plugin version. Attach:"
echo "${AI_SDLC_VAULT_ROOT}/critic-calibration-log.json"
```

This is a **suggestion, not a gate** — the project already benefits now via the vault overlay; mailing the log is only
how generic checks reach the shipped `agents/critique.md`. Never send mail automatically; the user decides.

## Critical rules

- USE the Agent tool with `subagent_type: "critic-calibrate"`. Do not re-implement the classification rubric here.
- NEVER edit `agents/critique.md` from a project. Accepted checks go to the vault overlay (`active_checks`, Step 4a); generic ones reach the base ONLY via the maintainer-mailed log (Step 5).
- ONE proposal at a time. Never bundle all three into one `AskUserQuestion`.
- TRUST the agent's zero-proposal outcome. Don't re-prompt.
- EVIDENCE-BASED only. Every proposal cites miss counts and slice numbers, never hypothetical.
- ALWAYS append the run to `runs[]` (Step 4b), including zero-proposal runs. Never overwrite a prior run.

## Anti-patterns

- **Bundled proposals**: present them separately; users accept/reject each independently.
- **Editing the plugin base from a project**: writing to `agents/critique.md` — the edit is lost on the next plugin upgrade AND dirties the code repo on `master` (breaks the slice-worktree contract). Accepted checks belong in `active_checks` (vault overlay); generic ones travel to the maintainer by mailed log.
- **Generic additions**: "pay more attention to edge cases" is useless; "Check HEIC EXIF orientation for iPhone upload paths (missed in slice-019, -021, -025)" is useful.
- **Over-calibrating**: if nothing accepted 3 runs in a row, widen the window (25-30 slices) or skip until more data accumulates.

## Pipeline position

- predecessor: none (standalone maintenance; typically run every 10-20 slices after `/reflect`)
- successor: none (`hands_off_to: []`)
- auto-advance: false
- user-input gates: proposal review in Step 3 (one gate per proposal, up to 3 gates per run)
