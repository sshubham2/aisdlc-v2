"""Shared-vault-safe claim coordination (slice-091 / SC-198 / ADR-113).

Makes candidate CLAIMING safe on a shared vault: an OPT-IN, shared-remote-only coordination
primitive so the SECOND concurrent claim is REFUSED, never a silent duplicate. THIN scope (ADR-112/113):
the seam + single-key create-if-absent + the reference ``LocalDirClaimBackend`` ship here. The GIT
production backend landed in SC-216 / slice-100 (see ``tests/test_claim_coord_git.py``); the S3/MinIO
production backend is still unbuilt.

Load-bearing (B1 / ADR-113): the reference backend's create-if-absent is a GENUINE atomic no-clobber
``os.link`` publish (NOT ``_shard_store._write_exclusive``, which slice-090/ADR-109 stripped of O_EXCL and
whose atomicity comes only from the caller's gate-log lock). The AC1/AC2 proof RACES TWO barrier-synced
CONCURRENT PROCESSES -- never a sequential A-then-B proxy, which passes even on a broken check-then-write
(the laundered green that BLOCKED rounds 1-2). ``test_naive_check_then_write_double_picks`` is the
negative control that proves the barrier genuinely interleaves and would CATCH the B1 regression.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]  # the worktree root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_CLAIM = _REPO / "skills" / "slice" / "scripts" / "claim_candidate.py"

A = ("Owner A", "a@test")
B = ("Owner B", "b@test")


# ── real barrier-synced two-process race worker (the REAL LocalDirClaimBackend) ─────
# A file-barrier synchronises both processes to within microseconds of the create call, so a
# broken check-then-write primitive would double-CREATE (proven by the negative control below),
# while the genuine os.link publish yields EXACTLY one CREATED. __REPO__ is .replace-d (never
# .format-d) so the Windows backslash path stays literal inside the generated r"..." string.
_RACE_WORKER = r'''
import sys, os, glob, time, json
sys.path.insert(0, r"__REPO__")
from pathlib import Path
from scripts.lib import _claim_coord

vault, candidate, token, barrier_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
backend = _claim_coord.LocalDirClaimBackend(Path(vault))
Path(barrier_dir, "ready_" + token).write_text("1", encoding="utf-8")
deadline = time.monotonic() + 30
while len(glob.glob(os.path.join(barrier_dir, "ready_*"))) < n:  # tight spin -- no sleep
    if time.monotonic() > deadline:
        print("TIMEOUT " + token); sys.exit(3)
body = {"candidate": candidate, "actor": {"git_user": token, "git_email": token + "@t"},
        "idempotency_token": token, "at": "2026-01-01T00:00:00Z"}
res = backend.create_if_absent(_claim_coord.claim_key(candidate), body)
print(res.status + " " + token)
'''

# Negative control: a NAIVE check-then-write (the exact B1 bug) raced by the SAME harness. A 20ms
# sleep after the (both-False) existence check widens the TOCTOU window so the barrier-synced pair
# DETERMINISTICALLY both write -> two CREATEDs. This proves the harness genuinely interleaves; a real
# os.link create closes that window entirely.
_NAIVE_WORKER = r'''
import sys, os, glob, time, json
from pathlib import Path
vault, candidate, token, barrier_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
target = Path(vault) / "claims" / candidate / "HELD.json"
target.parent.mkdir(parents=True, exist_ok=True)
Path(barrier_dir, "ready_" + token).write_text("1", encoding="utf-8")
deadline = time.monotonic() + 30
while len(glob.glob(os.path.join(barrier_dir, "ready_*"))) < n:
    if time.monotonic() > deadline:
        print("TIMEOUT " + token); sys.exit(3)
existed = target.exists()          # check
time.sleep(0.02)                   # widen the TOCTOU window (the B1 defect)
if not existed:
    target.write_text(json.dumps({"idempotency_token": token}), encoding="utf-8")  # then-write (clobbers)
    print("CREATED " + token)
else:
    print("EXISTS " + token)
'''


def _run_race(worker: str, vault: Path, tmp_path: Path, rounds: int) -> list[list[str]]:
    """Race two barrier-synced processes over `rounds` fresh candidates; return per-round statuses."""
    code = worker.replace("__REPO__", str(_REPO))
    out_rounds: list[list[str]] = []
    for r in range(rounds):
        cand = f"SC-{r:03d}"
        bdir = tmp_path / f"barrier_{r}"; bdir.mkdir()
        procs = [subprocess.Popen(
            [sys.executable, "-c", code, str(vault), cand, tok, str(bdir), "2"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for tok in ("A", "B")]
        outs = [p.communicate(timeout=60) for p in procs]
        for (out, err), p in zip(outs, procs):
            assert p.returncode == 0, f"round {r} worker failed (rc={p.returncode}): {err or out}"
        out_rounds.append([o.strip().split()[0] for o, _ in outs])
    return out_rounds


# ── AC1 / AC2: exactly one winner under true concurrency (B1) ───────────────────────

def test_ac1_ac2_concurrent_double_create_exactly_one_winner(tmp_path):
    """AC1/AC2 (B1): two barrier-synced CONCURRENT processes create the same key -> exactly ONE
    CREATED (winner), one EXISTS (loser); the on-disk HELD holds the winner's token (never a silent
    last-write-wins clobber). Raced over many rounds -- never a sequential A-then-B proxy."""
    vault = tmp_path / "vault"; vault.mkdir()
    rounds = 12
    statuses = _run_race(_RACE_WORKER, vault, tmp_path, rounds)
    for r, st in enumerate(statuses):
        assert sorted(st) == ["CREATED", "EXISTS"], f"round {r}: expected one winner, got {st}"
    # AC2: no silent clobber -- the HELD body is the winner's, intact.
    for r in range(rounds):
        held = json.loads((vault / "claims" / f"SC-{r:03d}" / "HELD.json").read_text(encoding="utf-8"))
        assert held["idempotency_token"] in ("A", "B"), f"round {r}: HELD body torn/absent: {held}"


def test_naive_check_then_write_double_picks(tmp_path):
    """Negative control (proves the harness has teeth / B1): the same barrier raced against a NAIVE
    check-then-write (the exact defect ADR-113 forbids) DOES double-CREATE -- so the AC1/AC2 test
    above is a genuine concurrency proof, not a sequential proxy that green-passes a broken impl."""
    vault = tmp_path / "vault"; vault.mkdir()
    statuses = _run_race(_NAIVE_WORKER, vault, tmp_path, rounds=4)
    doubles = [st for st in statuses if st == ["CREATED", "CREATED"]]
    assert doubles, ("the naive check-then-write must double-pick under the barrier (else the AC1/AC2 "
                     f"harness proves nothing); got per-round statuses {statuses}")


# ── backend-level unit contract (create_if_absent / get / remove_if_owner) ──────────

def _backend(vault: Path):
    from scripts.lib import _claim_coord
    return _claim_coord, _claim_coord.LocalDirClaimBackend(vault)


def _body(cand, actor_email, token):
    return {"candidate": cand, "actor": {"git_user": actor_email, "git_email": actor_email},
            "idempotency_token": token, "at": "2026-01-01T00:00:00Z"}


def test_create_then_second_exists_no_overwrite(tmp_path):
    """AC1/AC2: a sequential second create returns EXISTS with the WINNER's body (never overwrites)."""
    cc, be = _backend(tmp_path)
    key = cc.claim_key("SC-1")
    r1 = be.create_if_absent(key, _body("SC-1", "a@t", "tokA"))
    assert r1.status == cc.CREATED
    r2 = be.create_if_absent(key, _body("SC-1", "b@t", "tokB"))
    assert r2.status == cc.EXISTS
    assert r2.body["idempotency_token"] == "tokA", "the loser must read back the winner's body"
    assert be.get(key)["idempotency_token"] == "tokA", "no silent last-write-wins overwrite"


