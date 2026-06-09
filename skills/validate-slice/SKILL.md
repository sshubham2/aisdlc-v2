---
name: validate-slice
description: "Reality check the current slice against real environments — real device, real user, real data. Executes per-criterion PASS/FAIL/PARTIAL checks with captured evidence, classifies failures (implementation bug / spec gap / reality surprise), runs VAL-1/WS-1/ETC-1 layered audits, and runs the shippability catalog regression check before handing off to /reflect."
when_to_use: "Trigger phrases: /validate-slice, 'validate this slice', 'reality check the slice', 'check slice on real device'. Use after /code-review, before /reflect. Per-slice continuous validation — NOT a terminal full-codebase audit. Auto-advances to /reflect only on aggregate Result: PASS."
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
context: fork
agent: general-purpose
---

# /validate-slice — reality check

You are validating the current slice on real environments. Tests passing is NOT enough — real device, real
user, real data. Both phases must pass before the slice is considered validated.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Active slice + inputs — injected

```!
$PY "${CLAUDE_SKILL_DIR}/scripts/active_slice_info.py" --vault "$AI_SDLC_VAULT_ROOT" --json
```

Read from the active slice folder:
- `mission-brief.json` — acceptance criteria, verification plan, walking-skeleton/exploratory-charter flags
- `build-log.json` — what was actually built; interpret deviations during AC validation

**Prerequisite gate**: if `build-log.json` is missing OR `result != "shipped"` → STOP and surface. The slice
is not ready to validate.

## Step 1 — per-criterion real-world checks

For each AC in `mission-brief.json`, execute the check described in its verification plan:

- **Backend endpoint**: hit with a real client (curl / real test harness). Inspect response + DB state.
- **Frontend page**: open in a real browser or local dev server. Perform the user action. Observe.
- **Mobile/multi-device**: install on TWO real devices for multi-device features.
- **CLI / script**: run on real sample data (not synthetic). Inspect output.
- **ML inference**: evaluate on held-out data (not training data).

Early projects without a deployment target: run locally with real sample data or demonstrate in user-facing
form (terminal, screenshot, recording). "We'll really test this later" is NOT acceptable.

## Step 2 — capture evidence per criterion

Every AC result MUST record:
- Command run + actual output (pasted)
- Screenshot reference if UI
- Log excerpt if backend
- Manual steps + observation if observation-based

"It worked" without evidence is not a PASS.

## Step 3 — classify results

For each criterion: **PASS** | **FAIL** | **PARTIAL**

For FAIL or PARTIAL, classify the cause:
- **implementation bug**: code wrong, spec right → fix code, re-validate, then /reflect
- **spec gap**: spec incomplete; do NOT fix now — /reflect captures it
- **reality surprise**: neither predicted → log immediately to risk-register.json (SVW-1: via vault_edit
  append, never raw Write/Edit), then /reflect

**PCA-1 gate-halt**: any per-criterion FAIL or PARTIAL is a user-input gate. DO NOT auto-advance to /reflect.
Surface the FAIL/PARTIAL + cause classification to the user and HALT. Auto-advance only on aggregate
`result: pass`.

## Step 4 — multi-instance validation (when applicable)

For features involving >1 user, >1 device, or >1 account: test on multiple instances simultaneously.
Single-instance passing is NOT proof.

## Step 5 — layered audits (VAL-1, WS-1, ETC-1)

**WT-ROOT-1:** Steps 5–6 inspect and run CODE, so they operate from the slice WORKTREE `$wt` (where the fix +
the repro test live), NOT the main tree. Each code ```bash block below is a fresh shell, so it must re-resolve
`$wt` and `cd "$wt"` first:
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
cd "$wt"
```
Run all three audits before the shippability catalog. Pass `--changed-files` as the list of files this slice
changed — from `build-log.json`, or (from `$wt`) `git -C "$wt" diff --name-only "$(git -C "$wt" merge-base HEAD origin/HEAD 2>/dev/null || echo HEAD)"` ∪ `git -C "$wt" ls-files --others --exclude-standard`.

### Layer A + B audit (VAL-1)

```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"; cd "$wt"   # WT-ROOT-1: scan worktree code
$PY "${CLAUDE_SKILL_DIR}/scripts/validate_slice_layers.py" \
  --slice <vault>/slices/slice-NNN-<name> \
  --changed-files <list> \
  --imports-allowlist tests
```

- **Layer A — credential scan (Critical, blocks)**: AWS keys, GitHub PATs, Slack tokens, JWTs, PEM private
  keys, Anthropic/OpenAI API keys, generic `api_key = "..."` literals. False positives silenced via
  `<vault>/.secrets-allowlist` (one regex/line). Any Critical finding → cannot proceed to /reflect; remove
  the secret and rotate it, or add a precise allowlist regex with a `#` comment.
