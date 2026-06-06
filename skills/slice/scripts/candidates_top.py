"""candidates_top.py — ranked digest of the live slice backlog for /slice (v2, NEW).

Single-skill injection tool for `/slice` Step "Live state". Reads the unified
`<vault>/candidates.json` backlog and prints the top-N PICKABLE candidates, ranked
by priority, with blocked-on-spike + unmet-dependency flags — so /slice can
"recommend the next cut" without re-running a multi-source fan-out (rollout #3: the
single candidates.json IS the pre-ranked, pre-materialized source of truth, replacing
v1's scattered backlog.md / risk-register-as-candidate-source / slice-queue.md).

NET-NEW in v2 (v1 had no unified candidates backlog). Read-only — never mutates the
vault; claiming the pick is `claim_candidate.py`'s job.

Classification (LIVE-file statuses, per schemas/slice-candidates.example.json):

  candidate | deferred  -> PICKABLE      (ranked into the main list)
  blocked              -> blocked-on-spike (own section; needs a fallback re-spike,
                          NOT a fresh pick). Also any candidate carrying a blocking
                          assumption whose spike_status == "failed".
  spiking | active     -> in-flight      (claimed; parallel slices are normal — not
                          re-picked, surfaced as a one-line consult)

A dependency is UNMET iff it is still LIVE in candidates.json — a shipped dependency
is MOVED out to archive/candidates.json, so its ABSENCE from the live file == satisfied.

Vault root: `--vault ROOT` overrides `$AI_SDLC_VAULT_ROOT` / the computed default
(mirrors vault_edit's `_root`). Exit 0 success (INCLUDING the normal "no backlog yet"
early state — never breaks the injection), 1 runtime error (malformed/unexpected JSON),
2 usage error (bad CLI args).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

_PICKABLE = {"candidate", "deferred"}
_BLOCKED = {"blocked"}
_IN_FLIGHT = {"spiking", "active"}
# effort -> sort rank (smaller cut first on a score tie). Unknown effort sorts last.
_EFFORT_RANK = {"XS": 0, "S": 1, "M": 2, "L": 3, "XL": 4}


# ── helpers ─────────────────────────────────────────────────────────────────────

def _root(vault_arg: str | None) -> Path:
    """The vault root: ``--vault`` when given, else the resolved ``VAULT_ROOT``."""
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _priority(cand: dict) -> dict:
    pr = cand.get("priority")
    return pr if isinstance(pr, dict) else {}


def _score(cand: dict) -> float:
    s = _priority(cand).get("score")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else 0.0


def _effort(cand: dict) -> str:
    return str(_priority(cand).get("effort") or "").strip()


def _effort_rank(cand: dict) -> int:
    return _EFFORT_RANK.get(_effort(cand).upper(), 9)


def _blast(cand: dict) -> str:
    return str(_priority(cand).get("blast_radius") or "").strip()


def _failed_blocking_assumption(cand: dict) -> dict | None:
    """The first blocking assumption whose spike has FAILED (None if none)."""
    for a in cand.get("assumptions") or []:
        if isinstance(a, dict) and a.get("blocking") and a.get("spike_status") == "failed":
            return a
    return None


def _classify(cand: dict) -> str:
    """pickable | blocked | in_flight | other (shipped/rejected — not in a live file)."""
    st = cand.get("status")
    if st in _BLOCKED or _failed_blocking_assumption(cand) is not None:
        return "blocked"
    if st in _IN_FLIGHT:
        return "in_flight"
    if st in _PICKABLE:
        return "pickable"
    return "other"


def _unmet_deps(cand: dict, live_ids: set[str]) -> list[str]:
    """Dependencies still LIVE in candidates.json (== not yet shipped/archived)."""
    return [d for d in (cand.get("dependencies") or []) if d in live_ids]


# ── formatting ───────────────────────────────────────────────────────────────────

def _fmt_text(project: str, ranked: list[tuple[dict, list[str]]],
              blocked: list[dict], in_flight: list[dict], top: int) -> str:
    lines: list[str] = []
    lines.append(
        f"CANDIDATES (live backlog: {project}) — "
        f"{len(ranked)} pickable, {len(blocked)} blocked-on-spike, "
        f"{len(in_flight)} in-flight"
    )

    lines.append("")
    if ranked:
        shown = ranked[:top] if top > 0 else ranked
        lines.append(f"Top picks (ranked, {len(shown)} of {len(ranked)}):")
        for i, (cand, unmet) in enumerate(shown, 1):
            cid = cand.get("id", "?")
            title = cand.get("title", "")
            meta = f"score {_score(cand):g}  effort {_effort(cand) or '?'}"
            if _blast(cand):
                meta += f"  blast: {_blast(cand)}"
            if unmet:
                meta += f"  [deps-unmet: {', '.join(unmet)}]"
            lines.append(f"  {i}. {cid}  {title}")
            lines.append(f"       {meta}")
            if cand.get("description"):
                lines.append(f"       {cand['description']}")
            if cand.get("user_visible_outcome"):
                lines.append(f"       -> {cand['user_visible_outcome']}")
    else:
        lines.append("No pickable candidates (none in {candidate, deferred}).")

    if blocked:
        lines.append("")
        lines.append("Blocked on spike (needs a fallback re-spike, NOT a fresh pick):")
        for cand in blocked:
            a = _failed_blocking_assumption(cand) or {}
            ev = a.get("spike_evidence") or ""
            line = f"  {cand.get('id', '?')}  {cand.get('title', '')}"
            if ev:
                line += f"  — {ev}"
            lines.append(line)
            if a.get("fallback"):
                lines.append(f"       fallback: {a['fallback']}")

    if in_flight:
        lines.append("")
        lines.append("In-flight (claimed; parallel slices are normal — do not re-pick):")
        for cand in in_flight:
            who = (cand.get("claimed_by") or {}).get("git_user") or "?"
            slc = cand.get("slice") or "?"
            prog = cand.get("progress") or "?"
            lines.append(
                f"  {cand.get('id', '?')}  {cand.get('title', '')}  [{slc}, {prog}, by {who}]"
            )

    return "\n".join(lines) + "\n"


def _fmt_json(project: str, ranked: list[tuple[dict, list[str]]],
              blocked: list[dict], in_flight: list[dict], top: int) -> str:
    shown = ranked[:top] if top > 0 else ranked
    payload = {
        "action": "candidates-top",
        "project": project,
        "counts": {
            "pickable": len(ranked),
            "blocked": len(blocked),
            "in_flight": len(in_flight),
        },
        "top": [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "score": _score(c),
                "effort": _effort(c) or None,
                "blast_radius": _blast(c) or None,
                "deps_unmet": unmet,
            }
            for c, unmet in shown
        ],
        "blocked": [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "evidence": (_failed_blocking_assumption(c) or {}).get("spike_evidence"),
                "fallback": (_failed_blocking_assumption(c) or {}).get("fallback"),
            }
            for c in blocked
        ],
        "in_flight": [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "slice": c.get("slice"),
                "progress": c.get("progress"),
                "by": (c.get("claimed_by") or {}).get("git_user"),
            }
            for c in in_flight
        ],
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="candidates_top",
        description="Ranked digest of the live slice backlog (<vault>/candidates.json) "
                    "for /slice. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--top", type=int, default=5,
                   help="how many ranked picks to show (<=0 = all; default 5)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON (default: human-readable text for prompt injection)")
    return p


def _emit_no_backlog(path: Path, as_json: bool) -> int:
    if as_json:
        print(json.dumps({
            "action": "candidates-top", "project": None, "note": "no-backlog",
            "counts": {"pickable": 0, "blocked": 0, "in_flight": 0},
            "top": [], "blocked": [], "in_flight": [],
        }, ensure_ascii=False))
    else:
        print(
            f"No candidates.json yet at {path} — run /discover (slice 1) or "
            f"/slice-candidates (brownfield) to populate the backlog."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success (incl. empty/absent backlog), 1 runtime error, 2 usage error."""
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    path = _root(args.vault) / "candidates.json"

    if not path.exists():
        return _emit_no_backlog(path, args.json)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return _emit_no_backlog(path, args.json)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"candidates_top: {path} is not valid JSON: {exc}\n")
        return 1
    if not isinstance(data, dict):
        sys.stderr.write(f"candidates_top: {path} top-level is not a JSON object\n")
        return 1

    cands = data.get("candidates")
    if cands is None:
        cands = []
    if not isinstance(cands, list):
        sys.stderr.write(f"candidates_top: {path} 'candidates' is not a JSON array\n")
        return 1

    project = data.get("project") or "<unnamed>"
    live_ids = {c.get("id") for c in cands if isinstance(c, dict)}

    pickable: list[dict] = []
    blocked: list[dict] = []
    in_flight: list[dict] = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        bucket = _classify(c)
        if bucket == "pickable":
            pickable.append(c)
        elif bucket == "blocked":
            blocked.append(c)
        elif bucket == "in_flight":
            in_flight.append(c)

    # Rank pickable: highest score first, then smaller effort, then id (stable).
    pickable.sort(key=lambda c: (-_score(c), _effort_rank(c), str(c.get("id"))))
    ranked = [(c, _unmet_deps(c, live_ids)) for c in pickable]
    blocked.sort(key=lambda c: str(c.get("id")))
    in_flight.sort(key=lambda c: str(c.get("id")))

    fmt = _fmt_json if args.json else _fmt_text
    sys.stdout.write(fmt(project, ranked, blocked, in_flight, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
