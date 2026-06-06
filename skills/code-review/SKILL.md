---
name: code-review
description: "Adversarial code-Critic review of the just-built slice's code diff along 9 fixed dimensions, producing path:line findings as code-review.json. Runs in a forked Critic context. Use AFTER /build-slice, BEFORE /validate-slice."
when_to_use: "Trigger phrases: /code-review, 'review the slice code', 'adversarial code review on the diff'. Auto-advances from /build-slice."
context: fork
agent: code-review
allowed-tools: Read, Grep, Glob, Bash, WebSearch, Write
---

# /code-review — forked code-Critic

You are running as the **code-Critic** (your persona, the 9 dimensions, frameworks, specificity/honesty/severity
rules, and the `code-review.json` output shape are your system prompt — `agent: code-review`). This skill is the
*task*: it gives you the slice inputs and the write target. Review the diff; do not approve it.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` / the git-common-dir
> `aisdlc/vault-root` config). You run forked and do NOT inherit the project CLAUDE.md — resolve it here.

## Active slice + inputs

The active slice is the latest `<vault>/slices/slice-NNN-*/`. Read these (they are the intent + design the code
must match — drift from them is Dimension 7):
- `mission-brief.json` — intent, acceptance criteria, must-not-defer, out-of-scope
- `design.json` — what's new, components touched, contracts, wiring matrix, ADR refs
- `decisions/ADR-*.json` referenced by the slice, and `build-log.json` (verify `result: shipped`)

## Slice diff (in-scope only) — injected

```!
base="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || echo main)"
git diff "$base...HEAD" -- 'src/**' 'skills/**' 'agents/**' 'scripts/**' 'tests/**' 2>/dev/null | head -1200
```

If the injected diff is empty → write `code-review.json` with `"result":"NO-CODE-CHANGES"` and stop.

## Task
1. Read the slice artifacts above.
2. Review the diff along your **9 dimensions, in order**. Every finding cites `path/to/file:line`. A dimension with
   nothing wrong gets an explicit "none: <reason>" — never manufacture findings.
3. Optional code-graph cross-check: use the `code-review-graph` MCP tools (impact-radius / search) for blast-radius
   and INFERRED edges the new code depends on.
4. Write `<vault>/slices/slice-NNN-<name>/code-review.json` (schema by example: `examples/code-review.json`).
5. Update `<vault>/slices/slice-NNN-<name>/milestone.json`: `stage: "code-review"`.

## Return
A 2-line summary to the main thread: `Result` + blocker/major/minor counts. The full review lives in
`code-review.json`. Findings are **advisory in v1** (they do not block `/validate-slice`).

## Pipeline position
- predecessor: `/build-slice` · successor: `/validate-slice` · auto-advance: true
- on-clean-completion: the main thread advances to `/validate-slice` after this fork returns.
