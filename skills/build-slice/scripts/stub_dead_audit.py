"""stub_dead_audit.py — STUB-DEAD-1: deterministic, diff-scoped stub/dead-code gate.

The /build-slice pre-finish gate's other checks are all vault/JSON/git-state audits — none
reads source, so the pipeline's "no stubs, no dead paths" promise rested on a model reading a
checklist. STUB-DEAD-1 makes it executable: it reads the slice diff (branch vs the LOCAL
integration base) and BLOCKS with the exact path:line when the diff INTRODUCES a stub body, a
silent broad `except: pass`, or statically-unreachable-after-terminal code — the same way a
failing test does.

Mechanism (ADR-099 / ADR-100): DIFF-SCOPE BY ADDED-LINE INTERSECTION on a single working-tree
AST parse. A finding is kept only when its trip-span intersects the git added-line set of that
file (masking, NOT baseline subtraction — line renumbering makes a pre-existing stub look new).
Blocking rule set is the three AST-decidable, high-precision rules only; vulture/ruff-style
unused-symbol reachability is deliberately NOT implemented (FP-prone; not even WARN).

Rules:
  STUB-BODY       a function whose body is a sole `pass` / `...` / `raise NotImplementedError`
                  (Name OR Call form) — docstring-prefixed / docstring-only included. Carve-outs
                  (NOT stubs): @abstractmethod/@abc.abstractmethod, @overload/@typing.overload,
                  members of a Protocol/ABC class, `.pyi` files, `if TYPE_CHECKING:` bodies,
                  an empty `__init__` and the named lifecycle hooks (__enter__/__exit__/
                  __aenter__/__aexit__/setUp/tearDown), plus any body carrying a `# stub-dead:allow`
                  token. Trips only when the def / a decorator / the body-stmt line is itself in
                  the added set (not merely any line inside the enclosing span).
  SILENT-EXCEPT   a sole-`pass` handler that is BARE (`except:`) or BROAD (`except Exception:` /
                  `except BaseException:`). Narrow-typed `except (Specific, ...): pass` is the
                  repo's deliberate best-effort idiom and is NOT blocked. Token-suppressible.
  UNREACHABLE     a statement immediately following a `return`/`raise`/`break`/`continue` in the
                  SAME suite. Terminal-node set only (no name-resolved sys.exit()/os._exit()).
                  Trip-span = the terminal line UNION the unreachable stmt's span, so an ADDED
                  terminal that orphans pre-existing code is attributed even though the orphaned
                  lines are not themselves added.

Fail-closed and fail-loud (must_not_defer #1): a git failure / a genuinely-unparseable added
`.py` / a detector crash / an unresolvable base is a BLOCK with a loud `[STUB-DEAD-1] INFRA:`
banner as the FIRST stdout line (survives pre_finish_gate's 6-line summary truncation), never a
silent pass. A slice that changed no `.py` (git-diff clean-empty — e.g. a docs-only slice) is a
legitimate PASS. base=='HEAD' on a REAL worktree is the documented no-remote fallback (the twins
degrade to it too) → diff vs HEAD, NOT infra (ADR-100 correction #2). A legitimately-unparseable
added `.py` — one under a `fixtures/` path or carrying a `# stub-dead:allow-unparseable` token —
PASSES; any other SyntaxError surfaces the running interpreter version so a version-skew block is
diagnosable (ADR-100 correction #3; documented minimum interpreter: Python 3.10).

Reuses scripts/lib/slice_diff_base.resolve_slice_diff_base (the same fork-point base
code-review/validate-slice trust) and scripts/lib/_stdout (UTF8-STDOUT-1). Follows the
mock_budget_lint.py stdlib-ast precedent.

Usage (run from / against the slice worktree):
    python stub_dead_audit.py --worktree <wt> [--base <ref>]

--base lets the caller (pre_finish_gate) thread the base it already resolved so STUB-DEAD-1
shares the exact scope of the other diff-scoped checks (M-add-3); omitted, the detector
self-resolves via slice_diff_base.

Exit codes:
    0  PASS — no in-scope stub/dead finding (incl. a slice that changed no .py)
    1  BLOCK — >=1 finding (each printed path:line) OR infra (banner-distinguished)
    2  usage error — missing/bogus --worktree (not an existing git worktree root)
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- single-skill import bootstrap (a skill command runs in the user's CWD; cannot use `-m`) ---
_REPO = Path(__file__).resolve().parents[3]  # skills/build-slice/scripts -> plugin root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib.slice_diff_base import resolve_slice_diff_base  # noqa: E402

# Documented minimum gate interpreter (the repo's `match`-statement floor). A file using syntax
# newer than the running interpreter raises SyntaxError; the INFRA banner surfaces the version so
# the remedy (run the gate under a matching interpreter) is diagnosable (ADR-100 correction #3).
MIN_PY = (3, 10)

# Suppression tokens (raw-source scan — ast strips comments).
TOKEN = "stub-dead:allow"                       # inline: suppress a finding on its construct's line
TOKEN_UNPARSEABLE = "stub-dead:allow-unparseable"  # file-level: a deliberately-broken .py PASSES

_TERMINALS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
_STUB_DECORATORS = frozenset({"abstractmethod", "abstractproperty", "overload"})
_LIFECYCLE_EXEMPT = frozenset({"__enter__", "__exit__", "__aenter__", "__aexit__",
                               "setUp", "tearDown"})
_PROTOCOL_ABC_BASES = frozenset({"Protocol", "ABC", "ABCMeta"})
# Body kinds that count as an intentional EMPTY concrete body for the __init__/lifecycle carve-out.
# A `raise NotImplementedError` is an explicit not-implemented marker, NOT an empty no-op, so it is
# still flagged even in __init__/lifecycle methods (M4 scope: exempt EMPTY bodies only).
_EMPTY_KINDS = frozenset({"pass", "ellipsis", "docstring-only"})

# git omits the added-count when it is exactly 1 (`@@ -2,0 +3 @@`); a pure deletion emits `+c,0`
# (zero added lines). Parse the added side as start + optional count (default 1) (M3).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class _Infra(Exception):
    """A fail-closed infrastructure condition (git failure / unparseable / read fault) — BLOCK,
    never a silent pass."""


@dataclass
class Finding:
    rule: str
    line: int                    # 1-based report line
    trip_lines: set              # if any is in the file's added-line set, the finding trips
    path: str
    message: str = ""
    key: tuple = field(default=(), compare=False)


def _pyver() -> str:
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _name_of(node) -> str | None:
    """Terminal identifier of a Name/Attribute (`typing.overload` -> 'overload')."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dec_name(dec) -> str | None:
    return _name_of(dec.func) if isinstance(dec, ast.Call) else _name_of(dec)


