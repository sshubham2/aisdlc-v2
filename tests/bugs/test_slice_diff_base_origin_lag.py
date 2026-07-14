"""
Bug: the slice diff base balloons when the local integration branch is ahead of origin.

Three skills (build-slice SKILL.md ~L203, code-review ~L49, validate-slice ~L129) derived the
slice diff base as `git merge-base HEAD origin/HEAD`. Slices branch off the LOCAL integration
branch (uat), which in the dogfood flow is AHEAD of origin. So `merge-base HEAD origin/HEAD`
resolves to a STALE ancestor and `git diff --name-only <base>` balloons to include files from
prior-merged slices, not just the current slice's own work.

Expected: the diff base is the fork point against the LOCAL integration branch, so the
changed-files set contains ONLY the current slice's files.

Fix: scripts/lib/slice_diff_base.py resolves the base off the LOCAL integration branch
(resolve_integration_branch in scripts/lib/_git_default_branch.py), falling back to HEAD when
none resolves; ALWAYS exit 0; the base ref is the sole stdout line.

Coverage:
- test_old_origin_head_base_balloons  -- proves the fixture truly reproduces the bug (guard).
- test_helper_scopes_diff_to_current_slice -- the helper scopes to ONLY the slice's file; the
  old origin/HEAD base is a STRICT superset (m2: committed slice work, strict-superset guard).
- test_helper_stdout_is_single_line   -- stdout carries EXACTLY the base ref, one line (m1).
- test_parity_origin_current_sha_equality -- when origin is up to date, the helper base SHA ==
  the old origin/HEAD base SHA (AC5: SHA-equality, the strictly stronger assertion).
- test_non_repo_falls_back_to_head    -- no resolvable base -> prints HEAD, exit 0, never aborts (AC1).
- test_skill_md_sites_wired_to_helper -- the THREE SKILL.md sites carry ZERO `merge-base HEAD
  origin/HEAD` and each sources the base from slice_diff_base.py (M-add-1: AC2 standing guard --
  the repro tests the helper module, this guards the SKILL.md-site wiring the helper exists to fix).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "scripts" / "lib" / "slice_diff_base.py"
SKILL_SITES = [
    REPO_ROOT / "skills" / "build-slice" / "SKILL.md",
    REPO_ROOT / "skills" / "code-review" / "SKILL.md",
    REPO_ROOT / "skills" / "validate-slice" / "SKILL.md",
]


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )


def _git_ok(cwd, *args):
    r = _git(cwd, *args)
    assert r.returncode == 0, f"git {args} failed: {r.stderr or r.stdout}"
    return r.stdout.strip()


def _changed_names(work, base):
    out = _git_ok(work, "diff", "--name-only", base)
    return sorted(n for n in out.splitlines() if n.strip())


def _run_helper(worktree):
    return subprocess.run(
        [sys.executable, str(HELPER), "--worktree", str(worktree)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )


def _make_base_repo(tmp_path):
    """origin (bare) + a local `work` repo on uat with one base commit PUSHED to origin
    (origin/HEAD -> origin/uat). Returns the work path with origin up to date."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "uat", str(origin)],
                   check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "uat", str(work)],
                   check=True, capture_output=True)
    _git_ok(work, "config", "user.email", "t@example.com")
    _git_ok(work, "config", "user.name", "Test")
    _git_ok(work, "config", "commit.gpgsign", "false")
    _git_ok(work, "remote", "add", "origin", str(origin))
    (work / "base.py").write_text("# base\n", encoding="utf-8")
    _git_ok(work, "add", "base.py")
    _git_ok(work, "commit", "-m", "base")
    _git_ok(work, "push", "origin", "uat")
    _git_ok(work, "remote", "set-head", "origin", "uat")
    return work


def _add_slice_branch(work):
    """Branch slice/curr off the current uat tip and COMMIT cur_slice.py on it."""
    _git_ok(work, "checkout", "-b", "slice/curr")
    (work / "cur_slice.py").write_text("# current slice\n", encoding="utf-8")
    _git_ok(work, "add", "cur_slice.py")
    _git_ok(work, "commit", "-m", "current slice work")


@pytest.fixture()
def origin_lag_repo(tmp_path):
    """Local uat is AHEAD of origin by a prior-merged slice, then a current slice branches off
    the local uat tip. origin/HEAD lags -> merge-base HEAD origin/HEAD balloons."""
    work = _make_base_repo(tmp_path)
    # a PRIOR slice merged into LOCAL uat, NOT pushed -> local uat is now ahead of origin
    (work / "prev_slice.py").write_text("# prev slice\n", encoding="utf-8")
    _git_ok(work, "add", "prev_slice.py")
    _git_ok(work, "commit", "-m", "prev slice")
    _add_slice_branch(work)
    return work


@pytest.fixture()
def origin_current_repo(tmp_path):
    """origin is UP TO DATE with local uat (nothing merged-but-unpushed), then a current slice
    branches off. The helper base and the old origin/HEAD base must resolve the IDENTICAL SHA."""
    work = _make_base_repo(tmp_path)
    _add_slice_branch(work)
    return work


def test_old_origin_head_base_balloons(origin_lag_repo):
    """Guard/documentation: prove the fixture genuinely reproduces the bug -- the OLD
    `merge-base HEAD origin/HEAD` base balloons to include a prior merged slice's file."""
    work = origin_lag_repo
    old_base = _git_ok(work, "merge-base", "HEAD", "origin/HEAD")
    ballooned = _changed_names(work, old_base)
    assert "prev_slice.py" in ballooned and "cur_slice.py" in ballooned, (
        f"expected the buggy origin/HEAD base to balloon to a prior slice; got {ballooned}"
    )


