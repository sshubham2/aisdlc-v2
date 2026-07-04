"""Unit + integration tests for the pluggable recall scorers (slice-063 / SC-096).

Covers: AC1 (graded surfaces a 1-keyword match, ranked; unrelated excluded), M2 (zero-norm doc never crashes /
degrades the leg), M1 (the `lexical` scorer preserves today's >=2 / >=1 gates), M-add-2 (LEG1 IDF-weighted
set-cosine ranks a rare-keyword share above a common-keyword share), AC5/m1 (a stub scorer registers AND is
DISPATCHED through main() with zero call-site edits), AC4 (--slice resolves a named mission under an ambiguous
multi-in-flight context).
"""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import reflection_scoring as rs  # noqa: E402
from scripts.lib import reflection_lookup  # noqa: E402

_SCRIPT = _REPO / "scripts" / "lib" / "reflection_lookup.py"


# ── tfidf-cosine unit ────────────────────────────────────────────────────────────────

def test_cosine_surfaces_one_keyword_and_excludes_unrelated():
    """AC1: a doc sharing ONE keyword is returned with a positive score, ranked below a 2-share; a doc
    sharing NOTHING is excluded (~0)."""
    docs = [
        {"tokens": ["worktree", "rebase"]},   # 0: 2 shared
        {"tokens": ["worktree", "tuning"]},   # 1: 1 shared
        {"tokens": ["database", "schema"]},   # 2: 0 shared
    ]
    ranked = rs.tfidf_cosine({"tokens": ["worktree", "rebase"]}, docs, leg="nearest_slice")
    ids = [i for i, _ in ranked]
    assert ids and ids[0] == 0        # exact 2-share ranks first
    assert 1 in ids                   # the 1-keyword match now surfaces (AC1)
    assert 2 not in ids               # unrelated excluded
    assert all(s > 0.0 for _, s in ranked)
    # descending order
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_cosine_zero_norm_doc_is_skipped_not_crashed():
    """M2: an empty (zero-norm) doc scores 0 and is skipped INSIDE the scorer — no ZeroDivisionError, and the
    other docs still rank (no whole-leg degrade)."""
    docs = [
        {"tokens": ["alpha", "beta"]},   # 0
        {"tokens": []},                  # 1: empty -> zero norm
        {"tokens": ["alpha"]},           # 2
    ]
    ranked = rs.tfidf_cosine({"tokens": ["alpha"]}, docs, leg="reflections")  # must not raise
    ids = [i for i, _ in ranked]
    assert 1 not in ids               # zero-norm doc skipped
    assert 0 in ids and 2 in ids      # the real docs still ranked


def test_cosine_empty_corpus_and_empty_query_are_safe():
    assert rs.tfidf_cosine({"tokens": ["x"]}, [], leg="reflections") == []
    assert rs.tfidf_cosine({"tokens": []}, [{"tokens": ["x"]}], leg="reflections") == []


def test_cosine_idf_weights_rare_share_above_common_share():
    """M-add-2 / INV-1: on LEG1 keyword SETS (TF=1), a doc sharing a RARE keyword outranks one sharing a
    near-universal keyword — the IDF weighting the old raw >=2 count ignored."""
    docs = [
        {"tokens": ["rare", "aaaa"]},     # 0: shares 'rare' (df=1, high IDF)
        {"tokens": ["common", "bbbb"]},   # 1: shares 'common' (df=3, low IDF)
        {"tokens": ["common", "cccc"]},   # 2
        {"tokens": ["common", "dddd"]},   # 3
    ]
    ranked = dict(rs.tfidf_cosine({"tokens": ["rare", "common"]}, docs, leg="nearest_slice"))
    assert ranked.get(0, 0.0) > ranked.get(1, 0.0)


# ── lexical scorer preserves today's behavior (M1) ───────────────────────────────────

def test_lexical_nearest_slice_keeps_gte2_gate():
    docs = [{"tokens": ["worktree", "rebase"]}, {"tokens": ["worktree"]}]
    ranked = rs.lexical({"tokens": ["worktree", "rebase"]}, docs, leg="nearest_slice")
    assert [i for i, _ in ranked] == [0]   # only the 2-share passes >=2; the 1-share is dropped (the cliff)


