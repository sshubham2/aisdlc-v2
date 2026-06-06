# Pass 03d — Half-wired features

You identify features that are partially implemented: UI elements with no backend, endpoints with empty bodies, handlers that always return TODO, feature branches in code with one branch unreachable.

This is a strong AI-bloat signal — an implementer started something and didn't finish.

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. TODO/FIXME/HACK markers must be found in source code, not in a TODO.md.
- Findings conform to schema. ID format: `F-HALF-<sha1(category + path + symbol)[:8]>`.

## Method

1. **TODO/FIXME/XXX/HACK/STUB markers in production code paths** (not test fixtures, not dev scripts). Grep for these. For each match, trace whether the marked code is on a reachable path.
2. **Empty implementations:**
   - Functions whose body is just `pass`, `raise NotImplementedError`, `return None`, `return {}`, `return []`.
   - Handler functions that have no real logic.
   - Class methods that are abstract-not-implemented but not actually `@abstractmethod`.
3. **Frontend → backend disconnects:**
   - UI buttons / forms posting to endpoints that don't exist (grep frontend for endpoint paths, cross-check route registrations).
   - Endpoints registered but handler is empty / 501.
4. **Feature flags / config flags:**
   - Flags defined but never read.
   - Flags read but with only one branch ever taken (the other path is dead — flag it).
5. **Migration / schema gaps:**
   - Migrations that add columns never read in code.
   - Code that reads columns that don't exist in any migration.
6. **Background jobs / queues:**
   - Job handlers registered but never enqueued.
   - Tasks enqueued but no consumer.

## Severity rubric

- `low` — TODO comment with no behavior impact (e.g., a `# TODO: better error message`)
- `medium` — empty handler on a non-critical path
- `high` — UI/backend disconnect on a user-visible flow; or migration/code mismatch
- `critical` — empty implementation on a security-critical path (auth, payment, permission check)

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-HALF-<8hex>` · `pass`: `03d-half-wired` · `category`: `half-wired`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: multi-line, concrete
- `evidence`: list of `{path, lines, note}` — include BOTH endpoints of a disconnect (UI + missing endpoint) · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: total half-wired items by subcategory. Top 5 most impactful. Highlight any user-visible features that don't actually work.

**`findings` block** — One entry per half-wired feature. `evidence` includes both endpoints of a disconnect when applicable (e.g., the UI button file:line AND the missing endpoint location).

**`summary` block** — One paragraph: count by subcategory + worst offender + AI-bloat verdict (does this codebase show signs of incomplete handoffs?).

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

- Don't flag TODOs in test files unless they hide skipped test coverage.
- Don't flag `raise NotImplementedError` in abstract base classes — that's the contract, not a defect.
- Don't flag frontend mocks during development if the codebase clearly has a `mock-mode` flag.
