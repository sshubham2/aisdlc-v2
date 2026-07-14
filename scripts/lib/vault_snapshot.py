"""vault_snapshot.py — multi-file vault digest for /pulse injection (v2, NEW).

Shared library + CLI. Replaces /pulse's 5-6 separate file reads with ONE bounded
text digest, injected into the pulse prompt (`` !`$PY .../vault_snapshot.py …` ``).
Read-only — never writes the vault.

Three mutually-exclusive modes, each taking a LIST of names (`nargs="+"`):
  - `--files A.json B.json …`: read each VAULT-RELATIVE file (full content, array-capped).
  - `--active-slice A.json B.json …`: for each file, read it from the ACTIVE SLICE
    folder when it exists there, else fall back to vault-relative. The active slice
    is resolved via `scripts.lib.active_slice.resolve_active_slice(vault, ".")`; when
    that returns None, every file falls back to vault-relative and the digest notes
    `(no active slice)`.
  - `--presence A.json actors …`: report EXISTENCE + a lightweight count per name, with
    NO content injected — `<name>: present (N items|files)` / `absent`. Handles both files
    (count = longest root array) and directories (count = file count). Used by /pulse to
    detect the Heavy-mode architecture phase (threat-model/requirements/actors/…) without
    bloating the prompt with full upfront-architecture documents.

Output is a TEXT digest: per requested file a section
    === <name> ===
    <pretty JSON content>
Missing file → `(absent)`; malformed JSON → `(unreadable JSON)`. To bound the prompt,
any top-level array — or an array-valued field of a root object (e.g. candidates.json
`{candidates:[…]}`) — is truncated to its first 20 elements with a trailing
`"... +N more"` string element (on a COPY — disk is never mutated).

Exit codes: 0 always (snapshot is best-effort; /pulse calls it under `2>/dev/null`),
2 on a usage error (neither/both modes).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md;
# a skill runs in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m`
# or `${CLAUDE_PLUGIN_ROOT}`). Add the plugin root so `from scripts.lib import …` resolves.
# No-op under `-m scripts.lib.vault_snapshot` from the plugin root. ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/vault_snapshot.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.active_slice import resolve_active_slice

_MAX_ARRAY = 20


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _cap(arr: list) -> list:
    extra = len(arr) - _MAX_ARRAY
    return arr[:_MAX_ARRAY] + [f"... +{extra} more"]


def _truncate(doc):
    """Bound large arrays to the first _MAX_ARRAY elements (on a copy), appending a
    `"... +N more"` marker. Handles a root-level list AND each array-valued field of a
    root object (the common <vault> shape, e.g. candidates.json `{candidates:[...]}`,
    lessons-learned.json `{lessons:[...]}`). Deeper nesting is left intact; disk is
    never mutated (a new container is built; untouched values are shared references)."""
    if isinstance(doc, list):
        return _cap(doc) if len(doc) > _MAX_ARRAY else doc
    if isinstance(doc, dict):
        return {k: (_cap(val) if isinstance(val, list) and len(val) > _MAX_ARRAY else val)
                for k, val in doc.items()}
    return doc


def _section(name: str, path: Path) -> str:
    """Render one `=== name ===` section for `path` (best-effort)."""
    header = f"=== {name} ==="
    if not path.is_file():
        return f"{header}\n(absent)"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return f"{header}\n(absent)"
    except json.JSONDecodeError:
        return f"{header}\n(unreadable JSON)"
    body = json.dumps(_truncate(doc), ensure_ascii=False, indent=2)
    return f"{header}\n{body}"


def _primary_count(doc) -> int | None:
    """Length of a root list, else the longest array-valued field of a root dict (the
    common <vault> shape, e.g. threat-model `{threats:[…]}`, requirements `{items:[…]}`).
    None when there is no array to count."""
    if isinstance(doc, list):
        return len(doc)
    if isinstance(doc, dict):
        lens = [len(v) for v in doc.values() if isinstance(v, list)]
        return max(lens) if lens else None
    return None


def _presence(name: str, path: Path) -> str:
    """One `<name>: present (N …)` / `absent` line — existence + a lightweight count, with
    NO content injected (bounds the prompt; safe for large Heavy-mode artifacts). Directory →
    count of contained files; JSON file → longest root array; unparseable/other → bare present."""
    if path.is_dir():
        try:
            n = sum(1 for c in path.iterdir() if c.is_file())
        except OSError:
            return f"{name}: present"
        return f"{name}: present ({n} file{'' if n == 1 else 's'})"
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return f"{name}: present"
        n = _primary_count(doc)
        return f"{name}: present" + (f" ({n} item{'' if n == 1 else 's'})" if n is not None else "")
    return f"{name}: absent"


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="vault_snapshot",
        description="Multi-file vault digest for /pulse injection. Read-only, best-effort.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--files", nargs="+", default=None,
                   help="vault-relative JSON files to digest")
    g.add_argument("--active-slice", dest="active_slice", nargs="+", default=None,
                   help="JSON files to read from the active slice folder (else vault-relative)")
    g.add_argument("--presence", nargs="+", default=None,
                   help="report existence + a lightweight count per file/dir; no content injected")
    args = p.parse_args(argv)

    vault = _root(args.vault)
    sections: list[str] = []

    if args.files is not None:
        for name in args.files:
            sections.append(_section(name, vault / name))
    elif args.presence is not None:
        for name in args.presence:
            sections.append(_presence(name, vault / name))
    else:
        # slice-069: the AUDITED read-only opt-out (owner_check=False). /pulse is ORIENTATION -- it
        # must still SHOW a teammate's in-flight slice, so it does not run the ownership check at
        # all. The guard protects the WRITE designation, not the READ. This opt-out is declared here
        # and enforced by active_slice_guard_audit's allowlist; it can never be taken silently.
        info = resolve_active_slice(vault, ".", owner_check=False)
        # slice-019 (AC4): the AMBIGUOUS sentinel is truthy with path=None -> treat as no-active-slice
        # (else Path(info["path"]) TypeErrors on the None path — the slice-019 crash class). The
        # ownership-refused sentinel carries path=None too, so this same branch absorbs it safely
        # even if the opt-out above is ever removed.
        if isinstance(info, dict) and (info.get("source") in ("ambiguous", "ownership-refused")
                                       or info.get("path") is None):
            info = None
        slice_dir = Path(info["path"]) if info else None
        if slice_dir is None:
            sections.append("(no active slice)")
        for name in args.active_slice:
            target = None
            if slice_dir is not None:
                cand = slice_dir / name
                if cand.is_file():
                    target = cand
            if target is None:
                target = vault / name
            sections.append(_section(name, target))

    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
