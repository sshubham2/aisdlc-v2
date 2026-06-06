---
name: diagnose
description: "Deep, owner-facing forensic analysis of a target codebase. Builds a code graph via code-review-graph (CRG), runs 11 structured analysis passes (10 general-purpose subagents + one cross-reference pass) plus a diagnose-narrator for the executive summary, then assembles a single self-contained interactive diagnosis.html. Findings are confirmed by the repo owner in-browser; the annotated file feeds /slice-candidates. NEVER modifies source files."
when_to_use: "Trigger phrases: /diagnose, 'diagnose this codebase', 'audit this repo', 'deep analysis of legacy code', 'forensic codebase review', 'what's wrong with this codebase', 'owner-facing audit'. Use when adopting, auditing, or inheriting an existing repo and you want a comprehensive diagnostic deliverable for the repo owner. Prerequisite: a non-empty directory with code in it. Pass a repo path as $1 (default: cwd). Pass --parallel to opt into the legacy single-message parallel dispatch (default is sequential per ADR-027)."
argument-hint: "[path-to-repo] [--parallel]"
allowed-tools: Bash, Read, Glob, Grep, Write, Agent
---

# /diagnose — forensic codebase analysis

Produces **one self-contained interactive `diagnosis.html`** for the repo owner. Owner opens it in any
browser, fills `Confirmed` + `Notes` per finding, clicks "Save annotated HTML" to download the annotated
copy. They send it back; `/slice-candidates` reads the embedded JSON to build the candidate backlog.

This skill is a **diagnostic deliverable**. It never modifies source files in the target repo.

> Paths: `${CLAUDE_SKILL_DIR}/scripts/` holds `write_pass.py`, `assemble.py`, `passes/`, `schema/`.
> Shared vault tooling: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py"`. `$PY` is the shared venv interpreter (env var set by the harness).
>
> **Format note:** `sections/*.md`, `summary/*.md`, and `sections/00-overview.md` are intermediate pipeline
> files consumed by `assemble.py` for HTML rendering. They are prose markdown — NOT vault artifacts subject
> to the `.md → .json` rollout. `skill.json`'s `v2_format: "json"` annotation on these paths is a manifest
> classification error (they are HTML-pipeline intermediates, not machine-queryable vault content);
> the runtime format is and remains markdown. `findings/*.yaml` and `diagnosis.html` are unaffected.

## Hard rules

1. **Never modify source files in the target repo.** All writes go to `diagnose-out/` only.
2. **No documentation reads from the target codebase.** Skip `.md`, `.rst`, `.txt`, `.adoc`, `docs/` in
   `TARGET`. Everything is derived from source code, config files, schemas/migrations, and CRG queries.
   Inline docstrings and code comments may be read but never trusted as ground truth — they drift.
3. **All output is pipeline-agnostic.** The deliverable must be consumable by anyone with any toolchain.
4. **Stable content-derived finding IDs.** IDs derived from category + evidence path + key signature —
   not generation order. Re-runs produce the same ID for the same finding so owner annotations carry over.
5. **Never invent findings.** If a pass has no signal, it writes an empty findings file and a one-line
   summary. Do not pad with speculative issues.

## Step 1 — Resolve target + args

Parse args — flags are stripped before `TARGET` so a flag-shaped token never becomes the path:

```bash
PARALLEL=0; ARGS=()
for a in "$@"; do
  case "$a" in
    --parallel) PARALLEL=1 ;;
    --*) echo "WARNING: unknown flag '$a' ignored" >&2 ;;
    *) ARGS+=("$a") ;;
  esac
done
TARGET="${ARGS[0]:-$PWD}"
OUT="$TARGET/diagnose-out"
```

Verify `$TARGET` is a non-empty directory with code. Abort with a clear message if not.

### cwd-mismatch guard

```bash
TARGET_REAL=$(cd "$TARGET" 2>/dev/null && pwd) || { echo "TARGET does not exist: $TARGET"; exit 1; }
if [ "$TARGET_REAL" != "$(pwd)" ]; then
  echo "WARNING: TARGET ($TARGET_REAL) != PWD ($(pwd))."
  echo "Subagents may lose Read/Grep/Bash access (claude-code #57037 / R-1)."
  echo "Recommendation: cd to TARGET first, then re-invoke /diagnose."
fi
```

Surface the warning verbatim if it fires; proceed (do not abort). User may interrupt and re-invoke.

## Step 2 — Set up output structure

```
$TARGET/diagnose-out/
  sections/        ← per-pass prose (intermediate)
  findings/        ← per-pass structured YAML (intermediate)
  summary/         ← per-pass one-paragraph summaries (intermediate)
  .code-review-graph/  ← CRG graph
  .tmp/            ← raw subagent responses + .failed.raw files
  diagnosis.html   ← final assembled deliverable (Step 8)
  diagnosis.prev.html  ← prior run rotated by assemble.py
```

Create all subdirs. If `diagnose-out/diagnosis.html` already exists, **leave it in place** — `assemble.py`
reads its embedded JSON for annotation carryover (Confirmed/Notes).

