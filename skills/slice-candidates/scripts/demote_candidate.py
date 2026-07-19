"""demote_candidate.py — the 'good enough for now' backlog DEMOTE lever (slice-077 / SC-138 / [[ADR-088]]).

`/slice-candidates --demote SC-NNN --reason "..."`. Records a bounded off-path demote on a live
candidate as two presence-symmetric sibling fields (`demoted_at` + `demote_reason`) plus an
append-only `demoted` history event, so its backlog rank drops by the score-space term (−4) at the
`candidates_top` pick surface. The demote NEVER deletes — or even opens for write — the append-only
risk-register.json (AC5): it is a rank-time metadata write on candidates.json ONLY, through the SVW-1
locked seam (`safe_mutate_text`), so a parallel writer can never lose-update it.

ELIGIBILITY GUARD (M2 / M-add-2): a demote is REFUSED when the target is product-scope-sourced
(on-path — a core product capability is not a 'good enough for now' risk) OR in the CRITICAL band
(severity `critical`, or the critical score band `>=9`) — so a critical bug is STRUCTURALLY
non-demotable and a NON-demoted critical always tops the board (AC3). Only a genuinely-low-value
off-path risk can ever be demoted, so a recorded-but-inert demote is impossible.

HONEST SCOPE for 'critical/security' (CR1): a genuinely CRITICAL security bug MATERIALIZES at
severity `critical` / score 9 (`build_backlog._SEV_SCORE`), so the critical band structurally
protects it — 'a critical security bug tops the board' holds. But a MATERIALIZED candidate carries
NO structured security category (`build_backlog` folds the finding category into `rationale` FREE
TEXT, not a structured field), so a SUB-critical security item cannot be reliably auto-detected here
and remains demotable by deliberate, reversible, audited user judgment. Widening protection to
sub-critical security needs a structured security signal on the candidate (a future slice — e.g.
SC-165's component lens); this guard does NOT scan free text (BC-PROJ-4).

FAIL-VISIBLE (never a silent no-op): an unknown candidate id, an empty/whitespace reason, a
non-pickable target (only `candidate`/`deferred`), a malformed file, and a re-demote with a DIFFERENT
reason all fail visible. A re-demote with the SAME reason is an idempotent no-op (the existing audit
record is preserved, never overwritten).

Vault root: `--vault ROOT` overrides `$AI_SDLC_VAULT_ROOT` / the computed default.
Exit 0 success (incl. the idempotent same-reason no-op), 1 runtime refusal (unknown id / ineligible /
non-pickable / malformed / different-reason overwrite / write failure), 2 usage error (empty reason).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout, product_priority, product_scope
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text

_JSON_DUMP = {"indent": 2, "ensure_ascii": False, "sort_keys": False}
_PICKABLE = {"candidate", "deferred"}
# A demote is refused on a CRITICAL-band target so a critical bug is structurally non-demotable.
_CRITICAL_SEVERITIES = {"critical"}
_CRITICAL_SCORE = 9  # the critical band (build_backlog._SEV_SCORE: critical -> 9)


class _DemoteError(RuntimeError):
    """Fail-visible demote refusal → CLI exit 1."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _priority(rec: dict) -> dict:
    pr = rec.get("priority")
    return pr if isinstance(pr, dict) else {}


def _severity(rec: dict) -> str:
    return str(_priority(rec).get("severity") or "").strip().lower()


def _score(rec: dict):
    s = _priority(rec).get("score")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else 0


def _critical_reason(rec: dict) -> str | None:
    """The reason a candidate is non-demotable (it is in the CRITICAL band), else None. This is the
    ENFORCED half of AC3's 'critical/security' guard: a genuinely critical security bug materializes
    at severity `critical` / score 9, so this structurally protects it. Sub-critical security is a
    documented, un-enforced scope (see the module docstring) — this reads ONLY structured severity/
    score, never free text (BC-PROJ-4)."""
    sev = _severity(rec)
    if sev in _CRITICAL_SEVERITIES:
        return f"critical-severity (severity={sev!r})"
    if _score(rec) >= _CRITICAL_SCORE:
        return f"critical band (score={_score(rec)} >= {_CRITICAL_SCORE})"
    return None


