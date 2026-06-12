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
  export [--vault P] [--out F]
                          Tar-gzip the vault to ONE portable archive (roadmap §2.2: backup / team
                          handoff — the design record currently has less durability than the code
                          it explains). Default out: ./<vault-name>-vault.tgz
  import ARCHIVE [--vault P] [--force]
                          Restore an exported archive into this repo's vault dir. Refuses a
                          non-empty target unless --force (then the target is REPLACED). Prints a
                          write-pin reminder — an imported vault should be pinned to its new repo.

Exit: 0 ok · 2 usage (not a git tree / unknown vault / unconfirmed delete / bad archive).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
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


def _this_repo_vault(arg: str | None) -> Path | None:
    """The vault dir: --vault when given, else computed for this repo's common-dir."""
    if arg:
        return Path(arg)
    common = _git_common_dir()
    if not common:
        sys.stderr.write("vault_admin: not in a git work tree — pass --vault explicitly.\n")
        return None
    return external_store_path(common)


def cmd_export(args: argparse.Namespace) -> int:
    vault = _this_repo_vault(args.vault)
    if vault is None:
        return 2
    if not vault.is_dir():
        sys.stderr.write(f"vault_admin: no vault at {vault} — nothing to export.\n")
        return 2
    out = Path(args.out) if args.out else Path.cwd() / f"{vault.name}-vault.tgz"
    try:
        with tarfile.open(out, "w:gz") as tf:
            tf.add(vault, arcname=vault.name)
    except OSError as e:
        sys.stderr.write(f"vault_admin: export failed: {e}\n")
        return 2
    n = sum(1 for p in vault.rglob("*") if p.is_file())
    print(f"exported {vault.name} ({n} files) -> {out}")
    print("restore on another machine/repo with: vault_admin.py import "
          f"{out.name} [--vault <target>], then `vault_admin.py write-pin`.")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    if not archive.is_file():
        sys.stderr.write(f"vault_admin: no archive at {archive}\n")
        return 2
    vault = _this_repo_vault(args.vault)
    if vault is None:
        return 2
    if vault.is_dir() and any(vault.iterdir()) and not args.force:
        sys.stderr.write(f"vault_admin: target vault {vault} is NOT empty — re-run with --force to "
                         "REPLACE it (the existing contents are deleted), or pass a different --vault.\n")
        return 2
    try:
        with tempfile.TemporaryDirectory() as td:
            with tarfile.open(archive, "r:*") as tf:
                tf.extractall(td, filter="data")  # refuses absolute paths / .. traversal
            roots = [p for p in Path(td).iterdir()]
            # an export archive holds ONE top-level dir (the vault); tolerate a flat archive too
            src = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(td)
            if vault.is_dir():
                shutil.rmtree(vault)
            shutil.copytree(src, vault)
    except (OSError, tarfile.TarError) as e:
        sys.stderr.write(f"vault_admin: import failed: {e}\n")
        return 2
    print(f"imported {archive.name} -> {vault}")
    print("now pin it to this repo:  vault_admin.py write-pin"
          + (f" --vault {vault}" if args.vault else ""))
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
    ex = sub.add_parser("export", help="tar-gzip the vault to one portable archive (§2.2)")
    ex.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    ex.add_argument("--out", default=None, help="output archive (default: ./<vault-name>-vault.tgz)")
    im = sub.add_parser("import", help="restore an exported vault archive (§2.2)")
    im.add_argument("archive", help="path to the .tgz produced by export")
    im.add_argument("--vault", default=None, help="target vault path (default: computed for this repo)")
    im.add_argument("--force", action="store_true", help="replace a non-empty target vault")
    args = p.parse_args(argv)
    return {"write-pin": cmd_write_pin, "list": cmd_list, "uninstall": cmd_uninstall,
            "export": cmd_export, "import": cmd_import}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
