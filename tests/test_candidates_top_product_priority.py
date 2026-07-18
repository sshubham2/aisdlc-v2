"""candidates_top folds the product-priority score-space term into the pick sort (slice-077 /
SC-138 / ADR-088). Production-shape: drive the CLI exactly as /slice's Live-state injection does.

Covers AC1 (a demoted candidate ranks below its non-demoted severity peer; on-path vs
unclassified is rank-inert), AC3a/AC3b (demoted HIGH < on-path MEDIUM; a non-demoted critical
tops the board), AC4 (all-term-less backlog is an order-preserving no-op), and M4/M5 (a demote
co-constraint violation fails visible + exit 1; a non-dict priority stays injection-safe exit 0;
the digest surfaces the [demoted: reason] tag + effective score).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOP = _REPO / "skills" / "slice" / "scripts" / "candidates_top.py"


def _write(vault, cands):
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "t",
         "candidates": cands, "pick_log": []}), encoding="utf-8")


def _run(vault, *args):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    return subprocess.run([sys.executable, str(_TOP), "--vault", str(vault), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _cand(cid, score, *, effort="M", severity="medium", source_type="finding",
          demoted_reason=None, demoted_at=None):
    c = {"id": cid, "title": cid.lower(), "status": "candidate", "progress": "not-started",
         "slice": None, "claimed_by": None, "started_at": None,
         "source": [{"type": source_type, "ref": "r-1"}],
         "priority": {"score": score, "severity": severity, "effort": effort}}
    if demoted_reason is not None:
        c["demote_reason"] = demoted_reason
        c["demoted_at"] = demoted_at or "2026-07-18T00:00:00Z"
    elif demoted_at is not None:
        c["demoted_at"] = demoted_at  # co-constraint VIOLATION shape (at without reason)
    return c


def _top_ids(vault, *args):
    cp = _run(vault, "--json", *args)
    assert cp.returncode == 0, cp.stderr
    return [c["id"] for c in json.loads(cp.stdout)["top"]], cp


# ── AC1 / AC3a: a demoted candidate ranks BELOW its non-demoted peer ────────────────

def test_demoted_ranks_below_nondemoted_severity_peer(tmp_path):
    """AC1: two HIGH (score 7) peers; the demoted one (eff 7-4=3) must rank below the
    non-demoted one (eff 7) even though the demoted has the LOWER id (would win the tie on HEAD)."""
    _write(tmp_path, [_cand("SC-210", 7, demoted_reason="good enough for now"),
                      _cand("SC-211", 7)])
    ids, _ = _top_ids(tmp_path)
    assert ids.index("SC-211") < ids.index("SC-210"), ids


def test_demoted_high_ranks_below_onpath_medium(tmp_path):
    """AC3a: a demoted HIGH (7-4=3) ranks below an on-path MEDIUM (product-scope, 5)."""
    _write(tmp_path, [_cand("SC-230", 7, demoted_reason="later"),
                      _cand("SC-231", 5, source_type="product-scope")])
    ids, _ = _top_ids(tmp_path)
    assert ids.index("SC-231") < ids.index("SC-230"), ids


def test_onpath_vs_unclassified_is_rank_inert(tmp_path):
    """AC1: two non-demoted score-5 peers differing ONLY in on-path/unclassified are
    rank-identical (term 0) — the lower-id one stays first; on-path never jumps ahead."""
    _write(tmp_path, [_cand("SC-220", 5, source_type="finding"),
                      _cand("SC-221", 5, source_type="product-scope")])
    ids, _ = _top_ids(tmp_path)
    assert ids.index("SC-220") < ids.index("SC-221"), ids


# ── AC3b: a NON-demoted critical still tops the board ───────────────────────────────

def test_nondemoted_critical_tops_the_board(tmp_path):
    """AC3b: a non-demoted critical (score 9, term 0) tops the board over an on-path product
    candidate and a demoted HIGH — the off-path penalty never buries a default critical."""
    _write(tmp_path, [_cand("SC-CRIT", 9, severity="critical"),
                      _cand("SC-PROD", 5, source_type="product-scope"),
                      _cand("SC-DEM", 8, demoted_reason="meh")])
    ids, _ = _top_ids(tmp_path)
    assert ids[0] == "SC-CRIT", ids


# ── AC4: an all-term-less backlog is an order-preserving no-op ───────────────────────

def test_all_termless_backlog_is_a_noop(tmp_path):
    """AC4: ranking an all-unclassified backlog yields pure (-score, effort, id) order — the
    term changed nothing — and no migration/regression warning is emitted."""
    _write(tmp_path, [_cand("SC-301", 3), _cand("SC-302", 8), _cand("SC-303", 5)])
    ids, cp = _top_ids(tmp_path)
    assert ids == ["SC-302", "SC-303", "SC-301"], ids
    blob = (cp.stdout + cp.stderr).lower()
    assert "migration" not in blob and "regression" not in blob and "warning" not in blob


# ── M4: co-constraint violation fails visible; non-dict priority stays injection-safe ─

def test_demote_coconstraint_violation_fails_visible(tmp_path):
    """M4: a half-written demote (demoted_at without demote_reason) exits 1 with a message
    naming the offending id — never a raw traceback."""
    _write(tmp_path, [_cand("SC-240", 7, demoted_at="2026-07-18T00:00:00Z"),
                      _cand("SC-241", 5)])
    cp = _run(tmp_path, "--json")
    assert cp.returncode == 1, (cp.returncode, cp.stdout, cp.stderr)
    assert "SC-240" in cp.stderr, cp.stderr
    assert "Traceback" not in cp.stderr, cp.stderr


def test_nondict_priority_stays_injection_safe(tmp_path):
    """M4: a non-dict priority (the live SC-152/153 shape) never breaks the injection — exit 0,
    the candidate still listed (fail-SAFE term 0)."""
    weird = _cand("SC-250", 0)
    weird["priority"] = ["SC-152", "SC-153"]  # the real non-dict shape
    _write(tmp_path, [weird, _cand("SC-251", 5)])
    ids, _ = _top_ids(tmp_path)
    assert "SC-250" in ids and "SC-251" in ids, ids


# ── M5: the digest surfaces the demote tag + effective score ────────────────────────

def test_digest_surfaces_demote_tag_text(tmp_path):
    """M5: a demoted row shows a [demoted: <reason>] tag + its effective score in the text digest
    so the ordering is explicable and the override auditable at the pick surface."""
    _write(tmp_path, [_cand("SC-260", 7, demoted_reason="good enough for now")])
    cp = _run(tmp_path)  # text
    assert cp.returncode == 0, cp.stderr
    assert "demoted" in cp.stdout.lower(), cp.stdout
    assert "good enough for now" in cp.stdout, cp.stdout


def test_digest_surfaces_path_class_and_effective_score_json(tmp_path):
    """M5: the JSON digest carries path_class + effective_score for a demoted row."""
    _write(tmp_path, [_cand("SC-260", 7, demoted_reason="later")])
    cp = _run(tmp_path, "--json")
    assert cp.returncode == 0, cp.stderr
    row = json.loads(cp.stdout)["top"][0]
    assert row["path_class"] == "off-path", row
    assert row["effective_score"] == 3, row  # 7 - 4


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
