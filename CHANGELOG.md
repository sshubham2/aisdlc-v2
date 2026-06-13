# Changelog

All notable changes to the **ai-sdlc** plugin, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/)
(**patch** = fix/docs/refactor · **minor** = new skill or backward-compatible feature · **major** = breaking change).

This file is a complete history reconstructed from the git log. Entries for **v2.28.0 and later** are sourced from the
per-slice `changelog.json` records that `/commit-slice` writes (and are regenerable by `/product-doc`); earlier
versions were reconstructed from commit history. Every pushed commit carries a `version` bump, so each release below
maps to a real point in `master`.

## [Unreleased]

_Nothing unreleased — `master` is at v2.32.1 and pushed._

## [2.32.1] — 2026-06-13

### Changed

- **docs:** Regenerated the product documentation (README / API reference / user guide / CHANGELOG) via `/product-doc` to track the v2.32.0 slice-story enrichment.

## [2.32.0] — 2026-06-13

### Added

- **slice-story:** `/slice-story` now tells the WHOLE story — it narrates the design tournament (which approaches were weighed, which was picked, and why) and weaves a single "what went wrong, and what we did" problems-and-resolutions thread that replaces the scattered per-stage problem narration. (slice-005-enrich-slice-story) [ADR-003]
- **commit-slice:** On `--merge`/`--push`, `/commit-slice` auto-emits the full refreshed story into `slices/archive/<slice>/` (best-effort, never blocking the commit) via a new archive-aware resolver (`active_slice.resolve_slice_by_id` + `--slice`) and a `Skill` grant on commit-slice. No render or schema change — `render_story.py` stays byte-identical.

## [2.31.0] — 2026-06-13

### Added

- **risk-spike:** The terminal spike write now preserves the **ternary verdict** (`go` | `no-go` | `conditional`) as first-class data via sibling fields `spike_verdict` + `spike_constraints[]`, while `spike_status` keeps its binary gate semantics — so a CONDITIONAL spike's named constraints reach every downstream reader without re-opening the spike file. The `design-slice` Step 5 pass-through carries the verdict into `design.json`. (slice-004-preserve-conditional-spike-verdicts) [ADR-002]

### Changed

- **artifact_lint:** Enforces the new verdict/status enums plus a per-row `conditional ⇒ constraints` co-constraint (stale-constraints leak guard + clear-on-transition on re-spike). Fully backward-compatible — legacy binary records lint clean.

## [2.30.0] — 2026-06-12

### Added

- **supersede:** The supersession block gains an optional `revert{commit, pr, note}` recording HOW superseded code was unwound. Validation is orthogonal to link completeness (it fires on half-written records), treats null-as-absent, and strictly rejects unknown keys; a mandatory Step 2b ask captures the optional field. The audit is standalone and refuse-never-crash. (slice-003-supersession-revert-refs)

## [2.29.0] — 2026-06-12

### Added

- **drift-check:** `/drift-check --status` — a deterministic, read-only fold of the append-only drift-log into current state: accepted-drift (with rationale + age + re-detections), open (including re-opened), resolved, and unfoldable entries (verbatim). Asymmetric supersession is ratified at TRI-1; the sort key (parsed aware-UTC datetime, then array index) breaks same-second ties. (slice-002-drift-status-view)

## [2.28.0] — 2026-06-12

### Added

- **design-schema:** `design.json` gains an optional `assumptions_proven[]` — the spike→design evidence cross-ref. It is a pure pass-through of the claimed candidate's proven assumptions (`{assumption, statement, spike_ref}`; no verdict field — the spike file stays the verdict authority). Optionality is delivered via `artifact_lint` `OPTIONAL_KEYS` (not merely asserted), and Critic Dimension 1 gains the spike-evidence walk. (slice-001-design-proven-by-crossrefs) [ADR-001]

## [2.27.0] — 2026-06-12

### Added

- **improvement-roadmap batch:** A CI merge gate, vault export/import, graduated reality contact, and designer decorrelation metrics — the roadmap batch carrying the durable pipeline improvements out of the external audit.

## [2.26.0] — 2026-06-12

### Changed

- **design-decision batch:** Applied the design decisions surfaced by the external audit across the skills and supporting docs.

## [2.25.6] — 2026-06-12

### Fixed

- **external-audit batch:** Encoding fixes, removal of prose contradictions, fork-relay correctness, and scratch-file hygiene flagged by the external audit.

## [2.25.5] — 2026-06-12

### Removed

- **chore:** Retired `aisdlc-v2-fix-plan.md` now that the post-deep-review remediation is 100% shipped (the plan lives on in git history).

