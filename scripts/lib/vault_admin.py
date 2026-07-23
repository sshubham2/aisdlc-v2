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
  migrate [--vault P] [--reverse]
                          Convert gate-log <-> the append-only shard store (slice-088 / ADR-106):
                          forward explodes the flat gate-log.json into per-entry shards + a derived
                          local cache; --reverse rebuilds the flat file and tears the shards down.
                          Fail-closed, reversible, idempotent (a re-run is a no-op); holds the single
                          gate-log.json.lock across the whole read->build->verify->publish, so a
                          parallel-slice append can never be lost or duplicated. Actions are logged.
  sync push|pull [--vault P] [--remote R] [--force] [--force-drop-local]
                          Git-native vault sync (slice-092 / ADR-114): "sync the log, never the
                          view". push = ensure sync hygiene -> git add -A -> commit -> push; a fresh
                          clone reconstructs the vault via the derive-on-missing readers (the derived
                          gate-log cache + *.lock/*.tmp/.source-repo + .git/ are excluded). pull =
                          fetch + fast-forward-only by default (REFUSES a dirty tree or divergent
                          history), --force mirror-resets (still guarding unpushed local commits;
                          --force-drop-local overrides), then invalidates the derived cache.
                          NOTE: `sync push` transmits the WHOLE vault UNREDACTED — use a PRIVATE
                          remote (a secret committed once persists in the pushed history).

Exit: 0 ok · 2 usage (not a git tree / unknown vault / unconfirmed delete / bad archive / sync:
        unconfigured-or-ambiguous remote / detached-HEAD / no-upstream / missing committer identity)
      · 3 genuine failure (slice-058/ADR-055: a fail-visible pin-write / git-init-actuator
        failure, DISTINCT from the benign exit-2 so a skill exit-check can tell them apart; sync:
        missing remote / auth / network / push-reject / refused data-loss pull / cache-invalidation).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store, _stdout, _sync_config, _vault_git_sync
from scripts.lib._vault_paths import (
    _BASE_CONFIG_FILE, _CONFIG_REL, _canonical, _git_common_dir, external_store_path, resolve_base,
)
from scripts.lib._vault_write import read_vault_root_config, safe_write_text, write_vault_root_config

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


def _git_common_dir_at(root: str) -> str | None:
    """Absolute git-common-dir resolved for a SPECIFIC root — mirrors
    ``_vault_paths._git_common_dir`` but runs ``git -C <root>`` so the git-init
    re-verify (slice-058) checks the newly-inited root, not the process cwd.
    Bytes captured + decoded in the main thread (a ``UnicodeDecodeError`` is not
    an ``OSError``)."""
    try:
        cp = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        raw = cp.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return raw or None


def cmd_git_init(args: argparse.Namespace) -> int:
    """Consented git-init actuator (slice-058 / ADR-055 / SC-107). Validate the
    root is a writable directory, run ``git init`` in LIST form (no ``shell=True``
    — a root with shell metacharacters cannot inject; m2), then RE-VERIFY that a
    git-common-dir now resolves AND its parent CANONICALLY equals the intended
    root (``_canonical`` folds Windows drive-case / separator / symlink spelling —
    M2 — so a legitimate init is never false-STOPped; and an init that bound an
    ancestor repo's home instead of the intended root is caught). Fail-closed:
    ANY failure -> stderr + exit 3, and no vault is touched. This NEVER
    self-consents; the caller (the skill's AskUserQuestion) owns consent."""
    root = Path(args.root)
    if not root.is_dir():
        sys.stderr.write(f"vault_admin git-init: {root} is not a directory — cannot init "
                         "(fail-closed; no vault written).\n")
        return 3
    if not os.access(root, os.W_OK):
        sys.stderr.write(f"vault_admin git-init: {root} is not writable — cannot init "
                         "(fail-closed; no vault written).\n")
        return 3
    try:
        cp = subprocess.run(["git", "init", str(root)], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"vault_admin git-init: `git init` failed to run ({exc}) — fail-closed.\n")
        return 3
    if cp.returncode != 0:
        msg = cp.stderr.decode("utf-8", "replace").strip()
        sys.stderr.write(f"vault_admin git-init: `git init {root}` exited {cp.returncode}: {msg} "
                         "— fail-closed.\n")
        return 3
    common = _git_common_dir_at(str(root))
    if not common:
        sys.stderr.write(f"vault_admin git-init: git init ran but no git-common-dir resolves at "
                         f"{root} — refusing to proceed (fail-closed).\n")
        return 3
    got, want = _canonical(str(Path(common).parent)), _canonical(str(root))
    if got != want:
        sys.stderr.write(
            f"vault_admin git-init: the resolved git-common-dir's root ({got}) does not match the "
            f"intended project root ({want}) — a GIT_DIR / GIT_COMMON_DIR override or a gitlink/worktree "
            "indirection is redirecting resolution (a plain `git init` always creates a local .git here); "
            "refusing to bind the wrong home (fail-closed).\n")
        return 3
    print(f"git-init: initialized git at {root} (common-dir {common})")
    return 0


def cmd_write_pin(args: argparse.Namespace) -> int:
    common = _git_common_dir()
    if not common:
        sys.stderr.write("vault_admin: not in a git work tree — no common-dir pin to write "
                         "(set AI_SDLC_VAULT_ROOT or run `git init`).\n")
        return 2
    vault = Path(args.vault) if args.vault else external_store_path(common)
    # M1/AC3 (slice-058): fail-VISIBLE pin write with a DISTINCT exit code (3) for a genuine
    # failure, so a skill exit-check can tell a real IO/encoding failure from the benign
    # not-a-git-tree (exit 2). A raw uncaught raise (the prior behavior) surfaced as a traceback.
    try:
        cfg = write_vault_root_config(common, vault)
    except (OSError, ValueError, UnicodeError) as exc:
        sys.stderr.write(f"vault_admin write-pin: FAILED to write the pin at "
                         f"{Path(common) / _CONFIG_REL} ({exc}) — no trustworthy pin; fail-visible.\n")
        return 3
    # AC3: read-back verify — the write is only 'done' when an independent read confirms it
    # (Shingo successive self-inspection). Converts a silent partial into a loud stop.
    got, want = read_vault_root_config(common), str(vault).strip()
    if got != want:
        sys.stderr.write(f"vault_admin write-pin: read-back MISMATCH after write "
                         f"(wrote {want!r}, read {got!r}) — pin NOT trustworthy; fail-visible.\n")
        return 3
    repo_root = str(Path(_canonical(common)).parent)  # <repo>/.git -> <repo>
    try:
        vault.mkdir(parents=True, exist_ok=True)
        (vault / _SOURCE_MARKER).write_text(repo_root + "\n", encoding="utf-8")
    except OSError as exc:
        # M1/must_not_defer (slice-058): WARN, never silently swallow. The pin (the load-bearing
        # artifact) is written + verified; the .source-repo back-ref is best-effort orphan-detection
        # metadata, so a failure here degrades detection but does not invalidate the pin.
        sys.stderr.write(f"vault_admin write-pin: WARNING — pin written + verified, but the "
                         f".source-repo back-ref could not be written ({exc}); orphan detection for "
                         "this vault will be degraded.\n")
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


def cmd_migrate(args: argparse.Namespace) -> int:
    """Convert gate-log <-> the shard store (slice-088 / ADR-106). Fail-closed, reversible,
    idempotent; holds the single gate-log.json.lock across the whole read -> build -> verify ->
    publish so no parallel-slice append is lost or duplicated. Actions are logged to stdout
    (auditable, must-not-defer #4); a build / verify / publish failure is fail-VISIBLE (stderr +
    exit 3 — the genuine-failure code, DISTINCT from the benign usage exit 2), and the flat file
    is left intact (a re-run is safe)."""
    vault = _this_repo_vault(args.vault)
    if vault is None:
        return 2
    if not vault.is_dir():
        sys.stderr.write(f"vault_admin migrate: no vault at {vault} — nothing to migrate.\n")
        return 2
    rel_key, array = "gate-log.json", "entries"
    try:
        result = _shard_store.migrate(vault, rel_key, array, reverse=args.reverse,
                                      log=lambda m: print(m))
    except Exception as exc:  # noqa: BLE001 — fail-closed actuator: ANY failure -> exit 3, flat intact
        sys.stderr.write(f"vault_admin migrate: FAILED ({type(exc).__name__}: {exc}) — fail-closed; "
                         f"the flat {rel_key} is left intact and no entry was lost or reordered.\n")
        return 3
    print(f"migrate: {result}")
    return 0


def cmd_read_entries(args: argparse.Namespace) -> int:
    """Print a (possibly sharded) aggregate's entries as JSON — the shell entry point for SKILL.md
    gate-log reads that cannot import _shard_store (slice-089 / SC-194). Derive-on-missing via
    _shard_store.read_entries: on a synced/cloned vault whose git-ignored cache is absent, the
    entries are rebuilt from the shard log (read-only, no write-back).

    Exit 3 (+ stderr) on a GENUINE read failure — a torn cache with no shards / a torn shard —
    matching vault_admin's 0-ok / 2-usage / 3-genuine-failure taxonomy (m1/critique, ADR-055), NOT
    the usage exit 2 (which would collide with 'bad args / unknown vault')."""
    vault = _this_repo_vault(args.vault)
    if vault is None:
        return 2
    if not vault.is_dir():
        sys.stderr.write(f"vault_admin read-entries: no vault at {vault}.\n")
        return 2
    try:
        entries = _shard_store.read_entries(vault, args.rel_key, args.array)
    except Exception as exc:  # noqa: BLE001 — fail-visible genuine failure (torn cache/shard) -> exit 3
        sys.stderr.write(f"vault_admin read-entries: FAILED to read {args.rel_key} "
                         f"({type(exc).__name__}: {exc}) — fail-visible; no rows emitted.\n")
        return 3
    print(json.dumps(entries, ensure_ascii=False))
    return 0


# ── sync-backend picker actuators (slice-097 / SC-206 / ADR-121 + ADR-123) ────────────

def _base_config_file() -> Path:
    """The external-vault BASE config file (`~/.claude/ai-sdlc-vault-base`) that `set-base` writes and
    `_vault_paths.resolve_base` reads. A dedicated seam (tests monkeypatch it, like `_git_common_dir`)
    so a set-base test never touches the developer's real home file."""
    return Path(os.path.expanduser(_BASE_CONFIG_FILE))


def _boto3_available() -> bool:
    """True iff boto3 can be imported — WITHOUT importing it (``find_spec`` only). set-backend uses
    this to WARN + surface the pip hint on absence while STILL persisting the s3 choice (m5: never a
    force-install, and validate-before-record is boto3-free so absence never blocks)."""
    return importlib.util.find_spec("boto3") is not None


def _s3_env_pairs(s3: dict) -> list[tuple[str, str]]:
    """(env-var, value) for each NON-EMPTY s3 field the config carries — the exact vars
    `_vault_s3_sync.resolve_config` reads (ADR-123 setdefault fold)."""
    return [(_sync_config.S3_FIELD_ENV[f], v) for f, v in s3.items()
            if f in _sync_config.S3_FIELD_ENV and v]


@contextlib.contextmanager
def _scoped_env_setdefault(pairs: list[tuple[str, str]]):
    """``os.environ.setdefault`` each (env, value) — NEVER clobbering a real env var — for the block,
    then RESTORE os.environ to its prior state on exit. Used for validate-before-record: the mutation
    mirrors cmd_sync's runtime setdefault fold (so validation resolves EXACTLY as a later sync will)
    but is scoped + reverted so an in-process caller/test sees no leak."""
    saved = {env: os.environ.get(env) for env, _ in pairs}
    try:
        for env, val in pairs:
            if val:
                os.environ.setdefault(env, val)
        yield
    finally:
        for env, old in saved.items():
            if old is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = old


def _warn_shadowing_s3_env(s3: dict) -> None:
    """m1: a real-env ``AISDLC_S3_*`` already set will SHADOW the freshly-picked file at sync time
    (env > file precedence, ADR-123), so a persist could be a silent no-op. Emit a VISIBLE WARN
    naming each shadowing var (a testable surface for the 'note')."""
    shadowed = sorted(_sync_config.S3_FIELD_ENV[f] for f in s3
                      if f in _sync_config.S3_FIELD_ENV and os.environ.get(_sync_config.S3_FIELD_ENV[f]))
    if shadowed:
        sys.stderr.write(
            "vault_admin: WARNING — these real-env vars are already set and will SHADOW the "
            "persisted sync-backend config at sync time (env > file precedence): "
            + ", ".join(shadowed) + ". Unset them so `vault_admin sync` uses the picked config.\n")


def cmd_set_backend(args: argparse.Namespace) -> int:
    """Consented actuator (owned by /setup's SKILL.md AskUserQuestion) that records the chosen sync
    backend {local,git,s3} + its NON-SECRET config to ``<git-common-dir>/aisdlc/sync-backend.json``.
    For s3, VALIDATES-before-record by calling the real, boto3-FREE ``resolve_config`` (a scoped
    setdefault fold identical to the runtime path), then persists via ``_sync_config`` + read-back-
    verifies. NEVER imports boto3 (m5); on boto3 absence WARNs + surfaces the pip hint but STILL
    persists (exit 0). Exit 0 ok / 2 usage (not a git tree / bad s3 config / userinfo endpoint /
    secret key) / 3 genuine failure (write or read-back mismatch)."""
    common = _git_common_dir()
    if not common:
        sys.stderr.write("vault_admin set-backend: not in a git work tree — no common-dir to anchor "
                         "the sync-backend config (run `git init`).\n")
        return 2
    backend = args.backend
    cfg: dict = {"backend": backend}
    if backend == "s3":
        s3: dict = {}
        for field, val in (("bucket", args.s3_bucket), ("endpoint", args.s3_endpoint),
                           ("region", args.s3_region), ("project", args.s3_project)):
            if val and val.strip():
                s3[field] = val.strip()
        # M3: reject a credential-bearing endpoint value early with a clear usage message.
        if _sync_config.endpoint_has_userinfo(s3.get("endpoint")):
            sys.stderr.write(
                "vault_admin set-backend: the S3 endpoint embeds credentials in its URL userinfo "
                "(user:pass@host) — refusing (M3). Use a bare endpoint (https://host:port); boto3's "
                "default chain supplies credentials.\n")
            return 2
        cfg["s3"] = s3
        # validate-before-record via the boto3-FREE resolve_config (scoped, reverted setdefault).
        vault = _this_repo_vault(args.vault)
        if vault is None:
            return 2
        try:
            with _scoped_env_setdefault(_s3_env_pairs(s3)):
                from scripts.lib import _vault_s3_sync  # boto3-free import; resolve_config never imports boto3
                _vault_s3_sync.resolve_config(vault)
        except _vault_git_sync.SyncUsageError as exc:
            sys.stderr.write(f"vault_admin set-backend: {exc}\n")
            return 2
        _warn_shadowing_s3_env(s3)  # m1: name any real-env AISDLC_S3_* that would shadow this persist
        if not _boto3_available():
            sys.stderr.write(
                "vault_admin set-backend: WARNING — boto3 is not installed; run `pip install boto3` "
                "before a real `sync --backend s3` (an OPTIONAL dependency, never auto-installed). "
                "Persisting the s3 choice anyway.\n")
    elif backend == "git":
        if args.remote and args.remote.strip():
            cfg["git"] = {"remote": args.remote.strip()}
    # persist (validate + BOM-free atomic write) — secret/usage errors are exit 2, IO is exit 3.
    try:
        p = _sync_config.save(cfg, common)
    except _sync_config.SyncConfigError as exc:
        sys.stderr.write(f"vault_admin set-backend: {exc}\n")
        return 2
    except (OSError, ValueError, UnicodeError) as exc:
        sys.stderr.write(f"vault_admin set-backend: FAILED to write the config ({exc}) — fail-visible.\n")
        return 3
    # read-back verify (Shingo self-inspection) — a silent partial becomes a loud stop.
    got = _sync_config.load(common)
    if not got or got.get("backend") != backend:
        sys.stderr.write(f"vault_admin set-backend: read-back MISMATCH after write (wrote backend "
                         f"{backend!r}, read {got!r}) — config NOT trustworthy; fail-visible.\n")
        return 3
    print(f"sync backend set: {backend} -> {p}")
    return 0


def cmd_set_base(args: argparse.Namespace) -> int:
    """Consented actuator: record the external-vault BASE directory in
    ``~/.claude/ai-sdlc-vault-base``. Confirms the base dir is creatable + writable, writes the
    config BOM-free, and READ-BACK-VERIFIES it (matching cmd_write_pin). Exit 0 ok / 3 genuine
    failure (base not writable, write or read-back mismatch). The lower-priority half of AC1 (the
    higher half — pinning the vault — is the existing `write-pin`)."""
    base_dir = Path(os.path.expanduser(args.dir))
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"vault_admin set-base: base dir {base_dir} is not creatable ({exc}) — "
                         "fail-visible.\n")
        return 3
    if not os.access(base_dir, os.W_OK):
        sys.stderr.write(f"vault_admin set-base: base dir {base_dir} is not writable — fail-visible.\n")
        return 3
    cfgfile = _base_config_file()
    try:
        safe_write_text(cfgfile, str(base_dir) + "\n")
    except (OSError, ValueError, UnicodeError) as exc:
        sys.stderr.write(f"vault_admin set-base: FAILED to write the base config at {cfgfile} "
                         f"({exc}) — fail-visible.\n")
        return 3
    try:
        got = cfgfile.read_text(encoding="utf-8-sig").strip()  # utf-8-sig: mirror resolve_base's read
    except OSError:
        got = None
    if got != str(base_dir):
        sys.stderr.write(f"vault_admin set-base: read-back MISMATCH (wrote {str(base_dir)!r}, read "
                         f"{got!r}) — base pin NOT trustworthy; fail-visible.\n")
        return 3
    print(f"vault base set: {cfgfile} -> {base_dir}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """`vault_admin sync push|pull [--backend git|s3]` — vault sync over a git remote (slice-092 /
    ADR-114) OR an S3/MinIO object store (slice-095 / ADR-119). Thin CLI wrapper that resolves the
    vault, dispatches to the selected engine, and maps its typed exceptions onto the 0-ok / 2-usage /
    3-genuine-failure taxonomy (mirroring cmd_migrate). Both engines RAISE the SAME
    ``_vault_git_sync.SyncUsageError`` / ``SyncFailure`` classes (the S3 engine imports them), so the
    single except surface covers both: a ``SyncUsageError`` (bad setup / config / optional-dep / an
    unresolvable S3 prefix) is exit 2 with a hint; a ``SyncFailure`` (network / auth / push-reject /
    a refused data-loss pull / a fork / an S3-slip key / a gapped shard set) is exit 3; the engine's
    log lines (auditable summary, the unredacted-transmission note) go to stdout."""
    vault = _this_repo_vault(args.vault)
    if vault is None:
        return 2
    if not vault.is_dir():
        sys.stderr.write(f"vault_admin sync: no vault at {vault}.\n")
        return 2
    # Load the persisted sync-backend config (ADR-121/123). Absent/empty/malformed -> None (the git
    # back-compat default); a secret-shaped key / userinfo endpoint -> SyncConfigError (fail-visible).
    try:
        cfg_file = _sync_config.load(
            _git_common_dir(), warn=lambda m: sys.stderr.write(f"vault_admin sync: {m}\n"))
    except _sync_config.SyncConfigError as exc:
        sys.stderr.write(f"vault_admin sync: {exc}\n")
        return 2
    # Resolve the backend: explicit --backend > persisted config > git (back-compat: no config AND no
    # flag => git, so every pre-slice-097 bare `sync` caller is unchanged).
    backend = getattr(args, "backend", None) or (cfg_file or {}).get("backend") or "git"
    if backend == "local":
        print("sync: local backend — no remote sync configured (nothing to push/pull).")
        return 0
    try:
        if backend == "s3":
            s3 = (cfg_file or {}).get("s3") or {}
            # ADR-123: fold the file's non-secret fields into os.environ via setdefault (NEVER
            # clobbering a real env var), then call the SHIPPED resolve_config UNCHANGED. Effective
            # precedence CLI-arg > real-env > file > computed-default is thereby STRUCTURAL. Scoped to
            # this one-shot CLI subprocess (a fresh process per SKILL.md call); tests save/restore env.
            _warn_shadowing_s3_env(s3)  # m1: a stale real-env AISDLC_S3_* silently shadows the file
            for env, val in _s3_env_pairs(s3):
                os.environ.setdefault(env, val)
            from scripts.lib import _vault_s3_sync
            cfg = _vault_s3_sync.resolve_config(
                vault, bucket=args.s3_bucket, endpoint_url=args.s3_endpoint_url, prefix=args.s3_prefix)
            client = _vault_s3_sync.build_client(cfg)  # SyncUsageError (exit 2) if boto3 is absent
            if args.direction == "push":
                result = _vault_s3_sync.sync_push(vault, cfg=cfg, client=client, force=args.force,
                                                  log=lambda m: print(m))
            else:
                result = _vault_s3_sync.sync_pull(vault, cfg=cfg, client=client, force=args.force,
                                                  log=lambda m: print(m))
        else:
            # git backend: fall back to the persisted remote when --remote is not passed.
            remote = args.remote or (cfg_file or {}).get("git", {}).get("remote")
            if args.direction == "push":
                result = _vault_git_sync.sync_push(vault, remote_arg=remote, log=lambda m: print(m))
            else:
                result = _vault_git_sync.sync_pull(
                    vault, remote_arg=remote, force=args.force,
                    force_drop_local=args.force_drop_local, log=lambda m: print(m))
    except _vault_git_sync.SyncUsageError as exc:
        sys.stderr.write(f"vault_admin sync: {exc}\n")
        return 2
    except _vault_git_sync.SyncFailure as exc:
        sys.stderr.write(f"vault_admin sync: {exc}\n")
        return 3
    except Exception as exc:  # noqa: BLE001 — fail-closed actuator: any unexpected failure -> exit 3
        sys.stderr.write(f"vault_admin sync: FAILED ({type(exc).__name__}: {exc}) — fail-closed.\n")
        return 3
    print(f"sync: {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="vault_admin", description="Vault lifecycle admin (4.7).")
    sub = p.add_subparsers(dest="cmd", required=True)
    wp = sub.add_parser("write-pin", help="write the tier-2 common-dir pin + source back-ref")
    wp.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    gi = sub.add_parser("git-init", help="consented git init + fail-closed root re-verify (slice-058)")
    gi.add_argument("--root", default=".", help="project root to init (default: cwd)")
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
    mg = sub.add_parser("migrate", help="convert gate-log <-> the shard store (slice-088; "
                                        "fail-closed, reversible, idempotent)")
    mg.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    mg.add_argument("--reverse", action="store_true",
                    help="rebuild the flat gate-log.json from shards, remove the shard dir, and "
                         "un-ignore the flat file (the symmetric rollback of a forward migrate)")
    re_ = sub.add_parser("read-entries", help="print a sharded aggregate's entries as JSON, "
                                              "deriving on a missing cache (slice-089)")
    re_.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    re_.add_argument("--rel-key", default="gate-log.json",
                     help="the aggregate's vault-relative cache file (default: gate-log.json)")
    re_.add_argument("--array", default="entries",
                     help="the array key to read (only 'entries' is supported today)")
    sy = sub.add_parser("sync", help="push|pull the vault sync-set over a git remote or an S3/MinIO "
                                     "bucket (slice-092/095; transmits the WHOLE vault UNREDACTED — "
                                     "use a PRIVATE remote / a Block-Public-Access bucket)")
    sy.add_argument("direction", choices=["push", "pull"], help="push or pull the vault")
    sy.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    sy.add_argument("--backend", choices=["local", "git", "s3"], default=None,
                    help="sync transport (default: the persisted /setup choice in "
                         "<git-common-dir>/aisdlc/sync-backend.json, else 'git' for back-compat). "
                         "'local' = no remote sync; 'git' = a git remote (slice-092); 's3' = S3/MinIO "
                         "object store (slice-095)")
    sy.add_argument("--remote", default=None,
                    help="git backend: remote name (default: 'origin' / the sole remote for push, "
                         "the branch upstream's remote for pull; ambiguity is an error)")
    sy.add_argument("--force", action="store_true",
                    help="pull: mirror-reset (git) / overwrite a conflicting local artifact (s3), "
                         "discarding local edits (git still refuses to drop unpushed local commits)")
    sy.add_argument("--force-drop-local", action="store_true",
                    help="git pull: additionally DISCARD unpushed local commits (enumerated before "
                         "the reset); implies --force")
    sy.add_argument("--s3-bucket", default=None,
                    help="s3 backend: target bucket (default: env AISDLC_S3_BUCKET)")
    sy.add_argument("--s3-endpoint-url", default=None,
                    help="s3 backend: endpoint URL (default: env AISDLC_S3_ENDPOINT; unset = AWS S3, "
                         "set = MinIO / S3-compatible)")
    sy.add_argument("--s3-prefix", default=None,
                    help="s3 backend: object-key prefix (default: env AISDLC_S3_PREFIX, else a "
                         "machine-invariant hash of the git remote URL / AISDLC_S3_PROJECT)")
    # slice-097: /setup's consented backend picker + base-location actuators.
    sb = sub.add_parser("set-backend", help="record /setup's chosen sync backend (local|git|s3) + "
                                            "its non-secret config for a later `sync` (slice-097)")
    sb.add_argument("--backend", choices=["local", "git", "s3"], required=True,
                    help="the sync backend to persist (local = no remote sync)")
    sb.add_argument("--s3-bucket", default=None, help="s3: target bucket (validated before record)")
    sb.add_argument("--s3-endpoint", default=None,
                    help="s3: endpoint URL (unset = AWS S3; a userinfo-bearing URL is REFUSED — M3)")
    sb.add_argument("--s3-region", default=None, help="s3: region (default us-east-1 at sync time)")
    sb.add_argument("--s3-project", default=None,
                    help="s3: a machine-INVARIANT project id (stable per project, NOT per machine) "
                         "-> the S3 key prefix, so two machines pull the SAME prefix (B3)")
    sb.add_argument("--remote", default=None, help="git: the remote name to record")
    sb.add_argument("--vault", default=None, help="vault path (default: computed for this repo)")
    sbase = sub.add_parser("set-base", help="set the external-vault base dir "
                                            "(~/.claude/ai-sdlc-vault-base), read-back-verified (slice-097)")
    sbase.add_argument("dir", help="the base directory to record")
    args = p.parse_args(argv)
    return {"write-pin": cmd_write_pin, "git-init": cmd_git_init, "list": cmd_list,
            "uninstall": cmd_uninstall, "export": cmd_export, "import": cmd_import,
            "migrate": cmd_migrate, "read-entries": cmd_read_entries, "sync": cmd_sync,
            "set-backend": cmd_set_backend, "set-base": cmd_set_base}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
