"""slice-097 / SC-206 / ADR-123 — AC1/AC2/AC3/AC4 doc-guard (APED-1).

The two picker steps are SKILL.md prose + interactive AskUserQuestion gates (not pytest-executable),
so a region-keyed doc-guard grep is the verification (repo norm: test_residue_gate_doc_guard.py /
test_convergence_trigger_doc_sync.py). Each assertion is NON-VACUOUS — it also pins the load-bearing
invariant (setdefault fold, resolve_config UNCHANGED, region included, credentials via boto3 default
chain, the actuator names) so it fails if the real wiring/prose were removed, not on an empty string.

TF-1: written FAILING before the SKILL.md edits.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "setup" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_doc_guard_marker_present():
    assert "[aisdlc:sync-backend-setdefault" in _text(), \
        "the region-keyed sync-backend wiring doc-guard must be present"


def test_doc_guard_pins_setdefault_and_unchanged_resolve_config():
    t = _text()
    assert "setdefault" in t, "the doc-guard must pin the os.environ.setdefault fold (ADR-123)"
    assert "resolve_config" in t and "UNCHANGED" in t, \
        "the doc-guard must state the shipped resolve_config is unchanged (zero-regression)"


def test_doc_guard_is_region_keyed():
    """M-add-2: region is the field with zero prior wiring — the guard must name it so dropping the
    region fold trips the guard."""
    t = _text().lower()
    assert "region" in t
    assert "us-east-1" in t and "eu-west-1" in t, \
        "the guard must warn that dropping the region fold degrades a non-default region"


def test_picker_offers_exactly_local_git_s3_and_names_boto3_chain():
    t = _text()
    assert "{local, git, s3}" in t, "the picker must offer exactly local|git|s3 (default local)"
    assert "boto3 default provider chain" in t or "boto3 default chain" in t, \
        "the picker must name the boto3 default chain as the credential source (AC4)"
    assert "NEVER prompted for or persisted" in t or "never prompted for or persisted" in t.lower() \
        or "Credentials are NEVER" in t, "the picker must state credentials are never persisted"


def test_persist_steps_are_after_the_deps_flow():
    """AC5 structural-by-ordering: the consented config steps must appear AFTER the 'Run setup'
    (deps) section, so a persist failure never aborts the install."""
    t = _text()
    assert t.index("## Run setup") < t.index("## Configure the vault base + sync backend"), \
        "the base/backend steps must come after the deps install (AC5 ordering)"


def test_actuators_are_wired():
    t = _text()
    for verb in ("set-backend", "set-base", "write-pin"):
        assert verb in t, f"the picker prose must invoke the {verb} actuator"
    assert "--s3-project" in t and "--s3-bucket" in t, "s3 non-secret fields must be wired"
