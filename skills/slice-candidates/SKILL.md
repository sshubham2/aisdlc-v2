---
name: slice-candidates
description: "Builds the slice-candidate backlog in <vault>/candidates.json from two sources. From an annotated diagnosis.html (produced by /diagnose and returned by the repo owner): extracts confirmed findings, uses code-review-graph blast-radius queries to detect file-overlap coupling, topo-sorts by dependency + severity/blast/effort priority, and flags must-do-together cycles; --obo walks the owner through findings one at a time. From <vault>/concept.json: --product decomposes the PRODUCT's own declared scope into candidate-shaped items ONCE, persists them with receiver-minted PS-NNN ids, and idempotently materializes them as PRODUCT-sourced candidates — without which a backlog fills with pipeline exhaust and the product itself is never pickable."
when_to_use: "Trigger phrases: /slice-candidates, 'generate slice candidates', 'build the backlog from diagnosis', 'turn confirmed findings into slices', 'what should we fix first', 'the backlog has no product work in it', 'materialize the product scope', 'why is the roadmap not in the backlog'. Finding path: use after /diagnose has been run and the owner has annotated and returned the saved HTML (prerequisite: diagnose-out/diagnosis.html with >=1 Confirmed: yes finding); pass --obo to walk findings interactively. Product path: pass --product after /discover has written concept.json (it hands off here) — needs no diagnosis at all."
argument-hint: "[path-to-diagnose-out — omit to use ./diagnose-out] [--obo for guided one-finding-at-a-time review] [--product to materialize the product's own scope from concept.json]"
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# /slice-candidates — Build slice candidates from an annotated diagnosis

Reads `diagnose-out/diagnosis.html`, extracts confirmed findings, detects coupling via CRG blast-radius,
topo-sorts into a DAG-ordered backlog, and appends candidates to `<vault>/candidates.json`.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git-common-dir `aisdlc/vault-root`
> config). Resolve it at runtime; do NOT hard-code the path.

## Hard rules

1. **Never modify source files** in the analyzed repo.
2. **Never read analyzed-repo source files directly.** All inputs come from `diagnose-out/`
   (`diagnosis.html`, `findings/*.yaml` fallback, `diagnose-out/.code-review-graph/`).
   **ADR-054 carve-out:** `--obo` "Validate then approve" may read a finding's cited evidence files,
   but ONLY via `build_backlog.py --obo-peek` (mechanically allow-set-gated). No direct `Read` of source.
3. **Never modify `diagnosis.html` or `findings/*.yaml`.** They are read-only inputs.
4. **Only consume `Confirmed: yes` rows.** `no`, `defer`, and blank are filtered out.
5. **One confirmed finding → one candidate.** Do not auto-consolidate; the DAG makes opportunities visible.
6. **Output is `<vault>/candidates.json`**, written via `vault_edit` (SVW-1 append-safe). Not `backlog.md`.

## Live state — injected

Existing candidate count (pre-run baseline):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" count --file candidates.json --array candidates 2>/dev/null || echo "0 (candidates.json not yet created)"
```

## Step 0 — Resolve paths + flags (ONE parse, both outputs)

```bash
DIAGNOSE_OUT=""; OBO=0; PRODUCT=0
for a in ${ARGUMENTS[@]}; do case "$a" in --obo) OBO=1 ;; --product) PRODUCT=1 ;; --*) ;; *) [ -z "$DIAGNOSE_OUT" ] && DIAGNOSE_OUT="$a" ;; esac; done
[ -n "$DIAGNOSE_OUT" ] || DIAGNOSE_OUT="./diagnose-out"
echo "DIAGNOSE_OUT=$DIAGNOSE_OUT OBO=$OBO PRODUCT=$PRODUCT"
```

> Shell vars do NOT persist across separate ```bash blocks, and skill args are 0-based Claude Code
> substitutions (`${ARGUMENTS[0]}` = the first arg; `$1` is the *second*). So each block below re-derives
> `DIAGNOSE_OUT` with this SAME non-flag scan — never bare `${ARGUMENTS[0]}`: `/slice-candidates --obo`
> (both args are optional) would make the flag the path (`DIAGNOSE_OUT="--obo"`), and bare `$ARGUMENTS`
> under an array binding sees only token 0. `${ARGUMENTS[@]}` unquoted is array-safe AND scalar-safe.
> Bundled scripts use `${CLAUDE_SKILL_DIR}` directly.

