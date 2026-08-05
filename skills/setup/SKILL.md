---
name: setup
description: "One-shot dependency doctor + installer for the AI SDLC pipeline. A tiny bash bootstrap resolves a working Python 3, then hands ALL work to scripts/setup.py — which installs the runtime deps (PyYAML + code-review-graph) with VISIBLE streaming progress, verifies them, registers the code-review-graph MCP server for Claude Code (project .mcp.json, gitignored because the path is machine-specific), builds the code graph, and prints a status report plus the exact next steps (restart Claude Code + approve the MCP trust prompt). Idempotent — safe to re-run; installs only what's missing."
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

## Offer to commit the ai-sdlc config (consented)

`/setup` scaffolds `<repo>/.aisdlc/reality-gates.json` and appends ignore lines to `<repo>/.gitignore` —
repo-tracked config that is **meant to be committed** (it must travel to teammates + CI), but which `setup.py`
leaves *uncommitted*. Left that way, it makes the main tree dirty and trips the **WT-ROOT-1** pristine-main-tree
check on the first `/build-slice`. So close the loop here, **only with consent** (a git side-effect on the user's repo):

- If `setup.py`'s report contains an **`UNCOMMITTED AI-SDLC CONFIG`** block, `AskUserQuestion`: commit those files
  now? (Recommended — sends declared reality-gates to CI + keeps the tree clean for slice builds.) Show the exact
  paths it listed so consent is informed. `.mcp.json` is deliberately **not** offered (machine-specific + gitignored).
- **On yes**, run the actuator (it is pathspec-scoped — it commits ONLY those two files, never the user's other
  staged work — and is guarded: a visible no-op on a non-git repo / detached HEAD / nothing-to-commit):
  ```bash
  PYX="${PY:-}"; [ -f "$PYX" ] || PYX="$(printf '%s' "${AI_SDLC_PY:-}" | tr '\134' '/')"; [ -f "$PYX" ] || PYX="$(command -v python3 || command -v python || command -v py || true)"
  "$PYX" "${CLAUDE_SKILL_DIR}/scripts/setup.py" --commit
  ```
- **On no**, leave it — the report already told the user how to commit it themselves later.
- If the report has **no** `UNCOMMITTED AI-SDLC CONFIG` block (not a git repo, or already committed on a re-run),
  skip this step silently.

## Configure the vault base + sync backend (consented)

Two **additive, consented, read-then-write** steps that wire /setup's chosen backend so a later
`vault_admin sync` resolves it with **zero manual env export** (slice-097 / SC-206 / ADR-121 + ADR-123).
Both run **AFTER** the deps→verify→MCP→graph→report flow above — never before or inside the deps
install — so a config-persist failure (the actuator's **exit 3**) is fail-visible and **cannot abort
the dependency install** (AC5, structural by ordering). Skip both silently on a non-git repo, or if the
user declines. The `setup.py --check` surface at the top already shows the current **vault base**,
**vault (repo)**, and **sync backend** — use it to frame the offer.

### Step A — vault base + pin (AskUserQuestion)

Show the resolved **vault base** and **this-repo vault path** from the `--check` surface. `AskUserQuestion`:
pin this repo's vault (so a repo move/rename doesn't orphan it) and — only if they want a non-default
base — set the base dir? On consent, run the actuators (idempotent; each read-back-verifies and is a
visible no-op on a re-run):
```bash
PYX="${PY:-}"; [ -f "$PYX" ] || PYX="$(printf '%s' "${AI_SDLC_PY:-}" | tr '\134' '/')"; [ -f "$PYX" ] || PYX="$(command -v python3 || command -v python || command -v py || true)"
VA="${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py"
# only if the user chose a custom base dir:
# "$PYX" "$VA" set-base "<base-dir>"
"$PYX" "$VA" write-pin
```
Relay each actuator's output. A **non-zero exit** (2 = not a git tree; 3 = write/read-back failure) is
surfaced, not swallowed — it never touches the deps install (already complete above).

### Step B — sync backend picker (AskUserQuestion)

`AskUserQuestion` offering exactly **{local, git, s3}** (default **local** = no remote sync). Then run the
actuator with the chosen backend. **Credentials are NEVER prompted for or persisted** — S3 auth is the
**boto3 default provider chain** (`AWS_ACCESS_KEY_ID` / `~/.aws` / an IAM role); say so in the picker.
For **s3**, gather only the **non-secret** fields (bucket, optional endpoint, optional region, and a
**machine-invariant project id** → the S3 key prefix, stable per-project so two machines pull the SAME
prefix). A userinfo-bearing endpoint (`user:pass@host`) is **refused** (exit 2) — use a bare
`https://host:port`.
```bash
PYX="${PY:-}"; [ -f "$PYX" ] || PYX="$(printf '%s' "${AI_SDLC_PY:-}" | tr '\134' '/')"; [ -f "$PYX" ] || PYX="$(command -v python3 || command -v python || command -v py || true)"
VA="${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_admin.py"
# local (default): "$PYX" "$VA" set-backend --backend local
# git:             "$PYX" "$VA" set-backend --backend git --remote origin
# s3:              "$PYX" "$VA" set-backend --backend s3 --s3-bucket <bucket> \
#                     [--s3-endpoint https://host:port] [--s3-region <region>] --s3-project <stable-id>
```
Relay the actuator output: it echoes the persisted choice + config location (**never a secret**), and on
s3 with boto3 absent it WARNs + prints the `pip install boto3` hint but **still persists** (exit 0 — never
force-install). If it names a **shadowing** real-env `AISDLC_S3_*`, tell the user to unset it (env beats
the persisted file).

> **[aisdlc:sync-backend-setdefault -- doc-guarded: how the persisted config reaches a later session.**
> `vault_admin set-backend` records the chosen backend + non-secret s3 fields (bucket, endpoint,
> **region**, project) to the UNTRACKED `<git-common-dir>/aisdlc/sync-backend.json` (sibling of the
> vault-root pin). At sync time `cmd_sync` loads that file and folds its fields — **including region** —
> into `os.environ` via **`setdefault`** (never clobbering a real env var), then calls the **UNCHANGED**
> shipped `resolve_config`, so effective precedence is **CLI-arg > real-env > file > computed-default**
> and **no credential is ever written** (boto3 default chain). Keep this wiring: dropping the region fold
> silently degrades an `eu-west-1` pick to `us-east-1`.]**

## What this skill does NOT do

- **No vault CONTENT writes, no source edits.** `setup.py` only installs deps, writes `<repo>/.mcp.json` (+ a
  `.gitignore` line), scaffolds `<repo>/.aisdlc/reality-gates.json`, and builds `<repo>/.code-review-graph/`. The
  git side-effect it can make is the **consented** `--commit` of its own config above (`.aisdlc/reality-gates.json`
  + `.gitignore`) — never source, never the user's other staged work. The **consented** base/backend steps write
  only the vault-root **pin** and the **sync-backend config** at `<git-common-dir>/aisdlc/` (UNTRACKED, sibling of
  the pin) — machine-local config, **not** vault content and never inside the vault. No pipeline artifacts.
- **It cannot make the MCP server live this session** — a Claude Code lifecycle fact (MCP loads at startup, before
  skills run). The honest deliverable is "installed + registered, now restart."

## Pipeline position

Out-of-loop maintenance. Run before `/triage` or `/adopt`. No predecessor, no auto-advance successor — it hands
the user back to the opener with a restart in between.
