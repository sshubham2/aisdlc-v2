"""Unit coverage for reality_gate_runner.py -- the pluggable reality-gate decide-core CLI
(slice-062 / SC-095 / ADR-059; mirrors integration_health_gate.py).

Two-layer semantics:
  DECLARATION layer fail-OPEN  -> absent OR valid-empty manifest -> PASS no-op (exit 0)
  EXECUTION   layer fail-CLOSED -> present-but-malformed/unreadable/unknown-surface manifest
                                   -> REFUSE (exit 3); any declared gate non-PASS -> set FAIL (exit 1)
Covers AC1 (absent/empty no-op + schema), AC2 (fail-closed per-gate + aggregate), AC3/AC4 (the
machine contract both wires consume), M2 (argv: --json accepted no-op), M-add-1 (--repo-root required).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plugin root -> scripts.lib

from scripts.lib import reality_gate_runner as rg  # noqa: E402
from scripts.lib import artifact_lint  # noqa: E402

_PASS = 'python -c "pass"'
_FAIL = 'python -c "import sys; sys.exit(1)"'


def _write_manifest(root: Path, obj) -> Path:
    d = root / ".aisdlc"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "reality-gates.json"
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return p


# ── DECLARATION layer: fail-open no-op ───────────────────────────────────────
def test_absent_manifest_is_no_op_pass(tmp_path):
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.NOOP
    assert rg._exit_for(res) == 0


def test_valid_empty_gates_is_no_op_pass(tmp_path):
    _write_manifest(tmp_path, {"_schema": "aisdlc/reality-gates@1", "gates": {}})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 0


def test_all_empty_surface_lists_is_no_op_pass(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [], "nfr": [], "ops": []}})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 0


def test_gates_key_absent_is_no_op_pass(tmp_path):
    _write_manifest(tmp_path, {"_schema": "aisdlc/reality-gates@1"})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 0


# ── EXECUTION layer: fail-closed on a broken manifest (REFUSE exit 3) ─────────
def test_malformed_json_refuses_exit3(tmp_path):
    _write_manifest(tmp_path, "{ this is not : valid json ")
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.REFUSE and rg._exit_for(res) == 3


def test_top_level_not_object_refuses(tmp_path):
    _write_manifest(tmp_path, "[]")
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 3


def test_gates_not_object_refuses(tmp_path):
    _write_manifest(tmp_path, {"gates": ["security"]})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 3


def test_unknown_surface_key_refuses_never_silent_drop(tmp_path):
    # slice-004: an enum is only real where enforced. A typo'd surface must FAIL, never drop.
    _write_manifest(tmp_path, {"gates": {"securty": [{"id": "x", "command": _PASS}]}})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 3


def test_surface_value_not_a_list_refuses(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": {"id": "x"}}})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path))) == 3


# ── declared gates: pass / fail aggregate ────────────────────────────────────
def test_all_passing_gates_pass_exit0(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [{"id": "a", "command": _PASS}],
                                         "ops": [{"id": "b", "command": _PASS}]}})
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.PASS and rg._exit_for(res) == 0
    assert res["summary"]["declared"] == 2 and res["summary"]["failed"] == 0


def test_one_failing_gate_fails_exit1(tmp_path):
    _write_manifest(tmp_path, {"gates": {"nfr": [{"id": "budget", "command": _FAIL}]}})
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
    assert res["results"][0]["subkind"] == "exited-nonzero"


def test_bad_entry_fails_exit1_not_refuse(tmp_path):
    # A bad ENTRY (missing command) is per-entry FAIL (exit 1), NOT whole-file REFUSE (exit 3).
    _write_manifest(tmp_path, {"gates": {"security": [{"id": "good", "command": _PASS},
                                                      {"id": "bad"}]}})
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
    assert len(res["results"]) == 2  # both evaluated (per-entry totality)
    assert any(r["subkind"] == "bad-entry" for r in res["results"])


def test_missing_binary_declared_gate_fails(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [
        {"id": "scan", "command": "definitely-not-a-real-binary-zzz -r ."}]}})
    res = rg.run_gate(repo_root=str(tmp_path))
    assert res["action"] == rg.FAIL and rg._exit_for(res) == 1
    assert res["results"][0]["subkind"] == "not-runnable"


# ── --surface filter (present but the wires do not pass it yet -- SC-097) ─────
def test_surface_filter_runs_only_that_surface(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [{"id": "s", "command": _FAIL}],
                                         "ops": [{"id": "o", "command": _PASS}]}})
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path), surface_filter="ops")) == 0
    assert rg._exit_for(rg.run_gate(repo_root=str(tmp_path), surface_filter="security")) == 1


# ── structured result + per-gate logging (must_not_defer #3) ─────────────────
def test_result_logs_each_gate_id_and_status(tmp_path):
    _write_manifest(tmp_path, {"gates": {"security": [{"id": "a", "command": _PASS}]}})
    res = rg.run_gate(repo_root=str(tmp_path))
    row = res["results"][0]
    assert row["gate_id"] == "a" and row["surface"] == "security" and row["status"] == "PASS"


# ── CLI contract: --repo-root required (M-add-1), --json accepted no-op (M2) ──
def test_cli_repo_root_is_required():
    with pytest.raises(SystemExit) as exc:
        rg.main(["--json"])            # no --repo-root
    assert exc.value.code == 2         # argparse usage error


def test_cli_json_flag_is_accepted_no_op_and_emits_json(tmp_path, capsys):
    rc = rg.main(["--repo-root", str(tmp_path), "--json"])   # empty repo -> no-op
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)          # ALWAYS emits JSON (machine contract)
    assert payload["action"] == rg.NOOP


def test_cli_exit_codes_end_to_end(tmp_path):
    _write_manifest(tmp_path, {"gates": {"nfr": [{"id": "b", "command": _FAIL}]}})
    assert rg.main(["--repo-root", str(tmp_path)]) == 1


# ── AC1: the bundled example validates against the schema-by-example ──────────
def test_bundled_example_validates_against_schema():
    root = Path(__file__).resolve().parents[1]
    ex = json.loads((root / "schemas" / "reality-gates.example.json").read_text(encoding="utf-8"))
    examples = artifact_lint._load_examples()
    violations = artifact_lint.lint_artifact(ex, "reality-gates", examples["reality-gates"],
                                             "reality-gates.example.json")
    assert violations == []
