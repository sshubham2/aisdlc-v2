"""ship_receipt.py — CI merge-gate receipt emit + verify (roadmap §2.1).

Emit reads the slice's vault evidence (validation.json + gate-log rows, archive
location included) and writes .aisdlc/receipts/<slice-NNN>.json into the repo;
verify is the CI check (mirrored inline in assets/aisdlc-merge-gate.yml).
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "scripts/lib/ship_receipt.py"


def _seed_vault(vault: Path, *, result="pass", failed_rows=0, deferral_approved=False) -> str:
    sdir = vault / "slices" / "archive" / "slice-007-add-receipts"
    sdir.mkdir(parents=True)
    (sdir / "mission-brief.json").write_text(json.dumps({
        "slice": "slice-007", "candidate": "SC-012",
    }), encoding="utf-8")
    deferral = {"approved": True, "rationale": "known debt", "by": "user"} if deferral_approved else None
    (sdir / "validation.json").write_text(json.dumps({
        "_schema": "aisdlc/validation@1", "slice": "slice-007", "result": result,
        "criteria": [{"id": "AC1", "result": "pass"}, {"id": "AC2", "result": result}],
        "shippability_regression": {"ran": True,
                                    "failed_rows": [f"row-{i}" for i in range(failed_rows)],
                                    "deferral": deferral},
    }), encoding="utf-8")
    (vault / "gate-log.json").write_text(json.dumps({"entries": [
        {"at": "<ts>", "slice": "slice-007", "gate": "risk-spike", "verdict": "go",
         "findings_count": 0, "reality_contact": "high", "reality_proxy": "real-sandbox"},
        {"at": "<ts>", "slice": "slice-007", "gate": "critique", "kind": "miss",
         "reality_contact": "low", "severity": "major", "caught_by": "validate"},  # miss rows excluded
        {"at": "<ts>", "slice": "slice-099", "gate": "validate-slice", "verdict": "pass",
         "findings_count": 0, "reality_contact": "high"},  # other slice excluded
    ]}), encoding="utf-8")
    return "slice-007-add-receipts"


def test_emit_writes_receipt_from_archive(run_script, vault, tmp_path):
    name = _seed_vault(vault)
    repo = tmp_path / "repo"
    repo.mkdir()
    r = run_script(SCRIPT, ["emit", "--slice", name, "--vault", vault, "--repo-root", repo])
    assert r.returncode == 0, r.stderr
    receipt = json.loads((repo / ".aisdlc" / "receipts" / "slice-007.json").read_text(encoding="utf-8"))
    assert receipt["slice"] == "slice-007"
    assert receipt["candidate"] == "SC-012"
    assert receipt["result"] == "pass"
    assert receipt["criteria"] == {"pass": 2, "fail": 0, "partial": 0}
    # only THIS slice's verdict rows ride along (miss rows + other slices excluded)
    assert [g["gate"] for g in receipt["gates"]] == ["risk-spike"]
    assert receipt["gates"][0]["reality_proxy"] == "real-sandbox"


def test_emit_accepts_canonical_id_and_requires_validation(run_script, vault, tmp_path):
    _seed_vault(vault)
    repo = tmp_path / "repo"
    repo.mkdir()
    r = run_script(SCRIPT, ["emit", "--slice", "slice-007", "--vault", vault, "--repo-root", repo])
    assert r.returncode == 0, r.stderr
    # a slice without validation.json refuses (the receipt is evidence, not decoration)
    bare = vault / "slices" / "slice-008-bare"
    bare.mkdir(parents=True)
    r = run_script(SCRIPT, ["emit", "--slice", "slice-008-bare", "--vault", vault, "--repo-root", repo])
    assert r.returncode == 2
    assert "validation.json" in r.stderr


def test_verify_passes_clean_receipt(run_script, vault, tmp_path):
    name = _seed_vault(vault)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_script(SCRIPT, ["emit", "--slice", name, "--vault", vault, "--repo-root", repo])
    r = run_script(SCRIPT, ["verify", "--branch", "slice/007-add-receipts", "--repo-root", repo])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GATE PASS" in r.stdout


def test_verify_fails_on_missing_receipt_and_non_pass(run_script, vault, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    r = run_script(SCRIPT, ["verify", "--branch", "slice/007-x", "--repo-root", repo])
    assert r.returncode == 1
    assert "no ship receipt" in r.stdout
    # partial validation -> receipt emits but the gate refuses it
    name = _seed_vault(vault, result="partial")
    run_script(SCRIPT, ["emit", "--slice", name, "--vault", vault, "--repo-root", repo])
    r = run_script(SCRIPT, ["verify", "--branch", "slice/007-x", "--repo-root", repo])
    assert r.returncode == 1
    assert "not 'pass'" in r.stdout


def test_verify_regression_needs_approved_deferral(run_script, vault, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    name = _seed_vault(vault, failed_rows=2)  # regression, no deferral
    run_script(SCRIPT, ["emit", "--slice", name, "--vault", vault, "--repo-root", repo])
    r = run_script(SCRIPT, ["verify", "--branch", "slice/007-x", "--repo-root", repo])
    assert r.returncode == 1 and "deferral" in r.stdout
    # approved deferral -> consciously accepted, gate passes
    name = _seed_vault(tmp_path / "vault2", failed_rows=2, deferral_approved=True)
    (tmp_path / "vault2").mkdir(exist_ok=True)
    run_script(SCRIPT, ["emit", "--slice", name, "--vault", tmp_path / "vault2", "--repo-root", repo])
    r = run_script(SCRIPT, ["verify", "--branch", "slice/007-x", "--repo-root", repo])
    assert r.returncode == 0, r.stdout


def test_verify_non_slice_branch_not_applicable(run_script, tmp_path):
    r = run_script(SCRIPT, ["verify", "--branch", "feature/login", "--repo-root", tmp_path])
    assert r.returncode == 0
    assert "not applicable" in r.stdout
