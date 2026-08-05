"""Git-backed shared-claim coordination (slice-100 / SC-216 / ADR-131 + ADR-132).

Implements the FROZEN ``ClaimBackend`` seam (ADR-113) over one remote ref per candidate,
``refs/aisdlc-claims/<SC-NNN>``, whose CAS-from-nil create IS the winner-decision. Every AC is proven
against a REAL git remote -- a local ``git init --bare`` repository is a genuine git server for the
exact primitive under test (server-side ref rejection), so this is reality contact, not a mock.

PROOF STRUCTURE (B2 -- Rev 1's single subprocess race passed identically under a fully sequential run,
the laundered green that blocked slice-091 rounds 1-2). Three separate legs:

  1. CONCURRENCY (``test_transport_concurrent_race_exactly_one_winner``) -- a worker that imports
     ``_claim_git``, WARMS the lazy setup, writes its ready file, tight-spins on the barrier, then
     calls ``create_if_absent`` as the NEXT statement (barrier-to-primitive distance of one line,
     mirroring the shipped ``_RACE_WORKER`` at tests/test_claim_coord.py:40-57). The negative control
     (``test_transport_check_then_force_push_control_double_creates``) is a CHECK-THEN-FORCE-PUSH
     variant run through the SAME ``_run_race`` helper: it double-creates ONLY when the two processes
     genuinely interleave (sequentially its ls-remote pre-check sees the winner and reports EXISTS),
     so it proves THIS harness has teeth.
  2. WIRING (``test_claim_candidate_two_copies_one_winner``) -- a sequential two-vault-copy
     ``claim_candidate.py`` end-to-end test: one mint, one exit-5 LOST naming the winner, and a
     byte-unchanged loser candidates.json.
  3. INVARIANT PIN (``test_transport_lease_create_rejects_child``) -- stickiness is MANUFACTURED, not
     inherent: a plain push of a DESCENDANT commit silently fast-forwards over a live claim. This
     characterization test pins the parentless-root + ``--force-with-lease=<ref>:`` pair ADR-131 calls
     load-bearing.

Test names are chosen so every WS-1 ``-k`` selector in mission-brief.json's architectural_layers
resolves to at least one on-disk test: ``seam``, ``transport``, ``resolve``, ``claim_candidate`` (B1).
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


# ── fixtures: a REAL bare-repo remote + real vault working copies ───────────────────

def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check:
        assert r.returncode == 0, f"git {' '.join(args)} failed in {cwd}: {r.stderr or r.stdout}"
    return r


def _bare_remote(tmp_path: Path, name: str = "remote.git") -> Path:
    remote = tmp_path / name
    remote.mkdir(parents=True)
    _git(remote, "init", "--bare", "-q")
    return remote


def _vault(tmp_path: Path, name: str, remote: Path | str | None, *, identity: bool = True) -> Path:
    """A vault WORKING COPY: a git work tree with an `origin` remote (what `vault_admin git-init`
    leaves behind). No initial commit is needed -- the claim protocol pushes a forged sha directly."""
    v = tmp_path / name
    v.mkdir(parents=True, exist_ok=True)
    _git(v, "init", "-q")
    if remote is not None:
        _git(v, "remote", "add", "origin", str(remote))
    if identity:
        _git(v, "config", "user.name", "Vault Owner")
        _git(v, "config", "user.email", "vault@t")
    return v


def _prime_candidates(vault: Path, cid: str = "SC-216") -> Path:
    doc = {"_schema": "aisdlc/slice-candidates@1", "project": "t", "pick_log": [],
           "counters": {"slice": 0, "sc": 216},
           "candidates": [{"id": cid, "title": "t", "status": "candidate", "progress": "not-started",
                           "slice": None, "claimed_by": None, "started_at": None, "history": []}]}
    p = vault / "candidates.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _id_git_env(tmp_path: Path, name: str, email: str) -> dict:
    cfg = tmp_path / f"gc_{email}".replace("@", "_")
    cfg.write_text(f"[user]\n\tname = {name}\n\temail = {email}\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}


def _body(cand: str, email: str = "a@t", user: str = "A", token: str = "tokA") -> dict:
    return {"candidate": cand, "actor": {"git_user": user, "git_email": email},
            "idempotency_token": token, "at": "2026-01-01T00:00:00Z"}


def _backend(vault: Path):
    from scripts.lib import _claim_coord, _claim_git
    return _claim_coord, _claim_git, _claim_git.GitClaimBackend(vault)


def _remote_sha(vault: Path, ref: str) -> str | None:
    out = _git(vault, "ls-remote", "origin", ref).stdout.strip()
    return out.split()[0] if out else None


def _token_sidecar(vault: Path, candidate: str, email: str) -> Path:
    """Where this vault working copy persists its C2 idempotency token (via the production helper --
    never a hand-built path, so a relocation cannot silently desync test from code)."""
    import claim_candidate
    return claim_candidate._token_path(vault, candidate, email)


# ── git-invocation spy (AC4 / AC6) ──────────────────────────────────────────────────

_GIT_GLOBAL_FLAGS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
NETWORK_SUBCOMMANDS = frozenset({"push", "ls-remote", "fetch"})
# Every git subcommand the coordination path can issue -- AC4 asserts NONE of these fire on the
# unconfigured default path (the claim's own identity lookups `git config` are NOT coordination).
COORDINATION_SUBCOMMANDS = NETWORK_SUBCOMMANDS | {
    "hash-object", "mktree", "commit-tree", "cat-file", "for-each-ref", "update-ref"}
# The only LOCAL subcommands the two REUSED `_vault_git_sync` resolution helpers issue. They inherit
# that module's bulk-sync default timeout; a NETWORK subcommand showing up on that budget would mean
# a /slice pick can hang for the sync engine's window instead of the claim's (AC6 leg 3a).
SIBLING_LOCAL_SUBCOMMANDS = frozenset({"rev-parse", "remote"})


def _subcommand(argv) -> str:
    """The git subcommand of an argv, skipping global flags and their values."""
    parts = [str(a) for a in argv]
    if not parts or Path(parts[0]).stem != "git":
        return ""
    i = 1
    while i < len(parts):
        tok = parts[i]
        if tok in _GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok
    return ""


def _git_spy(monkeypatch, *, fail: set[str] | None = None):
    """Record every ``subprocess.run`` git invocation (argv + kwargs). Patching the ``subprocess``
    module itself catches BOTH ``_claim_git``'s own wrapper and the reused ``_vault_git_sync._git``.
    ``fail`` names subcommands that raise ``TimeoutExpired`` instead of running."""
    calls: list[tuple[list[str], dict]] = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        argv = [str(c) for c in cmd] if isinstance(cmd, (list, tuple)) else [str(cmd)]
        calls.append((argv, kw))
        if fail and _subcommand(argv) in fail:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 30)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def _network_calls(calls) -> list[list[str]]:
    return [argv for argv, _kw in calls if _subcommand(argv) in NETWORK_SUBCOMMANDS]


# ══════════════════════════════════════════════════════════════════════════════════════
# SEAM -- the frozen ClaimBackend contract, implemented over a real remote
# ══════════════════════════════════════════════════════════════════════════════════════

def test_seam_create_if_absent_contract(tmp_path):
    """AC1 (seam layer): the frozen create_if_absent contract over a REAL remote -- the first create
    is CREATED, a second create by a DIFFERENT actor is EXISTS carrying the WINNER's body (never a
    clobber), and get() reads that same body back."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    key = cc.claim_key("SC-216")

    r1 = be.create_if_absent(key, _body("SC-216", "a@t", "A", "tokA"))
    assert r1.status == cc.CREATED, f"first create must win: {r1}"
    r2 = be.create_if_absent(key, _body("SC-216", "b@t", "B", "tokB"))
    assert r2.status == cc.EXISTS, f"second create must lose: {r2}"
    assert r2.body["idempotency_token"] == "tokA", "the loser must read back the WINNER's body"
    assert r2.body["actor"]["git_email"] == "a@t"
    assert be.get(key)["idempotency_token"] == "tokA", "no silent last-write-wins overwrite"
    assert be.get(cc.claim_key("SC-40404")) is None, "an absent claim reads back as None"


