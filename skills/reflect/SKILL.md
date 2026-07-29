---
name: reflect
description: "Capture what the just-completed slice taught you and update the vault with reality across four categories: validated, corrected, discovered, and deferred. Writes reflection.json, appends to lessons-learned.json and shippability.json, optionally promotes build-checks and auto-archives the slice. Tracks Critic calibration per TRI-1. Use after /validate-slice, before next /slice."
when_to_use: "Trigger phrases: /reflect, 'reflect on slice', 'capture learnings', 'update vault with reality', 'slice retrospective'. Prerequisite: validation.json must exist. Auto-advance terminus — /commit-slice is always user-invoked."
argument-hint: "[slice-id]"
allowed-tools: Read, Write, Edit, Glob, Bash, AskUserQuestion
---

# /reflect — Capture Reality, Update Vault

The cure for spec rot: structured vault updates at every slice boundary so the vault tracks reality, not the original plan.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).
> SVW-1: shared aggregate files (`risk-register.json`, `lessons-learned.json`, `shippability.json`,
> `build-checks.json`, `_index.json`) mutate ONLY through `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"` (append or CAS-rewrite).
> NEVER raw-Write or Edit these files directly.
> **Scratch files**: every `--out-file` / `--content-file` below (`base.bin`, `lesson-entry.json`,
> `bc-rule.json`, `ship-row.json`, …) goes in a PORTABLE temp dir, re-derived per bash block (vars don't
> persist): `TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')"` then
> `T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"`, use `"$T/<file>"`, `rm -rf "$T"` when done.
> **NEVER a bare `mktemp -d`** — on Windows git-bash it returns `/tmp/…`, which the Windows-Python
> `vault_edit` resolves as a nonexistent `C:\tmp\…` — and NEVER the project CWD
> (one `git add -A` away from being committed). Step 6.2 shows the pattern in full.

## Step 0 — resolve the active slice (run this FIRST)

