"""Combined-path tests for the consolidated /slice-story report (slice-043, M-add-1).

render_story.py is now the single COMPOSER: given --slice-dir + --gate-log it composes the design-tournament
detail INTO one story.html as a second region (the former separate tournament.html is gone). These tests drive
the REAL CLI (subprocess) against fixture slice folders -- the exact gap the meta-Critic (M-add-1) flagged: the
old jargon test harness could not reach the --slice-dir compose path nor assert the new exit-4 contract.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDER_STORY = ROOT / "skills" / "slice-story" / "scripts" / "render_story.py"


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _story_sections() -> dict:
    return {
        "_schema": "aisdlc/story-sections@1",
        "slice": "slice-043", "title": "consolidate-slice-story-html", "stage": "pre-build",
        "tldr_md": "A short plain-language summary.",
        "sections": [{"heading": "What we set out to do", "body_md": "Plain language, no jargon here."}],
    }


def _make_slice(tmp_path: Path, *, proposals: str = "valid") -> Path:
    """proposals: 'valid' (real contest), 'malformed' (broken JSON -> exit 4), 'absent' (no-contest)."""
    d = tmp_path / "slice-043-x"
    d.mkdir(parents=True, exist_ok=True)
    _write(d / "design.json", {"slice": "slice-043", "tournament": {"proposals": [
        {"designer": "designer-practice", "selected": "core"},
        {"designer": "designer-crossdomain", "selected": "partial"},
        {"designer": "designer-expert", "selected": "core"}], "selection_rationale": "converged."}})
    _write(d / "milestone.json", {"slice": "slice-043", "progress": [{"step": "critique", "done": True}]})
    _write(d / "critique.json", {"slice": "slice-043", "verdict": "needs-fixes"})
    _write(d / "critique-review.json", {"slice": "slice-043", "verdict": "extend"})
    if proposals == "valid":
        _write(d / "design-proposals.json", {"_schema": "aisdlc/design-proposals@1", "slice": "slice-043",
            "proposals": [
                {"designer": "designer-practice", "approach": "practical composer"},
                {"designer": "designer-crossdomain", "approach": "linker analogy", "transfer_found": True},
                {"designer": "designer-expert", "approach": "Feathers seam",
                 "channeled_experts": [{"name": "Michael Feathers", "source": "https://example.com/x"}]}]})
    elif proposals == "malformed":
        (d / "design-proposals.json").write_text("{ not valid json", encoding="utf-8")
    # 'absent': leave no design-proposals.json -> the no-contest region
    return d


def _run(sections: dict, out: Path, *extra: str, tmp: Path) -> subprocess.CompletedProcess:
    src = tmp / "story-sections.json"
    src.write_text(json.dumps(sections), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RENDER_STORY), "--sections-file", str(src), "--out", str(out), *extra],
        capture_output=True, text=True, encoding="utf-8")


def test_combined_one_file_both_halves(tmp_path):
    d = _make_slice(tmp_path, proposals="valid")
    out = tmp_path / "story.html"
    r = _run(_story_sections(), out, "--slice-dir", str(d), tmp=tmp_path)
    assert r.returncode == 0
    h = out.read_text(encoding="utf-8")
    # story half AND tournament half in the SAME document (AC2)
    assert "What we set out to do" in h
    assert 'class="tournament-scope"' in h
    assert "Practice (battle-tested patterns)" in h
    # exactly ONE document -- one doctype / one body / one :root (AC1: not two stitched pages)
    assert h.lower().count("<!doctype") == 1
    assert len(re.findall(r"<body", h)) == 1
    assert h.count(":root{") == 1
    # the tournament CSS is scoped (M2): .badge resolves under the region, not globally
    assert ".tournament-scope .badge{" in h
    assert not re.search(r"(?m)^code\{", h)  # no global bare code rule bled in from the tournament half
    # m4 region heading demarcates the two halves
    assert "The design tournament behind this slice" in h


def test_story_prose_jargon_blocks_the_whole_combine(tmp_path):
    d = _make_slice(tmp_path, proposals="valid")
    out = tmp_path / "story.html"
    bad = _story_sections()
    bad["sections"] = [{"heading": "h", "body_md": "This leaks TRI-1 into the prose."}]
    r = _run(bad, out, "--slice-dir", str(d), tmp=tmp_path)
    assert r.returncode == 3            # AC3: the jargon tripwire gates the whole combine
    assert not out.exists()             # nothing written
    assert "JARGON-LEAK" in r.stderr


def test_malformed_tournament_delivers_story_with_notice_exit_4(tmp_path):
    d = _make_slice(tmp_path, proposals="malformed")
    out = tmp_path / "story.html"
    r = _run(_story_sections(), out, "--slice-dir", str(d), tmp=tmp_path)
    assert r.returncode == 4            # M3: a NEW distinct code, NOT 1 ('nothing written')
    assert out.exists()                 # M1: the keystone story half IS delivered
    h = out.read_text(encoding="utf-8")
    assert "What we set out to do" in h
    assert "could not be rendered" in h     # the visible tournament-unavailable notice (AC4)
    assert "unavailable" in r.stderr.lower()


def test_no_contest_renders_one_valid_file_exit_0(tmp_path):
    d = _make_slice(tmp_path, proposals="absent")
    out = tmp_path / "story.html"
    r = _run(_story_sections(), out, "--slice-dir", str(d), tmp=tmp_path)
    assert r.returncode == 0            # AC4: honest degradation, still a valid single page
    h = out.read_text(encoding="utf-8")
    assert "No design contest was captured" in h
    assert h.lower().count("<!doctype") == 1


def test_story_only_back_compat_without_slice_dir(tmp_path):
    """Without --slice-dir render_story is unchanged: story-only, no tournament region (back-compat)."""
    out = tmp_path / "story.html"
    r = _run(_story_sections(), out, tmp=tmp_path)
    assert r.returncode == 0
    h = out.read_text(encoding="utf-8")
    assert "What we set out to do" in h
    assert 'class="tournament-scope"' not in h
