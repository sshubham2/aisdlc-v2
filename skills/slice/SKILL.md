---
name: slice
description: "Define + claim the next thinnest vertical cut. Reads the ranked candidates.json backlog, recommends the next cut, claims it (worktree + branch + pick-log), writes mission-brief.json + milestone.json, then auto-advances to /risk-spike (the in-loop spike gate). Use after /reflect (or after /discover for slice 1)."
when_to_use: "Trigger phrases: /slice, 'define next slice', 'next slice', 'what should we build next'. First step of the per-slice loop."
argument-hint: "[optional slice description or hint]"
allowed-tools: Read, Grep, Glob, Bash, Write, AskUserQuestion
---

# /slice — define + claim the next cut

First step of the per-slice loop. A slice = one thin vertical cut, ≤1 day of AI work, big enough to retire risk or
ship user-visible value. You pick a candidate from the unified backlog, claim it, scaffold it, then auto-advance to
`/risk-spike`. **Candidate selection comes from `<vault>/candidates.json`** (pre-ranked, pre-materialized from
risks / diagnose findings / reflections / concept scope) — you do NOT re-run a multi-source fan-out.

## Live state — injected

Top live candidates (ranked; blocked-on-spike flagged):
```!
$PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --vault "$AI_SDLC_VAULT_ROOT" --top 5
```

Stranded-slice consult (R-26 — never define a slice on top of genuinely-stranded prior work):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json
```

- `status: divergent` (any entry `halt: true`) → **HALT** and present an `AskUserQuestion` gate naming the branch(es):
  resume via `/commit-slice`, continue its `/build-slice`, or proceed anyway (always offered).
- `status: clean` with informational entries → one-line note, **proceed** (parallel slices are normal).
- exit 2 (git unavailable) → surface stderr and continue (fail-visible, never silent).

## Step 1 — recommend (don't just list)
The injected candidates are already scored (priority.score, effort, blast_radius, blocked-on-spike) and carry a
**`couples-with`** line (Theme 4): other live candidates touching the same code area. Present a **ranked
recommendation** with a 🏆 top pick and a one-line "why this one", plus 2–4 alternatives. If `/slice
"<description>"` was invoked, evaluate that intent against the ranking — say so if it isn't strongest. If the user
says "you pick" / autonomous → take #1.

**Surface coupling when you recommend** — it changes the pick, not just decorates it:
- A pick that **`couples-with` an `[IN-FLIGHT: conflict risk]`** candidate touches the same files as a slice already
  in flight in another worktree → call it out: the two will likely **merge-conflict**. Recommend sequencing after
  that slice lands, or coordinating, rather than starting both blind.
- A pick that couples with another *pickable* candidate is a **merge opportunity** (cf. `/slice-candidates` thickness
  heuristic) — note "doing SC-X with it would share the context rebuild" so the user can choose a slightly thicker,
  coherent cut over two thin ones that fight over the same seam.

**Candidate selection is a user-input gate**: HALT for the pick unless explicit intent was supplied or the user said
"you pick".

### Step 1.5 — reserve the pick (close the selection->claim window; ADR-016)
The instant the candidate is settled (the Step-1 pick gate resolved), RESERVE it — a soft HOLD a parallel `/slice`
immediately sees as in-flight, so it can never re-pick the candidate you are about to spend Steps 2-4 defining
(the gap SC-053 closed). The reservation mints NO slice number and bumps NO counter (the number is issued only at
the Step-5.1 claim), so a later cancel costs nothing:
```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" \
    --candidate <SC-NNN> --reserve --repo-root .
```
It sets `status: reserved` / `progress: reserved` / `claimed_by` / `started_at`, is idempotent (a same-owner
re-reserve is a no-op) and identity-checked (a candidate already reserved by someone else refuses — coordinate or
pick another). Fail-visible on unset git identity (exit 1).

**`--release` the reservation on EVERY pre-claim abandon** — the hold is live from here until the Step-5.1 claim
upgrades it, so any exit before the claim must revert it (else it lingers `reserved`, invisible to other pickers):
`$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" --candidate <SC-NNN> --release`.
The pre-claim abandon exits are: a Step-2 "Not a bug" cancel · a Step-4 "too big → split" · the user re-selecting a
different candidate · abandoning the define window. (Once the Step-5.1 claim commits, the Step-5 failure ladder owns rollback.)

