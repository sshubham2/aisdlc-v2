---
name: triage
description: "Greenfield AI SDLC project opener. Picks the pipeline mode (Minimal / Standard / Heavy) via interactive Q&A (or accepts a pre-declared mode), builds the initial reversibility-tagged risk register, writes the thin vault skeleton (triage.json, risk-register.json, decisions/, spikes/, slices/) plus a project-root CLAUDE.md, optionally installs code-review-graph integration, and hands off to the mode-appropriate next step. Re-runnable for mid-project re-scoping. Greenfield only — routes substantial existing codebases to /adopt."
when_to_use: "Trigger phrases: /triage, /triage MINIMAL, /triage STANDARD, /triage HEAVY, 'start AI SDLC project', 'open project for hybrid pipeline', 'pick pipeline mode', 're-triage'. First step of every greenfield pipeline. Do NOT auto-trigger for general project planning — only when user invokes the AI SDLC pipeline explicitly."
argument-hint: "[MINIMAL|STANDARD|HEAVY] <one-sentence project description>"
user-invocable: true
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# /triage — AI SDLC Pipeline Opener (Greenfield)

Opens (or re-opens) a **greenfield** project for the AI SDLC pipeline. Pick mode, build the risk register,
scaffold the vault, write `./CLAUDE.md`, hand off.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config).

## Live state — injected

