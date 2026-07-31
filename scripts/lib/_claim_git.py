"""_claim_git.py — the GIT-backed shared-claim backend (slice-100 / SC-216 / ADR-131 + ADR-132).

The PRODUCTION backend behind the FROZEN ``ClaimBackend`` seam (``_claim_coord``, ADR-113). Where the
reference ``LocalDirClaimBackend`` mutually-excludes claimants through an ``os.link`` publish on ONE
shared filesystem, this backend does it across MACHINES on a git-hosted vault: one remote ref per
candidate, ``refs/aisdlc-claims/<SC-NNN>``, whose CAS-from-nil create IS the winner-decision.

THE MODEL — a STICKY (write-once) register. Each claimant writes its own value once; the register
keeps the FIRST write and rejects all others; the value each claimant then reads IS the decision. One
write + one read, no lock, no doorway, no agreement round — so a WINNING claim costs EXACTLY ONE
network round-trip (pinned by AC6, not by review).

**Stickiness is MANUFACTURED, never inherent (ADR-131 — load-bearing).** A bare git ref is NOT sticky:
the design spike's control arm pushed a fast-forward CHILD commit over a live claim on real GitHub and
the server ACCEPTED it (rc 0, porcelain flag ``' '``, the winner's HELD silently destroyed). Two
mechanisms manufacture it, both proven on real GitHub AND on a local bare repo, and BOTH are load
bearing — any future parented commit, ``--force`` or ``+refspec`` silently defeats them:

  (a) the claim object is a PARENTLESS ROOT commit — an unrelated root is rejected non-fast-forward
      even with no lease;
  (b) ``--force-with-lease=<ref>:`` (EMPTY expect = "the ref must not exist") — which rejects the
      descendant push (a) cannot.

``remove_if_owner`` is the symmetric server-enforced COMPARE-AND-DELETE
(``--force-with-lease=<ref>:<sha-just-read>``), ABA-free because every register value carries a fresh
uuid4 idempotency token, so a stale ``--release`` can never remove a successor's claim.

CLASSIFICATION IS STRUCTURAL, NEVER PROSE (ADR-132): six distinct reject phrasings were observed
across two spikes and two servers. Decisions key ONLY on the ``--porcelain`` FLAG character and an
EXACT ref-name match (candidate ids are a prefix family — SC-1 / SC-10 / SC-100 — so containment
would over-match). ``_classify_push`` / ``_ref_flag`` are pure and table-tested.

FAIL-CLOSED EVERYWHERE (ADR-132): an unrecognized flag is indeterminate; ``EXISTS`` with an unreadable
body is indeterminate, never a false LOST naming nobody; and "the register was never reached" is a
PROBE RESULT, never an inference — an UNVERIFIED create (rc != 0 with no ref line, or a
``TimeoutExpired``, which produces no rc at all) issues ONE bounded ``ls-remote`` before answering,
because a dropped response can leave a REAL claim on the remote while the user is told nothing
committed. That probe fires ONLY on the failure branch, so it is not the doorway the protocol forbids.

PRECONDITIONS. (1) REMOTE — the claim remote must be a real git service or a bare repo on a LOCAL
filesystem, never a bare repo on an NFS/SMB mount (the mirror of the ``os.link`` weakening at
``_claim_coord.py:119-124``). Linearizability of the ref update is proven on a real git service and a
local-FS bare repo; a network mount is undecided, and cannot be enforced from here. (2) CLIENT — git
>= 2.10 (Sept 2016), which is when the EMPTY-expect ``--force-with-lease=<ref>:`` form landed. That
one IS enforced: it is probed once at lazy setup and refused in words, because an old client fails
with no ref line and would otherwise be reported as a transport error the user is told to retry.

SECURITY — auth is delegated ENTIRELY to git (no credential is ever read, stored or logged), git is
non-interactive on every invocation (``GIT_TERMINAL_PROMPT=0`` + BatchMode SSH from the sibling
engine's ``_sync_env``, PLUS per-invocation ``-c credential.interactive=false -c
credential.guiPrompt=false`` — neither env var suppresses a credential HELPER's prompt, and GCM is the
default helper on Git for Windows), and BOTH git streams are userinfo-REDACTED at the single wrapper
boundary before anything is stored, compared or surfaced (``git push --porcelain`` writes ``To
<remote-url>`` on STDOUT — the stream the classifier parses). Only the remote NAME and the ref are
ever printed.

Leading-underscore module -> auto-excluded from the PMI-1 inventory (like ``_claim_coord`` /
``_vault_git_sync``).
"""
from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/_claim_git.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _claim_coord, _sync_config, _vault_git_sync  # noqa: E402 — after bootstrap