Run the `bash` block below **first** — it resolves the active slice in a BODY step that BINDS an explicit
`/reflect slice-NNN` `$ARG`. A `!`-injection runs at skill-LOAD *before* `${ARGUMENTS}` binds, so it CANNOT
resolve a named slice (SC-064 / ADR-022). Read the printed JSON; use THIS resolved slice (its folder/id)
for every `slice-NNN` reference in the Step-1 reads and the Step-6 archive steps — never re-derive it
elsewhere (a second resolution can disagree).
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then AS="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --json)"; else AS="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --json)"; fi   # no-arg keeps --repo-root . so an exit-4 AMBIGUOUS HALT surfaces (NO 2>/dev/null); capture+emit degrades it to a VISIBLE note, never a launch-abort
rc=$?; if [ "$rc" -ne 0 ]; then echo "HALT: slice resolution refused (rc=$rc) -- ownership or ambiguity; see stderr. Do NOT guess a slice. If you are an agent: STOP and report the owner to the user; do NOT set the override yourself." >&2; exit "$rc"; fi
# The guard MUST be the first statement after the capture: `rc=$?` reads the LAST command executed,
# so any command in between (even a display `if`) makes it read THAT command's 0 and the guard becomes
# a total no-op. /reflect shipped exactly that (code-review CR1) -- and note this site captures --json,
# whose refusal SENTINEL is printed to stdout, so an `[ -z "$AS" ]` test is FALSE on a refusal too:
# the exit code is the only honest signal here, and it must be read immediately.
if [ -z "$AS" ]; then echo "HALT: no slice resolved -- refusing to guess." >&2; exit 1; fi
if printf '%s' "$AS" | grep -q 'by-id-archive'; then echo "(M3: $ARG is already shipped/archived -- /reflect runs on the ACTIVE slice, not an archived one; nothing to reflect.)"; else printf '%s\n' "$AS"; fi
```

If the block printed the M3 archived note: **STOP here** — do not proceed to Step 1 against an archived folder
(there is nothing to reflect; a re-run would double-append lessons/shippability rows).

Read all of the following before proceeding (stop if `validation.json` is missing — run `/validate-slice` first):

- `<vault>/slices/slice-NNN/mission-brief.json` — intent and acceptance criteria
- `<vault>/slices/slice-NNN/design.json` — design claims to categorize
- `<vault>/slices/slice-NNN/critique.json` — Critic findings for calibration scoring (absent when the Critic
  was skipped on a low-tier slice — then skip Step 3 calibration; only a missing `validation.json` is a STOP)
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
- **Risk claim wrong** → one locked field update (retry-free; no CAS ladder needed for a per-risk field):
  ```bash
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update --file risk-register.json \
      --array risks --id <R-NN> --set status=<...> --set notes="<what reality showed>"
  ```
  (A genuinely STRUCTURAL rewrite — reordering/removing risks — still uses the read → edit → `rewrite
  --base-file` CAS path with exit-3 retry, scratch files under `$T/` per the preamble.)
  A **new** risk entry is an append, not a rewrite — PRE-MINT its id in-lock (never model-mint
  "next R-NN"; parallel slices collide on it):
  ```bash
  TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"
  R="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --file risk-register.json --kind r)"
  # (write the entry — with "id": "$R" — to "$T/new-risk.json", then:)
  $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file risk-register.json --array risks --content-file "$T/new-risk.json"
  rm -rf "$T"
  ```
- **Concept assumption wrong** → raw-Write `<vault>/concept.json` (created_by skill, single owner).
- **Slice design wrong** → Edit `<vault>/slices/slice-NNN/design.json`; note the deviation in `build-log.json`.

For each **Discovered** item:
- Append to `<vault>/risk-register.json` via `vault_edit append` (R-32 safe channel).
- **Capture** to `<vault>/candidates.json` through the **residue gate** — record-on-capture (see below).

For each **Deferred** item:
- **Capture** to `<vault>/candidates.json` through the **residue gate** — record-on-capture (see below).

### Record-on-capture — the residue gate (`residue_disposition`, slice-072 / [[ADR-077]])

At `/reflect` the owning slice is **closing** — Step 6 auto-archives it in this same run — so there is **no
resolve-in-owning-slice option** to offer (nothing live to resolve into). Every Deferred/Discovered item is
therefore **GUARANTEED captured** to `candidates.json`, exactly as before: **the capture is never dropped**
(regressing today's auto-capture safety net is forbidden). What changes is only that each capture now carries a
**recorded reason**. Build the candidate payload through `residue_disposition.py`, which stamps `ejected_from`
(the just-archived owning slice) + a **required, non-empty** `ejection_reason` and **fail-closes** if the reason
is empty — on refusal you **surface it and re-prompt the user for a reason, you never silently drop the item**.
The reason is the user's (main-thread); the gate only enforces that a non-empty one was recorded. Then append
through the SVW-1 `vault_edit` channel, **OMITTING** the `id` (the allocator mints `SC-NNN` in-lock; a supplied
id is rejected — slice-019 / [[ADR-013]]):

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"
# Write the item body (title/description/source/rationale/… — NO id) to "$T/item.json", then build + append:
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/residue_disposition.py" \
    --item-file "$T/item.json" --ejected-from slice-NNN \
    --ejection-reason "<why this residue left its owning slice — a recorded, non-empty reason>" \
    --json > "$T/cand.json"
rc=$?; [ "$rc" = 0 ] || { echo "STOP: residue gate refused (rc=$rc) — supply a non-empty ejection_reason; NEVER drop the capture" >&2; exit "$rc"; }
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file candidates.json --array candidates --content-file "$T/cand.json"
rm -rf "$T"
```

The item body MAY carry an optional `area` (slice-098 / [[ADR-125]]) when the residue plainly belongs to a known
product area — a candidate's own area is what makes it reachable by the `/slice --area` lens. It is **validated at
the mint leg**: an empty/whitespace/non-string value, the reserved `unassigned` sentinel, or a `component` key
(candidates carry `area`, never `component`) is **REFUSED at exit 2** with `candidates.json` byte-identical, and a
LIST payload is validated **element by element**. Omitting `area` entirely is the norm and always legal — annotate
later through the seam in `/slice-candidates` (product-6) rather than guessing here.

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

