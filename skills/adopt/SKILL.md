---
name: adopt
description: "AI SDLC pipeline opener for BROWNFIELD projects. Scans the existing codebase with code-review-graph, optionally runs /diagnose for forensic analysis, conducts a structured brownfield interview (one question at a time), then produces an initial vault (triage.json, concept.json, risk-register.json, CLAUDE.md, decisions/ADR-*.json, candidates.json) grounded in code reality — not doc claims. Use INSTEAD of /triage when code already exists."
when_to_use: "Trigger phrases: /adopt, 'adopt AI SDLC for this project', 'brownfield', 'onboard existing codebase', 'add AI SDLC to this repo'. Pass mode as argument: /adopt MINIMAL|STANDARD|HEAVY, or omit for interactive selection. Different from /triage (greenfield) — /adopt reverse-engineers current state from code first."
argument-hint: "[MINIMAL|STANDARD|HEAVY]"
allowed-tools: Read, Glob, Grep, Bash, Write, AskUserQuestion, Skill
---

# /adopt — AI SDLC pipeline opener for brownfield projects

Onboards the pipeline into an EXISTING codebase. Replaces `/triage` in that role — do not run both.
After `/adopt`, the normal pipeline continues: `/discover` (optional, if concept still needs sharpening) → then the per-slice loop: `/slice → /risk-spike → /design-slice → /critique → /build-slice → /code-review → /validate-slice → /reflect`.

**Trust hierarchy (load-bearing)**:
1. Code observations (code-review-graph AST + reachability) — primary
2. User pain points + firsthand history — secondary
3. Doc claims (README, design docs) — hypothesis only; never copied verbatim to vault

## Live state — injected

```!
# Prior vault check (UX-2 fail-closed, three-state — mirrors /triage 3.19.7)
AI_SDLC_VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
if [ -z "$AI_SDLC_VAULT" ]; then echo "VAULT_UNRESOLVED"
elif [ -f "$AI_SDLC_VAULT/triage.json" ]; then echo "EXISTING_VAULT=true (vault: $AI_SDLC_VAULT)"; grep -o '"adopt_state"[^,}]*' "$AI_SDLC_VAULT/triage.json" 2>/dev/null || true
else echo "EXISTING_VAULT=false (vault: $AI_SDLC_VAULT)"; fi
# Stack probe
for f in package.json pyproject.toml go.mod Cargo.toml; do [ -f "$f" ] && echo "STACK_FILE=$f"; done
# CRG graph status
if [ -d ".code-review-graph" ]; then echo "CRG=present"; else echo "CRG=missing"; fi
```

> **UX-2 fail-closed — read before ANY vault write (same rule as /triage).**
> - `VAULT_UNRESOLVED` → **STOP. Write NOTHING** — no Step-3 `/slice-candidates` sub-invocation, no Step 6–9
>   writes. The vault root can't be determined (usually `$PY` / the SessionStart hook isn't set up); tell the
>   user to run `/ai-sdlc:setup`, then retry. Refuse rather than guess — a wrong guess overwrites an existing
>   project's vault (the incident class /triage's UX-2 rework documents). An empty resolution must NEVER read
>   as "fresh adopt".
> - `EXISTING_VAULT=true` with `"adopt_state": "awaiting-annotation"` → a PAUSED `/adopt` (the Step-3
>   diagnose HALT) — resume at Step 3's resume point (`/slice-candidates`), do NOT ask merge-vs-fresh.
> - `EXISTING_VAULT=true` otherwise / `EXISTING_VAULT=false` → Step 0 handles as before.

## Step 0 — preflight

