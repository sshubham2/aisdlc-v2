"""Repro + standing guard (BFRD-1, slice-056 / SC-087): /code-review's review-diff
collection must not drop a branch-only NEW TOP-LEVEL file under a review root.

Root cause (empirically confirmed): skills/code-review/SKILL.md collected the review
diff with an UNQUOTED `$paths` glob list (`src/** ... tests/**`). Bash pathname-expands
it against the MAIN-TREE CWD *before* git runs (globstar off), so a top-level file that
exists only on the slice branch is absent from the expansion and never reaches git.
New files under an existing SUBDIR (tests/bugs/x.py) survive because the directory
`tests/bugs` is passed as a pathspec covering its subtree.

The fix (ADR-050): deliver the pathspecs to git as a QUOTED bash array
(`paths=('src/**' ...)` expanded `"${paths[@]}"`), so git receives them literally.

These tests extract the LIVE pathspec-collection region from SKILL.md and run it against
a temp git worktree scenario, so the fix flips them green and a future regression flips
them red. AC-mapped:
  AC1 -> test_ac1_toplevel_branch_only_collected
  AC2 -> test_ac2_subdir_new_file_still_collected
  AC3 -> test_ac3_second_populated_root_toplevel_collected  (skills/ POPULATED in main -- m1)
  AC4 -> test_ac4_collection_delivers_pathspecs_as_quoted_array  (static site-guard)
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # $wt (or repo root)
SKILL_MD = REPO_ROOT / "skills" / "code-review" / "SKILL.md"


def _bash():
    """Locate a bash interpreter (git-bash on Windows, /bin/bash on CI)."""
    p = shutil.which("bash")
    if p:
        return p
    for cand in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if Path(cand).exists():
            return cand
    return None


def _first_bash_block(text):
    """Return the (start, end) line indices of the FIRST ```bash ... ``` fenced block
    that contains a `paths=` assignment. N1 (slice-056 TRI-1): bounding extraction to a
    single block makes it robust to a future unrelated `$paths` token elsewhere in the
    file (the earlier global last-match scan was brittle -- slice-021/034 test-coupling
    lesson)."""
    lines = text.splitlines()
    in_block = False
    block_start = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if not in_block and stripped == "```bash":
            in_block = True
            block_start = i + 1
            continue
        if in_block and stripped == "```":
            block = lines[block_start:i]
            if any(re.match(r"\s*paths=", b) for b in block):
                return block_start, i
            in_block = False
            block_start = None
    raise AssertionError("no ```bash block containing a paths= assignment found in SKILL.md")


def _extract_collection_snippet(text):
    """Within the FIRST bash block carrying `paths=`, extract from the `paths=` line
    through the last line referencing `$paths`/`${paths` (the collection's git calls),
    dropping only the `base=` derivation line (we inject `base`). Whatever glob-safety
    form the fix uses (quoted array / set -f) is preserved and exercised."""
    lines = text.splitlines()
    b_start, b_end = _first_bash_block(text)
    start = end = None
    for i in range(b_start, b_end):
        if start is None and re.match(r"\s*paths=", lines[i]):
            start = i
        # End-bound anchored on the git COLLECTION line (git ... -- $paths / "${paths[@]}"),
        # NOT any $paths token, so a trailing paths-mentioning comment inside the block can't
        # be swept into the executed snippet (code-review m1; matches both the fixed quoted-array
        # and the unquoted form so red-on-revert still works).
        if start is not None and re.search(r"git\s+-C.*--\s+\S*\$\{?paths", lines[i]):
            end = i
    assert start is not None and end is not None, "paths= region not found inside the first bash block"
    region = lines[start:end + 1]
    kept = [ln for ln in region if not re.match(r"\s*base=", ln)]
    return "\n".join(kept)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _build_scenario(tmp):
    """main tree at base holds POPULATED roots tests/ AND skills/ (existing top-level
    files) + tests/bugs/; a worktree on a branch ADDS branch-only top-level files under
    BOTH roots plus a subdir file. skills/ is populated so the unquoted form actually
    pre-expands and drops the branch-only skills/ file (m1: an EMPTY root would stay
    literal and false-green)."""
    main = tmp / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", cwd=main)
    _git("config", "user.email", "t@t.t", cwd=main)
    _git("config", "user.name", "t", cwd=main)
    (main / "tests" / "bugs").mkdir(parents=True)
    (main / "skills").mkdir()
    (main / "tests" / "existing_top.py").write_text("# base top-level\n")
    (main / "tests" / "bugs" / "existing_sub.py").write_text("# base sub\n")
    (main / "skills" / "existing_top.md").write_text("# base skills top-level\n")
    _git("add", "-A", cwd=main)
    _git("commit", "-qm", "base", cwd=main)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main,
                          check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    wt = tmp / "wt"
    _git("worktree", "add", "-q", "-b", "feature", str(wt), "main", cwd=main)
    (wt / "tests" / "new_top.py").write_text("# branch-only TOP-LEVEL under tests/\n")
    (wt / "tests" / "bugs" / "new_sub.py").write_text("# branch-only sub\n")
    (wt / "skills" / "new_top.md").write_text("# branch-only TOP-LEVEL under skills/\n")
    _git("add", "-A", cwd=wt)
    _git("commit", "-qm", "feature", cwd=wt)
    return main, wt, base


def _collect(tmp):
    bash = _bash()
    if bash is None:
        pytest.skip("bash unavailable to reproduce the shell-glob behavior")
    snippet = _extract_collection_snippet(SKILL_MD.read_text(encoding="utf-8"))
    main, wt, base = _build_scenario(tmp)
    script = f'wt="{wt.as_posix()}"\nbase="{base}"\n{snippet}\n'
    out = subprocess.run([bash, "-c", script], cwd=str(main),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return out.stdout


def test_ac1_toplevel_branch_only_collected(tmp_path):
    """AC1: a branch-only NEW top-level file under a review root reaches git."""
    out = _collect(tmp_path)
    assert "tests/new_top.py" in out, (
        f"AC1 (SC-087): branch-only top-level tests/new_top.py was DROPPED.\n---collected---\n{out}")


def test_ac2_subdir_new_file_still_collected(tmp_path):
    """AC2: a NEW file under an existing subdir must remain collected (no regression)."""
    out = _collect(tmp_path)
    assert "tests/bugs/new_sub.py" in out, (
        f"AC2: new file under tests/bugs/ was not collected.\n---collected---\n{out}")


def test_ac3_second_populated_root_toplevel_collected(tmp_path):
    """AC3 (root-agnostic): a branch-only top-level file under a SECOND, POPULATED root
    (skills/) is also collected. m1: skills/ is populated in the scenario main tree so
    the unquoted form genuinely pre-expands and drops it -- red-on-bug, not a false-green
    from unmatched-glob passthrough."""
    out = _collect(tmp_path)
    assert "skills/new_top.md" in out, (
        f"AC3 (SC-087): branch-only top-level skills/new_top.md was DROPPED -- fix is not root-agnostic.\n"
        f"---collected---\n{out}")


def test_ac4_collection_delivers_pathspecs_as_quoted_array(tmp_path):
    """AC4 (site-guard / regression guard): the code-review collection region delivers
    the pathspecs to git as a quoted array `"${paths[@]}"`, never as an unquoted `$paths`.
    This is the durable guard for the audit conclusion (code-review:58 was the sole
    affected shell site and is fixed) and against a maintainer reverting the fix."""
    text = SKILL_MD.read_text(encoding="utf-8")
    b_start, b_end = _first_bash_block(text)
    lines = text.splitlines()[b_start:b_end]
    git_lines = [ln for ln in lines if re.search(r'git\s+-C\s+"\$wt"\s+(diff|ls-files)', ln)]
    assert git_lines, "no git diff/ls-files collection lines found in the first bash block"
    for ln in git_lines:
        assert '"${paths[@]}"' in ln, (
            f"AC4: collection line does not deliver pathspecs as a quoted array:\n  {ln.strip()}")
        assert not re.search(r'--\s+\$paths\b', ln), (
            f"AC4: collection line still uses an UNQUOTED $paths (the SC-087 bug):\n  {ln.strip()}")
