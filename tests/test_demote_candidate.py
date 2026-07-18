"""demote_candidate.py — the 'good enough for now' demote lever (slice-077 / SC-138 / ADR-088).

Production-shape (subprocess CLI, as /slice-candidates --demote invokes it). Covers AC5 (a demote
lowers rank WITHOUT deleting the append-only risk-register entry — the file is never opened for
write), AC3c / M2 / M-add-2 (the eligibility guard REFUSES a product-sourced OR critical/security
target), and the fail-visible contract (unknown id, empty reason, non-pickable status, re-demote
idempotence vs different-reason refusal).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DEMOTE = _REPO / "skills" / "slice-candidates" / "scripts" / "demote_candidate.py"
_TOP = _REPO / "skills" / "slice" / "scripts" / "candidates_top.py"


def _cand(cid, score, *, effort="M", severity="medium", status="candidate", source_type="finding"):
    return {"id": cid, "title": cid.lower(), "status": status, "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None,
            "source": [{"type": source_type, "ref": "r-1"}],
            "priority": {"score": score, "severity": severity, "effort": effort},
            "history": [{"event": "created", "by": "slice-candidates", "at": "t0", "ref": "x"}]}


_RISK = {"_schema": "aisdlc/risk-register@1", "project": "t",
         "risks": [{"id": "R-9", "statement": "a risk", "status": "open"}]}


def _write(vault, cands):
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "t",
         "candidates": cands, "pick_log": []}), encoding="utf-8")
    (vault / "risk-register.json").write_text(json.dumps(_RISK), encoding="utf-8")


def _run(vault, *args):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    return subprocess.run([sys.executable, str(_DEMOTE), "--vault", str(vault), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _cands(vault):
    return json.loads((vault / "candidates.json").read_text(encoding="utf-8"))["candidates"]


def _by_id(vault, cid):
    return next(c for c in _cands(vault) if c["id"] == cid)


def _top_ids(vault):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    cp = subprocess.run([sys.executable, str(_TOP), "--vault", str(vault), "--json"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    assert cp.returncode == 0, cp.stderr
    return [c["id"] for c in json.loads(cp.stdout)["top"]]


# ── AC5: a demote lowers rank + NEVER touches the risk-register ──────────────────────

def test_demote_lowers_rank_and_preserves_risk_register(tmp_path):
    """AC5: after a demote the candidate's rank drops below a lower-severity peer AND the
    append-only risk-register.json is byte-for-byte untouched (never opened for write)."""
    _write(tmp_path, [_cand("SC-500", 7, severity="high"), _cand("SC-501", 5)])
    before = (tmp_path / "risk-register.json").read_bytes()
    assert _top_ids(tmp_path)[0] == "SC-500", "HIGH outranks MEDIUM before the demote"

    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "good enough for now")
    assert cp.returncode == 0, cp.stderr

    rec = _by_id(tmp_path, "SC-500")
    assert rec.get("demote_reason") == "good enough for now"
    assert rec.get("demoted_at")
    assert _top_ids(tmp_path).index("SC-501") < _top_ids(tmp_path).index("SC-500"), "rank dropped"
    assert (tmp_path / "risk-register.json").read_bytes() == before, "risk-register untouched"


def test_demote_records_history_event(tmp_path):
    _write(tmp_path, [_cand("SC-500", 6)])
    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "later")
    assert cp.returncode == 0, cp.stderr
    hist = _by_id(tmp_path, "SC-500")["history"]
    assert any(e.get("event") == "demoted" and e.get("ref") == "later" for e in hist), hist


# ── AC3c / M2 / M-add-2: the eligibility guard REFUSES ──────────────────────────────

def test_refuses_product_sourced_target(tmp_path):
    _write(tmp_path, [_cand("SC-510", 5, source_type="product-scope")])
    cp = _run(tmp_path, "--candidate", "SC-510", "--reason", "nope")
    assert cp.returncode != 0, cp.stdout
    assert _by_id(tmp_path, "SC-510").get("demote_reason") is None


def test_refuses_critical_severity_target(tmp_path):
    _write(tmp_path, [_cand("SC-511", 9, severity="critical")])
    cp = _run(tmp_path, "--candidate", "SC-511", "--reason", "nope")
    assert cp.returncode != 0, cp.stdout
    assert _by_id(tmp_path, "SC-511").get("demote_reason") is None


def test_refuses_critical_band_by_score(tmp_path):
    """The critical band is score>=9 even if the severity label is absent/loose."""
    _write(tmp_path, [_cand("SC-513", 9, severity="high")])
    cp = _run(tmp_path, "--candidate", "SC-513", "--reason", "nope")
    assert cp.returncode != 0, cp.stdout


def test_refuses_critical_security_bug(tmp_path):
    """CR1 (honest): a genuinely critical SECURITY bug MATERIALIZES at severity 'critical' / score 9
    (build_backlog._SEV_SCORE), so the critical band structurally protects it — 'a critical security
    bug tops the board' holds. Driven with REALISTIC materialized data, not a fake severity='security'."""
    _write(tmp_path, [_cand("SC-512", 9, severity="critical")])  # how a critical security finding lands
    cp = _run(tmp_path, "--candidate", "SC-512", "--reason", "nope")
    assert cp.returncode != 0, cp.stdout
    assert _by_id(tmp_path, "SC-512").get("demote_reason") is None


