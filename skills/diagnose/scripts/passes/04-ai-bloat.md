# Pass 04 — AI-bloat signature analysis

This pass runs **after** passes 03b (duplicates) and 03d (half-wired). It composes signals from those structured findings (YAML only — never read prose) plus code-level pattern checks to identify the specific signature of AI-assisted-development drift.

## Inputs
- `TARGET`, `OUT`
- `OUT/findings/03b-duplicates.yaml` — read structured only
- `OUT/findings/03d-half-wired.yaml` — read structured only
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation.
- **No documentation reads from `TARGET`.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Source code, config, schemas, and CRG only. (Reading `OUT/findings/*.yaml` is fine — that's this skill's own structured output, not the target codebase.)
- Read ONLY the structured YAML files from prior passes — never the prose sections.
- Findings conform to schema. ID format: `F-BLOAT-<sha1(category + signature + path)[:8]>`.

## Signatures to identify

### S1 — Multiple impls of the same capability (compose from 03b)
Filter 03b semantic-duplicate findings where the duplicate cluster spans modules that look like *parallel attempts*: e.g., `users/service.py` and `api/users.py` both implementing user fetch, neither calling the other. This is the classic AI-forgot-it-already-built-this signature.

### S2 — Modules added without integration (compose from 03a + CRG)
Modules that exist (have code, have imports of stdlib) but have no inbound edges AND no exported public surface AND no entry-point decoration. AI generated, never wired in. Cross-check 03a-dead-code findings; flag separately as bloat-signature when the module's content suggests it was intended to be a feature, not a leftover.

### S3 — Stale scaffolding (heuristic scan)
Generated/scaffolded code that was never customized. Signals:
- Files with default placeholder names (`example.py`, `template.ts`, `boilerplate.tsx`)
- Comments like `# TODO: customize this`, `// your logic here`
- Functions with default implementations from a generator (e.g., FastAPI `read_root`, `read_item` left as default)
- Tests that just assert `True == True`

### S4 — Inconsistent patterns suggestive of session breaks
- Naming inconsistency within a single module (e.g., `getUser`, `fetch_user`, `retrieveUser` in the same file — possible signal of multiple AI sessions reformatting style each time)
- Mixed error-handling idioms within a module (some try/except, some `.catch`, some bare returns)
- Mixed logging idioms (`print`, `logger.info`, `console.log`) within the same module
- Unused imports added in bulk (signals a session that planned but didn't finish)

### S5 — Half-wired feature concentration (compose from 03d)
If 03d findings cluster in specific modules (e.g., 6 of 8 half-wired findings are in one feature folder), that feature folder is likely an abandoned AI-driven attempt. Flag the cluster as an aggregate bloat finding.

### S6 — Documentation/code drift (lightweight check)
- Docstrings that describe behavior the code doesn't have (parameters listed but not used; return types that don't match).
- Inline comments contradicting adjacent code.

This is a lightweight signal; only flag clear contradictions.

## Severity rubric

- `low` — cosmetic inconsistency with no behavior risk
- `medium` — clear AI-bloat signature with cleanup value
- `high` — multiple impls causing user-visible behavior differences; or abandoned feature folder
- `critical` — a security or correctness path is among the duplicated/abandoned implementations

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.** (You may Read the prior passes' `findings/03b-duplicates.yaml` + `findings/03d-half-wired.yaml` from `$OUT/findings/` — those are the structured cross-pass inputs this pass depends on.)

### Schema crib sheet (for the `findings` block)

- `id`: `F-BLOAT-<8hex>` · `pass`: `04-ai-bloat` · `category`: `ai-bloat`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: when composite, reference the 03b/03d finding ID(s)
- `evidence`: list of `{path, lines, note}` · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose with explicit signature headings (S1-S6). For each signature present, give the count, list top examples (3-5 each), and explain the AI-development-pattern implication. End with a verdict: "AI-bloat presence: low / medium / high / severe."

**`findings` block** — One entry per signature instance worth flagging. **Cross-reference**: include the related 03b/03d finding ID(s) in the description when this is a composite finding.

**`summary` block** — One paragraph: which signatures are present, the verdict, the single most actionable observation.

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

- **Never read 03b or 03d prose sections.** YAML cross-reads only.
- Don't synthesize a finding without specific evidence. "This codebase feels AI-bloated" is not a finding.
- Don't accuse — describe. Use neutral language: "multiple parallel implementations exist," not "AI made a mess."
- Don't double-count: if a finding is already in 03b/03d, the bloat finding should reference it, not restate it.
