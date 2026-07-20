"""scripts/lib/triage_precision.py — SSOT disposition classifier + gate-log precision/recall.

slice-052 / SC-088 / ADR-045. Covers:
  AC1/AC3 — classify_dispositions: real/noise sets == the documented first-Critic rule;
            meta select is ^M-add- ONLY (first-Critic majors M2 do NOT leak in); M/R/K mapping.
  m2      — leading-whitespace ids stripped; an unknown action DEGRADES (well_formed=False),
            genuinely malformed structure RAISES.
  M-add-1 — critique_review_row returns None when DR-1 did not run (no critique-review.json /
            skip marker) and a populated row when it did.
  AC4     — gate_precision_recall over a MIXED gate-log (legacy count-only + new real-bearing);
            absent findings_real is UNKNOWN (never 0); precision/recall computed from the log alone.
"""
from __future__ import annotations

import json

import pytest

from scripts.lib.triage_precision import (
    REAL_DISPOSITIONS,
    NOISE_DISPOSITIONS,
    META_PREFIX,
    classify_dispositions,
    critique_review_row,
    gate_precision_recall,
)


# ---- AC1 / AC3: the classification rule is the documented first-Critic rule (SSOT) ----

def test_real_noise_sets_match_documented_first_critic_rule():
    # /critique Step 4.5: real = {accepted-fixed, accepted-pending, deferred, escalated}; noise = {overridden}
    assert set(REAL_DISPOSITIONS) == {"accepted-fixed", "accepted-pending", "deferred", "escalated"}
    assert set(NOISE_DISPOSITIONS) == {"overridden"}
    assert META_PREFIX == "M-add-"


def test_classify_meta_mapping_M_R_K():
    disp = [
        {"finding": "M-add-1", "action": "accepted-fixed"},
        {"finding": "M-add-2", "action": "deferred"},
        {"finding": "M-add-3", "action": "overridden"},
    ]
    c = classify_dispositions(disp, select="meta")
    assert (c.count, c.real, c.noise) == (3, 2, 1)
    assert c.well_formed is True


def test_first_critic_ids_never_leak_into_meta():
    disp = [
        {"finding": "M2", "action": "accepted-fixed"},     # first-Critic MAJOR — must NOT match ^M-add-
        {"finding": "m1", "action": "overridden"},          # first-Critic minor
        {"finding": "B1", "action": "accepted-pending"},    # first-Critic blocker
        {"finding": "M-add-1", "action": "accepted-fixed"}, # the only meta finding
    ]
    meta = classify_dispositions(disp, select="meta")
    assert (meta.count, meta.real, meta.noise) == (1, 1, 0)   # only M-add-1
    fc = classify_dispositions(disp, select="first-critic")
    assert fc.count == 3                                      # M2, m1, B1 — M-add-1 excluded
    # partitions are disjoint and sum to the ratified set
    assert meta.count + fc.count == len(disp)


def test_leading_whitespace_id_still_classified_as_meta():
    # m2: strip before the ^M-add- match, else " M-add-1" would fall to first-Critic
    disp = [{"finding": " M-add-1 ", "action": "accepted-fixed"}]
    assert classify_dispositions(disp, select="meta").count == 1


# ---- m2: degrade-to-count-only vs raise ----

def test_unknown_action_degrades_not_raises():
    disp = [
        {"finding": "M-add-1", "action": "accepted-fixed"},
        {"finding": "M-add-2", "action": "some-future-action"},  # not in real|noise
    ]
    c = classify_dispositions(disp, select="meta")
    assert c.count == 2                # still counted
    assert c.well_formed is False      # caller must emit count-only (omit real/noise)


def test_malformed_structure_raises():
    with pytest.raises((TypeError, ValueError)):
        classify_dispositions("not-a-list", select="meta")
    with pytest.raises((TypeError, ValueError)):
        classify_dispositions([{"finding": "M-add-1"}, 42], select="meta")  # non-dict element


# ---- M-add-1: the phantom-row guard (emit only when DR-1 actually ran) ----