## Step 2 — bug-fix prelude (BFRD-1)
If the chosen candidate is a bug fix, a **failing repro test must exist first**. Treat it as a bug fix when ANY of:
- name matches `fix-*` / `*-fix` / `hotfix-*` / `harden-*-bug`; OR
- **`source.type` is `bug-hunt-finding`** — ALWAYS a defect (these come from /bug-hunt's correctness/security sweep,
  so the repro gate is mandatory); OR
- `source.type`/category otherwise identifies a defect — e.g. a `diagnose-finding` of category `correctness-bug` or
  `security` (a `diagnose-finding` of `dead-code`/`duplicate`/etc. is cleanup, NOT a bug — do not force repro); OR
- the description identifies a defect.

Check `<vault>/shippability.json` for a row whose `machine_cmd` targets `tests/bugs/*` (one that `/repro` — or
`/bug-hunt`'s own `/repro→/slice` handoff — may already have written). Two sub-cases drive Step 5:
- **Row present → STANDALONE repro** (a `/repro` run *before* `/slice` wrote the failing test to the MAIN tree,
  untracked). BFRD-1 is satisfied; **Step 5.6 relocates exactly that one named test into `$wt`** — capability-scoped
  by the row's recorded path, never a `tests/bugs/*` glob (ADR-012).
- **No row → IN-LOOP repro**: distill a one-line repro description and confirm it via an `AskUserQuestion`
  (Confirm / Modify / Not-a-bug-cancel) **now, before any worktree exists** (so a "Not a bug" cancel costs nothing).
  On Confirm/Modify → record the description for **Step 5.3** (which invokes `/repro` *after* the worktree exists, so
  the test is born inside `$wt`); on "Not a bug" → **`--release` the Step-1.5 reservation** (revert to `candidate`) and fail-closed (re-scope).

One AC must assert "the failing repro test passes at slice end". (**WT-ROOT-1 / ADR-012:** the worktree is created in
Step 5 BEFORE `/repro` runs — the in-loop test is written straight into `$wt` via `/repro --target-root`; a standalone
test already on the MAIN tree is relocated by **`repro_test_relocate.py`** to the one explicitly-named path, NEVER by
sweeping `tests/bugs/*`. This is the fix for the cross-slice repro-theft the old glob caused.)

## Step 3 — define the slice
- **Name**: verb-object (`add-receipt-upload`, `fix-thumbnail-orientation`). Never `phase-N` / vague nouns.
- **Risk tier**: `low | medium | high` (Step 3a). **Acceptance criteria** ≤5, testable. **Verification plan** per AC.
  **Must-not-defer** (auth/validation/error paths/logging — EVERY slice). **Out of scope**. Mid-slice smoke gate.

### Step 3a — risk tier (the per-slice cost lever)
Tier drives in-loop cost — the design-tournament size AND whether `/critique` runs — so pick it honestly:
`low` = pure CSS/copy/docs/test-only OR a genuinely small bug-fix / small feature; `medium` = a normal change;
`high` = novel domain / first integration / irreversible / needs extra scrutiny.

**Default tier by mode** (mode is NOT a per-slice cost lever — it only sets this default + Heavy's floor):
read `mode` from `triage.json` / `mission-brief.json` → **Minimal ⇒ default `low`** (small solo work is cheap by
default; bump up for a genuinely risky cut), **Standard ⇒ default `medium`**, **Heavy ⇒ default `medium`**. Offer
the default; let the user override.

**Always set `critic_required: true`** (even if tier=low) when the slice touches: auth/authz, new API contracts, data
model/migrations, multi-device/sync, external integrations, security paths, or the methodology surface
(`skills/**`, `agents/**`, `scripts/**`). **Heavy mode forces `critic_required: true` on EVERY slice** (its
compliance/audit floor — the Critic runs even on a low-tier Heavy slice, with sign-off). Tell the user when
low-tier still triggers the Critic and why.

### Step 3b — slice-discipline variants (the producer; 3.18.3)

Offer the three opt-in slice disciplines via ONE `AskUserQuestion` (multi-select; **default: none** — most slices
are standard). Each opted-in flag activates its own build/validate gate, so only opt in when the discipline earns
its cost. **Pre-suggest** by slice shape: a bug-fix → suggest `test_first`; a first integration / new transport →
`walking_skeleton`; an unknown-shaped area → `exploratory_charter`; otherwise none.

- **`test_first`** (TDD) — write the failing tests BEFORE the implementation. Activates TF-1 (build gate: the test
  files must exist + cover the ACs) and TPHD-1 (test-plan harmonization).
- **`walking_skeleton`** (Cockburn) — the thinnest end-to-end cut that exercises EVERY architectural layer.
  Activates WS-1, which at `/validate-slice` **actually runs** each layer's verification command (`--execute`,
  reality contact — 3.1).
- **`exploratory_charter`** — timeboxed exploration missions with recorded findings. Activates ETC-1 (each charter
  must end `completed` with findings, or `deferred` with a rationale).

Write the result into `mission-brief.json` (Step 5.3):
- Set `variants.<flag>: true` for each chosen discipline (default all `false` — a standard slice).
- **walking_skeleton chosen** → also write `architectural_layers[]`: one row per layer the cut spans (`layer`,
  `component`, `verification` as a **runnable command** — like a shippability `machine_cmd`, since WS-1 `--execute`
  runs it — `status: "pending"`). Draft the standard tiers (API / service / data / UI) the slice touches; build
  fills the exact commands.
- **exploratory_charter chosen** → also write `exploratory_charters[]`: one row per mission (`mission`, `timebox`,
  `status: "pending"`, `findings: ""`).
- **test_first chosen** → no extra field; the ACs' tests are written first and TF-1 verifies them at build.

Shapes (omitted from the standard mission-brief example — present only when opted in; `artifact_lint` validates
their `status` enums when present): `architectural_layers[]` = `{layer, component, verification (a runnable
command), status: "pending"|"exercised"}` (WS-1 docstring); `exploratory_charters[]` = `{mission, timebox,
status: "pending"|"in-progress"|"completed"|"deferred", findings}` (ETC-1 docstring).

## Step 4 — scope check
≤5 ACs, ≤1 day, system stays shippable. If it exceeds → **`--release` the Step-1.5 reservation** and split (the original SC-NNN returns to the pickable backlog; the sub-slices are picked fresh).

## Step 5 — claim + scaffold (BRANCH-3 worktree-at-pick; ADR-012 worktree-first repro ordering)
Once the candidate is settled AND scope passes, in THIS order. The sequence is load-bearing: a bug-fix repro is born
inside `$wt` (or relocated by the one explicit path), never grabbed from the main tree by a glob.

1. **Claim FIRST — mint the slice number in-lock** (reserve-then-scaffold; [[ADR-013]]). The model NEVER
   computes a slice number — `claim_candidate` mints it inside the locked claim and returns it:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" \
       --candidate <SC-NNN> --name <verb-object> --json
   ```
   In ONE locked read-modify-write it bumps `counters.slice`, allocates `slice-NNN`, sets the candidate
   `status: spiking` / `progress: spike` / `claimed_by {git_user, git_email}` / `started_at` / `slice`, appends
   the `pick_log` entry, and RETURNS `{"slice": "slice-NNN", "folder": "slice-NNN-<name>"}`. Read `folder` for all
   paths below. Fail-visible on unset git identity (exit 1). This is the **CONFIRM** phase of the two-phase claim
   (ADR-016): if the candidate was reserved at Step 1.5 (the normal path) the same locked write UPGRADES the
   reservation `reserved → spiking` — **same-owner only**; a reservation held by a different git identity refuses
   (exit 1, no slice number minted). A fresh `candidate`/`deferred` pick (no prior reservation) still claims directly.
2. **Compute paths** from the RETURNED `folder`: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder <folder> --repo-root .` (line 1 = `$wt` path, line 2 = `slice/NNN-<name>`).
3. **Resolve the integration base + create the worktree** (slice-022: slices branch off the integration branch `uat`, degrading visibly to the default trunk when uat is absent — never a hardcoded master):
   ```bash
   base=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root <main>)
   [ -z "$base" ] && { echo "STOP: cannot resolve the integration branch (git unusable)." >&2; exit 2; }
   git -C <main> worktree add <wt_path> -b slice/NNN-<name> "$base"
   ```
   **Failure → wrapper-enforced compensation**: `$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --vault "$AI_SDLC_VAULT_ROOT" --candidate <SC-NNN> --release` (reverts the claim to `candidate`; the counter is NOT decremented — monotonic-burn), then **STOP**.
4. **In-loop repro (Step 2 "No row" path only)**: if BFRD-1 confirmed an in-loop repro is needed, invoke
   `/repro "<desc>" --target-root=<wt_path>` once via the Skill tool. `/repro` writes the failing test under
   `<wt_path>/tests/bugs/` (born on the slice branch) and confirms it fails there — **no relocation needed**.
   (Non-bug-fix, or a standalone row already present → skip; that case is handled at 6.)
5. **Write the scaffold** to the **external vault store**:
   - `<vault>/slices/<folder>/mission-brief.json` (schema: `examples/mission-brief.json`)
   - `<vault>/slices/<folder>/milestone.json` (schema: `examples/milestone.json`; `stage: "spike"`, `next_action: "/risk-spike"`)
   Failure → `--release` the claim (step 3) + `git -C <main> worktree remove <wt_path>`, then STOP.
6. **Standalone repro relocation (Step 2 "Row present" path)** — a `/repro` ran *before* `/slice` and left the
   failing test untracked on the MAIN tree. Relocate the ONE test named by its shippability row into `$wt` —
   **capability-scoped, NEVER a `tests/bugs/*` glob** (ADR-012; the glob caused cross-slice theft). Derive the grant
   from the row(s) by reusing `shippability_path_audit._extract_test_tokens` (it strips `-q`/`::selector`), keep only
   the ones still untracked on the main tree:
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   cand=$($PY -c "
   import sys, json, subprocess
   sys.path.insert(0, 'skills/validate-slice/scripts')
   from shippability_path_audit import _extract_test_tokens
   rows = json.load(open('$AI_SDLC_VAULT_ROOT/shippability.json')).get('rows', [])
   paths = {t for r in rows for t,_ in _extract_test_tokens(r.get('machine_cmd','')) if t.startswith('tests/bugs/')}
   def untracked(p):  # scoped to the ONE grant path p -- never globs the whole tests/bugs/ folder
       r = subprocess.run(['git','-C','$repo_root','ls-files','--others','--exclude-standard','--',p],capture_output=True,text=True)
       return bool(r.stdout.strip())
   print('\n'.join(sorted(p for p in paths if untracked(p))))
   ")
   ```
   - **0 candidates** → nothing to relocate (in-loop repro already wrote into `$wt`, or non-bug-fix) → skip.
   - **>1 candidate** (parallel standalone repros) → `AskUserQuestion` listing the paths; the user picks THIS slice's.
   - For the chosen `<test-path>`, relocate exactly it (re-derive `repo_root` — a fresh bash block; vars do not
     carry over, BC-PROJ-2):
     ```bash
     repo_root="$(git rev-parse --show-toplevel)"
     $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/repro_test_relocate.py" \
         --slice-folder slice-NNN-<name> --repo-root "$repo_root" --test-path "<test-path>"
     ```
     The helper moves ONLY that file into `$wt` + stages it; it never enumerates `tests/bugs/`, so a sibling slice's
     untracked test is structurally safe. It exits non-zero + visibly on a missing worktree / missing named source /
     git-ignored path / stage failure (never a silent partial).

**Failure ladder (must-not-defer #1 — no silent partial scaffold; claim-first, so the claim is the FIRST committed state and the compensation is wrapper-enforced, not skippable prose):**
- claim (1) fails → **STOP**; nothing else ran (no worktree, no scaffold). If a Step-1.5 reservation is live, `--release` it — the claim/upgrade did not commit, so the hold must not linger `reserved`.
- worktree-add (3) fails → **wrapper-enforced compensation**: `claim_candidate.py --candidate <SC-NNN> --release` (revert the claim; counter not decremented — monotonic-burn), then STOP. No orphaned reservation.
- `/repro` (4) aborts, or its "test unexpectedly passes" recovery fires → `--release` the claim + `git -C <main> worktree remove <wt_path>` + `git branch -D slice/NNN-<name>`; leave no scaffold.
- scaffold (5) fails → `--release` the claim + roll the worktree back the same way.

The BFRD-1 Confirm/Not-a-bug gate (Step 2) runs BEFORE the Step-2 worktree-add, so cancelling a candidate as "Not a
bug" never orphans a worktree.

## Critical rules
- ASK before deciding the slice (unless explicit intent / "you pick"). ENFORCE scope (>1 day → split).
- VERB-OBJECT names only. INCLUDE must-not-defer EVERY slice. Do NOT design here — that's `/design-slice`.

## Pipeline position
- predecessor: `/reflect` (or `/discover` for slice 1) · successor: **`/risk-spike`** · auto-advance: true
- on-clean-completion: once the scaffold is written, the candidate is claimed, and a candidate was settled,
  invoke **`/risk-spike`** via the Skill tool — its in-loop spike gate must pass before `/design-slice`.
- user-input gates (halt auto-advance): candidate selection (Step 1); BFRD-1 confirm (Step 2); slice-discipline variants (Step 3b — one quick multi-select, default none).
