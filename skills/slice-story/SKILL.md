---
name: slice-story
description: "Turns one slice's internal artifacts into a single plain-language STORY for a mixed technical / non-technical audience (tilted slightly technical, ZERO pipeline jargon), rendered as a standalone story.html. Adaptive across the lifecycle: before building it covers the objective, what was proven in spikes, how it's built, and what the design review changed; after building it adds what was built, what code review found, what reality testing showed, and what was learned. A forked slice-story narrator subagent does the heavy synthesis and returns structured JSON; render_story.py renders the HTML; the report is saved in the slice folder AND pushed to Google Drive; then you're asked to start /build-slice when ready."
when_to_use: "Trigger phrases: /slice-story, 'tell the story of this slice', 'plain-language slice report', 'overview before build', 'explain this slice for non-engineers'. Runs automatically just after /critique (the pre-build report), and is user-invokable any time to get a readable report of where a slice stands. Optional arg: a slice id (default: the active slice); --no-drive to skip the Google Drive upload."
argument-hint: "[slice-id] [--no-drive]"
allowed-tools: Read, Write, Bash, Agent, mcp__claude_ai_Google_Drive__create_file
---

# /slice-story — the slice's story, in plain language

Produce **one standalone `story.html`** that tells the story of a slice so a product owner with no engineering
background AND the engineer about to build it can both follow it. Slightly technical, but plain English first —
and **no pipeline jargon** (`AC2`, `C1`, `TRI-1`, "the Critic", "dispositions"…) ever reaches the page.

The heavy synthesis is delegated to a forked **`slice-story` narrator subagent** so the parent context stays
lean. This skill orchestrates: resolve the slice → spawn the narrator → render the HTML → save it in the slice
folder AND push it to Google Drive → ask you to run `/build-slice` when ready. It never modifies source files.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT` /
> git config `aisdlc/vault-root`). Active slice = latest `<vault>/slices/slice-NNN-*/` (not under `archive/`).

## Live state — injected

Active slice + which artifacts exist (drives which sections the story includes):
```!
VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"
$PY -c "import json,glob,os,sys; v=sys.argv[1]; ds=sorted([d for d in glob.glob(f'{v}/slices/slice-*/') if 'archive' not in d]); d=ds[-1] if ds else None; have=[os.path.basename(f) for f in sorted(glob.glob(f'{d}/*.json'))] if d else []; mb=json.load(open(f'{d}/mission-brief.json')) if d and os.path.exists(f'{d}/mission-brief.json') else {}; ms=json.load(open(f'{d}/milestone.json')) if d and os.path.exists(f'{d}/milestone.json') else {}; print(json.dumps({'active_slice_dir':d,'slice':mb.get('slice'),'title':mb.get('title'),'mode':mb.get('mode'),'risk_tier':mb.get('risk_tier'),'stage':ms.get('stage'),'artifacts_present':have},indent=2))" "$VAULT" 2>/dev/null || echo "{}"
```

## Step 0 — resolve the target slice

From the injection above take `active_slice_dir` (absolute path), `slice`, `title`, `mode`, `risk_tier`, `stage`.
If `$ARGUMENTS` names a slice id (a non-flag token like `slice-017`), target that folder instead
(`<vault>/slices/<that-folder>/`). `--no-drive` in `$ARGUMENTS` skips the upload step.

Prerequisite: the folder must contain at least `mission-brief.json`. If it doesn't, STOP:
_"No slice to tell a story about — run /slice first."_ If `design.json` is absent, proceed but tell the user the
story will be thin (objective + acceptance outcomes only — there's no design or review to narrate yet).

Decide the **furthest lifecycle stage** from which artifacts are present:
`reflection.json` → `shipped` · `validation.json` → `validated` · `code-review.json` → `reviewed` ·
`build-log.json` → `built` · else → `pre-build`. This becomes the story's `stage`.

## Step 1 — gather the artifacts for the narrator

Read (from the target slice folder, whichever exist) so you can hand their contents to the narrator:
`mission-brief.json`, `design.json`, `critique.json`, `critique-review.json`, `milestone.json`, and any
`build-log.json` / `code-review.json` / `validation.json` / `reflection.json`. Also read this slice's spikes
(`<vault>/spikes/spike-*.json` whose `candidate` / `slice` ties to this slice) and the ADRs this slice locked
(`design.json.adrs[]` → `<vault>/decisions/ADR-*.json`).

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

**Await the real agent — never fabricate its output.** The narrator returns ONE JSON object (the
`aisdlc/story-sections@1` schema; see `examples/story-sections.json`) as its final message. Build the report
ONLY from what it returns.

If the returned text is wrapped in a ```` ```json ```` fence or has stray prose around it, strip to the JSON
object before writing. If it is not valid JSON, ask the narrator to re-emit raw JSON only — do not hand-write
the story yourself (that defeats the plain-language synthesis).

