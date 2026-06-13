# Pass 02 — Architecture mapping and judgment

You describe the **current architecture as observed in code**, then judge it for fitness against the inferred intent (which is also derivable from code — do not read pass 01's output).

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph
- The CRG graph at `$OUT/.code-review-graph/` for god nodes, components, hotspots (query via the `CRG_DATA_DIR` + `tools.query` subprocess — see Use CRG)

## Hard rules
- **No documentation reads.** Do not read any `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` files or anything in a `docs/` directory in `TARGET`. Source code, config (`.yaml`, `.toml`, `.json`, `.env`), schemas, build manifests, and CRG only. Inline docstrings/comments may be read but not trusted as ground truth.
- Do not modify any file in `TARGET`.
- Recommendations must be specific, evidence-backed, and tagged keep/modify/drop.

## Method

1. **Components.** Use CRG `review-context` / `search` (and the architecture overview) to identify the structural components (modules / layers / services) and their order. Name each, describe its responsibility from its code, list its in-edges and out-edges.
2. **Data flow.** Trace one or two primary user-facing flows from entry point through layers to persistence and back. State the path concretely (file → file → file).
3. **Stack.** Languages, major frameworks, persistence, queues, deployment hints (Dockerfile, k8s manifests, serverless configs).
4. **Where the code agrees with itself.** Patterns that are consistently applied: e.g., "all controllers use the service layer; all services use the repository layer." This is the implicit contract.
5. **Where the code disagrees with itself.** Modules that bypass the otherwise-consistent pattern. (These also become findings in pass 03f, but mention them here at architectural level.)
6. **Architecture judgment.** Is the architecture appropriate for what the system does? Is it over-engineered for its scope? Under-engineered? What's load-bearing?
7. **Recommendations.**
   - **KEEP** — strengths to preserve through any refactor (3-7 items, with rationale).
   - **MODIFY** — structural changes worth making (3-7 items, with rationale + concrete what).
   - **DROP** — parts that aren't earning their keep (0-5 items, with rationale).

## Use CRG

Query the isolated graph with the canonical `CRG_DATA_DIR` + `code_review_graph.tools.query` subprocess pattern
from the subagent contract above (no MCP tools; 2.3.x has no `search`/`impact-radius`/`review-context` CLI verb).
For this pass: component structure / "review context" → `query_graph(pattern=…, target=…, repo_root="$TARGET")`
or `semantic_search_nodes(query="<component-keyword>", repo_root="$TARGET")`; reachability from an entry file →
`get_impact_radius(changed_files=["<entry-file>"], repo_root="$TARGET")`.

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

Each finding has 12 required fields. Orchestrator validates + recomputes malformed IDs deterministically.

- `id`: `F-<CAT>-<8hex>` · `pass`: `02-architecture` · `category`: schema enum
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: multi-line · `evidence`: list of `{path, lines, note}`
- `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]` in the findings block.

### Block contents

**`section` block** — H2 "## 2. Architecture" with H3 subsections: "2.1 Current architecture (as observed)" (Components table | Data flow primary-path trace | Stack list | "Where the code agrees with itself" patterns | "Where the code disagrees with itself" selective bypasses), "2.2 Architecture judgment" (2-4 paragraphs on fitness, over/under-engineering, load-bearing parts), "2.3 Recommendations" with **KEEP** (3-7), **MODIFY** (3-7), **DROP** (0-5) bullet lists, each with rationale + evidence.

**`findings` block** — `[]` (Architectural recommendations live in the prose; code-level findings come in 03* passes).

**`summary` block** — One paragraph, ~80 words: stack at a glance, dominant architectural pattern, main strength, main structural concern, overall fit-for-purpose verdict (well-fit / over-engineered / under-engineered / drifting).

### Block template

`````
````section
<your section content>
````

````findings
[]
````

````summary
<your one-paragraph summary>
````
`````

## Anti-patterns

- Don't generate generic architecture advice ("consider microservices"). Recommendations must be tied to evidence in this codebase.
- Don't list every component as KEEP. Be opinionated; skipping a section is fine if there's nothing real to say.
- Don't recommend rewrites. Recommendations should be incremental.