def test_remove_if_owner_compare_and_delete(tmp_path):
    """M-add-1: remove_if_owner is a COMPARE-AND-DELETE -- a wrong owner is a NO-OP refusal (the HELD
    survives, so a stale --release cannot clobber a subsequent claimant); the right owner removes it."""
    cc, be = _backend(tmp_path)
    key = cc.claim_key("SC-1")
    be.create_if_absent(key, _body("SC-1", "a@t", "tokA"))
    assert be.remove_if_owner(key, "b@t") is False, "a foreign owner must NOT delete the HELD"
    assert be.get(key) is not None, "the HELD must survive a foreign remove"
    assert be.remove_if_owner(key, "a@t") is True, "the true owner removes the HELD"
    assert be.get(key) is None
    # re-claim after removal succeeds (no orphan lockout).
    assert be.create_if_absent(key, _body("SC-1", "a@t", "tokA2")).status == cc.CREATED


def test_get_absent_is_none(tmp_path):
    cc, be = _backend(tmp_path)
    assert be.get(cc.claim_key("SC-404")) is None


# ── coordination_backend resolution (opt-in, memoized, fail-closed) ─────────────────

def test_unconfigured_returns_none(tmp_path, monkeypatch):
    """AC4: no env + no config file -> None (unconfigured -> today's local route)."""
    from scripts.lib import _claim_coord, _vault_paths
    monkeypatch.delenv("AI_SDLC_CLAIM_BACKEND", raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR", None, raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR_DONE", True, raising=False)  # memo = "not a git tree"
    assert _claim_coord.coordination_backend(tmp_path) is None


