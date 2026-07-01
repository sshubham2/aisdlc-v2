---
name: build-slice
description: "Executes the current slice end-to-end using plan mode plus a sequence of verification gates. The Builder enters plan mode to explore code with code-review-graph queries and targeted Reads, drafts a task sequence, and obtains explicit user approval before writing code. Execution proceeds task-by-task with per-task verification, a mandatory mid-slice smoke gate, and a multi-audit pre-finish gate before declaring the slice done."
when_to_use: "Trigger phrases: /build-slice, 'build this slice', 'implement the slice', 'ship the slice'. Use after /critique blockers are addressed (and /critique-review where required). Outputs build-log.json + updated milestone.json; hands off to /code-review."
argument-hint: "[slice-id]"
allowed-tools: Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion
---

# /build-slice — Execute with Plan Mode + Verification Gates

The mission brief is the **intent**. The design is the **shape**. The Critic findings are the **constraints**. Plan mode is the **route through actual code**.

> **"Plan mode" here means THIS skill's planning phase** (Step 2 explore → Step 3 user-approval HALT) — it is
> NOT the harness `EnterPlanMode` tool (not in `allowed-tools`; do not attempt to invoke it).

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`). Active slice = latest `<vault>/slices/slice-NNN-*/`.

## Live state — injected

Active slice context:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json 2>/dev/null | head -5
```

Active mission brief (acceptance criteria, must-not-defer, test-first flag, smoke gate):
```!
# slice-036: at-a-glance ONLY + exit-0 tolerant -- never abort skill-load. This !-injection cannot see ${ARGUMENTS} (it binds only in a bash BODY block -- SC-064/ADR-022), so it shows the no-arg active slice when unambiguous, else a hint; the BODY resolution sites below bind the named /build-slice slice-NNN and OWN the fail-closed exit-4 HALT.
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --path-only || true)"   # slice-036: || true (NOT 2>/dev/null) -- tolerate the non-zero exit so skill-load never aborts, but SURFACE the AMBIGUOUS stderr (slice-014 AC4: never swallow the resolver's HALT)
if [ -n "$SDIR" ]; then cat "$SDIR/mission-brief.json" 2>/dev/null || true; else echo "(no unambiguous active slice -- pass /build-slice slice-NNN, or run from the slice worktree; the body resolves the named slice + fail-closes)"; fi
```

Critic constraints (Critic findings the build must respect):
```!
# slice-036: at-a-glance ONLY + exit-0 tolerant -- never abort skill-load (the BODY resolution sites below own the named-slice resolution + the fail-closed exit-4 HALT; SC-064/ADR-022).
SDIR="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --path-only || true)"   # slice-036: || true (NOT 2>/dev/null) -- tolerate the non-zero exit so skill-load never aborts, but SURFACE the AMBIGUOUS stderr (slice-014 AC4: never swallow the resolver's HALT)
if [ -n "$SDIR" ]; then cat "$SDIR/critique.json" 2>/dev/null || true; else echo "(no unambiguous active slice -- pass /build-slice slice-NNN)"; fi
```

## Prerequisite check

