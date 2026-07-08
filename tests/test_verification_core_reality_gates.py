"""Unit coverage for the reality-gate fold added to verification_core (slice-062 / SC-095 / ADR-059).

AC2: verification_core exposes a runner (run_declared_gates) that runs each declared gate
through the existing run_verification and maps its ExecVerdict to a fail-closed 2-valued
GateResult: ONLY ExecVerdict PASS -> GateResult PASS; ABSENT and EVERY FAIL subkind
(not-runnable / exited-nonzero / timeout / unparseable) -> GateResult FAIL. The fold is
purely additive (run_verification / ExecVerdict untouched) and leaf-pure (no file IO).

Design invariant #3 (per-entry totality / no common-cause abort): a malformed ENTRY FAILs
that entry (subkind 'bad-entry') without aborting the loop -- following entries still run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plugin root -> scripts.lib

from scripts.lib.verification_core import (  # noqa: E402
    ExecVerdict,
    GateResult,
    run_declared_gates,
    run_verification,
)

_ROOT = Path(__file__).resolve().parents[1]
_PASS = 'python -c "pass"'
_FAIL = 'python -c "import sys; sys.exit(1)"'


# ── the additive symbols exist and do not touch the ExecVerdict enum ──
def test_gate_result_is_a_two_valued_sibling_not_execverdict():
    r = GateResult(gate_id="x", surface="security", status="PASS")
    assert r.status == "PASS"
    # GateResult is its own type -- NOT an ExecVerdict (slice-004: sibling, not enum-widening)
    assert not isinstance(r, ExecVerdict)


# ── hermetic mapping via an injected fake _run (every ExecVerdict subkind) ──
def _fake(verdict: ExecVerdict):
    def _run(command, repo_root, *, timeout=None):
        return verdict
    return _run


def test_only_pass_maps_to_pass():
    gates = [{"id": "g", "surface": "security", "command": "whatever"}]
    out = run_declared_gates(gates, _ROOT, _run=_fake(ExecVerdict("PASS", "", "ok")))
    assert [r.status for r in out] == ["PASS"]


def test_every_fail_subkind_maps_to_fail():
    for sub in ("not-runnable", "exited-nonzero", "timeout", "unparseable", "exec-error"):
        gates = [{"id": "g", "surface": "nfr", "command": "whatever"}]
        out = run_declared_gates(gates, _ROOT, _run=_fake(ExecVerdict("FAIL", "boom", sub)))
        assert out[0].status == "FAIL"
        assert out[0].subkind == sub


def test_absent_maps_to_fail_not_pass():
    # ABSENT is a per-slice repro concept -- meaningless for a project reality gate, so it TRIPS.
    gates = [{"id": "g", "surface": "security", "command": "whatever"}]
    out = run_declared_gates(gates, _ROOT, _run=_fake(ExecVerdict("ABSENT", "absent", "absent-tests")))
    assert out[0].status == "FAIL"


# ── per-entry totality: a bad entry FAILs itself, the loop does not abort ──
def test_bad_entry_fails_without_aborting_the_loop():
    gates = [
        {"id": "good1", "surface": "security", "command": _PASS},
        {"id": "bad", "surface": "security"},                       # missing command
        {"id": "good2", "surface": "security", "command": _PASS},
    ]
    out = run_declared_gates(gates, _ROOT)                          # real run_verification
    assert len(out) == 3                                            # all three evaluated (no abort)
    assert out[0].status == "PASS" and out[2].status == "PASS"     # goods still ran after the bad one
    bad = out[1]
    assert bad.status == "FAIL" and bad.subkind == "bad-entry"


def test_missing_id_is_also_a_bad_entry():
    gates = [{"surface": "ops", "command": _PASS}]                  # missing id
    out = run_declared_gates(gates, _ROOT)
    assert out[0].status == "FAIL" and out[0].subkind == "bad-entry"


# ── CR1 fail-closed: a non-empty command that runs NOTHING must FAIL, not PASS ──
def test_command_with_no_runnable_segment_fails_not_pass():
    # ';' / '; ;' / backtick-fence-only tokenize to no runnable argv -- run_verification
    # would return PASS 'ok' (verifying nothing). The gate must FAIL (false-green guard).
    for cmd in (";", "; ;", " ; ", "``", "`  `"):
        out = run_declared_gates([{"id": "noop", "surface": "security", "command": cmd}], _ROOT)
        assert out[0].status == "FAIL", f"{cmd!r} slipped past as {out[0].status}"
        assert out[0].subkind == "no-runnable-command"


# ── real-engine reality checks (mirrors the design spike) ──
def test_real_pass_command():
    out = run_declared_gates([{"id": "p", "surface": "security", "command": _PASS}], _ROOT)
    assert out[0].status == "PASS"


def test_real_nonzero_command_fails():
    out = run_declared_gates([{"id": "b", "surface": "nfr", "command": _FAIL}], _ROOT)
    assert out[0].status == "FAIL" and out[0].subkind == "exited-nonzero"


def test_real_missing_binary_fails_not_runnable():
    out = run_declared_gates(
        [{"id": "m", "surface": "security", "command": "definitely-not-a-real-binary-zzz --scan ."}],
        _ROOT,
    )
    assert out[0].status == "FAIL" and out[0].subkind == "not-runnable"


def test_empty_gate_list_is_empty_result():
    assert run_declared_gates([], _ROOT) == []


# ── m5: the per-gate timeout is threaded to run_verification (real, not decorative) ──
def test_timeout_is_threaded_to_the_runner():
    seen = {}

    def _recording_run(command, repo_root, *, timeout=None):
        seen["timeout"] = timeout
        return ExecVerdict("PASS", "", "ok")

    run_declared_gates([{"id": "g", "surface": "nfr", "command": "x"}], _ROOT,
                       timeout=7.5, _run=_recording_run)
    assert seen["timeout"] == 7.5


# ── run_verification itself is untouched (no-op safety for existing importers) ──
def test_run_verification_unchanged_still_total_three_valued():
    assert run_verification(_PASS, _ROOT).status == "PASS"
    assert run_verification(_FAIL, _ROOT).status == "FAIL"
