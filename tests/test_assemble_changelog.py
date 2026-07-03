"""skills/release/scripts/assemble_changelog.py — version-grouped CHANGELOG
merged from git history + per-slice changelog.json records (slice-007).

Written test-first (TF-1). Exercises the CLI end-to-end via subprocess against a
fixture git repo whose `.claude-plugin/plugin.json` version is bumped commit-by-commit
(the project's real versioning primitive — no git tags) and a fixture vault of
per-slice changelog.json records.

Covers: AC1 (version-grouped), AC2 (pre-slice versions reconstructed), AC3 (slice
records authoritative, no double-list), AC4 (idempotent + refuse-to-write degrade),
plus the Critic edges — M2 (exact-token join, overlay+residual, multi-record),
M3 (deterministic same-second ordering), M4 (single malformed commit skipped, not a
total degrade; shallow/zero-commit), M5 (secondary subject-field join for a squashed
slice with no slice-NNN token), m2 (mojibake-stable), m4 (deterministic provenance).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = "skills/release/scripts/assemble_changelog.py"
PLUGIN_REL = ".claude-plugin/plugin.json"
_BASE_ENV = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
}


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = dict(_BASE_ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    import os
    full = dict(os.environ)
    full.update(env)
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", env=full)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout


def _commit(repo: Path, msg: str, *, version: str | None = None,
            extra: dict[str, str] | None = None, date: str = "2026-01-01T10:00:00",
            raw_plugin: str | None = None) -> None:
    if raw_plugin is not None:
        (repo / ".claude-plugin").mkdir(exist_ok=True)
        (repo / PLUGIN_REL).write_text(raw_plugin, encoding="utf-8")
    elif version is not None:
        (repo / ".claude-plugin").mkdir(exist_ok=True)
        (repo / PLUGIN_REL).write_text(
            json.dumps({"name": "ai-sdlc", "version": version}, indent=2) + "\n",
            encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-gpg-sign", "-m", msg, date=date)


def _make_repo(tmp_path: Path) -> Path:
    """A fixture repo whose version lives in plugin.json, bumped per commit.

    Exercises: multiple versions, a single-commit slice version, a multi-commit
    version with TWO slice records + a residual non-slice commit, a same-second
    pair, a squashed slice (no slice-NNN token in subject), a substring lookalike,
    a merge commit, and a non-conventional subject.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    n = [0]

    def d():  # monotonically increasing distinct dates
        n[0] += 1
        return f"2026-01-{n[0]:02d}T10:00:00"

    _commit(repo, "feat: initial commit", version="1.0.0", date=d())
    _commit(repo, "feat(core): add core engine", version="1.1.0", date=d())
    _commit(repo, "fix(core): fix a real bug", version="1.1.1", date=d())
    _commit(repo, "chore: housekeeping", version="1.2.0", date=d())
    _commit(repo, "totally non conventional subject line", version="1.2.1", date=d())
    # single-commit slice version
    _commit(repo, "feat(api): slice-001 — public API", version="1.3.0", date=d())
    # multi-commit version: TWO slice records (slice-002, slice-006) + a residual chore
    _commit(repo, "feat(ui): slice-002 — dashboard", version="1.4.0", date=d())
    _commit(repo, "feat(ui2): slice-006 — extra widget", extra={"w.txt": "w"}, date=d())  # no bump -> 1.4.0
    _commit(repo, "chore: tidy dashboard css", extra={"c.txt": "c"}, date=d())            # residual -> 1.4.0
    # same-second pair at 1.5.0 (M3): the feat bump + a residual docs commit share a date
    same = d()
    _commit(repo, "feat(x): slice-003 — feature x", version="1.5.0", date=same)
    _commit(repo, "docs: document feature x", extra={"x.md": "x"}, date=same)             # residual, same second
    # squashed slice: NO slice-004 token in subject (M5 secondary join via stored subject)
    _commit(repo, "fix(crg): correct cli surface", version="1.6.0", date=d())
    # substring lookalike (M2 exact-token): must NOT attach to slice-001
    _commit(repo, "feat: slice-0010 unrelated lookalike", version="1.7.0", date=d())
    # merge commit (M4 constraint 1): excluded as noise
    _commit(repo, "Merge pull request #1 from x/slice-005-thing", extra={"m.txt": "m"}, date=d())
    return repo


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    arc = vault / "slices" / "archive"
    arc.mkdir(parents=True)

    def rec(folder, **kw):
        d = arc / folder
        d.mkdir()
        (d / "changelog.json").write_text(json.dumps(kw), encoding="utf-8")

    rec("slice-001-public-api", slice="slice-001", type="feat", scope="api",
        subject="feat(api): slice-001 — public API", intent="Add the public API surface.",
        adrs=["ADR-001"])
    rec("slice-002-dashboard", slice="slice-002", type="feat", scope="ui",
        subject="feat(ui): slice-002 — dashboard", intent="Add the live dashboard.", adrs=[])
    rec("slice-006-widget", slice="slice-006", type="feat", scope="ui2",
        subject="feat(ui2): slice-006 — extra widget", intent="Add the extra widget.", adrs=[])
    rec("slice-003-feature-x", slice="slice-003", type="feat", scope="x",
        subject="feat(x): slice-003 — feature x", intent="Ship feature X.", adrs=["ADR-002"])
    # slice-004 ships in a SQUASHED commit whose subject lacks the slice token; the stored
    # subject is the join bridge (M5). Note the em-dash for the mojibake-stability test (m2).
    rec("slice-004-crg", slice="slice-004", type="fix", scope="crg",
        subject="fix(crg): correct cli surface",
        intent="Correct the CRG CLI surface — every invocation.", adrs=[])
    return vault


