"""claim_candidate.py — claim a slice candidate for /slice (v2, NEW).

Single-skill tool for `/slice` Step 5.4. Atomically marks a candidate as PICKED:
sets ``status=spiking``, ``progress=spike``, ``claimed_by={git_user,git_email}``,
``started_at``, ``slice`` (canonical ``slice-NNN``); appends a ``picked`` event to
the candidate's ``history[]`` and an entry to the file-level ``pick_log[]`` — ALL in
ONE SVW-1 locked read-modify-write (`_vault_write.safe_mutate_text`), so a parallel
`/slice` can never lose-update the shared candidates.json (R-32). The candidate stays
in the LIVE file (it is only MOVED to archive/candidates.json on ship/reject).

NET-NEW in v2 (v1 had no unified candidates backlog; the claim was scattered across
slice-queue.md). Fail-VISIBLE: unset git identity, a missing/unpickable candidate, a
malformed file, or a bad ``--slice`` shape each abort non-zero with a clear message —
never a silent partial claim. The mutate callback raising leaves the file UNTOUCHED
(safe_mutate_text writes nothing when mutate raises).

Vault root: `--vault ROOT` overrides `$AI_SDLC_VAULT_ROOT` / the computed default.
Exit 0 success, 1 runtime error (identity unset / git unavailable / candidate not found
/ not pickable / malformed JSON / write failure), 2 usage error (bad ``--slice`` shape).
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

from scripts.lib import _stdout
from scripts.lib._git_default_branch import run_git
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text

_JSON_DUMP = {"indent": 2, "ensure_ascii": False, "sort_keys": False}
_PICKABLE = {"candidate", "deferred"}
# A slice folder is `slice-NNN-<name>`; the candidate.slice field stores the canonical
# short `slice-NNN` join key (per schemas/slice-candidates.example.json, e.g. "slice-021").
_SLICE_RE = re.compile(r"^(slice-\d+)(?:-.+)?$")


class _ClaimError(RuntimeError):
    """Fail-visible claim failure → CLI exit 1."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _git_identity(repo_root: Path) -> tuple[str, str]:
    """Resolve ``git config user.name`` / ``user.email`` at ``repo_root``.

    Fail-VISIBLE: an empty name or email, or an unavailable git binary, raises
    ``_ClaimError`` (exit 1) — the claim must never record an anonymous owner.
    """
    try:
        name = run_git(repo_root, "config", "user.name").stdout.strip()
        email = run_git(repo_root, "config", "user.email").stdout.strip()
    except FileNotFoundError as exc:  # git binary not on PATH
        raise _ClaimError(f"git binary unavailable: {exc}") from exc
    if not name or not email:
        raise _ClaimError(
            "git identity is not set (user.name / user.email empty) — set it before "
            'claiming: `git config user.name "..."` and `git config user.email "..."`'
        )
    return name, email


def _make_mutate(path: Path, candidate_id: str, slice_short: str,
                 name: str, email: str, ts: str):
    """Build the SVW-1 mutate callback (current JSON text -> new JSON text)."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _ClaimError(
                f"{path} is empty or missing — no candidates to claim "
                f"(run /discover or /slice-candidates first)"
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _ClaimError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise _ClaimError(f"{path} top-level is not a JSON object")
        cands = data.get("candidates")
        if not isinstance(cands, list):
            raise _ClaimError(f"{path} has no candidates[] array")

        rec = next(
            (c for c in cands if isinstance(c, dict) and str(c.get("id")) == candidate_id),
            None,
        )
        if rec is None:
            raise _ClaimError(f"no candidate with id {candidate_id!r} in the live backlog")

        st = rec.get("status")
        if st not in _PICKABLE:
            who = (rec.get("claimed_by") or {}).get("git_user")
            raise _ClaimError(
                f"candidate {candidate_id} is not pickable (status={st!r}"
                + (f", claimed_by {who}" if who else "")
                + ") — it is already in-flight, blocked, or shipped"
            )

        rec["status"] = "spiking"
        rec["progress"] = "spike"
        rec["slice"] = slice_short
        rec["claimed_by"] = {"git_user": name, "git_email": email}
        rec["started_at"] = ts
        hist = rec.setdefault("history", [])
        if isinstance(hist, list):
            hist.append({"event": "picked", "by": "slice", "at": ts, "ref": slice_short})

        plog = data.setdefault("pick_log", [])
        if isinstance(plog, list):
            plog.append({
                "candidate": candidate_id,
                "slice": slice_short,
                "picked_by": f"{name} {email}",
                "at": ts,
            })
        data["updated"] = ts
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claim_candidate",
        description="Claim a slice candidate (SVW-1 locked) for /slice: status->spiking, "
                    "progress->spike, claimed_by, started_at, history + pick_log.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--candidate", required=True, metavar="SC-NNN",
                   help="the candidate id to claim")
    p.add_argument("--slice", required=True, dest="slice", metavar="slice-NNN-name",
                   help="the slice folder name; the canonical slice-NNN is derived from it")
    p.add_argument("--repo-root", "--root", dest="repo_root", type=Path, default=Path("."),
                   help="repo root for git identity (default: cwd)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON confirmation (default: human-readable text)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success, 1 runtime error, 2 usage error."""
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    candidate_id = args.candidate.strip()
    if not candidate_id:
        sys.stderr.write("claim_candidate: --candidate must name a candidate id\n")
        return 2
    m = _SLICE_RE.match(args.slice.strip())
    if not m:
        sys.stderr.write(
            f"claim_candidate: --slice {args.slice!r} is not a slice folder "
            f"(expected slice-NNN or slice-NNN-name)\n"
        )
        return 2
    slice_short = m.group(1)

    ts = _now_iso()
    path = _root(args.vault) / "candidates.json"
    try:
        name, email = _git_identity(args.repo_root.resolve())
        mutate = _make_mutate(path, candidate_id, slice_short, name, email, ts)
        safe_mutate_text(path, mutate)
    except _ClaimError as exc:
        sys.stderr.write(f"claim_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        sys.stderr.write(f"claim_candidate: write to {path} failed (fail-visible per R-7): {exc}\n")
        return 1

    if args.json:
        print(json.dumps({
            "action": "claim-candidate",
            "candidate": candidate_id,
            "slice": slice_short,
            "status": "spiking",
            "progress": "spike",
            "claimed_by": {"git_user": name, "git_email": email},
            "at": ts,
        }, ensure_ascii=False))
    else:
        print(
            f"claimed {candidate_id} -> {slice_short} (status: spiking, progress: spike) "
            f"by {name} <{email}>"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
