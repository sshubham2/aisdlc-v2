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
- `code-review.json` (if present) — (a) every `blocker` finding must be dispositioned in `triage.dispositions[]`
  (the CRD-1 prerequisite below); (b) any finding with `measure_at_validate: true` (a complexity/perf hypothesis
  the code-Critic deliberately did NOT assert) is benchmarked on real data in Step 1b

**Prerequisite gate:**
- if `build-log.json` is missing OR `result != "shipped"` → STOP and surface. The slice is not ready to validate.
- **CRD-1 — no un-dispositioned code-review blockers.** Every `blocker`-severity finding in `code-review.json`
  MUST carry a disposition in `triage.dispositions[]` (action `fixed`, or `overridden` + a non-empty rationale —
  written by the MAIN thread after `/code-review` returned). Any un-dispositioned blocker → STOP and surface:
  _"code-review blocker `<id>` has no disposition — fix it (re-run the relevant check) or override it with a
  rationale in `code-review.json` `triage`, then re-run `/validate-slice`."_ Check:
  ```bash
  repo_root="$(git rev-parse --show-toplevel)"
  slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --path-only)"
  $PY -c "import json,os,sys; p=sys.argv[1]+'/code-review.json'; d=json.load(open(p,encoding='utf-8')) if os.path.exists(p) else {}; F=d.get('findings') or []; tr=d.get('triage') or {}; D={str(x.get('finding','')).strip() for x in (tr.get('dispositions') or []) if str(x.get('action','')).strip().lower() in {'fixed','overridden'} and str(x.get('rationale','')).strip()}; miss=[str(f.get('id')) for f in F if str(f.get('severity','')).lower()=='blocker' and str(f.get('id','')).strip() not in D]; print('CRD-1 un-dispositioned code-review blocker(s): '+', '.join(miss)) if miss else print('CRD-1: ok'); sys.exit(1 if miss else 0)" "$slice_folder"
  ```
  Exit 1 → STOP. (Major/minor code-review findings are advisory — not gated here.)

## Step 1 — per-criterion real-world checks

For each AC in `mission-brief.json`, execute the check described in its verification plan:

- **Backend endpoint**: hit with a real client (curl / real test harness). Inspect response + DB state.
- **Frontend page**: open in a real browser or local dev server. Perform the user action. Observe.
- **Mobile/multi-device**: install on TWO real devices for multi-device features.
- **CLI / script**: run on real sample data (not synthetic). Inspect output.
- **ML inference**: evaluate on held-out data (not training data).

Early projects without a deployment target: run locally with real sample data or demonstrate in user-facing
form (terminal, screenshot, recording). "We'll really test this later" is NOT acceptable.

## Step 1b — measure code-review complexity candidates (Theme 8: reason → flag, measure → verdict)

`/code-review` flags performance/complexity concerns as **hypotheses** (`measure_at_validate: true`), never asserted
Big-O — because the model's complexity *reasoning* is hallucination-prone. You own the **verdict**: measure them.

Read `code-review.json` (skip this step if absent or no finding carries `measure_at_validate: true`). For each such
finding, working against the built slice code (the worktree `$wt`, as in Steps 5–6):

1. Build a **realistic** input at the scale the finding names — the largest real sample available, NOT a toy.
2. **Profile / benchmark** the flagged path on it (`time` / `timeit` / `hyperfine`; count ops or DB queries; watch
   memory — whatever the hypothesis predicts). Scale the input 1× → 10× → 100× and observe how cost actually grows.
3. Record a MEASURED verdict, numbers as evidence:
   - **CONFIRMED** — cost grows as feared at real scale → a real defect. If it breaks an AC's performance bar, that
     AC is FAIL/PARTIAL; otherwise log a `reality_surprise` (→ candidate).
   - **REFUTED** — flat / acceptable at real scale → the candidate was a code-review false-alarm; record it (with the
     measurement) so the model-gate's over-reach is visible, not silently dropped.
   - **INCONCLUSIVE** — no real input buildable this slice → say so explicitly; do NOT pass it off as measured.

Put the measurement (command + numbers) in the relevant AC's `evidence`, and any CONFIRMED/REFUTED outcome in
`reality_surprises`. A benchmarked number is a reality sign-off; a re-asserted Big-O is not.

## Step 2 — capture evidence per criterion

Every AC result MUST record:
- Command run + actual output (pasted)
- Screenshot reference if UI
- Log excerpt if backend
- Manual steps + observation if observation-based

"It worked" without evidence is not a PASS.

