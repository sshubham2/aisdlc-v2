"""scripts/lib/slice_index_regen.py — deterministic slice-index regenerator (slice-030 / SC-008).

test_first conformance guard for the index<->example reconciliation. Proves the regenerator's
output matches the canonical schema-by-example DOWN TO EVERY ENTRY (artifact_lint only checks
top-level keys -- slice-013/M3), is idempotent (byte-identical re-runs incl. a >=2-active fixture
-- M2), degrades gracefully on malformed/missing/empty input (must-not-defer), loses no entries
(set-equality, not count -- m2/AC4), derives a robust first-sentence summary (m3), and never emits
an out-of-enum 'reflect' stage for an active slice (m1). Would FAIL against the pre-fix thin shape.
"""
from __future__ import annotations

import json

import pytest

from scripts.lib import artifact_lint
from scripts.lib import slice_index_regen as sir

PIN = "2026-06-23T12:00:00Z"


# --- fixture-vault builders -------------------------------------------------

def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def _slice(vault, nnn, name, *, intent="## Intent\nA test slice. Second sentence.",
           stage="complete", archived=True, at="2026-01-01T00:00:00Z",
           mb=True, milestone=True, reflection=None):
    """Create slice-<nnn>-<name> under slices/ (active) or slices/archive/ (archived)."""
    base = vault / "slices" / ("archive" if archived else "") / f"slice-{nnn}-{name}"
    base = (vault / "slices" / "archive" / f"slice-{nnn}-{name}") if archived else (vault / "slices" / f"slice-{nnn}-{name}")
    base.mkdir(parents=True, exist_ok=True)
    if mb:
        _write(base / "mission-brief.json", {"slice": f"slice-{nnn}", "title": name, "intent": intent})
    if milestone:
        _write(base / "milestone.json", {"slice": f"slice-{nnn}", "stage": stage, "at": at})
    if archived:
        _write(base / "reflection.json", {"slice": f"slice-{nnn}", "at": (reflection or at)})
    return base


@pytest.fixture
def example_keysets():
    ex = artifact_lint._load_examples()
    # expected per-entry key-sets DERIVED from the canonical example (not literals -- M3)
    return {
        "active": set(ex["slice-index"]["active"][0]),
        "recent": set(ex["slice-index"]["recent"][0]),
        "archive": set(ex["slice-archive-index"]["slices"][0]),
        "live_top": {k for k in ex["slice-index"] if not k.startswith("_")},
        "archive_top": {k for k in ex["slice-archive-index"] if not k.startswith("_")},
    }


# --- AC3: artifact_lint clean (top-level) -----------------------------------

def test_generated_indexes_lint_clean_top_level(vault, tmp_path, run_script, example_keysets):
    _slice(vault, "001", "alpha", archived=True, at="2026-01-01T00:00:00Z")
    _slice(vault, "002", "beta", archived=False, stage="design")
    live, arch = sir.regenerate(vault, PIN)
    # top-level key-sets match the canonical example exactly
    assert {k for k in live if not k.startswith("_")} == example_keysets["live_top"]
    assert {k for k in arch if not k.startswith("_")} == example_keysets["archive_top"]
    # and artifact_lint (the real authority) passes on both
    lp = tmp_path / "live.json"; ap = tmp_path / "arch.json"
    lp.write_text(json.dumps(live), encoding="utf-8")
    ap.write_text(json.dumps(arch), encoding="utf-8")
    assert run_script("scripts/lib/artifact_lint.py", [str(lp), "--type", "slice-index"]).returncode == 0
    assert run_script("scripts/lib/artifact_lint.py", [str(ap), "--type", "slice-archive-index"]).returncode == 0


# --- M3 / slice-013: DESCEND EVERY entry, key-set == example ----------------

def test_every_entry_shape_matches_example(vault, example_keysets):
    for i in range(3):
        _slice(vault, f"01{i}", f"arch{i}", archived=True, at=f"2026-01-0{i+1}T00:00:00Z")
    _slice(vault, "020", "act0", archived=False, stage="build")
    _slice(vault, "021", "act1", archived=False, stage="design")
    live, arch = sir.regenerate(vault, PIN)
    assert live["active"], "expected active entries"
    assert live["recent"], "expected recent entries"
    assert arch["slices"], "expected archive entries"
    for e in live["active"]:
        assert set(e) == example_keysets["active"], f"active entry drift: {set(e)}"
    for e in live["recent"]:
        assert set(e) == example_keysets["recent"], f"recent entry drift: {set(e)}"
    for e in arch["slices"]:
        assert set(e) == example_keysets["archive"], f"archive entry drift: {set(e)}"


