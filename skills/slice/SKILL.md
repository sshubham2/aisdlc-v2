---
name: slice
description: "Define + claim the next thinnest vertical cut. Reads the ranked candidates.json backlog, recommends the next cut, claims it (worktree + branch + pick-log), writes mission-brief.json + milestone.json, then auto-advances to /risk-spike (the in-loop spike gate). Use after /reflect (or after /discover for slice 1)."
when_to_use: "Trigger phrases: /slice, 'define next slice', 'next slice', 'what should we build next'. First step of the per-slice loop."
argument-hint: "[optional slice description or hint]"
allowed-tools: Read, Grep, Glob, Bash, Write, AskUserQuestion, Skill
---

# /slice — define + claim the next cut

First step of the per-slice loop. A slice = one thin vertical cut, ≤1 day of AI work, big enough to retire risk or
ship user-visible value. You pick a candidate from the unified backlog, claim it, scaffold it, then auto-advance to
`/risk-spike`. **Candidate selection comes from `<vault>/candidates.json`** (pre-ranked; materialized from
risks / diagnose findings / reflections, and — once `/slice-candidates --product` has run — the **product's own
scope**) — you do NOT re-run a multi-source fan-out.

**`/slice`'s PICK PHASE is READ-ONLY** ([[ADR-067]] section 1, as scoped by [[ADR-080]] + [[ADR-152]]). It takes
no lock, mutates no vault file, and cannot be bricked by a parallel writer. The scope is honest about where the
claim ends: the POST-CLAIM writes at Steps 5.1 / 5.5 / 5.7 are unchanged and out of that section's scope. Product
scope is materialized in the ONCE-ACT — `/slice-candidates --product` — not by a per-pick reconciler tick, and the
completion gate below ROUTES rather than writing.

## Live state — injected

Top live candidates (ranked; blocked-on-spike flagged) + the app-completion verdict:
```!
$PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --top 5 --completion-gap
```

Product-scope presence (read-only backstop — slice-068):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" census --json
```

Product shape — per-AREA capability progress + OPTIONAL area lens (read-only, slice-080 / [[ADR-091]]; slice-084 renamed component→area):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_rollup.py" --json 2>/dev/null || echo '{"scope_present":false}'
```

