---
name: critic-calibrate
description: "Meta-skill that mines 'Missed by Critic' + 'Critic calibration' entries from the last N archived reflections, classifies blind-spot patterns (>=3-distinct-slices threshold), and produces 0-3 evidence-backed proposals to update agents/critique.md. Proposals are reviewed one-at-a-time — never auto-applied. Logs every run to critic-calibration-log.json. Spawns a 'critic-calibrate' subagent for the analysis; the main thread handles user review gates and log writes."
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

**1b. Current Critic prompt**

Read `~/.claude/agents/critique.md` in full. This is the canonical runtime copy. The in-repo copy is `agents/critique.md`; read both if drift is suspected, but the installed copy is what the agent actually uses.

**1c. Past calibration log**

Read `<vault>/critic-calibration-log.json` if it exists. Missing or empty runs array → pass `"no prior runs"`. Schema reference: `examples/critic-calibration-log.json`.

**1d. Effectiveness data**

For each prior accepted proposal in the log: count miss instances in that category within the current window vs the equivalent window before the proposal was applied. Include this as a structured block in the agent prompt.

## Step 2 — invoke the critic-calibrate subagent

Use the Agent tool with `subagent_type: "critic-calibrate"`. Pass all four inputs as the prompt body:

```
Window: last <N> archived reflections
Slice range: slice-<first> through slice-<last>

# Reflections in window
<extracted critic_calibration + missed_by_critic fields, tagged by slice>

# Current Critic prompt (from ~/.claude/agents/critique.md)
<full file contents>

# Past calibration log
<contents of critic-calibration-log.json, or "no prior runs">

# Effectiveness data
<for each prior accepted proposal: category, run date, miss count before, miss count after>
```

The subagent returns: pattern summary, effectiveness section, 0-3 proposals, "watching but not proposing" list.

**Zero-proposal is a valid result.** If the agent returns no proposals, skip to Step 4 (log the run). Do not push the agent to find something.

## Step 3 — present proposals one-at-a-time (user gate)

Present each proposal as a separate `AskUserQuestion` gate — do NOT bundle. For each:

1. Show the pattern (evidence: slice numbers + miss count).
2. Show the relevant excerpt from `agents/critique.md` the proposal targets.
3. Show the exact proposed text change.
4. Explain why this addition would have caught the observed misses.
5. Wait: **accept / modify / reject**.

Capture the user's decision (including any modification text) for the log.

## Step 4 — NEVER auto-apply

This skill produces proposals. It does NOT edit `~/.claude/agents/critique.md` or `agents/critique.md`.

For each **accepted** proposal, emit the apply instructions:

```
Accepted. To apply:
  1. Edit agents/critique.md (in-repo canonical source) — add the following text
     under the relevant section (see proposal target above):

     <exact proposed text>

  (v2: agents/critique.md is the plugin's single source of truth — no installed-copy
   forward-sync. Editing it IS the change.)
```

The user applies manually. If they ask Claude to apply it in a follow-up turn, that is a separate explicit action — not this skill.

## Step 5 — append calibration log

Append a new run entry to `<vault>/critic-calibration-log.json` using `vault_edit`:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" \
    --file critic-calibration-log.json --array runs --json '{
      "at": "<ISO-8601 timestamp>",
      "window": <N>,
      "patterns": [
        { "category": "<name>", "slices": ["slice-NNN", ...] }
      ],
      "proposals": [
        { "text": "<proposed text>", "decision": "accepted|modified|rejected" }
      ]
    }'
```

Schema by example: `examples/critic-calibration-log.json`. Always log the run — even zero-proposal runs (empty `proposals` array, patterns array populated with what was observed).

## Critical rules

- USE the Agent tool with `subagent_type: "critic-calibrate"`. Do not re-implement the classification rubric here.
- NEVER auto-apply prompt edits. Even after user accepts: instruct, don't write.
- ONE proposal at a time. Never bundle all three into one `AskUserQuestion`.
- TRUST the agent's zero-proposal outcome. Don't re-prompt.
- EVIDENCE-BASED only. Every proposal cites miss counts and slice numbers, never hypothetical.
- ALWAYS log the run (Step 5), including zero-proposal runs.

## Anti-patterns

- **Bundled proposals**: present them separately; users accept/reject each independently.
- **Auto-edit**: skip Step 4 or write to critique.md directly — this will corrupt the calibration audit trail.
- **Generic additions**: "pay more attention to edge cases" is useless; "Check HEIC EXIF orientation for iPhone upload paths (missed in slice-019, -021, -025)" is useful.
- **Over-calibrating**: if nothing accepted 3 runs in a row, widen the window (25-30 slices) or skip until more data accumulates.

## Pipeline position

- predecessor: none (standalone maintenance; typically run every 10-20 slices after `/reflect`)
- successor: none (`hands_off_to: []`)
- auto-advance: false
- user-input gates: proposal review in Step 3 (one gate per proposal, up to 3 gates per run)