Verify code-review-graph is installed. Run:
```bash
"${CRG:-code-review-graph}" --version 2>/dev/null && echo "CRG_OK" || echo "CRG_MISSING"
```
If `CRG_MISSING`: do NOT hard-stop with a raw pip command (that contradicts the README's graceful-degrade, `/triage`'s "advisory only", and bypasses the plugin's own doctor). Offer two paths via `AskUserQuestion`:
- **(a) Install + retry** — run `/ai-sdlc:setup` (it resolves + installs CRG and registers the MCP server, surfacing the resolved version), then re-run `/adopt`.
- **(b) Degraded adopt** — proceed WITHOUT the code graph: the brownfield interview + a stack-file scan still run, but every code-derived finding is marked **"unconfirmed — CRG absent"** (mirrors `/reduce`'s degraded pattern). Blast-radius / reachability claims are skipped, never guessed.

If injected state shows `EXISTING_VAULT=true`: ask via `AskUserQuestion` — merge with existing vault, or start
fresh (user must clean up first)? **Exception**: if the injection also printed `"adopt_state": "awaiting-annotation"`,
this is a PAUSED `/adopt` mid-diagnose — skip the ask and resume at Step 3's resume point. (`VAULT_UNRESOLVED`
already STOPped at the UX-2 rule above.)

## Step 1 — scan the codebase

Build (or refresh) the code graph **before asking the user anything**:
```bash
"${CRG:-code-review-graph}" build --repo .
```

Then read (in order of trust):
1. `.code-review-graph/GRAPH_REPORT.md` — code-derived digest: god nodes, communities, surprising connections
2. The detected `STACK_FILE` (e.g. `package.json` / `pyproject.toml`) — dependency truth
3. Top-level dir listing (≤10 entries)
4. `README.md` if present — **as hypothesis only**; note any discrepancy vs code

Summarize findings to the user (code-derived, not doc-derived):
```
Scanned codebase:
- Stack: <derived from STACK_FILE, not README>
- ~<N> LOC, <F> files, <E> endpoints inferred
- Main entry: <from graph>
- Tests: <from graph>
- README: <present/absent> (treated as hypothesis)
- Existing vault: <yes/no>
Proceeding to forensic analysis offer.
```

## Step 2 — detect mode argument

Parse: `/adopt MINIMAL|STANDARD|HEAVY` → skip mode-selection questions.
`/adopt` with no mode → interactive (ask at Step 4 after interview context).

## Step 2.5 — git-at-open gate (slice-058 / ADR-055 / SC-107 — run BEFORE any vault write, incl. Step 3 `/slice-candidates`)

Same rationale as `/triage` Step 0.5: the vault keys on `sha256(cwd)` pre-git vs `sha256(<git-common-dir>)`
after, so a later `git init` silently orphans a pre-git vault (SC-107). **This gate must precede the FIRST vault
write.** In `/adopt` that first write is NOT Step 9 — it is **Step 3's `/slice-candidates`** Skill-tool
invocation (on the `/diagnose`=YES path), which writes `<vault>/candidates.json` via `vault_edit`. (Step 1's
`crg build` is a CRG scan, not a vault write, so it legitimately precedes the gate; a Skill-tool sub-invocation
that writes the vault does NOT.) A brownfield repo is USUALLY already a git work tree (`GIT_OK`, the common path),
but handle the non-git case. Probe git:
```bash
git rev-parse --is-inside-work-tree >/dev/null 2>&1 && echo "GIT_OK" || echo "NON_GIT"
```
- **`GIT_OK`** → proceed to Step 3; the injected `$VAULT` is valid.
- **`NON_GIT`** → HALT and offer via `AskUserQuestion` (3 outcomes; the actuator NEVER self-consents):
  - **Run `git init` now (recommended)** — consented, fail-closed actuator:
    ```bash
    $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py" git-init --root .
    ```
    Non-zero (exit 3) → **STOP, write NO vault artifact** (do NOT run `/slice-candidates`); exit 0 → **RE-RESOLVE
    `$VAULT`** (next block).
  - **Decline (fail-closed)** → **STOP. Write NO vault artifact** (including no `/slice-candidates` run). State the
    pre-git fragility, as in `/triage` Step 0.5.
  - **Proceed without git anyway (explicit informed skip)** → proceed pre-git, but WARN concretely: a later
    `git init` (which the stranded-slice audit + CRG git-hook install both prompt) RE-KEYS + orphans this vault,
    and re-running the opener looks FRESH — you would hand-write the tier-2 pin to recover.

**Post-gate re-resolution (B1 — mandatory when `git init` just ran; used by Step 3 `/slice-candidates` AND
Steps 6-9 writes AND the Step-7 pin).** Shell vars do NOT persist across skill bash blocks and the top-of-file
`!`-injection resolved `$VAULT` **PRE-git**. Re-resolve in a bash BODY step and use THIS for every vault write;
**do NOT reuse the load-time injection.**
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
[ -z "$VAULT" ] && { echo "STOP: vault unresolved after git init." >&2; exit 2; }
if [ -f "$VAULT/triage.json" ]; then echo "EXISTING_VAULT=true (re-resolved: $VAULT)"; else echo "EXISTING_VAULT=false (re-resolved: $VAULT)"; fi
```

## Step 3 — offer /diagnose (gate 1)

For non-trivial brownfields, offer forensic analysis. Recommend `yes` (default) when: codebase >500 LOC OR >10 source files OR maturity=production/legacy/handed-off OR AI-assisted history suspected.

Offer via `AskUserQuestion`:
> "Before I trust any narrative about this codebase, I recommend running `/diagnose` — code-only forensic analysis (ignores docs). It produces `diagnose-out/diagnosis.html` covering dead code, half-wired features, contradictory assumptions, AI-bloat signatures, security gaps. You annotate findings Confirmed/No in a browser, save back, and confirmed ones seed the risk register + candidates backlog via `/slice-candidates`.
> Cost: ~10–20 min analysis + your annotation pass."
> Options: Run /diagnose now (recommended) | Skip (I know this codebase) | Tell me more

**If YES**: invoke `/diagnose` via Skill tool. Before HALTing, write a **durable resume marker** so a FRESH
session can resume deterministically (the HALT spans a session boundary; "say 'continue /adopt'" alone relies on
the user's phrasing): raw-write a minimal `<vault>/triage.json` stub —
`{"_schema": "aisdlc/triage@1", "adopt_state": "awaiting-annotation", "at": "<ts>"}` (safe: the git gate at
Step 2.5 already ran, and Step 9's full triage.json write overwrites this stub). Then **HALT** — tell user to
annotate `diagnose-out/diagnosis.html`, save it, and say "continue /adopt". On resume (detected by the user's
phrasing OR the injected `adopt_state` marker): invoke `/slice-candidates` via Skill tool; those confirmed
findings feed Steps 5 and 6.

**If SKIP**: note `diagnose_ran: false` + reason in triage.json. Risk register (Step 5) built from user pain points only.

**If tell me more**: one paragraph explanation, re-offer.

## Step 4 — brownfield interview (gate 2 per question)

Ask ONE question at a time via `AskUserQuestion`. Wait for each answer before the next.

**Always ask**:
1. What does this codebase do, in one sentence? (Compare to graph scan — flag mismatches)
2. What is the maturity? (Prototype / MVP shipped / Production / Legacy maintenance)
3. Who maintains it? (Just you / small team / multiple teams / handed off)
4. What is the next thing you want to build? (First slice candidate)
5. Are there known pain points? (Bugs you live with / tech debt / risky integrations / deferred work)

**If mode not yet declared**, also ask:
6. Who uses this? (Internal / B2B / B2C / mixed)
7. Compliance constraints? (None / light / heavy)

**Optional** (skip if user wants speed):
8. Any historical decisions you can articulate firsthand worth capturing as ADRs?
   STRICT: only capture decisions the user can describe themselves — context, options considered, why this won. Skip if they can't recall rationale. 0 real ADRs > 5 fictional ones.

## Step 5 — classify mode (if not pre-declared)

Heuristics:
- Heavy: compliance OR team >5 OR public API
- Minimal: solo + (prototype or legacy maintenance, <500 LOC)
- Standard: everything else

State mode + rationale, confirm via `AskUserQuestion`.

## Step 6 — build initial risk register

Sources, in trust order:
1. Confirmed /diagnose findings (if ran) — security gaps, half-wired features in production paths, contradictory assumptions, dead code in critical modules
2. User pain points from Q5 — known bugs, tech debt, risky integrations, deferred work
3. (NOT a source: README warnings or doc "known issues")

Tag each risk: `source: diagnose-finding-<id> | user-pain-point | both`.
<!-- vault-write-safe: project-open-single-shot --> Write `<vault>/risk-register.json` (schema: `examples/risk-register.json`).

## Step 7 — reverse-engineer concept.json

Write `<vault>/concept.json` (schema: `examples/concept.json`) grounded in code first:
- `what`: PRIMARY source = graph entry-points + reachability. SECONDARY: Q1 answer. If contradicting, record both
  and state which wins — AND record the verdict STRUCTURED, not just in the `what` prose: add
  `"q1_vs_code": {"match": true|false, "note": "<one line>"}` so the mismatch survives as data.
- `actors`: inferred from endpoints + auth/role-check sites in code, confirmed by Q1/Q3.
- `constraints.stack`: derived from STACK_FILE (concrete), NOT README.
- `constraints.infra`: from user only (code cannot determine this reliably).
- Add `doc_vs_code_discrepancies` field if /diagnose found README contradictions.

**Pin the vault (4.7, NEW vault only — skip when merging into an existing one).** Record the tier-2
git-common-dir pin so a later repo move/rename does not orphan this vault. **slice-058/M1 — guard on git
presence + check the exit** (on the Step-2.5 explicit-skip pre-git branch there is no git-common-dir to pin
into, so do NOT call write-pin there; on the git path a genuine failure (exit 3) must STOP):
```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py" write-pin; rc=$?
  [ "$rc" = 0 ] || { echo "STOP: vault_admin write-pin failed (rc=$rc) — surface + fix; do not leave the vault unpinned." >&2; exit "$rc"; }
else
  echo "note: pre-git explicit-skip path — no git-common-dir to pin into; the vault is fragile until \`git init\` (see Step 2.5)."
fi
```

## Step 8 — capture historical ADRs (firsthand only)

If Q8 produced firsthand decisions: write `<vault>/decisions/ADR-historical-NNN.json` for each (schema: `examples/adr.json`).
Set `"status": "accepted"`, add a field `"source": "firsthand-user-recall"`.

**Strict rules**:
- DO NOT extract ADRs from existing docs the user cannot articulate firsthand.
- DO NOT invent rationale. If user can't remember why → skip or record `reversibility: unknown` with an explicit note.

## Step 9 — scaffold vault

Create all vault paths. Failure to create any → STOP, surface error.

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
[ -n "$VAULT" ] || { echo "FATAL: vault root unresolved — run /ai-sdlc:setup or set AI_SDLC_VAULT_ROOT"; exit 1; }
MODE="<mode>"   # substitute the CONFIRMED mode (lowercase) before running — an unsubstituted placeholder inside the test below would silently no-op the Heavy scaffold
# Thin-vault rule (matches /triage): Minimal/Standard get ONLY the thin skeleton; components/
# contracts/schemas + actors/test-plan/frontend are Heavy-only ("the vault grows with the
# system, not ahead of it").
mkdir -p "$VAULT/decisions" "$VAULT/spikes" "$VAULT/slices"
if [ "$MODE" = "heavy" ]; then
  mkdir -p "$VAULT/components" "$VAULT/contracts" "$VAULT/schemas" "$VAULT/actors" "$VAULT/test-plan" "$VAULT/frontend"
fi
```

Write in order:
1. `<vault>/triage.json` (schema: `examples/triage.json`) — records adoption, whether /diagnose ran, mode,
   pipeline path. This FULL write replaces the Step-3 `awaiting-annotation` stub if one exists (drop the
   `adopt_state` field — adoption is complete)
2. `<vault>/concept.json` — from Step 7
3. `<vault>/risk-register.json` — from Step 6
4. `<vault>/decisions/ADR-historical-NNN.json` — from Step 8 (if any)
5. `<vault>/candidates.json` (schema: `examples/slice-candidates.json`) — from /slice-candidates output (if /diagnose ran); else write empty shell (`_schema: "aisdlc/slice-candidates@1"`, `candidates: []`, `pick_log: []`). Route appends through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"` (SVW-1).

**Heavy mode only** — additionally write (mark `fidelity: reverse-engineered | partial` in each; ask user to confirm before setting `confirmed`):
- `<vault>/components/<name>.json` (schema: `examples/component.json`) — one per major module from graph
- `<vault>/contracts/<name>.json` (schema: `examples/contract.json`) — from route handlers
- `<vault>/schemas/<entity>.json` (schema: `examples/schema.json`) — from data models
- `<vault>/threat-model.json` (schema: `examples/threat-model.json`) — STRIDE gaps + /diagnose + pain points
- `<vault>/cost-estimation.json` — placeholder; mark all fields `fidelity: user-population-required`; flag for user to populate actual cost estimates
- `<vault>/diagrams.json` — architecture diagrams derived from the CRG code graph (`.code-review-graph/GRAPH_REPORT.md` + impact-radius queries); mark `fidelity: reverse-engineered`

## Step 10 — generate ./CLAUDE.md

Short (~30 lines). Check first:

**If `./CLAUDE.md` does NOT exist** → create with the fresh brownfield template:

```markdown
# AI SDLC pipeline (adopted into existing codebase)

**Mode**: <mode> — see `<vault>/triage.json`
**Adopted**: <YYYY-MM-DD>
**Vault**: `<vault>/`
**Active slice**: check `<vault>/slices/_index.json`

## Hard rule before editing code

If the change is more than a typo / single-line tweak / comment / local-variable rename:
1. Check `<vault>/slices/_index.json` for an active slice.
2. If none → ASK the user via AskUserQuestion: "Run /slice first, or is this small enough to skip?"
3. Wait for explicit answer.

## Ask discipline

Present user input as structured options (recommended choice highlighted) via the AskUserQuestion tool —
never a bare free-text prompt. AskUserQuestion notifies the user; bare prose asks block silently.

## Tool-call hygiene

- Never `cd` in Bash — use `git -C <dir>` / absolute paths (cd resets shell cwd).
- Keep tool batches small, independent, non-duplicated (one error cancels the batch).
- After an "internal error", verify the write landed on disk before retrying.

## Brownfield rules

- **Code is truth, docs are hypothesis.** Verify doc claims against code before acting. Doc says X, code does Y → code wins; log the discrepancy.
- **Respect existing conventions.** Follow the pattern unless a slice explicitly revises it.
- **Deviations need an ADR.** Breaking convention = written reason, not a judgment call.
- **Refactors need a slice.** No "while I'm here" cleanups.
- **Tests-first for bug fixes.** Reproduce with a failing test before fixing (run /repro).
- **code-review-graph before wide changes.** Use CRG impact-radius tool to check blast radius.

## Vault discipline

ADRs are append-only (supersede with new ADR, never edit) -- enforced by the adr-append-only gate (ADR-APPEND-1 at /build-slice pre-finish). Run /drift-check before commit.
All vault appends route through vault_edit (SVW-1) — never raw whole-file overwrite.
```

**If `./CLAUDE.md` DOES exist** → APPEND only (do not overwrite existing content):

```markdown

## AI SDLC pipeline (brownfield-adopted)

**Mode**: <mode>. Vault: `<vault>/`. Active slice: `<vault>/slices/_index.json`.

**Hard rule**: before editing code (beyond typos/trivial), check for active slice. If none, ASK the user via AskUserQuestion — "Run /slice first?" Wait for answer.

**Brownfield rules**: code is truth, docs are hypothesis; respect existing conventions; deviations require ADRs; refactors need slices; tests-first for bug fixes; use CRG impact-radius before wide changes.

ADRs are append-only (enforced by the adr-append-only gate). Vault appends via vault_edit (SVW-1). Run /drift-check before commit.
```

## Step 11 — report + hand off

Tell the user:
```
Adoption complete. Vault at <vault>/:
- concept.json (code-derived first, user input second)
- risk-register.json (<N> risks: <N1> from /diagnose, <N2> from pain points)
- triage.json (mode: <mode>, diagnose_ran: <yes/no>)
- decisions/ADR-historical-*.json (<M> historical ADRs, firsthand-only)
- candidates.json (<K> slice candidates from /diagnose — or the empty shell on the skip path, ready for
  /slice-candidates / /discover to populate; the file ALWAYS exists after adoption)
- CLAUDE.md (brownfield-aware pipeline rules)

First slice candidate: <from Q4 if specific; else top entry from candidates.json>

Next step:
- If concept still needs sharpening → /discover (optional; skip if adopt interview + CRG scan already capture it)
- candidates.json has ranked candidates → /slice "<top entry>" (risk-spike runs inside the slice loop as its first gate)
```

## Critical rules

- TRUST CODE over docs. code-review-graph (CRG) scan + /diagnose findings are evidence; README is hypothesis. Where they conflict, code wins; log the discrepancy.
- DO NOT manufacture historical context. Reverse-engineered ADRs from firsthand recall ONLY — never doc archaeology.
- DO NOT rewrite existing code during adoption. `/adopt` is analysis + documentation, not implementation.
- RESPECT existing conventions. Default posture: don't refactor without justification.
- OFFER /diagnose for non-trivial brownfields. It is the cure for the doc-trust problem.
- HEAVY BROWNFIELD: mark fidelity (`reverse-engineered | partial | confirmed`) on every reverse-engineered component/contract/schema. Auditors must see what is verified vs inferred.
- ONE question at a time in the interview. Do not batch multiple questions.
- ALL vault multi-file appends route through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"` (SVW-1).

## Anti-patterns to avoid

- Trusting README claims without code verification
- Doc archaeology for ADRs (extracting decisions from docs the user can't articulate)
- Skipping /diagnose on a substantial brownfield to save time
- Over-documenting a small project (match vault scope to project scope)
- Silent auto-generation without user engagement (Q5 pain points are the most valuable subjective input)
- Making up ADR rationale when user can't recall it

## Pipeline position
- predecessor: none (entry point for brownfield) · successor: `/discover` (optional, if concept needs sharpening) → `/slice`
- auto-advance: no — multiple user-input gates (diagnose offer, interview Q1-Q8, mode confirmation)
- user-input gates: Step 3 (diagnose offer); Step 4 (each interview question); Step 5 (mode confirmation)
- on-clean-completion: point user to `/discover` (optional) then `/slice "<top candidate>"`; /risk-spike runs inside the slice loop — not directly from adopt
- hands-off-to: `/slice`, `/discover` (optional), `/slice-candidates` (if /diagnose ran), `/diagnose` (if offered)
