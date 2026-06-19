---
name: commit-slice
description: "Generate an audit-grade conventional commit message for a just-completed slice by reading vault artifacts (mission-brief.json, build-log.json, validation.json, reflection.json, critique.json, ADRs, shippability.json). Dispatches message rendering to a Haiku subagent (COST-1). Supports three mutually exclusive modes: --merge (solo-dev local merge + safe-delete), --push (shared rebase + push + gh-aware PR create + non-blocking auto-merge, degrading gracefully to push + printed hint), --sync-after-pr (post-PR cleanup, runnable from the main tree). No-flag default: generate and show only. Also writes a per-slice changelog.json audit record into the archived slice folder (Step 4.5); never writes to the code repo root EXCEPT the opt-in CI ship receipt (.aisdlc/receipts/, Step 4.8 — emitted only when the repo carries the aisdlc-merge-gate workflow)."
when_to_use: "Trigger phrases: /commit-slice, 'generate commit message', 'audit commit', 'slice commit message', '/commit-slice --merge', '/commit-slice --push', '/commit-slice --sync-after-pr'. Run after /reflect (which archives the slice). User-invoked only — never auto-advanced into."
argument-hint: "[--merge | --push | --sync-after-pr]"
allowed-tools: Read, Grep, Glob, Bash, Write, Agent, AskUserQuestion, Skill
---

# /commit-slice — audit-grade commit from vault

Generates a consistent, audit-ready commit message from the slice's vault artifacts. No hand-crafting.
One slice per commit. Every field sourced from a real vault file.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git config `aisdlc/vault-root`).

## Live state — injected

