# scripts/lib — shared tooling (plugin-level)

Code used by **more than one skill** lives here. Single-skill tools live in their own
`skills/<name>/scripts/` instead. Source for porting: `temp/tools/` (v1, read-only) — adapt for v2
(JSON artifacts instead of `.md`, `code-review-graph` instead of graphify).

## Invocation convention (runtime-correct)

A skill's shell command runs in the **user's CWD**, not the plugin root, and SKILL.md **cannot** use
`python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON hooks/MCP, not markdown — see
the claude-code-guide findings). So shared tools are invoked **by absolute path off the skill dir**:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" <args>   # ${CLAUDE_SKILL_DIR} = <plugin>/skills/<skill>/
```

Every directly-invoked shared module carries a 5-line **plugin-root sys.path bootstrap** (`parents[2]`)
so `from scripts.lib import …` resolves under path-invocation (no-op under `-m` from the plugin root).
Pure-stdlib leaves (`_vault_paths`, `_stdout`, `_pyfn`, `_vault_write`, `_git_default_branch`) are imported
by other modules, not invoked from SKILL.md, so they need no bootstrap. (Single-skill tools use the same
absolute-path idea: `$PY "${CLAUDE_SKILL_DIR}/scripts/<x>.py"` + a `parents[3]` bootstrap.)

## Shared tools (used by >1 skill — DO NOT duplicate per-skill)

