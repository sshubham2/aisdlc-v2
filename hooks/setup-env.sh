#!/usr/bin/env bash
# ai-sdlc plugin — SessionStart hook. Resolves a Python 3 interpreter and persists it as
# $PY for every skill bash block + `!` dynamic-injection in the session.
#
# How it reaches skills: Claude Code provides $CLAUDE_ENV_FILE to SessionStart hooks; lines
# appended here are sourced before EVERY subsequent Bash tool call (incl. skill bash + `!`
# injections). SKILL.md invokes bundled scripts as `$PY "${CLAUDE_SKILL_DIR}/.../X.py"`, so
# defining $PY once here makes all 131 such call-sites resolve — with no per-skill setup.
#
# Resolution order: $AI_SDLC_PY override, then python3 / python / py (the Windows launcher).
# Prefers an interpreter that can `import yaml` (only /diagnose needs PyYAML); falls back to
# the first that merely exists, emitting a warning. Never blocks the session.
#
# After resolving $PY it best-effort-installs requirements.txt (PyYAML + code-review-graph) into
# that interpreter — but ONLY when a dep is actually missing, and never blocking the session.
# Opt out with AI_SDLC_NO_AUTO_INSTALL=1 (e.g. if you manage deps in a venv yourself).
#
# Dev tip: point $PY at a venv with the deps by exporting AI_SDLC_PY before launching Claude
# Code, e.g.  export AI_SDLC_PY="C:/Users/you/.claude/.venv/Scripts/python.exe"

set -u
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0   # not a SessionStart context — nothing to persist

# resolve <need_yaml 0|1> -> echo the interpreter, or non-zero if none qualifies.
resolve() {
  local need_yaml="$1" c
  for c in "${AI_SDLC_PY:-}" python3 python py; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if [ "$need_yaml" = 0 ] || "$c" -c 'import yaml' >/dev/null 2>&1; then
      printf '%s\n' "$c"; return 0
    fi
  done
  return 1
}

chosen="$(resolve 1)" || chosen="$(resolve 0)" || chosen=""

if [ -z "$chosen" ]; then
  echo "ai-sdlc: no Python 3 interpreter found for \$PY — set AI_SDLC_PY, or install python3/python." >&2
  exit 0   # fail-soft: don't block the session; skills fail-visibly when they invoke an unset \$PY
fi

printf 'export PY=%q\n' "$chosen" >> "$CLAUDE_ENV_FILE"

# Resolve the plugin root once (reused by the dep-install block + the vault-root block below).
plugin_root="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)}"

# --- best-effort dependency auto-install (PyYAML + code-review-graph) ------------------------
# Installs requirements.txt into the SAME interpreter $PY resolves to, but ONLY when a dep is
# actually missing (a find_spec probe — no heavy import, so ~zero cost on every later session).
# Best-effort + non-fatal: a failure (offline / externally-managed env / no pip) only warns; the
# session still proceeds and skills fail-visibly if a dep is truly absent. Opt out with
# AI_SDLC_NO_AUTO_INSTALL=1. First run may take a minute (code-review-graph pulls tree-sitter).
req="$plugin_root/requirements.txt"
dep_probe='import importlib.util as u,sys; sys.exit(0 if u.find_spec("yaml") and u.find_spec("code_review_graph") else 1)'
if [ "${AI_SDLC_NO_AUTO_INSTALL:-0}" != 1 ] && [ -f "$req" ] && ! "$chosen" -c "$dep_probe" >/dev/null 2>&1; then
  echo "ai-sdlc: installing Python deps from requirements.txt into '$chosen' (one-time; first run may take a minute)..." >&2
  if "$chosen" -m pip install -r "$req" >/dev/null 2>&1 \
       || "$chosen" -m pip install --user -r "$req" >/dev/null 2>&1; then
    echo "ai-sdlc: dependency install OK." >&2
  else
    echo "ai-sdlc: note — auto pip install failed (offline / externally-managed env / no pip). Run '\$PY -m pip install -r requirements.txt' yourself, or set AI_SDLC_NO_AUTO_INSTALL=1 to silence." >&2
  fi
fi
# --------------------------------------------------------------------------------------------

# Resolve + persist $AI_SDLC_VAULT_ROOT so skill bash blocks can reference the vault directly
# (cat/ls/test/--file and positional script paths — not only the `--vault` empty-fallback form).
# _vault_paths.py applies the SAME 3-tier resolution used everywhere: the AI_SDLC_VAULT_ROOT env
# override, else the <git-common-dir>/aisdlc/vault-root pin, else the computed default
# <base>/<slug>-<hash> (base from ~/.claude/ai-sdlc-vault-base, else ~/.aisdlc). So setting the
# base file IS honored here, and a user-set AI_SDLC_VAULT_ROOT is echoed back unchanged
# (idempotent). Fail-soft: a resolution failure warns and skills fall back per call.
if vault_root="$("$chosen" "$plugin_root/scripts/lib/_vault_paths.py" --path 2>/dev/null)" \
     && [ -n "$vault_root" ]; then
  # Backslash -> forward slash: a Windows "C:\..." path emitted by pathlib becomes "C:/...",
  # the one form BOTH git-bash (cat/ls/test) AND Python (--vault/positional) accept. No-op on POSIX.
  vault_root="${vault_root//\\//}"
  printf 'export AI_SDLC_VAULT_ROOT=%q\n' "$vault_root" >> "$CLAUDE_ENV_FILE"
else
  echo "ai-sdlc: note — could not resolve \$AI_SDLC_VAULT_ROOT; skills fall back to the computed default per call." >&2
fi

if ! "$chosen" -c 'import yaml' >/dev/null 2>&1; then
  echo "ai-sdlc: note — \$PY='$chosen' lacks PyYAML; /diagnose needs it (pip install pyyaml)." >&2
fi
