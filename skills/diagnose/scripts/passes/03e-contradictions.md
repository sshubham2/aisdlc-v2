# Pass 03e — Contradictory assumptions across modules

You identify places where module A assumes one thing about a shared concept and module B assumes another — silent disagreements that cause bugs.

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Source code, config, schemas, and CRG only.
- Findings conform to schema. ID format: `F-CONTRA-<sha1(category + concept + path_a)[:8]>`.

## Method

1. Identify shared concepts/entities by scanning data models and frequently-imported types.
2. For each major entity (e.g., `User`, `Order`, `Tenant`), look at how different modules treat it:
   - Cardinality assumptions: 1:1 vs 1:many vs many:many — does one module assume a user has one org while another assumes many?
   - Identity assumptions: integer ID vs UUID vs email vs slug — which is canonical?
   - Validation assumptions: is the email format checked at the boundary? in the model? in multiple places with different rules?
   - Lifecycle assumptions: soft-delete vs hard-delete; one module filters `deleted_at IS NULL`, another doesn't.
   - Auth assumptions: one module assumes the request is authenticated, another re-checks; one checks a permission, another assumes the caller already did.
   - Time assumptions: timezone-aware vs naive; UTC vs local; ISO format vs unix timestamp.
3. For each contradiction:
   - Cite both sides (file:line).
   - Show concretely how they disagree.
   - Reason about which one is "right" relative to the inferred intent (or if neither is, flag as needing decision).
4. Find code paths where the contradiction is exercised. CRG is MCP-native (no single `graph.json` to load as a library) — query the CRG graph at `$OUT/.code-review-graph/` via its MCP tools / CLI for the connecting code path between the two modules:
   ```bash
   # path / reachability between the two disagreeing modules — CRG impact-radius / review-context
   code-review-graph impact-radius --from <module-a> --out $OUT/.code-review-graph
   code-review-graph review-context "<module-a> <module-b>" --out $OUT/.code-review-graph
   ```

## Severity rubric

- `low` — cosmetic mismatch (e.g., one module formats dates as ISO, another as RFC2822)
- `medium` — contradictions in non-critical paths
- `high` — contradictions affecting data integrity (e.g., soft-delete inconsistency)
- `critical` — auth/permission contradictions (one module assumes the caller is authenticated, another doesn't enforce)

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-CONTRA-<8hex>` · `pass`: `03e-contradictions` · `category`: `contradiction`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: must concretely state both sides + implication
- `evidence`: list of `{path, lines, note}` — cite both sides · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: contradictions grouped by entity. For each: the two sides, the user-facing symptom (or future symptom), recommended resolution direction.

**`findings` block** — One entry per contradiction. `description` must concretely state both sides and the implication.

**`summary` block** — One paragraph: count, top entity affected, worst contradiction.

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

- Don't flag stylistic differences (snake_case vs camelCase) unless they affect correctness.
- Don't flag intentional adapter layers (e.g., a translation layer between two systems with deliberately different shapes).
- Always cite both sides — single-side claims are not contradictions.
