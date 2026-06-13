"""crg_isolate.py — env-var graph-isolation guard for /diagnose + /bug-hunt (slice-006).

The two whole-codebase analysers isolate their throwaway code graph via the ``CRG_DATA_DIR``
environment variable, which (unlike the ``--data-dir`` flag) mutates **no** global state. But
``code_review_graph.incremental.get_data_dir`` resolves the data dir in priority order
**registry > CRG_DATA_DIR > default**, so two preconditions must hold for env-var isolation to
actually take effect — this guard checks them rather than assuming (slice-006 M-add-1 / M-add-3):

  check   (BEFORE build): exit 1 (caller aborts / takes its degraded path) if
            (a) <target> is not a VCS root (.git/.svn) — CRG validates this and would otherwise
                fail the build/readback (M-add-3); or
            (b) a pre-existing REGISTRY mapping for <target> would SHADOW CRG_DATA_DIR (registry is
                priority 1), so build+readback would silently use the registered dir, not <data-dir>.
                Abort loudly with how to clear it rather than produce a wrong-graph result (M-add-1).
  verify  (AFTER build): exit 1 if get_data_dir(<target>) != <data-dir> — proves isolation took
            effect (the belt to check's suspenders).

Mirrors scripts/lib/_crg_impact.py: best-effort, messages to stderr, exit codes. CRG-absent is NOT
this script's gate — the caller's existing CRG-missing degraded path owns that — so a missing CRG
import returns 0 and lets the build step decide.

Usage:  <py> crg_isolate.py <check|verify> --target <repo> --data-dir <CRG_DATA_DIR value>
Exit:   0 = OK to proceed / isolation verified;  1 = guard tripped;  2 = bad usage.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# Invoked as `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/crg_isolate.py" ...`, which puts
# scripts/lib (NOT the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib ...`
# resolves (matches gate_log.py et al.).
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/crg_isolate.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout


def _check(target: Path, data_dir: Path) -> int:
    # M-add-3: env-var isolation requires a recognizable project root.
    if not (target / ".git").exists() and not (target / ".svn").exists():
        print(f"crg_isolate: target {target} is not a VCS root (.git/.svn) — env-var graph "
              f"isolation is unsupported here; take the documented degraded path.", file=sys.stderr)
        return 1
    # M-add-1: a registry entry (priority 1) would shadow CRG_DATA_DIR (priority 2).
    try:
        from code_review_graph.registry import Registry
    except Exception:
        return 0  # CRG not installed — not this guard's concern; the build step handles it.
    try:
        registered = Registry().get_data_dir_for_repo(str(target))
    except Exception:
        return 0  # registry unreadable — let the build/verify step surface any real problem.
    if registered:
        reg_p = Path(registered).resolve()
        if reg_p != data_dir:
            print(f"crg_isolate: a pre-existing CRG registry mapping for {target} -> {reg_p} would "
                  f"SHADOW CRG_DATA_DIR={data_dir} (registry is priority 1), producing a wrong-graph "
                  f"read. Clear it first:  code-review-graph unregister \"{target}\"  — then re-run. "
                  f"Aborting to avoid a silent wrong-graph result.", file=sys.stderr)
            return 1
    return 0


def _verify(target: Path, data_dir: Path) -> int:
    try:
        from code_review_graph.incremental import get_data_dir
    except Exception:
        return 0  # CRG not installed — caller's degraded path owns this.
    try:
        resolved = get_data_dir(target).resolve()
    except Exception as exc:
        print(f"crg_isolate: could not resolve the graph dir for {target}: {exc}", file=sys.stderr)
        return 1
    if resolved != data_dir:
        print(f"crg_isolate: isolation FAILED — get_data_dir({target}) resolved to {resolved}, "
              f"expected {data_dir}. Aborting rather than reading the wrong graph.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(prog="crg_isolate")
    ap.add_argument("mode", choices=["check", "verify"])
    ap.add_argument("--target", required=True)
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args(argv)
    target = Path(args.target).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    return _check(target, data_dir) if args.mode == "check" else _verify(target, data_dir)


if __name__ == "__main__":
    sys.exit(main())
