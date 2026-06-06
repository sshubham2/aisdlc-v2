"""grep_vault.py — case-insensitive substring grep over vault JSON files (v2, NEW).

Shared library + CLI. Surfaces prior art for a concept across the vault so a skill
can inject "what we already wrote about X" into its prompt
(`` !`$PY .../grep_vault.py --vault … --pattern "…" --dir slices/archive` ``).
Read-only — never writes the vault.

CLI contract:
  - `--pattern STR` (required): case-insensitive SUBSTRING match against each JSON
    file's RAW text (not parsed — matches keys, values, anything).
  - `--dir REL` (default ""=whole vault): restrict the recursive search to
    `<vault>/<dir>`; only `*.json` files are scanned.
  - Output is a TEXT digest for prompt injection: per matching file a header
    `<relative/path.json>:` followed by up to ~5 trimmed matching lines. Total files
    shown is capped at ~30 with a `... +N more files` note. No matches →
    `(no matches for "<pattern>" under <dir>)`.

Exit codes: 0 always for a search result (a no-match is normal, not an error),
2 on missing --pattern (usage error).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md;
# a skill runs in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m`
# or `${CLAUDE_PLUGIN_ROOT}`). Add the plugin root so `from scripts.lib import …` resolves.
# No-op under `-m scripts.lib.grep_vault` from the plugin root. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/grep_vault.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

_MAX_FILES = 30
_MAX_LINES_PER_FILE = 5


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="grep_vault",
        description="Case-insensitive substring grep over vault JSON files. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--pattern", required=True,
                   help="case-insensitive substring to match against each file's raw text")
    p.add_argument("--dir", dest="subdir", default="",
                   help="vault-relative directory to restrict the search to (default: whole vault)")
    args = p.parse_args(argv)

    vault = _root(args.vault)
    needle = args.pattern.lower()
    base = vault / args.subdir if args.subdir else vault
    dir_label = args.subdir or "."

    matched: list[tuple[str, list[str]]] = []
    if base.is_dir():
        for path in sorted(base.rglob("*.json")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if needle not in text.lower():
                continue
            # BB-16: cap each line's LENGTH (not just the count) — a minified JSON or a
            # long single-line prose value would otherwise blow past the prompt bound.
            lines = [(s[:200] + "..." if len(s) > 200 else s)
                     for s in (ln.strip() for ln in text.splitlines()) if needle in s.lower()]
            try:
                rel = path.relative_to(vault).as_posix()
            except ValueError:
                rel = str(path)
            matched.append((rel, lines[:_MAX_LINES_PER_FILE]))

    if not matched:
        print(f'(no matches for "{args.pattern}" under {dir_label})')
        return 0

    shown = matched[:_MAX_FILES]
    out: list[str] = []
    for rel, lines in shown:
        out.append(f"{rel}:")
        out.extend(f"  {ln}" for ln in lines)
    extra = len(matched) - len(shown)
    if extra > 0:
        out.append(f"... +{extra} more files")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
