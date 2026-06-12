# v2 vault-artifact JSON conventions

Every vault artifact is JSON. Markdown is kept ONLY for `./CLAUDE.md` (harness-loaded) and `diagnosis.html`
stays HTML. The **schema by example** is the canonical instance for each artifact type: the bulk live in
`schemas/artifact-examples.json` (one key per type), and each is also bundled per-skill at
`skills/<name>/examples/<artifact>.json` (generated from that file by `.build/aggregate.py`). `artifact_lint`
enforces them (required keys + known enums + version-skew WARN).

## The pattern: structure-fields + markdown-body-strings
- Machine-readable parts are real JSON fields (status, ids, scores, refs, dates, enums, arrays).
- Human prose lives in **markdown-valued string fields**, named by role (`intent`, `rationale`, `decision`,
  `notes`, `on_resume`, `body`, …). This preserves rich prose AND machine-parseability — so "humans like prose"
  never justifies keeping a file as `.md`.

## Cross-cutting rules
- `_schema`: every file starts with a version tag, e.g. `"aisdlc/risk-register@1"`. The `@N` is a MAJOR
  schema version — bump it only on a breaking shape change, and add a migration note to the CHANGELOG (4.5).
- `_plugin_version`: artifacts CREATED via `tools.vault_edit` are stamped with the plugin version that wrote
  them, so a version mismatch is detectable (4.5).
- Timestamps: ISO-8601 strings, stamped by tooling (shown as `<ts>` in examples).
- Cross-references by **id**, never free text: risks `R-NN`, ADRs `ADR-NNN`, slices `slice-NNN`,
  candidates `SC-NNN`, acceptance criteria `ACn`.
- Enums: lowercase strings.
- Derived fields (e.g. risk `score`/`band`) are computed by the audit tool, not hand-set.
- **Append / CAS-only files** (risk-register, candidates, lessons-learned, shippability, `_index`, drift-log, ADRs):
  mutate via `tools.vault_edit` (append / rewrite-CAS), never a hand whole-file overwrite — the SVW-1 discipline.

## Schema & version skew (4.5)
- Readers (`artifact_lint`, run in the `/build-slice` pre-finish gate, `/reflect`, and `/drift-check`) WARN —
  **non-fatally** — when an artifact's `_schema@N` is NEWER than the running plugin's canonical example, or its
  `_plugin_version` is newer than the running plugin: i.e. a vault written by a NEWER plugin and read by an older
  one. Older-than-current (an archived slice) is benign and is not warned.
- A schema bump = a migration note in the CHANGELOG. The canonical examples in `artifact-examples.json` are the
  source of truth for the current `@N` per artifact type.

## Artifact → schema map
| live file | schema | notes |
|---|---|---|
| `<vault>/triage.json` | triage | project open / re-triage history |
| `<vault>/concept.json` | concept | what / who / constraints |
| `<vault>/risk-register.json` | risk-register | the risk **ledger** (RR-1) |
| `<vault>/candidates.json` | slice-candidates | the candidate **backlog** + assumptions (✅ done) |
| `<vault>/decisions/ADR-NNN.json` | adr | append-only, supersede-don't-edit |
| `<vault>/shippability.json` | shippability | regression catalog |
| `<vault>/slices/slice-NNN/mission-brief.json` | mission-brief | slice intent + ACs |
| `<vault>/slices/slice-NNN/milestone.json` | milestone | rolling resume-state |
| `<vault>/slices/slice-NNN/design.json` | design | per-slice spec + wiring matrix |
| `<vault>/slices/slice-NNN/build-log.json` | build-log | flight recorder |
| `<vault>/slices/slice-NNN/validation.json` | validation | per-AC PASS/FAIL + evidence |
| `<vault>/slices/slice-NNN/reflection.json` | reflection | retrospective + critic calibration |

Still markdown/other in v2: `./CLAUDE.md` (markdown), `diagnosis.html` (html), `tests/**`, `VERSION`, allowlists.
Heavy-mode artifacts (requirements, threat-model, non-functional) and logs (lessons-learned, drift-log, sync-log,
`_index`) follow the same pattern. The full set of example keys is enumerated in
`schemas/artifact-examples.json`; `artifact_lint --self-check` lints every one in CI.