## Step 3 — Build code graph + load pass templates

Build the CRG graph:

```bash
code-review-graph build "$TARGET" --out "$OUT/.code-review-graph"
```

If CRG fails (unsupported language, broken AST): report failure and ask whether to proceed in degraded
mode (passes that depend on the graph will produce reduced findings; clearly marked in the report).

**Read pass templates into memory now** — embed them into subagent prompts in Step 5. Subagents do NOT
read out-of-cwd files themselves (per ADR-001 / slice-001-diagnose-orchestration-fix). Use the Read tool
on the following from `${CLAUDE_SKILL_DIR}/scripts/passes/`:

```
01-intent.md  02-architecture.md  03a-dead-code.md  03b-duplicates.md
03c-size-outliers.md  03d-half-wired.md  03e-contradictions.md
03f-layering.md  03g-dead-config.md  03h-test-coverage.md
```

(Pass templates each embed a 5-line finding-schema crib sheet — do NOT embed the full `schema/finding.yaml`
separately; that would add ~30 KB of redundant context per run.)

## Step 4 — Detect prior run state

If `$OUT/diagnosis.html` exists from a prior run, note it. `assemble.py` handles carryover
automatically — it parses the embedded `<script type="application/json" id="diagnose-data">` block,
carries forward Confirmed/Notes on matching finding IDs, marks absent findings RESOLVED, and new findings
NEW. Leave the prior file in place.

## Step 5 — Dispatch analysis passes

Per ADR-001, each analysis subagent does **analysis only** — it returns three 4-backtick fenced blocks
(`section`, `findings`, `summary`) in its result. The subagent does NOT call Write, Bash, or python; it
does NOT read out-of-cwd files. The main thread writes all I/O via `write_pass.py` after each subagent.

**Dispatch mode** is controlled by `$PARALLEL` (Step 1):