def _is_docstring(stmt) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(getattr(stmt, "value", None), ast.Constant)
            and isinstance(stmt.value.value, str))


def _is_notimplemented(exc) -> bool:
    """True for `raise NotImplementedError` (Name) OR `raise NotImplementedError(...)` (Call) —
    distinct AST nodes; a Name-only rule silently misses the most common real stub form (M-add-2)."""
    if exc is None:
        return False
    target = exc.func if isinstance(exc, ast.Call) else exc
    return _name_of(target) == "NotImplementedError"


def _stub_body_kind(body) -> str | None:
    """The stub kind of a function body, or None. Docstring is allowed as a prefix (or the whole
    body): a `'''TODO'''`-only body is a stub (docstring-only)."""
    real = [s for s in body if not _is_docstring(s)]
    if not real:
        return "docstring-only"
    if len(real) != 1:
        return None
    s = real[0]
    if isinstance(s, ast.Pass):
        return "pass"
    if (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant)
            and s.value.value is Ellipsis):
        return "ellipsis"
    if isinstance(s, ast.Raise) and _is_notimplemented(s.exc):
        return "raise-NotImplementedError"
    return None


def _is_bare_or_broad(exc_type) -> bool:
    if exc_type is None:
        return True                                   # bare `except:`
    if isinstance(exc_type, ast.Tuple):
        return any(_name_of(e) in ("Exception", "BaseException") for e in exc_type.elts)
    return _name_of(exc_type) in ("Exception", "BaseException")


def _is_type_checking(test) -> bool:
    return _name_of(test) == "TYPE_CHECKING"