# The claim register's ref namespace — deliberately OUTSIDE refs/heads/* so the default fetch refspec
# never pulls claims down and `sync_push`'s branch push never touches them (ADR-131).
CLAIM_REF_PREFIX = "refs/aisdlc-claims/"
# Read-back scratch: uuid-SCOPED (not per-candidate — two processes racing the SAME candidate on one
# machine is exactly the AC1 scenario) and never FETCH_HEAD (process-global mutable state).
PEEK_REF_PREFIX = "refs/aisdlc-claim-peek/"

# Every claim-path git call is bounded: a hung claim must surface as a refusal, never an indefinite
# block at /slice pick time. 30s, NEVER the sibling engine's 300s bulk-sync default (measured
# real-GitHub push round-trip: median 2.1s, max 4.7s — 30s leaves generous headroom on a slow link).
CLAIM_TIMEOUT = 30

# Leaked-peek-ref sweep grace (CR5): a peek ref younger than this belongs to a live sibling's
# in-flight read-back, not to a killed process, and must never be swept out from under it.
PEEK_SWEEP_GRACE = 300  # seconds

# The EMPTY-expect form `--force-with-lease=<ref>:` — "the ref must not exist", the CAS-from-nil this
# whole protocol rests on — was added by John Keeping's July-2016 series and released in git 2.10
# (Sept 2016). On an older CLIENT the push errors with no ref line, which would otherwise read as a
# transport failure, so the requirement is probed once at lazy setup and refused in words (CR9).
MIN_GIT_VERSION = (2, 10)

# The key shape minted by _claim_coord.claim_key(); the candidate id is re-validated here as
# defense-in-depth (the consumer validates it too) BEFORE any ref path is composed.
_KEY_RE = re.compile(r"^claims/(?P<cand>[^/]+)/HELD\.json$")
_CAND_RE = re.compile(r"^SC-\d+$")

# Per-invocation credential-helper suppression (M-add-2). List-form `-c`, never a persisted config
# write, and never applied to `_vault_git_sync._sync_env` — that is on the shipped bulk-sync path
# where an interactive first-time auth is legitimate.
_NO_PROMPT = ("-c", "credential.interactive=false", "-c", "credential.guiPrompt=false")

# The FULL failure taxonomy that may cross into this module. `_sync_config.SyncConfigError` is a THIRD
# class in neither of the sibling engine's enumerated taxonomies and is raised on the READ path when a
# persisted profile carries a secret-shaped key. No exception may cross the seam (M7).
_BACKEND_ERRORS = (_vault_git_sync.SyncUsageError, _vault_git_sync.SyncFailure,
                   _sync_config.SyncConfigError, OSError, subprocess.SubprocessError, ValueError)

# ── userinfo redaction (M8) ─────────────────────────────────────────────────────────

# A URL carrying userinfo (`scheme://user:pass@host/...`) and its scp-like twin (`user@host:path`).
# The WHOLE match is dropped — replacing only the credential would leave `@host` behind.
_URL_USERINFO = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s/@]*@\S*")
_SCP_USERINFO = re.compile(r"(?<![\w.@\-])[\w.\-]+@[\w.\-]+:[^\s]+")
_REDACTED = "<redacted-url>"


def redact(text: str) -> str:
    """Strip credential-bearing remote URLs from git output. Applied at the single wrapper boundary to
    BOTH streams, so nothing downstream can quote a raw stream by accident."""
    if not text:
        return ""
    return _SCP_USERINFO.sub(_REDACTED, _URL_USERINFO.sub(_REDACTED, text))


