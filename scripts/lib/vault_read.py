"""vault_read.py — read a vault JSON file (or glob), optionally projecting fields (v2, NEW).

Shared library + CLI. The single canonical reader skills use to PRE-LOAD vault
JSON into a SKILL.md prompt via dynamic injection (`` !`$PY .../vault_read.py …` ``).
Replaces ad-hoc `cat | jq` snippets so every skill reads + projects the vault the
SAME way. Read-only — never writes the vault.

CLI contract:
  - A single file: `--file PATH` (or POSITIONAL `PATH`). Absolute → used as-is;
    relative → under the vault root. Emits the FULL JSON, or with `--fields a,b,c`
    only those top-level keys (if the doc's top-level is a LIST, each element is
    projected). A MISSING single file → stderr + exit 1 (so a SKILL's
    `2>/dev/null || echo '{...}'` fallback fires; nothing on stdout). Malformed
    JSON → stderr + exit 1.
  - A glob: `--glob PATTERN` (under the vault root) → emits a JSON LIST, one element
    per matching file (projected to `--fields` when given). NO matches → `[]` + exit 0
    (callers rely on the empty list, e.g. heavy-architect with no ADRs).
  - `--file`/positional and `--glob` are mutually exclusive; exactly one required.
  - Output is `json.dumps(..., ensure_ascii=False, indent=2)`.

Exit codes: 0 success (incl. empty glob), 1 runtime error (missing/malformed single
file — fail-visible to stderr), 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md;
# a skill runs in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m`
# or `${CLAUDE_PLUGIN_ROOT}`). Add the plugin root so `from scripts.lib import …` resolves.
# No-op under `-m scripts.lib.vault_read` from the plugin root. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/vault_read.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _resolve(vault: Path, ref: str) -> Path:
    """A file ref → absolute Path. Absolute as-is; relative under the vault root."""
    p = Path(ref)
    return p if p.is_absolute() else vault / p


def _project(doc, fields: list[str] | None):
    """Project `doc` to `fields` (top-level keys). A dict → keep only present keys;
    a list → project each element; `fields` None → identity. Non-dict elements pass
    through unchanged."""
    if not fields:
        return doc
    if isinstance(doc, list):
        return [_project(el, fields) for el in doc]
    if isinstance(doc, dict):
        return {k: doc[k] for k in fields if k in doc}
    return doc


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="vault_read",
        description="Read a vault JSON file (or glob), optionally projecting top-level fields. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("file", nargs="?", default=None,
                   help="vault-relative or absolute path to a single JSON file (same as --file)")
    p.add_argument("--file", dest="file_opt", default=None,
                   help="vault-relative or absolute path to a single JSON file")
    p.add_argument("--glob", dest="glob", default=None,
                   help="glob PATTERN under the vault root; emits a JSON list")
    p.add_argument("--fields", default=None,
                   help="comma-separated top-level keys to keep (project the object)")
    args = p.parse_args(argv)

    vault = _root(args.vault)
    fields = [f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None

    single = args.file or args.file_opt
    if args.glob and single:
        p.error("--glob and a file argument are mutually exclusive")
    if not args.glob and not single:
        p.error("one of --glob, --file, or a positional file is required")

    if args.glob:
        matches = sorted(vault.glob(args.glob))
        out = []
        for m in matches:
            if not m.is_file():
                continue
            try:
                doc = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                _stderr(f"vault_read: skipping unreadable {m}: {exc}")
                continue
            out.append(_project(doc, fields))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    # single file
    target = _resolve(vault, single)
    if not target.is_file():
        _stderr(f"vault_read: file not found: {target}")
        return 1
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _stderr(f"vault_read: cannot read JSON {target}: {exc}")
        return 1
    print(json.dumps(_project(doc, fields), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
