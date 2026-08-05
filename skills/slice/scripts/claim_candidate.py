"""claim_candidate.py — claim a slice candidate for /slice (v2; slice-019 claim-first rework).

Single-skill tool for `/slice` Step 5. CLAIM-FIRST (slice-019 / [[ADR-013]]): the slice NUMBER is
MINTED here, inside the claim's locked read-modify-write, NOT pre-decided by markdown `max+1`. In ONE
SVW-1 locked mutate (`_vault_write.safe_mutate_text`) it: bumps the monotonic ``counters.slice``,
allocates ``slice-NNN`` via ``id_allocator``, marks the candidate ``status=spiking`` /
``progress=spike`` / ``claimed_by`` / ``started_at`` / ``slice``, appends a ``picked`` history event +
a ``pick_log`` entry — then RETURNS the minted ``slice-NNN`` + the full folder name ``slice-NNN-<name>``
so /slice builds the worktree from it (reserve-then-scaffold). A parallel `/slice` can never lose-update
the shared candidates.json NOR collide on a slice number (R-32; proven by spike-lock-serialization).

``--release SC-NNN`` is the saga COMPENSATION: if the worktree create fails AFTER the vault claim
committed, /slice's failure path calls it to revert the candidate to ``status=candidate`` (no orphaned
reservation). Idempotent (a re-release of an already-released candidate succeeds), identity-checked
(refuses to release a claim owned by a different git identity), and MONOTONIC-BURN (the counter is
never decremented — a skipped slice number is harmless; reusing it would re-introduce a race).

Vault root: `--vault ROOT` overrides `$AI_SDLC_VAULT_ROOT` / the computed default.

Shared-vault claim coordination (slice-091 / SC-198 / ADR-113): when an OPT-IN shared-remote backend is
configured (``$AI_SDLC_CLAIM_BACKEND`` / ``<git-common-dir>/aisdlc/claim-backend``), a durable CLAIM gates
its entry on ``_claim_coord``'s single-key create-if-absent so two developers picking concurrently on a
shared vault can never both mint the same slice. The DEFAULT (unconfigured) local vault is UNCHANGED and
byte-identical — no backend probe subprocess, no added latency. Fail-closed: a configured-but-unreachable
backend REFUSES rather than silently local-minting a duplicate. ``--release`` on the configured path ALSO
tears down the shared HELD (compare-and-delete) so a failed worktree-create can never orphan it.

C2 (the crash-before-mint self-retry) is TOKEN-EXACT (slice-100 / SC-216 / [[ADR-131]] addition 2): the
idempotency token is PERSISTED machine-locally at HELD-create time (per-OWNER, under the VAULT REPO'S OWN
GIT DIR — ``<vault-git-common-dir>/aisdlc/claim-tokens/`` — which git never tracks, so it can never travel
to a peer) and compared EXACTLY against the read-back HELD. Keying C2 on
``actor.git_email`` instead — with the token re-minted per invocation, so it could never match — silently
ADMITTED the SAME git identity on a SECOND machine as a winner, minting a duplicate slice from that
machine's un-pulled candidates.json. On a solo-maintainer project that is the PRIMARY configuration.

Exit 0 success, 1 runtime error (identity unset / git unavailable / candidate not found / not pickable /
malformed file / write failure / cross-identity release), 2 usage error (bad ``--name`` / ``--candidate``
shape). Configured-coordination exits: 3 retryable (backend unreachable/transient — fail-closed), 4
ambiguous (indeterminate read-back — fail-closed), 5 ownership (a foreign developer won the concurrent pick).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _claim_coord, _stdout, id_allocator
from scripts.lib._git_default_branch import run_git
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text
from scripts.lib.slice_ownership import EXIT_OWNERSHIP  # 5 — a foreign hold refuses (shared claim collision)

_JSON_DUMP = {"indent": 2, "ensure_ascii": False, "sort_keys": False}
_PICKABLE = {"candidate", "deferred"}
# A slice NAME is the verb-object folder suffix (e.g. `fix-thumbnail-orientation`): lowercase
# tokens joined by single hyphens. The NUMBER is no longer a caller input — it is minted here.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A candidate id shape — validated on the CONFIGURED branch BEFORE composing any shared claims/ path
# (m3 / ADR-113); the default (unconfigured) path leaves candidate-id handling byte-identical.
_CAND_RE = re.compile(r"^SC-\d+$")
# slice-096 / m4: `transfer --slice` must be the EXACT stored zero-padded 3-digit form (matching
# owner_of's exact string compare at slice_ownership.py:119) — an unpadded `slice-96` is a usage
# refusal that echoes the expected shape, never a silent false 'unknown slice' first-match.
_SLICE_RE = re.compile(r"^slice-\d{3,}$")
# Configured-coordination exit codes (aligned with slice_ownership's taxonomy — m1 / ADR-113):
# 5 ownership (foreign hold), 3 retryable (transient/unreachable), 4 ambiguous (indeterminate read-back).
_EXIT_RETRYABLE = 3
_EXIT_AMBIGUOUS = 4


def _norm_email(value: object) -> str:
    """Stripped + casefolded email compare (a case-different address is the same owner)."""
    return value.strip().casefold() if isinstance(value, str) else ""


# The C2 self-retry key (slice-100 / M9 / [[ADR-131]] addition 2). The idempotency token is PERSISTED
# machine-locally, so a crash-and-retry can compare it EXACTLY.
#
# LOCATION (code-review CR2). The token must be per-WORKING-COPY by construction — if it can ever
# reach a peer, that peer is admitted as a false self-retry and mints a duplicate slice, re-opening
# precisely the hole AC2 leg 2 closes. It therefore lives under the VAULT REPO'S OWN GIT DIR, which
# git never tracks, rather than in the vault working tree behind a `.gitignore` line. The first design
# leaned on `_vault_git_sync._SYNC_IGNORE`'s `*.tmp` entry — but that entry only reaches
# `<vault>/.gitignore` via `ensure_sync_gitignore`, whose ONLY caller is `sync_push`. On a vault that
# has been git-init'd and remoted but not yet pushed (exactly the state `_require_git_tree`'s own
# instructions leave you in) a hand-driven `git add -A && git push` would have carried the token.
# A vault that is NOT a git work tree has no sync path at all, so the vault-root fallback is safe.
#
# NAME: keyed by a digest of the OWNER's email. On a SHARED-filesystem vault (the LocalDir backend's
# whole scenario) two developers share one directory, so a single per-candidate name would let the
# second READ the first's token and be admitted as a false self-retry, and would CLOBBER the first's
# C2 evidence on write. A digest, not the address, keeps a colleague's email out of a shared listing.
_TOKEN_DIR_REL = "aisdlc/claim-tokens"
_TOKEN_FALLBACK_REL = ".aisdlc-claim-tokens"
_TOKEN_SIDECAR = "{}-{}.token"


def _token_dir(vault_root: Path) -> Path:
    """The claim-token directory for this vault WORKING COPY: the vault repo's own git common-dir
    (never tracked, never synced, and identical across the vault's own worktrees), else the vault root
    itself when the vault is not a git work tree (nothing can sync it, so nothing can carry it)."""
    try:
        r = run_git(Path(vault_root), "rev-parse", "--git-common-dir")
        raw = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, ValueError):  # git unavailable -> fall back rather than fail the claim here
        raw = ""
    if not raw:
        return Path(vault_root) / _TOKEN_FALLBACK_REL
    common = Path(raw)
    if not common.is_absolute():  # git prints a vault-relative path (e.g. `.git`) for a plain repo
        common = Path(vault_root) / common
    return common / _TOKEN_DIR_REL


def _token_path(vault_root: Path, candidate_id: str, git_email: str) -> Path:
    """This OWNER's token sidecar for a candidate. Only ever composed AFTER the caller validated the
    candidate id against ``_CAND_RE`` (the same guard that gates ``claim_key``)."""
    digest = hashlib.sha256(_norm_email(git_email).encode("utf-8")).hexdigest()[:16]
    return _token_dir(vault_root) / _TOKEN_SIDECAR.format(candidate_id, digest)


def _own_token(vault_root: Path, candidate_id: str, git_email: str) -> str:
    """This working copy's stable claim token for ``candidate_id`` — read if already persisted, else
    minted and published NOW, BEFORE the create is attempted.

    Stability across invocations is what makes C2 work: a retry after a crash must present the SAME
    token the HELD carries. Re-minting per invocation (the pre-fix behaviour) made a token-keyed C2
    unable to match cross-process, which is why the shipped predicate fell back to actor.git_email —
    and an email-keyed predicate silently ADMITS the same identity on a SECOND machine and mints a
    duplicate slice from that machine's un-pulled candidates.json.

    The publish is an atomic no-clobber ``os.link`` (CR6), mirroring ``LocalDirClaimBackend``: two
    concurrent claims by the SAME owner (parallel slices are routine here) both find no sidecar and
    both mint, and a plain write would let the loser's token REPLACE the token the winner just put on
    the register — after which the winner's own crash-retry would be refused as LOST. The FIRST token
    published wins, and both processes then use it.

    Uniqueness of the REGISTER VALUE (the ABA property) is preserved because the sidecar is dropped
    when this actor's HELD is CONFIRMED torn down, so a released-then-reclaimed candidate presents a
    fresh token. A sidecar that cannot be published is fail-VISIBLE: a claim whose C2 evidence was
    silently lost would strand its own owner on the next retry."""
    path = _token_path(vault_root, candidate_id, git_email)
    existing = _read_token(path)
    if existing:
        return existing
    token = uuid.uuid4().hex
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{token}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(token + "\n", encoding="utf-8")
        try:
            os.link(str(tmp), str(path))  # raises FileExistsError iff a sibling published first
        except FileExistsError:
            published = _read_token(path)
            if published:
                return published  # a concurrent sibling won: adopt ITS token, never replace it
    except OSError as exc:
        raise _ClaimError(
            f"could not persist the claim idempotency token at {path} ({exc}) — refusing to claim "
            f"without it: a crash before the mint would leave a HELD this machine could no longer "
            f"prove it owns (fail-visible per R-7)") from exc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    return token


def _read_token(path: Path) -> str:
    with contextlib.suppress(OSError):
        return path.read_text(encoding="utf-8").strip()
    return ""


def _drop_own_token(vault_root: Path, candidate_id: str, git_email: str) -> None:
    """Forget this working copy's token once its HELD is CONFIRMED torn down, so the next claim of the
    same candidate presents a NEW register value (ABA-freedom).

    Only ever called on a CONFIRMED teardown (CR7): dropping it after a teardown that could not reach
    the register would throw away this machine's proof of ownership while the HELD survives remotely,
    and the next claim would then mis-diagnose its own orphan as another machine's claim."""
    with contextlib.suppress(OSError):
        _token_path(vault_root, candidate_id, git_email).unlink()


def _coordinate_claim(backend, candidate_id: str, git_name: str, git_email: str,
                      ts: str, vault_root: Path) -> tuple[int | None, bool]:
    """CONFIGURED-branch claim gate. Returns ``(exit_code_or_None, created_held)``: ``None`` proceeds to
    the existing in-lock mint (CREATED, or a TOKEN-EXACT EXISTS self-retry — C2), a non-None int
    short-circuits fail-closed. ``created_held`` is True only when THIS call minted a fresh HELD (so a
    later mint failure can compensate it — M-add-1)."""
    token = _own_token(vault_root, candidate_id, git_email)  # persisted BEFORE the create -- a crash keeps it
    body = {"candidate": candidate_id,
            "actor": {"git_user": git_name, "git_email": git_email},
            "idempotency_token": token, "at": ts}
    res = backend.create_if_absent(_claim_coord.claim_key(candidate_id), body)
    if res.status == _claim_coord.CREATED:
        return None, True  # won the atomic create -> proceed to mint
    if res.status == _claim_coord.EXISTS:
        owner = ((res.body or {}).get("actor") or {}).get("git_email")
        held_token = str((res.body or {}).get("idempotency_token") or "").strip()
        if held_token and held_token == token:
            # C2 self-retry: THIS machine created that HELD and died before minting -> WON.
            return None, False
        who = ((res.body or {}).get("actor") or {}).get("git_user") or "?"
        same_identity = (
            "\n  NOTE: the holder's git identity is YOURS, but this working copy has no persisted "
            "token for that claim. Either it was created from a DIFFERENT working copy (another "
            "machine, or a second vault clone), OR this copy's token was dropped by a `--release` "
            "whose REMOTE teardown failed (that release printed a WARNING carrying the manual "
            "`git push <remote> :<ref>` command). Minting here would duplicate the slice from this "
            "copy's un-pulled candidates.json, so it is refused either way: release it from the "
            "machine that holds it, run that manual teardown, or pull that copy's state."
            if _norm_email(owner) == _norm_email(git_email) else "")
        sys.stderr.write(
            f"claim_candidate: SLICE-CLAIM-REFUSED: {candidate_id} is already claimed by {who} "
            f"<{owner or 'unknown'}> on the shared vault — you are {git_name} <{git_email}>. This is a "
            f"collision guard: another developer won the concurrent pick. Coordinate with the owner, or "
            f"pick a different candidate.{same_identity}\n")
        return EXIT_OWNERSHIP, False
    # UNVERIFIABLE -> fail closed (never a silent local mint that could double-pick).
    if res.kind == "transient":
        sys.stderr.write(
            f"claim_candidate: claim UNVERIFIABLE ({res.reason or 'backend unreachable/transient'}) — "
            f"refusing fail-closed (retryable). The claim did NOT commit; retry it.\n")
        return _EXIT_RETRYABLE, False
    sys.stderr.write(
        f"claim_candidate: claim UNVERIFIABLE ({res.reason or 'indeterminate read-back'}) — refusing "
        f"fail-closed (ambiguous). Do NOT retry blindly; inspect the shared claim state first.\n")
    return _EXIT_AMBIGUOUS, False


class _ClaimError(RuntimeError):
    """Fail-visible claim failure → CLI exit 1."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _git_identity(repo_root: Path) -> tuple[str, str]:
    """Resolve ``git config user.name`` / ``user.email`` at ``repo_root``.

    Fail-VISIBLE: an empty name or email, or an unavailable git binary, raises ``_ClaimError``
    (exit 1) — a claim/release must never record (or compare against) an anonymous owner."""
    try:
        name = run_git(repo_root, "config", "user.name").stdout.strip()
        email = run_git(repo_root, "config", "user.email").stdout.strip()
    except FileNotFoundError as exc:  # git binary not on PATH
        raise _ClaimError(f"git binary unavailable: {exc}") from exc
    if not name or not email:
        raise _ClaimError(
            "git identity is not set (user.name / user.email empty) — set it before claiming: "
            '`git config user.name "..."` and `git config user.email "..."`'
        )
    return name, email


def _external_slice_max(vault: Path) -> int:
    """Floor for the slice counter from sources OUTSIDE candidates.json (read-only; a floor only,
    these grow on ship via a different flow): archive/candidates.json + the slices/ and
    slices/archive/ folder names. Tolerant of a missing/malformed archive (returns what it can)."""
    mx = 0
    ap = vault / "archive" / "candidates.json"
    if ap.exists():
        try:
            d = json.loads(ap.read_text(encoding="utf-8") or "{}")
            if isinstance(d, dict):
                mx = max(mx, id_allocator.scan_max(
                    [c.get("slice") for c in d.get("candidates", []) if isinstance(c, dict)], "slice"))
                mx = max(mx, id_allocator.scan_max(
                    [e.get("slice") for e in d.get("pick_log", []) if isinstance(e, dict)], "slice"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    for sub in ("slices", "slices/archive"):
        dd = vault / sub
        if dd.is_dir():
            try:
                mx = max(mx, id_allocator.scan_max(
                    [p.name for p in dd.iterdir() if p.is_dir()], "slice"))
            except OSError:
                pass
    return mx


def _make_claim_mutate(path: Path, candidate_id: str, name: str,
                       git_name: str, git_email: str, ts: str,
                       vault: Path, result: dict):
    """SVW-1 mutate (current JSON text -> new JSON text). Mints the slice number IN-LOCK and
    stashes the minted ``slice``/``folder`` into ``result`` for the caller to print. The
    external floor (``_external_slice_max``) is also computed INSIDE the locked mutate (and
    recomputed on a CAS retry), so an archive-move racing the claim cannot slip a folder past
    the scan — the pre-lock window that used to exist is gone."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _ClaimError(
                f"{path} is empty or missing — no candidates to claim "
                f"(run /discover or /slice-candidates first)")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ClaimError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise _ClaimError(f"{path} top-level is not a JSON object")
        cands = data.get("candidates")
        if not isinstance(cands, list):
            raise _ClaimError(f"{path} has no candidates[] array")

        rec = next((c for c in cands if isinstance(c, dict) and str(c.get("id")) == candidate_id), None)
        if rec is None:
            raise _ClaimError(f"no candidate with id {candidate_id!r} in the live backlog")
        st = rec.get("status")
        # slice-027 / M2 / ADR-016 -- the CONFIRM phase of the two-phase claim. A SAME-OWNER
        # `reserved` hold upgrades to a durable claim (it mints slice-NNN below); a reservation held
        # by a DIFFERENT git identity is refused HERE -- BEFORE id_allocator.next_id -- so a
        # cross-owner upgrade never mints a number (or bumps the counter) against another owner's
        # hold (no IDOR). Fresh {candidate, deferred} picks stay claimable by anyone (unchanged);
        # any other status (spiking/active/blocked/...) is refused.
        if st == "reserved":
            owner = (rec.get("claimed_by") or {}).get("git_email")
            if owner and owner != git_email:
                raise _ClaimError(
                    f"candidate {candidate_id} is reserved by {owner}, not you ({git_email}) -- "
                    f"cannot upgrade another owner's reservation")
        elif st not in _PICKABLE:
            who = (rec.get("claimed_by") or {}).get("git_user")
            raise _ClaimError(
                f"candidate {candidate_id} is not pickable (status={st!r}"
                + (f", claimed_by {who}" if who else "")
                + ") -- it is already in-flight, blocked, or shipped")

        # Mint the slice number IN-LOCK (claim-first). seed_max = max(external floor, in-data
        # slice fields + pick_log) so the counter never re-issues a live OR archived number.
        # The external floor is scanned here, under the lock, not pre-computed by the caller.
        in_data = id_allocator.scan_max(
            [c.get("slice") for c in cands if isinstance(c, dict)], "slice")
        in_data = max(in_data, id_allocator.scan_max(
            [e.get("slice") for e in data.get("pick_log", []) if isinstance(e, dict)], "slice"))
        slice_short = id_allocator.next_id(
            data, "slice", seed_max=max(_external_slice_max(vault), in_data))
        folder = f"{slice_short}-{name}"

        rec["status"] = "spiking"
        rec["progress"] = "spike"
        rec["slice"] = slice_short
        rec["claimed_by"] = {"git_user": git_name, "git_email": git_email}
        rec["started_at"] = ts
        # BB-20: a present-but-non-list history/pick_log must never silently drop the audit entry.
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        hist.append({"event": "picked", "by": "slice", "at": ts, "ref": slice_short})

        plog = data.get("pick_log")
        if not isinstance(plog, list):
            plog = data["pick_log"] = []
        plog.append({"candidate": candidate_id, "slice": slice_short,
                     "picked_by": f"{git_name} {git_email}", "at": ts})
        data["updated"] = ts

        result["slice"] = slice_short
        result["folder"] = folder
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _make_reserve_mutate(path: Path, candidate_id: str, git_name: str, git_email: str,
                         ts: str, result: dict):
    """SVW-1 mutate for the RESERVE (soft HOLD) phase (slice-027 / M2 / M4 / ADR-016).

    Marks a candidate claimed-in-intent the instant the user settles the pick, so a parallel
    /slice sees it as in-flight BEFORE the interactive define window -- WITHOUT minting a slice
    number or bumping any counter (the scarce serial is issued only at the CONFIRM/claim phase).
    Branch ORDER is load-bearing (M4): the same-owner idempotent no-op is checked BEFORE the
    reservable-status gate, else re-reserving an already-`reserved` candidate would wrongly fail
    'not reservable'. Identity-checked (a cross-owner reserve is refused) and fail-visible."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _ClaimError(
                f"{path} is empty or missing -- no candidates to reserve "
                f"(run /discover or /slice-candidates first)")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ClaimError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise _ClaimError(f"{path} top-level is not a JSON object")
        cands = data.get("candidates")
        if not isinstance(cands, list):
            raise _ClaimError(f"{path} has no candidates[] array")

        rec = next((c for c in cands if isinstance(c, dict) and str(c.get("id")) == candidate_id), None)
        if rec is None:
            raise _ClaimError(f"no candidate with id {candidate_id!r} in the live backlog")

        st = rec.get("status")
        # M4 -- check the same-owner idempotent no-op FIRST. A `reserved` candidate is NOT in
        # _PICKABLE, so the reservable-status gate below would otherwise reject a legitimate
        # re-reserve. A reservation held by a different identity is refused (cross-owner).
        if st == "reserved":
            owner = (rec.get("claimed_by") or {}).get("git_email")
            if owner and owner != git_email:
                raise _ClaimError(
                    f"candidate {candidate_id} is reserved by {owner}, not you ({git_email}) -- "
                    f"cannot re-reserve another owner's hold")
            result["reserved"] = False  # idempotent: already reserved by this owner -> no-op success
            return json.dumps(data, **_JSON_DUMP) + "\n"
        if st not in _PICKABLE:
            who = (rec.get("claimed_by") or {}).get("git_user")
            raise _ClaimError(
                f"candidate {candidate_id} is not reservable (status={st!r}"
                + (f", claimed_by {who}" if who else "")
                + ") -- only a `candidate`/`deferred` candidate can be reserved")

        rec["status"] = "reserved"
        rec["progress"] = "reserved"   # M-add-1: a dedicated pre-spike stage so candidates_top renders coherently
        rec["claimed_by"] = {"git_user": git_name, "git_email": git_email}
        rec["started_at"] = ts
        # NO slice number minted and NO counter bumped -- the scarce serial is issued only at CONFIRM.
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        hist.append({"event": "reserved", "by": "slice", "at": ts})
        data["updated"] = ts
        result["reserved"] = True
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _make_release_mutate(path: Path, candidate_id: str, git_email: str, ts: str, result: dict):
    """SVW-1 mutate for the saga compensation (--release). Idempotent + identity-checked +
    monotonic-burn (the counter is NOT decremented)."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _ClaimError(f"{path} is empty or missing — nothing to release")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ClaimError(f"{path} is not valid JSON: {exc}") from exc
        cands = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(cands, list):
            raise _ClaimError(f"{path} has no candidates[] array")
        rec = next((c for c in cands if isinstance(c, dict) and str(c.get("id")) == candidate_id), None)
        if rec is None:
            raise _ClaimError(f"no candidate with id {candidate_id!r} to release")

        if rec.get("status") in _PICKABLE:
            result["released"] = False  # idempotent: already un-claimed, no-op success
            return json.dumps(data, **_JSON_DUMP) + "\n"

        owner = (rec.get("claimed_by") or {}).get("git_email")
        if owner and owner != git_email:
            raise _ClaimError(
                f"refusing to release {candidate_id}: it is claimed by {owner}, not you ({git_email})")

        prior_status = rec.get("status")
        rec["status"] = "candidate"
        rec["progress"] = "not-started"
        rec["slice"] = None
        rec["claimed_by"] = None
        rec["started_at"] = None
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        # slice-027 (code-review m1): the recorded reason depends on WHAT was reverted. A `reserved`
        # soft HOLD never minted a slice number nor created a worktree, so the post-claim
        # saga-compensation text would be a FALSE audit record for a reservation-abandon -- pick the
        # reservation reason for it; keep the original wording for the post-claim compensation path.
        reason = ("reservation abandoned before the claim (pre-claim soft HOLD released; "
                  "no slice number was minted)" if prior_status == "reserved"
                  else "worktree create failed after the vault claim committed "
                       "(saga compensation; counter not decremented — monotonic-burn)")
        hist.append({"event": "released", "by": "slice", "reason": reason, "at": ts})
        data["updated"] = ts
        result["released"] = True
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