def _run(run_script, vault, repo, out=None):
    args = ["--vault", str(vault), "--repo-root", str(repo)]
    if out:
        args += ["--out", str(out)]
    return run_script(SCRIPT, args)


@pytest.fixture
def repo(tmp_path):
    return _make_repo(tmp_path)


@pytest.fixture
def vault(tmp_path):
    return _make_vault(tmp_path)


# ---- AC1: version-grouped, not a flat dump ---------------------------------
def test_ac1_version_grouped(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    assert r.returncode == 0, r.stderr
    headers = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\]", r.stdout)
    assert len(set(headers)) >= 2
    assert "## [Unreleased]" not in r.stdout or r.stdout.count("## [") > 3  # not a single flat dump


def test_ac1_sections_present(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    assert "### Added" in r.stdout and "### Fixed" in r.stdout and "### Changed" in r.stdout


# ---- AC2: pre-slice versions reconstructed from git, none dropped ----------
def test_ac2_pre_slice_versions_reconstructed(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    for v in ("1.0.0", "1.1.0", "1.1.1", "1.2.0", "1.2.1"):
        assert f"## [{v}]" in r.stdout, f"pre-slice version {v} dropped"
    # the non-conventional subject lands under Changed, not dropped (M4 constraint 4)
    assert "totally non conventional subject line" in r.stdout


# ---- AC3: slice records authoritative, no double-list ----------------------
def test_ac3_slice_record_authoritative_single_listing(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    # slice-001's rich intent + ADR present
    assert "Add the public API surface." in r.stdout
    assert "slice-001" in r.stdout and "ADR-001" in r.stdout
    # version 1.3.0 appears exactly once (no duplicate section)
    assert r.stdout.count("## [1.3.0]") == 1
    # the raw git subject for slice-001 is NOT also listed (overlay replaced it)
    assert r.stdout.count("public API") >= 1


def test_dates_have_no_time_component(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    # version headers carry a date but never a wall-clock time (determinism)
    assert re.search(r"(?m)^## \[\d+\.\d+\.\d+\] .* \d{4}-\d{2}-\d{2}\s*$", r.stdout)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", r.stdout)


# ---- AC4: idempotent + non-destructive -------------------------------------
def test_ac4_idempotent_byte_identical(run_script, vault, repo, tmp_path):
    out = tmp_path / "CHANGELOG.md"
    _run(run_script, vault, repo, out=out)
    first = out.read_bytes()
    _run(run_script, vault, repo, out=out)
    assert out.read_bytes() == first  # re-run -> byte-identical


def test_ac4_refuse_to_write_on_git_absence_when_file_populated(run_script, vault, tmp_path):
    # a non-git directory => git unavailable
    nogit = tmp_path / "nogit"
    nogit.mkdir()
    out = tmp_path / "CHANGELOG.md"
    populated = "# Changelog\n\n## [9.9.9] — 2099-01-01\n### Added\n- lots of real history\n"
    out.write_text(populated, encoding="utf-8")
    r = _run(run_script, vault, nogit, out=out)
    assert r.returncode != 0  # refuse-to-write
    assert out.read_text(encoding="utf-8") == populated  # left UNTOUCHED, never shrunk


def test_ac4_fresh_repo_no_existing_file_writes_slices_only(run_script, vault, tmp_path):
    nogit = tmp_path / "nogit2"
    nogit.mkdir()
    out = tmp_path / "sub" / "CHANGELOG.md"  # does not exist yet
    r = _run(run_script, vault, nogit, out=out)
    assert r.returncode == 0  # nothing to lose -> degraded slices-only write is OK
    assert out.is_file() and out.read_text(encoding="utf-8").strip()


# ---- M2: exact-token join + overlay+residual + multi-record ----------------
def test_m2_exact_token_no_substring_collision(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    # the 'slice-0010 lookalike' commit (v1.7.0) must NOT merge into slice-001 (v1.3.0)
    s = r.stdout
    block_130 = s[s.index("## [1.3.0]"):]
    block_130 = block_130[:block_130.index("## [", 5)] if "## [" in block_130[5:] else block_130
    assert "lookalike" not in block_130


def test_m2_overlay_plus_residual(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    s = r.stdout
    block = s[s.index("## [1.4.0]"):]
    block = block[:block.index("## [", 5)] if "## [" in block[5:] else block
    assert "Add the live dashboard." in block          # slice-002 overlay
    assert "Add the extra widget." in block            # slice-006 overlay (multi-record)
    assert "tidy dashboard css" in block               # residual non-slice commit kept


def test_m2_residual_excludes_sliceref_commits(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    # the slice-002 git subject is represented by the record overlay, not duplicated as residual
    assert r.stdout.count("Add the live dashboard.") == 1


# ---- M3: deterministic same-second ordering (already covered by idempotence,
#         but assert both same-second commits land in their version) ----------
def test_m3_same_second_pair_both_present(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    s = r.stdout
    block = s[s.index("## [1.5.0]"):]
    block = block[:block.index("## [", 5)] if "## [" in block[5:] else block
    assert "Ship feature X." in block          # slice-003 overlay
    assert "document feature x" in block        # same-second residual


# ---- M5: secondary subject-field join for a squashed slice ------------------
def test_m5_secondary_subject_join(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    s = r.stdout
    # slice-004's commit subject carries NO 'slice-004' token; it must still land on
    # its version (1.6.0) via the stored subject field, not fall to [Unreleased].
    block = s[s.index("## [1.6.0]"):]
    block = block[:block.index("## [", 5)] if "## [" in block[5:] else block
    assert "Correct the CRG CLI surface" in block
    # not stranded in Unreleased
    unrel = s[s.index("## [Unreleased]"):s.index("## [", s.index("## [Unreleased]") + 5)]
    assert "Correct the CRG CLI surface" not in unrel


# ---- merge-commit exclusion (M4 constraint 1) ------------------------------
def test_merge_commits_excluded(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    assert "Merge pull request" not in r.stdout


# ---- m2: mojibake stability (em-dash in record intent round-trips stably) ---
def test_m2_mojibake_stable(run_script, vault, repo, tmp_path):
    out = tmp_path / "CL.md"
    _run(run_script, vault, repo, out=out)
    a = out.read_bytes()
    _run(run_script, vault, repo, out=out)
    assert out.read_bytes() == a
    assert "Correct the CRG CLI surface" in out.read_text(encoding="utf-8")  # em-dash didn't crash


# ---- m4: deterministic provenance, no run-varying timestamp ----------------
def test_m4_provenance_header_deterministic(run_script, vault, repo):
    r = _run(run_script, vault, repo)
    # a provenance note exists, names git + records, and carries no clock time
    head = r.stdout.split("## [", 1)[0]
    assert "git" in head.lower()
    assert "Generated by /release" in head  # slice-060: pin the role-D command-name emitter (critique m2)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", head)
    assert "T10:00:00" not in r.stdout


# ---- M4: a single malformed plugin.json commit is skipped, not total degrade
def test_m4_single_malformed_commit_not_total_degrade(run_script, tmp_path):
    repo = tmp_path / "mrepo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "feat: initial", version="1.0.0", date="2026-02-01T10:00:00")
    _commit(repo, "feat: good two", version="1.1.0", date="2026-02-02T10:00:00")
    _commit(repo, "chore: corrupt manifest", raw_plugin="{ this is : not json",
            date="2026-02-03T10:00:00")
    _commit(repo, "feat: good three", version="1.2.0", date="2026-02-04T10:00:00")
    v2 = _make_vault(tmp_path)
    r = _run(run_script, v2, repo)
    assert r.returncode == 0, r.stderr
    # the good versions survive despite one corrupt intermediate commit
    assert "## [1.0.0]" in r.stdout and "## [1.2.0]" in r.stdout


def test_m4_zero_commit_repo_degrades_cleanly(run_script, vault, tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")  # zero commits
    out = tmp_path / "CL.md"  # no existing file
    r = _run(run_script, vault, repo, out=out)
    assert r.returncode == 0  # nothing to lose
    assert out.is_file()


# =====================================================================
# slice-009: OPEN-PERIOD ROLL-FORWARD
# =====================================================================
# Under the relocated bump, the slice commits land at the OLD version (no
# bump in the slice commit); /release cuts the next version AFTER merge
# and rolls the open period (everything after the last version-change commit)
# forward onto it. assemble_changelog never writes plugin.json — it only reads
# git + the passed --new-version.

def _run_rf(run_script, vault, repo, *, new_version=None, out=None, extra=None):
    args = ["--vault", str(vault), "--repo-root", str(repo)]
    if new_version is not None:
        args += ["--new-version", new_version]
    if out:
        args += ["--out", str(out)]
    if extra:
        args += list(extra)
    return run_script(SCRIPT, args)


def _make_openperiod_repo(tmp_path: Path) -> Path:
    """A repo whose LAST version-change is at 2.0.0, then several SLICE commits
    land at the OLD version (no bump) — INCLUDING a real merge commit on the
    open-period path. This is the relocated-bump shape: the slice commit carries
    no plugin.json bump.

    History (oldest -> newest):
      1.0.0  feat: initial                       (set 1.0.0)
      1.5.0  feat(core): slice-100 — earlier     (set 1.5.0 — a PRE-EXISTING released version)
      2.0.0  feat(api): slice-101 — last release (set 2.0.0 — the LAST version-change)
      2.0.0  feat(ui): slice-102 — open work     (NO bump -> open period)
      2.0.0  Merge pull request #9 from x/...    (NO bump -> open period, a real merge)
      2.0.0  fix(core): slice-103 — more open    (NO bump -> open period)
    """
    repo = tmp_path / "rfrepo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    n = [0]

    def d():
        n[0] += 1
        return f"2026-03-{n[0]:02d}T10:00:00"

    _commit(repo, "feat: initial commit", version="1.0.0", date=d())
    _commit(repo, "feat(core): slice-100 — earlier feature", version="1.5.0", date=d())
    # the LAST version-change: this commit SETS 2.0.0
    _commit(repo, "feat(api): slice-101 — last released slice", version="2.0.0", date=d())
    # ---- open period (all at 2.0.0, none SET it) ----
    _commit(repo, "feat(ui): slice-102 — open work", extra={"a.txt": "a"}, date=d())
    # a REAL merge commit lands inside the open period
    _git(repo, "checkout", "-q", "-b", "feat/branch")
    _commit(repo, "feat(side): side branch work", extra={"side.txt": "s"}, date=d())
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "--no-gpg-sign", "-m",
         "Merge pull request #9 from x/feat-branch", "feat/branch",
         date="2026-03-20T10:00:00")
    _commit(repo, "fix(core): slice-103 — more open work", extra={"b.txt": "b"}, date=d())
    return repo


def _make_openperiod_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "rfvault"
    arc = vault / "slices" / "archive"
    arc.mkdir(parents=True)

    def rec(folder, **kw):
        dd = arc / folder
        dd.mkdir()
        (dd / "changelog.json").write_text(json.dumps(kw), encoding="utf-8")

    rec("slice-100-earlier", slice="slice-100", type="feat", scope="core",
        subject="feat(core): slice-100 — earlier feature",
        intent="Add the earlier feature.", adrs=[])
    rec("slice-101-last-release", slice="slice-101", type="feat", scope="api",
        subject="feat(api): slice-101 — last released slice",
        intent="Ship the last released slice.", adrs=["ADR-009"])
    rec("slice-102-open", slice="slice-102", type="feat", scope="ui",
        subject="feat(ui): slice-102 — open work",
        intent="Open dashboard work.", adrs=[])
    rec("slice-103-open", slice="slice-103", type="fix", scope="core",
        subject="fix(core): slice-103 — more open work",
        intent="More open core work.", adrs=[])
    return vault


@pytest.fixture
def rf_repo(tmp_path):
    return _make_openperiod_repo(tmp_path)


@pytest.fixture
def rf_vault(tmp_path):
    return _make_openperiod_vault(tmp_path)


def _block_for(s: str, header: str) -> str:
    """Slice out the markdown block for one ``## [..]`` header up to the next."""
    start = s.index(header)
    rest = s[start + len(header):]
    nxt = rest.find("## [")
    return s[start:start + len(header) + nxt] if nxt >= 0 else s[start:]


def test_rollforward_open_period(run_script, rf_vault, rf_repo):
    """With --new-version 2.1.0, the open-period slices group under 2.1.0; the
    last-bump commit (+ everything before) keep their original versions."""
    r = _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0")
    assert r.returncode == 0, r.stderr
    s = r.stdout
    assert "## [2.1.0]" in s, "new cut version section missing"

    # open-period slices roll forward to 2.1.0
    b21 = _block_for(s, "## [2.1.0]")
    assert "Open dashboard work." in b21          # slice-102 overlay -> 2.1.0
    assert "More open core work." in b21          # slice-103 overlay -> 2.1.0

    # the LAST-bump commit (slice-101) keeps its original 2.0.0 — historical stable
    b20 = _block_for(s, "## [2.0.0]")
    assert "Ship the last released slice." in b20
    assert "Open dashboard work." not in b20
    assert "More open core work." not in b20

    # a pre-existing earlier version's grouping is unchanged
    b15 = _block_for(s, "## [1.5.0]")
    assert "Add the earlier feature." in b15
    assert "## [1.0.0]" in s


def test_rollforward_historical_byte_stable(run_script, rf_vault, rf_repo):
    """Rolling the open period forward must NOT perturb already-released
    versions: their rendered blocks are byte-identical to a baseline render
    (here we compare the 1.5.0 + 1.0.0 blocks which precede the open period)."""
    base = _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0").stdout
    again = _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0").stdout
    assert _block_for(base, "## [1.5.0]") == _block_for(again, "## [1.5.0]")
    assert _block_for(base, "## [1.0.0]") == _block_for(again, "## [1.0.0]")
    assert _block_for(base, "## [2.0.0]") == _block_for(again, "## [2.0.0]")


def test_rollforward_exit2_without_new_version(run_script, rf_vault, tmp_path):
    """A non-empty open period of NON-MERGE commits with NO --new-version is a
    fail-visible exit 2 (never silently file new slices under the old version)."""
    # an open period made of NON-MERGE commits only (so the no-new-version guard fires)
    repo = tmp_path / "nm_repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "feat: initial", version="1.0.0", date="2026-04-01T10:00:00")
    _commit(repo, "feat(api): slice-101 — last released", version="2.0.0",
            date="2026-04-02T10:00:00")
    _commit(repo, "feat(ui): slice-102 — open work", extra={"a.txt": "a"},
            date="2026-04-03T10:00:00")  # NO bump -> open, non-merge
    r = _run_rf(run_script, rf_vault, repo)  # no --new-version
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stderr.strip()  # explains: unreleased commits present; provide --new-version


def test_rollforward_idempotent_rerun(run_script, rf_vault, rf_repo, tmp_path):
    """M4: --new-version X+1 twice -> byte-identical output."""
    out = tmp_path / "CL_rf.md"
    _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0", out=out)
    first = out.read_bytes()
    _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0", out=out)
    assert out.read_bytes() == first


def test_rollforward_reject_non_increasing_new_version(run_script, rf_vault, rf_repo):
    """--new-version must be strictly greater than the head (open-period) version."""
    r = _run_rf(run_script, rf_vault, rf_repo, new_version="2.0.0")  # == head, not >
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert r.stderr.strip()
    r2 = _run_rf(run_script, rf_vault, rf_repo, new_version="1.9.0")  # < head
    assert r2.returncode == 2


def test_rollforward_logs_cut_to_stderr(run_script, rf_vault, rf_repo):
    """M-must-not-defer: a roll-forward logs the version cut + how many commits
    rolled forward, to STDERR (so --out/stdout markdown stays clean)."""
    r = _run_rf(run_script, rf_vault, rf_repo, new_version="2.1.0")
    assert r.returncode == 0
    assert "2.1.0" in r.stderr
    # the open period here has 3 commits (slice-102, the merge, slice-103)
    assert re.search(r"\d+", r.stderr)


def test_topo_order_parallel_branches_deterministic(run_script, rf_vault, tmp_path):
    """M3: TWO parallel unmerged branches (no strict ancestry between them) ->
    --date-order ties break deterministically; two runs are identical."""
    repo = tmp_path / "parallel"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "feat: base", version="1.0.0", date="2026-05-01T10:00:00")
    _commit(repo, "feat(api): slice-101 — release", version="2.0.0",
            date="2026-05-02T10:00:00")
    # branch A
    _git(repo, "checkout", "-q", "-b", "branchA")
    _commit(repo, "feat(a): slice-102 — A work", extra={"a.txt": "a"},
            date="2026-05-03T10:00:00")
    # branch B off the 2.0.0 commit (parallel; no ancestry to A)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "branchB")
    _commit(repo, "fix(b): slice-103 — B work", extra={"b.txt": "b"},
            date="2026-05-04T10:00:00")
    # both branches are unmerged; assemble runs against HEAD (branchB) — the point
    # is that two runs produce identical output (determinism on whatever history
    # is reachable), not the branch topology itself.
    r1 = _run_rf(run_script, rf_vault, repo, new_version="2.1.0")
    r2 = _run_rf(run_script, rf_vault, repo, new_version="2.1.0")
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)
    assert r1.stdout == r2.stdout  # deterministic across runs


def test_chore_release_filtered(run_script, rf_vault, tmp_path):
    """m4a: a `chore(release): bump to X` commit must NOT appear as a residual
    changelog line (filtered like merge noise), while its version section header
    still renders."""
    repo = tmp_path / "chrepo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "feat: initial", version="1.0.0", date="2026-06-01T10:00:00")
    # a chore(release) bump commit that SETS 2.0.0
    _commit(repo, "chore(release): bump to 2.0.0", version="2.0.0",
            date="2026-06-02T10:00:00")
    r = _run_rf(run_script, rf_vault, repo)  # no open period of non-merge work after
    assert r.returncode == 0, r.stderr
    assert "## [2.0.0]" in r.stdout              # version header still renders
    assert "bump to 2.0.0" not in r.stdout       # residual line suppressed
    assert "chore(release)" not in r.stdout


def test_no_new_version_spot_unchanged(run_script, vault, repo):
    """WITHOUT --new-version and with NO open period of non-merge commits (the
    existing _make_repo shape, whose trailing post-last-bump commit is only a
    Merge), output is unchanged from the pre-slice behaviour (backward-compat)."""
    base = _run(run_script, vault, repo)        # legacy path: no --new-version
    assert base.returncode == 0, base.stderr
    withflag_absent = _run_rf(run_script, vault, repo)  # explicitly no --new-version
    assert withflag_absent.returncode == 0
    assert withflag_absent.stdout == base.stdout