Existing triage (re-triage detection — UX-2 fail-closed, 3.19.7):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
if [ -z "$VAULT" ]; then echo "VAULT_UNRESOLVED"
elif [ -f "$VAULT/triage.json" ]; then echo "RE_TRIAGE (existing vault: $VAULT)"; cat "$VAULT/triage.json"
else echo "FRESH (vault: $VAULT)"; fi
```

> **UX-2 fail-closed (3.19.7) — read before any write.** This used to be a bare
> `cat "$AI_SDLC_VAULT_ROOT/triage.json" 2>/dev/null || echo NOT_FOUND`: when
> `$AI_SDLC_VAULT_ROOT` was unset (hook not fired / fresh session) it printed `NOT_FOUND`,
> so triage thought an **already-opened** project was fresh and **raw-wrote over its vault**
> (data loss). The block above resolves the vault via the env var OR `_vault_paths.py`:
> - `VAULT_UNRESOLVED` → **STOP. Do NOT write** triage.json / risk-register.json. The vault
>   root can't be determined (usually `$PY` / the SessionStart hook isn't set up). Tell the
>   user to run `/ai-sdlc:setup` (or `export AI_SDLC_VAULT_ROOT=…`), then retry. Refuse rather
>   than guess — a wrong guess overwrites an existing project's vault.
> - `RE_TRIAGE …` → an opened project EXISTS. Take the **re-triage** path (Step 5a append to
>   `history`; never raw-overwrite triage.json / risk-register.json).
> - `FRESH …` → genuinely new; the Step 5a single-shot raw-write is safe. Write to that `$VAULT`.

Project-root CLAUDE.md (append vs fresh-create):
```!
test -f ./CLAUDE.md && echo "EXISTS" || echo "NOT_FOUND"
```

## Step 0 — Preflight (prerequisites)

Run the prerequisite check. The `agents` / `skills` prerequisites are bundled in **this** plugin, so resolve
them relative to `${CLAUDE_SKILL_DIR}` (the plugin's own `skills/<name>` dir), **not** the legacy
`~/.claude/agents/` · `~/.claude/skills/` install paths — under a plugin-cache install (e.g.
`.../plugins/cache/ai-sdlc/<ver>/`) the files live in the cache, so a `~/.claude/` probe falsely reports
MISSING. `${CLAUDE_SKILL_DIR}`-relative resolution works for both a plugin-cache install and a dev checkout:

```bash
test -n "$PY" && test -f "$PY"                          && echo "venv: OK"   || echo "venv: MISSING"
"${CRG:-code-review-graph}" --version >/dev/null 2>&1   && echo "crg: OK"    || echo "crg: MISSING (optional)"
test -f "${CLAUDE_SKILL_DIR}/../../agents/critique.md"  && echo "agents: OK" || echo "agents: MISSING"
test -f "${CLAUDE_SKILL_DIR}/../slice/SKILL.md"         && echo "skills: OK" || echo "skills: MISSING"
```

If `venv`, `agents`, or `skills` are MISSING — **STOP**. Tell the user the prerequisite is missing and point
them to the install docs. Do NOT proceed with missing prerequisites.

`crg: MISSING` is advisory only (code-review-graph is optional; skip the Step 5b-pre offer if absent).

## Step 1 — Detect re-triage

If the injected `triage.json` is NOT `"NOT_FOUND"`: this is a **re-triage**. Read the existing mode and
risks. Ask: "What changed? Does this require a mode change?" Only update what's different; append a new
`history` entry. Skip Step 2 mode logic if mode is unchanged.

If `"NOT_FOUND"`: fresh project — continue to Step 2.

## Step 2 — Parse explicit mode argument

- `/triage MINIMAL <desc>` / `/triage STANDARD <desc>` / `/triage HEAVY <desc>` → mode pre-declared;
  acknowledge and **skip mode-selection questions** in Step 3a; proceed to Step 3b.
- `/triage <desc>` with no mode token → full interactive mode selection (Step 3a).

Mode tokens are case-insensitive. If the first argument word matches a mode, use it; the rest is the description.

If a pre-declared mode conflicts with what the user says later (e.g., "MINIMAL" but mentions HIPAA): flag it
via `AskUserQuestion`: "Your project sounds like it needs <other mode> because <reason>. Stick with
<pre-declared> or switch?"

## Step 3a — Mode-selection Q&A (only if mode NOT pre-declared)

Ask **one question at a time**. Present each as an `AskUserQuestion`. Do NOT batch. Wait for the answer,
engage, then ask the next.

1. What are you building, in one sentence?
2. Who uses this? (Internal / B2B / B2C / mixed)
3. What is the biggest unknown? (Domain / tech / users / scale / integration / other)
4. Compliance constraints? (None / light — logging + access control / heavy — HIPAA, PCI, SOC2)
5. Team size and timeline?

Classify on three axes: `domain_clarity` (known | fuzzy), `compliance` (none | light | heavy),
`audience` (internal | b2b | b2c | mixed).

Pick mode:

| Mode | When |
|------|------|
| **Heavy** | compliance-heavy OR team >5 OR long-lived enterprise / regulated / public API |
| **Minimal** | solo dev AND (MVP or exploration or one-off) |
| **Standard** | everything else (default) |

**What mode actually controls (be honest with the user).** Mode is NOT the per-slice review cost — that is set
by each slice's **risk tier** (`/slice` Step 3a; `/critique` keys on tier, not mode — the design tournament now runs all 3 designers on every slice regardless of tier, ADR-018). Mode
controls three things: (1) the **default tier** for new slices — Minimal ⇒ `low` (small work is cheap by default;
bump up for risky cuts), Standard/Heavy ⇒ `medium`; (2) **vault structure** — Heavy creates components/contracts/
threat-model/etc., Standard/Minimal stay thin; (3) **Heavy's compliance floor** — Heavy forces `critic_required`
on every slice + requires human sign-off. So Minimal mostly saves cost by *defaulting slices to low tier*, not by
weakening review on a slice you mark `medium`/`high`.

**Regulated-domain guard (§2.4) — applies in BOTH 3a and the pre-declared path (Step 2).** If the answers (or
the pre-declared project description) name a regulated domain — health/medical (HIPAA), payments/cards (PCI),
PII at scale / children's data (GDPR/COPPA), finance (SOX/SOC2), government — and the chosen/declared mode is
**Minimal or Standard**, do NOT silently accept it. Say plainly: *"This looks regulated (<signal>). Minimal/
Standard has no compliance floor — no forced Critic, no human sign-off, no threat model. Heavy is the designed
mode for this; the pipeline is NOT an audit-grade compliance process in any mode (no sign-off workflow, no
audit-trail enforcement) — regulated projects need their own compliance review on top."* If the user keeps the
lighter mode, record the override in `triage.json` (`mode_override: {signal, declined: "heavy", rationale}`).
Their call — but never an uninformed one.

State the mode + 2–3 sentence rationale. **Wait for user confirmation** via `AskUserQuestion` before
writing any files.

## Step 3b — Risk questions (mode pre-declared, skip Q2/Q4 from Step 3a)

Ask only:
1. What are you building? (if not already in the description argument)
2. What is the biggest unknown?
3. Team size and timeline?

## Step 4 — Build initial risk register

From the conversation, identify risks. For each risk tag reversibility:
- `cheap` — change costs hours
- `expensive` — change costs days, may need migration
- `irreversible` — requires data migration, user re-onboarding, or is impossible

Compute score = likelihood × impact (low=1 / med=2 / high=3 → 1..9).
Band: 1–2 = low, 3–4 = medium, 6–9 = high.

For each HIGH-band risk, decide whether a `/risk-spike` is warranted; note it in `mitigation`.

After writing, validate with:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/risk_register_audit.py"
```
Exit 0 = scores/bands/statuses are valid. Non-zero = fix the flagged entries and re-run before continuing
(score must equal likelihood×impact; band must match; status/id/required-fields must be well-formed).

