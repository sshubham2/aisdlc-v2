---
name: build-slice
description: "Executes the current slice end-to-end using plan mode plus a sequence of verification gates. The Builder enters plan mode to explore code with code-review-graph queries and targeted Reads, drafts a task sequence, and obtains explicit user approval before writing code. Execution proceeds task-by-task with per-task verification, a mandatory mid-slice smoke gate, and a multi-audit pre-finish gate before declaring the slice done."
when_to_use: "Trigger phrases: /build-slice, 'build this slice', 'implement the slice', 'ship the slice'. Use after /critique blockers are addressed (and /critique-review where required). Outputs build-log.json + updated milestone.json; hands off to /code-review."
allowed-tools: Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion
disable-model-invocation: true
---

# /build-slice — Execute with Plan Mode + Verification Gates

The mission brief is the **intent**. The design is the **shape**. The Critic findings are the **constraints**. Plan mode is the **route through actual code**.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`). Active slice = latest `<vault>/slices/slice-NNN-*/`.

## Live state — injected

Active slice context:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json 2>/dev/null | head -5
```

Active mission brief (acceptance criteria, must-not-defer, test-first flag, smoke gate):
```!
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --path-only 2>/dev/null)"
[ -n "$SDIR" ] && cat "$SDIR/mission-brief.json" 2>/dev/null
```

Critic constraints (Critic findings the build must respect):
```!
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --path-only 2>/dev/null)"
[ -n "$SDIR" ] && cat "$SDIR/critique.json" 2>/dev/null
```

## Prerequisite check

1. Find the active slice folder. Read `mission-brief.json`, `design.json`, `critique.json`, `critique-review.json` (if present — incorporate any MUST-FIX items as hard build constraints), and any ADRs created this slice.
2. If `critique.json` is absent: this is OK **iff** the Critic was deliberately skipped. Read `milestone.json` `progress[]` and look for `{ "step": "critique", "done": "skipped" }` (the `/critique` skip path writes this on a low-tier slice with no mandatory triggers — `done` is the string `"skipped"`, not the boolean `true`). Skip recorded → proceed (Builder self-review applies). No such marker → STOP: run `/critique` first.
3. If `critique.json` shows `"verdict": "blocked"`: STOP — address blockers before build.
4. **TPHD-1 pre-flight**: scan the `mission-brief.json` TF-1 plan table; verify each Test path will exist at the right path and the Test function name matches what will be built. Flag any drift for user fix BEFORE entering plan mode.
5. **CRP-1** (critique-review prerequisite):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/critique_review_prerequisite_audit.py" <vault>/slices/slice-NNN-<name>
   ```
   On `mandatory-critique-review-absent` exit 1: STOP and tell the user verbatim: **"STOP: this slice has a mandatory /critique-review (DR-1) that has not been run. Run /critique-review for this slice before /build-slice. If the skip is deliberate, document it by adding `critique-review-skip: \"skip — rationale: <text>\"` to milestone.json."** Do not enter plan mode until exit 0.

### WT-ROOT-1 — the slice WORKTREE is the build surface (READ FIRST)

