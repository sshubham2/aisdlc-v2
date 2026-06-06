"""latest_archived_slice.py — the most recently archived slice (v2, NEW).

Shared library + CLI. The single canonical answer to "which archived slice is the
newest?", used by /commit-slice to resolve its target when the just-shipped slice
has already been moved to `slices/archive/` by /reflect. Read-only — never writes.

Resolution: scan `<vault>/slices/archive/` for folders matching `slice-(\\d+)-(.+)`.
Pick the latest by each folder's `milestone.json` `at` field (ISO-8601 string
compare — descending), tie-broken by highest NNN. When no folder carries an `at`,
fall back to highest NNN.

CLI: `--vault ROOT [--json]`. With `--json` emits
    {"slice","folder","path","stage","at"}
(or `{"slice": null}` when the archive is empty/absent); without `--json` a one-line
text summary (or "no archived slices"). Exit 0 always (an empty archive is a normal
early state, not an error). milestone.json is read defensively
(missing/malformed → stage/at treated as null).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md;
# a skill runs in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m`
# or `${CLAUDE_PLUGIN_ROOT}`). Add the plugin root so `from scripts.lib import …` resolves.
# No-op under `-m scripts.lib.latest_archived_slice` from the plugin root. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/latest_archived_slice.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

_SLICE_FOLDER_RE = re.compile(r"^slice-(\d+)-(.+)$")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _read_milestone(folder: Path) -> dict:
    p = folder / "milestone.json"
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def resolve_latest_archived(vault: str | Path) -> dict | None:
    """The newest archived slice info dict, or None when the archive is empty/absent."""
    archive = Path(vault) / "slices" / "archive"
    if not archive.is_dir():
        return None
    folders = [p for p in archive.iterdir()
               if p.is_dir() and _SLICE_FOLDER_RE.match(p.name)]
    if not folders:
        return None
    scored = []
    for p in folders:
        m = _read_milestone(p)
        num = int(_SLICE_FOLDER_RE.match(p.name).group(1))
        scored.append((p, str(m.get("at") or ""), num, m))
    scored.sort(key=lambda s: (s[1], s[2]), reverse=True)  # at desc, then NNN desc
    folder, at, num, m = scored[0]
    return {
        "slice": f"slice-{_SLICE_FOLDER_RE.match(folder.name).group(1)}",
        "folder": folder.name,
        "path": str(folder),
        "stage": m.get("stage"),
        "at": m.get("at"),
    }


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="latest_archived_slice",
        description="Resolve the most recently archived slice. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--json", action="store_true",
                   help="emit the info dict as JSON (default: one-line text)")
    args = p.parse_args(argv)

    info = resolve_latest_archived(_root(args.vault))
    if args.json:
        print(json.dumps(info if info else {"slice": None}, ensure_ascii=False))
    elif info:
        print(f"latest archived slice: {info['folder']} "
              f"(stage={info['stage']}, at={info['at']})")
    else:
        print("no archived slices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
