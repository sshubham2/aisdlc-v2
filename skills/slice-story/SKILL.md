---
name: slice-story
description: "Turns one slice's internal artifacts into a single plain-language STORY for a mixed technical / non-technical audience (tilted slightly technical, ZERO pipeline jargon), rendered as a standalone story.html and delivered straight to you — including your phone over Remote Control — via SendUserFile. Adaptive across the lifecycle: pre-build it covers the objective, spike proofs, the build approach, and what design review changed; after building it adds build, code-review, reality-testing results and learnings. A forked narrator subagent writes story-sections.json; render_story.py renders the HTML; then you're asked to start /build-slice when ready."
when_to_use: "Trigger phrases: /slice-story, 'tell the story of this slice', 'plain-language slice report', 'overview before build', 'explain this slice for non-engineers'. Auto-invoked after /critique ONLY when the design review surfaced >=1 finding to narrate (a clean zero-finding review or a skipped low-tier Critic hands straight to /build-slice); always user-invokable any time to get a readable report of where a slice stands. Optional arg: a slice id (default: the active slice)."
argument-hint: "[slice-id]"
allowed-tools: Read, Write, Bash, Agent, SendUserFile
---

# /slice-story — the slice's story, in plain language

Produce **one standalone `story.html`** that tells the story of a slice so a product owner with no engineering
background AND the engineer about to build it can both follow it. Slightly technical, but plain English first —
and **no pipeline jargon** (`AC2`, `C1`, `TRI-1`, "the Critic", "dispositions"…) ever reaches the page.

The heavy synthesis is delegated to a forked **`slice-story` narrator subagent** so the parent context stays
lean. This skill orchestrates: resolve the slice → spawn the narrator → render the HTML → **deliver it straight
to the user via `SendUserFile`** (it reaches them wherever they are, phone included) → ask them to run
`/build-slice` when ready. It never modifies source files.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` /
> git config `aisdlc/vault-root`). Active slice = latest `<vault>/slices/slice-NNN-*/` (not under `archive/`).

## Step 0 — resolve the TARGET slice + its artifacts (SINGLE authority; run this FIRST)

Run the `bash` block below **first** — it resolves the target slice in a BODY step that BINDS an explicit
`/slice-story slice-NNN` `$ARG` (a `!`-injection runs at skill-LOAD before `${ARGUMENTS}` binds, so it
CANNOT — SC-064 / ADR-022) and prints which artifacts exist (this drives which story sections to include).
The printed `active_slice_dir` is **the ONE authoritative `<target-slice>` path** for ALL subsequent reads
(Step 1) and writes (Steps 3, 4) — there is no second resolution step and no override to remember:

- **Arg present → `--slice`** (ARCHIVE-AWARE: searches `slices/` AND `slices/archive/`, active first, and
  prints an ABSOLUTE path). A `/commit-slice` on-ship auto-emit may target an already-archived slice —
  `/reflect`'s DD-20 archives *before* `/commit-slice` runs — and the absolute path keeps the write target
  cwd-independent (correct under `--merge`, which `cd`s to the main tree, AND `--push`, on the worktree).
  NEVER `--repo-root` for the explicit case — it EXCLUDES `archive/`.
- **No arg → `--repo-root .`** (the active, non-archived slice). The `[ -n "$ARG" ]` guard is load-bearing
  (SC-064/M2): a no-arg invocation must never run `--slice ""`, which mis-resolves.
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
ARG="${ARGUMENTS[0]:-}"   # a slice id (e.g. /commit-slice's on-ship auto-emit) may target an ARCHIVED slice
if [ -n "$ARG" ]; then SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --slice "$ARG" --path-only)"; else SDIR="$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only)"; fi
$PY -c "import json,glob,os,sys; d=sys.argv[1] or None; have=[os.path.basename(f) for f in sorted(glob.glob(f'{d}/*.json'))] if d else []; mb=json.load(open(f'{d}/mission-brief.json',encoding='utf-8')) if d and os.path.exists(f'{d}/mission-brief.json') else {}; ms=json.load(open(f'{d}/milestone.json',encoding='utf-8')) if d and os.path.exists(f'{d}/milestone.json') else {}; print(json.dumps({'active_slice_dir':d,'slice':mb.get('slice'),'title':mb.get('title'),'mode':mb.get('mode'),'risk_tier':mb.get('risk_tier'),'stage':ms.get('stage'),'artifacts_present':have},indent=2))" "$SDIR" 2>/dev/null || echo "{}"
```

From the printed JSON take `active_slice_dir` (= `<target-slice>`, absolute), `slice`, `title`, `mode`,
`risk_tier`, `stage`.