## [2.25.4] — 2026-06-12

### Added

- **tests:** A guard (`tests/test_assemble_assets.py`) for the 3.19.6 asset extraction — verifies `assemble.py` loads its sibling `assemble.css`/`assemble.js` and the rendered HTML stays byte-identical. Suite now 136 green.

## [2.25.3] — 2026-06-12

### Changed

- **perf (3.19.5):** `_vault_paths.VAULT_ROOT` is now resolved lazily via a PEP-562 module `__getattr__` — no `git rev-parse` at import time.
- **refactor (3.19.6):** Extracted `assemble.py`'s inline CSS/JS to sibling `assemble.css`/`assemble.js`; rendered HTML is byte-identical (proven by value-sha).

## [2.25.2] — 2026-06-12

### Changed

- **docs:** Brought the README current to v2.25.2 — version line, dropped a leaked plan reference, and added the 4.6.1 vault note.

## [2.25.1] — 2026-06-12

### Fixed

- **NAW-1:** No-ops cleanly when there is no default-branch diff base, keeping CI green. (Merged via PR #4 — the remediation-plan branch.)

## [2.25.0] — 2026-06-12

### Fixed

- **4.6.1:** Stop freezing the vault — the vault root is resolved per-invocation. The SessionStart hook no longer exports `AI_SDLC_VAULT_ROOT`, and 9 skills across 17 sites switched to a `${VAR:-…}` fallback, fixing a cross-repo bug where two repos in one session resolved a single shared vault. Phase 4 complete (+3 tests).

## [2.24.1] — 2026-06-12

### Changed

- **docs (4.9):** Meta-docs cleanup — shipped the durable rules and removed transient planning scaffolding.

## [2.24.0] — 2026-06-12

### Added

- **4.7 vault lifecycle & secret hygiene:** A `secret_scrub` secrets-sweep (the crown-jewel guard) plus a tier-2 `vault_admin` (pin / orphan garbage-collection) for vault lifecycle management.

## [2.23.0] — 2026-06-12

### Added

- **4.5:** Vault artifact version-skew detection — surfaces artifacts written by a different plugin version than the one reading them.

## [2.22.4] — 2026-06-12

### Fixed

- **4.3:** Pinned the `code-review-graph` supply chain (`<3`) and surfaced the resolved version.

## [2.22.3] — 2026-06-12

### Changed

- **refactor (4.8):** Made the remaining `.build/` scripts portable (no machine-specific roots).

## [2.22.2] — 2026-06-12

### Changed

- **docs (4.2):** Rewrote the storefront / marketplace descriptions.

## [2.22.1] — 2026-06-11

### Fixed

- **4.6 (hook-only subset):** SessionStart hook now dedupes and short-circuits its env-file writes.
- **3.19.8:** The hook relays a vault WARN on stderr instead of swallowing it.

## [2.22.0] — 2026-06-11

### Changed

- **3.1-rest (BCSG-1):** The gate-log is now treated as a model-tier measurement spine — the by-construction safety guarantee reframed around it.

## [2.21.0] — 2026-06-11

### Added

- **4.1:** MIT license.

### Changed

- **4.10:** Demoted the generated design record — `skill.json` (×30) + `skill-graph.json` are now git-ignored and kept LOCAL only (zero runtime consumers; the harness loads `SKILL.md`). `examples/` stay tracked; `.build/` is kept for CI.

## [2.20.3] — 2026-06-11

### Changed

- **refactor (3.7):** Merged the three brief-variant audits into a single `brief_variants_audit` (parity proven across 23 fixtures).

## [2.20.2] — 2026-06-11

### Fixed

- **PCA-1:** Reconciled the canonical-chain audit using the Option-1 membership matcher; gated the self-audits in CI.

## [2.20.1] — 2026-06-11

### Fixed

- **UTF8-STDOUT-1:** UTF-8 stdout conformance on 5 tools (Windows cp1252 crash guard).

## [2.20.0] — 2026-06-11

### Added

- **4.4:** A plugin test suite + GitHub Actions CI — pytest matrix · build-consistency · plugin self-audits (7/7), all gating.

## [2.19.19] — 2026-06-11

### Changed

- **docs:** Pre-compaction status sync — fix-plan header + version references to v2.19.19.

## [2.19.18] — 2026-06-11

### Changed

- **docs:** Synced execution status — 3.19 bug fixes done.

## [2.19.17] — 2026-06-11

### Fixed

- **Phase 3:** Triage raw-write-over-project (UX-2) and write-path residuals (3.19.7, 3.19.4).

## [2.19.16] — 2026-06-11

### Fixed

- **Phase 3:** `mock_budget` JS gap (3.19.1) and a `/diagnose` PERSISTING bug (3.19.2).

## [2.19.15] — 2026-06-11

### Changed

- **docs:** Synced execution status — 3.18 fully done, Phase 3 ~18/19.

## [2.19.14] — 2026-06-11

### Added

- **Phase 3 (3.18.3):** Wired the brief-variants producer (per a user decision).

## [2.19.13] — 2026-06-11

### Added

- **Phase 3 (3.18, minus 3.18.3):** The schema/contract layer + `artifact_lint`.

## [2.19.12] — 2026-06-11

### Changed

- **docs:** Synced execution status — 3.11 done, Phase 3 ~17/19.

## [2.19.11] — 2026-06-11

### Changed

- **Phase 3 (3.11):** Shrank SVW-1 to a dumb advisory tripwire.

## [2.19.10] — 2026-06-11

### Changed

- **docs:** Synced execution status — Phase 3 ~16/19 (3.17, 3.9, 3.19.3 done).

## [2.19.9] — 2026-06-11

### Fixed

- **Phase 3:** Deleted the dead NFR-1 carry-over (3.9) and fixed the `finding_dedup` header (3.19.3).

## [2.19.8] — 2026-06-11

### Fixed

- **Phase 3 (3.17):** The `validate-slice` fork must not ask interactively mid-fork.

## [2.19.7] — 2026-06-11

### Changed

- **docs:** Annotated the remediation plan with session progress (Phase 3 ~14/19).

## [2.19.6] — 2026-06-11

### Fixed

- **Phase 3 batch C.2 (3.8):** Hyphen-tolerant skip regex.

## [2.19.5] — 2026-06-11

### Changed

- **Phase 3 batch C.1 (3.1):** WS-1 becomes a real reality gate.

## [2.19.4] — 2026-06-11

### Added

- **Phase 3 batch B.2 (3.3):** Measure designer divergence (tournament decorrelation).

## [2.19.3] — 2026-06-11

### Changed

- **Phase 3 batch B.1 (3.5, 3.2):** `critic-calibrate` overlay template + a gate-skip mechanism.

## [2.19.2] — 2026-06-11

### Fixed

- **Phase 3 batch A (3.10, 3.12, 3.13):** Coherence fixes across the skill set.

## [2.19.1] — 2026-06-11

### Fixed

- **Phase 3 (partial):** Coherence + pipeline-philosophy fixes.

## [2.19.0] — 2026-06-11

### Changed

- **Phase 2 — cost & loop redesign:** Tier-driven gating. Risk tier (not mode) drives whether `/critique`, `/critique-review`, and the design tournament run, so cost scales with per-slice risk rather than a global mode setting.

## [2.18.4] — 2026-06-11

### Fixed

- **Phase 1:** Critical-correctness remediation from the post-deep-review pass.

## [2.18.3] — 2026-06-10

### Changed

- **docs:** Refreshed the README to the current pipeline. (Merged via PR #2.)

## [2.18.2] — 2026-06-10

### Fixed

- **mock_budget_lint:** Corrected the nested-test double-count (a proper fix, no test-evasion). (Merged via PR #1.)

## [2.18.1] — 2026-06-10

### Fixed

- **pipeline audit:** Doc references, AC-label normalizer, and missing artifact examples.

## [2.18.0] — 2026-06-10

### Added

- **P3 batch:** Anti-alert-fatigue measures, auto-maintained docs, and expert-vocabulary support across the Critic skills.

## [2.17.0] — 2026-06-10

### Added

- **critique:** Tier-gated source/fact verification in `/critique`.

## [2.16.0] — 2026-06-10

### Added

- **Theme 4 — seam coherence:** Pick-time coupling detection + a cross-slice audit so cuts that should ship together are flagged.

## [2.15.0] — 2026-06-10

### Changed

- **code-review:** Aimed the code-review scholar and added the measure-complexity handoff.

## [2.14.0] — 2026-06-10

### Added

- **slice-candidates:** A thickness heuristic that flags over-thin coupled cuts.

## [2.13.0] — 2026-06-09

### Added

- **per-gate recall loop:** Gate-log miss rows — the measurement spine now records per-gate misses for recall tracking.

## [2.12.0] — 2026-06-09

### Added

- **critique:** Method-heterogeneous critics + an approach-level reframe (independent reviewers with different methods/personas).

## [2.11.0] — 2026-06-09

### Added

- **critic-calibrate:** Progressive Critic calibration.
- **product-doc:** New `/product-doc` skill — grounded README / CHANGELOG / API-reference / user-guide generation.
- **drift-check:** A stale-doc loop that flags `/product-doc`-generated docs which drift from the code surface they documented.

## [2.10.0] — 2026-06-09

### Added

- **design-slice:** The design tournament — 2–3 BLIND designer subagents (practice / cross-domain / expert) feeding a reality-grounded synthesis.
- **risk-spike:** Split into two modes — the step-0 feasibility spike and a post-synthesis `--mode design` spike that lets reality adjudicate the tournament's empirically-decidable disagreements.
- **critique:** Expert-independent critique (the expert lens stays independent of the channeled designer).

## [2.9.0] — 2026-06-09

### Added

- **gate measurement:** The gate-log measurement spine.
- **slice-candidates:** Reality-contact ranking.
- **design-slice:** A cross-domain seed for the design tournament.

## [2.6.1] — 2026-06-09

### Changed

- **slice-story:** Deliver the report via `SendUserFile` (reaching your phone over Remote Control); dropped the Google Drive path.

## [2.6.0] — 2026-06-08

### Added

- **slice-story:** New `/slice-story` skill — a plain-language per-slice report generator that runs after `/critique` as the pre-build overview, spawns a forked narrator agent, and renders a standalone `story.html`.

## [2.5.2] — 2026-06-08

### Added

- **setup:** A hook-health doctor in `/ai-sdlc:setup`.

## [2.5.1] — 2026-06-08

### Fixed

- **vault:** Dropped the v1 `architecture/` fallback in `adopt` / `critique` / `reduce` (the v2 vault is external).

## [2.5.0] — 2026-06-07

### Added

- **setup:** New `/ai-sdlc:setup` dependency doctor.

### Changed

- **hooks:** Ported the SessionStart env-resolution hook to Python (fixing the git-bash backslash/BOM/quoting traps), with a Bash bootstrap shim.

## [2.4.0] — 2026-06-07

### Added

- **critique:** A project-calibrated checks overlay — `/critique` reads project-local active checks before each review.

### Changed

- **critic-calibrate:** Stops editing the plugin — accepted checks persist to the project's vault overlay instead of the shipped `agents/critique.md`, so calibration survives plugin upgrades.

## [2.3.0] — 2026-06-07

### Added

- **commit-slice:** Per-slice `changelog.json` records (the deterministic source for `/product-doc`'s CHANGELOG).

### Fixed

- **graph:** Graph-consistency fixes.

## [2.2.0] — 2026-06-07

### Added

- **WT-ROOT-1:** A worktree contract for the slice loop, the repro-into-worktree fix, and SessionStart hook auto-install.

## [2.1.0] — 2026-06-07

### Added

- **bug-hunt:** New `/bug-hunt` skill — whole-codebase correctness + security defect sweep.
- **diagnose / bug-hunt:** Cross-pass finding de-duplication (`finding_dedup`) and shared forensic infrastructure (`write_pass.py` / `assemble.py` / `finding.yaml` promoted to `scripts/lib/`).

## [2.0.3] — 2026-06-07

### Fixed

- **pulse:** Completed the pre-slice next-action state machine.

## [2.0.2] — 2026-06-07

### Fixed

- **pulse:** Probe the Heavy-mode architecture phase.

## [2.0.1] — 2026-06-07

### Fixed

- **paths:** Resolve bundled + vault paths for plugin-cache installs.

## [2.0.0] — 2026-06-06

### Added

- **Initial release of the ai-sdlc v2 plugin** — the spec-driven AI SDLC pipeline rebuilt around JSON skill manifests. Packaged as one plugin (`.claude-plugin/plugin.json`, `agents/`, shared `scripts/lib/`, and `skills/<name>/` each with `SKILL.md` + `examples/` + `scripts/`). Ships the full pipeline: `triage`/`adopt` → `discover` → the per-slice loop (`slice` → `risk-spike` → `design-slice` → `critique`/`critique-review` → `build-slice` → `code-review` → `validate-slice` → `reflect`), `commit-slice`, the maintenance skills (`drift-check`, `reduce`, `archive`, `sync`, `supersede-slice`, `critic-calibrate`), orientation (`pulse`, `query-design`), and brownfield analysis (`diagnose`, `slice-candidates`).
- A README + self-hosted marketplace manifest.

### Fixed

- Resolved 34 bug-bounty findings and authored the 5 missing agent personas.
- Fixed a systemic cross-block-variable bug in `/diagnose` + `build-slice`; added the audit tool that catches it.
