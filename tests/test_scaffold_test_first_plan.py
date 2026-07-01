"""slice-051 / SC-062 — the deterministic test_first_plan producer (scaffold_pending_plan +
the scaffold_test_first_plan.py CLI), and the gate it feeds.

The producer/gate gap: `/slice` opted a slice into test_first but wrote NO test_first_plan,
while the TF-1 gate (`SPECS['test_first']`) requires one row per AC — so the builder had to
hand-author it mid-build. This pins the fix (ADR-041 -> ADR-042):
  * AC1 — one PENDING row per declared AC, four keys, ac populated (no hand-authoring).
  * AC2 — the scaffolded plan passes the NON-strict audit (TPHD-1 pre-flight sees it present).
  * AC3 — the STRICT gate still FAILS while any row is PENDING (a head start, never a bypass).
  * AC5 — per-AC MERGE (append only uncovered ACs, never clobber a builder row / M2),
          PRUNE only scaffolder-created PENDING orphans on AC removal/re-id (M-add-1),
          and the CLI's SAME-DIRECTORY atomic write (M-add-3: cross-volume-safe on the
          external vault) + fail-visible errors (must_not_defer).

Written test-first per the slice's test_first discipline. The single authoritative shape is
`SPECS['test_first']` in scripts/lib/brief_variants_audit.py; the producer reuses the gate's
own `_declared_acs` / `_normalize_ac_label`, so producer and gate cannot re-diverge.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lib.brief_variants_audit import SPECS, audit, scaffold_pending_plan


def _kinds(result):
    return {v.kind for v in result.violations}


def _brief(**over) -> dict:
    d = {"variants": {"test_first": True},
         "acceptance_criteria": [{"id": "AC1"}, {"id": "AC2"}, {"id": "AC3"}]}
    d.update(over)
    return d


# ── AC1 — one PENDING row per declared AC, four keys, ac populated ────────────────────

def test_ac1_one_pending_row_per_ac():
    data, notes = scaffold_pending_plan(_brief())
    plan = data["test_first_plan"]
    assert len(plan) == 3, "one row per declared AC"
    for row, ac in zip(plan, ("AC1", "AC2", "AC3")):
        assert set(row) == {"ac", "status", "test_path", "test_function"}, "exactly the four keys"
        assert row["ac"] == ac and row["status"] == "PENDING"
        assert row["test_path"] == "" and row["test_function"] == "", "builder still fills these"
    assert any("appended" in n for n in notes), "the scaffold action is observable (note)"


def test_ac1_not_enabled_is_noop():
    data, notes = scaffold_pending_plan({"variants": {"test_first": False},
                                         "acceptance_criteria": [{"id": "AC1"}]})
    assert "test_first_plan" not in data
    assert any("not enabled" in n for n in notes)


# ── AC2 — the scaffolded plan passes the NON-strict audit (TPHD-1 present) ────────────

def test_ac2_scaffolded_plan_passes_non_strict_audit(tmp_path):
    data, _ = scaffold_pending_plan(_brief())
    p = tmp_path / "mission-brief.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    r = audit(p, SPECS["test_first"])   # non-strict: TPHD-1 pre-flight
    assert r.enabled and not r.violations, (
        "a freshly scaffolded plan must be present + conforming (no missing-section / "
        "empty-table / ac-without-row): {}".format(_kinds(r)))


# ── AC3 — the STRICT gate still FAILS while any row is PENDING ────────────────────────

def test_ac3_strict_gate_still_fails_until_passing(tmp_path):
    data, _ = scaffold_pending_plan(_brief())
    p = tmp_path / "mission-brief.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    r_pending = audit(p, SPECS["test_first"], strict=True, root=tmp_path)
    assert "non-passing-pre-finish" in _kinds(r_pending), (
        "PENDING rows must trip --strict-pre-finish — the stub is never a gate bypass (AC3)")

    # Flip every row to PASSING pointing at a REAL on-disk test (so PTFCD-1/PTFFD-1 pass too),
    # and the strict gate is then clean — proving PENDING was the only thing failing it.
    (tmp_path / "sample_test.py").write_text(
        "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n", encoding="utf-8")
    for row, fn in zip(data["test_first_plan"], ("test_a", "test_b", "test_c")):
        row["status"] = "PASSING"
        row["test_path"] = "sample_test.py"
        row["test_function"] = fn
    p.write_text(json.dumps(data), encoding="utf-8")
    r_passing = audit(p, SPECS["test_first"], strict=True, root=tmp_path)
    assert not r_passing.violations, "all-PASSING with real tests must clear the strict gate: {}".format(
        _kinds(r_passing))


# ── AC5 / M2 — per-AC MERGE: append only uncovered ACs, never clobber a builder row ───

def test_merge_appends_only_uncovered_ac_keeps_builder_row():
    # AC1 already has a builder-authored PASSING row; AC2 is newly declared.
    data = _brief(acceptance_criteria=[{"id": "AC1"}, {"id": "AC2"}],
                  test_first_plan=[{"ac": "AC1", "status": "PASSING",
                                    "test_path": "tests/t.py", "test_function": "test_x"}])
    out, notes = scaffold_pending_plan(data)
    plan = out["test_first_plan"]
    by_ac = {r["ac"]: r for r in plan}
    assert by_ac["AC1"] == {"ac": "AC1", "status": "PASSING",
                            "test_path": "tests/t.py", "test_function": "test_x"}, \
        "the builder's AC1 row must be untouched (never clobbered — M2/must_not_defer)"
    assert by_ac["AC2"]["status"] == "PENDING" and by_ac["AC2"]["test_path"] == "", \
        "the newly-declared AC2 gets a fresh PENDING row (the 'AC added post-/slice' backstop)"
    assert any("AC2" in n for n in notes)


# ── AC5 / M-add-1 — PRUNE only scaffolder-created PENDING orphans on AC removal/re-id ──

def test_prune_removes_scaffolder_orphan_keeps_builder_orphan():
    # AC1 dropped from the declared set. Two "orphan" rows whose ac (AC1) is no longer declared:
    #   - a scaffolder PENDING orphan (empty test fields)  -> MUST be pruned
    #   - a builder row (PASSING, real path)               -> MUST be kept (never our call to drop)
    data = _brief(acceptance_criteria=[{"id": "AC2"}],
                  test_first_plan=[
                      {"ac": "AC1", "status": "PENDING", "test_path": "", "test_function": ""},
                      {"ac": "AC1", "status": "PASSING",
                       "test_path": "tests/t.py", "test_function": "test_x"},
                  ])
    out, notes = scaffold_pending_plan(data)
    plan = out["test_first_plan"]
    kinds = [(r["ac"], r["status"]) for r in plan]
    assert ("AC1", "PENDING") not in kinds, "the scaffolder-created PENDING orphan must be pruned"
    assert ("AC1", "PASSING") in kinds, "a builder-populated orphan row must NEVER be pruned (M-add-1)"
    assert ("AC2", "PENDING") in kinds, "AC2 (newly declared) gets its PENDING row"
    assert any("prune" in n for n in notes)


def test_cr1_malformed_non_list_plan_is_observable():
    """CR1 (code-review): a present-but-malformed (non-list) test_first_plan is replaced with a
    fresh plan AND that mutation emits a note (must_not_defer: the scaffold action is observable
    on EVERY mutation path, not just append/prune)."""
    data, notes = scaffold_pending_plan({"variants": {"test_first": True},
                                         "acceptance_criteria": [{"id": "AC1"}],
                                         "test_first_plan": "oops-not-a-list"})
    assert isinstance(data["test_first_plan"], list) and len(data["test_first_plan"]) == 1
    assert any("malformed non-list" in n for n in notes), "the replacement must be observable"


def test_idempotent_noop_when_already_covered():
    data, _ = scaffold_pending_plan(_brief())
    snapshot = json.dumps(data["test_first_plan"], sort_keys=True)
    again, notes = scaffold_pending_plan(data)
    assert json.dumps(again["test_first_plan"], sort_keys=True) == snapshot, "second run is a no-op"
    assert any("no change" in n for n in notes)


# ── must_not_defer — empty/malformed ACs are fail-visible, never a silent pass ────────

def test_empty_acs_yield_empty_plan_and_gate_fires(tmp_path):
    data, notes = scaffold_pending_plan({"variants": {"test_first": True},
                                         "acceptance_criteria": []})
    assert data["test_first_plan"] == [], "no ACs -> empty plan, not a fabricated row"
    assert any("no acceptance_criteria" in n.lower() or "empty plan" in n.lower() for n in notes)
    p = tmp_path / "mission-brief.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    r = audit(p, SPECS["test_first"])
    assert "empty-table" in _kinds(r), "the gate fires fail-visibly on the empty plan"


# ── AC5 — the CLI: same-directory atomic write + end-to-end merge, fail-visible errors ─

def test_ac5_merge_prune_and_atomic_write(tmp_path, monkeypatch):
    import scripts.lib.scaffold_test_first_plan as cli

    # end-to-end: a test_first brief with a partial plan -> CLI scaffolds the rest on disk.
    brief = tmp_path / "mission-brief.json"
    brief.write_text(json.dumps(_brief(
        acceptance_criteria=[{"id": "AC1"}, {"id": "AC2"}],
        test_first_plan=[{"ac": "AC1", "status": "WRITTEN-FAILING",
                          "test_path": "tests/t.py", "test_function": "test_x"}],
    )), encoding="utf-8")

    # M-add-3: the atomic temp MUST be created in the TARGET's directory (same filesystem),
    # or os.replace cross-device-fails on the external vault. Capture the dir= passed to mkstemp.
    seen = {}
    import tempfile as _tempfile
    real_mkstemp = _tempfile.mkstemp

    def _spy_mkstemp(*a, **kw):
        seen["dir"] = kw.get("dir")
        return real_mkstemp(*a, **kw)

    monkeypatch.setattr(cli.tempfile, "mkstemp", _spy_mkstemp)

    rc = cli.main([str(brief)])
    assert rc == 0
    assert seen["dir"] == str(brief.parent), (
        "the atomic temp file must be created beside the target on the same filesystem (M-add-3)")

    out = json.loads(brief.read_text(encoding="utf-8"))
    by_ac = {r["ac"]: r for r in out["test_first_plan"]}
    assert by_ac["AC1"]["status"] == "WRITTEN-FAILING", "builder row preserved through the CLI"
    assert by_ac["AC2"]["status"] == "PENDING", "AC2 scaffolded by the CLI"


def test_cli_fail_visible_on_bad_json(tmp_path, capsys):
    import scripts.lib.scaffold_test_first_plan as cli
    bad = tmp_path / "mission-brief.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = cli.main([str(bad)])
    assert rc != 0, "an unreadable/invalid brief is a fail-visible non-zero exit, never a silent pass"


def test_cli_missing_brief_is_nonzero(tmp_path):
    import scripts.lib.scaffold_test_first_plan as cli
    rc = cli.main([str(tmp_path / "nope.json")])
    assert rc != 0


def test_bc_proj_3_cli_preserves_non_ascii_roundtrip(tmp_path):
    """BC-PROJ-3 (cp1252 file-write leg): the CLI serializes the brief with ensure_ascii=False,
    so a non-ASCII field (em-dash U+2014, which real ACs carry) survives as the LITERAL char,
    never a \\u2014 escape."""
    import scripts.lib.scaffold_test_first_plan as cli
    brief = tmp_path / "mission-brief.json"
    brief.write_text(json.dumps({
        "variants": {"test_first": True},
        "acceptance_criteria": [{"id": "AC1", "text": "render — the em-dash — verbatim"}],
    }, ensure_ascii=False), encoding="utf-8")
    assert cli.main([str(brief)]) == 0, "scaffold appends AC1 -> writes the brief"
    raw = brief.read_text(encoding="utf-8")
    assert "—" in raw, "the literal em-dash must survive the scaffolder write"
    assert "\\u2014" not in raw, "must NOT be ASCII-escaped (ensure_ascii=False)"


def test_bc_proj_7_skill_sites_wire_the_scaffolder(plugin_root):
    """BC-PROJ-7: the helper is wired into TWO SKILL.md bash SITES (/slice Step 5.3 primary
    producer, /build-slice Step 1 idempotent backstop). SKILL.md bash is not statically checked,
    so a module test alone would not catch a silent revert at a SITE -- this grep guards it."""
    for rel in ("skills/slice/SKILL.md", "skills/build-slice/SKILL.md"):
        text = (plugin_root / rel).read_text(encoding="utf-8")
        assert "scaffold_test_first_plan.py" in text, (
            f"{rel} must invoke scaffold_test_first_plan.py -- the producer/backstop wiring "
            f"could silently revert (BC-PROJ-7)")
