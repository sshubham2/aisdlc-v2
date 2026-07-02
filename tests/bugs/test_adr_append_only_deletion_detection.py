"""Bug (SC-068 / slice-055): a SEALED ADR deleted from disk is not flagged.

adr_append_only_audit.verify() iterates the on-disk ADR-*.json files and compares each
against the baseline, but it NEVER iterates the baseline's sealed ids. So when a sealed
ADR's file is REMOVED from disk, verify() sees nothing to check and reports clean.

Expected: deleting a sealed ADR is a distinct, fail-visible signal (non-zero exit that
          names the missing ADR id) -- deletion of an immutable record is at least as
          severe as an in-place edit.
Actual:   verify() returns exit 0 'clean' and the deletion is invisible.

This test is written FAILING before the fix (BFRD-1). It stays outcome-focused: it does
NOT pin the exact exit-code number or signal-key name (those are /design-slice decisions),
only that a deleted sealed ADR is no longer reported as clean and is named in the output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "lib" / "adr_append_only_audit.py"
PY = sys.executable


def _write_adr(decisions: Path, adr_id: str, **over) -> Path:
    adr = {
        "_schema": "aisdlc/adr@1",
        "_note": "Append-only.",
        "id": adr_id,
        "title": f"Title for {adr_id}",
        "status": "accepted",
        "reversibility": "cheap",
        "supersedes": None,
        "superseded_by": None,
        "slice": "slice-001",
        "date": "2026-01-01T00:00:00Z",
        "context": "## Context\nsome context",
        "decision": "## Decision\nsome decision",
        "consequences": "## Consequences\nsome consequences",
    }
    adr.update(over)
    p = decisions / f"{adr_id}.json"
    p.write_text(json.dumps(adr, indent=2), encoding="utf-8")
    return p


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(AUDIT), *args], capture_output=True, text=True)


def test_deleted_sealed_adr_is_flagged(tmp_path):
    d = tmp_path / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    # seal both into the baseline sidecar
    assert _run("--decisions", str(d), "--backfill").returncode == 0
    assert _run("--decisions", str(d)).returncode == 0  # clean while both present

    # delete a SEALED ADR from disk (its baseline entry remains)
    (d / "ADR-002.json").unlink()

    r = _run("--decisions", str(d), "--json")
    out = r.stdout + r.stderr
    # BUG: verify() returns 0 'clean' here because it never checks baseline keys.
    assert r.returncode != 0, (
        "deleting a sealed ADR must be fail-visible (non-zero exit), "
        f"got exit {r.returncode}:\n{out}"
    )
    assert "ADR-002" in out, f"the missing sealed ADR must be named:\n{out}"
    # it must not be reported as clean
    try:
        parsed = json.loads(r.stdout)
        assert parsed.get("clean") is not True, f"must not report clean:\n{out}"
    except json.JSONDecodeError:
        assert "clean --" not in out, f"must not report clean:\n{out}"