def test_red_against_thin_writer_shape(example_keysets):
    # the PRE-FIX thin shape would FAIL the per-entry assertion -- proves the guard bites (AC5)
    thin = {"slice": "slice-001", "folder": "archive/slice-001-x", "title": "x",
            "stage": "complete", "one_liner": "..."}
    assert set(thin) != example_keysets["archive"]
    assert set(thin) != example_keysets["recent"]


# --- M2: idempotency byte-identical, incl. >=2 active -----------------------

def test_idempotent_byte_identical_with_multiple_active(vault):
    _slice(vault, "001", "a", archived=True, at="2026-01-01T00:00:00Z")
    _slice(vault, "030", "act-z", archived=False, stage="design")
    _slice(vault, "031", "act-a", archived=False, stage="design")  # 2 active -> ordering must be fixed
    r1 = sir.regenerate(vault, PIN)
    r2 = sir.regenerate(vault, PIN)
    canon = lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False)
    assert canon(r1[0]) == canon(r2[0]), "live index not byte-identical across runs"
    assert canon(r1[1]) == canon(r2[1]), "archive index not byte-identical across runs"
    # active[] sorted by NNN (deterministic) -- 030 before 031
    assert [e["slice"] for e in r1[0]["active"]] == ["slice-030", "slice-031"]


# --- must-not-defer: graceful degrade ---------------------------------------

def test_degrade_malformed_missing_empty(vault):
    _slice(vault, "001", "good", archived=True, at="2026-01-01T00:00:00Z")
    # malformed mission-brief
    bad = vault / "slices" / "archive" / "slice-002-malformed"
    bad.mkdir(parents=True)
    (bad / "mission-brief.json").write_text("{ not json ", encoding="utf-8")
    (bad / "reflection.json").write_text(json.dumps({"at": "2026-01-02T00:00:00Z"}), encoding="utf-8")
    (bad / "milestone.json").write_text(json.dumps({"stage": "complete", "at": "2026-01-02T00:00:00Z"}), encoding="utf-8")
    # archived folder missing mission-brief entirely
    nomb = vault / "slices" / "archive" / "slice-003-nomb"
    nomb.mkdir(parents=True)
    (nomb / "reflection.json").write_text(json.dumps({"at": "2026-01-03T00:00:00Z"}), encoding="utf-8")
    live, arch = sir.regenerate(vault, PIN)  # must NOT raise
    ids = {e["slice"] for e in arch["slices"]}
    assert {"slice-001", "slice-002", "slice-003"} <= ids, f"a degraded slice was DROPPED: {ids}"


def test_empty_and_missing_vault_no_crash(tmp_path):
    empty = tmp_path / "empty_vault"; empty.mkdir()
    live, arch = sir.regenerate(empty, PIN)
    assert live["active_count"] == 0 and live["archived_count"] == 0 and live["total"] == 0
    assert live["active"] == [] and live["recent"] == [] and arch["slices"] == []
    # a vault whose slices/ dir does not exist at all
    live2, arch2 = sir.regenerate(tmp_path / "does_not_exist", PIN)
    assert live2["total"] == 0


# --- m2 / AC4: set-equality (not count), recent cap, no loss -----------------

def test_set_equality_and_recent_cap(vault):
    for i in range(1, 13):  # 12 archived -> recent capped at 10, full catalog = 12
        _slice(vault, f"{i:03d}", f"arch{i}", archived=True, at=f"2026-01-{i:02d}T00:00:00Z")
    _slice(vault, "200", "act", archived=False, stage="build")
    live, arch = sir.regenerate(vault, PIN)
    archive_ids = {e["slice"] for e in arch["slices"]}
    folder_ids = {f"slice-{i:03d}" for i in range(1, 13)}
    assert archive_ids == folder_ids, "archive set != folder set (a slice was lost/added)"
    assert len(live["recent"]) == 10, "recent[] must be capped at 10"
    assert arch["total"] == 12 and live["active_count"] == 1
    assert {e["slice"] for e in live["active"]} == {"slice-200"}


