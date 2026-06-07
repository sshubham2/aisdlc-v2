"""Vault-root path constant (v2; ported from v1 slice-068/093/115 + ADR-085/107/109).

Exports ``VAULT_ROOT`` — the single seam routing ``scripts.lib`` filesystem
references to the vault directory.

**v2 change from v1 (the flip is collapsed into the default).** v1 defaulted to
an in-tree ``architecture/`` vault and relocated it to the external shared store
``~/.aisdlc/<slug>-<hash>/`` only after an explicit one-time *flip*
(``tools/_vault_flip.py``) that wrote the per-project git-common-dir config. v2's
repo is **code-only** — there is no in-tree vault to migrate — so the external
store is the DEFAULT resolution, with no flip step. The path scheme is byte-for-byte
identical to v1's ``_vault_flip.external_store_path`` (so an already-flipped v1
project resolves to the same folder).

**Resolution precedence** (read ONCE at module import):

  1. env var ``AI_SDLC_VAULT_ROOT`` (if set) — explicit override / test injection /
     harness-set. UNCHANGED from v1 (ADR-065).
  2. the per-project config at ``<git-common-dir>/aisdlc/vault-root`` (a single
     line: the absolute vault path) — shared across all worktrees of a repo (the
     git common-dir is shared), never git-tracked. An explicit pin (e.g. a custom
     base, or a v1 project already flipped). UNCHANGED from v1 (ADR-085).
  3. **computed external-store default** ``<base>/<slug>-<shorthash>`` —
     ``base`` = ``~/.claude/ai-sdlc-vault-base`` contents or ``~/.aisdlc``;
     ``slug`` = sanitized repo-root basename; ``shorthash`` = first 8 hex of
     ``sha256(os.path.normcase(resolve(<abs git-common-dir>)))``. Keyed on the git
     common-dir so all worktrees of a repo share ONE vault. When NOT a git work
     tree (git unavailable / not a repo), the seed falls back to the **cwd**
     (slug = cwd basename) so a non-git project still resolves to a deterministic
     external store. This replaces v1's ``Path("architecture")`` default.

**Leaf invariant**: this module imports ONLY stdlib (``hashlib``, ``os``, ``re``,
``subprocess``, ``sys``, ``pathlib``) — never ``scripts.lib.*`` — so it stays the
dependency leaf of the VAULT_ROOT cascade (``scripts.lib._vault_write`` imports
``_CONFIG_REL`` FROM here; the external-store helpers below also live here so a
future ``_vault_flip`` port imports them from the leaf rather than re-deriving).

**Read-at-import + consumer-freeze cascade**: the env var, the git-common-dir
config, AND the computed default are resolved EXACTLY ONCE at this module's
import. Downstream consumers that compose ``VAULT_ROOT`` into their own
module-level constants FREEZE the derived Path at THEIR import-time value;
in-process ``monkeypatch.setattr`` of ``VAULT_ROOT`` does NOT propagate. Cross-process
env override works via subprocess fixtures (env injection at process boundary).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

_ENV_VAR = "AI_SDLC_VAULT_ROOT"

# The per-project config path RELATIVE to the git common-dir. SINGLE SOURCE OF
# TRUTH for the config location: ``scripts.lib._vault_write`` imports this
# constant rather than re-deriving it, so the inline reader here and the writer
# there cannot diverge.
_CONFIG_REL = "aisdlc/vault-root"

# External-store base (v1 ADR-085 §Install enhancement; ported verbatim).
_BASE_CONFIG_FILE = "~/.claude/ai-sdlc-vault-base"
_DEFAULT_BASE = "~/.aisdlc"
_MAX_SLUG_LEN = 48


def _stderr(msg: str) -> None:
    """Encoding-safe stderr write (leaf-safe — stdlib only). A bare
    ``print(..., file=sys.stderr)`` raises ``UnicodeEncodeError`` on a non-ASCII
    path under a cp1252 stderr (the documented Windows footgun), which would
    crash EVERY consumer at import since these diagnostics fire during
    ``_resolve_vault_root`` at module-import. Write UTF-8 bytes with
    ``errors="replace"`` when a buffer is available, else an ascii-folded
    ``print`` fallback.
    """
    line = msg + "\n"
    buf = getattr(sys.stderr, "buffer", None)
    if buf is not None:
        try:
            buf.write(line.encode("utf-8", "replace"))
            buf.flush()
            return
        except (OSError, ValueError):
            pass
    try:
        print(msg, file=sys.stderr)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), file=sys.stderr)


def _stdout(msg: str) -> None:
    """Encoding-safe stdout write (leaf-safe — stdlib only), mirroring
    ``_stderr``. The discoverability CLI (``python -m scripts.lib._vault_paths``)
    prints the resolved vault path, which may be non-ASCII; a bare ``print``
    would raise ``UnicodeEncodeError`` on a cp1252 console. This module cannot
    import ``scripts.lib._stdout`` — that would break the stdlib-only leaf
    invariant — so it carries its own tiny writer.
    """
    line = msg + "\n"
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        try:
            buf.write(line.encode("utf-8", "replace"))
            buf.flush()
            return
        except (OSError, ValueError):
            pass
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


# ── external-store path computation (ported from v1 _vault_flip; ADR-085/109) ──

def _git_common_dir() -> str | None:
    """Absolute git-common-dir string, or ``None`` when not a git work tree /
    git unavailable.

    Always ``--path-format=absolute --git-common-dir`` (never bare). Captured as
    BYTES and decoded explicitly in the MAIN thread: a
    ``subprocess.run(..., encoding="utf-8")`` decodes in the reader thread and
    would raise an UNCAUGHT ``UnicodeDecodeError`` there on a non-UTF-8 path
    (``UnicodeDecodeError`` is not an ``OSError``).
    """
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,  # BYTES — decoded explicitly below (main thread)
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # git binary unavailable / failed to spawn
    if cp.returncode != 0:
        return None  # not inside a git work tree
    try:
        raw = cp.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        _stderr("WARN: AI-SDLC git-common-dir path is not UTF-8; keying vault on cwd.")
        return None
    return raw or None


def _canonical(path_str: str) -> str:
    """``normcase(resolve(path))`` — the stable hash seed: resolves symlinks and
    folds case (Windows is case-insensitive) so equivalent path spellings map to
    ONE store (worktree / idempotency stability)."""
    return os.path.normcase(str(Path(path_str).resolve()))


def resolve_base() -> Path:
    """The external-store BASE directory: ``~/.claude/ai-sdlc-vault-base`` contents,
    else the default ``~/.aisdlc``. An absent/empty/unreadable base file is the
    NORMAL path → default (not an error)."""
    cfg = Path(os.path.expanduser(_BASE_CONFIG_FILE))
    try:
        text = cfg.read_text(encoding="utf-8-sig").strip()  # utf-8-sig strips a leading BOM (PowerShell Out-File adds one; plain utf-8 leaves ﻿ in the path)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        text = ""
    return Path(os.path.expanduser(text or _DEFAULT_BASE))


def _project_slug(raw_name: str) -> str:
    """Human-readable, filesystem-safe slug from a directory basename: non
    ``[A-Za-z0-9._-]`` runs fold to ``-``; bounded to ``_MAX_SLUG_LEN``; empty →
    ``"vault"``. On case-insensitive filesystems the seed is already
    ``normcase``-lowered, so the slug folds lowercase there — by design."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.")[:_MAX_SLUG_LEN].strip("-.")
    return slug or "vault"


