---
name: user-test
description: "Real-user validation gate — an actual person tests a mockup, prototype, or working slice. Prepares the artifact, generates behavior-focused observation questions, frames the session protocol, captures structured findings in user-tests/<name>.json, and appends new risks to risk-register.json (SVW-1). Applies to B2C and user-facing projects. Use before /design-slice when UX uncertainty is present."
when_to_use: "Trigger phrases: /user-test, 'test with real user', 'validate UX with users', 'mockup test', 'prototype test'. Run after /discover or /reflect when /triage or /reflect flags UX uncertainty. Skip for pure backend, internal tools, ML research, or engineer-facing CLI projects."
argument-hint: "mockup | prototype | slice"
allowed-tools: Read, Bash, Write, AskUserQuestion
---

# /user-test — real-user validation gate

Goal: get an actual person's reaction to a real artifact, then record what their **behavior** revealed. Actor
files are hypotheses; this skill retires UX risk with evidence.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).

## Live vault state — injected

Current risk register (for deduplication in Step 6):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"; $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_read.py" "$VAULT/risk-register.json" 2>/dev/null || echo "{}"
```

## Step 1 — confirm fit (user-input gate)

Ask via `AskUserQuestion`:

> "Is this a B2C or user-facing project where a real user's reaction will surface things actor simulation misses?"

- **No** → suggest `/critique` instead; stop.
- **Yes** → proceed to Step 2.

For pure backend / internal tools / ML research / engineer-facing CLI: surface this concern and confirm before
continuing. Usually the wrong tool.

## Step 2 — resolve mode

If no argument was given, ask via `AskUserQuestion`:

> "Which artifact are you testing? `mockup` (static wireframe / Figma), `prototype` (clickable, not functional),
> or `slice` (working build)."

### Artifact prep by mode

**mockup**
- Suggest tools: Figma, hand sketch, plain HTML, ASCII wireframe.
- If the user wants you to produce an HTML mockup: scope it to ONE flow; no nav chrome outside that flow.

**prototype**
- Suggest tools: Figma prototype, React/Vue with mocked data, ProtoPie.
- Confirm the flow is scoped to what the test covers; out-of-flow links are dead-ends.

**slice**
- Confirm a slice is built and runnable (`build-log.json` result: shipped).
- Help the user write a task script: 3–5 concrete tasks stated as goals, not instructions.

## Step 3 — generate observation questions (user-input gate)

Produce 3–5 behavior-focused observation questions for the facilitator. Then halt for the user to confirm or
modify them before the session starts.

**Good (behavior):**
- "Show me how you'd add a new expense from this screen."
- "You see this notification — what would you do next?"
- "Find the receipt for last Tuesday's coffee."

**Bad (opinion — never use):**
- "Do you like this design?"
- "How does this look to you?"
- "Would you use this?"

Users are unreliable narrators of their own behavior. Watch what they DO, not what they SAY.

## Step 4 — frame the session

Tell the user these session rules before they start:

- One user per session. Group sessions feel inclusive but are not honest.
- Prefer 1 user × 5 sessions over 5 users in one session.
- Do NOT lead. If the user asks "what should I do?" say "What would you do?"
- Do NOT fix bugs or explain during the session. Confusion is data.
- Facilitator observes silently; no encouragement or discouragement.
- Multi-device features (shared state, sync, multi-user flows) require simultaneous testing on >1 device instance.

Halt here: tell the user to run the session and return with observations (paste them or answer questions).

## Step 5 — capture findings (user-input gate)

After the session, prompt the user for observations across four categories:

| Category | What to capture |
|---|---|
| **Surprised** | Things the user did that weren't predicted — assumptions wrong |
| **Ignored** | Things in the artifact the user didn't notice or use — scope-bloat candidates |
| **Wanted** | Things the user expected that aren't there — missing feature or scope creep |
| **Stuck** | Friction points — where the user paused, got confused, or gave up |

Once observations are collected, write `<vault>/user-tests/<test-name>.json`
(schema by example: `examples/user-test.json`).

Write the file with the **Write** harness tool (raw-write; this is a new file per session, not an append).

## Step 6 — update risk register (SVW-1)

For each "Surprised" or "Stuck" finding that represents a new risk not already in `risk-register.json`:
construct a new risk entry and append it via vault_edit (the SVW-1 safe channel):

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --vault "$VAULT" --file risk-register.json \
    --array risks \
    --content-file <tmp-json-with-new-risk-entries>
```

Schema for each appended risk entry matches `examples/risk-register.json`.
Populate: `id` (next R-NN), `title`, `likelihood`, `impact`, `reversibility`, `status: open`,
`discovered.phase: user-test`, `discovered.at: <timestamp>`.

**NEVER use raw Write or Edit on risk-register.json** — it is an append/CAS-only file (SVW-1).

## Step 7 — recommend next action

Based on findings:

| Finding pattern | Recommended next |
|---|---|
| Clean session, no surprises | `/slice` (or `/design-slice` if a slice is already claimed) |
| Significant flow friction | Revise design before `/design-slice`; specify what to change |
| Concept assumption challenged | `/discover` |
| Critical UX miss requiring new slice | `/slice` with the miss as the candidate |

State the recommendation and the rationale. Then hand off.

## Critical rules

- **DO NOT skip or simulate.** If a real user is not available, say so. A simulated actor is NOT a user test.
- **DO NOT lead** the participant. If they get stuck, do not rescue them — that's the finding.
- **DO NOT count opinion as validation.** "They liked it" is not evidence. Behavior matters; opinion doesn't.
- **DO NOT batch** user tests with feature reviews. One artifact, one user, one session.
- **SVW-1**: all risk-register appends route through `scripts.lib.vault_edit`. Never raw-write the ledger.
- Escalate multi-device features: any flow involving >1 device/user/instance must be tested on >1 instance simultaneously.

## Anti-pattern

Skipping `/user-test` because "the actors file says what users want." Actor files are starting hypotheses, not
validated truth. Real users surprise you in ways no specification predicts.

## Pipeline position

- **Predecessor**: `/discover` (pre-loop, first B2C project) or `/reflect` (any slice introducing a new UX pattern)
- **Successor**: `/slice`, `/design-slice`, or `/discover` — determined by findings (Step 7)
- **Auto-advance**: no — findings determine the branch; user confirms the next action
- **User-input gates**: Step 1 (fit confirmation), Step 2 (mode), Step 3 (observation questions approval), Step 4 (session run), Step 5 (observations capture)
