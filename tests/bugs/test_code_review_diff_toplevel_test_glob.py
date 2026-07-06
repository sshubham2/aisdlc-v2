"""Standing guard for /code-review's review-diff collection (slice-056 / SC-087,
superseded by the 2026-07 review sweep's no-pathspec fix).

History:
- SC-087 (slice-056): the collection used an UNQUOTED `$paths` glob list that bash
  pathname-expanded against the MAIN-tree cwd, silently dropping branch-only
  top-level files. Fixed with a quoted pathspec array (ADR-050).
- Review sweep 2026-07: the quoted array itself was the deeper bug — its five
  entries ('src/**' 'skills/**' 'agents/**' 'scripts/**' 'tests/**') are THIS
  PLUGIN'S OWN layout, so any host project shaped differently (app/, lib/,
  packages/, ...) produced an EMPTY diff -> a confident false NO-CODE-CHANGES
  review. The fix removes the pathspec entirely: the worktree diff vs the
  fork-point base IS the slice's change (.gitignore already scopes noise; the
  vault is external). No pathspec also retires the SC-087 expansion trap.

These tests extract the LIVE collection region from SKILL.md and run it against a
temp git worktree scenario:
  AC1 -> branch-only top-level file under tests/ collected        (SC-087 guard)
  AC2 -> new file under an existing subdir collected              (no regression)
  AC3 -> branch-only top-level under a second populated root      (root-agnostic)
  AC4 -> the collection delivers NO pathspec to git               (site-guard)
  AC5 -> a change under a NON-plugin-shaped root (app/) collected (false-green guard)
  AC6 -> a >1200-line diff emits the DIFF-TRUNCATED marker        (partial-input signal)
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # $wt (or repo root)
SKILL_MD = REPO_ROOT / "skills" / "code-review" / "SKILL.md"


def _bash():
    """Locate a WORKING bash (git-bash on Windows, /bin/bash on CI). Candidates are
    validated by actually running them: on Windows, `shutil.which('bash')` often finds
    the System32 WSL stub, which errors out when WSL is not installed — fall through
    to the next candidate instead of failing every test environmentally."""
    candidates = []
    p = shutil.which("bash")
    if p:
        candidates.append(p)
    candidates += [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ]
    for cand in candidates:
        if not Path(cand).exists():
            continue
        try:
            ok = subprocess.run([cand, "-c", "echo ok"], capture_output=True,
                                text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if ok.returncode == 0 and "ok" in (ok.stdout or ""):
            return cand
    return None


def _collection_block(text):
    """Return the lines of the FIRST ```bash block containing a `git -C "$wt" diff`
    collection line (the review-diff block)."""
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
            if any(re.search(r'git\s+-C\s+"\$wt"\s+diff', b) for b in block):
                return block
            in_block = False
            block_start = None
    raise AssertionError("no ```bash block containing the review-diff collection found in SKILL.md")


def _extract_collection_snippet(text):
    """From the collection block, extract the executable collection region: the first
    `git -C "$wt" diff` line through the last collection line (the ls-files untracked
    listing), skipping comments. `wt`/`base` are injected by the test."""
    block = _collection_block(text)
    start = end = None
    for i, ln in enumerate(block):
        if start is None and re.search(r'git\s+-C\s+"\$wt"\s+diff', ln):
            start = i
        if re.search(r'git\s+-C\s+"\$wt"\s+ls-files', ln):
            end = i
    assert start is not None and end is not None, "collection region not found in the diff block"
    return "\n".join(block[start:end + 1])


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _build_scenario(tmp, big_diff=False):
    """main tree at base holds populated roots tests/, skills/ AND a NON-plugin root
    app/ (the host-project shape the old hardcoded pathspec was blind to); a worktree
    on a branch adds branch-only files under each. big_diff additionally commits a
    >1200-line change to exercise the truncation marker."""
    main = tmp / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", cwd=main)
    _git("config", "user.email", "t@t.t", cwd=main)
    _git("config", "user.name", "t", cwd=main)
    (main / "tests" / "bugs").mkdir(parents=True)
    (main / "skills").mkdir()
    (main / "app").mkdir()
    (main / "tests" / "existing_top.py").write_text("# base top-level\n")
    (main / "tests" / "bugs" / "existing_sub.py").write_text("# base sub\n")
    (main / "skills" / "existing_top.md").write_text("# base skills top-level\n")
    (main / "app" / "views.py").write_text("VIEW = 1\n")
    _git("add", "-A", cwd=main)
    _git("commit", "-qm", "base", cwd=main)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main,
                          check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    wt = tmp / "wt"
    _git("worktree", "add", "-q", "-b", "feature", str(wt), "main", cwd=main)
    (wt / "tests" / "new_top.py").write_text("# branch-only TOP-LEVEL under tests/\n")
    (wt / "tests" / "bugs" / "new_sub.py").write_text("# branch-only sub\n")
    (wt / "skills" / "new_top.md").write_text("# branch-only TOP-LEVEL under skills/\n")
    (wt / "app" / "views.py").write_text("VIEW = 2  # host-project-shaped change\n")
    if big_diff:
        (wt / "app" / "big.py").write_text(
            "\n".join(f"x{i} = {i}" for i in range(1500)) + "\n")
    _git("add", "-A", cwd=wt)
    _git("commit", "-qm", "feature", cwd=wt)
    return main, wt, base