# ── transfer (slice-096 / SC-146 / ADR-122): re-mint claimed_by of an ALREADY-CLAIMED candidate ───

def _valid_email(email: str) -> bool:
    """A minimally well-formed `local@domain`: exactly one @, both parts non-empty, no whitespace or
    angle brackets. Deliberately does NOT require a dot in the domain (existing claims use `a@test`)."""
    if not email or any(c in email for c in "<> \t"):
        return False
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and bool(domain)


def _parse_to(raw: str) -> dict:
    """Parse a `--to` identity — the git-ident angle-bracket form `Name <email>` OR a
    last-whitespace-token `Name email` — into ``{git_user, git_email}`` under a STRICT grammar
    (M1). Raises ``ValueError`` (-> usage exit 2) on anything malformed.

    Deliberately NOT ``email.utils.parseaddr`` / ``getaddresses`` — they NEVER fail and silently
    mis-attribute (CVE-2019-16056 / CVE-2023-27043), which on a twin-reader field like ``claimed_by``
    could persist a blank-email owner that ``owner_of`` reads as de-owned (slice_ownership.py:126)
    while ``stranded_slice_audit`` reads as live. The persisted email MUST be non-blank so a transfer
    can never silently de-own the slice."""
    s = (raw or "").strip()
    if not s:
        raise ValueError('--to is empty (expected "<Name> <email>")')
    if any(ord(ch) < 0x20 for ch in s):
        raise ValueError("--to contains control characters")
    if "<" in s or ">" in s:
        # exactly one, well-formed, TRAILING <...> group; name is everything before it.
        if (s.count("<") != 1 or s.count(">") != 1 or not s.endswith(">")
                or s.index("<") > s.index(">")):
            raise ValueError('--to angle-bracket form must be `Name <email>` (one trailing <email>)')
        lt = s.index("<")
        email = s[lt + 1:-1].strip()
        name = s[:lt].strip()
    else:
        parts = s.split()
        if len(parts) < 2:
            raise ValueError('--to must be "<Name> <email>" (a non-blank name AND an email)')
        email = parts[-1]
        name = " ".join(parts[:-1]).strip()
    if not name:
        raise ValueError("--to has a blank name")
    if any(c in name for c in "@<>"):
        raise ValueError("--to name may not contain '@', '<' or '>' (ambiguous / two-address form)")
    if not _valid_email(email):
        raise ValueError(f"--to email {email!r} is not a well-formed local@domain address")
    return {"git_user": name, "git_email": email}


