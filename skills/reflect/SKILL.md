---
name: reflect
description: "Capture what the just-completed slice taught you and update the vault with reality across four categories: validated, corrected, discovered, and deferred. Writes reflection.json, appends to lessons-learned.json and shippability.json, optionally promotes build-checks and auto-archives the slice. Tracks Critic calibration per TRI-1. Use after /validate-slice, before next /slice."
when_to_use: "Trigger phrases: /reflect, 'reflect on slice', 'capture learnings', 'update vault with reality', 'slice retrospective'. Prerequisite: validation.json must exist. Auto-advance terminus — /commit-slice is always user-invoked."
allowed-tools: Read, Write, Edit, Glob, Bash, AskUserQuestion
---

# /reflect — Capture Reality, Update Vault

The cure for spec rot: structured vault updates at every slice boundary so the vault tracks reality, not the original plan.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).
> SVW-1: shared aggregate files (`risk-register.json`, `lessons-learned.json`, `shippability.json`,
> `build-checks.json`, `_index.json`) mutate ONLY through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"` (append or CAS-rewrite).
> NEVER raw-Write or Edit these files directly.

## Active slice + inputs — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --json
```

Read all of the following before proceeding (stop if `validation.json` is missing — run `/validate-slice` first):

- `<vault>/slices/slice-NNN/mission-brief.json` — intent and acceptance criteria
- `<vault>/slices/slice-NNN/design.json` — design claims to categorize
- `<vault>/slices/slice-NNN/critique.json` — Critic findings for calibration scoring
- `<vault>/slices/slice-NNN/build-log.json` — build deviations
- `<vault>/slices/slice-NNN/validation.json` — PASS/FAIL evidence (prerequisite)

---

## Step 1 — Synthesize into four categories

Read all slice files above. Produce one finding per item; be honest, not promotional.

**Validated** — design claims reality confirmed.
> "POST /receipts accepts HEIC with EXIF normalization — curl tests on all formats passed."

**Corrected** — design claims reality refuted. Each corrected item triggers a vault update (Step 2).
> "Design said async queue via SQS; shipped sync. ADR-008 superseded by ADR-014."

**Discovered** — things not in the spec: new risks, edge cases, constraints.
> "Safari retry header absent on SSE — thumbnails appeared sideways without EXIF handling."

**Deferred** — out-of-scope items, per mission-brief or deliberate cut.
> "Multiple receipts per transaction — separate slice."

**Honesty rule**: if Discovered is empty across multiple slices, you are not capturing. Push harder.

---

## Step 2 — Update affected vault files

For each **Corrected** item:

- **Decision wrong** → supersede: mark original ADR `status: superseded` + `superseded_by: ADR-NNN`; create new ADR with `supersedes: ADR-NNN`. Never edit original ADR content (append-only).
- **Risk claim wrong** → CAS-rewrite `<vault>/risk-register.json`:
  ```bash
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read   --file risk-register.json --out-file base.bin
  # edit a copy, then:
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file risk-register.json --base-file base.bin --content-file edited.json
  # exit 3 = parallel write conflict → re-read + re-apply + retry (max 5, then STOP)
  ```
  A **new** risk entry is an append, not a rewrite:
  ```bash
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file risk-register.json --array risks --content-file new-risk.json
  ```
- **Concept assumption wrong** → raw-Write `<vault>/concept.json` (created_by skill, single owner).
- **Slice design wrong** → Edit `<vault>/slices/slice-NNN/design.json`; note the deviation in `build-log.json`.

For each **Discovered** item:
- Append to `<vault>/risk-register.json` via `vault_edit append` (R-32 safe channel).
- Append to `<vault>/candidates.json` via `vault_edit append` (future slice seed).

For each **Deferred** item:
- Append to `<vault>/candidates.json` via `vault_edit append`.

---

## Step 3 — Critic calibration (TRI-1)

For every finding in `critique.json`, score its outcome using build/validate evidence:

| Verdict | Meaning |
|---|---|
| `VALIDATED` | Critic was right; concern materialized |
| `FALSE-ALARM` | User overrode; reality confirmed the user (Critic over-reached) |
| `OVERRIDE-MISJUDGED` | User overrode; reality showed Critic was right — signal for both |
| `NOT-YET` | Deferred; re-score in the future slice that addresses it |
| `MISSED` | Surfaced during build/validate, absent from Critic findings entirely |

Pattern observations feed `/critic-calibrate` every 10–20 slices.

---

## Step 3b — Emit recall rows to the gate-log (measurement spine, Phase 0.2 recall half)

Step 3's `MISSED` verdicts are the **recall** signal — a real issue a Critic gate should have caught but didn't (it surfaced in build/validate, or post-ship). Precision already lands in the gate-log at `/critique` TRI-1; recall lands HERE, so per-gate **recall = catches / (catches + misses)** becomes computable (catches = the gate's confirmed-real findings; misses = these rows). Emit **one miss row per `MISSED` finding** — skip this whole step if Step 3 produced none.

For each `MISSED` finding, pick the **owning gate** (the gate whose scope should have caught it: `critique` for a design-level gap, `code-review` for a code defect, `critique-review` only if the meta-pass also should have flagged it), its `severity` (`blocker`/`major`/`minor`), and where it was finally caught (`build` or `validate`), then append via the SVW-1 channel:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" --kind miss \
    --gate <critique|code-review|critique-review> --slice slice-NNN \
    --severity <blocker|major|minor> --caught-by <build|validate> --ref "<one line: what was missed>" \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --file gate-log.json --array entries --stdin
```

