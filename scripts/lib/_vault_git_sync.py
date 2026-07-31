"""_vault_git_sync.py — git-native push/pull of the vault sync-set (slice-092 / SC-201 / ADR-114).

The engine behind `vault_admin sync push|pull`. SYNC model (a local working copy that is explicitly
pushed to / pulled from a git remote), NOT a live-mounted backend and NOT a multi-writer merge/CRDT
engine. Concurrent CLAIMING on a shared vault is coordinated elsewhere, in the ``_claim_coord`` seam
(slice-091 / ADR-113) and its git backend ``_claim_git`` (slice-100 / SC-216 / ADR-131); a
multi-writer merge engine for whole-file artifacts remains unbuilt. "Sync the log, never the view":
the append-only shard log
(slice-088) + `_meta.json` + whole-file artifacts are shipped; the git-ignored derived `gate-log.json`
cache + all coordination cruft are excluded and rebuilt on the far side by the EXISTING
`_shard_store.read_entries` derive-on-missing (no new reconstruct code). `.git/` is structurally never
tracked.

Plain list-form subprocess git — no shell, no git library, no StorageBackend abstraction — mirroring
`vault_admin`'s `cmd_git_init` / `cmd_migrate` fail-closed actuator shape. Credentials are delegated
entirely to git; this module never reads or stores them.

Error surface (mapped to `vault_admin`'s 0-ok / 2-usage / 3-genuine-failure taxonomy by `cmd_sync`):
  * ``SyncUsageError`` -> exit 2: not a git work tree, unconfigured/ambiguous remote, detached HEAD /
    no upstream, missing committer identity, a --remote / upstream mismatch. These are "fix your
    setup" conditions with an actionable hint.
  * ``SyncFailure``    -> exit 3: a genuine git failure (missing remote / auth / network / push
    rejection), a refused pull (dirty tree / divergent history / unpushed-commit guard), or a
    fail-visible derived-cache invalidation failure.

Critic-accepted refinements folded in (critique.json / DR-1, all accepted-pending):
  * M1  — remote resolution defaults to 'origin' / the sole remote and ERRORS on ambiguity (never
    silently picks one); the pull target is the branch's configured UPSTREAM and REFUSES on a
    detached HEAD / no upstream (never falls back to <remote>/HEAD); a missing committer identity
    surfaces an exit-2 hint.
  * M2  — the subprocess env sets ``GIT_TERMINAL_PROMPT=0`` AND (when unset) a BatchMode
    ``GIT_SSH_COMMAND`` so an SSH remote fails FAST + visible instead of hanging on a passphrase /
    host-key prompt.
  * M3  — the derived-cache invalidation on pull is fail-VISIBLE: a failed delete of a PRESENT cache
    after a successful ref-advance surfaces (exit 3, after a bounded EPERM retry), never swallowed;
    a missing cache (fresh clone) is a benign no-op.
  * m1  — a ``.gitattributes`` (``* -text``) is pinned into the sync-set so the log + artifacts are
    byte-faithful across ``core.autocrlf`` configs / platforms.
  * m3  — ``sync push`` announces that it transmits the WHOLE vault UNREDACTED (choose a PRIVATE
    remote); a secret committed once persists in the pushed history forever, so a private remote is
    the durable mitigation.
  * M-add-1 — the working-tree dirty guard is blind to committed-but-unpushed LOCAL commits; before a
    ``--force`` reset the engine detects a divergent local (HEAD not an ancestor of the target),
    ENUMERATES the to-be-dropped commit shas, and REFUSES unless ``--force-drop-local`` (which
    proceeds but PRINTS them) — the loss is never silent.

Leading-underscore module -> auto-excluded from the PMI-1 inventory (like ``_shard_store`` /
``_vault_write``).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/_vault_git_sync.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store  # noqa: E402 — after the sys.path bootstrap

# Sync-invariant excludes OWNED by this module (ADR-114 §6): vault-wide coordination cruft + the
# node-local back-ref. DISJOINT from `_shard_store._gitignore_entries` (the derived-cache line
# `/gate-log.json` + `gate-log/*.lock|*.tmp`, which `migrate --reverse` strips symmetrically) —
# folding those here would let a --reverse'd, then re-pushed, vault wrongly ignore its now-truth flat
# file. `*.lock`/`*.tmp` are no-slash globs -> match at ANY depth (subsuming the shard-dir cruft +
# the 11 root sidecars).
_SYNC_IGNORE = ["*.lock", "*.tmp", ".source-repo", ".s3-sync-state.json"]  # +slice-095: the S3 backend's
# node-local last-synced-hash baseline (`_vault_s3_sync.BASELINE_NAME`) is per-NODE state and must
# never propagate via ANY backend — a git-synced baseline would give a peer node a wrong 3-way merge.
_GITATTRIBUTES_RULE = "* -text"  # m1: disable autocrlf normalization -> byte-faithful WAL

_SSH_CONNECT_TIMEOUT = 10  # seconds (M2: bound the SSH connect so a dead host fails fast)
_GIT_TIMEOUT = 300  # seconds — a generous backstop for network push/pull; BatchMode fails far sooner

# Windows held-handle (EPERM) bounded retry for the cache unlink (M3). Local names (NOT imported from
# _vault_write) so a test can monkeypatch them without touching the shared write path.
_EPERM_RETRIES = 6
_EPERM_BACKOFF_BASE = 0.05  # seconds; exponential 0.05, 0.10, 0.20, ...


class SyncUsageError(Exception):
    """A "fix your setup" condition -> exit 2 (usage)."""


class SyncFailure(Exception):
    """A genuine sync failure or a refused data-loss operation -> exit 3."""


# ── subprocess git (list-form, no shell, fast-fail auth) ──────────────────────────

def _sync_env() -> dict:
    """The subprocess env: git never prompts. ``GIT_TERMINAL_PROMPT=0`` suppresses git's own HTTPS
    credential prompt; a BatchMode ``GIT_SSH_COMMAND`` (only when the user has not set their own)
    stops the SSH transport from hanging on a passphrase / host-key confirmation (M2)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not env.get("GIT_SSH_COMMAND"):
        env["GIT_SSH_COMMAND"] = f"ssh -oBatchMode=yes -oConnectTimeout={_SSH_CONNECT_TIMEOUT}"
    return env