**Every code Read/Edit/Write and every code-touching bash command in this skill (and in `/code-review`,
`/validate-slice`) targets the slice WORKTREE `$wt`, NEVER the main tree.** Two harness facts make a single
`cd` insufficient, so you must root EVERY operation explicitly:
- `Edit`/`Write`/`Read` take ABSOLUTE paths — a bash `cd` does NOT redirect them.
- Each ```bash block is a FRESH shell — a `cd` does NOT persist to the next block.

Therefore:
- **Resolve `$wt` and re-derive it in every code bash block** (it does not carry over):
  ```bash
  repo_root="$(git rev-parse --show-toplevel)"
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
  wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
  cd "$wt"                       # fresh shell each block — re-derive + re-cd every time
  ```
- **Code I/O uses `"$wt/<relpath>"` ABSOLUTE paths** — plan-mode Reads, task Edits/Writes, the smoke gate, and
  code-scanning audits. NEVER edit `<repo_root>/<relpath>` (the main tree). When in doubt, `git -C "$wt"`.
- **Vault I/O is unaffected** — `$AI_SDLC_VAULT_ROOT` is the EXTERNAL store (already absolute); mission-brief /
  build-log / milestone writes there are correct as-is and have nothing to do with `$wt`.
- The main tree MUST stay clean of slice code — the **WT-ROOT-1 audit** (Step 6) fails the slice if code leaked there.

### Branch / worktree state (BRANCH-2 / BRANCH-3)

Resolve the repo paths. **Shell vars do NOT persist across separate code blocks** (each ```bash block is a
fresh shell), so this is the reference derivation — the executable block in case 2 below re-derives what it needs:
```bash
repo_root="$(git rev-parse --show-toplevel)"
wt_base="$(dirname "$repo_root")/$(basename "$repo_root")-wt"
default=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
[ -z "$default" ] && default=$(git config init.defaultBranch 2>/dev/null)
# STOP if neither resolves
```