The rollup envelope carries an `areas` array ranked **least-complete-first** (`{name, done, total, rank}`)
plus the mandatory `unassigned / cross-cutting` bucket — a **priority-ranked product-area list** for orienting the
pick. **If the user invoked `/slice --area <NAME>`** (or otherwise wants the pick surface scoped to ONE product
area), re-run the candidate digest filtered to it and recommend from that filtered set:
`$PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --top 5 --area <NAME> --completion-gap` (use `unassigned`
for PRODUCT capabilities not yet grouped into an area). The flag rides the filtered re-run too, and the WHOLE
completion population — `pickable_product[]`, `unbuilt[]`, `done`, `total` and the headline — is then computed
over that SAME filtered set, with the headline labelled area-scoped, so `done/total` legitimately differ between
`/slice` and `/slice --area <NAME>` on one vault. The population is every candidate with an **area SOURCE** (slice-098 /
[[ADR-125]] section 1): one that **asserts** the area itself via its own `area` field, or a product capability
bound to it through `owner_refs`. An **un-annotated** pipeline chore has no area source and stays out entirely,
so slice-084 A1's anti-conflation still holds; and an annotated candidate can never land in `unassigned` (the
write seams refuse the sentinel). Each pick renders its provenance — `area: <NAME> (via candidate)` vs
`(via product-scope)` — because a candidate's own area **overrides** the one derived from its parent and can
therefore mask a mis-parenting ([[ADR-124]] section 1). A `near_matches` WARN means the area name is split across
two spellings. To annotate a candidate, use the seam documented in `/slice-candidates` (product-6). This is a
**LENS, not a lock** — it filters the PICKABLE list only (blocked/in-flight stay global), takes no lock, mints no
id, writes no status/ownership. `--component <NAME>` stays as a back-compat alias.
Default-OFF (no `--area`) is byte-identical to the digest above. **If the rollup envelope carries a non-empty
`governor` string** (the product's scope is decomposed but 0 of its capabilities are built — slice-084 B4),
surface it as a WARN and **bias the recommendation toward a product-sourced capability** (`/slice --area
<NAME>`, or any candidate whose `source.type == product-scope`) over more pipeline instrumentation. The governor
is descriptive: it changes no ranking and blocks no pick. **OMIT the governor WARN entirely when the completion
gate returned `suppress_governor: true`** (slice-102 / SC-232): at `done == 0` with no pickable product candidate
BOTH computations fire, and two contradictory instructions at one surface is how a user learns to ignore the
surface. The gate's route is the specific one, so the descriptive governor yields — exactly ONE instruction
reaches the pick. Omit the product-area surface entirely when `scope_present` is false (an un-decomposed product).

Stranded-slice consult (R-26 — never define a slice on top of genuinely-stranded prior work):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/stranded_slice_audit.py" --repo-root . --json
```

- `status: divergent` (any entry `halt: true`) → **HALT** and present an `AskUserQuestion` gate naming the branch(es):
  resume via `/commit-slice`, continue its `/build-slice`, or proceed anyway (always offered).
- `status: clean` with informational entries → one-line note, **proceed** (parallel slices are normal).
- exit 2 (git unavailable) → surface stderr and continue (fail-visible, never silent).

**Product-scope NOTICE (read-only; never blocks the pick).** If the census reports `counts.PRODUCT == 0`, print
ONE terse line and proceed:

> The product's own scope is not represented in this backlog — every candidate is exhaust (risks, findings,
> reflections) or hand-typed. Run `/discover` (if `concept.json` is missing), then `/slice-candidates --product`.

Why this notice exists and is **not** suppressible: across two real vaults, PRODUCT-sourced candidates were **0 of
145 ever minted** — one product's orchestrator, the thing it exists to be, was never a candidate at all, so eleven
slices went to peripheral hardening while the core app stayed unbuilt. An opt-out switch here would institutionalize
"the product's scope is absent and nobody is told," which is precisely the state that produced that number. It is a
BACKSTOP — the primary path is `/discover`'s hand-off. Print it once, only when `PRODUCT == 0`, and never let it
block a pick (a non-zero exit from the census is advisory: note it and proceed).

## Step 1 — recommend (don't just list)

### Step 1.0 — the completion gate: RENDER the returned decision, derive nothing (slice-102 / SC-232)

The injected digest carries a `COMPLETION GAP` block (and `completion_gap` in `--json`). **Read `recommendation`
and render it. Do NOT re-derive the verdict, and NEVER re-rank to find the pick** — a score-5 product mint
measures ~11th of 117 on a real vault, so re-ranking silently loses it.

Why this gate exists: `/slice-candidates --product` decomposes the product's scope **exactly once** by design, so
an EXHAUSTED scope is the steady state of every project past its initial capability list. In that state the
backlog is pure pipeline exhaust and nothing tells you. Measured on a real vault: 61 of 64 pickable candidates
were exhaust while the product sat at 12/14 — and every existing backstop stayed silent (the `PRODUCT == 0`
census notice does not fire when PRODUCT is non-zero; the completeness governor fires only at 0-built).

| `recommendation.mode` | what you do |
|---|---|
| `product-pick` | Recommend `pick_id` **by identity** as the 🏆 top pick, using the hoisted `Completion pick` row (it carries the row's REAL rank and any `[deps-unmet: …]`). Alternatives come from the ordinary ranked list. |
| `route-add-item` | **HALT.** Every declared capability is built, so there is no app-completion work left to pick. Render the headline + the honest `done_definition` + the unbuilt list, then tell the user to run **`/slice-candidates --add-item`** (it elicits, previews and confirms before anything is minted). Do not elicit the capability here, do not stage a payload, do not print a command, and write nothing. |
| `route-discover` | **HALT.** No product scope exists at all → run `/discover` (if `concept.json` is missing), then `/slice-candidates --product`. Never the one-item route: the bulk decomposition is what this project is missing. |
| `route-materialize` | **HALT.** A capability has no children at all → render the returned `rationale`, which names the idempotent remedy. |
| `route-coordinate` | **HALT.** Every unbuilt capability is already in flight → coordinate with its owner rather than starting a parallel cut on the same capability. |
| `route-repair` | **HALT.** A capability is in an UNKNOWN state (a child claims two parents, or its provenance is torn) → render the returned `rationale`; repair the provenance, then re-run `/slice`. |
| `route-rescope` | **HALT.** A capability was KILLED (archived children all rejected) → it needs a re-decision, not a materialize. Render the returned `rationale`. |
| `headline-only` | Render the headline; raise no question. |

**The decline is ALWAYS offered and always returns the ordinary ranked pick** (`recommendation.offer_decline` is
`true` on every halting mode). Say so at the halt, in the same breath as the route. A gate that cannot be
declined is a lock on the user's own backlog, and this one is deliberately not that.

**If the block reports `UNDECIDABLE`** (a verdict-less payload carrying `error` + `cause_kind`): surface the
named cause and proceed with the ordinary ranked pick. The digest below it is still correct — only the
completeness verdict is withheld. Never read an undecidable result as "this project has no product".

**Explicit intent.** When `/slice "<description>"` supplied intent, re-run the digest with `--explicit-intent` so
the gate renders its headline and raises no question. The Step-1 injection above runs at skill-LOAD and cannot
see arguments (SC-064 / [[ADR-022]]), so this is a bash BODY block.

**The trigger is a NON-FLAG argument, never "any argument".** `/slice --area <NAME>` is a documented invocation
of this same skill, so `${ARGUMENTS[0]}` is routinely the literal `--area` — a bare `[ -n "$ARG" ]` would fire on
it, silently return `headline-only`, and disable the routing on the whole area-scoped pick path. The scan below
is the Step-0 non-flag idiom `/slice-candidates` already uses, extended to SKIP the value token of a
value-taking flag (`--area payments` must not read `payments` as a description):
```bash
HAS_INTENT=0; SKIP=0
for a in ${ARGUMENTS[@]}; do
  if [ "$SKIP" = 1 ]; then SKIP=0; continue; fi
  case "$a" in --area|--component) SKIP=1 ;; --*) ;; *) HAS_INTENT=1 ;; esac
