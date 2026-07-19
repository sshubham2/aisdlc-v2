"""Detector-level coverage for STUB-DEAD-1 (slice-085 / SC-142 / ADR-099 + ADR-100).

Drives skills/build-slice/scripts/stub_dead_audit.py against synthetic git worktrees, asserting
the exact path:line + rule + exit-code contract. The through-the-gate FOLD (AC4) lives in
test_pre_finish_gate_stub_dead.py.

AC map:
  AC1  a newly-introduced stub / silent broad-except / unreachable is caught at the right path:line
       (incl. `raise NotImplementedError("...")` Call form — M-add-2; a single-line-add stub — M3).
  AC2  diff-scoped: pre-existing stubs the slice never touched do NOT fail; editing a param of a
       pre-existing stub does NOT re-flag it (M2); a deleted/renamed .py is PASS-neutral (B2).
  AC3  a newly-added terminal that orphans pre-existing code is caught via the terminal-union span.
  Plus the fail-closed / carve-out policy rows (M1 base==HEAD, M4 empty-body carve-outs, M-add-1
  legitimately-unparseable, narrow-except idiom, TYPE_CHECKING / abstractmethod panel-of-normals).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DET = REPO_ROOT / "skills" / "build-slice" / "scripts" / "stub_dead_audit.py"


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, f"git {args} failed: {r.stderr or r.stdout}"
    return r.stdout.strip()


def _init_repo(tmp_path, base_files: dict):
    """A git repo on branch uat with `base_files` committed. Returns (work, base_sha)."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "uat")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    _git(work, "config", "commit.gpgsign", "false")
    for rel, content in base_files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    return work, _git(work, "rev-parse", "HEAD")


def _write(work, rel, content):
    p = work / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _run(work, base=None):
    argv = [sys.executable, str(DET), "--worktree", str(work)]
    if base is not None:
        argv += ["--base", base]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


# ── AC1: newly-introduced defects caught at path:line ────────────────────────
STUB_FORMS = {
    "pass":            "def f():\n    pass\n",
    "ellipsis":        "def f():\n    ...\n",
    "raise_name":      "def f():\n    raise NotImplementedError\n",
    "raise_call":      'def f():\n    raise NotImplementedError("TODO: implement")\n',  # M-add-2
    "docstring_only":  'def f():\n    """TODO"""\n',                                     # B1
    "docstring_pass":  'def f():\n    """doc"""\n    pass\n',                            # B1 prefixed
}


@pytest.mark.parametrize("form", sorted(STUB_FORMS))
def test_ac1_stub_forms_caught(tmp_path, form):
    """AC1: every stub body form (incl. the NotImplementedError Call form and docstring variants)
    is caught. Guards M-add-2 (Name-only would miss `raise NotImplementedError("...")`)."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", STUB_FORMS[form])
    r = _run(work, base)
    assert r.returncode == 1, f"[{form}] expected BLOCK; got {r.returncode}\n{r.stdout}"
    assert "[STUB-BODY]" in r.stdout and "new.py:" in r.stdout, r.stdout


def test_m1_docstring_prefixed_stub_reports_operative_line(tmp_path):
    """m1: a docstring-prefixed stub reports the operative `pass` line (3), not the docstring (2) —
    the exact path:line the reader must act on (AC1 'emit the exact path:line')."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", 'def f():\n    """doc"""\n    pass\n')
    r = _run(work, base)
    assert r.returncode == 1 and "new.py:3:" in r.stdout, f"expected the pass line (3): {r.stdout}"


def test_ac1_silent_broad_except_caught(tmp_path):
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "def g():\n    try:\n        risky()\n    except Exception:\n        pass\n")
    r = _run(work, base)
    assert r.returncode == 1 and "[SILENT-EXCEPT]" in r.stdout, r.stdout
    assert "new.py:5:" in r.stdout, f"expected the pass line reported: {r.stdout}"


