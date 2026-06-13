# Pass 03a — Dead code detection

You identify code unreachable from any entry point.

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation in `TARGET`.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Source code, config, schemas, build manifests, and CRG only.
- Findings must conform to the schema crib sheet below (full schema: the bundled `schema/finding.yaml`).
- ID format: `F-DEAD-<sha1(category + primary_evidence_path + signature)[:8]>` where `signature` = the function/class/module name being flagged.

## Method

1. Identify modules / functions with **no inbound edges** (the orphan set). CRG has no direct `orphans` verb — derive it: query CRG via the `CRG_DATA_DIR` + `tools.query` subprocess from the contract (no MCP, no CLI verbs) for each module to find ones nothing reaches, and cross-check with Grep for inbound references:
   ```bash
   CRG_DATA_DIR="$OUT/.code-review-graph" $PY -c "import json; from code_review_graph.tools.query import get_impact_radius; print(json.dumps(get_impact_radius(changed_files=['<module-or-symbol>'], repo_root='$TARGET'), default=str)[:3000])"
   ```
2. For each candidate, **verify before flagging:**
   - grep for the symbol name as a string literal (catches dynamic imports / reflection / config-driven loading).
   - Check if it's a public API exported from the package (might be intentionally part of the surface).
   - Check if it's a test fixture, a CLI subcommand, or a plugin entry point (these often have no static inbound edges but ARE entry points).
3. Identify entry-point candidates by scanning for: `if __name__ == "__main__"`, framework decorators (`@app.route`, `@cli.command`, `@click.command`, `@pytest.fixture`), exported package symbols (`__all__`, `index.ts` exports).
4. Use CRG reachability / `get_impact_radius` from each entry point to get the union of reachable code:
   ```bash
   CRG_DATA_DIR="$OUT/.code-review-graph" $PY -c "import json; from code_review_graph.tools.query import get_impact_radius; print(json.dumps(get_impact_radius(changed_files=['<entry>'], repo_root='$TARGET'), default=str)[:3000])"
   ```
5. Anything not in that union AND not a verified entry point AND not referenced as a string literal is dead code.
6. Severity rubric:
   - `low` — entire module / file unreachable; cheap to remove
   - `medium` — function within a live module is dead; moderate clarity benefit
   - `high` — dead code path inside a function (e.g., unreachable branch after an early return); may indicate a logic bug
   - `critical` — only if the dead code is masking a security check or correctness invariant

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-DEAD-<8hex>` · `pass`: `03a-dead-code` · `category`: `dead-code`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: multi-line, include verification step
- `evidence`: list of `{path, lines, note}` · `suggested_action`: concrete · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: how many dead items, broken down by file/module/function/branch level. Top 5 most impactful (largest modules / clearest cuts). Note any verified-but-uncertain cases (e.g., "appears dead but referenced in YAML config — flagged for owner verification").

**`findings` block** — One YAML entry per dead item. Include the verification step taken in `description`.

**`summary` block** — One paragraph: "Detected N dead-code items: M unreachable modules, K dead functions, J dead branches. Largest cluster: `<name>` (`<LOC>` lines). Verification: dynamic-import grep, config-reference grep."

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

- **Don't flag without verification.** Dynamic imports (`importlib`, `require()` with computed strings, `eval`) defeat static reachability. Always grep for the symbol string before flagging.
- **Don't flag public API.** A function exported from a package's `__init__.py` / `index.ts` may be deliberately part of the surface even if no internal caller exists.
- **Don't flag test fixtures.** Pytest fixtures, jest setup hooks, etc., have implicit inbound edges via the test runner.
- **Don't flag plugin entry points** registered via setuptools entry-points / package.json `bin`.
