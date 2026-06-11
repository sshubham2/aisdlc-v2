---
name: drift-check
description: "Audits vault claims against code reality and flags divergence in four categories: DRIFT (blocker), UNSPECIFIED CODE (major), STALE CLAIM (major), and STALE DOC (major — a /product-doc-generated doc that no longer matches the code surface it documented, via the doc-manifest.json provenance anchor). Runs in fast mode (<2s, for the /build-slice pre-finish gate) or full mode (on-demand audit). Appends findings to drift-log.json via the SVW-1 safe channel. Detect-only, all-modes counterpart to the Heavy-mode-only /sync skill."
when_to_use: "Trigger phrases: /drift-check, 'check for drift', 'vault sync check', 'is the vault still accurate', 'audit vault vs code'. Use in --fast mode as the /build-slice pre-finish gate (DCE-1), or on-demand (full mode) before starting a new slice or after external changes. (Not a git pre-commit hook — nothing installs one; hooks/ is SessionStart-only.)"
argument-hint: "[--fast] [--resolve] [path]"
allowed-tools: Read, Grep, Glob, Bash, AskUserQuestion, Skill
---

# /drift-check — Vault vs Code Sync Audit

Compares vault claims against code reality. Runs fast enough for the in-loop `/build-slice` gate and thorough enough for full audits.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git config `aisdlc/vault-root`).

## Live state — injected

Changed files in working tree (for --fast scoping):
```!
git diff --name-only HEAD 2>/dev/null | head -60
```

