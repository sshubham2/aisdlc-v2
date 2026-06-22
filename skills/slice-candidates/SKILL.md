---
name: slice-candidates
description: "Reads an annotated diagnosis.html (produced by /diagnose and returned by the repo owner), extracts confirmed findings, and appends DAG-ordered slice candidates to <vault>/candidates.json. Uses code-review-graph (CRG) blast-radius queries to detect file-overlap coupling, topo-sorts by dependency + severity/blast/effort priority, and flags must-do-together cycles. An optional --obo mode walks the owner through findings one at a time via structured prompts, writing diagnosis.annotated.html before building the backlog."
when_to_use: "Trigger phrases: /slice-candidates, 'generate slice candidates', 'build the backlog from diagnosis', 'turn confirmed findings into slices', 'what should we fix first'. Use after /diagnose has been run, the HTML report has been sent to the repo owner, and the owner has annotated and returned the saved file. Prerequisite: diagnose-out/diagnosis.html with at least one Confirmed: yes finding. Pass --obo to walk findings interactively instead of batch-processing."
argument-hint: "[path-to-diagnose-out — omit to use ./diagnose-out] [--obo for guided one-finding-at-a-time review]"
allowed-tools: Bash, Read, AskUserQuestion
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
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" count --vault "$AI_SDLC_VAULT_ROOT" --file candidates.json --array candidates 2>/dev/null || echo "0 (candidates.json not yet created)"
```

## Step 0 — Resolve paths

```bash
DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"
```

> Shell vars do NOT persist across separate ```bash blocks, and skill args are 0-based Claude Code
> substitutions (`${ARGUMENTS[0]}` = the first arg; `$1` is the *second*). So each block below re-derives
> `DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"`, and bundled scripts use `${CLAUDE_SKILL_DIR}` directly.

Verify:
- `$DIAGNOSE_OUT/diagnosis.html` exists and contains an embedded `<script type="application/json" id="diagnose-data">` block
- At least one entry has `confirmed=yes`

If either check fails, report the specific reason and stop. If the HTML exists but has no `Confirmed: yes`
entries, tell the user to confirm this is the **saved-and-downloaded** version from the owner (not the
original sent — the owner must click "Save annotated HTML" to bake in their annotations).

If `--obo` is in the arguments, skip to [--obo interactive review mode](#--obo-interactive-review-mode).

## Step 1 — Run the backlog builder

```bash
DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"
$PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" \
    --vault "$AI_SDLC_VAULT_ROOT" --crg-graph "$DIAGNOSE_OUT/.code-review-graph/"
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
- `appended` new candidates (and `skipped_existing` already-present findings, if any)
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
DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"
$PY "${CLAUDE_SKILL_DIR}/scripts/build_backlog.py" --in "$DIAGNOSE_OUT" --obo-extract
```

Returns JSON `{total, reviewed, findings:[…]}`. Each finding carries: `reviewed` (true if already in
annotations), `current` (`{confirmed, notes}`), and `evidence_paths` (the `--obo-peek` allow-set).

On resume: skip every finding with `reviewed: true`; start at the first `reviewed: false`.

### obo-2 — Per-finding prompt loop

For each unreviewed finding in order, present it (title, severity, description, suggested action,
evidence) and ask via `AskUserQuestion` with a header:

> `Finding k of N · A approved · D deferred · R rejected`

Options:
- **Approve** → record `{confirmed:"yes", notes:""}` (or owner's note if given).
- **Validate then approve** → run:
  ```bash
  DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"
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
DIAGNOSE_OUT="${ARGUMENTS[0]:-./diagnose-out}"
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

## Candidate shape (reference)

Each appended candidate matches `examples/slice-candidates.json`:

```
id: SC-NNN  title  status: candidate  progress: not-started
source[{type, ref}]  description  rationale  dependencies[SC-NNN]
priority{score, effort, blast_radius}
assumptions[{id, statement, risk_ref, blocking, spike_status, spike_verdict, spike_constraints}]
verification_plan  history[{event, by, at}]
```

Source type is `finding` (from diagnosis); ref is the finding id (e.g. `F-XXX-abc12345`).

## Anti-patterns

- Do NOT infer confirmation — blank `Confirmed` = not confirmed.
- Do NOT fold multiple findings into one candidate — keep them separate with a dependency edge.
- Do NOT add candidates not derived from confirmed findings (no "while you're here" additions).
- Do NOT write `backlog.md` — the v2 output is `<vault>/candidates.json`.
- Use CRG (`diagnose-out/.code-review-graph/`) for blast-radius queries; do NOT call the old vault-graph CLI or the multimodal-ingest path.

## Pipeline position

- predecessor: `/diagnose` (produces `diagnose-out/`) · successor: `/slice`
- auto-advance: false — user decides when to invoke `/slice` after reviewing the candidate list
- user-input gates: `--obo` per-finding loop (obo-2); otherwise no interactive gate (batch path is mechanical)
- on-clean-completion: report the backlog summary; suggest `/slice` to start the first cut