# --- m3: robust first-sentence summary --------------------------------------

def test_summary_first_sentence_robust(vault):
    # colon-led + '## Intent' header + a code-ref token containing '.'
    _slice(vault, "001", "x", archived=True, at="2026-01-01T00:00:00Z",
           intent="## Intent\nFix the slices/_index.json drift here. Then a second sentence that should be dropped.")
    live, arch = sir.regenerate(vault, PIN)
    s = arch["slices"][0]["summary"]
    assert s, "summary must be non-empty"
    assert len(s) <= 500
    assert "## Intent" not in s, "the markdown header must be stripped"
    assert "slices/_index.json" in s, "must NOT truncate mid-token at the '.' inside _index.json"
    assert "second sentence" not in s, "must keep only the first sentence"


# --- m1: active stage never the out-of-enum 'reflect' -----------------------

def test_active_stage_never_reflect(vault):
    # a non-archived folder whose milestone LEGACY-says 'reflect' (the slice-015 landmine)
    s = vault / "slices" / "slice-040-legacy"
    s.mkdir(parents=True)
    (s / "mission-brief.json").write_text(json.dumps({"title": "legacy", "intent": "x. y."}), encoding="utf-8")
    (s / "milestone.json").write_text(json.dumps({"stage": "reflect", "at": "2026-02-01T00:00:00Z"}), encoding="utf-8")
    (s / "design.json").write_text(json.dumps({"slice": "slice-040"}), encoding="utf-8")
    live, _ = sir.regenerate(vault, PIN)
    stages = {e["stage"] for e in live["active"]}
    assert "reflect" not in stages, "active[] must never carry the out-of-enum 'reflect' stage (m1)"
    # file-presence rule: design.json present, no build/validate/reflection -> 'design'
    assert live["active"][0]["stage"] == "design"


# --- m4: imports _read_milestone from one named module ----------------------

def test_reuses_read_milestone_import():
    from scripts.lib.latest_archived_slice import _read_milestone as src
    assert sir._read_milestone is src, "must REUSE latest_archived_slice._read_milestone (m4), not re-copy"


# --- CLI contract -----------------------------------------------------------

def test_cli_emit_live_and_archive(vault, run_script):
    _slice(vault, "001", "a", archived=True, at="2026-01-01T00:00:00Z")
    r_live = run_script("scripts/lib/slice_index_regen.py", ["--vault", str(vault), "--emit", "live", "--updated", PIN])
    assert r_live.returncode == 0, r_live.stderr
    live = json.loads(r_live.stdout)
    assert live["_schema"] == "aisdlc/slice-index@1" and live["updated"] == PIN
    r_arch = run_script("scripts/lib/slice_index_regen.py", ["--vault", str(vault), "--emit", "archive", "--updated", PIN])
    assert r_arch.returncode == 0, r_arch.stderr
    assert json.loads(r_arch.stdout)["_schema"] == "aisdlc/slice-archive-index@1"


def test_cli_empty_vault_valid_index(vault, run_script):
    r = run_script("scripts/lib/slice_index_regen.py", ["--vault", str(vault), "--emit", "live", "--updated", PIN])
    assert r.returncode == 0, r.stderr
    live = json.loads(r.stdout)
    assert live["total"] == 0 and live["active"] == [] and live["recent"] == []


def test_cli_out_file_no_bom_matches_stdout(vault, tmp_path, run_script):
    _slice(vault, "001", "a", archived=True, at="2026-01-01T00:00:00Z")
    of = tmp_path / "idx_new.json"
    r = run_script("scripts/lib/slice_index_regen.py",
                   ["--vault", str(vault), "--emit", "live", "--updated", PIN, "--out-file", str(of)])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", "with --out-file, stdout must be empty (content goes to the file)"
    raw = of.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "out-file must be UTF-8 without a BOM (CAS-safe)"
    assert json.loads(raw.decode("utf-8"))["_schema"] == "aisdlc/slice-index@1"