class _Visitor(ast.NodeVisitor):
    """Collects STUB-BODY + SILENT-EXCEPT findings, tracking enclosing-class bases and
    TYPE_CHECKING depth for the carve-outs."""

    def __init__(self, rel: str):
        self.rel = rel
        self.findings: list[Finding] = []
        self.class_bases: list[frozenset] = []
        self.type_checking_depth = 0

    def visit_ClassDef(self, node):
        bases = frozenset(b for b in (_name_of(x) for x in node.bases) if b)
        self.class_bases.append(bases)
        self.generic_visit(node)
        self.class_bases.pop()

    def visit_If(self, node):
        if _is_type_checking(node.test):
            self.type_checking_depth += 1
            for stmt in node.body:
                self.visit(stmt)
            self.type_checking_depth -= 1
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._func(node)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self._except(node)
        self.generic_visit(node)

    def _func(self, node):
        if self.type_checking_depth > 0:
            return
        if any(_dec_name(d) in _STUB_DECORATORS for d in node.decorator_list):
            return
        if self.class_bases and (self.class_bases[-1] & _PROTOCOL_ABC_BASES):
            return
        kind = _stub_body_kind(node.body)
        if not kind:
            return
        # M4: an empty __init__ / named lifecycle hook is an intentional no-op, not a stub —
        # but a `raise NotImplementedError` in one is still an explicit stub, so exempt EMPTY kinds only.
        if node.name in _LIFECYCLE_EXEMPT or node.name == "__init__":
            if kind in _EMPTY_KINDS:
                return
        # Report the OPERATIVE stub statement (the pass/Ellipsis/raise), not a leading docstring —
        # the exact path:line the reader must act on (m1). Falls back to the docstring line for a
        # docstring-only body, then the def line. This is also the M2 trip key: only a change to the
        # operative body stmt / def / decorator re-flags a stub, never editing a docstring above it.
        operative = [s for s in node.body if not _is_docstring(s)]
        body_line = (operative[0].lineno if operative
                     else (node.body[0].lineno if node.body else node.lineno))
        trip = {node.lineno, body_line}
        trip.update(d.lineno for d in node.decorator_list)
        self.findings.append(Finding(
            "STUB-BODY", body_line, trip, self.rel,
            f"{node.name}: stub body ({kind}) — implement it or remove the placeholder",
            key=(self.rel, "STUB-BODY", node.name, body_line)))

    def _except(self, node):
        real = [s for s in node.body if not _is_docstring(s)]
        if len(real) != 1 or not isinstance(real[0], ast.Pass):
            return
        if not _is_bare_or_broad(node.type):
            return
        pass_line = real[0].lineno
        trip = {node.lineno, pass_line}
        self.findings.append(Finding(
            "SILENT-EXCEPT", pass_line, trip, self.rel,
            "silent broad 'except: pass' swallows every error — narrow the except or handle it",
            key=(self.rel, "SILENT-EXCEPT", pass_line)))


def _unreachable_findings(tree, rel: str) -> list[Finding]:
    """A statement immediately following a terminal in the SAME suite is unreachable. Attribute it
    to the FIRST (causing) terminal; the trip-span unions the terminal line with the unreachable
    stmt's span so an added terminal orphaning pre-existing code is caught (AC3)."""
    out: list[Finding] = []
    for node in ast.walk(tree):
        for attr in ("body", "orelse", "finalbody"):
            suite = getattr(node, attr, None)
            if not isinstance(suite, list):
                continue
            terminal_line = None
            for stmt in suite:
                if terminal_line is not None:
                    end = getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno
                    trip = {terminal_line, *range(stmt.lineno, end + 1)}
                    out.append(Finding(
                        "UNREACHABLE", stmt.lineno, trip, rel,
                        "unreachable code after return/raise/break/continue — remove it",
                        key=(rel, "UNREACHABLE", stmt.lineno)))
                elif isinstance(stmt, _TERMINALS):
                    terminal_line = stmt.lineno
    return out


def _line_has_inline_token(src_lines: list[str], lineno: int) -> bool:
    if 1 <= lineno <= len(src_lines):
        text = src_lines[lineno - 1]
        return TOKEN in text and TOKEN_UNPARSEABLE not in text
    return False


def _suppressed(src_lines: list[str], lines) -> bool:
    return any(_line_has_inline_token(src_lines, ln) for ln in lines)


def _is_fixture_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return any(p in ("fixtures", "fixture") for p in parts)