- **Default (sequential, `$PARALLEL=0`):** Spawn each pass's `Agent`, wait for its result, run
  `write_pass.py`, then spawn the next. This avoids the parallel-spawn permission cascade-failure
  (claude-code #57037 / R-1) where spawned subagents lose Read/Grep/Bash access.
- **`--parallel` opt-in (`$PARALLEL=1`):** Dispatch all 10 passes as one message with multiple `Agent`
  calls. Faster wall-clock; re-exposes R-1. Caller has chosen this explicitly.

Per **COST-1.1**, each pass uses a model matched to its cognitive shape:

| Pass | Template | Model |
|------|----------|-------|
| 01-intent | `passes/01-intent.md` | opus |
| 02-architecture | `passes/02-architecture.md` | opus |
| 03a-dead-code | `passes/03a-dead-code.md` | sonnet |
| 03b-duplicates | `passes/03b-duplicates.md` | opus |
| 03c-size-outliers | `passes/03c-size-outliers.md` | sonnet |
| 03d-half-wired | `passes/03d-half-wired.md` | opus |
| 03e-contradictions | `passes/03e-contradictions.md` | opus |
| 03f-layering | `passes/03f-layering.md` | sonnet |
| 03g-dead-config | `passes/03g-dead-config.md` | sonnet |
| 03h-test-coverage | `passes/03h-test-coverage.md` | sonnet |

Spawn each as **`Agent` with `subagent_type: general-purpose`** and `model: <opus|sonnet>` from the table.

### Subagent prompt structure

Embed into each subagent's prompt:

1. The pass template content (loaded in Step 3)
2. `TARGET` and `OUT` paths
3. The canonical subagent contract (verbatim):

> **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for
> CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Pass-specific: 03f-layering LAYER-EVID-1

The `03f-layering` pass MUST apply the **textual import-evidence requirement** before emitting any
HIGH-severity layering-violation finding. The rule lives in `passes/03f-layering.md` (Method step 4 +
Severity rubric + Anti-patterns section). Required to prevent false-positives like F-LAYER-bca9c001
(parallel type files sharing a name but with no textual imports between them).

### After each subagent returns

For each completed pass (immediately in sequential mode; as they finish in parallel mode):

1. Save raw response to `$OUT/.tmp/<pass-name>.raw` (create `.tmp/` if missing).
2. Run:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/scripts/write_pass.py" \
       --pass <pass-name> \
       --out "$OUT" \
       --raw-file "$OUT/.tmp/<pass-name>.raw"
   ```
3. Helper extracts the three fenced blocks, recomputes malformed finding IDs deterministically, validates
   required fields, writes three pass files. Exit codes: 0 clean / 1 validation failure / 2 parse failure.
4. **3-attempt cap per pass (slice-001 critique M3):** On non-zero exit, re-spawn the pass and retry the
   writer. After 3 total attempts, save to `$OUT/.tmp/<pass-name>.failed.raw` and mark the pass degraded.
   Proceed — `assemble.py` will surface degraded passes in the final report. Do not loop forever.

Subagents must NOT read other passes' outputs and must NOT modify source files.

### Step 5.5 — Verify all 10 passes

After all 10 passes have written (or been marked degraded), verify outputs:

```bash
ls "$OUT/sections" "$OUT/findings" "$OUT/summary"
```

Expected: 10 entries in each directory. Check `$OUT/.tmp/` for `*.failed.raw` (degraded passes).

For any pass missing its three-file triple AND no `.failed.raw` (un-spawned due to interrupted loop):
**re-spawn it** (sequentially). Do NOT proceed to Step 6 with silent gaps — gaps become silent omissions
in the final report.

## Step 6 — Cross-reference pass: 04-ai-bloat

Depends on `findings/03b-duplicates.yaml` and `findings/03d-half-wired.yaml`. Run after Step 5.5
confirms both exist.

Spawn one `Agent` with `subagent_type: general-purpose`, `model: opus`. The 04-ai-bloat subagent MAY
read both prior YAML files from `$OUT/findings/`. Embed `passes/04-ai-bloat.md` content + paths +
the canonical "do NOT call Write / return three fenced blocks" contract.

After it returns, run `write_pass.py --pass 04-ai-bloat --raw-file $OUT/.tmp/04-ai-bloat.raw` with the
same 3-attempt cap. Verify all three `04-ai-bloat.{md,yaml,md}` files exist before continuing.

## Step 6.5 — Narrative synthesis: diagnose-narrator

After all 11 forensic passes have written their YAMLs + summaries, spawn:

```
Agent tool, subagent_type: "diagnose-narrator"
```

Hand it only the output path:

```
The /diagnose run for this codebase has completed all 11 analysis passes.
Output directory: $OUT

Read findings/*.yaml and summary/*.md from there. Synthesize a narrative
executive summary as described in your system prompt. Write it to
sections/00-overview.md.
```

The narrator's tone, structure, and length rules live in `agents/diagnose-narrator.md` — do NOT
re-state them here. The narrator has an explicit Write tool grant and writes only `sections/00-overview.md`
(this pattern is preserved per slice-001 / ADR-001 — named subagents with explicit tool declarations
operate normally; the fenced-block + write_pass.py pattern is for anonymous general-purpose subagents).

After it returns, verify `$OUT/sections/00-overview.md` exists. If absent, log the failure and
continue — `assemble.py` falls back to per-pass summary stitching (degraded but not broken).

## Step 7 — Assemble

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/assemble.py" --out "$OUT"
```

`assemble.py`:
1. Reads section/finding/summary files in manifest order.
2. Reads prior `diagnosis.html` (if exists), extracts embedded Confirmed/Notes.
3. Applies carryover: marks findings NEW / PERSISTING / RESOLVED.
4. Renders one new self-contained `diagnosis.html` with: inline CSS+JS, executive summary, ordered prose
   sections, per-finding detail blocks, interactive findings index (Confirmed dropdown + Notes textarea
   per row), a resolved-since-last-run table, and an embedded JSON state block.
5. Rotates prior HTML to `diagnosis.prev.html`.

If `assemble.py` fails: report the full error and stop. Do not partial-assemble manually.

## Step 8 — Report to user

Tell the user:

- Path to `diagnosis.html`
- Counts: total findings, by severity, NEW vs PERSISTING vs RESOLVED
- One sentence on the overall verdict (from executive summary)
- The intended owner workflow:
  1. Send `diagnosis.html` to the repo owner.
  2. Owner opens it in any browser, fills `Confirmed` (yes/no/defer) + `Notes` per finding.
  3. Owner clicks "Save annotated HTML" — JS bakes annotations into the embedded JSON and downloads a copy.
  4. Owner sends the downloaded file back.
  5. Place that file at `$OUT/diagnosis.html` (replacing the original).
- Do not mention `/slice-candidates` unless the user asks "what now?". It is the natural successor once the annotated file is returned, but let the user decide when to invoke it.

## Anti-patterns

- **Reading `diagnosis.html` mid-process.** Never. Passes write new files; `assemble.py` composes.
- **Cross-pass prose dependencies.** Pass N must never read pass M's `sections/*.md`. Only pass 04
  reads prior findings YAMLs.
- **Inventing findings.** Zero findings for a pass is a valid and correct result.
- **Over-summarizing.** "There are issues" is not a finding — every finding requires a specific
  evidence path (file:line) and a concrete suggested action.
- **Touching the analyzed repo.** Even a `.gitignore` entry in the target is forbidden.
- **Using the old vault graph tool.** Use CRG (`diagnose-out/.code-review-graph/`) exclusively for all code-graph queries.

## Pipeline position

- predecessor: none (standalone entry point for brownfield repos)
- successor: `/slice-candidates` (once the owner returns the annotated HTML)
- auto-advance: false — the skill hands off by describing the owner workflow; user decides when
  to invoke `/slice-candidates` after the annotated file is returned
- user-input gates: none in the main flow (cwd-mismatch warning surfaces but does not halt);
  user may interrupt before Step 5 if the warning fires and they choose to re-invoke from TARGET
