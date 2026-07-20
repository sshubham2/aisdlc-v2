---
name: critic-calibrate
description: "Meta-skill that mines 'Missed by Critic' + 'Critic calibration' entries from the last N archived reflections, classifies blind-spot patterns (>=3-distinct-slices threshold), and produces 0-3 evidence-backed Critic checks reviewed one-at-a-time. Accepted proposals are persisted to the project's vault overlay (active_checks[] / calibration_notes[] / gate_skips[] in critic-calibration-log.json), which /critique reads before every review — it NEVER edits the plugin's agents/critique.md. Every run is appended to runs[] (audit history, never overwritten); the user is prompted to mail the log to the maintainer to fold recurring checks into the next plugin version. Spawns a 'critic-calibrate' subagent for the analysis."
when_to_use: "Trigger phrases: /critic-calibrate, 'calibrate the Critic', 'improve Critic prompt based on misses', 'analyze Critic blind spots'. Run every 10-20 slices as a routine calibration pass, after repeated misses in the same Critic category, or after a serious post-ship bug. Runs in all pipeline modes (minimal/standard/heavy). No predecessor or successor — standalone maintenance skill."
argument-hint: "[--window N]  (default: 15 reflections)"
allowed-tools: Read, Bash, Agent, AskUserQuestion
---

# /critic-calibrate — Close the Critic Feedback Loop

Mine Critic miss patterns from archived reflections, produce targeted prompt-update proposals, present them for human review, log results.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config).

## Prerequisite check

Archive must have >= 5 slices (can't find patterns in fewer). Run this Bash gate:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
count=$(ls -t "${VAULT}/slices/archive/" 2>/dev/null | wc -l)
if [ "$count" -lt 5 ]; then
  echo "INSUFFICIENT_ARCHIVE count=$count"
fi
```

If output contains `INSUFFICIENT_ARCHIVE`: tell the user to return after more slices accumulate, and stop.

## Step 1 — gather inputs

Collect the four inputs the subagent needs.

**1a. Archived reflections (window)**

Parse `--window N` from invocation args; default N=15.

List the last N archived slice folders:
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
ls -t "${VAULT}/slices/archive/" | head -n <N>
```

For each folder, read `<vault>/slices/archive/<folder>/reflection.json`. Extract the `critic_calibration` and `missed_by_critic` fields. Concatenate into one block tagged by slice id.

**1b. Base Critic prompt**

Read the plugin's base Critic prompt at `${CLAUDE_SKILL_DIR}/../../agents/critique.md` in full — for **context only**,
so the agent proposes checks NET-NEW to it (the base already covers many dimensions). This file is the plugin's base,
shipped per version; this skill **never writes it** — project-specific checks layer on top via the vault overlay
(Step 4). Resolves for both a plugin-cache install and a dev checkout.

**1c. Past calibration log + active overlay**

Read `<vault>/critic-calibration-log.json` if it exists (schema: `examples/critic-calibration-log.json`, `@3`):
- `active_checks[]` — the checks ALREADY live in this project's overlay. Pass them so the agent proposes only
  NET-NEW checks (never a duplicate of an active check or of the base `agents/critique.md`).
- `calibration_notes[]` — the LIGHTEN signals ALREADY recorded (Phase 4.1). Pass them so the agent never
  re-proposes a lighten already in effect.
- `runs[]` — past runs for the effectiveness pass. Missing file or empty `runs` → pass `"no prior runs"`.

(CC/CN/GS ids are NOT hand-assigned from this read — Step 4 mints each one in-lock via
`vault_edit alloc --kind cc|cn|gs`, so a re-run or a stale read can never collide ids. This read is
context for the agent + the dedup keys, nothing more.)

**1d. Effectiveness data**

For each prior accepted proposal in the log: count miss instances in that category within the current window vs the equivalent window before the proposal was applied. Include this as a structured block in the agent prompt.

**1e. Gate precision + quiet-rate — the LIGHTEN signal (Phase 4.1 / Theme 5)**

Calibration runs in BOTH directions: **ADD** the checks the Critic was missing (1a–1d above), and **LIGHTEN** the
model-on-model gates/checks that have added no value. Hand the agent the recent gate-log rows for the
**model-on-model gates ONLY**, PLUS the per-gate precision/recall from the SHIPPED helper
(`triage_precision.gate_precision_recall` — the SAME computation `/pulse` uses; deterministic, not
model-eyeballed), and it proposes any lightening. **`critique-review` precision is now computable** here: once its
rows carry `findings_real` (slice-052/ADR-045), the helper includes it, so DR-1 is measured on gate-log data, not
mined from reflection prose:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
# slice-089/SC-194: derive-on-missing via the read-entries CLI (a synced/cloned vault has no local cache), captured with an explicit exit-check so a torn log surfaces read-entries' clean stderr, not a downstream JSON traceback (m3); a read failure degrades LOUDLY to "no gate-log data" (never a silent []).
rows_json="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py" read-entries --vault "$VAULT")" || { echo "critic-calibrate: gate-log unreadable via read-entries (see stderr above) -- proceeding with 'no gate-log data'." >&2; rows_json="[]"; }
printf '%s' "$rows_json" | $PY -c "import json,sys; rows=json.load(sys.stdin); M={'critique','critique-review','code-review'}; print(json.dumps([e for e in rows if e.get('gate') in M and e.get('kind') != 'miss'],indent=2))"
# per-gate precision/recall via the shipped, tested computation (absent findings_real -> UNKNOWN, never 0):
for g in critique critique-review code-review; do $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/triage_precision.py" --gate-precision --gate "$g" --gate-log "$VAULT/gate-log.json"; done
```

**HARD RULE — the reality spine never lightens.** The filter passes ONLY `critique` / `critique-review` /
`code-review`. `risk-spike` and `validate-slice` are HIGH reality-contact (real environments; they can say a hard
*no*) and are **never** lighten candidates regardless of their numbers — they are excluded by the filter and must
never be re-introduced. Missing/empty gate-log → pass `"no gate-log data"` (lighten analysis simply produces nothing).
The filter also excludes `kind == "miss"` rows: those are **recall** rows (a gate MISSED something), the opposite
signal — a miss is evidence to **tighten/ADD**, never to lighten, and it carries no `findings_count` so it would
otherwise distort precision/quiet. The misses feed the ADD side via 1f.

**1f. Gate-log misses — structured ADD corroboration (Phase 0.2 recall half / Theme 8)**

The recall rows `/reflect` appended (`kind == "miss"`) are structured "Missed by Critic" data that corroborates the
reflection mining in 1a–1d — a *post-ship* miss (`caught_by` in `post-ship`/`bug-hunt`/`user`/`repro`) is a laundered
error that passed every gate, the **highest-signal ADD evidence** there is. Extract them for the agent prompt:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
# slice-089/SC-194: derive-on-missing via read-entries + exit-check (m3), same fail-visible degrade as 1e above.
rows_json="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py" read-entries --vault "$VAULT")" || { echo "critic-calibrate: gate-log unreadable via read-entries (see stderr above) -- proceeding with 'no gate-log data'." >&2; rows_json="[]"; }
printf '%s' "$rows_json" | $PY -c "import json,sys; rows=json.load(sys.stdin); print(json.dumps([e for e in rows if e.get('kind')=='miss'],indent=2))"
```

