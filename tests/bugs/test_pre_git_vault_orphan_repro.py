"""slice-058 / SC-107 / critique B1 — the keystone end-to-end characterization.

The field incident: /triage ran pre-git (vault keyed on ``sha256(cwd)``), then
``git init`` re-keyed the store to ``sha256(<git-common-dir>)`` -> the pre-git vault
orphaned. The fix (ADR-055): gate git at open, then RE-RESOLVE the vault path so
every vault write lands on the SAME git-common-dir-keyed path the pin records.

This test proves the invariant the fix rests on (tournament invariant 1, which
the critique correctly demoted from `holds` to `must-verify`): after the gate's
``git init``, the freshly re-resolved vault path == the pinned path == the
git-common-dir-keyed store, AND that path DIFFERS from the stale pre-git cwd key
(so re-resolution is load-bearing, not decorative). Subprocess resolve dodges the
``_RESOLVED`` memoization.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess

import pytest

from scripts.lib import _vault_paths as vp
from scripts.lib import vault_admin as va

RES = "scripts/lib/_vault_paths.py"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_files_and_pin_coincide_after_git_init(run_script, tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    repo.mkdir()

    # gate's consented actuator: git init at the project root, fail-closed re-verify
    assert va.cmd_git_init(argparse.Namespace(root=str(repo))) == 0
    common = va._git_common_dir_at(str(repo))
    assert common

    # keep the external store under tmp (never pollute the real ~/.aisdlc base)
    base = tmp_path / "base"
    monkeypatch.setattr(vp, "resolve_base", lambda: base)

    expected = vp.external_store_path(common)          # git-common-dir-keyed target
    # the STALE pre-git path the skill would have used WITHOUT re-resolution: keyed on cwd=repo
    stale = base / (
        f"{vp._project_slug(repo.name)}-"
        f"{hashlib.sha256(vp._canonical(str(repo)).encode('utf-8')).hexdigest()[:8]}"
    )
    assert expected != stale  # git-common-dir key != cwd key -> exactly the SC-107 divergence

    # write the pin as the skill does post-gate (no --vault -> external_store_path(common))
    monkeypatch.setattr(va, "_git_common_dir", lambda: common)
    assert va.cmd_write_pin(argparse.Namespace(vault=None)) == 0
    pin_content = (vp.Path(common) / va._CONFIG_REL).read_text(encoding="utf-8").strip()

    # what the skill MUST re-resolve to for its Step-5 writes (subprocess -> fresh process)
    resolved = run_script(RES, ["--path"], cwd=repo).stdout.strip()

    # THE invariant: files-target == pin == the git-common-dir-keyed store (no divergence),
    # and it is NOT the stale cwd key -> the re-resolution is what closes SC-107.
    assert vp.Path(pin_content) == expected
    assert vp.Path(resolved) == expected
    assert vp.Path(resolved) != stale
