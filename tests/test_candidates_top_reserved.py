"""candidates_top classifies 'reserved' as in-flight (not pickable) + renders a null-slice hold
coherently (slice-027 / SC-053 / AC2 + DR-1 M-add-1).

Fails on HEAD: candidates_top._classify routes 'reserved' -> 'other' -> the held candidate is
dropped from all buckets, so a parallel /slice would re-list it as pickable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOP = _REPO / "skills" / "slice" / "scripts" / "candidates_top.py"


def _write(vault, cands):
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "t",
         "candidates": cands, "pick_log": []}), encoding="utf-8")


def _run(vault, *args):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    return subprocess.run([sys.executable, str(_TOP), "--vault", str(vault), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _reserved(cid="SC-001"):
    return {"id": cid, "title": cid.lower(), "status": "reserved", "progress": "reserved",
            "slice": None, "claimed_by": {"git_user": "Owner A", "git_email": "a@test"},
            "started_at": "2026-06-22T00:00:00Z", "history": [],
            "priority": {"score": 3, "effort": "S"}}


def _pickable(cid="SC-002"):
    return {"id": cid, "title": cid.lower(), "status": "candidate", "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None,
            "priority": {"score": 5, "effort": "S"}}


def test_reserved_classified_in_flight_not_pickable(tmp_path):
    """AC2: a reserved candidate is in_flight, excluded from the ranked pickable list."""
    vault = tmp_path
    _write(vault, [_reserved("SC-001"), _pickable("SC-002")])
    cp = _run(vault, "--json")
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    in_flight_ids = [c["id"] for c in payload["in_flight"]]
    top_ids = [c["id"] for c in payload["top"]]
    assert "SC-001" in in_flight_ids, "reserved must be classified in_flight"
    assert "SC-001" not in top_ids, "reserved must NOT appear in the pickable list"
    assert "SC-002" in top_ids, "a real candidate is still pickable"


def test_reserved_in_flight_row_renders_coherently(tmp_path):
    """M-add-1: the In-flight text row for a null-slice hold must not show a bare '?' slice."""
    vault = tmp_path
    _write(vault, [_reserved("SC-001")])
    cp = _run(vault)  # text output
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert "SC-001" in out, "the reserved hold must be surfaced under In-flight"
    assert "held" in out.lower(), "a null-slice reserved row must render a 'held' marker, not a bare '?'"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
