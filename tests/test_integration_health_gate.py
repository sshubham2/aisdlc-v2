"""slice-059 / SC-093 / ADR-056 — the pre-merge integration-health gate.

Regression-covers the ENFORCEMENT itself (AC5) plus the critique fixes:
  * decide-core PROCEED / REFUSE / REFUSE-UNRUNNABLE / OVERRIDDEN mapping, via the
    injected FakeRunner seam (AC1 decision level, AC4 override, m5 no-timeout);
  * PROCEED vs OVERRIDDEN are distinguishable by the JSON `action` field, since both
    exit 0 (M-add-2 — the SKILL logs an override by parsing `action`, not exit code);
  * an empty/whitespace override reason is REJECTED as a usage error (m4);
  * the gate targets the EXPLICIT --repo-root, not ambient cwd (m3/practice);
  * the resolved runner path is the real sibling-skill runner (m3);
  * a REAL end-to-end run through the actual shippability_runner -> verification_core
    (AC3 reuse) REFUSES a seeded-red catalog and PROCEEDS on green (AC5 reality);
  * a region-keyed doc-guard pins the SKILL sub-step-2.7 wiring: it invokes the gate
    AFTER the 2.5 rebase and BEFORE the step-3 merge, STOPs on any non-zero exit, is
    --merge-only (no 5c gate), and logs the override (M2 wiring-guard).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import FakeRunner, cp, load_script  # noqa: E402

GATE = load_script("integration_health_gate")
_ROOT = Path(__file__).resolve().parents[1]
_REAL_RUNNER = GATE._DEFAULT_RUNNER  # exists (asserted below) -> unit tests reach the injected runner


def _runresult(*, failed=0, passed=1, absent=0, fail_rows=()):
    rows = [{"row": f"pass{i}", "status": "PASS", "index": i, "detail": ""} for i in range(passed)]
    rows += [{"row": r, "status": "FAIL", "index": 100 + i, "detail": "boom"} for i, r in enumerate(fail_rows)]
    rows += [{"row": f"absent{i}", "status": "ABSENT", "index": 200 + i, "detail": "not on checkout"} for i in range(absent)]
    return json.dumps({"rows_run": passed + failed + absent, "passed": passed,
                       "failed": failed, "absent": absent, "rows": rows})


# ── decide-core: the injected-runner seam (no real repo/suite needed) ──────────

def test_green_suite_proceeds():
    fake = FakeRunner(lambda argv: cp(argv, returncode=0, stdout=_runresult(passed=3, absent=1)))
    r = GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/wt",
                      runner_path=_REAL_RUNNER)
    assert r["action"] == GATE.PROCEED
    assert GATE._exit_for(r) == 0
    assert not r["failing_rows"]


def test_red_suite_refuses_and_names_rows():
    fake = FakeRunner(lambda argv: cp(argv, returncode=1,
                                      stdout=_runresult(passed=2, failed=1, fail_rows=("bad_test_row",))))
    r = GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/wt", runner_path=_REAL_RUNNER)
    assert r["action"] == GATE.REFUSE
    assert GATE._exit_for(r) == 1
    assert [row["row"] for row in r["failing_rows"]] == ["bad_test_row"]
    assert "bad_test_row" in r["reason"]
    assert "uat is untouched" in r["reason"]


def test_runner_usage_error_is_fail_closed():
    # runner exit 2 (catalog missing/unreadable) -> UNRUNNABLE, never a silent pass.
    fake = FakeRunner(lambda argv: cp(argv, returncode=2, stderr="catalog not found"))
    r = GATE.run_gate(runner=fake, catalog="missing.json", repo_root="/wt", runner_path=_REAL_RUNNER)
    assert r["action"] == GATE.REFUSE_UNRUNNABLE
    assert GATE._exit_for(r) == 3


def test_unparseable_output_is_fail_closed():
    fake = FakeRunner(lambda argv: cp(argv, returncode=0, stdout="not json at all"))
    r = GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/wt", runner_path=_REAL_RUNNER)
    assert r["action"] == GATE.REFUSE_UNRUNNABLE
    assert GATE._exit_for(r) == 3


def test_missing_runner_is_fail_closed_without_calling():
    fake = FakeRunner(lambda argv: pytest.fail("runner must NOT be invoked when the runner path is absent"))
    r = GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/wt",
                      runner_path="/nonexistent/shippability_runner.py")
    assert r["action"] == GATE.REFUSE_UNRUNNABLE
    assert GATE._exit_for(r) == 3
    assert fake.calls == []


def test_override_short_circuits_and_is_distinguishable_from_proceed():
    # AC4 + M-add-2: OVERRIDDEN exits 0 like PROCEED, but the action field distinguishes it,
    # and the reason is carried for logging. The runner is NOT called (bypasses the run).
    fake = FakeRunner(lambda argv: pytest.fail("override must bypass the run_catalog call"))
    r = GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/wt", runner_path=_REAL_RUNNER,
                      override=True, override_reason="known flaky infra, tracked in SC-999")
    assert r["action"] == GATE.OVERRIDDEN
    assert GATE._exit_for(r) == 0
    assert r["overridden"] is True
    assert "SC-999" in r["reason"]
    assert fake.calls == []
    # distinguishable from a clean PASS which is also exit 0:
    assert GATE.OVERRIDDEN != GATE.PROCEED


def test_gate_targets_explicit_repo_root_not_cwd():
    # m3/practice: the gate passes --repo-root <the given checkout> to the runner, deterministically.
    seen = {}
    def handler(argv):
        seen["argv"] = argv
        return cp(argv, returncode=0, stdout=_runresult(passed=1))
    fake = FakeRunner(handler)
    GATE.run_gate(runner=fake, catalog="cat.json", repo_root="/explicit/worktree", runner_path=_REAL_RUNNER)
    argv = seen["argv"]
    assert "--repo-root" in argv
    assert argv[argv.index("--repo-root") + 1] == "/explicit/worktree"
    assert "--json" in argv
    assert "--timeout" not in argv  # m5: default timeout=None (no per-segment timeout)


# ── CLI main(): the m4 empty-reason usage error (no runner needed) ─────────────

def test_cli_rejects_empty_override_reason(capsys):
    rc = GATE.main(["--repo-root", "/wt", "--skip-integration-health", "   "])
    assert rc == 2
    assert "NON-EMPTY reason" in capsys.readouterr().err


def test_cli_override_with_reason_proceeds(capsys):
    rc = GATE.main(["--repo-root", "/wt", "--catalog", "unused.json",
                    "--skip-integration-health", "deploying a docs-only hotfix"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == GATE.OVERRIDDEN
    assert "docs-only hotfix" in out["override_reason"]


# ── m3: the resolved sibling-skill runner path is real ─────────────────────────

def test_default_runner_path_resolves_to_real_shippability_runner():
    assert _REAL_RUNNER == _ROOT / "skills" / "validate-slice" / "scripts" / "shippability_runner.py"
    assert _REAL_RUNNER.is_file()


# ── AC3 + AC5: a REAL end-to-end run through the actual runner -> verification_core ─

def _write_catalog(tmp_path: Path, machine_cmds: list[str]) -> Path:
    rows = [{"id": f"row{i}", "machine_cmd": c} for i, c in enumerate(machine_cmds)]
    p = tmp_path / "shippability.json"
    p.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return p


def test_real_runner_proceeds_on_green(tmp_path, capsys):
    cat = _write_catalog(tmp_path, ['python -c "import sys; sys.exit(0)"'])
    rc = GATE.main(["--repo-root", str(tmp_path), "--catalog", str(cat)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert out["action"] == GATE.PROCEED
    assert out["evidence"]["failed"] == 0


def test_real_runner_refuses_on_seeded_red(tmp_path, capsys):
    cat = _write_catalog(tmp_path, [
        'python -c "import sys; sys.exit(0)"',
        'python -c "import sys; sys.exit(1)"',   # seeded RED
    ])
    rc = GATE.main(["--repo-root", str(tmp_path), "--catalog", str(cat)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1, out
    assert out["action"] == GATE.REFUSE
    assert out["evidence"]["failed"] == 1
    assert out["failing_rows"], "a seeded-red row must be named in failing_rows"


# ── M2: region-keyed doc-guard for the SKILL sub-step-2.7 wiring ───────────────

_SKILL = (_ROOT / "skills" / "commit-slice" / "SKILL.md").read_text(encoding="utf-8")


def _idx(hay: str, needle: str) -> int:
    i = hay.find(needle)
    assert i != -1, f"missing marker: {needle!r}"
    return i


def test_skill_27_invokes_the_gate_between_rebase_and_merge():
    # ordering: 2.5 rebase  <  the 2.7 gate invocation  <  the step-3 merge
    i_rebase = _idx(_SKILL, "2.5. **PSQ-3 rebase**")
    i_gate = _idx(_SKILL, "integration_health_gate.py")
    i_merge = _idx(_SKILL, "git merge --no-ff slice/NNN")
    assert i_rebase < i_gate < i_merge, "the 2.7 gate must sit AFTER the 2.5 rebase and BEFORE the step-3 merge"


def test_skill_27_stops_before_merge_on_nonzero():
    # the wiring must STOP before the merge on any non-zero gate exit (fail-closed enforcement)
    region = _SKILL[_idx(_SKILL, "integration_health_gate.py"):_idx(_SKILL, "git merge --no-ff slice/NNN")]
    assert "STOP" in region
    assert "non-zero" in region.lower()


def test_skill_27_is_merge_only_no_5c_gate():
    # M-add-1: the gate is --merge-only; it must NOT appear in the 5c --push section.
    i_gate = _idx(_SKILL, "integration_health_gate.py")
    i_5c = _idx(_SKILL, "### 5c")
    assert i_gate < i_5c, "the integration-health gate must live in 5b (--merge), before the 5c --push section"
    assert "integration_health_gate.py" not in _SKILL[i_5c:], "no integration-health gate in the --push path (M-add-1)"


def test_skill_27_logs_override_and_names_the_flag():
    region = _SKILL[_idx(_SKILL, "integration_health_gate.py"):_idx(_SKILL, "git merge --no-ff slice/NNN")]
    assert "--skip-integration-health" in region          # the reason-required override flag
    assert "build-log.json" in region and "Events" in region  # the override/outcome is logged
    assert "action" in region                              # M-add-2: parse the JSON action (not exit-code-only)