Pattern observations feed `/critic-calibrate` every 10–20 slices. **Record the verdicts STRUCTURED, not just
prose**: write a `calibration[]` array into `reflection.json` (Step 4) — one `{"finding": "<id>", "verdict":
"<VALIDATED|FALSE-ALARM|OVERRIDE-MISJUDGED|NOT-YET|MISSED>", "note": "<one line>"}` row per scored finding — so
`/critic-calibrate` counts them without text-mining reflection prose (its 1h user-override signal reads exactly
these rows).

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

Write `<vault>/slices/slice-NNN/reflection.json` (schema: `examples/reflection.json`), including the Step-3
structured `calibration[]` rows when the Critic ran (omit the array when the Critic was skipped).

---

## Step 5 — Append to lessons-learned.json (SVW-1)

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file lessons-learned.json --array entries --content-file "$T/lesson-entry.json"
rm -rf "$T"
```

Schema by example: `examples/lessons-learned.json`. One entry per slice: `{ "slice", "at", "lesson" }`.

---

## Step 5b — Build-checks promotion (BC-1, opt-in)

Ask the user:

> Did this slice surface a **recurring pattern** that should become a build-check? (y/N)
>
> Promote: "Image uploads need EXIF orientation normalization" — third time this slice hit it.
> Do NOT promote: one-off typo fixes, library version bumps, endpoint-specific bugs.

If yes, gather: **title** (imperative), **severity** (`critical`/`important`), **applies_when** (a JSON **object**, e.g. `{"glob": "**/*.py"}` or `{"always": true}` — never a bare string like `always:true`; a non-object `applies_when` is rejected at mint and would enforce nothing), **rule** (actionable check), **rationale**, **validation_hint**. Build `bc-rule.json` **WITHOUT an `id`** —
`build-checks.json`/`rules` is a MANAGED array: the allocator mints `BC-PROJ-N` in-lock and a caller-supplied id
is rejected, so two parallel reflects can never collide on an id.

Append the rule to `<vault>/build-checks.json` under `rules[]` via:
```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file build-checks.json --array rules --content-file "$T/bc-rule.json"
rm -rf "$T"
```
Schema by example: `examples/build-checks.json`.

The `vault_edit append` above is the whole promotion — the rule is now live for the next slice's
`/build-slice` BC-1 gate to enforce. (BCI-1, the build-checks **integrity** audit, is a plugin self-audit run
in CI against the plugin's own fixtures — it is NOT a per-project step and references no user-side fixture.)

---

## Step 5.3 — Append to shippability.json (SVW-1)

One critical-path test per slice: "If this slice silently broke later, what is THE one test that would catch it first?"

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-reflect.XXXXXX")"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file shippability.json --array rows --content-file "$T/ship-row.json"
rm -rf "$T"
```

Schema by example: `examples/shippability.json`. Build `ship-row.json` WITHOUT an `id` — the allocator mints `SHIP-NNN` in-lock (`vault_edit append` on `shippability.json`/`rows` rejects a supplied id). The `machine_cmd` must be runnable from project root; runtime < 10 s.

---

## Step 5.8 — Update milestone.json

Edit `<vault>/slices/slice-NNN/milestone.json`:
- `stage: "complete"`, `next_action: "/commit-slice"`, `updated: <today>`
- Check progress step `reflect: done: true`
- `current_focus`: "Slice validated; lessons captured. Run /commit-slice to land the code."

Schema by example: `examples/milestone.json`.

---

## Step 6 — Auto-archive this slice

> **The archive-before-commit window (DD-20).** From this step until the user runs `/commit-slice`, the slice
> folder lives in `slices/archive/` while its CODE is still **uncommitted** in the worktree. In that window
> there is NO active slice (`active_slice.py` excludes `archive/`) — `/pulse` shows "pre-slice" and a premature
> `/slice` would start a new cut on top of unlanded work. That is why Step 8 leads with "run `/commit-slice`";
> `/commit-slice` finds the slice via `latest_archived_slice.py`, and `stranded_slice_audit` flags the window if
> it goes stale. Do not "fix" this by archiving later — "reflected ⇒ archived" keeps the live `slices/` dir
> meaning exactly "work in flight".

