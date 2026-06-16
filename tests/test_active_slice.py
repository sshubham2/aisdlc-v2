"""scripts/lib/active_slice.py — in-flight slice resolution (4.4 priority d).

Resolution precedence: git branch (slice/NNN-name) -> vault scan (non-terminal
preferred, then most-recent) -> None. The vault-scan tests pass a non-git repo_root
so `_git_branch` returns None and resolution falls through to the scan.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id


def _make_slice(vault, folder, *, stage=None, at=None):
    d = vault / "slices" / folder
    d.mkdir(parents=True)
    m = {}
    if stage is not None:
        m["stage"] = stage
    if at is not None:
        m["at"] = at
    if m:
        (d / "milestone.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def test_none_when_no_slices(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    assert resolve_active_slice(vault, repo_root=tmp_path) is None


def test_vault_scan_prefers_non_terminal(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-001-done", stage="complete", at="2026-01-01")
    _make_slice(vault, "slice-002-wip", stage="build", at="2026-01-02")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info is not None
    assert info["slice"] == "slice-002"
    assert info["source"] == "vault-scan"


def test_two_non_terminal_is_ambiguous(tmp_path):
    # AC3 RECONCILIATION (slice-014): two genuinely-active slices from a non-disambiguating
    # (non-git) call site is AMBIGUOUS -- the resolver refuses to recency-guess. This test
    # previously ENSHRINED the bug by asserting the most-recent slice was returned.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-003-a", stage="design", at="2026-03-01")
    _make_slice(vault, "slice-004-b", stage="design", at="2026-03-09")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info is not None
    assert info["slice"] is None
    assert info["source"] == "ambiguous"
    ids = {c["slice"] for c in info["candidates"]}
    assert ids == {"slice-003", "slice-004"}


def test_all_terminal_falls_back_to_most_recent(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-005-a", stage="complete", at="2026-05-01")
    _make_slice(vault, "slice-006-b", stage="complete", at="2026-05-09")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["slice"] == "slice-006"


def test_archive_dir_excluded(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "slices" / "archive").mkdir(parents=True)
    _make_slice(vault, "slice-007-a", stage="build", at="2026-07-01")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["slice"] == "slice-007"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_branch_resolution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "tester")
    git("commit", "--allow-empty", "-m", "init")
    git("checkout", "-b", "slice/009-feature")

    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-009-feature", stage="build", at="2026-09-01")
    info = resolve_active_slice(vault, repo_root=repo)
    assert info is not None
    assert info["slice"] == "slice-009"
    assert info["source"] == "git-branch"


def test_folder_only_cli(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-010-x", stage="build", at="2026-10-01")
    r = run_script(
        "scripts/lib/active_slice.py",
        ["--vault", str(vault), "--repo-root", str(tmp_path), "--folder-only"],
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "slice-010-x"


# ── resolve_slice_by_id (archive-aware by-id lookup for /slice-story; B1) ─────────

def test_resolve_by_id_active(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-011-feature", stage="build")
    info = resolve_slice_by_id(vault, "slice-011")
    assert info is not None
    assert info["slice"] == "slice-011"
    assert info["source"] == "by-id-active"


def test_resolve_by_id_finds_archived(tmp_path):
    # a shipped slice lives under slices/archive/ — where /reflect moved it BEFORE
    # /commit-slice's on-ship auto-emit runs (the B1 case resolve_active_slice misses).
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-012-shipped", stage="complete")
    info = resolve_slice_by_id(vault, "slice-012")
    assert info is not None
    assert info["slice"] == "slice-012"
    assert info["source"] == "by-id-archive"
    assert "archive" in info["path"].replace("\\", "/")


def test_resolve_by_id_active_preferred_over_archive(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-013-active", stage="build")
    _make_slice(vault, "archive/slice-013-old", stage="complete")
    info = resolve_slice_by_id(vault, "slice-013")
    assert info["source"] == "by-id-active"  # slices/ searched before slices/archive/


def test_resolve_by_id_matches_full_folder_name(tmp_path):
    # the id is given as the canonical slice-NNN, but slice-NNN-<name> must also match
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-014-some-name", stage="complete")
    assert resolve_slice_by_id(vault, "slice-014")["slice"] == "slice-014"
    assert resolve_slice_by_id(vault, "slice-014-some-name")["slice"] == "slice-014"


def test_resolve_by_id_none_when_missing(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-015-x", stage="build")
    assert resolve_slice_by_id(vault, "slice-099") is None
    assert resolve_slice_by_id(vault, "not-a-slice") is None


def test_resolve_by_id_absolute_path(tmp_path):
    # M4: the returned path is absolute so the on-ship write target is cwd-independent.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-016-z", stage="complete")
    info = resolve_slice_by_id(vault, "slice-016")
    assert Path(info["path"]).is_absolute()


def test_slice_cli_resolves_archive(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "archive/slice-017-shipped", stage="complete")
    r = run_script(
        "scripts/lib/active_slice.py",
        ["--vault", str(vault), "--slice", "slice-017", "--path-only"],
    )
    assert r.returncode == 0
    assert r.stdout.strip().replace("\\", "/").endswith("slices/archive/slice-017-shipped")


# ── slice-014: fail-closed tri-state resolution under parallelism (SC-23 / ADR-010) ──

def test_single_non_terminal_resolves_even_from_nongit(tmp_path):
    # M3 happy path: exactly ONE active slice resolves cleanly even from a non-git cwd
    # (the >=2 ambiguity trigger must NEVER refuse the single-active 99% case).
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-040-only", stage="build", at="2026-04-01")
    info = resolve_active_slice(vault, repo_root=tmp_path)  # tmp_path is non-git
    assert info is not None
    assert info["slice"] == "slice-040"
    assert info["source"] == "vault-scan"


def test_ambiguous_sentinel_distinct_from_none(tmp_path):
    # The AMBIGUOUS sentinel MUST be distinct from the benign source='none' (no slices),
    # so a consumer can tell 'refuse + name candidates' from 'nothing to do'.
    vault = tmp_path / "v"
    vault.mkdir()
    assert resolve_active_slice(vault, repo_root=tmp_path) is None  # 0 slices -> None
    _make_slice(vault, "slice-041-a", stage="design", at="2026-04-01")
    _make_slice(vault, "slice-042-b", stage="design", at="2026-04-02")
    info = resolve_active_slice(vault, repo_root=tmp_path)
    assert info["source"] == "ambiguous"
    assert info["exists"] is False
    # candidate entries carry the M2 shape {slice, folder, stage, at}
    c = info["candidates"][0]
    assert set(c) == {"slice", "folder", "stage", "at"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_is_main_worktree_main_vs_linked(tmp_path):
    from scripts.lib.active_slice import is_main_worktree
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)

    git(repo, "init")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "tester")
    git(repo, "commit", "--allow-empty", "-m", "init")
    assert is_main_worktree(repo) is True            # main tree: git-dir == git-common-dir
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-b", "slice/100-x", str(wt))
    assert is_main_worktree(wt) is False             # linked worktree: they differ


def test_is_main_worktree_nongit_is_indeterminate(tmp_path):
    from scripts.lib.active_slice import is_main_worktree
    assert is_main_worktree(tmp_path) is None         # non-git cwd -> indeterminate (not refuse)


def test_cli_exit4_and_empty_path_only_on_ambiguous(run_script, tmp_path):
    # AC5: ambiguous -> exit 4 (NOT 3), EMPTY --path-only stdout, fail-visible HALT on
    # stderr NAMING the candidate slices + the disambiguation remedy.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-050-a", stage="design", at="2026-05-01")
    _make_slice(vault, "slice-051-b", stage="design", at="2026-05-02")
    r = run_script("scripts/lib/active_slice.py",
                   ["--vault", str(vault), "--repo-root", str(tmp_path), "--path-only"])
    assert r.returncode == 4
    assert r.stdout.strip() == ""
    assert "AMBIGUOUS" in r.stderr
    assert "slice-050" in r.stderr and "slice-051" in r.stderr
    assert "--slice" in r.stderr


def test_cli_json_ambiguous_payload(run_script, tmp_path):
    # M-add-2: --json on ambiguous emits a parseable full-keyed payload with
    # source=='ambiguous' + candidates[]; the stderr HALT never contaminates stdout JSON.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-050-a", stage="design", at="2026-05-01")
    _make_slice(vault, "slice-051-b", stage="design", at="2026-05-02")
    r = run_script("scripts/lib/active_slice.py",
                   ["--vault", str(vault), "--repo-root", str(tmp_path), "--json"])
    assert r.returncode == 4
    payload = json.loads(r.stdout)  # stdout is clean parseable JSON
    assert payload["source"] == "ambiguous"
    assert payload["slice"] is None
    assert {c["slice"] for c in payload["candidates"]} == {"slice-050", "slice-051"}
    assert "exists" in payload  # full stable key set


def test_cli_path_only_clean_no_log_contamination(run_script, tmp_path):
    # m3: a clean single-active resolve -> stdout is EXACTLY the path (one line), exit 0;
    # the observability log goes to STDERR only.
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-052-x", stage="build", at="2026-05-03")
    r = run_script("scripts/lib/active_slice.py",
                   ["--vault", str(vault), "--repo-root", str(tmp_path), "--path-only"])
    assert r.returncode == 0
    out = r.stdout.strip()
    assert out.replace("\\", "/").endswith("slices/slice-052-x")
    assert "\n" not in out  # exactly one line, no log line leaked onto stdout
    assert "active-slice resolved" in r.stderr  # the log went to stderr


# ── slice-014: the 2 Python wrappers must handle AMBIGUOUS without crashing (B1) and
#    NAME candidates rather than lying 'no active slice' (M-add-1). RED until T5. ──

def test_active_slice_brief_ambiguous_names_candidates(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-060-a", stage="design", at="2026-06-01")
    _make_slice(vault, "slice-061-b", stage="design", at="2026-06-02")
    r = run_script("scripts/lib/active_slice_brief.py",
                   ["--vault", str(vault), "--repo-root", str(tmp_path)])
    blob = (r.stdout + r.stderr).lower()
    assert "no active slice" not in blob          # must not lie
    assert "slice-060" in blob and "slice-061" in blob  # must name candidates
    assert "traceback" not in blob                # must not crash (B1)


def test_active_slice_info_ambiguous_not_no_slice(run_script, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    _make_slice(vault, "slice-060-a", stage="design", at="2026-06-01")
    _make_slice(vault, "slice-061-b", stage="design", at="2026-06-02")
    r = run_script("skills/validate-slice/scripts/active_slice_info.py",
                   ["--vault", str(vault), "--repo-root", str(tmp_path), "--json"])
    assert "Traceback" not in (r.stdout + r.stderr)  # must not crash (B1)
    payload = json.loads(r.stdout)
    assert payload.get("source") == "ambiguous"
    assert payload.get("ready_to_validate") is False
    blob = json.dumps(payload).lower()
    assert "run /slice first" not in blob            # must not lie
    assert "slice-060" in blob and "slice-061" in blob


# ── AC4 executable guard: no SKILL.md injection may swallow the resolver stderr ──

def test_ac4_no_skill_swallows_active_slice_stderr():
    # AC4 (slice-014/B2): the resolver fail-visibly exits 4 + prints an AMBIGUOUS HALT to
    # stderr; no SKILL.md injection may 2>/dev/null-swallow it (that would silently skip).
    from scripts.lib.active_slice_guard_audit import audit
    repo = Path(__file__).resolve().parents[1]
    violations = audit(repo)
    assert violations == [], (
        "SKILL.md injections that swallow the active_slice resolver stderr "
        f"(AMBIGUOUS HALT would be discarded): {violations}"
    )