def test_ac3_memoized_no_extra_subprocess(tmp_path, monkeypatch):
    """AC3 (M1, STRUCTURAL): the coordination probe adds ZERO git subprocess -- git_common_dir() is
    memoized, so repeated coordination_backend() calls on the unconfigured path never re-spawn. Pinned
    structurally (a call-counter), NOT by a flaky timing measurement."""
    from scripts.lib import _claim_coord, _vault_paths
    monkeypatch.delenv("AI_SDLC_CLAIM_BACKEND", raising=False)
    calls = {"n": 0}

    def counting_git_common_dir():
        calls["n"] += 1
        return None  # simulate "not a git work tree" -> unconfigured

    monkeypatch.setattr(_vault_paths, "_git_common_dir", counting_git_common_dir)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR", None, raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR_DONE", False, raising=False)
    _vault_paths.git_common_dir()          # warm once (what VAULT_ROOT resolution already does)
    assert calls["n"] == 1
    for _ in range(5):
        assert _claim_coord.coordination_backend(tmp_path) is None
    assert calls["n"] == 1, "coordination_backend must add NO git subprocess (memoized common-dir)"


def test_configured_via_env(tmp_path, monkeypatch):
    """AC4/m2: AI_SDLC_CLAIM_BACKEND=local -> a LocalDirClaimBackend (opt-in active)."""
    from scripts.lib import _claim_coord
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "local")
    be = _claim_coord.coordination_backend(tmp_path)
    assert isinstance(be, _claim_coord.LocalDirClaimBackend)


def test_configured_via_common_dir_config_file(tmp_path, monkeypatch):
    """m2: the durable per-clone opt-in reads <git-common-dir>/aisdlc/claim-backend via the memoized
    git_common_dir() helper (not the vault-root pin)."""
    from scripts.lib import _claim_coord, _vault_paths
    monkeypatch.delenv("AI_SDLC_CLAIM_BACKEND", raising=False)
    common = tmp_path / "common"; (common / "aisdlc").mkdir(parents=True)
    (common / "aisdlc" / "claim-backend").write_text("local\n", encoding="utf-8")
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR", str(common), raising=False)
    monkeypatch.setattr(_vault_paths, "_COMMON_DIR_DONE", True, raising=False)
    be = _claim_coord.coordination_backend(tmp_path)
    assert isinstance(be, _claim_coord.LocalDirClaimBackend)


