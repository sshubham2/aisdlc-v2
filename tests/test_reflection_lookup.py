"""Characterization + behavior tests for reflection_lookup.py (slice-063 / SC-096).

M3 (CC-002): NO test pinned reflection_lookup's stdout SHAPE before this slice. This file locks it — the two
block prefixes, the seam-coherence line, the per-folder structure, and the empty-case notes — so the graded-
scorer refactor cannot silently drift the layout that is injected verbatim into three designers (AC3). It also
proves the behavioral split the refactor introduces: the `lexical` scorer preserves today's >=2 / >=1 gates
byte-for-byte (M1), while the default `tfidf-cosine` scorer surfaces a strong 1-keyword match the old cliff
dropped (AC1), and the scorer-provenance line prints on every branch incl. no-match (M4).

The `shape` + `notes` tests run GREEN against the pre-refactor code too (they characterize today); the
`--scorer`-flag tests are new behavior and pass after the refactor.
"""
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "lib" / "reflection_lookup.py"


def _mb(vault: Path, folder: str, title: str, intent: str = "", acs=None):
    d = vault / "slices" / folder
    d.mkdir(parents=True, exist_ok=True)
    mb = {"slice": folder, "title": title, "intent": intent,
          "acceptance_criteria": [{"id": f"AC{i+1}", "text": t} for i, t in enumerate(acs or [])]}
    (d / "mission-brief.json").write_text(json.dumps(mb), encoding="utf-8")


def _refl(vault: Path, folder: str, lessons):
    d = vault / "slices" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "reflection.json").write_text(json.dumps({"slice": folder, "lessons": lessons}), encoding="utf-8")


def _fixture(vault: Path):
    # LEG1 corpus (mission-briefs): 201 shares TWO query keywords, 202 shares ONE, 203 shares none.
    _mb(vault, "slice-201-alpha", "worktree rebase handling")
    _mb(vault, "slice-202-beta", "worktree tuning notes")
    _mb(vault, "slice-203-gamma", "database schema design")
    # LEG2 corpus (reflections): 201 shares two, 205 shares one, 203 shares none.
    _refl(vault, "slice-201-alpha", ["A worktree rebase merge lesson learned here."])
    _refl(vault, "slice-205-epsilon", ["Worktree only note kept here."])
    _refl(vault, "slice-203-gamma", ["A database schema migration lesson."])


def _run(vault: Path, *extra):
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT), "--vault", str(vault), *extra],
        capture_output=True, text=True)
    return cp


# ── shape (characterization — green pre- and post-refactor) ──────────────────────────

def test_shape_prefixes_and_seam_line(tmp_path):
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    cp = _run(vault, "--keywords", "worktree rebase")
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert "NEAREST PRIOR SLICE (most similar mission):" in out
    assert "Prefer consistency with its approach" in out  # the seam-coherence line
    assert "RELEVANT PAST REFLECTIONS (matched on:" in out
    assert "slice-201-alpha" in out
    # per-folder structure in LEG2: "  <folder>  [matched: ...]" then "     - <lesson>"
    assert "[matched:" in out
    assert "     - A worktree rebase merge lesson" in out


def test_notes_on_empty_cases(tmp_path):
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    # keywords that are all stop-words -> the empty-keyword-set note, exit 0
    cp = _run(vault, "--keywords", "the and for with that this")
    assert cp.returncode == 0, cp.stderr
    assert "no keywords" in cp.stdout.lower()
    # keywords that match nothing -> the no-match note, exit 0
    cp2 = _run(vault, "--keywords", "kubernetes helm istio")
    assert cp2.returncode == 0, cp2.stderr
    assert "no past slice matches" in cp2.stdout.lower()


