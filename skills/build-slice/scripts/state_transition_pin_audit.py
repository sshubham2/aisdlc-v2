"""State-transition stale-pin audit (STP-1) — v2 JSON.

Per **STP-1** (`methodology-changelog.md` v0.54.0; slice-044; [[ADR-047]]).
Detects the recurring methodology-discipline class: a slice performs a *state
transition* but a pre-existing test still pins the OLD value and was not
realigned in the same fix block.

Two mechanically-detectable sub-forms (the fuzzy ADR accepted->superseded
sub-form is explicitly out of scope per ADR-047):

- **Sub-form A — SKILL.md-prose-repoint stale pin** (git-diff-independent
  standing invariant). For every ``tests/**/test_*skill*.py`` prose-pin
  asserting a constant string literal via positive ``in`` membership against a
  name traced (directly or through ``str.split(...)[i]`` / subscript / slice
  chains) from a ``read_file("skills/<x>/SKILL.md")`` call, the folded literal
  MUST be present in the *full* target ``SKILL.md``. A ``not in`` pin, or any
  membership whose enclosing ``BoolOp`` has a ``NotIn`` / non-constant sibling,
  is excluded. (Paths are already v2-correct — ``skills/<x>/SKILL.md`` is the
  v2 layout.)

- **Sub-form B — risk-status-stale pin** (git-diff-independent standing
  invariant). A test ``FunctionDef`` name matching
  ``(?:^|_)r[_-]?(\\d+).*?_(stays|remains|is)_(open|mitigating|retired|accepted)(?:_|$)``
  claims ``R-<num>`` is at the named status; if the *live* risk register's
  status for that risk differs from the claimed status it is a stale pin.

**v2 changes from v1.**
- **Register read flipped md -> JSON.** v1 read ``architecture/risk-register.md``
  (H2-structured markdown) via ``risk_register_audit._parse_risks(text, path)``.
  v2 reads ``<vault>/risk-register.json`` (the ``risks[]`` array) — loaded with
  ``json.loads`` and parsed via the v2 ``risk_register_audit._parse_risks(data,
  path)`` (object-identity reuse, CSP-1; the SAME parser RR-1 uses). The Risk
  field is ``.id`` (v2), not v1's ``.risk_id``.
- **VAULT_ROOT routing.** The register lives at ``VAULT_ROOT / "risk-register.json"``
  (the external store), not an in-tree ``architecture/``.
- **Graceful no-op (v2 reality).** The methodology test suite (``tests/**``) may
  not exist yet. When there are NO matching test files, BOTH sub-forms are pure
  no-ops and the audit exits 0 (clean) — including skipping the register read
  entirely (there are no pins that need it). The v1 fail-closed-on-missing-
  register path fires ONLY when test files that assert risk statuses actually
  exist and therefore need the live register.

Parse-failure discipline (ADR-037 / PTFFD-1): a per-scanned file that does not
parse is **skip-with-visible-note, NO violation, NOT exit 2** — a false-FAIL on
a parse failure would halt the gate. Exit-2 fail-closed is reserved for
hard-input failure: the live ``risk-register.json`` is present-but-unreadable /
unparseable WHILE risk-status pins exist, or repo-root unresolvable.

Usage:
    python state_transition_pin_audit.py
    python state_transition_pin_audit.py --root <repo-root>
    python state_transition_pin_audit.py --json

Exit codes:
    0  clean (incl. when there are no test files, or scanned files were
       skipped-with-note for parse failure)
    1  Important violation (`stale-skill-prose-pin` / `stale-risk-status-pin`)
    2  usage-error (hard-input failure only — see above)
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402
from scripts.lib._vault_paths import VAULT_ROOT  # noqa: E402
# Object-identity reuse (CSP-1) — Sub-form B parses the live register via the
# SAME parser RR-1 uses; NOT re-derived. (v2 `_parse_risks` takes a parsed dict.)
from scripts.lib.risk_register_audit import _parse_risks  # noqa: E402
from scripts.lib.risk_status import RISK_STATUSES  # noqa: E402

# Sub-form A: recognise a `read_file("skills/<x>/SKILL.md")` call.
_SKILL_MD_ARG_RE = re.compile(r"^skills/[^\"']+/SKILL\.md$")

# Sub-form B detector. `(?:^|_)`-anchored; status alternation terminated by
# `(?:_|$)`. Non-greedy `.*?` binds the nearest status.
# Status alternation DERIVED from the canonical SSOT (slice-010 / ADR-008) — this audit is a
# 4th consumer of risk_status.RISK_STATUSES, so its recognized vocabulary can never drift from
# the validators (it was previously a hand-kept literal {open,mitigating,retired,accepted} that
# was blind to the elevated `blocking`/`conditional` statuses risk-spike writes). sorted() for a
# stable pattern; the anchored `_(stays|remains|is)_<status>(?:_|$)` grammar bounds the token, so
# widening to all canonical statuses cannot over-match an unrelated function name.
_RISK_STATUS_ALT = "|".join(sorted(RISK_STATUSES))
_RISK_STATUS_FN_RE = re.compile(
    r"(?:^|_)r[_-]?(\d+).*?_(stays|remains|is)_(" + _RISK_STATUS_ALT + r")(?:_|$)"
)

_LITERAL_TRUNC = 80


@dataclass(frozen=True)
class StateTransitionViolation:
    kind: str       # "stale-skill-prose-pin" | "stale-risk-status-pin" |
                    # "usage-error"
    severity: str   # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    repo_root: str = ""
    violations: list[StateTransitionViolation] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    boolop_pin_stats: dict = field(
        default_factory=lambda: {"positive_only": 0, "mixed_excluded": 0}
    )

    def to_dict(self) -> dict:
        important = [v for v in self.violations if v.severity == "Important"]
        return {
            "rule": "STP-1",
            "repo_root": self.repo_root,
            "violations": [v.to_dict() for v in self.violations],
            "skipped": list(self.skipped),
            "boolop_pin_stats": dict(self.boolop_pin_stats),
            "summary": {
                "violation_count": len(important),
                "skipped_count": len(self.skipped),
                "clean": not important,
            },
        }


def _const_str(node: ast.AST) -> str | None:
    """Return the folded constant str value of `node`, else None.

    CPython folds implicit adjacent-string concatenation at parse time into a
    single `ast.Constant`. f-strings (`ast.JoinedStr`), `%`/`+`/`.format()`-
    built, and name-only operands are NOT constants and return None.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _root_name(node: ast.AST) -> str | None:
    """Resolve the root identifier of a (possibly sliced/subscripted) expr.

    `content` -> "content"; `content.split("##")[1]` -> "content";
    `prereq_block[0:50]` -> "prereq_block". Returns None if the expression does
    not bottom out in a bare Name.
    """
    cur = node
    while True:
        if isinstance(cur, ast.Name):
            return cur.id
        if isinstance(cur, ast.Subscript):
            cur = cur.value
        elif isinstance(cur, ast.Call):
            if isinstance(cur.func, ast.Attribute):
                cur = cur.func.value
            else:
                return None
        elif isinstance(cur, ast.Attribute):
            cur = cur.value
        else:
            return None


