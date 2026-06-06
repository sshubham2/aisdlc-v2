"""Shared test-function resolution helper for PTFFD-1 (slice-037).

PTFFD-1 ("Phantom-Test-Function-citation Discipline") extends FILE-level
PTFCD-1 (slice-025) to the test-FUNCTION level: a PASSING TF-1 plan row or a
shippability `Machine-cmd` pytest selector may not cite a test *function* that
does not exist in an otherwise-present test file.

This module is the single source of truth for the three primitives both
enforcing audits (`tools/test_first_audit.py`, `tools/shippability_path_audit.py`)
need:

- `is_checkable_function_name(s)` — the B2 discriminator. Returns True ONLY for
  a strict Python-identifier shape (optionally with a trailing `[param-id]`),
  and never for an `_EMPTY_SENTINELS` value. Prose like
  `(full existing module — non-regression)` (a real archived slice-034 TF-1
  row) is NOT checkable → the caller degrades to FILE-level-only rather than
  false-positively flagging it (AC3 zero-false-positive linchpin).
- `selector_terminal_name(s)` — for a pytest selector `path::A::b` or a bare
  `name[param]`, return the terminal callable name with any `[param-id]`
  stripped (`b`, `name`). Out-of-scope: per-parameter resolution.
- `function_defined_in_file(py_file, func_name)` — TRI-STATE. `True` if the
  file parses and a `FunctionDef`/`AsyncFunctionDef` named `func_name` exists at
  ANY nesting depth (module-level, class method, nested). `False` if it parses
  and no such def exists (→ the caller emits a phantom-function violation).
  `None` if the file cannot be parsed / is not Python / is unreadable — the
  caller skips the function check WITH a visible skip-note and emits NO
  violation (ADR-037 skip-with-note: a false-FAIL on a parse failure would halt
  the pipeline gate, the strictly worse failure mode for an audit).

`_pyfn` never raises into an audit: `SyntaxError`, `OSError`, `ValueError`,
and `UnicodeDecodeError` are swallowed and surface as the `None` tri-state.

This is a leading-underscore helper module with no `main()`, so it is
structurally excluded from UTF8-STDOUT-1 (`utf8_stdout_audit.py` skips
`name.startswith("_")`) and from INST-1/PMI-1 `_CANONICAL_TOOLS`
(`install_audit.py` leading-underscore convention) — mirroring `tools/_stdout.py`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# Mirrors tools/test_first_audit.py:_EMPTY_SENTINELS (kept in sync by the
# PTFFD-1 tests). A value in this set means "no function to check".
_EMPTY_SENTINELS: frozenset[str] = frozenset(
    {"", "—", "-", "n/a", "none", "(none)"}
)

# Strict Python-identifier, optionally followed by a pytest `[param-id]`.
# Anchored full-match: anything with whitespace, `(`, `—`, `::`, or multiple
# tokens is NOT a checkable function name.
_CHECKABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[.*\])?")

# Trailing pytest parametrize id, e.g. `test_x[case-1]` -> base `test_x`.
_PARAM_SUFFIX_RE = re.compile(r"\[.*\]$")


def is_checkable_function_name(s: str) -> bool:
    """B2 discriminator: True iff `s` is a strict identifier (± `[param]`).

    Prose / multi-token / sentinel values return False so the caller degrades
    to FILE-level-only — never a false-positive `missing-test-function`.
    """
    if s is None:
        return False
    stripped = s.strip()
    if stripped.lower() in _EMPTY_SENTINELS:
        return False
    return _CHECKABLE_NAME_RE.fullmatch(stripped) is not None


def selector_terminal_name(s: str) -> str | None:
    """Return the terminal callable name of a selector / function token.

    `tests/x.py::TestC::test_m` -> `test_m`; `test_x[case]` -> `test_x`;
    `tests/x.py` (no `::`, not an identifier) -> None.
    Strips a trailing `[param-id]`. Returns None when no checkable terminal
    name can be extracted.
    """
    if s is None:
        return None
    tok = s.strip().strip("`").strip().strip('"').strip("'")
    if "::" in tok:
        tok = tok.split("::")[-1].strip()
    tok = _PARAM_SUFFIX_RE.sub("", tok).strip()
    if not tok or tok.lower() in _EMPTY_SENTINELS:
        return None
    # Only return it if it is itself a bare identifier (post-strip). A path
    # like `tests/x.py` has no `::` and is not an identifier -> None.
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok) is None:
        return None
    return tok


def _collect_def_names(tree: ast.AST) -> set[str]:
    """Every FunctionDef / AsyncFunctionDef name at any nesting depth."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def function_defined_in_file(
    py_file: Path, func_name: str
) -> bool | None:
    """Tri-state: True=defined, False=parsed-but-absent, None=cannot-determine.

    `func_name` may carry a `[param-id]`; it is stripped to the base
    identifier before matching (out-of-scope: per-parameter resolution).
    Returns None (skip-with-note, no violation) when the file cannot be read
    or parsed — `_pyfn` never raises into the audit.
    """
    base = _PARAM_SUFFIX_RE.sub("", func_name.strip()) if func_name else ""
    if not base or base.lower() in _EMPTY_SENTINELS:
        return None
    try:
        source = py_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    return base in _collect_def_names(tree)
