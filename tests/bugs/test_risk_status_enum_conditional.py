"""
Bug (SC-006): artifact_lint REJECTS a valid risk-register.json whose risk status is
"conditional".

The risk-register status enum is enforced by THREE disagreeing sources of truth:
  - scripts/lib/artifact_lint.py:70       -> {open, mitigated, accepted, closed, retired}
  - scripts/lib/risk_register_audit.py:78 -> {open, mitigating, retired, accepted, blocking, conditional}
  - skills/risk-spike/SKILL.md  Step 5    -> WRITES status in {retired, blocking, conditional}

So a real CONDITIONAL/NO-GO spike that writes risks[].status = "conditional" is ACCEPTED
by risk_register_audit (and is exactly what risk-spike writes) but REJECTED by
artifact_lint -- the two enforcement gates disagree on what a legal status is.

Expected: artifact_lint accepts a risk-register.json carrying status="conditional"
          (zero violations on the status field) once the enum is reconciled across all
          three sources.
Actual:   artifact_lint reports `risks[].status = 'conditional' not in
          ['accepted', 'closed', 'mitigated', 'open', 'retired']` and exits 1.

This test PASSES when the canonical status enum includes the spike-written values.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LINT = REPO / "scripts" / "lib" / "artifact_lint.py"


def _lint(register: dict) -> dict:
    """Run the real artifact_lint CLI over a register written to a temp file; return its
    --json result ({"checked", "violations", "warnings"}). File-path arg avoids any
    inline-JSON shell quoting trap on Windows."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "risk-register.json"
        f.write_text(json.dumps(register), encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(LINT), str(f), "--json"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        assert out.returncode in (0, 1), f"unexpected exit {out.returncode}: {out.stderr}"
        return json.loads(out.stdout)


def _minimal_register(status: str) -> dict:
    """A minimal, otherwise-valid risk-register.json (every required top-level key
    present) carrying a single risk with the given status."""
    return {
        "_schema": "aisdlc/risk-register@1",
        "project": "repro",
        "risks": [{"id": "R-1", "title": "x", "status": status}],
        "updated": "2026-06-15T00:00:00Z",
    }


def test_open_status_is_the_control():
    """Sanity: an already-allowed status ('open') lints CLEAN -- proves the fixture
    shape itself is valid, so the conditional failure below isolates the enum, not
    unrelated missing-key noise."""
    res = _lint(_minimal_register("open"))
    assert res["violations"] == [], res["violations"]


def test_conditional_status_lints_clean():
    """The bug: a risk written with the conditional spike verdict must lint clean."""
    res = _lint(_minimal_register("conditional"))
    status_violations = [v for v in res["violations"] if "status" in v]
    assert status_violations == [], (
        "artifact_lint rejected a valid 'conditional' risk status -- SC-006 enum "
        f"divergence (artifact_lint vs risk_register_audit vs risk-spike): {status_violations}"
    )