def _skill_md_from_call(node: ast.AST) -> str | None:
    """If `node` is `read_file("skills/<x>/SKILL.md")`, return that rel-path."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Name) and func.id == "read_file"):
        return None
    if not node.args:
        return None
    arg0 = _const_str(node.args[0])
    if arg0 and _SKILL_MD_ARG_RE.match(arg0):
        return arg0
    return None


def _trace_bindings(scope_body: list[ast.stmt], inherited: dict[str, str]) -> dict[str, str]:
    """Map every name bound (transitively) from a SKILL.md `read_file(...)` call
    within this scope to its SKILL.md rel-path."""
    traced = dict(inherited)
    for stmt in scope_body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        rhs = stmt.value
        direct = _skill_md_from_call(rhs)
        if direct is not None:
            traced[target.id] = direct
            continue
        root = _root_name(rhs)
        if root is not None and root in traced:
            traced[target.id] = traced[root]
    return traced


def _excluded_compare_ids(tree: ast.AST) -> set[int]:
    """ids() of Compare nodes that must NOT be treated as positive pins.

    Excluded iff (i) the node's single op is `ast.NotIn`, OR (ii) it is an
    operand of an `ast.Or` BoolOp (a disjunction pins no single operand), OR
    (iii) it is an operand of an `ast.And` BoolOp whose other operands include a
    `NotIn` or a non-constant-left comparison. A positive-only `ast.And` chain
    IS evaluated per-operand.
    """
    excluded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if len(node.ops) == 1 and isinstance(node.ops[0], ast.NotIn):
                excluded.add(id(node))
        elif isinstance(node, ast.BoolOp):
            cmps = [v for v in node.values if isinstance(v, ast.Compare)]
            if isinstance(node.op, ast.Or):
                for c in cmps:
                    excluded.add(id(c))
                continue
            has_bad_sibling = False
            for c in node.values:
                if isinstance(c, ast.Compare) and len(c.ops) == 1:
                    if isinstance(c.ops[0], ast.NotIn):
                        has_bad_sibling = True
                        break
                    if isinstance(c.ops[0], ast.In) and _const_str(c.left) is None:
                        has_bad_sibling = True
                        break
                else:
                    has_bad_sibling = True
                    break
            if has_bad_sibling:
                for c in cmps:
                    excluded.add(id(c))
    return excluded


def _scan_skill_prose_pins(root: Path, result: AuditResult) -> None:
    """Sub-form A. No-op when tests/ is absent (no matching files)."""
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return
    for py in sorted(tests_dir.rglob("test_*skill*.py")):
        try:
            src = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            result.skipped.append(f"{py} (unreadable; ADR-037 skip-with-note)")
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            result.skipped.append(f"{py} (unparseable; ADR-037 skip-with-note)")
            continue

        module_traced = _trace_bindings(tree.body, {})
        excluded = _excluded_compare_ids(tree)
        rel = py.relative_to(root).as_posix()
        skill_cache: dict[str, str | None] = {}

        def _emit(cmp_node: ast.Compare, fn_name: str, traced: dict[str, str]) -> None:
            if id(cmp_node) in excluded:
                return
            if not (len(cmp_node.ops) == 1 and isinstance(cmp_node.ops[0], ast.In)):
                return
            literal = _const_str(cmp_node.left)
            if literal is None:
                return  # non-constant operand — cannot prove absence
            if not cmp_node.comparators:
                return
            rhs_root = _root_name(cmp_node.comparators[0])
            if rhs_root is None or rhs_root not in traced:
                return
            skill_rel = traced[rhs_root]
            if skill_rel not in skill_cache:
                try:
                    skill_cache[skill_rel] = (root / skill_rel).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    skill_cache[skill_rel] = None
                    result.skipped.append(
                        f"{skill_rel} (target unreadable; ADR-037 skip-with-note)"
                    )
            skill_text = skill_cache[skill_rel]
            if skill_text is None:
                return
            if literal not in skill_text:
                shown = literal if len(literal) <= _LITERAL_TRUNC else literal[:_LITERAL_TRUNC] + "..."
                result.violations.append(
                    StateTransitionViolation(
                        kind="stale-skill-prose-pin",
                        severity="Important",
                        message=(
                            f"{rel}::{fn_name} asserts a prose-pin literal "
                            f"absent from {skill_rel}: {shown!r}. The SKILL.md "
                            f"anchor was repointed but this pin was not "
                            f"realigned/superseded in the same fix block — "
                            f"realign or supersede this prose-pin (the R-10 "
                            f"class; STP-1 / ADR-047)."
                        ),
                    )
                )

        def _visit(node: ast.AST, traced: dict[str, str], fn_name: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = _trace_bindings(node.body, traced)
                for child in node.body:
                    _visit(child, local, node.name)
                return
            if isinstance(node, ast.Compare):
                _emit(node, fn_name, traced)
            for child in ast.iter_child_nodes(node):
                _visit(child, traced, fn_name)

        for stmt in tree.body:
            _visit(stmt, module_traced, "<module>")


def _count_boolop_stats(root: Path, result: AuditResult) -> None:
    """Machine-classify the full BoolOp-pin set (B-add-1 reservation)."""
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return
    for py in sorted(tests_dir.rglob("test_*skill*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            continue
        excluded = _excluded_compare_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp):
                continue
            cmps = [
                v for v in node.values
                if isinstance(v, ast.Compare) and len(v.ops) == 1
                and isinstance(v.ops[0], ast.In) and _const_str(v.left) is not None
            ]
            if not cmps:
                continue
            if any(id(c) in excluded for c in cmps):
                result.boolop_pin_stats["mixed_excluded"] += 1
            else:
                result.boolop_pin_stats["positive_only"] += 1


def _load_live_risk_status(register: Path) -> tuple[dict[str, str] | None, StateTransitionViolation | None]:
    """Load the live ``risk-register.json`` and return ``{risk_id: status}``.

    Returns ``(status_by_id, None)`` on success; ``(None, usage_violation)`` when
    the register is present-but-unreadable / unparseable (fail-closed). A MISSING
    register is handled by the caller (it only matters when risk-status pins
    exist, so the caller decides whether absence is fatal).
    """
    try:
        reg_text = register.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, StateTransitionViolation(
            kind="usage-error", severity="Important",
            message=f"risk-register.json unreadable: {e} (fail-closed).",
        )
    try:
        data = json.loads(reg_text) if reg_text.strip() else {}
    except json.JSONDecodeError as e:
        return None, StateTransitionViolation(
            kind="usage-error", severity="Important",
            message=f"risk-register.json is not valid JSON: {e} (fail-closed).",
        )
    try:
        risks, _violations = _parse_risks(data, str(register))
    except Exception as e:  # noqa: BLE001 — any parser failure is fail-closed
        return None, StateTransitionViolation(
            kind="usage-error", severity="Important",
            message=(
                f"risk-register.json unparseable via "
                f"risk_register_audit._parse_risks: {e} (fail-closed)."
            ),
        )
    # RR-1 *content* violations (missing field, etc.) do NOT make the file
    # unparseable — the parsed risks still carry authoritative .status.
    return {r.id: r.status for r in risks}, None


def _scan_risk_status_pins(root: Path, result: AuditResult) -> None:
    """Sub-form B — git-diff-independent standing invariant.

    No-op when tests/ is absent. The live register is loaded LAZILY — only after
    we find at least one risk-status pin (so a missing register on a repo with no
    such pins is a clean no-op, not a usage error).
    """
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return

    register = VAULT_ROOT / "risk-register.json"
    status_by_id: dict[str, str] | None = None
    register_loaded = False

    for py in sorted(tests_dir.rglob("*.py")):
        try:
            src = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            result.skipped.append(f"{py} (unreadable; ADR-037 skip-with-note)")
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            result.skipped.append(f"{py} (unparseable; ADR-037 skip-with-note)")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            m = _RISK_STATUS_FN_RE.search(node.name)
            if not m:
                continue

            # First risk-status pin seen → the live register is now required.
            if not register_loaded:
                register_loaded = True
                if not register.exists():
                    result.violations.append(
                        StateTransitionViolation(
                            kind="usage-error", severity="Important",
                            message=(
                                f"risk-register.json not found at {register} but "
                                f"a risk-status pin exists ({py.relative_to(root).as_posix()}"
                                f"::{node.name}) — Sub-form B cannot resolve live "
                                f"risk statuses (fail-closed; hard-input failure)."
                            ),
                        )
                    )
                    return
                status_by_id, usage = _load_live_risk_status(register)
                if usage is not None:
                    result.violations.append(usage)
                    return

            assert status_by_id is not None
            risk_id = f"R-{int(m.group(1))}"
            claimed = m.group(3)
            live = status_by_id.get(risk_id)
            if live is None:
                continue  # risk not in register — not a contradiction
            if live != claimed:
                rel = py.relative_to(root).as_posix()
                result.violations.append(
                    StateTransitionViolation(
                        kind="stale-risk-status-pin",
                        severity="Important",
                        message=(
                            f"{rel}::{node.name} pins {risk_id} = {claimed!r} but "
                            f"the live risk-register.json has {risk_id} status "
                            f"{live!r} ({claimed} -> {live}). The risk status "
                            f"transitioned but this test was not realigned in the "
                            f"same fix block — realign the test to the live status "
                            f"or rename off the `_<verb>_<status>` token "
                            f"(slice-041 class; STP-1 / ADR-047)."
                        ),
                    )
                )


def audit(root: Path | None = None) -> AuditResult:
    """Run the STP-1 audit. `root` defaults to the nearest ancestor with .git
    (falling back to the plugin root inferred from this script)."""
    if root is None:
        here = Path(__file__).resolve()
        for parent in [here] + list(here.parents):
            if (parent / ".git").exists():
                root = parent
                break
        else:
            # No .git ancestor — fall back to the plugin root (the v2 plugin is
            # not necessarily a git repo when audited in isolation).
            root = _REPO
    root = Path(root).resolve()
    result = AuditResult(repo_root=str(root))

    # Graceful no-op (v2 reality): the methodology test suite may not exist yet.
    # With no tests/ directory there are no pins to check at all — clean exit 0,
    # and we deliberately do NOT touch the risk register (nothing needs it).
    if not (root / "tests").is_dir():
        return result

    if not (root / "skills").is_dir():
        result.violations.append(
            StateTransitionViolation(
                kind="usage-error",
                severity="Important",
                message=f"skills/ directory missing at {root} (fail-closed).",
            )
        )
        return result

    _scan_skill_prose_pins(root, result)
    _count_boolop_stats(root, result)
    _scan_risk_status_pins(root, result)
    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="state_transition_pin_audit",
        description="STP-1: state-transition stale-pin audit (Sub-form A + B).",
    )
    parser.add_argument("--root", type=Path, default=None,
                        help="Repo root (default: nearest .git ancestor / plugin root).")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        result = audit(root=args.root)
    except Exception as e:  # noqa: BLE001 — never raise into the gate
        print(f"state_transition_pin_audit: error: {e}", file=sys.stderr)
        return 2

    important = [v for v in result.violations if v.severity == "Important"]
    usage = [v for v in important if v.kind == "usage-error"]

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for v in important:
            print(f"[{v.severity}] {v.kind}: {v.message}")
        for note in result.skipped:
            print(f"[skip] {note}")
        if not important:
            print(
                f"State-transition stale-pin audit (STP-1): clean. "
                f"{len(result.skipped)} file(s) skipped-with-note; "
                f"BoolOp pins positive-only={result.boolop_pin_stats['positive_only']} "
                f"mixed-excluded={result.boolop_pin_stats['mixed_excluded']}."
            )

    if usage:
        return 2
    if important:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
