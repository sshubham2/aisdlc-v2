---
name: reduce
description: "Complexity budget enforcer. Audits the vault and codebase for over-engineering, dead claims, speculative generality, and god-nodes against mode-specific thresholds. Produces a ranked reduction-candidate report and — if confirmed — proposes a simplification slice. Appends an over-engineering pattern lesson to lessons-learned.json after a reduction slice ships."
when_to_use: "Trigger phrases: /reduce, 'reduce complexity', 'simplify the design', 'check for over-engineering', 'complexity audit'. Run when vault exceeds component-count threshold (auto-suggested by /reflect), before major releases, when drift-log accumulates >5 unresolved entries, or periodically in Heavy mode (every ~5 slices)."
argument-hint: "[--force]"
context: fork
agent: general-purpose
allowed-tools: Read, Grep, Glob, Bash
---

# /reduce — complexity budget enforcer

You audit the vault and code for over-engineering, then present ranked reduction candidates. Propose a simplification slice if confirmed. AI has no natural bias toward "less" — this skill is the counterweight.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Argument modes

- `/reduce` — audit + suggestions (no commitment; user picks what to act on)
- `/reduce --force` — proceed with all REDUCE-NOW items as a simplification slice (skip confirmation)

## Live state — injected

Active project mode (for threshold selection):
```!
cat "$AI_SDLC_VAULT_ROOT/triage.json" 2>/dev/null | $PY -c "import sys,json; t=json.load(sys.stdin); print(t.get('mode','unknown'))" 2>/dev/null || echo "unknown"
```

Concept scope (for component-count comparison):
```!
cat "$AI_SDLC_VAULT_ROOT/concept.json" 2>/dev/null | $PY -c "import sys,json; c=json.load(sys.stdin); print('name:', c.get('name','?')); print('scope:', c.get('scope','?')); print('constraints:', len(c.get('constraints', [])))" 2>/dev/null || echo "concept unavailable"
```

Vault file inventory (count by type):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "
import pathlib, sys
vault = pathlib.Path(sys.argv[1])
for kind, glob in [('components', 'components/*.json'), ('contracts', 'contracts/*.json'), ('adrs', 'decisions/ADR-*.json'), ('slices', 'slices/slice-*/mission-brief.json')]:
    count = len(list(vault.glob(glob)))
    print(f'{kind}: {count}')
print(f'total_vault_files: {len(list(vault.rglob(\"*.json\")))}')
" "$VAULT" 2>/dev/null || echo "vault inventory unavailable"
```

## Step 1 — determine thresholds

Read `<vault>/triage.json` for `mode`. Apply:

| Mode    | Component cap | Contract cap | ADR cap | Vault files cap |
|---------|--------------|--------------|---------|-----------------|
| minimal | 8            | 10           | 15      | ~50             |
| standard| 15           | 25           | 30      | ~100            |
| heavy   | 25           | 50           | 60      | ~200            |

## Step 2 — vault metrics

Collect:
- Component, contract, ADR, total vault file counts (from injected state above)
- ADRs with zero cross-references (unreferenced after creation): run `Grep` for each ADR id across `<vault>/` — an ADR returned only by its own file is a candidate
- Concept scope from `<vault>/concept.json` — compare intended breadth vs actual component count

```bash
# Check unreferenced ADRs (example for one ADR — repeat for each)
grep -r "ADR-NNN" "$AI_SDLC_VAULT_ROOT" --include="*.json" -l 2>/dev/null
```

## Step 3 — code metrics (CRG)

Use `code-review-graph` MCP tools for god-node and blast-radius detection:

- **God nodes**: use `impact-radius` or `search` on highest-degree files/functions — flag nodes with
  in-degree + out-degree > 2× average as god-node candidates.
- **Dead paths**: search for symbols defined but not referenced elsewhere.
- **Layer-cake**: identify files that are pure pass-throughs (imported by one consumer, imports one target, adds no logic).

If CRG graph is not yet built:
```bash
"${CRG:-code-review-graph}" build 2>/dev/null || echo "CRG unavailable — skip code metrics"
```

## Step 4 — identify reduction candidates

Walk vault + code for:

| Pattern | Severity |
|---------|----------|
| Component with <50-line doc + <100-line impl that can be inlined | REDUCE-NOW |
| ADR never cross-referenced after creation | REDUCE-NOW |
| Contract with single caller (CRG: 1 inbound edge) | REDUCE-NEXT |
| Single-implementation interface / single-product factory | REDUCE-NEXT |
| Config flag / env var with no reader (CRG: orphan config node) | REDUCE-NOW |
| Dead vault entry — component doc with no code-side counterpart | REDUCE-NOW |
| Pass-through layer adding no logic (layer-cake) | REDUCE-NEXT |
| Premature ADR for trivial choice (naming convention, formatting) | REDUCE-NOW |
| Speculative generality: abstracted with 1 impl + 1 caller "for future flexibility" | REDUCE-NEXT |
| Duplicate pattern across 3+ identical vault sections | WATCH |
| ADR count climbing >2/slice over last 3 slices | WATCH |

### Severity definitions
- **REDUCE-NOW** — clear win, low/no risk; can delete or inline without a design discussion
- **REDUCE-NEXT** — needs a small refactor slice; design review required
- **WATCH** — at threshold; flag but don't act yet

## Step 5 — present audit

Output the audit in this format:

```
Complexity audit (mode: <mode>)