- **Layer B — dependency hallucination check (Important, surfaces)**: Python ast-parses changed `.py` files,
  resolves imports against `pyproject.toml` / `requirements.txt`. Anything unresolved is a possible
  AI-hallucinated import. Surface to user; defer-with-rationale allowed.
- Skip flags: `--skip-secrets` / `--skip-deps` when the project runs its own scanner/linter.
- NFR-1 carry-over: slices with `mission-brief.json` mtime pre-2026-05-06 are exempt automatically.

### Walking-skeleton audit (WS-1)

Only when `mission-brief.json` sets `variants.walking_skeleton: true`:

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/walking_skeleton_audit.py" <vault>/slices/slice-NNN-<name> --strict-pre-finish
```

Every architectural layer in the `architectural_layers` table must have `status: exercised`. Any
`non-exercised-pre-finish` finding → STOP; the layer was not reached during validation.

### Exploratory-charter audit (ETC-1)

Only when `mission-brief.json` sets `variants.exploratory_charter: true`:

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/exploratory_charter_audit.py" <vault>/slices/slice-NNN-<name> --strict-pre-finish
```

Every charter must be `COMPLETED` (with findings) or `DEFERRED` (with rationale). Any `non-final-pre-finish`
finding → STOP; complete or defer every charter before proceeding.

Both WS-1 and ETC-1 pass silently if the respective flag is absent or false.

## Step 6 — shippability catalog regression check

Skip if `<vault>/shippability.json` does not exist (first slice — /reflect will create it).

### Pre-catalog gates (run ALL four before the catalog)

**WT-ROOT-1** — the slice's code (fix + repro test) is in the WORKTREE; the main tree must be clean:
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/wt_root_audit.py" --worktree "$wt"
```
Non-zero → STOP: slice code leaked into the main tree — move it into `$wt` before validating.

**SCMD-1** — verifies every row has a prose-free Machine-cmd column:
```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/shippability_decoupling_audit.py" <vault>/shippability.json
```
Non-zero → STOP: fix the row before running the catalog.

**PTFCD-1** — verifies every `tests/<...>.py` token in Machine-cmd cells resolves to a file on disk (in `$wt`):
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"; cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/shippability_path_audit.py" <vault>/shippability.json
```
Non-zero → STOP: report the phantom test-file citation (the repro test must live in `$wt/tests/bugs/`) and fix it.

**SVW-1** — verifies no SKILL.md prescribes an unrouted mutation of a shared-aggregate vault file:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/skill_vault_write_safety_audit.py"
```
Non-zero → STOP: route the directive through `vault_edit append` or add a sanctioned exemption.

### Run the catalog (SRSC-1)

Do NOT hand-roll the execution loop. **WT-ROOT-1: run it from `$wt`** so each `machine_cmd` executes against the
worktree, where this slice's fix AND its repro test both live (running from the main tree would test code without
the fix → false regression):
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"; cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/shippability_runner.py" <vault>/shippability.json
```

The runner reads each row's Machine-cmd, splits on ` ; `, strips backticks per segment (reuses SCMD-1
`_segments()`), executes each interpreter-anchored segment from the worktree root (`$wt`), reports PASS/FAIL per row.

If any row FAILS: the current slice broke something a past slice established. This blocks /reflect. Either
fix the regression or get explicit user approval to defer (with rationale logged in validation.json).

## Step 7 — write validation.json

Write `<vault>/slices/slice-NNN-<name>/validation.json` (schema by example: `examples/validation.json`):

```json
{
  "_schema": "aisdlc/validation@1",
  "slice": "slice-NNN",
  "result": "pass|fail|partial",
  "reality_contact": "high",
  "criteria": [
    {
      "id": "AC1",
      "result": "pass|fail|partial",
      "evidence": "<command + output, screenshot ref, log excerpt>",
      "cause": null
    }
  ],
  "reality_surprises": [
    {
      "note": "<what was not predicted by design or critique>",
      "becomes_candidate": "<SC-NNN or null>"
    }
  ],
  "shippability_regression": {
    "ran": true,
    "failed_rows": []
  },
  "at": "<ts>"
}
```

Top-level `"result"` is computed as `"pass"` only when ALL criteria are `"pass"` AND all audits and
shippability checks are green. Any criterion `"fail"` or `"partial"` → aggregate `"fail"` or `"partial"`.

