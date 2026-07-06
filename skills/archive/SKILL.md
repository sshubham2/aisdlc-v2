---
name: archive
description: "Archive completed slices (those with reflection.json) from slices/ to slices/archive/ and regenerate both _index.json files. In normal flow /reflect auto-archives each slice; /archive handles batch cleanup and stale index rebuilds. Index regeneration runs via the deterministic slice_index_regen.py — no subagent (supersedes the former Haiku COST-1 dispatch); the main thread owns the compare-and-swap write via vault_edit rewrite. The sweep also backstops CAND-1: a candidate stranded in both candidates.json files by an interrupted /commit-slice Step 6 is deduped (archive wins)."
when_to_use: "Trigger phrases: /archive, 'archive completed slices', 'rebuild slice index', 'regenerate index'. Use after a /reflect was interrupted, after manual slice moves, after fresh clone (--index-only), or to batch-sweep stuck completed slices."
argument-hint: "[--index-only]"
allowed-tools: Read, Glob, Bash, Write
context: fork
agent: general-purpose
---

# /archive — Slice Archival + Index Maintenance

Maintenance skill. Archive completed slices and keep the two `_index.json` files accurate.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / `aisdlc/vault-root` git config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Live state — injected

Active slice folders (non-recursive):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" list --dir slices/ 2>/dev/null || echo "(vault unavailable)"
```

Archived slice count:
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" list --dir slices/archive/ --count 2>/dev/null || echo "(unavailable)"
```

## Argument modes

- `/archive` — full sweep: move any slice with `reflection.json` from `slices/` to `slices/archive/`, run the candidate both-files backstop (Step 2.5), then regenerate both index files.
- `/archive --index-only` — rebuild both `_index.json` files without moving anything (skips the Step-2 sweep AND the Step-2.5 candidate backstop). Use when: indexes are stale, files were moved manually, after fresh clone, or a prior `/archive` failed after the move but before the write.

## Prerequisite check

`<vault>/slices/` must exist. If missing, stop with: "Archive aborted: `<vault>/slices/` not found."

## Step 1 — Enumerate current state

**If `--index-only`: skip Steps 1–2.5 entirely and go to Step 3** (Step 3b's generator re-scans the
folders itself — the enumeration here would be dead work).

Scan `<vault>/slices/` (non-recursive) using Glob:

- Folders **without** `reflection.json` → active (correct).
- Folders **with** `reflection.json` → violators (should be in archive).

Also scan `<vault>/slices/archive/` to count existing archived slices.

## Step 2 — Sweep violators to archive

**Before any move, print the full violator list** (one line per folder: `will archive: slice-NNN-<name>`).
The sweep has no undo beyond a manual `mv` back — the printed list makes a mistaken sweep visible in the
transcript before N moves happen.

Then, for each violator folder in `slices/`:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" move \
    --from slices/<slice-folder> \
    --to slices/archive/
```

- Run per-slice (not batched). Preserves folder contents exactly — archive is `mv`, never `rm`.
- **Collision guard**: if `slices/archive/<same-name>/` already exists → stop and report; never overwrite. User must resolve manually.
- After all moves: "Archived N slices to `slices/archive/`." (name them — the list feeds Step 4).

## Step 2.5 — Candidate both-files backstop (CAND-1; full sweep only)

`/commit-slice` Step 6 ships a candidate with TWO per-file-locked writes (append the shipped copy to
`archive/candidates.json`, then remove it from live `candidates.json`) — an interrupt between them strands the
candidate in BOTH files (the live copy reads shipped-but-live, which `candidates_top` silently drops from every
bucket). This step is the backstop that `/commit-slice` Step 6 promises: detect ids present in both files and
resolve them — **archive wins**; only the live duplicate is removed. Removal goes via the same CAS-rewrite path
`/commit-slice` uses (`candidates[]` is an id-managed array — `vault_edit remove` refuses it by design; never
touch `archive/candidates.json` here, it is the winner and append-only).

```bash
TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')" || { echo "STOP: cannot resolve a portable temp dir" >&2; exit 1; }
T="$(mktemp -d "$TMPD/aisdlc-archive-cand.XXXXXX")"
# NOTE: `read` on a MISSING file exits 0 with an EMPTY out-file (that IS the CAS base for
# "doesn't exist yet") -- so the missing-file skip lives in the python below, keyed on emptiness,
# NOT on these exit codes (which only catch real errors: unresolvable vault, unwritable $T).
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read --file candidates.json         --out-file "$T/live_base.bin" || { echo "STOP: cannot read candidates.json" >&2; rm -rf "$T"; exit 1; }
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read --file archive/candidates.json --out-file "$T/arch.bin"      || { echo "STOP: cannot read archive/candidates.json" >&2; rm -rf "$T"; exit 1; }
T="$T" $PY -c "
import json, os, pathlib
t = pathlib.Path(os.environ['T'])
def load(p):
    b = p.read_bytes()
    return json.loads(b.decode('utf-8')) if b.strip() else None