def external_store_path(common: str | None = None, *, base: Path | None = None) -> Path:
    """``<base>/<project-slug>-<shorthash>`` — the per-project external vault dir
    (identical scheme to v1 ``_vault_flip.external_store_path``).

    ``common`` = the absolute git-common-dir (pass it to avoid a second git call;
    when ``None`` it is resolved here). In a git work tree the seed is the
    canonical common-dir and the slug is the repo-root basename (the common-dir's
    parent). NOT in a git work tree → the seed is the canonical **cwd** and the
    slug is the cwd basename. Both segments derive from the ONE canonical seed, so
    the whole name is deterministic + per-repo-stable + worktree-shared.
    """
    base = base or resolve_base()
    if common is None:
        common = _git_common_dir()
    if common:
        cc = _canonical(common)
        slug_src = Path(cc).parent.name  # repo root = parent of <repo>/.git
    else:
        cc = _canonical(str(Path.cwd()))
        slug_src = Path(cc).name  # cwd basename (non-git fallback)
    short = hashlib.sha256(cc.encode("utf-8")).hexdigest()[:8]
    return base / f"{_project_slug(slug_src)}-{short}"


# ── git-common-dir config (the explicit pin / v1 flip signal) ──────────────────

def _read_config_at(common: str | None) -> str | None:
    """Return the vault path pinned at ``<common-dir>/aisdlc/vault-root``, or
    ``None`` when no config applies. Present-but-unreadable/empty → stderr WARN +
    ``None`` (never a silent mis-resolve)."""
    if not common:
        return None
    cfg = Path(common) / _CONFIG_REL
    try:
        if not cfg.exists():
            return None  # no pin written (the normal case)
        text = cfg.read_text(encoding="utf-8-sig").strip()  # utf-8-sig strips a leading BOM (PowerShell Out-File adds one; plain utf-8 leaves ﻿ in the path)
    except (OSError, UnicodeDecodeError) as exc:
        _stderr(
            f"WARN: AI-SDLC vault-root config at {cfg} is present but unreadable "
            f"({exc}); ignoring it (using the computed default)."
        )
        return None
    if not text:
        _stderr(
            f"WARN: AI-SDLC vault-root config at {cfg} is empty; "
            f"ignoring it (using the computed default)."
        )
        return None
    return text