def test_lexical_reflections_substring_gte1():
    docs = [{"text": "a realtime worktree note"}, {"text": "unrelated content"}]
    ranked = rs.lexical({"tokens": ["time", "worktree"]}, docs, leg="reflections")
    # 'time' substring-hits inside 'realtime' AND 'worktree' -> doc 0 passes; doc 1 has neither
    assert [i for i, _ in ranked] == [0]


# ── registry + AC5 dispatch-through-main ─────────────────────────────────────────────

def test_registry_register_and_list():
    assert "tfidf-cosine" in rs.list_scorers()
    assert "lexical" in rs.list_scorers()
    assert rs.get_scorer("tfidf-cosine") is rs.tfidf_cosine
    assert rs.get_scorer("nope-not-here") is None


def _mk_vault(tmp_path):
    vault = tmp_path / "v"
    (vault / "slices" / "slice-201-alpha").mkdir(parents=True)
    (vault / "slices" / "slice-201-alpha" / "mission-brief.json").write_text(
        json.dumps({"title": "worktree rebase handling"}), encoding="utf-8")
    (vault / "slices" / "slice-205-eps").mkdir(parents=True)
    (vault / "slices" / "slice-205-eps" / "reflection.json").write_text(
        json.dumps({"lessons": ["A worktree rebase lesson."]}), encoding="utf-8")
    return vault


def test_stub_scorer_is_dispatched_through_main(tmp_path):
    """AC5/m1: a stub scorer registers via the seam and is actually DISPATCHED by main() (its rank() runs and
    its provenance reaches stdout) with ZERO call-site edits — registration alone would be a shallower proxy."""
    calls = []

    @rs.register("test-stub-063")
    def _stub(query, docs, *, leg):
        calls.append(leg)
        return [(0, 1.0)] if docs else []

    try:
        vault = _mk_vault(tmp_path)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = reflection_lookup.main(
                ["--vault", str(vault), "--keywords", "worktree rebase", "--scorer", "test-stub-063"])
        out = buf.getvalue()
        assert rc == 0
        assert calls, "stub scorer was never dispatched through main()"
        assert "[recall: test-stub-063]" in out   # provenance proves the stub (not the default) ran
    finally:
        rs._SCORERS.pop("test-stub-063", None)


# ── AC4: --slice resolves a named mission under ambiguity ────────────────────────────

def test_slice_flag_resolves_named_mission_under_ambiguity(tmp_path):
    """AC4: with >=2 in-flight slices (the ambiguous trigger) from a non-git cwd, --slice resolves the NAMED
    slice's mission and returns graded matches instead of the silent-ambiguous note."""
    vault = tmp_path / "v"
    # the named target (has a mission-brief) + a sibling in-flight (makes active resolution ambiguous)
    (vault / "slices" / "slice-301-target").mkdir(parents=True)
    (vault / "slices" / "slice-301-target" / "mission-brief.json").write_text(
        json.dumps({"title": "worktree rebase handling"}), encoding="utf-8")
    (vault / "slices" / "slice-302-sibling").mkdir(parents=True)
    (vault / "slices" / "slice-302-sibling" / "milestone.json").write_text(
        json.dumps({"stage": "build"}), encoding="utf-8")
    # a reflection to match against
    (vault / "slices" / "slice-305-past").mkdir(parents=True)
    (vault / "slices" / "slice-305-past" / "reflection.json").write_text(
        json.dumps({"lessons": ["A worktree rebase merge lesson."]}), encoding="utf-8")
    nongit = tmp_path / "nongit"; nongit.mkdir()
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT), "--vault", str(vault),
         "--slice", "slice-301", "--repo-root", str(nongit)],
        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert "ambiguous" not in cp.stdout.lower()          # --slice bypassed the ambiguous path (AC4)
    assert "slice-305-past" in cp.stdout                 # graded match surfaced
    assert "[recall:" in cp.stdout.lower()               # provenance present
