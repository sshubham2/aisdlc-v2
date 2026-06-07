---
name: bug-hunt
description: "Whole-codebase correctness + security defect sweep. Risk-ranks the riskiest code via CRG, fans out intent-aware finder subagents along the code-review 9 dimensions, de-duplicates across finders (shared finding_dedup), then adversarially verifies every candidate (refute pass) so confirmed bugs survive and false positives are killed. Fills the empty cell of the pipeline 2x2: whole-codebase defect-finding (vs /diagnose = whole-codebase structural; /code-review = diff-scoped defect). NEVER modifies source files."
when_to_use: "Trigger phrases: /bug-hunt, 'find bugs in this codebase', 'hunt for defects', 'security sweep', 'whole-repo bug audit'. Use when adopting/auditing a repo, before a release, after a large merge, or when something smells wrong. Pass a repo path as $1 (default cwd). --report renders an owner-facing HTML; --top N caps the risk-ranked work-list; --since <ref> scopes to code changed since a git ref."
argument-hint: "[path-to-repo] [--report] [--top N] [--since <ref>] [--parallel]"
allowed-tools: Bash, Read, Glob, Grep, Write, Agent, WebSearch
---

# /bug-hunt — whole-codebase defect + security sweep

> **Status: v0.1 — build-wired, not yet battle-tested.** Registered in the build (manifest in
> `.build/manifests/batch6.json`; generated `skill.json` + `examples/` + `skill-graph.json` node). Both original
> prerequisites are DONE:
> 1. **Finding-schema enum** — `correctness-bug` + `security` are in `scripts/lib/finding.yaml` (+ `_category_short`
>    `BUG`/`SEC` in `assemble.py`). The schema now has a home for a found bug (the root cause of "/diagnose misses bugs").
> 2. **Shared infra promoted** — `write_pass.py`, `assemble.py`, and the `finding.yaml` schema now live in `scripts/lib/`
>    (alongside `finding_dedup.py`), shared by BOTH `/diagnose` and this skill. No more cross-skill reach.
>
> Caveat: this SKILL.md has NOT been through the adversarial review the original 26 skills had — treat as v0.

This skill finds **what is wrong** — logic defects, broken error handling, boundary/null/concurrency
bugs, and security holes (injection, authz gaps, secrets, unsafe deserialization). It is the
defect-finding complement to `/diagnose` (which is structural/forensic and, by design, finds none of
these). It never modifies source. Output is `bug-hunt-out/findings.json` (+ optional `--report` HTML),
and it **offers** (never forces) a handoff to `/repro` → `/slice` for confirmed high/critical bugs.

> Paths: all tooling is **shared** in `scripts/lib/`, invoked `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<x>.py"`:
> `write_pass.py`, `assemble.py`, the `finding.yaml` schema, `finding_dedup.py`, `vault_edit.py`.
> `$PY` is the interpreter resolved by the SessionStart hook.

## Hard rules

1. **Never modify source files in the target repo.** All writes go to `bug-hunt-out/` (and, only on an
   accepted handoff, to `<vault>/candidates.json` via the `vault_edit` append seam).
2. **Intent-aware — the key difference from /diagnose.** A bug is a deviation from *intended* behavior,
   so finders MAY read docstrings, comments, READMEs, and tests **as hypotheses about intent**. They are
   leads to *test against the code*, never proof. (Diagnose forbids doc reads; bug-hunt needs them, but
   trusts only the code.)
3. **Verify before reporting.** Every finding survives the Step 6 adversarial refute pass or it is dropped.
   No unverified speculation reaches the report. "No bugs in this area" is a valid, honest result.
4. **Specificity.** Every finding cites `path/to/file:line` + the concrete failing path. No "missing error
   handling" — instead "`src/api.py:42` awaits nothing; a 5xx from `fetch()` is swallowed, returns `None`,
   caller at :60 dereferences it."

## Step 1 — Resolve target + args

Skill args arrive as the `$ARGUMENTS` substitution (0-based `${ARGUMENTS[N]}`); `"$@"`/`$1` do NOT carry
them, and shell vars do NOT persist across ```bash blocks — re-derive in every block.

Parse with a single `for a in $ARGUMENTS` scan (NOT `set --`/`$1`/`$@` — Claude Code pre-substitutes
those positional tokens in the markdown, so they would expand wrong/empty; an `expect` flag captures a
flag's value on the next iteration):

```bash
TARGET=""; TOP=40; SINCE=""; REPORT=0; PARALLEL=0; expect=""
for a in $ARGUMENTS; do
  if [ -n "$expect" ]; then
    case "$expect" in top) TOP="$a" ;; since) SINCE="$a" ;; esac
    expect=""; continue
  fi
  case "$a" in
    --report)   REPORT=1 ;;
    --parallel) PARALLEL=1 ;;
    --top)      expect=top ;;
    --since)    expect=since ;;
    --*)        ;;                                  # unknown flag → ignore
    *)          [ -z "$TARGET" ] && TARGET="$a" ;;  # first non-flag token = repo path
  esac