Thresholds:
  Components: <N> / cap <cap>  — [OK | OVER | NEAR]
  Contracts:  <N> / cap <cap>  — [OK | OVER | NEAR]
  ADRs:       <N> / cap <cap>  — [OK | OVER | NEAR]
  Vault files:<N> / cap <cap>  — [OK | OVER | NEAR]

Reduction candidates:

REDUCE-NOW (clear wins):
  1. <path> — <one-line justification>
  ...

REDUCE-NEXT (refactor needed):
  N. <path> — <one-line justification>
  ...

WATCH:
  N+1. <pattern> — <threshold note>

Summary: <N> REDUCE-NOW, <N> REDUCE-NEXT, <N> WATCH
```

If all metrics are within thresholds and no candidates found: output "No reduction candidates found — complexity is within budget." and stop (do not propose a slice).

## Step 6 — user decision gate

**Without `--force`**: present the audit and stop. The main thread will ask the user which items to act on. Do NOT proceed to a slice automatically.

**With `--force`**: include all REDUCE-NOW items in the simplification slice proposal and advance to Step 7.

## Step 7 — propose a simplification slice (if confirmed)

Treat the confirmed reductions as a normal slice candidate. Propose via `/slice "reduce: <summary>"`. The
reduction slice's acceptance criteria must be:
- Each targeted file is deleted or inlined (with per-item justification)
- No external behavior change (backward-compat preserved)
- All tests pass after reductions
- Vault counts are back within threshold caps

Route through the normal loop: `/slice → /risk-spike → /design-slice → /critique → /build-slice → /code-review → /validate-slice → /reflect`.

## Step 8 — append lesson to lessons-learned.json

After a reduction slice ships, note the over-engineering pattern that led to the bloat. Route through
`vault_edit` (SVW-1 — never a raw Write/Edit on append-only files):

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
    --file "$AI_SDLC_VAULT_ROOT/lessons-learned.json" \
    --array entries \
    --content-file <tmp_lesson_json>
```

Schema by example: `examples/lessons-learned.json` (`aisdlc/lessons-learned@1`).

## Critical rules

- DO NOT extract abstractions for hypothetical reuse. Three similar files is better than premature abstraction.
- DO NOT rename for cosmetic consistency. Rename has a real cost.
- DO NOT collapse components with different responsibilities just because they're small.
- DO NOT touch code that isn't hurting. Old working code is an asset.
- DO catch: speculative generality, dead vault entries, config sprawl, layer-cake, unreferenced ADRs.
- `--force` is for confidence cases (clear deletes), not "decide for me."
- NEVER raw-write to `lessons-learned.json` — always route through `vault_edit` (SVW-1).

## Anti-patterns to catch

| Anti-pattern | Why bad |
|---|---|
| Single-implementation interface | Abstract for no benefit |
| Single-product factory | YAGNI |
| ADR for naming convention | Not a load-bearing decision |
| Pass-through service wrapper | Adds layer, no value |
| Config flags never overridden | Inline the value |
| Speculative plugin system (1 plugin) | YAGNI++ |

## Healthy patterns — leave alone

- Three similar slice folders — may be genuinely separate concerns
- Two components with overlapping names but different consumers / SLAs
- Multiple ADRs in the same area — historical record has value

## Pipeline position

- predecessor: `/reflect` (or on-demand; Heavy mode auto-suggests every ~5 slices)
- successor: `/slice "reduce: <summary>"` (when reductions confirmed)
- auto-advance: NO — user must confirm which items to act on (unless `--force`)
- user-input gate: Step 6 (candidate selection); `--force` bypasses the confirmation gate but NOT the slice gate
