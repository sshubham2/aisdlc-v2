"""slice-082 / SC-184 / [[ADR-093]] — render_story renders the product-shape counts DETERMINISTICALLY.

Two things this pins:
  * M-add-1 count fidelity — the numbers a stakeholder reads come from the projected substrate rendered
    by code, NOT transcribed by the narrator. The test asserts the EXACT counts appear and (AC3) that the
    section degrades honestly on the no-scope / empty / all-unassigned / error states with a zero-error render.
  * M4 / m2 non-vacuity — the narrow underscore-exact tripwire really fires on a transcribed rollup token
    (mutate-and-observe), AND does NOT false-FAIL the legitimate plain-language 'no children' / 'rejected
    only' the narrator must be free to write (the adversarial battery, executed not reasoned).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "slice-story" / "scripts" / "render_story.py"


def _run(tmp_path: Path, data: dict, *extra: str) -> subprocess.CompletedProcess:
    src = tmp_path / "story-sections.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "story.html"
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--sections-file", str(src), "--out", str(out), *extra],
        capture_output=True, text=True, encoding="utf-8",
    )


def _story(**overrides) -> dict:
    d = {
        "_schema": "aisdlc/story-sections@1",
        "slice": "slice-082",
        "title": "wire-slice-story",
        "headline": "Show where this slice fits in the product.",
        "stage": "pre-build",
        "tldr_md": "A short plain-language summary.",
        "sections": [{"heading": "What we set out to do", "body_md": "Plain body."}],
    }
    d.update(overrides)
    return d


_POPULATED = {
    "state": "populated", "unit": "capabilities", "_source": "story_inputs.inject",
    "whole_app": {"done": 6, "in_progress": 3, "total": 16},
    "areas": [
        {"name": "auth", "done": 2, "in_progress": 1, "total": 7, "rank": 1},
        {"name": "billing", "done": 3, "in_progress": 2, "total": 5, "rank": 2},
    ],
    "unassigned": {"done": 1, "in_progress": 0, "total": 4},
}


# ── M-add-1: exact deterministic counts on the page ─────────────────────────────────────────

def test_populated_counts_are_rendered_exactly(tmp_path):
    cp = _run(tmp_path, _story(product_shape=_POPULATED))
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Where this fits in the product" in html
    assert "6 of 16 capabilities" in html                     # whole-app
    assert "(3 in progress)" in html                          # m3 whole-app in-flight
    assert "auth" in html and "2 of 7 built" in html          # per-area
    assert "billing" in html and "3 of 5 built" in html
    assert "(1 in progress)" in html and "(2 in progress)" in html
    assert "Not yet grouped into an area: 1 of 4 built" in html  # cross-cutting unassigned bucket


def test_component_ordering_is_preserved(tmp_path):
    cp = _run(tmp_path, _story(product_shape=_POPULATED))
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert html.index("auth") < html.index("billing")        # rank order as given by the substrate


def test_narrator_framing_renders_above_counts(tmp_path):
    d = _story(product_shape=_POPULATED, product_shape_framing="This slice touches the sign-in area.")
    cp = _run(tmp_path, d)
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "This slice touches the sign-in area." in html
    assert html.index("This slice touches") < html.index("6 of 16 capabilities")


# ── AC3 graceful degrade — every state renders with exit 0, no crash ────────────────────────

def test_no_scope_omits_the_section(tmp_path):
    cp = _run(tmp_path, _story(product_shape={"state": "no_scope"}))
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Where this fits in the product" not in html      # cleanly omitted


def test_missing_product_shape_omits_the_section(tmp_path):
    cp = _run(tmp_path, _story())                             # no product_shape key at all
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Where this fits in the product" not in html


def test_unstamped_narrator_authored_shape_is_never_rendered(tmp_path):
    # CR1: a product_shape a narrator authored (against its persona rule) carries NO inject provenance stamp,
    # so even with plausible counts it must NOT render -- the M-add-1 guarantee never rests on the LLM.
    rogue = json.loads(json.dumps(_POPULATED))
    del rogue["_source"]                                       # narrator can't produce the stamp
    rogue["whole_app"] = {"done": 40404, "in_progress": 0, "total": 40404}   # bogus LLM counts (CSS-safe sentinel)
    cp = _run(tmp_path, _story(product_shape=rogue))
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert 'class="section product-shape"' not in html        # the deterministic section is omitted
    assert "40404" not in html                                # the LLM counts never reach the page


def test_degenerate_unassigned_shows_whole_counts_and_note_no_components(tmp_path):
    shape = {"state": "degenerate_unassigned", "unit": "capabilities", "_source": "story_inputs.inject",
             "whole_app": {"done": 0, "in_progress": 4, "total": 4}, "areas": [],
             "unassigned": {"done": 0, "in_progress": 4, "total": 4},
             "note": "Progress isn't broken down by area yet — every capability is still unassigned to an area."}
    cp = _run(tmp_path, _story(product_shape=shape))
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Where this fits in the product" in html
    assert "0 of 4 capabilities" in html and "(4 in progress)" in html
    assert "still unassigned to an area" in html
    assert "By area:" not in html                            # no per-area breakdown when all unassigned


def test_empty_scope_renders_note(tmp_path):
    shape = {"state": "empty_scope", "unit": "capabilities", "_source": "story_inputs.inject",
             "note": "A product shape is defined, but no capabilities have been broken out into it yet."}
    cp = _run(tmp_path, _story(product_shape=shape))
    assert cp.returncode == 0, cp.stderr
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Where this fits in the product" in html
    assert "no capabilities have been broken out" in html


def test_error_state_is_fail_visible_zero_error_render(tmp_path):
    shape = {"state": "error", "error": "strata-sum conservation breached", "_source": "story_inputs.inject"}
    cp = _run(tmp_path, _story(product_shape=shape))
    assert cp.returncode == 0, cp.stderr                     # renders, never crashes
    html = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "could not be shown" in html and "conservation breached" in html


# ── M4 / m2 non-vacuity — the narrow tripwire fires on the transcription path only ──────────

def test_transcribed_underscore_token_trips_exit3(tmp_path):
    # mutate-and-observe: a rollup identifier transcribed into PROSE reddens the exact field.
    d = _story()
    d["sections"] = [{"heading": "How it's built", "body_md": "We counted the rejected_only bucket."}]
    cp = _run(tmp_path, d)
    assert cp.returncode == 3
    assert "rejected_only" in cp.stderr and "sections[0].body_md" in cp.stderr
    assert not (tmp_path / "story.html").exists()


def test_done_definition_and_pulse_line_and_phrase_trip(tmp_path):
    for token in ("no_children", "pulse_line", "done_definition", "materialized candidate archived"):
        d = _story(tldr_md=f"The report showed the {token} value.")
        cp = _run(tmp_path, d)
        assert cp.returncode == 3, f"{token!r} should trip the tripwire"
        assert token in cp.stderr


def test_legitimate_plain_language_does_not_false_fail(tmp_path):
    # the adversarial battery: the SPACE/HYPHEN plain forms are legitimate prose the narrator must write.
    for phrase in ("no children", "no-children", "rejected only", "rejected-only",
                   "these capabilities are still in progress"):
        d = _story()
        d["sections"] = [{"heading": "Where this fits", "body_md": f"Some areas have {phrase} yet."}]
        cp = _run(tmp_path, d)
        assert cp.returncode == 0, f"{phrase!r} must NOT trip the tripwire: {cp.stderr}"


def test_product_shape_framing_prose_is_scanned(tmp_path):
    # the narrator's framing line is prose -> a transcription there is caught too (m2).
    d = _story(product_shape=_POPULATED, product_shape_framing="See the done_definition for details.")
    cp = _run(tmp_path, d)
    assert cp.returncode == 3
    assert "product_shape_framing" in cp.stderr and "done_definition" in cp.stderr


def test_product_shape_counts_are_not_scanned_as_prose(tmp_path):
    # the substrate is DATA (deterministic), not prose: an area literally named with an underscore token
    # is html-escaped data, never a jargon leak (only prose fields are scanned).
    shape = json.loads(json.dumps(_POPULATED))
    shape["areas"][0]["name"] = "no_children-service"
    cp = _run(tmp_path, _story(product_shape=shape))
    assert cp.returncode == 0, cp.stderr