The agent weighs these alongside the reflection misses when proposing ADD checks (the `>=3-distinct-slices` threshold
still applies); a single post-ship escape is a strong prior but still needs the pattern to cross the bar.

**1g. DR-1 unique-catch rate — does the meta-critic earn its spawn? (§2.5)**

The one decorrelation claim the spine doesn't otherwise measure: does `/critique-review`'s premortem method ever
catch what the first Critic missed? From the recent archived slices' `critique.json` files: count slices where
DR-1 ran, and `M-add-*` findings whose ratified disposition is real (accepted-*/deferred/escalated). Pass the
ratio + sample to the agent. **~0 unique catches over ≥10 DR-1 runs is legitimate evidence for a
`gate_skips` proposal targeting `critique-review`'s ADVISORY trigger** (the mandatory triggers — high tier,
critic_required, findings ≥5 — are a floor that never lightens). The other direction matters equally: a healthy
unique-catch rate is the documented answer to "why pay for two critics?".

**1h. User-override calibration — the human gate gets measured too (§6.4)**

From the recent reflections' calibration sections: count `FALSE-ALARM` (user override vindicated by reality) vs
`OVERRIDE-MISJUDGED` (user override refuted by reality). Pass both counts to the agent. A run of
OVERRIDE-MISJUDGED is the inverse of alert fatigue — the user is dismissing real findings — and warrants an
ADD-side proposal phrased as a TRI-1 presentation change (e.g. "require a second look before overriding
findings in dimension X"), never an automatic block: the user stays the final authority; this only makes the
trend visible to them.

**1i. Sampled low-tier audit — calibrate the skip rule itself (§2.6)**

Gate precision is measured only on slices the Critic actually reviewed — survivorship bias: the 50 skipped
low-tier slices contribute nothing, so the SKIP rule is never tested. Recommendation to surface to the user
(not auto-run): roughly **every 10th low-tier skipped slice, run `/critique --force` on it** and let the row
land in the gate-log like any other. If sampled low-tier critiques keep coming back clean, the skip rule is
validated cheaply; if they keep finding real issues, the tier default is mis-set — propose raising it via
re-triage. Note in the run record when the user declines (that is data too).

## Step 2 — invoke the critic-calibrate subagent

Use the Agent tool with `subagent_type: "critic-calibrate"`. Pass all four inputs as the prompt body:

```
Window: last <N> archived reflections
Slice range: slice-<first> through slice-<last>

# Reflections in window
<extracted critic_calibration + missed_by_critic fields, tagged by slice>

# Current Critic prompt (from the plugin's agents/critique.md)
<full file contents>

# Active project checks (already in this project's overlay — propose only NET-NEW; never duplicate these or the base prompt)
<contents of active_checks[], or "none">

# Past calibration log (runs)
<runs[] of critic-calibration-log.json, or "no prior runs">

# Effectiveness data
<for each prior accepted proposal: category, run date, miss count before, miss count after>

# Gate precision — model-on-model gates only (LIGHTEN candidates; reality gates excluded by construction)
<the filtered gate-log rows from 1e, or "no gate-log data">

# Gate-log misses — recall rows, ADD corroboration (post-ship = laundered errors, highest signal)
<the kind=="miss" rows from 1f, or "no gate-log misses">

# Active calibration notes already in this project's overlay (do not re-propose a lighten already recorded)
<contents of calibration_notes[] from critic-calibration-log.json, or "none">
```

The subagent returns: pattern summary, effectiveness section, **0–3 ADD proposals** (a missing Critic check),
**0–2 LIGHTEN proposals** (a model-on-model dimension/gate or a noisy active_check to lighten/retire), AND
**0–1 GATE-SKIP proposal** (stop a model gate's discretionary per-slice spawn — precision < 0.2 over ≥ 8 runs with
zero real blockers), plus a "watching but not proposing" list. **Reality gates (`risk-spike`/`validate-slice`) are
never lighten OR skip candidates.**

**Zero-proposal is a valid result** in BOTH directions. If the agent returns no proposals, skip to Step 4 (log the run). Do not push the agent to find something.

## Step 3 — present proposals one-at-a-time (user gate)

Present each proposal — **ADD, LIGHTEN, and GATE-SKIP alike** — as a separate `AskUserQuestion` gate — do NOT bundle.

**ADD proposal** (a new `active_checks[]` overlay check):
1. Show the pattern (evidence: slice numbers + miss count).
2. Show the proposed overlay check — `trigger` / `check` / `example` — and, for orientation only, the closest base
   `agents/critique.md` dimension it sharpens. The base file is **never edited**; the check layers on via the vault
   overlay (Step 4a), and `/critique` applies it on top of its 9 fixed dimensions.
3. Explain why this check would have caught the observed misses.
4. Wait: **accept / modify / reject**.

**LIGHTEN proposal** (a model-on-model dimension/gate or a noisy project check that has added no value):
1. Show the evidence: the gate/dimension, its **precision** + **quiet-rate** over the window, and the slice list.
2. Name the target — a `critique`/`code-review` dimension (→ a `calibration_notes` entry) OR an existing
   `active_check` CC-NNN that's been FALSE-ALARM (→ retire it).
3. State plainly what lightening does and does NOT do: it **informs** the Critic to weight that dimension lighter /
   stops running a noisy project check; it does **NOT** disable a gate, change the mode/tier table, or touch the
   reality spine (`risk-spike`/`validate-slice` can never be lightened).
4. Wait: **accept / modify / reject**.

**GATE-SKIP proposal** (stop a model gate's discretionary per-slice spawn — the heaviest lever; Phase 3.2):
1. Show the evidence: the model gate, its **precision** over **≥ 8 verdict runs** (must be < 0.2), that it caught
   **zero** real blockers, and the slice list.
2. Name the target (`critique` / `critique-review` / `code-review`) and the action (`skip` discretionary firing, or
   `tier-gate-high-only`). A gate-skip persists as a `gate_skips[]` entry (Step 4d) — the only overlay `/critique`
   reads to decide whether to *run* a model gate.
3. State plainly: a **compliance-mandatory** trigger (`critic_required` / Heavy / `high` tier) STILL forces the gate;
   the skip removes only the discretionary spawns where the gate measurably produced nothing. The reality spine
   (`risk-spike`/`validate-slice`) can **never** be skipped at any precision.
4. Wait: **accept / reject** (default-reject if unsure — this is the strongest lever; under-skipping is safe).

Capture each decision (with any modification text) for the log. LIGHTEN and GATE-SKIP proposals need strong evidence —
if the user is unsure, default to rejecting (under-lightening/under-skipping is safe; over-doing it erodes the floor).

## Step 4 — persist accepted checks to the vault overlay

This skill **NEVER edits `agents/critique.md`** (the plugin's base Critic — owned by the maintainer and shipped per
plugin version). A project-side edit would be **lost on the next plugin upgrade** AND would dirty the code repo on
`master`, breaking the slice-worktree contract (the main tree must stay clean). Accepted checks instead live in the
**EXTERNAL vault**, where `/critique` reads them before every review and they survive plugin upgrades.

Writes to `<vault>/critic-calibration-log.json` (all SVW-1 via `vault_edit`; none overwrite prior data):

**4a. Upsert each accepted/modified proposal into `active_checks[]`** — the small, deduped overlay `/critique` reads.
The `id` is MINTED IN-LOCK (never model-computed from the Step-1c read), and the append carries a mechanical
`--unique-key check` dedup so a re-run after a partial failure never duplicates a check (a same-text conflict
exits 2 fail-visible instead of silently doubling):

```bash
CC=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --file critic-calibration-log.json --kind cc)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --file critic-calibration-log.json --array active_checks --unique-key check --json '{
      "id": "'"$CC"'", "check": "<imperative, specific check>",
      "category": "<pattern name>", "evidence": ["slice-NNN", ...], "added_at": "<ISO-8601>"
    }'
```

A **rejected** proposal that names an already-active check → RETIRE it with the wired one-element removal
(SVW-1 locked; fail-visible if the id doesn't exist):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" remove \
    --file critic-calibration-log.json --array active_checks --id CC-NNN
```

A plain new rejection adds nothing. Retire is the ONLY case that mutates an existing element; everything else is
append-only. (The retired id is never re-issued — the allocator's seed floor also scans `runs[]` history.)

**4b. Append the full run to `runs[]`** — the append-only audit history (every run, even zero-proposal):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --file critic-calibration-log.json --array runs --json '{
      "at": "<ISO-8601 timestamp>", "window": <N>,
      "patterns": [ { "category": "<name>", "slices": ["slice-NNN", ...] } ],
      "proposals": [ { "text": "<proposed check>", "decision": "accepted|modified|rejected", "check_id": "CC-NNN|null" } ],
      "sampled_audit": { "suggested": true, "user_response": "ran|declined|n/a" }
    }'
```

`sampled_audit` is the §2.6 record as a STRUCTURED field (queryable, not a prose note): whether the 1i
sampled low-tier `/critique --force` suggestion was surfaced and what the user chose — a decline is data too.
Omit the field when 1i didn't apply this run.

Each proposal in the run carries `"kind": "add"|"lighten"` and the id it produced (`check_id` for ADD, `note_id`
for a dimension/gate LIGHTEN, or the retired `check_id` for an active-check LIGHTEN).

Schema by example: `examples/critic-calibration-log.json` (`@3`). Always append the run — even zero-proposal runs
(empty `proposals`, `patterns` populated with what was observed). `active_checks[]` + `calibration_notes[]` stay
small (all `/critique` loads); `runs[]` grows but `/critique` never reads it.

**4c. Persist each accepted LIGHTEN proposal (Phase 4.1)** — two cases:

- **Target is a model-on-model dimension/gate** (`critique` / `critique-review` / `code-review`) → append a
  `calibration_notes[]` entry. The `id` is minted in-lock; the dedup ("never two notes for the same
  gate+dimension") is MECHANICAL via the composite `--unique-key` (a same-target conflict exits 2 fail-visible):

```bash
CN=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --file critic-calibration-log.json --kind cn)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --file critic-calibration-log.json --array calibration_notes \
    --unique-key target_gate --unique-key target_dimension --json '{
      "id": "'"$CN"'", "target_gate": "critique", "target_dimension": "<dimension name/number>",
      "signal": "low-precision|quiet", "window": <N>, "precision": <0-1 or null>,
      "evidence": ["slice-NNN", ...],
      "note": "<what /critique should do: weight this dimension lighter on low-tier; do NOT inflate>",
      "confirmed_at": "<ISO-8601>"
    }'
```

- **Target is a noisy project `active_check` (CC-NNN)** → RETIRE it with `vault_edit remove` (the same wired
  path 4a uses for a rejected active check). Retiring the check IS the lightening — no note needed:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" remove \
    --file critic-calibration-log.json --array active_checks --id CC-NNN
```

**NEVER** write a `calibration_note` targeting `risk-spike` or `validate-slice` — the reality spine does not lighten.
If the agent ever proposed one, drop it (agent bug); do not persist it.

**4d. Persist each accepted GATE-SKIP proposal (Phase 3.2)** — append a `gate_skips[]` entry. The `id` is minted
in-lock; "one skip per gate" is MECHANICAL via `--unique-key target_gate` (a second skip for the same gate exits
2 fail-visible). This is the ONLY overlay array `/critique` reads to decide whether to *run* a model gate
(`active_checks`/`calibration_notes` only shape an in-progress review). It targets a model-on-model gate ONLY —
the same `{critique, critique-review, code-review}` set the 1e filter passes:

```bash
GS=$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" alloc --file critic-calibration-log.json --kind gs)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --file critic-calibration-log.json --array gate_skips --unique-key target_gate --json '{
      "id": "'"$GS"'", "target_gate": "critique-review", "action": "skip",
      "precision": <0..0.2>, "runs_observed": <N >= 8>, "real_blockers_caught": 0,
      "evidence": ["slice-NNN", ...], "rationale": "<one line>",
      "user_accepted_at": "<ISO-8601>"
    }'
```

`action` is `skip` (suppress discretionary firing) or `tier-gate-high-only` (run only on `high`-tier slices).
**NEVER** write a `gate_skips` entry targeting `risk-spike` or `validate-slice` — the reality spine cannot be
skipped at any precision. If the agent ever proposed one, drop it (agent bug).

## Step 5 — declassify + suggest mailing the REDACTED export to the maintainer

Accepted checks are now live for THIS project via the overlay. To improve the **base Critic for every project** in the
next plugin version, the maintainer needs the *recurring, generic* signal — but `critic-calibration-log.json` also holds
project-private content (free-text check/note prose that can quote your own code identifiers, absolute paths, code
excerpts). So Step 5 does **not** mail the raw log. It runs a **default-deny declassifier**
(`scripts/calibration_export.py`, ADR-103/104) that emits ONE safe-by-default maintainer payload, then suggests mailing
THAT.

**5a. active_checks consent gate (in-loop human declassification — the ONLY path active_checks reach the payload).**
The export auto-emits `calibration_notes` / `gate_skips` structurally (closed field+value vocab; free text withheld) and
does NOT machine-read `active_checks` from the log — a check's `check` text is both the payload's whole value AND an
unclosable free-prose leak surface, so each accepted check crosses ONLY through your explicit review here. For each
`active_check` (`CC-NNN`) you want to forward upstream:

1. **Show ONLY its `check` text.** Drop `example`, `id`, `added_at`, `category`, `evidence` — never forward them (the
   `example` field in particular embeds project-private code identifiers verbatim).
2. **Genericize it in a free-text conversational turn** — you rewrite the prose to strip every project-specific example
   (e.g. "run_catalog vs _execute_verifications" → "two divergent implementations of the same behavior"). This is a
   **free-text rewrite, NOT an `AskUserQuestion` choice** (a multiple-choice tool cannot capture rewritten prose).
3. **Then** gate include/skip with `AskUserQuestion` (include the genericized text / skip). **Default is EXCLUDE** — an
   un-reviewed check is never emitted.

**5b. Resolve the staging path — the Write step (5a) and the export MUST agree on ONE literal path.** Run this block
first; it prints the deterministic staging path (a fixed name, NO shell `$$` — a `$$` PID is unknowable to the Write
tool, so the file you write and the file the export reads would not coincide). If you included any checks in 5a, use the
Write tool to write the JSON **array** of `{text, recurrence_count}` objects (with `recurrence_count =
len(set(check.evidence))` — an integer distinct-slice count, the evidence-backed prioritization signal; **never the
slice-ids**) to **exactly** the `STAGING` path printed here. Forward no checks → skip the Write entirely; the export then
emits the notes/skips structural digest only. The export reads the staging file single-shot and removes it.

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
STAGING="$TMPD/aisdlc-calib-approved.json"        # DETERMINISTIC (no $$): 5a's Write MUST target this exact path
echo "If you included any checks in 5a, Write the {text, recurrence_count} JSON array to: $STAGING"
echo "If you forwarded no checks, do NOT create that file — the export will emit the notes/skips digest only."
```

**5c. Run the export.** It re-derives the same deterministic paths (each bash block is a fresh shell), passes
`--approved-checks` ONLY when 5a actually wrote the staging file (an `-f` existence test — so the notes/skips-only mode
is reachable, not an always-on flag), then points you at the REDACTED artifact to mail.

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
STAGING="$TMPD/aisdlc-calib-approved.json"        # same deterministic path 5b printed / 5a wrote
OUT="$TMPD/aisdlc-calibration-upstream.md"
if [ -f "$STAGING" ]; then
  $PY "${CLAUDE_SKILL_DIR}/scripts/calibration_export.py" --vault "$VAULT" --approved-checks "$STAGING" --out "$OUT"
else
  $PY "${CLAUDE_SKILL_DIR}/scripts/calibration_export.py" --vault "$VAULT" --out "$OUT"
fi
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "calibration_export REFUSED (rc=$rc) — see the redaction manifest on stderr above; NOTHING was emitted." >&2
  echo "Common causes: an empty/hollow log (no notes/skips AND no included checks), or an un-genericized token in a check." >&2
else
  echo "Wrote a REDACTED, maintainer-ready calibration digest to: $OUT"
  echo "Review it, then mail it to the maintainer (s2.shubh2@gmail.com) so recurring, generic checks can be folded into"
  echo "the base agents/critique.md in the next plugin version. Attach the REDACTED export ($OUT) — NOT the raw log."
fi
```

This is a **suggestion, not a gate** — the project already benefits now via the vault overlay; mailing the redacted
export is only how generic checks reach the shipped `agents/critique.md`. **Never mail the raw
`critic-calibration-log.json`** — it carries project-private free text; the declassified export is the safe boundary.
Never send mail automatically; the user decides. The manifest + this wording make **no "fully scrubbed" claim** —
paths/code safety comes from the allowlist + the consent gate; `secret_scrub` is a credential-only backstop applied
last.

## Critical rules

- USE the Agent tool with `subagent_type: "critic-calibrate"`. Do not re-implement the classification rubric here.
- NEVER edit `agents/critique.md` from a project. Accepted checks go to the vault overlay (`active_checks`, Step 4a); generic ones reach the base ONLY via the maintainer-mailed **redacted export** (Step 5) — never the raw log.
- ONE proposal at a time. Never bundle all three into one `AskUserQuestion`.
- TRUST the agent's zero-proposal outcome. Don't re-prompt.
- EVIDENCE-BASED only. Every proposal cites miss counts (ADD) or precision/quiet-rate + slice numbers (LIGHTEN), never hypothetical.
- ALWAYS append the run to `runs[]` (Step 4b), including zero-proposal runs. Never overwrite a prior run.
- **The reality spine never lightens or skips.** LIGHTEN and GATE-SKIP proposals may target only `critique`/`critique-review`/`code-review` dimensions/gates + project `active_checks`. `risk-spike`/`validate-slice` are excluded by the 1e filter and must never be lightened or skipped — LIGHTEN *informs* the Critic / retires a noisy check; GATE-SKIP stops a model gate's *discretionary* per-slice spawn but never removes a compliance-mandatory trigger (`critic_required`/Heavy/high-tier), disables a gate wholesale, or overrides the mode/tier table.

## Anti-patterns

- **Bundled proposals**: present them separately; users accept/reject each independently.
- **Editing the plugin base from a project**: writing to `agents/critique.md` — the edit is lost on the next plugin upgrade AND dirties the code repo on `master` (breaks the slice-worktree contract). Accepted checks belong in `active_checks` (vault overlay); generic ones travel to the maintainer via the Step-5 redacted export (never the raw log).
- **Generic additions**: "pay more attention to edge cases" is useless; "Check HEIC EXIF orientation for iPhone upload paths (missed in slice-019, -021, -025)" is useful.
- **Over-calibrating**: if nothing accepted 3 runs in a row, widen the window (25-30 slices) or skip until more data accumulates.

## Pipeline position

- predecessor: none (standalone maintenance; typically run every 10-20 slices after `/reflect`)
- successor: none (`hands_off_to: []`)
- auto-advance: false
- user-input gates: proposal review in Step 3 (one gate per proposal — up to 3 ADD + 2 LIGHTEN + 1 GATE-SKIP per run)