Most recently archived slice (for default no-flag / `--merge` / `--push` target resolution):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/latest_archived_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --json
```

## Mode flags (mutually exclusive)

- **No flag** — generate + show message; user copies to `git commit -m` (default; no git ops)
- **`--merge`** — commit on slice branch → shared rebase → no-ff merge into default → safe-delete (solo-dev / no-protected-branch path)
- **`--push`** — commit on slice branch → **shared rebase onto default** → `git push -u origin slice/NNN-<name>` → (gh present + GitHub origin) create the PR → (confirmed merge rights) enable non-blocking auto-merge; degrades gracefully to push + printed hint at every step (never merges locally; **auto-merge only** — no direct-merge)
- **`--sync-after-pr`** — post-PR cleanup (skips Steps 1–4); **resolves the target slice from anywhere, incl. the main tree** → two-signal merged-state detection → checkout default → pull --ff-only → safe-delete

If two or more flags passed: STOP — "Mode flags `--merge`, `--push`, `--sync-after-pr` are mutually exclusive; pass exactly one (or none for generate-only default)."

## Step 1 — identify target slice

Default: most recently archived slice (highest `slice-NNN` under `<vault>/slices/archive/`).
`--sync-after-pr`: current `slice/*` branch (no archive lookup needed).
If multiple uncommitted slices exist under `--merge`: ask user which to commit.

Prerequisite: archived slice folder has `reflection.json` (slice completed) — or active slice has `build-log.json` for mid-slice commits. If neither: STOP, tell user to run `/reflect` first.

## Step 2 — read vault artifacts

From `<vault>/slices/archive/slice-NNN-<name>/` (or active slice folder for mid-slice):

- `mission-brief.json` → intent (one-line), AC count
- `critique.json` (if present) → design-Critic blocker count + addressed status
- `code-review.json` (if present) → code-Critic blocker count + each blocker's CRD-1 disposition (`fixed` / `overridden` + rationale)
- `build-log.json` → `files_changed`, `deferrals`
- `validation.json` → per-AC PASS/FAIL, shippability regression status
- `reflection.json` → validated items, design corrections

Also:
- New ADRs referencing this slice: `Grep` for `"slice": "slice-NNN"` in `<vault>/decisions/ADR-*.json`
- `<vault>/shippability.json` → shippability entry added by this slice
- `<vault>/non-functional.json` (Heavy mode only, if present) → compliance frameworks for the Compliance line

## Step 3 — classify commit type + scope

Type from intent verb: add→`feat`, fix→`fix`, refactor/reduce→`refactor`, improve/perf→`perf`, test→`test`, migrate→`chore` (or `feat` if user-facing), docs→`docs`. Default: `feat`.
Scope: derived from the slice name area (e.g., `slice-023-add-receipt-ocr` → `receipt`).

## Step 4 — generate the commit message (inline)

Fill the template below **directly in the main thread** from the Step 2 input dict
`{type, scope, slice_id, slice_path, intent_one_line, body_2_3_sentences, ac_pass, ac_total, critic_blockers,
adrs, shippability_entry_n, shippability_entry_text, deferrals, regressions}`. This is a ~12-line mechanical fill —
the old COST-1 Haiku-subagent dispatch cost more in spawn overhead than the ~500 tokens it saved, so it is done
inline. (The defensible Haiku dispatch stays in `/archive`'s index regeneration.)

**Commit message template:**
```
<type>(<scope>): slice-NNN — <one-line intent from mission-brief.json>

<body: what was built / changed, 2–3 sentences>

Slice: [slice-NNN-<name>](<vault>/slices/archive/slice-NNN-<name>/)
Acceptance criteria: <X>/<Y> PASS (see validation.json)
Critic blockers addressed: <design-Critic list or "none">
Code-review blockers: <code-Critic list + disposition (fixed / overridden + rationale) or "none">
ADRs: <ADR-NNN, …> (or "none")
Shippability entry: #<N> — <one-line>
<if deferrals:   Deferred: <summary> (see reflection.json)>
<if regressions: Regression caught: <summary>>
```

**Heavy mode** — append:
```
Reviewer sign-offs: <from critique.json + validation.json>
<if <vault>/non-functional.json exists: Compliance: <applicable frameworks from non-functional.json>>
```
(Read `<vault>/non-functional.json` if present; omit the Compliance line entirely if the file does not exist — it is a Heavy-mode-only optional artifact.)

**Critic skipped** (low-tier slice, no mandatory trigger → no critique.json): `Critic: skipped (low-tier, no mandatory trigger)` (never omit the line).

**Bug-fix slice** (preceded by `/repro`): body notes the reproduction test path and that it now passes.

**Edge cases**: no new ADRs → `ADRs: none`; no regression → omit regression line; no deferrals → omit deferral line.

Example output → `examples/build-log.json` (the build-log schema also shows the Events append shape used in Steps 5b/5c below).
PCR audit log → `examples/parallel-conflict-resolution-log.json` — the `{"entries": [...]}` shape written to `<vault>/parallel-conflict-resolution-log.json`, appended (SVW-1 locked) ONLY on a HARD resolution via the PCR-2b `--record-hard-resolution` call (Step 5b sub-step 7). v2 has no auto-resolution entries (the vault is external, so every rebase conflict is CODE/HARD).

## Step 4.5 — write the per-slice changelog.json

Record what this slice changed as the JSON twin of the commit message, beside the slice's other artifacts.
`/reflect` already archived the slice, so the folder exists at `<vault>/slices/archive/slice-NNN-<name>/`.

Runs in **no-flag / `--merge` / `--push`** (any mode that ran Steps 1–4). `--sync-after-pr` skips this — it
generates no message (Steps 1–4 skipped); the changelog.json was already written when `--push` ran. The write is
idempotent (single-shot overwrite), so re-invoking `/commit-slice` on the same slice is safe.

Pass the Step 2 record + the Step 4 rendered subject as JSON on stdin (`--mode` = `none` for no-flag):

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/write_changelog.py" \
    --vault "$AI_SDLC_VAULT_ROOT" --slice slice-NNN-<name> --mode <none|merge|push> --json <<'EOF'
{"type":"<type>","scope":"<scope>","subject":"<full subject line from Step 4>",
 "intent_one_line":"<intent>","body_2_3_sentences":"<body>",
 "ac_pass":<X>,"ac_total":<Y>,"critic_blockers":"<list or none>",
 "adrs":[<"ADR-NNN", …> or empty],"shippability_entry_n":<N>,"shippability_entry_text":"<one-line>",
 "deferrals":<"…" or null>,"regressions":<"…" or null>}
EOF
```

Output schema by example → `examples/changelog.json`. The script writes `<slice-dir>/changelog.json` and prints the
path. On exit 2 (slice folder not found): surface the message but do **NOT** block the flow — the changelog.json is
an audit artifact, never a gate. This skill writes nothing to the code repo root.

## Step 4.6 — product-doc refresh hook (Theme 6: auto-maintain docs as slices ship)

If `<vault>/doc-manifest.json` exists, this project maintains product docs with `/product-doc`, and the slice you
just shipped may have made them stale — the per-slice `changelog.json` from Step 4.5 is the CHANGELOG's source.
**Offer** a refresh (don't force, don't auto-run — it writes to the repo root):

> "Slice shipped. Product docs are maintained here — refresh them? `/product-doc --docs changelog` reassembles the
>  CHANGELOG from the per-slice entries; if this slice changed user-facing behavior, add `readme,guide` to also
>  refresh those."

Skip silently when `doc-manifest.json` is absent (the project doesn't generate docs yet — `/product-doc` is how to
start). This is a reminder, never a gate; never block the commit flow on it. `/drift-check`'s `stale-doc` category
is the backstop for docs that drift when this hook is declined.

## Step 4.8 — CI ship receipt (opt-in — roadmap §2.1: discipline the SYSTEM maintains)

**Keyed on the workflow file's presence** — check `.github/workflows/aisdlc-merge-gate.yml` in the repo:

- **Present** (the project opted into the CI merge gate): emit the receipt into the worktree so it rides the
  slice commit and the PR carries its own reality evidence:
  ```bash
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/ship_receipt.py" emit \
      --slice slice-NNN-<name> --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$wt"
  ```
  It writes `.aisdlc/receipts/slice-NNN.json` (validation result + criteria counts + shippability/deferral
  state + this slice's gate-log rows). **Stage it with the commit** (add it to the Step 5b/5c `git add` set).
  On exit 2 (no validation.json — mid-slice commit): surface the message; the CI gate will then rightly fail
  the PR until validation runs.
- **Absent**: skip silently — this skill writes nothing to the code repo root without the opt-in. If the user
  asks how to get a merge gate, point them to the bundled template:
  `cp "${CLAUDE_SKILL_DIR}/assets/aisdlc-merge-gate.yml" .github/workflows/` (then make the check required in
  branch protection). Offer once, never auto-install — workflow files are repo policy, owned by the user.

`--sync-after-pr` skips this step (no commit is generated).

## Shared — rebase onto default (PSQ-3 + PCR-2b)

Both `--merge` (5b) and `--push` (5c) reach the **REBASED** rung through this ONE section,
so the rebase + conflict gate behave IDENTICALLY in both modes —
`parallel_conflict_resolver.py` is invoked UNCHANGED (extracting the rebase into a script
was rejected: its hard part, the PCR-2b gate, is interactive). Resolve the INTEGRATION branch
(slice-022: slices rebase ONTO `uat`, not the released trunk; rebase does not advance master, so the
read-only resolution is correct):
```bash
default=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root .)
```
STOP exit 2 if it does not resolve (NAW-1; git unusable). Run `git rebase <default>` on the slice branch. Outcomes:
- **Fast-forward no-op** or **clean replay**: REBASED reached → return to the caller (5b sub-step 3 / 5c push).
- **Conflict**: STOP — do NOT advance. Print conflicting U-files + `git rebase --abort` hint. Then:

  **PCR dispatch**: run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --classify --json`.
  In v2 the vault is an external, untracked store, so a vault file can NEVER be a rebase stage — every rebase
  conflict is a CODE conflict, hand-resolved via the PCR-2b gate (PCR NEVER auto-merges). `--classify` returns
  `conflict_class`:
  - `conflict_class: HARD` (any unmerged path): enter **PCR-2b gate** (below).
  - `conflict_class: UNKNOWN` (no unmerged paths / unreadable rebase state): fall through to SOAD-1 block.
  - non-zero exit (resolver unavailable / import failure): print stderr verbatim; fall through to SOAD-1 block.

  **PCR-2b HARD gate** (ADR-075 / TRI-RESOLVE-1):
  1. Bootstrap guard: if resolver unavailable → fall through to SOAD-1 (no weaker than pre-PCR-2b).
  2. Print `--diagnose --json` output. For `_index.json`-sole HARD: print hint "resolve by re-running `/archive` to regenerate, then `git add`".
  3. User (or Claude at user's instruction) resolves conflict markers + `git add`s each U-file.
  4. Run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --verify-resolution --json`.
     - `exit 1` (`git-state-unreadable`): fall through to SOAD-1 abort path; do NOT loop to step 3.
     - `exit 0` + `action: STOP` (`paths-still-unmerged` or `unresolved-markers-present` — keyed on `<<<<<<<`/`>>>>>>>` openers, NOT `=======`): print reason; return to step 3 (or offer Abort).
     - `exit 0` + `action: CLEAN`: proceed to step 5.
  5. Spawn `code-review` agent via Agent tool with the resolved diff (`git diff --cached` of U-file set), `--diagnose --json` context, and both rebase stages (`git show :2:<path>` / `git show :3:<path>`). Task: review for lost-hunk, dropped-side, both-sides-intent, semantic correctness, stray markers, vault/ADR contradiction. Capture verdict + findings INLINE. Any blocker → verdict BLOCKED.
  6. **TRI-RESOLVE-1 gate** (AskUserQuestion — 3 options):
     - **Apply resolution (continue rebase)** — offered ONLY when code-review verdict has no blocker.
     - **Re-resolve (edit again)** — return to step 3.
     - **Abort rebase** — fall through to SOAD-1 (a) abort path.
     Fail-closed: every non-Apply option and any interrupt → STOP-no-continue. NEVER call `git rebase --continue` except on explicit Apply with non-blocking verdict.
  7. On Apply: run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --record-hard-resolution --verdict "<verdict>" --disposition apply --json` (best-effort audit), THEN `git rebase --continue`, THEN append the PCR-2b breadcrumb to `build-log.json` Events. Order load-bearing: record BEFORE `--continue`.

  **SOAD-1 block** (3-option structured ask via AskUserQuestion):
  - (a) Abort rebase + investigate: print `git rebase --abort` recovery hint.
  - (b) Resolve manually + `git rebase --continue` outside skill, then re-invoke the caller mode.
  - (c) Cancel entirely: print `git rebase --abort` hint; skill exits cleanly.

  **A3 fallback — rebase intractable** (slice-008): when the rebase cannot be carried (repeated unresolvable
  conflicts, or the user declines to hand-resolve via SOAD-1 (a)/(c)), OFFER a **merge-default-into-slice-branch**
  fallback instead of the rebase: `git merge --no-ff <default>` on the slice branch, routing any conflict through
  the SAME PCR-2b gate above. Whichever lands clean (rebase OR merge-into-branch) yields a mergeable branch; the
  two-signal detection (5d) handles BOTH topologies (A3-proven, squash Pass-2 + regular Pass-1). NEVER force-push
  to paper over a failed rebase.

**Non-conflict rebase failure** (detached HEAD, broken ref): STOP with `git rebase --abort` hint + git stderr verbatim.

## Step 5 — present or execute

### 5a — no flag (default)
Show message with copy instruction:
```
git commit -m "$(cat <<'EOF'
<full message>
EOF
)"
```
No git operations are executed.

### 5b — `--merge` (BRANCH-1 sub-mode b / PSQ-3 rebase discipline)

**Pre-flight (run BEFORE any state change):**

1. **Stale-branch check** (parallel-aware, ADR-081): run `$PY "${CLAUDE_SKILL_DIR}/scripts/stale_branch_classifier.py" --repo-root . --json` from the slice worktree (cwd BEFORE any `cd`; self-exclusion requires HEAD == slice branch).
   - `verdict: refuse` (≥1 orphan_branches — worktree-less) → STOP: "Stale slice branches detected (no live worktree): `<orphan_branches>`. For post-PR-merge stragglers run `/commit-slice --sync-after-pr`; for other artefacts resolve manually (`git branch -d` each after verifying merged) before retrying."
   - `verdict: allow` + non-empty `parallel_slices` → one-line note "N parallel slice(s) in flight (worktree-backed): `<list>`"; PROCEED.
   - `verdict: allow` + empty `parallel_slices` → proceed silently.
   - Bootstrap/import failure (exit 1/2): fall back to legacy flag-all check (`git for-each-ref refs/heads/slice/` minus HEAD); STOP if any remain. Surface failure reason (fail-visible, never silent skip).

**Merge flow (sub-steps):**

2. Show message + `git status` (files to be staged) on current slice branch.
   Confirm: "Commit on `<current branch>`? (yes/no)" → yes: `git add <files_from_build-log>` + `git commit -m "..."`.

2.1. **WT-clean post-commit guard**: `git status --porcelain` MUST return empty after sub-step 2. If non-empty: STOP — "WT non-empty after commit. Unexpected uncommitted files: `<list>`. Commit or discard before proceeding." Vacuous on PSQ-3 re-entry (WT already clean).

2.5. **PSQ-3 rebase** (ADR-068): run the **Shared rebase section** (§ *Shared — rebase onto default*, above) to
   bring the slice branch onto `<default>`. On a clean / fast-forward rebase (or a clean A3 merge-into-branch
   fallback) → proceed to sub-step 3. On a blocked / aborted rebase (PCR-2b STOP, SOAD-1 abort/cancel) the shared
   section has already halted — do NOT proceed.

3. **Switch to main tree + merge** (BRANCH-2 worktree collision fix): resolve main tree path:
   ```bash
   main_tree=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')
   ```
   If empty: STOP — "main-tree-unresolvable." `cd "$main_tree"`. **Resolve the integration branch as a WRITE target (slice-022 M3)** — `default=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --write --repo-root .)`; on non-zero exit (uat absent) **STOP**: "integration branch uat absent; refusing to merge a slice into the released trunk — establish uat first" (NEVER fall back to master for a write — a `--merge` advances `$default`). Then `git checkout "$default"` + `git merge --no-ff slice/NNN-<name> -m "Merge slice/NNN-<name>: <intent>"`. If conflict: STOP with manual resolution hint (no recovery flow in v1).

4. Confirm: "Confirm merge + delete? (yes/no)" — on no: ABORT cleanly, leave merged branch intact.

5. **Idempotent worktree-remove** (BRANCH-2 / ADR-063): check `git worktree list --porcelain | grep "^branch refs/heads/slice/NNN-<name>$"`. If empty: LOG skip to `build-log.json` Events; proceed. Else: extract `wt_path` via awk + `git worktree remove "$wt_path"`. On refuse: STOP with hint.

6. `git branch -d slice/NNN-<name>` (safe-delete ONLY; NEVER force-delete a branch). If refused: STOP — "Safe-delete refused. Inspect with `git log <default>..slice/NNN-<name>`. Do NOT force-delete without understanding what's being discarded." Order load-bearing: worktree-remove MUST precede branch-delete.

7. Show `git log -1` + `git log --graph --oneline -5`.

**Critical rules — `--merge`**: NEVER `git push`, NEVER force-delete a branch, NEVER auto-resolve conflicts, NEVER skip git hooks.

### 5c — `--push` (ADR-020 / gh-aware PR flow — auto-merge only)

Turns `--push` from "push + print a hint" into the full ladder: REBASED → PUSHED → PR_CREATED →
AUTOMERGE_ENABLED, degrading gracefully to push + printed hint at every step. **Auto-merge only** — the
direct-merge path was dropped at slice-008 TRI-1 (ADR-006); nothing merges locally.

**Pre-flight:**
1. WT-clean: `git status --porcelain` must be empty.
2. Stale-branch check: identical to 5b pre-flight check above.
3. Current branch must start with `slice/`: `git symbolic-ref --short HEAD`. Otherwise STOP.
4. Origin remote: `git remote get-url origin` must succeed. Otherwise STOP.

**Push flow:**
1. Show message + `git status`.
2. Confirm commit on slice branch (yes/no) → `git add` + `git commit -m "..."`.
3. **Shared rebase section** (§ *Shared — rebase onto default*, above) → REBASED rung. On a blocked / aborted
   rebase the section has already halted — do NOT continue.
4. **ONE confirmation** (AskUserQuestion / yes-no): _"Push `slice/NNN-<name>` to origin, open a PR, and enable
   non-blocking auto-merge when you have merge rights? Auto-merge is non-blocking (it merges after CI); nothing
   merges locally."_ On no → STOP (branch left committed + rebased; nothing pushed).
5. On yes → invoke the non-interactive PR ladder (it owns push → PR-create → auto-merge-enable + verify). Each
   ```bash``` block is a FRESH shell (vars do NOT persist across blocks), so resolve `default` IN THIS block; 5c
   runs ON the slice branch, so the worktree IS the cwd — pass `--repo-root .` (do NOT reference a `$wt` from
   another block):
   ```bash
   # slice-022 (B1): pr_flow self-resolves the INTEGRATION branch (uat) for the PR base + rebase target.
   # Do NOT pass --default -- an inline origin/HEAD=master would OVERRIDE the swapped resolver.
   $PY "${CLAUDE_SKILL_DIR}/scripts/pr_flow.py" --confirmed \
       --branch slice/NNN-<name> --repo-root . --json
   ```
   (pr_flow self-resolves the integration branch from `--repo-root` via `resolve_integration_branch` — uat when
   present, the released trunk otherwise.) `pr_flow.py` pushes (`git push -u origin slice/NNN-<name>` — never force-push, never skip
   hooks) → creates the PR with `gh pr create --base <default> --head <branch> --fill` (the `--fill` is REQUIRED so
   the title/body come from the slice commits — `gh pr create` is otherwise interactive; gh present + GitHub origin,
   else it prints the hint) → enables non-blocking auto-merge ONLY when `.permissions.push==true`, **verifying the
   outcome** by reading `autoMergeRequest` back (never trusts gh's exit code) → treats a 422 / no-permission /
   silent-false-success as the EXPECTED graceful fallback. It EXITS 0 on every graceful degradation; exit 1 =
   internal error; 2 = usage.
6. **Read the verdict** — the LAST stdout JSON line. Report `rung_reached`, `action`, and `pr_url`, then print the
   verdict's `reason` (the exact manual command to finish from the halted rung). Append each verdict `event` to
   `build-log.json` Events via `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append`.

**What `--push` never does**: merge locally, delete any branch, force-push, or skip git hooks; and it NEVER enables
auto-merge without a confirmed merge-permission signal (`unknown` → fall back, never enable).
**Critical rules — `--push`**: NEVER force-push, NEVER push to a non-`origin` remote, NEVER skip git hooks, NEVER
enable auto-merge on an unconfirmed merge-permission signal.

### 5d — `--sync-after-pr` (ADR-020 / post-PR cleanup, runnable from anywhere)

Skips Steps 1–4 (no commit generated). Runs from the MAIN tree OR a slice worktree (AC4) — it resolves the
target slice itself, so the owner need not `cd` into the slice worktree.

**Pre-flight:**
1. WT-clean: `git status --porcelain` must be empty.
2. Origin remote present.

**Cleanup flow:**
1. **Resolve the target slice** (runs from anywhere):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/resolve_sync_target.py" --repo-root . --vault "$AI_SDLC_VAULT_ROOT" --json
   ```
   (Add `--slice slice-NNN` to target a specific slice.) `resolve_sync_target.py` is resolve-only (never deletes):
   explicit `--slice` → archive-aware by-id; on a `slice/*` branch → resolves self (back-compat); else auto-detects
   from local `slice/*` refs, **EXCLUDING worktree-backed in-flight slices** [M4], and two-signal-merged over the
   survivors. Read the plan's `status`:
   - `resolved` → use its `slice` / `branch` / `worktree_path` as `<branch>` below.
   - `ambiguous` → AskUserQuestion among `candidates` (or re-run with `--slice`); NEVER auto-pick.
   - `none` → STOP with the plan's `reason` (nothing merged-and-not-in-flight to clean). Pass `--slice` to clean a
     worktree-backed merged slice explicitly.
2. `git fetch --prune origin <default> <branch>` (explicit refspec required for Signal B).
3. **Resolve the integration branch** (slice-022 M-add-1): `default=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root .)`. A slice merges to `uat`, so the two-signal merged-detection below (`git cherry origin/<default>` / `git merge-base origin/<default>`) and the post-merge `git checkout <default>` + `git pull --ff-only origin <default>` target `origin/uat` / `uat`, NOT the released trunk. STOP exit 2 if it does not resolve.
4. **Two-signal merged-state detection** on the RESOLVED `<branch>` (unchanged):
   - **Signal A**: `git ls-remote --exit-code origin <branch>` returns non-zero (remote absent).
   - **Signal B** (two-pass):
     - Pass 1: `git cherry origin/<default> <branch>` → no `+` lines → YES.
     - Pass 2 (fallback when Pass 1 has `+` lines): compute `BASE=$(git merge-base origin/<default> <branch>)` + `FILES=$(git diff --name-only BASE..<branch>)`.
       - Empty-FILES guard: if FILES empty → Signal B = NO; STOP with diagnostic.
       - Perf bound: if `BASE..origin/<default>` > 500 commits → STOP with diagnostic.
       - Predicate: any commit C on `BASE..origin/<default>` whose touched-file set ⊇ FILES AND tree-state at FILES paths equals `<branch>^{tree}` → Signal B = YES (squash-merge detected).
   - Both signals must be YES to proceed. (This assumes head-branch auto-delete is ON — Signal A = remote gone;
     hardening the auto-delete-OFF case is **SC-018**, deferred at slice-008 TRI-1.)
   - Signal A=NO: STOP — "Remote slice branch still exists. PR may be unmerged."
   - Signal B=NO: STOP — "Slice commits not on `origin/<default>`. Re-run after PR merges."
5. Confirm: "PR appears merged + remote-deleted. Confirm cleanup (checkout `<default>` + pull --ff-only + safe-delete `<branch>`)? (yes/no)"
6. On yes (order load-bearing): `git checkout <default>` → `git pull --ff-only origin <default>` (NEVER a bare `git pull`) → idempotent worktree-remove guard (identical to 5b sub-step 5, using the plan's `worktree_path` when set) → `git branch -d <branch>` (safe-delete). From the main tree, no `cd` into the slice worktree is needed.
7. Show `git log -1` + `git log --graph --oneline -5`.

**Critical rules — `--sync-after-pr`**: NEVER force-delete a branch (safe-delete only), NEVER omit `--ff-only`, NEVER omit the explicit fetch refspec, NEVER skip the both-signal AND, NEVER skip git hooks.

## Step 6 — mark the candidate shipped + archive it (CAND-1)

Run this **only when a commit was actually created** (modes `--merge` / `--push`). `/reflect` left the candidate
`validated` in `candidates.json`; now that the code has landed, mark it `shipped` and move it to the archive so
the live backlog stays small (Direction #3) and a `shipped` candidate ALWAYS means committed code.

1. Read `<vault>/candidates.json`, find the candidate whose `slice` matches this slice; set its `status` to `"shipped"`.
2. Append the shipped entry to `<vault>/archive/candidates.json`:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file archive/candidates.json --array candidates --content-file shipped-candidate.json
   ```
3. Remove it from `<vault>/candidates.json` via CAS-rewrite (scratch files in a temp dir, NEVER the repo CWD —
   they'd be one `git add -A` from being committed):
   ```bash
   T="$(mktemp -d)"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read    --file candidates.json --out-file "$T/base.bin"
   # drop the shipped candidate from candidates[], write to "$T/updated.json", then:
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file candidates.json --base-file "$T/base.bin" --content-file "$T/updated.json"
   rm -rf "$T"
   # exit 3 → re-read + re-apply + retry (max 5)
   ```

(In default mode 5a — which only PRINTS the commit command — the candidate stays `validated` until the user
actually commits and re-runs `/commit-slice --merge`/`--push`.)

## Step 6.5 — auto-emit the shipped slice story (AC1 / ADR-003 — best-effort, NEVER a gate)

Run this **only in `--merge` / `--push`** (the modes where Step 6 marked the candidate shipped — no-flag default
and `--sync-after-pr` do NOT auto-emit). Now that the slice has shipped, refresh its plain-language story with the
full build / review / reality-testing / learnings folded in, into the **archived** slice folder (`/reflect`
already moved the slice to `slices/archive/slice-NNN-<name>/` — DD-20), replacing the pre-build draft.

Invoke `/slice-story slice-NNN-<name>` via the **`Skill`** tool, passing the slice id as the argument.
`/slice-story` resolves the (now archived) slice via its archive-aware by-id resolver (`active_slice.py --slice`),
regenerates `story-sections.json` + `story.html` in place (overwrite), and delivers it `proactive` (the shipped
story is the keystone deliverable — it reaches the owner's phone). The `Skill` grant is in this skill's
`allowed-tools` (line 6) and authorizes this OUTBOUND Skill call. (`/commit-slice` is model-invocable — so it can be
relayed from Remote Control — but every git state change is gated behind this skill's explicit yes/no confirmations,
NOT a frontmatter flag.) **Do NOT remove the grant.**

**Fire-and-forget — NEVER a gate (must-not-defer):**
- The commit/merge/push has ALREADY completed; the story refresh is a downstream side-effect, not part of the
  commit. Do **NOT await** the forked narrator inside the commit flow.
- If `/slice-story` errors / times out / its narrator fails: surface ONE line — _"Story refresh failed — the
  shipped story was not updated; the commit/merge/push already completed."_ — and CONTINUE. Never block, abort,
  or roll back anything on a story-refresh failure.
- This is **not** an auto-advance edge: `/commit-slice` still ends at its own hand-off; Step 6.5 is a best-effort
  flourish that emits the final story, nothing more.

`--sync-after-pr` skips this step (it generates no commit; the story was already refreshed when `--push` ran).

## Critical rules (all modes)

- NEVER fabricate content — every field sourced from a vault file.
- Missing field (e.g., no critique.json — Critic skipped on a low-tier slice with no mandatory trigger): write `Critic: skipped (low-tier, no mandatory trigger)` — never omit the line.
- CONSISTENT FORMAT — every slice commit looks the same shape (audit tools scan for these patterns).
- NEVER skip or bypass git pre-commit hooks (`/drift-check` and friends exist for a reason).
- One slice per commit — if two are ready, two commits.
- NEVER amend past commits with updated messages (commit = snapshot of what was true at commit time).
- Hand-editing generated messages is an anti-pattern; fix the vault file instead.

## Pipeline position

- predecessor: `/reflect` (user-invoked handoff — NOT an auto-advance edge)
- successor: `/slice` (next slice) or `/pulse` (re-orient). In `--merge`/`--push`, Step 6.5 also makes ONE
  best-effort OUTBOUND `Skill` call to `/slice-story <slice-id>` to emit the shipped story — fire-and-forget,
  never awaited, never a gate (ADR-003); this is the only outbound auto-invoke from `/commit-slice`.
- auto-advance: false — `/commit-slice` is never auto-invoked INTO by any skill; it is always user-triggered (the
  Step 6.5 emit is an OUTBOUND best-effort side-effect, not an auto-advance of the loop)
- user-input gates: always user-invoked; `--merge`/`--push`/`--sync-after-pr` each require explicit yes/no confirmations before any git state change; PCR-2b HARD gate uses TRI-RESOLVE-1 (AskUserQuestion, 3 options)
- on-clean-completion: after the chosen mode's git actions succeed, write `changelog.json` into the archived slice + report the result; hand back to the user for `/slice` (next) or `/pulse` (re-orient) — never auto-advances.