done
[ -n "$TARGET" ] || TARGET="$PWD"
OUT="$TARGET/bug-hunt-out"
echo "TARGET=$TARGET OUT=$OUT REPORT=$REPORT PARALLEL=$PARALLEL TOP=$TOP SINCE=${SINCE:-<none>}"
```

Verify `$TARGET` is a non-empty directory with code; abort with a clear message if not. Surface the same
cwd-mismatch guard as /diagnose (subagents may lose Read/Grep/Bash when `TARGET != PWD`).

## Step 2 — Build a fresh CRG graph

**Always rebuild — never reuse a prior `/diagnose` (or earlier `/bug-hunt`) graph.** A stale graph (code
changed since it was built) would mis-rank risk and feed finders wrong reachability; bug-hunt must reflect
the code as it is *now*. Independence from `/diagnose` is deliberate — bug-hunt owns its own graph.

```bash
TARGET="$PWD"; for a in $ARGUMENTS; do case "$a" in --*) ;; *) TARGET="$a"; break ;; esac; done
OUT="$TARGET/bug-hunt-out"; mkdir -p "$OUT/findings" "$OUT/sections" "$OUT/summary" "$OUT/.tmp"
rm -rf "$OUT/.code-review-graph"                       # discard any prior graph — force a fresh build
code-review-graph build "$TARGET" --out "$OUT/.code-review-graph"
```

## Step 3 — Risk-rank into a work-list (depth, not breadth)

Bugs concentrate in complex, central, recently-churned code. Build a ranked work-list of the **top `$TOP`**
risky units rather than sweeping everything uniformly:

- **Centrality / blast-radius** — CRG `impact-radius` on entry points; high fan-in functions rank up.
- **Size / complexity** — large or deeply-nested functions, derived from the fresh graph + direct file metrics (no dependency on a diagnose run).
- **Churn** — if `$TARGET` is a git repo, `git log` hotspots (most-changed files) rank up. Skip if `--since`
  is set; then the work-list is exactly the code changed since `$SINCE`.
- **Boundaries** — request handlers, deserializers, SQL/exec/shell sinks, auth/permission checks (grep sinks).

Partition the work-list into **K buckets** (K ≈ 4–8 by repo size), each a coherent slice of files/modules.
Write the buckets to `$OUT/.tmp/worklist.json`. Log what was ranked OUT (the un-reviewed tail) — silent
truncation reads as "covered everything" when it didn't.

## Step 4 — Fan out finder subagents (multi-finder, intent-aware)

For each bucket, spawn an `Agent` (`subagent_type: general-purpose`, `model: opus` for buckets containing
auth/payment/security sinks, else `sonnet`). **Default sequential** (per the diagnose R-1 parallel-spawn
caveat); `--parallel` opts into one-message fan-out.

Embed in each finder prompt:
1. The bucket's file list + `TARGET`/`OUT` paths.
2. The **code-review 9-dimension lens** (reuse `agents/code-review.md` — dimensions 1, 2, 5, 6, 9 are the
   load-bearing ones for whole-repo defects: unfounded assumptions, missing edge cases, contract gaps,
   security, cross-cutting). Tell the finder to walk every dimension and emit "none: <reason>" when clean.
3. The **intent rule** (Hard rule 2): read docstrings/tests/README as hypotheses, prove against the code.
4. The finding schema crib (with the NEW categories `correctness-bug`, `security`), and the canonical
   subagent contract (verbatim, same as diagnose):

   > **Do NOT call Write (the orchestrator handles that). Return three 4-backtick fenced blocks
   > (`section`, `findings`, `summary`). You MAY use Bash/python for CRG queries within
   > `$OUT/.code-review-graph/`, and Read/Grep/Glob for source within `$TARGET`.**

**Coverage, not one-shot.** Single-pass LLM bug recall is incomplete — that is *the* reason the current
diagnose "misses bugs many times". Run **two finder rounds** per bucket with different lenses (round A:
correctness/edge-cases; round B: security/contracts), OR a loop-until-dry (stop after a round adds nothing
new). Dedup (Step 6) collapses the overlap, so over-finding is cheap and under-finding is the real risk.

### After each finder returns

Save raw to `$OUT/.tmp/<bucket>.raw`, then write via the shared helper (3-attempt cap, same as diagnose):

```bash
TARGET="$PWD"; for a in $ARGUMENTS; do case "$a" in --*) ;; *) TARGET="$a"; break ;; esac; done
OUT="$TARGET/bug-hunt-out"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/write_pass.py" \
    --pass "<bucket>" --out "$OUT" --raw-file "$OUT/.tmp/<bucket>.raw"
