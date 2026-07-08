"""
Bug: /release's release_cut cuts version X.Y.Z (merging the integration branch into
master + bumping plugin.json), but the CHANGELOG it commits files the released slices
under '## [Unreleased]' and produces NO '## [X.Y.Z]' section carrying that work.

Root cause: release_cut runs assemble_changelog while the integration merge is still
STAGED (`git merge --no-ff --no-commit`). assemble_changelog reconstructs versions from
git history read at the un-advanced HEAD, so the merged-in open-period commits are not
yet reachable and `_roll_forward(--new-version X.Y.Z)` has nothing to attribute onto the
new version. The released work becomes reachable only when the merge commits — which
happens AFTER the changelog was generated and written into that same commit.

Expected: after a successful release cut of 2.36.0, CHANGELOG.md contains a
'## [2.36.0]' section carrying the released slice, and that slice is NOT stranded under
'## [Unreleased]'.
Actual: no '## [2.36.0]' section; the released slice sits under '## [Unreleased]'.

Reproduces on REAL git with the REAL assemble_changelog hook. (The existing
tests/test_release_cut.py injects a FAKE changelog hook that always appends a version
section, which is exactly why this ordering bug was never caught there.)
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib._git_default_branch import run_git  # noqa: E402

RC = ROOT / "skills" / "release" / "scripts" / "release_cut.py"
gitok = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

INTENT = "DEMO-SLICE-INTENT-the-released-slice-work"


def _load():
    spec = importlib.util.spec_from_file_location("release_cut", RC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_cut"] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _set_version(repo, v):
    p = repo / ".claude-plugin" / "plugin.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": "ai-sdlc", "version": v}, indent=2) + "\n", encoding="utf-8")


def _fake_bump(repo, version, level):
    """Only the CHANGELOG step is under test — bump is injected (mirrors test_release_cut.py)."""
    _set_version(Path(repo), version)
    return (True, version)


def _repo_with_integration_slice(tmp_path):
    """A released trunk at 2.35.1 + an aisdlc-uat integration branch ONE slice ahead;
    the slice commit's subject carries an exact `slice-900` token so its changelog
    record can join to it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    _set_version(repo, "2.35.1")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [2.35.1] — 2026-01-01\n- base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base 2.35.1")
    _git(repo, "branch", "aisdlc-uat")
    _git(repo, "checkout", "aisdlc-uat")
    (repo / "feat.py").write_text("slice work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat(demo): the slice work (slice-900)")
    _git(repo, "checkout", "master")
    return repo


def _vault_with_slice_record(tmp_path):
    """A temp vault whose slices/archive holds one changelog.json record for slice-900."""
    vault = tmp_path / "vault"
    d = vault / "slices" / "archive" / "slice-900-demo"
    d.mkdir(parents=True)
    (d / "changelog.json").write_text(json.dumps({
        "slice": "slice-900",
        "type": "feat",
        "scope": "demo",
        "intent": INTENT,
        "adrs": [],
    }), encoding="utf-8")
    return vault


def _section(changelog: str, header: str) -> str:
    """Text from `header` up to the next '## [' section header (exclusive); '' if absent."""
    i = changelog.find(header)
    if i < 0:
        return ""
    rest = changelog[i + len(header):]
    j = rest.find("\n## [")
    return rest if j < 0 else rest[:j]


@gitok
def test_release_cut_files_released_work_under_its_version_section(tmp_path):
    rc = _load()
    repo = _repo_with_integration_slice(tmp_path)
    vault = _vault_with_slice_record(tmp_path)

    r = rc.run_release_cut(
        repo, "2.36.0",
        git=run_git,
        bump=_fake_bump,
        changelog=rc._make_default_changelog(str(vault)),  # the REAL changelog hook
    )
    # the cut itself succeeds; the defect is purely in the CHANGELOG content it committed.
    assert r["action"] == "released", r

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")

    # (1) the released version must have its own section.
    assert "## [2.36.0]" in changelog, (
        "release_cut committed a CHANGELOG with NO '## [2.36.0]' section: the released "
        "work was filed under [Unreleased] because assemble_changelog ran pre-commit "
        "against a HEAD that could not yet see the staged merge.\n"
        f"---- CHANGELOG.md ----\n{changelog}\n----"
    )
    # (2) the released slice must live UNDER its version...
    assert INTENT in _section(changelog, "## [2.36.0]"), (
        "the released slice is missing from the [2.36.0] section (mis-attributed to an "
        f"older version or dropped).\n---- CHANGELOG.md ----\n{changelog}\n----"
    )
    # (3) ...and must NOT be stranded under [Unreleased].
    assert INTENT not in _section(changelog, "## [Unreleased]"), (
        "the released slice is stranded under [Unreleased] instead of [2.36.0]."
    )


