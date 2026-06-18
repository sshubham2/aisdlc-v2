"""active_slice_guard_audit.py — AC4 guard (slice-014 / SC-23).

The hardened resolver (`active_slice.py`) fail-visibly **exits 4** and prints an
AMBIGUOUS HALT on **stderr** when >=2 slices are in flight and no slice is designated
(ADR-010). A SKILL.md injection that pipes the resolver call to `2>/dev/null` would
DISCARD that HALT and silently skip -- recreating the silent no-op the slice exists to
kill. This audit asserts that NO `active_slice.py` injection swallows the resolver's
stderr, so the fail-visible refusal always reaches the agent.

It flags a `2>/dev/null` that lands INSIDE the `active_slice.py $(...)` call (before its
closing paren). A `2>/dev/null` on a WRAPPING command (after the `)`, e.g. a project-frame
or audit call that consumes the resolved path) is fine and is NOT flagged.

CLI: `[--root <plugin-root>] [--json]`. Exit 0 = clean, 1 = violations found. Read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# <plugin>/scripts/lib/active_slice_guard_audit.py -> <plugin>
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout

# The swallow: `2>/dev/null` reached without first closing the active_slice.py `$(...)`
# (i.e. `[^)]*` consumes only up to the first ')' which is the call's own close).
_SWALLOW = re.compile(r"active_slice\.py[^)]*2>\s*/dev/null")


def audit(root: str | Path) -> list[str]:
    """Return a sorted list of `skills/<name>/SKILL.md:LINE` sites where an
    `active_slice.py` injection swallows the resolver stderr. Empty list = clean."""
    root = Path(root)
    violations: list[str] = []
    for md in sorted((root / "skills").glob("*/SKILL.md")):
        for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            # strip an inline ` # ...` comment first so prose mentioning 2>/dev/null in a
            # comment is never mistaken for an actual stderr-swallowing redirect.
            code = re.sub(r"\s+#.*$", "", line)
            if _SWALLOW.search(code):
                violations.append(f"{md.relative_to(root).as_posix()}:{i}")
    return violations


# slice-019 (AC4 / M4): the PYTHON consumer family of resolve_active_slice. A file that CALLS the
# resolver MUST handle the AMBIGUOUS sentinel (it checks source=='ambiguous' before dereferencing
# info['path']); a caller that never mentions 'ambiguous' TypeErrors on the truthy None-path sentinel
# (the slice-019 reflection_lookup/vault_snapshot crash). The roster is derived PROGRAMMATICALLY (every
# importer), so a FUTURE consumer can't silently skip the guard -- slice-016: audit the family, don't hope.
_CALL = re.compile(r"\bresolve_active_slice\s*\(")
_GUARD_TOKEN = "ambiguous"
_NOT_CONSUMERS = {"active_slice.py", "active_slice_guard_audit.py"}


def audit_python_consumers(root: str | Path) -> list[str]:
    """Sorted list of `<relpath>` python files that CALL resolve_active_slice but never handle the
    AMBIGUOUS sentinel (no 'ambiguous' token). Empty list = clean (the programmatic family check)."""
    root = Path(root)
    pyfiles = list((root / "scripts" / "lib").glob("*.py")) + list((root / "skills").glob("*/scripts/*.py"))
    out: list[str] = []
    for py in sorted(pyfiles):
        if py.name in _NOT_CONSUMERS:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _CALL.search(text) and _GUARD_TOKEN not in text:
            out.append(py.relative_to(root).as_posix())
    return out


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="active_slice_guard_audit",
        description="AC4 guard: no SKILL.md injection may 2>/dev/null-swallow the active_slice resolver stderr.",
    )
    p.add_argument("--root", default=str(_PLUGIN_ROOT), help="plugin root (default: derived from this file)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    violations = audit(args.root)
    py_unguarded = audit_python_consumers(args.root)
    clean = not violations and not py_unguarded
    if args.json:
        print(json.dumps({"stderr_swallow": violations, "python_unguarded": py_unguarded,
                          "clean": clean}, ensure_ascii=False))
    else:
        if violations:
            print("AC4 guard FAIL -- active_slice.py injections that SWALLOW the resolver stderr "
                  "(an AMBIGUOUS exit-4 HALT would be discarded -> silent skip):")
            for v in violations:
                print(f"  {v}")
        if py_unguarded:
            print("AC4 guard FAIL -- resolve_active_slice PYTHON consumers missing the AMBIGUOUS guard "
                  "(they would TypeError on the truthy None-path sentinel):")
            for v in py_unguarded:
                print(f"  {v}")
        if clean:
            print("AC4 guard: clean -- no stderr-swallow, and every resolve_active_slice python "
                  "consumer guards the AMBIGUOUS sentinel.")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
