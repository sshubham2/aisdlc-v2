"""forbidden_flag_audit.py — static source scanner enforcing AC1's safety floor.

slice-004 principle: a safety property is only real where something ENFORCES it.
This static scanner forbids ``--force`` / ``--force-with-lease`` / ``-D`` /
``--no-verify`` ever appearing as a whole-token git/gh argument in
skills/commit-slice/SKILL.md + pr_flow.py + resolve_sync_target.py — the PROSE +
script path the runtime argv assertion (in test_commit_slice_push) cannot reach.

M1 / APED-1 battery: the scanner must FAIL on a planted forbidden flag, be CLEAN
on the real source, NOT over-match the benign ``--force-window`` (substring trap),
and NOT under-match ``--force-with-lease`` (the longer real flag).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _commit_slice_helpers import load_script  # noqa: E402

audit = load_script("forbidden_flag_audit")


# ── pure scan_text battery (APED-1: over/under-match proof) ────────────────────

def _tokens(text: str) -> list[str]:
    return [m.token for m in audit.scan_text(text)]


def test_flags_bare_force():
    assert _tokens("git push --force") == ["--force"]


def test_flags_force_with_lease():
    # under-match guard: the LONGER real flag must be caught, not silently passed.
    assert _tokens("git push --force-with-lease origin x") == ["--force-with-lease"]


def test_flags_force_delete_uppercase_D():
    assert _tokens("git branch -D slice/001-x") == ["-D"]


def test_flags_no_verify():
    assert _tokens("git commit --no-verify -m x") == ["--no-verify"]


def test_does_not_overmatch_force_window():
    # APED-1 over-match guard: --force-window is a different flag; --force must not
    # match inside it.
    assert _tokens("git foo --force-window 30 --force-with-lease=no") == ["--force-with-lease"] \
        or _tokens("git foo --force-window 30") == []
    assert _tokens("git foo --force-window 30") == []


def test_does_not_match_safe_lowercase_d():
    # `-d` (safe branch delete) and `-D`-embedded compiler defines must pass.
    assert _tokens("git branch -d slice/001-x") == []
    assert _tokens("cc -DDEBUG main.c") == []


def test_clean_text_no_findings():
    assert _tokens("git push -u origin slice/008-x\ngh pr create --base master") == []


def test_scan_text_reports_line_numbers():
    findings = audit.scan_text("ok line\ngit push --force\nok again")
    assert len(findings) == 1
    assert findings[0].line == 2
    assert findings[0].token == "--force"


# ── CLI contract: clean on real source, FAIL on a planted flag ────────────────

def test_cli_clean_on_real_source(run_script):
    # The default scan targets the three real source files; after the SKILL.md
    # prohibition-prose reword (T8) they contain no literal forbidden token.
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", [])
    assert r.returncode == 0, f"scanner not clean on real source:\n{r.stdout}\n{r.stderr}"


def test_cli_fails_on_planted_flag(run_script, tmp_path):
    planted = tmp_path / "planted.md"
    planted.write_text("safe line\nrun `git push --force` to fix\n", encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py",
                   ["--files", str(planted)])
    assert r.returncode == 1
    assert "--force" in r.stdout
    assert "planted.md" in r.stdout


def test_cli_clean_on_planted_safe_file(run_script, tmp_path):
    safe = tmp_path / "safe.md"
    safe.write_text("git push -u origin x\ngit branch -d x\n", encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py",
                   ["--files", str(safe)])
    assert r.returncode == 0


def test_m1_forbidden_flag_audit_fails_on_planted_flag_clean_on_source(run_script, tmp_path):
    """The named TF-1 row: planted FAIL + clean-source + no over/under-match."""
    # planted -> FAIL
    for flag in ("--force", "--force-with-lease", "-D", "--no-verify"):
        p = tmp_path / f"p_{flag.strip('-')}.py"
        p.write_text(f"subprocess.run(['git', 'push', '{flag}'])\n", encoding="utf-8")
        r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py",
                       ["--files", str(p)])
        assert r.returncode == 1, f"{flag} not flagged"
    # real source -> clean
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", [])
    assert r.returncode == 0, f"real source not clean:\n{r.stdout}"
    # over-match guard (benign --force-window) clean
    bw = tmp_path / "benign.sh"
    bw.write_text("git foo --force-window 30\n", encoding="utf-8")
    assert run_script("skills/commit-slice/scripts/forbidden_flag_audit.py",
                      ["--files", str(bw)]).returncode == 0


# ── AC3 / AC4 / M-add-1 — the scoped force-delete exception (slice-057 / SC-092) ──
# The actuator's ONE gh-MERGED-gated `-D` is permitted WITHOUT weakening the floor:
# token -D only, blessed actuator file only, marked line only, budget of one.

_MARKER = "forbidden-flag-audit:allow=branch_force_delete"


def test_actuator_is_genuinely_in_default_scan_set():
    # M-add-1(a): the actuator MUST be a real, EXISTING file in the resolved default set --
    # else scan_file swallows FileNotFoundError -> [] and 'clean' is vacuously true (a path
    # typo would silently UN-strengthen the floor while every test stayed green).
    targets = audit._resolve_targets(audit._REPO.resolve(), None)
    blessed = [t for t in targets if t.name == "local_branch_delete.py"]
    assert blessed, "the actuator must be in DEFAULT_TARGETS"
    assert blessed[0].is_file(), f"actuator not on disk -> silently un-scanned: {blessed[0]}"


def test_real_source_clean_with_actuator_scanned(run_script):
    # AC3: with the actuator now scanned, the default scan is still clean (its one -D is blessed).
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", [])
    assert r.returncode == 0, f"scanner not clean on real source:\n{r.stdout}\n{r.stderr}"


def test_blessed_line_suppressed_only_for_D_in_actuator():
    # AC3 pure: a -D on a marked line in the blessed file is dropped; the finding set empties.
    line = f'    runner(["git", "branch", "-D", branch])  # {_MARKER}'
    fs = audit.scan_text(line, path="skills/commit-slice/scripts/local_branch_delete.py")
    assert [f.token for f in fs] == ["-D"]
    assert audit.apply_scoped_suppressions(fs) == []


def test_unmarked_second_D_in_actuator_fails(run_script, tmp_path):
    # M-add-1(a) positive coverage: a 2nd UNMARKED -D in the actuator file still FAILs.
    f = tmp_path / "local_branch_delete.py"
    f.write_text(
        f'runner(["git", "branch", "-D", b])  # {_MARKER}\n'
        'other(["git", "branch", "-D", b2])\n',           # unmarked -> must FAIL
        encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", ["--files", str(f)])
    assert r.returncode == 1, "an unmarked 2nd -D in the actuator must FAIL (budget of one)"
    assert "-D" in r.stdout


def test_copied_sentinel_in_other_file_still_fails(run_script, tmp_path):
    # M-add-1(b) cross-file rejection: the marker is NOT honored outside the blessed file.
    f = tmp_path / "SKILL.md"
    f.write_text(f'run `git branch -D {{branch}}`  <!-- {_MARKER} -->\n', encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", ["--files", str(f)])
    assert r.returncode == 1, "a copied sentinel in a non-actuator file must NOT bypass the floor"
    assert "-D" in r.stdout


def test_marker_does_not_bless_other_forbidden_flags(run_script, tmp_path):
    # AC3 non-widening: --force on the marked blessed line still FAILs (the exception is -D-only).
    f = tmp_path / "local_branch_delete.py"
    f.write_text(f'run(["git", "push", "--force"])  # {_MARKER}\n', encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", ["--files", str(f)])
    assert r.returncode == 1, "--force must never be blessed by the -D-only exception"
    assert "--force" in r.stdout


def test_budget_of_one_second_blessed_D_fails(run_script, tmp_path):
    # Two marked -D in the blessed file -> only ONE is suppressed; the 2nd FAILs (provably one line).
    f = tmp_path / "local_branch_delete.py"
    f.write_text(
        f'a(["git", "branch", "-D", b])  # {_MARKER}\n'
        f'a(["git", "branch", "-D", b])  # {_MARKER}\n',
        encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", ["--files", str(f)])
    assert r.returncode == 1, "budget of one: a 2nd blessed -D must still FAIL"


def test_stray_D_in_non_actuator_file_still_fails(run_script, tmp_path):
    # AC4: the never-force floor for a stray -D anywhere else is unchanged (a planted -D FAILs).
    p = tmp_path / "some_other.py"
    p.write_text('subprocess.run(["git", "branch", "-D", "x"])\n', encoding="utf-8")
    r = run_script("skills/commit-slice/scripts/forbidden_flag_audit.py", ["--files", str(p)])
    assert r.returncode == 1
