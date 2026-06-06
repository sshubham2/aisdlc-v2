---
name: repro
description: "Establish a FAILING test that reproduces a bug BEFORE any fix code is written. Parses the issue description (asking <=3 clarifying questions if vague), queries code-review-graph for context in the buggy code area, writes a runnable test under tests/bugs/, confirms it actually fails with the expected signature, then appends a shippability.json entry via vault_edit so the bug can never silently return."
when_to_use: "Trigger phrases: /repro, 'reproduce this bug', 'failing test first', 'repro <issue>', 'establish repro'. Run BEFORE /slice when fixing a non-trivial bug. Skip for trivial fixes (typo, one-liner). After /repro, run /slice 'fix <issue>' — the fix slice's ACs include 'make the failing repro test pass'."
argument-hint: "<issue description>"
allowed-tools: Read, Grep, Glob, Write, Bash, AskUserQuestion
---

# /repro — reproduction-first bug fix

Establish a FAILING test that reproduces the bug before any fix code is written. The test must be confirmed
FAILING before this skill completes. After this skill, `/slice "fix <issue>"` carries an unambiguous AC.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).

## Step 0 — live state (injected)

Current shippability catalog (next entry ID + existing test paths):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" get --file shippability.json --path . 2>/dev/null || echo '{"rows":[]}'
```

## Step 1 — understand the issue

Parse the argument. If vague (no reproduction steps, expected vs actual, or trigger condition), ask clarifying
questions ONE AT A TIME — maximum 3, via `AskUserQuestion`:

1. **Exact reproduction steps**: what input / request / state triggers the bug?
2. **Expected vs actual behavior**: what should happen vs what does?
3. **Environment conditions**: data shape, auth state, platform, file size, concurrency?

Do not accept "it's slow" or "it breaks". Require an actionable description:
- Bad: "upload fails sometimes"
- Good: "POST /receipts returns 500 for HEIC files >5MB; expected 201 within 10s"

## Step 2 — query the code graph

Before writing the test, understand the buggy code area via CRG MCP tools. Use these three queries in order:

1. **Keyword/semantic search** — find the relevant module and symbol:
   Call the CRG MCP tool `search` with keywords from the bug description (e.g., the endpoint path, function name,
   or error message). Identify the file(s) and symbol(s) most likely involved.

2. **Impact radius** — what the buggy symbol affects:
   Call the CRG MCP tool `impact-radius` with the buggy symbol identified above. This surfaces callers,
   dependents, and related paths that the test may need to exercise or stub.

3. **Review context** — full context around the symbol:
   Call the CRG MCP tool `review-context` with the buggy file path and symbol name to read the surrounding
   logic. Use `Grep` / `Glob` / `Read` to examine source files as needed.

If CRG surfaces a past slice touching this area, check its `reflection.json` — this may be a regression.
If so, note it: the shippability entry for that slice is incomplete.

## Step 3 — write the failing test

Write the test under `tests/bugs/` (or the project's bug-test convention). The test MUST:

- Be runnable from project root with a single command (`pytest tests/bugs/test_<slug>.py -v`)
- Target the specific bug trigger, not adjacent surface
- Complete in <10 seconds (shippability catalog runtime budget — SCMD-1)
- Have a clear assertion that passes when the bug is fixed
- Include a docstring stating: bug description, expected behavior, actual behavior

Example layout:
```python
# tests/bugs/test_receipt_upload_heic_timeout.py
"""
Bug: POST /receipts returns 500 for HEIC files >5MB
Expected: 201 within 10s
Actual: 500 (timeout) after ~30s
"""
def test_heic_large_upload_succeeds():
    resp = client.post(
        "/transactions/test-001/receipt",
        files={"file": ("sample.heic", open("tests/fixtures/5mb.heic", "rb"))},
    )
    assert resp.status_code == 201
    assert resp.json()["receipt_url"]
```

Write the file via the Write harness tool.

## Step 4 — confirm the test FAILS

Run the test and verify it fails with the expected signature:

```bash
pytest tests/bugs/test_<slug>.py -v
```

If the test **passes**: STOP. Do NOT proceed. Either:
- The bug is already fixed / env doesn't match
- The test isn't targeting the right path

Present an `AskUserQuestion` gate: "the test I wrote passes — can you confirm the reproduction steps?"
Loop back to Step 1. Do NOT add a passing test to the shippability catalog.

## Step 5 — append to shippability catalog

Append the new row via `vault_edit` (SVW-1 — never a raw Write/Edit on shippability.json).

Build the JSON row as a Python dict and pass it inline:

```bash
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append --file shippability.json --array rows --json '{
  "id": "SHIP-<next>",
  "slice": "<placeholder-fix-slice-name>",
  "kind": "test",
  "description": "<one-line bug description>",
  "machine_cmd": "pytest tests/bugs/test_<slug>.py -q",
  "critical_path": true,
  "added": "<ts>"
}'
```

Schema by example: `examples/shippability.json` (`aisdlc/shippability@1`).

- `id`: next `SHIP-NNN` from the injected catalog above (Step 0)
- `slice`: placeholder fix-slice name (user confirms when `/slice` runs; update if they rename it)
- `machine_cmd`: must be a single runnable command from project root (SCMD-1: no prose, no shell expansions)
- `critical_path`: true for all bug repros
- `added`: ISO-8601 timestamp

## Step 6 — hand off

Output a summary block, then instruct the user to run `/slice "fix <issue>"`:

```
Reproduction established.

Test:   tests/bugs/test_<slug>.py
Status: FAILING as expected (<actual error>)
Shippability: SHIP-<N> appended to <vault>/shippability.json

Run /slice "fix <short issue name>" next.
The fix slice's mission-brief must include:
  AC: pytest tests/bugs/test_<slug>.py passes
  Out of scope: unrelated refactors
```

## Critical rules

- NEVER proceed if the test passes. A non-reproducing "repro" is worse than none — it creates false confidence.
- NEVER write fix code. Fix code is the slice's job; /repro only establishes the test.
- TESTS must run in <10s — the shippability catalog runs every /validate-slice; latency compounds.
- ONE bug per /repro. Never bundle multiple bugs into one test.
- DOCUMENT the bug in the test docstring (expected / actual behavior).
- SVW-1: shippability.json appends route through `vault_edit` — never raw Write/Edit.

## Pipeline position

- predecessor: standalone (invoked before `/slice` on bug-fix candidates) or from `/slice` BFRD-1 gate
- successor: `/slice "fix <issue>"`
- auto-advance: no — user triggers `/slice` after reviewing the output
- user-input gates: clarifying questions (Step 1, up to 3); "test passes" recovery gate (Step 4)