# ── the pure porcelain classifier (ADR-132 addition 3 — table-tested) ───────────────

def _ref_flag(stdout: str, ref: str) -> str | None:
    """The ``--porcelain`` status FLAG for exactly ``ref``, or ``None`` when no line names it.

    Porcelain emits ``<flag> TAB <src>:<dst> TAB <summary>``. The rule is exact: skip lines with no
    TAB; split field 1 on the LAST ':' (the src may be a sha or the literal ``(delete)``); compare the
    remainder ``==`` the full ref path — NEVER a containment test, because candidate ids form a prefix
    family (SC-1 in SC-10 in SC-100). Note stdout is NEVER empty on a completed push (a leading
    ``To <url>`` and a trailing ``Done``), so "no ref line" must not be implemented as "stdout is
    empty"."""
    for line in (stdout or "").splitlines():
        line = line.rstrip("\r")
        if "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        if parts[1].rsplit(":", 1)[-1].strip() == ref:
            return parts[0].strip() or " "
    return None


def _classify_push(rc: int | None, stdout: str, ref: str) -> str:
    """``(rc, porcelain stdout, ref) -> won | rejected | transient | indeterminate``.

    ONLY ``*`` (a NEW ref) is a win. ``rejected`` means "our write did not take, go read the register
    back and find out who did" — it covers ``!`` and also ``=``:

      ``=`` (up to date) is reachable ONLY on a C2 self-retry. The register already holds EXACTLY the
      sha we just pushed, and that sha is the hash of a body carrying OUR uuid4 idempotency token — so
      nobody else could have put it there. git short-circuits an up-to-date push BEFORE the lease
      check, so this arrives as rc 0 / ``=`` rather than a reject. It is NOT classified as CREATED
      (ADR-132's rule holds); it routes to the same read-back-and-decide branch, where the token
      compare makes it a WON self-retry — and would still refuse if the body were somehow foreign.

    A space flag is a successful FAST-FORWARD update: with the lease in place it is unreachable, and
    if it is ever reached the lease was bypassed, so it must fail closed. ``transient`` here means
    UNVERIFIED — the caller routes it to the existence oracle, never straight to the user."""
    flag = _ref_flag(stdout, ref)
    if flag is None:
        # rc != 0 with no ref line = the push did not complete -> UNVERIFIED (probe it).
        # rc == 0 with no ref line = git says success but said nothing about our ref -> ambiguous.
        return "transient" if rc != 0 else "indeterminate"
    if flag == "*":
        return "won"
    if flag in ("!", "="):
        return "rejected"
    return "indeterminate"  # ' ', '+', '-' or anything unrecognized -> fail closed


@dataclass(frozen=True)
class _Run:
    """One git invocation. ``out``/``err`` are decoded AND userinfo-redacted; ``out_bytes`` is the RAW
    stdout, used ONLY to read back our own HELD JSON (never porcelain)."""

    rc: int | None
    out: str
    err: str
    out_bytes: bytes
    timed_out: bool = False


