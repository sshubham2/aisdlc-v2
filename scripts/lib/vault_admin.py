"""vault_admin.py — vault lifecycle admin (4.7): pin, sibling-detect, list, uninstall.

The external vault keys on the git-common-dir path, so a repo move/rename silently ORPHANS it.
A tier-2 pin (`<common-dir>/aisdlc/vault-root` -> vault) survives a rename (the common-dir moves
WITH the repo, and `_vault_paths` reads the pin at tier 2) — but the writer `write_vault_root_config`
shipped with ZERO callers. This tool is the caller.

Subcommands:
  write-pin [--vault P]   Write the tier-2 pin for THIS repo's vault (so a rename doesn't orphan it)
                          + drop a `.source-repo` back-ref IN the vault (for orphan detection), and
                          WARN if a same-slug / different-hash SIBLING vault already exists (4.7.2).
  list                    List every vault under the base, its source repo, and orphan status (4.7.5).
  uninstall NAME [--yes]  Delete a vault dir under the base (GC orphans; --yes to confirm) (4.7.5).

Exit: 0 ok · 2 usage (not a git tree / unknown vault / unconfirmed delete).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import (
    _canonical, _git_common_dir, external_store_path, resolve_base,
)
from scripts.lib._vault_write import write_vault_root_config

_SOURCE_MARKER = ".source-repo"  # written INSIDE the vault: the source repo root (orphan detection)


def _slug_of(name: str) -> str:
    """A vault dir is `<slug>-<8hex>`; strip the trailing `-<hash>` to get the slug."""
    return name.rsplit("-", 1)[0] if "-" in name else name


def _siblings(vault: Path) -> list[Path]:
    """Same-slug, different-hash vault dirs under the same base (a likely rename orphan)."""
    base, me = vault.parent, vault.name
    my_slug = _slug_of(me)
    if not base.is_dir():
        return []
    return [p for p in sorted(base.iterdir())
            if p.is_dir() and p.name != me and _slug_of(p.name) == my_slug]


def cmd_write_pin(args: argparse.Namespace) -> int:
    common = _git_common_dir()
    if not common:
        sys.stderr.write("vault_admin: not in a git work tree — no common-dir pin to write "
                         "(set AI_SDLC_VAULT_ROOT or run `git init`).\n")
        return 2
    vault = Path(args.vault) if args.vault else external_store_path(common)
    cfg = write_vault_root_config(common, vault)
    repo_root = str(Path(_canonical(common)).parent)  # <repo>/.git -> <repo>
    try:
        vault.mkdir(parents=True, exist_ok=True)
        (vault / _SOURCE_MARKER).write_text(repo_root + "\n", encoding="utf-8")
    except OSError:
        pass
    print(f"vault pin written: {cfg} -> {vault}")
    sibs = _siblings(vault)
    if sibs:
        print(f"WARNING: {len(sibs)} same-name vault(s) with a DIFFERENT hash exist (a likely orphan "
              f"from a repo move/rename) — migrate what you need, then `vault_admin uninstall`:")
        for s in sibs:
            print(f"  - {s.name}")
    return 0


def vault_rows(base: Path) -> list[tuple[str, str, str]]:
    """(status, name, source) per vault dir under ``base``. status ∈ {live, ORPHAN, ?} where ? is a
    pre-4.7 vault with no `.source-repo` back-ref and ORPHAN is a back-ref whose repo path is gone."""
    rows: list[tuple[str, str, str]] = []
    if not base.is_dir():
        return rows
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        marker = p / _SOURCE_MARKER
        source = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
        if source is None:
            status = "?"
        elif Path(source).exists():
            status = "live"
        else:
            status = "ORPHAN"
        rows.append((status, p.name, source or "unknown"))
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    base = resolve_base()
    rows = vault_rows(base)
    if not rows:
        print(f"no vaults under {base} (nothing installed).")
        return 0
    print(f"vaults under {base}:")
    for status, name, source in rows:
        print(f"  [{status:6}] {name}   source={source}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    base = resolve_base()
    target = base / args.name
    if base not in target.resolve().parents or not target.is_dir():
        sys.stderr.write(f"vault_admin: no vault named {args.name!r} under {base}\n")
        return 2
    if not args.yes:
        sys.stderr.write(f"vault_admin: would delete {target} — re-run with --yes to confirm.\n")
        return 2
    shutil.rmtree(target)
    print(f"deleted {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="vault_admin", description="Vault lifecycle admin (4.7).")
    sub = p.add_subparsers(dest="cmd", required=True)
    wp = sub.add_parser("write-pin", help="write the tier-2 common-dir pin + source back-ref")
    wp.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    sub.add_parser("list", help="list vaults under the base + orphan status")
    un = sub.add_parser("uninstall", help="delete a vault dir under the base")
    un.add_argument("name", help="vault dir name (<slug>-<hash>)")
    un.add_argument("--yes", action="store_true", help="confirm deletion")
    args = p.parse_args(argv)
    return {"write-pin": cmd_write_pin, "list": cmd_list, "uninstall": cmd_uninstall}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
