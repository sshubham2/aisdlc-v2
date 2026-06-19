---
name: product-doc
description: "Generate + maintain product documentation grounded in code reality. CHANGELOG.md is assembled deterministically by merging git history (the plugin.json version cut post-merge by /product-doc; no tags) with the per-slice changelog.json records /commit-slice writes, version-grouped; README / API-reference / user-guide are drafted by a forked product-doc agent from the code-review-graph public surface + the vault (concept, slices), with every interface fact grounded in a real CRG node (unverifiable claims are omitted, never invented). Docs are markdown DELIVERABLES written to the code repo; a doc-manifest.json provenance record is written to the vault so /drift-check can flag docs that drift from code. NEVER modifies source code; gates before overwriting a hand-written doc."
when_to_use: "Trigger phrases: /product-doc, 'generate docs', 'update the README', 'write API reference', 'regenerate CHANGELOG', 'document this project'. Out-of-loop maintenance — user-invokable any time (after shipping slices, before a release, when onboarding docs go stale). NOT auto-wired into the slice loop."
argument-hint: "[--docs readme,changelog,api,guide]  (default: all four)"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, AskUserQuestion
---

# /product-doc — grounded product documentation

Turn **code reality** (code-review-graph) + the **vault** into accurate README / CHANGELOG / API-reference /
user-guide. Docs are user-facing **deliverables written to the code repo** (a deliberate exception to the
JSON-vault rule — product docs are markdown by nature). The only vault artifact is a provenance manifest the
drift loop reads. **This skill never modifies source code.**

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / git
> config `aisdlc/vault-root`).

## Scope — which docs

Default: **all four** (README, CHANGELOG, API reference, user guide). If the user passed `--docs <list>` (comma
list of `readme,changelog,api,guide`), produce only those. CHANGELOG is deterministic (Step 1); the other three
are agent-drafted (Step 2).

## Step 0 — resolve + gather grounding

- Repo root: `git rev-parse --show-toplevel`. Vault: `$AI_SDLC_VAULT_ROOT`.
- **CRG public surface** (the ground truth for interface facts). If `.code-review-graph/` is missing/stale,
  `"${CRG:-code-review-graph}" build` (or `update`). Then harvest the public surface:

Harvest it with the CRG **MCP tool** (live-MCP context): call
`mcp__code-review-graph__semantic_search_nodes_tool` with a query like
`"entrypoint OR export OR cli OR endpoint OR public api"`. (CRG 2.3.x has no `search` CLI verb — it is MCP-only.)

  Capture a compact summary (entry points / exported functions / CLI commands / endpoints) — this is what the agent
  documents and what goes into the manifest's `public_surface`.
- **Vault reads:** `concept.json` (what/why/actors), `triage.json` (mode), `slices/_index.json` (shipped features).
- **Existing docs:** read the current `README.md` + `docs/*` if present — to refresh, not blindly rewrite.

## Step 1 — CHANGELOG (deterministic; skip if not requested)

`CHANGELOG.md` is rebuilt — no model needed — by MERGING the project's **git history** (the `version` field in
`.claude-plugin/plugin.json`) with the per-slice `changelog.json` records `/commit-slice` writes. The output is
**version-grouped** (Keep-a-Changelog `## [x.y.z]` sections), with the per-slice records laid over the versions
they cover.

**The plugin version is cut HERE — AS the deliberate `uat->master` merge — not in the slice commit.** Slice commits
integrate onto `uat` WITHOUT a version bump (so parallel slices never conflict on the plugin.json `version` line);
`/product-doc`'s release cut (`release_cut.py`) bumps the version once as it merges `uat` into the released `master`,
and rolls every unreleased commit (the *open period* — everything merged into uat since the last version-change)
forward onto it.

**Sub-step 1a — the atomic release cut (`release_cut.py`).** Under the uat/master model (slice-022) the
`uat->master` merge IS the version cut, and `release_cut.py` performs it ATOMICALLY — it is the **ONLY** path that
advances `master` (AC4). The human/release supplies the target (`--new-version X.Y.Z`, the primary form, or
`--level patch|minor|major`):

```bash
repo_root="$(git rev-parse --show-toplevel)"
$PY "${CLAUDE_SKILL_DIR}/scripts/release_cut.py" --confirmed \
    --repo-root "$repo_root" --vault "$AI_SDLC_VAULT_ROOT" \
    --new-version "${TARGET:?supply the release version, e.g. 2.36.0}" --json
```