def test_helper_scopes_diff_to_current_slice(origin_lag_repo):
    """The fixed helper resolves the base off the LOCAL integration branch, so the
    changed-files set contains ONLY the current slice's file -- and the old origin/HEAD base
    is a STRICT superset (m2: committed slice work makes the assertion genuine, not trivial)."""
    work = origin_lag_repo
    r = _run_helper(work)
    assert r.returncode == 0, (
        f"slice_diff_base.py failed (rc={r.returncode}): {r.stderr or r.stdout}"
    )
    base = r.stdout.strip()
    assert base, "slice_diff_base.py printed no base"
    scoped = _changed_names(work, base)
    assert scoped == ["cur_slice.py"], (
        f"expected only the current slice's file; got {scoped} (base={base})"
    )
    old_base = _git_ok(work, "merge-base", "HEAD", "origin/HEAD")
    ballooned = set(_changed_names(work, old_base))
    assert set(scoped) < ballooned, (
        f"helper set {scoped} must be a STRICT subset of the old ballooned set {sorted(ballooned)}"
    )


def test_helper_stdout_is_single_line(origin_lag_repo):
    """m1: stdout carries EXACTLY the base ref and nothing else (one line) -- so a consumer's
    bare `base=$(...)` capture is always the clean base; diagnostics go to stderr only."""
    r = _run_helper(origin_lag_repo)
    assert r.returncode == 0, f"helper failed: {r.stderr or r.stdout}"
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must be exactly one line (the base); got {r.stdout!r}"


def test_parity_origin_current_sha_equality(origin_current_repo):
    """AC5 parity: when origin is up to date, the helper base SHA == the old origin/HEAD base
    SHA (SHA-equality, not mere file-set equality -- the strictly stronger assertion)."""
    work = origin_current_repo
    r = _run_helper(work)
    assert r.returncode == 0, f"helper failed: {r.stderr or r.stdout}"
    helper_base = r.stdout.strip()
    old_base = _git_ok(work, "merge-base", "HEAD", "origin/HEAD")
    assert helper_base == old_base, (
        f"parity broken: helper base {helper_base} != origin/HEAD base {old_base}"
    )


def test_non_repo_is_now_REFUSED_not_a_head_fallback(tmp_path):
    """CONTRACT CHANGE (slice-069 / ADR-072; critique M2 + DR-1 M-add-2) — this test used to assert
    the OPPOSITE, and the contract it pinned was itself the defect.

    It previously read: "with no resolvable base (a non-git directory) the helper prints HEAD and
    exits 0 -- it must NEVER abort a gate". That generosity is what made an UNUSABLE worktree
    indistinguishable from a healthy one: the helper prints `HEAD`, the caller runs
    `git diff HEAD...HEAD` (with the production line's literal `2>/dev/null` swallowing git's own
    complaint), gets ZERO lines, and reports "no code changes" -- a confident FALSE GREEN on a slice
    that changed plenty. Reality proved the whole chain end-to-end during this slice's design spike.

    The "never abort a gate" intent is PRESERVED where it is legitimate: a REAL git worktree whose
    integration branch is genuinely unresolvable (no remote, fresh repo) still falls back to HEAD and
    exits 0 -- see `test_real_repo_without_a_remote_still_falls_back_to_head` below. What is refused
    is a --worktree that is not a git worktree ROOT at all (empty string, a two-line capture, a
    nonexistent path, a plain directory). Those are input errors, and an input error must never be
    laundered into a clean diff.
    """
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    r = _run_helper(plain)
    assert r.returncode != 0, (
        "a non-git directory resolved a base -- the fail-open that manufactures an empty-diff "
        f"false green is back (rc={r.returncode}, stdout={r.stdout!r})"
    )
    assert r.stdout.strip() == "", (
        f"stdout must be EMPTY on refusal so a `$( )` capture goes empty and the call-site guard "
        f"fires; got {r.stdout!r}"
    )


def test_real_repo_without_a_remote_still_falls_back_to_head(tmp_path):
    """The legitimate half of the old contract, kept: a REAL git worktree with no remote / no
    integration branch must still resolve (HEAD) and exit 0 -- base resolution never aborts a gate
    for a healthy worktree. Only a BOGUS worktree is refused."""
    repo = tmp_path / "real_repo"
    repo.mkdir()
    _git_ok(repo, "init")
    _git_ok(repo, "config", "user.email", "t@example.com")
    _git_ok(repo, "config", "user.name", "T")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git_ok(repo, "add", "-A")
    _git_ok(repo, "commit", "-m", "init")
    r = _run_helper(repo)
    assert r.returncode == 0, f"a real worktree must still resolve (rc={r.returncode}): {r.stderr}"
    assert r.stdout.strip(), "a real worktree must never yield an EMPTY base"


def test_skill_md_sites_wired_to_helper():
    """M-add-1 (AC2 standing guard): the repro tests the helper MODULE, but the three diff-base
    SITES live in SKILL.md bash that pytest never sources. Without this, a future revert of any
    site to `merge-base HEAD origin/HEAD` is undetected. Assert (a) zero occurrences remain and
    (b) each site sources the base from slice_diff_base.py."""
    for site in SKILL_SITES:
        assert site.exists(), f"expected SKILL.md site at {site}"
        text = site.read_text(encoding="utf-8")
        assert "merge-base HEAD origin/HEAD" not in text, (
            f"{site.parent.name}/SKILL.md still derives the diff base from origin/HEAD (SC-043 regression)"
        )
        assert "slice_diff_base.py" in text, (
            f"{site.parent.name}/SKILL.md does not source the diff base from slice_diff_base.py"
        )
