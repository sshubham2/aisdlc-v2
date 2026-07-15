# ai-sdlc

> Spec-driven AI SDLC pipeline, packaged as a single [Claude Code](https://code.claude.com/docs/en/claude-code) plugin.

`ai-sdlc` turns "build this feature" into a disciplined, reviewable pipeline built on one rule —
**diverse at generation · reality-grounded at selection · independent at review.** It gives you: risk-first
**slicing** with an in-loop **spike gate**; an always-on **design tournament** (all 3 blind designers generate on
every slice, then reality selects); **method-heterogeneous adversarial review** (a Builder plus forked Critics that decorrelate by
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
| **PyYAML** | `/diagnose` + `/bug-hunt` (shared pass/finding tooling) + a YAML fallback in `slice-candidates` | installed for you by `/ai-sdlc:setup`; everything else is stdlib-only |
| **git** | recommended | gives each repo a stable, worktree-shared vault key (works without it, but the vault then keys on the current directory) |
| **code-review-graph** *(recommended)* | code-graph queries (blast-radius, reachability) in ~15 skills + the CRG MCP server | installed & registered by `/ai-sdlc:setup`; absent → graceful degrade |
| **bandit + pip-audit** *(optional, Python projects)* | the deterministic security reality gates `/build-slice` and `/validate-slice` run | **not** installed by `/ai-sdlc:setup` — `pip install bandit pip-audit`, or the gate fails **visibly** (`TOOL-MISSING`), never silently |

> **Supply-chain / trust boundary.** `code-review-graph` is a **third-party** package
> ([github.com/tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph), not this
> project's author). `/ai-sdlc:setup` installs it and registers it as a **trusted MCP server** in your
> project's gitignored `.mcp.json`, and it feeds the "reality > code-graph" trust tier — so it has real
> blast radius. It is pinned to `>=2.3,<3` in `requirements.txt`; setup prints the resolved version before
> registering, and you approve a one-time trust prompt. For a reproducible install, pin an exact version.

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
`.mcp.json`), scaffolds a repo-tracked `.aisdlc/reality-gates.json` (the pluggable reality-gate declaration that
`/build-slice`'s pre-finish gate and `/validate-slice` run via `scripts/lib/reality_gate_runner.py`). On a Python
project (source files and/or a `requirements*.txt` present) it also seeds two deterministic **security reality
gates** into that manifest — `bandit` (SAST, fails closed on any HIGH finding) and `pip-audit` (dependency CVEs)
— and vendors the fail-closed guard to `.aisdlc/gates/py_security_gate.py`; `bandit`/`pip-audit` themselves are
**not** installed for you, so a missing tool fails the gate **visibly** (`TOOL-MISSING`), never a silent pass.
It then builds the code graph and prints the next steps — including, on a git repo, a consented offer to commit
that new `.aisdlc/` config so it reaches your teammates and CI. Two of the next steps matter most:

1. **Restart Claude Code** — MCP servers load at startup, so the CRG graph tools only appear on the next launch.
2. **Approve the one-time trust prompt** for `code-review-graph`; then `/mcp` should show it connected.

`/ai-sdlc:setup` accepts optional flags: `--no-mcp` (skip MCP registration) and `--no-graph` (skip the initial
graph build), plus an optional `[repo-path]` argument to target a specific directory.

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

The vault resolves **per-invocation** from these tiers — the SessionStart hook does *not* freeze it, so
mid-session work on a different repo (e.g. `/bug-hunt <other-path>`, `/diagnose <other-path>`) resolves *that*
repo's vault, not the one you started in. Tier 1 (`AI_SDLC_VAULT_ROOT`) is purely an explicit user override; the
hook does not auto-set it.

**Example — change the default base for a whole machine** (e.g. on a Linux box, from `~/.aisdlc` to `/collab/.aisdlc`):

```bash
mkdir -p ~/.claude
printf '/collab/.aisdlc\n' > ~/.claude/ai-sdlc-vault-base   # tier 3: base for ALL projects
mkdir -p /collab/.aisdlc                                     # ensure it exists / is writable
```

Every project then resolves to `/collab/.aisdlc/<slug>-<hash>`.

**Lifecycle, backup & GC.** The vault is plain JSON outside your repo, so it is **not** covered by your repo's
git history. To version or back it up, `git init` inside the vault dir (`cd "$(… _vault_paths.py --path)" && git init`)
or copy it anywhere. `/triage` and `/adopt` write the **tier-2 pin** automatically so a repo move/rename doesn't
orphan the vault. To audit or GC machine-wide:

```bash
$PY scripts/lib/vault_admin.py list                 # every vault under the base + orphan status
$PY scripts/lib/vault_admin.py uninstall <name> --yes   # delete an orphaned vault
```

**Export / import (backup + team handoff).** The design record is the most valuable thing the pipeline
produces — give it at least the durability of the code it explains:

```bash
$PY scripts/lib/vault_admin.py export                       # -> ./<vault-name>-vault.tgz (this repo's vault)
$PY scripts/lib/vault_admin.py import <archive>.tgz         # restore on another machine / for a new teammate
$PY scripts/lib/vault_admin.py write-pin                    # then pin the imported vault to the repo
```

`import` refuses a non-empty target without `--force`. Put `export` in a cron/backup job if the project is
long-lived — a machine wipe should never be able to erase the project's risk ledger and decision history.

### CI merge gate (optional — make the pipeline enforced, not just followed)

By default nothing stops a slice branch from merging without `/validate-slice` — the vault is external, so CI
can't see it. The **ship receipt** closes that: install the bundled workflow once,

```bash
cp <plugin>/skills/commit-slice/assets/aisdlc-merge-gate.yml .github/workflows/
# then mark the "aisdlc merge gate" check as REQUIRED in branch protection
```

and from then on `/commit-slice` detects it and emits `.aisdlc/receipts/<slice-NNN>.json` into every slice
commit — a slim evidence record (validation result, criteria counts, shippability/deferral state, the slice's
gate-log rows). The workflow refuses to merge a `slice/*` PR whose receipt is missing, non-passing, or carries
an unapproved regression. Non-slice branches pass trivially. Without the workflow file, nothing is written to
your repo — the gate is strictly opt-in.

**Captured evidence is secret-swept.** `/validate-slice` and `/risk-spike` run commands against real
environments; before any captured output is stored as `evidence`, pipe it through
`scripts/lib/secret_scrub.py`, which redacts credentials (`[REDACTED:<type>]`) using the same VAL-1 patterns —
so tokens/keys don't persist plaintext in the vault.

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
     → design-slice              (design tournament: all 3 blind designers generate on every slice → reality-grounded synthesis)
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
  `release` (grounded README / CHANGELOG / API-ref / user-guide).
- **Heavy-mode only:** `heavy-architect`, `sync`.

Backlog of work lives in `<vault>/candidates.json` (live) and `<vault>/archive/candidates.json` (shipped); the
risk ledger is `<vault>/risk-register.json`. `/slice-candidates --product` materializes the **product's own
declared scope** (decomposed once from `concept.json`) into that backlog — without it, a backlog can fill
entirely with pipeline *exhaust* (risks, findings, reflections) and the product itself is never pickable.

Invoke any skill with its slash command — the plugin namespaces them, so `/diagnose` is `/ai-sdlc:diagnose`, etc.

### What a slice actually costs (be honest before adopting)

Per-slice review cost keys on the slice's **risk tier**, not the project mode (mode only sets the default tier
+ Heavy's sign-off floor). Rough expectations, including your own think time:

| Tier | Agent spawns | Your gates | Wall-clock overhead |
|------|-------------|-----------|---------------------|
| **low / mechanical** | ~3–4 (3-designer tournament always runs; Critic skipped unless mandatory) | plan approval | ~15–25 min |
| **medium** | ~6–8 (tournament ×3, recon, Critic, code-Critic, narrator) | candidate pick · plan approval · TRI-1 triage | ~1–2 h |
| **high / novel** | ~8–10 (+ meta-Critic, design spike) | + spike/validation failure gates | ~2–4 h |

One-time: `/triage` ~15 min · `/discover` ~30 min · `/heavy-architect` (Heavy only) ~2 h. Maintenance
(`/drift-check`, `/critic-calibrate`, `/sync`) is signal-driven, not per-slice. If these numbers feel heavy for
your project, run Minimal mode and let low-tier defaults do their job — the cost story holds only when slices
stay genuinely thin.

### What this is NOT for

- **Teams > ~3 without the CI merge gate installed** — the vault has no locking. Slice resolution does enforce
  an ownership **collision guard** (a step run against slice-X refuses to silently read/write into slice-Y's
  files when a different git identity holds that candidate's claim) — but this is explicitly **not an
  authorization boundary** (git identity is self-assignable with two `git config` commands); it only catches
  the *honest* cross-slice mistake between cooperating humans and their forked agents. Coordination beyond that
  is social, surfaced via claim/heartbeat in `/pulse`.
- **Audit-grade compliance processes** — Heavy mode adds rigor (forced Critic, human sign-off, threat model),
  but there is no compliance sign-off workflow or audit-trail enforcement. Regulated projects need their own
  compliance review on top; `/triage` will tell you so.
- **Projects whose validation surface is unreachable from a dev machine** (production-only hardware, app-store
  release trains): the reality spine degrades to weaker proxies — the gate-log records that honestly
  (`reality_proxy`), but the strongest greens are simply unavailable to you.
- **Drive-by contributions** — the loop pays off across slices (calibration, lessons, shippability compound);
  a one-afternoon fix doesn't amortize the vault.

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

Shubhendu Shubham · plugin `ai-sdlc` v2.39.0

---

## Further reading

- [User guide](docs/user-guide.md) — step-by-step walkthrough of running the pipeline on a real project
- [API reference](docs/api-reference.md) — every slash command, CLI script, env var, and config file, grounded in the real files that define them
