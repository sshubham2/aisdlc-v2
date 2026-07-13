"""UTF8-STDOUT-1 structural audit (v2).

Validates every v2 Python tool module exposing a top-level ``main()`` calls
``_stdout.reconfigure_stdout_utf8()`` as the first executable statement.
Closes the recurring Windows cp1252 console encoding class.

Per UTF8-STDOUT-1 (methodology-changelog.md v0.37.0). The canonical
invocation pattern (v2) is::

    from scripts.lib import _stdout

    def main(argv: list[str] | None = None) -> int:
        _stdout.reconfigure_stdout_utf8()
        # ... rest of main() body ...

**v2 changes from v1.**
- v1 scanned ``tools/*.py`` (the single flat v1 tool dir). v2 scans the two v2
  Python tool locations: ``scripts/lib/*.py`` (SHARED tools) AND
  ``skills/*/scripts/*.py`` (single-skill tools).
- The canonical import is now ``from scripts.lib import _stdout`` (the v1
  ``from tools import _stdout`` is gone). Either the qualified
  ``_stdout.reconfigure_stdout_utf8()`` call or the bare from-import
  ``reconfigure_stdout_utf8()`` call is accepted.
- Exclusion list: ``__init__.py``, ``_stdout.py`` itself, AND the
  leading-underscore helper modules (``_vault_paths.py``, ``_vault_write.py``,
  ``_pyfn.py``, ``_worktree_paths.py``, ``_git_default_branch.py``, …) — they
  are helpers / leaves with no ``main()``. (Generalised: any leading-underscore
  filename is a helper and is skipped, matching v1's ``_is_helper_module``.)
- Files with no top-level ``main()`` are not subject to the rule (skipped).

**Standalone-vendored carve-out (2026-07).** The rule as originally written was
self-contradictory for one real file. ``scripts/lib/security_gate.py`` is
standalone-and-stdlib-only BY CONTRACT (slice-067 / ADR-065) — it is vendored
verbatim into a consumer repo as ``.aisdlc/gates/py_security_gate.py`` and must
run with NO plugin on ``sys.path``, so it may not import ``scripts.lib`` at all,
and ``tests/test_security_gate_standalone_import.py`` AST-asserts precisely that.
UTF8-STDOUT-1 meanwhile DEMANDED ``from scripts.lib import _stdout``. Two audits in
one repo requiring opposite things: the file could not satisfy both, and CI sat red
on code that was in fact correct.

So the audit now enforces the **property** (``main()`` reconfigures stdout to UTF-8
before writing anything) rather than the **spelling** (one blessed import + call).
Accepted forms:

    _stdout.reconfigure_stdout_utf8()   # canonical — still requires the canonical import
    reconfigure_stdout_utf8()           # from-import form
    _reconfigure_stdout()               # a MODULE-LOCAL helper, iff its body really calls
                                        # <stream>.reconfigure(encoding=...)

The teeth are intact: a local helper that does not actually reconfigure (a stub, a
misnamed no-op) is still a violation.

Detection mechanism: AST-based. Parse each candidate file; find the top-level
``def main(...)``; identify the first executable statement after the docstring;
verify it reconfigures stdout by one of the accepted forms above.

Usage:
    python utf8_stdout_audit.py
    python utf8_stdout_audit.py --json
    python utf8_stdout_audit.py --root <repo-root>

Exit codes:
    0 = clean (every audited tool conforms)
    1 = violation (>=1 tool non-conforming)
    2 = usage error (root missing, no tool dirs, etc.)
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from dataclasses import dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402

_EXCLUDED_FILENAMES: frozenset[str] = frozenset({"__init__.py"})
_CANONICAL_FUNCTION_NAME: str = "reconfigure_stdout_utf8"
_CANONICAL_HELPER_NAME: str = "_stdout"
# The v2 canonical import origin (was `tools` in v1).
_CANONICAL_IMPORT_MODULE: str = "scripts.lib"


@dataclass(frozen=True)
class Violation:
    file: str  # repo-relative path
    function: str
    line: int
    message: str

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "function": self.function,
            "line": self.line,
            "message": self.message,
        }


@dataclass
class AuditResult:
    tools_scanned: int = 0
    tools_with_main: int = 0
    tools_clean: int = 0
    violations: list[Violation] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "clean" if not self.violations else "violation"

    def to_dict(self) -> dict:
        return {
            "tools_scanned": self.tools_scanned,
            "tools_with_main": self.tools_with_main,
            "tools_clean": self.tools_clean,
            "violations": [v.to_dict() for v in self.violations],
            "status": self.status,
        }


def _is_helper_module(name: str) -> bool:
    """Helper modules (leading underscore, no main()) are excluded from scan.

    Covers ``_stdout.py``, ``_vault_paths.py``, ``_vault_write.py``,
    ``_pyfn.py``, ``_worktree_paths.py``, ``_git_default_branch.py``, and any
    future ``_*.py`` leaf — same generalisation v1 used.
    """
    return name.startswith("_")


def _candidate_tools(root: Path) -> list[Path]:
    """Return every v2 Python tool file eligible for audit.

    v2 tool locations: ``scripts/lib/*.py`` (shared) + ``skills/*/scripts/*.py``
    (single-skill). Excludes ``__init__.py`` and any leading-underscore helper.
    Returned sorted by repo-relative path for stable output.
    """
    candidates: list[Path] = []

    lib_dir = root / "scripts" / "lib"
    if lib_dir.is_dir():
        candidates.extend(lib_dir.glob("*.py"))

    skills_dir = root / "skills"
    if skills_dir.is_dir():
        candidates.extend(skills_dir.glob("*/scripts/*.py"))

    eligible = [
        p
        for p in candidates
        if p.name not in _EXCLUDED_FILENAMES and not _is_helper_module(p.name)
    ]
    return sorted(eligible, key=lambda p: p.relative_to(root).as_posix())


def _find_main_function(tree: ast.Module) -> ast.FunctionDef | None:
    """Find top-level ``def main(...)`` in the module's AST."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "main":
                return node  # type: ignore[return-value]
    return None


def _first_executable_statement(func: ast.FunctionDef) -> ast.stmt | None:
    """Return the first executable statement in func.body, skipping docstring."""
    body = func.body
    if not body:
        return None
    first = body[0]
    # Skip docstring: a bare Expr whose value is a Constant string.
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1] if len(body) > 1 else None
    return first


def _is_canonical_reconfigure_call(stmt: ast.stmt) -> bool:
    """Check if stmt is ``_stdout.reconfigure_stdout_utf8()`` or equivalent.

    Accepts:
      - _stdout.reconfigure_stdout_utf8()        (canonical pinned form)
      - reconfigure_stdout_utf8()                (from-import form)
    """
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Attribute):
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == _CANONICAL_HELPER_NAME
            and func.attr == _CANONICAL_FUNCTION_NAME
        ):
            return True
    if isinstance(func, ast.Name):
        if func.id == _CANONICAL_FUNCTION_NAME:
            return True
    return False


def _called_local_name(stmt: ast.stmt) -> str | None:
    """The bare function name if ``stmt`` is a plain ``some_name()`` call, else None."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    func = stmt.value.func
    return func.id if isinstance(func, ast.Name) else None


def _module_level_reconfigurer(tree: ast.Module, name: str) -> bool:
    """True iff ``name`` is a top-level function IN THIS MODULE that really reconfigures a
    standard stream to UTF-8 (i.e. it calls ``<stream>.reconfigure(encoding=...)``).

    This is the STANDALONE-VENDORED carve-out, and it exists because the rule as originally
    written was self-contradictory. ``scripts/lib/security_gate.py`` is standalone-and-stdlib-only
    BY CONTRACT (slice-067 / ADR-065): it is vendored verbatim into a consumer repo as
    ``.aisdlc/gates/py_security_gate.py`` and must run there with NO plugin on sys.path, so it may
    not import ``scripts.lib`` at all — and ``tests/test_security_gate_standalone_import.py``
    AST-asserts exactly that. UTF8-STDOUT-1 simultaneously DEMANDED ``from scripts.lib import
    _stdout``. Two audits in one repo requiring opposite things: the file could not satisfy both,
    so CI sat red on a file that was, in fact, correct.

    The resolution is to enforce the PROPERTY (main() reconfigures stdout to UTF-8 before it writes
    anything) rather than the SPELLING (one specific import + call). A module-local helper is
    accepted only when its body genuinely performs the reconfigure — a stub named
    ``_reconfigure_stdout`` that does nothing still FAILS, so the audit keeps its teeth.
    """
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != name:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            # `stream.reconfigure(encoding=...)` — the direct form; or a bare `reconfigure(encoding=...)`
            # where the bound method was fetched first (`reconfigure = getattr(stream, "reconfigure", None)`),
            # which is exactly the shape BOTH the canonical _stdout helper and the vendored guard use.
            f = sub.func
            named_reconfigure = (
                (isinstance(f, ast.Attribute) and f.attr == "reconfigure")
                or (isinstance(f, ast.Name) and f.id == "reconfigure")
            )
            if named_reconfigure and any(kw.arg == "encoding" for kw in sub.keywords):
                return True
    return False


def _has_canonical_import(tree: ast.Module) -> bool:
    """Check the module imports ``_stdout`` from ``scripts.lib`` (v2 canonical).

    Accepts either::

        from scripts.lib import _stdout           # qualified-call form
        from scripts.lib._stdout import reconfigure_stdout_utf8  # bare form
    """
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        # `from scripts.lib import _stdout`
        if module == _CANONICAL_IMPORT_MODULE:
            for alias in node.names:
                if alias.name == _CANONICAL_HELPER_NAME:
                    return True
        # `from scripts.lib._stdout import reconfigure_stdout_utf8`
        if module == f"{_CANONICAL_IMPORT_MODULE}.{_CANONICAL_HELPER_NAME}":
            for alias in node.names:
                if alias.name == _CANONICAL_FUNCTION_NAME:
                    return True
    return False


def audit_root(root: Path) -> AuditResult:
    """Audit every v2 tool module under root; return AuditResult."""
    result = AuditResult()
    for path in _candidate_tools(root):
        result.tools_scanned += 1
        rel_path = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, ValueError) as e:
            result.violations.append(
                Violation(
                    file=rel_path,
                    function="<module>",
                    line=getattr(e, "lineno", 0) or 0,
                    message=f"SyntaxError parsing module: {getattr(e, 'msg', e)}",
                )
            )
            continue
        except (OSError, UnicodeDecodeError) as e:
            result.violations.append(
                Violation(
                    file=rel_path,
                    function="<module>",
                    line=0,
                    message=f"unreadable module: {e}",
                )
            )
            continue

        main_func = _find_main_function(tree)
        if main_func is None:
            # Tool has no main() — not subject to UTF8-STDOUT-1.
            continue

        result.tools_with_main += 1
        first_stmt = _first_executable_statement(main_func)

        if first_stmt is None:
            result.violations.append(
                Violation(
                    file=rel_path,
                    function="main",
                    line=main_func.lineno,
                    message="main() body is empty or docstring-only",
                )
            )
            continue

        canonical = _is_canonical_reconfigure_call(first_stmt)

        # The standalone-vendored carve-out: a module-local helper that REALLY reconfigures a stream
        # to UTF-8. Required for a file that may not import scripts.lib at all (see
        # _module_level_reconfigurer). A local call whose target does NOT reconfigure still fails.
        local_name = None if canonical else _called_local_name(first_stmt)
        standalone = bool(local_name) and _module_level_reconfigurer(tree, local_name)

        if not canonical and not standalone:
            try:
                got = ast.unparse(first_stmt)
            except Exception:
                got = f"<line {first_stmt.lineno}>"
            result.violations.append(
                Violation(
                    file=rel_path,
                    function="main",
                    line=first_stmt.lineno,
                    message=(
                        f"first executable statement does not reconfigure stdout to UTF-8 "
                        f"— expected _stdout.reconfigure_stdout_utf8(), or (for a "
                        f"standalone-vendored tool that cannot import scripts.lib) a "
                        f"module-local helper that calls <stream>.reconfigure(encoding=...) "
                        f"(got: {got})"
                    ),
                )
            )
            continue

        # The canonical form must carry the canonical import. The standalone form must NOT need it —
        # requiring it there is what made this audit contradict the standalone-import contract.
        if canonical and not _has_canonical_import(tree):
            result.violations.append(
                Violation(
                    file=rel_path,
                    function="<module>",
                    line=1,
                    message=(
                        "missing canonical import 'from scripts.lib import "
                        "_stdout' (per UTF8-STDOUT-1)"
                    ),
                )
            )
            continue

        result.tools_clean += 1

    return result


def _format_human(result: AuditResult) -> str:
    if not result.violations:
        return (
            f"UTF8-STDOUT-1 audit: clean. "
            f"{result.tools_scanned} tool(s) scanned; "
            f"{result.tools_with_main} with main(); "
            f"{result.tools_clean} clean.\n"
        )

    out: list[str] = [
        f"UTF8-STDOUT-1 audit: {len(result.violations)} violation(s) "
        f"({result.tools_with_main - result.tools_clean} of "
        f"{result.tools_with_main} tool(s) with main() non-conforming):\n\n"
    ]
    for v in result.violations:
        out.append(f"  {v.file}:{v.line} {v.function}: {v.message}\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="utf8_stdout_audit",
        description=(
            "UTF8-STDOUT-1 structural audit: every v2 Python tool "
            "(scripts/lib/*.py + skills/*/scripts/*.py) with main() must "
            "call _stdout.reconfigure_stdout_utf8() as first executable "
            "statement."
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Repo root (defaults to the plugin root inferred from this script)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args(argv)

    if args.root is None:
        root = _REPO
    else:
        root = args.root.resolve()

    lib_dir = root / "scripts" / "lib"
    skills_dir = root / "skills"
    if not lib_dir.is_dir() and not skills_dir.is_dir():
        sys.stderr.write(
            f"no v2 tool directories found at {root} "
            f"(expected scripts/lib/ and/or skills/*/scripts/)\n"
        )
        return 2

    result = audit_root(root)

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
