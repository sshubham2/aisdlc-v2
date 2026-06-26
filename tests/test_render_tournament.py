"""Tests for skills/slice-story/scripts/render_tournament.py (slice-039).

Loads the renderer by path (it is a single-skill script, not an importable package), then exercises
render() against fixture slice folders: the three designer sections, M2 honest badge labels, M5 escaping,
M4 which-reviews-ran panel + vault-root gate-log filtering, AC5 honest degrade, and M-add-2 (renders from
any folder, including an archive path).
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_RT_PATH = Path(__file__).resolve().parents[1] / "skills" / "slice-story" / "scripts" / "render_tournament.py"
_spec = importlib.util.spec_from_file_location("render_tournament_under_test", _RT_PATH)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _make_slice(tmp_path: Path, *, experts=None, critique=True, critique_review=True,
                critique_skipped=False, proposals=True, name="slice-039-x") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    _write(d / "design.json", {
        "slice": "slice-039",
        "tournament": {
            "proposals": [
                {"designer": "designer-practice", "selected": "partial"},
                {"designer": "designer-crossdomain", "selected": "core"},
                {"designer": "designer-expert", "selected": "core"},
            ],
            "selection_rationale": "all three converged on a faithful read-only renderer.",
        },
    })
    if proposals:
        _write(d / "design-proposals.json", {
            "_schema": "aisdlc/design-proposals@1", "slice": "slice-039",
            "proposals": [
                {"designer": "designer-practice", "approach": "a practical page-builder",
                 "prior_art": [{"pattern": "adr-viewer", "where": "https://github.com/mrwilson/adr-viewer", "authority": "oss"}],
                 "over_engineering_flag": True, "over_engineering_note": "a live checker is over-built"},
                {"designer": "designer-crossdomain", "approach": "PKI trust-anchor framing", "transfer_found": True,
                 "cross_domain_transfer": {"source_domain": "PKI", "pattern": "trust-anchor",
                                           "invariants": [{"precondition": "offline-distinguishable", "status": "holds", "evidence": "string shape"}]}},
                {"designer": "designer-expert", "approach": "Nygard ADR read-only projection",
                 "channeled_experts": experts if experts is not None else
                     [{"name": "Michael Nygard", "source": "https://martinfowler.com/bliki/ArchitectureDecisionRecord.html"}],
                 "staleness_note": "ADR canon predates LLM records"},
            ],
        })
    progress = [{"step": "critique", "done": "skipped" if critique_skipped else False}]
    _write(d / "milestone.json", {"slice": "slice-039", "progress": progress})
    if critique:
        _write(d / "critique.json", {"slice": "slice-039", "verdict": "needs-fixes"})
    if critique_review:
        _write(d / "critique-review.json", {"slice": "slice-039", "verdict": "extend"})
    return d


def _gate_log(tmp_path: Path) -> Path:
    p = tmp_path / "gate-log.json"
    _write(p, {"entries": [
        {"slice": "slice-039", "gate": "design-tournament", "verdict": "overlapping", "reality_contact": "low"},
        {"slice": "slice-039", "gate": "risk-spike", "verdict": "go", "reality_contact": "high"},
        {"slice": "slice-007", "gate": "critique", "verdict": "clean", "reality_contact": "low"},  # other slice
    ]})
    return p


# ----------------------------------------------------------------- core render
def test_renders_three_designer_sections(tmp_path):
    d = _make_slice(tmp_path)
    html, code = rt.render(d, _gate_log(tmp_path))
    assert code == 0
    assert "Practice (battle" in html
    assert "Cross-domain" in html
    assert "Expert-channeled" in html
    assert "Michael Nygard" in html
    assert "The three approaches, at a glance" in html  # summary block


def test_badge_labels_are_honest_M2(tmp_path):
    experts = [
        {"name": "Real Cite", "source": "https://martinfowler.com/x.html"},
        {"name": "From Memory", "source": "training-knowledge"},
        {"name": "No Source Person"},  # dict without source
    ]
    d = _make_slice(tmp_path, experts=experts)
    html, code = rt.render(d, None)
    assert code == 0
    assert ">cites a source<" in html
    assert ">self-attested<" in html
    assert ">no source<" in html
    # M2: the human-visible badge must NEVER claim more than presence
    assert ">verified<" not in html
    assert ">proven real<" not in html


# ----------------------------------------------------------------- AC5 honest degrade
def test_absent_proposals_renders_no_contest_page(tmp_path):
    d = _make_slice(tmp_path, proposals=False)
    html, code = rt.render(d, None)
    assert code == 0  # honest degrade, NOT an error
    assert "No design contest" in html


def test_malformed_proposals_exit_1(tmp_path):
    d = _make_slice(tmp_path)
    (d / "design-proposals.json").write_text("{not valid json", encoding="utf-8")
    _html, code = rt.render(d, None)
    assert code == 1


# ----------------------------------------------------------------- M5 security (escaping)
def test_source_markup_is_escaped_M5(tmp_path):
    experts = [{"name": "X", "source": 'https://e.com/x"><script>alert(1)</script>'}]
    d = _make_slice(tmp_path, experts=experts)
    html, _ = rt.render(d, None)
    assert "<script>alert(1)</script>" not in html  # no raw markup breakout
    assert "&lt;script&gt;" in html  # escaped instead


def test_apostrophe_url_is_escaped_M5(tmp_path):
    # A real corpus example with an embedded apostrophe must not break a quoted href attribute.
    experts = [{"name": "DRY", "source": "https://en.wikipedia.org/wiki/Don't_repeat_yourself"}]
    d = _make_slice(tmp_path, experts=experts)
    html, _ = rt.render(d, None)
    assert "Don't" not in html.split("<footer")[0] or "&#x27;" in html  # apostrophe escaped in attr context
    assert "&#x27;" in html


# ----------------------------------------------------------------- M4 which-reviews-ran
def test_reviews_panel_ran(tmp_path):
    d = _make_slice(tmp_path, critique=True, critique_review=True)
    html, _ = rt.render(d, _gate_log(tmp_path))
    assert "Independent design review:</b> ran" in html
    assert "Second-pass meta-review:</b> ran" in html


def test_reviews_panel_skipped_vs_not_run(tmp_path):
    # critique skipped (milestone marker, no critique.json) + meta-review absent
    d = _make_slice(tmp_path, critique=False, critique_review=False, critique_skipped=True)
    html, _ = rt.render(d, None)
    assert "deliberately skipped" in html
    assert "Second-pass meta-review:</b> not run" in html


def test_reviews_panel_did_not_run(tmp_path):
    d = _make_slice(tmp_path, critique=False, critique_review=False, critique_skipped=False)
    html, _ = rt.render(d, None)
    assert "Independent design review:</b> did not run" in html


def test_gate_log_filtered_to_this_slice_M4(tmp_path):
    d = _make_slice(tmp_path)
    html, _ = rt.render(d, _gate_log(tmp_path))
    assert "design-tournament" in html
    assert "risk-spike" in html
    # the other slice's row (slice-007 critique) must NOT leak in
    assert html.count("reality contact") == 2  # exactly the 2 slice-039 rows


# ----------------------------------------------------------------- M-add-2 (renders from any folder)
def test_renders_from_archive_path(tmp_path):
    archive_dir = tmp_path / "slices" / "archive"
    archive_dir.mkdir(parents=True)
    d = _make_slice(archive_dir, name="slice-039-archived")
    html, code = rt.render(d, None)
    assert code == 0
    assert "Expert-channeled" in html


# ----------------------------------------------------------------- code-review minors (m1, m2)
def test_unknown_designer_id_escaped_once_m1(tmp_path):
    d = _make_slice(tmp_path)
    p = json.loads((d / "design-proposals.json").read_text(encoding="utf-8"))
    p["proposals"].append({"designer": "designer-<b>x</b>", "approach": "y"})
    (d / "design-proposals.json").write_text(json.dumps(p), encoding="utf-8")
    html, _ = rt.render(d, None)
    assert "&lt;b&gt;x&lt;/b&gt;" in html  # escaped exactly once
    assert "&amp;lt;" not in html  # not double-escaped


def test_recall_miss_gate_row_m2(tmp_path):
    d = _make_slice(tmp_path)
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps({"entries": [
        {"slice": "slice-039", "gate": "code-review", "kind": "miss"},
    ]}), encoding="utf-8")
    html, _ = rt.render(d, gl)
    assert "recall MISS" in html
    assert "verdict ?" not in html


# --- slice-043: render_body() extraction + scoped_css() isolation -----------------

def test_render_body_is_a_fragment_no_page_chrome(tmp_path):
    d = _make_slice(tmp_path)
    body, code, slice_id, title = rt.render_body(d, None)
    assert code == 0
    # a fragment: NO doctype / head / style / footer chrome
    assert "<!doctype" not in body.lower()
    assert "<head" not in body.lower()
    assert "<style" not in body.lower()
    # ...but it DOES carry the inner blocks
    assert 'class="card designer"' in body
    assert slice_id == "slice-039"


def test_render_is_page_of_render_body_seam(tmp_path):
    """Feathers seam: render() must equal _page() wrapped around render_body() (byte-identical CLI output)."""
    d = _make_slice(tmp_path)
    gl = tmp_path / "gl.json"
    gl.write_text(json.dumps({"entries": []}), encoding="utf-8")
    body, code, slice_id, title = rt.render_body(d, gl)
    full, full_code = rt.render(d, gl)
    assert code == full_code == 0
    assert full == rt._page(slice_id, title, body)


def test_render_body_malformed_returns_code_1(tmp_path):
    d = _make_slice(tmp_path)
    (d / "design-proposals.json").write_text("{ not json", encoding="utf-8")
    body, code, slice_id, title = rt.render_body(d, None)
    assert code == 1
    assert "not valid JSON" in body


def test_scoped_css_isolates_under_tournament_scope_M2(tmp_path):
    css = rt.scoped_css()
    # the load-bearing collision: .badge is scoped, not global
    assert ".tournament-scope .badge{" in css
    # M2: the bare code/a rules become DESCENDANTS, never global (they would bleed into the story half)
    assert ".tournament-scope code{" in css
    assert ".tournament-scope a{" in css
    assert ".tournament-scope a:hover{" in css
    assert not re.search(r"(?m)^code\{", css)
    assert not re.search(r"(?m)^a\{", css)
    # the shell-hoisted rules are DROPPED (the composer's shell supplies value-identical :root + *)
    assert ":root" not in css
    assert not re.search(r"(?m)^body\{", css)
    assert not re.search(r"(?m)^h1\{", css)
    assert not re.search(r"(?m)^\*\{", css)
    # @media print survives with its inner class rule scoped + the inner bare body dropped
    assert "@media print{" in css
    assert ".tournament-scope .card{break-inside" in css


def test_no_contest_body_has_no_page_chrome(tmp_path):
    body = rt._no_contest_body("nothing here")
    assert "No design contest was captured" in body
    assert "<!doctype" not in body.lower()
    # and the standalone no-contest PAGE still wraps it (back-compat)
    page = rt._no_contest_page("slice-039", "nothing here")
    assert page == rt._page("slice-039", "Design tournament", body)


# --- code-review m1: guard the scoped_css brace-scanner's '_CSS is plain' precondition -------------
# The hand-rolled find('{')/find('}') splitter is correct ONLY while _CSS has no CSS comment and no brace
# inside a quoted string. Both are absent today; pin them so a future _CSS edit that violates the
# precondition fails HERE (loudly) rather than silently mis-scoping and reintroducing the M2 story-half bleed.

def test_scoped_css_precondition_no_css_comment_m1():
    # a `/* ... */` comment would fold into the FOLLOWING selector and silently corrupt scoping.
    assert "/*" not in rt._CSS


def test_scoped_css_precondition_no_brace_inside_quotes_m1():
    # a brace inside a quoted string (e.g. content:"}") would mis-split the scanner. _CSS legitimately
    # contains quotes (font-family "Segoe UI"), so assert the precise invariant: no { or } inside any "...".
    for quoted in re.findall(r'"[^"]*"', rt._CSS):
        assert "{" not in quoted and "}" not in quoted, f"brace inside quoted string breaks the scanner: {quoted!r}"


def test_scoped_css_output_is_brace_balanced_m1():
    css = rt.scoped_css()
    assert css.count("{") == css.count("}")   # a mis-split would leave the merged <style> unbalanced
    assert css.count("{") >= 20               # all the real rules survived the scope pass
