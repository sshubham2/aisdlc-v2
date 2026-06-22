---
name: archive
description: "Archive completed slices (those with reflection.json) from slices/ to slices/archive/ and regenerate both _index.json files. In normal flow /reflect auto-archives each slice; /archive handles batch cleanup and stale index rebuilds. Index regeneration is dispatched to a Haiku subagent for cost efficiency (COST-1); the main thread owns the compare-and-swap write via vault_edit rewrite."
when_to_use: "Trigger phrases: /archive, 'archive completed slices', 'rebuild slice index', 'regenerate index'. Use after a /reflect was interrupted, after manual slice moves, after fresh clone (--index-only), or to batch-sweep stuck completed slices."
argument-hint: "[--index-only]"
allowed-tools: Read, Glob, Bash, Write, Agent
context: fork
agent: general-purpose
---

# /archive — Slice Archival + Index Maintenance

Maintenance skill. Archive completed slices and keep the two `_index.json` files accurate.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / `aisdlc/vault-root` git config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Live state — injected

Active slice folders (non-recursive):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" list --dir slices/ --vault "$AI_SDLC_VAULT_ROOT" 2>/dev/null || echo "(vault unavailable)"
```

Archived slice count:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" list --dir slices/archive/ --vault "$AI_SDLC_VAULT_ROOT" --count 2>/dev/null || echo "(unavailable)"
```

## Argument modes

- `/archive` — full sweep: move any slice with `reflection.json` from `slices/` to `slices/archive/`, then regenerate both index files.
- `/archive --index-only` — rebuild both `_index.json` files without moving anything. Use when: indexes are stale, files were moved manually, after fresh clone, or a prior `/archive` failed after the move but before the write.

## Prerequisite check

`<vault>/slices/` must exist. If missing, stop with: "Archive aborted: `<vault>/slices/` not found."

## Step 1 — Enumerate current state

Scan `<vault>/slices/` (non-recursive) using Glob:

- Folders **without** `reflection.json` → active (correct).
- Folders **with** `reflection.json` → violators (should be in archive).

Also scan `<vault>/slices/archive/` to count existing archived slices.

If `--index-only`: skip to Step 3.

## Step 2 — Sweep violators to archive

For each violator folder in `slices/`:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" move \
    --from slices/<slice-folder> \
    --to slices/archive/ \
    --vault "$AI_SDLC_VAULT_ROOT"
```

- Run per-slice (not batched). Preserves folder contents exactly — archive is `mv`, never `rm`.
- **Collision guard**: if `slices/archive/<same-name>/` already exists → stop and report; never overwrite. User must resolve manually.
- After all moves: "Archived N slices to `slices/archive/`."

## Step 3 — Regenerate `slices/_index.json` + `slices/archive/_index.json`

Index regeneration runs via a **Haiku subagent** (COST-1 — pure file reading + table assembly, no synthesis). The **main thread owns the CAS write**; the subagent is a pure content generator (ADR-088).

### 3a — Capture CAS bases (main thread, before dispatch)

```bash
# slice-026: portable PER-RUN temp dir. $TMPD is the dir the bundled Windows-Python tools resolve via
# tempfile.gettempdir(), so a git-bash write + a Windows-Python read land on the SAME real path (a
# hardcoded /tmp/... diverges on Windows). The SAME $PY on both sides keeps it self-consistent. The
# per-run mktemp -d dir means two concurrent /archive (or /reflect auto-archive) runs never collide.
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
D="$(mktemp -d "$TMPD/aisdlc-archive.XXXXXX")"
echo "archive CAS temp dir: $D"   # NOTE this path -- Step 3c is a FRESH shell that won't keep $D; reuse the SAME dir there

$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read \
    --file slices/_index.json \
    --out-file "$D/idx_base.bin" \
    --vault "$AI_SDLC_VAULT_ROOT"

$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read \
    --file slices/archive/_index.json \
    --out-file "$D/archive_base.bin" \
    --vault "$AI_SDLC_VAULT_ROOT"
