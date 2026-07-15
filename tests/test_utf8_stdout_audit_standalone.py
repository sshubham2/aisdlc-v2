"""UTF8-STDOUT-1 must enforce the PROPERTY, not the SPELLING — and must keep its teeth.

THE BUG (CI red on `aisdlc-uat`, 2026-07): the rule demanded that `main()`'s first executable
statement be `_stdout.reconfigure_stdout_utf8()` AND that the module carry
`from scripts.lib import _stdout`. But `scripts/lib/security_gate.py` is standalone-and-stdlib-only
BY CONTRACT (slice-067 / ADR-065): it is vendored verbatim into a consumer repo as
`.aisdlc/gates/py_security_gate.py` and must run there with NO plugin on sys.path, so it may not
import `scripts.lib` at all — and `tests/test_security_gate_standalone_import.py` AST-asserts exactly
that.

So two audits in the same repo required OPPOSITE things. security_gate could not satisfy both, and CI
sat red on a file that was, at runtime, perfectly correct: its inlined `_reconfigure_stdout()` does the
same work as the canonical helper. The audit was wrong, not the code.

The fix enforces the property (main() reconfigures stdout to UTF-8 before writing anything) and accepts
a module-local helper for a file that cannot import the shared one. The danger of loosening a rule is
that it stops catching anything — so these tests pin BOTH arms: a local helper that really reconfigures
PASSES, and a local helper that does not (a stub, a misnamed no-op) still FAILS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "skills" / "build-slice" / "scripts"))

import utf8_stdout_audit as audit  # noqa: E402

CANONICAL = '''\
from scripts.lib import _stdout

def main(argv=None) -> int:
    _stdout.reconfigure_stdout_utf8()
    return 0
'''

STANDALONE_REAL = '''\
import sys

def _reconfigure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

def main(argv=None) -> int:
    _reconfigure_stdout()
    return 0
'''

STANDALONE_STUB = '''\
import sys

def _reconfigure_stdout() -> None:
    pass                      # named like the real thing; does NOTHING

def main(argv=None) -> int:
    _reconfigure_stdout()
    return 0
'''

MISSING = '''\
import sys

def main(argv=None) -> int:
    print("hello")            # writes before reconfiguring — the cp1252 crash class
    return 0
'''


def _audit(tmp_path: Path, source: str):
    lib = tmp_path / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "tool.py").write_text(source, encoding="utf-8")
    return audit.audit_root(tmp_path)


def test_canonical_form_passes(tmp_path):
    assert _audit(tmp_path, CANONICAL).violations == []


def test_standalone_local_helper_passes(tmp_path):
    """The carve-out: a vendored, stdlib-only guard that CANNOT import scripts.lib."""
    assert _audit(tmp_path, STANDALONE_REAL).violations == [], (
        "a module-local helper that genuinely reconfigures stdout must satisfy the rule — demanding "
        "the scripts.lib import here directly contradicts the standalone-import contract"
    )


def test_a_local_helper_that_does_NOT_reconfigure_still_fails(tmp_path):
    """THE TEETH. Loosening a rule is only safe if it still catches the thing it exists for: a helper
    NAMED like the real one but doing nothing must not buy a pass."""
    v = _audit(tmp_path, STANDALONE_STUB).violations
    assert len(v) == 1, "a no-op stub named _reconfigure_stdout bought a free pass"
    assert "does not reconfigure stdout" in v[0].message


def test_no_reconfigure_at_all_still_fails(tmp_path):
    v = _audit(tmp_path, MISSING).violations
    assert len(v) == 1
    assert v[0].function == "main"


def test_the_real_repo_is_clean():
    """The whole point: the audit is green on the real tree, including the standalone guard."""
    r = subprocess.run(
        [sys.executable, str(_ROOT / "skills" / "build-slice" / "scripts" / "utf8_stdout_audit.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"UTF8-STDOUT-1 is red on the tree:\n{r.stdout}{r.stderr}"


def test_security_gate_still_imports_no_plugin_module():
    """Non-regression on the OTHER side of the contradiction: the fix must not have tempted anyone to
    'solve' this by importing scripts.lib into the vendored guard, which would break every consumer
    repo (the failure test_security_gate_standalone_import.py exists to prevent)."""
    src = (_ROOT / "scripts" / "lib" / "security_gate.py").read_text(encoding="utf-8")
    assert "from scripts.lib import" not in src
    assert "import scripts.lib" not in src
