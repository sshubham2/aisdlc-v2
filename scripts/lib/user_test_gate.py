"""user_test_gate.py — the real-user-validation firewall for /user-test (slice-044 / SC-076).

Two pure functions the MAIN THREAD calls (never the sim agent — the guardrails are enforced
in code, not by a self-reporting model on the axis where LLM self-judgment is least reliable):

  is_real_user_validated(session) -> bool
      The ONE canonical definition of 'this slice is real-user-validated'. ADAPTED to the
      PRODUCTION user-tests/<name>.json schema (ADR-034, supersedes ADR-031's verbatim claim):
      True iff participants>=1 AND findings[] holds >=1 entry explicitly tagged
      source=='real-user' (NO default). Structurally blind to the heuristic_walkthrough
      sibling, so no shape of heuristic / laundered / untagged data can satisfy it (AC3).

  ingest_heuristic_walkthrough(raw, *, artifact_ai_generated=False) -> dict
      Normalizes the forked sim agent's raw return into the stored heuristic_walkthrough
      section, ENFORCING the A1 guardrails the agent cannot be trusted to self-police:
        - A1.G1: drop any finding without a non-empty verbatim evidence_quote;
        - A1.G3: force confidence='low' on any predicts_interaction finding;
        - A1.G5: set an echo_chamber_caveat when the artifact under review is itself AI-generated.
      Tolerates a missing / empty / malformed / {status:'skipped'} return by yielding a
      DEFINED skip-with-note section (status=='skipped', findings==[]) — never a crash — so
      /user-test can fall through to its normal real-user flow unchanged (AC4 / M7).

This module is the canonical predicate any future UX-validated consumer (validate-slice,
reflect) MUST call rather than re-derive the rule (ADR-031 #consequences; m1: it gates
nothing until such a consumer exists).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent  # scripts/lib/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_REAL_USER = "real-user"

_ECHO_CAVEAT = ("This artifact was itself AI-generated, so a model reviewing it shares its "
                "blind spots (echo chamber). Treat these heuristic findings as especially weak "
                "and confirm everything with the real-user session.")


def is_real_user_validated(session: Any) -> bool:
    """True iff `session` records a genuine real-user result: participants>=1 AND at least one
    findings[] entry explicitly tagged source=='real-user'. Defensive against any non-dict /
    malformed shape (returns False, never raises). Never reads heuristic_walkthrough."""
    if not isinstance(session, dict):
        return False
    participants = session.get("participants")
    if not isinstance(participants, int) or isinstance(participants, bool) or participants < 1:
        return False
    findings = session.get("findings")
    if not isinstance(findings, list):
        return False
    return any(isinstance(f, dict) and f.get("source") == _REAL_USER for f in findings)


def _skip(note: str) -> dict:
    return {"source": "sim-agent", "color": "heuristic", "status": "skipped",
            "note": note, "findings": []}


def ingest_heuristic_walkthrough(raw: Any, *, artifact_ai_generated: bool = False) -> dict:
    """Normalize + enforce the sim agent's return into the stored heuristic_walkthrough section.
    Degrades to a defined skip-with-note on any unusable input (AC4 / M7)."""
    if not isinstance(raw, dict):
        return _skip("sim agent returned no/invalid object")
    if raw.get("status") == "skipped":
        note = raw.get("note")
        if not (isinstance(note, str) and note.strip()):
            rec = raw.get("recommendation")
            note = (rec.get("rationale") if isinstance(rec, dict) else None) or "sim agent skipped"
        return _skip(note)
    findings = raw.get("findings")
    if not isinstance(findings, list):
        return _skip("sim agent output missing a findings[] list")

    normalized: list[dict] = []
    for f in findings:
        if not isinstance(f, dict):
            continue  # M7: junk elements skipped, never crash
        evidence = f.get("evidence_quote")
        if not (isinstance(evidence, str) and evidence.strip()):
            continue  # A1.G1: a finding without verbatim evidence is dropped
        g = dict(f)
        if g.get("predicts_interaction"):
            g["confidence"] = "low"  # A1.G3: the agent cannot observe dynamic behavior statically
        normalized.append(g)

    disclaimed = raw.get("disclaimed_scopes")
    section: dict = {
        "source": "sim-agent",
        "color": "heuristic",
        "status": "ok",
        "disclaimed_scopes": disclaimed if isinstance(disclaimed, list) else [],
        "findings": normalized,
    }
    if artifact_ai_generated:
        section["echo_chamber_caveat"] = _ECHO_CAVEAT  # A1.G5 (set by the main thread, not the agent)
    return section


def _main(argv: list[str] | None = None) -> int:
    """CLI used by /user-test Step 2.5 (the main thread is the enforcer, not the agent).

      ingest  --raw <agent-json-file> [--ai-generated]   -> normalized heuristic_walkthrough JSON (stdout)
      validate --session <session-json-file>             -> exit 0 if real-user-validated, else 1
    """
    from scripts.lib import _stdout
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="user_test_gate")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest", help="normalize+enforce a sim-agent return into a heuristic_walkthrough section")
    pi.add_argument("--raw", required=True, type=Path, help="file holding the sim agent's raw JSON return")
    pi.add_argument("--ai-generated", action="store_true",
                    help="the artifact under test was itself AI-generated (sets the echo-chamber caveat)")
    pv = sub.add_parser("validate", help="is this session real-user-validated? (the canonical predicate)")
    pv.add_argument("--session", required=True, type=Path, help="file holding the user-tests/<name>.json session")
    args = p.parse_args(argv)

    if args.cmd == "ingest":
        try:
            raw = json.loads(args.raw.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A malformed/absent agent file IS the degrade case — emit a defined skip, never crash (AC4).
            raw = None
            sys.stderr.write(f"user_test_gate: unreadable sim-agent JSON ({exc}); emitting skip\n")
        # BC-PROJ-3: ensure_ascii=False so a finding's verbatim evidence_quote (em-dashes / smart
        # quotes copied from the artifact) round-trips as the literal char, not a \uXXXX escape.
        print(json.dumps(ingest_heuristic_walkthrough(raw, artifact_ai_generated=args.ai_generated),
                         indent=2, ensure_ascii=False))
        return 0

    # validate
    try:
        session = json.loads(args.session.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"user_test_gate: unreadable session ({exc})\n")
        return 1
    return 0 if is_real_user_validated(session) else 1


if __name__ == "__main__":
    sys.exit(_main())