`release_cut.py` (slice-022): REFUSES on a dirty target tree (B2); treats a uat-not-ahead state as a clean
**no-op** (idempotent re-run — M2); else CAPTURES the pre-merge `master` SHA, stages `git merge --no-ff --no-commit uat`,
runs `bump_plugin_version.py` (refuses a non-increasing bump / malformed manifest; no-op at-target — M4) +
`assemble_changelog.py` (open-period grouping) into the worktree, then lands the merge + bump + changelog as **ONE
commit** (the atomic boundary), and finally syncs `uat` back to the new release. On ANY pre-commit failure it does
`git reset --hard <captured-SHA>` so `master` is byte-identical (the `merge --abort`-alone gap, proven by
spike-release-cut-atomicity, is why the cleanup is an explicit reset). **`/product-doc` fails visibly if the target
version cannot be determined (no silent skip).** Read the verdict JSON's `action` — `released` (cut landed), `no-op`
(nothing to release), or a `refuse-*` / `*-failed` reason (master untouched). After a `released` action, verify the
integrity invariant with `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/release_advance_audit.py" --root "$repo_root"`
(asserts `master` advanced only via versioned cuts since the recorded `release-genesis`).

**Standalone CHANGELOG regen (no release).** To refresh `CHANGELOG.md` WITHOUT advancing `master` (a docs-only
run), invoke `assemble_changelog.py` directly — it reads committed git history + the per-slice records, never bumps
plugin.json and never merges:

```bash
repo_root="$(git rev-parse --show-toplevel)"   # fresh shell — re-derive (vars don't cross ```bash blocks)
new_version="$($PY -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$repo_root/.claude-plugin/plugin.json")"
$PY "${CLAUDE_SKILL_DIR}/scripts/assemble_changelog.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root "$repo_root" --new-version "$new_version" --out "$repo_root/CHANGELOG.md"
```

If there is genuinely no unreleased work, `--new-version` may be omitted and assemble degrades to spot attribution;
with unreleased non-merge commits and no `--new-version`, assemble **exits 2 (fail-visible)**.

> **First cut under the uat/master model (slice-022):** `uat` is established from `master@2.35.1` (the recorded
> `release-genesis`); the first `release_cut.py` run merges `uat` into `master` and bumps from `2.35.1` to the chosen
> next version, rolling the open-period commits forward onto it.

> **M1 note (artifact version stamps under post-merge bump):** a vault artifact's `_plugin_version` stamp now
> records *the plugin version present when the artifact was written* — which, because the bump happens after merge,
> is no longer `==` the about-to-ship version (the artifact is written at the OLD version, then the release cuts the
> new one). This is benign: `artifact_lint`'s skew check only WARNs when an artifact's stamp is *newer* than the
> running plugin, and a post-merge bump makes stamps *older* (or equal), never newer.

CHANGELOG.md is a generated PROJECTION recomputed in full each run (its header says "do not hand-edit —
regenerate") and never read back, so a re-run is byte-identical and a happy-path overwrite is safe — no gate.
**Degraded run (exit 3):** if git history is unavailable / shallow / the repo has no commits, the script falls
back to a slice-records-only render and, rather than shrink a populated `CHANGELOG.md`, **refuses to overwrite an
existing file** (exit 3, file left untouched) — restore git history and regenerate. An empty archive + working git
still writes a valid version-grouped CHANGELOG.

## Step 2 — draft README / API-reference / user-guide (forked agent)

For the requested agent-docs, spawn the **`product-doc`** agent via the **Agent tool**
(`subagent_type: "product-doc"`). The persona carries the anti-hallucination mandate + output schema — do NOT
re-state them. Pass only inputs:

```
Requested docs: <readme | api-reference | user-guide subset>

# CRG public surface
<your Step 0 surface summary>

# Vault context
concept.json: <contents>
triage.json mode: <minimal|standard|heavy>
slices/_index.json: <contents>

