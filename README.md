# ai-sdlc

> Spec-driven AI SDLC pipeline, packaged as a single [Claude Code](https://code.claude.com/docs/en/claude-code) plugin.

`ai-sdlc` turns "build this feature" into a disciplined, reviewable pipeline: risk-first **slicing** with an
in-loop **spike gate**, two-persona **adversarial review** (a Builder and a forked Critic), machine-queryable
**JSON vault artifacts**, and one unified **candidate backlog**. It ships 26 skills, named review agents, a
shared Python tooling library, and a SessionStart hook — all as one plugin.

The pipeline writes its artifacts to an **external vault** (outside your code repo) so the design record never
pollutes your source tree, and every project gets its own isolated, worktree-shared vault.

---

## Requirements

| Need | Why | Notes |
|------|-----|-------|
| **Claude Code** | the host runtime | desktop / CLI / IDE |
| **Python 3** | the bundled tooling scripts | resolved automatically; see [Python interpreter](#python-interpreter) to override |
| **PyYAML** | only `/diagnose` (+ a YAML fallback in `slice-candidates`) | `pip install pyyaml`; everything else is stdlib-only |
| **git** | recommended | gives each repo a stable, worktree-shared vault key (works without it, but the vault then keys on the current directory) |
| **code-review-graph** *(optional)* | blast-radius coupling in `/slice-candidates` + structural queries in several skills | `pip install code-review-graph`; absent → graceful degrade |

---

## Installation

This repo is its own marketplace (it ships `.claude-plugin/marketplace.json`). Add it, then install:

```bash
/plugin marketplace add sshubham2/aisdlc-v2
/plugin install ai-sdlc@ai-sdlc
```

`/plugin marketplace add` also accepts a full git URL (`https://github.com/sshubham2/aisdlc-v2.git`) or a local
path. The plugin's **SessionStart hook fires automatically** once installed — no extra step — resolving a Python
interpreter and exposing it to every skill.

> **Windows:** Claude Code runs plugin hooks via the bundled **git-bash**, not PowerShell. The shipped hook is
> POSIX bash, so this works out of the box as long as Git for Windows (git-bash) is present.

### Local development

To iterate on a local clone without publishing:

```bash
claude --plugin-dir /path/to/aisdlc-v2     # load directly, no install
```

Or register the local checkout as a marketplace:

```bash
/plugin marketplace add ./        # from the repo root
/plugin install ai-sdlc@ai-sdlc
```

---

## Configuration

Two things are environment-resolved at runtime: **which Python** the pipeline runs, and **where the vault lives.**
Neither is hard-coded.

### Python interpreter

The SessionStart hook (`hooks/setup-env.sh`) resolves an interpreter and persists it as `$PY` for every skill.
Resolution order:

```
$AI_SDLC_PY  →  python3  →  python  →  py
```

It prefers an interpreter that can `import yaml` (only `/diagnose` needs it) and falls back to the first that
merely exists, warning on stderr if none has PyYAML.

**To use a Python in a custom location**, set `AI_SDLC_PY` to the interpreter's absolute path *before launching
Claude Code*:

```bash
# Linux / macOS — add to ~/.bashrc or ~/.zshrc to make it stick
export AI_SDLC_PY="/opt/python-3.13/bin/python3"
```

```powershell
# Windows PowerShell
$env:AI_SDLC_PY = "D:\Python313\python.exe"                                       # current shell
[Environment]::SetEnvironmentVariable("AI_SDLC_PY","D:\Python313\python.exe","User")  # persistent
```

It must be a Python **3** interpreter. If it lacks PyYAML, `pip install pyyaml` into *that* interpreter.

### Vault location

Each project's vault resolves to `<base>/<project-slug>-<hash>` — e.g. `~/.aisdlc/aisdlc-v2-a5c48e41`. The
`<hash>` is `sha256` of the repo's absolute git-common-dir (so all worktrees of a repo share one vault, and the
key is checkout-location-specific by design). Resolution is **3 tiers, highest precedence first:**

| # | Lever | Scope | Sets | Use when |
|---|-------|-------|------|----------|
| 1 | `AI_SDLC_VAULT_ROOT` env var | the shell / process | the **exact** vault dir | one-off or single-project override; test injection |
| 2 | `<git-common-dir>/aisdlc/vault-root` file (e.g. `.git/aisdlc/vault-root`) | **one repo** (+ its worktrees) | the **exact** vault dir | relocate a single project's vault, persistently |
| 3 | `~/.claude/ai-sdlc-vault-base` file | **all projects on this machine** | the **base** dir (keeps `<slug>-<hash>` auto-naming) | move every vault off the default `~/.aisdlc` parent |

Tiers 1 & 2 pin an **exact directory** (no `<slug>-<hash>` appended). Tier 3 changes only the **parent** and keeps
per-project auto-naming. None of these files is git-tracked.

**Example — change the default base for a whole machine** (e.g. on a Linux box, from `~/.aisdlc` to `/collab/.aisdlc`):

```bash
mkdir -p ~/.claude
printf '/collab/.aisdlc\n' > ~/.claude/ai-sdlc-vault-base   # tier 3: base for ALL projects
mkdir -p /collab/.aisdlc                                     # ensure it exists / is writable
```

Every project then resolves to `/collab/.aisdlc/<slug>-<hash>`.

**Example — pin just one repo** (tier 2):

```bash
mkdir -p "$(git rev-parse --git-common-dir)/aisdlc"
echo "/data/vaults/my-project" > "$(git rev-parse --git-common-dir)/aisdlc/vault-root"
```

**Verify which tier won:**

```bash
python3 /path/to/aisdlc-v2/scripts/lib/_vault_paths.py
# vault-root: /collab/.aisdlc/aisdlc-v2-XXXXXXXX
# source:     computed external-store default (base /collab/.aisdlc)   # or: env / git-common-dir config
```

> **Moving an existing vault is not automatic.** If a vault already holds artifacts, copy its contents to the new
> location yourself before switching. Precedence means a project with an env var or git-pin set will ignore the
> base file.

---

## The pipeline at a glance

```
triage / adopt  →  discover  →  (user-test)
                                   │
        ┌──────────────────────────┘   per-slice loop
        ▼
   slice (pick candidate)
     → risk-spike     (in-loop BLOCKING spike gate: prove the candidate's assumptions, or block)
     → design-slice
     → critique  (+ critique-review)
     → build-slice
     → code-review
     → validate-slice
     → reflect  →  next slice
                                   commit-slice finalizes a slice
```

- **Brownfield entry:** `diagnose` (forensic, owner-facing HTML report) → `slice-candidates` (annotated report → backlog).
- **Orientation:** `pulse`, `query-design`.
- **Maintenance:** `drift-check`, `reduce`, `archive`, `sync`, `supersede-slice`, `critic-calibrate`.
- **Heavy-mode only:** `heavy-architect`, `sync`.

Backlog of work lives in `<vault>/candidates.json` (live) and `<vault>/archive/candidates.json` (shipped); the
risk ledger is `<vault>/risk-register.json`.

Invoke any skill with its slash command — the plugin namespaces them, so `/diagnose` is `/ai-sdlc:diagnose`, etc.

---

## Layout

```
.claude-plugin/
  plugin.json          plugin manifest (name: ai-sdlc)
  marketplace.json     self-hosted marketplace entry (makes the repo installable)
skills/<name>/
  SKILL.md             the runnable skill
  examples/            output JSON examples bundled with the skill
  scripts/             single-skill tools
  skill.json           generated design manifest (see below)
agents/                named Critic / worker personas (system prompts)
scripts/lib/           shared Python tooling used by >1 skill (vault_edit, the vault-root resolver, …)
hooks/                 SessionStart hook (resolves $PY) + setup-env.sh
schemas/               artifact schemas-by-example
skill-graph.json       the whole pipeline as one dependency graph (generated)
.build/                reproducible build pipeline: source manifests + the aggregator
requirements.txt       runtime Python deps (PyYAML; optional code-review-graph)
```

### Development

`skill.json` (×26), each skill's `examples/`, and `skill-graph.json` are **generated** from the source manifests
in `.build/manifests/` — they are the diffable *design record*, not hand-maintained. `SKILL.md` and `scripts/`
are hand-authored. To change a skill's design metadata, edit its `.build/manifests/batch*.json` entry and
regenerate:

```bash
python3 .build/aggregate.py
```

A re-runnable cross-block-var audit guards the SKILL.md bash blocks:

```bash
python3 .build/cross_block_audit.py skills/*/SKILL.md
```

---

## Author

Shubhendu Shubham · plugin `ai-sdlc` v2.0.3
