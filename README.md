# ai-sdlc

> Spec-driven AI SDLC pipeline, packaged as a single [Claude Code](https://code.claude.com/docs/en/claude-code) plugin.

`ai-sdlc` turns "build this feature" into a disciplined, reviewable pipeline built on one rule —
**diverse at generation · reality-grounded at selection · independent at review.** It gives you: risk-first
**slicing** with an in-loop **spike gate**; a tier-gated **design tournament** (blind designers generate, then
reality selects); **method-heterogeneous adversarial review** (a Builder plus forked Critics that decorrelate by
*method*, not just persona); a per-gate **measurement spine** (precision + recall, ranked by reality-contact);
machine-queryable **JSON vault artifacts**; and one unified **candidate backlog**. It ships **30 skills**, named
review + designer agents, a shared Python tooling library, and a SessionStart hook — all as one plugin.

The guiding philosophy: ground the model in **executable reality** (the code graph, throwaway spikes on the real
environment, tests, drift-checks) — never in external authority. Generate freely, then *prove against reality*; and
trust a gate exactly as much as it touches something that is **not the model** (reality > code-graph > model-critic).
*One deliberate exception:* authority may be **channeled at generation time** inside the design tournament (the
`designer-expert` flight) to widen the sample of approaches — never at **selection**. Reality and the synthesis
rules select; channeling an expert to *generate* a candidate is diversity, not deference.

The pipeline writes its artifacts to an **external vault** (outside your code repo) so the design record never
pollutes your source tree, and every project gets its own isolated, worktree-shared vault.

---

## Requirements

| Need | Why | Notes |
|------|-----|-------|
| **Claude Code** | the host runtime | desktop / CLI / IDE |
| **Python 3** | the bundled tooling scripts | resolved automatically; see [Python interpreter](#python-interpreter) to override |
| **PyYAML** | only `/diagnose` (+ a YAML fallback in `slice-candidates`) | installed for you by `/ai-sdlc:setup`; everything else is stdlib-only |
| **git** | recommended | gives each repo a stable, worktree-shared vault key (works without it, but the vault then keys on the current directory) |
| **code-review-graph** *(recommended)* | code-graph queries (blast-radius, reachability) in ~15 skills + the CRG MCP server | installed & registered by `/ai-sdlc:setup`; absent → graceful degrade |

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
> a tiny POSIX-bash bootstrap (it hands off to Python), so this works out of the box as long as Git for
> Windows (git-bash) is present.

### First run: `/ai-sdlc:setup`

After installing the plugin, run the one-time dependency doctor:

```
/ai-sdlc:setup
```

It resolves a working Python, installs the runtime deps (PyYAML + `code-review-graph`) **with visible
progress**, registers the `code-review-graph` **MCP server** for Claude Code (a project-scoped, gitignored
`.mcp.json`), builds the code graph, then prints the next steps. Two of them matter:

1. **Restart Claude Code** — MCP servers load at startup, so the CRG graph tools only appear on the next launch.
2. **Approve the one-time trust prompt** for `code-review-graph`; then `/mcp` should show it connected.

`/ai-sdlc:setup` is idempotent — re-run it any time the toolchain looks broken (e.g. a skill reports
`CRG_MISSING`). The SessionStart hook also nudges you to run it whenever a dependency is missing. Prefer a
silent, automatic install instead? Set `AI_SDLC_AUTO_INSTALL=1` before launching Claude Code and the hook
installs the deps itself (MCP registration still goes through `/ai-sdlc:setup`).

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

The SessionStart hook resolves an interpreter and persists it as `$PY` (forward-slash-normalized, so it survives the shell round-trip) for every skill. The hook is a tiny bash bootstrap (`hooks/setup-env.sh`) that hands all logic to a Python resolver (`hooks/setup_env.py`).
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

### Slice reports (`/slice-story`)

`/slice-story` renders a plain-language report of a slice (`story.html`) and **delivers it straight to you** — it's
pushed into the conversation, so it reaches you wherever you are, **including your phone over Remote Control**. The
file is also saved in the slice folder. No external service, no upload step, no extra permission.

---

## The pipeline at a glance

```
triage / adopt  →  discover  →  (user-test)
                                   │
        ┌──────────────────────────┘   per-slice loop
        ▼
   slice (pick candidate)
     → risk-spike                (FEASIBILITY — in-loop BLOCKING step-0: prove the candidate's assumptions, or block)
     → design-slice              (tier-gated design tournament: 2–3 blind designers generate → reality-grounded synthesis)
     → risk-spike --mode design  (post-synthesis: reality adjudicates the tournament's empirically-decidable disagreements)
     → critique  (+ critique-review)
     → slice-story               (plain-language report of the slice, delivered straight to you — phone included)
     → build-slice
     → code-review
     → validate-slice
     → reflect  →  next slice
                                   commit-slice finalizes a slice
```

- **Measurement spine:** every gate logs one row to `<vault>/gate-log.json` ranked by **reality-contact**
  (real env/device/data > code-graph > model-on-model), so per-gate **precision + recall** is measurable and a
  reality-approval is never rendered the same green as a model-approval. `/pulse` surfaces it.
- **Brownfield entry:** `diagnose` (forensic, owner-facing HTML report) → `slice-candidates` (annotated report →
  backlog); `bug-hunt` (whole-codebase correctness + security defect sweep).
- **Orientation:** `pulse`, `query-design`.
- **Maintenance:** `drift-check`, `reduce`, `archive`, `sync`, `supersede-slice`, `critic-calibrate`,
  `product-doc` (grounded README / CHANGELOG / API-ref / user-guide).
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
agents/                named Critic / worker personas (system prompts)
scripts/lib/           shared Python tooling used by >1 skill (vault_edit, the vault-root resolver, …)
hooks/                 SessionStart hook: setup-env.sh (bootstrap shim) + setup_env.py (resolver)
schemas/               artifact schemas-by-example
.build/                reproducible build pipeline: source manifests + the aggregator + CI audits
requirements.txt       runtime Python deps (PyYAML; optional code-review-graph)
tests/ + .github/      pytest suite + CI for the plugin itself
```

### Development

Each skill's `examples/` are **generated** from `schemas/artifact-examples.json` by the aggregator; `SKILL.md`
and `scripts/` are hand-authored. To regenerate the bundled examples:

```bash
python3 .build/aggregate.py
```

> The aggregator also produces a diffable **design record** (`skill.json` ×30 + `skill-graph.json`) from the
> source manifests in `.build/manifests/`. That record has no runtime consumers — the harness loads `SKILL.md`,
> not these — so it is **not shipped** (git-ignored); it is kept locally as authoring history. The runnable
> contract is the `SKILL.md` set.

A re-runnable cross-block-var audit guards the SKILL.md bash blocks:

```bash
python3 .build/cross_block_audit.py skills/*/SKILL.md
```

---

## License

Licensed under the **MIT License** — see [LICENSE](LICENSE).

## Author

Shubhendu Shubham · plugin `ai-sdlc` v2.21.0
