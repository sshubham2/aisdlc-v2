---
name: sync
description: "Heavy-mode-only bidirectional vault-code reconciliation. Regenerates code-derived vault files (components/*.json from AST, contracts/*.json from OpenAPI/annotations, schemas/*.json from data models), detects drift in human-authored vault files, runs the CSP-1 cross-spec parity audit, and appends a dated sync record to sync-log.json. Interactively walks user through each drift finding."
when_to_use: "Trigger phrases: /sync, 'sync vault and code', 'regenerate component docs', 'reconcile architecture'. Heavy mode only — Standard/Minimal users use /drift-check instead. Run every 5-10 slices, after major refactors, before a release/audit, or when /drift-check reports many findings. Flags: --dry-run (show diff, no write), --regen-only (skip drift detection), --check-only (detect only, no regen), [path] (scope to one component/contract)."
argument-hint: "[--dry-run] [--regen-only] [--check-only] [path]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion, Skill
---

# /sync — Heavy-mode vault-code reconciliation

**Heavy mode only.** Two jobs: (1) regenerate code-derived vault JSON fresh from code AST/OpenAPI/type defs; (2)
detect drift in human-authored vault claims. Interactive drift-resolution gate in Step 5.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).

## Live mode check — injected

```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" get --file triage.json --path .mode 2>/dev/null || echo "UNRESOLVABLE"
```

If the injected mode is not `heavy` → **STOP**: print "sync is Heavy-mode only — use /drift-check instead" and exit.

> **Boundary vs `/drift-check`:** `/drift-check` is the every-slice, all-modes detective audit (read-only
> findings + optional `--resolve`; never regenerates). `/sync` is the Heavy-only reconciler that additionally
> REGENERATES the code-derived vault files. Both write `drift-log.json` through the same `build_entry.py`
> shape, so either skill's entries fold in `/drift-check --status`.

## Argument modes

| Flag | Behavior |
|---|---|
| _(none)_ | Full sync: regen + drift detect + CSP-1 |
| `--dry-run` | Show diff plan; do not write any files |
| `--regen-only` | Regenerate code-derived files only; skip drift + CSP-1 |
| `--check-only` | Drift + CSP-1 only; skip regeneration |
| `[path]` | Scope to one component / contract folder |

## Step 1 — Build code graph (CRG)

```bash
"${CRG:-code-review-graph}" build --repo .
```

Produces `.code-review-graph/` — AST-level structure (classes, functions, endpoints, imports, types). All
regeneration and drift detection in Steps 2–3 queries this graph via `code-review-graph` MCP tools.

If `--check-only` is active: skip this step.

## Step 2 — Regenerate code-derived vault JSON

Skip if `--check-only`.