After `reflection.json` is written and `milestone.json` is complete:

0. **Lint the slice's artifacts (3.18.7)** before archiving — a malformed / enum-invalid artifact must not be
   frozen into the archive:
   ```bash
   VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --dir "$VAULT/slices/slice-NNN-<name>" --skip-unknown
   ```
   Non-zero → fix the offending artifact (required key / known enum), then proceed to the move.
1. **Move the slice folder** (R-32 seam-routed, ADR-103):
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" move --from slices/slice-NNN --to slices/archive/
   ```
   Refuses if `slices/archive/slice-NNN` already exists (no-overwrite).

2. **Regenerate BOTH index files from the slice folders** (deterministic full recompute -> CAS-rewrite; ADR-020/SC-008). Step 1 already moved this slice into `slices/archive/`, so `slice_index_regen.py` picks it up automatically -- it drops out of `active[]` and joins the catalog. Do NOT hand-edit `active[]`/`recent[]`/`slices[]`; the generator is the single source of both indexes' SHAPE + CONTENT. Pass ONE `--updated` stamp to both emits (the only non-deterministic field -- keeps re-runs byte-identical):
   ```bash
   TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
   T="$(mktemp -d "$TMPD/aisdlc-reflect-idx.XXXXXX")"; TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read --file slices/_index.json         --out-file "$T/idx_base.bin"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read --file slices/archive/_index.json --out-file "$T/arch_base.bin"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_index_regen.py" --emit live    --updated "$TS" --out-file "$T/idx_new.json"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_index_regen.py" --emit archive --updated "$TS" --out-file "$T/arch_new.json"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file slices/_index.json         --base-file "$T/idx_base.bin"  --content-file "$T/idx_new.json"
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file slices/archive/_index.json --base-file "$T/arch_base.bin" --content-file "$T/arch_new.json"
   rm -rf "$T"
   # exit 3 on either rewrite -> re-read THAT base + re-emit (the regenerator re-scans the folders, picking up any concurrent slice) + retry (max 5)
   ```
   The live index keeps `recent[]` at the 10 newest archived slices; the archive index carries the full `slices[]` catalog. The generator emits the canonical per-entry shape ({slice,title,stage} active; {slice,title,shipped,summary} recent/catalog) so the conformance test (`tests/test_slice_index_regen.py`) stays green. Schema by example: `examples/slice-index.json` + `examples/slice-archive-index.json`.

---

## Step 6b — Mark the candidate VALIDATED (CAND-1)

**The code is NOT committed yet at `/reflect`** — the worktree changes land at `/commit-slice`. So the candidate
is **not `shipped` here; it is `validated`.** Set its status to `validated` in the LIVE backlog and LEAVE it in
`candidates.json`. `/commit-slice` is what marks it `shipped` and moves it to `archive/candidates.json` once the
code actually lands (so a `shipped` candidate always means the code is committed — "shipped" means shipped).

Set the matching candidate's `status` to `"validated"` in ONE locked, retry-free call — `--id-key slice`
matches on the candidate's `slice` field (do NOT archive it here; no CAS read/rewrite ladder needed for a
single-field update):
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" update \
    --file candidates.json --array candidates --id-key slice --id slice-NNN \
    --set status=validated
```

Schema by example: `examples/slice-candidates.json`. (The `validated → shipped` move to
`archive/candidates.json` happens at `/commit-slice`, after the commit lands.)

---

## Step 7 — Preview next-slice candidates

Run the SAME ranking `/slice` will show (consistency — don't hand-rank a miniature duplicate):
```bash
$PY "${CLAUDE_SKILL_DIR}/../slice/scripts/candidates_top.py" --top 3
```
Present its output briefly, noting any entry that came from THIS slice's Discovered/Deferred items. Example:

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
- "Tip: `/commit-slice` and the next `/slice` can start in a fresh session (/clear first) — all resume
  state lives in the vault, and a lean context is cheaper and more focused."

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
