"""slice-003 — supersession revert refs (TF-1: written BEFORE the audit change).

Ratified contract: the supersession block gains OPTIONAL revert {commit?, pr?, note?}
(>=1 member, non-empty strings, NO unknown keys — a deliberate strict-reject on a
human-typed write object). supersede_audit validates it in a STANDALONE guarded pass,
independent of superseded_by/link completeness; absent = valid (every legacy record
passes); malformed = kind 'revert-malformed' with a per-case message, never a crash.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "skills/supersede-slice/scripts/supersede_audit.py"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _vault(tmp_path: Path, supersession, active_brief: dict | None = None) -> Path:
    v = tmp_path / "vault"
    arch = v / "slices" / "archive" / "slice-010-old"
    arch.mkdir(parents=True)
    (arch / "reflection.json").write_text(json.dumps({
        "_schema": "aisdlc/reflection@1", "slice": "slice-010",
        "validated": [], "corrected": [], "discovered": [], "deferred": [],
        "critic_calibration": [], "lessons": [],
        "supersession": supersession, "at": "<ts>",
    }, ensure_ascii=False), encoding="utf-8")
    if active_brief is not None:
        act = v / "slices" / "slice-011-new"
        act.mkdir(parents=True)
        (act / "mission-brief.json").write_text(json.dumps(active_brief), encoding="utf-8")
    return v


def _audit(run_script, vault: Path):
    return run_script(SCRIPT, ["--json"], env={"AI_SDLC_VAULT_ROOT": str(vault)})


def _violations(cp) -> list[dict]:
    return json.loads(cp.stdout).get("violations", [])


def test_revert_shapes_accepted_and_refused(run_script, tmp_path):
    # accepted: commit-only / pr-only / note-only / combined (no link claims -> clean run)
    for i, revert in enumerate([{"commit": "abc123"}, {"pr": "#42"}, {"note": "hand-unwound"},
                                {"commit": "abc123", "pr": "https://x/pr/7", "note": "partial"}]):
        v = _vault(tmp_path / f"ok{i}", {"superseded_by": "", "date": "2026-06-12",
                                         "reason": "r", "revert": revert})
        cp = _audit(run_script, v)
        assert cp.returncode == 0, f"{revert}: {cp.stdout}{cp.stderr}"

    # refused: each malformed shape -> exit 1, kind revert-malformed, per-case message, NO crash
    cases = [
        ({}, "no members"),
        ("abc123", "not an object"),          # bare string (M2 wrong-type)
        (["abc123"], "not an object"),        # list (M2 wrong-type)
        ({"commit": ""}, "empty"),
        ({"unknown": "x"}, "unknown key"),
    ]
    for i, (revert, msg_bit) in enumerate(cases):
        v = _vault(tmp_path / f"bad{i}", {"superseded_by": "", "date": "2026-06-12",
                                          "reason": "r", "revert": revert})
        cp = _audit(run_script, v)
        assert cp.returncode == 1, f"{revert!r} should refuse: rc={cp.returncode} {cp.stdout}{cp.stderr}"
        assert "Traceback" not in cp.stderr, f"{revert!r} CRASHED instead of refusing"
        viol = _violations(cp)
        assert any(x["kind"] == "revert-malformed" and msg_bit in x["message"] for x in viol), \
            f"{revert!r}: expected revert-malformed with {msg_bit!r} in {viol}"

    # null-as-absent (code-review M2): "revert": null is the file's own null convention -> valid
    v = _vault(tmp_path / "nullrev", {"superseded_by": "", "date": "2026-06-12",
                                      "reason": "r", "revert": None})
    cp = _audit(run_script, v)
    assert cp.returncode == 0, f"revert:null should be absent-equivalent: {cp.stdout}{cp.stderr}"

    # ORTHOGONALITY (DR-1 M-add-1): malformed revert + ABSENT superseded_by still fires,
    # and no link violation is invented for the half-written record.
    v = _vault(tmp_path / "orth", {"date": "2026-06-12", "reason": "half-written",
                                   "revert": {"comit": "typo"}})
    cp = _audit(run_script, v)
    assert cp.returncode == 1
    viol = _violations(cp)
    assert all(x["kind"] == "revert-malformed" for x in viol)
    assert "comit" in viol[0]["message"]


def test_skill_md_documents_revert():
    # PLUGIN_ROOT-absolute + utf-8 (m2: first SKILL.md-reading test; cp1252/cwd hazards)
    text = (PLUGIN_ROOT / "skills" / "supersede-slice" / "SKILL.md").read_text(encoding="utf-8")
    assert '"revert"' in text                      # Step 3 block documents the field
    assert "Step 2b" in text                        # the optional revert-ref ask exists
    assert "user-input gates" in text and "2b" in text.split("user-input gates", 1)[1]  # m1 inventory


def test_legacy_records_and_exit_semantics_unchanged(run_script, tmp_path):
    # legacy block (no revert) + closed bidirectional link -> exit 0, zero violations
    # (link fields carry FOLDER names — the audit's known-id set is folder names)
    v = _vault(tmp_path / "legacy",
               {"superseded_by": "slice-011-new", "date": "2026-06-12", "reason": "contradicted"},
               active_brief={"_schema": "aisdlc/mission-brief@1", "slice": "slice-011",
                             "supersedes": "slice-010-old"})
    cp = _audit(run_script, v)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert _violations(cp) == []

    # pre-existing one-way-link semantics unchanged (exit 1, same kind)
    v = _vault(tmp_path / "oneway",
               {"superseded_by": "slice-011-new", "date": "2026-06-12", "reason": "contradicted"},
               active_brief={"_schema": "aisdlc/mission-brief@1", "slice": "slice-011",
                             "supersedes": None})
    cp = _audit(run_script, v)
    assert cp.returncode == 1
    kinds = {x["kind"] for x in _violations(cp)}
    assert "one-way-link" in kinds and "revert-malformed" not in kinds