def _write_slice(tmp_path, *, critique_review=None, dispositions=None, milestone=None):
    if dispositions is not None:
        (tmp_path / "critique.json").write_text(
            json.dumps({"triage": {"dispositions": dispositions}}), encoding="utf-8")
    if critique_review is not None:
        (tmp_path / "critique-review.json").write_text(json.dumps(critique_review), encoding="utf-8")
    if milestone is not None:
        (tmp_path / "milestone.json").write_text(json.dumps(milestone), encoding="utf-8")
    return str(tmp_path)


def test_critique_review_row_none_when_no_critique_review_json(tmp_path):
    d = _write_slice(tmp_path, dispositions=[{"finding": "M-add-1", "action": "accepted-fixed"}])
    assert critique_review_row(d) is None   # DR-1 did NOT run -> ZERO rows


def test_critique_review_row_none_when_skip_marker(tmp_path):
    d = _write_slice(
        tmp_path,
        critique_review={"verdict": "extend"},
        dispositions=[],
        milestone={"critique-review-skip": "skip -- rationale: low-tier advisory"},
    )
    assert critique_review_row(d) is None   # explicit skip marker -> ZERO rows


def test_critique_review_row_populated_when_dr1_ran(tmp_path):
    d = _write_slice(
        tmp_path,
        critique_review={"verdict": "extend"},
        dispositions=[
            {"finding": "M-add-1", "action": "accepted-fixed"},   # real
            {"finding": "M-add-2", "action": "overridden"},        # noise
            {"finding": "M2", "action": "accepted-fixed"},         # first-Critic — excluded
        ],
    )
    row = critique_review_row(d)
    assert row is not None
    assert row["verdict"] == "extend"
    assert row["findings_count"] == 2   # 2 M-add-* only
    assert row["findings_real"] == 1
    assert row["findings_noise"] == 1


def test_critique_review_row_count_only_on_degrade(tmp_path):
    d = _write_slice(
        tmp_path,
        critique_review={"verdict": "extend"},
        dispositions=[{"finding": "M-add-1", "action": "mystery"}],  # unknown action
    )
    row = critique_review_row(d)
    assert row["findings_count"] == 1
    assert "findings_real" not in row      # count-only (m2 degrade — never block the append)
    assert "findings_noise" not in row


# ---- AC4: consumers compute precision/recall from the gate-log alone (shipped path) ----

def test_gate_precision_recall_mixed_gatelog():
    entries = [
        # legacy count-only critique-review rows (no findings_real) -> UNKNOWN, excluded
        {"gate": "critique-review", "verdict": "extend", "findings_count": 2},
        {"gate": "critique-review", "verdict": "accept", "findings_count": 0},
        # new real-bearing critique-review rows
        {"gate": "critique-review", "verdict": "extend", "findings_count": 3, "findings_real": 2, "findings_noise": 1},
        {"gate": "critique-review", "verdict": "extend", "findings_count": 1, "findings_real": 1, "findings_noise": 0},
        # a recall (miss) row — excluded from verdict/precision math
        {"gate": "critique-review", "kind": "miss", "severity": "major"},
        # an unrelated gate — must not bleed in
        {"gate": "critique", "verdict": "clean", "findings_count": 9, "findings_real": 9, "findings_noise": 0},
    ]
    r = gate_precision_recall(entries, "critique-review")
    assert r["runs"] == 4                       # 4 verdict rows (miss excluded)
    assert r["precision"] == pytest.approx(0.75)  # sum_real 3 / (3 + noise 1)
    assert r["misses"] == 1
    assert r["recall"] == pytest.approx(0.75)     # catches 3 / (3 + misses 1)


def test_gate_precision_recall_all_legacy_is_unknown_not_zero():
    entries = [
        {"gate": "critique-review", "verdict": "extend", "findings_count": 2},
        {"gate": "critique-review", "verdict": "accept", "findings_count": 0},
    ]
    r = gate_precision_recall(entries, "critique-review")
    assert r["runs"] == 2
    assert r["precision"] is None   # UNKNOWN — never reported as 0


