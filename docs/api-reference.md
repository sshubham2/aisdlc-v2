# ai-sdlc API Reference

Interface reference for the `ai-sdlc` Claude Code plugin, **v2.39.0**. This is a **reference**, not a tutorial —
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
| `/slice` | `["<description or hint>"]` | Reads the ranked `candidates.json` backlog (read-only pick path), presents a ranked recommendation, claims the pick (worktree + branch + `pick_log`), writes `mission-brief.json` + `milestone.json`. Requires a failing `/repro` test first for bug-fix candidates (BFRD-1). | yes → `/risk-spike` |
| `/risk-spike` | `[--mode feasibility\|design] [slice-NNN \| SC-NNN \| R-NN \| all]` | **Two modes.** `feasibility` (default, step-0, auto-triggered by `/slice`): proves the candidate's blocking `assumptions[]` with a `field-recon` subagent on the real environment; any NO-GO blocks the candidate until a fallback is re-spiked. `design` (`--mode design`, auto-triggered by `/design-slice`): reality-adjudicates `design.json`'s `decidable_disagreements` + must-verify cross-domain invariants. | yes (feasibility → `/design-slice`; design, all GO → `/critique`, any NO-GO → `/design-slice`) |
| `/design-slice` | `[slice-id]` | Runs on **every** slice regardless of tier: spawns all 3 blind designer subagents (practice / cross-domain / expert), reality-grounds one synthesis (CRG blast-radius, spike evidence, reversibility, simplest-that-works), tags every new ADR with reversibility. Writes `design.json`. | yes → `/risk-spike --mode design` (conditional) or `/critique` |
| `/critique` | `[slice-id] [--force]` | Tier-driven adversarial design review. **Runs** when `risk_tier ∈ {medium, high}` OR `critic_required == true` (auth/authz, API contracts, data-model, security paths, methodology surface `skills/**`/`agents/**`/`scripts/**`; Heavy forces it on every slice); **skipped** on `low` tier with no mandatory trigger. Spawns a forked `critique` subagent (9 fixed attack dimensions), writes `critique.json`, then the **interactive TRI-1 user triage gate** (accept / defer / reject with rationale, on the main thread). `--force` runs regardless of tier/gate-skip. | yes (CLEAN/NEEDS-FIXES only; BLOCKED halts) |
| `/critique-review` | `[slice-id]` | The meta-Critic (DR-1). **Mandatory** when ANY holds: `risk_tier == high`; `critic_required == true`; first-Critic `findings` count ≥ 5; **full tournament convergence** — no designer pair in `design.json`'s `approach_divergence` classified `disjoint` (slice-066 / ADR-064, computed by `scripts/lib/tournament_convergence.py`, never eyeballed). Advisory on 3+ consecutive clean verdicts. Spawns a second adversarial subagent that classifies each first-Critic finding `valid`/`suspicious`/`severity-wrong` and surfaces `missed[]`; feeds back into the same TRI-1 gate. Runs IN-LOOP, before triage. | yes → `/critique` Step 4.5 (TRI-1) |
| `/slice-story` | `[slice-id]` | Auto-invoked after `/critique` **only when it produced ≥1 finding**. A forked `slice-story` narrator subagent turns every artifact into plain English (zero jargon); `render_story.py` renders `story.html`, delivered via `SendUserFile` (phone included over Remote Control). Also user-invokable any time, at any lifecycle stage. | false — halts and prompts `/build-slice` |
| `/build-slice` | `[slice-id]` | Plan-mode execution. Explores code with CRG queries + targeted Reads, drafts a task sequence, **halts for explicit user approval** before writing code. Task-by-task verification, a mandatory mid-slice smoke gate, and a consolidated pre-finish gate (`pre_finish_gate.py`: all ACs, must-not-defer, `/drift-check --fast`, and the project's declared reality gates via `reality_gate_runner.py`, fail-closed). Writes `build-log.json`. | yes → `/code-review` |
| `/code-review` | `[slice-id]` | Forked `code-review` agent context. Reviews the just-built diff along the same 9 fixed dimensions as the design Critic, producing `path:line` findings as `code-review.json`. Blocker findings must be dispositioned before `/validate-slice`. | yes → `/validate-slice` |
| `/validate-slice` | `[slice-id]` | Forked context. Per-criterion PASS/FAIL/PARTIAL checks on the **real environment**. Runs VAL-1/WS-1/ETC-1 layered audits, the shippability-catalog regression check, and the project's declared reality gates (`reality_gate_runner.py --repo-root <worktree> --json`, fail-closed — an absent/empty manifest is a structural no-op). | conditional — aggregate `Result: PASS` only |
| `/reflect` | `[slice-id]` | Captures validated / corrected / discovered / deferred learnings. Writes `reflection.json`, appends `lessons-learned.json` + `shippability.json`, tracks Critic calibration, auto-archives the slice. | **false — the auto-advance terminus** |
| `/commit-slice` | `[--merge \| --push \| --sync-after-pr]` | User-invoked only (never auto-advanced into). Generates an audit-grade conventional commit message from vault artifacts. `--merge`: solo-dev local merge + safe branch delete, re-running the shippability catalog against the post-rebase integration-branch tip first (refuses on red). `--push`: push + rebase onto the integration branch + gh-aware PR + non-blocking auto-merge (degrades gracefully). `--sync-after-pr`: post-PR cleanup. No flag: generate + show only. Emits `.aisdlc/receipts/<slice-NNN>.json` if the CI merge-gate workflow is installed. | false |

### 1.4 Orientation

| Command | Arguments | Purpose |
|---|---|---|
| `/pulse` | `[--brief \| --full]` | Read-only vault scan: active slice stage + next action, risk exposure, regression health, Critic calibration, per-gate hit-rate (measurement spine), top lessons, candidate backlog snapshot, recommended next action. Default ~60 lines, `--brief` ~20, `--full` ~150. |
| `/query-design` | `["<question about the codebase>"]` | Read-only, grounded Q&A — every answer cites file/line/symbol evidence via Grep/Read/CRG. Writes nothing. May offer (never force) a handoff to `/slice`. |

### 1.5 Maintenance

| Command | Arguments | Purpose |
|---|---|---|
| `/drift-check` | `[--fast] [--resolve] [--status] [path]` | Audits vault claims vs. code reality: `DRIFT` (blocker), `UNSPECIFIED CODE` / `STALE CLAIM` / `STALE DOC` (major — the last via the `/release`-written `doc-manifest.json` provenance anchor). `--fast` scopes to changed files (<2s, used by `/build-slice`'s pre-finish gate); `--resolve` walks findings interactively; `--status` folds `drift-log.json` into current state, read-only. |
| `/reduce` | `[--force]` | Complexity-budget enforcer: over-engineering, dead claims, speculative generality, god-nodes, cross-slice incoherence. Produces a ranked reduction report; proposes a simplification slice if confirmed. `--force` bypasses the confirmation gate, not the slice gate. |
| `/archive` | `[--index-only]` | Archives completed slices (`reflection.json` present) from `slices/` to `slices/archive/`, regenerates both `_index.json` files. `--index-only` rebuilds indexes without moving files. |
| `/supersede-slice` | `<archived-slice-id>` | Establishes a bidirectional supersession link between an archived slice and the active slice correcting it. Appends to the archived `reflection.json`, sets `Supersedes` in the active `mission-brief.json`. |
| `/critic-calibrate` | `[--window N]` (default 15) | Mines "Missed by Critic" entries from the last N archived reflections, proposes 0–3 evidence-backed Critic check changes. Persisted to a project overlay (`critic-calibration-log.json`) — never edits the shipped `agents/critique.md`. |
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
| `/slice-candidates` | `[path-to-diagnose-out] [--obo] [--product]` | **Finding path** (default): reads the annotated `diagnosis.html`, keeps `Confirmed: yes` rows, detects file-overlap coupling via CRG blast-radius, topo-sorts into `candidates.json`. `--obo` walks findings one at a time interactively. **`--product` path** (peer of `--obo`, slice-068 / ADR-067): reads `<vault>/concept.json` instead — decomposes the product's own declared scope into candidate-shaped items **once** (ids minted in-lock by `scripts/lib/product_scope.py`, never by the model), persists to `product-scope.json`, materializes as `product-scope`-sourced candidates. Idempotent re-run via `product_scope.py materialize`; extend/correct via `product_scope.py revise --items-file PATH [--cut PS-NNN --reason TEXT]` — a WHOLE-LIST replace that REFUSES a payload omitting a live item (an omission was a silent delete before slice-073 / ADR-078); removal is the explicit `--cut` (repeatable, `--reason` required), recorded in `product-scope.json`'s append-only `revisions[]`. `vault_edit remove`/`set --path items`/`append` on `product-scope.json`/`items` all refuse (ADR-080). |
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

`prog="secret_scrub"`. `--in <file>` (default stdin) → `--out <file>` (default stdout), redacting matched credential patterns as `[REDACTED:<type>]`. `--check` — gate mode: don't redact, exit `1` if any secret is found, `0` otherwise. Used before any captured `/validate-slice` / `/risk-spike` evidence is written to the vault.

### `scripts/lib/release_advance_audit.py` — release-model invariant audit

`--root <dir>` (default `.`), `--genesis <tag>` (default `release-genesis`), `--json`. Walks `git rev-list --first-parent <genesis>..<master>` and asserts every advance of `master` changed `.claude-plugin/plugin.json`'s `version` — i.e. `master` only ever advances via a versioned `/release` cut, never a direct commit. No-ops on a non-methodology repo (no `.claude-plugin/plugin.json`). Exit `0` clean / `1` violation / `2` usage (git unusable / genesis absent).

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