def test_ac1_bare_except_caught(tmp_path):
    """AC1 recall: a BARE `except:` (not only broad `except Exception:`) is blocked (m2)."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "def g():\n    try:\n        risky()\n    except:\n        pass\n")
    r = _run(work, base)
    assert r.returncode == 1 and "[SILENT-EXCEPT]" in r.stdout, r.stdout


def test_ac1_single_line_added_stub_caught(tmp_path):
    """M3: git omits the hunk count for a single added line (`@@ -n,0 +m @@`); a +c,d-only parser
    would miss it. Add exactly ONE line (a one-line stub via `...`) to a tracked file."""
    work, base = _init_repo(tmp_path, {"m.py": "def f():\n    return 1\n"})
    # append a new one-line-body function as a single added line region
    _write(work, "m.py", "def f():\n    return 1\ndef stub(): ...\n")
    r = _run(work, base)
    assert r.returncode == 1 and "[STUB-BODY]" in r.stdout, r.stdout
    assert "m.py:3:" in r.stdout, r.stdout


# ── AC2: diff-scoping ────────────────────────────────────────────────────────
def test_ac2_preexisting_stub_untouched_passes(tmp_path):
    """A pre-existing stub in a file the slice does not touch is not flagged (nothing added)."""
    work, base = _init_repo(tmp_path, {"old.py": "def stub():\n    pass\n"})
    _write(work, "new.py", "def real():\n    return 42\n")  # the only change; not a stub
    r = _run(work, base)
    assert r.returncode == 0, f"expected PASS; got {r.returncode}\n{r.stdout}"


def test_ac2_edit_param_of_preexisting_stub_no_trip(tmp_path):
    """M2: adding a parameter to a pre-existing stub touches the def line but NOT the body-stmt.
    STUB-BODY must trip only when the def / decorator / body line is added — a signature edit that
    leaves the `pass` untouched must NOT re-flag a stub the slice did not introduce."""
    work, base = _init_repo(tmp_path, {"m.py": "def stub(a):\n    pass\n\n\ndef anchor():\n    return 1\n"})
    # change ONLY the anchor's body far from the stub; the stub's def+body lines are unchanged.
    _write(work, "m.py", "def stub(a):\n    pass\n\n\ndef anchor():\n    return 2\n")
    r = _run(work, base)
    assert r.returncode == 0, f"a signature/other edit must not re-flag the untouched stub\n{r.stdout}"


def test_ac2_deleted_py_is_pass_neutral(tmp_path):
    """B2: a slice that DELETES a .py must PASS, not INFRA-block (--diff-filter=d + skip missing)."""
    work, base = _init_repo(tmp_path, {"gone.py": "def stub():\n    pass\n", "keep.py": "# keep\n"})
    (work / "gone.py").unlink()
    r = _run(work, base)
    assert r.returncode == 0, f"deleting a .py must be PASS-neutral, not INFRA\n{r.stdout}\n{r.stderr}"
    assert "INFRA" not in r.stdout, r.stdout


def test_ac2_renamed_py_does_not_infra(tmp_path):
    """B2: renaming a .py (old path gone) must not INFRA-block on the vanished old path."""
    work, base = _init_repo(tmp_path, {"a.py": "def real():\n    return 1\n"})
    _git(work, "mv", "a.py", "b.py")
    r = _run(work, base)
    assert r.returncode == 0, f"rename must not INFRA\n{r.stdout}\n{r.stderr}"
    assert "INFRA" not in r.stdout, r.stdout


def test_ac2_narrow_except_not_flagged(tmp_path):
    """The repo's deliberate narrow-typed best-effort idiom must not cry wolf (design resolution)."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py",
           "def g():\n    try:\n        risky()\n    except (ValueError, KeyError):\n        pass\n")
    r = _run(work, base)
    assert r.returncode == 0, f"narrow-typed except:pass must not be blocked\n{r.stdout}"


@pytest.mark.parametrize("decorated", [
    "from abc import abstractmethod\nclass C:\n    @abstractmethod\n    def m(self): ...\n",
    "from typing import overload\n@overload\ndef f(x: int) -> int: ...\n",
    "from typing import Protocol\nclass P(Protocol):\n    def contract(self) -> int: ...\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    def only(): pass\n",
])
def test_ac2_panel_of_normals_carveouts_pass(tmp_path, decorated):
    """Panel-of-normals: a newly-added abstractmethod / overload / Protocol member / TYPE_CHECKING
    def is NOT a stub (M4 carve-out set)."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", decorated)
    r = _run(work, base)
    assert r.returncode == 0, f"carve-out wrongly flagged:\n{decorated}\n{r.stdout}"


def test_ac2_empty_init_and_lifecycle_pass(tmp_path):
    """M4: an empty __init__ and named lifecycle hooks are intentional no-ops, not stubs."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py",
           "class C:\n    def __init__(self):\n        pass\n"
           "    def __enter__(self):\n        pass\n"
           "    def tearDown(self):\n        pass\n")
    r = _run(work, base)
    assert r.returncode == 0, f"empty __init__/lifecycle must pass\n{r.stdout}"