def _repo_with_two_merged_slices(tmp_path):
    """A released trunk at 2.35.1 + an aisdlc-uat integration branch that received TWO
    slices, EACH via a `--no-ff` MERGE commit (mirroring /commit-slice) -- so each slice's
    work commit sits on the SECOND-parent side, reachable only via MERGE_HEAD's full parent
    traversal. This is the real 2.38.0 incident shape (M1), NOT a linear 1-commit fixture:
    a `--first-parent`-only union walk would silently drop these slices."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    _set_version(repo, "2.35.1")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [2.35.1] — 2026-01-01\n- base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base 2.35.1")
    _git(repo, "branch", "aisdlc-uat")
    for n in (901, 902):
        _git(repo, "checkout", "-b", f"slice/{n}", "aisdlc-uat")
        (repo / f"feat_{n}.py").write_text(f"slice {n} work\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"feat(demo{n}): work (slice-{n})")
        _git(repo, "checkout", "aisdlc-uat")
        _git(repo, "merge", "--no-ff", f"slice/{n}", "-m", f"Merge slice/{n} (slice-{n})")
    _git(repo, "checkout", "master")
    return repo


def _vault_with_two_slice_records(tmp_path):
    vault = tmp_path / "vault"
    for n in (901, 902):
        d = vault / "slices" / "archive" / f"slice-{n}-demo"
        d.mkdir(parents=True)
        (d / "changelog.json").write_text(json.dumps({
            "slice": f"slice-{n}", "type": "feat", "scope": f"demo{n}",
            "intent": f"MERGED-INTENT-{n}", "adrs": [],
        }), encoding="utf-8")
    return vault


@gitok
def test_release_cut_files_merged_slices_under_version(tmp_path):
    """M1: the DEPLOYED path at the real incident's shape -- multiple slices EACH brought in
    via a --no-ff MERGE commit, so the slice work is reachable ONLY via MERGE_HEAD's
    second-parent traversal. Proves the union walk pulls merge-commit second parents on the
    real run_release_cut + real _make_default_changelog path (a --first-parent regression
    would keep the 1-commit repro green while stranding these)."""
    rc = _load()
    repo = _repo_with_two_merged_slices(tmp_path)
    vault = _vault_with_two_slice_records(tmp_path)

    r = rc.run_release_cut(
        repo, "2.36.0",
        git=run_git,
        bump=_fake_bump,
        changelog=rc._make_default_changelog(str(vault)),
    )
    assert r["action"] == "released", r

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [2.36.0]" in changelog, changelog
    sec = _section(changelog, "## [2.36.0]")
    unrel = _section(changelog, "## [Unreleased]")
    for n in (901, 902):
        assert f"MERGED-INTENT-{n}" in sec, (
            f"slice-{n} (reachable only via MERGE_HEAD's second parent) is missing from the "
            f"[2.36.0] section -- a --first-parent walk would drop it.\n---- CHANGELOG.md ----\n{changelog}")
        assert f"MERGED-INTENT-{n}" not in unrel, f"slice-{n} stranded under [Unreleased]"


@gitok
def test_make_default_changelog_fails_visible_when_merge_head_absent(tmp_path):
    """M-add-1: the in-cut changelog hook must FAIL VISIBLY when MERGE_HEAD is absent, NOT
    silently fall back to a HEAD-only walk (which would strand the released slices under
    [Unreleased] -- the exact incident this slice fixes). With no staged merge, MERGE_HEAD is
    absent, so the hook must return (False, ...) -> which routes into run_release_cut's
    changelog-failed rollback -- and must NOT write a slice-less CHANGELOG."""
    rc = _load()
    repo = _repo_with_integration_slice(tmp_path)   # a normal repo, NO staged merge
    vault = _vault_with_slice_record(tmp_path)
    ok, msg = rc._make_default_changelog(str(vault))(str(repo), "2.36.0")
    assert ok is False, "hook must fail-visibly when MERGE_HEAD is absent, not silently fall back"
    assert "MERGE_HEAD absent" in msg, msg
    # the CHANGELOG must NOT have been overwritten by a silent HEAD-only walk
    assert "## [2.36.0]" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