**Post-ship escape (laundered error — Theme 8, optional but highest-signal):** if this slice FIXES a bug that escaped a *previously shipped* slice (you ran `/repro`, a shippability regression fired, or a user reported it), also emit a miss against the **introducing** slice: `--slice slice-MMM` (the one that shipped the bug), the owning gate, and `--caught-by post-ship|bug-hunt|user|repro`. That is the bug class that passed EVERY gate including validate — the most valuable calibration data `/critic-calibrate` consumes.

`kind:"miss"` rows are recall-only: `/pulse` and `/critic-calibrate` filter them OUT of the precision/raised math, so they never distort the existing gate hit-rate.

---

## Step 4 — Write reflection.json

Write `<vault>/slices/slice-NNN/reflection.json` (schema: `examples/reflection.json`).

---

## Step 5 — Append to lessons-learned.json (SVW-1)

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file lessons-learned.json --array entries --content-file lesson-entry.json
```

Schema by example: `examples/lessons-learned.json`. One entry per slice: `{ "slice", "at", "lesson" }`.

---

## Step 5b — Build-checks promotion (BC-1, opt-in)

Ask the user:

> Did this slice surface a **recurring pattern** that should become a build-check? (y/N)
>
> Promote: "Image uploads need EXIF orientation normalization" — third time this slice hit it.
> Do NOT promote: one-off typo fixes, library version bumps, endpoint-specific bugs.

If yes, gather: **title** (imperative), **severity** (`critical`/`important`), **applies_when** (glob or `always:true`), **rule** (actionable check), **rationale**, **validation_hint**. Assign next `BC-PROJ-NNN` id.

Append the rule to `<vault>/build-checks.json` under `rules[]` via:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file build-checks.json --array rules --content-file bc-rule.json
```
Schema by example: `examples/build-checks.json`.

