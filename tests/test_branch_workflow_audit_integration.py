"""slice-022 AC3 — BRANCH-1 keys on the INTEGRATION branch (uat), not the released trunk.

Under the uat/master model a developer never works on master; "parked on uat with no
slice branch" is the real BRANCH-1 violation. So branch_workflow_audit must resolve uat
(not master) as the branch slices are cut from. Genuine red->green for the
branch_workflow_audit.py:316 swap: on uat the audit resolves 'uat' (pre-swap: 'master')
and fires `on-default-branch` (pre-swap: a `slice-branch-mismatch`).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "skills" / "build-slice" / "scripts" / "branch_workflow_audit.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _load():
    spec = importlib.util.spec_from_file_location("branch_workflow_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["branch_workflow_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _setup(tmp_path, checkout):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "uat")
    _git(repo, "branch", "slice/001-x")
    _git(repo, "checkout", checkout)
    slice_folder = tmp_path / "slice-001-x"
    slice_folder.mkdir()
    return repo, slice_folder


@gitok
def test_on_uat_fires_on_default_branch(tmp_path):
    bwa = _load()
    repo, sf = _setup(tmp_path, checkout="uat")
    res = bwa.audit(slice_folder=sf, repo_root=repo)
    assert res.resolved_default_branch == "uat", "BRANCH-1 must resolve the integration branch (uat)"
    kinds = [v.kind for v in res.violations]
    assert "on-default-branch" in kinds, kinds


@gitok
def test_on_slice_branch_clean(tmp_path):
    bwa = _load()
    repo, sf = _setup(tmp_path, checkout="slice/001-x")
    res = bwa.audit(slice_folder=sf, repo_root=repo)
    assert res.resolved_default_branch == "uat"
    important = [v for v in res.violations if v.severity == "Important"]
    assert not important, [v.kind for v in important]