# ---- gate_summary: the bounded whole-file aggregation /pulse consumes (2026-07 review sweep) ----

def _summary_entries():
    return [
        {"gate": "risk-spike", "slice": "slice-050", "verdict": "go", "findings_count": 0,
         "reality_contact": "high", "cross_domain": True},
        {"gate": "risk-spike", "slice": "slice-051", "verdict": "no-go", "findings_count": 1,
         "reality_contact": "high", "cross_domain": True},
        {"gate": "validate-slice", "slice": "slice-050", "verdict": "pass", "findings_count": 0,
         "reality_contact": "high", "reality_proxy": "simulator"},
        {"gate": "critique", "slice": "slice-050", "verdict": "clean", "findings_count": 0,
         "reality_contact": "low"},
        {"gate": "critique", "slice": "slice-051", "verdict": "needs-fixes", "findings_count": 3,
         "findings_real": 2, "findings_noise": 1, "reality_contact": "low"},
        {"gate": "critique", "slice": "slice-051", "kind": "miss", "severity": "major",
         "caught_by": "validate-slice"},
        {"gate": "design-tournament", "slice": "slice-050", "approach_divergence": "overlapping"},
        {"gate": "design-tournament", "slice": "slice-051", "approach_divergence": "identical"},
    ]


def test_gate_summary_orders_by_reality_contact_and_excludes_informational():
    from scripts.lib.triage_precision import gate_summary
    s = gate_summary(_summary_entries())
    names = [g["gate"] for g in s["gates"]]
    assert "design-tournament" not in names          # informational — never in the quiet math
    assert names.index("risk-spike") < names.index("critique")   # high before low
    crit = next(g for g in s["gates"] if g["gate"] == "critique")
    assert crit["runs"] == 2 and crit["misses"] == 1
    assert crit["last"] == {"verdict": "needs-fixes", "slice": "slice-051"}
    assert s["design_tournament"] == {"runs": 2,
                                      "divergence": {"overlapping": 1, "identical": 1}}


def test_gate_summary_cross_domain_reality_pass_class_only():
    from scripts.lib.triage_precision import gate_summary
    s = gate_summary(_summary_entries())
    # two cross_domain rows at high contact: go (held) + no-go (not held)
    assert s["cross_domain"] == {"held": 1, "total": 2}


def test_gate_summary_slice_rows_match_canonical_prefix():
    from scripts.lib.triage_precision import gate_summary
    entries = _summary_entries()
    entries.append({"gate": "code-review", "slice": "slice-050-some-name", "verdict": "clean",
                    "findings_count": 0, "reality_contact": "low"})
    s = gate_summary(entries, slice_id="slice-050")
    gates = sorted(r["gate"] for r in s["slice_rows"])
    # folder-form slice id folds onto the canonical row set
    assert gates == ["code-review", "critique", "design-tournament", "risk-spike",
                     "validate-slice"]
    vs = next(r for r in s["slice_rows"] if r["gate"] == "validate-slice")
    assert vs["reality_proxy"] == "simulator"        # proxies survive into the compact rows


def test_gate_summary_recent_is_capped_and_newest_first():
    from scripts.lib.triage_precision import gate_summary
    s = gate_summary(_summary_entries(), recent=3)
    assert len(s["recent"]) == 3
    assert s["recent"][0]["gate"] == "design-tournament"   # last appended row comes first
    assert s["total_entries"] == 8


def test_gate_summary_quiet_flag_needs_five_runs_and_zero_raised():
    from scripts.lib.triage_precision import gate_summary
    entries = [{"gate": "drift-check", "slice": f"slice-{i:03d}", "verdict": "clean",
                "findings_count": 0, "reality_contact": "medium"} for i in range(5)]
    s = gate_summary(entries)
    assert s["gates"][0]["quiet"] is True
    s4 = gate_summary(entries[:4])
    assert s4["gates"][0]["quiet"] is False


# ── slice-089 / SC-194: the CLI gate-log reads derive on a synced/cloned vault ────

