# Pass 01 — Intent reconstruction

You reconstruct what this codebase is **trying to be** from raw code. No README, no docs.

## Inputs
- `TARGET` — path to the repo being diagnosed
- `OUT` — path to `diagnose-out/`
- `OUT/.code-review-graph/` — the CRG code graph (query via the `CRG_DATA_DIR` + `tools.query` subprocess — see Use CRG)

## Hard rules
- **No documentation reads.** Do not read any `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` files or anything in a `docs/` directory in `TARGET`. Source code, config (`.yaml`, `.toml`, `.json`, `.env`), schemas, build manifests, and CRG only. Inline docstrings/comments may be read but not trusted as ground truth.
- Do not modify any file in `TARGET`. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.** Return your output as 4-backtick fenced blocks per the Output format section; the orchestrator writes the three pass files.
- Findings (none expected for this pass) must conform to the schema crib sheet in the Output format section below.

## Method

1. Identify entry points: `main`, `index`, route definitions, CLI entrypoints, server startup, queue workers, scheduled jobs.
2. Identify route handlers / public API surface. List endpoints, their methods, what data they accept/return.
3. Identify primary data models / entities. Names, fields, relationships.
4. Identify user-facing strings: error messages, UI labels, log messages — these reveal *who the user is* and *what they're being told*.
5. Identify external integrations: third-party APIs, queues, databases, payment providers — each integration is a clue to capability.
6. Synthesize: in 2-3 paragraphs, **what is this system trying to do, and for whom?**
7. List inferred actors with one line each (role + how they interact with the system).
8. List the capability map: 5-15 verb-phrase capabilities the system delivers (e.g., "schedule recurring payments", "export account statements as PDF").

## Use CRG

Query the isolated graph with the canonical `CRG_DATA_DIR` + `code_review_graph.tools.query` subprocess pattern
from the subagent contract above (you have no MCP tools, and 2.3.x has no `search` CLI verb).
For this pass call `semantic_search_nodes(query=…, repo_root="$TARGET")` for: `"main entry routes handlers"`,
`"models entities domain"`, `"external api integration client"`.

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

Each finding has 12 required fields. Orchestrator validates + recomputes malformed IDs deterministically.

- `id`: `F-<CAT>-<8hex>` · `pass`: `01-intent` · `category`: schema enum
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars, no trailing period · `description`: multi-line, concrete
- `evidence`: list of `{path, lines, note}` · `suggested_action`: concrete · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]` in the findings block.

### Block contents

**`section` block** — H2 "## 1. What the codebase does" with H3 subsections: "Inferred intent" (2-3 paragraphs), "Actors" (bulleted `**<actor>** — <role>; interacts via <surface> (<file:line>)`), "Domain model" (table or list of primary entities), "Capability map" (verb-phrase bullets with `<file:line>` evidence), "External integrations" (`<service>: <file:line>, purpose`).

**`findings` block** — `[]` (Pass 01 is prose-only; produces no findings).

**`summary` block** — One paragraph, ~80 words: "This codebase appears to be a `<type>` for `<users>`, primarily delivering `<top 3 capabilities>`. Built on `<stack>`. `<One sentence on what's distinctive or surprising>`."

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

- Don't quote the README. Don't quote design docs. Code only.
- Don't speculate about future intent — only what current code implements.
- If the codebase has no clear entry point (library or framework code), say so explicitly in the prose and degrade actors/capabilities to "consumers of API X".
