---
name: commit-slice
description: "Generate an audit-grade conventional commit message for a just-completed slice by reading vault artifacts (mission-brief.json, build-log.json, validation.json, reflection.json, critique.json, ADRs, shippability.json). Dispatches message rendering to a Haiku subagent (COST-1). Supports three mutually exclusive modes: --merge (solo-dev local merge + safe-delete), --push (push slice branch + display PR hint), --sync-after-pr (post-PR local cleanup). No-flag default: generate and show only. Also writes a per-slice changelog.json audit record into the archived slice folder (Step 4.5); never writes to the code repo root."
when_to_use: "Trigger phrases: /commit-slice, 'generate commit message', 'audit commit', 'slice commit message', '/commit-slice --merge', '/commit-slice --push', '/commit-slice --sync-after-pr'. Run after /reflect (which archives the slice). User-invoked only — never auto-advanced into."
argument-hint: "[--merge | --push | --sync-after-pr]"
allowed-tools: Read, Grep, Glob, Bash, Write, Agent, AskUserQuestion
disable-model-invocation: true
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
- **`--merge`** — commit on slice branch → PSQ-3 rebase → no-ff merge into default → safe-delete (solo-dev / no-protected-branch path)
- **`--push`** — commit on slice branch → `git push -u origin slice/NNN-<name>` → display `gh pr create` hint (PR-based workflow; never merges locally)
- **`--sync-after-pr`** — post-PR local cleanup only (skips Steps 1–4); two-signal merged-state detection → checkout default → pull → safe-delete

If two or more flags passed: STOP — "Mode flags `--merge`, `--push`, `--sync-after-pr` are mutually exclusive; pass exactly one (or none for generate-only default)."

## Step 1 — identify target slice

Default: most recently archived slice (highest `slice-NNN` under `<vault>/slices/archive/`).
`--sync-after-pr`: current `slice/*` branch (no archive lookup needed).
If multiple uncommitted slices exist under `--merge`: ask user which to commit.

Prerequisite: archived slice folder has `reflection.json` (slice completed) — or active slice has `build-log.json` for mid-slice commits. If neither: STOP, tell user to run `/reflect` first.

## Step 2 — read vault artifacts

From `<vault>/slices/archive/slice-NNN-<name>/` (or active slice folder for mid-slice):

- `mission-brief.json` → intent (one-line), AC count
- `critique.json` (if present) → blocker count + addressed status
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

## Step 4 — generate commit message via Haiku dispatch

Per **COST-1**: dispatch to a Haiku subagent via the Agent tool (`model: haiku`). Hand the agent:
- Input dict from Step 2: `{type, scope, slice_id, slice_path, intent_one_line, body_2_3_sentences, ac_pass, ac_total, critic_blockers, adrs, shippability_entry_n, shippability_entry_text, deferrals, regressions}`
- The template below as fill spec

The main thread gathers input (Step 2) and executes (Step 5); Haiku fills the template.