done
if [ "$HAS_INTENT" = 1 ]; then
  $PY "${CLAUDE_SKILL_DIR}/scripts/candidates_top.py" --top 5 --completion-gap --explicit-intent
fi
```
`HAS_INTENT` is a **flag, not the text**: `${ARGUMENTS[@]}` word-splits, so a quoted description arrives as
several tokens and no single one is "the description". `--explicit-intent` is boolean — the gate only needs to
know that intent WAS supplied; you already have the description itself from the invocation.
Run this re-run **at most once**, and render exactly ONE `recommendation.mode`: the `--explicit-intent` result
when it fired, otherwise the injected one. Two mode renders on one surface is the same "contradictory
instructions" failure `suppress_governor` exists to prevent.

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

**Stale holds**: if the injected In-flight list flags a `[STALE HOLD]` (a `reserved` hold older than 24h that never
upgraded to a claim), surface it before recommending. A stale hold owned by the CURRENT git identity is usually an
abandoned pre-claim pick — offer to `--release` it (returns the candidate to the pickable pool); a hold owned by
someone else needs coordination, never a force-release.

**Candidate selection is a user-input gate**: HALT for the pick unless explicit intent was supplied or the user said
"you pick".

### Step 1.5 — reserve the pick (close the selection->claim window; ADR-016)
The instant the candidate is settled (the Step-1 pick gate resolved), RESERVE it — a soft HOLD a parallel `/slice`
**reading the same `candidates.json`** immediately sees as in-flight, so it can never re-pick the candidate you are
about to spend Steps 2-4 defining.
The reservation mints NO slice number and bumps NO counter (the number is issued only at
the Step-5.1 claim), so a later cancel costs nothing:
```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --candidate <SC-NNN> --reserve --repo-root .
```
(No `--vault` flag: 4.6.1 — `AI_SDLC_VAULT_ROOT` is no longer exported; the script resolves the
vault internally, which is the actual mechanism. Same for every `claim_candidate`/`candidates_top`
call in this skill.)
It sets `status: reserved` / `progress: reserved` / `claimed_by` / `started_at`, is idempotent (a same-owner
re-reserve is a no-op) and identity-checked (a candidate already reserved by someone else refuses — coordinate or
pick another). Fail-visible on unset git identity (exit 1).

> **SCOPE of the reserve promise (slice-100 / [[ADR-131]] decision 4).** The hold lives in
> `candidates.json` ONLY — `--reserve` never touches the shared claim register. So the promise above holds
> for pickers sharing ONE `candidates.json` (a local vault, or the `local` claim backend on one filesystem)
> and does NOT hold ACROSS MACHINES on a git-synced vault, where each peer works from its own copy between
> explicit `vault_admin sync` calls: two developers can both reserve and both spend the whole define phase,
> and only one loses at the Step-5.1 claim, which IS cross-machine safe. Extending the register to
> `--reserve` was deliberately rejected — an immortal team-global ref for a soft, abandonable hold turns
> every abandoned pre-claim pick into a permanent lockout, strictly worse than the gap. A shared soft-hold
> needs a releasable, EXPIRING register: a separate cut.

**`--release` the reservation on EVERY pre-claim abandon** — the hold is live from here until the Step-5.1 claim
upgrades it, so any exit before the claim must revert it (else it lingers `reserved`, invisible to other pickers):
`$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --candidate <SC-NNN> --release`.
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
  On Confirm/Modify → record the description for **Step 5.4** (which invokes `/repro` *after* the worktree exists, so
  the test is born inside `$wt`); on "Not a bug" → **`--release` the Step-1.5 reservation** (revert to `candidate`) and fail-closed (re-scope).

One AC must assert "the failing repro test passes at slice end". (**WT-ROOT-1 / ADR-012:** the worktree is created in
Step 5 BEFORE `/repro` runs — the in-loop test is written straight into `$wt` via `/repro --target-root`; a standalone
test already on the MAIN tree is relocated by **`repro_test_relocate.py`** to the one explicitly-named path, NEVER by
sweeping `tests/bugs/*`.)

## Step 3 — define the slice
- **Name**: verb-object (`add-receipt-upload`, `fix-thumbnail-orientation`). Never `phase-N` / vague nouns.
- **Risk tier**: `low | medium | high` (Step 3a). **Acceptance criteria** ≤5, testable. **Verification plan** per AC.
  **Must-not-defer** (auth/validation/error paths/logging — EVERY slice). **Out of scope**. Mid-slice smoke gate.

### Step 3a — risk tier (the per-slice cost lever)
Tier drives in-loop cost — whether `/critique` runs (the design tournament runs all 3 designers on every slice regardless of tier; ADR-018) — so pick it honestly:
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

- **`test_first`** (TDD) — write the failing tests BEFORE the implementation. Activates TF-1 (the `/build-slice` pre-finish
  gate) and TPHD-1 (test-plan harmonization). The producer **scaffolds a PENDING `test_first_plan[]` stub at `/slice`**
  (Step 5.5 runs `scaffold_test_first_plan.py`), one `{ac, status: "PENDING", test_path, test_function}` row per AC. The
  builder then fills each row's `test_path`/`test_function` and walks it to PASSING — the test bodies are **authored by the
  builder at build** time, because the test functions do not exist yet at `/slice`. TF-1 requires **one PASSING row per AC**,
  each pointing at a real on-disk test (the PENDING stub is a head start, never a gate bypass).
- **`walking_skeleton`** (Cockburn) — the thinnest end-to-end cut that exercises EVERY architectural layer.
  Activates WS-1, which at `/validate-slice` **actually runs** each layer's verification command (`--execute`,
  reality contact — 3.1).
- **`exploratory_charter`** — timeboxed exploration missions with recorded findings. Activates ETC-1 (each charter
  must end `completed` with findings, or `deferred` with a rationale).

Write the result into `mission-brief.json` (Step 5.5):
- Set `variants.<flag>: true` for each chosen discipline (default all `false` — a standard slice).
- **walking_skeleton chosen** → also write `architectural_layers[]`: one row per layer the cut spans (`layer`,
  `component`, `verification` as a **runnable command** — like a shippability `machine_cmd`, since WS-1 `--execute`
  runs it — `status: "pending"`). Draft the standard tiers (API / service / data / UI) the slice touches; build
  fills the exact commands.
- **exploratory_charter chosen** → also write `exploratory_charters[]`: one row per mission (`mission`, `timebox`,
  `status: "pending"`, `findings: ""`).
- **test_first chosen** → the producer scaffolds a PENDING `test_first_plan[]` stub *here* at `/slice` (Step 5.5 runs `scaffold_test_first_plan.py`), one `{ac, status: "PENDING", test_path, test_function}` row per AC. The row bodies (`test_path`/`test_function` + the walk to PASSING) are **authored by the builder at build** time, because the test functions do not exist yet; the TF-1 pre-finish gate then requires one PASSING row per AC. (`/build-slice` re-runs the same scaffolder as an idempotent backstop for slices opened before this existed.) See the Shapes block below for its shape.

Shapes (omitted from the standard mission-brief example — present only when opted in): `test_first_plan[]` = `{ac, status: "PENDING"|"WRITTEN-FAILING"|"PASSING",
test_path, test_function}` -- its canonical shape is `SPECS['test_first']` in
`scripts/lib/brief_variants_audit.py` (builder-authored at BUILD time, >=1 PASSING row per AC; NOT validated by
`artifact_lint`); `architectural_layers[]` = `{layer, component, verification (a runnable
command), status: "pending"|"exercised"}` (WS-1 docstring); `exploratory_charters[]` = `{mission, timebox,
status: "pending"|"in-progress"|"completed"|"deferred", findings}` (ETC-1 docstring). `artifact_lint` validates
the `status` enums of `architectural_layers`/`exploratory_charters` when present.

## Step 4 — scope check
≤5 ACs, ≤1 day, system stays shippable. If it exceeds → **`--release` the Step-1.5 reservation** and split (the original SC-NNN returns to the pickable backlog; the sub-slices are picked fresh).

## Step 5 — claim + scaffold (BRANCH-3 worktree-at-pick; ADR-012 worktree-first repro ordering)
Once the candidate is settled AND scope passes, in THIS order (list item N below = **Step 5.N** everywhere this
document cross-references it). The sequence is load-bearing: a bug-fix repro is born
inside `$wt` (or relocated by the one explicit path), never grabbed from the main tree by a glob.

1. **Claim FIRST — mint the slice number in-lock** (reserve-then-scaffold; [[ADR-013]]). The model NEVER
   computes a slice number — `claim_candidate` mints it inside the locked claim and returns it:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --candidate <SC-NNN> --name <verb-object> --json
   ```
   In ONE locked read-modify-write it bumps `counters.slice`, allocates `slice-NNN`, sets the candidate
   `status: spiking` / `progress: spike` / `claimed_by {git_user, git_email}` / `started_at` / `slice`, appends
   the `pick_log` entry, and RETURNS `{"slice": "slice-NNN", "folder": "slice-NNN-<name>"}`. Read `folder` for all
   paths below. Fail-visible on unset git identity (exit 1). This is the **CONFIRM** phase of the two-phase claim
   (ADR-016): if the candidate was reserved at Step 1.5 (the normal path) the same locked write UPGRADES the
   reservation `reserved → spiking` — **same-owner only**; a reservation held by a different git identity refuses
   (exit 1, no slice number minted). A fresh `candidate`/`deferred` pick (no prior reservation) still claims directly.
2. **Compute paths** from the RETURNED `folder`: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder <folder> --repo-root .` (line 1 = `$wt` path, line 2 = `slice/NNN-<name>`).
3. **Resolve the integration base + create the worktree** (slice-022/061: slices branch off the integration branch `aisdlc-uat` — legacy `uat` accepted as back-compat in an ai-sdlc-managed repo — degrading visibly to the default trunk when no integration branch exists; never a hardcoded master):
   ```bash
   base=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_git_default_branch.py" --integration --repo-root <main>)
   [ -z "$base" ] && { echo "STOP: cannot resolve the integration branch (git unusable)." >&2; exit 2; }
   git -C <main> worktree add <wt_path> -b slice/NNN-<name> "$base"
   ```
   **Failure → wrapper-enforced compensation**: `$PY "${CLAUDE_SKILL_DIR}/scripts/claim_candidate.py" --candidate <SC-NNN> --release` (reverts the claim to `candidate`; the counter is NOT decremented — monotonic-burn), then **STOP**.
4. **In-loop repro (Step 2 "No row" path only)**: if BFRD-1 confirmed an in-loop repro is needed, invoke
   `/repro "<desc>" --target-root=<wt_path>` once via the Skill tool. `/repro` writes the failing test under
   `<wt_path>/tests/bugs/` (born on the slice branch) and confirms it fails there — **no relocation needed**.
   (Non-bug-fix, or a standalone row already present → skip; that case is handled at Step 5.6.)
5. **Write the scaffold** to the **external vault store**:
   - `<vault>/slices/<folder>/mission-brief.json` (schema: `examples/mission-brief.json`)
   - `<vault>/slices/<folder>/milestone.json` (schema: `examples/milestone.json`; `stage: "spike"`, `next_action: "/risk-spike"`)
   - **If `variants.test_first` is true — scaffold the PENDING `test_first_plan[]` NOW (PRIMARY producer; slice-051/ADR-042).**
     Immediately after writing mission-brief.json, run the shared scaffolder against it so the brief arrives at
     `/build-slice` already carrying one PENDING row per AC (never hand-authored mid-build):
     ```bash
     $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/scaffold_test_first_plan.py" "<vault>/slices/<folder>/mission-brief.json"
     ```
     It appends one `{ac, status: "PENDING", test_path: "", test_function: ""}` row per declared AC (idempotent; a
     cross-volume-safe same-directory atomic write). **Surface/halt on a non-zero exit** — the scaffold action is
     observable, never fire-and-forget (must-not-defer). The builder fills each row's `test_path`/`test_function` and
     walks it to PASSING at build.
   Failure → `--release` the claim (the same wrapper-enforced compensation as Step 5.3) + `git -C <main> worktree remove <wt_path>`, then STOP.
6. **Standalone repro relocation (Step 2 "Row present" path)** — a `/repro` ran *before* `/slice` and left the
   failing test untracked on the MAIN tree. Relocate the ONE test named by its shippability row into `$wt` —
   **capability-scoped, NEVER a `tests/bugs/*` glob** (ADR-012; the glob caused cross-slice theft). Derive the grant
   from the row(s) by reusing `shippability_path_audit._extract_test_tokens` (it strips `-q`/`::selector`), keep only
   the ones still untracked on the main tree:
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation; never inline-read the bare env var
   cand=$(VAULT="$VAULT" SKILL_DIR="${CLAUDE_SKILL_DIR}" REPO_ROOT="$repo_root" $PY -c "
   import sys, os, json, subprocess
   # Paths arrive via env, NEVER shell-interpolated into this source: a native Windows vault
   # path inside a quoted Python literal is a SyntaxError, and a CWD-relative sys.path only
   # works on the plugin's own repo. The sibling-skill dir is anchored off CLAUDE_SKILL_DIR.
   sys.path.insert(0, os.path.join(os.environ['SKILL_DIR'], '..', 'validate-slice', 'scripts'))
   from shippability_path_audit import _extract_test_tokens
   with open(os.path.join(os.environ['VAULT'], 'shippability.json'), encoding='utf-8') as f:
       rows = json.load(f).get('rows', [])
   paths = {t for r in rows for t,_ in _extract_test_tokens(r.get('machine_cmd','')) if t.startswith('tests/bugs/')}
   def untracked(p):  # scoped to the ONE grant path p -- never globs the whole tests/bugs/ folder
       r = subprocess.run(['git','-C',os.environ['REPO_ROOT'],'ls-files','--others','--exclude-standard','--',p],capture_output=True,text=True)
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
   - **Reconcile the row's placeholder `slice` field** (you read the row to derive the grant, so you know its
     `SHIP-NNN` id): `/repro` recorded a placeholder fix-slice name at catalog time — update it to the slice
     just claimed, so the placeholder never persists in the catalog:
     ```bash
     $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --file shippability.json --array rows --id SHIP-NNN --set slice=slice-NNN-<name>
     ```
7. **Gate row for the completion gate (slice-102 / [[ADR-147]]) — POST-CLAIM, and only here.** Not at 5.1: that
   step's own failure-ladder entry asserts that NOTHING ELSE RAN at that point, and appending a vault row there
   would falsify its premise. Emit ONE row recording the verdict the pick surface carried and what the user
   decided, so `/pulse` and `/critic-calibrate` can later show how often the gate fired and how often it was
   declined. `decision` is one of `product-pick` (the gate's pick was taken) | `declined` (the halt was declined
   and an ordinary candidate claimed) | `explicit-intent`.
   **A route-* mode emits NO row** — the user left `/slice` WITHOUT claiming, and the row's ABSENCE is the honest
   record. Stated plainly: this makes firing-vs-decline measurable and accept-rate NOT measurable from `/slice`;
   the durable record of an accept is the PS/SC mint itself.
   ```bash
   VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
   TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
   T="$(mktemp -d "$TMPD/aisdlc-cg-row.XXXXXX")"
   "$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
       --gate completion-gap --slice slice-NNN-<name> \
       --verdict <product-work-available|scope-exhausted|scope-absent> --findings-count 0 \
       --note "decision=<product-pick|declined|explicit-intent>; reason=<r>; done=X/Y; unbuilt=N; dangling=M" \
       --out "$T/row.json" \
     && "$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
           --vault "$VAULT" --file gate-log.json --array entries --content-file "$T/row.json"; rc=$?
   [ "$rc" = 0 ] || echo "WARN: completion-gap gate row not appended (rc=$rc) -- the claim and scaffold STAND; only the measurement row is lost." >&2
   rm -rf "$T"
   ```

**Failure ladder (must-not-defer #1 — no silent partial scaffold; claim-first, so the claim is the FIRST committed state and the compensation is wrapper-enforced, not skippable prose):**
- claim (5.1) fails → **STOP**; nothing else ran (no worktree, no scaffold). If a Step-1.5 reservation is live, `--release` it — the claim/upgrade did not commit, so the hold must not linger `reserved`.
- worktree-add (5.3) fails → **wrapper-enforced compensation**: `claim_candidate.py --candidate <SC-NNN> --release` (revert the claim; counter not decremented — monotonic-burn), then STOP. No orphaned reservation.
- `/repro` (5.4) aborts, or its "test unexpectedly passes" recovery fires → `--release` the claim + `git -C <main> worktree remove <wt_path>` + `git branch -D slice/NNN-<name>`; leave no scaffold.
- scaffold (5.5) fails → `--release` the claim + roll the worktree back the same way.
- gate-row append (5.7) fails → **WARN and continue.** The claim and the scaffold STAND; only the measurement row is lost, and a lost measurement must never cost a claimed slice its worktree. Surfaced, never fire-and-forget.

The BFRD-1 Confirm/Not-a-bug gate (Step 2) runs BEFORE the Step-5.3 worktree-add, so cancelling a candidate as "Not a
bug" never orphans a worktree.

## Critical rules
- ASK before deciding the slice (unless explicit intent / "you pick"). ENFORCE scope (>1 day → split).
- VERB-OBJECT names only. INCLUDE must-not-defer EVERY slice. Do NOT design here — that's `/design-slice`.

## Pipeline position
- predecessor: `/reflect` (or `/discover` for slice 1) · successor: **`/risk-spike`** · auto-advance: true
- on-clean-completion: once the scaffold is written, the candidate is claimed, and a candidate was settled,
  invoke **`/risk-spike`** via the Skill tool — its in-loop spike gate must pass before `/design-slice`.
- user-input gates (halt auto-advance): candidate selection (Step 1); BFRD-1 confirm (Step 2); slice-discipline variants (Step 3b — one quick multi-select, default none).