live, arch = load(t / 'live_base.bin'), load(t / 'arch.bin')
if live is None or arch is None:
    print(json.dumps({'both_files': [], 'skipped': 'live' if live is None else 'archive'}))
else:
    arch_ids = {c.get('id') for c in arch.get('candidates', []) if c.get('id')}
    dups = [c['id'] for c in live.get('candidates', []) if c.get('id') in arch_ids]
    if dups:
        live['candidates'] = [c for c in live.get('candidates', []) if c.get('id') not in arch_ids]
        (t / 'live_new.json').write_text(json.dumps(live, indent=2) + chr(10), encoding='utf-8')
    print(json.dumps({'both_files': dups}))
"
if [ -f "$T/live_new.json" ]; then
    $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite --file candidates.json --base-file "$T/live_base.bin" --content-file "$T/live_new.json" \
        || { echo "STOP: CAS rewrite failed -- exit 3 = conflict: re-run Step 2.5 from the top (bound ~5 attempts)" >&2; rm -rf "$T"; exit 1; }
    echo "candidate backstop: removed the live copy of the both-files id(s) above (archive wins)"
else
    echo "candidate backstop: clean (no candidate present in both files)"
fi
rm -rf "$T"
```

Report the deduped ids (or "clean") in Step 4. A live copy that differs from the archived copy beyond `status`
still loses — the archive row was written from the shipped state and `shipped` MUST mean committed code.

## Step 3 — Regenerate `slices/_index.json` + `slices/archive/_index.json`

Index regeneration runs via the deterministic **`slice_index_regen.py`** (ADR-020/SC-008 — a pure file-scan generator; no subagent, no model round-trip). The **main thread owns the CAS write**; the generator only produces CONTENT. (This supersedes the former Haiku content-gen subagent for the index artifact — a deterministic scan replaces the COST-1 round-trip, and the index can no longer drift from its schema-by-example.)

> Same regen + CAS recipe as `/reflect` Step 6.2 (its per-slice auto-archive) — the two flows share everything
> but the sweep; a regen/CAS bug fixed here must be fixed there too.

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
    --out-file "$D/idx_base.bin"

$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" read \
    --file slices/archive/_index.json \
    --out-file "$D/archive_base.bin"
```

Use `--out-file`, NOT shell `>` (PowerShell `>` emits UTF-16LE+BOM → CAS livelock). One base file per target; never reuse across the two distinct files.

If either file does not exist yet, write an empty placeholder first via `vault_edit` so the CAS base is established.

### 3b — Generate both index bodies (deterministic; no subagent)

Reuse the SAME per-run dir `$D` from 3a. Run `slice_index_regen.py` to emit both index bodies straight into the content-files 3c rewrites. Pass ONE `--updated` stamp to both emits (the only non-deterministic field — keeps re-runs byte-identical):

```bash
D="<the per-run dir path printed in Step 3a>"   # fresh shell -- reuse SAME $D from 3a (it holds the captured bases)
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# No --vault flag anywhere in 3a/3b/3c: every tool resolves the vault internally via the SAME precedence
# (env -> config pin -> computed), so the generator SCANS the SAME root the CAS write targets (code-review M1).
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_index_regen.py" --emit live    --updated "$TS" --out-file "$D/idx_new.json"
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/slice_index_regen.py" --emit archive --updated "$TS" --out-file "$D/archive_new.json"
```