For each EXISTING target file, DERIVE only the whitelisted code-derived fields (per artifact type below) into
a per-file `<dir>__<name>.derived.json` under a per-run temp dir — do **NOT** hand-write the merged file. The
merge that preserves human-authored fields is **MECHANICAL**: `sync_merge.py` (Steps 4/5a) replaces ONLY
whitelisted derived keys and exits 2 on a derived file that touches anything else — so a slip cannot destroy
human-authored content (these are create-semantics files with no CAS/append channel to catch a bad Write).
Only a **NEW** artifact (no existing vault file) is authored fresh via raw Write — nothing human-authored
exists there to protect; `sync_merge` refuses a missing base by design.

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
S="$(mktemp -d "$TMPD/aisdlc-sync.XXXXXX")"
echo "sync derived-content dir: $S"   # NOTE this path -- later steps run in FRESH shells; reuse the SAME dir
```

### Components — `<vault>/components/<name>.json`

Schema by example: `examples/component.json` (`aisdlc/component@1`).

For each component:
- Locate corresponding source module (via `implements_in` field in existing JSON, or name-match).
- Use CRG AST to re-derive: `public_surface[]`, `depends_on[]`.
- Write ONLY those keys to `"$S/components__<name>.derived.json"`.
- (`responsibility`, `failure_modes` — human-authored markdown strings — never appear in a derived file;
  `sync_merge` carries them through from the existing artifact.)

### Contracts — `<vault>/contracts/<name>.json`

Schema by example: `examples/contract.json` (`aisdlc/contract@1`).

For HTTP contracts:
- Parse `openapi.json` if present, or use CRG endpoint scan (route definitions / annotations).
- Re-derive: `endpoints[]` (method, path, request, response) → `"$S/contracts__<name>.derived.json"`.

For event contracts:
- Find publisher/subscriber annotations in code via Grep (`@publish`, `@subscribe`, event bus calls).
- Re-derive: `event`, `payload_schema`, `delivery_guarantee` → same derived-file shape.

(`notes` — auth model, idempotency, rate-limits, ordering, retry, dead-letter commentary — is human-authored;
never in a derived file.)

### Schemas — `<vault>/schemas/<name>.json`

Schema by example: `examples/schema.json` (`aisdlc/schema@1`).

- Find data-model code (SQLAlchemy / Prisma / Pydantic / Drizzle / Zod) via Glob + Grep.
- Re-derive: `fields[]`, `constraints[]` → `"$S/schemas__<name>.derived.json"`.
- (`state_diagram` — the Mermaid state-transition string — is human-authored; never in a derived file.)

## Step 3 — Vault-to-code drift detection

Skip if `--regen-only`.

Check these classes of drift (for each mismatch: record `{ claim, reality, file }`):

1. **ADR library claims** — for each `<vault>/decisions/ADR-*.json`: is the ADR-chosen library still in
   `pyproject.toml` / `package.json` / `requirements.txt`? Use Grep on dependency files.
2. **ADR approach drift** — is the ADR-chosen architecture pattern still reflected in code structure?
   Use CRG blast-radius to verify.
3. **Risk-register retired claims** — for each entry in `<vault>/risk-register.json` with `status: retired`:
   confirm the mitigation path still exists in code.
4. **Threat-model component existence** — for each component in `<vault>/threat-model.json`: does the
   corresponding code module still exist? Use Glob.
5. **Slice design path references** — for referenced file paths in recent `<vault>/slices/slice-NNN/design.json`
   files: confirm they exist on disk. _(Note: design.json is not listed in the manifest reads[] — this step
   reads it at runtime as an unlisted supplementary source; the manifest read[] list covers only the primary
   drift sources.)_

### Step 3b — CSP-1 cross-spec parity audit

Skip if `--regen-only`.

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/cross_spec_parity_audit.py" --root .
```

Validates every item in `threat-model.json`, `requirements.json`, `non-functional.json`:
- `Status` field present and in allowed vocabulary (TM: mitigated/accepted/open; REQ: implemented/pending/deferred; NFR: met/unmet/unverified).
- For statuses that imply real implementation (mitigated/implemented/met): `Implementation` or `Verification`
  field references a path that exists on disk.
- Refusal semantics: `missing-field`, `invalid-status`, `missing-ref`, `broken-ref`.

Surface any CSP-1 violations in the Step 4 diff. Do NOT write regenerated artifacts past violations in
human-authored Heavy files — ask the user to fix references or change status first.

## Step 4 — Present diff (always, even with --dry-run)

Build each merged preview mechanically (vault untouched — `--out-file`, not `--write`), then diff it against
the existing artifact to fill the "Regenerate" lines:

```bash
S="<the per-run dir path printed in Step 2>"   # fresh shell -- reuse the SAME $S (it holds the derived files)
$PY "${CLAUDE_SKILL_DIR}/scripts/sync_merge.py" --file components/<name>.json \
    --derived-file "$S/components__<name>.derived.json" --out-file "$S/components__<name>.merged.json"
```

(One call per target file; exit 2 here = the derived file is malformed or touches a human-authored key —
fix the derivation BEFORE showing the plan.)

Show a grouped plan:

```
SYNC PLAN

Regenerate (auto, safe):
  components/orders.json  — public_surface: 3 new functions added
  contracts/webhook.json  — POST /webhook/stripe response schema updated
  schemas/user.json       — added field last_login_at

Drift detected (need decision):
  ADR-008 — chose sendgrid; pyproject.toml now has resend
  risk-register R-3 — marked retired; mitigation path src/queue.py missing
  threat-model TM-04 — references component auth-service; no such module found

CSP-1 violations (fix before writing):
  requirements.json REQ-12 — status: implemented but Implementation field is empty

Preserved (human-authored, untouched):
  components/orders.json  — responsibility, failure_modes
  contracts/webhook.json  — notes (auth model, idempotency)
```

If `--dry-run`: print this plan and **stop here** (do not write anything).

## Step 5 — Execute + interactive drift resolution

Skip execution if `--dry-run`.

### 5a — Apply regenerated content (auto)

For each EXISTING file in the "Regenerate" list, apply via the mechanical merge (never a raw Write):

```bash
S="<the per-run dir path printed in Step 2>"   # fresh shell -- reuse the SAME $S
$PY "${CLAUDE_SKILL_DIR}/scripts/sync_merge.py" --file components/<name>.json \
    --derived-file "$S/components__<name>.derived.json" --write
```

