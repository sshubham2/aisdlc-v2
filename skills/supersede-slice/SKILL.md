---
name: supersede-slice
description: "Establish a formal bidirectional supersession link between an archived slice whose design has been contradicted by reality and the active slice that fixes it (SUP-1). Appends a supersession block to the archived reflection.json, sets the Supersedes field in the active mission-brief.json, and validates the link via supersede_audit. (It does NOT write _index.json — per 3.12 the index regenerators drop non-canonical fields; the reflection.json block is the durable record.)"
when_to_use: "Trigger phrases: /supersede-slice, 'supersede slice NNN', 'mark slice obsolete', 'retire shipped slice', 'link supersession'. Use when an archived slice's claims would otherwise stand as live assertions while a new active slice corrects them. Optional maintenance step — most archived slices need no supersession."
argument-hint: "<archived-slice-id>"
allowed-tools: Read, Edit, Glob, Bash, AskUserQuestion
---

# /supersede-slice — Mark a Shipped Slice Obsolete (SUP-1)

You are establishing a formal bidirectional supersession link between an archived (shipped) slice and the new active slice that supersedes it.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).

## When to use vs. skip

Use when ALL three hold: (1) an archived slice's design/mission-brief continues to read as a live claim; (2) a new slice in active development is fixing the issue; (3) an auditable link is needed (compliance, critic-calibrate feed).

Skip when: adding a feature on top of a shipped slice (normal new slice); fixing a typo in an archived file (direct edit); mid-iteration design correction on an in-flight slice (use `/design-slice` deviation pattern instead).

## Injected state

Active slice (latest):
```!
$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$AI_SDLC_VAULT_ROOT" --repo-root . --folder-only 2>/dev/null
```

---

## Step 1 — Validate the archived target

If `<archived-slice-id>` was supplied as an argument, check it exists:

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"  # 4.6.1: resolve per-invocation
test -d "$VAULT/slices/archive/<archived-slice-id>"
```

If not found: STOP. List available archived slices via Glob (`<vault>/slices/archive/*/`) and tell the user which IDs are available.

Read `<vault>/slices/archive/<archived-slice-id>/reflection.json` (schema: `examples/reflection.json`). Surface a one-line summary of its `validated`, `corrected`, and `lessons` fields so the user has context before providing a supersession reason.

If no `<archived-slice-id>` argument was given: present the available archived slices and ask which one to supersede via `AskUserQuestion`.

## Step 2 — Gather the supersession reason (user-input gate)

Halt and ask via `AskUserQuestion`:

> Why is `<archived-slice-id>` being superseded?
> Provide one paragraph: what reality contradicted, which active slice replaces the work, when the contradiction was discovered.

**Good reason examples:**
- "Slice-008's claim that S3 sync upload is sufficient was retired by slice-014's load test (30s timeout exceeded). Async queue is now the path; slice-014 implements it."
- "Slice-005's auth middleware was inlined into routers in slice-019; the standalone module no longer exists, but reflection still claims it does."

**Reject these reasons** (ask again):
- "It's old" — supersession is for design contradiction, not age.
- "We refactored" — too generic; name the specific broken claim.
- "TBD" — must be specific before proceeding.

Identify (or confirm) the active slice that supersedes the archived one. Typically the latest `<vault>/slices/slice-NNN-*/` but the user may name a different one.

## Step 2b — Ask whether the code was unwound (revert ref)

**ALWAYS ask** (one question, via `AskUserQuestion` — the skill cannot know this without asking): _"Was
`<archived-slice-id>`'s shipped code actually unwound (reverted/removed)? If yes, give the revert ref — a
commit sha, a PR url/number, and/or a one-line note (e.g. 'partially unwound: kept the schema, reverted the
handler'). If no (fix-forward, or a vault-claim-only contradiction), say so."_

The ASK is mandatory; the FIELD is optional: a "no unwind" answer simply proceeds with no `revert` recorded —
it never blocks the supersession. At least one of commit / pr / note when a ref is given; the audit (Step 5)
refuses a malformed shape.

## Step 3 — Append supersession block to archived reflection.json

Read `<vault>/slices/archive/<archived-slice-id>/reflection.json`.

Set the `supersession` field (currently `null`) to:

```json
{
  "superseded_by": "<active-slice-id>",
  "date": "<YYYY-MM-DD>",
  "reason": "<user-provided reason from Step 2>",
  "revert": { "commit": "<sha>", "pr": "<url-or-number>", "note": "<how the code was unwound>" }
}
```

`"revert"` (slice-003) is OPTIONAL — include it only when Step 2b gathered a revert ref (omit the whole key
otherwise; omit any member not provided, but keep at least one). It records HOW the superseded code was
unwound; the audit validates the shape when present (members non-empty strings; unknown keys refused — a
typo'd key would silently lose the revert ref).

Use Edit to update the `"supersession": null` value in place. Do NOT modify any other content — supersession is append-only history, like ADR supersession. The rest of the reflection remains frozen.

Schema reference: `examples/reflection.json` (the `supersession` field).

## Step 4 — Update the active slice's mission-brief.json

Read `<vault>/slices/<active-slice-id>/mission-brief.json`.

Set `"supersedes": "<archived-slice-id>"` (the field exists in the schema, currently `null`).

Use Edit to update the `"supersedes": null` value. Do not change any other field.

Schema reference: `examples/mission-brief.json` (the `supersedes` field).

If no active slice exists yet: tell the user to set this field when running `/slice` to define the replacement work. The bidirectional audit will fail until both ends are linked.

## Step 5 — Run the supersession audit

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/supersede_audit.py" --root .
```