def _seed_and_shard(vault: Path, rows: list):
    """Write a flat gate-log, migrate it to shards, then delete the derived cache (a synced vault)."""
    from scripts.lib import _shard_store as S
    (vault / "gate-log.json").write_text(json.dumps({"entries": list(rows)}), encoding="utf-8")
    S.migrate(vault, "gate-log.json", "entries")
    (vault / "gate-log.json").unlink()


def test_gate_precision_cli_derives_on_missing_cache(run_script, vault):
    """AC3 (transitive, critic-calibrate:84): --gate-precision derives on a cache-absent sharded vault."""
    rows = [
        {"gate": "critique", "verdict": "needs-fixes", "findings_count": 2, "findings_real": 2, "findings_noise": 0},
        {"gate": "critique", "verdict": "clean", "findings_count": 0, "findings_real": 0, "findings_noise": 0},
    ]
    (vault / "gate-log.json").write_text(json.dumps({"entries": rows}), encoding="utf-8")
    gl = str(vault / "gate-log.json")
    r0 = run_script("scripts/lib/triage_precision.py", ["--gate-precision", "--gate", "critique", "--gate-log", gl])
    assert r0.returncode == 0, r0.stderr
    base = json.loads(r0.stdout)
    assert base["runs"] == 2 and base["catches"] == 2

    _seed_and_shard(vault, rows)
    r1 = run_script("scripts/lib/triage_precision.py", ["--gate-precision", "--gate", "critique", "--gate-log", gl])
    assert r1.returncode == 0, r1.stderr
    assert json.loads(r1.stdout) == base, "gate-precision must derive the same result from shards"


def test_summary_cli_derives_on_missing_cache(run_script, vault, tmp_path):
    """AC4 (/pulse): --summary derives-on-missing so per-gate hit-rate is non-zero on a synced vault;
    a genuinely-empty log (neither cache nor shards) still returns the {absent} sentinel."""
    rows = [{"gate": "critique", "verdict": "clean", "findings_count": 0, "reality_contact": "low"},
            {"gate": "validate-slice", "verdict": "pass", "findings_count": 0, "reality_contact": "high"}]
    (vault / "gate-log.json").write_text(json.dumps({"entries": rows}), encoding="utf-8")
    gl = str(vault / "gate-log.json")
    r0 = run_script("scripts/lib/triage_precision.py", ["--summary", "--gate-log", gl])
    assert r0.returncode == 0, r0.stderr
    assert json.loads(r0.stdout)["total_entries"] == 2

    _seed_and_shard(vault, rows)
    r1 = run_script("scripts/lib/triage_precision.py", ["--summary", "--gate-log", gl])
    assert r1.returncode == 0, r1.stderr
    assert json.loads(r1.stdout)["total_entries"] == 2, "summary must derive non-zero rows on a synced vault"

    # genuinely-empty (neither cache nor shards) -> {absent} sentinel preserved.
    empty = tmp_path / "emptyvault"; empty.mkdir()
    r2 = run_script("scripts/lib/triage_precision.py", ["--summary", "--gate-log", str(empty / "gate-log.json")])
    assert r2.returncode == 0, r2.stderr
    assert json.loads(r2.stdout) == {"absent": True}


def test_summary_fails_visible_on_torn_gate_log(run_script, tmp_path):
    """slice-089/must_not_defer[0]: a torn gate-log with NO shards fails visibly (exit 2 + stderr)
    rather than degrading to {absent}/[] -- the fail-visible RED path (BC-PROJ-12)."""
    v = tmp_path / "torn"; v.mkdir()
    (v / "gate-log.json").write_text('{"entries": [trunc', encoding="utf-8")  # torn, no shard dir
    r = run_script("scripts/lib/triage_precision.py", ["--summary", "--gate-log", str(v / "gate-log.json")])
    assert r.returncode == 2, f"a torn gate-log must fail-visibly (exit 2), got {r.returncode}: {r.stderr}"
    assert "gate-log" in r.stderr
