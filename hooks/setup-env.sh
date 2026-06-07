#!/usr/bin/env bash
# ai-sdlc SessionStart bootstrap. Finds a Python 3 and hands ALL logic to setup_env.py.
#
# Deliberately tiny and logic-free: it has NO path normalization, NO encoding, NO env-file
# quoting — those are exactly where bash-on-Windows breaks (the ${var//\\//} no-op, the
# `printf %q` backslash-mangling that turned $PY into a non-file, UTF-8 BOMs, the PATH
# `C:`-vs-`:` collision). All of that now lives in hooks/setup_env.py, where it's just
# str.replace / shlex.quote / utf-8. This shim's only job is the bootstrap that Python
# itself cannot do: locate an interpreter to run the real hook.
#
# It does NOT change directory — setup_env.py must see the project CWD so the vault-root
# resolver keys off the right repo. It resolves the script via $CLAUDE_PLUGIN_ROOT (the
# python-openable path the hook system provides), falling back to a BASH_SOURCE-relative dir.
set -u
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0   # not a SessionStart context — nothing to do

root="${CLAUDE_PLUGIN_ROOT:-}"
[ -n "$root" ] || root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"

for py in "${AI_SDLC_PY:-}" python3 python py; do
  [ -n "$py" ] || continue
  command -v "$py" >/dev/null 2>&1 || continue
  exec "$py" "$root/hooks/setup_env.py"
done

# No interpreter at all — nudge (stdout = SessionStart context Claude relays) and fail soft.
echo "ai-sdlc: SETUP REQUIRED — no Python 3 found. Install Python 3 (or set AI_SDLC_PY, forward slashes), then run /ai-sdlc:setup."
exit 0
