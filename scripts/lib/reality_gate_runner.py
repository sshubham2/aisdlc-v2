"""reality_gate_runner.py -- the pluggable reality-gate decide-core CLI
(slice-062 / SC-095 / ADR-059). Mirrors skills/commit-slice/scripts/integration_health_gate.py
(decide-core + injected runner + result-dict + exit-map), and reuses the leaf-pure fold
scripts/lib/verification_core.run_declared_gates.

A project DECLARES which deterministic checks apply to it in a repo-tracked manifest at
<repo-root>/.aisdlc/reality-gates.json (DISTINCT from the external per-machine ~/.aisdlc
vault). The /build-slice pre-finish gate and /validate-slice invoke THIS runner against
the real shipping checkout; it runs exactly the declared set and reports a fail-closed
verdict. The concrete gates (bandit/pip-audit/latency/telemetry) are out of scope -- they
ship as manifest entries in SC-097 + feedback #6/#12; this is the SLOT + the policy.

TWO-LAYER semantics (the load-bearing correctness fork, spike-proven at design time):
  * DECLARATION layer fail-OPEN: an ABSENT manifest OR a valid-but-empty declared set is a
    structural NO-OP -> PASS. Every existing project (no manifest) is unaffected.
  * EXECUTION layer fail-CLOSED: a present-but-malformed / unreadable / unknown-surface
    manifest -> REFUSE (a broken policy is a FAIL, never a false 'all good'); any declared
    gate that is missing / errors / exits nonzero / ABSENT -> the set FAILs. A declared
    security check that silently does not run is worse than one that loudly fails.

Per-entry totality (design invariant #3): a single malformed gate ENTRY (missing a
non-empty id/command) FAILs that entry (subkind 'bad-entry') without aborting the run --
handled inside run_declared_gates; it FAILs the set (exit 1), it does NOT REFUSE (exit 3).

The {security,nfr,ops} surface enum is ENFORCED (slice-004: an enum is only real where the
linter enforces it): an unknown top-level surface key is malformed -> REFUSE, never a
silent drop of the declared gates under it.

Exit codes (the machine contract both wires consume): 0 = PASS (incl. the no-op) ·
1 = FAIL (>=1 declared gate tripped) · 3 = REFUSE (malformed/unreadable manifest --
fail-closed) · 2 = usage (e.g. a missing required --repo-root). --repo-root is REQUIRED
(M-add-1: an omitted/wrong root must fail LOUD, never silently resolve the wrong checkout
and no-op the declared security gates); --json is an accepted no-op (the runner ALWAYS
emits JSON -- M2: without it argparse would exit 2 on a wire that passes --json and FAIL
every gate).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_REPO = Path(__file__).resolve().parents[2]  # scripts/lib/X.py -> plugin root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib.verification_core import run_declared_gates  # noqa: E402

# Result actions
PASS = "pass"
NOOP = "noop"
FAIL = "fail"
REFUSE = "refuse"

KNOWN_SURFACES = ("security", "nfr", "ops")

_MANIFEST_RELPATH = Path(".aisdlc") / "reality-gates.json"


def _exit_for(result: dict) -> int:
    """Map a run_gate result dict to the CLI exit code (single contract home).

    The wires key on ANY non-zero exit -> the check FAILs (pre_finish_gate._run maps any
    nonzero to CheckResult FAIL; validate-slice returns blocked). PASS and NOOP both exit 0."""
    action = result.get("action")
    if action in (PASS, NOOP):
        return 0
    if action == FAIL:
        return 1
    if action == REFUSE:
        return 3
    return 2


def _refuse(reason: str, manifest: str) -> dict:
    return {"action": REFUSE, "reason": reason, "results": [], "manifest": manifest,
            "summary": {"declared": 0, "passed": 0, "failed": 0}}


def default_manifest_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / _MANIFEST_RELPATH


def run_gate(*, repo_root: str | Path, manifest_path: str | Path | None = None,
             surface_filter: str | None = None, gate_runner=run_declared_gates,
             timeout: float | None = None) -> dict:
    """Decide the reality-gate verdict for a checkout. Returns a result dict with
    ``action`` in {pass, noop, fail, refuse}, plus ``reason``, ``results`` (one per gate),
    ``summary`` {declared, passed, failed}, and ``manifest`` (the resolved path).

    ``repo_root`` is the checkout the gates run against -- ALWAYS supplied explicitly by the
    caller (never ambient cwd; the env-lucky wrong-checkout class, slice-046/059)."""
    mpath = Path(manifest_path) if manifest_path else default_manifest_path(repo_root)
    manifest = str(mpath)

    # 1. DECLARATION layer fail-open: absent manifest -> structural no-op.
    if not mpath.is_file():
        return {"action": NOOP,
                "reason": f"no reality-gates manifest at {manifest} -- structural no-op (project declares none).",
                "results": [], "manifest": manifest,
                "summary": {"declared": 0, "passed": 0, "failed": 0}}

    # 2. EXECUTION layer fail-closed: an unreadable / unparseable manifest is a FAULT, not 'no gates'.
    try:
        raw = mpath.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _refuse(
            f"reality-gates manifest at {manifest} is unreadable / not valid JSON ({exc}) "
            f"-- REFUSED (fail-closed: a broken policy is never a silent PASS).", manifest)

    if not isinstance(data, dict):
        return _refuse(f"reality-gates manifest top level is not an object -- REFUSED (fail-closed).", manifest)

    gates_by_surface = data.get("gates", {})
    if gates_by_surface is None:
        gates_by_surface = {}
    if not isinstance(gates_by_surface, dict):
        return _refuse(f"reality-gates manifest `gates` is not an object -- REFUSED (fail-closed).", manifest)

    # 3. Enforce the surface enum + collect every declared entry (never a silent drop).
    specs: list[dict] = []
    for surface, entries in gates_by_surface.items():
        if surface not in KNOWN_SURFACES:
            return _refuse(
                f"reality-gates manifest declares an unknown surface {surface!r} "
                f"(allowed: {list(KNOWN_SURFACES)}) -- REFUSED (fail-closed; a typo'd surface must "
                f"never silently drop its declared gates).", manifest)
        if not isinstance(entries, list):
            return _refuse(
                f"reality-gates surface {surface!r} is not a list -- REFUSED (fail-closed).", manifest)
        if surface_filter and surface != surface_filter:
            continue
        for e in entries:
            specs.append({
                "id": (e.get("id") if isinstance(e, dict) else None),
                "surface": surface,
                "command": (e.get("command") if isinstance(e, dict) else None),
            })

    # 4. Zero declared (or filtered to none) -> no-op PASS.
    if not specs:
        return {"action": NOOP,
                "reason": (f"reality-gates manifest at {manifest} declares no "
                           f"{('' if not surface_filter else surface_filter + ' ')}gates -- structural no-op."),
                "results": [], "manifest": manifest,
                "summary": {"declared": 0, "passed": 0, "failed": 0}}

    # 5. Run the declared set through the leaf-pure fold (fail-closed per-gate mapping).
    gate_results = gate_runner(specs, repo_root, timeout=timeout)
    rows = [dataclasses.asdict(r) for r in gate_results]
    failed = [r for r in rows if r["status"] == "FAIL"]
    passed = [r for r in rows if r["status"] == "PASS"]
    summary = {"declared": len(rows), "passed": len(passed), "failed": len(failed)}

    if failed:
        names = ", ".join(f"{r['surface']}/{r['gate_id']}({r['subkind']})" for r in failed) or "(see results)"
        return {"action": FAIL,
                "reason": (f"reality-gates FAILED: {len(failed)} of {len(rows)} declared gate(s) tripped "
                           f"[{names}] against {repo_root}. Fix the gate(s) or the code they check."),
                "results": rows, "manifest": manifest, "summary": summary}

    return {"action": PASS,
            "reason": f"reality-gates PASSED: all {len(rows)} declared gate(s) green against {repo_root}.",
            "results": rows, "manifest": manifest, "summary": summary}


# ----------------------------- CLI -----------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reality_gate_runner",
        description="Run a project's DECLARED reality gates (<repo-root>/.aisdlc/reality-gates.json) "
                    "fail-closed. PASS/no-op = exit 0, gate FAIL = exit 1, malformed manifest = exit 3.")
    p.add_argument("--repo-root", required=True,
                   help="the checkout the declared gates run against (REQUIRED -- never ambient cwd; "
                        "an omitted/wrong root must fail LOUD, not silently no-op the declared gates).")
    p.add_argument("--manifest", default=None,
                   help="path to reality-gates.json (default: <repo-root>/.aisdlc/reality-gates.json).")
    p.add_argument("--surface", default=None, choices=KNOWN_SURFACES,
                   help="run only the declared gates for this surface (optional; the wires run the full "
                        "set as a fail-safe superset -- per-surface narrowing is deferred to SC-097).")
    p.add_argument("--timeout", type=float, default=None,
                   help="per-gate timeout in seconds (default: none; a hung gate blocks -- fail-safe).")
    # --json is an accepted no-op: the runner ALWAYS emits its JSON result (machine contract).
    # Without it, argparse would exit 2 on a wire that passes --json and FAIL every gate (M2 / the
    # slice-059 --json dogfood class).
    p.add_argument("--json", action="store_true",
                   help="accepted no-op: the runner always emits JSON (machine contract).")
    return p


def main(argv: list | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)   # argparse exits 2 on a missing --repo-root
    result = run_gate(repo_root=args.repo_root, manifest_path=args.manifest,
                      surface_filter=args.surface, timeout=args.timeout)
    sys.stdout.write(json.dumps(result) + "\n")
    return _exit_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