> **SECRET-SWEEP (4.7) — redact before persisting.** Captured command output can carry secrets
> (tokens, connection strings, API keys) that would sit in the vault as plaintext FOREVER (VAL-1
> scans the code diff, not the vault). Before writing captured output into any `evidence` field,
> pipe it through the redactor — it replaces credentials with `[REDACTED:<type>]` using the same
> VAL-1 patterns:
> ```bash
> <your-command> 2>&1 | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/secret_scrub.py"
> ```

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
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
cd "$wt"
```
Run all three audits before the shippability catalog. Pass `--changed-files` as the list of files this slice
changed — from `build-log.json`, or (from `$wt`) `git -C "$wt" diff --name-only "$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_diff_base.py" --worktree "$wt")"` ∪ `git -C "$wt" ls-files --others --exclude-standard` (SC-043: base = fork point vs the LOCAL integration branch, never origin/HEAD).

### Layer A + B audit (VAL-1)

```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
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

### Walking-skeleton audit (WS-1) — reality-grounded (3.1)

Only when `mission-brief.json` sets `variants.walking_skeleton: true`:

```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"   # WT-ROOT-1: re-resolve $wt (fresh shell)
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/brief_variants_audit.py" <vault>/slices/slice-NNN-<name> --variant walking_skeleton --execute --repo-root "$wt"
```

`--execute` (implies `--strict-pre-finish`) actually **runs** each layer's `verification` command from the slice
worktree, so WS-1 touches reality instead of trusting the model-written `status` marker (3.1 — "add reality or
demote"): a non-zero exit is a `verification-failed` violation → STOP; a `verification` that is prose / not a
runnable command **degrades to a non-gating advisory** (we could not reality-check it — fall back to the marker,
never a hard fail). For this to bite, write each layer's `verification` as a **runnable command** (like a
shippability `machine_cmd`), not a prose sentence. Every layer must still be `status: exercised`.

### Exploratory-charter audit (ETC-1)

Only when `mission-brief.json` sets `variants.exploratory_charter: true`:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/brief_variants_audit.py" <vault>/slices/slice-NNN-<name> --variant exploratory_charter --strict-pre-finish
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
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
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
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"; cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/shippability_path_audit.py" <vault>/shippability.json
```
Non-zero → STOP: report the phantom test-file citation (the repro test must live in `$wt/tests/bugs/`) and fix it.

> **Parallel-slice residual (SC-021 / SC-058).** PTFCD-1 audits EVERY catalog row's token with no per-slice
> scoping, so under parallel slices it STILL STOPs here on a *sibling* slice's not-yet-merged repro test —
> BEFORE the runner runs. SC-021 made the **runner** (SRSC-1, below) treat an absent-on-checkout row as a
> distinct non-regression `ABSENT` verdict, but it deliberately did NOT touch this PTFCD-1 pre-gate (out of
> scope). So the live parallel-slice false-STOP at THIS gate is **not yet cleared** — that is **SC-058**'s job
> (symmetric absent-test scoping in the path audit). Until SC-058 ships, a sibling's absent repro still STOPs
> Step 6 here; the slice-025 workaround (filter the catalog to worktree-present rows) applies.

_(SVW-1 — the skill-vault-write-safety scan — is no longer run here. With no `--root` it audited the **plugin's
own** `SKILL.md` prose (a constant per plugin version, zero per-slice user value — same 1.5 reasoning that evicted
the other self-audits), and 3.11 demoted it to a CI-only **advisory** check via `.build/plugin_self_audits.py`. The
real per-slice control against raw shared-file writes is the `vault_edit` wrapper itself, used in Step 9 below.)_

### Run the catalog (SRSC-1)

Do NOT hand-roll the execution loop. **WT-ROOT-1: run it from `$wt`** so each `machine_cmd` executes against the
worktree, where this slice's fix AND its repro test both live (running from the main tree would test code without
the fix → false regression):
```bash
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --folder-only)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"; cd "$wt"
$PY "${CLAUDE_SKILL_DIR}/scripts/shippability_runner.py" <vault>/shippability.json
```