1. Find the active slice folder. Read `mission-brief.json`, `design.json`, `critique.json`, `critique-review.json` (if present — incorporate any MUST-FIX items as hard build constraints), and any ADRs created this slice.
2. If `critique.json` is absent: this is OK **iff** the Critic was deliberately skipped. Read `milestone.json` `progress[]` and look for `{ "step": "critique", "done": "skipped" }` (the `/critique` skip path writes this on a low-tier slice with no mandatory triggers — `done` is the string `"skipped"`, not the boolean `true`). Skip recorded → proceed (Builder self-review applies). No such marker → STOP: run `/critique` first.
3. If `critique.json` shows `"verdict": "blocked"`: STOP — address blockers before build.
4. **TPHD-1 pre-flight**: on a `test_first` slice the `test_first_plan[]` PENDING stub is **producer-scaffolded** — one row per AC, written by `/slice` (Step 5.3) or by the Step-1 idempotent backstop below (slice-051/ADR-042). The builder then authors each row's `test_path`/`test_function` and walks it to PASSING at build. So this pre-flight verifies the planned test paths/functions the builder is about to author cover every AC. Flag any drift for user fix BEFORE entering plan mode.
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
  ARG="${ARGUMENTS[0]:-}"        # slice-036: bind the explicit /build-slice slice-NNN (binds in a bash BODY block, NOT a !-injection -- SC-064/ADR-022)
  if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
    slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --folder-only)"
  else
    slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"   # slice-014: NO 2>/dev/null -- the no-arg AMBIGUOUS exit-4 HALT surfaces HERE (the body is the fail-closed consumer)
  fi
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
# slice-022: base a slice on the INTEGRATION branch (uat), not the released trunk.
default=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root "$repo_root")
# STOP if it does not resolve (git unusable)
```

Then — **resolve which slice FIRST (slice-036):** the slice is the one NAMED by `${ARGUMENTS[0]}` when it is slice-shaped (`/build-slice slice-NNN` — the main-launched path), else the no-arg active slice (which keeps the fail-closed exit-4 AMBIGUOUS HALT). Derive the expected branch `slice/NNN-<name>` and worktree `$wt` from THAT named/resolved slice — **not** from the session's current branch. Then:
1. **Worktree exists** at `<wt_base>/slice-NNN-<name>` (BRANCH-3 normal case): resolve `$wt` (WT-ROOT-1), `cd "$wt"`, verify `git -C "$wt" branch --show-current` matches the resolved `slice/NNN-<name>`. **Under the explicit named-slice-from-main path the SESSION branch is `uat`/`master`, NOT the slice branch — that is the EXPECTED main-launch state, never the case-3 STOP** (the check runs against `$wt` AFTER the `cd`, so it still matches the slice branch). All subsequent code I/O is `$wt`-rooted.
2. **No worktree** (legacy / `WORKTREE=skip`): create it via the shared helper (re-derives `repo_root`/`default` —
   they do not carry over from the block above):
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   # slice-022: base the (re)created worktree on the INTEGRATION branch (uat).
   default=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root "$repo_root")
   [ -z "$default" ] && { echo "STOP: cannot resolve the integration branch." >&2; exit 2; }
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder slice-NNN-<name> --repo-root "$repo_root"
   git worktree add <wt_path> -b slice/NNN-<name> "$default"
   ```
3. **Wrong branch / other slice**: STOP — ask user to switch context or document `WORKTREE=skip` in build-log.json events. **Carve-out (slice-036):** an explicit named-slice-from-main invocation (`/build-slice slice-NNN` from the main tree) is NOT this case — the session branch differing from the slice branch is the EXPECTED main-launch state; resolve `$wt` for the NAMED slice (case 1) and proceed. This STOP fires only when no slice resolves (no arg + ambiguous → the exit-4 HALT) or the *resolved* slice's worktree genuinely mismatches its branch.
4. **Dirty main tree** (legacy pre-BRANCH-3 only): apply the canonical switch-commit-switch-worktree sequence; no auto-stash.

Escape-hatch shape (audit-required): `<YYYY-MM-DD HH:MM> DEVIATION: WORKTREE=skip — rationale: <text>`.

### Worktree lifecycle (L-2 — who owns `$wt` when)

```
/slice          creates $wt + branch slice/NNN-<name>
/build-slice    edits in $wt; leaves it UNCOMMITTED at exit (by contract)
/code-review    reads the uncommitted diff in $wt
/validate-slice tests the uncommitted code in $wt
/reflect        vault-only (archives the slice folder; $wt untouched, still uncommitted)
/commit-slice   stages + commits the $wt branch, merges/pushes per flags, removes $wt
```
**Re-entry after a `/validate-slice` FAIL:** the worktree is RESUMED, never recreated — case 1 above applies
(worktree exists → verify branch → proceed). Re-enter at Step 1 and re-plan only the failing surface (Step 3
approval still gates the new edits); the prior uncommitted work in `$wt` is the base, not an error.

## Step 1: Load full slice context

State briefly:
- "Slice NNN: <name>"
- "Acceptance criteria: <count>"
- "Must-not-defer items: <count>"
- "Critic blockers addressed: yes / pending"

Update `milestone.json`: `stage: "build"`, `current_focus: "plan mode"`, `next_action: "plan approval"`.

Create `<vault>/slices/slice-NNN-<name>/build-log.json` now (empty `events[]`, schema: `examples/build-log.json`)
if it does not exist — Step 4 appends events to it *before* risky tool calls, so it must exist before execution
starts, not be created at Step 8.

