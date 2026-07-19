# ai-sdlc API Reference

Interface reference for the `ai-sdlc` Claude Code plugin, **v2.39.2**. This is a **reference**, not a tutorial —
for task-oriented walkthroughs see the [User Guide](user-guide.md).

Every command, flag, script, environment variable, and config file below is grounded in a real file in this
repo (a `SKILL.md`, a bundled script, or a shared library module) — nothing here is inferred or aspirational.

---

## 1. Slash commands

All 30 skills are namespaced `/ai-sdlc:<name>` (shorthand `/<name>` once the plugin is active in a
conversation). Each is defined in `skills/<name>/SKILL.md`.

### 1.1 Openers (project entry points)

| Command | Arguments | Purpose | Auto-advance |
|---|---|---|---|
| `/triage` | `[MINIMAL\|STANDARD\|HEAVY] <one-sentence description>` | Greenfield opener. Picks the pipeline mode via one-question-at-a-time Q&A (or accepts a pre-declared mode), builds the initial risk register, scaffolds the vault skeleton, writes `./CLAUDE.md`. Re-runnable for re-scoping. | no — hands off to `/discover` / `/user-test` / `/heavy-architect` / `/slice` |
| `/adopt` | `[MINIMAL\|STANDARD\|HEAVY]` | Brownfield opener — replaces `/triage`. Scans the codebase with `code-review-graph`, offers `/diagnose`, runs a structured interview, reverse-engineers `concept.json` and the risk register from code (docs are hypothesis, never copied verbatim). | no — hands off to `/discover` (optional) / `/slice-candidates --product` / `/slice` |
| `/ai-sdlc:setup` | `[--no-mcp] [--no-graph] [repo-path]` | One-shot dependency doctor. Installs PyYAML + `code-review-graph`, registers the CRG MCP server, scaffolds `.aisdlc/reality-gates.json` (id-merging the `bandit`/`pip-audit` security gates on a Python frame + vendoring the guard to `.aisdlc/gates/py_security_gate.py`), builds the code graph. Idempotent. `--no-mcp` skips MCP registration, `--no-graph` skips the initial graph build. | n/a — out-of-loop maintenance |

### 1.2 Discovery

| Command | Arguments | Purpose | Auto-advance |
|---|---|---|---|
| `/discover` | none | One-topic-at-a-time discovery: WHAT (concept/scope/non-goals), WHO (actors + top actions), CONSTRAINTS (stack/infra/team, reversibility-tagged), HIGH-risk mapping. Names the first slice candidate. Writes `concept.json`, updates `risk-register.json`, appends the first `candidates.json` entry. | no — hands off to `/slice-candidates --product`, `/user-test`, or `/slice` |
| `/user-test` | `mockup \| prototype \| slice` | Real-user validation gate. Prepares the artifact, generates observation questions, captures findings in `user-tests/<name>.json`, appends new risks to `risk-register.json`. | no — branch determined by findings |
| `/heavy-architect` | none | Heavy-mode-only comprehensive upfront architecture vault: `threat-model.json`, `cost-estimation.json`, `requirements.json`, `non-functional.json`, `diagrams.json`, `actors/`. Aborts if `triage.json` shows Standard/Minimal. | no |

### 1.3 The per-slice loop

Run in this order; each auto-advances to the next except where noted.

