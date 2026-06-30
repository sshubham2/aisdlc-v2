"""AC4 (slice-047): /build-slice documents the walking_skeleton marker-flip discipline.

A layer's `status` flips pending -> exercised ONLY after its verification actually
ran. This guard is non-vacuous: it pins the load-bearing clauses, so if the prose
drifts back to silence (or drops the STOP / loud-advisory distinction) it goes red.
"""
from __future__ import annotations

from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "build-slice" / "SKILL.md"


def test_marker_flip_discipline_documented():
    text = _SKILL.read_text(encoding="utf-8")
    low = text.lower()
    # the discipline is named for walking_skeleton + architectural_layers
    assert "walking_skeleton marker-flip" in low or "marker-flip discipline" in low
    assert "architectural_layers" in text
    # the core rule: pending -> exercised ONLY AFTER the verification ran
    assert "pending" in low and "exercised" in low
    assert "only after" in low
    assert "verification" in low
    # the enforcement teeth: a decidable failure is a hard STOP, not a silent pass
    assert "stop" in low
    # the M-add-1 distinction survives: a not-runnable command is a loud advisory
    assert "loud advisory" in low


def test_marker_flip_guard_is_non_vacuous():
    # Prove the guard would actually fail if the discipline prose were removed: the
    # exact sentinel phrase must be present. Normalize whitespace first because the
    # markdown source wraps long lines (a newline where the sentence has a space).
    normalized = " ".join(_SKILL.read_text(encoding="utf-8").split())
    assert "ONLY after that layer's `verification` command has actually been run" in normalized