def test_seam_remove_if_owner_compare_and_delete(tmp_path):
    """AC3: remove_if_owner is a genuine server-enforced COMPARE-AND-DELETE -- a FOREIGN identity is
    a no-op refusal (False) that leaves the ref present AND unchanged, the recorded owner tears it
    down (True), and a re-claim then succeeds (no orphan lockout)."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    key, ref = cc.claim_key("SC-216"), "refs/aisdlc-claims/SC-216"

    assert be.create_if_absent(key, _body("SC-216", "a@t", "A", "tokA")).status == cc.CREATED
    before = _remote_sha(v, ref)
    assert before, "the claim ref must exist on the remote"

    assert be.remove_if_owner(key, "b@t") is False, "a foreign identity must NOT delete the HELD"
    assert _remote_sha(v, ref) == before, "a foreign release must leave the ref present and UNCHANGED"

    assert be.remove_if_owner(key, "A@T") is True, "the recorded owner removes it (email compare is _norm)"
    assert _remote_sha(v, ref) is None, "the ref must be gone after an owner release"
    assert be.get(key) is None
    assert be.create_if_absent(key, _body("SC-216", "a@t", "A", "tokA2")).status == cc.CREATED
    # idempotent re-release of an already-absent claim reads as False, never an error (spike arm g).
    assert be.remove_if_owner(cc.claim_key("SC-40404"), "a@t") is False


def test_seam_classify_push_adversarial_table():
    """m2 / ADR-132 addition 3: the porcelain classifier is STRUCTURAL (flag + exact ref), never
    prose. Executed against an adversarial battery: the verbatim reject strings, a clean-win 3-line
    stdout, a delete line, a To+Done-only stdout, an empty stdout with rc 128, a SC-1/SC-10 prefix
    cross-probe, an `=` flag, a space flag (a successful FAST-FORWARD -- rc 0, NOT a create), and a
    CRLF-terminated variant."""
    from scripts.lib._claim_git import _classify_push, _ref_flag

    ref = "refs/aisdlc-claims/SC-1"
    to = "To ../remote.git\n"
    done = "Done\n"

    def out(*lines: str) -> str:
        return to + "".join(lines) + done

    win = f"*\tdeadbeef:{ref}\t[new reference]\n"
    assert _classify_push(0, out(win), ref) == "won"
    # the six verbatim reject phrasings observed across two spikes and two servers -- all `!`.
    for reason in ("[rejected] (stale info)", "[rejected] (non-fast-forward)",
                   "[rejected] (fetch first)", "[rejected] (already exists)",
                   "[remote rejected] (pre-receive hook declined)",
                   "[remote rejected] (permission denied)"):
        line = f"!\tdeadbeef:{ref}\t{reason}\n"
        assert _classify_push(1, out(line), ref) == "rejected", reason
    # PREFIX FAMILY: SC-1 / SC-10 / SC-100 must never cross-match (containment would over-match).
    other = "refs/aisdlc-claims/SC-10"
    assert _classify_push(1, out(f"!\tdeadbeef:{other}\t[rejected] (stale info)\n"), ref) == "transient"
    assert _classify_push(1, out(f"!\tdeadbeef:{ref}\t[rejected] (stale info)\n"), other) == "transient"
    # framing-lines only: NEVER implement "no ref line" as "stdout is empty".
    assert _classify_push(1, to + done, ref) == "transient"
    assert _classify_push(0, to + done, ref) == "indeterminate", "rc 0 with no ref line is ambiguous"
    assert _classify_push(128, "", ref) == "transient", "rc!=0, no ref line -> unverified (probe)"
    # a SUCCESSFUL fast-forward is flag ' ' with rc 0 -- NOT a create; it must fail closed.
    assert _classify_push(0, out(f" \tdeadbeef:{ref}\t[fast forward]\n"), ref) == "indeterminate"
    assert _classify_push(0, out(f"+\tdeadbeef:{ref}\t[forced update]\n"), ref) == "indeterminate"
    # `=` (up to date) is reachable ONLY on a C2 self-retry whose forged object is byte-identical
    # (git short-circuits an up-to-date push BEFORE the lease check). It is NOT a create -- it routes
    # to the read-back, where the token compare decides WON-self-retry vs LOST.
    assert _classify_push(0, out(f"=\tdeadbeef:{ref}\t[up to date]\n"), ref) == "rejected"
    # CRLF-terminated porcelain still parses.
    assert _classify_push(0, out(win).replace("\n", "\r\n"), ref) == "won"
    # the delete line is read by remove_if_owner via the same pure parser.
    assert _ref_flag(out(f"-\t(delete):{ref}\t[deleted]\n"), ref) == "-"
    assert _ref_flag(out(f"!\t(delete):{ref}\t[rejected] (stale info)\n"), ref) == "!"
    assert _ref_flag(to + done, ref) is None


def test_seam_exists_stderr_names_remote_and_ref(tmp_path, capsys):
    """m6: a LOST is attributable to a REGISTER, not just an owner -- the backend itself writes one
    stderr line naming the remote NAME and the ref, so a divergent-register / stale-claim collision
    is diagnosable without touching the consumer."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    key = cc.claim_key("SC-216")
    be.create_if_absent(key, _body("SC-216", "a@t", "A", "tokA"))
    capsys.readouterr()
    assert be.create_if_absent(key, _body("SC-216", "b@t", "B", "tokB")).status == cc.EXISTS
    err = capsys.readouterr().err
    assert "origin" in err, f"the EXISTS line must name the remote NAME: {err!r}"
    assert "refs/aisdlc-claims/SC-216" in err, f"the EXISTS line must name the ref: {err!r}"


def test_seam_userinfo_redacted_from_all_messages(tmp_path, capsys):
    """M8 / must_not_defer item 3: `git push --porcelain` writes `To <remote-url>` on STDOUT -- the
    stream the classifier parses -- so the redactor must cover BOTH streams.

    THREE legs, because the end-to-end leg alone cannot prove the redactor exists: modern git often
    self-anonymizes URLs in its own error text, and M8's whole point is that a security guarantee may
    not rest on unverified upstream behaviour. Leg 1 tests the redactor directly, leg 2 proves it is
    APPLIED at the single wrapper boundary (using a command whose STDOUT is the raw credential-bearing
    URL), and leg 3 is the end-to-end no-leak assertion."""
    from scripts.lib._claim_git import redact

    url = "https://claimuser:s3cr3t-pw@127.0.0.1:1/x.git"

    # leg 1 -- the redactor itself, over the shapes git actually emits on both streams.
    for raw in (f"To {url}\n*\tdeadbeef:refs/aisdlc-claims/SC-1\t[new reference]\nDone\n",
                f"fatal: unable to access '{url}': Could not resolve host\n",
                "fatal: could not read from 'git@host.example:team/vault.git'\n"):
        clean = redact(raw)
        for secret in ("s3cr3t-pw", "claimuser", "@127.0.0.1", "git@host.example"):
            assert secret not in clean, f"redact() leaked {secret!r} from {raw!r} -> {clean!r}"
    assert "[new reference]" in redact(f"To {url}\nx\t[new reference]\n"), \
        "redaction must not destroy the porcelain the classifier parses"

    v = _vault(tmp_path, "vaultA", url)
    cc, _cg, be = _backend(v)

    # leg 2 -- the redactor is APPLIED at the wrapper, to STDOUT (where porcelain writes `To <url>`).
    raw_url = be._run_git(["config", "--get", "remote.origin.url"])
    assert raw_url.rc == 0, raw_url.err
    assert "s3cr3t-pw" not in raw_url.out and "claimuser" not in raw_url.out, \
        f"the wrapper must redact STDOUT before anything can quote it: {raw_url.out!r}"

    # leg 3 -- end to end: no message the seam produces carries the credential.
    capsys.readouterr()
    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216", "a@t", "A", "tokA"))
    assert res.status == cc.UNVERIFIABLE, f"an unreachable remote must fail closed: {res}"
    surface = (res.reason or "") + capsys.readouterr().err
    for secret in ("s3cr3t-pw", "claimuser", "@127.0.0.1"):
        assert secret not in surface, f"{secret!r} leaked into a seam message: {surface!r}"


# ══════════════════════════════════════════════════════════════════════════════════════
# TRANSPORT -- the atomic remote-side create-if-absent + read-back
# ══════════════════════════════════════════════════════════════════════════════════════