```

## Step 5 — Verify all buckets wrote

`ls "$OUT/findings"` — expect one `<bucket>.yaml` per bucket. Re-spawn any missing bucket (no silent gaps).
A bucket that failed 3× is marked degraded **loudly** (not a footnote) — a degraded security bucket means
bugs are missing, so re-run it before continuing.

## Step 6 — De-duplicate across finders (shared helper)

Two finder rounds + overlapping buckets produce duplicate findings. Collapse them on **code location**
(path + line span), independent of which finder/category produced them:

```bash
TARGET="$PWD"; for a in $ARGUMENTS; do case "$a" in --*) ;; *) TARGET="$a"; break ;; esac; done
OUT="$TARGET/bug-hunt-out"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/finding_dedup.py" \
    --findings-dir "$OUT/findings" --out "$OUT/.tmp/merged.json" --format json --report
```

Merged findings gain `merged_ids` / `seen_by_passes` / `categories` / `merge_count` and a stable
`F-MRG-<8hex>` id. This is the **same shared helper `/diagnose` already calls** from `assemble.py` (after
`load_findings()`, with merged-id annotation carryover) — both skills dedup through one code path, so the
behavior never drifts. Run dedup here BEFORE Step 7 so refuters never waste votes on duplicate findings.

## Step 7 — Adversarial verification (the keep/kill gate)

For each merged finding, spawn a **refuter** `Agent` (`subagent_type: code-review`, `model: opus`) whose
job is to *disprove* it: read the cited code and decide `real | not-real | uncertain`, defaulting to
`not-real` when it cannot reproduce the failing path. This is what stops the flood of plausible-but-wrong
findings while the multi-finder breadth (Step 4) stops misses.

- For HIGH/CRITICAL findings, use **3 refuters** and keep only if ≥2 say `real` (majority vote).
- For MEDIUM/LOW, a single refuter suffices.
- Annotate each surviving finding with `verdict: real` + the refuter's one-line justification; write the
  survivors back to `$OUT/findings/` (overwrite the per-bucket files with the verified, merged set, or write
  a single `$OUT/findings/00-verified.yaml` — your choice, keep it consistent with Step 8's assemble).

Drop everything that fails the vote. Log the drop count.

## Step 8 — Route the results

```bash
TARGET="$PWD"; for a in $ARGUMENTS; do case "$a" in --*) ;; *) TARGET="$a"; break ;; esac; done
OUT="$TARGET/bug-hunt-out"
# Always: machine-readable verified findings.
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/assemble.py" --out "$OUT"   # only if --report (renders HTML)
```

- **Default (pipeline-native):** emit `$OUT/findings.json` (the verified, merged list) + a concise summary
  to the user. Do **not** auto-write candidates.
- **`--report`:** also render `$OUT/diagnosis.html` via the shared `assemble.py` (owner-facing deliverable;
  same annotate → `/slice-candidates` round-trip as /diagnose).
- **Offer, don't force.** For each confirmed **high/critical correctness-bug or security** finding, offer a
  declinable handoff: `/repro` (write the failing test first, per bug-fix discipline) → `/slice` (fix it).
  Append accepted ones to `<vault>/candidates.json` via:
  `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --vault <vault> candidates ...`
  (source `{type: "bug-hunt-finding", ref: "<F-id>"}`).

## Step 9 — Report to user

- Path to `findings.json` (+ HTML if `--report`).
- Counts: total verified, by severity, by category (correctness-bug vs security vs other), how many merged,
  how many dropped by the refute gate, and the size of the un-reviewed tail (from Step 3).
- One-sentence verdict + the top 3 must-fix findings (`path:line`).
- The offered next step (`/repro` for the worst confirmed bug), left to the user to invoke.

## Anti-patterns

- **One-shot finding.** A single finder pass under-detects — always multi-finder + verify.
- **Reporting unverified findings.** Step 7 is mandatory; an un-refuted finding is a guess.
- **Breadth over depth.** Don't sweep every file uniformly — risk-rank and go deep on the dangerous tail.
- **Trusting docs/comments as truth.** They are intent *hypotheses*; the code is the only ground truth.
- **Re-deriving dedup per skill.** Use the shared `finding_dedup.py`; do not reinvent it here or in diagnose.
- **Touching the analyzed repo.** Read-only on `$TARGET`, always.

## Pipeline position

- predecessor: none (standalone entry; fully independent of `/diagnose` — always builds its own fresh graph)
- successor: `/repro` → `/slice` for confirmed bugs (offered, declinable); or `/slice-candidates` when `--report`
- auto-advance: false — hands off by offering the next step; the user decides
- relationship: fills the whole-codebase **defect** cell that `/diagnose` (structural) and `/code-review`
  (diff-scoped) leave empty; reuses the `code-review` agent persona as both finder lens and refuter
