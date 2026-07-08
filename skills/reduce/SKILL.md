---
name: reduce
description: "Complexity budget enforcer. Audits the vault and codebase for over-engineering, dead claims, speculative generality, god-nodes, and cross-slice incoherence (the same idea solved N ways across slices) against mode-specific thresholds. Produces a ranked reduction-candidate report and — if confirmed — proposes a simplification slice. Appends an over-engineering pattern lesson to lessons-learned.json after a reduction slice ships."
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
- `/reduce --force` — include ALL REDUCE-NOW items in the slice proposal without the per-item confirmation
  gate. It does NOT start the slice: this skill runs forked (no Skill tool), so the proposal still lands with
  the user/main thread, and the slice starts only when they act (matches Pipeline position: "--force bypasses
  the confirmation gate but NOT the slice gate").

## Live state — injected

Active project mode (for threshold selection):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"; cat "$VAULT/triage.json" 2>/dev/null | $PY -c "import sys,json; t=json.load(sys.stdin); print(t.get('mode','unknown'))" 2>/dev/null || echo "unknown"
```

Concept scope (for component-count comparison):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"; cat "$VAULT/concept.json" 2>/dev/null | $PY -c "import sys,json; c=json.load(sys.stdin); print('name:', c.get('name','?')); print('scope:', c.get('scope','?')); print('constraints:', len(c.get('constraints', [])))" 2>/dev/null || echo "concept unavailable"
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
- ADRs with zero cross-references (unreferenced after creation) — ONE pass, not a per-ADR Grep loop
  (O(ADRs × vault) tool calls on a mature vault):
- Concept scope from `<vault>/concept.json` — compare intended breadth vs actual component count

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
VAULT="$VAULT" $PY -c "
import json, os, pathlib, re
vault = pathlib.Path(os.environ['VAULT'])
adr_ids = sorted(p.stem for p in (vault / 'decisions').glob('ADR-*.json'))
refs = {a: 0 for a in adr_ids}
for p in vault.rglob('*.json'):
    try:
        text = p.read_text(encoding='utf-8')
    except OSError:
        continue
    for a in adr_ids:
        # a reference from any file OTHER than the ADR's own (supersedes-chain refs count)
        if p.stem != a and re.search(a + r'\b', text):
            refs[a] += 1
print(json.dumps({'unreferenced_adrs': [a for a, n in refs.items() if n == 0],
                  'adr_total': len(adr_ids)}, indent=2))
"
```

## Step 3 — code metrics (CRG)

Use `code-review-graph` MCP tools for god-node and blast-radius detection:

- **God nodes**: use the CRG MCP tools `mcp__code-review-graph__get_impact_radius_tool` /
  `mcp__code-review-graph__semantic_search_nodes_tool` (CRG 2.3.x has no `impact-radius`/`search` verbs) on
  highest-degree files/functions — flag nodes with
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
| Same concept implemented across ≥2 slices with divergent abstractions (cross-slice incoherence) | REDUCE-NEXT — see Step 4b |

### Severity definitions
- **REDUCE-NOW** — clear win, low/no risk; can delete or inline without a design discussion
- **REDUCE-NEXT** — needs a small refactor slice; design review required
- **WATCH** — at threshold; flag but don't act yet

## Step 4b — cross-slice coherence audit (Theme 4: the same idea solved N ways)

Slicing trades within-task error (visible) for **between-slice incoherence** (invisible) — the same concept
implemented differently across slices, divergent abstractions, the same problem solved three ways. The per-slice
gates can't see this; it only shows up when you look ACROSS slices. This audit is that periodic look. Walk the
SHIPPED slices and hunt three patterns:

- **Concept duplication** — two+ slices that each introduced their OWN version of the same capability (two retry
  helpers, two date formatters, two "current user" resolvers, two cache layers). Read `slices/archive/slice-*/mission-brief.json`
  intents + `reflection.json` discovered-items, cluster by concept (lexical overlap of intent), then confirm with
  CRG `search` for same-named / same-shaped functions across the slices' touched files.
- **Divergent abstractions** — the same concept modeled inconsistently (one slice uses a class, another a dict; one
  returns a Result, another raises; one is sync, another async). Use CRG `search` on the concept's symbols to compare
  shapes — divergence is the finding, not mere duplication.
- **Same idea solved 3 ways** — three+ implementations of one pattern. Threshold for a finding: a concept appears in
  **≥2 slices with materially different implementations** (3+ is the strongest signal).

For each, file a **COHERENCE-DEDUP** candidate at severity **REDUCE-NEXT** (unifying divergent abstractions is a
refactor — design review required, never an auto-delete). Cite the slices + the divergent symbols (path:line via CRG).

**Guard against false positives (the Healthy-patterns rule applies here too):** genuinely separate concerns that
merely look alike are NOT duplication. Two "validators" for different domains, two endpoints with different SLAs, the
same word in different bounded contexts — leave alone. A coherence finding requires the implementations to be doing
the *same job*, not just sharing a name. If CRG is unavailable, run this audit lexically and mark each finding
`(unconfirmed — CRG absent)` so it reads as a lead, not a verdict.

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

CROSS-SLICE COHERENCE (same idea solved N ways — REDUCE-NEXT, refactor; Step 4b):
  N. <concept> — solved in slice-AAA (<symbol>) and slice-BBB (<divergent symbol>); unify
  ...

WATCH:
  N+1. <pattern> — <threshold note>

Summary: <N> REDUCE-NOW, <N> REDUCE-NEXT, <N> cross-slice-coherence, <N> WATCH
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

**Lesson capture rides the loop, not this fork.** Include in the proposal text: _"the reduction slice's
`/reflect` should record the over-engineering pattern that led to the bloat as its lesson (which pattern from
the reduction table, and what produced it)."_ This fork ends at the proposal — the slice ships days later in a
different session, where `/reflect` owns lesson capture; there is no executable moment here to append a lesson
from. (The old Step 8 prescribed an append this skill could never be running at the time of.)

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
