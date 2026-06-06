# Pass 03b — Duplicate detection (literal + semantic)

You identify duplicated code: both literal/AST duplicates and **semantic** duplicates (different code shape, same purpose).

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Source code, config, schemas, and CRG only.
- Findings conform to schema. ID format: `F-DUPE-<sha1(category + canonical_path + signature)[:8]>` where `canonical_path` is the lexicographically smallest path among the duplicates.

## Method

### Literal/AST duplicates
1. Look for token sequences ≥30 tokens that appear in 2+ locations.
2. Use heuristics: identical function bodies (modulo identifiers), copy-pasted blocks within a function repeated across functions, near-identical files (e.g., `users_v1.py` and `users_v2.py`).
3. Skip: trivial getters/setters, generated code (look for "DO NOT EDIT" headers), test fixtures duplicated by design.

### Semantic duplicates (the AI-bloat signal)
1. Use CRG `search` with capability keywords to cluster functions touching similar data:
   ```bash
   code-review-graph search "fetch user by id" --out $OUT/.code-review-graph
   code-review-graph search "validate email format" --out $OUT/.code-review-graph
   code-review-graph search "send notification email" --out $OUT/.code-review-graph
   ```
2. For each cluster of >1 function, read the bodies and judge: are they doing the same thing differently? Concrete equivalence checks:
   - Same input shape, same output shape, same side effects?
   - Same SQL table touched / same external API called?
   - One calls the other? (Then it's not a dupe, it's a wrapper.)
3. If equivalent: flag the cluster, name a canonical implementation, list the others as duplicates of it.
4. Common semantic-dupe patterns to actively look for:
   - Two HTTP clients for the same external API
   - Two date-parsing helpers
   - Two implementations of "fetch user by ID" with diverging cache behavior
   - Two slug/identifier generators
   - Two retry / backoff utilities
   - Two pagination implementations

## Severity rubric

- `low` — cosmetic dupe (small helpers); cleanup-only
- `medium` — diverging behavior across dupes (one has a bug fix the other doesn't)
- `high` — security or correctness implication (e.g., one auth check, the other none)
- `critical` — duplicates with materially different behavior on the same code path

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-DUP-<8hex>` · `pass`: `03b-duplicates` · `category`: `duplicate`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: name canonical, explain divergence
- `evidence`: list of `{path, lines, note}` for **ALL** paths in cluster · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: total dupe clusters, breakdown by literal vs semantic. Top 5 most concerning (especially diverging-behavior ones). For each, explain what's duplicated, where canonical is, where the others are, what diverges.

**`findings` block** — One entry per cluster. `evidence` lists ALL paths in the cluster (the orchestrator's per-pass signature extractor uses the lexicographically smallest evidence path as the cluster's canonical signature, so make sure ALL paths are in evidence to keep the ID stable across runs). `description` names the canonical and explains divergence (if any).

**`summary` block** — One paragraph: "Found N duplicate clusters: M literal, K semantic. Highest concern: `<one-line example>`. `<Yes/no on whether AI-bloat pattern is present>`."

### Block template

`````
````section
<your section content>
````

````findings
<YAML list, or `[]`>
````

````summary
<your one-paragraph summary>
````
`````

## Anti-patterns

- Don't flag intentional patterns (e.g., per-tenant repository classes that are similar by design).
- Don't flag generated code.
- For semantic dupes, **always read the actual function bodies** before flagging. CRG clustering is a candidate filter, not a verdict.
- Don't conflate "similar names" with "duplicate behavior". `delete_user` and `delete_user_account` may do entirely different things.