@pytest.mark.parametrize("unbuilt", ["s3", "minio"])
def test_unsupported_backend_name_fails_closed(tmp_path, monkeypatch, unbuilt):
    """AC4: a backend configured but NOT built must FAIL CLOSED (UnsupportedBackend), never silently
    fall back to a local-only claim that could double-pick. Re-pointed from `git` to `s3`/`minio` by
    slice-100 -- `git` now RESOLVES (to `_claim_git.GitClaimBackend`), so leaving this row on `git`
    would ship red the moment the production backend landed."""
    from scripts.lib import _claim_coord
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", unbuilt)
    with pytest.raises(_claim_coord.UnsupportedBackend):
        _claim_coord.coordination_backend(tmp_path)


def test_local_fs_precondition_documented():
    """m5: the reference backend documents the local-FS-only precondition (O_EXCL/os.link is
    unreliable over NFS/SMB; a REMOTE backend uses a dedicated git ref / If-None-Match instead)."""
    from scripts.lib import _claim_coord
    doc = (_claim_coord.__doc__ or "") + (_claim_coord.LocalDirClaimBackend.__doc__ or "")
    assert "NFS" in doc or "SMB" in doc, "the local-FS precondition (m5) must be documented"


# ── claim_candidate opt-in gate: exit-code taxonomy (in-process, injected backend) ──

def _prime_vault(tmp_path, cid="SC-198"):
    vault = tmp_path / "vault"; vault.mkdir()
    doc = {"_schema": "aisdlc/slice-candidates@1", "project": "t", "pick_log": [],
           "counters": {"slice": 0, "sc": 198},
           "candidates": [{"id": cid, "title": "t", "status": "candidate", "progress": "not-started",
                           "slice": None, "claimed_by": None, "started_at": None, "history": []}]}
    (vault / "candidates.json").write_text(json.dumps(doc), encoding="utf-8")
    return vault