Prerequisite: the resolved folder must contain at least `mission-brief.json`. If it doesn't (empty
`active_slice_dir` / no such slice id), STOP:
_"No slice to tell a story about — run /slice first."_ If `design.json` is absent, proceed but tell the user the
story will be thin (objective + acceptance outcomes only — there's no design or review to narrate yet).

Decide the **furthest lifecycle stage** from which artifacts are present:
`reflection.json` → `shipped` · `validation.json` → `validated` · `code-review.json` → `reviewed` ·
`build-log.json` → `built` · else → `pre-build`. This becomes the story's `stage`.

## Step 1 — gather the artifacts for the narrator

Read (from the target slice folder, whichever exist) so you can hand their contents to the narrator:
`mission-brief.json`, `design.json`, `critique.json`, `critique-review.json`, `milestone.json`, and any
`build-log.json` / `code-review.json` / `validation.json` / `reflection.json`. Also read the ADRs this slice
locked (`design.json.adrs[]` → `<vault>/decisions/ADR-*.json`) and this slice's spikes — **the spike join is
deterministic, not model judgment** (a spike may be keyed by the slice id OR by its source candidate SC-NNN;
this block resolves the candidate↔slice mapping from candidates.json live + archive and prints the matching
spike files, which you then Read):
```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
TS="<target-slice>"   # the Step-0 absolute path
TS="$TS" VAULT="$VAULT" $PY -c "
import glob, json, os
ts, vault = os.environ['TS'], os.environ['VAULT']
mb = json.load(open(os.path.join(ts, 'mission-brief.json'), encoding='utf-8'))
slice_id = mb.get('slice')
cand = None
for rel in ('candidates.json', 'archive/candidates.json'):
    p = os.path.join(vault, rel)
    if not os.path.exists(p):
        continue
    for c in json.load(open(p, encoding='utf-8')).get('candidates', []):
        if isinstance(c, dict) and c.get('slice') == slice_id:
            cand = c.get('id'); break
    if cand:
        break
spikes = []
for f in sorted(glob.glob(os.path.join(vault, 'spikes', 'spike-*.json'))):
    try:
        d = json.load(open(f, encoding='utf-8'))
    except (OSError, ValueError):
        continue
    if isinstance(d, dict) and (d.get('slice') == slice_id or (cand and d.get('candidate') == cand)):
        spikes.append(f)
print(json.dumps({'slice': slice_id, 'candidate': cand, 'spikes': spikes}, indent=2))
"
```

Embed the **full JSON contents** into the narrator prompt (Step 2). Embedding (rather than handing the subagent
a path to the external vault) avoids the out-of-cwd subagent access pitfall (R-1) — the narrator needs no
filesystem access and the artifacts are small.

## Step 2 — spawn the narrator subagent

Use the **Agent tool** with `subagent_type: "slice-story"`. The narrator persona (audience rules, the
translate-don't-transcribe table, the banned-jargon list, the output schema, tone) lives in
`agents/slice-story.md` — do **not** restate it here. Pass only inputs:

```
Slice: <slice-id> — <title>
Mode: <minimal | standard | heavy>   Risk tier: <low | medium | high>
Lifecycle stage reached: <pre-build | built | reviewed | validated | shipped>
Write target (absolute): <target-slice>/story-sections.json
Delivery (record verbatim as the `delivery` field): {"status": "<proactive|normal>", "auto_invoked": <true|false>}

# mission-brief.json
<full JSON>

# design.json
<full JSON, or "none yet">

# spikes (this slice)
<full JSON of each, or "none">

# decisions / ADRs locked by this slice
<full JSON of each, or "none">

# critique.json (design review)
<full JSON, or "none">

# critique-review.json (second-pass review)
<full JSON, or "none">

# post-build artifacts (only if the slice has advanced)
<full JSON of build-log / code-review / validation / reflection, each labelled, or "none yet">
```

**Await the real agent — never fabricate its output.** The narrator WRITES `<target-slice>/story-sections.json`
itself (the `aisdlc/story-sections@1` schema; see `examples/story-sections.json`) and returns a SHORT receipt
(path, stage, section count, headline) — never the full JSON back into this context. Build the report ONLY from
the file it wrote; do not hand-write the story yourself (that defeats the plain-language synthesis).

**Bounded retry**: if the Step-3 verification fails (file missing / invalid JSON / wrong slice or delivery), or
the narrator times out / returns null, re-prompt it ONCE to re-write the file. If it fails twice, STOP and tell
the user: _"The story narrator failed — try `/slice-story` again later, or proceed to `/build-slice` without a
narrated story."_ Never loop indefinitely.

## Step 3 — verify story-sections.json (receiving inspection)

The narrator wrote `<target-slice>/story-sections.json` itself (raw-write is correct — a per-slice
active-folder artifact, not a shared aggregate, so SVW-1's `vault_edit` requirement does not apply). The
`delivery` field it recorded — `{"status": "proactive"|"normal", "auto_invoked": true|false}`, handed to it
verbatim in the Step-2 prompt (auto_invoked = this run was spawned by `/critique` or `/commit-slice`, not typed
by the user) — keeps the delivery behavior auditable from the artifact. Verify the file WITHOUT pulling its
contents into this context (the receipt is not proof; this check is):

```bash
TS="<target-slice>"
TS="$TS" $PY -c "
import json, os
p = os.path.join(os.environ['TS'], 'story-sections.json')
d = json.load(open(p, encoding='utf-8'))
assert d.get('_schema') == 'aisdlc/story-sections@1', 'wrong _schema'
assert d.get('sections'), 'no sections'
assert d.get('delivery', {}).get('status') in ('proactive', 'normal'), 'delivery missing/invalid'
print(json.dumps({'ok': True, 'slice': d.get('slice'), 'stage': d.get('stage'),
                  'sections': len(d['sections']), 'headline': d.get('headline')}))
"
```

Check the printed `slice`/`stage` against Step 0. Any assertion failure or mismatch → the Step-2 bounded retry
(re-prompt the narrator once, then STOP). Do NOT repair the file by hand-writing story content yourself.

## Step 4 — render the ONE combined story.html

`render_story.py` renders **one self-contained page** that carries the plain-language story AND — composed into it
as a second region — the full design-tournament detail (the per-designer proposals, the honest offline
expert-source badge "cites a source" / "self-attested" / "no source", and the "which reviews ran" panel). Pass
`--slice-dir` + `--gate-log` so the tournament half is composed in, and carry the `$VAULT` resolution into THIS
call so `--gate-log` resolves (slice-043: the former separate Step-4b `tournament.html` is gone — one render, one
file):

```bash
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY "${CLAUDE_SKILL_DIR}/scripts/render_story.py" \
    --sections-file "<target-slice>/story-sections.json" \
    --slice-dir "<target-slice>" \
    --gate-log "$VAULT/gate-log.json" \
    --out "<target-slice>/story.html"
```

`render_story.py` is deterministic, stdlib-only, stamps the generation time, and is the SINGLE exit-code authority
for the combined render. `<target-slice>` is the archive-aware resolved folder (Step 0), so the tournament half
composes for an in-flight OR an already-shipped/archived slice. The tournament half degrades honestly: a slice with
no three-designer contest composes an honest "no contest" region, never an invented one; and (M-add-1) it shows
what each designer *found and proposed*, NOT the literal search queries it ran. The jargon tripwire guards ONLY the
story prose — the tournament half (designer names, review detail) keeps its full vocabulary.

Exit codes (the combined contract):
- **0** — rendered (story + composed tournament, or story-only when `--slice-dir` is omitted).
- **3 = JARGON-LEAK** (story prose only): stderr names each leaking field + token. Ask the narrator to re-translate
  exactly those fields (refs belong in `ref`, never prose), rewrite `story-sections.json`, re-render — **once**. If
  it still exits 3, re-run with `--allow-jargon` and tell the user which tokens leaked. **Nothing is written.**
- **4** — the story rendered but the composed tournament detail was UNAVAILABLE (malformed `design-proposals.json`
  / render error): `story.html` **IS** written with the story half intact + a visible "tournament view unavailable"
  notice, and the cause is on stderr. **Deliver it** (Step 5) — the readable story is the keystone deliverable and
  must never be dropped over the companion view.
- **1 / 2** — bad/empty story JSON / io error: **nothing is written** — report the stderr and stop (Step 5 delivers
  no file). Do not hand-assemble HTML.

## Step 5 — deliver the report to the user

Send the rendered report straight to the user with the **`SendUserFile`** tool. This reaches them wherever they
are — including a phone over Remote Control — with no external service and no extra permission, and (because the
file goes to the user's own session, not a third-party endpoint) the auto-mode safety classifier does not block it:

- `files`: `["<target-slice>/story.html"]` — the ONE combined report (the design-tournament detail is composed
  into it as a second region; there is no separate `tournament.html` anymore). On exit **4** still deliver this one
  file (the story half is intact + carries the tournament-unavailable notice). On exit **1/2/3** NOTHING was written
  — deliver NO file and report the stderr (never `SendUserFile` a path that does not exist).
- `status`: use `"proactive"` when `/slice-story` was auto-invoked (by `/critique` pre-build, or by
  `/commit-slice` on ship — the shipped story is the keystone deliverable) or the user may be away (so it pushes
  to their phone); use `"normal"` when the user just invoked `/slice-story` themselves and is watching. Use the
  SAME value you recorded in `story-sections.json.delivery` at Step 3 (the artifact is the audit record).
- `caption`: one short line, e.g.
  `"Slice story — <slice-id> <title> (<stage-label>): a plain-language overview of the objective, how it's built, and what review changed."`

The local `<target-slice>/story.html` is the source of truth; `SendUserFile` surfaces that exact file. If the
delivery tool is somehow unavailable, fall back to telling the user the local path — never fail the skill.

## Step 6 — report and hand off

After delivering the file, tell the user plainly. **The hand-off depends on the slice's stage:**

**Pre-build / mid-lifecycle** (stage is NOT `shipped`):
```
Slice story ready — <slice-id> <title> (<stage-label>).
  • Delivered above (story.html) — open it for the full styled report.
  • Saved at: <target-slice>/story.html

A plain-language overview of the objective, what it took to get here, and — if review happened — what changed
because of it.

When you're ready, run /build-slice to start building. Tip: starting it in a fresh session
(/clear first) is cheaper and just as safe — all resume state lives in the vault
(milestone.json), and a lean context lets the build concentrate on this slice.
```

**Shipped** (stage == `shipped` — the on-ship auto-emit from `/commit-slice`, or a manual run against an archived
slice): the build already happened, so do NOT prompt `/build-slice` — it would strand the reader:
```
Slice story ready — <slice-id> <title> (shipped — story archived).
  • Delivered above (story.html) — the complete record of this shipped slice.
  • Saved at: <target-slice>/story.html

This slice has shipped; its full story — the objective, the approaches weighed, what review and reality testing
found, and what was learned — is archived alongside it. Nothing more to do here.
```

**Do NOT auto-advance to /build-slice.** This is a halt point: a pre-build reader reviews the story and starts the
build themselves (`/build-slice` is user-invoked by design; and a shipped slice has no build to start). If a
pre-build story surfaces something that should change the design, the user can loop back to `/design-slice` →
`/critique` before building.

## Critical rules

- USE the `Agent` tool (`subagent_type: "slice-story"`). The narrator does the synthesis; do not self-narrate in the main thread.
- Do NOT restate the narrator's persona/rules in the prompt — they live in `agents/slice-story.md`.
- NO pipeline jargon in the deliverable. The narrator handles translation; if you spot a leaked code (`AC2`, `C1`, `TRI-1`…) in the output, send it back to re-translate.
- RENDER story.html only from the story-sections.json the narrator wrote (Step-3-verified) — never fabricate the story.
- DELIVER the report with `SendUserFile` so it reaches the user wherever they are (phone included). Use `proactive` status when auto-invoked or the user may be away; `normal` when they're watching. Never fail the skill if delivery is unavailable — fall back to the local path.
- HALT after reporting — never auto-invoke `/build-slice` (it is user-invoked by design).
- READ-ONLY on source + the rest of the vault: this run writes only `story-sections.json` (the narrator) + `story.html` (the renderer), both in the slice folder.

## Anti-patterns

- Narrating in the main thread "to save a subagent call" — that re-pollutes the context this skill exists to keep lean.
- Letting internal codes leak into prose because "the engineer will understand" — the report is for both audiences; codes go in the small grey `ref` tags only.
- Padding the story with sections for stages that haven't happened (no build yet ⇒ no "what we built").
- Only naming the local file path and not delivering it — always `SendUserFile` the report so the user can open it directly (especially on phone).

## Pipeline position

- predecessor: `/critique` — auto-invoked only when the Critic ran and surfaced ≥1 finding (a zero-finding clean review or a skipped low-tier Critic hands straight to `/build-slice`); also user-invokable out-of-loop any time
- successor: `/build-slice`
- auto-advance: false — generate + deliver the report, then HALT and ask the user to run `/build-slice` when ready
- on-clean-completion: write `story-sections.json` + `story.html`, deliver `story.html` to the user via `SendUserFile` (proactive when auto-invoked / the user may be away), report the saved path, and prompt `/build-slice`. Do NOT auto-invoke the build.
- user-input gates (halt auto-advance): always halts at Step 6 — the user reviews the story and starts the build (or loops back to `/design-slice`) themselves.