`reality_contact` is always `"high"` for this gate (Phase 1): validation runs against the **real** environment
(real device / real user / real data), so a `pass` here is a **reality sign-off** — the strongest kind of
green, categorically distinct from the model-approvals (`/critique`, `/code-review`) earlier in the loop. The
gate-log row this skill writes (Step 9b) already carries `reality_contact: high`; say "reality-approved" — not
just "passed" — when you summarize a clean result.

## Step 8 — update milestone.json

Edit `<vault>/slices/slice-NNN-<name>/milestone.json` (schema by example: `examples/milestone.json`):
- `stage: "validate"`
- mark `validate` step `done: true`
- `next_action`: `/reflect` (clean) or `fix regression then re-run /validate-slice` (blocked)
- `current_focus`: validation summary — on PASS, **reality-approved** (N/M ACs passed on the real env, shippability status); on FAIL/PARTIAL, what reality rejected
- `updated_by: "validate-slice"`, `at: <ts>`

## Step 9 — append reality surprises to shared files (SVW-1)

For each reality surprise discovered:

```bash
# Append to risk-register.json (SVW-1: vault_edit append, never raw Write/Edit)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
  --file "<vault>/risk-register.json" \
  --array risks \
  --json '{"id":"R-NN","title":"<title>","likelihood":"...","impact":"...","status":"open","score":...,"band":"...","mitigation":"...","discovered":{"phase":"validate","at":"<ts>","ref":"slice-NNN"}}'

# If the surprise spawns future work, append to candidates.json too (SVW-1)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
  --file "<vault>/candidates.json" \
  --array candidates \
  --json '{"id":"SC-NNN","title":"...","status":"candidate","source":[{"type":"reality-surprise","ref":"R-NN"}],...}'
```

## Step 9b — record gate outcome (measurement spine)

One row per slice into `<vault>/gate-log.json` (roadmap Theme 8 / plan Phase 0). **Runs ALWAYS, even on a
clean pass.** `validate-slice` is a **high** reality-contact gate (real device / real user / real data) —
`gate_log.py` stamps that. Run in-fork (shared vault + `vault_edit` lock make it write-safe; this skill
already appends to shared files in Step 9):

```bash
# verdict = aggregate result: pass|partial|fail; findings-count = number of FAIL + PARTIAL criteria
# --cross-domain (Phase 2.3): set when this slice's design imported a cross-domain pattern — this reality
# verdict is the PRIMARY signal for the cross-domain validity ratio (did reality confirm the borrowed pattern?).
SLICE_DIR="$AI_SDLC_VAULT_ROOT/slices/<slice-NNN-name>"
CD=""; [ -f "$SLICE_DIR/design.json" ] && $PY -c "import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get('cross_domain_transfer') else 1)" "$SLICE_DIR/design.json" 2>/dev/null && CD="--cross-domain"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate validate-slice --slice <slice-NNN-name> \
    --verdict <pass|partial|fail> --findings-count <N fail+partial> $CD \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

## Critical rules

- USE REAL ENVIRONMENTS. No mocks or simulators when real is possible.
- CAPTURE EVIDENCE per criterion. "It worked" without evidence is not a PASS.
- MULTI-INSTANCE for multi-user/device/account features. Always.
- DO NOT auto-fix spec gaps during validation. /reflect formalizes them.
- DO NOT pass a partial criterion as PASS. Partial is partial.
- DO NOT skip a criterion because "it's covered by the test suite."
- Shared-aggregate vault files (risk-register.json, candidates.json) mutate ONLY via vault_edit append.
- Heavy mode: produce compliance-grade records — reproducible commands, timestamped evidence, sign-off field,
  cross-reference to test-plan IDs.
- **Reality spine (Phase 1.3).** HIGH reality-contact — a `pass` is **reality-approved** (real device / user /
  data), the strongest kind of green and categorically above any model-approval. Mandatory at every tier; no
  model-on-model gate (`/critique`, `/code-review`) can wave through a reality FAIL/PARTIAL.

## Pipeline position

- predecessor: `/code-review` · successor: `/reflect` · auto-advance: conditional
- on-clean-completion (aggregate `result: pass`, all audits green, shippability clean): the main thread
  advances to `/reflect` automatically after this fork returns.
- user-input gates (halt auto-advance — surface to user, resume only on explicit user action):
  - Any per-criterion FAIL — HALT (user decides remediation / failure classification). (PCA-1)
  - Any PARTIAL (per-criterion or aggregate) — HALT. Same disposition as FAIL; explicit gate required. (PCA-1)
  - Layer A (credential) finding — HALT. Cannot advance to /reflect until resolved.
  - Shippability regression — HALT unless user approves explicit deferral with rationale.
