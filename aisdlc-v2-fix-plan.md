# ai-sdlc v2 — Remediation Plan

**Repo:** `C:\Users\sshub\aisdlc-v2` (Claude Code plugin `ai-sdlc`, v2.18.3 at time of review)
**Source:** Deep multi-agent review (2026-06-11): 8 lenses + adversarial verification of every checkable major+ finding. Full findings: `C:\Users\sshub\projets\sdlc-comparison\aisdlc_analysis\review_digest.txt`; narrative report: `aisdlc-v2-deep-analysis.md` (same dir).
**Audience:** an executing AI session with NO prior context. Everything needed is in this document.

---

## ⏳ EXECUTION STATUS (as of 2026-06-11 — read FIRST on resume)

All work is on branch **`fix/remediation-plan`** (committed locally, **NOT pushed**). Plugin now at **v2.19.6**.

- **Phase 1 — ✅ DONE (8/8, tested)** · commit `2ed5500` (v2.18.4)
- **Phase 2 — ✅ DONE (10/11, tested)** · commit `b381bcc` (v2.19.0)
  - **2.1 was implemented as TIER-ONLY** (user decision, *not* the plan's mode-column): in-loop cost keys on
    `risk_tier` + `critic_required` alone; mode only sets the default tier (Minimal→low, Standard/Heavy→medium)
    + Heavy's compliance floor. **Honor this in any later item that touches gating.**
  - **2.11 DEFERRED** (optional; tier-only already cuts Minimal gate writes).
- **Phase 3 — 🔶 PARTIAL (~14/19)** · prior commit `c2285d2` (v2.19.1) did 3.4, 3.6, 3.14, 3.15, 3.16.
  Session of 2026-06-11 (commits `4e2438c`→`b2d86d1`, v2.19.2→v2.19.6) added:
  - **3.10** (v2.19.2) — removed the degenerate `--resolve-soft` surface + dead resolve fns; commit-slice routes off `--classify`.
  - **3.12** (v2.19.2) — deleted supersede-slice's doomed `_index.json` stamp; `/pulse --full` reads supersession from reflection.json.
  - **3.13** (v2.19.2) — ONE canonical stage-derivation rule shared by `/archive` + `/pulse` (critique.json OPTIONAL).
  - **3.5** (v2.19.3) — critic-calibrate ADD template now emits an `active_checks[]` overlay check, not splice-into-critique.md text.
  - **3.2** (v2.19.3) — NEW third calibration kind **gate-skip** (`gate_skips[]` overlay; precision<0.2 over ≥8 runs; model-tier only; compliance-mandatory always overrides; reality spine excluded). `/critique` consumes it in its gating decision.
  - **3.3** (v2.19.4) — design-slice records per-pair `approach_divergence` in design.json.tournament + a `design-tournament` gate-log row (gate_log.py gained the gate + `--approach-divergence`, marked INFORMATIONAL so pulse excludes it from precision/quiet); `/pulse --full` surfaces it + the drop-to-2-designers rule.
  - **3.1 (WS-1 portion)** (v2.19.5) — walking_skeleton_audit `--execute` actually RUNS each layer's verification (subprocess); non-zero exit = violation, prose/unrunnable = non-gating advisory. validate-slice WS-1 gate now calls `--execute`. (DCE-1 timestamp cross-check + BCSG-1 demote still TODO.)
  - **3.8 (hyphen portion)** (v2.19.6) — skip regex now tolerates em-dash/en-dash/hyphen in BOTH CRP-1 + DCE-1. The CRP/DCE **merge** is DROPPED (re-scope: CRP-1 no longer a byte-clone after 2.3).
  - **REMAINING:** **3.7** (merge test_first/walking_skeleton/exploratory_charter → `brief_variants_audit.py` — RISKY ~970-line consolidation, recommend AFTER the 4.4 test harness exists); **3.9** (delete `date(2026,5,6)` carry-over from the 5 remaining audits — triage_audit, build_checks_audit, critique_review_audit, exploratory_charter_audit, validate_slice_layers — mechanical, keep `--no-carry-over` no-op; walking_skeleton already done); **3.1 rest** (DCE-1 drift-log-timestamp cross-check, BCSG-1 demote-to-advisory in build_checks_audit ≈513-531); **3.11** (shrink SVW-1 skill_vault_write_safety_audit.py 733→~150 lines); **3.17** (validate-slice fork must not AskUserQuestion mid-fork — return `blocked: needs-deferral-decision`, main thread asks + appends); **3.18** (schema/contract batch — incl `artifact_lint.py`, `_index.json` two shapes, examples carry load-bearing fields, **3.18.3 NEEDS USER DECISION** [variants producer-vs-delete], pulse updated_at→at, actor example, spike_ref format); **3.19** (8 small fixes — mock_budget JS gap, diagnose PERSISTING bug, finding_dedup stale header, write-path residuals, vault_root lazy, assemble split, UX-2 preamble, UX-8 hook stderr).
  - **WS-1 gate-log/example polish owed:** the mission-brief `architectural_layers` example still shows a prose `verification`; update to a runnable command under 3.18.2.
  - Helpers a later item should know about: `.build/plugin_self_audits.py` (six evicted self-audits, for CI 4.4), `skills/build-slice/scripts/pre_finish_gate.py` (2.5 gate orchestrator), `active_slice.py --folder-only`, and `gate_log.py`'s new `design-tournament` gate + `INFORMATIONAL_GATES`.
- **Phase 4 — ⬜ NOT STARTED.** Needs **USER DECISIONS**: 4.1 (license choice) and 4.10 (ship-vs-demote the
  generated design record); 4.4 (tests + CI) is the high-value root-cause fix.

Resume by reading this file + `git log --stat` on `fix/remediation-plan`.

---

## 0. Ground rules for the executing session (read first)

These come from the repo's own conventions — violating them breaks the build or the install:

1. **Generated vs hand-authored.** `skills/<name>/skill.json` (×30), `skills/<name>/examples/*.json` (×74), and `skill-graph.json` are **GENERATED** by `.build/aggregate.py` from `.build/manifests/batch*.json` + `schemas/artifact-examples.json`. Never hand-edit them — edit the manifest/canonical-example and re-run:
   ```
   $PY = "C:\Users\sshub\.claude\.venv\Scripts\python.exe"   # any Python 3 works
   & $PY C:\Users\sshub\aisdlc-v2\.build\aggregate.py
   ```
   `SKILL.md` and `scripts/` are hand-authored — edit those directly.
   ⚠️ `aggregate.py` (and 8 sibling scripts) hardcode `ROOT = r"C:\Users\sshub\aisdlc-v2"` — fine on this machine; item 4.8 fixes it. Never hand-edit the inverse-link fields (`created_by`/`edited_by`/`read_by`/`validated_by`) in any skill.json.
2. **Version bump is mandatory on every pushed commit** — bump `version` in `.claude-plugin/plugin.json` (patch = fix/docs, minor = feature/new skill, major = breaking) in the same commit. Multiple items may share one commit/bump; never push a behavioral change on an unchanged version.
3. **ADRs are append-only** (in user vaults); the plugin's own design files have no such rule, but keep change rationale in commit messages.
4. **Line numbers in this plan may have drifted.** Always locate by the quoted text, not the line number alone. If quoted text is absent, re-verify the finding before "fixing" — a few items may have been fixed since the review.
5. **Self-verification after each phase:** run the listed acceptance checks. There is currently no test suite (that's item 4.4) — until it exists, verification is grep + running the audit scripts manually.
6. **Scope guard:** this plan modifies only `C:\Users\sshub\aisdlc-v2`. Never touch `C:\Users\sshub\ai_sdlc` (v1, read-only).

**Suggested commit strategy:** one commit per numbered item (or per coherent group), version bump per push batch. Phases are ordered by dependency and value; within a phase, items are independent unless noted.

---

## Phase 1 — Critical correctness (the pipeline is broken for real users today)

### 1.1 Un-deadlock the critique-skip path *(CRITICAL, confirmed)*
**Problem:** `skills/critique/SKILL.md` gating table row `Standard | low | false | SKIP` (≈line 64) legitimately skips critique, writing **only** a milestone marker (lines ≈75–78), no `critique.json`. But `skills/build-slice/SKILL.md` ≈line 35: *"If critique.json is absent (Standard / Heavy mode): STOP — run /critique first."* Running /critique skips again → closed loop. `skills/design-slice/SKILL.md` ≈line 279 ("OR /build-slice if critic_required: false") hits the same wall.
**Fix:** in build-slice's prerequisite #2, accept the skip record: critique.json absent is OK **iff** milestone.json marks the critique step `skipped`. Alternative (also acceptable): make critique's SKIP path write a stub `critique.json` `{"verdict":"skipped","findings":[],...}` — if chosen, update the stage-derivation chains (item 3.13) and commit-slice's reporting accordingly.
**Accept:** trace the Standard/low/no-triggers path through critique → slice-story → build-slice on paper: no STOP fires. Grep build-slice for the new skip-acceptance text.

### 1.2 One active-slice resolver everywhere *(CRITICAL, confirmed)*
**Problem:** parallel slices are explicitly supported (`skills/slice/SKILL.md` ≈30–31 "parallel slices are normal"; commit-slice ≈159 `parallel_slices → PROCEED`), but skills resolve "the active slice" three incompatible ways, silently corrupting cross-slice state when 2+ slices are in flight (wrong mission-brief, wrong worktree via `_worktree_paths.py`, wrong gate-log rows):
- mtime: `ls -1t …/slices/ | grep -v archive | head -1` — build-slice ≈24, 29, 56, 169; validate-slice ≈102, 113, 164, 179, 198; code-review ≈46.
- lexicographic: `sorted(glob(...))[-1]` — critique ≈26, 32; slice-story ≈28; risk-spike ≈215; supersede-slice ≈25.
- branch-first (CORRECT): `scripts/lib/active_slice.py` (git branch `slice/NNN-name` → folder, vault-scan fallback) — already used by reflect, critique-review, design-slice (`active_slice_brief.py`), validate-slice's first injection (`scripts/active_slice_info.py`).
Note: critique already injects `pulse_worktree_resolver` output at ≈line 20 and then ignores it.
**Fix:** replace every mtime/lexicographic site with an injection or bash call to `active_slice.py` (mirror how validate-slice line ≈21 / reflect do it). Slices/ also contains non-slice files (`_index.json`, `action-points.json`) — the resolver already handles this; the `ls -1t` sites don't.
**Accept:** `grep -rn "ls -1t" skills/*/SKILL.md` → no active-slice-resolution hits; `grep -rn "sorted(glob" skills/*/SKILL.md` → none used for slice resolution. validate-slice no longer self-disagrees (branch-aware at top, `ls -1t` later).

### 1.3 One critique-review contract *(CRITICAL, confirmed)*
**Problem:** three mutually incompatible specs:
- `agents/critique-review.md` (system prompt): premortem + independent re-derivation, **9** dimensions, output `confirmed[]/suspicious[]/severity_adjustments[]` (≈line 72).
- `skills/critique-review/SKILL.md` Step 2 (≈75–80): orders the OPPOSITE method ("independently apply the **8** review dimensions" forward) and output `assessments[]/missed[]`.
- Enforcement: `skills/critique-review/scripts/critique_review_audit.py` (≈line 80) + bundled `examples/critique-review.json` enforce the **SKILL.md** shape — while `/critique` Step 3.5's merge (critique/SKILL.md ≈188–194) consumes the **agent-file** shape. So audit-valid output breaks the merge; agent-shaped output fails the audit.
**Fix (pick one schema — recommended: the audited `assessments[]/missed[]` shape, since the audit + example already enforce it):**
1. Rewrite `agents/critique-review.md` §Output to the audited schema (keep the premortem/re-derivation METHOD — that's the decorrelation value).
2. Strip SKILL.md Step 2's inline persona/method/schema block to **inputs-only** (mirror `/critique`, which says "do NOT re-state… pass only inputs"). Remove the "8 dimensions" sentence entirely.
3. Update `/critique` Step 3.5 merge logic (critique/SKILL.md ≈188–194) to consume `assessments[]/missed[]` (map classifications VALID/SUSPICIOUS/SEVERITY-WRONG onto the merge behavior).
**Accept:** the schema appears in exactly ONE place (agent file); SKILL.md passes inputs only; merge step names the same fields as the audit's required-field list; `critique_review_audit.py` passes against `examples/critique-review.json`.

### 1.4 One critique contract; bundled example must pass its own audit *(CRITICAL, confirmed by execution)*
**Problem:** `agents/critique.md` ≈147 specifies `"result":"CLEAN|NEEDS-FIXES|BLOCKED"` + `issue`/`evidence`/`builder_response` (uppercase), while critique/SKILL.md ≈177–180 requires `verdict` (lowercase) + `disposition` + `triage`, and the canonical example uses `"action": "fix-now"` — which `skills/critique/scripts/triage_audit.py` ≈94–96 REJECTS (allowed: `accepted-fixed, accepted-pending, overridden, deferred, escalated`). Verified: running triage_audit.py on the plugin's own bundled example → exit 1, 2 violations.
**Fix:**
1. In `schemas/artifact-examples.json` (critique key, ≈line 187): `"action": "fix-now"` → `"accepted-pending"` (matches the example's `needs-fixes` verdict). Check the example's `_note` enum on ≈line 183 for the same stale value.
2. Align `agents/critique.md` ≈147 output contract to the example's exact field names (lowercase `verdict`, `disposition` enum).
3. Re-run `aggregate.py` to fan the corrected example out to `skills/critique/examples/critique.json`.
**Accept:** `$PY skills/critique/scripts/triage_audit.py` against `skills/critique/examples/critique.json` → exit 0. `grep -rn "fix-now" schemas/ skills/` → no hits. Agent file and SKILL.md name identical fields.

### 1.5 Evict the six plugin self-audits from the user-facing build gate *(CRITICAL, confirmed)*
**Problem:** `skills/build-slice/SKILL.md` ≈193–212 unconditionally runs, in EVERY user project on EVERY slice: `UTF8-STDOUT-1` (utf8_stdout_audit.py), `PCA-1` (pipeline_chain_audit.py), `BCI-1` (build_checks_integrity.py), `STP-1` (state_transition_pin_audit.py), `NAW-1` (new_agent_warning_audit.py), `SVW-1` (skill_vault_write_safety_audit.py). All default `--root` to the **plugin install dir** (`parents[3]` of the script). For users: BCI-1/STP-1/NAW-1 are no-ops; PCA-1/UTF8/SVW-1 re-scan the plugin's own static files every slice (constant result per plugin version). ~109KB of script, 6 subprocess calls/slice, zero user value. Additionally `skills/reflect/SKILL.md` ≈151 tells the user to edit `tests/methodology/fixtures/build_checks/canonical_project_checks.json` — a path that exists NOWHERE (repo has no tests/ dir), broken even for self-development.
**Fix:**
1. Remove the six invocations from build-slice's pre-finish gate (the gate keeps: WT-ROOT-1, DCE-1, CRP-1, BRANCH-1, WIRE-1, BC-1, TF-1 conditional, LINT-MOCK — the user-facing set).
2. Move the six into plugin CI: invoked from `.build/` (alongside `cross_block_audit.py`) and wired into the GitHub Actions workflow created in item 4.4. The scripts themselves stay where they are (or move under `.build/`; either way SKILL.md no longer references them).
3. Fix reflect's BCI-1 step: either create the fixture (and make `build_checks_integrity.py` resolve it correctly) for plugin self-development only, or remove the BCI-1 promotion instruction from reflect and keep BCI-1 purely in CI.
**Accept:** `grep -n "PCA-1\|BCI-1\|STP-1\|NAW-1\|UTF8-STDOUT-1\|SVW-1" skills/build-slice/SKILL.md` → none in the gate command list; CI config invokes all six; `grep -n "tests/methodology" skills/reflect/SKILL.md` → no dangling path.

### 1.6 Fix the cp1252 git runners; collapse four duplicates *(MAJOR, confirmed)*
**Problem:** known bug class (repo's own lessons: slice-090, BB-25) left in shared helpers: `scripts/lib/_git_default_branch.py` ≈15–22 `run_git` and `scripts/lib/pulse_worktree_resolver.py` ≈149–155 `_run_git` both use `subprocess.run(text=True)` with NO `encoding=` → UnicodeDecodeError / mojibake on Windows with non-ASCII branch/worktree names. Third/fourth copies: `scripts/lib/stranded_slice_audit.py` ≈119–120, `scripts/lib/wt_root_audit.py` ≈41–42. `skills/commit-slice/scripts/stale_branch_classifier.py` ≈38–43 carries a comment *working around* the bug by name instead of fixing it. Exposed consumers: NAW-1, BRANCH-1, pulse, stranded_slice_audit, `skills/slice/scripts/claim_candidate.py` ≈37.
**Fix:** make `_git_default_branch.run_git` the single shared runner with `encoding="utf-8", errors="replace"` (or bytes-capture + main-thread decode, matching `_vault_paths.py` ≈122–142 / `active_slice.py` ≈62–70 which already do it right). Delete the three local copies; import the shared one. Delete the workaround comment in stale_branch_classifier.
**Accept:** `grep -rn "text=True" scripts/ skills/*/scripts/ | grep -i git` → zero hits without an `encoding=` on the same call; one runner definition repo-wide.

### 1.7 vault_edit `list`/`count` must fail visibly *(MINOR but quick, on the load-bearing path)*
**Problem:** `scripts/lib/vault_edit.py` `_cmd_list` ≈409–420 returns exit 0 + empty output for a missing/typo'd `--dir` (indistinguishable from an empty dir); `_cmd_count` ≈423–433 prints `0` for a non-array field. Contradicts the module's own fail-VISIBLE contract (`get` exits 2 on missing targets).
**Fix:** `list` exits 2 when `--dir` doesn't exist or isn't a directory; `count` exits 2 on a non-array field. Check SKILL.md call sites (archive sweeps, index rebuilds) tolerate the new exit code (`|| fallback` patterns).
**Accept:** manual run of each subcommand against a missing target → exit 2 with a message.

### 1.8 Default-branch resolution fallback for `git init` repos *(MAJOR, confirmed/reproduced)*
**Problem:** `scripts/lib/_git_default_branch.py` ≈25–41 tries only `origin/HEAD` symbolic-ref (exists only after clone) and `git config init.defaultBranch` (commonly unset) → returns None → BRANCH-1 (`branch_workflow_audit.py`) and NAW-1 (`new_agent_warning_audit.py` ≈219–224) exit 2 on a plain `git init` repo. Remediation message exists, so it's one-time — but it's a spurious deterministic failure in the user's first slice.
**Fix:** add fallbacks before declaring unresolvable: `git symbolic-ref --short HEAD` (works at repo birth), then probe for `main`/`master` refs. Degrade BRANCH-1 to WARN (not exit 2) when genuinely unresolvable.
**Accept:** in a scratch `git init` repo with `init.defaultBranch` unset: resolver returns the current branch; BRANCH-1 returns 0/WARN.

---

## Phase 2 — Cost & loop redesign (the adoption-killer)

Background numbers (verified): a medium-tier Standard slice for a 50-line change = 9–11 subagent spawns, ~80–100 script invocations, 6–9 user halts. Minimal mode barely changes this because in-loop gating keys on risk **tier** (default: medium), not mode.

### 2.1 Make mode a real cost lever *(MAJOR, confirmed)*
**Problem:** `skills/slice/SKILL.md` ≈73 default tier = medium; `skills/design-slice/SKILL.md` ≈69–77 tournament table keys on tier only (no mode column); `skills/critique/SKILL.md` ≈59–67 runs the Critic on `Minimal | medium/high`; build-slice gate has no mode switch. So Minimal ≈ Standard in-loop. Bonus contradiction: `skills/commit-slice/SKILL.md` ≈91 claims "Minimal mode (no critique)".
**Fix (design decision included):** add a mode row to the cost-bearing tables:
- Minimal + medium tier → single-flight design (no tournament), critique OPT-IN (prompt once, default skip — and per 1.1 the skip must not deadlock), no critique-review, slice-story not auto-invoked (see 2.2).
- Minimal + high tier → keep current behavior (high risk is high risk).
- Update `skills/triage/SKILL.md`'s mode-explanation text to state honestly what each mode costs per slice; fix commit-slice's ≈91 parenthetical to "no critique.json present (skipped)".
**Accept:** tables in design-slice/critique/slice-story show a mode dimension; a Minimal+medium dry-run on paper spawns ≤2 subagents before build.

### 2.2 Take slice-story out of the mandatory chain *(MAJOR, confirmed)*
**Problem:** `skills/critique/SKILL.md` ≈75–78 (even the SKIP path!) and ≈301–302/322–323 auto-advance to /slice-story; `skills/slice-story/SKILL.md` ≈144–146 "Do NOT auto-advance to /build-slice. This is a halt point" — a narrator subagent spawn (12.8KB persona, all artifacts re-embedded), HTML render, SendUserFile push, and a forced halt on EVERY slice in EVERY mode, minutes after the user personally triaged findings at TRI-1 and immediately before build-slice's own plan-approval halt (≈121–123). Three consecutive user stops.
**Fix:** tier/mode-gate the auto-invocation: auto-run only when (tier ∈ {medium, high} AND mode ∈ {Standard, Heavy}) — or simplest: only when critique actually RAN and produced ≥1 finding. On the skip path: print one line offering `/slice-story`, advance directly to build-slice's plan gate. Keep the skill fully user-invokable (it already supports out-of-loop invocation). Optional stronger variant: make it non-blocking when auto-run (deliver HTML in background; do not halt).
**Accept:** critique's skip path no longer invokes slice-story; the Standard+medium clean path has at most 2 consecutive halts (TRI-1 → plan approval).

### 2.3 Align critique-review's trigger with its own frontmatter; delete the duplicate CRP-1 run *(MAJOR)*
**Problem:** three conflicting rules: critique/SKILL.md ≈186–188 ("mandatory in Standard/Heavy for methodology surfaces — advisory otherwise; skip only on a low-tier slice with no mandatory triggers" — self-contradictory for medium-tier), critique-review frontmatter ("Recommended for: high-tier… Optional for low-tier"), and `skills/build-slice/scripts/critique_review_prerequisite_audit.py` enforcing its own notion. CRP-1 also runs TWICE in build-slice (≈38–42 STOP + ≈196–197 "defense-in-depth").
**Fix:** one table, stated once in critique/SKILL.md Step 3.5 (like the critique gate table): run critique-review when {tier = high} OR {slice touches auth/data-model/contracts/methodology surface} OR {3+ consecutive clean critiques} OR {≥5 findings}. Everything else: skip with a milestone marker. Make `critique_review_prerequisite_audit.py` implement exactly that table (it can read tier + trigger flags from mission-brief/milestone). Update critique-review's frontmatter to reference the table. Delete the second CRP-1 invocation at ≈196–197.
**Accept:** the rule exists in one place; the audit's logic provably matches it; `grep -c critique_review_prerequisite skills/build-slice/SKILL.md` → 1.

### 2.4 Give code-review's output a consumer *(MAJOR)*
**Problem:** code-review findings are advisory and nothing downstream reads its blockers: `skills/code-review/SKILL.md` ≈84 ("Findings are advisory in v1"); validate-slice reads only `measure_at_validate` (≈27); commit-slice reports critique blockers but not code-review's (≈46). A blocker-level finding can sail to merge untouched — the loop's most expensive advisory comment.
**Fix (cheapest sufficient):** add a disposition gate where code-review returns to the main thread: blocker-level findings must each be either fixed (re-run the relevant check) or explicitly overridden with a one-line rationale recorded into code-review.json (`triage` block mirroring TRI-1's shape, batched UX). validate-slice's prerequisite: no un-dispositioned blockers. commit-slice's report includes code-review blockers + dispositions.
**Accept:** the words "advisory in v1" are gone; validate-slice checks the disposition field; a blocker without disposition blocks validate-slice with a clear message.

### 2.5 One orchestrator for the build gate *(MAJOR)*
**Problem:** even after 1.5, the pre-finish gate is ~7 separate `$PY` invocations whose outputs the model must individually run and reconcile (`skills/build-slice/SKILL.md` ≈163–213; BC-1 runs twice ≈183–185). Likeliest real failure: the model skips one.
**Fix:** new `skills/build-slice/scripts/pre_finish_gate.py` that imports the remaining audit modules (they expose `audit()`/`check()`-style entry points; where one doesn't, add a thin function) and emits ONE consolidated JSON verdict {gate: PASS/FAIL, per-check results, remediation strings}. SKILL.md Step 6 becomes one command + one interpretation block. Deduplicate the double BC-1.
**Accept:** build-slice Step 6 contains exactly one `$PY` gate invocation; running it in a scratch vault produces consolidated JSON.

### 2.6 Inline the commit-message template (drop the Haiku dispatch) *(MINOR)*
**Problem:** `skills/commit-slice/SKILL.md` ≈61–67: main thread gathers all inputs, then spawns a Haiku subagent to fill a 12-line template — spawn overhead > the ~500 tokens saved (COST-1 misapplied).
**Fix:** fill the template inline in commit-slice. Keep Haiku dispatch in `/archive` (index regen — the defensible use).
**Accept:** commit-slice contains no Agent spawn for the commit message.

### 2.7 Tier the agent models *(MAJOR, quick)*
**Problem:** all 11 `agents/*.md` pin `model: opus` (frontmatter line ≈5 in each). Narrator/survey personas don't need it.
**Fix:** set `model: sonnet` for `slice-story.md`, `diagnose-narrator.md`, `product-doc.md`, `field-recon.md`. Keep opus for the 3 critics + 3 designers + critic-calibrate.
**Accept:** `grep -l "model: opus" agents/*.md` → exactly 7 files.

### 2.8 Stop double-injecting slice artifacts *(MINOR)*
**Problem:** mission-brief.json/design.json are dynamically injected at critique skill load (critique/SKILL.md ≈29–33) AND re-embedded verbatim in the critique agent prompt (≈113–163), again in critique-review, again in the slice-story narrator (slice-story ≈62–87), again at build-slice (≈22–30) — 3–4 full crossings each.
**Fix:** at the skill level inject only the orchestration scalars (slice id, tier, critic_required — already a separate injection); let only the agent prompt carry full JSON. Apply the same review to critique-review and slice-story.
**Accept:** critique/SKILL.md no longer injects full design.json at skill load.

### 2.9 Delete the finding-distribution anchors *(MAJOR, confirmed verbatim)*
**Problem:** `agents/critique.md` ≈139 "Most slices: 0–2 blockers, 1–4 majors" + ≈164 "'no issues' three slices running is statistically suspect — look harder"; same pair in `agents/code-review.md` ≈63/≈67; streak line repeated in critique/SKILL.md ≈333. These prime the model to manufacture findings, directly biasing the precision metric the gate-log/critic-calibrate spine exists to measure — and contradict "zero findings is a valid result" (critique.md ≈133) two paragraphs above.
**Fix:** delete the distribution sentence and the quiet-streak suspicion lines from both agent files and critique/SKILL.md. Keep the adjacent anti-severity-inflation sentence ("If you want to file everything as blocker, recalibrate") — that one is fine. Under-firing detection belongs to /critic-calibrate empirically.
**Accept:** `grep -rn "0–2 blockers\|1–4 majors\|statistically suspect\|three slices running" agents/ skills/` → zero hits.

### 2.10 Design-spike: fix the vacuous high-tier trigger; add a materiality bar *(MINOR)*
**Problem:** design-slice ≈227–229 fires `/risk-spike --mode design` on `risk_tier high OR irreversible ADR` — but design-spike mode (risk-spike ≈209–224) only reads pending decidable_disagreements + must-verify invariants; with none, it writes a skip note and forwards. The tier table (≈74) sells this as a "mandatory design spike" — an empty round-trip. Separately, the synthesis is instructed to mine for decidable disagreements with no materiality bar, making the "conditional" spike near-routine.
**Fix:** (a) drop the tier/irreversible clause from Step 8 (rely on the two real target conditions) and fix the tier-table cell; (b) add a materiality filter to the synthesis instruction: record a decidable_disagreement only when the losing answer would force re-synthesis; cheap questions resolve during build's smoke gate.
**Accept:** design-slice Step 8 lists exactly two trigger conditions; the synthesis prompt contains the materiality sentence.

### 2.11 Gate-log: one write per slice in Minimal *(MINOR, optional)*
**Problem:** 6+ two-process bash invocations (gate_log.py | vault_edit append) per slice feed analytics consumed every 10–20 slices (/pulse, /critic-calibrate). Pure overhead for short projects.
**Fix:** in Minimal mode, skip per-gate rows and let /reflect emit one consolidated append (it already reads every artifact). Standard/Heavy unchanged (the spine is the product there).
**Accept:** Minimal-mode skills' gate-log blocks are conditioned on mode.

---

## Phase 3 — Coherence, philosophy alignment & script consolidation

### 3.1 Attestation gates: add reality or demote *(MAJOR)*
**Problem:** DCE-1 (`drift_check_audit.py`), CRP-1, BCSG-1 (in `build_checks_audit.py` ≈513–531), WS-1 (`walking_skeleton_audit.py`), ETC-1 (`exploratory_charter_audit.py`) verify model-written JSON markers and run as hard exit-code STOPs — model-grading-model wearing deterministic clothing, while sitting entirely OUTSIDE the gate-log measurement spine. The scripts self-describe honestly ("was-it-MARKED, not was-it-RUN" — drift_check_audit.py ≈9–13).
**Fix:** (a) WS-1: actually execute each layer's `verification` command (subprocess, like `shippability_runner.py` ≈148–171 does for machine_cmd) — turns it into a real reality gate; (b) DCE-1: cross-check the drift-log entry timestamp against a /drift-check side-effect file; (c) where reality contact isn't feasible (BCSG-1 ack echoes), demote to advisory (exit 0 + report) and/or log them as model-tier rows in gate-log so the spine sees them.
**Accept:** WS-1 spawns the verification commands; remaining attestation-only checks are advisory or gate-logged as model-tier.

### 3.2 Let the calibration loop retire worthless gates *(MAJOR)*
**Problem:** `agents/critic-calibrate.md` ≈147–148/≈159: the LIGHTEN direction "NEVER disables the gate, changes the mode/tier table" — so measured precision ≈ 0 can never stop the per-slice critique-review spawn. The 3-layer critic stack is unfalsifiable in practice.
**Fix:** add a third proposal kind `gate-skip`: tier-gate or skip a MODEL-tier gate for this project, requiring precision < 0.2 over ≥ 8 logged runs AND explicit user acceptance; persisted to the same vault overlay (`critic-calibration-log.json` active_checks mechanism). The reality spine (risk-spike, validate-slice) stays structurally untouchable — keep the existing filter-by-construction.
**Accept:** critic-calibrate.md + skills/critic-calibrate/SKILL.md document the third kind with the evidence threshold; reality-spine exclusion still provable from the filter.

### 3.3 Measure designer divergence *(MINOR, high information value)*
**Problem:** "diverse at generation" is asserted, never measured — the pipeline records CHOSEN/PARTIAL/NOT-CHOSEN but not whether proposals materially differed. Only crossdomain's WebSearch deprivation is structural; practice and expert plausibly converge.
**Fix:** design-slice's synthesis step records `approach_divergence: identical|overlapping|disjoint` per designer-pair into design.json.tournament and a gate-log row. /critic-calibrate (or /pulse --full) reports it. Decision rule documented in design-slice: if practice≈expert on most high-tier slices over a project, drop to 2 designers.
**Accept:** design.json example gains the field; synthesis instructions populate it.

### 3.4 Reconcile designer-expert with the philosophy ban *(MAJOR doc fix, small)*
**Problem:** CLAUDE.md ≈13–14 bans verbatim "design it the way expert X would… never in external authority", while `agents/designer-expert.md` ≈10–11 is exactly that. The generation-vs-selection carve-out exists only in untracked roadmap.md + design-slice ≈197–205.
**Fix:** add one sentence next to the ban (CLAUDE.md and README's philosophy paragraph): "Exception: authority may be channeled at GENERATION time inside the tournament (designer-expert) — never at SELECTION; reality and the synthesis rules select." Defer the drop-the-agent decision to the 3.3 divergence data.
**Accept:** the ban paragraph carries the carve-out wherever it's stated.

### 3.5 critic-calibrate output template targets the overlay, not the shipped file *(MINOR)*
**Problem:** `agents/critic-calibrate.md` ≈89–93 instructs proposals as splice-into-`agents/critique.md` text — the file the skill itself forbids editing (lost on upgrade); the real mechanism is the `active_checks[]` overlay; ≈156 "the user applies them" is stale.
**Fix:** rewrite the Step-5 proposal template to emit overlay-check entries (CC-NNN: trigger + check + example); ≈156 → "the skill persists accepted checks to the vault overlay."
**Accept:** no instruction anywhere tells the user to edit agents/critique.md.

### 3.6 De-duplicate doctrine between /critique and its agent *(MINOR)*
**Problem:** critique/SKILL.md ≈110–111 says "do NOT re-state them here", then ≈125–132 and ≈134–141 restate the cross-domain attack guidance and expert-independence rule nearly verbatim from agents/critique.md ≈28.
**Fix:** reduce the spawn template's two doctrine blocks to data only (tournament block + channeled_experts list).
**Accept:** the doctrine prose exists only in the agent file.

### 3.7 Merge the three variant audits *(MAJOR consolidation)*
**Problem:** `test_first_audit.py` + `walking_skeleton_audit.py` + `exploratory_charter_audit.py` (~44KB total) are one parameterizable shape: variants.<flag> gate → rows array → required fields → status enum → --strict-pre-finish → exit 0/1/2. Real differences to preserve: TF-1's disk checks (PTFCD/PTFFD) + AC-coverage cross-check + --root; WS/ETC's mtime carry-over (being deleted in 3.9 anyway); ETC's status-conditional required field. Known drift between them already (UPPER vs lower statuses).
**Fix:** one `brief_variants_audit.py` driven by a declarative table {variant → array_key, required_fields, statuses, terminal_status, special_hooks}; TF-1's disk/AC checks as hooks. Update the two SKILL.md call sites (build-slice ≈188, validate-slice ≈136/147). Normalize the status-case drift while merging.
**Accept:** three scripts deleted, one added (~12–15KB); both SKILL.mds invoke the merged audit; behavior parity spot-checked on crafted PASS/FAIL fixtures.

### 3.8 Merge CRP-1/DCE-1; tolerate the hyphen *(MINOR)*
**Problem:** `drift_check_audit.py` and `critique_review_prerequisite_audit.py` are acknowledged byte-clones (drift_check_audit.py ≈63–65 says so) differing only in marker file/key; the skip escape-hatch regex requires the literal em-dash `skip — rationale:` — a plain hyphen blocks the gate (typography, not dishonesty).
**Fix:** one `prerequisite_marker_audit.py` parameterized by {marker_file, skip_key} (or a shared lib helper both thin wrappers import); skip regex accepts `—`, `–`, or `-`.
**Accept:** the clone comment is gone; a hyphenated skip line passes.

### 3.9 Delete the NFR-1 mtime carry-over *(MINOR)*
**Problem:** `date(2026, 5, 6)` release-date exemption hardcoded in 6 scripts (`triage_audit.py` ≈85, `critique_review_audit.py` ≈76, `build_checks_audit.py` ≈88, `validate_slice_layers.py` ≈64, `walking_skeleton_audit.py` ≈68, `exploratory_charter_audit.py` ≈70) — dead for every post-install user, mtime is the wrong key (copy/restore flips it), and `wiring_matrix_audit.py` ≈35–39 already removed it with written justification.
**Fix:** delete the branch + constant from all six (3.7's merge removes two of them anyway); keep `--no-carry-over` as an accepted no-op flag for one release.
**Accept:** `grep -rn "2026, 5, 6" skills/ scripts/` → zero.

### 3.10 Remove the degenerate `--resolve-soft` surface *(MINOR)*
**Problem:** `parallel_conflict_resolver.py` ≈41–44 self-describes `--resolve-soft` as DEGENERATE (nothing to auto-resolve in v2); the flag + its commit-slice prose keep a dead model alive.
**Fix:** remove the flag and its SKILL.md prose; keep `--diagnose` / `--verify-resolution` / `--record-hard-resolution`.
**Accept:** flag gone from script and commit-slice SKILL.md.

### 3.11 Shrink SVW-1 to a dumb advisory check *(MAJOR simplification)*
**Problem:** `scripts/lib/skill_vault_write_safety_audit.py` — 733 lines of regex-NLP (verb lexicons, negation lookback, hyphen-compounds, per-(file,reason) exemption-count pins) grading model-written prose; its own HONEST SCOPE (≈36–44) admits it cannot observe the actual runtime hazard, and BB-01 (≈187–194) records it once matched nothing real. After 1.5 it's CI-only, but still worth shrinking.
**Fix:** reduce to: flag any non-fenced SKILL.md/agents line containing a shared-aggregate basename (risk-register.json etc.) with no `vault_edit`/`safe_` token within N lines; advisory exit 0 + report. Delete the lexicons, op-class inference, exemption pinning. Also fix its stale docstring/--help claiming `scripts/**` is scanned (BB-05 removed that — ≈7–10 vs ≈461–467).
**Accept:** file ≤ ~150 lines; docstring matches actual scan surface; CI still runs it.

### 3.12 supersede-slice: drop the doomed index stamp *(MINOR)*
**Problem:** Step 6 (supersede-slice/SKILL.md ≈102–121) CAS-writes `superseded_by` into a `_index.json` recent[] row — but archive's regen contract (archive/SKILL.md ≈117–124: fixed field set, "EXACTLY the 10 most recent") and reflect's regen rebuild rows WITHOUT that field, erasing it; rows age out of recent[] within 10 slices anyway. The durable link (reflection.json + mission-brief.json + supersede_audit) already survives.
**Fix:** delete Step 6 — OR stamp `slices/archive/_index.json` (the full catalog) instead and add `superseded_by` to both regen field sets (archive + reflect). Recommended: delete Step 6; have /pulse read supersession from reflection.json when listing archived slices. Consider (separate decision) folding supersede-slice into a /reflect prompt to shrink the skill count.
**Accept:** no write path whose next regen erases it.

### 3.13 One stage-derivation rule *(MINOR)*
**Problem:** archive's index regen (archive/SKILL.md ≈127–133: "no critique.json → design" before checking build-log) vs pulse's fallback (pulse/SKILL.md ≈103–110: skips critique.json) label the same skipped-critique slice differently.
**Fix:** tiny shared script (or one canonical prose table both reference) deriving stage from file presence, with critique.json OPTIONAL (consistent with 1.1).
**Accept:** both skills cite the same rule; skipped-critique slice gets one stage.

### 3.14 Reflect must hand off to /commit-slice, and "shipped" must mean shipped *(MAJOR)*
**Problem:** at /reflect the code is still uncommitted (code-review's WT-ROOT-1 contract), yet reflect archives the slice, marks the candidate `shipped` (reflect/SKILL.md ≈217–218), and closes with "Run /slice" (≈255) — never mentioning /commit-slice, contradicting its own footer (≈271–275). Vault claims a state the repo doesn't have.
**Fix:** candidate status `validated` at reflect; `shipped` set by /commit-slice (which moves it to archive/candidates.json). Reflect's close message leads with "/commit-slice --merge (or --push) to land the code, then /slice". Update the slice-candidates example/enum docs (`status` lifecycle) accordingly.
**Accept:** grep shows reflect never writes `shipped`; commit-slice does; close message updated.

### 3.15 /adopt degrades gracefully without CRG *(MAJOR)*
**Problem:** adopt/SKILL.md ≈37 "If CRG_MISSING: STOP and tell the user to install it (pip install …)" — contradicts README ≈30 ("absent → graceful degrade"), /triage (≈48 "advisory only"), and bypasses the plugin's own `/ai-sdlc:setup` doctor.
**Fix:** on CRG_MISSING: offer (a) run `/ai-sdlc:setup` then retry, or (b) degraded adopt — interview + stack-file scan with findings marked "unconfirmed — CRG absent" (mirror reduce/SKILL.md ≈131–132's pattern).
**Accept:** adopt has no raw-pip instruction; degraded path documented.

### 3.16 drift-check: remove the phantom pre-commit claim *(MINOR)*
**Problem:** drift-check/SKILL.md ≈3–4 advertises "pre-commit hook (auto, --fast), <2s" — a SKILL.md cannot run as a git hook; hooks/ is SessionStart-only; nothing installs one.
**Fix:** rewrite as "fast mode for the /build-slice pre-finish gate" (which works) — or actually ship `drift_check_fast.py` + an opt-in git-hook installer in /setup. Recommended: rewrite the claim now; hook installer only if demanded later.
**Accept:** no pre-commit claim without a shipped mechanism. Update the manifest summary too (regenerate via aggregate.py).

### 3.17 validate-slice fork vs the deferral gate *(MAJOR)*
**Problem:** validate-slice is `context: fork` (SKILL.md ≈6–7) but ≈205–207 requires "explicit user approval to defer (with rationale logged in validation.json)" — the fork writes validation.json (Step 7) before returning and cannot AskUserQuestion. (The PCA-1 FAIL/PARTIAL HALTs are fine — ≈313–322 defines them as surface-and-resolve-post-return.)
**Fix:** the fork detects the shippability regression, returns `blocked: needs-deferral-decision` WITHOUT writing the deferral; the main thread asks the user and appends the decision + rationale to validation.json via vault_edit. Update CLAUDE.md's fork-list note if its wording changes.
**Accept:** no mid-fork user-input requirement remains; the deferral write happens main-thread.

### 3.18 Schema/contract layer fixes *(batch; all confirmed unless noted)*
1. **`_index.json` one shape** — archive/SKILL.md ≈119–120 claims fields (`total/active_count/archived_count/updated`, `recent[].summary`) its own cited example lacks (example rows: slice/title/shipped only); archive-catalog `slices[]` (≈136–137) exemplified nowhere; reflect/SKILL.md ≈205 says insert "after table separator" (v1 markdown leftover) and names the wrong array. Fix: two keys in `artifact-examples.json` (live index, archive catalog), align archive's field list, fix reflect's sentence/array, re-run aggregate.
2. **Examples carry every load-bearing field** — `critic_required` missing from the mission-brief example though slice writes it and critique gates on it; concept lacks `references[]`; design example lacks fields design-slice ≈173–176 names; no `wiring_matrix` exemption-variant example though the audit hard-fails on it. Fix: regenerate `artifact-examples.json` so every field a SKILL.md names appears (use the `cross_domain_transfer` present-with-`_note` pattern for optionals); then aggregate.
3. **`variants` gets a producer or dies** — three audits + TF-1 gate + TPHD-1 key on flags no skill ever sets true. Fix (choose): /slice Step 3 offers the three variants via one AskUserQuestion (recommended — the machinery is decent), OR delete the consumers. If 3.7's merge happens first, this is one audit + table rows either way.
4. **pulse `updated_at` → `at`** (pulse/SKILL.md ≈95 vs milestone schema `at`).
5. **heavy-architect actor example** — SKILL.md ≈97 cites `examples/actors/example-actor.json`; impossible (no `actor` key; aggregate.py emits flat paths and rmtrees examples/ each run). Fix: add `actor` key to artifact-examples.json, reference flat `examples/actor.json` in SKILL.md.
6. **spike_ref one format** — example says `"spikes/spike-ws-presence"` (path), producer writes bare `spike-<name>`. Standardize bare id; document the file-vs-dir layout of spikes/ in the example's `_note`.
7. **artifact_lint.py** — new `scripts/lib/artifact_lint.py` (~100 lines) driven directly by artifact-examples.json: require `_schema` tag, require the example's non-`_`-prefixed keys, check known enums. Call it in build-slice pre-finish (via 2.5's orchestrator), reflect, and /drift-check --fast. This converts schema-by-example from decorative to enforced and would have caught 1.4 automatically.
**Accept:** aggregate runs clean; artifact_lint passes on every bundled example (this check goes into CI, item 4.4).

### 3.19 Remaining small fixes *(batch)*
1. **mock_budget_lint JS gap** — `mock_budget_lint.py` ≈485–493: hoisted module-level `vi.mock`/`jest.mock` calls belong to no it()/test() scope and are silently dropped (the dominant JS pattern). Fix: count module-level mock calls as file-scope mocks against the TS boundary list; state Go's reduced coverage (≈533–536) in the output header.
2. **diagnose PERSISTING bug** — `scripts/lib/assemble.py` ≈2161 keys status off `carried_anno` (annotated-only; JS collect() ≈1678 stores only non-empty) so unannotated persisting findings show as New, and counts (≈2128–2129) are wrong. Fix: status from `prior_finding_ids` (already computed ≈2072–2077) incl. merged_ids; keep carried_anno purely for annotation carryover.
3. **finding_dedup stale header** — `finding_dedup.py` line 3 "SKETCH v0 (not yet wired into the build)" + stale ≈38–41 carryover note; it's load-bearing in /diagnose and /bug-hunt. Fix the docstring, name the two live call sites.
4. **Write-path residuals** — `_vault_write.py`: loop `os.write` (≈201) to completion or raise on short write; either fsync temp before `os.replace` or scope the "never truncated" docstring (≈149–163) to concurrency-not-crash; document or sweep the immortal `.lock` sidecars (≈104–106) — e.g. /archive offers a sweep.
5. **vault_root lazy resolution** *(optional)* — `_vault_paths.py` ≈262 resolves at import (git subprocess + stderr nag per CLI invocation). Consider module `__getattr__` lazy+cached resolution; preserves precedence, kills the per-import spawn.
6. **assemble.py split** *(optional)* — move the ~1,100-line CSS + ~180-line JS (≈501–1771, ~57% of the file) to sibling `assemble.css`/`assemble.js` inlined at build time; fix the false "Self-contained — no scripts.lib" comment (≈2260–2262 vs the import at line 43).
7. **UX-2 standard degradation preamble** — skills diverge on missing `$AI_SDLC_VAULT_ROOT`: adopt fails closed with `${VAR:-$($PY _vault_paths.py)}` + FATAL (≈23, 147–148); triage (≈22) and build-slice (≈24, 29, 56, 169) bare-`cat` with `2>/dev/null || echo NOT_FOUND` — triage can mis-detect an opened project as fresh and raw-write over it. Fix: one shared preamble (`VAULT=${AI_SDLC_VAULT_ROOT:-$("$PY" .../_vault_paths.py --path)}; [ -n "$VAULT" ] || FATAL`) in every vault-touching SKILL.md; triage fails closed before any raw-write.
8. **UX-8 hook swallows the cwd-keyed vault warning** — `hooks/setup_env.py` ≈113 captures and discards the resolver's stderr; `_vault_paths.py` ≈248–258 deliberately nags loudly when keying the vault on a non-git cwd. Fix: relay the WARN to the hook's stdout, or skip exporting `AI_SDLC_VAULT_ROOT` when resolution fell to the cwd fallback. (Interacts with 4.6 — do together.)

---

## Phase 4 — Distribution, infrastructure, lifecycle

### 4.1 LICENSE *(blocking for public distribution)*
**Problem:** publicly distributed (README instructs `/plugin marketplace add sshubham2/aisdlc-v2`; repo is its own marketplace; external PRs merged) with NO license file, no `license` key in plugin.json, zero README mentions — legally all-rights-reserved.
**Fix:** USER DECISION REQUIRED — pick a license (MIT/Apache-2.0 typical for plugins). Add `LICENSE`, `"license"` key in `.claude-plugin/plugin.json`, one README line. Ask the user; do not pick silently.

### 4.2 Fix the storefront *(MINOR, quick)*
**Problem:** `.claude-plugin/marketplace.json` line ≈8 still describes v1 ("two-persona adversarial review") vs plugin.json's current 3-critic description (same dir, same version!); plugin.json's description is a ~1,100-char jargon wall — the first text every installer reads.
**Fix:** rewrite both descriptions: one plain-language sentence (what it does, for whom) + one short feature sentence. Keep keywords for search.
**Accept:** both files agree; description < 400 chars, no pipeline jargon.

### 4.3 Pin the supply chain *(MAJOR)*
**Problem:** `code-review-graph` is third-party (github.com/tirth8205/code-review-graph — not the author's), pinned `>=2.3` with no upper bound (`requirements.txt` ≈19), auto-pip-installed by /setup, auto-registered as a trusted MCP server into the user's `.mcp.json` (setup.py ≈150–167), with a silent reinstall path at SessionStart (`setup_env.py` ≈140–152, AI_SDLC_AUTO_INSTALL=1). It feeds the "reality > code-graph" trust tier.
**Fix:** pin `code-review-graph>=2.3,<3` minimum (prefer `==` exact + a documented bump procedure); note the trust boundary in README's Requirements table; have /setup print the resolved version before MCP registration.
**Accept:** requirements.txt has an upper bound; setup surfaces the version.

### 4.4 Tests + CI for the plugin itself *(MAJOR — the root-cause fix)*
**Problem:** 72 shipped .py files (the gate scripts, the SVW-1 write path, finding_dedup, assemble.py), zero tests, zero CI, no .github/. This is how 1.1–1.4 survived: nothing deterministic ever exercised the happy path. (.gitignore even reserves `.pytest_cache/` — line ≈17.)
**Fix:**
1. `tests/` with pytest. Priority order: (a) `_vault_write.py` concurrency primitives (lock, CAS exit-3, append, EOL preservation — use tmp_path); (b) `vault_edit.py` subcommand contracts incl. the new exit codes from 1.7; (c) every audit script against crafted PASS/FAIL fixtures (the merged audits from 3.7/3.8 included); (d) `active_slice.py` resolution (branch vs fallback); (e) `finding_dedup.py` merge rules.
2. **Examples-pass-their-own-audits check** (would have caught 1.4): a test that runs triage_audit/critique_review_audit/artifact_lint over every bundled `examples/*.json`.
3. `.github/workflows/ci.yml`: pytest on windows-latest + ubuntu-latest (the repo's Windows-specific code paths make the matrix non-optional) + the six relocated self-audits (1.5) + `.build/cross_block_audit.py` + a `aggregate.py`-is-clean check (run it, `git diff --exit-code` on generated files).
**Accept:** CI green on both OSes; the 1.4 regression test fails if `fix-now` is reintroduced.

### 4.5 Vault artifact versioning *(MAJOR)*
**Problem:** contracts churn every minor version; no artifact carries a schema/plugin version; a vault written by 2.16 read by 2.19 has zero skew detection. Only v1→v2 PATH migration exists (`_vault_paths.py` ≈6–13).
**Fix:** every writer stamps `_schema: <artifact>/v1` + `_plugin_version` (vault_edit can inject on create; SKILL.md templates include it — the `_conventions.md` `_schema` tag already half-exists). Readers (pulse, drift-check, artifact_lint) WARN on unknown/major-newer schema. Document the policy in `_conventions.md`: schema bump = migration note in CHANGELOG.
**Accept:** new artifacts carry both fields; pulse surfaces a skew warning when present.

### 4.6 Fix the SessionStart hook's vault pinning *(MAJOR)*
**Problem:** `hooks/setup_env.py` ≈158–164 exports `AI_SDLC_VAULT_ROOT` resolved from the session-start cwd into `CLAUDE_ENV_FILE`; `_vault_paths.py` ≈235–240 gives that env var tier-1 precedence, frozen at import. So mid-session work against a DIFFERENT repo (`/bug-hunt <path>`, `/diagnose <path>`, cd) routes every vault write to the FIRST project's vault, silently. The hook also re-appends duplicate export lines on every clear/compact (file opened `'a'`), and runs ~5–6 subprocesses in every Claude Code project, ai-sdlc user or not.
**Fix:**
1. Stop exporting `AI_SDLC_VAULT_ROOT` from the hook entirely — export only `$PY`/`$CRG`, and let `_vault_paths.py` resolve the vault per-invocation from the actual cwd/git context (it already does this well; the env tier remains for explicit user overrides). If skills' bash blocks consume the env var directly, the 3.19.7 shared preamble covers the fallback.
2. Dedupe `CLAUDE_ENV_FILE` writes: read-check before append, or rewrite the managed block.
3. Cheap short-circuit at hook start when the project shows no ai-sdlc markers AND `$PY` already resolved this session (env-file check) — cuts the every-project subprocess tax.
**Accept:** two repos in one session resolve two different vaults; env file contains one export per var after 3 clears; non-ai-sdlc project hook run does ≤1 subprocess after first resolution.

### 4.7 Vault lifecycle & data hygiene *(MAJOR)*
**Problems (all verified):** (a) repo move/rename silently orphans the vault — the hash keys on the git-common-dir path; the tier-2 pin mechanism that would survive renames exists (`_vault_write.write_vault_root_config`) with ZERO callers; (b) no backup story (grep "backup" repo-wide: nothing); (c) validate-slice/risk-spike/field-recon persist captured command output (potential tokens/connection strings) as plaintext under `~/.aisdlc` forever — VAL-1 scans only the code diff, never the vault, and the secrets ALLOWLIST lives in the vault where editing it silently bypasses the Critical gate; (d) no GC/uninstall — orphaned vaults accumulate.
**Fix:**
1. /triage and /adopt write the tier-2 git-common-dir pin automatically at vault creation (mechanism exists; call it).
2. Openers detect a same-slug/different-hash sibling vault and offer migration instead of silently creating fresh.
3. README "Vault location" section gains a backup paragraph (e.g. `git init` inside the vault; it's just JSON).
4. VAL-1's secret patterns also sweep new evidence text before it's written to the vault (cheap: run the same regexes over captured output, redact matches with `[REDACTED:<type>]`); allowlist edits append a dated entry to drift-log (audit trail).
5. New `/setup --uninstall` (or a documented manual procedure): list this machine's vaults with their source repos, flag orphans (source path gone), offer deletion.
**Accept:** fresh /triage writes the pin file; renaming a scratch repo and re-running /pulse finds the vault; evidence with a planted fake AWS key is redacted in the vault copy.

### 4.8 Portable .build scripts *(MINOR, quick)*
**Problem:** 9 of 11 `.build/*.py` hardcode author-machine paths (`aggregate.py:9 ROOT = r"C:\Users\sshub\aisdlc-v2"`; same in manifest_reconcile, crg_swap, graph_audit, render_graph_html, crg_prose, candidates_spike, json_rollout; probe.py hardcodes a temp dir). README ≈237–244 documents `python3 .build/aggregate.py` as the contributor workflow — broken anywhere else.
**Fix:** `ROOT = Path(__file__).resolve().parents[1]` in each. Delete `probe.py` and `review.json` (session detritus — see 4.10.3).
**Accept:** `aggregate.py` runs from a clone in any directory.

### 4.9 Meta-docs cleanup *(MAJOR for maintainability)*
1. **CLAUDE.md rewrite** (~80 lines): philosophy paragraph; hard rules MINUS the temp/ rules (temp/ was deleted at the first commit — Test-Path False — yet ≈128–129/156 still enforce it); current layout; $PY/runtime section; regenerate instructions. Kill: "v2.0.0" (≈143), the three conflicting skill counts (26/27/30), `.md` ledger names (≈221–225), the dangling `memory/` reference, all "rollout in progress" narrative (it's restated in MILESTONE).
2. **CURRENT-STATE discipline:** top of MILESTONE.md becomes a single OVERWRITTEN current-state block (version, what's true now, next action); history appends below. Fix the three-way contradiction (CLAUDE.md says next = script porting [done]; MILESTONE says next = Phase 3 [shipped]; plan.md says all unpushed [merged as PRs #1/#2]).
3. **CONTRIBUTING.md (tracked):** the version-bump rule + never-hand-edit-generated-fields rule + regenerate commands currently live only in gitignored CLAUDE.md — i.e., only on the author's machine. Ship the durable rules.
4. **schemas/_conventions.md:** fix the false per-file example layout claim (≈4 — 35 of 36 examples live inside artifact-examples.json), enumerate all example keys (or generate the table in aggregate.py), delete the "next batch" footer.
5. **README dependency table:** sync with requirements.txt (PyYAML = diagnose + bug-hunt + slice-candidates fallback; CRG "installed by setup; degrades gracefully if absent" — consistent wording both places).

### 4.10 Stop shipping the dead design record *(MAJOR, decision required)*
**Problem (verified):** `skill.json` ×30 (250,698 B) + `skill-graph.json` (107,206 B) + `.build/` (367,654 B) = ~725KB ≈ 32% of the tracked repo with ZERO runtime consumers (grep across skills/scripts/agents/hooks: only 2 commentary mentions). The record is also already WRONG: its critique→build-slice edge predates slice-story (the shipped flow is critique→slice-story→build), and all 30 `source` fields + the graph's `generated_from` point at the deleted `temp/` tree.
**Fix (recommended option b):**
- (a) Keep shipping + make it true: regenerate manifests to include slice-story wiring, fix `source` fields (aggregate.py ≈243 default), add a CI check diffing `hands_off_to` vs SKILL.md successor lines. Cost: permanent double bookkeeping.
- **(b) Demote + stop shipping: gitignore `skill.json`/`skill-graph.json`/`.build/` (CLAUDE.md and skill-graph.html are already gitignored — same logic), keep them locally as the historical authoring record, delete the "source of truth" language from README/CLAUDE.md. Keep `examples/` — SKILL.md files genuinely reference those, so aggregate.py remains needed for example fan-out only.**
- Either way: 3. delete `.build/probe.py` + `.build/review.json` (55KB session detritus).
**USER DECISION REQUIRED between (a) and (b)** — (b) halves install size and ends the drift class; (a) preserves the public design-record story.

---

## Execution order & dependency notes

- Phase 1 first, items independent; 1.5 creates work consumed by 4.4 (CI) — fine to land the SKILL.md eviction first and wire CI later.
- 2.1/2.2 touch the same critique/SKILL.md sections as 1.1 — do 1.1 first, then 2.x.
- 3.7 before 3.9 (the merge deletes two carry-over sites for free). 3.18.7 (artifact_lint) before 4.4 (CI consumes it).
- 4.6 and 3.19.7/3.19.8 overlap (hook + env preamble) — implement together.
- Decisions needing the user: 4.1 (license choice), 4.10 (ship vs demote the design record), 2.1 (exact Minimal-mode table), 3.18.3 (variants: producer vs delete).
- After ANY change to `schemas/artifact-examples.json` or `.build/manifests/*`: re-run `aggregate.py` and commit the regenerated files together with the source change.
- Final sweep: bump plugin.json version appropriately (this plan is at least a minor, arguably more), update README's trailing version line (≈251), and re-run all relocated audits + the new test suite green before push.

## What NOT to change (protect these)

- `_vault_write.py`'s locking/CAS design (only the small residuals in 3.19.4) — it is correct.
- The reality-contact hierarchy, gate-log measurement spine, and TRI-1 triage design.
- designer-crossdomain's two-halves contract and its WebSearch deprivation.
- field-recon's asymmetric drop rule.
- The hook's fail-soft posture and the Windows hardening (BOM/cp1252/forward-slash discipline).
- The single-canonical-example → generated fan-out mechanism for examples/ (fix content, keep the mechanism).