_RACE_WORKER = r'''
import sys, os, glob, time
sys.path.insert(0, r"__REPO__")
from pathlib import Path
from scripts.lib import _claim_coord, _claim_git

vault, candidate, token, barrier_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
be = _claim_git.GitClaimBackend(Path(vault))
be._remote()   # WARM the lazy setup PRE-barrier so the barrier-to-push distance is the forge only
key = _claim_coord.claim_key(candidate)
body = {"candidate": candidate, "actor": {"git_user": token, "git_email": token + "@t"},
        "idempotency_token": token, "at": "2026-01-01T00:00:00Z"}
Path(barrier_dir, "ready_" + token).write_text("1", encoding="utf-8")
deadline = time.monotonic() + 60
while len(glob.glob(os.path.join(barrier_dir, "ready_*"))) < n:  # tight spin -- no sleep
    if time.monotonic() > deadline:
        print("TIMEOUT " + token); sys.exit(3)
res = be.create_if_absent(key, body)
print(res.status + " " + token)
'''

# Negative control: CHECK-THEN-FORCE-PUSH -- the exact non-atomic implementation B2 forbids (an
# ls-remote pre-check, then a plain force push). SEQUENTIALLY it cannot double-create (the second
# run's pre-check sees the winner and reports EXISTS); only genuine INTERLEAVING makes both see an
# absent ref and both write. A 20ms sleep widens the TOCTOU window, exactly as the shipped
# _NAIVE_WORKER does at tests/test_claim_coord.py:63-81.
_FORCE_WORKER = r'''
import sys, os, glob, time, subprocess
from pathlib import Path
vault, candidate, token, barrier_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
ref = "refs/aisdlc-claims/" + candidate
env = dict(os.environ)
env.update(GIT_AUTHOR_NAME=token, GIT_AUTHOR_EMAIL=token + "@t",
           GIT_COMMITTER_NAME=token, GIT_COMMITTER_EMAIL=token + "@t")

def g(args, inp=None):
    return subprocess.run(["git", *args], cwd=vault, input=inp, capture_output=True, env=env)

Path(barrier_dir, "ready_" + token).write_text("1", encoding="utf-8")
deadline = time.monotonic() + 60
while len(glob.glob(os.path.join(barrier_dir, "ready_*"))) < n:
    if time.monotonic() > deadline:
        print("TIMEOUT " + token); sys.exit(3)
if g(["ls-remote", "origin", ref]).stdout.strip():      # check
    print("EXISTS " + token); sys.exit(0)
time.sleep(0.02)                                        # widen the TOCTOU window (the forbidden shape)
blob = g(["hash-object", "-w", "--stdin"], inp=('{"idempotency_token":"%s"}' % token).encode()).stdout.decode().strip()
tree = g(["mktree"], inp=("100644 blob %s\tHELD.json\n" % blob).encode()).stdout.decode().strip()
sha = g(["commit-tree", tree, "-m", "claim"]).stdout.decode().strip()
p = g(["push", "--porcelain", "--force", "origin", sha + ":" + ref])   # then-write (clobbers)
print(("CREATED " if p.returncode == 0 else "FAILED ") + token)
'''


