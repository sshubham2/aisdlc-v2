"""slice-092 / SC-201 / ADR-114 — git-native vault sync verb (`vault_admin sync push|pull`).

test_first tests for the sync verb + the `_vault_git_sync` engine. Every AC is driven through the
SHIPPED `vault_admin sync push|pull` CLI (or the real engine functions) — NEVER a copy of the design
spike's `design_pull()` (Critic m2). The Critic-accepted findings each get a dedicated row:
  * M1  — remote resolution (ambiguity), branch/upstream resolution (detached / no-upstream),
          committer-identity hint
  * M2  — the subprocess env forces SSH BatchMode (fast-fail, no passphrase/host-key hang)
  * M3  — a failed delete of a PRESENT derived cache after a ref-advance is fail-VISIBLE (never a
          silent stale-serve); a missing cache (fresh clone) is a benign no-op
  * m1  — a `.gitattributes` (`* -text`) is pinned into the sync-set for byte-faithfulness
  * m3  — `sync push` announces that it transmits the WHOLE vault unredacted (choose a PRIVATE remote)
  * M-add-1 — `--force` refuses to silently drop unpushed local COMMITS past the working-tree dirty
              guard; `--force-drop-local` discards them but PRINTS them first
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# conftest.py (tests/) already puts the plugin root on sys.path; mirror it for isolated runs.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store  # noqa: E402 — after the sys.path bootstrap

VAULT_ADMIN = "scripts/lib/vault_admin.py"


# ── git + vault fixtures ──────────────────────────────────────────────────────────

def _git(args, cwd, *, env=None, check=True):
    """Run git in ``cwd`` (test-side setup only — the engine has its own runner)."""
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=e)
    if check:
        assert r.returncode == 0, f"git {args} failed in {cwd}: {r.stderr}"
    return r


def _branch(vault) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], vault).stdout.strip()


def _init_bare(tmp_path, name="remote.git") -> Path:
    bare = tmp_path / name
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    return bare


def _init_repo(vault, *, identity=True):
    _git(["init", "-q"], vault)
    if identity:
        _git(["config", "user.email", "test@example.com"], vault)
        _git(["config", "user.name", "Test User"], vault)


def _make_sharded_vault(path: Path, n: int) -> Path:
    """A faithfully-SHARDED vault (the real migrate path): a flat gate-log.json exploded into the
    per-entry shard log + a derived cache, WITH migrate's `.gitignore` lines (/gate-log.json +
    gate-log/*.lock|*.tmp). Also drops the ambient cruft a live vault carries (root *.lock sidecars,
    the .source-repo back-ref, a whole-file candidates.json artifact)."""
    path.mkdir(parents=True, exist_ok=True)
    entries = [{"gate": "g", "slice": f"s{i:03d}", "verdict": "clean", "n": i} for i in range(n)]
    (path / "gate-log.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    _shard_store.migrate(path, "gate-log.json", "entries")  # shards + cache + .gitignore lines
    # ambient cruft that MUST NOT sync (AC3/AC4)
    (path / "gate-log.json.lock").write_text("", encoding="utf-8")
    (path / "candidates.json.lock").write_text("", encoding="utf-8")
    (path / ".source-repo").write_text("/some/source/repo\n", encoding="utf-8")
    # a whole-file artifact that MUST sync
    (path / "candidates.json").write_text(
        json.dumps({"counters": {"sc": 1}, "candidates": []}), encoding="utf-8")
    return path


def _make_flat_vault(path: Path, n: int) -> Path:
    """A NON-sharded (un-migrated) vault — the fresh-install default: gate-log.json is the FLAT source
    of truth (no shard dir, no migrate, so it is a tracked/synced file, not a derived cache)."""
    path.mkdir(parents=True, exist_ok=True)
    entries = [{"gate": "g", "slice": f"s{i:03d}", "n": i} for i in range(n)]
    (path / "gate-log.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")
    (path / "candidates.json").write_text(
        json.dumps({"counters": {"sc": 1}, "candidates": []}), encoding="utf-8")
    return path


def _seed_more(vault, n, start_msg="more"):
    """Append n more gate rows through the real shard store (a divergence source for pull tests)."""
    for i in range(n):
        _shard_store.append_entry(vault, "gate-log.json", "entries",
                                  {"gate": "g", "slice": f"{start_msg}{i:03d}", "verdict": "clean"})


def _run(run_script, *args, env=None):
    return run_script(VAULT_ADMIN, list(args), env=env)


# ── AC1 — push advances the bare remote (walking-skeleton layer: push) ─────────────

def test_push_advances_bare_remote(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 5)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)

    r = _run(run_script, "sync", "push", "--vault", str(vault))
    assert r.returncode == 0, f"push failed: {r.stderr}\n{r.stdout}"

    br = _branch(vault)
    remote_ref = _git(["ls-remote", str(bare), br], vault).stdout.strip()
    assert remote_ref, "the remote ref must advance after push"
    # auditable summary (must-not-defer #3): commit sha + file count surfaced
    assert "sync" in (r.stdout + r.stderr).lower()


# ── AC2 transport — a clone from the bare remote (walking-skeleton layer: transport) +
#     AC3 exclusions (the tracked tree reveals what synced) ──────────────────────────

def test_clone_from_bare_remote(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 4)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0

    clone = tmp_path / "clone"
    _git(["clone", "-q", str(bare), str(clone)], tmp_path)
    tracked = _git(["ls-files"], clone).stdout.split()

    # AC3: none of these are in the pushed tree
    assert "gate-log.json" not in tracked, "the derived cache must not sync"
    assert not any(f.endswith(".lock") for f in tracked), f"no *.lock may sync: {tracked}"
    assert not any(f.endswith(".tmp") for f in tracked), f"no *.tmp may sync: {tracked}"
    assert ".source-repo" not in tracked, "the node-local back-ref must not sync"
    assert not any(f.startswith(".git/") for f in tracked), ".git/ is structurally excluded"
    # the shard log DID sync (reconstruction source)
    assert any(f.startswith("gate-log/") and f.endswith(".json") for f in tracked), tracked


# ── AC2 core — a FRESH clone reconstructs the full vault via derive-on-missing
#     (walking-skeleton layer: reconstruct) ──────────────────────────────────────────

def test_fresh_clone_reconstructs_vault(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 6)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0

    clone = tmp_path / "clone"
    _git(["clone", "-q", str(bare), str(clone)], tmp_path)
    assert not (clone / "gate-log.json").exists(), "the derived cache is absent on a fresh clone"

    # derive-on-missing THROUGH the shipped read-entries verb (no re-implementation)
    r = _run(run_script, "read-entries", "--vault", str(clone))
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert len(rows) == 6, f"all 6 rows reconstruct from shards alone: got {len(rows)}"
    # meta + whole-file artifacts survived
    assert (clone / "gate-log" / "_meta.json").exists()
    assert (clone / "candidates.json").exists()
    shard_files = [p.name for p in (clone / "gate-log").glob("*.json") if p.name != "_meta.json"]
    assert len(shard_files) == 6, f"shard count matches source: {shard_files}"


# ── AC2 pull — pull advances the local vault to the remote (walking-skeleton layer:
#     pull + reset local) ──────────────────────────────────────────────────────────

def test_pull_resets_local_to_remote(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    # A = primary: push 5
    A = _make_sharded_vault(tmp_path / "A", 5)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0

    # B = a second clone: append 3, push (remote now 8, A is behind)
    B = tmp_path / "B"
    _git(["clone", "-q", str(bare), str(B)], tmp_path)
    _git(["config", "user.email", "b@example.com"], B)
    _git(["config", "user.name", "B User"], B)
    _seed_more(B, 3, "B")
    assert _run(run_script, "sync", "push", "--vault", str(B)).returncode == 0

    # A is clean + strictly behind -> ff-only pull advances it; the stale 5-row cache is invalidated
    r = _run(run_script, "sync", "pull", "--vault", str(A))
    assert r.returncode == 0, f"clean ff pull must succeed: {r.stderr}"
    rows = json.loads(_run(run_script, "read-entries", "--vault", str(A)).stdout)
    assert len(rows) == 8, f"A reconstructs 8 rows after pull (no stale-serve): got {len(rows)}"


def test_force_pull_mirror_resets_clean_behind(tmp_path, run_script):
    """`--force` on a clean, strictly-behind local mirror-resets to the remote (no local commits to
    drop) — the AC5 explicit-override path."""
    bare = _init_bare(tmp_path)
    A = _make_sharded_vault(tmp_path / "A", 5)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0
    B = tmp_path / "B"
    _git(["clone", "-q", str(bare), str(B)], tmp_path)
    _git(["config", "user.email", "b@example.com"], B)
    _git(["config", "user.name", "B User"], B)
    _seed_more(B, 2, "B")
    assert _run(run_script, "sync", "push", "--vault", str(B)).returncode == 0

    r = _run(run_script, "sync", "pull", "--vault", str(A), "--force")
    assert r.returncode == 0, r.stderr
    rows = json.loads(_run(run_script, "read-entries", "--vault", str(A)).stdout)
    assert len(rows) == 7


# ── AC3 — the sync-set excludes .git/, the derived cache, and *.lock/*.tmp ──────────

def test_ac3_syncset_excludes_cache_and_cruft(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    # add a *.tmp too
    (vault / "gate-log" / "scratch.tmp").write_text("x", encoding="utf-8")
    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0

    tracked = _git(["ls-files"], vault).stdout.split()
    assert "gate-log.json" not in tracked
    assert not any(f.endswith((".lock", ".tmp")) for f in tracked), tracked
    assert not any(f.startswith(".git/") for f in tracked)


# ── AC4 — a previously-tracked *.lock sidecar becomes untracked ────────────────────

def test_ac4_lock_sidecar_untracked(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    # simulate the wart: a lock sidecar already committed (force past any ignore)
    _git(["add", "-f", "gate-log.json.lock", "candidates.json.lock"], vault)
    _git(["commit", "-q", "-m", "legacy: lock sidecars tracked"], vault)
    assert "gate-log.json.lock" in _git(["ls-files"], vault).stdout

    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0
    tracked = _git(["ls-files"], vault).stdout.split()
    assert not any(f.endswith(".lock") for f in tracked), f"AC4: no lock may remain tracked: {tracked}"


# ── AC5 — pull data-loss guards + fail-visible missing remote ──────────────────────

def test_ac5_pull_refuses_dirty_worktree(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    A = _make_sharded_vault(tmp_path / "A", 5)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0
    B = tmp_path / "B"
    _git(["clone", "-q", str(bare), str(B)], tmp_path)
    _git(["config", "user.email", "b@example.com"], B)
    _git(["config", "user.name", "B User"], B)
    _seed_more(B, 2, "B")
    assert _run(run_script, "sync", "push", "--vault", str(B)).returncode == 0

    # uncommitted edit to a TRACKED artifact
    (A / "candidates.json").write_text(
        json.dumps({"counters": {"sc": 2}, "candidates": [{"id": "SC-DIRTY"}]}), encoding="utf-8")
    r = _run(run_script, "sync", "pull", "--vault", str(A))
    assert r.returncode == 3, f"dirty pull must exit 3: {r.stdout}\n{r.stderr}"
    assert "dirty" in r.stderr.lower() or "uncommitted" in r.stderr.lower()


def test_ac5_missing_remote_fails_visible(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    A = _make_sharded_vault(tmp_path / "A", 4)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0
    # repoint origin at a nonexistent path (upstream config survives)
    _git(["remote", "set-url", "origin", str(tmp_path / "does-not-exist.git")], A)

    r = _run(run_script, "sync", "pull", "--vault", str(A))
    assert r.returncode == 3, f"missing remote must exit 3: {r.stdout}\n{r.stderr}"
    assert r.stderr.strip(), "a visible error is required"


# ── M1 — remote / branch / upstream / identity resolution ──────────────────────────

def test_m1_ambiguous_remote_refuses(tmp_path, run_script):
    bare1 = _init_bare(tmp_path, "r1.git")
    bare2 = _init_bare(tmp_path, "r2.git")
    vault = _make_sharded_vault(tmp_path / "vault", 2)
    _init_repo(vault)
    _git(["remote", "add", "alpha", str(bare1)], vault)
    _git(["remote", "add", "beta", str(bare2)], vault)

    r = _run(run_script, "sync", "push", "--vault", str(vault))
    assert r.returncode == 2, f"ambiguous remote must exit 2 (usage): {r.stdout}\n{r.stderr}"
    assert "remote" in r.stderr.lower()
    # explicit --remote disambiguates
    assert _run(run_script, "sync", "push", "--vault", str(vault), "--remote", "alpha").returncode == 0


def test_m1_detached_head_refuses(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 2)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    _git(["add", "-A"], vault)
    _git(["commit", "-q", "-m", "seed"], vault)
    _git(["checkout", "-q", "--detach", "HEAD"], vault)

    r = _run(run_script, "sync", "push", "--vault", str(vault))
    assert r.returncode == 2, f"detached HEAD must exit 2: {r.stdout}\n{r.stderr}"
    assert "detach" in r.stderr.lower()


def test_m1_no_upstream_pull_refuses(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    _git(["add", "-A"], vault)
    _git(["commit", "-q", "-m", "seed"], vault)  # committed but NEVER push -u -> no upstream

    r = _run(run_script, "sync", "pull", "--vault", str(vault))
    assert r.returncode == 2, f"no-upstream pull must exit 2: {r.stdout}\n{r.stderr}"
    assert "upstream" in r.stderr.lower()


def test_m1_missing_committer_identity_hint(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 2)
    _init_repo(vault, identity=False)  # no local user.name/email
    _git(["remote", "add", "origin", str(bare)], vault)
    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    env = {"GIT_CONFIG_GLOBAL": str(empty_cfg), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "", "GIT_AUTHOR_EMAIL": "",
           "GIT_COMMITTER_NAME": "", "GIT_COMMITTER_EMAIL": ""}

    r = _run(run_script, "sync", "push", "--vault", str(vault), env=env)
    assert r.returncode == 2, f"missing identity must exit 2: {r.stdout}\n{r.stderr}"
    assert "identity" in r.stderr.lower() or "user.name" in r.stderr.lower()


# ── M2 — SSH BatchMode fast-fail (no passphrase/host-key hang) ─────────────────────

def test_m2_sync_env_forces_ssh_batchmode(monkeypatch):
    import scripts.lib._vault_git_sync as vgs
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    env = vgs._sync_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "ConnectTimeout=" in env["GIT_SSH_COMMAND"]
    # a deliberate user GIT_SSH_COMMAND override is respected (they own their transport)
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /my/key")
    assert vgs._sync_env()["GIT_SSH_COMMAND"] == "ssh -i /my/key"


def test_m2_ssh_missing_remote_fast_fail(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    A = _make_sharded_vault(tmp_path / "A", 3)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0
    # an uncredentialed ssh-style remote at a refusing port -> must fail fast + visible, never hang
    _git(["remote", "set-url", "origin", "ssh://git@localhost:1/nope.git"], A)

    t0 = time.monotonic()
    r = _run(run_script, "sync", "pull", "--vault", str(A))
    dt = time.monotonic() - t0
    assert r.returncode == 3, f"ssh missing remote must exit 3: {r.stdout}\n{r.stderr}"
    assert dt < 40, f"must fail fast (BatchMode), not hang: took {dt:.1f}s"
    assert r.stderr.strip()


# ── M3 — fail-visible derived-cache invalidation ───────────────────────────────────

def test_m3_cache_invalidation_failure_is_visible(tmp_path, monkeypatch):
    import scripts.lib._vault_git_sync as vgs
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    assert (vault / "gate-log.json").exists(), "precondition: a present derived cache"
    monkeypatch.setattr(vgs, "_EPERM_RETRIES", 2)
    monkeypatch.setattr(vgs, "_EPERM_BACKOFF_BASE", 0.0)
    orig_unlink = Path.unlink

    def boom(self, *a, **k):
        if self.name == "gate-log.json":
            raise PermissionError("held handle (simulated)")
        return orig_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom)
    with pytest.raises(vgs.SyncFailure):
        vgs._invalidate_derived_caches(vault, log=lambda m: None)


def test_m3_missing_cache_is_benign_noop(tmp_path):
    import scripts.lib._vault_git_sync as vgs
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    (vault / "gate-log.json").unlink()  # fresh-clone state: no local cache
    vgs._invalidate_derived_caches(vault, log=lambda m: None)  # must NOT raise


# ── M-add-1 — --force must not silently drop unpushed local COMMITS ─────────────────

def _diverge_local_commit(tmp_path, run_script):
    """Return (vault, bare, dropped_sha): a vault that is clean but carries ONE unpushed local
    commit not on the remote (so a reset --hard would drop it)."""
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 4)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0  # remote = 4, upstream set
    # a local, committed, UNPUSHED gate append (working tree stays clean)
    _seed_more(vault, 1, "local")
    _git(["add", "-A"], vault)
    _git(["commit", "-q", "-m", "local unpushed row"], vault)
    dropped = _git(["rev-parse", "HEAD"], vault).stdout.strip()
    return vault, bare, dropped


def test_madd1_force_refuses_divergent_local(tmp_path, run_script):
    vault, _bare, dropped = _diverge_local_commit(tmp_path, run_script)
    head_before = _git(["rev-parse", "HEAD"], vault).stdout.strip()

    r = _run(run_script, "sync", "pull", "--vault", str(vault), "--force")
    assert r.returncode == 3, f"--force must refuse to drop unpushed commits: {r.stdout}\n{r.stderr}"
    out = (r.stdout + r.stderr).lower()
    assert "commit" in out and ("drop" in out or "force-drop-local" in out)
    assert dropped[:9] in (r.stdout + r.stderr), "the to-be-dropped sha must be enumerated"
    assert _git(["rev-parse", "HEAD"], vault).stdout.strip() == head_before, "HEAD must be preserved"


def test_madd1_force_drop_local_discards_visibly(tmp_path, run_script):
    vault, _bare, dropped = _diverge_local_commit(tmp_path, run_script)

    r = _run(run_script, "sync", "pull", "--vault", str(vault), "--force-drop-local")
    assert r.returncode == 0, f"--force-drop-local must proceed: {r.stdout}\n{r.stderr}"
    out = (r.stdout + r.stderr).lower()
    assert "discard" in out or "drop" in out, "the loss must be PRINTED, never silent"
    assert dropped[:9] in (r.stdout + r.stderr)
    rows = json.loads(_run(run_script, "read-entries", "--vault", str(vault)).stdout)
    assert len(rows) == 4, "local reset to the remote's 4 rows"


# ── m1 — a .gitattributes is pinned for byte-faithfulness ──────────────────────────

def test_m1_gitattributes_pinned_byte_faithful(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 3)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    assert _run(run_script, "sync", "push", "--vault", str(vault)).returncode == 0

    ga = vault / ".gitattributes"
    assert ga.exists(), ".gitattributes must be created"
    assert "-text" in ga.read_text(encoding="utf-8")
    assert ".gitattributes" in _git(["ls-files"], vault).stdout, "it must be part of the sync-set"


# ── m3 — push announces the whole-vault-unredacted transmission ────────────────────

def test_m3_push_announces_unredacted(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    vault = _make_sharded_vault(tmp_path / "vault", 2)
    _init_repo(vault)
    _git(["remote", "add", "origin", str(bare)], vault)
    r = _run(run_script, "sync", "push", "--vault", str(vault))
    assert r.returncode == 0
    out = (r.stdout + r.stderr).lower()
    assert "unredacted" in out or "entire vault" in out or "private" in out


# ── B1 (code-review) — a NON-sharded vault's flat truth file survives pull ─────────

def test_b1_flat_vault_truth_file_preserved_on_pull(tmp_path, run_script):
    """On a non-sharded vault, gate-log.json is the FLAT SOURCE OF TRUTH (tracked + synced) — pull
    must NOT delete it as if it were a derived cache (that was a silent data loss)."""
    bare = _init_bare(tmp_path)
    A = _make_flat_vault(tmp_path / "A", 5)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0
    # a flat vault has no /gate-log.json ignore line -> the truth file IS tracked
    assert "gate-log.json" in _git(["ls-files"], A).stdout

    B = tmp_path / "B"
    _git(["clone", "-q", str(bare), str(B)], tmp_path)
    _git(["config", "user.email", "b@example.com"], B)
    _git(["config", "user.name", "B User"], B)
    # B grows the flat truth to 6 rows, commits, pushes (A is now behind)
    entries6 = [{"gate": "g", "slice": f"s{i:03d}", "n": i} for i in range(6)]
    (B / "gate-log.json").write_text(json.dumps({"entries": entries6}), encoding="utf-8")
    _git(["add", "-A"], B)
    _git(["commit", "-q", "-m", "B grows the flat log"], B)
    assert _run(run_script, "sync", "push", "--vault", str(B)).returncode == 0

    r = _run(run_script, "sync", "pull", "--vault", str(A))
    assert r.returncode == 0, f"clean ff pull must succeed: {r.stderr}"
    assert (A / "gate-log.json").exists(), "B1: the flat truth file must survive the pull"
    assert "D  gate-log.json" not in _git(["status", "--porcelain"], A).stdout
    rows = json.loads((A / "gate-log.json").read_text(encoding="utf-8"))["entries"]
    assert len(rows) == 6, f"the flat truth reflects the pulled 6 rows: {len(rows)}"


# ── M1 (code-review) — --force removes untracked local shards (true mirror-reset) ──

def test_m1cr_force_pull_removes_untracked_local_shards(tmp_path, run_script):
    bare = _init_bare(tmp_path)
    A = _make_sharded_vault(tmp_path / "A", 4)
    _init_repo(A)
    _git(["remote", "add", "origin", str(bare)], A)
    assert _run(run_script, "sync", "push", "--vault", str(A)).returncode == 0  # remote truth = 4

    # an UNCOMMITTED local shard append past the remote timeline (the normal between-push state)
    _seed_more(A, 1, "local")
    assert (A / "gate-log" / "000004.json").exists()
    assert "?? gate-log/000004.json" in _git(["status", "--porcelain"], A).stdout

    r = _run(run_script, "sync", "pull", "--vault", str(A), "--force")
    assert r.returncode == 0, f"--force mirror-reset must succeed: {r.stderr}"
    assert not (A / "gate-log" / "000004.json").exists(), \
        "M1: the untracked local shard must be removed by the mirror-reset"
    rows = json.loads(_run(run_script, "read-entries", "--vault", str(A)).stdout)
    assert len(rows) == 4, f"the vault mirrors the remote's exactly-4 rows: {len(rows)}"
