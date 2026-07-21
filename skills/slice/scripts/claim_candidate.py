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

Exit 0 success, 1 runtime error (identity unset / git unavailable / candidate not found / not pickable /
malformed file / write failure / cross-identity release), 2 usage error (bad ``--name`` / ``--candidate``
shape). Configured-coordination exits: 3 retryable (backend unreachable/transient — fail-closed), 4
ambiguous (indeterminate read-back — fail-closed), 5 ownership (a foreign developer won the concurrent pick).
"""
from __future__ import annotations

import argparse
import contextlib
import json
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
# Configured-coordination exit codes (aligned with slice_ownership's taxonomy — m1 / ADR-113):
# 5 ownership (foreign hold), 3 retryable (transient/unreachable), 4 ambiguous (indeterminate read-back).
_EXIT_RETRYABLE = 3
_EXIT_AMBIGUOUS = 4


def _norm_email(value: object) -> str:
    """Stripped + casefolded email compare (a case-different address is the same owner)."""
    return value.strip().casefold() if isinstance(value, str) else ""


def _coordinate_claim(backend, candidate_id: str, git_name: str, git_email: str,
                      ts: str) -> tuple[int | None, bool]:
    """CONFIGURED-branch claim gate. Returns ``(exit_code_or_None, created_held)``: ``None`` proceeds to
    the existing in-lock mint (CREATED, or an own-token EXISTS self-retry — C2), a non-None int
    short-circuits fail-closed. ``created_held`` is True only when THIS call minted a fresh HELD (so a
    later mint failure can compensate it — M-add-1)."""
    body = {"candidate": candidate_id,
            "actor": {"git_user": git_name, "git_email": git_email},
            "idempotency_token": uuid.uuid4().hex, "at": ts}
    res = backend.create_if_absent(_claim_coord.claim_key(candidate_id), body)
    if res.status == _claim_coord.CREATED:
        return None, True  # won the atomic create -> proceed to mint
    if res.status == _claim_coord.EXISTS:
        owner = ((res.body or {}).get("actor") or {}).get("git_email")
        if _norm_email(owner) == _norm_email(git_email):
            return None, False  # C2 self-retry: own HELD (crash-before-mint) -> WON -> proceed to mint
        who = ((res.body or {}).get("actor") or {}).get("git_user") or "?"
        sys.stderr.write(
            f"claim_candidate: SLICE-CLAIM-REFUSED: {candidate_id} is already claimed by {who} "
            f"<{owner or 'unknown'}> on the shared vault — you are {git_name} <{git_email}>. This is a "
            f"collision guard: another developer won the concurrent pick. Coordinate with the owner, or "
            f"pick a different candidate.\n")
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
        return _EXIT_RETRYABLE  # 3 — configured but this build can't serve it (a SC-197 build could)

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
        rc, created_held = _coordinate_claim(backend, candidate_id, git_name, git_email, ts)
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
        if args.release and backend is not None:
            with contextlib.suppress(OSError):
                backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
    except _ClaimError as exc:
        # Compensate a HELD this call just minted if the local mint then failed (no orphaned HELD).
        if created_held and backend is not None:
            with contextlib.suppress(Exception):
                backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        if created_held and backend is not None:
            with contextlib.suppress(Exception):
                backend.remove_if_owner(_claim_coord.claim_key(candidate_id), git_email)
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
