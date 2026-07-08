"""Unit coverage for scaffold_reality_gates.py -- the idempotent reality-gates manifest
scaffolder (slice-062 / SC-095 / ADR-059; mirrors scaffold_test_first_plan.py's atomic write).

AC5: scaffolds the empty <repo-root>/.aisdlc/reality-gates.json skeleton; a project that
declares no gates yields an empty set (no-op); a populated manifest is NEVER clobbered.
m3: mkdir's the .aisdlc/ parent on a greenfield repo (no dir yet). M-add-2: distinguishes
the repo manifest from the external ~/.aisdlc vault and asserts/force-includes the path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plugin root -> scripts.lib

from scripts.lib import scaffold_reality_gates as sg  # noqa: E402

_MANIFEST_REL = Path(".aisdlc") / "reality-gates.json"


def test_greenfield_no_aisdlc_dir_creates_parent_and_empty_skeleton(tmp_path):
    # m3: the greenfield case the scaffolder EXISTS to serve -- no .aisdlc/ dir yet.
    assert not (tmp_path / ".aisdlc").exists()
    res = sg.scaffold(str(tmp_path))
    p = tmp_path / _MANIFEST_REL
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["_schema"] == "aisdlc/reality-gates@1"
    assert data["gates"] == {"security": [], "nfr": [], "ops": []}   # empty set -> no-op
    assert res["action"] == "created"


def test_idempotent_second_run_is_noop(tmp_path):
    sg.scaffold(str(tmp_path))
    before = (tmp_path / _MANIFEST_REL).read_text(encoding="utf-8")
    res = sg.scaffold(str(tmp_path))
    after = (tmp_path / _MANIFEST_REL).read_text(encoding="utf-8")
    assert before == after                 # byte-identical -> genuinely idempotent
    assert res["action"] == "noop"


def test_never_clobbers_a_populated_manifest(tmp_path):
    d = tmp_path / ".aisdlc"
    d.mkdir(parents=True)
    populated = {"_schema": "aisdlc/reality-gates@1",
                 "gates": {"security": [{"id": "bandit", "command": "<interp> -m bandit -r ."}],
                           "nfr": [], "ops": []}}
    (d / "reality-gates.json").write_text(json.dumps(populated), encoding="utf-8")
    res = sg.scaffold(str(tmp_path))
    after = json.loads((d / "reality-gates.json").read_text(encoding="utf-8"))
    assert after == populated              # declared gates preserved, never overwritten
    assert res["action"] == "noop"


def test_result_reports_the_repo_path_not_the_external_vault(tmp_path):
    # M-add-2: the scaffold target is the REPO manifest, distinct from the external ~/.aisdlc vault.
    res = sg.scaffold(str(tmp_path))
    assert str(tmp_path) in res["path"]
    assert res["path"].endswith(str(_MANIFEST_REL)) or res["path"].endswith(".aisdlc/reality-gates.json")


def test_cli_scaffolds_and_exits_zero(tmp_path):
    rc = sg.main([str(tmp_path)])
    assert rc == 0
    assert (tmp_path / _MANIFEST_REL).is_file()


def test_cli_missing_repo_arg_is_usage_error():
    import pytest
    with pytest.raises(SystemExit) as exc:
        sg.main([])
    assert exc.value.code == 2


def test_gitignore_hint_fires_when_aisdlc_is_ignored(tmp_path):
    # CR3 / M-add-2: the CI-manifest defense -- if a host repo gitignores .aisdlc/, the
    # scaffolder must WARN with a force-include hint (else the security manifest silently
    # never travels to CI). Exercise the is-ignored branch with a real git repo.
    import subprocess
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if run("init").returncode != 0:
        import pytest
        pytest.skip("git not available")
    (tmp_path / ".gitignore").write_text(".aisdlc/\n", encoding="utf-8")
    res = sg.scaffold(str(tmp_path))
    assert res["gitignore_hint"] is not None
    assert "!.aisdlc/reality-gates.json" in res["gitignore_hint"]


def test_gitignore_hint_is_none_when_tracked(tmp_path):
    import subprocess
    if subprocess.run(["git", "-C", str(tmp_path), "init"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        import pytest
        pytest.skip("git not available")
    res = sg.scaffold(str(tmp_path))   # no .gitignore -> not ignored
    assert res["gitignore_hint"] is None


def test_setup_wires_the_scaffolder_on_the_default_path(tmp_path):
    # AC5 wiring doc-guard: /setup's default (non---check) path invokes the scaffolder, placed
    # BEFORE the deps install (m3) and gated to the mutation path (--check returns earlier).
    root = Path(__file__).resolve().parents[1]
    src = (root / "skills" / "setup" / "scripts" / "setup.py").read_text(encoding="utf-8")
    assert "scaffold_reality_gates" in src
    assert 'if "--check" in argv' in src
    # the scaffold call sits after the --check early-return (default path) and before Step 1 deps
    idx_check = src.index('if "--check" in argv')
    idx_scaffold = src.index("scaffold_reality_gates import scaffold")
    idx_deps = src.index("Step 1 - install deps")
    assert idx_check < idx_scaffold < idx_deps