Exit 2 = refusal (derived file touches a non-whitelisted key, or base missing) — STOP and re-derive; never
fall back to a raw Write of an EXISTING artifact. NEW artifacts (no existing file) are the one raw-Write case.
No user gate needed for regenerated content. After the last apply: `rm -rf "$S"`.

### 5b — Resolve drift findings (interactive gate)

For each drift finding, present an `AskUserQuestion` with three options:

1. **Update vault** — code is right; update the vault claim to match code.
2. **Fix code** — vault is right; invoke `/slice` via the Skill tool to address the discrepancy.
3. **Accept drift** — intentional; build the entry with the SHARED `build_entry.py` (the ONE
   `drift-log.json` entry shape — `/drift-check --status` folds it; a hand-rolled
   `{at, claim, reality, rationale}` shape is permanently UNFOLDABLE to that reader), then append via
   the SVW-1 channel:
   ```bash
   $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/build_entry.py" \
       --category <drift|stale-claim> \
       --finding "claim: <claim> -- reality: <reality>" \
       --trigger sync --action accept-drift \
       --rationale "<user-supplied rationale incl. next-action slice>" \
     | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
           --file drift-log.json --array entries --stdin
   ```
   Category per drift class: ADR library / ADR approach divergence → `drift`; retired-risk mitigation,
   threat-model component, and design path-ref claims that no longer hold → `stale-claim`.
   `build_entry` fail-closes if `accept-drift` has no rationale.

Walk each finding in sequence. Record each resolution.

### 5c — Resolve CSP-1 violations

For each CSP-1 violation: present an `AskUserQuestion` — fix the reference field or change the status.
Do not write regenerated artifacts until all CSP-1 violations in human-authored files are resolved.

## Step 6 — Append sync record to sync-log.json

Schema by example: `examples/sync-log.json` (`aisdlc/sync-log@1`).

Route through `vault_edit` (SVW-1 — append-only file):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file sync-log.json --array runs --json '{
  "at": "<ISO-8601-timestamp>",
  "mode": "<full|dry-run|regen-only|check-only>",
  "regenerated": ["<list of files written>"],
  "drift": [{"claim":"<claim>","reality":"<reality>","action":"<resolution>"}],
  "csp1": {"violations": <N-found>, "resolved": <N-resolved-in-5c>, "kinds": ["<missing-field|invalid-status|missing-ref|broken-ref>", "..."]}
}'
```

(`csp1` makes the record a complete audit of the run — omit it only when CSP-1 was skipped
(`--regen-only`), never write it as null.)

Skip if `--dry-run`.

## Regenerate vs preserve table

| Section | Source | Action |
|---|---|---|
| `public_surface`, `depends_on` | Code AST | Regenerate |
| `responsibility`, `failure_modes` | Human | Preserve |
| `endpoints[]` | OpenAPI / route annotations | Regenerate |
| `notes` (auth, idempotency, rate-limits) | Human | Preserve |
| `fields[]`, `constraints[]` | Data model | Regenerate |
| `state_diagram` | Human | Preserve |
| ADR rationale | Human | Drift-check only |
| Threat model | Human | Drift-check only |

## Critical rules

- VERIFY Heavy mode first (injected check above). Do NOT run in Standard/Minimal.
- PRESERVE all human-authored string fields. Regeneration touches only derived array/object fields — and
  ONLY through `sync_merge.py` for existing artifacts (its whitelist is the mechanical form of the table
  below; a refusal means re-derive, never raw-Write over an existing file).
- DO NOT silently fix code drift. Every drift finding requires an explicit user decision (Step 5b).
- DO NOT auto-delete ADRs if a library is removed — mark superseded; link a new ADR.
- DO NOT apply regenerated content past unresolved CSP-1 violations in human-authored files.
- ALWAYS append to sync-log.json via `vault_edit` (SVW-1). Never overwrite the whole file.
- SHOW the diff (Step 4) before any writes, even when not `--dry-run`.

## Pipeline position

- predecessor: any slice (periodic cadence: every 5-10 slices), or `/drift-check` (escalation path), or pre-release
- successor: `/slice` (continue normal loop) or `/reduce` (if many drift findings warrant simplification)
- auto-advance: false — this is a maintenance skill; user decides next step after sync completes
- user-input gates: Step 5b drift resolution (one `AskUserQuestion` per drift finding); Step 5c CSP-1 violation resolution