Active slice folders (skip archive):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" list --vault "$AI_SDLC_VAULT_ROOT" --dir slices/ 2>/dev/null || true
```

## Step 1 — Detect mode

- `--fast`: scope to files changed since last commit (from injected diff above); target <2s; skip deep graph traversal.
- `--resolve`: load existing drift findings, walk user through each interactively (see Step 5).
- `<path>` argument: scope the audit to that component/contract folder.
- Default: full audit (all accepted ADRs + active slices; Heavy mode adds components/contracts/schemas).

## Step 2 — Load vault state

Read only the live-claim surfaces. Always included:

- `<vault>/decisions/ADR-*.json` — status `accepted` only; extract tech/library/approach claims.
- `<vault>/risk-register.json` — risks with `status: retired`; verify retirement is real.
- `<vault>/slices/*/mission-brief.json` — active slices only; check `must_not_defer` items are implemented.
- `<vault>/slices/*/design.json` — active slices only; verify referenced file paths exist and components are touched.
- `<vault>/doc-manifest.json` — **if it exists** (written by `/product-doc`, all modes): the generated docs + the CRG public-surface snapshot each was grounded in. Drives the STALE DOC check (Step 3). Absent → skip the doc audit entirely (no-op for projects that never ran `/product-doc`).

**Skip**: `slices/archive/*` (historical, not live assertions), `slices/_index.json` (metadata), any folder that doesn't exist.

**Heavy mode only** (if `<vault>/components/`, `<vault>/contracts/`, `<vault>/schemas/` exist): include component doc claims, contract endpoint signatures, and schema field claims.

## Step 3 — Verify each claim against code

For each vault claim, check against code reality. Prefer CRG MCP tools when available; fall back to Grep/Read.

**Schema-by-example lint (3.18.7):** also run `artifact_lint` over the in-scope vault artifacts — a
malformed / enum-invalid artifact (e.g. an unknown `verdict`, a missing required key) is vault↔contract drift the
claim checks won't catch. In `--fast` mode scope it to the active slice folder; full mode can sweep the vault:
```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/artifact_lint.py" --dir "$AI_SDLC_VAULT_ROOT/slices/slice-NNN-<name>" --skip-unknown
```
Non-zero → surface each violation as a STALE CLAIM finding (Step 4). Detect-only; `--fast` writes nothing.

| Claim type | Verification |
|---|---|
| ADR chose library `X` | CRG search for `X` in imports/pyproject.toml/package.json; fallback: `Grep "X" pyproject.toml` |
| ADR chose framework `Y` | CRG search for framework imports; fallback: Grep top-level imports |
| Slice design references `src/foo.py` | Check file exists (Glob); CRG impact-radius if available |
| Risk `R-NN` marked retired | Read the spike evidence; check the guard/code is present |
| Must-not-defer item (e.g. auth on POST /X) | CRG search for auth middleware on the route; fallback: Grep route handler |
| Heavy: contract endpoint signature | CRG search for the handler; compare param/return types |
| Heavy: schema field | Grep model/schema definition for field name |
| Doc-manifest tracked doc | doc file still exists; each `grounded_in` CRG node still resolves; `public_surface` snapshot still matches current CRG |

For each mismatch capture: vault file path, vault claim text, code evidence, severity.

**Doc-vs-code (STALE DOC).** If `<vault>/doc-manifest.json` exists, for each entry in its `docs[]`: (a) confirm the
doc file still exists on disk; (b) re-resolve each `grounded_in` CRG node — a documented command / endpoint / export
that no longer resolves means the doc describes something gone; (c) diff the manifest's `public_surface` snapshot
against the current CRG surface — a removed/renamed public symbol means the README / API-reference likely document a
vanished interface. Each mismatch → a **STALE DOC** finding citing the doc path + the specific vanished symbol, with
`Resolve: regenerate via /product-doc`. No manifest → skip this check (no-op).

In `--fast` mode: only check claims in files touched by the injected diff. Skip deep graph traversal.

## Step 4 — Classify findings

- **DRIFT (blocker)** — vault says X, code does Y. Must pick one: update vault or fix code.
- **UNSPECIFIED CODE (major)** — code does X, vault doesn't mention it. Either scope creep or missing ADR.
- **STALE CLAIM (major)** — vault mentions a removed feature/library/file. Delete or supersede.
- **STALE DOC (major)** — a `/product-doc`-generated doc (README / API-reference / user-guide, per `doc-manifest.json`) documents a code surface that no longer matches reality. Regenerate via `/product-doc`. (Skipped when no `doc-manifest.json` exists.)

## Step 5 — Output

### `--fast` mode (build-gate / on-demand)

Print to stdout. Exit 0 if clean, 1 if blockers, 2 if warns only. Format:

```
DRIFT BLOCKING COMMIT:

[BLOCKER] decisions/ADR-008.json claims WebSocket transport
          but src/transport.py uses SSE (EventSource)
          Resolve: /drift-check --resolve  OR  fix code  OR  supersede ADR

[WARN]    decisions/ADR-012.json claims pyheif for HEIC decoding
          but pyproject.toml has no pyheif dependency (removed)
          Resolve: update ADR or restore dependency

To bypass (NOT RECOMMENDED): git commit --no-verify
```

Do NOT write `drift-log.json` in `--fast` mode — stdout only.

### Full mode (audit)

Build an audit entry matching the bundled example (schema: `examples/drift-log.json`, schema id `aisdlc/drift-log@1`).

Build each entry with `build_entry.py` and append it via the SVW-1 stdin channel (never
whole-file overwrite):

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/build_entry.py" \
    --category <drift|unspecified-code|stale-claim|stale-doc> \
    --finding "<finding>" --trigger slice-NNN [--resolution "<how resolved>"] \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --file drift-log.json --array entries --stdin
```

`build_entry.py` stamps the timestamp, canonicalizes the `slice-NNN` trigger (DCE-1), and
omits empty fields. (Prefer `build_entry.py … --out <file>` + `vault_edit append
--content-file <file>` if you want a temp file instead of the pipe.)

**Record the gate outcome (measurement spine — one row per slice, roadmap Theme 8 / plan Phase 0).**
Full mode ONLY — `--fast` writes nothing (stdout-only, no side effects; do NOT log there). Skip when run
standalone with no active slice (a slice-less audit has no per-slice precision to record). `drift-check`
is a **medium** reality-contact gate (claims vs real code); `gate_log.py` stamps that:

```bash
# verdict: clean | drift (any BLOCKER) | warn (warns only); findings-count = drift + unspecified-code + stale-claim + stale-doc
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate drift-check --slice slice-NNN \
    --verdict <clean|drift|warn> --findings-count <N> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

Print a 2-line summary: finding counts + whether the audit is clean.

### `--resolve` mode (interactive)

For each finding, present an `AskUserQuestion` gate:

```
Finding [DRIFT blocker]: ADR-008.json claims WebSocket; src/transport.py uses SSE.

Options:
  [1] Update vault — supersede ADR-008 (code is correct)
  [2] Fix code — create /slice "fix-transport-to-websocket"
  [3] Accept drift — log rationale (intentional; planned reconciliation)
```

Execute the chosen action:
- **Update vault**: edit the relevant JSON file via `vault_edit`; commit with the code change.
- **Fix code**: invoke `/slice "fix drift: <area>"` via the Skill tool — do NOT silently fix; track it.
- **Accept drift**: append an entry via `build_entry.py --category <cat> --finding "<f>" --trigger slice-NNN --action accept-drift --rationale "<rationale incl. next-action slice>"` piped to `vault_edit append --file drift-log.json --array entries --stdin`. `build_entry` fail-closes if `accept-drift` has no `rationale`.

## Step 6 — Heavy mode: cross-spec parity (CSP-1)

In Heavy mode only, after the main audit, run:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/cross_spec_parity_audit.py" --root .
```

This validates `Implementation:` / `Verification:` cross-references in `threat-model.json`, `requirements.json`, `non-functional.json` against real file paths. Findings are appended to `drift-log.json` via the same `scripts.lib.vault_edit append --array entries` channel. This is complementary to `/sync`'s Step 3b — `/drift-check` is fast and in-loop-friendly; CSP-1 is per-artifact-deep.

## Critical rules

- DO NOT silently fix code drift. Even small fixes go through `/slice` for traceability.
- IN `--fast` MODE: <2s budget. Skip graph rebuilds and deep schema diffs. File existence + key imports only.
- IN FULL MODE: deep checks acceptable; up to 30s for Heavy mode.
- ACCEPT DRIFT entries MUST include a `rationale` with a planned reconciliation slice. Periodic audits of `drift-log.json` catch accumulation.
- DO NOT write `drift-log.json` in `--fast` mode (stdout only, no side effects).
- Trigger field in `drift-log.json` entries: use canonical dashed form `slice-NNN` (the DCE-1 audit tool matches on this pattern).

## /sync vs /drift-check

`/drift-check` is **detect-only** and works in **all modes** (Minimal / Standard / Heavy). Its `--fast` mode is the `/build-slice` pre-finish gate (DCE-1).

`/sync` is **bidirectional** (regenerates code-derived vault files) and **Heavy mode only**. Use `/drift-check` for the fast in-loop gate; use `/sync` periodically for deeper reconciliation including component/contract/schema regeneration.

## Pipeline position

- predecessor: invoked by `/build-slice` (pre-finish gate, `--fast`); also standalone on-demand (full mode)
- successor: none (`hands_off_to: []`) — clean means continue; blockers mean resolve via `--resolve` or create a fix slice
- auto-advance: no (detect-only; user decides resolution path)
- user-input gates: `--resolve` mode gates on each finding (AskUserQuestion per finding)
