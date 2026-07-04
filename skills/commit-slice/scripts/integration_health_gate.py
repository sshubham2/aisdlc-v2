"""integration_health_gate.py -- the pre-merge INTEGRATION-HEALTH gate for
/commit-slice --merge (slice-059 / SC-093 / ADR-056).

The gap it closes ("integration is the leak"): a slice can pass its whole
per-slice loop on its own branch while the integration branch (aisdlc-uat; legacy
uat in an ai-sdlc-managed repo) is red from a sibling's already-landed break, and
nothing re-runs the full suite against the POST-REBASE integration-branch tip
before the local merge advances it. This gate is that re-run, wired into
`commit-slice/SKILL.md` sub-step 2.7 -- AFTER the 2.5 rebase onto the integration
branch and BEFORE the step-3 `git checkout <integration>; git merge --no-ff`.
Because the merge is strictly downstream, a REFUSE here means the merge never
runs, so the integration branch is left untouched BY PLACEMENT (no git rollback
to get wrong).

It is --merge-only (M-add-1): --push pushes the slice branch + opens a
remote-auto-merge PR and does NOT advance the integration branch locally, so its integration-health
enforcement is the CI required-check (front a of SC-093, out of scope), not this
local gate.

Shape mirrors the house actuator `local_branch_delete.py`:

  * **DECIDE/ACTUATE split.** `run_gate` is a pure decide-core that takes the
    injected `runner` (the seam that invokes the suite runner), maps the runner's
    verdict to an action, and returns a result dict. The CLI binds the real runner
    to a `subprocess.run` of `shippability_runner.py --json`; tests inject a fake.
  * **REUSE, don't re-implement (AC3).** The suite is run by the EXISTING
    `skills/validate-slice/scripts/shippability_runner.py` (`run_catalog` ->
    `scripts/lib/verification_core.run_verification`) -- the SAME check
    /validate-slice Step 6 runs. This gate contains NO suite-running logic; it
    subprocess-invokes that runner (a direct import is impossible: each skill's
    scripts/ is on sys.path only for that skill).
  * **FAIL-CLOSED / DENY BY DEFAULT (Saltzer & Schroeder).** failed==0 -> PROCEED;
    failed>=1 -> REFUSE (names the rows); a runner that cannot even run (missing
    runner, unreadable catalog, crash, unparseable output) -> REFUSE-UNRUNNABLE. A
    safety check that cannot run must never quietly wave a merge through.
  * **ABSENT is not a failure.** verification_core classifies a row whose cited
    tests/*.py are all absent on the checkout as ABSENT (a sibling's not-yet-merged
    repro) -- `run_catalog` never counts it toward `failed`, so it never REFUSEs.
  * **Explicit, reason-required, logged override (jidoka/andon).** The ONLY bypass
    is `--skip-integration-health <reason>`. An empty/whitespace reason is REJECTED
    as a usage error (m4) so the reason-required deny-by-default cannot decay into a
    bare-flag bypass. The override SHORT-CIRCUITS the run (it bypasses the
    run_catalog call) and returns OVERRIDDEN; the SKILL logs the reason.
  * **Structured result for the SKILL (M-add-2).** PROCEED and OVERRIDDEN both exit
    0, so exit code alone cannot distinguish them. The gate emits a JSON result
    {action, reason, failing_rows, ...}; the sub-step-2.7 wiring parses `action` at
    exit 0 to LOG an override + surface a loud warning, never exit-code-only.
  * **Explicit target, not ambient cwd.** The runner is invoked with an explicit
    `--repo-root <post-rebase worktree>` (the additive flag slice-059 adds to the
    runner CLI), so the gate deterministically tests the right checkout instead of
    guessing from inherited cwd (the env-lucky wrong-checkout class this project's
    reflections keep getting bitten by).
  * **No per-segment timeout by default (m5).** timeout=None matches
    /validate-slice Step 6 -- an aggressive timeout would false-REFUSE a
    slow-but-passing row (fail-SAFE, but a needless merge-blocker).

Exit codes (CLI): 0 = PROCEED **or** OVERRIDDEN (distinguish via the JSON `action`) ·
1 = REFUSE (>=1 FAIL row; named) · 3 = REFUSE-UNRUNNABLE (fail-closed: runner
missing / catalog unreadable / crash / unparseable) · 2 = usage (bad args, or an
empty/whitespace --skip-integration-health reason).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

# --- single-skill import bootstrap (mirror local_branch_delete.py) ---
_HERE = Path(__file__).resolve().parent                 # <plugin>/skills/commit-slice/scripts
_REPO = _HERE.parents[2]                                 # -> <plugin>
for _p in (str(_HERE), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.lib import _stdout  # noqa: E402

# m3 (critique): resolve the sibling-skill runner path via the HOUSE convention --
# _HERE.parents[1] is <plugin>/skills (NOT parents[1] off __file__, which would be
# <plugin>/skills/commit-slice). A wrong path is fail-closed anyway (runner-not-found
# -> REFUSE-UNRUNNABLE) and is pinned by a test asserting this resolved path exists.
_DEFAULT_RUNNER = _HERE.parents[1] / "validate-slice" / "scripts" / "shippability_runner.py"

Runner = Callable[[list], subprocess.CompletedProcess]

# Result actions
PROCEED = "proceed"
REFUSE = "refuse"
REFUSE_UNRUNNABLE = "refuse-unrunnable"
OVERRIDDEN = "overridden"


def _exit_for(result: dict) -> int:
    """Map a run_gate result dict to the CLI exit code (single contract home).

    The SKILL keys on ANY non-zero exit -> STOP before the step-3 merge; at exit 0
    it parses `action` to tell PROCEED from OVERRIDDEN (M-add-2)."""
    action = result.get("action")
    if action in (PROCEED, OVERRIDDEN):
        return 0
    if action == REFUSE:
        return 1
    if action == REFUSE_UNRUNNABLE:
        return 3
    return 2


def _failing_rows(payload: dict) -> list:
    """Extract the FAIL rows from a shippability RunResult --json payload."""
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("status") == "FAIL":
            out.append({"row": r.get("row"), "index": r.get("index"),
                        "detail": (r.get("detail") or "")[:500]})
    return out


def run_gate(
    *,
    runner: Runner,
    catalog: str,
    repo_root: str,
    runner_path: str | Path = _DEFAULT_RUNNER,
    override: bool = False,
    override_reason: str | None = None,
) -> dict:
    """Decide the pre-merge integration-health verdict. Returns a result dict with
    ``action`` in {proceed, refuse, refuse-unrunnable, overridden}, plus
    ``reason``, ``failing_rows``, ``overridden``, ``override_reason`` and
    ``evidence`` (the runner's counts).

    Deny-by-default: anything that is not a clean all-green run REFUSEs; a runner
    that cannot run REFUSEs-UNRUNNABLE. The override SHORT-CIRCUITS the run (it
    bypasses the run_catalog call) -- caller guarantees a non-empty reason (the CLI
    rejects an empty one before we get here)."""
    # 1. Override short-circuits the run entirely (design: bypasses the run_catalog
    #    call). The reason is guaranteed non-empty by the CLI (m4).
    if override:
        return {
            "action": OVERRIDDEN,
            "reason": f"integration-health gate OVERRIDDEN by explicit --skip-integration-health: {override_reason}",
            "failing_rows": [],
            "overridden": True,
            "override_reason": override_reason,
            "evidence": {},
        }

    # 2. Fail-closed if the runner itself is absent (before spending a subprocess).
    if not Path(runner_path).is_file():
        return {
            "action": REFUSE_UNRUNNABLE,
            "reason": (
                f"integration-health gate CANNOT RUN: suite runner not found at {runner_path}. "
                f"A safety check that cannot run must not wave the merge through -- REFUSED "
                f"(fail-closed). Bypass only with --skip-integration-health <reason>."
            ),
            "failing_rows": [],
            "overridden": False,
            "override_reason": None,
            "evidence": {},
        }

    # 3. Run the EXISTING catalog runner against the EXPLICIT repo_root (the
    #    post-rebase worktree). No --timeout -> timeout=None, matching validate Step 6 (m5).
    argv = [sys.executable, str(runner_path), str(catalog), "--repo-root", str(repo_root), "--json"]
    proc = runner(argv)
    rc = proc.returncode
    raw = proc.stdout or ""

    # 4. rc 2 (usage: catalog missing/unreadable/invalid) or any unexpected non-{0,1}
    #    exit -> fail-closed. (rc 2 is ALSO what a missing runner script yields, so
    #    this covers runner-crash + catalog-missing uniformly.)
    if rc not in (0, 1):
        tail = (raw[-300:] + (proc.stderr or "")[-300:]).strip()
        return {
            "action": REFUSE_UNRUNNABLE,
            "reason": (
                f"integration-health gate CANNOT RUN (suite runner exited {rc}): the full suite could "
                f"not be evaluated against the post-rebase state -- REFUSED (fail-closed). {tail}"
            ),
            "failing_rows": [],
            "overridden": False,
            "override_reason": None,
            "evidence": {},
        }

    # 5. rc in {0,1}: parse the RunResult. An unparseable payload is itself unrunnable.
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {
            "action": REFUSE_UNRUNNABLE,
            "reason": (
                "integration-health gate CANNOT RUN: the suite runner did not return parseable JSON "
                "-- REFUSED (fail-closed)."
            ),
            "failing_rows": [],
            "overridden": False,
            "override_reason": None,
            "evidence": {},
        }

    evidence = {
        "rows_run": payload.get("rows_run"),
        "passed": payload.get("passed"),
        "failed": payload.get("failed"),
        "absent": payload.get("absent"),
    }
    failed = payload.get("failed", 0) or 0
    # Trust the parsed count over the exit code (belt-and-suspenders): any FAIL -> REFUSE.
    if failed or rc == 1:
        failing = _failing_rows(payload)
        names = ", ".join(str(r["row"]) for r in failing) or "(see report)"
        return {
            "action": REFUSE,
            "reason": (
                f"integration-health gate REFUSED the merge: {failed} shippability catalog row(s) FAIL "
                f"against the post-rebase integration-branch tip [{names}]. The integration branch is untouched (the merge did not run). "
                f"Fix the regression, or bypass with --skip-integration-health <reason>."
            ),
            "failing_rows": failing,
            "overridden": False,
            "override_reason": None,
            "evidence": evidence,
        }

    return {
        "action": PROCEED,
        "reason": (
            f"integration-health gate PASSED: full suite green against the post-rebase integration-branch tip "
            f"({evidence.get('passed')} passed, {evidence.get('absent')} absent/not-on-checkout, 0 failed)."
        ),
        "failing_rows": [],
        "overridden": False,
        "override_reason": None,
        "evidence": evidence,
    }


# ----------------------------- CLI -----------------------------


def _real_runner() -> Runner:
    def run(argv):
        return subprocess.run([str(a) for a in argv], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    return run


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="integration_health_gate",
        description="Pre-merge integration-health gate for /commit-slice --merge: re-run the full "
                    "shippability catalog against the post-rebase integration-branch tip and REFUSE the merge on red "
                    "(fail-closed; reuses shippability_runner). --merge-only.",
    )
    p.add_argument("--repo-root", required=True,
                   help="the post-rebase slice worktree the suite runs against (the about-to-merge state)")
    p.add_argument("--catalog", default=None,
                   help="path to shippability.json (default: <vault>/shippability.json)")
    p.add_argument("--runner", default=None,
                   help="path to shippability_runner.py (default: the sibling validate-slice script; "
                        "override for hermetic tests)")
    p.add_argument("--skip-integration-health", dest="skip_reason", default=None,
                   help="EXPLICIT, reason-required override: bypass the gate. An empty/whitespace reason "
                        "is rejected (usage error). The reason is logged by the SKILL.")
    # The gate ALWAYS emits a JSON result (it is a machine tool the SKILL parses). --json is accepted as a
    # harmless no-op for caller-convention parity with shippability_runner (and because the SKILL 2.7 wiring
    # passes it) -- without it, argparse would exit 2 on the SKILL invocation and the gate would REFUSE every
    # merge with its own usage error (the slice-059 merge-time dogfood catch).
    p.add_argument("--json", action="store_true",
                   help="accepted no-op: the gate always emits JSON (machine contract).")
    return p


def _default_catalog() -> str:
    # Lazy import so a missing/odd vault never breaks --catalog-explicit callers.
    from scripts.lib._vault_paths import VAULT_ROOT
    return str(Path(VAULT_ROOT) / "shippability.json")


def main(argv: list | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    # m4: reason-required override -- an empty/whitespace reason is a usage error, NOT an override.
    override = args.skip_reason is not None
    if override and not args.skip_reason.strip():
        sys.stderr.write(
            "integration_health_gate: --skip-integration-health requires a NON-EMPTY reason "
            "(deny-by-default: a blank reason is not an override).\n")
        return 2

    catalog = args.catalog or _default_catalog()
    runner_path = args.runner or _DEFAULT_RUNNER

    result = run_gate(
        runner=_real_runner(),
        catalog=catalog,
        repo_root=args.repo_root,
        runner_path=runner_path,
        override=override,
        override_reason=args.skip_reason,
    )
    sys.stdout.write(json.dumps(result) + "\n")
    return _exit_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