def test_sub_critical_finding_is_demotable_honest_scope(tmp_path):
    """CR1 (honest scope): a materialized candidate carries NO structured security category
    (build_backlog folds it into rationale free text), so a SUB-critical finding-sourced item IS
    demotable — the guard reads structured severity/score only, never free text (BC-PROJ-4). This
    documents the un-enforced edge of AC3's 'critical/security' instead of laundering a fake green."""
    _write(tmp_path, [_cand("SC-514", 7, severity="high")])  # a high finding, no structured security signal
    cp = _run(tmp_path, "--candidate", "SC-514", "--reason", "genuinely low value for now")
    assert cp.returncode == 0, cp.stderr
    assert _by_id(tmp_path, "SC-514").get("demote_reason") == "genuinely low value for now"


# ── fail-visible contract ───────────────────────────────────────────────────────────

def test_unknown_id_fails_visible(tmp_path):
    _write(tmp_path, [_cand("SC-500", 5)])
    cp = _run(tmp_path, "--candidate", "SC-999", "--reason", "x")
    assert cp.returncode != 0
    assert "SC-999" in cp.stderr


def test_empty_reason_fails_closed(tmp_path):
    _write(tmp_path, [_cand("SC-500", 5)])
    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "   ")
    assert cp.returncode != 0
    assert _by_id(tmp_path, "SC-500").get("demote_reason") is None


def test_non_pickable_status_refused(tmp_path):
    _write(tmp_path, [_cand("SC-500", 5, status="active")])
    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "x")
    assert cp.returncode != 0


def test_re_demote_same_reason_is_idempotent(tmp_path):
    _write(tmp_path, [_cand("SC-500", 5)])
    assert _run(tmp_path, "--candidate", "SC-500", "--reason", "same").returncode == 0
    first_at = _by_id(tmp_path, "SC-500")["demoted_at"]
    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "same")
    assert cp.returncode == 0, cp.stderr
    assert _by_id(tmp_path, "SC-500")["demoted_at"] == first_at, "idempotent no-op keeps the record"


def test_re_demote_different_reason_refused(tmp_path):
    _write(tmp_path, [_cand("SC-500", 5)])
    assert _run(tmp_path, "--candidate", "SC-500", "--reason", "first").returncode == 0
    cp = _run(tmp_path, "--candidate", "SC-500", "--reason", "second")
    assert cp.returncode != 0, "must not silently overwrite an existing demote record"
    assert _by_id(tmp_path, "SC-500")["demote_reason"] == "first"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