def _resolve_vault_root() -> tuple[Path, str]:
    """3-tier resolution: env → git-common-dir config → computed external-store
    default. Returns ``(path, source)`` where source ∈ {``env``, ``config``,
    ``default``}. One git call (shared between the config read + the default
    computation).

    Observability: a NON-default resolution (env or config) emits a one-line
    stderr INFO naming the chosen root + WHY; the computed default — the normal
    v2 case, fired on every tool import — stays SILENT to avoid per-import noise.
    """
    env = os.environ.get(_ENV_VAR)
    if env:
        env = env.lstrip("\ufeff").strip()  # tolerate a leading BOM / whitespace (PowerShell-written values, stale env-file round-trips)
    if env:
        _stderr(f"INFO: AI-SDLC vault root = {env!r} (via {_ENV_VAR} env var).")
        return Path(env), "env"
    common = _git_common_dir()  # one git call; reused by the default computation
    cfg = _read_config_at(common)
    if cfg:
        _stderr(f"INFO: AI-SDLC vault root = {cfg!r} (via git-common-dir config).")
        return Path(cfg), "config"
    if common is None:
        # cwd-fallback: NOT a git work tree, so there is no stable per-repo key. Keying
        # on cwd is best-effort and depends on WHERE the process runs (e.g. running a
        # tool from $HOME keys on the home dir, not the project) — surface it LOUDLY so
        # it is never a silent mis-name. The git-common-dir case stays silent (correct +
        # worktree-stable). This fires on every import in a non-git project, which is the
        # point: it nags the user toward a stable pin.
        _stderr(
            f"WARN: AI-SDLC vault not resolved from a git work tree — keying the vault on "
            f"the current directory ({Path.cwd()}); the slug is its basename, not necessarily "
            f"the project. Run `git init` in the project root, or set {_ENV_VAR}, to pin a "
            f"stable per-project vault."
        )
    return external_store_path(common), "default"


VAULT_ROOT, _RESOLUTION_SOURCE = _resolve_vault_root()

# True iff resolution fell through to the computed external-store default (env
# unset AND no git-common-dir config pin). Frozen-at-import per the consumer-freeze
# cascade.
VAULT_ROOT_IS_DEFAULT: bool = _RESOLUTION_SOURCE == "default"


if __name__ == "__main__":  # pragma: no cover - thin discoverability CLI
    # ``python -m scripts.lib._vault_paths``        → human-readable (path + source)
    # ``python -m scripts.lib._vault_paths --path`` → just the resolved path (for $() capture)
    if "--path" in sys.argv[1:]:
        _stdout(str(VAULT_ROOT))
    else:
        if _RESOLUTION_SOURCE == "env":
            _source = f"{_ENV_VAR} env var"
        elif _RESOLUTION_SOURCE == "config":
            _source = f"git-common-dir config file (<git-common-dir>/{_CONFIG_REL})"
        else:
            _source = f"computed external-store default (base {resolve_base()})"
        _stdout(f"vault-root: {VAULT_ROOT}")
        _stdout(f"source:     {_source}")