def _id_git_env(tmp_path, name, email):
    cfg = tmp_path / f"gc_{email}".replace("@", "_")
    cfg.write_text(f"[user]\n\tname = {name}\n\temail = {email}\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}


class _StubBackend:
    """A configured backend whose create_if_absent returns an injected result (the only shipped real
    backend -- LocalDir -- is always reachable, so AC4's UNVERIFIABLE path needs an injected stub)."""

    def __init__(self, result):
        self._result = result
        self.removed = []

    def create_if_absent(self, key, body):
        return self._result

    def get(self, key):
        return self._result.body

    def remove_if_owner(self, key, owner_email):
        self.removed.append((key, owner_email))
        return True


def _run_main_with_backend(monkeypatch, tmp_path, vault, backend, git_env, argv):
    """Call claim_candidate.main() in-process with coordination_backend injected."""
    import claim_candidate  # noqa: E402  (skills/slice/scripts on sys.path below)
    from scripts.lib import _claim_coord
    monkeypatch.setattr(_claim_coord, "coordination_backend", lambda vault_root: backend)
    for k, v in git_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AI_SDLC_VAULT_ROOT", raising=False)
    return claim_candidate.main(["--vault", str(vault), "--repo-root", str(tmp_path), *argv])


@pytest.fixture(autouse=True)
def _skill_on_path():
    sys.path.insert(0, str(_CLAIM.parent))
    yield


def test_gate_unverifiable_transient_exit_3(monkeypatch, tmp_path):
    """AC4/m1: a transient/unreachable backend -> UNVERIFIABLE(transient) -> exit 3, candidates.json
    UNCHANGED (fail-closed, never a silent local mint)."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    before = (vault / "candidates.json").read_text(encoding="utf-8")
    stub = _StubBackend(_claim_coord.ClaimResult(status=_claim_coord.UNVERIFIABLE, kind="transient"))
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, stub, _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing"])
    assert rc == 3, "transient UNVERIFIABLE must fail-closed retryable (exit 3)"
    assert (vault / "candidates.json").read_text(encoding="utf-8") == before, "no silent mint"


def test_gate_unverifiable_indeterminate_exit_4(monkeypatch, tmp_path):
    """AC4/m1: an indeterminate read-back -> UNVERIFIABLE(indeterminate) -> exit 4 (ambiguous)."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    stub = _StubBackend(_claim_coord.ClaimResult(status=_claim_coord.UNVERIFIABLE, kind="indeterminate"))
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, stub, _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing"])
    assert rc == 4, "indeterminate read-back must be ambiguous (exit 4)"


def test_gate_foreign_exists_exit_5(monkeypatch, tmp_path):
    """AC1/AC2: a foreign-owned EXISTS -> LOST -> exit 5 (slice_ownership) naming the winner."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    foreign = _claim_coord.ClaimResult(status=_claim_coord.EXISTS,
                                       body={"candidate": "SC-198",
                                             "actor": {"git_user": "Owner B", "git_email": "b@test"},
                                             "idempotency_token": "tokB"})
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, _StubBackend(foreign),
                                _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing"])
    assert rc == 5, "a foreign HELD must refuse with the ownership code (5)"


def test_gate_created_proceeds_to_mint(monkeypatch, tmp_path):
    """AC1: CREATED -> the existing in-lock slice-NNN mint runs (coordination gates ENTRY, the local
    mint stays the source of truth for the ordinal)."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    created = _claim_coord.ClaimResult(status=_claim_coord.CREATED,
                                       body=_body("SC-198", "a@test", "tokA"))
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, _StubBackend(created),
                                _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing", "--json"])
    assert rc == 0
    rec = next(c for c in json.loads((vault / "candidates.json").read_text())["candidates"]
               if c["id"] == "SC-198")
    assert rec["status"] == "spiking" and rec["slice"] == "slice-001"


def test_gate_own_token_exists_self_retry_mints(monkeypatch, tmp_path):
    """C2: an own-token EXISTS (a crash-before-mint retry by the SAME actor) -> WON -> proceed to mint.

    slice-100 / M9: the predicate is TOKEN-EXACT, so the retry must present the token PERSISTED at
    HELD-create time -- planted here as the crashed first attempt would have left it."""
    from scripts.lib import _claim_coord
    import claim_candidate
    vault = _prime_vault(tmp_path)
    sidecar = claim_candidate._token_path(vault, "SC-198", A[1])
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("tokA\n", encoding="utf-8")
    own = _claim_coord.ClaimResult(status=_claim_coord.EXISTS,
                                   body={"candidate": "SC-198",
                                         "actor": {"git_user": "Owner A", "git_email": "a@test"},
                                         "idempotency_token": "tokA"})
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, _StubBackend(own),
                                _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing"])
    assert rc == 0, "own-token EXISTS is a WON self-retry -> mint proceeds"