| Command | Arguments | Purpose | Auto-advance |
|---|---|---|---|
| `/slice` | `["<description or hint>"] [--area <NAME>\|--component <NAME>]` | Reads the ranked `candidates.json` backlog (read-only pick path), presents a ranked recommendation, claims the pick (worktree + branch + `pick_log`), writes `mission-brief.json` + `milestone.json`. **`--area <NAME>`** (alias `--component`, slice-080/084) scopes the ranked recommendation to ONE product area (`unassigned` for un-grouped product capabilities) — a read-only LENS on `candidates_top.py`'s digest: it takes no lock, mints no id, writes no status, and blocked/in-flight candidates stay visible globally. `/slice` also surfaces `product_rollup.py`'s per-area capability-progress rollup and biases the recommendation toward a product-sourced capability whenever it carries a non-empty `governor` (the product's scope is decomposed but 0 capabilities are built). Requires a failing `/repro` test first for bug-fix candidates (BFRD-1). | yes → `/risk-spike` |
| `/risk-spike` | `[--mode feasibility\|design] [slice-NNN \| SC-NNN \| R-NN \| all]` | **Two modes.** `feasibility` (default, step-0, auto-triggered by `/slice`): proves the candidate's blocking `assumptions[]` with a `field-recon` subagent on the real environment; any NO-GO blocks the candidate until a fallback is re-spiked. `design` (`--mode design`, auto-triggered by `/design-slice`): reality-adjudicates `design.json`'s `decidable_disagreements` + must-verify cross-domain invariants. | yes (feasibility → `/design-slice`; design, all GO → `/critique`, any NO-GO → `/design-slice`) |
| `/design-slice` | `[slice-id]` | Runs on **every** slice regardless of tier: spawns all 3 blind designer subagents (practice / cross-domain / expert), reality-grounds one synthesis (CRG blast-radius, spike evidence, reversibility, simplest-that-works), tags every new ADR with reversibility. Writes `design.json`. | yes → `/risk-spike --mode design` (conditional) or `/critique` |
| `/critique` | `[slice-id] [--force]` | Tier-driven adversarial design review. **Runs** when `risk_tier ∈ {medium, high}` OR `critic_required == true` (auth/authz, API contracts, data-model, security paths, methodology surface `skills/**`/`agents/**`/`scripts/**`; Heavy forces it on every slice); **skipped** on `low` tier with no mandatory trigger. Spawns a forked `critique` subagent (9 fixed attack dimensions), writes `critique.json`, then the **interactive TRI-1 user triage gate** (accept / defer / reject with rationale, on the main thread). `--force` runs regardless of tier/gate-skip. | yes (CLEAN/NEEDS-FIXES only; BLOCKED halts) |
| `/critique-review` | `[slice-id]` | The meta-Critic (DR-1). **Mandatory** when ANY holds: `risk_tier == high`; `critic_required == true`; first-Critic `findings` count ≥ 5; **full tournament convergence** — no designer pair in `design.json`'s `approach_divergence` classified `disjoint` (slice-066 / ADR-064, computed by `scripts/lib/tournament_convergence.py`, never eyeballed). Advisory on 3+ consecutive clean verdicts. Spawns a second adversarial subagent that classifies each first-Critic finding `valid`/`suspicious`/`severity-wrong` and surfaces `missed[]`; feeds back into the same TRI-1 gate. Runs IN-LOOP, before triage. | yes → `/critique` Step 4.5 (TRI-1) |
| `/slice-story` | `[slice-id]` | Auto-invoked after `/critique` **only when it produced ≥1 finding**. A forked `slice-story` narrator subagent turns every artifact into plain English (zero jargon); `render_story.py` renders `story.html`, delivered via `SendUserFile` (phone included over Remote Control). Also user-invokable any time, at any lifecycle stage. Also embeds `story_inputs.py project`'s **product-shape projection** (a jargon-stripped view of `product_rollup.compute_rollup`) so the report can show where this slice sits in the product's capability progress. | false — halts and prompts `/build-slice` |
| `/build-slice` | `[slice-id]` | Plan-mode execution. Explores code with CRG queries + targeted Reads, drafts a task sequence, **halts for explicit user approval** before writing code. Task-by-task verification, a mandatory mid-slice smoke gate, and a consolidated pre-finish gate (`pre_finish_gate.py`: all ACs, must-not-defer, `/drift-check --fast`, **STUB-DEAD-1** (`stub_dead_audit.py` — a deterministic, diff-scoped stub/dead-code scan; see §2), and the project's declared reality gates via `reality_gate_runner.py`, fail-closed). Writes `build-log.json`. | yes → `/code-review` |
| `/code-review` | `[slice-id]` | Forked `code-review` agent context. Reviews the just-built diff along the same 9 fixed dimensions as the design Critic, producing `path:line` findings as `code-review.json`. Blocker findings must be dispositioned before `/validate-slice`. | yes → `/validate-slice` |
| `/validate-slice` | `[slice-id]` | Forked context. Per-criterion PASS/FAIL/PARTIAL checks on the **real environment**. Runs VAL-1/WS-1/ETC-1 layered audits, the shippability-catalog regression check, and the project's declared reality gates (`reality_gate_runner.py --repo-root <worktree> --json`, fail-closed — an absent/empty manifest is a structural no-op). | conditional — aggregate `Result: PASS` only |
| `/reflect` | `[slice-id]` | Captures validated / corrected / discovered / deferred learnings. Writes `reflection.json`, appends `lessons-learned.json` + `shippability.json`, tracks Critic calibration, auto-archives the slice. | **false — the auto-advance terminus** |
| `/commit-slice` | `[--merge \| --push \| --sync-after-pr]` | User-invoked only (never auto-advanced into). Generates an audit-grade conventional commit message from vault artifacts. `--merge`: solo-dev local merge + safe branch delete, re-running the shippability catalog against the post-rebase integration-branch tip first (refuses on red). `--push`: push + rebase onto the integration branch + gh-aware PR + non-blocking auto-merge (degrades gracefully). `--sync-after-pr`: post-PR cleanup. No flag: generate + show only. Emits `.aisdlc/receipts/<slice-NNN>.json` if the CI merge-gate workflow is installed. | false |

### 1.4 Orientation

| Command | Arguments | Purpose |
|---|---|---|
| `/pulse` | `[--brief \| --full]` | Read-only vault scan: active slice stage + next action, risk exposure, regression health, Critic calibration, per-gate hit-rate (measurement spine), top lessons, candidate backlog snapshot (including a per-area **"Product shape"** capability-progress rollup, a completeness-governor WARN, and out-of-scope/orphaned candidates), and recommended next action. Default ~60 lines, `--brief` ~20, `--full` ~150. |
| `/query-design` | `["<question about the codebase>"]` | Read-only, grounded Q&A — every answer cites file/line/symbol evidence via Grep/Read/CRG. Writes nothing. May offer (never force) a handoff to `/slice`. |

### 1.5 Maintenance

| Command | Arguments | Purpose |
|---|---|---|
| `/drift-check` | `[--fast] [--resolve] [--status] [path]` | Audits vault claims vs. code reality: `DRIFT` (blocker), `UNSPECIFIED CODE` / `STALE CLAIM` / `STALE DOC` (major — the last via the `/release`-written `doc-manifest.json` provenance anchor). `--fast` scopes to changed files (<2s, used by `/build-slice`'s pre-finish gate); `--resolve` walks findings interactively; `--status` folds `drift-log.json` into current state, read-only. Full mode (never `--fast`) also runs the **ACL-1** area↔code-link sweep (`area_code_link_audit.py`, slice-084): a product-scope item's optional `code_components[]` link that no longer resolves against the Heavy `components/*.json` inventory surfaces as a **STALE CLAIM**; it degrades cleanly to a no-op when there is no product scope, no declared links, or no code-component inventory. |
| `/reduce` | `[--force]` | Complexity-budget enforcer: over-engineering, dead claims, speculative generality, god-nodes, cross-slice incoherence. Produces a ranked reduction report; proposes a simplification slice if confirmed. `--force` bypasses the confirmation gate, not the slice gate. |
| `/archive` | `[--index-only]` | Archives completed slices (`reflection.json` present) from `slices/` to `slices/archive/`, regenerates both `_index.json` files. `--index-only` rebuilds indexes without moving files. |
| `/supersede-slice` | `<archived-slice-id>` | Establishes a bidirectional supersession link between an archived slice and the active slice correcting it. Appends to the archived `reflection.json`, sets `Supersedes` in the active `mission-brief.json`. |
| `/critic-calibrate` | `[--window N]` (default 15) | Mines "Missed by Critic" entries from the last N archived reflections, proposes 0–3 evidence-backed Critic check changes (plus optional LIGHTEN/GATE-SKIP proposals). Persisted to a project overlay (`critic-calibration-log.json`) — never edits the shipped `agents/critique.md`. Step 5 (slice-087) runs a default-deny **declassifier** (`calibration_export.py`; see §2) that turns the overlay into one redacted, maintainer-ready upstream digest: `calibration_notes`/`gate_skips` are auto-projected through a closed field/value allowlist (free text withheld), any `active_checks` text you choose to forward crosses only via an explicit human genericize-and-consent gate, and a fail-closed credential scrub runs last — so you can mail a safe digest upstream, never the raw log. |
| `/release` | `[--docs readme,changelog,api,guide]` (default: all four) · `[--new-version X.Y.Z \| --level patch\|minor\|major]` (release cut) | Cuts a release (`aisdlc-uat` → `master`, atomic version bump + CHANGELOG regen via `release_cut.py`) **and** generates/refreshes grounded product docs. The release cut runs a **CI pre-flight** (`ci_gate.py`): the integration branch's HEAD must be green on CI, or the cut is refused (blocking a locally-merged-but-never-CI-tested slice from advancing `master`); degrades to a warning when there is no GitHub CI. CHANGELOG is assembled deterministically from per-slice `changelog.json` records — never drafted by the agent. README/API-reference/user-guide are drafted by a forked `product-doc` agent from the CRG public surface + vault, gated before overwriting a hand-written doc, and anchored by a written `doc-manifest.json` that `/drift-check` later audits. |
| `/sync` | `[--dry-run] [--regen-only] [--check-only] [path]` | **Heavy mode only.** Regenerates code-derived vault files (components/contracts/schemas from AST/OpenAPI/type defs), detects human-authored-file drift, runs the CSP-1 cross-spec parity audit, appends a dated `sync-log.json` record. |

### 1.6 Bug fixes

| Command | Arguments | Purpose |
|---|---|---|
| `/repro` | `<issue description>` | Establishes a FAILING test before any fix code. Parses the issue (≤3 clarifying questions), queries CRG for context, writes a runnable test under `tests/bugs/`, confirms it fails, appends a `shippability.json` row so the bug can't silently return. |

### 1.7 Brownfield forensics

| Command | Arguments | Purpose |
|---|---|---|
| `/diagnose` | `[path-to-repo] [--parallel]` | Owner-facing forensic analysis: builds a CRG graph, runs 11 structured passes (10 subagents + a cross-reference pass) + a narrator, assembles a self-contained `diagnose-out/diagnosis.html`. Owner annotates Confirmed/No in-browser; the saved file feeds `/slice-candidates`. Never modifies source. `--parallel` opts into the legacy single-message parallel dispatch (default: sequential). |
| `/slice-candidates` | `[path-to-diagnose-out] [--obo] [--product] [--demote SC-NNN --reason TEXT]` | **Finding path** (default): reads the annotated `diagnosis.html`, keeps `Confirmed: yes` rows, detects file-overlap coupling via CRG blast-radius, topo-sorts into `candidates.json`. `--obo` walks findings one at a time interactively. **`--product` path** (peer of `--obo`, slice-068 / ADR-067): reads `<vault>/concept.json` instead — decomposes the product's own declared scope into candidate-shaped items **once** (ids minted in-lock by `scripts/lib/product_scope.py`, never by the model), persists to `product-scope.json`, materializes as `product-scope`-sourced candidates. Idempotent re-run via `product_scope.py materialize`; extend/correct via `product_scope.py revise --items-file PATH [--cut PS-NNN --reason TEXT]` — a WHOLE-LIST replace that REFUSES a payload omitting a live item (an omission was a silent delete before slice-073 / ADR-078); removal is the explicit `--cut` (repeatable, `--reason` required), recorded in `product-scope.json`'s append-only `revisions[]`. Group a materialized capability into a named **product area** via `product_scope.py set-area --item PS-NNN --area NAME` (slice-081/084 — the day-0 structuring step `/slice --area` and `/pulse`'s per-area rollup read at query time; `set-component`/`--component` are back-compat aliases of the pre-slice-084 term). **`--demote SC-NNN --reason TEXT`** (slice-077 / ADR-088): lowers a genuinely low-value **off-path** candidate's backlog rank by a bounded score-space penalty via `demote_candidate.py`, WITHOUT deleting its append-only risk-register entry; refuses on a product-scope-sourced (on-path) or critical-band candidate. `vault_edit remove`/`set --path items`/`append` on `product-scope.json`/`items` all refuse (ADR-080). |
| `/bug-hunt` | `[path-to-repo] [--report] [--top N] [--since <ref>] [--parallel]` | Whole-codebase correctness + security defect sweep (fills the gap between `/diagnose`'s structural sweep and `/code-review`'s diff scope). Risk-ranks via CRG, fans out intent-aware finder subagents (the code-review 9-dimension lens), de-duplicates (`finding_dedup.py`), adversarially refutes candidates. `--report` renders an owner-facing HTML; `--top N` caps the risk-ranked list; `--since <ref>` scopes to changes since a git ref. **v0.1 — build-wired, not yet battle-tested**; treat findings as leads, not verdicts. |

---

## 2. CLI scripts (bundled tooling)

All are invoked as `$PY <path>` — never `python -m`, since these are consumed by absolute path from
`SKILL.md` bash blocks running in the user's cwd.

### `scripts/lib/vault_admin.py` — vault lifecycle admin

Subcommands (`argparse` subparsers, `prog="vault_admin"`):

| Subcommand | Flags | Purpose |
|---|---|---|
| `list` | none | List every vault under the resolved base + orphan status. |
| `write-pin` | `--vault <path>` (default: computed for this repo) | Write the tier-2 `<git-common-dir>/aisdlc/vault-root` pin + a `.source-repo` back-ref. |
| `git-init` | `--root <dir>` (default: `.`) | Consented `git init` + fail-closed canonical-root re-verify (slice-058). |
| `export` | `--vault <path>`, `--out <archive>` (default `./<vault-name>-vault.tgz`) | Tar-gzip the vault to one portable archive. |
| `import` | `<archive>`, `--vault <path>`, `--force` | Restore an exported archive; refuses a non-empty target without `--force`. |
| `uninstall` | `<name>`, `--yes` | Delete a vault dir under the base (confirmation required). |

### `scripts/lib/_vault_paths.py` — vault-root resolver (discoverability CLI)

Run as `python -m scripts.lib._vault_paths` (or by absolute path). No args → human-readable `vault-root: … / source: …`. `--path` → just the resolved absolute path (for `$(...)` capture). Exports the module-level `VAULT_ROOT` constant that every shared tool imports; resolved lazily, once per process (PEP 562 `__getattr__`).

### `scripts/lib/reality_gate_runner.py` — pluggable reality-gate runner

`prog="reality_gate_runner"`. Runs a project's *declared* deterministic gates (`<repo-root>/.aisdlc/reality-gates.json`), fail-closed.

| Flag | Required | Purpose |
|---|---|---|
| `--repo-root <dir>` | **yes** | The checkout the declared gates run against — never ambient cwd. |
| `--manifest <path>` | no | Override manifest path (default: `<repo-root>/.aisdlc/reality-gates.json`). |
| `--surface {security,nfr,ops}` | no | Run only one declared surface. |
| `--timeout <seconds>` | no | Per-gate timeout (default: none — a hung gate blocks, fail-safe). |
| `--json` | no | Accepted no-op — the runner always emits JSON. |

Exit codes: `0` = PASS (incl. an absent/empty manifest, a structural no-op) · `1` = FAIL (≥1 declared gate tripped) · `3` = REFUSE (malformed/unreadable manifest, fail-closed) · `2` = usage error.

### `scripts/lib/scaffold_reality_gates.py` — reality-gates manifest scaffolder

`scaffold_reality_gates.py <repo> [--json]`. Ensures `<repo>/.aisdlc/reality-gates.json` exists (empty skeleton on a fresh repo); on a Python frame (source files and/or a `requirements*.txt` present), id-keyed **merges** the `bandit`/`pip-audit` security gates into `gates.security[]` (never clobbers a user-customized entry) and vendors the guard to `<repo>/.aisdlc/gates/py_security_gate.py`, re-vendoring only when the plugin's guard content changes. Invoked by `/ai-sdlc:setup`'s default (non-`--check`) path.

### `scripts/lib/security_gate.py` — deterministic security reality-gate guard

Vendored verbatim (standalone, stdlib-only) to `<repo>/.aisdlc/gates/py_security_gate.py`; the manifest command it ships as is `python .aisdlc/gates/py_security_gate.py --tool {bandit|pip-audit}`.

| Flag | Purpose |
|---|---|
| `--tool {bandit,pip-audit,pip_audit}` | **required** — which deterministic tool to run as the gate. |
| `target` (positional) | Directory to scan/audit (default: cwd). |
| `--requirements <path>` | `pip-audit` only — explicit requirements file (default: auto-detect `requirements*.txt`). |
| `--timeout <seconds>` | Bounded subprocess timeout (default: 300s bandit / 90s pip-audit). |
| `--print-version` | Print `GUARD_VERSION` and exit 0 (used by the re-vendor integrity check). |

Exit `0` = clean PASS · `1` = any FAIL bucket (`FINDING`, `ZERO-SCAN`, `INCOMPLETE`, `INFRA`, `TOOL-MISSING`) — fail-closed, so a scan of *nothing* (0 loc / 0 deps) FAILs rather than false-greening. `bandit`/`pip-audit` themselves are **not installed** by this guard or by `/ai-sdlc:setup` — a missing tool reports `TOOL-MISSING` and fails the gate, visibly.

### `scripts/lib/ship_receipt.py` — CI merge-gate receipt

`prog="ship_receipt"`, two subcommands:

| Subcommand | Flags | Purpose |
|---|---|---|
| `emit` | `--slice <id>` (required), `--vault <path>`, `--repo-root <dir>` (default `.`) | Write `.aisdlc/receipts/<slice-NNN>.json` from the vault's validation/shippability/gate-log evidence. |
| `verify` | `--branch <name>` or `--slice <id>`, `--repo-root <dir>` | Check the receipt for a slice branch (used by the `aisdlc-merge-gate.yml` CI workflow, `skills/commit-slice/assets/aisdlc-merge-gate.yml`). |

### `scripts/lib/secret_scrub.py` — credential redaction (VAL-1)

`prog="secret_scrub"`. `--in <file>` (default stdin) → `--out <file>` (default stdout), redacting matched credential patterns as `[REDACTED:<type>]`. `--check` — gate mode: don't redact, exit `1` if any secret is found, `0` otherwise. Used before any captured `/validate-slice` / `/risk-spike` evidence is written to the vault, and as the last credential-scrub pass in `calibration_export.py`'s upstream digest.

### `scripts/lib/release_advance_audit.py` — release-model invariant audit

`--root <dir>` (default `.`), `--genesis <tag>` (default `release-genesis`), `--json`. Walks `git rev-list --first-parent <genesis>..<master>` and asserts every advance of `master` changed `.claude-plugin/plugin.json`'s `version` — i.e. `master` only ever advances via a versioned `/release` cut, never a direct commit. No-ops on a non-methodology repo (no `.claude-plugin/plugin.json`). Exit `0` clean / `1` violation / `2` usage (git unusable / genesis absent).

### `scripts/lib/product_scope.py` — product-scope decomposition, materialization & area grouping

`prog="product_scope"`. Materializes the product's own declared scope (from `concept.json`) into `<vault>/candidates.json` (slice-068), and groups materialized capabilities into named **product areas** (slice-081/084). `--vault ROOT` / `--json` are accepted either before or after the verb.

| Subcommand | Flags | Purpose |
|---|---|---|
| `decompose-context` | none | Emit `concept.json` for the model to decompose (exit 3 if `concept.json` is absent). |
| `persist` | `--items-file PATH` (required) | THE ONCE-ACT: cross the model's decomposition into the vault, mint `PS-NNN` ids in-lock (the receiver, never the model, owns identity), and materialize candidates. Create-only — refuses if `product-scope.json` already exists (use `revise`). |
| `materialize` | `--dry-run`, `--scope-file PATH` (implies `--dry-run`), `--acknowledge PS-NNN` (repeatable) | Idempotently mint candidates from the persisted scope — deterministic, create-only, keyed on candidate provenance (`source: [{type: "product-scope", ref: "PS-NNN"}]`). |
| `revise` | `--items-file PATH` (required), `--cut PS-NNN` (repeatable, requires `--reason`), `--reason TEXT` | Explicit, user-gated scope correction: a WHOLE-LIST replace that preserves already-minted ids by id and **REFUSES** a payload silently omitting a live item (an omission used to be a silent delete before slice-073 / ADR-078); an item is removed only via the explicit `--cut`, recorded in `product-scope.json`'s append-only `revisions[]`. |
| `census` | none | Classify every candidate (live ∪ archive) as `PRODUCT` / `HUMAN` / `EXHAUST` / `UNCLASSIFIED`, with an `unclassified` tripwire list for any source-type value the taxonomy doesn't yet recognize. |
| `done` | `--item PS-NNN` (optional; default: every item) | 4-valued, read-only: is a capability finished (`done` \| `in-progress` \| `no-children` \| `unknown`), computed fresh from its candidates' archive state every call. |
| `set-area` | `--item PS-NNN` (required), `--area NAME` (required) | Assign ONE product-area to ONE already-materialized capability — a focused, atomic in-place annotation (mints nothing, re-materializes nothing, appends no `revisions[]` entry). `--area` is rejected if empty/whitespace or the reserved `unassigned` sentinel (case-insensitive). A same-value re-annotation is an idempotent no-op. `set-component --component NAME` is a back-compat alias (slice-084 renamed the product-area axis `component` → `area`, to disambiguate it from the code-component inventory below). |

Exit codes: `0` ran (a 0-mint states its reason; `set-area`'s no-op reports `changed:false`) · `1` runtime error · `2` usage error (incl. a model-supplied id, `--scope-file` without `--dry-run`, an unknown `done`/`set-area --item` id, an empty/reserved `set-area --area`) · `3` `concept.json` absent (`decompose-context`, `persist`) · `4` `product-scope.json` absent (`materialize`, `revise`, `done`, `set-area`) or already-exists (`persist`).

### `scripts/lib/product_rollup.py` — per-area capability-progress rollup

`--vault ROOT` (default: resolved `VAULT_ROOT`), `--json` (default: human-readable text). A read-only DERIVED VIEW (writes nothing, slice-080 / ADR-091) — reuses `product_scope.py`'s `done` classification, joins each capability to its `product-scope` `area`, and reports done/pending counts in **capabilities** (never slices), whole-app + per-area, with a mandatory `unassigned` catch-all stratum so no capability is ever dropped. Areas are ordered least-complete-first. Emits a `governor` string only when the scope is decomposed (≥1 capability) but 0 are built — the completeness nudge `/pulse` and `/slice` render as a WARN. Exit `0` always — any compute error rides an `error` field on stdout rather than a non-zero exit. Consumed by `/pulse` (the "Product shape" line), `/slice` (the `--area` lens, via `candidates_top.py`), and `/slice-story` (via `story_inputs.py project`).

### `scripts/lib/product_priority.py` — product-priority path-class taxonomy (shared library, no CLI)

Not a standalone script — imported by `skills/slice/scripts/candidates_top.py` and `skills/slice-candidates/scripts/demote_candidate.py` (slice-077 / ADR-088). `path_class(candidate)` derives a 3-valued class — `off-path` (explicitly demoted; checked first), `on-path` (product-scope-sourced), `unclassified` (everything else) — and raises `DemoteCoConstraintError` only on a half-written demote (`demoted_at` xor `demote_reason`). `product_term(path_class)` returns the bounded score-space ranking term (`on-path`/`unclassified` = 0; `off-path` = −4, never a lexicographic dominator). `build_demote_record(reason, ts)` builds a demote's two presence-symmetric sibling fields plus its append-only history event; raises on an empty reason/timestamp.

### `scripts/lib/area_code_link_audit.py` — area↔code-component link audit (ACL-1)

`--vault ROOT`, `--json` (default: human-readable text). Reconciles each product-scope item's optional `code_components[]` link (the PRODUCT area axis) against the Heavy `components/*.json` inventory (the CODE axis, AST-derived by `/sync`); a link naming a component absent from that inventory is a `STALE LINK`. Degrades cleanly to a no-op when there is no `product-scope.json`, no item declares a link, or no `components/` inventory exists yet (pre-code / Minimal / not-yet-synced is not drift). Exit `0` clean or degraded (status names which) · `1` ≥1 stale link · `2` usage error. Run by `/drift-check`'s full-mode ACL-1 sweep (§1.5).

### `scripts/lib/trust_ledger.py` — per-slice trust ledger (compose + render)

`prog="trust_ledger"` (slice-084 / SC-143), two subcommands:

| Subcommand | Flags | Purpose |
|---|---|---|
| `compose` | `--slice slice-NNN` (required), `--vault PATH`, `--out PATH`, `--format json\|text\|md` (default `json`) | Mechanically compose one human-facing per-slice trust ledger from `validation.json`, `gate-log.json`, and `shippability.json` — zero model authorship. Partitions evidence into reality-confirmed / model-only / not-checked / known-escapes / informational sections; every rendered line carries a required `{file, locator}` provenance citation. |
| `render` | `--from PATH` (an existing `trust-ledger.json`) or `--slice slice-NNN` (+ `--vault`), `--format text\|md` | A pure, deterministic view of an already-composed ledger — re-derives nothing. |

Exit `0` ok · `2` usage error / slice not found / unreadable `--from` ledger. **Not currently invoked from any `SKILL.md`** (auto-wiring into a fixed loop stage was explicitly deferred at slice-084) — run it directly, e.g. `$PY scripts/lib/trust_ledger.py compose --slice slice-NNN --format text`, to inspect a slice's assurance case without reading its diff.

### `skills/slice/scripts/candidates_top.py` — ranked backlog digest (with the `--area` lens)

Single-skill tool behind `/slice`'s injected "Live state." `--top N` (default 5; `<=0` = all), `--json`, `--vault`, and the optional **`--area NAME`** lens (alias `--component`, slice-080/084): filters the pickable list to product-sourced candidates bound to one product area (`unassigned` for un-grouped capabilities) — read-only, takes no lock, mints no id, writes no status. Blocked/in-flight candidates stay global context regardless of the lens. Default-OFF (no `--area`) output is byte-identical to the un-lensed digest.

### `skills/slice-candidates/scripts/demote_candidate.py` — product-priority DEMOTE lever

`prog="demote_candidate"`. `--candidate SC-NNN` (required), `--reason TEXT` (required), `--vault`, `--json` (slice-077 / ADR-088). Records a bounded off-path demote (`demoted_at` + `demote_reason` + an append-only `history[]` event) that lowers a candidate's rank at the `candidates_top.py` pick surface by `product_priority`'s score-space term — WITHOUT deleting or opening its risk-register entry. Refuses on a product-scope-sourced (on-path) target or a critical-band one (severity `critical`, or score ≥ 9). A same-reason re-demote is an idempotent no-op; a different reason refuses (never silently overwrites the audit record). Exit `0` success (incl. the idempotent no-op) · `1` runtime refusal · `2` usage error. Invoked as `/slice-candidates --demote SC-NNN --reason TEXT`.

### `skills/pulse/scripts/orphaned_candidates.py` — out-of-scope candidate leak report

Single-skill tool behind `/pulse`'s injected out-of-scope surface (slice-078 / ADR-089). `--vault PATH`. Read-only: subprocesses `product_scope.py materialize --dry-run --json`, keys on its exit code, and filters its `orphaned` set to candidates present in the **live** `candidates.json` — a mark-without-sweep report of still-pickable candidates whose parent product-scope capability was cut. Emits `{scope_present, orphaned:[{candidate, ref}], error?}`. Exit `0` always (any failure rides the `error` field on stdout).

### `skills/build-slice/scripts/stub_dead_audit.py` — STUB-DEAD-1 stub/dead-code gate

`prog="stub_dead_audit"` (slice-085 / ADR-099, ADR-100). `--worktree DIR` (required — must be a real git worktree root), `--base REF` (default: self-resolved via `scripts/lib/slice_diff_base.resolve_slice_diff_base`; `pre_finish_gate.py` normally threads its own already-resolved base). Deterministic, diff-scoped stdlib-`ast` scan of the slice's changed `.py` files (diff-scope by added-line intersection, not baseline subtraction) for three rules: `STUB-BODY` (a function body that is solely `pass`/`...`/`raise NotImplementedError`, with carve-outs for `@abstractmethod`/`@overload`/`Protocol`/`ABC` members, `.pyi` files, `TYPE_CHECKING` blocks, and empty `__init__`/lifecycle hooks), `SILENT-EXCEPT` (a bare or broad `except: pass`), `UNREACHABLE` (a statement following a terminal `return`/`raise`/`break`/`continue` in the same suite). Inline-suppressible via a `# stub-dead:allow` token. Fail-closed on any git/parse fault, printing a `[STUB-DEAD-1] INFRA:` banner as the first line. Exit `0` PASS (incl. a diff touching no `.py`) · `1` BLOCK (finding or infra) · `2` usage error. Part of `/build-slice`'s pre-finish gate.

### `skills/critic-calibrate/scripts/calibration_export.py` — upstream calibration declassifier

`prog="calibration_export"` (slice-087 / ADR-103, ADR-104). `--vault DIR` or `--log FILE` (the calibration log to read), `--approved-checks FILE` (the Step-5 human-consent staging file: a JSON array of `{text, recurrence_count}`), `--out FILE` (default: stdout). A **default-deny declassifier** for `<vault>/critic-calibration-log.json`: `calibration_notes[]`/`gate_skips[]` are auto-projected through a small, closed field+value allowlist (`EMIT_SCHEMA`) with free-text bodies withheld and reduced to a recurrence count — an unrecognized field or out-of-vocabulary value is dropped, never emitted; `active_checks[]` reaches the payload ONLY through the caller-supplied, human-genericized `--approved-checks` staging file (the log itself is never machine-read for this array), and a structural backstop refuses any confirmed check text still carrying an un-genericized path / `slice-NNN` / `CC-NNN`/`SHIP-NNN`/`GS-NNN`/`CN-NNN` token; `runs[]` is out of scope entirely. `scripts/lib/secret_scrub.py`'s `redact()`/`scan()` run last on the final serialized markdown as a credential defense-in-depth pass (not the primary control). Fail-closed: a missing/malformed/empty log, an all-withheld (hollow) projection, a backstop hit, or a scrub failure exits non-zero with **nothing** on stdout; a redaction manifest (types/counts only, never values) goes to stderr. Exit `0` emitted · `1` refused · `2` usage error. Invoked by `/critic-calibrate` Step 5.

### `.build/aggregate.py` — design-record + examples regenerator

`python3 .build/aggregate.py` (no flags). Regenerates each skill's bundled `examples/<artifact>.json` from `schemas/artifact-examples.json`, plus the git-ignored local design record (`skill.json` ×30 + `skill-graph.json`) from `.build/manifests/`. `SKILL.md` and `scripts/` are never touched — hand-authored, not generated.

### `.build/cross_block_audit.py` — SKILL.md bash cross-block variable audit

`python3 .build/cross_block_audit.py skills/*/SKILL.md` (or any set of `SKILL.md` paths). Flags a bash variable referenced in one fenced block but only assigned in a *different* block — since skill bash blocks don't share shell state across separate tool calls, such a reference would silently see an empty value at runtime.

---

## 3. Environment variables

| Variable | Resolved by | Effect |
|---|---|---|
| `AI_SDLC_PY` | `hooks/setup_env.py::resolve_interpreter` | Pin the Python 3 interpreter every skill's `$PY` resolves to (highest precedence, before `python3`/`python`/`py`). |
| `AI_SDLC_CRG` | `hooks/setup_env.py::resolve_crg` | Pin the `code-review-graph` CLI entry point `$CRG` resolves to (before `<py-scripts-dir>/code-review-graph[.exe]` / a bare PATH lookup). |
| `AI_SDLC_VAULT_ROOT` | `scripts/lib/_vault_paths.py::VAULT_ROOT` | Tier-1 vault override — the *exact* vault directory for the current shell/process (highest-precedence resolution tier; see the README's Vault location table). |
| `AI_SDLC_AUTO_INSTALL` | `hooks/setup_env.py::main` | Set to `1` to have the SessionStart hook silently install missing deps itself (the default is a visible nudge toward `/ai-sdlc:setup`; MCP registration always goes through `/ai-sdlc:setup` regardless). |

## 4. Config files

| Path | Tracked? | Written by | Purpose |
|---|---|---|---|
| `.mcp.json` | gitignored (machine-specific) | `/ai-sdlc:setup` | Registers the `code-review-graph` MCP server for this project. |
| `.aisdlc/reality-gates.json` | repo-tracked (offered for consented commit) | `scripts/lib/scaffold_reality_gates.py` (via `/ai-sdlc:setup`) | Declares the project's deterministic reality gates by surface (`security`/`nfr`/`ops`); read by `reality_gate_runner.py` at `/build-slice`'s pre-finish gate and `/validate-slice`. Absent/empty ⇒ structural no-op. |
| `.aisdlc/gates/py_security_gate.py` | repo-tracked (vendored) | `scripts/lib/scaffold_reality_gates.py` | Vendored, standalone copy of `scripts/lib/security_gate.py` — the committed manifest command stays portable (no plugin path on CI/teammate machines). |
| `.aisdlc/receipts/<slice-NNN>.json` | committed per slice (opt-in) | `/commit-slice` (via `scripts/lib/ship_receipt.py emit`) | The CI merge-gate evidence record — validation result, criteria counts, shippability/deferral state, gate-log rows. Only written when `.github/workflows/aisdlc-merge-gate.yml` is installed. |
| `<git-common-dir>/aisdlc/vault-root` | never git-tracked | `vault_admin.py write-pin` (via `/triage`/`/adopt`) | Tier-2 vault pin — the exact vault dir for this one repo (shared across its worktrees). |
| `~/.claude/ai-sdlc-vault-base` | machine-local, not repo-tracked | hand-written by the user | Tier-3 vault base override — changes the parent directory for every project's auto-named vault. |

## 5. code-review-graph (CRG) — external dependency surface

`code-review-graph` is a **third-party** pip package (`github.com/tirth8205/code-review-graph`, pinned `>=2.3,<3` in `requirements.txt`), installed and MCP-registered by `/ai-sdlc:setup`. The plugin never bundles or vendors it.

**CLI verbs** (invoked as `"${CRG:-code-review-graph}" <verb>` in skill bash blocks):

| Verb | Used by | Purpose |
|---|---|---|
| `--version` | `/triage`, `/adopt` (preflight) | Confirm CRG is installed; prints the resolved version. |
| `build [--repo <path>]` | `/adopt`, `/diagnose`, `/bug-hunt` | Build (or refresh) the code graph at `<repo>/.code-review-graph/`. |
| `update` | maintenance paths | Refresh an existing graph incrementally. |
| `install --platform claude-code` | `/triage` Step 5b-pre | Install CRG's git hooks + register its MCP tools for Claude Code. |

**Python query API** (CRG 2.3.x has **no** `search` / `impact-radius` / `review-context` CLI verb — these are
Python functions in `code_review_graph.tools.query`, invoked either as MCP tools when the server is live, or as
a `python -c` subprocess otherwise, e.g. `skills/slice-candidates/scripts/_crg_impact.py` and
`skills/release/scripts/harvest_degrade.py`):

| Function | Exposed as (MCP) | Purpose |
|---|---|---|
| `semantic_search_nodes(query, repo_root, limit)` | `mcp__code-review-graph__semantic_search_nodes_tool` | Symbol/keyword search over the code graph. |
| `get_impact_radius(changed_files, repo_root)` | `mcp__code-review-graph__get_impact_radius_tool` | Blast-radius / reachability for a set of changed files. |
| `list_graph_stats` | (no MCP wrapper referenced in this repo) | Coarse graph counts — used as the degraded/no-embedding fallback in `/release`'s harvester (`skills/release/scripts/harvest_degrade.py`). |

Skills using these MCP tools directly: `/design-slice`, `/build-slice`, `/code-review`, `/query-design`,
`/reduce`, `/repro`. Skills invoking the Python functions as a subprocess (no MCP dependency): `/diagnose`,
`/bug-hunt`, `/slice-candidates` (`_crg_impact.py`), `/release` (`harvest_degrade.py`).

---

## Further reading

- [README](../README.md) — orientation, install, configuration
- [User guide](user-guide.md) — task-oriented walkthroughs of the full pipeline
