"""M4 / M-add-1 -- the vendored guard MUST be import-standalone (stdlib-only) so it runs in an
arbitrary CONSUMER repo with no plugin on sys.path.

This repo's own scripts/lib/ would accidentally RESOLVE a plugin-relative import (e.g. the
house-style `from scripts.lib import _stdout` bootstrap), so a naive in-repo test would go GREEN
while every real consumer repo goes permanently RED (the green-on-dev-repo trap). These tests
therefore (a) AST-assert the guard has NO plugin-relative / non-stdlib imports, and (b) copy the
vendored guard into a fixture repo that has NO scripts/ package and run it as a FRESH subprocess
(clean interpreter, no inherited sys.path) -- the faithful consumer-repo shape.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts.lib import scaffold_reality_gates as srg  # noqa: E402

_GUARD_SRC = _ROOT / "scripts" / "lib" / "security_gate.py"

# The guard may import ONLY these stdlib modules (M4 stdlib-only contract).
_STDLIB_ALLOW = {"__future__", "argparse", "json", "subprocess", "sys", "pathlib"}
_PLUGIN_PKGS = {"scripts", "tools"}

_CLEAN = "def f(x):\n    return x + 1\n"
_HIGH = "import hashlib\ndef d(x):\n    return hashlib.md5(x).hexdigest()\n"


def _top_level_imports(source: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                mods.add("<relative>")            # a relative import is by definition non-standalone
            elif node.module:
                mods.add(node.module.split(".")[0])
    return mods


def test_guard_has_no_plugin_relative_or_nonstdlib_imports():
    mods = _top_level_imports(_GUARD_SRC.read_text(encoding="utf-8"))
    assert not (mods & _PLUGIN_PKGS), f"guard imports a plugin package: {mods & _PLUGIN_PKGS}"
    assert "<relative>" not in mods, "guard uses a relative import -- not standalone"
    offenders = mods - _STDLIB_ALLOW
    assert not offenders, f"guard imports beyond the stdlib allowlist: {offenders}"


def test_vendored_copy_is_byte_identical_to_plugin_source(tmp_path):
    _write = lambda p, t: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text(t, encoding="utf-8"))
    _write(tmp_path / "app.py", _CLEAN)
    srg.scaffold(tmp_path)
    vendored = tmp_path / ".aisdlc" / "gates" / "py_security_gate.py"
    assert vendored.is_file()
    assert vendored.read_text(encoding="utf-8") == _GUARD_SRC.read_text(encoding="utf-8")


def test_vendored_guard_runs_standalone_in_a_non_plugin_repo(tmp_path):
    # A consumer repo: NO scripts/ package anywhere on its tree. Seed via the scaffolder (as /setup
    # would), then run the vendored guard as a fresh subprocess rooted in the consumer -- if the
    # guard had a plugin-relative import it would ImportError here (permanent RED). It must not.
    consumer = tmp_path / "consumer-repo"
    (consumer).mkdir()
    (consumer / "main.py").write_text(_HIGH, encoding="utf-8")   # a HIGH finding to detect
    srg.scaffold(consumer)
    guard = consumer / ".aisdlc" / "gates" / "py_security_gate.py"
    assert guard.is_file()
    assert not (consumer / "scripts").exists()                   # genuinely no plugin package

    proc = subprocess.run(
        [sys.executable, str(guard), "--tool", "bandit", "."],
        cwd=str(consumer), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 1, f"stderr={proc.stderr!r}"       # HIGH -> fail-closed, ran standalone
    assert "FINDING" in proc.stdout

    # clean the finding -> standalone PASS (proves it is not merely erroring uniformly).
    (consumer / "main.py").write_text(_CLEAN, encoding="utf-8")
    proc2 = subprocess.run(
        [sys.executable, str(guard), "--tool", "bandit", "."],
        cwd=str(consumer), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    assert proc2.returncode == 0 and "PASS" in proc2.stdout


def test_re_vendor_on_content_diff_else_byte_identical(tmp_path):
    # M4: the guard file is plugin code, re-vendored when the repo copy differs (security-fix
    # propagation) -- the ONE exception to preserve-existing -- else a byte-identical no-op.
    _write = lambda p, t: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text(t, encoding="utf-8"))
    _write(tmp_path / "app.py", _CLEAN)
    r1 = srg.scaffold(tmp_path)
    assert r1["guard"] == "vendored"
    r2 = srg.scaffold(tmp_path)                                  # unchanged -> no-op
    assert r2["guard"] == "noop"

    # simulate a stale/neutered repo copy -> the scaffolder MUST overwrite it (not preserve).
    vendored = tmp_path / ".aisdlc" / "gates" / "py_security_gate.py"
    vendored.write_text("import sys; sys.exit(0)  # neutered\n", encoding="utf-8")
    r3 = srg.scaffold(tmp_path)
    assert r3["guard"] == "re-vendored"
    assert vendored.read_text(encoding="utf-8") == _GUARD_SRC.read_text(encoding="utf-8")
