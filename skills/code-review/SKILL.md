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

**WT-ROOT-1:** the diff and any targeted Reads come from the slice WORKTREE `$wt` (HEAD = the slice branch),
NOT the main tree (HEAD = default there → an empty diff). The build leaves changes UNCOMMITTED in `$wt`
(commit happens at `/commit-slice`), so diff the working tree against the branch base, not `base...HEAD`.

```!
repo_root="$(git rev-parse --show-toplevel)"
slice_folder="$(ls -1t "$AI_SDLC_VAULT_ROOT/slices/" | grep -v archive | head -1)"
wt="$($PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/_worktree_paths.py" --slice-folder "$slice_folder" --repo-root "$repo_root" | head -1)"
paths='src/** skills/** agents/** scripts/** tests/**'
base="$(git -C "$wt" merge-base HEAD origin/HEAD 2>/dev/null)"   # fork point (real repos w/ origin/HEAD)
if [ -n "$base" ]; then
  git -C "$wt" diff "$base" -- $paths 2>/dev/null | head -1200    # committed + uncommitted since fork
else
  git -C "$wt" diff HEAD -- $paths 2>/dev/null | head -1200       # no remote: work is uncommitted (WT-ROOT-1 contract)
fi
git -C "$wt" ls-files --others --exclude-standard -- $paths 2>/dev/null | sed 's/^/NEW-UNTRACKED: /'
```

If the diff AND the untracked list are both empty → write `code-review.json` with `"result":"NO-CODE-CHANGES"` and
stop. Read any `NEW-UNTRACKED:` files from `"$wt/<path>"` for review (new files aren't in `git diff`).

## Task
1. Read the slice artifacts above.
2. Review the diff along your **9 dimensions, in order**. Every finding cites `path/to/file:line`. A dimension with
   nothing wrong gets an explicit "none: <reason>" — never manufacture findings.
3. Optional code-graph cross-check: use the `code-review-graph` MCP tools (impact-radius / search) for blast-radius
   and INFERRED edges the new code depends on.
4. Write `<vault>/slices/slice-NNN-<name>/code-review.json` (schema by example: `examples/code-review.json`).
5. Update `<vault>/slices/slice-NNN-<name>/milestone.json`: `stage: "code-review"`.
6. **Record the gate outcome** — one row per slice into `<vault>/gate-log.json` (measurement spine, roadmap
   Theme 8 / plan Phase 0). `code-review` is a **low** reality-contact gate (the model grading a diff);
   `gate_log.py` stamps that. Run this in-fork (the shared vault + `vault_edit` lock make it write-safe):

```bash
# verdict: no-code-changes | clean (0 findings) | findings (>=1); findings-count = blocker+major+minor
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" \
    --gate code-review --slice <slice-NNN-name> \
    --verdict <no-code-changes|clean|findings> --findings-count <N> \
  | $PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" append \
        --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin
```

## Return
A 2-line summary to the main thread: `Result` + blocker/major/minor counts. The full review lives in
`code-review.json`. Findings are **advisory in v1** (they do not block `/validate-slice`).

**Model-on-model gate (Phase 1.3).** This is LOW reality-contact — the model grading a diff (the CRG cross-check
reads real code, but the verdict is still the model's judgment). It is advisory by design; the gate that can
actually say *no* against reality is `/validate-slice` (real device/data). Never present a clean code-review as
a reality sign-off, and never let it override a reality gate.

## Pipeline position
- predecessor: `/build-slice` · successor: `/validate-slice` · auto-advance: true
- on-clean-completion: the main thread advances to `/validate-slice` after this fork returns.