def _git(args: list[str], cwd: Path, *, check: bool = True,
         timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd`` (list-form, captured, text). A subprocess-level failure
    (git absent / timeout) is always a genuine ``SyncFailure``; a non-zero exit raises only when
    ``check`` (callers that map specific non-zero exits to usage errors pass ``check=False``)."""
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_sync_env(), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SyncFailure(f"git {' '.join(args)} timed out after {timeout}s in {cwd}") from exc
    except OSError as exc:
        raise SyncFailure(f"could not run git {' '.join(args)} in {cwd} ({exc})") from exc
    if check and r.returncode != 0:
        raise SyncFailure(f"git {' '.join(args)} failed (rc={r.returncode}): {r.stderr.strip()}")
    return r


def _short(sha: str | None) -> str:
    return sha[:9] if sha else "(none)"


# ── sync-set hygiene (m1 / AC3 / AC4) ─────────────────────────────────────────────

def ensure_sync_gitattributes(vault: Path, *, log) -> None:
    """Pin ``* -text`` into ``<vault>/.gitattributes`` (m1) so line endings are byte-faithful
    regardless of ``core.autocrlf`` — the WAL shards + whole-file artifacts must not gain spurious
    CRLF diffs across platforms/configs. Idempotent; preserves any existing content."""
    ga = Path(vault) / ".gitattributes"
    existing = ga.read_text(encoding="utf-8") if ga.exists() else ""
    if _GITATTRIBUTES_RULE in existing.splitlines():
        return
    text = existing + ("\n" if existing and not existing.endswith("\n") else "") + _GITATTRIBUTES_RULE + "\n"
    ga.write_text(text, encoding="utf-8", newline="")
    log(".gitattributes: pinned `* -text` (byte-faithful line endings across autocrlf configs)")


def ensure_sync_gitignore(vault: Path, *, log) -> None:
    """Idempotently ensure ``<vault>/.gitignore`` excludes the sync-invariant cruft (``*.lock``,
    ``*.tmp``, ``.source-repo``), then ``git rm --cached`` any already-tracked cruft so ``git
    ls-files`` shows none (AC4). Does NOT touch ``_shard_store._gitignore_entries`` (the derived-cache
    line) — that lifecycle is owned by ``migrate``/``--reverse`` (CC-001 disjointness)."""
    vault = Path(vault)
    gi = vault / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    added = [w for w in _SYNC_IGNORE if w not in lines]
    if added:
        lines.extend(added)
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        log(f".gitignore: added sync-invariant excludes {added}")
    # Untrack any cruft already committed in a prior life (AC4). --ignore-unmatch: a clean no-op when
    # nothing matches (e.g. the first push before any commit). Pathspecs are git no-slash globs.
    rm = _git(["rm", "--cached", "--ignore-unmatch", "-r", "--", *_SYNC_IGNORE], vault, check=False)
    removed = [ln for ln in rm.stdout.splitlines() if ln.strip()]
    if removed:
        log(f".gitignore: untracked {len(removed)} previously-tracked cruft path(s)")


# ── derived-cache invalidation (INV-A / M3, fail-visible) ─────────────────────────

def _unlink_with_retry(path: Path) -> None:
    """Delete ``path`` with a bounded EPERM retry (Windows held handle). On budget exhaustion RAISE
    ``SyncFailure`` (M3): a stale derived cache surviving a ref-advance would make the read fast-path
    serve pre-pull rows — a silent data loss — so this NEVER swallows the failure."""
    last: BaseException | None = None
    for attempt in range(_EPERM_RETRIES):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return  # raced away between the exists()-check and here — fine
        except PermissionError as exc:  # Windows WinError 5: a handle is held
            last = exc
            time.sleep(_EPERM_BACKOFF_BASE * (2**attempt))
    raise SyncFailure(
        f"FAILED to invalidate the derived cache {path} after {_EPERM_RETRIES} attempts (a handle "
        "is held by another process?) — a stale cache surviving the ref-advance would serve pre-pull "
        f"rows (silent data loss); fail-visible. Last error: {last}")


def _invalidate_derived_caches(vault: Path, *, log) -> None:
    """Delete each SHARDED aggregate's derived cache so the next ``read_entries`` derives from the
    freshly-reset shards (INV-A). A PRESENT cache whose delete fails is fail-visible (M3); an ABSENT
    cache (a fresh clone/replica) is the benign no-op.

    B1 (code-review): the delete is gated on ``is_sharded`` — checked AFTER the pull's ref advance, so
    it reflects the freshly-pulled state — EXACTLY as ``read_entries`` gates its derive. On a
    non-sharded / un-migrated vault (the fresh-install default) ``<rel_key>`` is the FLAT SOURCE OF
    TRUTH, not a derived view, and deleting it would be a silent data loss (there is no shard log to
    rebuild from). Only a real derived cache (a live shard dir alongside it) is invalidated."""
    vault = Path(vault)
    for (rel_key, array), _name in _shard_store._SHARDED.items():
        if not _shard_store.is_sharded(vault, rel_key, array):
            continue  # non-sharded: /<rel_key> is the flat TRUTH file, not a derived cache (B1)
        cache = vault / rel_key
        if not cache.exists():
            continue  # sharded but no local cache (fresh clone / replica) — benign
        _unlink_with_retry(cache)
        log(f"invalidated derived cache /{rel_key} (re-derives on next read)")


# ── remote / branch / upstream / identity resolution (M1) ─────────────────────────

def _require_git_tree(vault: Path) -> None:
    r = _git(["rev-parse", "--is-inside-work-tree"], vault, check=False)
    if r.returncode != 0 or r.stdout.strip() != "true":
        raise SyncUsageError(
            f"{vault} is not a git work tree — run `vault_admin git-init` (or `git -C \"{vault}\" "
            "init`) and add a remote before sync.")


def _resolve_remote(vault: Path, remote_arg: str | None) -> str:
    """M1: an explicit --remote must exist; else default to 'origin' / the sole remote; ERROR on
    ambiguity (multiple remotes, none 'origin') — never silently pick one."""
    remotes = _git(["remote"], vault).stdout.split()
    if remote_arg:
        if remote_arg not in remotes:
            raise SyncUsageError(
                f"remote {remote_arg!r} is not configured (have: {', '.join(remotes) or 'none'}); "
                f"add it with `git -C \"{vault}\" remote add {remote_arg} <url>`.")
        return remote_arg
    if not remotes:
        raise SyncUsageError(
            f"no git remote is configured for the vault — add one with `git -C \"{vault}\" remote "
            "add origin <url>` (or pass --remote).")
    if len(remotes) == 1:
        return remotes[0]
    if "origin" in remotes:
        return "origin"
    raise SyncUsageError(
        f"multiple remotes are configured ({', '.join(sorted(remotes))}) and none is named 'origin' "
        "— pass --remote <name> to choose (refusing to silently pick one).")


def _current_branch(vault: Path) -> str:
    """The checked-out branch; REFUSE a detached HEAD (M1) — `git push HEAD` on a detached HEAD has
    no branch name and a `<remote>/HEAD` reset is nonsense."""
    r = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], vault, check=False)
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        raise SyncUsageError(
            "the vault repo is in DETACHED HEAD state — checkout a branch before sync (refusing to "
            "sync a detached HEAD).")
    return branch


def _resolve_upstream(vault: Path) -> tuple[str, str, str]:
    """M1: the pull target is the current branch's configured UPSTREAM (e.g. ``origin/main``). REFUSE
    on a detached HEAD or a branch with no upstream — never fall back to ``<remote>/HEAD``. Returns
    ``(remote, branch, upstream_ref)``."""
    r = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], vault, check=False)
    ref = r.stdout.strip()
    if r.returncode != 0 or not ref:
        b = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], vault, check=False)
        branch = b.stdout.strip()
        if b.returncode != 0 or not branch:
            raise SyncUsageError(
                "the vault repo is in DETACHED HEAD state — checkout a branch before pull (refusing "
                "to reset to <remote>/HEAD).")
        raise SyncUsageError(
            f"branch {branch!r} has no upstream configured — set one with `git -C \"{vault}\" push "
            f"-u <remote> {branch}` (first push) or `git -C \"{vault}\" branch "
            f"--set-upstream-to=<remote>/{branch}` (refusing to guess the pull target).")
    remote, _, branch = ref.partition("/")  # remote names carry no '/'; branch may
    return remote, branch, ref


def _check_committer_identity(vault: Path) -> None:
    """M1: a commit needs a committer identity; surface an exit-2 hint rather than a raw git error."""
    name = _git(["config", "user.name"], vault, check=False).stdout.strip()
    email = _git(["config", "user.email"], vault, check=False).stdout.strip()
    if not name or not email:
        raise SyncUsageError(
            "no git committer identity is configured — set `git config user.name` and `git config "
            "user.email` (globally, or in the vault repo) before `sync push`.")


def _remote_tracking_sha(vault: Path, remote: str, branch: str) -> str | None:
    r = _git(["rev-parse", "--verify", "--quiet", f"{remote}/{branch}"], vault, check=False)
    return r.stdout.strip() or None


# ── push / pull ───────────────────────────────────────────────────────────────────

def sync_push(vault: Path, *, remote_arg: str | None = None, log) -> dict:
    """Ensure sync hygiene -> ``git add -A`` -> commit (skip cleanly when nothing staged) -> push.
    Logs an auditable summary (commit sha, ref old->new, file count) + the m3 unredacted-transmission
    note."""
    vault = Path(vault)
    _require_git_tree(vault)
    remote = _resolve_remote(vault, remote_arg)
    branch = _current_branch(vault)
    ensure_sync_gitattributes(vault, log=log)
    ensure_sync_gitignore(vault, log=log)
    _git(["add", "-A"], vault)

    commit_sha: str | None = None
    files = 0
    if _git(["diff", "--cached", "--quiet"], vault, check=False).returncode != 0:  # something staged
        _check_committer_identity(vault)
        files = len([ln for ln in _git(["diff", "--cached", "--name-only"], vault).stdout.splitlines()
                     if ln.strip()])
        msg = f"vault sync: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        _git(["commit", "-m", msg], vault)  # identity pre-checked; other failures -> SyncFailure
        commit_sha = _git(["rev-parse", "HEAD"], vault).stdout.strip()

    if _git(["rev-parse", "--verify", "--quiet", "HEAD"], vault, check=False).returncode != 0:
        raise SyncUsageError("the vault repo has no commits and nothing to stage — nothing to push.")

    log("note: `sync push` transmits the ENTIRE vault (gate-log, candidates, lessons-learned, "
        "slices, decisions) UNREDACTED — push only to a PRIVATE remote (a secret committed once "
        "persists in the pushed history).")
    has_upstream = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                        vault, check=False).returncode == 0
    old = _remote_tracking_sha(vault, remote, branch)
    push_args = ["push", remote, branch] if has_upstream else ["push", "-u", remote, branch]
    pr = _git(push_args, vault, check=False)
    if pr.returncode != 0:
        raise SyncFailure(
            f"git push to {remote}/{branch} FAILED (rc={pr.returncode}): {pr.stderr.strip()} "
            "— missing remote / auth / network / rejected push (fail-visible).")
    new = _remote_tracking_sha(vault, remote, branch)
    log(f"pushed to {remote}/{branch}: ref {_short(old)} -> {_short(new)}"
        + (f", commit {commit_sha[:9]}, {files} file(s)" if commit_sha else " (nothing new to commit)"))
    return {"action": "pushed", "remote": remote, "branch": branch, "commit": commit_sha,
            "files": files, "old_ref": old, "new_ref": new}


def sync_pull(vault: Path, *, remote_arg: str | None = None, force: bool = False,
              force_drop_local: bool = False, log) -> dict:
    """fetch -> fast-forward-only by default (REFUSE dirty tree OR divergent history) -> invalidate
    the derived cache. ``--force`` mirror-resets to the upstream, but still refuses to silently drop
    unpushed local COMMITS (M-add-1) unless ``--force-drop-local``."""
    vault = Path(vault)
    _require_git_tree(vault)
    remote, branch, upstream = _resolve_upstream(vault)
    if remote_arg and remote_arg != remote:
        raise SyncUsageError(
            f"--remote {remote_arg!r} does not match the branch's configured upstream remote "
            f"{remote!r}; the pull target is the upstream {upstream!r}.")
    force = force or force_drop_local  # --force-drop-local implies --force

    # DATA-LOSS GUARD 1 — the working tree, BEFORE any ref move.
    porcelain = _git(["status", "--porcelain"], vault).stdout.strip()
    if porcelain and not force:
        raise SyncFailure(
            f"refusing to pull: {len(porcelain.splitlines())} uncommitted vault edit(s) in the "
            "working tree would be overwritten — commit or discard them, or re-run with --force "
            "(dirty-tree data-loss guard).")

    fr = _git(["fetch", remote], vault, check=False)
    if fr.returncode != 0:
        raise SyncFailure(
            f"git fetch {remote} FAILED (rc={fr.returncode}): {fr.stderr.strip()} — missing remote / "
            "auth / network (fail-visible).")

    target = upstream  # e.g. origin/main, freshly fetched
    old_head = _git(["rev-parse", "HEAD"], vault).stdout.strip()
    if force:
        # M-add-1: is local HEAD carrying commits the target lacks? (clean-tree divergent local)
        is_ancestor = _git(["merge-base", "--is-ancestor", "HEAD", target], vault,
                           check=False).returncode == 0
        if not is_ancestor:
            dropped = [s for s in _git(["rev-list", f"{target}..HEAD"], vault).stdout.split() if s]
            if dropped:
                shas = ", ".join(s[:9] for s in dropped)
                if not force_drop_local:
                    raise SyncFailure(
                        f"refusing --force reset: it would PERMANENTLY DROP {len(dropped)} local "
                        f"commit(s) not on {target} ({shas}) — committed vault rows not yet pushed. "
                        "Push them first, or re-run with --force-drop-local to discard them "
                        "(unpushed-commit data-loss guard, M-add-1).")
                log(f"WARNING: --force-drop-local is DISCARDING {len(dropped)} unpushed local "
                    f"commit(s): {shas}")
        _git(["reset", "--hard", target], vault)
        # M1 (code-review): `reset --hard` resets only TRACKED paths — an UNTRACKED local shard append
        # past the remote's timeline (a normal between-push state: a gate append writes the shard to
        # disk but only git-commits at the next push) would survive and fold into the derived view,
        # breaking the advertised "mirror-reset to the remote". `git clean -fd` completes the mirror.
        # NOT `-x`: git-IGNORED files (the derived cache + *.lock/*.tmp sidecars) are left for the M3
        # invalidation step below — cleaning them here would race the lock discipline.
        _git(["clean", "-fd"], vault)
    else:
        ff = _git(["merge", "--ff-only", target], vault, check=False)
        if ff.returncode != 0:
            raise SyncFailure(
                f"refusing to pull: local history has diverged from {target} (fast-forward not "
                "possible) — your local commits would be lost by a reset. Push them, or re-run with "
                "--force to mirror-reset to the remote (divergent-history guard).")

    new_head = _git(["rev-parse", "HEAD"], vault).stdout.strip()
    _invalidate_derived_caches(vault, log=log)  # INV-A / M3
    log(f"pulled {target}: HEAD {_short(old_head)} -> {_short(new_head)}")
    return {"action": "pulled", "remote": remote, "branch": branch, "target": target,
            "old_head": old_head, "new_head": new_head}
