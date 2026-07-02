---
name: code-review
description: "Adversarial code-Critic review of the just-built slice's code diff along 9 fixed dimensions, producing path:line findings as code-review.json. Runs in a forked Critic context. Use AFTER /build-slice, BEFORE /validate-slice."
when_to_use: "Trigger phrases: /code-review, 'review the slice code', 'adversarial code review on the diff'. Auto-advances from /build-slice."
context: fork
agent: code-review
argument-hint: "[slice-id]"
allowed-tools: Read, Grep, Glob, Bash, WebSearch, Write
---

# /code-review — forked code-Critic

You are running as the **code-Critic** (your persona, the 9 dimensions, frameworks, specificity/honesty/severity
rules, and the `code-review.json` output shape are your system prompt — `agent: code-review`). This skill is the
*task*: it gives you the slice inputs and the write target. Review the diff; do not approve it.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Active slice + inputs

The active slice is the latest `<vault>/slices/slice-NNN-*/`. Read these (they are the intent + design the code
must match — drift from them is Dimension 7):
- `mission-brief.json` — intent, acceptance criteria, must-not-defer, out-of-scope
- `design.json` — what's new, components touched, contracts, wiring matrix, ADR refs
- `decisions/ADR-*.json` referenced by the slice, and `build-log.json` (verify `result: shipped`)

## Project calibration overlay — injected (Phase 4.1)

Dimensions this project found low-signal via `/critic-calibrate` (weight them LIGHTER — never skip, never reality gates):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,os,sys; v=sys.argv[1]; f=f'{v}/critic-calibration-log.json'; d=json.load(open(f,encoding='utf-8')) if os.path.exists(f) else {}; print(json.dumps([n for n in d.get('calibration_notes',[]) if n.get('target_gate') in ('code-review','critique')],indent=2))" "$VAULT" 2>/dev/null || echo "[]"
```
For each note, treat the named dimension as lower-yield FOR THIS PROJECT (it has been FALSE-ALARM / quiet over the
cited window): hold a higher bar before filing in it, and do not pad severity. This NEVER suppresses a real issue
(file it if you see one) and NEVER applies to a reality gate — it only counters this project's measured over-firing.

## Slice diff (in-scope only) — run this block FIRST (a body step, not a load-time injection)

**WT-ROOT-1:** the diff and any targeted Reads come from the slice WORKTREE `$wt` (HEAD = the slice branch),
NOT the main tree (HEAD = default there → an empty diff). The build leaves changes UNCOMMITTED in `$wt`
(commit happens at `/commit-slice`), so diff the working tree against the branch base, not `base...HEAD`.

```bash
# slice-036: a bash BODY block (NOT a !-injection) so a forked /code-review invoked as `/code-review slice-NNN`
# (build-slice's handoff passes the id) BINDS ${ARGUMENTS} and resolves the NAMED slice -- a !-injection runs at
# skill-LOAD before ${ARGUMENTS} binds (SC-064/ADR-022), so the named-from-main diff would target the wrong slice.
# Run this block FIRST: it fetches the diff you review.
repo_root="$(git rev-parse --show-toplevel)"
ARG="${ARGUMENTS[0]:-}"
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --folder-only)"
else
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"   # slice-014: NO 2>/dev/null -- no-arg AMBIGUOUS exit-4 HALT surfaces HERE
fi
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
paths=('src/**' 'skills/**' 'agents/**' 'scripts/**' 'tests/**')   # slice-056/ADR-050: a QUOTED bash array (elements quoted at assignment) expanded as "${paths[@]}" so git receives LITERAL pathspecs. The old unquoted `$paths` string was pathname-expanded by bash against the MAIN-tree cwd before git ran, silently dropping branch-only top-level files (e.g. tests/foo.py). NOT set -f (global noglob leaks on an early-abort path); NOT bare-dir (would change the pathspec strings).
base="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_diff_base.py" --worktree "$wt")"   # SC-043: fork point vs the LOCAL integration branch (never origin/HEAD); always non-empty (HEAD fallback when no remote -- WT-ROOT-1)
git -C "$wt" diff "$base" -- "${paths[@]}" 2>/dev/null | head -1200    # committed + uncommitted since fork (base is a ref or the HEAD fallback)
git -C "$wt" ls-files --others --exclude-standard -- "${paths[@]}" 2>/dev/null | sed 's/^/NEW-UNTRACKED: /'
```

If the diff AND the untracked list are both empty → write a **schema-complete** `code-review.json` and stop. It
MUST carry every required key, so the Step-4b self-check below and `/validate-slice`'s deterministic gate (ADR-033)
never false-fail a legitimately empty review (M2):
`{ "_schema":"aisdlc/code-review@1", "slice":"slice-NNN", "reviewed_by":"code-review agent", "result":"NO-CODE-CHANGES", "findings":[], "dimensions_checked":[], "triage":null }`.
Read any `NEW-UNTRACKED:` files from `"$wt/<path>"` for review (new files aren't in `git diff`).

## Task
1. Read the slice artifacts above.
2. Review the diff along your **9 dimensions, in order**. Every finding cites `path/to/file:line`. A dimension with
   nothing wrong gets an explicit "none: <reason>" — never manufacture findings.
3. Optional code-graph cross-check: use the `code-review-graph` MCP tools (impact-radius / search) for blast-radius
   and INFERRED edges the new code depends on.
4. Write `<vault>/slices/slice-NNN-<name>/code-review.json` (schema by example: `examples/code-review.json`). Include `"triage": null` — the per-blocker dispositions are filled by the MAIN thread after you return (you are forked and cannot run the interactive disposition gate).
4b. **Self-check the artifact you just wrote — in-fork BEST-EFFORT lint (ADR-033 / AC1).** Run the schema-by-example
   linter on the `code-review.json` you just wrote (source inspection at the producing station). This is
   **best-effort early feedback**: you are the same forked agent that wrote it, so a non-zero exit cannot *force* you
   to stop — the DETERMINISTIC guarantee that a malformed artifact never advances is `/validate-slice`'s prerequisite
   gate (ADR-033). On a violation, read the exact message, fix the offending key in `code-review.json`, and re-lint
   (at most twice); if it still violates, **surface the violations in your Return** — do NOT report a clean verdict.
   ```bash
   repo_root="$(git rev-parse --show-toplevel)"
   ARG="${ARGUMENTS[0]:-}"
   if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
     sf="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --slice "$ARG" --path-only)"
   else
     sf="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"
   fi
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --type code-review "$sf/code-review.json"; rc=$?
   # exit 0 = clean (proceed) · 1 = schema violation (fix the key + re-lint; surface in Return if still failing) · 2 = usage/tooling error (surface as a tool error, NOT a clean pass)
   [ "$rc" = 0 ] || echo "ARTIFACT-LINT: code-review.json did not conform (rc=$rc) -- fix + re-lint or surface the violations in your Return."
   ```
5. Update `<vault>/slices/slice-NNN-<name>/milestone.json`: `stage: "code-review"`.
6. **Record the gate outcome** — one row per slice into `<vault>/gate-log.json` (measurement spine, roadmap
   Theme 8 / plan Phase 0). `code-review` is a **low** reality-contact gate (the model grading a diff);
   `gate_log.py` stamps that. Run this in-fork (the shared vault + `vault_edit` lock make it write-safe):

```bash
# verdict: no-code-changes | clean (0 findings) | findings (>=1); findings-count = blocker+major+minor
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate code-review --slice <slice-NNN-name> \
    --verdict <no-code-changes|clean|findings> --findings-count <N> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