**Commit message template:**
```
<type>(<scope>): slice-NNN — <one-line intent from mission-brief.json>

<body: what was built / changed, 2–3 sentences>

Slice: [slice-NNN-<name>](<vault>/slices/archive/slice-NNN-<name>/)
Acceptance criteria: <X>/<Y> PASS (see validation.json)
Critic blockers addressed: <list or "none">
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

**Minimal mode** (no critique): `Critic: skipped (Minimal mode)` (never omit the line).

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

2.5. **PSQ-3 rebase** (ADR-068): resolve default branch:
   ```bash
   default=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
   [ -z "$default" ] && default=$(git config init.defaultBranch 2>/dev/null)
   ```
   STOP exit 2 if neither resolves (NAW-1). Run `git rebase <default>` on the slice branch. Outcomes:
   - **Fast-forward no-op** or **clean replay**: proceed to sub-step 3.
   - **Conflict**: STOP — do NOT proceed to sub-step 3. Print conflicting U-files + `git rebase --abort` hint. Then:

     **PCR dispatch**: run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --resolve-soft --json`.
     In v2 the vault is an external, untracked store, so a vault file can NEVER be a rebase stage — every rebase
     conflict is a CODE conflict. `--resolve-soft` therefore NEVER auto-resolves; it only classifies and routes:
     - `exit 0` + `action: STOP` + `conflict_class: HARD` (any unmerged path): enter **PCR-2b gate** (below).
     - `exit 0` + `action: STOP` + `conflict_class: UNKNOWN` (no unmerged paths / unreadable rebase state): fall through to SOAD-1 block.
     - `exit 1/2`: print stderr verbatim; fall through to SOAD-1 block.

     **PCR-2b HARD gate** (ADR-075 / TRI-RESOLVE-1):
     1. Bootstrap guard: if resolver unavailable → fall through to SOAD-1 (no weaker than pre-PCR-2b).
     2. Print `--diagnose --json` output. For `_index.json`-sole HARD: print hint "resolve by re-running `/archive` to regenerate, then `git add`".
     3. User (or Claude at user's instruction) resolves conflict markers + `git add`s each U-file.
     4. Run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --verify-resolution --json`.
        - `exit 1` (`git-state-unreadable`): fall through to SOAD-1 abort path; do NOT loop to step 3.
        - `exit 0` + `action: STOP` (`paths-still-unmerged` or `unresolved-markers-present` — keyed on `<<<<<<<`/`>>>>>>>` openers, NOT `=======`): print reason; return to step 3 (or offer Abort).
        - `exit 0` + `action: CLEAN`: proceed to step 5.
     5. Spawn `code-review` agent via Agent tool with the resolved diff (`git diff --cached` of U-file set), `--diagnose --json` context, and both rebase stages (`git show :2:<path>` / `git show :3:<path>`). Task: review for lost-hunk, dropped-side, both-sides-intent, semantic correctness, stray markers, vault/ADR contradiction. Capture verdict + findings INLINE (do NOT write `code-review.json`; do NOT run triage/critique audits; do NOT call `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --verify-resolution --json` a second time). Any blocker → verdict BLOCKED.
     6. **TRI-RESOLVE-1 gate** (AskUserQuestion — 3 options):
        - **Apply resolution (continue rebase)** — offered ONLY when code-review verdict has no blocker.
        - **Re-resolve (edit again)** — return to step 3.
        - **Abort rebase** — fall through to SOAD-1 (a) abort path.
        Fail-closed: every non-Apply option and any interrupt → STOP-no-continue. NEVER call `git rebase --continue` except on explicit Apply with non-blocking verdict.
     7. On Apply: run `$PY "${CLAUDE_SKILL_DIR}/scripts/parallel_conflict_resolver.py" --record-hard-resolution --verdict "<verdict>" --disposition apply --json` (best-effort audit — failure logs to stderr, does NOT block), THEN `git rebase --continue`, THEN append PCR-2b breadcrumb to `build-log.json` Events (`<ts> PCR-2b HARD resolved + applied — see <vault>/parallel-conflict-resolution-log.json`). Order load-bearing: record BEFORE `--continue`.

     **SOAD-1 block** (3-option structured ask via AskUserQuestion):
     - (a) Abort rebase + investigate: print `git rebase --abort` recovery hint.
     - (b) Resolve manually + `git rebase --continue` outside skill, then re-invoke `/commit-slice --merge`.
     - (c) Cancel merge entirely: print `git rebase --abort` hint; skill exits cleanly.

   **Non-conflict rebase failure** (detached HEAD, broken ref): STOP with `git rebase --abort` hint + git stderr verbatim.

3. **Switch to main tree + merge** (BRANCH-2 worktree collision fix): resolve main tree path:
   ```bash
   main_tree=$(git worktree list --porcelain | awk '/^worktree / {print $2; exit}')
   ```
   If empty: STOP — "main-tree-unresolvable." `cd "$main_tree"`. Resolve default (same helper as sub-step 2.5). `git checkout $default` + `git merge --no-ff slice/NNN-<name> -m "Merge slice/NNN-<name>: <intent>"`. If conflict: STOP with manual resolution hint (no recovery flow in v1).

4. Confirm: "Confirm merge + delete? (yes/no)" — on no: ABORT cleanly, leave merged branch intact.

5. **Idempotent worktree-remove** (BRANCH-2 / ADR-063): check `git worktree list --porcelain | grep "^branch refs/heads/slice/NNN-<name>$"`. If empty: LOG skip to `build-log.json` Events; proceed. Else: extract `wt_path` via awk + `git worktree remove "$wt_path"`. On refuse: STOP with hint.

6. `git branch -d slice/NNN-<name>` (safe-delete ONLY; NEVER `-D`). If refused: STOP — "Safe-delete refused. Inspect with `git log <default>..slice/NNN-<name>`. Do NOT use `-D` without understanding what's being discarded." Order load-bearing: worktree-remove MUST precede branch-delete.

7. Show `git log -1` + `git log --graph --oneline -5`.

**Critical rules — `--merge`**: NEVER `git push`, NEVER `-D`, NEVER auto-resolve conflicts, NEVER `--no-verify`.

### 5c — `--push` (ADR-020 / PR-based workflow)

**Pre-flight:**
1. WT-clean: `git status --porcelain` must be empty.
2. Stale-branch check: identical to 5b pre-flight check above.
3. Current branch must start with `slice/`: `git symbolic-ref --short HEAD`. Otherwise STOP.
4. Origin remote: `git remote get-url origin` must succeed. Otherwise STOP.

**Push flow:**
1. Show message + `git status`.
2. Confirm commit on slice branch (yes/no) → `git add` + `git commit -m "..."`.
3. Confirm push to `origin/<slice-branch>` (yes/no) → `git push -u origin slice/NNN-<name>`. NEVER `--force` or `--force-with-lease`.
   - Non-ff push (remote diverged): STOP with manual force-push hint.
   - Fast-forward re-push (remote ahead): ask explicit confirmation before proceeding.
4. Display PR hint:
   - GitHub.com remote: `gh pr create --base <default> --head slice/NNN-<name> --web` + `https://github.com/OWNER/REPO/compare/<default>...slice/NNN-<name>`
   - Non-GitHub.com: `gh pr create` command + note "Or open PR via your hosting UI."
5. Show `git log -1 origin/slice/NNN-<name>`.

**What `--push` never does**: checkout default, merge locally, delete any branch, create the PR.
**Critical rules — `--push`**: NEVER `--force`, NEVER auto-create PR, NEVER push to non-`origin`, NEVER `--no-verify`.

### 5d — `--sync-after-pr` (ADR-020 / post-PR local cleanup)

Skips Steps 1–4 (no commit generated).

**Pre-flight:**
1. WT-clean: `git status --porcelain` must be empty.
2. Current branch must start with `slice/`.
3. Origin remote present.
4. Slice branch has upstream tracking (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` must succeed) — else STOP: "No upstream — was `--push` run?"

**Cleanup flow:**
1. `git fetch --prune origin <default> slice/NNN-<name>` (explicit refspec required for Signal B).
2. Resolve default (same helper as 5b/5c).
3. **Two-signal merged-state detection**:
   - **Signal A**: `git ls-remote --exit-code origin slice/NNN-<name>` returns non-zero (remote absent).
   - **Signal B** (two-pass):
     - Pass 1: `git cherry origin/<default> slice/NNN-<name>` → no `+` lines → YES.
     - Pass 2 (fallback when Pass 1 has `+` lines): compute `BASE=$(git merge-base origin/<default> slice/NNN-<name>)` + `FILES=$(git diff --name-only BASE..slice/NNN-<name>)`.
       - Empty-FILES guard: if FILES empty → Signal B = NO; STOP with diagnostic.
       - Perf bound: if `BASE..origin/<default>` > 500 commits → STOP with diagnostic.
       - Predicate: any commit C on `BASE..origin/<default>` whose touched-file set ⊇ FILES AND tree-state at FILES paths equals `slice/NNN-<name>^{tree}` → Signal B = YES (squash-merge detected).
   - Both signals must be YES to proceed.
   - Signal A=NO: STOP — "Remote slice branch still exists. PR may be unmerged."
   - Signal B=NO: STOP — "Slice commits not on `origin/<default>`. Re-run after PR merges."
4. Confirm: "PR appears merged + remote-deleted. Confirm local cleanup (checkout `<default>` + pull --ff-only + safe-delete `slice/NNN-<name>`)? (yes/no)"
5. On yes: `git checkout <default>` → `git pull --ff-only origin <default>` (NEVER bare `git pull`) → idempotent worktree-remove guard (identical to 5b sub-step 5) → `git branch -d slice/NNN-<name>` (safe-delete; order load-bearing).
6. Show `git log -1` + `git log --graph --oneline -5`.

**Critical rules — `--sync-after-pr`**: NEVER `-D`, NEVER omit `--ff-only`, NEVER omit explicit fetch refspec, NEVER skip both-signal AND, NEVER `--no-verify`.

## Critical rules (all modes)

- NEVER fabricate content — every field sourced from a vault file.
- Missing field (e.g., no critique.json in Minimal): write `Critic: skipped (Minimal mode)` — never omit the line.
- CONSISTENT FORMAT — every slice commit looks the same shape (audit tools scan for these patterns).
- NEVER `--no-verify` (pre-commit hooks like `/drift-check` exist for a reason).
- One slice per commit — if two are ready, two commits.
- NEVER amend past commits with updated messages (commit = snapshot of what was true at commit time).
- Hand-editing generated messages is an anti-pattern; fix the vault file instead.

## Pipeline position

- predecessor: `/reflect` (user-invoked handoff — NOT an auto-advance edge)
- successor: `/slice` (next slice) or `/pulse` (re-orient)
- auto-advance: false — `/commit-slice` is never auto-invoked by any skill; it is always user-triggered
- user-input gates: always user-invoked; `--merge`/`--push`/`--sync-after-pr` each require explicit yes/no confirmations before any git state change; PCR-2b HARD gate uses TRI-RESOLVE-1 (AskUserQuestion, 3 options)