```

Use `--out-file`, NOT shell `>` (PowerShell `>` emits UTF-16LE+BOM → CAS livelock). One base file per target; never reuse across the two distinct files.

If either file does not exist yet, write an empty placeholder first via `vault_edit` so the CAS base is established.

### 3b — Dispatch Haiku subagent

Invoke the Agent tool (`subagent_type: "general-purpose"`, `model: haiku`). Hand it:

- The list of active slice folders in `slices/` and all archived folders in `slices/archive/`.
- Instruction to read each folder's `mission-brief.json` (for `intent` field, first sentence — the one-liner).
- `<vault>/concept.json` (for project name) and `<vault>/triage.json` (for mode).
- The output shapes for both `_index.json` files (see schemas below).

The agent returns two JSON content strings: one for `slices/_index.json`, one for `slices/archive/_index.json`.

### 3c — Write via CAS (main thread)

Reuse the SAME per-run dir `$D` from Step 3a (a fresh shell does NOT keep `$D` — set `D=<the path Step 3a printed>`, which holds the captured bases). Write each content string to `$D/idx_new.json` and `$D/archive_new.json`, then:

```bash
D="<the per-run dir path printed in Step 3a>"   # fresh shell -- reuse the SAME $D from 3a (it holds the captured bases)
[ -f "$D/idx_base.bin" ] || { echo "STOP: set D to the dir Step 3a printed -- it holds the CAS bases (got '$D')" >&2; exit 1; }   # code-review m2: clear error instead of a deep vault_edit failure
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite \
    --file slices/_index.json \
    --base-file "$D/idx_base.bin" \
    --content-file "$D/idx_new.json" \
    --vault "$AI_SDLC_VAULT_ROOT"

$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite \
    --file slices/archive/_index.json \
    --base-file "$D/archive_base.bin" \
    --content-file "$D/archive_new.json" \
    --vault "$AI_SDLC_VAULT_ROOT"
rm -rf "$D"
```

**On exit 3 (CAS conflict)**: a parallel slice completion wrote `_index.json` between base-capture and write. Re-capture the base, re-dispatch the Haiku subagent (so the regen picks up the concurrent row), re-attempt. Bound to ~5 attempts. On persistent conflict: STOP loudly. **Recovery**: Step 2 moves already completed — re-run `/archive --index-only` to redo only the index writes.

## `slices/_index.json` schema: `examples/slice-index.json`

Key fields: `_schema`, `project`, `mode`, `total`, `active_count`, `archived_count`, `updated`,
`active[]` (slice/title/stage), `recent[]` (slice/title/shipped/summary), `action_points_ref`, `archive_ref`.

**THIN-ROUTER CONTRACT (ADR-093):**
- `recent` = EXACTLY the 10 most recent archived slices (or all if < 10).
- Each `summary` is the slice's `mission-brief.json` `intent` field, first sentence, trimmed, ≤ 500 chars. NOT the reflection's full summary paragraph.
- `action-points.json` is **never regenerated or modified by `/archive`** — it is a curated artifact. Only emit a ref pointer.

**Canonical stage-derivation rule** (shared verbatim with `/pulse` Step 2 — keep the two identical): derive
from file presence, checking the **highest** stage first (first match wins). `critique.json` is **OPTIONAL**
(a low-tier slice with no mandatory trigger skips it — 1.1), so `build-log.json` presence decides `build`
regardless of whether `critique.json` exists:

| Highest-present file in slice folder | Stage |
|---|---|
| `reflection.json` | `reflect` (complete) |
| `validation.json` | `validate` |
| `build-log.json` | `build` |
| `critique.json` (only when `build-log.json` is **absent**) | `critique` |
| `design.json` | `design` |
| `mission-brief.json` | `spike` |
| (none / empty dir) | `none` — not started |

## `slices/archive/_index.json` schema: `examples/slice-archive-index.json`

Full chronological catalog of all archived slices (not capped at 10 — that cap is the live index's `recent[]`).
Fields: `_schema`, `total`, `updated`, `slices[]` (slice/title/shipped/summary). Same one-liner source rule as above.

## Step 4 — Summary

Report:

```
Archive sweep complete.
- Active slices: A (in slices/)
- Archived: C (in slices/archive/)
- slices/_index.json regenerated (thin recent-10 + action-points pointer; action-points.json left untouched)
- slices/archive/_index.json regenerated (full catalog)
```

## Critical rules

- **NEVER delete slice folders.** Archive = `mv`, never `rm`. Slice history is audit trail.
- **NEVER touch file contents during archive.** Move + regenerate indexes only.
- **NEVER leave completed slices in `slices/`** (those with `reflection.json`). That breaks the convention.
- **`action-points.json` is untouched** — curated artifact, not regenerated here (ADR-093).
- Both `_index.json` files are always regenerated (unless a CAS conflict halts the run — recoverable via `--index-only`).
- All `_index.json` writes route through `vault_edit rewrite` CAS — never a raw whole-file overwrite (SVW-1 discipline).
- `_index.json` stays a **thin router**: recent-10 one-liners (≤500 chars each, from `mission-brief.json` intent). Never paste reflection summaries or lesson dumps.

## Pipeline position

- predecessor: invoked by `/reflect` (auto, after each slice) — or manually at any time
- successor: `/slice` (next slice, if archival was the trigger for cleanup)
- auto-advance: **no** — maintenance skill, returns to user after completing
- user-input gates: none (fully mechanical); collision on archive move halts for manual resolution
