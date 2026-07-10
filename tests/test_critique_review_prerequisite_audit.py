"""slice-066 / SC-119 — CRP-1 gains 'full-tournament-convergence' as a 4th MANDATORY
DR-1 trigger, sourced from design.json (via tournament_convergence).

This is the FIRST unit test for critique_review_prerequisite_audit.py, so it does
double duty (critique M4 / CC-002):
  - NEW behaviour: a fully-convergent design with no critique-review.json + no skip
    REFUSES (exit 1) with 'full-tournament-convergence' in mandatory_triggers -- run
    at the AUDIT-FOLDER level (a real fixture folder + subprocess), which pins that the
    trigger is APPENDED into `triggers` BEFORE the `if not triggers:` acceptance branch;
    a classify()-level or pre-seeded-trigger test would miss the placement.
  - CHARACTERIZATION: the three existing triggers (risk_tier=high / critic_required /
    findings>=5) and the 0/1/2 exit contract behave exactly as before -- proven WITH and
    WITHOUT design.json present, so the new design.json read cannot regress them.

TF-1: the NEW-behaviour tests are written FAILING before the impl (the audit does not
yet read design.json); the characterization tests pin the pre-change behaviour and pass now.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

AUDIT = "skills/build-slice/scripts/critique_review_prerequisite_audit.py"

_VALID_SKIP = "skip - rationale: deliberately deferred for this test"


def _write(folder: Path, name: str, obj) -> None:
    (folder / name).write_text(json.dumps(obj), encoding="utf-8")


def _design(divs):
    names = ["designer-practice", "designer-crossdomain", "designer-expert"]
    ad = [{"pair": [names[i % 3], names[(i + 1) % 3]], "divergence": d} for i, d in enumerate(divs)]
    return {"slice": "slice-x", "tournament": {"approach_divergence": ad}}


CONVERGENT = _design(["overlapping", "overlapping", "overlapping"])
DISJOINT = _design(["overlapping", "disjoint", "overlapping"])
OUT_OF_ENUM = _design(["overlapping", "divergent", "overlapping"])


def _slice(tmp_path: Path, *, risk_tier="low", critic_required=False, findings=0,
           design=None, critique_review=False, skip=None, milestone=True):
    """Build a fixture slice folder and return its path."""
    d = tmp_path / "slice-x"
    d.mkdir(exist_ok=True)
    _write(d, "mission-brief.json", {"risk_tier": risk_tier, "critic_required": critic_required})
    if milestone:
        ms = {"slice": "slice-x"}
        if skip is not None:
            ms["critique-review-skip"] = skip
        _write(d, "milestone.json", ms)
    if findings:
        _write(d, "critique.json", {"findings": [{"id": f"C{i}"} for i in range(findings)]})
    if design is not None:
        _write(d, "design.json", design)
    if critique_review:
        _write(d, "critique-review.json", {"verdict": "accept"})
    return d


def _run(run_script, folder: Path):
    r = run_script(AUDIT, [str(folder), "--json"])
    payload = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
    return r, payload


# ============ NEW: the convergence trigger (AC2, M4 placement) ==============

def test_convergent_only_slice_refuses_build(run_script, tmp_path):
    # low tier, not critic_required, 0 findings, NO critique-review, NO skip:
    # the ONLY thing that can refuse is the new convergence trigger -> proves it
    # reaches the refuse branch (append BEFORE `if not triggers:`).
    folder = _slice(tmp_path, design=CONVERGENT)
    r, payload = _run(run_script, folder)
    assert r.returncode == 1, r.stdout
    assert "full-tournament-convergence" in payload["mandatory_triggers"]


def test_convergent_with_critique_review_accepts(run_script, tmp_path):
    folder = _slice(tmp_path, design=CONVERGENT, critique_review=True)
    r, _ = _run(run_script, folder)
    assert r.returncode == 0, r.stdout


def test_convergent_with_valid_skip_accepts(run_script, tmp_path):
    folder = _slice(tmp_path, design=CONVERGENT, skip=_VALID_SKIP)
    r, payload = _run(run_script, folder)
    assert r.returncode == 0, r.stdout
    # the trigger still HELD (skip discharges it, it is not absent)
    assert "full-tournament-convergence" in payload["mandatory_triggers"]


# ============ AC4: no false-positive + no crash =============================

def test_disjoint_design_does_not_trigger(run_script, tmp_path):
    folder = _slice(tmp_path, design=DISJOINT)
    r, payload = _run(run_script, folder)
    assert r.returncode == 0, r.stdout
    assert "full-tournament-convergence" not in payload.get("mandatory_triggers", [])


def test_absent_design_json_does_not_crash_or_trigger(run_script, tmp_path):
    folder = _slice(tmp_path, design=None)  # no design.json at all
    r, payload = _run(run_script, folder)
    assert r.returncode == 0, r.stderr  # graceful, never exit 2 / exception
    assert "full-tournament-convergence" not in payload.get("mandatory_triggers", [])


def test_out_of_enum_design_is_indeterminate_no_trigger(run_script, tmp_path):
    # M2: malformed approach_divergence must NOT fire the trigger, and must be surfaced.
    folder = _slice(tmp_path, design=OUT_OF_ENUM)
    r, payload = _run(run_script, folder)
    assert r.returncode == 0, r.stdout
    assert "full-tournament-convergence" not in payload.get("mandatory_triggers", [])
    # fail-visible: the convergence status is surfaced in --json
    assert payload.get("convergence", {}).get("state") == "indeterminate"


def test_indeterminate_never_read_as_convergent(run_script, tmp_path):
    folder = _slice(tmp_path, design=OUT_OF_ENUM)
    _, payload = _run(run_script, folder)
    assert payload.get("convergence", {}).get("is_full_convergence") is False


# ============ CHARACTERIZATION: existing triggers + exit contract ============
# Each existing trigger fires alone; proven WITH a disjoint design.json AND with none,
# so the new design.json read cannot regress the existing behaviour.

@pytest.mark.parametrize("design", [None, DISJOINT], ids=["no-design", "disjoint-design"])
def test_no_trigger_accepts(run_script, tmp_path, design):
    folder = _slice(tmp_path, risk_tier="low", critic_required=False, findings=0, design=design)
    r, _ = _run(run_script, folder)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("design", [None, DISJOINT], ids=["no-design", "disjoint-design"])
def test_high_tier_alone_refuses(run_script, tmp_path, design):
    folder = _slice(tmp_path, risk_tier="high", design=design)
    r, payload = _run(run_script, folder)
    assert r.returncode == 1, r.stdout
    assert "risk_tier=high" in payload["mandatory_triggers"]


@pytest.mark.parametrize("design", [None, DISJOINT], ids=["no-design", "disjoint-design"])
def test_critic_required_alone_refuses(run_script, tmp_path, design):
    folder = _slice(tmp_path, critic_required=True, design=design)
    r, payload = _run(run_script, folder)
    assert r.returncode == 1, r.stdout
    assert "critic_required=true" in payload["mandatory_triggers"]


@pytest.mark.parametrize("design", [None, DISJOINT], ids=["no-design", "disjoint-design"])
def test_findings_threshold_alone_refuses(run_script, tmp_path, design):
    folder = _slice(tmp_path, findings=5, design=design)
    r, payload = _run(run_script, folder)
    assert r.returncode == 1, r.stdout
    assert any("findings=" in t for t in payload["mandatory_triggers"])


def test_missing_milestone_is_usage_error(run_script, tmp_path):
    folder = _slice(tmp_path, milestone=False, design=CONVERGENT)
    r, _ = _run(run_script, folder)
    assert r.returncode == 2


def test_existing_trigger_with_valid_skip_accepts(run_script, tmp_path):
    # the skip-rationale hatch still discharges an existing trigger, design.json present or not
    folder = _slice(tmp_path, risk_tier="high", design=DISJOINT, skip=_VALID_SKIP)
    r, _ = _run(run_script, folder)
    assert r.returncode == 0, r.stdout