Expected: 1 link validated, no violations.

If audit reports `one-way-link` or `missing-target`: fix the reflection.json or mission-brief.json and re-run. Do not advance until the audit is clean.

> **No `_index.json` stamp (3.12).** Earlier versions CAS-wrote `superseded_by` into a `slices/_index.json`
> `recent[]` row here. That write was doomed: `/archive` and `/reflect` regenerate `recent[]` from a fixed field
> set that drops the field, and rows age out of `recent[]` within 10 slices anyway. The supersession link is
> **already durable** in the two files Steps 3–4 wrote (the archived `reflection.json` `supersession` block + the
> active `mission-brief.json` `supersedes` field), validated by Step 5's audit. `/pulse --full` surfaces the link
> by reading `reflection.json` when it lists recent reflections — no index row is needed.

## Step 6 — Confirm and hand off

Tell the user:
- "Slice `<archived-slice-id>` superseded by `<active-slice-id>`."
- "reflection.json in archive updated; mission-brief.json `supersedes` field set; audit clean. (The durable link lives in those two files — `/pulse --full` reads it from reflection.json.)"
- "Run `/critique` on the active slice next — cite the supersession reason in design.json if it informs design choices."

## Critical rules

- APPEND-ONLY on reflection.json — only the `supersession` field changes; no other content touched.
- NEVER delete the archived slice folder. It remains in `slices/archive/` as permanent historical record.
- VALIDATE the archived slice id strictly — it must match an existing folder under `slices/archive/`. A typo creates an orphan claim.
- BIDIRECTIONAL link is required, not optional. The audit rejects one-way links.
- DO NOT use for in-flight corrections (slice not yet archived) — those are deviations recorded in build-log.json per `/build-slice`.
- The supersession link lives ONLY in `reflection.json` + `mission-brief.json` (audit-validated) — do NOT stamp it into `slices/_index.json` (the next `/archive` or `/reflect` regen erases it; 3.12).

## Anti-patterns

- Superseding to clean up the archive: archived slices are reference, not clutter. Design decisions made in context should stay frozen.
- Bulk supersession with a single generic reason: each link needs a specific reason. Multiple slices with one reason is a smell — use individual supersession or a vault-rewrite operation.
- Superseding without a replacement slice: premature. Capture the obsolescence in the risk register instead and supersede when the fix slice exists.

## Pipeline position

- predecessor: any slice loop stage (maintenance step, invokable at any point after `/reflect` archives a slice)
- successor: `/critique` (on the active slice that is doing the superseding)
- auto-advance: false — user-input gate at Step 2; confirm before advancing
- user-input gates: Step 2 (supersession reason); Step 2b (revert ref — optional, never blocking); Step 1 if no argument supplied (which archived slice)
