# Pass 03f — Layering violations / selective bypass

You identify places where the codebase's dominant layering pattern is selectively bypassed.

## Inputs
- `TARGET`, `OUT`
- `OUT/.code-review-graph/` — the CRG code graph

## Hard rules
- No source mutation.
- **No documentation reads.** Skip every `.md`, `.rst`, `.txt`, `.adoc`, `.markdown`, `.org` file and any `docs/` directory in `TARGET`. Source code, config, schemas, and CRG only.
- Findings conform to schema. ID format: `F-LAYER-<sha1(category + violator_path + bypassed_layer)[:8]>`.
- **Establish the dominant pattern first**, then flag deviations. Don't import external best practices.

## Method

1. **Infer the layering pattern.** Look at the majority behavior:
   - Do most controllers/handlers go through a service layer? Through a repository layer to reach DB?
   - Does the codebase use middleware for auth uniformly? For request parsing?
   - Is there an event bus / queue most async work goes through?
   - Is direct DB access concentrated in one module?
2. **Flag the minority that bypasses.** Examples:
   - 4/5 controllers call `UserService.get`, but one controller calls the DB directly.
   - 6/7 producers send via the queue, but one calls the consumer in-process.
   - All routes go through the auth middleware, but two have it disabled with no recorded reason.
3. **Distinguish intentional from accidental bypass:**
   - Intentional: explicit comment, matches a known requirement (e.g., "health check must not query DB"), part of a documented module exception.
   - Accidental: no comment, no obvious reason, looks like the implementer just didn't know.
4. **Grep-verify the import statement before emitting a HIGH-severity finding** (LAYER-EVID-1, `methodology-changelog.md` v0.33.0). For each candidate finding, apply the **textual import-evidence requirement**: grep `$TARGET` for an actual textual import statement matching the alleged bypass. Multi-line semantics: use `re.DOTALL` / ripgrep `--multiline` to span newlines (TypeScript codebases routinely break long named-import lists across lines).

   **TypeScript / JavaScript (5 import variants + 3 re-export variants + 2 dynamic forms)** — regex strings below are byte-equal to those in `tests/skills/diagnose/test_layering_pass_textual_evidence.py::_grep_textual_import` per slice-019 /critique M1 visual byte-equality mitigation; edit-in-lockstep with the test helper if these patterns ever change:
   - `^\s*import\s+\w+\s+from\s+['"]<bypassed-path>['"]`                    # default import: `import X from "..."`
   - `^\s*import\s+\{[^}]*\}\s+from\s+['"]<bypassed-path>['"]`              # named import: `import { X, Y } from "..."` (multi-line via DOTALL)
   - `^\s*import\s+\*\s+as\s+\w+\s+from\s+['"]<bypassed-path>['"]`         # namespace import: `import * as X from "..."`
   - `^\s*import\s+type\s+\{[^}]*\}\s+from\s+['"]<bypassed-path>['"]`       # type-only named import: `import type { X } from "..."`
   - `^\s*import\s+['"]<bypassed-path>['"]\s*;?\s*$`                        # side-effect import (no `from`): `import "..."`
   - `^\s*export\s+\{[^}]*\}\s+from\s+['"]<bypassed-path>['"]`              # re-export named: `export { X } from "..."`
   - `^\s*export\s+\*\s+from\s+['"]<bypassed-path>['"]`                     # re-export all: `export * from "..."`
   - `^\s*export\s+type\s+\{[^}]*\}\s+from\s+['"]<bypassed-path>['"]`       # re-export type-only: `export type { X } from "..."`
   - `require\(\s*['"]<bypassed-path>['"]\s*\)`                             # CommonJS: `require("...")`
   - `\bimport\(\s*['"]<bypassed-path>['"]\s*\)`                            # dynamic: `import("...")`

   **Alias-aware grep** (load-bearing — the witnessed F-LAYER-bca9c001 used `@/*` alias resolution): if `<bypassed-path>` is repo-relative (e.g., `src/workflow/adapters/types.ts`) AND `$TARGET` has `tsconfig.json` or `jsconfig.json` with non-empty `compilerOptions.paths` or `compilerOptions.baseUrl`, the subagent MUST also grep for the alias-resolved logical name (e.g., if `paths` maps `@/*` -> `src/*`, also grep for `from ['"]@/workflow/adapters/types['"]`). Failing to check both forms means the rule fails closed on the alias-pattern that produced the original witness.

   **Python**:
   - `^\s*from\s+<module>\s+import\s+`                                       # `from module import X`
   - `^\s*import\s+<module>(\s|$|\.)`                                        # `import module` / `import module.sub`

   **Rust**: `\buse\s+[\w:]*<module-name>(::|;|\s)` (e.g., `use crate::backend::types;`).
   **Go**: `^\s*import\s+(\(\s*)?["']<path>["']` (single or block form).
   **Java**: `^\s*import\s+(static\s+)?<fqn>(\.\*)?\s*;` (single import, optionally static, optionally `*`).
   **Other languages**: fall back to grepping the bypassed-layer path string anchored within 1-3 tokens of any of `import|from|use|include|require` keywords.

   **Action on result**: if zero textual matches exist in the evidence file across all applicable variants, the CRG edge is a phantom (often from cross-file same-name symbol collapse) — DOWNGRADE the finding to severity `low` with `evidence[].note: "downgraded: no textual import grep-match (LAYER-EVID-1)"`, OR skip emission entirely if no other layering signal supports it (e.g., no dynamic-import via string literal, no test-gap on alleged boundary).

