"""ci_gate.classify_ci — the /release CI pre-flight classifier (pure, gh-free).

The gate keys on the EXACT integration-branch HEAD SHA, fails CLOSED on red / pending /
no-run, and treats skipped/neutral as non-failing. These tests pin that logic so a
future edit can't silently let a red or unverified uat cut to master (the 066-069 hole).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "release" / "scripts"))

import ci_gate  # noqa: E402

SHA = "b4f3f41abc0000000000000000000000000000ff"
OTHER = "0ff4348dead0000000000000000000000000000ff"


def _run(sha, status="completed", conclusion="success", wf="CI", rid=1):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "workflowName": wf, "databaseId": rid}


def test_green_when_head_sha_run_succeeded():
    v = ci_gate.classify_ci([_run(SHA)], SHA)
    assert v["gate"] == "green"


def test_red_when_head_sha_run_failed():
    v = ci_gate.classify_ci([_run(SHA, conclusion="failure")], SHA)
    assert v["gate"] == "red" and v["conclusion"] == "failure"


def test_pending_when_run_not_completed():
    v = ci_gate.classify_ci([_run(SHA, status="in_progress", conclusion=None)], SHA)
    assert v["gate"] == "pending"


def test_no_run_for_sha_when_only_other_shas_present():
    # a green run for an OLDER sha must NOT vouch for the current HEAD (the whole point)
    v = ci_gate.classify_ci([_run(OTHER, conclusion="success")], SHA)
    assert v["gate"] == "no-run-for-sha"


def test_no_run_for_sha_on_empty():
    assert ci_gate.classify_ci([], SHA)["gate"] == "no-run-for-sha"


def test_failure_dominates_success_for_same_sha():
    runs = [_run(SHA, conclusion="success", rid=1), _run(SHA, conclusion="failure", wf="pytest", rid=2)]
    assert ci_gate.classify_ci(runs, SHA)["gate"] == "red"


def test_pending_dominates_completed_for_same_sha():
    runs = [_run(SHA, conclusion="success", rid=1), _run(SHA, status="queued", conclusion=None, rid=2)]
    assert ci_gate.classify_ci(runs, SHA)["gate"] == "pending"


def test_skipped_and_neutral_are_not_failures():
    runs = [_run(SHA, conclusion="skipped", rid=1), _run(SHA, conclusion="success", rid=2)]
    assert ci_gate.classify_ci(runs, SHA)["gate"] == "green"


def test_all_skipped_no_failure_is_green():
    v = ci_gate.classify_ci([_run(SHA, conclusion="skipped")], SHA)
    assert v["gate"] == "green"


def test_exit_code_mapping_fails_closed():
    # red / pending / no-run BLOCK (exit 1); missing GitHub DEGRADES (exit 3); green proceeds (0)
    assert ci_gate._EXIT["green"] == 0
    assert ci_gate._EXIT["red"] == 1 and ci_gate._EXIT["pending"] == 1 and ci_gate._EXIT["no-run-for-sha"] == 1
    assert ci_gate._EXIT["gh-absent"] == 3 and ci_gate._EXIT["not-github"] == 3


def test_empty_head_sha_never_matches():
    # a blank sha must not accidentally match runs with blank headSha
    v = ci_gate.classify_ci([_run("", conclusion="success")], "")
    assert v["gate"] == "no-run-for-sha"
