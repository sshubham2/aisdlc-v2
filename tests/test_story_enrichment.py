"""Render fixtures for the enriched slice-story (slice-005): the tournament beat
("How we chose the approach") + the single problems-and-resolutions thread ("What went
wrong, and what we did") must render clean (exit 0), the no-jargon tripwire must STILL
fire on the highest-leak section (exit 3), the no-tournament graceful-degrade path must
render, and the pre-build front-half must still render with back-half artifacts absent.

Renders render_story.py as a subprocess (its real CLI contract), like
test_render_story_jargon.py. Per the feasibility spike, real archived slices here carry
NO tournament block, so the rich content is exercised ONLY by these hand-made fixtures —
they are the contract for the enriched narrator's output shape (M2 / m2).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "slice-story" / "scripts" / "render_story.py"


def _render(tmp_path: Path, data: dict, *extra: str) -> subprocess.CompletedProcess:
    src = tmp_path / "story-sections.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "story.html"
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--sections-file", str(src), "--out", str(out), *extra],
        capture_output=True, text=True, encoding="utf-8",
    )


def _base(**overrides) -> dict:
    d = {
        "_schema": "aisdlc/story-sections@1",
        "slice": "slice-021",
        "title": "realtime-presence",
        "headline": "Show teammates who's viewing a document, live.",
        "stage": "pre-build",
        "tldr_md": "A short plain-language summary.",
        "sections": [],
    }
    d.update(overrides)
    return d


# (a) POSITIVE — the tournament beat + a single problems-and-resolutions thread, all
#     plain-language (trace codes only in `ref`), renders clean.
def test_positive_tournament_and_problems_render_clean(tmp_path):
    data = _base(stage="shipped", sections=[
        {
            "heading": "How we chose the approach",
            "body_md": (
                "Three approaches were weighed. We **built on** the convergent-replica option, "
                "**borrowed part of** the managed pub/sub idea, and **set aside** the causal-order log — "
                "because the live-view code already speaks the chosen protocol and the simplest shape was enough."
            ),
            "items": [
                {"label": "Built on: convergent-replica over a one-way live feed.",
                 "ref": "design.tournament", "badge": "decision"},
                {"label": "Set aside: an explicit causal-order log (more than this slice needed).",
                 "badge": "decision"},
            ],
        },
        {
            "heading": "What went wrong, and what we did",
            "body_md": (
                "A few things needed fixing along the way, each handled before shipping.\n\n"
                "- An early experiment showed one server tapped out around 120 live connections, so we "
                "switched to a lightweight polling channel instead of an always-open socket.\n"
                "- An independent reviewer caught that the live feed didn't check who was asking; we made it "
                "require a signed-in session before it opens.\n"
                "- Real-device testing surprised us with a reconnect storm on flaky networks; we added backoff."
            ),
            "items": [
                {"label": "One server capped at ~120 connections.",
                 "detail": "Switched to a polling channel.", "ref": "R-27", "badge": "changed-course"},
                {"label": "The live feed didn't check who was asking.",
                 "detail": "Now requires a signed-in session.", "ref": "C1", "badge": "fixed"},
            ],
        },
    ])
    cp = _render(tmp_path, data)
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "How we chose the approach" in html
    assert "What went wrong, and what we did" in html
    assert "built on" in html.lower()  # core/partial/none were translated, not leaked


# (b) NEGATIVE — the problems beat is the highest-leak section; a leak MUST still be
#     blocked (the tripwire stays enforced — must-not-defer).
def test_negative_jargon_in_problems_beat_blocks(tmp_path):
    data = _base(sections=[
        {"heading": "What went wrong, and what we did",
         "body_md": "The Critic flagged C2 and the disposition was accepted-fixed."},
    ])
    cp = _render(tmp_path, data)
    assert cp.returncode == 3
    assert "JARGON-LEAK" in cp.stderr
    assert not (tmp_path / "story.html").exists()


# (c) NO-TOURNAMENT-BUILT — the dominant real case (a single-approach slice): the
#     graceful 'only one approach' note renders clean, no fabricated contest.
def test_no_tournament_graceful_degrade_renders(tmp_path):
    data = _base(stage="shipped", sections=[
        {"heading": "How we chose the approach",
         "body_md": "Only one sensible approach here, so there was no contest to weigh."},
        {"heading": "What we built", "body_md": "A small wording change to the report writer."},
    ])
    cp = _render(tmp_path, data)
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "only one sensible approach" in html.lower()


# (d) PRE-BUILD front-half — renders correctly when the back-half artifacts are absent.
def test_pre_build_front_half_renders(tmp_path):
    data = _base(stage="pre-build", sections=[
        {"heading": "What we set out to do", "body_md": "Make the report tell the whole story."},
        {"heading": "What \"done\" looks like", "body_md": "Five outcomes."},
    ])
    cp = _render(tmp_path, data)
    assert cp.returncode == 0, cp.stderr
    assert (tmp_path / "story.html").is_file()