def test_ac2_suppression_token_passes(tmp_path):
    """M4: any other intentional empty concrete body passes with an inline `# stub-dead:allow`."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "def handler(evt):\n    pass  # stub-dead:allow intentional no-op\n")
    r = _run(work, base)
    assert r.returncode == 0, f"token-suppressed body must pass\n{r.stdout}"


# ── AC3: newly-added terminal orphaning pre-existing code ─────────────────────
def test_ac3_added_terminal_orphans_preexisting_unreachable(tmp_path):
    """AC3 / causal-attribution: an ADDED `return` above pre-existing statements orphans them.
    The orphaned lines are not themselves added, so a per-line scope would drop them; the
    terminal-line UNION unreachable-span catches them (design-spike TARGET 3)."""
    work, base = _init_repo(tmp_path, {"m.py": "def f():\n    print('a')\n    print('b')\n"})
    # insert `return` as line 2 (ADDED); the pre-existing prints (now 3,4) become unreachable.
    _write(work, "m.py", "def f():\n    return\n    print('a')\n    print('b')\n")
    r = _run(work, base)
    assert r.returncode == 1 and "[UNREACHABLE]" in r.stdout, r.stdout
    assert "m.py:3:" in r.stdout, f"expected the first orphaned line reported: {r.stdout}"


def test_ac3_while_true_break_no_false_positive(tmp_path):
    """m2 FP-guard: `while True: break` (break is last in its suite) is not unreachable, and the
    cleanup statement AFTER the loop is reachable — neither must be flagged."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py",
           "def loop():\n    while True:\n        break\n    cleanup()\n")
    r = _run(work, base)
    assert r.returncode == 0, f"while-True/break + post-loop cleanup must pass\n{r.stdout}"


# ── fail-closed / policy rows ────────────────────────────────────────────────
def test_bogus_worktree_is_usage_error(tmp_path):
    """A non-git directory is a usage error (exit 2), never a silent audit of the main repo."""
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run(plain, base=None)
    assert r.returncode == 2, f"expected usage exit 2; got {r.returncode}\n{r.stderr}"


def test_m1_real_worktree_unresolvable_base_does_not_infra(tmp_path):
    """M1: a REAL worktree whose integration base is unresolvable (no remote) yields base=='HEAD'
    from slice_diff_base — the twins degrade to a diff-vs-HEAD there, so STUB-DEAD-1 must too, NOT
    INFRA-block. Self-resolve (no --base) so the HEAD fallback is exercised end-to-end."""
    work, _ = _init_repo(tmp_path, {"base.py": "# base\n"})  # no remote, no aisdlc-uat ahead
    _write(work, "new.py", "def real():\n    return 1\n")     # a clean uncommitted change
    r = _run(work, base=None)
    assert r.returncode == 0, f"unresolvable-base real worktree must not INFRA\n{r.stdout}\n{r.stderr}"
    assert "INFRA" not in r.stdout, r.stdout


def test_m_add_1_unparseable_under_fixtures_path_passes(tmp_path):
    """M-add-1: a deliberately-broken .py under a fixtures/ path PASSES (not INFRA)."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "tests/fixtures/broken.py", "def broken(:\n    this is not python\n")
    r = _run(work, base)
    assert r.returncode == 0, f"fixtures/ unparseable must pass\n{r.stdout}\n{r.stderr}"


def test_m_add_1_unparseable_with_token_passes(tmp_path):
    """M-add-1: a deliberately-broken .py carrying the token PASSES."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "# stub-dead:allow-unparseable — intentional malformed fixture\ndef x(:\n")
    r = _run(work, base)
    assert r.returncode == 0, f"token-carrying unparseable must pass\n{r.stdout}\n{r.stderr}"


def test_m_add_1_unparseable_real_file_infra_blocks_with_version(tmp_path):
    """Fail-closed: a genuinely-unparseable added .py (no carve-out) BLOCKS with an INFRA banner
    that names the running interpreter version so a version-skew block is diagnosable."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "def x(:\n    broken\n")
    r = _run(work, base)
    assert r.returncode == 1 and "[STUB-DEAD-1] INFRA:" in r.stdout, r.stdout
    assert "python" in r.stdout.lower(), f"INFRA banner must surface the interpreter version: {r.stdout}"


def test_no_py_changed_is_a_clean_pass(tmp_path):
    """A docs-only slice (no .py changed) is a legitimate PASS, distinct from an infra failure."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "README.md", "# docs only\n")
    r = _run(work, base)
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout


def test_banner_is_first_stdout_line_on_finding(tmp_path):
    """M5: the [STUB-DEAD-1] banner is the FIRST stdout line so it survives pre_finish_gate's
    lines[:6] summary truncation."""
    work, base = _init_repo(tmp_path, {"base.py": "# base\n"})
    _write(work, "new.py", "def s():\n    pass\n")
    r = _run(work, base)
    first = r.stdout.splitlines()[0]
    assert first.startswith("[STUB-DEAD-1] FINDING:"), f"banner must be line 1: {r.stdout!r}"
