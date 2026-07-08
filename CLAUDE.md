# AI SDLC — v2 rebuild

> **Resuming after a compaction / new session?** The v2 build, the pipeline-improvement track, **and** the
> post-deep-review **remediation plan** (Phases 1-4; the `aisdlc-v2-fix-plan.md` doc was retired at v2.25.5 — it lives in git history now) are all **COMPLETE + VALIDATED** —
> the remediation was **merged to `master` (PR #4, 2026-06-12)**, the plugin is at **v2.25.4 on `master`**, and
> **4.6.1 is validated** (the installed hook de-leaks the legacy `AI_SDLC_VAULT_ROOT` export + two repos resolve
> two vaults; proven 2026-06-12). Read this file → `memory/` (esp. `memory/remediation-track.md`, the live state).
> **The ENTIRE plan is now implemented — nothing is open.** The last two OPTIONAL items shipped in **v2.25.3**:
> `3.19.5` (lazy `_vault_paths.VAULT_ROOT` via PEP-562 `__getattr__` — no `git rev-parse` at import) + `3.19.6`
> (extracted `assemble.py`'s CSS/JS to sibling `assemble.css`/`assemble.js`; rendered HTML byte-identical, proven by
> value-sha). The old local working docs (`roadmap.md`/`plan.md`/`MILESTONE.md`) are retired.

This repo is the **v2** rebuild of a spec-driven AI SDLC pipeline. v2's core change from v1:
**skills are described as JSON manifests instead of v1's markdown `SKILL.md` files.** The work
proceeds one skill at a time; v1 is the reference, v2 is the JSON.

## Pipeline philosophy (read before changing any skill's behavior)

This pipeline grounds AI output in **executable reality** — code-review-graph (blast-radius / reachability),
`/risk-spike` (throwaway code on *real* environments), `/drift-check` — and **never in external authority**
(no "design it the way expert X would"). **Exception (generation vs selection):** authority MAY be channeled at
GENERATION time inside the design tournament (`designer-expert`) to widen the approach sample — it is NEVER used
at SELECTION; reality and the synthesis rules select. That is deliberate and load-bearing: authority-grounding
*at selection* suppresses the model's most valuable capability — *cross-domain pattern transfer* — and anchors to
a lagging canon; verification-grounding unleashes it safely (let the model generate freely, then **prove against
reality**).
The operating rule at every seam: **diverse at generation · reality-grounded at selection · independent at
review.** Trust a gate exactly as much as it touches something that is *not the model* (reality > code-graph >
model-critic); never render "passed critique" and "passed spike" as the same green.

**The deeper crux (the *why* — load-bearing; this was `roadmap.md` Part I, now folded here so it survives the doc's deletion):**

- **Proximity is where insight AND hallucination both live.** The same vector-space nearness that retrieves the
  brilliant cross-domain transfer also retrieves the seductive-but-wrong one. Every analogy carries *invariants*
  (preconditions; CRDTs work only because ops commute/are idempotent). The model recognizes an analogy's *surface*
  far better than it checks whether its *preconditions hold here*. So **the model knows the solution; it does not
  reliably know *when* the solution applies** — that gap is the entire reason verification exists.
- **The pipeline is ONE principle applied recursively** — *don't trust the model's output, verify it* — at every
  seam it could be confidently wrong: assumptions→`risk-spike`, design→`critique`/`critique-review`, code→`code-review`,
  reality→`validate-slice`, claims-vs-code→`drift-check`/`sync`. The **slice** is the bounded unit that makes
  generate-then-verify fit in one head — "small cut so it can concentrate" is load management, not ceremony.
- **Diverse at generation · reality-grounded at selection · independent at review.** The cross-domain jackpot is a
  *generation* event (a critic finds flaws, it does not search the design space) → sample diversely via the design
  tournament, don't just scrutinize one flight harder. At selection, reality > code-graph > model-critic; where a
  choice is empirically decidable, let a *spike* decide, never taste. At review, the critic shares the generator's
  blind spots → keep reviewers independent (different method/persona/expert) and treat the reality-touching gates as
  the real spine.
- **Two standing hazards:** (1) vector-space proximity is *popularity-weighted* — left alone the model drifts to the
  most-blogged, over-engineered answer (keep `reduce` + the Critic pushing back). (2) Stacked scrutiny that shares
  blind spots *launders* errors into false confidence — more checks ≠ more safety unless they are independent and
  reality-touching; nagging about non-issues trains the human to rubber-stamp the triage gate.
- **Empirical humility:** how much to trust the latent space is *measurable*, not a belief — the spike already
  produces the number (how often a proposed cross-domain transfer actually holds). The gate-log measurement spine
  records it; tune the weight of scrutiny to the measurement, not to intuition.

**Preserve — do NOT break** (the load-bearing subsystems): `risk-spike` (the crown jewel — what makes cross-domain
creativity *safe*; the tournament splits & elevates it, never dilutes it) · code-review-graph grounding (the real
anchor) · `commit-slice` conflict resolution · the slice loop + `candidates.json` DAG (the bounded-unit precondition) ·
user-owned TRI-1 triage + append-only vault.

> **The improvement track that operationalized all of this is COMPLETE and shipped** — v2.7.0 → v2.18.0, on `master`,
> pushed (plugin now v2.18.3). The old local `roadmap.md` (8-theme backlog) / `plan.md` (phased execution) /
> `MILESTONE.md` (build log) are **retired**; their durable residue is *this* section, the build log below, and
> `memory/pipeline-improvement-track.md` (the decisions-not-to-relitigate, the verification recipe, and the remaining
> non-dogfood caveats). Git history `v2.7.0`→`v2.18.3` is the per-feature record.

## Direction — locked decisions (all four APPLIED)

These four are agreed. **Rollout status: ALL FOUR APPLIED** (manifests/graph reflect them):

1. **JSON, not markdown — ✅ APPLIED.** All vault artifact paths renamed `.md → .json` in the manifests/graph (structure as fields; prose as markdown-valued string fields). Schemas-by-example live in `schemas/artifact-examples.json`; each skill bundles an example of every JSON artifact it produces at `skills/<name>/examples/<artifact>.json` (per the Claude Code skills `examples/` convention) — 74 files across 24 skills (pulse/query-design read-only, diagnose emits HTML). Markdown kept ONLY for `./CLAUDE.md` (the harness auto-loads it as markdown — its reader is the runtime, not a skill) and `diagnosis.html` stays HTML (different medium). "Humans like prose" is NOT a reason to keep `.md`.
2. **graphify → `code-review-graph` (CRG), full swap — ✅ APPLIED.** CRG is the code graph (richer blast-radius/reachability, MCP-native, `code-review-graph build`/`update`/`install --platform claude-code`; graph stored in `.code-review-graph/`, queried via 30 MCP tools). 15 skills use it; `archive`/`critique`/`discover` dropped graphify entirely (their only use was the now-removed vault-graph/ingest). Two graphify features are **gone**: vault-graph (query the JSON vault directly) and multimodal ingest (external refs become plain reference *fields* in JSON). NOTE: `temp/skills/<name>/SKILL.md` (v1 source) still says graphify — that's expected; the v2 manifests + `skill-graph.json` are the swapped source of truth. The `slice-candidates/build_backlog.py` graphify migration is ✅ DONE — it queries CRG via `_crg_impact.py` subprocess (best-effort, degrades to shared-evidence-only); its only remaining "graphify" mention is the docstring saying it does NOT use it.
3. **One `<vault>/candidates.json`** — ✅ APPLIED. The single slice-candidate source of truth — absorbs v1's `backlog.md` + risk-register-as-candidate-source + `slice-queue.md` candidate section (both nodes now gone from the graph). Schema: `schemas/slice-candidates.example.json`. risk-register stays the *risk ledger*; risks materialize into candidates. Candidate carries `status`, `progress` (loop stage), `claimed_by {git_user,git_email}`, `started_at`, `dependencies` (DAG), `priority`, `assumptions[]`, `pick_log`. `candidates.json` holds only LIVE candidates (not-started + in-flight); on ship/reject the candidate is **moved** to `<vault>/archive/candidates.json` (mirrors `slices/` vs `slices/archive/`), so the live backlog stays small.
4. **`/risk-spike` moves inside the slice loop as a blocking step-0 — ✅ APPLIED; SPLIT in Phase 3.** `/slice` picks a candidate → `/risk-spike` (feasibility) proves each blocking `assumption` with throwaway code → all proven advances to `/design-slice`; any FAILED blocks the slice until a risk-free `fallback` is discussed and re-spiked. risk-spike removed from triage/discover/adopt hand-offs (no longer a pre-pipeline step). **Phase 3 (v2.10.0) split risk-spike into two modes**: the step-0 **feasibility** spike (above, unchanged default) PLUS a post-synthesis **design** spike (`--mode design`) that lets reality adjudicate the design tournament's empirically-decidable disagreements. New loop: `/slice → /risk-spike (feasibility) → /design-slice (tournament+synthesis) → [/risk-spike --mode design] → /critique → /slice-story → /build-slice → /code-review → /validate-slice → /reflect`.

## Skill model — manifest → real SKILL.md (next phase, per code.claude.com/docs/en/skills)

The 30 `skill.json` are the **design spec**. Authoring runnable skills means writing `skills/<name>/SKILL.md`
(+ bundled `examples/`, `scripts/`). Manifest fields map to real frontmatter:

| manifest field | real SKILL.md frontmatter | note |
|---|---|---|
| `requires_full_context: false` | `context: fork` (+ `agent: <type>`) | delegable skills run as forked subagents natively — **but see the fork caveat below** |
| `user_invokable` | `user-invocable` (hyphen) | |
| `harness_tools` | `allowed-tools` | pre-approve exactly what the skill needs |
| `agents` (named persona) | `context: fork, agent: <persona>` OR spawn via Agent tool | a Critic skill can *be* the forked agent — collapses skill+agent |
| `tools` (methodology) | Bash `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/X.py"` (shared) / `.../scripts/X.py` (single-skill) | path-form, not `-m` (see runtime-path note) |
| `outputs[].example` | bundled `examples/<artifact>.json` (✅ done) | |
| `summary` | `description` + `when_to_use` | combined ≤ 1,536 chars |

**Fork caveat (load-bearing):** `context: fork` applies ONLY when the skill is **non-interactive** — a forked
subagent cannot run `AskUserQuestion` mid-task. A "delegable" skill that has an interactive gate or a side-effecting
confirmation stays **INLINE**: `commit-slice` (git side-effects + conflict gates), `drift-check --resolve`,
`supersede-slice` (reason gate), `slice-candidates --obo` (per-finding gate). A Critic with an interactive triage
gate (`critique`, `critique-review`) stays inline **and spawns** its agent via the Agent tool (does NOT itself fork).
Clean `context: fork` skills: `pulse`, `reduce`, `archive`, `validate-slice`, `code-review` (fork+agent). So
`runs_in: delegable` is necessary-but-not-sufficient for `context: fork`; non-interactivity is the real gate.

Efficiency levers the v1 skills don't use: **`context: fork`** (collapse the Builder/Critic two-persona model
— critique/critique-review/code-review become forked agent-skills, no separate spawn); **dynamic injection**
`` !`cmd` `` to pre-load vault JSON into the prompt (now that the vault is JSON); **`allowed-tools`** per skill;
**`paths`** for auto-activation. (**`disable-model-invocation`** was initially set on the side-effecting skills
but was **REMOVED 2026-06-15** so `build-slice`/`commit-slice`/`setup` are **model-invocable from Remote Control** —
on remote, a `/build-slice` or `/commit-slice` typed on the phone is relayed to the model, which a `disable-model-invocation`
skill blocks. Safe to remove: each already gates side effects with its OWN interactive gate — build-slice's plan-approval
HALT, commit-slice's per-mode yes/no confirmations before any git state change, setup is idempotent — so the flag was a
redundant outer layer, not the real guardrail.) SKILL.md < 500 lines with detail pushed to bundled reference files.

**LOCKED — v2 is packaged as ONE plugin (plugin + hybrid scripts).** Plugin root holds `.claude-plugin/plugin.json`,
`agents/`, a shared `scripts/lib/` (the 15 shared tools incl. `vault_edit` [used 16×], `risk_register_audit`,
`_worktree_paths`, the `_stdout`/`_vault_*` helpers, forward-sync gates), and `skills/<name>/` each with
`SKILL.md` + `examples/` + `scripts/` (the 23 single-skill tools + `build_backlog.py`/diagnose's `assemble.py`/`passes/`).
Single-skill scripts bundle in the skill; shared code stays shared (no per-skill duplication / drift).

**LOCKED — full adopt of the efficiency levers.** Delegable skills (8) and the Critic skills run via `context: fork`;
critique-review/code-review become `context: fork, agent: <persona>` agent-skills (skill = the forked Critic);
add `allowed-tools`, dynamic injection `` !`cmd` `` of vault JSON. (The `disable-model-invocation` lever was later
**REMOVED** from `build-slice`/`commit-slice`/`setup` for Remote Control invocability — see the note above.)
NUANCE to resolve during authoring: `/critique`'s TRI-1 **user triage gate is interactive** — its adversarial review
forks, but the triage ratification stays main-agent (so critique is fork-review + main-gate, not a pure fork).

## Authoring status + deferred work

**✅ DONE:** all 26 `SKILL.md` authored (fan-out), each adversarially reviewed (96 issues found), then fixed
(fan-out + my verification). All systemic invariants verified GREEN: zero `graphify`, no pre-pipeline `/risk-spike`,
no hardcoded interpreter paths, `context: fork` on exactly {pulse, reduce, archive, validate-slice, code-review},
interactive "delegable" skills correctly kept inline, examples refs present.

**✅ SCRIPT PORTING DONE** (the executable layer): all SKILL.md-referenced scripts exist + are tested —
28/28 single-skill (`$PY "${CLAUDE_SKILL_DIR}/scripts/<x>.py"`) + 15/15 shared
(`$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<x>.py"`). Both use absolute-path invocation off `${CLAUDE_SKILL_DIR}` + an
internal sys.path bootstrap — skill commands run in the user's CWD and CANNOT use `python -m` or `${CLAUDE_PLUGIN_ROOT}`
(markdown skills don't expand it). The 3 former runtime gaps are all resolved: `$PY` resolution (SessionStart hook →
`$CLAUDE_ENV_FILE`), CRG blast-radius (pinned to `get_impact_radius` run as a subprocess — there is no CLI verb), and
PyYAML for `/diagnose` (in `requirements.txt`). The one remaining hook dependency is the DEFERRED `$PY` self-resolution
(see `memory/deferred-py-self-resolution.md`).

**✅ MANIFEST RECONCILIATION DONE** — `.build/manifest_reconcile.py` (reproducible) + `aggregate.py` v2 fixes brought
the design record in line with the corrected SKILL.md: dropped tools removed (forward-sync ×3, `vault_flip_prose_inventory`,
`critique_agent_drift_audit`), slice's v1 `slice_queue_*` → `candidates_top`+`claim_candidate`, all `-m tools.X` invocations →
the v2 path form + `.md`→`.json`, prose `tools.vault_edit`→`scripts.lib.vault_edit`, discover risk-register `raw-write`→
`vault_edit-append`, the MCFS-1 `methodology-changelog.md` read dropped. `aggregate.py` now discovers tools from the v2 tree
(`scripts/lib/` + `skills/*/scripts/`), emits `found` + a v2 `path` (was `found_in_temp` + `temp/tools/`). Verified: 0 missing
refs, 21/21 skill.json checks green. (ADR `Standard→Standard+Heavy` was already applied.)

**✅ NEW SKILL `/bug-hunt` + `/diagnose` hardening** (added later — NOT part of the original 26-skill fan-out / 96-issue
review). `/diagnose` is structural/forensic and finds no correctness/security bugs by design; `/bug-hunt` is its
whole-codebase **defect-finding** complement (vs `/code-review`'s diff scope). Three changes shipped:
1. **Finding-schema enum** gained `correctness-bug` + `security` (`skills/diagnose/scripts/schema/finding.yaml` + `_category_short`
   BUG/SEC in `assemble.py`) — previously the schema had no home for a found bug (the framing root cause of missed bugs).
2. **Shared cross-pass de-dup** `scripts/lib/finding_dedup.py` (NEW, tested): collapses findings sharing a code location
   (path + line span, *category-independent*) into one `F-MRG-*` finding (`merged_ids`/`seen_by_passes`). Wired into
   `/diagnose`'s `assemble.py` after `load_findings()`, with merged-id annotation carryover (constituents never falsely RESOLVED).
3. **`bug-hunt` skill** (`skills/bug-hunt/SKILL.md`, main-agent orchestrator): risk-rank via CRG → multi-finder fan-out
   (code-review 9-dim lens, intent-aware, NOT one-shot) → `finding_dedup` → adversarial code-review refute gate → `findings.json`
   (+ optional `--report` HTML) → declinable `/repro`→`/slice` handoff. Shares `write_pass.py` / `assemble.py` /
   `finding.yaml` with `/diagnose` in **`scripts/lib/`** (promotion DONE — no cross-skill reach; `passes/*.md` stay
   diagnose-only). Manifest in `.build/manifests/batch6.json`; `aggregate.py` count guard now 30. **Now 30 skills total** (bug-hunt + `/setup` + `/slice-story` + `/release`, hand-authored in `.build/manifests/batch8.json` / `batch9.json` / `batch10.json`).

**✅ NEW SKILL `/slice-story`** (added later — NOT part of the original fan-out): a plain-language per-slice **report
generator**. Runs just after `/critique` as the pre-build overview (also user-invokable any time), spawns the forked
**`slice-story`** narrator agent (`agents/slice-story.md`) which translates every pipeline code (`AC2`, `C1`, `R-27`,
ADRs, severities/dispositions) into plain English for a mixed tech/non-tech audience (tech-tilted) and returns
structured `story-sections.json`; the single-skill `render_story.py` renders a standalone `story.html` — saved in the
slice folder and delivered to you via **SendUserFile** (reaches your phone over Remote Control) — then HALTS and prompts `/build-slice`.
Adaptive across the lifecycle (pre-build → shipped: adds build/review/validation/learnings as artifacts appear). Wiring
is **prose-only** on `/critique` (its audited `successor` field is unaffected by PCA-1's stale canonical model — PCA-1
already carried 4 pre-existing violations, count unchanged); `/slice-story` sits OUTSIDE the audited 10-skill chain.
New artifact example key `story-sections` in `schemas/artifact-examples.json`; manifest in `.build/manifests/batch9.json`
(aggregator loop now `range(0,11)`, assert 30); `aggregate.py` `VALID_AGENTS` gained `slice-story`, the 3 tournament designers, and `product-doc`. **Requires a Claude
Code restart to register the new skill + agent** (NAW-1: the agent registry loads at session start).

## Hard rules

- **Never edit v1.** The original pipeline lives at `C:\Users\sshub\ai_sdlc` — read-only, off-limits.
- **No `temp/` anymore.** The verbatim v1 reference copy that used to live at `temp/` was removed (Session-4); v1
  lives ONLY at the read-only original `C:\Users\sshub\ai_sdlc`. (Older notes saying "read `temp/skills/<name>/SKILL.md`" mean that path.)
- **Never hand-edit inverse-link fields** (`created_by` / `edited_by` / `read_by` / `validated_by`) in any
  `skill.json`. They are **computed** from the global file-access map by `.build/aggregate.py`. To change
  them, edit the source manifest and re-run the aggregator (see *Regenerate* below).
- **The integration/master release model makes "served = release-only" STRUCTURAL (SC-020 / slice-022 — IMPLEMENTED).**
  The integration branch is **`aisdlc-uat`** (namespaced in slice-061/SC-114 so it cannot collide with a host
  project's own `uat`; legacy `uat` is still accepted as back-compat **only in an ai-sdlc-managed repo** — one
  carrying a `release-genesis` tag — so existing installs keep working). Slices branch from + merge to the
  integration branch and integrate WITHOUT a version bump (so parallel slices never conflict on the `version` line);
  `master` is released-only and is the marketplace-served default, advanced ONLY by the deliberate
  integration→`master` release cut. `/release`'s `release_cut.py` performs that cut ATOMICALLY — stage
  `merge --no-ff --no-commit <integration>` + bump `.claude-plugin/plugin.json` (semver: **patch** = fix /
  docs / refactor, **minor** = new skill / backward-compatible feature, **major** = breaking change) + regenerate
  the version-grouped CHANGELOG as **ONE commit**, then sync the integration branch back (on any pre-commit failure it
  `git reset --hard <captured-SHA>` so master is untouched). `release_advance_audit.py` enforces the invariant — every
  first-parent `master` advance since the recorded `release-genesis` tag is a versioned cut. Every branch-base /
  rebase / PR-base / merged-detection call site resolves the integration branch via `resolve_integration_branch`
  (whose single precedence point `existing_integration_branch` probes `aisdlc-uat` → genesis-gated legacy `uat` →
  None); the `--merge` WRITE path REFUSES on a full trunk-degrade (no `aisdlc-uat` AND no ai-sdlc-managed `uat`) —
  keyed on resolution SOURCE, never name-equality — so it never advances the released trunk without an integration
  branch. **There is NO per-commit version-bump mandate — the bump lives only in the release cut.**
  **Transition (one-time, POST-slice-022-ship) — ✅ EXECUTED 2026-06-19 (genesis = master@2.36.0; SC-048):** `uat` +
  the durable `release-genesis` tag were established from `master` at the FIRST release under this model —
  master@**2.36.0** (the first clean new-model baseline, NOT 2.35.1; slice-022 itself bootstrapped via the old
  merge-to-master path, so its code wasn't live until re-published at 2.36.0). The bootstrap 2.36.0 cut was driven
  directly through `bump_plugin_version.py` + `assemble_changelog.py`, NOT `release_cut.py` — the latter correctly
  no-ops when `uat` carries no un-released work, which is exactly the bootstrap state. From here on slices branch off
  the integration branch; `master` advances ONLY via `release_cut`. (The branch established in 2026 was literally
  named `uat`; slice-061/SC-114 renames it to `aisdlc-uat` — the live local+origin rename runs one-time at
  `/commit-slice` with explicit go-ahead per `docs/runbooks/aisdlc-uat-rename.md`; until then the genesis-gated
  legacy-`uat` probe keeps this repo resolving. The `release-genesis` tag and its descent invariant survive the rename.)
- **Local dev workflow (this working copy).** This repo is dogfooded by launching Claude Code with
  `--plugin-dir C:\Users\sshub\aisdlc-v2`, so the LIVE plugin is this working tree, not the marketplace cache (run
  `/reload-plugins` after editing skills/agents). **This working copy stays checked out to the integration branch**
  (`aisdlc-uat` after the slice-061 rename lands; still `uat` until then); all slice work happens here. `master` is
  merged from the integration branch on a **weekly** release cadence via the integration→`master` release cut
  (`/release` → `release_cut.py`), never by direct commits to `master`. **This
  `CLAUDE.md` is now git-tracked** (dropped from `.gitignore` 2026-06-19) for version history — it remains local dev
  scaffolding (plugin installers do not load it as instructions); the enforcement that ships is the code/audit above
  per SC-019/SC-020.

## Layout

```
aisdlc-v2/  (the v2 PLUGIN)
  .claude-plugin/plugin.json   ← plugin manifest (name: ai-sdlc; version cut POST-MERGE by /release — see Hard rules; not a per-commit bump)
  CLAUDE.md                    ← you are here
  skill-graph.json             ← the global dependency graph
  requirements.txt             ← runtime deps (PyYAML, for /diagnose + build_backlog's yaml fallback)
  hooks/                       ← hooks.json (SessionStart) + setup-env.sh — resolve + persist $PY (see Runtime below)
  schemas/                     ← artifact-examples.json (schemas-by-example) + _conventions.md
  agents/                      ← Critic/worker personas (system prompts) — e.g. code-review.md   [authoring phase]
  scripts/lib/                 ← SHARED tooling used by >1 skill (vault_edit etc.); see scripts/lib/README.md
  skills/<name>/
    skill.json                 ← GENERATED design manifest (by .build/aggregate.py)
    examples/<artifact>.json   ← GENERATED output examples (bundled per the skills convention)
    SKILL.md                   ← HAND-AUTHORED runnable skill   [✅ all 26 authored + adversarially reviewed + fixed]
    scripts/                   ← single-skill tools (e.g. skills/slice/scripts/)
  .build/                      ← reproducible build pipeline (manifests + aggregate.py → skill.json + examples/ + graph)
```
**`skill.json` + `examples/` are GENERATED** (re-running `.build/aggregate.py` overwrites them); **`SKILL.md` +
`scripts/` are HAND-AUTHORED** (aggregate.py never touches them). skill.json = design manifest; SKILL.md = runnable skill.

> **4.10 (v2.21.0):** `skill.json` (×30) + `skill-graph.json` are now **git-ignored — kept LOCAL only**, not shipped
> (zero runtime consumers; the harness loads `SKILL.md`). `examples/` stay tracked + shipped (SKILL.md references
> them). The runnable contract is the `SKILL.md` set; the design record is local authoring history.

## Runtime — how `$PY` resolves (and deps)

Every SKILL.md invokes bundled scripts as `$PY "${CLAUDE_SKILL_DIR}/.../X.py"`. `$PY` is **set once per session by the
`SessionStart` hook** (`hooks/hooks.json` → `hooks/setup-env.sh`): the hook resolves a Python 3 interpreter and appends
`export PY=…` to `$CLAUDE_ENV_FILE`, which Claude Code sources before every Bash tool call — so `$PY` is live in all skill
bash blocks + `` !`…` `` injections with **zero per-skill setup**. (Why a hook and not skill markdown: shell vars don't
persist across skill bash blocks, and `${CLAUDE_PLUGIN_ROOT}` only expands in hook/MCP JSON, not skill markdown — but
`$CLAUDE_ENV_FILE` does persist. Confirmed via claude-code-guide.)

- **Resolution order:** `$AI_SDLC_PY` override → `python3` → `python` → `py`; prefers an interpreter that already has PyYAML.
- **Dev override:** `export AI_SDLC_PY="C:/Users/you/.claude/.venv/Scripts/python.exe"` (forward slashes) before launching
  Claude Code to pin `$PY` to a venv with the deps.
- **Deps:** only `/diagnose` + `build_backlog`'s yaml fallback need **PyYAML** (`requirements.txt`); everything else is
  stdlib. The hook warns on stderr if the chosen `$PY` lacks it.
- **`$CRG` (code-review-graph CLI):** the SAME hook resolves + exports `$CRG` to an ABSOLUTE path to the
  `code-review-graph` entry point in `$PY`'s scripts dir (`$AI_SDLC_CRG` override → `<scripts>/code-review-graph[.exe]`
  → bare `code-review-graph` on PATH). Skill bash blocks invoke CRG as `"${CRG:-code-review-graph}"`, **never** bare
  `code-review-graph` — the hook installs CRG into `$PY`'s env, whose scripts dir is OFF `PATH` for a venv / pinned-`$PY`
  install (the documented dev setup) on Windows, so a bare probe false-negatives as `CRG_MISSING` even though CRG is there.
  The `${CRG:-…}` fallback keeps skills working in the same session the hook hasn't re-fired yet.
- **Caveat (residual):** the hook can't be fired here (needs a live plugin load); the resolver logic + `$CLAUDE_ENV_FILE`
  write are verified. Confirm the hook fires on a real install (Windows uses git-bash; the plugin's skills are POSIX bash).

## What the files are

### `skills/<name>/skill.json` — one per skill (30 total)

Each describes a single pipeline skill. Locked schema (keep identical across all 30 for diffability):

| field | meaning |
|---|---|
| `name`, `user_invokable` | identity |
| `requires_full_context` / `runs_in` | **the two-category classification.** `true` / `main-agent` = interactive, needs the live conversation. `false` / `delegable-to-subagent` = a mechanical/analytical pass that could run as a fresh-context subagent. |
| `context_rationale`, `summary` | why that classification; what the skill does + produces |
| `reads[]` | files the skill **consumes** — `{path, kind, produced_by, purpose}` (forward edge) |
| `outputs[]` | files/dirs/side-effects the skill **writes** — `{path, kind, v1_format, v2_format, write_semantics, write_mechanism, modes, created_by, edited_by, read_by, validated_by, purpose}` |
| `agents[]` | named subagents the skill spawns (one of: critique, critic-calibrate, critique-review, diagnose-narrator, field-recon, code-review) |
| `tools[]` | methodology tools (`$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<x>.py"` shared, or `.../scripts/<x>.py` single-skill — must exist in `scripts/lib/` or `skills/<name>/scripts/`) + external (`code-review-graph`). Each carries `found` + a v2 `path`. |
| `harness_tools[]` | ambient Claude Code built-ins (Read/Write/Bash/AskUserQuestion/…) |
| `hands_off_to[]` | the next skill(s) this one points the user to |
| `source` | the v1 `temp/skills/<name>/SKILL.md` it was derived from |
| `missing_references[]` | any tool/agent referenced but not found in the v2 tree (`scripts/lib/` or `skills/<name>/scripts/`). `code-review-graph` is `expected:true` (external pip package, never bundled). A methodology tool here with `expected:false` is a real gap to investigate. |

### `skill-graph.json` — the whole pipeline as one graph

`nodes` grouped into `skills` / `agents` / `tools` / `files`; `edges` is a flat typed list:
`reads`, `creates`, `updates`, `appends`, `uses_tool`, `uses_harness`, `spawns`, `hands_off_to`.
Each file node carries its `v2_format` plus the global `created_by` / `edited_by` / `read_by` / `validated_by`.
Start here to answer "what touches X?" or "what does skill Y produce?". `stats` holds the headline counts.

## Conventions that the data encodes

- **md → json conversion.** Every vault content file becomes JSON in v2 **except**: `./CLAUDE.md` stays
  **markdown** (Claude Code reads it natively each session) and `diagnosis.html` stays **html** (load-bearing
  annotation deliverable). Directories stay directories; code/config (`tests/bugs/*.py`, `VERSION`, allowlists)
  stay as-is. A file node's `v2_format` field tells you which it is (`json` = conversion target).
- **`update` = read-modify-write.** A skill that `update`s a file reads it first, so an `update` access counts
  toward **both** `read_by` and `edited_by`. (This is why reflect/discover/risk-spike appear in
  `risk-register.md`'s `read_by`.)
- **SVW-1 write safety.** Shared, append-mutated files (notably `risk-register.md`, `lessons-learned.md`,
  `shippability.md`, `drift-log.md`, `_index.md`) split into two write mechanisms: openers do single-shot
  `raw-write`; mid-slice appenders MUST route through `tools.vault_edit append` (`write_mechanism:
  vault_edit-append`). **This append-safe path must survive the JSON conversion** — a naive whole-file
  read/mutate/write would reintroduce the race that `vault_edit` exists to prevent.
- **ADRs are append-only** — supersede with a new ADR, never edit one in place.
- **`code-review-graph` (CRG) is external** — a separate pip package (`pip install code-review-graph`), MCP-native,
  never bundled in the plugin; its absence from the script tree is expected, not a gap. (Replaced graphify in rollout #2.)

## The pipeline at a glance (v2)

`triage`/`adopt` → `discover` → (`user-test`) → **per-slice loop:** `slice` (pick candidate) →
`risk-spike` (**feasibility spike** — step-0; prove the candidate's blocking assumptions or block) →
`design-slice` (**design tournament — all 3 BLIND designers [practice/cross-domain/expert] on EVERY slice** → reality-grounded synthesis) →
`risk-spike --mode design` (**design spike** — post-synthesis; reality adjudicates the tournament's empirically-decidable disagreements + must-verify invariants; conditional) →
`critique` (+`critique-review`) → `slice-story` (plain-language pre-build report; delivered to you via SendUserFile) → `build-slice` → `code-review` → `validate-slice` →
`reflect` → next `slice`. `commit-slice` finalizes. Maintenance: `drift-check`, `reduce`, `archive`, `sync`,
`supersede-slice`, `critic-calibrate`, `release` (grounded README/CHANGELOG/API-ref/user-guide; out-of-loop).
Orientation: `pulse`, `query-design`. Brownfield analysis: `diagnose`, `bug-hunt`,
`slice-candidates`. **Heavy-mode-only:** `heavy-architect`, `sync`.
Single candidate backlog: `<vault>/candidates.json` (live) + `<vault>/archive/candidates.json` (shipped).
Risk ledger: `risk-register.md`.

## Regenerate / extend

The 30 `skill.json` and `skill-graph.json` are **generated**, not hand-maintained:

```
PY=C:\Users\sshub\.claude\.venv\Scripts\python.exe
$PY .build/aggregate.py
```

To fix or add a skill: edit its `.build/manifests/batch*.json` entry (the source of truth for forward edges),
then re-run `aggregate.py`. It re-derives all inverse links and re-emits everything, so consistency is
guaranteed. `batch0` is hand-authored (triage, discover); `batch1-7` came from sub-agent extractions of the
v1 `SKILL.md` files. The aggregator `html.unescape`s the manifests, canonicalizes paths into stable graph
node ids (e.g. all `slice-NNN-<name>/` → `slice-NNN/`, all ADR references → one `decisions/ADR-*.md` node),
then computes the global map.

## AISDLC Pipeline discipline
- User is not reading your conversation output, so, avoid conversational and narrative output as much as possible.
- If user specifically asks any question then only proceed as conversation. In all other cases, you are just wasting tokens.
- Reply in the most concise form possible. Skip pleasantries, preambles, and recaps.
- If user is required to invoke next pipeline skill, give precise, short and clear next steps and always include slice id.
- Do not narrate your steps.
- AISDLC Pipeline discipline rules does not apply to the content you write in any file.