If the Step-0 parse printed `PRODUCT=1`, skip to
[--product mode](#--product-mode--materialize-the-products-own-scope-slice-068--adr-067). It reads
`<vault>/concept.json`, **not** `diagnose-out/` — so the diagnosis checks below do not apply and must not be run.
(`--product` and `--obo` are peers; if both are passed, run `--product` and tell the user `--obo` was ignored.)

Verify (the finding path only):
- `$DIAGNOSE_OUT/diagnosis.html` exists and contains an embedded `<script type="application/json" id="diagnose-data">` block
- At least one entry has `confirmed=yes`

If either check fails, report the specific reason and stop. If the HTML exists but has no `Confirmed: yes`
entries, tell the user to confirm this is the **saved-and-downloaded** version from the owner (not the
original sent — the owner must click "Save annotated HTML" to bake in their annotations).

If the Step-0 parse printed `OBO=1`, skip to [--obo interactive review mode](#--obo-interactive-review-mode)
(one parsing mechanism — never re-detect the flag by eyeballing the argument string).

## Step 1 — Run the backlog builder

```bash
DIAGNOSE_OUT=""; for a in ${ARGUMENTS[@]}; do case "$a" in --*) ;; *) [ -z "$DIAGNOSE_OUT" ] && DIAGNOSE_OUT="$a" ;; esac; done; [ -n "$DIAGNOSE_OUT" ] || DIAGNOSE_OUT="./diagnose-out"   # Step-0 non-flag scan (never bare ${ARGUMENTS[0]} — a flag would become the path)
$PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" \
    --crg-graph "$DIAGNOSE_OUT/.code-review-graph/"
```

`build_backlog.py` does it all in one run (no separate append step — it routes the write through the SVW-1
locked channel itself):
1. Parses the embedded JSON state from `diagnosis.html`; falls back to `findings/*.yaml` only if no embedded list.
2. Filters to `confirmed=yes` (annotation `confirmed=="yes"`; `no`/`defer`/blank dropped).
3. **Couples** findings via two UNDIRECTED signals: **shared evidence files** (findings citing the same file)
   and **CRG blast-radius** (a finding's evidence appears in another's blast-radius, via the prebuilt graph in
   `diagnose-out/.code-review-graph/`). If CRG is unavailable it degrades to shared-evidence-only and reports
   `"crg":"degraded"|"absent"` in the summary.
4. Groups coupled findings into connected **clusters** ("must-do-together"); orders clusters (and findings within
   them) by priority `severity_rank×10 + blast_rank − effort_rank` (desc); SC-NNN ids are assigned in that order.
   **Thickness heuristic (Theme 4):** a coupled cluster whose members are EACH thin (small effort, non-large blast)
   is over-sliced — slicing them apart pays N× the context-rebuild + critique/review/validate overhead for one shared
   code seam — so it is flagged for **MERGE** (advisory: a `THIN+COUPLED … consider MERGING` rationale note + a
   `merge_recommendations` summary entry). Candidates are **never** auto-consolidated (Hard Rule #5 holds — one finding
   → one candidate, DAG intact); the user merges at `/slice` time if they agree.
5. Constructs one candidate per finding (schema: `examples/slice-candidates.json`), with `source:[{type:"finding",
   ref}]`, a normalized 1–10 `priority.score`, `dependencies:[]` (a confirmed finding is an independent, coupled-not-
   blocked fix — so candidates.json stays a valid DAG), and a "Must do together with SC-X" rationale note for cluster
   mates. It **appends them atomically** to `<vault>/candidates.json` via the SVW-1 locked read-modify-write,
   assigning ids + de-duplicating against findings already turned into candidates (live ∪ `archive/candidates.json`)
   inside the lock.
6. Emits a JSON summary to stdout: `{appended, appended_ids, skipped_existing, confirmed_findings, clusters,
   merge_recommendations:[{ids,size,action:"merge"|"group"}], crg, order, top:{id,title,rationale}}`. Relay it in Step 2.

## Step 2 — Report to user

Relay the builder's JSON summary:

- Path to `<vault>/candidates.json` (`vault_file`) — updated
- `appended` new candidates — and when `skipped_existing` > 0, list `skipped_existing_ids`
  (each `{finding, existing_candidate}`): an owner re-running after a partial annotation round needs to
  see WHICH findings were deduped against existing candidates, not just how many
- `crg` mode (`used` / `degraded` / `absent`) — note the degradation if not `used`
- `clusters` — any "must-do-together" SC groups
- `merge_recommendations` — thin coupled clusters (Theme 4): list each as "consider **merging** SC-X+SC-Y into one
  slice" (`action:"merge"`, size ≤3) or "**group** SC-X…SC-Z into fewer slices" (`action:"group"`, size ≥4). Frame
  it as a recommendation the owner weighs at `/slice` time — the candidates remain separate; nothing was merged.
- `top` — the first candidate by recommended order (`id` + `title` + one-line `rationale`)

## --obo interactive review mode

Invoked when `--obo` appears in arguments. Walks findings one-at-a-time, severity-ordered
(critical → high → medium → low, stable within band). Bakes decisions into `diagnosis.annotated.html`,
then optionally runs Step 1 on the annotated copy.

> Under `--obo`, you MUST NOT `Read` any analyzed-repo source file directly. `--obo-peek` is the
> only permitted source-read channel (ADR-054 carve-out, mechanically allow-set-gated).

### obo-1 — Extract findings

```bash
DIAGNOSE_OUT=""; for a in ${ARGUMENTS[@]}; do case "$a" in --*) ;; *) [ -z "$DIAGNOSE_OUT" ] && DIAGNOSE_OUT="$a" ;; esac; done; [ -n "$DIAGNOSE_OUT" ] || DIAGNOSE_OUT="./diagnose-out"   # Step-0 non-flag scan (never bare ${ARGUMENTS[0]} — a flag would become the path)
$PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" --obo-extract
```

Returns JSON `{total, reviewed, findings:[…]}`. Each finding carries: `reviewed` (true if already in
annotations), `current` (`{confirmed, notes}`), and `evidence_paths` (the `--obo-peek` allow-set).

On resume: skip every finding with `reviewed: true`; start at the first `reviewed: false`.

### obo-2 — Per-finding prompt loop

For each unreviewed finding in order, present it (title, severity, description, suggested action,
evidence) and ask via `AskUserQuestion` with a header:

> `Finding k of N · A approved · D deferred · R rejected`

**Anti-alert-fatigue (the /critique lesson applies verbatim):** walk HIGH/CRITICAL findings individually,
but **batch the LOW-severity tail** as ONE group question — "*M low-severity findings remain, all
<category summary> — approve all / defer all? (or name any id to review individually)*". Forcing a
keystroke per low finding trains the rubber-stamp reflex that makes the whole review theater.

Options:
- **Approve** → record `{confirmed:"yes", notes:""}` (or owner's note if given).
- **Validate then approve** → run:
  ```bash
  DIAGNOSE_OUT=""; for a in ${ARGUMENTS[@]}; do case "$a" in --*) ;; *) [ -z "$DIAGNOSE_OUT" ] && DIAGNOSE_OUT="$a" ;; esac; done; [ -n "$DIAGNOSE_OUT" ] || DIAGNOSE_OUT="./diagnose-out"   # Step-0 non-flag scan
  $PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" \
      --obo-peek --finding <id> --file <path>
  ```
  for the finding's cited evidence path(s) ONLY (refuses out-of-set paths with non-zero exit + logged refusal).
  `Read` the returned content; give a real / likely-real / not-real verdict with reasoning.
  Re-offer Approve / Defer / Reject for this finding.
- **Defer** → record `{confirmed:"defer", notes:""}`.
- **Reject** → capture a free-text "why"; record `{confirmed:"no", notes:"<why>"}`.
- **Exit** → stop the loop; proceed to obo-3 with decisions gathered so far.

**Defer is terminal for resume.** A Deferred finding is written as `confirmed:"defer"` and is treated as
reviewed on any later `--obo` run against the annotated copy — it will NOT be re-offered. State this to
the owner when they choose Defer.

### obo-3 — Write annotated copy

Collect decisions into `{finding_id: {confirmed, notes}}` (omit never-reached findings — do NOT write
empty entries). Serialize via Bash here-doc into a temp file, then run:

```bash
DIAGNOSE_OUT=""; for a in ${ARGUMENTS[@]}; do case "$a" in --*) ;; *) [ -z "$DIAGNOSE_OUT" ] && DIAGNOSE_OUT="$a" ;; esac; done; [ -n "$DIAGNOSE_OUT" ] || DIAGNOSE_OUT="./diagnose-out"   # Step-0 non-flag scan (never bare ${ARGUMENTS[0]} — a flag would become the path)
# slice-026: portable temp dir ($PY gettempdir(), forward-slash) so the file mktemp creates in
# git-bash is read back by the bundled Windows-Python tool at the SAME real path -- safe because the
# SAME $PY resolves gettempdir() for both the bash $TMPD derivation and the in-tool reads. Keep the
# XXXXXX randomness + the .json suffix (mktemp preserves a suffix after the X's).
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
TMPFILE="$(mktemp "$TMPD/obo-decisions-XXXXXX.json")"
cat > "$TMPFILE" << 'DECISIONS_EOF'
<decisions-json>
DECISIONS_EOF
$PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" \
    --obo-write --decisions "$TMPFILE"
rm -f "$TMPFILE"
```

This bakes decisions into `diagnose-out/diagnosis.annotated.html`, asserts `diagnosis.html` is
byte-unchanged, and fails closed on malformed input. Appends to `diagnose-out/obo-run.log`.

Report: remaining-unreviewed count, distinguishing **never-reached** from **Deferred**.

### obo-4 — Continue (optional)

If at least one finding was Approved, offer to run Step 1 immediately against the annotated copy.
Otherwise stop and tell the owner where the annotated copy lives.

## --product mode — materialize the PRODUCT's own scope (slice-068 / [[ADR-067]])

Invoked when `--product` appears in arguments. **A peer of `--obo`, not a sub-step of the finding path**: it reads
`<vault>/concept.json`, not `diagnose-out/`, and it needs no diagnosis at all.

**Why this mode exists.** A census of every candidate ever minted across two real vaults found **PRODUCT-sourced
candidates = 0, out of 145**. `/discover` mints exactly one product candidate (`concept.json`'s
`first_slice_candidate`) — which fires once, at slice 1, and never again. Everything after it is *exhaust*: risks,
code-review findings, reality-surprises, reflection residues. In one real product, the orchestrator it exists to be
was never minted as a candidate at all — it survived only as a line in slice-001's `out_of_scope` — so `/slice`
structurally **could not pick it**, and eleven slices went to peripheral hardening while the core app stayed
unbuilt. This mode makes the product's scope *appear in the list*. The pick gate stays user-owned; presence is
what was missing.

**The model does the decomposition; the vault owns the identities.** `build_backlog.py` is deterministic and
cannot read prose, so only a model context can turn a concept's narrative into candidate-shaped items — but a model
key cannot be trusted across runs. Two BLIND decompositions of the *same* concept agreed on only **22%** of their
keys, and five of seven semantically-identical items — *including the orchestrator itself* — drifted. So the model
runs **once**, and every id is minted by the receiver, in-lock.

### product-1 — Get the decomposition context

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" decompose-context --json
```

- **exit 3** → `concept.json` is absent. STOP and tell the user to run `/discover` first. Do not invent a concept.
- **exit 0** with `already_decomposed: true` → the scope was already crossed in. `persist` is CREATE-ONLY; to extend
  or correct the scope use `revise` (see product-4). Report the existing items and stop.
- **exit 0** otherwise → proceed with the returned `concept`.

### product-2 — Decompose the concept into product items

Read the concept's `what`, `non_goals`, `actors[].top_actions`, and `constraints`. Print the items-file path:

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
echo "ITEMS=$TMPD/aisdlc-product-scope-items.json"   # FIXED name: every block below re-derives it identically (vars do NOT persist across bash blocks)
```

Now **`Write`** the decomposition to that exact absolute path (use `Write`, not a bash heredoc — item prose
contains apostrophes, which a heredoc mangles):

```json
{ "items": [
  { "label": "<your run-local id, e.g. payments-core>",
    "title": "<verb-led candidate title, e.g. build-payments-core>",
    "description": "<what this capability IS, in one or two sentences>",
    "user_visible_outcome": "<what a real user can DO once it exists>",
    "depends_on": ["<label of another item in THIS list>"],
    "assumptions": [ { "id": "A1", "statement": "<the blocking feasibility premise>",
                       "blocking": true, "spike_status": "unproven" } ],
    "verification_plan": "<how reality confirms it works>" } ] }
```

Rules, each of which is load-bearing — `persist` REFUSES the file (exit 2) if any is broken:

- **NEVER emit an `id`.** Identity is minted in-lock by the receiver; `persist` rejects a supplied id. Your `label`
  is an intra-call correlation id for `depends_on` only — it is discarded as an identity the moment the lock closes.
- **Every `label` and `title` must be UNIQUE.** Two items sharing an identity would alias onto one minted candidate.
- **Every `depends_on` must name a `label` in THIS list.** An unresolvable dependency is a hard error, never a
  silent drop: dropping it would make the item a false DAG *root* and surface unready work first at the pick gate.
- **Every item MUST carry at least one blocking, unproven assumption.** `/risk-spike` step-0 SKIPS a candidate with
  none — so `assumptions: []` would walk the least-understood work in the product straight past the pipeline's
  reality gate. A finding-derived candidate is a proven bug with nothing to spike; a product capability is unproven
  by definition and has everything to spike.
- **Emit the `depends_on` DAG.** It yields the critical path: items are minted in topological order, so the roots —
  the things everything else waits on — surface first at the pick gate.
- Decompose the product as it IS scoped, not as you would scope it. Respect `non_goals`.

### product-3 — Cross it into the vault (the ONCE-ACT)

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
ITEMS="$TMPD/aisdlc-product-scope-items.json"   # re-derived, not inherited (fresh shell per block)
[ -s "$ITEMS" ] || { echo "STOP: $ITEMS is missing or empty -- Write the product-2 decomposition first." >&2; exit 1; }
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" persist --items-file "$ITEMS" --json
rc=$?; rm -f "$ITEMS"; exit $rc
```

One command, PERSIST **and** MATERIALIZE: it mints a `PS-NNN` per item, rewrites `depends_on` into those ids, writes
`<vault>/product-scope.json`, then mints one candidate per item into `<vault>/candidates.json` — deduped on
provenance `source: [{type: "product-scope", ref: "PS-NNN"}]` across live ∪ archive, so a re-run mints nothing and a
*shipped* item is never resurrected. (These are two per-file locked writes, not one transaction: if it dies between
them, re-run `materialize` — that is exactly why the verb exists.)

A non-zero exit means nothing was written — fix the items file and re-run. Report to the user: the minted candidate
ids, any **REFUSED** items (a candidate already carries that item's title without the expected provenance — minting
would duplicate or resurrect it; re-run with `--acknowledge PS-NNN` if they are genuinely different), and any items
**withheld** behind a refusal. Then suggest `/slice`.

### product-4 — Later: reconcile, correct, measure

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" materialize --json   # idempotent re-mint (safe any time)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" census --json        # PRODUCT / EXHAUST / HUMAN split
```

To **extend or correct** the scope, repeat product-2 (`Write` the **full** revised item list to the same `$ITEMS`
path, carrying **every** kept item's minted `id` verbatim), then:

> **`revise` is a WHOLE-LIST replace, not a delta — and it REFUSES a payload that leaves a live item out.**
> Re-state every item you mean to KEEP. An omitted item is not "unchanged": until slice-073 it was silently DELETED
> from the product's scope of record at exit 0, with no trace. To remove one, say so explicitly with `--cut` (below).

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
ITEMS="$TMPD/aisdlc-product-scope-items.json"
[ -s "$ITEMS" ] || { echo "STOP: $ITEMS is missing or empty -- Write the revised decomposition first." >&2; exit 1; }
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" revise --items-file "$ITEMS" --json
rc=$?; rm -f "$ITEMS"; exit $rc
```

`revise` preserves already-minted `PS` ids **by id** and never re-mints; new items omit `id` as usual. An `id` this
vault never minted is REFUSED (the model may reuse an identity the receiver gave it, never invent one), and so is a
REPEATED one, and so is one that was previously `--cut` (a cut id's candidate may already be shipped, so reviving it
would alias two capabilities onto one record).

**To REMOVE a scope item, cut it explicitly** — repeatable, and `--reason` is required (an added item is
self-describing; a cut destroys the only record of what was there):

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
ITEMS="$TMPD/aisdlc-product-scope-items.json"
[ -s "$ITEMS" ] || { echo "STOP: $ITEMS is missing or empty -- Write the revised decomposition first." >&2; exit 1; }
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" revise --items-file "$ITEMS" \
    --cut PS-003 --reason "descoped -- the concept revision dropped in-app refunds" --json
rc=$?; rm -f "$ITEMS"; exit $rc
```

Every accepted membership change appends a record to `product-scope.json`'s **append-only `revisions[]`**
(`{at, cut[], added[], reason, items_before, items_after}`) — prior entries are never rewritten. A membership-preserving
revise (e.g. a description edit) appends nothing. That ledger is load-bearing, not documentation: its `cut` ids are the
`PS` retirement history the id allocator scans, so a retired id is never re-issued onto a shipped candidate.

A **cut** scope item leaves its candidate untouched — the backlog is append-only and it may already be shipped — and
`materialize` reports it as `orphaned`, taking no action.

Every refusal names the offending id(s) on stderr with a non-zero exit and leaves `product-scope.json` byte-identical.
The same protection covers the sibling write paths: `vault_edit remove` / `set --path items` / `append` on
`product-scope.json`/`items` all REFUSE ([[ADR-080]]) — scope items are written only by `persist`/`revise`, which
enforce the decomposition contract.

### product-5 — `set-component`: annotate a capability's component (makes the `/slice --component` lens non-degenerate)

After `materialize`, every capability lands in the reserved **`unassigned`** stratum, so the capability-progress
rollup reads `Whole app 0/N … N unassigned` — correct but **inert**. Run **`set-component`** to assign a real component
to a capability, so the per-component progress view and the `/slice --component <NAME>` lens become non-degenerate
(slice-081 / [[ADR-092]]):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/product_scope.py" set-component --item PS-NNN --component "<NAME>" --json
```

- `set-component` annotates ONE already-materialized capability **in place** (atomic `safe_mutate_text` write, bumps `revised_at`); it
  mints nothing, re-materializes nothing, and appends no `revisions[]` entry — the read-side consumers
  (`product_rollup`, `candidates_top --component`) join the component at READ time via `owner_refs`.
- The `--component` name is validated at the write seam: an **empty/whitespace** name, or the reserved sentinel
  **`unassigned`** (case-insensitive), is **REFUSED** (non-zero exit) and leaves `product-scope.json` byte-identical.
- Re-annotating a capability to the **same** value is an idempotent no-op (`changed:false`, byte-identical, no
  `revised_at` churn); a **reassignment** echoes the prior component in the command result.
- Un-annotated capabilities stay in `unassigned` (backward-compatible; a zero-annotation rollup is byte-identical).
  After annotating ≥1 capability, re-run `/slice --component <NAME>` (or `product_scope done`) to see the lens.

## --demote mode — 'good enough for now' (slice-077 / [[ADR-088]])

Lower a genuinely-low-value **off-path** candidate's backlog rank by a bounded score-space term
(−4 at the `candidates_top` pick surface) WITHOUT deleting anything — the append-only risk-register
entry is never opened for write. The demote is recorded as two presence-symmetric sibling fields
(`demoted_at` + `demote_reason`) plus an append-only `demoted` history event; **off-path is derived
from their presence** — there is no stored path-class enum.

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/demote_candidate.py" --candidate SC-NNN --reason "<why it can wait>"
```

- The `--reason` is **required and non-empty** (a reason-less demote is refused — the record is
  append-only and auditable).
- **ELIGIBILITY GUARD** — the demote is REFUSED (fail-visible, non-zero) when the target is
  **product-scope-sourced** (on-path: a core product capability is not a "good enough for now" risk)
  OR in the **critical band** (severity `critical` or score `>=9`). A critical bug is therefore
  structurally non-demotable, so a **non-demoted critical always tops the board**. A genuinely
  critical **security** bug materializes at score 9, so the critical band protects it too; a
  *sub-critical* security item carries no structured signal on a materialized candidate (the finding
  category lives in `rationale` free text, not a field), so it is demotable by deliberate, reversible,
  audited user judgment — the guard reads structured severity/score only, never free text.
- Only a **pickable** target (`candidate`/`deferred`) may be demoted; an active/spiking/blocked target
  is refused. A re-demote with the **same** reason is an idempotent no-op; a **different** reason is
  refused (the existing record is never silently overwritten). An unknown id fails visible.
- The write routes through the SVW-1 locked seam; `risk-register.json` is intentionally NOT a write
  target (AC5 preserved by construction).

To undo a demote, remove BOTH `demoted_at` and `demote_reason` (they are presence-symmetric — the
`artifact_lint` co-constraint fails a half-cleared pair).

## Candidate shape (reference)

Each appended candidate matches `examples/slice-candidates.json`:

```
id: SC-NNN  title  status: candidate  progress: not-started
source[{type, ref}]  description  rationale  dependencies[SC-NNN]
priority{score, effort, blast_radius}
assumptions[{id, statement, risk_ref, blocking, spike_status, spike_verdict, spike_constraints}]
verification_plan  history[{event, by, at}]
```

Source type is `finding` (from diagnosis; ref = the finding id, e.g. `F-XXX-abc12345`) or **`product-scope`**
(from `--product`; ref = the allocator-minted `PS-NNN`). `product-scope` **supersedes** the never-emitted
`concept-scope` (ADR-067). Its ref is the materializer's idempotency key, so it must survive the ship→archive move.
The persisted scope itself matches `examples/product-scope.json`.

## Anti-patterns

- Do NOT infer confirmation — blank `Confirmed` = not confirmed.
- Do NOT fold multiple findings into one candidate — keep them separate with a dependency edge.
- Do NOT add candidates not derived from confirmed findings (no "while you're here" additions).
- Do NOT write `backlog.md` — the v2 output is `<vault>/candidates.json`.
- Use CRG (`diagnose-out/.code-review-graph/`) for blast-radius queries; do NOT call the old vault-graph CLI or the multimodal-ingest path.
- `--product`: do NOT emit an `id` on a scope item, and do NOT hand-write `<vault>/product-scope.json` — identity is
  minted in-lock by the receiver, and `persist` rejects a supplied id. Do NOT emit an item with `assumptions: []`
  (it would skip `/risk-spike` step-0). Do NOT re-run `persist` to "refresh" the scope — it is create-only; use
  `revise`. Do NOT auto-claim or auto-pick a minted candidate: materialization makes the product *pickable*, and the
  pick gate stays user-owned.

## Pipeline position

- predecessor: `/diagnose` (produces `diagnose-out/`) for the finding path; **`/discover`** (writes `concept.json`, then
  hands off here) for `--product` · successor: `/slice`
- auto-advance: false — user decides when to invoke `/slice` after reviewing the candidate list
- user-input gates: `--obo` per-finding loop (obo-2); otherwise no interactive gate (both batch paths are mechanical)
- on-clean-completion: report the backlog summary; suggest `/slice` to start the first cut
