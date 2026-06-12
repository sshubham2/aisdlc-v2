# Contributing to ai-sdlc

A few durable rules keep the plugin consistent and releasable. (These previously lived only in a
machine-local file; they are shipped here so every contributor has them — 4.9.)

## 1. Bump the version on every pushed commit

Every commit that gets pushed MUST bump `version` in `.claude-plugin/plugin.json` (semver):

- **patch** — a fix, docs, or refactor (no user-facing behavior change)
- **minor** — a new skill or a backward-compatible feature
- **major** — a breaking change

Stage the bump in the same commit. An unchanged version makes the installed plugin
indistinguishable from the prior build.

## 2. Don't hand-edit generated files

- `skills/<name>/examples/*.json` are **generated** from `schemas/artifact-examples.json` by the
  aggregator. Edit the source, not the output.
- The **design record** (`skill.json` per skill + `skill-graph.json`) is also generated, and is
  **git-ignored** — kept local as authoring history, not shipped (see the README "Development"
  note). It has no runtime consumers; the runnable contract is the `SKILL.md` set.
- To change a skill's design metadata, edit its `.build/manifests/batch*.json` entry, then
  regenerate. Never hand-edit the computed inverse-link fields (`created_by` / `edited_by` /
  `read_by` / `validated_by`) — the aggregator recomputes them.

Regenerate the bundled examples (and the local design record):

```bash
python3 .build/aggregate.py
```

## 3. Run the tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

CI (`.github/workflows/ci.yml`) gates every push:

- **pytest** matrix — Ubuntu + Windows × Python 3.11/3.13 (the vault writer and git runners carry
  Windows-specific paths, so the matrix is load-bearing);
- **build-consistency** — `artifact_lint --self-check`, the cross-block-var audit over every
  `SKILL.md`, and an aggregate-clean check on the bundled `examples/`;
- **plugin self-audits** — `.build/plugin_self_audits.py` (UTF8-STDOUT, pipeline-chain, etc.).

## 4. Authoring skills

`SKILL.md` and `scripts/` are hand-authored; `examples/` are generated. Two rules the CI enforces:

- **Bash blocks are self-contained.** Shell variables do NOT persist across `SKILL.md` `bash`
  blocks — re-derive what you need in each block (the cross-block audit fails a cross-block use).
- **Invoke bundled scripts by absolute path** off `${CLAUDE_SKILL_DIR}` (e.g.
  `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<x>.py"`), never `python -m` — a skill's shell runs
  in the user's CWD, and markdown skills can't expand `${CLAUDE_PLUGIN_ROOT}`.

## License

By contributing you agree your contributions are licensed under the [MIT License](LICENSE).