## Step 3 — write story-sections.json

Write the narrator's returned object to `<target-slice>/story-sections.json` (raw-write — this is a per-slice
active-folder artifact, not a shared aggregate, so SVW-1's `vault_edit` requirement does not apply). Set/keep
`slice`, `title`, `stage`, `mode`, `risk_tier` consistent with Step 0 if the narrator left any blank.

## Step 4 — render story.html

```bash
$PY "${CLAUDE_SKILL_DIR}/scripts/render_story.py" \
    --sections-file "<target-slice>/story-sections.json" \
    --out "<target-slice>/story.html"
```

`render_story.py` is deterministic, stdlib-only, and stamps the generation time. On a non-zero exit, report the
stderr and stop — do not hand-assemble HTML.

## Step 5 — push to Google Drive

Skip this step entirely if `--no-drive` was passed, or if the `mcp__claude_ai_Google_Drive__create_file` tool
is not available in this session (Drive MCP not connected) — in that case just note "Google Drive not connected;
report saved locally only" and continue.

Otherwise read `<target-slice>/story.html` and upload it:

- tool: `mcp__claude_ai_Google_Drive__create_file`
- `title`: `"<slice-id> — <title> — slice story (<stage-label>).html"` (stage-label = the plain stage, e.g. "before building")
- `textContent`: the full story.html contents
- `contentMimeType`: `"text/html"`
- `disableConversionToGoogleType`: `true`  (keep it a real HTML file, not a converted Google Doc)
- `parentId`: if `$AI_SDLC_DRIVE_FOLDER` is set (or git config `aisdlc/drive-folder` resolves a folder id), pass it; otherwise omit (uploads to Drive root).

Capture the returned file's link/id. If the upload errors, report the error but DO NOT fail the skill — the
local `story.html` is the source of truth; Drive is a convenience copy.

## Step 6 — report and hand off

Tell the user, plainly:

```
Slice story ready — <slice-id> <title> (<stage-label>).
  • Saved:  <target-slice>/story.html
  • Drive:  <link>            (or "not connected / skipped")

This is a plain-language overview of the objective, what it took to get here, and — if review happened —
what changed because of it. Open it, share it, or read it yourself.

When you're ready, run /build-slice to start building.
```

**Do NOT auto-advance to /build-slice.** This is a halt point: the user reviews the story and starts the build
themselves (`/build-slice` is user-invoked by design). If the story surfaces something that should change the
design, the user can loop back to `/design-slice` → `/critique` before building.

## Critical rules

- USE the `Agent` tool (`subagent_type: "slice-story"`). The narrator does the synthesis; do not self-narrate in the main thread.
- Do NOT restate the narrator's persona/rules in the prompt — they live in `agents/slice-story.md`.
- NO pipeline jargon in the deliverable. The narrator handles translation; if you spot a leaked code (`AC2`, `C1`, `TRI-1`…) in the output, send it back to re-translate.
- WRITE story.html from the narrator's returned JSON only — never fabricate the story.
- The Google Drive push is OUTWARD. It happens because this skill's job is to publish the report; honor `--no-drive` and degrade gracefully when Drive isn't connected. Never block the skill on an upload failure.
- HALT after reporting — never auto-invoke `/build-slice` (it is user-invoked by design).
- READ-ONLY on source + the rest of the vault: this skill writes only `story-sections.json` + `story.html` in the slice folder (plus the Drive copy).

## Anti-patterns

- Narrating in the main thread "to save a subagent call" — that re-pollutes the context this skill exists to keep lean.
- Letting internal codes leak into prose because "the engineer will understand" — the report is for both audiences; codes go in the small grey `ref` tags only.
- Padding the story with sections for stages that haven't happened (no build yet ⇒ no "what we built").
- Failing the skill because Google Drive wasn't connected — the local report still shipped.

## Pipeline position

- predecessor: `/slice-story` follows `/critique` (the pre-build report); also user-invokable out-of-loop any time
- successor: `/build-slice`
- auto-advance: false — generate + publish the report, then HALT and ask the user to run `/build-slice` when ready
- on-clean-completion: write `story-sections.json` + `story.html`, push to Google Drive (unless `--no-drive` / not connected), report both locations, and prompt `/build-slice`. Do NOT auto-invoke the build.
- user-input gates (halt auto-advance): always halts at Step 6 — the user reviews the story and starts the build (or loops back to `/design-slice`) themselves.
