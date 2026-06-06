# Pass 03g — Dead config, flags, and env vars

You identify configuration entries, feature flags, and environment variables that are defined but never consumed (or consumed but never set).

## Inputs
- `TARGET`, `OUT`

## Hard rules
- No source mutation.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Config files (`.env`, `.yaml`, `.toml`, `.json`, `.ini`) ARE in scope — those are config, not docs.
- Findings conform to schema. ID format: `F-CONFIG-<sha1(category + name + defined_path)[:8]>`.

## Method

1. **Locate config sources:**
   - `.env`, `.env.example`, `.env.local`
   - YAML/TOML config files (`config/*.yml`, `pyproject.toml [tool.*]`, `application.yml`)
   - Code-defined defaults (`Config` classes, settings modules)
   - Feature flag registries (LaunchDarkly definitions, in-code flag tables)
2. **Locate config consumers:**
   - `os.environ.get`, `process.env.X`, `config.X`, `settings.X`, `getenv("X")`
   - Feature-flag check calls
3. **Cross-reference:**
   - Defined-but-not-read → dead config
   - Read-but-never-set (no default, missing from .env.example, no production manifest) → potentially broken at runtime
   - Read in only one branch → orphaned flag (also covered in 03d half-wired, cross-link via finding ID)
4. **Special:**
   - Hardcoded "should be config" values — values referenced in many places that look like they wanted to be config (but lower priority — surface as a note).

## Severity rubric

- `low` — dead var in `.env.example` only
- `medium` — defined in code but unread; or read in code but with safe default
- `high` — read with no default, not present in any deployment manifest (will crash at runtime if reached)
- `critical` — flag controlling security/billing behavior with no consumer (feature is not actually gated)

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-CONFIG-<8hex>` · `pass`: `03g-dead-config` · `category`: `dead-config`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: multi-line, concrete
- `evidence`: list of `{path, lines, note}` · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: counts by subcategory (dead defined / read-but-unset / orphaned-flag). Tables listing each.

**`findings` block** — One entry per dead/broken config item.

**`summary` block** — One paragraph: count by category + most concerning (e.g., a critical flag with no consumer).

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

- Don't flag standard framework env vars (e.g., `PORT`, `NODE_ENV`, `DATABASE_URL`) even if your scan misses their consumer — they may be read by the framework.
- Don't flag deploy-only env vars (`AWS_REGION`, `K8S_NAMESPACE`) that are consumed by infra, not app code.
- Don't flag dev/test-only flags clearly gated to non-prod.