## Step 5 — Write vault skeleton

Create `<vault>/` if absent. Write the thin skeleton — these files/dirs only:

```
<vault>/
  triage.json          ← Step 5a
  risk-register.json   ← Step 5a
  decisions/           ← empty dir for ADRs
  spikes/              ← empty dir for /risk-spike output
  slices/              ← empty dir for slice folders
```

**Heavy mode only**: also create empty dirs `components/ contracts/ actors/ test-plan/ frontend/ schemas/`
for compliance.

Do NOT create those dirs for Minimal or Standard.

### Step 5a — Write triage.json and risk-register.json

**Gate (UX-2, 3.19.7):** the single-shot raw-writes below run **only on the `FRESH` signal** from the re-triage
injection above, targeting that resolved `$VAULT`. On `RE_TRIAGE` use the append/update path (end of this step); on
`VAULT_UNRESOLVED` you already STOPped — never raw-write a guessed path.

Write `<vault>/triage.json` — schema: `examples/triage.json`. Required top-level fields:
`_schema`, `mode`, `date`, `classification` (domain_clarity / compliance / audience), `mode_rationale`,
`pipeline_path`, `deferred_steps`, `history`.

<!-- vault-write-safe: project-open-single-shot --> Write `<vault>/risk-register.json` — schema: `examples/risk-register.json`. Each risk entry must
have: `id`, `title`, `likelihood`, `impact`, `status`, `reversibility`, `score`, `band`, `mitigation`,
`discovered.phase`, `discovered.at`. `score` and `band` are computed by the audit tool — set them correctly
or the audit will reject. These are project-open single-shot writes (no parallel-append hazard).

On **re-triage**: append a new `history` entry to `triage.json` via
`$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault "$AI_SDLC_VAULT_ROOT" --file triage.json --array history --json '<entry>'`.
Update `risk-register.json` via `scripts.lib.vault_edit update --file risk-register.json --array risks --id <R-NN> --set ...` (or `append` for a new risk) for any changed/new risks.

**Pin the vault (4.7, FRESH only).** After the FRESH writes, record the tier-2 git-common-dir pin so a later
repo move/rename does NOT orphan this vault (the pin survives a rename; `_vault_paths` reads it at tier 2):
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py" write-pin
```
It also drops a `.source-repo` back-ref in the vault (for `vault_admin list`'s orphan detection) and WARNs if a
same-name / different-hash sibling vault already exists (a likely prior-rename orphan to migrate + clean up).

### Step 5b-pre — Offer code-review-graph integration

If `crg` passed Step 0, offer via `AskUserQuestion`:

> "Install code-review-graph integration (MCP tools + git hooks + .code-review-graph/ store)? This gives
> every subsequent skill automatic blast-radius analysis and code search. [recommended: yes]"

If yes:
```bash
"${CRG:-code-review-graph}" install --platform claude-code
"${CRG:-code-review-graph}" build --repo .
```

If the directory is not a git repo and `code-review-graph install` fails with a git error: **STOP**. Ask:
"This isn't a git repo yet. Run `git init` yourself, then re-run /triage, or skip the git-hook step for now?"
Do NOT run `git init` or `git config`. Repo creation is the user's decision.

If no or crg is absent: note in `triage.json` `deferred_steps` — can be installed later.

### Step 5b — Write/update `./CLAUDE.md`

**Do NOT create `<vault>/CLAUDE.md`** — only `./CLAUDE.md` at the project root.

Keep it short (~20 lines). Skill detail lives in SKILL.md files — do not duplicate here.

**If `./CLAUDE.md` does NOT exist** — create with the fresh template:

```markdown
# AI SDLC pipeline

**Mode**: <mode> — details in `<vault>/triage.json`
**Vault**: `<vault>/`
**Active slice**: check `<vault>/slices/_index.json`

## Hard rule before editing code

