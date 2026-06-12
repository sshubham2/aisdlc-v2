"""scripts/lib/finding_dedup.py — cross-pass finding merge rules (4.4 priority e)."""
from __future__ import annotations

from scripts.lib.finding_dedup import dedupe_findings


def _f(fid, path, lines="", severity="medium", category="x", pass_="p1", title="t"):
    return {
        "id": fid, "severity": severity, "category": category, "pass": pass_,
        "title": title, "description": "d",
        "evidence": [{"path": path, "lines": lines, "note": ""}],
    }


def test_singleton_returned_unchanged():
    f = _f("F-A", "a.py", "10-20")
    merged, report = dedupe_findings([f])
    assert merged == [f]   # same object, no merged_ids -> existing carryover survives
    assert report == []


def test_merge_same_location_different_category():
    a = _f("F-A", "god.py", "10-20", category="dead-code")
    b = _f("F-B", "god.py", "15-25", category="size")
    merged, report = dedupe_findings([a, b], gap=3)
    assert len(merged) == 1
    m = merged[0]
    assert m["id"].startswith("F-MRG-")
    assert set(m["merged_ids"]) == {"F-A", "F-B"}
    assert m["merge_count"] == 2
    assert len(report) == 1


def test_no_merge_different_files():
    merged, _ = dedupe_findings([_f("F-A", "a.py", "10-20"), _f("F-B", "b.py", "10-20")])
    assert len(merged) == 2


def test_no_merge_distant_ranges():
    a = _f("F-A", "a.py", "10-20")
    b = _f("F-B", "a.py", "200-210")
    merged, _ = dedupe_findings([a, b], gap=3)
    assert len(merged) == 2


def test_gap_brings_adjacent_ranges_together():
    a = _f("F-A", "a.py", "10-20")
    b = _f("F-B", "a.py", "22-30")  # 20 and 22 within gap 3
    merged, _ = dedupe_findings([a, b], gap=3)
    assert len(merged) == 1


def test_merged_severity_is_max():
    a = _f("F-A", "a.py", "10-20", severity="low")
    b = _f("F-B", "a.py", "12-22", severity="high")
    merged, _ = dedupe_findings([a, b])
    assert merged[0]["severity"] == "high"


def test_cluster_id_stable_across_calls():
    a = _f("F-A", "a.py", "10-20")
    b = _f("F-B", "a.py", "12-22")
    m1, _ = dedupe_findings([a, b])
    m2, _ = dedupe_findings([a, b])
    assert m1[0]["id"] == m2[0]["id"]


def test_over_merge_guard_wide_finding():
    # a "wide" finding (>3 paths, whole-file) must NOT chain unrelated whole-file
    # per-file findings via a bare whole-file match (needs a real range overlap).
    wide = {
        "id": "F-WIDE", "severity": "medium", "category": "dup", "pass": "p",
        "title": "cluster", "description": "d",
        "evidence": [{"path": f"f{i}.py", "lines": "", "note": ""} for i in range(5)],
    }
    single = _f("F-S", "f0.py", "")  # whole-file, shares a path with one wide member
    merged, _ = dedupe_findings([wide, single])
    assert len(merged) == 2