Then:
1. **Worktree exists** at `<wt_base>/slice-NNN-<name>` (BRANCH-3 normal case): resolve `$wt` (WT-ROOT-1), `cd "$wt"`, verify `git branch --show-current` matches `slice/NNN-<name>`. All subsequent code I/O is `$wt`-rooted.
2. **No worktree** (legacy / `WORKTREE=skip`): create it via the shared helper (re-derives `repo_root`/`default` —
   they do not carry over from the block above):
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   default=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
   [ -z "$default" ] && default=$(git config init.defaultBranch 2>/dev/null)
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder slice-NNN-<name> --repo-root "$repo_root"
   git worktree add <wt_path> -b slice/NNN-<name> "$default"
   ```
3. **Wrong branch / other slice**: STOP — ask user to switch context or document `WORKTREE=skip` in build-log.json events.
4. **Dirty main tree** (legacy pre-BRANCH-3 only): apply the canonical switch-commit-switch-worktree sequence; no auto-stash.

Escape-hatch shape (audit-required): `<YYYY-MM-DD HH:MM> DEVIATION: WORKTREE=skip — rationale: <text>`.

## Step 1: Load full slice context

State briefly:
- "Slice NNN: <name>"
- "Acceptance criteria: <count>"
- "Must-not-defer items: <count>"
- "Critic blockers addressed: yes / pending"

Update `milestone.json`: `stage: "build"`, `current_focus: "plan mode"`, `next_action: "plan approval"`.

## Step 2: Plan mode — explore actual code

**WT-ROOT-1:** explore the WORKTREE — `Read`/`Grep`/`Glob` files under `"$wt/"`, not the main tree. The task
sequence's file paths must be `$wt`-rooted, so the edits in Step 4 land on the slice branch.

Use code-review-graph MCP tools for structural understanding before Reads:
- `impact-radius` on the module(s) this slice touches — what it reaches transitively
- `search` for symbols and integration points

Then Read specific files for detail. Build dependency understanding for this slice's surface area.

Draft a concrete task sequence:
- Grounded in code you have actually read (not what `design.json` assumes)
- Specific: file paths, function names
- Ordered so the mid-slice smoke gate is reachable at ~50% of work
- Each task independently verifiable

## Step 3: User approval (PCA-1 gate-halt)

Present the plan. **HALT for explicit user sign-off — do NOT auto-advance into Step 4.**

If user requests changes: revise and re-present.

If the plan reveals the design is wrong: STOP. Surface to user: "Design says X. Code reality requires Y. Revise design, or proceed with a deviation?"

## Step 4: Execute task-by-task

**WT-ROOT-1:** every `Edit`/`Write` targets a `"$wt/<relpath>"` absolute path; every code-touching bash block
re-derives `$wt` and `cd "$wt"` first (fresh shell each block). The main tree is never written.

For each task:
1. Implement the task.
2. Run the relevant AC check (or smoke test if AC is not yet testable).
3. Pass: mark complete, move on.
4. Fail: fix then re-verify. Still failing after reasonable attempts: STOP, ask for help — do not accumulate broken state.

Update `milestone.json` after each task: progress counter, current work, files being edited, next step.
Append a one-line event to `build-log.json` events **before** any tool call that could fail and erase in-memory context (screenshots, large reads, binary outputs). Format: `<YYYY-MM-DD HH:MM> <CATEGORY>: <description>` where CATEGORY is `BUILD | TEST | SMOKE | FINDING | ERROR | DEFERRAL | DEVIATION`.

## Step 5: Mid-slice smoke gate (~50%)

Run the smoke gate from `mission-brief.json` on a real environment:
- Backend: hit the endpoint with curl / test client; check DB state
- Frontend: open the page in a real browser
- Mobile: install on a real device
- ML: run inference on a real sample

Fail: **STOP (PCA-1 gate-halt)** — diagnose, surface to user, HALT. Do NOT auto-advance on a broken base.

## Step 6: Pre-finish gate

All of the following must pass before declaring done:

- [ ] All ACs pass with evidence
- [ ] All must-not-defer items addressed (no TODO, no stub, no silent except)
- [ ] No new TODOs / FIXMEs / debug prints / console.logs
- [ ] Mid-slice smoke still passes (no regression)
- [ ] `/drift-check` **full mode** (appends `Trigger` entry to `<vault>/drift-log.json` — DCE-1 verifies this)

The pre-finish gate is **one consolidated command** — `pre_finish_gate.py` subprocess-orchestrates every
user-facing audit (WT-ROOT-1, DCE-1, LINT-MOCK, WIRE-1, BC-1, TF-1, BRANCH-1) and emits ONE verdict, so no check
can be silently skipped (the failure mode of the old hand-run-each-block gate).

**Step A — enumerate BC-1 Critical rules and attest them** (the only multi-step part; the gate then runs BC-1
strict with your acks):
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"
$PY "${CLAUDE_SKILL_DIR}/scripts/build_checks_audit.py" --slice "$slice_folder" --changed-files <list> --json
```
Address each applicable Critical rule, attest it in `build-log.json` (e.g. "BC-PROJ-3: this slice performs no
destructive git reset of uncommitted work"), and collect the addressed ids for `--ack-critical`.

**Step B — run `/drift-check` full mode** (it appends the `Trigger` entry to `<vault>/drift-log.json` that DCE-1
verifies), then run the consolidated gate from the worktree:
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$(basename "$slice_folder")" --repo-root "$repo_root" | head -1)"
cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/pre_finish_gate.py" \
    --slice "$slice_folder" --worktree "$wt" \
    --changed-files <list> \
    --changed-test-files <changed-test-files> \
    --ack-critical <addressed-ids>
# Append optional flags as applicable: --seam-allowlist <vault>/.cross-chunk-seams (if present);
#   --test-first (when mission-brief.json test_first == true); --strict (Heavy mode, LINT-MOCK Important rules block).
```
The gate prints `=== pre-finish gate: PASS|FAIL ===` with one line per check (`ok` / `FAIL` / `skip`). **Any FAIL
→ do not declare done; fix or escalate.** (CRP-1 already ran as prerequisite #5 before plan mode — not re-run here;
the six plugin self-audits are CI-only per 1.5.)

> **Note — plugin self-audits are NOT in this gate.** Six checks that grade the *plugin's own* static
> files (`UTF8-STDOUT-1`, `PCA-1`, `BCI-1`, `STP-1`, `NAW-1`, `SVW-1`) used to run here on every slice in every
> user project, where they are either no-ops or re-scan the plugin install (a constant result per plugin
> version, zero user value). They now run only in plugin CI (`.build/plugin_self_audits.py`, wired into the
> GitHub Actions workflow), never on a user's slice. The **SVW-1 routing discipline** below still applies to
> the Builder when it appends to shared vault files — that is a build-time rule, not a per-slice audit.

**BC-1 Critical rule handling**: address each applicable Critical rule, attest in `build-log.json` (e.g. "BC-PROJ-3: this slice performs no destructive git reset of uncommitted work"), then pass the IDs via `--ack-critical`. Important rules may be deferred with rationale logged in `build-log.json`.

**SVW-1 scope**: append-mutating a shared-aggregate vault file (`risk-register.json`, `lessons-learned.json`, `shippability.json`, `drift-log.json`, `build-checks.json`, `_index.json`) MUST route through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append` (SVW-1). Raw whole-file overwrites are only permitted for per-slice active-folder artifacts (build-log.json, milestone.json, etc.).

## Step 7: Do-not-defer enforcement

Items in `mission-brief.json` `must_not_defer` CANNOT be shipped as TODO / stub / silent-except / skipped. If deferring any: STOP, ask user explicitly. Approved deferrals go in `build-log.json` `deferrals` with rationale.

## Step 8: Write output artifacts

### build-log.json (create)
Schema by example: `examples/build-log.json`

### milestone.json (update)
Schema by example: `examples/milestone.json`. At pre-finish gate pass: `stage: "build"`, `next_action: "/code-review"`. Preserve any `critique-review-skip` / `drift-check-skip` frontmatter keys verbatim throughout — they are CRP-1 / DCE-1 escape-hatch records and must survive every rewrite.

### drift-log.json (append via vault_edit)
If `/drift-check` found entries, route through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append`. Schema by example: `examples/drift-log.json`. **Never raw-overwrite** this file.

## When the design is wrong mid-build

1. STOP execution immediately.
2. Write what you discovered in the conversation (not a file yet).
3. Ask: "Design says X. Code reality requires Y. Revise design, or proceed with documented deviation?"
4. If revise: stop the slice, run `/design-slice` updates, re-run `/critique` on changed parts, then resume.
5. If deviate: log the deviation in `build-log.json` events and continue.

## Heavy mode additions

- Test coverage report at pre-finish (compliance trail).
- `sign_off` field in `build-log.json` (human reviewer).
- Audit-grade commit messages referencing slice + ADRs.
- `--strict` flag required on `mock_budget_lint` (Important rules block).

## Critical rules

- ENTER PLAN MODE FIRST. Do not start editing without a user-approved plan.
- USE code-review-graph + Read/Grep/Glob to understand actual code BEFORE planning.
- DO NOT skip the mid-slice smoke gate. Catches "builds but doesn't work" early.
- DO NOT bypass the pre-finish gate. If something cannot pass, the slice is not done.
- DO NOT silently defer must-not-defer items. Ask explicitly; log approved deferrals.
- APPEND to build-log.json events BEFORE risky tool calls. Tool failures erase in-memory context; committed files persist.
- IF design is wrong: STOP and surface — do not silently "make it work."

## Pipeline position

- predecessor: `/slice-story` when the Critic surfaced ≥1 finding, else `/critique` (or its skip path) directly — both HALT post-TRI-1 and prompt the build (user-invoked) · successor: `/code-review` · auto-advance: true
- on-clean-completion: once the pre-finish gate fully passes (all ACs, must-not-defer, drift-check, all Step 6 audits) and `build-log.json` is written, invoke `/code-review` via the Skill tool. `/code-review` auto-advances to `/validate-slice` on clean completion.
- user-input gates (halt auto-advance):
  - Plan-mode approval (Step 3) — HALT for explicit user sign-off before any code edits.
  - Mid-slice smoke-gate failure (Step 5) — HALT, diagnose; do NOT auto-advance on a broken base.
  - Design-is-wrong mid-build — HALT and surface ("design says X, code says Y; revise or deviate?").