def _detect_file(rel: str, source: str, added: set) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        if _is_fixture_path(rel) or TOKEN_UNPARSEABLE in source:
            return []                                 # legitimately-unparseable → PASS-neutral (ADR-100 #3)
        raise _Infra(
            f"cannot parse changed file {rel} with python {_pyver()} "
            f"(SyntaxError line {exc.lineno}: {exc.msg}). If it uses newer syntax, run the gate "
            f"under a matching interpreter (>= python {MIN_PY[0]}.{MIN_PY[1]}); if it is an "
            f"intentional fixture, place it under a fixtures/ path or add a "
            f"'# {TOKEN_UNPARSEABLE}' token")
    src_lines = source.splitlines()
    visitor = _Visitor(rel)
    visitor.visit(tree)
    candidates = visitor.findings + _unreachable_findings(tree, rel)
    kept: list[Finding] = []
    for f in candidates:
        if f.trip_lines & added and not _suppressed(src_lines, f.trip_lines | {f.line}):
            kept.append(f)
    return kept


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    # -c core.quotePath=false so a non-ASCII / space path is emitted literally, not C-quoted (m3).
    return subprocess.run(
        ["git", "-C", str(worktree), "-c", "core.quotePath=false", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _nul_split(text: str) -> list[str]:
    return [p for p in text.split("\0") if p]


def _changed_py(worktree: Path, base: str) -> tuple[list[str], set]:
    """(changed_existing_py_paths, untracked_set). Excludes deletions (--diff-filter=d) so a slice
    that deletes/renames a .py is PASS-neutral, not INFRA (B2). NUL-delimited for path safety."""
    r = _git(worktree, "diff", "--name-only", "-z", "--diff-filter=d", base, "--", "*.py")
    if r.returncode != 0:
        raise _Infra(f"`git diff --name-only {base}` failed: {(r.stderr or '').strip()}")
    tracked = _nul_split(r.stdout)
    ru = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z", "--", "*.py")
    if ru.returncode != 0:
        raise _Infra(f"`git ls-files --others` failed: {(ru.stderr or '').strip()}")
    untracked = _nul_split(ru.stdout)
    ordered: list[str] = []
    for p in tracked + untracked:
        if p not in ordered:
            ordered.append(p)
    return ordered, set(untracked)


def _added_lines(worktree: Path, base: str, rel: str, source: str, is_untracked: bool) -> set:
    if is_untracked:
        return set(range(1, len(source.splitlines()) + 1))  # whole untracked file is added
    r = _git(worktree, "diff", "--unified=0", base, "--", rel)
    if r.returncode != 0:
        raise _Infra(f"`git diff --unified=0 {base} -- {rel}` failed: {(r.stderr or '').strip()}")
    added: set = set()
    for line in r.stdout.splitlines():
        m = _HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        added.update(range(start, start + count))
    return added


def run_audit(worktree: Path, base: str) -> list[Finding]:
    """The full diff-scoped scan. Raises _Infra (fail-closed) on any git/read fault."""
    changed, untracked = _changed_py(worktree, base)
    findings: list[Finding] = []
    for rel in changed:
        fp = worktree / rel
        if rel.endswith(".pyi") or not fp.is_file():   # type-stub / vanished (deletion) → skip
            continue
        try:
            source = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _Infra(f"cannot read changed file {rel}: {exc}")
        added = _added_lines(worktree, base, rel, source, rel in untracked)
        if not added:
            continue
        findings.extend(_detect_file(rel, source, added))
    return findings


def _emit(findings: list[Finding], base: str) -> int:
    if not findings:
        tag = "HEAD" if base == "HEAD" else base[:12]
        print(f"[STUB-DEAD-1] PASS: no stub/dead-code introduced by this diff (base {tag})")
        return 0
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    n = len(findings)
    print(f"[STUB-DEAD-1] FINDING: {n} stub/dead-code issue(s) introduced by this diff (blocking)")
    shown = findings[:4]
    for f in shown:
        print(f"{f.path}:{f.line}: [{f.rule}] {f.message}")
    if n > len(shown):
        print(f"(+{n - len(shown)} more — full list below)")
    for f in findings[len(shown):]:
        print(f"{f.path}:{f.line}: [{f.rule}] {f.message}")
    return 1


def _is_git_worktree_root(worktree: Path) -> bool:
    """A real, existing git worktree ROOT. `is_dir()` alone is insufficient — an empty-string
    --worktree becomes Path('.') (truthy, is_dir True); require `git rev-parse` to confirm."""
    try:
        if not worktree.is_dir():
            return False
        r = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="stub_dead_audit",
        description="STUB-DEAD-1: deterministic diff-scoped stub/dead-code pre-finish gate.")
    ap.add_argument("--worktree", required=True, help="the slice worktree (HEAD = the slice branch)")
    ap.add_argument("--base", default=None,
                    help="diff base ref (default: resolve via slice_diff_base). pre_finish_gate "
                         "threads the base it already resolved so scope matches the other checks.")
    args = ap.parse_args(argv)

    raw = args.worktree or ""
    worktree = Path(raw)
    if not raw.strip() or not _is_git_worktree_root(worktree):
        sys.stderr.write(
            f"stub_dead_audit: --worktree {raw!r} is not an existing git worktree root — refusing "
            f"to audit (a bogus worktree would silently operate on the main repo).\n")
        return 2

    try:
        # base resolution is INSIDE the fail-closed try (m3): a self-resolve fault must surface as
        # the loud [STUB-DEAD-1] INFRA banner, not a raw traceback. Production threads --base, so
        # this branch is rarely hit — but fail-loud must hold on the self-resolve path too.
        base = args.base.strip() if (args.base and args.base.strip()) else resolve_slice_diff_base(worktree)
        findings = run_audit(worktree, base)
    except _Infra as exc:
        # Fail-closed + fail-loud: BLOCK with the banner as the FIRST line (survives truncation).
        print(f"[STUB-DEAD-1] INFRA: {exc} (blocking; running python {_pyver()})")
        return 1
    except Exception as exc:  # noqa: BLE001 — any detector fault BLOCKS, never a silent pass (must_not_defer #1)
        print(f"[STUB-DEAD-1] INFRA: unexpected detector fault: "
              f"{type(exc).__name__}: {exc} (blocking; running python {_pyver()})")
        return 1

    return _emit(findings, base)


if __name__ == "__main__":
    sys.exit(main())