**`test_first` slice — ensure the `test_first_plan[]` exists NOW (TF-1 / SC-023; slice-051/ADR-042 backstop):** if
`mission-brief.json` has `variants.test_first == true`, run the shared scaffolder as an idempotent BACKSTOP at build start,
so the `test_first_plan[]` is present even for a slice opened before the `/slice`-time producer existed:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/scaffold_test_first_plan.py" "<vault>/slices/slice-NNN-<name>/mission-brief.json"
```
It appends one `{ac, status: "PENDING", test_path, test_function}` row per AC (idempotent — a no-op when `/slice` already
scaffolded the plan; it never clobbers a builder-authored row). **Surface/halt on a non-zero exit.** Then, still at build
start, the builder fills each row's `test_path`/`test_function` and walks it `PENDING -> WRITTEN-FAILING -> PASSING` in
Step 4 as the tests are written. Ensuring the plan HERE — not at the Step-6 gate — is what prevents the surprise
pre-finish FAIL: the Step-6 `brief_variants_audit --variant test_first --strict-pre-finish` gate requires every
row `PASSING` (at least one per AC). The canonical shape is `SPECS['test_first']` in `scripts/lib/brief_variants_audit.py`.

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
- **≤ 10 tasks (slice-inflation guard).** A plan that needs more is a multi-slice cut wearing one mission-brief —
  the bounded-unit premise ("small cut so it can concentrate") fails quietly right here, at the gate where a
  tired user rubber-stamps. If you exceed 10: do NOT present the long plan for approval; propose the split
  instead ("this is 2 slices: <A> now, <B> as candidate SC-NNN") and let the user choose.

## Step 3: User approval (PCA-1 gate-halt)

Present the plan. **HALT for explicit user sign-off — do NOT auto-advance into Step 4.**

If user requests changes: revise and re-present.

If the plan reveals the design is wrong: STOP. Surface to user: "Design says X. Code reality requires Y. Revise design, or proceed with a deviation?"

## Step 4: Execute task-by-task

**WT-ROOT-1:** every `Edit`/`Write` targets a `"$wt/<relpath>"` absolute path; every code-touching bash block
re-derives `$wt` and `cd "$wt"` first (fresh shell each block). The main tree is never written.

**`test_first`:** for each AC, write its test FIRST (it must FAIL — RED), implement, then flip that AC's
`test_first_plan[]` row `PENDING -> WRITTEN-FAILING -> PASSING` with its on-disk `test_path` + `test_function`
(the plan was drafted at Step 1).

**`walking_skeleton` marker-flip discipline (WS-1; slice-047/ADR-038).** On a `walking_skeleton` slice, flip an
`architectural_layers[]` row's `status` from `pending` to `exercised` **ONLY after that layer's `verification`
command has actually been run and observed to pass** — never set `exercised` up front as an intention. The
`exercised` marker is a positive claim of reality contact, and `/validate-slice` enforces it for real: WS-1
`--execute` runs each `exercised` layer's `verification` through the shared `verification_core`, and a decidable
failure (a non-portable bare-`pytest` form, a cited test absent on this checkout, a non-zero exit, or an
unparseable command) is a hard **STOP**, not a silent pass. A genuinely not-runnable command (program not on this
host's PATH) yields a **loud advisory** rather than a STOP, so a legitimate env-dependent skeleton is not
false-blocked — but that advisory is your signal to anchor the command (`<interp> -m pytest …`) or provide the
runtime, NOT to hand-mark the layer `exercised` to dodge the check.

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
user-facing audit (WT-ROOT-1, DCE-1, LINT-MOCK, WIRE-1, BC-1, TF-1, BRANCH-1, ARTIFACT-LINT) and emits ONE
verdict, so no check can be silently skipped (the failure mode of the old hand-run-each-block gate).
(ARTIFACT-LINT = 3.18.7 schema-by-example lint over this slice's vault JSON artifacts; on FAIL, fix the
offending artifact's required keys / enum values.)

**Derive `--changed-files` canonically — the gate's coverage is exactly as good as this list** (LINT-MOCK
SKIPs entirely on an empty test-file list; BC-1 scopes to what it is told). Fresh shell — re-derive `$wt` first
(WT-ROOT-1):
```bash
repo_root="$(git rev-parse --show-toplevel)"
ARG="${ARGUMENTS[0]:-}"   # slice-036: bind the explicit /build-slice slice-NNN (BODY block binds ${ARGUMENTS}; SC-064)
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --folder-only)"
else
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"   # slice-014: NO 2>/dev/null -- no-arg AMBIGUOUS exit-4 HALT surfaces HERE
fi
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
base="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_diff_base.py" --worktree "$wt")"   # SC-043: fork point vs the LOCAL integration branch (never origin/HEAD); HEAD fallback, never aborts
changed="$( { git -C "$wt" diff --name-only "$base"; git -C "$wt" ls-files --others --exclude-standard; } | sort -u )"
# --changed-test-files = the subset of $changed matching the project's test layout (tests/**, *_test.*, *.test.*)
```

**Step A — enumerate BC-1 Critical rules and attest them** (the only multi-step part; the gate then runs BC-1
strict with your acks):
```bash
repo_root="$(git rev-parse --show-toplevel)"
ARG="${ARGUMENTS[0]:-}"   # slice-036: bind the explicit /build-slice slice-NNN (BODY block; SC-064)
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --path-only)"
else
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"   # slice-014: NO 2>/dev/null -- no-arg AMBIGUOUS exit-4 HALT surfaces HERE
fi
$PY "${CLAUDE_SKILL_DIR}/scripts/build_checks_audit.py" --slice "$slice_folder" --changed-files <list> --json
```
Address each applicable Critical rule, attest it in `build-log.json` (e.g. "BC-PROJ-3: this slice performs no
destructive git reset of uncommitted work"), and collect the addressed ids for `--ack-critical`.

**Step B — run `/drift-check` full mode** (it appends the `Trigger` entry to `<vault>/drift-log.json` that DCE-1
verifies), then run the consolidated gate from the worktree:
```bash
repo_root="$(git rev-parse --show-toplevel)"
ARG="${ARGUMENTS[0]:-}"   # slice-036: bind the explicit /build-slice slice-NNN (BODY block; SC-064)
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --path-only)"
else
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"   # slice-014: NO 2>/dev/null -- no-arg AMBIGUOUS exit-4 HALT surfaces HERE
fi
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$(basename "$slice_folder")" --repo-root "$repo_root" | head -1)"
[ -d "$wt" ] || { echo "STOP: worktree '$wt' does not exist -- refusing to run the pre-finish gate against it (pre_finish_gate.py silently falls back to the main tree's cwd on an invalid --worktree). m3/C3: fail-visible, never a silent main-tree audit." >&2; exit 2; }
cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/pre_finish_gate.py" \
    --slice "$slice_folder" --worktree "$wt" \
    --changed-files <list> \
    --changed-test-files <changed-test-files> \
    --ack-critical <addressed-ids>