class GitClaimBackend(_claim_coord.ClaimBackend):
    """The git implementation of the FROZEN seam. The constructor is INERT — zero subprocess, zero
    network: ``claim_candidate.py:571`` builds a backend on the ``transfer`` path purely to test
    ``is not None`` and catches only ``UnsupportedBackend``, so a constructor that could raise would
    crash ``transfer`` with a traceback. Setup errors surface LAZILY on first use as a fail-closed
    ``UNVERIFIABLE`` carrying the actionable reason."""

    def __init__(self, vault_root: Path | str, *, remote: str | None = None):
        self._vault = Path(vault_root)
        self._remote_name = remote
        self._swept = False

    # ── lazy setup ─────────────────────────────────────────────────────────────────

    def _remote(self) -> str:
        """The claim remote NAME, resolved once. The vault's persisted sync profile supplies it when
        it names a git backend; otherwise (a NON-git backend, or no profile at all — M5c) fall back to
        the sibling engine's resolution, which defaults to 'origin' / the sole remote and REFUSES on
        multi-remote ambiguity rather than silently picking one."""
        if not self._remote_name:
            _vault_git_sync._require_git_tree(self._vault)  # SyncUsageError names the vault path
            self._require_client_version()
            cfg = _sync_config.load()
            arg = None
            if isinstance(cfg, dict) and cfg.get("backend") == "git":
                arg = (cfg.get("git") or {}).get("remote") or None
            self._remote_name = _vault_git_sync._resolve_remote(self._vault, arg)
        return self._remote_name

    def _require_client_version(self) -> None:
        """CR9: the empty-expect lease is a CLIENT capability (git >= 2.10). Probed ONCE, inside the
        one-time lazy setup, with a LOCAL `git --version` — so it costs no network round-trip and
        never touches the AC6 budget. An old client would otherwise fail with no ref line, route to
        the existence oracle, find the ref absent and tell the user 'nothing committed; retry it' —
        advice that can never work. An unparseable version is NOT treated as a failure (a vendor
        build may print anything); the refusal fires only on a version we can read AND that is too
        old, so this can never false-block a working install."""
        probed = self._run_git(["--version"])
        if probed.rc != 0:
            return
        m = re.search(r"\bgit version (\d+)\.(\d+)", probed.out)
        if not m:
            return
        found = (int(m.group(1)), int(m.group(2)))
        if found < MIN_GIT_VERSION:
            raise _vault_git_sync.SyncUsageError(
                f"the git claim backend requires git >= {MIN_GIT_VERSION[0]}.{MIN_GIT_VERSION[1]} on "
                f"the CLIENT (found {found[0]}.{found[1]}): the claim's mutual exclusion is the "
                f"empty-expect `--force-with-lease=<ref>:` form, which older git does not support, so "
                f"claims here would fail in a way indistinguishable from a network error. Upgrade "
                f"git, or unset the claim backend to fall back to uncoordinated local claiming.")

    def _ref_for(self, key: str) -> str:
        m = _KEY_RE.match(key or "")
        cand = m.group("cand") if m else ""
        if not _CAND_RE.match(cand):
            raise ValueError(
                f"claim key {key!r} does not name a ^SC-\\d+$ candidate — refusing to compose a "
                f"claim ref from it")
        return CLAIM_REF_PREFIX + cand

    # ── subprocess git (list-form, binary stdin, bounded, non-interactive, redacted) ─

    def _run_git(self, args: list[str], *, input_bytes: bytes | None = None,
                 env_extra: dict | None = None, timeout: int = CLAIM_TIMEOUT) -> _Run:
        """Run ``git <args>`` in the vault. PRIVATE rather than a widened
        ``_vault_git_sync._git``: that wrapper hardcodes ``text=True`` and takes no ``input=``, and
        widening its signature would mutate a function on the live ``vault_admin sync`` path (m3). A
        ``TimeoutExpired`` is returned as a value (``rc=None``), not raised — the caller must route it
        to the existence oracle, because a timeout produces no rc and can still have committed."""
        env = _vault_git_sync._sync_env()
        if env_extra:
            env.update(env_extra)
        try:
            r = subprocess.run(["git", *_NO_PROMPT, *args], cwd=str(self._vault),
                               input=input_bytes if input_bytes is not None else b"",
                               capture_output=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return _Run(rc=None, out="", out_bytes=b"", timed_out=True,
                        err=f"git {args[0] if args else '?'} timed out after {timeout}s")
        out_b = r.stdout or b""
        return _Run(rc=r.returncode, out=redact(out_b.decode("utf-8", "replace")),
                    err=redact((r.stderr or b"").decode("utf-8", "replace")), out_bytes=out_b)

    # ── the parentless object forge (ADR-131 addition 1) ───────────────────────────

    def _forge(self, cand: str, body: dict) -> str:
        """Forge the claim object: a PARENTLESS root commit whose tree is exactly one entry,
        ``HELD.json``. Authored AND committed as the CLAIMING ACTOR — that is what makes the backend
        independent of the vault repo's own git identity (the sibling engine REFUSES when the vault
        has none) and makes ``git log refs/aisdlc-claims/*`` an attributable collision trail. Binary
        stdin throughout; no index, no working-tree write."""
        actor = body.get("actor") or {}
        name = str(actor.get("git_user") or "").strip()
        email = str(actor.get("git_email") or "").strip()
        if not email:
            raise ValueError("body.actor.git_email is blank — refusing to forge an anonymous claim "
                             "(a claim nobody can be attributed to is worse than no claim)")
        ident = {"GIT_AUTHOR_NAME": name or email, "GIT_AUTHOR_EMAIL": email,
                 "GIT_COMMITTER_NAME": name or email, "GIT_COMMITTER_EMAIL": email}

        payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blob = self._run_git(["hash-object", "-w", "--stdin"], input_bytes=payload)
        blob_sha = blob.out.strip()
        if blob.rc != 0 or not blob_sha:
            raise _vault_git_sync.SyncFailure(f"could not write the claim blob: {blob.err.strip()}")
        entry = f"100644 blob {blob_sha}\tHELD.json\n".encode("utf-8")
        tree = self._run_git(["mktree"], input_bytes=entry)
        tree_sha = tree.out.strip()
        if tree.rc != 0 or not tree_sha:
            raise _vault_git_sync.SyncFailure(f"could not write the claim tree: {tree.err.strip()}")
        commit = self._run_git(["commit-tree", tree_sha, "-m", f"aisdlc claim {cand}"],
                               env_extra=ident)
        commit_sha = commit.out.strip()
        if commit.rc != 0 or not commit_sha:
            raise _vault_git_sync.SyncFailure(f"could not forge the claim commit: {commit.err.strip()}")
        return commit_sha

    def _push_claim(self, ref: str, sha: str) -> _Run:
        """The ONE network round-trip of a winning claim: a lease-guarded create. The EMPTY expect in
        ``--force-with-lease=<ref>:`` is what makes the write itself the decision."""
        return self._run_git(["push", "--porcelain", f"--force-with-lease={ref}:",
                              self._remote(), f"{sha}:{ref}"])

    # ── read-back (uuid-scoped private ref; NEVER FETCH_HEAD) ───────────────────────

    def _sweep_peek_refs(self) -> None:
        """Best-effort sweep of LEAKED read-back refs, on FIRST USE (never in the constructor, which
        must stay inert). A ``finally`` does not survive SIGKILL or a hard tool-timeout, and a leaked
        peek ref permanently ROOTS the fetched claim objects AND survives ``sync_pull --force``, which
        resets only the working tree and never ``refs/``.

        The sweep SKIPS refs younger than ``PEEK_SWEEP_GRACE`` (CR5). The uuid scoping exists so two
        processes racing the SAME candidate on ONE machine — the AC1 scenario — do not clobber each
        other's read-back; an unconditional global sweep would undo exactly that, deleting a sibling's
        in-flight peek ref between its ``fetch`` and its ``cat-file`` and turning a clean exit-5 LOST
        into a spurious exit-4. The age comes from the ref NAME, not from ``%(creatordate)`` — that
        reports the pointed-to CLAIM commit's date, which can be arbitrarily old. A name that predates
        this scheme (no parseable stamp) is swept, as before."""
        if self._swept:
            return
        self._swept = True
        now = int(time.time())
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            listed = self._run_git(["for-each-ref", "--format=%(refname)", PEEK_REF_PREFIX])
            if listed.rc != 0:
                return
            for name in listed.out.split():
                if not name.startswith(PEEK_REF_PREFIX):
                    continue
                stamp = name[len(PEEK_REF_PREFIX):].split("-", 1)[0]
                if stamp.isdigit() and now - int(stamp) < PEEK_SWEEP_GRACE:
                    continue  # a live sibling's read-back is in flight — never touch it
                self._run_git(["update-ref", "-d", name])

    def _fetch_held(self, ref: str) -> tuple[str | None, dict | None]:
        """``(remote sha, HELD body)`` for ``ref``, each ``None`` when absent/unreachable/unreadable.
        Fetches into a per-PROCESS uuid-scoped private ref — never ``FETCH_HEAD`` (process-global
        mutable state two concurrent claims would clobber) and never a remote-tracking ref, so the
        read that follows a rejected write returns THE decided value."""
        self._sweep_peek_refs()
        # <epoch>-<uuid>: the stamp is what lets the sweep tell a LEAKED ref from a live sibling's
        # in-flight read-back (CR5). uuid-scoped, never per-candidate and never FETCH_HEAD.
        tmp = f"{PEEK_REF_PREFIX}{int(time.time())}-{uuid.uuid4().hex}"
        fetched = self._run_git(["-c", "gc.auto=0", "fetch", "--no-tags", self._remote(),
                                 f"+{ref}:{tmp}"])
        if fetched.timed_out or fetched.rc != 0:
            return None, None  # absent on the remote, or unreachable
        try:
            rev = self._run_git(["rev-parse", tmp])
            sha = rev.out.strip() if rev.rc == 0 else None
            blob = self._run_git(["cat-file", "blob", f"{tmp}:HELD.json"])
            if blob.rc != 0:
                return sha, None  # the ref exists but carries no readable HELD.json
            try:
                body = json.loads(blob.out_bytes.decode("utf-8"))  # RAW: never the redacted text
            except (ValueError, UnicodeDecodeError):
                return sha, None
            return sha, (body if isinstance(body, dict) else None)
        finally:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                self._run_git(["update-ref", "-d", tmp])

    @staticmethod
    def _usable(body: dict | None) -> bool:
        """A HELD body is usable only if it can NAME an owner. ``get()`` collapses absent, unreachable
        AND unreadable to ``None`` per the frozen contract, so a body that cannot be attributed must
        never become an EXISTS — that is the false LOST printing 'already claimed by ? <unknown>'."""
        return isinstance(body, dict) and bool(_claim_coord._norm((body.get("actor") or {}).get("git_email")))

    # ── the frozen seam ────────────────────────────────────────────────────────────

    def create_if_absent(self, key: str, body: dict) -> _claim_coord.ClaimResult:
        try:
            return self._create(key, body)
        except _BACKEND_ERRORS as exc:
            return self._refuse(exc)

    def get(self, key: str) -> dict | None:
        try:
            ref = self._ref_for(key)
            _sha, body = self._fetch_held(ref)
            return body if isinstance(body, dict) else None
        except _BACKEND_ERRORS:
            return None  # absent OR unreachable OR unreadable -> None (the frozen contract)

    def remove_if_owner(self, key: str, owner_email: str) -> bool:
        """Server-enforced COMPARE-AND-DELETE. NEVER raises: ``claim_candidate.py`` calls this on the
        ``--release`` path AFTER candidates.json has already been mutated, so a leaked exception would
        crash a release that had, in fact, succeeded locally."""
        ref = remote = None
        try:
            ref = self._ref_for(key)
            remote = self._remote()
            sha, body = self._fetch_held(ref)
            if not sha or not self._usable(body):
                # A failed read-back is AMBIGUOUS: genuinely gone (a clean idempotent no-op) or the
                # register was unreachable (a HELD left standing that will block this candidate for
                # every peer). Distinguish STRUCTURALLY -- by probe rc, never by parsing git prose.
                probe = self._run_git(["ls-remote", remote, ref])
                if probe.timed_out or probe.rc != 0:
                    self._warn_orphan(remote, ref, probe.err or "the claim register is unreachable")
                elif probe.out.strip():
                    self._warn_orphan(remote, ref, "the claim is present but its body is unreadable, "
                                                   "so ownership could not be verified")
                return False  # already gone / unverifiable -> no-op refusal, never an exception
            held_email = (body.get("actor") or {}).get("git_email")
            if _claim_coord._norm(held_email) != _claim_coord._norm(owner_email):
                return False  # foreign owner -> refuse (a stale --release cannot clobber a successor)
            # the lease pins the EXACT sha just read, so a claim released-and-reclaimed in between
            # (a different uuid4 token => a different value => a different sha) is NOT deleted (ABA).
            pushed = self._run_git(["push", "--porcelain", f"--force-with-lease={ref}:{sha}",
                                    remote, f":{ref}"])
            flag = None if pushed.timed_out else _ref_flag(pushed.out, ref)
            if flag == "-":
                return True
            if flag == "!":
                return False  # a successor claim, or already gone (spike arm g) -> not an error
            self._warn_orphan(remote, ref, pushed.err or pushed.out)
            return False
        except _BACKEND_ERRORS as exc:
            self._warn_orphan(remote, ref, str(exc))
            return False

    # ── decision helpers ───────────────────────────────────────────────────────────

    def _create(self, key: str, body: dict) -> _claim_coord.ClaimResult:
        ref = self._ref_for(key)
        remote = self._remote()
        cand = ref[len(CLAIM_REF_PREFIX):]
        sha = self._forge(cand, body)
        pushed = self._push_claim(ref, sha)
        verdict = "transient" if pushed.timed_out else _classify_push(pushed.rc, pushed.out, ref)

        if verdict == "won":
            return _claim_coord.ClaimResult(status=_claim_coord.CREATED, body=body)
        if verdict == "rejected":
            return self._decide_rejected(ref, remote)
        if verdict == "transient":
            return self._probe_unverified(ref, remote, sha, body, pushed)
        return self._indeterminate(
            remote, ref,
            f"the push completed but its porcelain status for {ref} is unrecognized "
            f"(rc={pushed.rc}) — refusing to guess whether the claim committed")

    def _decide_rejected(self, ref: str, remote: str) -> _claim_coord.ClaimResult:
        """A `!` reject means our write did not take — someone else's did, OR the server declined it.
        Read the register back to find out WHICH. One retry, then fail closed: this mirrors
        ``LocalDirClaimBackend``'s shipped one-retry-then-indeterminate shape (``_claim_coord.py``
        :157-168) so the two backends stay behaviourally twinned (CC-001)."""
        _sha, body = self._fetch_held(ref)
        if not self._usable(body):
            _sha, body = self._fetch_held(ref)  # one retry (a release may have raced the read-back)
        if not self._usable(body):
            return self._indeterminate(
                remote, ref,
                "the push was REJECTED but the claim read-back is absent, unreachable or unreadable "
                "(no attributable owner) — this is either a concurrent claim that has since been "
                "released, or PERMISSION DENIED pushing to the claim namespace. Refusing to name an "
                "owner it cannot read")
        self._warn_register(remote, ref)
        return _claim_coord.ClaimResult(status=_claim_coord.EXISTS, body=body)

    def _probe_unverified(self, ref: str, remote: str, our_sha: str, body: dict,
                          pushed: _Run) -> _claim_coord.ClaimResult:
        """THE EXISTENCE ORACLE (ADR-132 addition 1). 'The register was never reached' must be a PROBE
        RESULT, never an inference: a connection dropped AFTER receive-pack committed the ref, and a
        ``TimeoutExpired`` (no rc at all), both leave a REAL claim on the remote while the consumer
        would print 'the claim did NOT commit; retry it'. ONE bounded ``ls-remote`` decides. It fires
        ONLY here — never before a create — so the happy path keeps its one-round-trip budget."""
        probe = self._run_git(["ls-remote", remote, ref])
        if probe.timed_out or probe.rc != 0:
            detail = (pushed.err or "").strip() or "no ref status"
            return self._indeterminate(
                remote, ref,
                f"the claim push did not report a result ({detail}) and the existence probe could "
                f"not answer either — the claim MAY have committed. Inspect it with "
                f"`git ls-remote {remote} {ref}` before retrying")
        listed = probe.out.strip()
        if not listed:
            return _claim_coord.ClaimResult(
                status=_claim_coord.UNVERIFIABLE, kind="transient",
                reason=f"the claim push failed and the register was PROVEN not to carry {ref} "
                       f"(probed on remote {remote!r}) — nothing committed; retry is safe")
        remote_sha = listed.split()[0]
        if remote_sha == our_sha:
            # our own object is on the register: the push DID commit before the connection dropped.
            return _claim_coord.ClaimResult(status=_claim_coord.CREATED, body=body)
        _sha, held = self._fetch_held(ref)
        if not self._usable(held):
            return self._indeterminate(
                remote, ref,
                "the claim push did not report a result and the register carries a FOREIGN claim "
                "whose body is unreadable — refusing to decide WON/LOST")
        self._warn_register(remote, ref)
        return _claim_coord.ClaimResult(status=_claim_coord.EXISTS, body=held)

    # ── refusals + operator-facing diagnostics ─────────────────────────────────────

    def _indeterminate(self, remote: str | None, ref: str | None,
                       why: str) -> _claim_coord.ClaimResult:
        where = f" [register: remote {remote!r}, ref {ref}]" if remote and ref else ""
        return _claim_coord.ClaimResult(status=_claim_coord.UNVERIFIABLE, kind="indeterminate",
                                        reason=why + where)

    def _refuse(self, exc: BaseException) -> _claim_coord.ClaimResult:
        """Setup / transport failures surface LAZILY here rather than from the constructor.

        The split is by WHETHER A RETRY CAN WORK, not merely by whether anything committed (CR3).
        The design's error_model reserves ``transient`` (exit 3, "the claim did NOT commit; retry
        it") for a register that was PROVEN unreached — and a proof only ever comes from the
        failure-branch probe. A ``SyncUsageError`` (vault not a git work tree, no remote, ambiguous
        remote), a ``SyncConfigError`` (a sync profile carrying a secret-shaped key) and a malformed
        key are CONFIGURATION faults: nothing committed, but an identical retry fails identically, so
        telling the user to retry sends them into a loop. They are refused as ``indeterminate``
        (exit 4, "do not retry blindly") with a reason that says so in words. Genuine subprocess /
        OS failures keep ``transient``."""
        setup_fault = isinstance(exc, (ValueError, _vault_git_sync.SyncUsageError,
                                       _sync_config.SyncConfigError))
        if setup_fault:
            return _claim_coord.ClaimResult(
                status=_claim_coord.UNVERIFIABLE, kind="indeterminate",
                reason=f"{redact(str(exc))} [this is a CONFIGURATION error, not a transient one — "
                       f"nothing was written to the claim register, and retrying unchanged will fail "
                       f"identically; fix the setup above, or unset the claim backend]")
        return _claim_coord.ClaimResult(status=_claim_coord.UNVERIFIABLE, kind="transient",
                                        reason=redact(str(exc)))

    def _warn_register(self, remote: str, ref: str) -> None:
        """m6: a LOST must be attributable to a REGISTER, not merely to an owner — on a shared vault a
        bare 'claimed by <email>' cannot distinguish a colleague's live claim from a stale claim on a
        register you did not expect to be reading (the divergent-remote / asymmetric-opt-in cases)."""
        sys.stderr.write(
            f"_claim_git: shared claim register consulted: remote {remote!r}, ref {ref} — the "
            f"decision below comes from THAT register (a peer pointing at a different remote holds a "
            f"DIFFERENT register).\n")

    def _warn_orphan(self, remote: str | None, ref: str | None, detail: str) -> None:
        """A teardown that could not reach the register leaves an IMMORTAL claim blocking that
        candidate for the whole team — the difference between a recoverable failure and a permanent
        lockout — so it is announced with the exact recovery command, never swallowed."""
        if not ref:
            sys.stderr.write(f"_claim_git: WARNING — claim teardown failed: {redact(detail).strip()}\n")
            return
        name = remote or "<remote>"
        sys.stderr.write(
            f"_claim_git: WARNING — could NOT tear down the shared claim {ref} on remote {name!r} "
            f"({redact(detail).strip() or 'transport failure'}). The claim is still HELD and will "
            f"block this candidate for every peer until it is removed by hand: "
            f"`git push {name} :{ref}`\n")