def _collect(tmp, big_diff=False):
    bash = _bash()
    if bash is None:
        pytest.skip("no working bash available to exercise the collection block")
    snippet = _extract_collection_snippet(SKILL_MD.read_text(encoding="utf-8"))
    main, wt, base = _build_scenario(tmp, big_diff=big_diff)
    script = f'wt="{wt.as_posix()}"\nbase="{base}"\n{snippet}\n'
    out = subprocess.run([bash, "-c", script], cwd=str(main),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return out.stdout


def test_ac1_toplevel_branch_only_collected(tmp_path):
    """AC1 (SC-087 guard): a branch-only NEW top-level file reaches git."""
    out = _collect(tmp_path)
    assert "tests/new_top.py" in out, (
        f"AC1 (SC-087): branch-only top-level tests/new_top.py was DROPPED.\n---collected---\n{out}")


def test_ac2_subdir_new_file_still_collected(tmp_path):
    """AC2: a NEW file under an existing subdir must remain collected (no regression)."""
    out = _collect(tmp_path)
    assert "tests/bugs/new_sub.py" in out, (
        f"AC2: new file under tests/bugs/ was not collected.\n---collected---\n{out}")


def test_ac3_second_populated_root_toplevel_collected(tmp_path):
    """AC3 (root-agnostic): a branch-only top-level file under a SECOND populated root."""
    out = _collect(tmp_path)
    assert "skills/new_top.md" in out, (
        f"AC3: branch-only top-level skills/new_top.md was DROPPED.\n---collected---\n{out}")


def test_ac4_collection_has_no_pathspec(tmp_path):
    """AC4 (site-guard): the collection lines pass NO pathspec to git — a hardcoded
    directory list is the false-NO-CODE-CHANGES bug on differently-shaped host projects,
    and this guard trips if a maintainer re-scopes the diff."""
    block = _collection_block(SKILL_MD.read_text(encoding="utf-8"))
    assert not any(re.match(r"\s*paths=", ln) for ln in block), (
        "AC4: the collection block reintroduced a `paths=` pathspec list — the reviewer's "
        "field of view must be the whole worktree diff, never a hardcoded layout.")
    coll = [ln for ln in block if re.search(r'git\s+-C\s+"\$wt"\s+(diff|ls-files)', ln)]
    assert coll, "no git diff/ls-files collection lines found in the block"
    for ln in coll:
        assert not re.search(r"\s--\s", ln.split("#")[0]), (
            f"AC4: collection line scopes git with a pathspec (` -- `):\n  {ln.strip()}")


def test_ac5_nonplugin_root_change_collected(tmp_path):
    """AC5 (the false-green guard): a change under a NON-plugin-shaped root (app/ — a
    host-project layout the old hardcoded pathspec was blind to) IS collected, so the
    reviewer can never write a confident NO-CODE-CHANGES over it."""
    out = _collect(tmp_path)
    assert "app/views.py" in out, (
        f"AC5: the app/ change was invisible to the collection — the false "
        f"NO-CODE-CHANGES bug is back.\n---collected---\n{out}")


def test_ac6_truncation_marker_emitted(tmp_path):
    """AC6: a >1200-line diff emits the DIFF-TRUNCATED marker so the reviewer must
    reassemble per-file diffs and record diff_truncated in the artifact — a silently
    partial input must never read as full coverage."""
    out = _collect(tmp_path, big_diff=True)
    assert "DIFF-TRUNCATED" in out, (
        f"AC6: no DIFF-TRUNCATED marker on a large diff.\n---tail---\n{out[-800:]}")
