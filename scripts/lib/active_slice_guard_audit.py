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
    if args.json:
        print(json.dumps({"violations": violations, "clean": not violations}, ensure_ascii=False))
    elif violations:
        print("AC4 guard FAIL -- active_slice.py injections that SWALLOW the resolver stderr "
              "(an AMBIGUOUS exit-4 HALT would be discarded -> silent skip):")
        for v in violations:
            print(f"  {v}")
    else:
        print("AC4 guard: clean -- no active_slice.py injection swallows the resolver stderr.")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