## Return
A 2-line summary to the main thread: `Result` + blocker/major/minor counts. The full review lives in
`code-review.json`. **Also carry one `artifact_lint: clean` line** (or the residual violations if the Step-4b
self-check could not be made to pass) — so a skipped or failed self-check is VISIBLE to the resuming main thread,
which is the andon cord made observable (ADR-033). This attestation is best-effort; `/validate-slice`'s
prerequisite gate is the deterministic backstop regardless of what this line says.

**Blocker findings are consequential (CRD-1).** If you filed ≥1 `blocker`, your return MUST instruct the main
thread: _"N code-review blocker(s) — disposition each in `code-review.json` `triage.dispositions[]` (action
`fixed` after re-running the relevant check, or `overridden` + a one-line rationale) BEFORE `/validate-slice`;
`/validate-slice` refuses an un-dispositioned blocker."_ Major/minor findings stay advisory (recorded, not
gated). You are forked, so you do NOT run the disposition gate yourself — you hand it to the main thread.

**Model-on-model gate (Phase 1.3).** This is LOW reality-contact — the model grading a diff (the CRG cross-check
reads real code, but the verdict is still the model's judgment). Its **blockers** are dispositioned, not silently
ignored (CRD-1 above); its major/minor findings stay advisory. The gate that can actually say *no* against reality
is still `/validate-slice` (real device/data) — never present a clean code-review as a reality sign-off, and never
let it override a reality gate.

## Pipeline position
- predecessor: `/build-slice` · successor: `/validate-slice` · auto-advance: true
- on-clean-completion: the main thread advances to `/validate-slice` after this fork returns.
- user-input gates: none — `/code-review` runs as a forked agent and returns its verdict to the main thread (no mid-fork user prompts; the CRD-1 blocker disposition is written by the main thread post-return and gate-ENFORCED by `/validate-slice`'s prerequisite, not here).
