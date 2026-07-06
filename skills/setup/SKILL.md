---
name: setup
description: "One-shot dependency doctor + installer for the AI SDLC pipeline. A tiny bash bootstrap resolves a working Python 3, then hands ALL work to scripts/setup.py — which installs the runtime deps (PyYAML + code-review-graph) with VISIBLE streaming progress, verifies them, registers the code-review-graph MCP server for Claude Code (project .mcp.json, gitignored because the path is machine-specific), builds the code graph, and prints a status report plus the exact next steps (restart Claude Code + approve the MCP trust prompt). Idempotent — safe to re-run; installs only what's missing. Fixes the silent/opaque first-run SessionStart-hook install by doing it interactively where the user can see it."
when_to_use: "Trigger phrases: /setup, /ai-sdlc:setup, 'set up ai-sdlc', 'install pipeline dependencies', 'fix CRG', 'code-review-graph not found', 'CRG MCP not showing', 'dependency doctor', 'why is CRG missing', '$PY broken'. Run ONCE right after installing the ai-sdlc plugin (or whenever a skill reports a missing dep / CRG_MISSING / a broken interpreter), BEFORE /triage or /adopt. Re-run any time the toolchain looks broken. Out-of-loop maintenance — not part of the per-slice loop."
argument-hint: "[--no-mcp] [--no-graph] [repo-path]"
user-invocable: true
allowed-tools: Read, Bash, AskUserQuestion
---

# /setup — AI SDLC dependency doctor + installer

Solves the pipeline's dependency setup in one visible pass, then tells the user exactly what to do next.
This exists because the SessionStart hook's auto-install is **invisible** (hook output never reaches the
terminal) and **blocks startup opaquely** — so the heavy, user-facing install belongs in an interactive skill
where pip output streams live.

> **Architecture:** all logic lives in `scripts/setup.py` (path / git / gitignore via `os`/`pathlib`/`subprocess`,
> no bash-on-Windows traps). The bash blocks here are pure bootstrap — they resolve a working interpreter into
> `$PYX` and exec `setup.py`. `$PYX` is re-derived in each block (skill bash vars don't persist): hook `$PY` if it's
> a real file → else a forward-slash-normalized `$AI_SDLC_PY` → else `python3`/`python`/`py`. `setup.py` then drives
> everything (including CRG) through `sys.executable`, never `$CRG` (stale until next launch) or a bare `$PY`.

## Live state — injected

Current toolchain state (read-only — `setup.py --check` makes no changes):
```!
PYX="${PY:-}"; [ -f "$PYX" ] || PYX="$(printf '%s' "${AI_SDLC_PY:-}" | tr '\134' '/')"; [ -f "$PYX" ] || PYX="$(command -v python3 || command -v python || command -v py || true)"
[ -n "$PYX" ] && "$PYX" "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check 2>&1 || echo "interpreter: NONE FOUND — install Python 3 or set AI_SDLC_PY (forward slashes)"
```

Parse the injected state. If `interpreter: NONE FOUND`, **STOP** and tell the user to install Python 3 (or
`export AI_SDLC_PY="C:/Users/you/.../python.exe"` with forward slashes), then restart and re-run `/setup`.
Otherwise announce a one-line plan and run the install step.

## Run setup

Bootstrap a working interpreter and hand off to `setup.py` (it streams pip/CRG output so the user sees progress,
then prints a status report + next steps). Pass through the user's flags/args verbatim:
```bash
PYX="${PY:-}"; [ -f "$PYX" ] || PYX="$(printf '%s' "${AI_SDLC_PY:-}" | tr '\134' '/')"; [ -f "$PYX" ] || PYX="$(command -v python3 || command -v python || command -v py || true)"; [ -n "$PYX" ] || { echo "FATAL: no Python 3 found — install one or set AI_SDLC_PY (forward slashes), then re-run /setup"; exit 1; }
# ${ARGUMENTS[@]} UNQUOTED: under ARRAY binding it expands per-element; bare $ARGUMENTS sees only
# element 0 (so `/setup --no-mcp --no-graph` would silently drop --no-graph). Under SCALAR binding
# the unquoted form word-splits — every flag still arrives (a quoted "${ARGUMENTS[@]}" would glue
# them into ONE argv token there). Caveat: a repo-path arg containing spaces needs a scalar-bound
# quoted form — pass such a path by cd'ing to it instead.
"$PYX" "${CLAUDE_SKILL_DIR}/scripts/setup.py" ${ARGUMENTS[@]}
```

Then:
- **Relay `setup.py`'s report verbatim** — the status table + NEXT STEPS block are the user's deliverable.
- If `setup.py` exits non-zero, surface its `FATAL:` line and the fix it prints; do not pretend success.
- The MCP server **cannot** go live this session (Claude Code reads MCP config at startup) — the report's
  "RESTART Claude Code" step is mandatory, not optional. Reinforce it.

## What this skill does NOT do

- **No vault writes, no source edits.** `setup.py` only installs deps, writes `<repo>/.mcp.json` (+ a `.gitignore`
  line), and builds `<repo>/.code-review-graph/`. It is not part of the per-slice loop and produces no pipeline artifacts.
- **It cannot make the MCP server live this session** — a Claude Code lifecycle fact (MCP loads at startup, before
  skills run). The honest deliverable is "installed + registered, now restart."

## Pipeline position

Out-of-loop maintenance. Run before `/triage` or `/adopt`. No predecessor, no auto-advance successor — it hands
the user back to the opener with a restart in between.