The runner reads each row's Machine-cmd, splits on ` ; `, strips backticks per segment (reuses SCMD-1
`_segments()`), executes each interpreter-anchored segment from the worktree root (`$wt`), and reports a
three-valued verdict per row — **PASS / FAIL / ABSENT** (SC-021 / ADR-021). A row whose cited `tests/...py`
file(s) are absent on this checkout (a sibling slice's not-yet-merged repro) is recorded **ABSENT** — decided
by file existence, never the pytest exit code (exit 4 conflates absent-file / phantom-citation / usage-error) —
and is NOT counted as a regression; a row with a present test file, or no test token, still runs (so a
present-but-failing test still FAILs).

**ABSENT rows do NOT block** — they are reported distinctly (a test not on this checkout is information, not a
regression) and never enter `failed_rows`; only a **FAIL** blocks. If any row FAILS: the current slice broke
something a past slice established — this blocks /reflect. Record the
failed rows in `validation.json.shippability_regression.failed_rows` and leave `deferral: null`. **This skill runs
as a forked context (`context: fork`) and CANNOT `AskUserQuestion`** — so the fork does NOT self-approve a deferral
and does NOT prompt. It returns `blocked: needs-deferral-decision` to the main thread, which resolves it post-return
(see **Main-thread deferral resolution** below): the user either fixes the regression (re-run `/validate-slice`) or
approves an explicit deferral with rationale, and the **main thread** appends that decision to `validation.json`.
NEVER write an approved deferral from inside the fork (3.17).

**Fork output contract (the main thread never reads this file — relay the protocol):** your final return
message MUST contain, verbatim for the main thread: (a) the aggregate verdict; (b) on a clean PASS — "advance
to `/reflect` via the Skill tool"; (c) on `blocked: needs-deferral-decision` — the failed rows AND the full
Main-thread-deferral-resolution steps (AskUserQuestion fix-vs-defer, rationale mandatory, the exact `deferral`
JSON shape, write via `vault_edit`, only then advance); (d) on any FAIL/PARTIAL/credential HALT — what to
surface to the user. A bare verdict with no relayed instructions strands the main thread.

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
    "failed_rows": [],
    "deferral": null
  },
  "at": "<ts>"
}
```

`shippability_regression.deferral` stays `null` when the fork writes validation.json. If `failed_rows` is
non-empty, the **main thread** fills it post-return (Main-thread deferral resolution): either it is never written
(the user chose to fix → re-run) or it becomes `{"approved": true, "rationale": "<text>", "by": "user", "at":
"<ts>"}`. The fork never sets `approved` — it cannot ask (3.17).

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
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
SLICE_DIR="$VAULT/slices/<slice-NNN-name>"
CD=""; [ -f "$SLICE_DIR/design.json" ] && $PY -c "import json,sys;sys.exit(0 if json.load(open(sys.argv[1],encoding='utf-8')).get('cross_domain_transfer') else 1)" "$SLICE_DIR/design.json" 2>/dev/null && CD="--cross-domain"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate validate-slice --slice <slice-NNN-name> \
    --verdict <pass|partial|fail> --findings-count <N fail+partial> $CD \
    --reality-proxy <real-device|real-account|real-sandbox|staging|local-real-data|simulator|docs-only> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$VAULT" --file gate-log.json --array entries --stdin
# --reality-proxy (§2.7): the WEAKEST environment any criterion was checked on (a slice validated
# on a simulator must not log the same green as one validated on real devices).
```

## Main-thread deferral resolution (post-return — runs OUTSIDE the fork) (3.17)

The fork has returned. If it returned `blocked: needs-deferral-decision` (a shippability regression with
`shippability_regression.failed_rows` non-empty and `deferral: null`), the **main thread** now resolves it —
this is where the user is asked, because a forked context cannot `AskUserQuestion`:

1. Surface the failed rows from `validation.json.shippability_regression.failed_rows`.
2. `AskUserQuestion` — two paths:
   - **Fix the regression** → do NOT write a deferral; tell the user to fix it and re-run `/validate-slice`. HALT
     (no advance to `/reflect`).
   - **Defer with rationale** (rationale is mandatory; empty → re-ask) → append the decision to `validation.json`
     via `vault_edit` (the file is a shared-aggregate write target; do not raw-overwrite):
     set `shippability_regression.deferral = {"approved": true, "rationale": "<text>", "by": "user", "at": "<ts>"}`.
     Then the regression is consciously accepted and the slice may advance to `/reflect`, which will record the
     deferral as a known-debt lesson.
3. Only after a `deferral.approved == true` (or a clean run with no failed rows) does the main thread auto-advance
   to `/reflect`.

The other post-return HALTs (per-criterion FAIL/PARTIAL, Layer-A credential finding — PCA-1) are surfaced the same
way: the fork writes the verdict; the main thread owns the user gate.

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
  - Shippability regression — the fork CANNOT ask mid-task; it returns `blocked: needs-deferral-decision` and the
    MAIN THREAD resolves it post-return (fix + re-run, OR append a user-approved deferral + rationale to
    validation.json via vault_edit — see "Main-thread deferral resolution"). HALT until resolved (3.17).