def _make_transfer_mutate(path: Path, *, slice_id: str | None, candidate_id: str | None,
                          to_identity: dict, performer: dict, ts: str, result: dict):
    """SVW-1 in-lock mutate: re-mint ONLY ``claimed_by`` of the target candidate to ``to_identity``,
    set ``data['updated']``, and append an append-only ``pick_log`` + candidate-``history``
    ``transferred`` memorial. Leaves status/progress/slice/started_at + counters byte-identical
    (never calls ``id_allocator``). Fail-visible + ZERO-write on every refusal (the mutate raises
    ``_ClaimError`` before returning, so ``safe_mutate_text`` never writes).

    Target resolution: ``--candidate`` keys by the unique id (deterministic; rescues a RESERVED
    slice==None candidate — M-add-2). ``--slice`` keys by the ``slice`` field and REFUSES fail-visible
    if >1 LIVE candidate carries it (R-5 collision safety — M-add-1), never first-match-write."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _ClaimError(f"{path} is empty or missing — no candidates to transfer")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ClaimError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise _ClaimError(f"{path} top-level is not a JSON object")
        cands = data.get("candidates")
        if not isinstance(cands, list):
            raise _ClaimError(f"{path} has no candidates[] array")

        if candidate_id is not None:
            rec = next((c for c in cands
                        if isinstance(c, dict) and str(c.get("id")) == candidate_id), None)
            if rec is None:
                raise _ClaimError(
                    f"{candidate_id} is not in the live backlog — already shipped or unknown")
        else:
            matches = [c for c in cands if isinstance(c, dict) and c.get("slice") == slice_id]
            if not matches:
                raise _ClaimError(
                    f"{slice_id} is not in the live backlog — already shipped or unknown "
                    f"(expected the exact stored zero-padded form, e.g. slice-096)")
            if len(matches) > 1:  # R-5 collision — never first-match-write (M-add-1)
                ids = ", ".join(str(c.get("id")) for c in matches)
                raise _ClaimError(
                    f"{slice_id} matches >1 live candidate ({ids}) — ambiguous; disambiguate with "
                    f"`transfer --candidate SC-NNN`. Refusing to first-match-write.")
            rec = matches[0]

        prior = rec.get("claimed_by")
        if not isinstance(prior, dict) or not _norm_email(prior.get("git_email")):
            raise _ClaimError(
                f"candidate {rec.get('id')} carries no current owner (status={rec.get('status')!r}) — "
                f"use `claim`, not `transfer`")

        prior_owner = {"git_user": prior.get("git_user"), "git_email": prior.get("git_email")}
        new_owner = {"git_user": to_identity["git_user"], "git_email": to_identity["git_email"]}
        rec["claimed_by"] = dict(new_owner)

        # BB-20: a present-but-non-list history/pick_log must never silently drop the audit entry.
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        hist.append({"event": "transferred", "from": dict(prior_owner), "to": dict(new_owner),
                     "by": dict(performer), "at": ts})

        plog = data.get("pick_log")
        if not isinstance(plog, list):
            plog = data["pick_log"] = []
        plog.append({"event": "transferred", "candidate": rec.get("id"), "slice": rec.get("slice"),
                     "from": dict(prior_owner), "to": dict(new_owner), "by": dict(performer), "at": ts})
        data["updated"] = ts

        result["from"] = prior_owner
        result["to"] = new_owner
        result["candidate"] = rec.get("id")
        result["slice"] = rec.get("slice")
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _transfer_main(argv: list[str]) -> int:
    """The `transfer` verb — re-mint claimed_by of an already-claimed candidate to a new owner in one
    SVW-1 in-lock RMW (ADR-122). Open to ANY identified caller (anonymous refused); a third-party
    transfer proceeds with a LOUD owner-naming warning. Exit 0 ok / 1 runtime / 2 usage.

    Intercepted at the TOP of main() BEFORE _build_arg_parser (M2), so reserve/claim/release parsing
    stays byte-identical (this verb has its OWN parser; the sibling --candidate-required parser never
    sees it)."""
    p = argparse.ArgumentParser(
        prog="claim_candidate transfer",
        description="Re-mint claimed_by of an ALREADY-CLAIMED candidate to a new owner (logged, "
                    "append-only, allocator-free). Open to any identified caller; a third-party "
                    "transfer proceeds with a loud owner-naming warning (ADR-122).")
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    key = p.add_mutually_exclusive_group(required=True)
    key.add_argument("--slice", dest="slice_id", metavar="slice-NNN",
                     help="the target slice id (exact stored zero-padded 3-digit form)")
    key.add_argument("--candidate", dest="candidate_id", metavar="SC-NNN",
                     help="the target candidate id (unique key; also rescues a reserved slice==None candidate)")
    p.add_argument("--to", required=True, metavar='"<Name> <email>"', help="the new owner identity")
    p.add_argument("--repo-root", "--root", dest="repo_root", type=Path, default=Path("."),
                   help="repo root for the caller git identity (default: cwd)")
    p.add_argument("--json", action="store_true", help="emit JSON confirmation")
    args = p.parse_args(argv)  # exit 2 on neither/both key or missing --to

    # Shape-gate the key BEFORE any work (malformed shape -> usage exit 2; m4).
    slice_id = (args.slice_id or "").strip() or None
    candidate_id = (args.candidate_id or "").strip() or None
    # CR1 (code-review): argparse's one-of-required guard checks PRESENCE only, so an empty/whitespace
    # `--slice ""` / `--candidate ""` (e.g. `--slice "$unset_var"` in automation) both normalize to
    # None here and would fall into the --slice `c.get("slice") == None` match -> a WRONG-TARGET
    # re-mint of a reserved (slice==None) candidate. Refuse fail-visible BEFORE the shape gate.
    if slice_id is None and candidate_id is None:
        sys.stderr.write(
            "claim_candidate: transfer requires a non-empty --slice slice-NNN OR --candidate SC-NNN "
            "(an empty/whitespace value is not a valid target)\n")
        return 2
    if slice_id is not None and not _SLICE_RE.match(slice_id):
        sys.stderr.write(
            f"claim_candidate: --slice {slice_id!r} is not the exact stored zero-padded slice id "
            f"(expected ^slice-\\d{{3,}}$, e.g. slice-096)\n")
        return 2
    if candidate_id is not None and not _CAND_RE.match(candidate_id):
        sys.stderr.write(
            f"claim_candidate: --candidate {candidate_id!r} is not a valid candidate id "
            f"(expected ^SC-\\d+$)\n")
        return 2

    try:
        to_identity = _parse_to(args.to)
    except ValueError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 2

    ts = _now_iso()
    vault_root = _root(args.vault)
    path = vault_root / "candidates.json"
    result: dict = {}

    # Performer identity (attribution); fail-visible on unset/unavailable -> runtime exit 1.
    try:
        git_name, git_email = _git_identity(args.repo_root.resolve())
    except _ClaimError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    performer = {"git_user": git_name, "git_email": git_email}

    # M6: is a shared _claim_coord backend configured? (read-only — no backend mutation). If so, a
    # successful transfer leaves the durable HELD naming the PRIOR owner -> warn loudly post-write.
    backend_configured = False
    try:
        backend_configured = _claim_coord.coordination_backend(vault_root) is not None
    except _claim_coord.UnsupportedBackend:
        backend_configured = True  # a backend IS configured (this build can't serve it) -> still warn

    try:
        mutate = _make_transfer_mutate(path, slice_id=slice_id, candidate_id=candidate_id,
                                       to_identity=to_identity, performer=performer, ts=ts,
                                       result=result)
        safe_mutate_text(path, mutate)
    except _ClaimError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        sys.stderr.write(f"claim_candidate: write to {path} failed (fail-visible per R-7): {exc}\n")
        return 1

    prior = result["from"]
    # ADR-122: a third-party transfer (caller != current owner) proceeds over a LIVE collision -> emit
    # a warning as LOUD + OWNER-NAMING as the AI_SDLC_ALLOW_FOREIGN_SLICE override warning.
    if _norm_email(prior.get("git_email")) != _norm_email(git_email):
        sys.stderr.write(
            f"claim_candidate: TRANSFER-THIRD-PARTY: {result['candidate']} "
            f"({result.get('slice') or 'reserved'}) was claimed by "
            f"{prior.get('git_user') or '?'} <{prior.get('git_email')}> and you are "
            f"{git_name} <{git_email}> — you recorded a transfer of a LIVE claim you do not own to "
            f"{to_identity['git_user']} <{to_identity['git_email']}>. This is a collision guard, not "
            f"a permission wall (ADR-068/122): the transfer PROCEEDED and is recorded in the "
            f"append-only pick_log with your identity as the performer.\n")
    # M6: the shared durable HELD still names the prior owner -> must be reconciled (fail-VISIBLE).
    if backend_configured:
        sys.stderr.write(
            f"claim_candidate: TRANSFER-HELD-STALE: a shared claim-coordination backend is "
            f"configured, and this transfer updated candidates.json ONLY — the durable HELD for "
            f"{result['candidate']} still names the PRIOR owner <{prior.get('git_email')}>. The new "
            f"owner's later `--release` will NOT tear it down (remove_if_owner is owner-checked), "
            f"which can orphan the HELD into a fail-closed lockout. Reconcile the shared HELD "
            f"manually.\n")

    payload = {"action": "transfer-candidate", "candidate": result["candidate"],
               "slice": result.get("slice"), "from": prior, "to": to_identity,
               "by": performer, "at": ts}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"transferred {result['candidate']} ({result.get('slice') or 'reserved'}) from "
              f"{prior.get('git_user') or '?'} <{prior.get('git_email')}> to "
              f"{to_identity['git_user']} <{to_identity['git_email']}> "
              f"(recorded by {git_name} <{git_email}>)")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claim_candidate",
        description="Reserve (--reserve, soft HOLD on pick), claim (default, CLAIM-FIRST: mint "
                    "slice-NNN in-lock), or --release (saga compensation) a slice candidate for /slice.")
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--candidate", required=True, metavar="SC-NNN", help="the candidate id")
    p.add_argument("--name", default=None, metavar="verb-object",
                   help="the slice name (folder suffix; the slice NUMBER is minted in-lock). "
                        "Required for a claim; ignored for --reserve / --release.")
    # slice-027 / m1: --reserve (soft HOLD on pick) and --release (saga compensation) are mutually
    # exclusive modes; the default (neither) is the durable CLAIM/CONFIRM that mints slice-NNN.
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--reserve", action="store_true",
                      help="soft HOLD on pick: mark the candidate `reserved` (claimed-in-intent) "
                           "WITHOUT minting a slice number, so a parallel /slice sees it in-flight; "
                           "the later claim upgrades a same-owner reservation. Idempotent, "
                           "identity-checked. --name is not required.")
    mode.add_argument("--release", action="store_true",
                      help="saga compensation: revert this candidate's claim OR reservation "
                           "(idempotent, identity-checked, monotonic-burn)")
    p.add_argument("--repo-root", "--root", dest="repo_root", type=Path, default=Path("."),
                   help="repo root for git identity (default: cwd)")
    p.add_argument("--json", action="store_true", help="emit JSON confirmation")
    return p


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success, 1 runtime error, 2 usage error."""
    _stdout.reconfigure_stdout_utf8()
    # slice-096 / M2: intercept the `transfer` verb at the TOP, BEFORE _build_arg_parser, so the
    # sibling --candidate-required parser never sees it and reserve/claim/release parsing stays
    # byte-identical. `transfer` has its own isolated parser in _transfer_main.
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "transfer":
        return _transfer_main(argv[1:])
    args = _build_arg_parser().parse_args(argv)

    candidate_id = args.candidate.strip()
    if not candidate_id:
        sys.stderr.write("claim_candidate: --candidate must name a candidate id\n")
        return 2

    ts = _now_iso()
    vault_root = _root(args.vault)
    path = vault_root / "candidates.json"
    result: dict = {}

    # Identity first (every mode needs it; fail-visible on unset/unavailable).
    try:
        git_name, git_email = _git_identity(args.repo_root.resolve())
    except _ClaimError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1

    # slice-091: resolve the OPT-IN coordination backend (None unless a shared-remote backend is
    # configured). Unconfigured -> today's path, byte-identical, no added subprocess (AC3, memoized
    # git_common_dir). A configured-but-unavailable backend fails CLOSED (AC4).
    try:
        backend = _claim_coord.coordination_backend(vault_root)
    except _claim_coord.UnsupportedBackend as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return _EXIT_RETRYABLE  # 3 — configured but this build can't serve it (e.g. s3/minio; `git`
        # IS servable since SC-216/slice-100, and `local` always was)

    # For a CLAIM, validate --name BEFORE any HELD is created (else a bad name would orphan a HELD).
    name = (args.name or "").strip()
    if not args.reserve and not args.release and not _NAME_RE.match(name):
        sys.stderr.write(
            f"claim_candidate: --name {args.name!r} is not a verb-object slice name "
            f"(expected lowercase tokens joined by hyphens, e.g. fix-thumbnail-orientation)\n")
        return 2

    # m3: on the CONFIGURED branch (claim/release compose a shared claims/<candidate>/ path), validate
    # the candidate id BEFORE composing it. reserve is candidates.json-only; the default path is
    # untouched (byte-identical), so the guard never fires there.
    if backend is not None and not args.reserve and not _CAND_RE.match(candidate_id):
        sys.stderr.write(
            f"claim_candidate: --candidate {candidate_id!r} is not a valid candidate id "
            f"(expected ^SC-\\d+$) — refusing to compose a shared claims/ path\n")
        return 2

    # CONFIGURED CLAIM: gate entry on the atomic single-key create-if-absent (AC1/AC2/AC4). CREATED /
    # own-WON fall through to the existing in-lock mint; LOST/UNVERIFIABLE short-circuit fail-closed.
    created_held = False
    if backend is not None and not args.reserve and not args.release:
        try:
            rc, created_held = _coordinate_claim(backend, candidate_id, git_name, git_email, ts,
                                                 vault_root)
        except _ClaimError as exc:
            sys.stderr.write(f"claim_candidate: {exc}\n")
            return 1
        if rc is not None:
            return rc

    try:
        if args.release:
            mutate = _make_release_mutate(path, candidate_id, git_email, ts, result)
        elif args.reserve:
            mutate = _make_reserve_mutate(path, candidate_id, git_name, git_email, ts, result)
        else:
            mutate = _make_claim_mutate(path, candidate_id, name, git_name, git_email, ts,
                                        vault_root, result)
        safe_mutate_text(path, mutate)
        # M-add-1: on the CONFIGURED release path, ALSO tear down the shared HELD (compare-and-delete)
        # so a failed worktree-create cannot orphan it into a permanent EXISTS->LOST lockout. The
        # candidates.json release above is identity-checked, so a foreign release already raised.
        # M7(b) / slice-100: suppress the FULL backend taxonomy, not just OSError — a remote backend
        # raises SyncFailure / SyncUsageError / SyncConfigError (all plain Exceptions), and a leak
        # here would crash `--release` with a traceback AFTER candidates.json was already mutated.
        # The backend itself warns (naming the remote, the ref and the teardown command) before it
        # returns False, so this suppression can never be a SILENT swallow.
        if args.release and backend is not None:
            removed = False
            with contextlib.suppress(Exception):
                removed = backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
            if removed:
                # CR7: drop the C2 evidence ONLY on a CONFIRMED teardown, so a re-claim mints a NEW
                # register value (ABA). When the teardown could NOT reach the register the HELD is
                # still live remotely -- keeping the token is what lets THIS copy still prove it owns
                # it (the backend has already warned, naming the manual teardown command).
                _drop_own_token(vault_root, candidate_id, git_email)
    except _ClaimError as exc:
        # Compensate a HELD this call just minted if the local mint then failed (no orphaned HELD).
        if created_held and backend is not None:
            with contextlib.suppress(Exception):
                backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
            _drop_own_token(vault_root, candidate_id, git_email)
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        if created_held and backend is not None:
            with contextlib.suppress(Exception):
                backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
            _drop_own_token(vault_root, candidate_id, git_email)
        sys.stderr.write(f"claim_candidate: write to {path} failed (fail-visible per R-7): {exc}\n")
        return 1

    if args.reserve:
        did = result.get("reserved", False)
        payload = {"action": "reserve-candidate", "candidate": candidate_id,
                   "reserved": did, "status": "reserved",
                   "claimed_by": {"git_user": git_name, "git_email": git_email}, "at": ts}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"{'reserved' if did else 'already reserved'} {candidate_id} "
                  f"(soft HOLD -- no slice number minted) by {git_name} <{git_email}>")
        return 0

    if args.release:
        payload = {"action": "release-candidate", "candidate": candidate_id,
                   "released": result.get("released", False), "at": ts}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"{'released' if result.get('released') else 'already released'} {candidate_id} "
                  f"(counter not decremented — monotonic-burn)")
        return 0

    if args.json:
        print(json.dumps({
            "action": "claim-candidate", "candidate": candidate_id,
            "slice": result["slice"], "folder": result["folder"],
            "status": "spiking", "progress": "spike",
            "claimed_by": {"git_user": git_name, "git_email": git_email}, "at": ts,
        }, ensure_ascii=False))
    else:
        print(f"claimed {candidate_id} -> {result['folder']} (status: spiking, progress: spike) "
              f"by {git_name} <{git_email}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
