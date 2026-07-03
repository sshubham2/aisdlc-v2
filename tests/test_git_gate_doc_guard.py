"""slice-058 / SC-107 / M3 — region-keyed doc-guard for the git-at-open gate.

AC1/AC2 are PROSE-enforced (nothing structurally refuses a vault write in a
non-git dir), so this guard is the compensating control (critique M3): it pins
that BOTH openers carry the gate, that the gate PRECEDES the first vault write,
that the decline branch is fail-closed (states 'write NO vault artifact'), and
that the explicit-skip branch names the acute later-git-init re-key/orphan
failure (M-add-2). Paired positive+negative assertions, slice-038/051 pattern.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TRIAGE = (_ROOT / "skills" / "triage" / "SKILL.md").read_text(encoding="utf-8")
_ADOPT = (_ROOT / "skills" / "adopt" / "SKILL.md").read_text(encoding="utf-8")


def _idx(hay: str, needle: str) -> int:
    i = hay.find(needle)
    assert i != -1, f"missing marker: {needle!r}"
    return i


# ── /triage ───────────────────────────────────────────────────────────────────

def test_triage_gate_precedes_first_vault_write():
    # ordering: the gate heading precedes the first `<vault>/` write (Step 5a)
    assert _idx(_TRIAGE, "git-at-open gate") < _idx(_TRIAGE, "Write `<vault>/triage.json`")


def test_triage_gate_has_probe_and_consented_actuator():
    assert "git rev-parse --is-inside-work-tree" in _TRIAGE   # probe (no new tool)
    assert "git-init --root ." in _TRIAGE                     # consented actuator
    assert "never self-consent" in _TRIAGE.lower() or "actuator never self-consents" in _TRIAGE.lower()


def test_triage_decline_is_fail_closed():
    # NEGATIVE assertion: the decline branch writes nothing
    assert "Write NO vault artifact" in _TRIAGE


def test_triage_skip_warns_concretely():
    # M-add-2: the explicit-skip branch names the acute later-git-init re-key/orphan failure
    assert "RE-KEY" in _TRIAGE.upper() and "orphan" in _TRIAGE.lower()


def test_triage_mandates_post_gate_reresolution():
    # B1: the load-time injection is pre-git and must NOT be reused for writes
    assert "post-gate re-resolution" in _TRIAGE.lower()
    assert "do not reuse the load-time" in _TRIAGE.lower()


# ── /adopt ──────────────────────────────────────────────────────────────────────

def test_adopt_gate_precedes_slice_candidates_write():
    # M-add-1: the gate precedes Step 3, where /slice-candidates writes candidates.json
    assert _idx(_ADOPT, "git-at-open gate") < _idx(_ADOPT, "## Step 3 — offer /diagnose")


def test_adopt_gate_enumerates_slice_candidates_as_write_site():
    gate_region = _ADOPT[_idx(_ADOPT, "git-at-open gate"):_idx(_ADOPT, "## Step 3 — offer /diagnose")]
    assert "/slice-candidates" in gate_region and "candidates.json" in gate_region


def test_adopt_decline_is_fail_closed():
    assert "Write NO vault artifact" in _ADOPT


def test_adopt_mandates_post_gate_reresolution():
    assert "post-gate re-resolution" in _ADOPT.lower()
    assert "do not reuse the load-time" in _ADOPT.lower()
