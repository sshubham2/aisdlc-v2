---
name: user-test
description: "Real-user validation gate — an actual person tests a mockup, prototype, or working slice. Prepares the artifact, generates behavior-focused observation questions, frames the session protocol, captures structured findings in user-tests/<name>.json, and appends new risks to risk-register.json (SVW-1). Applies to B2C and user-facing projects. Use before /design-slice when UX uncertainty is present."
when_to_use: "Trigger phrases: /user-test, 'test with real user', 'validate UX with users', 'mockup test', 'prototype test'. Run after /discover or /reflect when /triage or /reflect flags UX uncertainty. Skip for pure backend, internal tools, ML research, or engineer-facing CLI projects."
argument-hint: "mockup | prototype | slice"
allowed-tools: Read, Bash, Write, AskUserQuestion, Agent
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

## Step 2.5 — heuristic pre-flight (OPT-IN, declinable; slice-044 / ADR-034)

A real user test has a high FIXED cost (recruit / schedule / prep / observe), so small slices get
declined and the gate feels brutal. This OPT-IN pre-flight spawns a forked **novice-engineer** model to
run a cheap static **screen** that FOCUSES the real session — it does **NOT** replace it. The model
checking the model shares the builder's blind spots, so its output is a **weaker, presumptive
"heuristic-walkthrough (model-only)" signal** that can **never** count as real-user validation (the
firewall is the canonical predicate `scripts/lib/user_test_gate.py:is_real_user_validated()`, which is
structurally blind to it).

**Offer it (AskUserQuestion):** "Run a quick model-only heuristic pre-flight to pre-draft observation
questions and surface obvious confusions before the real session? (It's a weaker screen, never a
substitute for the real user test.)" — options **Run pre-flight** / **Skip to the real test**.

- **Skip** → go straight to Step 3; the real-user flow is byte-for-byte unchanged.
- **Run pre-flight** → spawn the agent, ENFORCE its guardrails in code, persist the screen, then Step 3:

1. **Spawn `user-test-sim` via the Agent tool**, passing ONLY the **limited artifact** inline — the
   artifact text + the task(s) + `artifact_ai_generated` (true here: the artifact came out of this
   pipeline). **Do NOT pass design.json, build context, the vault, or repo paths** — the limited
   context is load-bearing (M4); a novice that can read the design stops being a novice and re-opens
   the echo chamber. The agent has no file tools by frontmatter; you keep it limited from the caller side.
2. **AC4 degrade — any failure is non-blocking:** if the agent is unavailable / unregistered (NAW-1) /
   errors / returns non-JSON, do not stop — note it and proceed to Step 3 with the real-user flow
   unchanged. (The `ingest` step below also degrades a malformed return to a defined skip.)
3. **Enforce in code (M5), never trust the agent to self-police.** Write the agent's raw JSON return to
   a temp file and normalize it through the canonical enforcer — it DROPS findings lacking a verbatim
   `evidence_quote` (A1.G1), FORCES `confidence:'low'` on interaction-predicting findings (A1.G3), sets
   the echo-chamber caveat (A1.G5), and degrades a missing/empty/malformed return to a defined
   skip-with-note (AC4):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/user_test_gate.py" ingest \
       --raw <tmp-agent-return.json> --ai-generated   # prints the normalized heuristic_walkthrough section
   ```
4. **Persist the screen now (must-not-defer #3 — log which sessions used the pre-flight).** Write
   `<vault>/user-tests/<test-name>.json` with `preflight_used: true`, the normalized
   `heuristic_walkthrough` section, and **empty real-user placeholders** so the artifact is conformant
   even if the real session is later declined: `participants: 0`, `tasks: []`, `findings: []` (schema by
   example: `examples/user-test.json`). Step 5 read-merges the real-user data into this file.
5. **Render distinctly + carry into Step 3.** Show the heuristic findings under a clearly WEAKER
   "heuristic-walkthrough (model-only)" heading, with the echo-chamber caveat and `disclaimed_scopes`
   visible, and carry each finding's `drafts_observation_question` into Step 3 as a **candidate** (never
   a confirmed result).

## Step 3 — generate observation questions (user-input gate)

Produce the observation protocol in **two clearly-separated blocks**, then halt for the user to confirm or
modify before the session starts.

### 3a — the flow-complete BASE protocol (ALWAYS present; INV-3 blinded confirmation)

Write **3–5 behavior-focused questions that cover the whole task end-to-end**, independent of any
pre-flight findings. This base MUST always include the dimensions a model screen is **blind** to and only
a real user reveals:
- **task-completion** — "Complete <the primary task> end to end; narrate as you go."
- **interaction-dynamics** — "Where did you hesitate, backtrack, or feel unsure what to do next?"
- **motivational-dropout** — "At any point did you want to give up? Why?"

**This base is non-negotiable even when the pre-flight ran.** The drafted questions below are a focusing
*screen*, never a substitute for full-flow coverage — if you let them replace the base you steer the real
session onto only what the model already saw (incorporation bias), losing exactly what the real test is
for. (This is the load-bearing INV-3 guard the design spike proved; see `tests/test_user_test_inv3.py`.)

### 3b — candidate additions from the model screen (only if the pre-flight ran)

If Step 2.5 produced a `heuristic_walkthrough`, append its `drafts_observation_question`s here as a
**delimited, clearly-weaker block** the user can edit or drop — framed as **hypotheses to DISCONFIRM**,
never as findings:

> **Candidate additions (from the model-only screen — presumptive, unconfirmed):**
> - "<drafted question>" — *hypothesis to check, not a known issue*

If no pre-flight ran (declined or AC4 degrade), this block is simply absent and the base protocol stands
alone — the real-user flow is unchanged.

**Good (behavior):** "Show me how you'd add a new expense from this screen." · "You see this notification —
what would you do next?" · "Find the receipt for last Tuesday's coffee."
**Bad (opinion — never use):** "Do you like this design?" · "How does this look?" · "Would you use this?"

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

**Stamp every real-user finding `source: "real-user"` (M1).** The canonical predicate
`is_real_user_validated()` requires this explicit tag — there is NO default — so an unstamped real
finding will NOT count as validation, and (by design) untagged or laundered data can never validate.

**READ-MERGE, do not clobber (M3).** If Step 2.5 already wrote this file (a `heuristic_walkthrough`
section + `preflight_used: true` + empty real-user placeholders), you MUST preserve those fields and
merge the real-user data into them — replacing the placeholder `participants`/`tasks`/`findings` with the
real values while keeping `heuristic_walkthrough` and `preflight_used` intact (must-not-defer #3: the
pre-flight-used log must survive). Read the existing file first; if none exists (no pre-flight ran),
write fresh. Use the **Write** harness tool for the merged whole-file write (a per-session active
artifact, not a shared-aggregate file — SVW-1 does not apply here).

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

- **DO NOT skip or simulate *as validation*.** If a real user is not available, say so — a simulated actor
  is NOT a user test and NEVER counts as real-user validation. **Carve-out (slice-044):** the OPT-IN Step-2.5
  heuristic pre-flight is a permitted, explicitly-*weaker* model-only SCREEN that AUGMENTS the real test
  (lowers its fixed cost) — it is firewalled by `is_real_user_validated()`, rendered in a distinct weaker
  color, and can never substitute for the real session. Simulation-as-validation stays prohibited;
  simulation-as-a-screen-before-the-real-test is allowed.
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
- **User-input gates**: Step 1 (fit confirmation), Step 2 (mode), Step 2.5 (heuristic pre-flight opt-in — declinable), Step 3 (observation questions approval), Step 4 (session run), Step 5 (observations capture)
