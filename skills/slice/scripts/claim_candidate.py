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
Exit 0 success, 1 runtime error (identity unset / git unavailable / candidate not found / not pickable /
malformed file / write failure / cross-identity release), 2 usage error (bad ``--name`` shape).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout, id_allocator
from scripts.lib._git_default_branch import run_git
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text

_JSON_DUMP = {"indent": 2, "ensure_ascii": False, "sort_keys": False}
_PICKABLE = {"candidate", "deferred"}
# A slice NAME is the verb-object folder suffix (e.g. `fix-thumbnail-orientation`): lowercase
# tokens joined by single hyphens. The NUMBER is no longer a caller input — it is minted here.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
                       external_max: int, result: dict):
    """SVW-1 mutate (current JSON text -> new JSON text). Mints the slice number IN-LOCK and
    stashes the minted ``slice``/``folder`` into ``result`` for the caller to print."""

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
        if st not in _PICKABLE:
            who = (rec.get("claimed_by") or {}).get("git_user")
            raise _ClaimError(
                f"candidate {candidate_id} is not pickable (status={st!r}"
                + (f", claimed_by {who}" if who else "")
                + ") — it is already in-flight, blocked, or shipped")

        # Mint the slice number IN-LOCK (claim-first). seed_max = max(external floor, in-data
        # slice fields + pick_log) so the counter never re-issues a live OR archived number.
        in_data = id_allocator.scan_max(
            [c.get("slice") for c in cands if isinstance(c, dict)], "slice")
        in_data = max(in_data, id_allocator.scan_max(
            [e.get("slice") for e in data.get("pick_log", []) if isinstance(e, dict)], "slice"))
        slice_short = id_allocator.next_id(data, "slice", seed_max=max(external_max, in_data))
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

        rec["status"] = "candidate"
        rec["progress"] = "not-started"
        rec["slice"] = None
        rec["claimed_by"] = None
        rec["started_at"] = None
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        hist.append({"event": "released", "by": "slice",
                     "reason": "worktree create failed after the vault claim committed "
                               "(saga compensation; counter not decremented — monotonic-burn)",
                     "at": ts})
        data["updated"] = ts
        result["released"] = True
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claim_candidate",
        description="Claim a slice candidate CLAIM-FIRST (mint slice-NNN in-lock) for /slice, or "
                    "--release a reserved claim (saga compensation).")
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--candidate", required=True, metavar="SC-NNN", help="the candidate id")
    p.add_argument("--name", default=None, metavar="verb-object",
                   help="the slice name (folder suffix; the slice NUMBER is minted in-lock). "
                        "Required for a claim; ignored for --release.")
    p.add_argument("--release", action="store_true",
                   help="saga compensation: revert this candidate's claim (idempotent, "
                        "identity-checked, monotonic-burn)")
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
    path = _root(args.vault) / "candidates.json"
    result: dict = {}
    try:
        git_name, git_email = _git_identity(args.repo_root.resolve())
        if args.release:
            mutate = _make_release_mutate(path, candidate_id, git_email, ts, result)
        else:
            name = (args.name or "").strip()
            if not _NAME_RE.match(name):
                sys.stderr.write(
                    f"claim_candidate: --name {args.name!r} is not a verb-object slice name "
                    f"(expected lowercase tokens joined by hyphens, e.g. fix-thumbnail-orientation)\n")
                return 2
            mutate = _make_claim_mutate(path, candidate_id, name, git_name, git_email, ts,
                                        _external_slice_max(_root(args.vault)), result)
        safe_mutate_text(path, mutate)
    except _ClaimError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        sys.stderr.write(f"claim_candidate: write to {path} failed (fail-visible per R-7): {exc}\n")
        return 1

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