# Existing docs (refresh, don't bulldoze)
README.md: <current contents, or "none">
docs/*: <current contents, or "none">
```

**Await the real agent — never fabricate doc content.** It returns one `aisdlc/product-doc-draft@1` JSON object
(`readme` / `api_reference` / `user_guide` markdown + `grounding` + `ungrounded_claims_omitted`). Surface its
`ungrounded_claims_omitted` to the user — those are real gaps (code the agent couldn't verify), not oversights.

## Step 2.5 — verify the agent's grounding against reality (slice-015 / ADR-011)

The agent SELF-ATTESTS its `grounding`; **never write it verbatim** — independently re-derive each token against
reality first, so a hallucinated flag can't ship with false provenance. Ensure the code map is fresh
(`"${CRG:-code-review-graph}" update` — M2: a stale graph false-rejects a just-merged symbol), then run the
deterministic verifier (no model judges the model — exact membership only):

```bash
repo_root="$(git rev-parse --show-toplevel)"
# M2: guard the vault root — an unset/empty $AI_SDLC_VAULT_ROOT must NOT pass "" (the verifier would
# then be unable to resolve vault: tokens against the right root). Resolve deterministically, fail-visible.
vault_root="${AI_SDLC_VAULT_ROOT:-$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
[ -n "$vault_root" ] || { echo "grounding-verify: vault root unresolved — run /setup or set AI_SDLC_VAULT_ROOT" >&2; exit 1; }
$PY -c "import json,sys; json.dump({'grounding': <agent.grounding>, 'repo_root': sys.argv[1], 'vault_root': sys.argv[2]}, sys.stdout)" "$repo_root" "$vault_root" \
  | $PY "${CLAUDE_SKILL_DIR}/scripts/grounding_verify.py"
```

It returns `{docs: {<doc>: {verified[], grounding_unverified[{token,reason}]}}, grounding_check{ran, crg_reachable,
graph_last_updated, graph_stale, public_surface_verified}}`. Each token is `crg:<repo-rel-path>::<symbol>` /
`file:<repo-rel-path>` / `vault:<path>` (path-based — B1); a token it can't confirm is dropped. **Fail-CLOSED**:
when `crg_reachable` is false (code map unreachable) the affected tokens are `source-unavailable`, NOT silently
passed (AC3). **Surface to the user** the verified vs unverified counts per doc + whether the code map was
reachable and stale — these are real gaps, not noise. (`public_surface` stays Step-0 fuzzy-harvested and
`public_surface_verified: false` — a known, visible unverified anchor; M-add-1.)

## Step 3 — write the docs to the repo (overwrite gate)

Write each requested agent-doc to the repo: `README.md`, `docs/api-reference.md`, `docs/user-guide.md`.

**Overwrite gate (never clobber hand-written docs):** if the target file already exists AND was not produced by a
prior `/product-doc` run (check `doc-manifest.json` — if the path isn't listed there, treat it as hand-written),
show the user a diff and `AskUserQuestion`: **overwrite / skip / let me merge**. A file absent from the manifest +
present on disk = hand-authored; default to NOT overwriting without confirmation. New files: write directly.

Never write a doc the agent returned `null` for or omitted.

## Step 4 — write the provenance manifest (vault)

Write `<vault>/doc-manifest.json` (schema: `examples/doc-manifest.json`) — the anchor `/drift-check` audits:

- `at`, `source_commit` (`git rev-parse --short HEAD`), `public_surface` (the Step 0 snapshot)
- `docs[]` — one entry per doc actually written: `path`, `kind`, `generated_at`, and **`grounded_in` = the Step 2.5
  verifier's `verified[]` ONLY** (never the agent's raw `grounding` — slice-015). Also write the sibling
  `grounding_unverified` (the dropped `{token, reason}` list) + `grounding_check` (`ran`, `crg_reachable`,
  `graph_last_updated`, `graph_stale`, `public_surface_verified`) for that doc, so a degraded/partial verification
  is visible, never silently blended with the solid sources. For the **CHANGELOG** doc, `grounded_in` stays
  `["git:.claude-plugin/plugin.json@history", "vault:slices/archive/*/changelog.json"]` (it is deterministic, not
  agent-grounded — not verifier-gated).
- **`/drift-check` Step 3** (`skills/drift-check/SKILL.md`) re-resolves each `grounded_in` token to detect a
  `stale-doc`; because `grounded_in` is now verified-only path tokens, that re-resolution operates on confirmed,
  resolvable sources (B2 — the consumer is drift-check's Step 3, not `build_entry.py`, which only serializes the
  drift-log).

<!-- vault-write-safe: project-open-single-shot -->
This is a single-shot full rewrite each run (not an append-mutated shared file), so a direct `Write` is correct
(SVW-1: single-shot create/overwrite, not the `vault_edit append` class).

## Step 5 — report

Report what was written (repo doc paths + the vault manifest), the `ungrounded_claims_omitted` gaps, and note that
`/drift-check` will now flag any of these docs as `stale-doc` if the documented code surface later changes.

## Critical rules

- **NEVER modify source code.** Only docs (deliverables) + the vault manifest.
- **GROUND every interface fact** — the agent omits what it can't verify; surface the omissions, never paper over them.
- **GATE before overwriting a hand-written doc** (one not in `doc-manifest.json`). New/previously-generated docs: write directly.
- **CHANGELOG is deterministic** (Step 1 script), never agent-authored — git history (the plugin.json `version`, now cut post-merge HERE) + the per-slice records are the source of truth, merged version-grouped and recomputed in full each run (never read back).
- **The version bump lives HERE, post-merge** (Step 1a `bump_plugin_version.py`), NOT in the slice commit — fail visibly if the new version can't be determined; never bump silently.
- **ALWAYS write `doc-manifest.json`** — it is the drift anchor; without it `/drift-check` can't audit the docs.

## Pipeline position

- predecessor: none — out-of-loop, user-invokable any time, all modes.
- successor: none (`hands_off_to: []`). Not auto-wired into the auto-advancing slice loop — but `/commit-slice`
  Step 4.6 now **offers** a `/product-doc --docs changelog` refresh on ship when a `doc-manifest.json` exists
  (roadmap Theme 6 [P3], landed as a non-blocking reminder, not an auto-run). `/drift-check` `stale-doc` consumes the manifest this writes.
- auto-advance: false.
- user-input gates: Step 3 overwrite confirmation for any existing hand-written doc.