def _make_demote_mutate(path: Path, candidate_id: str, reason: str, ts: str, result: dict):
    """SVW-1 mutate (current JSON text -> new JSON text). Applies the demote in-lock; stashes the
    outcome (`demoted` True/False for the idempotent no-op) into `result` for the caller to print."""

    def mutate(text: str) -> str:
        if not text.strip():
            raise _DemoteError(
                f"{path} is empty or missing — no candidates to demote "
                f"(run /discover or /slice-candidates first)")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _DemoteError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise _DemoteError(f"{path} top-level is not a JSON object")
        cands = data.get("candidates")
        if not isinstance(cands, list):
            raise _DemoteError(f"{path} has no candidates[] array")

        rec = next((c for c in cands if isinstance(c, dict) and str(c.get("id")) == candidate_id), None)
        if rec is None:
            raise _DemoteError(f"no candidate with id {candidate_id!r} in the live backlog")

        # Idempotence FIRST (before the guards): a same-reason re-demote of an already-demoted
        # candidate is a no-op success; a DIFFERENT reason refuses (never silently overwrite the
        # append-only audit record). Checked before eligibility so a re-run stays deterministic.
        existing = str(rec.get("demote_reason") or "").strip()
        if existing:
            if existing == reason:
                result["demoted"] = False  # idempotent no-op
                return json.dumps(data, **_JSON_DUMP) + "\n"
            raise _DemoteError(
                f"candidate {candidate_id} is already demoted (reason: {existing!r}) — refusing to "
                f"overwrite the append-only demote record with a different reason {reason!r}")

        # Eligibility guard (M2 / M-add-2): on-path (product-sourced) or critical/security -> refuse.
        if product_scope.owner_ref(rec) is not None:
            raise _DemoteError(
                f"candidate {candidate_id} is product-scope-sourced (on-path: "
                f"{product_scope.owner_ref(rec)}) — a core product capability is not a "
                f"'good enough for now' risk and cannot be demoted")
        crit = _critical_reason(rec)
        if crit:
            raise _DemoteError(
                f"candidate {candidate_id} is {crit} — a critical candidate is structurally "
                f"non-demotable (a demote must never bury it below the pick window; a critical "
                f"security bug materializes in this band and is protected here)")

        # Status guard: only a pickable candidate can be demoted (not active/spiking/blocked/reserved).
        st = rec.get("status")
        if st not in _PICKABLE:
            who = (rec.get("claimed_by") or {}).get("git_user")
            raise _DemoteError(
                f"candidate {candidate_id} is not pickable (status={st!r}"
                + (f", claimed_by {who}" if who else "")
                + ") — only a `candidate`/`deferred` candidate can be demoted")

        # build_demote_record is pure + fail-closed on an empty reason (belt-and-braces: main already
        # rejected an empty reason); the two sibling fields keep the artifact_lint co-constraint by
        # construction (demoted_at truthy <=> demote_reason non-empty).
        record = product_priority.build_demote_record(reason, ts)
        rec["demoted_at"] = record["demoted_at"]
        rec["demote_reason"] = record["demote_reason"]
        hist = rec.get("history")
        if not isinstance(hist, list):
            hist = rec["history"] = []
        hist.append(record["history_event"])
        data["updated"] = ts
        result["demoted"] = True
        return json.dumps(data, **_JSON_DUMP) + "\n"

    return mutate


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="demote_candidate",
        description="Demote a slice candidate ('good enough for now'): lower its backlog rank by a "
                    "bounded off-path term WITHOUT deleting its append-only risk-register entry.")
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--candidate", required=True, metavar="SC-NNN", help="the candidate id to demote")
    p.add_argument("--reason", required=True, metavar="TEXT",
                   help="the non-empty 'good enough for now' reason (recorded, append-only)")
    p.add_argument("--json", action="store_true", help="emit JSON confirmation")
    return p


def main(argv: list[str] | None = None) -> int:
    """Exit 0 success (incl. idempotent no-op), 1 runtime refusal, 2 usage error."""
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    candidate_id = args.candidate.strip()
    if not candidate_id:
        sys.stderr.write("demote_candidate: --candidate must name a candidate id\n")
        return 2
    reason = (args.reason or "").strip()
    if not reason:
        sys.stderr.write(
            "demote_candidate: --reason must be a non-empty 'good enough for now' rationale "
            "(the demote is recorded append-only; a reason-less demote is refused)\n")
        return 2

    ts = _now_iso()
    path = _root(args.vault) / "candidates.json"
    result: dict = {}
    try:
        safe_mutate_text(path, _make_demote_mutate(path, candidate_id, reason, ts, result))
    except _DemoteError as exc:
        sys.stderr.write(f"demote_candidate: {exc}\n")
        return 1
    except (OSError, TimeoutError) as exc:
        sys.stderr.write(f"demote_candidate: write to {path} failed (fail-visible per R-7): {exc}\n")
        return 1

    did = result.get("demoted", False)
    if args.json:
        print(json.dumps({"action": "demote-candidate", "candidate": candidate_id,
                          "demoted": did, "reason": reason, "at": ts}, ensure_ascii=False))
    else:
        print(f"{'demoted' if did else 'already demoted (same reason — no-op)'} {candidate_id} "
              f"(rank lowered off-path; risk-register untouched) — reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