def test_gate_same_identity_foreign_token_exists_is_lost(monkeypatch, tmp_path):
    """slice-100 / M9 (the SAFETY hole the email-keyed predicate left open): the SAME git identity
    presenting a HELD whose token this machine did NOT mint is NOT a self-retry. It is refused as
    LOST -- otherwise a second machine mints a duplicate slice from its own un-pulled
    candidates.json, which on a solo-maintainer project is the PRIMARY configuration."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    before = (vault / "candidates.json").read_text(encoding="utf-8")
    foreign_token = _claim_coord.ClaimResult(
        status=_claim_coord.EXISTS,
        body={"candidate": "SC-198", "actor": {"git_user": "Owner A", "git_email": "a@test"},
              "idempotency_token": "minted-on-another-machine"})
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, _StubBackend(foreign_token),
                                _id_git_env(tmp_path, *A),
                                ["--candidate", "SC-198", "--name", "do-thing"])
    assert rc == 5, "a token this machine never minted must be LOST, even under the same identity"
    assert (vault / "candidates.json").read_text(encoding="utf-8") == before, "no duplicate mint"


def test_token_sidecar_is_per_owner_on_a_shared_vault(tmp_path):
    """slice-100 regression guard: the C2 token sidecar is keyed by OWNER, not just by candidate.

    On a SHARED-filesystem vault (this backend's whole scenario) both developers reach the same
    token directory. A single per-candidate name would let the SECOND developer read the FIRST's
    token and be admitted as a false self-retry -- and would clobber the first's C2 evidence on
    write, stranding the true owner on their own retry. Caught by
    ``test_e2e_configured_second_claim_exits_5`` when this was first built the naive way."""
    import claim_candidate
    a_path = claim_candidate._token_path(tmp_path, "SC-198", "a@test")
    b_path = claim_candidate._token_path(tmp_path, "SC-198", "b@test")
    assert a_path != b_path, "two owners must not share one token sidecar"
    assert a_path.parent == b_path.parent, "same vault + same candidate -> one token directory"
    # the email itself never appears in the name (it would land in a shared directory listing).
    assert "a@test" not in a_path.name and a_path.name.endswith(".token")
    # case-insensitive, whitespace-tolerant: the same owner always resolves to the same sidecar.
    assert claim_candidate._token_path(tmp_path, "SC-198", " A@TEST ") == a_path


def test_token_sidecar_lives_under_the_vault_git_dir_when_it_is_a_repo(tmp_path):
    """CR2: on a git vault the token lives under the vault repo's OWN git dir -- untrackable by
    construction, so it can never travel to a peer through ANY sync path (the `.gitignore` route it
    used to depend on is only written by `sync_push`). A non-git vault has no sync path at all, so
    the vault-root fallback is safe -- and both must be per-VAULT-COPY, never shared."""
    import subprocess

    import claim_candidate
    plain, repo = tmp_path / "plain", tmp_path / "repo"
    plain.mkdir()
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    in_repo = claim_candidate._token_path(repo, "SC-198", "a@test")
    assert (repo / ".git") in in_repo.parents, f"a git vault must store the token under .git: {in_repo}"
    in_plain = claim_candidate._token_path(plain, "SC-198", "a@test")
    assert (plain / ".git") not in in_plain.parents and plain in in_plain.parents, in_plain
    assert in_repo.parent != in_plain.parent, "two vault copies must never share a token directory"


def test_gate_configured_rejects_bad_candidate_id(monkeypatch, tmp_path):
    """m3: on the CONFIGURED branch the candidate id is validated (^SC-\\d+$) BEFORE composing any
    claims/ path -- a traversal id ('../x') is refused (exit 2), and create_if_absent never runs."""
    from scripts.lib import _claim_coord
    vault = _prime_vault(tmp_path)
    stub = _StubBackend(_claim_coord.ClaimResult(status=_claim_coord.CREATED, body={}))
    called = {"n": 0}
    orig = stub.create_if_absent
    stub.create_if_absent = lambda k, b: (called.__setitem__("n", called["n"] + 1) or orig(k, b))
    rc = _run_main_with_backend(monkeypatch, tmp_path, vault, stub, _id_git_env(tmp_path, *A),
                                ["--candidate", "../x", "--name", "do-thing"])
    assert rc == 2, "a non-^SC-\\d+$ candidate id must be refused on the configured branch"
    assert called["n"] == 0, "the id guard must short-circuit BEFORE any claims/ path is composed"


# ── configured end-to-end (real LocalDirClaimBackend via subprocess) ────────────────

def _run_claim(vault, args, git_env, repo_root, backend="local"):
    child = dict(os.environ)
    child.pop("AI_SDLC_VAULT_ROOT", None)
    child.update(git_env)
    if backend is not None:
        child["AI_SDLC_CLAIM_BACKEND"] = backend
    return subprocess.run(
        [sys.executable, str(_CLAIM), "--vault", str(vault), "--repo-root", str(repo_root),
         *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=child)


def test_e2e_configured_second_claim_exits_5(tmp_path):
    """AC1/AC2 end-to-end: A claims (mints + HELD); B's claim of the SAME candidate is auto-detected
    and REFUSED with exit 5 naming A -- never a silent duplicate."""
    vault = _prime_vault(tmp_path)
    cp_a = _run_claim(vault, ["--candidate", "SC-198", "--name", "do-thing", "--json"],
                      _id_git_env(tmp_path, *A), tmp_path)
    assert cp_a.returncode == 0, cp_a.stderr
    assert (vault / "claims" / "SC-198" / "HELD.json").is_file(), "the configured claim must create a HELD"
    cp_b = _run_claim(vault, ["--candidate", "SC-198", "--name", "steal-it", "--json"],
                      _id_git_env(tmp_path, *B), tmp_path)
    assert cp_b.returncode == 5, (cp_b.returncode, cp_b.stdout, cp_b.stderr)
    assert "a@test" in (cp_b.stdout + cp_b.stderr), "the refusal must name the winning owner"


def test_e2e_unconfigured_no_claims_dir_and_mints(tmp_path):
    """AC3: the DEFAULT (unconfigured) path mints exactly as today and creates NO claims/ dir (the
    coordination machinery never fires)."""
    vault = _prime_vault(tmp_path)
    cp = _run_claim(vault, ["--candidate", "SC-198", "--name", "do-thing", "--json"],
                    _id_git_env(tmp_path, *A), tmp_path, backend=None)
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["slice"] == "slice-001"
    assert not (vault / "claims").exists(), "the default path must not create a claims/ dir"


def test_e2e_configured_release_removes_held_allows_reclaim(tmp_path):
    """M-add-1: --release on the configured path removes the HELD (compare-and-delete), so a re-claim
    of the same candidate SUCCEEDS -- a failed worktree-create can never orphan a HELD into a
    permanent EXISTS->LOST candidate lockout."""
    vault = _prime_vault(tmp_path)
    env = _id_git_env(tmp_path, *A)
    assert _run_claim(vault, ["--candidate", "SC-198", "--name", "do-thing"], env, tmp_path).returncode == 0
    assert (vault / "claims" / "SC-198" / "HELD.json").is_file()
    rel = _run_claim(vault, ["--candidate", "SC-198", "--release"], env, tmp_path)
    assert rel.returncode == 0, rel.stderr
    assert not (vault / "claims" / "SC-198" / "HELD.json").exists(), "release must remove the HELD"
    # re-claim of the same candidate now succeeds (no orphan lockout).
    reclaim = _run_claim(vault, ["--candidate", "SC-198", "--name", "do-thing", "--json"], env, tmp_path)
    assert reclaim.returncode == 0, reclaim.stderr


def test_e2e_configured_foreign_release_leaves_held(tmp_path):
    """M-add-1 defense-in-depth: a FOREIGN --release is refused by the existing identity gate before
    remove_if_owner, and remove_if_owner's compare-and-delete would refuse it too -- A's HELD survives."""
    vault = _prime_vault(tmp_path)
    assert _run_claim(vault, ["--candidate", "SC-198", "--name", "do-thing"],
                      _id_git_env(tmp_path, *A), tmp_path).returncode == 0
    cp = _run_claim(vault, ["--candidate", "SC-198", "--release"], _id_git_env(tmp_path, *B), tmp_path)
    assert cp.returncode != 0, "a foreign release must be refused"
    assert (vault / "claims" / "SC-198" / "HELD.json").is_file(), "a foreign release must not remove the HELD"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