It scans `slices/` (active) + `slices/archive/` (full catalog) and derives every field from the folders: the one-liner `summary` from each `mission-brief.json` `intent` (first sentence, ≤500 chars), the project from `concept.json` + mode from `triage.json`, the stage from the file-presence rule below — emitting the canonical per-entry shapes, so there is no hand-assembly and no per-entry drift (the conformance test `tests/test_slice_index_regen.py` enforces this).

### 3c — Write via CAS (main thread)

Reuse the SAME per-run dir `$D` from Step 3a (a fresh shell does NOT keep `$D` — set `D=<the path Step 3a printed>`, which holds the captured bases AND the `idx_new.json` / `archive_new.json` that 3b emitted). Then CAS-rewrite both:

```bash
D="<the per-run dir path printed in Step 3a>"   # fresh shell -- reuse the SAME $D from 3a (it holds the captured bases)
[ -f "$D/idx_base.bin" ] || { echo "STOP: set D to the dir Step 3a printed -- it holds the CAS bases (got '$D')" >&2; exit 1; }   # code-review m2: clear error instead of a deep vault_edit failure
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite \
    --file slices/_index.json \
    --base-file "$D/idx_base.bin" \
    --content-file "$D/idx_new.json"

$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" rewrite \
    --file slices/archive/_index.json \
    --base-file "$D/archive_base.bin" \
    --content-file "$D/archive_new.json"
rm -rf "$D"
```

**On exit 3 (CAS conflict)**: a parallel slice completion wrote `_index.json` between base-capture and write. Re-capture the base and re-run `slice_index_regen.py` (it re-scans the folders, picking up the concurrent row), re-attempt. Bound to ~5 attempts. On persistent conflict: STOP loudly. **Recovery**: Step 2 moves already completed — re-run `/archive --index-only` to redo only the index writes.

## `slices/_index.json` schema: `examples/slice-index.json`

Key fields: `_schema`, `project`, `mode`, `total`, `active_count`, `archived_count`, `updated`,
`active[]` (slice/title/stage), `recent[]` (slice/title/shipped/summary), `action_points_ref`, `archive_ref`.

**THIN-ROUTER CONTRACT (ADR-093):**
- `recent` = EXACTLY the 10 most recent archived slices (or all if < 10).
- Each `summary` is the slice's `mission-brief.json` `intent` field, first sentence, trimmed, ≤ 500 chars. NOT the reflection's full summary paragraph.
- `action-points.json` is **never regenerated or modified by `/archive`** — it is a curated artifact. Only emit a ref pointer.

**Canonical stage-derivation rule** — the single source of truth is the CODE:
`scripts/lib/slice_index_regen.py:_derive_stage` (what Step 3b actually runs). This table and `/pulse`
Step 2's are RENDERINGS of that function — fix the function first, then re-render both tables. Derive
from file presence, checking the **highest** stage first (first match wins). `critique.json` is **OPTIONAL**
(a low-tier slice with no mandatory trigger skips it — 1.1), so `build-log.json` presence decides `build`
regardless of whether `critique.json` exists:

| Highest-present file in slice folder | Stage |
|---|---|
| `reflection.json` | `complete` (the legacy `reflect` label is out-of-enum — slice-030 m1) |
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
- Moved: N (slice-..., slice-...) — or "none"
- Active slices: A (in slices/)
- Archived: C (in slices/archive/)
- Candidate backstop: clean | deduped SC-... (archive won) | skipped (--index-only / file missing)
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
- **Step 2.5 never edits `archive/candidates.json`** — archive wins; only the live duplicate is removed, via CAS rewrite (never `vault_edit remove` — `candidates[]` is an id-managed array it refuses).
- `_index.json` stays a **thin router**: recent-10 one-liners (≤500 chars each, from `mission-brief.json` intent). Never paste reflection summaries or lesson dumps.

## Pipeline position

- predecessor: invoked by `/reflect` (auto, after each slice) — or manually at any time
- successor: `/slice` (next slice, if archival was the trigger for cleanup)
- auto-advance: **no** — maintenance skill, returns to user after completing
- user-input gates: none (fully mechanical); collision on archive move halts for manual resolution