def _spawn(worker: str, vault: Path, cand: str, tok: str, bdir: Path, n: int):
    code = worker.replace("__REPO__", str(_REPO)).replace("__CLAIM__", str(_CLAIM))
    return subprocess.Popen(
        [sys.executable, "-c", code, str(vault), cand, tok, str(bdir), str(n)],
        cwd=str(vault), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace")


def _status(proc, out: str, err: str, where: str) -> str:
    assert proc.returncode == 0, f"{where} worker failed (rc={proc.returncode}): {err or out}"
    return out.strip().splitlines()[-1].split()[0]


# CR8: the CONSUMER raced, not just the backend. AC1's text says two processes race
# `claim_candidate` -- the backend race proves the primitive and the two-copy test proves the wiring,
# but their COMPOSITION (create gate -> token sidecar -> local mint -> compensation) was only ever
# observed serialized. This worker shells out to the real CLI from behind the same barrier, so the
# composition is exercised under genuine interleaving. It prints the CLI's exit code, which the test
# maps: 0 = minted, 5 = LOST.
_CONSUMER_WORKER = r'''
import sys, os, glob, time, subprocess
from pathlib import Path
vault, candidate, token, barrier_dir, n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
claim = r"__CLAIM__"
env = dict(os.environ)
env["AI_SDLC_CLAIM_BACKEND"] = "git"
env.pop("AI_SDLC_VAULT_ROOT", None)
env["GIT_CONFIG_GLOBAL"] = os.path.join(barrier_dir, "gc_" + token)
env["GIT_CONFIG_NOSYSTEM"] = "1"
Path(env["GIT_CONFIG_GLOBAL"]).write_text(
    "[user]\n\tname = Dev " + token + "\n\temail = dev" + token.lower() + "@test\n", encoding="utf-8")
argv = [sys.executable, claim, "--vault", vault, "--repo-root", vault,
        "--candidate", candidate, "--name", "do-thing-" + token.lower()]
Path(barrier_dir, "ready_" + token).write_text("1", encoding="utf-8")
deadline = time.monotonic() + 60
while len(glob.glob(os.path.join(barrier_dir, "ready_*"))) < n:   # tight spin -- no sleep
    if time.monotonic() > deadline:
        print("TIMEOUT " + token); sys.exit(3)
cp = subprocess.run(argv, cwd=vault, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("RC%d %s" % (cp.returncode, token))
'''


def _run_race(worker: str, vaults: list[tuple[str, Path]], tmp_path: Path,
              rounds: int, first: int = 0) -> list[list[str]]:
    """Race N barrier-synced processes -- one per (token, vault) -- over `rounds` FRESH candidates
    against ONE shared bare remote; return the per-round statuses."""
    n = len(vaults)
    out_rounds: list[list[str]] = []
    for r in range(first, first + rounds):
        cand = f"SC-{r + 900:03d}"
        bdir = tmp_path / f"barrier_{r}"
        bdir.mkdir()
        procs = [_spawn(worker, vault, cand, tok, bdir, n) for tok, vault in vaults]
        outs = [p.communicate(timeout=120) for p in procs]
        out_rounds.append([_status(p, o, e, f"round {r}") for (o, e), p in zip(outs, procs)])
    return out_rounds


def _run_sequential(worker: str, vaults: list[tuple[str, Path]], tmp_path: Path,
                    cand: str, tag: str) -> list[str]:
    """Run the SAME worker one-at-a-time (each its own n=1 barrier, so it never waits) over ONE
    candidate -- the sequential counterfactual the barrier run is measured against."""
    statuses = []
    for i, (tok, vault) in enumerate(vaults):
        bdir = tmp_path / f"seq_{tag}_{i}"
        bdir.mkdir()
        p = _spawn(worker, vault, cand, tok, bdir, 1)
        out, err = p.communicate(timeout=120)
        statuses.append(_status(p, out, err, f"sequential {tag}[{i}]"))
    return statuses


def test_transport_concurrent_race_exactly_one_winner(tmp_path):
    """AC1 (the load-bearing proof): TWO barrier-synced CONCURRENT PROCESSES -- never a sequential
    proxy -- racing create_if_absent for the SAME candidate against ONE real bare-repo remote yield
    EXACTLY ONE CREATED and one EXISTS per round, and the surviving HELD is the winner's body."""
    remote = _bare_remote(tmp_path)
    vaults = [("A", _vault(tmp_path, "vaultA", remote)), ("B", _vault(tmp_path, "vaultB", remote))]
    rounds = 12
    statuses = _run_race(_RACE_WORKER, vaults, tmp_path, rounds)
    for i, st in enumerate(statuses):
        assert sorted(st) == ["CREATED", "EXISTS"], f"round {i}: expected exactly one winner, got {st}"
    # no silent clobber: each surviving claim carries exactly one of the two racers' tokens.
    probe = vaults[0][1]
    for i in range(rounds):
        ref = f"refs/aisdlc-claims/SC-{i + 900:03d}"
        _git(probe, "fetch", "--no-tags", "origin", f"+{ref}:refs/probe/{i}")
        held = json.loads(_git(probe, "cat-file", "blob", f"refs/probe/{i}:HELD.json").stdout)
        assert held["idempotency_token"] in ("A", "B"), f"round {i}: HELD torn/absent: {held}"


def test_transport_check_then_force_push_control_double_creates(tmp_path):
    """Negative control (B2) -- the harness is measured against its own SEQUENTIAL counterfactual.

    The control worker is the non-atomic shape the design forbids: an ls-remote PRE-CHECK, then a
    plain force push. Run SEQUENTIALLY it can never double-write -- the second run's pre-check sees
    the winner and reports EXISTS. Run through the SAME barrier harness, BOTH workers pass the
    pre-check and proceed to write, which is only possible if the two processes genuinely INTERLEAVE
    inside the TOCTOU window. (Which of the two racing writes then wins the remote's ref lock is not
    the point and is deliberately not asserted -- passing the check is.)

    So this proves the AC1 harness has teeth: it is a concurrency proof, not a sequential proxy."""
    remote = _bare_remote(tmp_path)
    vaults = [("A", _vault(tmp_path, "vaultA", remote)), ("B", _vault(tmp_path, "vaultB", remote))]

    seq = _run_sequential(_FORCE_WORKER, vaults, tmp_path, "SC-800", "force")
    assert seq == ["CREATED", "EXISTS"], (
        f"sequentially the pre-check MUST see the winner (else this control proves nothing): {seq}")

    statuses = _run_race(_FORCE_WORKER, vaults, tmp_path, rounds=6, first=100)
    interleaved = [st for st in statuses if "EXISTS" not in st]
    assert interleaved, (
        "under the barrier BOTH workers must pass the ls-remote pre-check (the interleaving the AC1 "
        f"proof depends on); every round serialised instead: {statuses}")


def test_transport_lease_create_rejects_child(tmp_path):
    """INVARIANT PIN (ADR-131): stickiness is MANUFACTURED, not inherent. The design spike's control
    arm proved a plain push of a DESCENDANT commit silently fast-forwards over a live claim (rc 0,
    flag ' ', the winner's HELD destroyed). This pins BOTH manufacturing halves: (a) the claim commit
    is PARENTLESS, and (b) `--force-with-lease=<ref>:` rejects a child push through the backend's OWN
    push path, leaving the ref byte-unchanged."""
    from scripts.lib._claim_git import _ref_flag

    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    ref = "refs/aisdlc-claims/SC-216"
    assert be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216")).status == cc.CREATED

    claim_sha = _remote_sha(v, ref)
    body = _git(v, "cat-file", "-p", claim_sha).stdout
    assert "parent " not in body, f"(a) the claim commit MUST be parentless: {body!r}"

    _git(v, "fetch", "--no-tags", "origin", f"+{ref}:refs/probe/live")
    tree = _git(v, "rev-parse", "refs/probe/live^{tree}").stdout.strip()
    child = subprocess.run(["git", "commit-tree", tree, "-p", claim_sha, "-m", "hostile child"],
                           cwd=str(v), capture_output=True, text=True,
                           env={**os.environ, "GIT_AUTHOR_NAME": "X", "GIT_AUTHOR_EMAIL": "x@t",
                                "GIT_COMMITTER_NAME": "X", "GIT_COMMITTER_EMAIL": "x@t"})
    child_sha = child.stdout.strip()
    assert child_sha, child.stderr

    pushed = be._push_claim(ref, child_sha)  # the backend's OWN lease-guarded push path
    assert _ref_flag(pushed.out, ref) == "!", f"(b) a child push MUST be rejected: {pushed.out!r}"
    assert _remote_sha(v, ref) == claim_sha, "the winner's claim must be byte-unchanged"


def test_transport_claim_namespace_is_outside_the_default_refspec(tmp_path):
    """ADR-131: the claim namespace sits deliberately OUTSIDE `refs/heads/*` so the default fetch
    refspec never pulls claims down and `sync_push`'s branch push never touches them. That was stated
    but never asserted -- and it is what keeps a team's claim register from turning into hundreds of
    remote-tracking branches in every peer's vault (and from being clobbered by an ordinary push)."""
    remote = _bare_remote(tmp_path)
    va, vb = _vault(tmp_path, "vaultA", remote), _vault(tmp_path, "vaultB", remote)
    cc, _cg, be = _backend(va)
    assert be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216")).status == cc.CREATED
    assert _cg.CLAIM_REF_PREFIX.startswith("refs/") and not _cg.CLAIM_REF_PREFIX.startswith("refs/heads/")

    _git(vb, "fetch", "origin")  # a PLAIN fetch: whatever the default refspec brings, and nothing more
    local = _git(vb, "for-each-ref", "--format=%(refname)").stdout
    assert "aisdlc-claims" not in local, \
        f"a plain `git fetch` must not pull the claim register into a peer's vault: {local!r}"


def test_transport_winning_claim_issues_exactly_one_network_call(tmp_path, monkeypatch):
    """AC6 leg 1 (discharges spike A3 constraint 2 by EVIDENCE): the sticky-register protocol is
    doorway-free -- the write ITSELF decides. A WINNING configured claim issues EXACTLY ONE network
    round-trip (the create push): no doorway, no unconditional ls-remote pre-check, no read-back.
    A refactor that reintroduces a pre-check is caught HERE, not by review."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    be._remote()  # exclude the one-time lazy setup (LOCAL calls only) from the measurement
    calls = _git_spy(monkeypatch)
    assert be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216")).status == cc.CREATED
    net = _network_calls(calls)
    assert len(net) == 1, f"a winning claim must cost exactly ONE network round-trip, got {net}"
    assert _subcommand(net[0]) == "push"
    assert not [a for a in calls if _subcommand(a[0]) == "ls-remote"], \
        "the ADR-132 existence probe must NOT fire on a win"


def test_transport_probe_fires_only_on_unverified_create(tmp_path, monkeypatch):
    """AC6 leg 2 + M1: on an UNVERIFIED create (rc!=0 with no ref line) 'never reached' must be a
    PROBE RESULT, not an inference -- exactly ONE bounded ls-remote fires, and only on that branch."""
    v = _vault(tmp_path, "vaultA", tmp_path / "does-not-exist.git")
    cc, _cg, be = _backend(v)
    calls = _git_spy(monkeypatch)
    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    assert res.status == cc.UNVERIFIABLE, res
    probes = [a for a, _kw in calls if _subcommand(a) == "ls-remote"]
    assert len(probes) == 1, f"the existence oracle must fire EXACTLY once, got {len(probes)}"
    assert any("refs/aisdlc-claims/SC-216" in tok for tok in probes[0]), probes[0]


def test_transport_every_network_call_carries_30s_timeout(tmp_path, monkeypatch):
    """AC6 leg 3a (discharges spike A3 constraint 1): every claim-path git invocation carries an
    explicit FINITE timeout, and every NETWORK call is bounded at 30s -- never the sibling engine's
    300s bulk-sync default, which would hang a /slice pick.

    Reading on record: the two REUSED resolution helpers (`_vault_git_sync._require_git_tree` /
    `_resolve_remote`, imported per the design's no-re-definition rule) call the sibling `_git` with
    its 300s default; m3 forbids widening that signature. They issue only LOCAL subcommands
    (`rev-parse`, `remote`), which cannot hang on the network -- asserted below.

    ORDER-INDEPENDENCE (R-12 / SC-221): both bounds are read from the PRODUCTION constants, and the
    process-wide `_vault_paths.git_common_dir()` memo is warmed BEFORE the spy is installed. A cold
    memo spawns a `git rev-parse` of its own on the vault-RESOLUTION seam (bounded, local, on nobody's
    claim budget), so whether the spy saw it used to depend entirely on whether an earlier test in the
    module had already warmed it: standalone this test FAILED, and the whole-module ordering that
    passed never inspected the call it exists to bound -- a laundered green over a latent red that any
    single-test selection or future shard would trip. Warming here makes the measurement the claim
    path's OWN calls in EVERY ordering; same deliberate-exclusion idiom as leg 1's `be._remote()`."""
    from scripts.lib import _vault_git_sync, _vault_paths
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, cg, be = _backend(v)
    _vault_paths.git_common_dir()  # warm the shared memo OUT of the measurement (see docstring)
    # The 30s network BOUND stays a literal pin. Deriving it from CLAIM_TIMEOUT would make the
    # assertion move with the constant, so a widened claim budget would still read green while a
    # /slice pick blocks for the new window -- a bound that follows its subject bounds nothing.
    # The sibling ALLOWANCE is derived: widening it cannot loosen the claim guarantee, because it is
    # admitted only for the LOCAL subcommands asserted below.
    assert cg.CLAIM_TIMEOUT == 30, \
        f"the claim budget widened to {cg.CLAIM_TIMEOUT}s -- a /slice pick now blocks that long"
    sibling_t = _vault_git_sync._GIT_TIMEOUT
    calls = _git_spy(monkeypatch)
    be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216", "b@t", "B", "tokB"))  # EXISTS: fetch too
    be.remove_if_owner(cc.claim_key("SC-216"), "a@t")
    assert calls, "the spy recorded nothing"
    assert _network_calls(calls), "no NETWORK call was measured -- the 30s bound went untested"
    for argv, kw in calls:
        sub = _subcommand(argv)
        timeout = kw.get("timeout")
        assert timeout is not None, f"{sub}: an UNBOUNDED claim-path git call would hang /slice: {argv}"
        if sub in NETWORK_SUBCOMMANDS:
            assert timeout == 30, f"{sub}: network calls must be bounded at 30s, got {timeout}"
        else:
            assert timeout in (30, sibling_t), f"{sub}: unexpected timeout {timeout}"
            if timeout == sibling_t:  # the reused sibling helpers only
                assert sub in SIBLING_LOCAL_SUBCOMMANDS, \
                    f"a NON-local subcommand {sub} inherited the {sibling_t}s bulk-sync " \
                    f"default: {argv}"


def test_transport_timeout_refuses_indeterminate(tmp_path, monkeypatch):
    """AC6 leg 3b / ADR-132 addition 1: a TimeoutExpired produces NO rc and never reaches the
    classifier, so it must NOT read as 'the claim did not commit' -- it routes to the existence
    oracle, and an oracle that also cannot answer is INDETERMINATE (exit 4), never transient."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    be._remote()
    _git_spy(monkeypatch, fail={"push", "ls-remote"})
    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    assert res.status == cc.UNVERIFIABLE and res.kind == "indeterminate", res
    assert "timed out" in (res.reason or "").lower(), res.reason


def _squat_ref(v: Path, ref: str, entries: dict[str, str]) -> str:
    """Push a hand-forged commit onto `ref` whose tree is exactly `entries` ({path: content}).

    BINARY stdin throughout -- deliberately, and load-bearing for this fixture's honesty. In text
    mode Python's TextIOWrapper rewrites `\\n` to `os.linesep`, so on Windows the mktree record
    `100644 blob <sha>\\tHELD.json\\n` becomes `...HELD.json\\r\\n` and git names the entry
    `HELD.json\\r`. Every squat then degenerates into "the tree has no HELD.json", and the four cases
    below all collapse into ONE -- a probe that does not exercise what it claims to (BC-PROJ-18).
    Caught by the BC-PROJ-12 mutation battery, not by the green run."""
    records = b""
    for name, content in entries.items():
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(v),
                              input=content.encode("utf-8"), capture_output=True).stdout.decode().strip()
        records += f"100644 blob {blob}\t{name}\n".encode("utf-8")
    tree = subprocess.run(["git", "mktree"], cwd=str(v), input=records,
                          capture_output=True).stdout.decode().strip()
    assert tree, f"mktree produced no tree for {list(entries)}"
    sha = subprocess.run(["git", "commit-tree", tree, "-m", "squat"], cwd=str(v),
                         capture_output=True, text=True,
                         env={**os.environ, "GIT_AUTHOR_NAME": "X", "GIT_AUTHOR_EMAIL": "x@t",
                              "GIT_COMMITTER_NAME": "X", "GIT_COMMITTER_EMAIL": "x@t"}).stdout.strip()
    _git(v, "push", "origin", f"{sha}:{ref}")
    # self-inspection: the squatted tree must carry EXACTLY the entry names asked for, so a silent
    # newline/encoding mangle can never let a degenerate fixture bank a false PASS.
    listed = {ln.split("\t")[-1] for ln in
              _git(v, "ls-tree", "--name-only", sha).stdout.splitlines() if ln.strip()}
    assert listed == set(entries), f"squatted tree is {sorted(listed)}, expected {sorted(entries)}"
    return sha


@pytest.mark.parametrize("entries,case", [
    ({"README": "not a claim"}, "no HELD.json at all"),
    ({"HELD.json": "{ this is not json"}, "HELD.json is not parseable JSON"),
    ({"HELD.json": '["not", "an", "object"]'}, "HELD.json is not a JSON object"),
    ({"HELD.json": '{"candidate": "SC-216", "actor": {"git_user": "?", "git_email": "  "}}'},
     "the body parses but names NO owner (blank actor.git_email)"),
])
def test_transport_exists_with_unreadable_body_is_indeterminate(tmp_path, entries, case):
    """M2 / ADR-132 addition 2: get() collapses absent, unreachable AND unreadable to None, so a
    rejected create whose read-back yields no USABLE body must be INDETERMINATE, never EXISTS.

    All FOUR unusable shapes the ADR enumerates are exercised -- especially the last: a body that
    parses but carries a blank `actor.git_email` would otherwise flow through as an EXISTS and print
    'already claimed by ? <unknown>', the false LOST naming nobody that ADR-130's own text forbids."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    ref = "refs/aisdlc-claims/SC-216"
    _squat_ref(v, ref, entries)

    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    assert res.status == cc.UNVERIFIABLE and res.kind == "indeterminate", f"{case}: {res}"
    assert "unreadable" in (res.reason or "").lower(), f"{case}: {res.reason!r}"
    got = be.get(cc.claim_key("SC-216"))
    assert got is None or not str((got.get("actor") or {}).get("git_email") or "").strip(), \
        f"{case}: get() must never hand back a HELD naming an owner it does not actually have"


def test_transport_permission_denied_pre_receive_refuses_indeterminate(tmp_path):
    """m4: a server-side POLICY decline is a THIRD branch neither AC1 (contention) nor AC5
    (unreachable) reaches -- rc!=0 WITH a `!` ref line whose read-back proves the ref ABSENT. It must
    fail closed as indeterminate, and the message must name BOTH causes (a concurrent claim since
    released, and missing push permission) plus the remote NAME and the ref."""
    remote = _bare_remote(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'permission denied' >&2\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hook, 0o755)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)

    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    assert res.status == cc.UNVERIFIABLE and res.kind == "indeterminate", res
    reason = (res.reason or "").lower()
    assert "permission" in reason, f"the message must name the push-permission cause: {res.reason!r}"
    assert "released" in reason or "concurrent" in reason, \
        f"the message must name the concurrent-claim cause: {res.reason!r}"
    assert "origin" in (res.reason or "") and "refs/aisdlc-claims/SC-216" in (res.reason or "")


def test_transport_forge_uses_actor_identity_without_vault_identity(tmp_path, monkeypatch):
    """M-add-4 / ADR-131 addition 1: the claim object is forged with the CLAIMING ACTOR's identity
    injected, so (a) a vault repo with NO configured git identity can still claim (unlike the sibling
    engine, which REFUSES at `_check_committer_identity`), and (b) `git log refs/aisdlc-claims/*` is a
    free attributable trail. Note the actor identity comes from the PROJECT repo (claim_candidate's
    --repo-root), NOT from the vault repo -- which is exactly why the injection is load-bearing."""
    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))  # no global identity either
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote, identity=False)
    assert not _git(v, "config", "user.email", check=False).stdout.strip(), \
        "the vault must have NO identity anywhere (local, global or system)"
    cc, _cg, be = _backend(v)
    assert be.create_if_absent(cc.claim_key("SC-216"),
                               _body("SC-216", "claimer@test", "Claimer")).status == cc.CREATED
    _git(v, "fetch", "--no-tags", "origin", "+refs/aisdlc-claims/SC-216:refs/probe/x")
    log = _git(v, "log", "-1", "--format=%an <%ae> | %cn <%ce>", "refs/probe/x").stdout
    assert "claimer@test" in log, f"the claim must be attributable to the CLAIMING actor: {log!r}"


def test_transport_argv_carries_credential_suppression_flags(tmp_path, monkeypatch):
    """M-add-2 / must_not_defer item 4: GIT_TERMINAL_PROMPT=0 + BatchMode SSH do NOT suppress a
    CREDENTIAL HELPER's own prompt, and GCM is the default helper on Git for Windows -- this
    project's own platform, often driven headlessly where the dialog is invisible and blocks until
    the timeout. Every claim-path invocation must carry the per-invocation -c suppressions.
    (An argv assertion, not a behavioural one -- the GCM hang is not hermetically reproducible.)"""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    calls = _git_spy(monkeypatch)
    be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    own = [argv for argv, _kw in calls if _subcommand(argv) in COORDINATION_SUBCOMMANDS]
    assert own, "the claim issued no coordination git calls"
    for argv in own:
        joined = " ".join(argv)
        assert "credential.interactive=false" in joined, argv
        assert "credential.guiPrompt=false" in joined, argv
    env = calls[0][1].get("env") or {}
    assert env.get("GIT_TERMINAL_PROMPT") == "0", "non-interactive git is a must_not_defer item"


def test_transport_peek_ref_sweep_removes_leaked_refs(tmp_path):
    """m5: a `finally` does not survive SIGKILL or a hard tool-timeout, and a leaked
    refs/aisdlc-claim-peek/<uuid> permanently ROOTS the fetched claim objects AND survives the
    vault's own repair path (`sync_pull --force` touches only the working tree, never refs/). The
    backend sweeps stale peek refs on FIRST USE -- never in the constructor, which must stay inert.

    CR5: the sweep must NOT be indiscriminate. The uuid scoping exists so two processes racing the
    SAME candidate on one machine (the AC1 scenario) do not clobber each other's read-back -- an
    unconditional global sweep would undo exactly that, deleting a live sibling's in-flight peek ref
    between its fetch and its cat-file and turning a clean exit-5 LOST into a spurious exit-4. So a
    ref carrying a RECENT stamp is left alone; only genuinely stale (or unstamped legacy) refs go."""
    import time as _time

    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    assert be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216")).status == cc.CREATED
    _git(v, "fetch", "--no-tags", "origin", "+refs/aisdlc-claims/SC-216:refs/probe/seed")
    seed = _git(v, "rev-parse", "refs/probe/seed").stdout.strip()

    old_stamp = int(_time.time()) - (_cg.PEEK_SWEEP_GRACE + 60)
    stale = [f"refs/aisdlc-claim-peek/{old_stamp}-deadbeef", "refs/aisdlc-claim-peek/legacy-unstamped"]
    live = f"refs/aisdlc-claim-peek/{int(_time.time())}-livesibling"
    for ref in (*stale, live):
        _git(v, "update-ref", ref, seed)

    _cg.GitClaimBackend(v).get(cc.claim_key("SC-216"))  # a FRESH backend sweeps on first use
    after = set(_git(v, "for-each-ref", "--format=%(refname)",
                     "refs/aisdlc-claim-peek/").stdout.split())
    assert after == {live}, (
        f"the sweep must remove leaked refs but SPARE a live sibling's in-flight read-back; "
        f"remaining={sorted(after)}")


def test_transport_unreachable_remote_get_returns_none(tmp_path):
    """The frozen contract: get() returns None on absent OR unreachable -- it never raises across
    the seam, so a read-back can never crash a claim."""
    v = _vault(tmp_path, "vaultA", tmp_path / "nope.git")
    cc, _cg, be = _backend(v)
    assert be.get(cc.claim_key("SC-216")) is None
    assert be.get("claims/../../etc/passwd/HELD.json") is None, "a malformed key must not raise"


# ══════════════════════════════════════════════════════════════════════════════════════
# RESOLVE -- coordination_backend config resolution (opt-in, inert, fail-closed)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_resolve_git_signal_to_backend(tmp_path, monkeypatch):
    """config-resolution layer: `git` now RESOLVES to the GitClaimBackend instead of raising
    UnsupportedBackend; s3/minio still fail closed (they are a separate cut)."""
    from scripts.lib import _claim_coord, _claim_git
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "git")
    be = _claim_coord.coordination_backend(tmp_path)
    assert isinstance(be, _claim_git.GitClaimBackend)
    assert isinstance(be, _claim_coord.ClaimBackend), "must implement the FROZEN seam"
    for still_unbuilt in ("s3", "minio"):
        monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", still_unbuilt)
        with pytest.raises(_claim_coord.UnsupportedBackend):
            _claim_coord.coordination_backend(tmp_path)


def test_resolve_constructor_is_inert(tmp_path, monkeypatch):
    """The constructor must be INERT -- zero subprocess, zero network. claim_candidate.py:571 builds
    a backend on the `transfer` path purely to test `is not None`, catching only UnsupportedBackend,
    so a constructor that could raise would crash `transfer` with a traceback. Setup errors surface
    LAZILY on first use as a fail-closed UNVERIFIABLE carrying the actionable reason."""
    from scripts.lib import _claim_coord
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "git")
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    calls = _git_spy(monkeypatch)
    be = _claim_coord.coordination_backend(not_a_repo)  # must NOT raise, must NOT spawn git
    assert be is not None
    assert not calls, f"the constructor must spawn NO subprocess, got {[a for a, _ in calls]}"
    res = be.create_if_absent(_claim_coord.claim_key("SC-216"), _body("SC-216"))
    assert res.status == _claim_coord.UNVERIFIABLE, res
    assert "not a git work tree" in (res.reason or ""), res.reason
    # CR3: a SETUP fault is NOT transient. `transient` renders as "the claim did NOT commit; retry
    # it" -- advice that can never work for a configuration error, sending the user into a loop.
    assert res.kind == "indeterminate", res
    assert "CONFIGURATION error" in (res.reason or ""), res.reason


def test_resolve_sync_config_secret_key_fails_closed(tmp_path, monkeypatch):
    """M7 (c): `_sync_config.load()` raises SyncConfigError -- a THIRD exception class in neither
    enumerated taxonomy -- when the persisted profile carries a secret-shaped key. It must surface as
    a fail-closed UNVERIFIABLE, never an uncaught traceback out of the backend."""
    from scripts.lib import _claim_coord, _claim_git, _sync_config
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)

    def boom(*_a, **_kw):
        raise _sync_config.SyncConfigError("sync-backend config carries a secret-shaped key")

    monkeypatch.setattr(_claim_git._sync_config, "load", boom)
    be = _claim_git.GitClaimBackend(v)
    res = be.create_if_absent(_claim_coord.claim_key("SC-216"), _body("SC-216"))
    assert res.status == _claim_coord.UNVERIFIABLE, res
    assert "secret-shaped" in (res.reason or ""), res.reason
    assert res.kind == "indeterminate", f"CR3: a config-integrity fault is not retryable: {res}"
    assert be.remove_if_owner(_claim_coord.claim_key("SC-216"), "a@t") is False, "must never raise"


def test_resolve_unconfigured_makes_zero_coordination_git_calls(tmp_path, monkeypatch, capsys):
    """AC4 (leg b -- the subprocess spy): with NO AI_SDLC_CLAIM_BACKEND and no aisdlc/claim-backend
    config, a claim performs ZERO git subprocess calls for COORDINATION and its output is
    byte-identical to the pre-change baseline."""
    import claim_candidate
    from scripts.lib import _claim_coord

    monkeypatch.delenv("AI_SDLC_CLAIM_BACKEND", raising=False)
    monkeypatch.delenv("AI_SDLC_VAULT_ROOT", raising=False)
    v = _vault(tmp_path, "vault", None)
    _prime_candidates(v)
    for k, val in _id_git_env(tmp_path, *A).items():
        monkeypatch.setenv(k, val)
    assert _claim_coord.coordination_backend(v) is None, "the default path must be unconfigured"

    calls = _git_spy(monkeypatch)
    capsys.readouterr()
    rc = claim_candidate.main(["--vault", str(v), "--repo-root", str(tmp_path),
                               "--candidate", "SC-216", "--name", "do-thing", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    coordination = [a for a, _kw in calls if _subcommand(a) in COORDINATION_SUBCOMMANDS]
    assert not coordination, f"the unconfigured path must make ZERO coordination git calls: {coordination}"
    payload = json.loads(out)
    assert payload["action"] == "claim-candidate" and payload["slice"] == "slice-001"
    assert payload["folder"] == "slice-001-do-thing" and payload["status"] == "spiking"
    assert not (v / "claims").exists(), "the default path must not create a claims/ dir"


def test_resolve_unconfigured_child_interpreter_never_imports_claim_git(tmp_path):
    """AC4 (leg a -- M3): the import-graph assertion runs in a FRESH CHILD INTERPRETER. An in-process
    assertion is INVALID: sys.modules is sticky and this project merges the whole catalog into ONE
    pytest session (ADR-061), so a sibling test importing _claim_git would poison it."""
    v = _vault(tmp_path, "vault", None)
    _prime_candidates(v)
    code = (
        'import sys, json\n'
        f'sys.path.insert(0, r"{_REPO}")\n'
        f'sys.path.insert(0, r"{_CLAIM.parent}")\n'
        'import claim_candidate\n'
        f'rc = claim_candidate.main(["--vault", r"{v}", "--repo-root", r"{tmp_path}",'
        '                            "--candidate", "SC-216", "--name", "do-thing"])\n'
        'print("RESULT", rc, "scripts.lib._claim_git" in sys.modules,'
        '      "scripts.lib._vault_git_sync" in sys.modules)\n'
    )
    env = {**os.environ, **_id_git_env(tmp_path, *A)}
    env.pop("AI_SDLC_CLAIM_BACKEND", None)
    env.pop("AI_SDLC_VAULT_ROOT", None)
    cp = subprocess.run([sys.executable, "-c", code], cwd=str(v), env=env,
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert cp.returncode == 0, cp.stderr
    line = [ln for ln in cp.stdout.splitlines() if ln.startswith("RESULT")][-1]
    assert line == "RESULT 0 False False", f"the unconfigured path must never import the git backend: {line}"


# ══════════════════════════════════════════════════════════════════════════════════════
# CLAIM_CANDIDATE -- consumer wiring (claim / --release / LOST + WON reporting)
# ══════════════════════════════════════════════════════════════════════════════════════

def _run_claim(vault: Path, args, git_env: dict, repo_root: Path, backend: str | None = "git",
               cwd: Path | None = None) -> subprocess.CompletedProcess:
    child = dict(os.environ)
    child.pop("AI_SDLC_VAULT_ROOT", None)
    child.pop("AI_SDLC_CLAIM_BACKEND", None)
    child.update(git_env)
    if backend is not None:
        child["AI_SDLC_CLAIM_BACKEND"] = backend
    return subprocess.run(
        [sys.executable, str(_CLAIM), "--vault", str(vault), "--repo-root", str(repo_root),
         *[str(a) for a in args]],
        cwd=str(cwd or vault), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=child)


def test_claim_candidate_two_copies_one_winner(tmp_path):
    """AC1 (the WIRING leg): two developers with SEPARATE vault working copies against ONE shared
    remote. Exactly one mints; the loser exits 5 naming the winner's git_email and leaves its own
    candidates.json BYTE-UNCHANGED (never a silent duplicate slice)."""
    remote = _bare_remote(tmp_path)
    va, vb = _vault(tmp_path, "vaultA", remote), _vault(tmp_path, "vaultB", remote)
    _prime_candidates(va)
    before_b = _prime_candidates(vb).read_text(encoding="utf-8")

    cp_a = _run_claim(va, ["--candidate", "SC-216", "--name", "do-thing", "--json"],
                      _id_git_env(tmp_path, *A), tmp_path)
    assert cp_a.returncode == 0, cp_a.stderr
    assert json.loads(cp_a.stdout)["slice"] == "slice-001"

    cp_b = _run_claim(vb, ["--candidate", "SC-216", "--name", "steal-it", "--json"],
                      _id_git_env(tmp_path, *B), tmp_path)
    assert cp_b.returncode == 5, (cp_b.returncode, cp_b.stdout, cp_b.stderr)
    assert "a@test" in (cp_b.stdout + cp_b.stderr), "the refusal must name the winning owner"
    assert (vb / "candidates.json").read_text(encoding="utf-8") == before_b, \
        "the loser must mint NOTHING -- its candidates.json is byte-unchanged"


def test_claim_candidate_two_processes_race_the_cli_one_mint(tmp_path):
    """AC1, taken LITERALLY (CR8): two barrier-synced processes racing the real `claim_candidate.py`
    CLI -- not the backend, and not a sequential proxy. This is the only place the COMPOSITION of the
    create gate, the token sidecar publish, the in-lock slice mint and the compensation paths is
    observed under genuine interleaving; exactly one process mints, the other is refused LOST, and
    the two vault copies together hold exactly one slice number."""
    remote = _bare_remote(tmp_path)
    vaults = [("A", _vault(tmp_path, "vaultA", remote)), ("B", _vault(tmp_path, "vaultB", remote))]
    for _tok, v in vaults:
        _prime_candidates(v, "SC-900")

    bdir = tmp_path / "barrier_cli"
    bdir.mkdir()
    procs = [_spawn(_CONSUMER_WORKER, v, "SC-900", tok, bdir, len(vaults)) for tok, v in vaults]
    outs = [p.communicate(timeout=180) for p in procs]
    codes = sorted(_status(p, o, e, "cli race") for (o, e), p in zip(outs, procs))
    assert codes == ["RC0", "RC5"], f"expected exactly one mint and one LOST, got {codes}"

    minted = [json.loads((v / "candidates.json").read_text())["candidates"][0]["slice"]
              for _tok, v in vaults]
    assert sorted(x is None for x in minted) == [False, True], \
        f"exactly one copy may carry a minted slice, got {minted}"


def test_claim_candidate_c2_self_retry_same_machine_mints(tmp_path, monkeypatch, capsys):
    """AC2 leg 1 (C2, TOKEN-EXACT): an owner whose process died AFTER the HELD create but BEFORE the
    local mint re-runs the claim, matches the token PERSISTED locally at HELD-create time against the
    token in the read-back HELD, and is treated as WON -- it proceeds to mint, exactly once."""
    import claim_candidate

    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    _prime_candidates(v)
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "git")
    monkeypatch.delenv("AI_SDLC_VAULT_ROOT", raising=False)
    for k, val in _id_git_env(tmp_path, *A).items():
        monkeypatch.setenv(k, val)
    argv = ["--vault", str(v), "--repo-root", str(tmp_path),
            "--candidate", "SC-216", "--name", "do-thing", "--json"]

    def die(*_a, **_kw):  # a CRASH, not a handled error: BaseException skips every compensation path
        raise KeyboardInterrupt("process killed after the HELD create, before the mint")

    monkeypatch.setattr(claim_candidate, "safe_mutate_text", die)
    with pytest.raises(KeyboardInterrupt):
        claim_candidate.main(argv)
    capsys.readouterr()
    assert _remote_sha(v, "refs/aisdlc-claims/SC-216"), "the HELD must survive the crash"
    assert json.loads((v / "candidates.json").read_text())["candidates"][0]["slice"] is None

    monkeypatch.undo()  # restore safe_mutate_text; re-apply the env the retry needs
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "git")
    monkeypatch.delenv("AI_SDLC_VAULT_ROOT", raising=False)
    for k, val in _id_git_env(tmp_path, *A).items():
        monkeypatch.setenv(k, val)
    assert claim_candidate.main(argv) == 0, "the same-machine self-retry must be WON, not LOST"
    rec = json.loads((v / "candidates.json").read_text())["candidates"][0]
    assert rec["slice"] == "slice-001" and rec["status"] == "spiking"
    assert json.loads((v / "candidates.json").read_text())["counters"]["slice"] == 1, "minted exactly once"


def test_claim_candidate_same_identity_second_copy_refused(tmp_path):
    """AC2 leg 2 (the M9 REGRESSION GUARD -- this leg must FAIL against the pre-fix email-keyed C2
    predicate): the SAME git identity on a DIFFERENT machine (a second vault copy with no persisted
    token for that HELD) is NOT a self-retry. It is refused as LOST and mints nothing -- closing the
    same-identity double-mint on this project's PRIMARY configuration (a solo maintainer)."""
    remote = _bare_remote(tmp_path)
    va, vb = _vault(tmp_path, "vaultA", remote), _vault(tmp_path, "vaultB", remote)
    _prime_candidates(va)
    before_b = _prime_candidates(vb).read_text(encoding="utf-8")
    env = _id_git_env(tmp_path, *A)  # ONE identity, two machines

    assert _run_claim(va, ["--candidate", "SC-216", "--name", "do-thing"], env, tmp_path).returncode == 0
    cp = _run_claim(vb, ["--candidate", "SC-216", "--name", "do-thing"], env, tmp_path)
    assert cp.returncode == 5, (cp.returncode, cp.stdout, cp.stderr)
    assert (vb / "candidates.json").read_text(encoding="utf-8") == before_b, "no duplicate mint"


def test_claim_candidate_unreachable_remote_refuses_fail_closed(tmp_path):
    """AC5: a CONFIGURED-but-unreachable git backend REFUSES fail-closed -- non-zero exit, no slice
    minted, no candidate status changed. It NEVER falls back to a local-only claim that could
    double-pick (the whole reason this slice exists)."""
    v = _vault(tmp_path, "vaultA", tmp_path / "no-such-remote.git")
    before = _prime_candidates(v).read_text(encoding="utf-8")
    cp = _run_claim(v, ["--candidate", "SC-216", "--name", "do-thing"],
                    _id_git_env(tmp_path, *A), tmp_path)
    # CR3: pinned to the EXACT code. The remote IS configured (it just points nowhere), so the push
    # is attempted and the existence oracle cannot answer -> indeterminate (4, "inspect first"),
    # never 3's "the claim did NOT commit; retry it" -- the fate of that push is genuinely unknown.
    assert cp.returncode == 4, (cp.returncode, cp.stdout, cp.stderr)
    assert (v / "candidates.json").read_text(encoding="utf-8") == before, "nothing may change"
    assert "UNVERIFIABLE" in cp.stderr, cp.stderr


def test_claim_candidate_release_unreachable_remote_warns_not_raises(tmp_path):
    """M7 (b): claim_candidate.py suppressed only OSError on the release path, so a leaked
    SyncFailure would crash `--release` with a traceback AFTER candidates.json was already mutated.
    A release against an unreachable remote must exit 0 with a WARNING naming the remote, the ref and
    the literal teardown command -- never a traceback, never a silent swallow."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    _prime_candidates(v)
    env = _id_git_env(tmp_path, *A)
    assert _run_claim(v, ["--candidate", "SC-216", "--name", "do-thing"], env, tmp_path).returncode == 0

    token_before = _token_sidecar(v, "SC-216", A[1])
    assert token_before.is_file(), "the claim must have persisted this copy's C2 token"

    _git(v, "remote", "set-url", "origin", str(tmp_path / "vanished.git"))
    rel = _run_claim(v, ["--candidate", "SC-216", "--release"], env, tmp_path)
    assert rel.returncode == 0, (rel.returncode, rel.stdout, rel.stderr)
    assert "Traceback" not in rel.stderr, rel.stderr
    assert "origin" in rel.stderr and "refs/aisdlc-claims/SC-216" in rel.stderr, rel.stderr
    assert "git push origin :refs/aisdlc-claims/SC-216" in rel.stderr, \
        f"the warning must carry the literal teardown command: {rel.stderr!r}"
    rec = json.loads((v / "candidates.json").read_text())["candidates"][0]
    assert rec["status"] == "candidate", "the local release itself must still have committed"
    # CR7: the HELD survives on the remote, so this copy must KEEP its proof of ownership. Dropping
    # it would leave the machine unable to prove it owns its own orphan, and the next claim would
    # mis-diagnose that orphan as another machine's claim.
    assert token_before.is_file(), \
        "an UNCONFIRMED teardown must not throw away this copy's C2 token"


def test_claim_candidate_timeout_exit_4(tmp_path, monkeypatch, capsys):
    """AC6 leg 3c: a claim-path timeout surfaces to the USER as a fail-closed exit 4 ('inspect the
    shared claim state first'), NOT exit 3's 'the claim did NOT commit; retry it' -- a dropped
    response can leave a real claim on the remote."""
    import claim_candidate

    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    before = _prime_candidates(v).read_text(encoding="utf-8")
    monkeypatch.setenv("AI_SDLC_CLAIM_BACKEND", "git")
    monkeypatch.delenv("AI_SDLC_VAULT_ROOT", raising=False)
    for k, val in _id_git_env(tmp_path, *A).items():
        monkeypatch.setenv(k, val)
    _git_spy(monkeypatch, fail={"push", "ls-remote"})
    rc = claim_candidate.main(["--vault", str(v), "--repo-root", str(tmp_path),
                               "--candidate", "SC-216", "--name", "do-thing"])
    capsys.readouterr()
    assert rc == 4, f"an undecidable timeout must be ambiguous (exit 4), got {rc}"
    assert (v / "candidates.json").read_text(encoding="utf-8") == before


def test_claim_candidate_token_never_reaches_the_vault_working_tree(tmp_path):
    """CR2 (code-review major): the C2 token must be per-WORKING-COPY BY CONSTRUCTION.

    The first design put the sidecar in the vault working tree and leaned on
    `_vault_git_sync._SYNC_IGNORE`'s `*.tmp` rule -- but that rule only reaches `<vault>/.gitignore`
    via `ensure_sync_gitignore`, whose ONLY caller is `sync_push`. On a vault that is git-init'd and
    remoted but not yet pushed -- EXACTLY the state `_require_git_tree`'s own error message leaves the
    user in -- a hand-driven `git add -A && git push` would have carried the token to a peer, and that
    peer would then be admitted as a false self-retry and mint a duplicate: precisely the hole AC2
    leg 2 closes. Proven the strong way: `git status --porcelain` on the vault sees NOTHING, and
    `git add -A` stages nothing, with no .gitignore in play at all."""
    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    _prime_candidates(v)
    assert not (v / ".gitignore").exists(), "the premise: this vault has never been sync-pushed"
    assert _run_claim(v, ["--candidate", "SC-216", "--name", "do-thing"],
                      _id_git_env(tmp_path, *A), tmp_path).returncode == 0

    sidecar = _token_sidecar(v, "SC-216", A[1])
    assert sidecar.is_file(), f"the token must be persisted somewhere: {sidecar}"
    _git(v, "add", "-A")
    staged = [ln for ln in _git(v, "diff", "--cached", "--name-only").stdout.splitlines() if ln.strip()]
    assert not any("TOKEN" in s.upper() or "token" in s for s in staged), \
        f"the C2 token must be untrackable by the vault repo, staged: {staged}"
    tracked = _git(v, "ls-files", "--other", "--cached").stdout
    assert "claim-tokens" not in tracked, f"the token dir must be outside the work tree: {tracked!r}"


def test_transport_old_git_client_refuses_with_the_version_reason(tmp_path, monkeypatch):
    """CR9: the empty-expect `--force-with-lease=<ref>:` form is a CLIENT capability (git >= 2.10,
    Sept 2016). On an older client the push errors with NO ref line, the oracle finds the ref absent,
    and the user is told 'nothing committed; retry it' -- advice that can never work. Probed once at
    lazy setup, refused in words, and classed as a setup fault (indeterminate), not a transient."""
    from scripts.lib import _claim_git

    remote = _bare_remote(tmp_path)
    v = _vault(tmp_path, "vaultA", remote)
    cc, _cg, be = _backend(v)
    real = _claim_git.GitClaimBackend._run_git

    def old_client(self, args, **kw):
        if args and args[0] == "--version":
            return _claim_git._Run(rc=0, out="git version 2.9.5\n", err="", out_bytes=b"")
        return real(self, args, **kw)

    monkeypatch.setattr(_claim_git.GitClaimBackend, "_run_git", old_client)
    res = be.create_if_absent(cc.claim_key("SC-216"), _body("SC-216"))
    assert res.status == cc.UNVERIFIABLE and res.kind == "indeterminate", res
    assert "2.10" in (res.reason or "") and "2.9" in (res.reason or ""), res.reason
    assert _remote_sha(v, "refs/aisdlc-claims/SC-216") is None, "nothing may be pushed on refusal"
    # an unparseable version must NEVER false-block a working install.
    monkeypatch.setattr(_claim_git.GitClaimBackend, "_run_git",
                        lambda self, args, **kw: (_claim_git._Run(rc=0, out="git version wat\n", err="",
                                                                  out_bytes=b"")
                                                  if args and args[0] == "--version"
                                                  else real(self, args, **kw)))
    assert _cg.GitClaimBackend(v).create_if_absent(cc.claim_key("SC-217"),
                                                   _body("SC-217")).status == cc.CREATED


@pytest.fixture(autouse=True)
def _skill_on_path():
    sys.path.insert(0, str(_CLAIM.parent))
    yield


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
