"""DD-9 jargon tripwire — render_story.py refuses pipeline jargon in prose fields.

Runs the renderer as a subprocess (its real CLI contract): exit 3 + JARGON-LEAK on
stderr when prose leaks a banned token; --allow-jargon downgrades to a warning;
clean input renders; `ref` fields are the sanctioned home for trace tags (no flag).
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


def _story(body: str, **overrides) -> dict:
    d = {
        "_schema": "aisdlc/story-sections@1",
        "slice": "slice-021",
        "title": "realtime-presence",
        "headline": "Show teammates who's viewing a document, live.",
        "stage": "pre-build",
        "tldr_md": "A short plain-language summary.",
        "sections": [{"heading": "What we set out to do", "body_md": body}],
    }
    d.update(overrides)
    return d


def test_clean_story_renders(tmp_path):
    cp = _run(tmp_path, _story("The page polls a lightweight channel every 3 seconds."))
    assert cp.returncode == 0, cp.stderr
    assert (tmp_path / "story.html").is_file()


def test_rule_code_in_prose_blocks(tmp_path):
    cp = _run(tmp_path, _story("The TRI-1 gate ratified everything."))
    assert cp.returncode == 3
    assert "JARGON-LEAK" in cp.stderr and "TRI-1" in cp.stderr
    assert not (tmp_path / "story.html").exists()  # nothing written on a blocked render


def test_trace_id_in_prose_blocks_but_ref_field_is_sanctioned(tmp_path):
    # ADR id in body_md → leak
    cp = _run(tmp_path, _story("We locked ADR-014 for this."))
    assert cp.returncode == 3 and "ADR-014" in cp.stderr
    # same id in items[].ref → fine (refs are the sanctioned home)
    clean = _story("We committed to a server-sent-events feed here.")
    clean["sections"][0]["items"] = [
        {"label": "Locked the live-feed decision.", "ref": "ADR-014", "badge": "decision"}
    ]
    cp = _run(tmp_path, clean)
    assert cp.returncode == 0, cp.stderr


def test_plumbing_vocab_blocks(tmp_path):
    cp = _run(tmp_path, _story("The Critic reviewed the blast-radius of the change."))
    assert cp.returncode == 3
    assert "blast-radius" in cp.stderr.lower()


def test_allow_jargon_downgrades_to_warning(tmp_path):
    cp = _run(tmp_path, _story("The TRI-1 gate ratified everything."), "--allow-jargon")
    assert cp.returncode == 0, cp.stderr
    assert "JARGON-LEAK" in cp.stderr  # still reported, no longer blocking
    assert (tmp_path / "story.html").is_file()


def test_signoff_prose_is_scanned(tmp_path):
    d = _story("Plain body.")
    d["signoff"] = {"model_approved": [{"what": "C2 was accepted-pending.", "by": "review"}]}
    cp = _run(tmp_path, d)
    assert cp.returncode == 3
    assert "signoff.model_approved[0].what" in cp.stderr
