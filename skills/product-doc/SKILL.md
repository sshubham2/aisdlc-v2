---
name: product-doc
description: "Generate + maintain product documentation grounded in code reality. CHANGELOG.md is assembled deterministically from the per-slice changelog.json records /commit-slice writes; README / API-reference / user-guide are drafted by a forked product-doc agent from the code-review-graph public surface + the vault (concept, slices), with every interface fact grounded in a real CRG node (unverifiable claims are omitted, never invented). Docs are markdown DELIVERABLES written to the code repo; a doc-manifest.json provenance record is written to the vault so /drift-check can flag docs that drift from code. NEVER modifies source code; gates before overwriting a hand-written doc."
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

```bash
"${CRG:-code-review-graph}" search "entrypoint OR export OR cli OR endpoint OR public api"
```

  Capture a compact summary (entry points / exported functions / CLI commands / endpoints) — this is what the agent
  documents and what goes into the manifest's `public_surface`.
- **Vault reads:** `concept.json` (what/why/actors), `triage.json` (mode), `slices/_index.json` (shipped features).
- **Existing docs:** read the current `README.md` + `docs/*` if present — to refresh, not blindly rewrite.

## Step 1 — CHANGELOG (deterministic; skip if not requested)

`/commit-slice` already writes one `changelog.json` per shipped slice. Assemble them — no model needed:

```bash
repo_root="$(git rev-parse --show-toplevel)"
$PY "${CLAUDE_SKILL_DIR}/scripts/assemble_changelog.py" --vault "$AI_SDLC_VAULT_ROOT" --out "$repo_root/CHANGELOG.md"
```

CHANGELOG.md is a generated artifact (its header says "do not hand-edit — regenerate"), so overwriting it is safe —
no gate. If the archive is empty it writes a valid minimal CHANGELOG.

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
- `docs[]` — one entry per doc actually written: `path`, `kind`, `generated_at`, `grounded_in` (from the agent's
  `grounding`, or `["vault:slices/archive/*/changelog.json"]` for the CHANGELOG)

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
- **CHANGELOG is deterministic** (Step 1 script), never agent-authored — the per-slice records are the source of truth.
- **ALWAYS write `doc-manifest.json`** — it is the drift anchor; without it `/drift-check` can't audit the docs.

## Pipeline position

- predecessor: none — out-of-loop, user-invokable any time, all modes.
- successor: none (`hands_off_to: []`). NOT auto-wired into the slice loop (auto-maintain-on-ship is deferred,
  roadmap Theme 6 [P3]). `/drift-check` consumes the manifest this writes.
- auto-advance: false.
- user-input gates: Step 3 overwrite confirmation for any existing hand-written doc.
