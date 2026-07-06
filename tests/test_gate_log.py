"""scripts/lib/gate_log.py — the measurement-spine row emitter.

Covers the 3.1c addition: BCSG-1 `build-checks` is now a model-tier (`low`
reality-contact) gate in the spine, and the reality spine stays `high`.
"""
from __future__ import annotations

import json

from scripts.lib.gate_log import GATE_CONTACT, INFORMATIONAL_GATES


def test_build_checks_is_model_tier():
    assert GATE_CONTACT["build-checks"] == "low"


def test_build_checks_not_informational():
    # it CAN raise findings (unacknowledged-critical) -> a real verdict gate, not informational
    assert "build-checks" not in INFORMATIONAL_GATES


def test_reality_spine_stays_high():
    assert GATE_CONTACT["risk-spike"] == "high"
    assert GATE_CONTACT["validate-slice"] == "high"


def test_model_gates_stay_low():
    for g in ("critique", "critique-review", "code-review", "build-checks"):
        assert GATE_CONTACT[g] == "low"


def test_build_checks_row_via_cli(run_script):
    r = run_script("scripts/lib/gate_log.py",
                   ["--gate", "build-checks", "--slice", "slice-007-x",
                    "--verdict", "clean", "--findings-count", "0",
                    "--mode", "standard", "--tier", "medium"])
    assert r.returncode == 0, r.stderr
    row = json.loads(r.stdout.strip())
    assert row["gate"] == "build-checks"
    assert row["reality_contact"] == "low"
    assert row["verdict"] == "clean"
    assert row["findings_count"] == 0
    assert row["slice"] == "slice-007"  # canonicalized


def test_note_written_only_when_passed(run_script):
    # --note records a degraded-input marker (e.g. design ran without CRG context);
    # omitted -> the key is absent (omit-empty convention), passed -> carried verbatim.
    base = ["--gate", "design-tournament", "--slice", "slice-009",
            "--verdict", "disjoint", "--findings-count", "0"]
    r = run_script("scripts/lib/gate_log.py", base)
    assert r.returncode == 0, r.stderr
    assert "note" not in json.loads(r.stdout.strip())
    r = run_script("scripts/lib/gate_log.py", base + ["--note", "crg-context:unavailable"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip())["note"] == "crg-context:unavailable"


def test_unknown_gate_exit2(run_script):
    r = run_script("scripts/lib/gate_log.py",
                   ["--gate", "bogus-gate", "--slice", "slice-001",
                    "--verdict", "clean", "--findings-count", "0"])
    assert r.returncode == 2