5. Use CRG to confirm the layer order / structural components, via the `CRG_DATA_DIR` + `tools.query` subprocess from the contract (no `review-context` CLI verb):
   ```bash
   CRG_DATA_DIR="$OUT/.code-review-graph" $PY -c "import json; from code_review_graph.tools.query import semantic_search_nodes; print(json.dumps(semantic_search_nodes(query='layer order components', repo_root='$TARGET', limit=20).get('results', []), default=str)[:3000])"
   ```

## Severity rubric

- `low` — cosmetic layering inconsistency
- `medium` — bypass of service or repository layer
- `high` — bypass of auth, validation, or transaction boundary
- `critical` — bypass of a security-critical layer (authn, authz, audit logging)
- **Downgrade rule (LAYER-EVID-1)**: any candidate `high` or `critical` finding whose alleged import is not textually grep-verifiable in the evidence file is downgraded to `low` (or skipped). Phantom CRG edges from cross-file same-name symbol collapse are the witnessed failure mode (slice-019, F-LAYER-bca9c001 false-positive — frontend code with parallel-type-file defining identically-named enums; CRG symbol-resolution synthesized a phantom edge into the backend tier where zero textual imports existed).

## Output format

Per ADR-001 (slice-001) + slice-002, return your output as three 4-backtick fenced blocks in your final message. **Do NOT call Write to produce output files (the orchestrator handles that). You MAY use Bash/python for CRG queries within $OUT/.code-review-graph/, and Read/Grep/Glob for source files within $TARGET.**

### Schema crib sheet (for the `findings` block)

- `id`: `F-LAYER-<8hex>` · `pass`: `03f-layering` · `category`: `layering-violation`
- `severity`: `low | medium | high | critical` · `blast_radius`: `small | medium | large` · `reversibility`: `cheap | expensive | irreversible`
- `title`: ≤100 chars · `description`: include dominant pattern + specific deviation
- `evidence`: list of `{path, lines, note}` · `suggested_action` · `effort_estimate`: `small | medium | large` · `slice_candidate`: `yes | no | maybe`

Empty findings: return `[]`.

### Block contents

**`section` block** — Prose: state the inferred dominant layering pattern (with evidence: "X out of Y controllers follow this"). Then list each deviation, what layer it bypasses, and the apparent reason or absence of one.

**`findings` block** — One entry per violation. `description` must include the dominant pattern reference and the specific deviation.

**`summary` block** — One paragraph: dominant pattern + violation count + worst violation.

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

- **Don't apply external "best practices."** If the codebase has no service layer at all, don't flag controllers calling the DB directly — that's the codebase's pattern.
- Don't flag the first/only place a pattern appears — there's no dominant pattern to violate.
- Don't flag adapter/bridge code that explicitly translates between two layered worlds.
- **Don't trust CRG edges for HIGH-severity boundary findings without textual import-evidence verification.** Cross-file same-name symbols (e.g., parallel type files defining identically-named enums) can collapse into phantom edges. Apply LAYER-EVID-1's grep-verification before emitting (see Method step 4 + Severity rubric downgrade rule).