The `vault_edit append` above is the whole promotion — the rule is now live for the next slice's
`/build-slice` BC-1 gate to enforce. (BCI-1, the build-checks **integrity** audit, is a plugin self-audit run
in CI against the plugin's own fixtures — it is NOT a per-project step and references no user-side fixture.)

---

## Step 5.3 — Append to shippability.json (SVW-1)

One critical-path test per slice: "If this slice silently broke later, what is THE one test that would catch it first?"

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file shippability.json --array rows --content-file ship-row.json
```

Schema by example: `examples/shippability.json`. The `machine_cmd` must be runnable from project root; runtime < 10 s.

---

## Step 5.8 — Update milestone.json

Edit `<vault>/slices/slice-NNN/milestone.json`:
- `stage: "complete"`, `next_action: "/commit-slice"`, `updated: <today>`
- Check progress step `reflect: done: true`
- `current_focus`: "Slice validated; lessons captured. Run /commit-slice to land the code."

Schema by example: `examples/milestone.json`.

---

## Step 6 — Auto-archive this slice

After `reflection.json` is written and `milestone.json` is complete:

0. **Lint the slice's artifacts (3.18.7)** before archiving — a malformed / enum-invalid artifact must not be
   frozen into the archive:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --dir "$AI_SDLC_VAULT_ROOT/slices/slice-NNN-<name>" --skip-unknown
   ```
   Non-zero → fix the offending artifact (required key / known enum), then proceed to the move.
1. **Move the slice folder** (R-32 seam-routed, ADR-103):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" move --from slices/slice-NNN --to slices/archive/
   ```
   Refuses if `slices/archive/slice-NNN` already exists (no-overwrite).

2. **Update `<vault>/slices/_index.json`** (active → recent-10, CAS-rewrite):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read    --file slices/_index.json --out-file base.bin
   # regen: remove slice from active[], add thin one-liner to recent[] (keep exactly 10), then:
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file slices/_index.json --base-file base.bin --content-file regen.json
   # exit 3 → re-read + re-regen + retry (max 5)
   ```
   Schema by example: `examples/slice-index.json`. Thin one-liner ≤ 500 chars from mission-brief intent.

3. **Prepend to `<vault>/slices/archive/_index.json`** (newest-first, CAS-rewrite):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read    --file slices/archive/_index.json --out-file base.bin
   # insert the thin one-liner row at the TOP of slices[] (newest-first), then:
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file slices/archive/_index.json --base-file base.bin --content-file edited.json
   ```
   NOT `vault_edit append` — appending at EOF would put the row at the oldest position.
   Schema by example: `examples/slice-archive-index.json` — the FULL catalog; its array is `slices[]`, NOT the
   live index's `recent[]` (3.18.1).

---

## Step 6b — Mark the candidate VALIDATED (CAND-1)

**The code is NOT committed yet at `/reflect`** — the worktree changes land at `/commit-slice`. So the candidate
is **not `shipped` here; it is `validated`.** Set its status to `validated` in the LIVE backlog and LEAVE it in
`candidates.json`. `/commit-slice` is what marks it `shipped` and moves it to `archive/candidates.json` once the
code actually lands (so a `shipped` candidate always means the code is committed — "shipped" means shipped).

1. Read `<vault>/candidates.json`, find the candidate whose `slice` field matches `slice-NNN`.
2. Set its `status` to `"validated"` via CAS-rewrite (do NOT archive it here):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read    --file candidates.json --out-file base.bin
   # set the matching candidate's status to "validated", then:
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file candidates.json --base-file base.bin --content-file updated.json
   # exit 3 → re-read + re-apply + retry (max 5)
   ```

Schema by example: `examples/slice-candidates.json`. (The `validated → shipped` move to
`archive/candidates.json` happens at `/commit-slice`, after the commit lands.)

---

## Step 7 — Preview next-slice candidates

Surface 2–3 top candidates from Discovered, Deferred, active high risks in `candidates.json`. Keep it brief — `/slice` does full scoring. Example:

```
Next-slice candidates (preview — run /slice for full ranking):
#1: fix-safari-sse-retry — retires R-27 (Safari SSE reliability)
#2: add-receipt-deletion — completes deferred scope from this slice
#3: harden-s3-put-timeout — discovered: default 30 s too aggressive for >5 MB
```

---

## Step 8 — Close

State:
- "Reflection complete. Vault updates: `<list files>`."
- "Slice archived → `slices/archive/slice-NNN/`. Index refreshed."
- "Discoveries: `<count>` (added to risk-register + candidates)."
- "Deferrals: `<count>` (surfaced as slice candidates)."
- "Run `/slice` to define the next cut."

---

## Critical rules

- HONESTY: not a victory lap. Capture what didn't work, where you got lucky, where you're still guessing.
- UPDATE THE VAULT for every Corrected item — the vault must reflect reality, not the original design.
- NEVER edit superseded ADRs — decisions are append-only history.
- TRACK Critic accuracy every slice — calibration data compounds.
- SVW-1: all shared-aggregate file writes route through `vault_edit`. No raw-Write/Edit on those files.

---

## Pipeline position

- predecessor: `/validate-slice`
- successor: `/commit-slice` (then `/slice` for next cut)
- auto-advance: **false** — this is the auto-advance terminus
- user-input gates: build-checks promotion (Step 5b, opt-in y/N)
- on-clean-completion: present the hand-off summary leading with **"Slice validated — run `/commit-slice --merge` (or `--push`) to land the code and mark the candidate shipped, then `/slice` for the next cut"**, plus the next-slice candidate preview. The candidate is `validated`, NOT `shipped`, until the code is committed. NEVER auto-invoke `/commit-slice` — it is always user-invoked by contract (PCA-1).