If the change is more than a typo / single-line tweak / comment / local-variable rename:

1. Check `<vault>/slices/_index.json` for an active slice
2. If none → **ASK** via structured options: "Run `/slice` first, or is this small enough to skip?"
3. Wait for explicit answer. Don't proceed by default.

## Ask discipline

When a skill needs user input, use `AskUserQuestion` (structured options) — never a bare free-text prompt.
Claude Code only notifies on options prompts; a free-text question blocks silently.

## Tool-call hygiene

- No `cd` in Bash — use `git -C <dir>` / absolute paths (cd resets shell cwd)
- Keep tool batches small, independent, non-duplicated (one error cancels the whole batch)
- After "internal error", verify the write landed before retrying; blind retry can double-apply

## Vault discipline

- ADRs (`<vault>/decisions/ADR-*.json`) are append-only — supersede with a new ADR, never edit in place -- enforced by the **adr-append-only** gate (ADR-APPEND-1 at `/build-slice` pre-finish), not just convention
- Mid-build deviations → update active slice's `design.json` + note in `build-log.json`
- Run `/drift-check` before commit

## Testing discipline

Inside an active slice, "tests pass" means `/validate-slice` passed — including shippability regressions.
```

**If `./CLAUDE.md` EXISTS** — append the block below (do not overwrite existing content):

```markdown

## AI SDLC pipeline

**Mode**: <mode>. Vault: `<vault>/`. Active slice: `<vault>/slices/_index.json`.

**Hard rule**: before editing code (anything more than a typo / 1-line tweak / comment / local rename),
check for an active slice. If none, **ASK** via structured options — "Run `/slice` first, or is this
small enough to skip?" Wait for the answer.

**Ask discipline**: use `AskUserQuestion` for structured options — never bare free-text. Claude Code
only notifies on options prompts; a bare ask blocks silently.

**Tool hygiene**: no `cd` in Bash; keep batches small and non-duplicated; verify writes after errors.

**Testing**: inside an active slice, "tests pass" means `/validate-slice` passed (incl. shippability).

ADRs are append-only (supersede, don't edit) -- enforced by the adr-append-only gate (ADR-APPEND-1). Run `/drift-check` before commit.
```

## Step 6 — Tell user what's next (mode-specific)

**Minimal**: run `/discover` next.

**Standard**:
- B2C with UX uncertainty → "Run `/user-test mockup` next"
- Otherwise → "Run `/discover` next — it feeds the risk register into the `/slice` → `/risk-spike` in-loop spike gate"

Note: `/risk-spike` is an **in-loop** step reached via `/slice`, not a pre-pipeline step. HIGH-band risks are
noted in the risk register; they are addressed in-loop when `/slice` picks the candidate and `/risk-spike` proves
its assumptions.

**Heavy**:
1. `/discover` (full role-play per actor)
2. `/heavy-architect` (comprehensive upfront vault: components, contracts, threat model, cost)
3. `/user-test` if B2C
4. `/slice` to start the build loop
5. Periodic: `/sync` every 5–10 slices; `/reduce` every 5 slices

Remind: "I created/updated `./CLAUDE.md` (~20 lines) with the hard rule and vault discipline. It keeps
me on the pipeline across sessions. If you ever want me to bypass, say so explicitly."

## Critical rules

- **ONE question at a time.** Never batch. Wait for the answer.
- **Do NOT invent answers.** If the user is vague, re-ask.
- **Do NOT state the mode silently.** State rationale; wait for confirmation before writing.
- **Do NOT write files until Step 5** (mode confirmed).
- **Re-triage**: append via `vault_edit` — never overwrite history.
- **NEVER run `git init` or `git config`.** Repo creation is the user's decision.
- **Greenfield only**: if the user has >500 LOC of existing code, STOP and suggest `/adopt` instead.

## Mode quick reference

| Mode | For |
|------|-----|
| Minimal | Solo dev, MVP, exploration, one-off scripts |
| Standard | B2C, small teams, product work — the default |
| Heavy | Compliance, enterprise, regulated, public APIs |

## Pipeline position
- predecessor: none (pipeline entry point) · successor: `/discover`, `/user-test`, `/heavy-architect`, or `/slice`
- auto-advance: NO — mode confirmation and next-step selection require explicit user sign-off
- user-input gates: mode selection (Step 3a), mode confirmation (Step 3a), risk questions (Step 3b/4), code-review-graph install offer (Step 5b-pre)