| module | used by | role |
|---|---|---|
| `vault_edit` | 16 skills | **SVW-1 safe-write** — append / rewrite-CAS / move on shared aggregate JSON. The single most-shared module; per-skill copies would drift. |
| `risk_register_audit` | triage, slice, pulse | score/sort/validate `risk-register.json` (RR-1) |
| `project_frame_synth` | design-slice, critique, critique-review | ephemeral project-frame (PFS-1) |
| `_worktree_paths` | slice, build-slice | canonical worktree path + branch (single source of truth) |
| `stranded_slice_audit` | slice, pulse | classify unmerged `slice/*` branches |
| `parallel_conflict_resolver` | commit-slice (×4 modes) | rebase-conflict resolution |
| `pulse_worktree_resolver` | pulse | worktree state classification |
| `build_checks_integrity` | build-slice, reflect | BC-1 canonical-fixture integrity |
| `cross_spec_parity_audit` | sync, drift-check | CSP-1 Heavy parity |
| `skill_vault_write_safety_audit` | CI only (`.build/plugin_self_audits.py`) | SVW-1 **advisory** raw-write tripwire over SKILL.md/agents prose (3.11 shrank 733→~150 lines + demoted from the per-slice gate; was build-slice/validate-slice) |
| `vault_flip_prose_inventory` | build-slice, validate-slice | vault op-gate |
| forward-sync gates (`methodology_changelog_forward_sync`, `ai_sdlc_version_forward_sync`, `ai_sdlc_tools_version_forward_sync`) | build-slice, reflect | installed==repo parity |
| `finding_dedup` | diagnose, bug-hunt | cross-pass / cross-finder finding de-dup — merge same-location findings into one `F-MRG-*` (category-independent). The single dedup seam for both forensic skills. |
| `grounding_verify` | release, drift-check | deterministic doc-grounding verifier (exact-membership over `crg:`/`file:`/`vault:` tokens + public_surface; fail-closed). Promoted from skills/release/scripts (2026-07 review sweep — /drift-check's STALE-DOC audit shares it). |
| `build_entry` | drift-check, sync | serialize the ONE foldable `drift-log.json` entry shape (`{at, trigger, category, finding, …}`; DCE-1 slice-trigger canonicalization; accept-drift REQUIRES rationale). Promoted from skills/drift-check/scripts (2026-07 review sweep — /sync's accept-drift shares it; a divergent hand-rolled shape is UNFOLDABLE to `drift_status.py`). |
| `supersede_audit` | supersede-slice, drift-check | SUP-1 project-wide supersession-link sweep (bidirectional consistency + strict revert-shape validation). Promoted from skills/supersede-slice/scripts (2026-07 review sweep — /drift-check full mode re-runs it periodically to catch links broken after `/supersede-slice` validated them). |
| `_crg_grounding_probe` | release, drift-check (via `grounding_verify`/`harvest_degrade` subprocess) | the ONE CRG-touching probe (`--health`/`--batch`/`--names`); never imported, always a subprocess. Moves with `grounding_verify` as a sibling. |
| `write_pass` | diagnose, bug-hunt | parse a subagent's 4-backtick fenced section/findings/summary blocks → normalize/validate → per-pass files. Imports `assemble`. |
| `assemble` | diagnose, bug-hunt | compose `diagnosis.html` from per-pass artifacts; runs `finding_dedup` after load + carries over prior annotations. Imports `finding_dedup`. |

> **Note — `finding.yaml` (data, not a tool).** The shared finding schema lives at `scripts/lib/finding.yaml` next to
> its enforcer (`assemble.REQUIRED_FIELDS`). It is a contract doc, never imported/executed, so the `*.py` tool-glob in
> `aggregate.py` does not pick it up. Both `/diagnose` and `/bug-hunt` findings conform to it (categories incl.
> `correctness-bug` + `security`).

## Shared helpers (imported by nearly every tool)

`_stdout` (UTF-8 reconfigure), `_vault_write` (safe_write_text), `_vault_paths` (vault-root resolution),
`_vault_git`, `_vault_flip`, `_pyfn`, `_forward_sync_base`, `_forward_sync_breadcrumb`, `_worktree_paths`.

## Ported so far (v2)
- **Leaf helpers** ✅ `_stdout`, `_vault_paths`, `_vault_write` (+`safe_mutate_text`), `_pyfn`, `_worktree_paths`,
  `_git_default_branch` (NEW — shared `resolve_default_branch`, extracted from the single-skill `branch_workflow_audit`).
- **`vault_edit`** ✅ canonical JSON-native CLI (9 subcommands; tested).
- **Category-B audits** ✅ ported + verified: `risk_register_audit` (md-table→json array + score/band validation),
  `cross_spec_parity_audit` (Heavy CSP-1; REQ vocab adds `planned`), `project_frame_synth` (reuses `risk_register_audit`),
  `skill_vault_write_safety_audit` (SVW-1; guarded set→`.json`, allowlist→{triage,adopt}; corpus now passes clean).
- **Category-C (vault↔git decoupled redesign)** ✅ — `pulse_worktree_resolver` + `stranded_slice_audit` (read slice
  state from the ONE shared external vault's `milestone.json` + claims from `candidates.json`; NO per-branch `git show`,
  `slice-queue.md`, or `vault_is_external`). `build_checks_integrity` ✅ minimal v2 (JSON, project-only, no-op when no fixture).
- **DROPPED in v2** (user decisions / degenerate): `_vault_git`, `vault_flip_prose_inventory` (flip collapsed),
  forward-sync gates ×3 + `_forward_sync_base`/`_breadcrumb` (plugin is the source of truth, no `~/.claude` parity).
  **`parallel_conflict_resolver` → deferred to the commit-slice single-skill phase** (shrink: vault-file classes dissolve;
  keep code-conflict hand-resolve). **SHARED-HELPER PORT COMPLETE.**
- **Vault-root scheme CHANGED (v2):** `_vault_paths.py` no longer defaults to in-repo `architecture/`. v2 collapses
  v1's explicit *flip* into the default — `VAULT_ROOT` resolves to the EXTERNAL store
  `~/.aisdlc/<slug>-<sha256(git-common-dir)[:8]>/` (precedence: `$AI_SDLC_VAULT_ROOT` env → `<git-common-dir>/aisdlc/vault-root`
  config pin → computed default; cwd-keyed when not a git tree). Scheme is byte-identical to v1 `tools/_vault_flip.external_store_path`.
  Discoverability: `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path` (or, from the plugin
  root during dev, `$PY -m scripts.lib._vault_paths --path`). (See memory `vault-root-scheme`.)
- **`vault_edit` canonical interface LOCKED** (supersedes the ~16 inconsistent shapes the SKILL.md authors invented):
  subcommands `read` / `get` / `query` / `append` / `update` / `rewrite` / `move` / `list` / `count`, global `--vault`.
  JSON-native `append`/`update` are SVW-1 **locked read-modify-write** (lock serializes concurrent writers). Built on a new
  `_vault_write.safe_mutate_text` locked-RMW primitive. Call-sites get normalized to this one interface.
- **Diagnose/bug-hunt shared trio PROMOTED** ✅ — `write_pass`, `assemble`, `finding_dedup` (.py) + the `finding.yaml`
  schema moved from `skills/diagnose/scripts/` → `scripts/lib/` when `/bug-hunt` became a second consumer (no more
  cross-skill `../diagnose/scripts/` reach). Import chain: `write_pass` → `assemble` → `finding_dedup`, all via the
  `parents[2]` plugin-root bootstrap. `passes/*.md` stay diagnose-only in `skills/diagnose/scripts/`.

## Owed v2 code changes (when porting from temp/tools)
- `vault_edit`: gain a candidates-archive **move** (live `candidates.json` → `archive/candidates.json` on ship).
- all tools: read/write `.json` artifacts (not `.md`).
- any graphify call → `code-review-graph`.