# Append optional flags as applicable: --seam-allowlist <vault>/.cross-chunk-seams (if present);
#   --test-first (when mission-brief.json test_first == true) -> TF-1 runs brief_variants_audit --variant
#     test_first --strict-pre-finish, requiring every test_first_plan[] row PASSING (the plan drafted at Step 1);
#   --strict (Heavy mode, LINT-MOCK Important rules block).
```
The gate prints `=== pre-finish gate: PASS|FAIL ===` with one line per check (`ok` / `FAIL` / `skip`). **Any FAIL
→ do not declare done; fix or escalate.** (CRP-1 already ran as prerequisite #5 before plan mode — not re-run here;
the six plugin self-audits are CI-only per 1.5.)

**Step C — gate-log the BCSG-1 build-checks attestation (model-tier).** BCSG-1 is a **model-tier self-attestation**
gate: it verifies you *acknowledged* each applicable Critical build-check, **not** that *reality* verified it. Per
3.1c it is gate-logged at `low` reality-contact so the measurement spine (`/pulse`, `/critic-calibrate`) can SEE it
— its green is **not** a reality green, and the hard STOP stays (a forcing function), but it is now measured. After
the gate PASSES, append ONE `build-checks` row (`findings-count` = unacknowledged-critical raised, `0` on the pass
path; mode from `triage.json`, tier = `risk_tier` from `mission-brief.json`):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate build-checks --slice <slice-NNN-name> \
    --verdict <clean|blocked> --findings-count <unacknowledged-critical count; 0 on pass> \
    --mode <minimal|standard|heavy> --tier <low|medium|high> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

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
- on-clean-completion: once the pre-finish gate fully passes (all ACs, must-not-defer, drift-check, all Step 6 audits) and `build-log.json` is written, invoke **`/code-review slice-NNN`** via the Skill tool — **pass the resolved slice id** (slice-036 / M3) so the forked `/code-review` resolves the SAME named slice from a main-launched build (its body binds `${ARGUMENTS[0]}` and the slice-shaped arg routes to `--slice "$ARG"`). Without the id the fork falls back to `--repo-root .` against its inherited cwd and AMBIGUOUS-HALTs under parallel slices, so the id is REQUIRED on the named-from-main path. `/code-review` auto-advances to **`/validate-slice slice-NNN`** (the id threads onward) on clean completion.
- user-input gates (halt auto-advance):
  - Plan-mode approval (Step 3) — HALT for explicit user sign-off before any code edits.
  - Mid-slice smoke-gate failure (Step 5) — HALT, diagnose; do NOT auto-advance on a broken base.
  - Design-is-wrong mid-build — HALT and surface ("design says X, code says Y; revise or deviate?").
