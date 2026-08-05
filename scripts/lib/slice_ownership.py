"""slice_ownership.py — read the mark back at the designation boundary (slice-069, NEW).

The pipeline MINTS `claimed_by {git_user, git_email}` in-lock on every claim
(`skills/slice/scripts/claim_candidate.py`) and, until this module, **nothing ever read it back**.
Slice resolution designated a slice purely by LOCATION — the branch name, the vault scan, the folder
position — so a skill run for slice-X could resolve, and write into, slice-Y. Observed at slice-011:
a forked `/code-review` wrote slice-010's `code-review.json` + `milestone.json` + a gate-log row.

**THIS IS A COLLISION GUARD, NOT AN AUTHORIZATION BOUNDARY** (ADR-068). Git identity is unverified
and self-assignable with two `git config` commands — in April 2026 exactly that was used to spoof a
maintainer and make a Claude-based code-review workflow approve malicious code. This module catches
the HONEST cross-slice mistake between cooperating humans and their forked agents. It does not stop a
determined actor, and no message in it may imply otherwise. (Advisory, in the `flock(2)` sense.)

**The cost function is asymmetric, and it decides every ambiguity** (ADR-068): a FALSE REFUSAL of the
rightful owner bricks all 10 loop skills; a MISSED catch merely restores today's behaviour. So:

  1. **Owner-lookup FIRST, identity-read SECOND** — you only need to know who you are when there is
     someone to collide WITH. (It also means every existing resolver fixture, which builds a vault
     with no `candidates.json`, keeps passing untouched.)
  2. **The caller identity is an ACCEPT-SET**, not a single value: `git config user.email` (the
     projection that MINTED the mark) → `$GIT_AUTHOR_EMAIL` / `$GIT_COMMITTER_EMAIL` → the email in
     `git var GIT_AUTHOR_IDENT` (consulted only on a mismatch — zero extra subprocess on the happy
     path). Deliberately WIDER than the mint side. **Do NOT "harmonize" the two ends** — the
     asymmetry is the false-refusal insurance (m3).
  3. **Every degenerate input is NAMED, never silent**, and none of them may refuse the owner:
     a missing candidate row → `unowned`; a blank/absent/wrong-typed `claimed_by` → `legacy`;
     identity UNSET or UNREADABLE (`safe.directory`, CVE-2022-24765) *with an owner on record* →
     refuse, with a message naming the specific remedy.
  4. **The ARCHIVE arm WARNS, it never REFUSES** (ADR-072). A terminal, shipped slice has no live
     collision to guard, while the archive is BY CONSTRUCTION where identity drift accumulates — the
     real vault carries a stale owner email for the same human, so gating it would refuse the
     rightful owner on his own shipped slices. `enforced` is False there.

Read-only: this module never writes to the vault and never raises for the owner (a malformed
candidates.json degrades to `unowned`) — the resolver is the hottest shared path in the pipeline and
an exception here would brick every loop skill.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

# Distinct + NON-retryable. 3 is RESERVED for the retryable CAS-write-conflict signal and 4 for the
# AMBIGUOUS sentinel (ADR-010/B3); a consumer must be able to tell "refuse, name the owner" from
# "refuse, disambiguate" — so the ownership refusal gets its own code.
EXIT_OWNERSHIP = 5

# The override is SLICE-SCOPED — its value must be the specific slice id, so it authorizes exactly
# one object. A bare `=1`/`=true` is REJECTED: a boolean override is ambient authority by another
# name, and it would be exported once and forgotten — re-creating the very ambient authority this
# gate exists to remove. An env var is also the ONLY override channel that reaches the production
# shape (a forked skill's bash line cannot be hand-edited by the operator mid-run).
OVERRIDE_ENV = "AI_SDLC_ALLOW_FOREIGN_SLICE"

# Verdicts that STOP a caller. `unowned` / `legacy` / `owner` / `overridden` all proceed.
_REFUSALS = frozenset({"foreign", "identity-unset", "identity-unreadable"})

# The archive arm is diagnostic-only (ADR-072): the verdict stays honest, but it is not ENFORCED.
_UNENFORCED_ARMS = frozenset({"by-id-archive"})

_IDENT_EMAIL = re.compile(r"<([^>]+)>")


def is_refusal(verdict: str) -> bool:
    """True for the verdicts that mean STOP. (Whether a refusal is ENFORCED at a given call site is
    a separate question — see `check_ownership(...)['enforced']` and the archive-arm rule.)"""
    return verdict in _REFUSALS


def _run_git(repo_root, *args: str) -> tuple[int, str, str]:
    """`git -C <root> <args>` -> (rc, stdout, stderr). Never raises. BB-19: capture BYTES and decode
    in the main thread — a non-UTF-8 ref/path would otherwise raise an uncaught UnicodeDecodeError
    inside the subprocess reader thread."""
    try:
        cp = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return 127, "", "git unavailable"
    return (cp.returncode,
            cp.stdout.decode("utf-8", "replace").strip(),
            cp.stderr.decode("utf-8", "replace").strip())


def _norm(value) -> str:
    """The comparison normal form: stripped + casefolded. Emails are case-insensitive in practice and
    a case-different address must never be read as a different human (that would be a false refusal)."""
    return value.strip().casefold() if isinstance(value, str) else ""


def owner_of(vault, slice_id: str) -> tuple[str, dict | None]:
    """The recorded owner of `slice_id` -> (status, owner).

    status: ``owner-found`` | ``no-candidate`` | ``no-claim``.
    Walks the LIVE ``candidates.json`` first, then ``archive/candidates.json`` — the archive arm is
    load-bearing, not defensive: ``/commit-slice`` auto-emits ``/slice-story`` AFTER ``/reflect``
    archived the folder, so a live-only lookup would report "unclaimed" on every legitimate post-ship
    run. Joined on the candidate's ``slice`` field (the same field ``claim_candidate`` writes).

    Type-defensive by design (M5): a ``claimed_by`` that is absent, ``{}``, blank, or the wrong type
    is ``no-claim`` — NEVER an owner that matches nothing (which would refuse EVERYONE for that slice,
    permanently). A malformed/unreadable candidates.json degrades to ``no-candidate``, never a crash.
    """
    root = Path(vault)
    seen_row = False
    for rel in ("candidates.json", os.path.join("archive", "candidates.json")):
        p = root / rel
        if not p.is_file():
            continue
        try:
            rows = json.loads(p.read_text(encoding="utf-8")).get("candidates", [])
        except (OSError, ValueError, AttributeError):
            continue  # a malformed vault must not brick the resolver
        if not isinstance(rows, list):
            continue
        for c in rows:
            if not isinstance(c, dict) or c.get("slice") != slice_id:
                continue
            seen_row = True
            cb = c.get("claimed_by")
            if not isinstance(cb, dict):            # None / str / int / list -> legacy, never foreign
                continue
            name, email = cb.get("git_user"), cb.get("git_email")
            if not _norm(email):                    # blank / absent / wrong-typed email -> legacy
                continue
            return "owner-found", {"git_user": name if isinstance(name, str) else "",
                                   "git_email": email.strip(),
                                   "candidate": c.get("id")}
    return ("no-claim", None) if seen_row else ("no-candidate", None)


def caller_identity(repo_root) -> tuple[str, str, set[str]]:
    """Who is calling -> (status, display_name, accepting_emails).

    status: ``ok`` | ``unset`` | ``unreadable``.

    ``unset`` and ``unreadable`` are DIFFERENT states and must stay distinct from "wrong owner":
      * UNSET       — `git config user.email` exits 1 with EMPTY stdout (git's rich "Please tell me
                      who you are" fires only at COMMIT time, never on a config read).
      * UNREADABLE  — `safe.directory` / dubious-ownership (CVE-2022-24765, git >= 2.35.2) makes git
                      FATAL when the repo's on-disk owner differs from the running OS user. A live
                      risk for a sandboxed/containerised subagent. Fail-closed is right; mis-logging
                      it as "wrong owner" is not.

    The accepting set is deliberately WIDER than the mint side (which reads config only): config ∪
    env ∪ `git var GIT_AUTHOR_IDENT`. `git config` is blind to `GIT_AUTHOR_EMAIL`/`-c` overrides —
    precisely the environment a CI runner or forked subagent injects — so a single-source read would
    turn that disagreement into a FALSE REFUSAL. Widening can only ever miss a catch (a *different*
    human matches no projection); it can never brick the owner.
    """
    rc, email, err = _run_git(repo_root, "config", "user.email")
    if rc != 0 and ("dubious ownership" in err.lower() or "safe.directory" in err.lower()):
        return "unreadable", "", set()
    rc_n, name, _ = _run_git(repo_root, "config", "user.name")

    accepting = {_norm(email)} if email else set()
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        if _norm(os.environ.get(var)):
            accepting.add(_norm(os.environ[var]))

    display = name if rc_n == 0 and name else (email or "")
    if not accepting:
        return "unset", display, set()
    return "ok", display or email, accepting


def _git_var_email(repo_root) -> str:
    """The email `git var GIT_AUTHOR_IDENT` would actually stamp RIGHT NOW (config + env +
    user.useConfigOnly folded in). Consulted ONLY on a mismatch, so the happy path pays nothing.
    Exits 128 ("unable to auto-detect email address") when no identity exists — handled, not raised."""
    rc, out, _ = _run_git(repo_root, "var", "GIT_AUTHOR_IDENT")
    if rc != 0:
        return ""
    m = _IDENT_EMAIL.search(out)
    return _norm(m.group(1)) if m else ""


def _override_slice() -> str:
    """The slice id the operator explicitly authorized, or "" — a bare `1`/`true` is NOT an override."""
    raw = (os.environ.get(OVERRIDE_ENV) or "").strip()
    return raw if re.match(r"^slice-\d+", raw) else ""


def _refusal_message(slice_id: str, owner: dict, caller: str, reason: str) -> str:
    """AC3: NAME the owner and NAME the override — an operator who legitimately needs to act on
    someone else's slice must never be left guessing who to coordinate with or how to proceed.

    M10: the override is applicable BY THE WRONG PARTY (a forked agent can prefix the env var onto
    its own bash line; the human cannot export into a running session). So the message tells the
    AGENT to stop and report, and tells the HUMAN how to proceed deliberately.
    """
    who = f"{owner.get('git_user') or '?'} <{owner['git_email']}>"
    cand = f" ({owner['candidate']})" if owner.get("candidate") else ""
    line = (f"SLICE-OWNERSHIP-REFUSED: {slice_id}{cand} is claimed by {who}; {reason}.\n"
            f"  This is a COLLISION GUARD, not a permission check -- it exists to catch an honest\n"
            f"  cross-slice mistake, and it means you are about to write into someone else's slice.\n"
            f"  If you are an AGENT: STOP and report the owner to the user. Do NOT set the override\n"
            f"  yourself. If you are the OPERATOR and you mean to proceed anyway, re-run with:\n"
            f"      {OVERRIDE_ENV}={slice_id} <re-run the exact command that was refused>\n"
            f"  To take the slice over permanently (no override needed), record it with the logged,\n"
            f"  append-only transfer verb:\n"
            f"      claim_candidate.py transfer --slice {slice_id} --to \"<Name> <email>\"")
    return line


def check_ownership(vault, slice_id: str, repo_root=".", *, arm: str = "live",
                    allow_foreign: bool | None = None) -> dict:
    """May this caller act on this slice? Never raises for the owner.

    `arm` is the resolution arm that produced the slice (``git-branch`` | ``vault-scan`` |
    ``by-id-active`` | ``by-id-archive`` | ``live``). It decides ENFORCEMENT, not the verdict:
    a refusal on ``by-id-archive`` is reported and warned about, but **never enforced** (ADR-072).

    Returns `{verdict, enforced, owner, caller, reason, message, warning, override}`.
    """
    status, owner = owner_of(vault, slice_id)

    # (1) OWNER-LOOKUP FIRST. No recorded owner => no collision is possible => never ask who we are,
    #     and never refuse. This is what keeps every existing resolver fixture green, untouched.
    if status == "no-candidate":
        return {"verdict": "unowned", "enforced": False, "owner": None, "caller": None,
                "reason": "no candidate record for this slice",
                "message": "", "warning": f"ownership UNVERIFIED for {slice_id}: no candidate record",
                "override": None}
    if status == "no-claim":
        return {"verdict": "legacy", "enforced": False, "owner": None, "caller": None,
                "reason": "candidate carries no usable claimed_by (pre-allocator/legacy record)",
                "message": "",
                "warning": f"ownership UNVERIFIED for {slice_id}: candidate has no usable claimed_by (legacy)",
                "override": None}

    # (2) There IS someone to collide with -> now, and only now, read the caller's identity.
    id_status, display, accepting = caller_identity(repo_root)
    if id_status == "unreadable":
        v = "identity-unreadable"
        return {"verdict": v, "enforced": arm not in _UNENFORCED_ARMS, "owner": owner, "caller": None,
                "reason": "git cannot read your identity here (dubious ownership / safe.directory)",
                "message": _refusal_message(
                    slice_id, owner, "",
                    "and git CANNOT READ your identity here (dubious ownership -- CVE-2022-24765). "
                    "Remedy: git config --global --add safe.directory <path>"),
                "warning": "", "override": OVERRIDE_ENV}
    if id_status == "unset":
        v = "identity-unset"
        return {"verdict": v, "enforced": arm not in _UNENFORCED_ARMS, "owner": owner, "caller": None,
                "reason": "your git identity is not set",
                "message": _refusal_message(
                    slice_id, owner, "",
                    'your git identity is NOT SET, so ownership cannot be verified. Remedy: '
                    'git config user.name "..." && git config user.email "..."'),
                "warning": "", "override": OVERRIDE_ENV}

    owner_email = _norm(owner["git_email"])
    if owner_email in accepting:
        return {"verdict": "owner", "enforced": False, "owner": owner,
                "caller": {"git_user": display, "git_email": sorted(accepting)[0]},
                "reason": "", "message": "",
                "warning": "", "override": None}

    # (3) MISMATCH on the config/env projections -> lazily widen with what git would ACTUALLY stamp.
    #     (Only here, so the happy path never pays for the extra subprocess.)
    gv = _git_var_email(repo_root)
    if gv and gv == owner_email:
        return {"verdict": "owner", "enforced": False, "owner": owner,
                "caller": {"git_user": display, "git_email": gv},
                "reason": "matched via git var GIT_AUTHOR_IDENT", "message": "",
                "warning": "", "override": None}

    caller = {"git_user": display, "git_email": sorted(accepting)[0] if accepting else ""}

    # (4) The explicit, SLICE-SCOPED override — honoured loudly, on every call.
    overridden = allow_foreign if allow_foreign is not None else (_override_slice() == slice_id)
    if overridden:
        return {"verdict": "overridden", "enforced": False, "owner": owner, "caller": caller,
                "reason": "explicit slice-scoped override",
                "message": "",
                "warning": (f"OVERRIDE: proceeding on {owner.get('git_user') or '?'} "
                            f"<{owner['git_email']}>'s {slice_id} by explicit {OVERRIDE_ENV}"),
                "override": OVERRIDE_ENV}

    reason = f"you are {display or '?'} <{caller['git_email'] or 'unknown'}>"
    enforced = arm not in _UNENFORCED_ARMS
    out = {"verdict": "foreign", "enforced": enforced, "owner": owner, "caller": caller,
           "reason": reason, "message": _refusal_message(slice_id, owner, caller["git_email"], reason),
           "warning": "", "override": OVERRIDE_ENV}
    if not enforced:
        # ADR-072: the archive arm reports the mismatch but does NOT stop the caller. The real vault
        # carries a STALE owner email for the same human (an identity that drifted over time), so
        # enforcing here would refuse the rightful owner on his own shipped slices -- and a terminal
        # slice has no live collision to guard in the first place.
        out["warning"] = (f"ownership MISMATCH on the archived {slice_id}: claimed by "
                          f"{owner.get('git_user') or '?'} <{owner['git_email']}>, you are "
                          f"{display or '?'} <{caller['git_email'] or 'unknown'}>. Not enforced on a "
                          f"shipped slice (identity drift is expected in the archive).")
        out["message"] = ""
    return out
