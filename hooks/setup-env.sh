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

if ! "$chosen" -c 'import yaml' >/dev/null 2>&1; then
  echo "ai-sdlc: note — \$PY='$chosen' lacks PyYAML; /diagnose needs it (pip install pyyaml)." >&2
fi