def test_ambiguous_active_slice_note(tmp_path):
    # >=2 in-flight slices from a non-git cwd -> the ambiguous note, exit 0 (the original incident's honest path).
    vault = tmp_path / "v"; vault.mkdir()
    (vault / "slices" / "slice-001-a").mkdir(parents=True)
    (vault / "slices" / "slice-001-a" / "milestone.json").write_text(json.dumps({"stage": "design"}), encoding="utf-8")
    (vault / "slices" / "slice-002-b").mkdir(parents=True)
    (vault / "slices" / "slice-002-b" / "milestone.json").write_text(json.dumps({"stage": "build"}), encoding="utf-8")
    nongit = tmp_path / "nongit"; nongit.mkdir()
    cp = _run(vault, "--from-mission-brief", "--repo-root", str(nongit))
    assert cp.returncode == 0, cp.stderr
    assert "ambiguous" in (cp.stdout + cp.stderr).lower()


# ── behavior split: lexical preserves the cliff, cosine surfaces 1-keyword (post-refactor) ──

def test_lexical_scorer_preserves_the_gte2_cliff(tmp_path):
    """M1: the `lexical` scorer keeps today's LEG1 >=2 gate — slice-202 (1 shared keyword) does NOT appear."""
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    cp = _run(vault, "--keywords", "worktree rebase", "--scorer", "lexical")
    assert cp.returncode == 0, cp.stderr
    assert "slice-201-alpha" in cp.stdout          # 2 shared -> present
    # 202 shares only "worktree" (1) -> excluded from the nearest-slice leg under the >=2 cliff
    assert "slice-202-beta" not in cp.stdout


def test_cosine_scorer_surfaces_one_keyword_match(tmp_path):
    """AC1: the default graded scorer surfaces slice-202 (a single strong shared keyword) the >=2 cliff dropped."""
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    cp = _run(vault, "--keywords", "worktree rebase")  # default scorer = tfidf-cosine
    assert cp.returncode == 0, cp.stderr
    assert "slice-201-alpha" in cp.stdout
    assert "slice-202-beta" in cp.stdout             # now surfaced (was below the old cliff)


def test_provenance_line_on_every_branch(tmp_path):
    """M4: the scorer-provenance line prints even when nothing matches -> never a silent-empty return."""
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    # a match -> provenance present
    cp = _run(vault, "--keywords", "worktree rebase")
    assert "[recall:" in cp.stdout.lower() or "[scorer:" in cp.stdout.lower()
    # NO match -> the note AND the provenance are both present (the incident class)
    cp2 = _run(vault, "--keywords", "kubernetes helm istio")
    assert "no past slice matches" in cp2.stdout.lower()
    assert "[recall:" in cp2.stdout.lower() or "[scorer:" in cp2.stdout.lower()


def test_design_slice_consumer_wires_slice_and_scorer():
    """M-add-1: the SOLE automated consumer (design-slice) must invoke reflection_lookup as a BODY step passing
    --slice "$ARG" + --scorer, so graded recall reaches the ambiguous-active trigger. A dead flag on the
    automated path (a bare `!`-injection --from-mission-brief, which cannot bind $ARG) must not ship green."""
    import re as _re
    skill = (_REPO / "skills" / "design-slice" / "SKILL.md").read_text(encoding="utf-8")
    # the recall call passes BOTH the graded scorer and the disambiguating --slice "$ARG"
    assert '--slice "$ARG" --scorer tfidf-cosine' in skill
    # and it is NOT resolved in a load-time `!`-injection (which runs before ${ARGUMENTS} binds)
    for bang in _re.findall(r"```!\n(.*?)\n```", skill, _re.DOTALL):
        assert "reflection_lookup.py" not in bang, "reflection_lookup must run in a BODY step, not a !-injection"


def test_unknown_scorer_degrades_to_lexical(tmp_path):
    """AC2: an unknown/failing scorer degrades to the lexical legs with a visible note, exit 0, no crash."""
    vault = tmp_path / "v"; vault.mkdir()
    _fixture(vault)
    cp = _run(vault, "--keywords", "worktree rebase", "--scorer", "does-not-exist")
    assert cp.returncode == 0, cp.stderr
    assert "fallback" in cp.stdout.lower() or "unavailable" in cp.stdout.lower()
    assert "slice-201-alpha" in cp.stdout  # lexical legs still produce results
