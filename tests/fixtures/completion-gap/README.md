# `tests/fixtures/completion-gap/` — the completion-gap fixture inputs (slice-102 / SC-232)

Every fixture the completion-gap suite drives is built into `tmp_path` from bytes committed **here** or
in `../aivlc-vault/`. **No fixture path resolves outside `tests/fixtures/`**, and no machine-local vault
(`~/.aisdlc/<slug>-<hash>`) is a fixture or a test target anywhere — `tests/conftest.py` strips
`AI_SDLC_VAULT_ROOT` from every child env precisely so a test can never reach the developer's real vault,
and such a path exists on exactly one machine (round-2 B3).

## Provenance, split honestly (round-3 M10)

The claim "all nine fixtures derive from the committed replay" was **false as written**, so it is stated
per fixture instead:

| # | fixture | provenance |
|---|---|---|
| 1 | unbuilt-present | **replay** — `../aivlc-vault/decomposition-run-b.json` through the real `persist` verb, then one candidate's `effort` lowered so a dependent OUTRANKS its own live prerequisite |
| 2 | none-pickable | **replay** — every product child moved to `active` (in-flight) |
| 3 | all-built | **hand-committed** (`all-built/`) — the committed replay has ZERO product-sourced rows in either candidates file, so an all-built stratum is not reachable from it |
| 4 | scope-absent | **replay** — `product-scope.json` removed |
| 5 | scope-corrupt | **replay** — `product-scope.json` overwritten with non-JSON |
| 6 | candidates-absent ×3 | **replay** — `candidates.json` absent / zero-byte / `{"candidates": []}` |
| 7 | nine-key | **hand-committed** (`nine-key/`) — a trimmed legacy snapshot. No shipped writer emits 9 keys: `cmd_persist` and `cmd_revise`'s `out_items` projection both write `code_components`, so this shape can only be committed, never replayed |
| 8 | rejected-only | **hand-committed** (`rejected-only/`) — needs archived children with a `rejected` composition |
| 9 | two-area | **replay** + the real `set-area` verb, then one area's children archived as shipped |

The three hand-committed vaults carry `archive/candidates.json` rows with
`source: [{"type": "product-scope", "ref": "PS-NNN"}]` — the provenance every reader joins on.

## What each hand-committed vault pins

- **`all-built/`** — every capability archived-and-**shipped** (`done == total`, `unbuilt[]` EMPTY). The
  only honest pin for `route-add-item`: deleting a candidate from a live vault can only ever reach
  `none-pickable`, never `all-built` (round-2 M4).
- **`rejected-only/`** — one shipped capability plus one whose archived children are all **rejected**.
  It is the pin that `all-built` can never fire while `done < total`: a `rejected_only` capability falls
  out of `done` AND would fall out of `unbuilt[]` if the projection carried only one of `state`/`bucket`.
- **`nine-key/`** — this repo's own legacy item shape. Used as the negative half of AC4's
  "every pre-existing item is equal under revise's own `out_items` projection": raw byte-equality is
  FALSE here (the projection legitimately backfills `code_components`), which is what makes the
  semantic-identity assertion honest rather than vacuous.
