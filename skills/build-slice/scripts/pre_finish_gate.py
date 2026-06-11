"""Consolidated /build-slice pre-finish gate (remediation-plan 2.5).

Runs the user-facing pre-finish audits as ONE command and emits a SINGLE JSON
verdict, so the Builder cannot silently skip one of seven separate invocations
(the likeliest real failure of the old hand-run-each-block gate). Each audit's CLI
stays the source of truth for its own checks — this orchestrator subprocess-runs
them with the right arguments and aggregates exit codes. It does NOT re-implement
any check.

NOT included here: the six plugin self-audits (UTF8-STDOUT-1, PCA-1, BCI-1, STP-1,
NAW-1, SVW-1) — those are CI-only (.build/plugin_self_audits.py, remediation-plan 1.5),
never a user's per-slice gate.

Checks (a check SKIPS when its inputs are absent — e.g. TF-1 without --test-first,
LINT-MOCK with no changed test files):

  WT-ROOT-1  wt_root_audit         --worktree <wt>
  DCE-1      drift_check_audit     <slice>
  LINT-MOCK  mock_budget_lint      <changed-test-files> [--seam-allowlist] [--strict]
  WIRE-1     wiring_matrix_audit   <slice>
  BC-1       build_checks_audit    --slice <slice> --changed-files ... --strict --ack-critical ...
  TF-1       test_first_audit      <slice> --strict-pre-finish      (only with --test-first)
  BRANCH-1   branch_workflow_audit <slice>

The BC-1 *enumerate* pass (`--json`, no `--strict`) that lists the applicable Critical
rules for the Builder to attest stays a manual PRE-step — this gate runs BC-1 once, in
strict mode, with the attested `--ack-critical` ids (deduplicates the old double BC-1).

Usage (run from the slice worktree):
    python pre_finish_gate.py --slice <slice-folder> --worktree <wt> \
        [--changed-files a.py b.ts ...] [--changed-test-files t_a.py ...] \
        [--ack-critical BC-PROJ-1,BC-PROJ-3] [--seam-allowlist <path>] \
        [--test-first] [--strict] [--json]

Exit codes:
    0  gate PASS (every non-skipped check passed)
    1  gate FAIL (>=1 check failed)
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent              # skills/build-slice/scripts
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]      # plugin root
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_PY = sys.executable


@dataclass
class CheckResult:
    name: str
    status: str                 # "PASS" | "FAIL" | "SKIP"
    exit_code: int | None = None
    summary: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _run(name: str, argv: list[str], cwd: Path) -> CheckResult:
    try:
        cp = subprocess.run(
            argv, cwd=str(cwd), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        return CheckResult(name=name, status="FAIL", exit_code=None,
                           summary=f"could not run: {exc}", command=argv)
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    summary = out or err or "(no output)"
    # keep the summary compact — first ~6 non-empty lines
    lines = [ln for ln in summary.splitlines() if ln.strip()]
    summary = "\n".join(lines[:6])
    return CheckResult(
        name=name,
        status="PASS" if cp.returncode == 0 else "FAIL",
        exit_code=cp.returncode,
        summary=summary,
        command=argv,
    )


def run_gate(args: argparse.Namespace) -> tuple[str, list[CheckResult]]:
    slice_folder = str(Path(args.slice).resolve())
    worktree = str(Path(args.worktree).resolve())
    cwd = Path(worktree) if Path(worktree).is_dir() else Path.cwd()
    results: list[CheckResult] = []

    # WT-ROOT-1
    results.append(_run(
        "WT-ROOT-1",
        [_PY, str(_LIB / "wt_root_audit.py"), "--worktree", worktree],
        cwd,
    ))

    # DCE-1
    results.append(_run(
        "DCE-1",
        [_PY, str(_SCRIPTS / "drift_check_audit.py"), slice_folder],
        cwd,
    ))

    # LINT-MOCK (skip when no changed test files were passed)
    if args.changed_test_files:
        argv = [_PY, str(_SCRIPTS / "mock_budget_lint.py"), *args.changed_test_files]
        if args.seam_allowlist:
            argv += ["--seam-allowlist", args.seam_allowlist]
        if args.strict:
            argv += ["--strict"]
        results.append(_run("LINT-MOCK", argv, cwd))
    else:
        results.append(CheckResult(name="LINT-MOCK", status="SKIP",
                                   summary="no --changed-test-files supplied"))

    # WIRE-1
    results.append(_run(
        "WIRE-1",
        [_PY, str(_SCRIPTS / "wiring_matrix_audit.py"), slice_folder],
        cwd,
    ))

    # BC-1 (strict, with attested Critical-rule acks)
    bc = [_PY, str(_SCRIPTS / "build_checks_audit.py"),
          "--slice", slice_folder, "--strict"]
    if args.changed_files:
        bc += ["--changed-files", *args.changed_files]
    if args.ack_critical:
        bc += ["--ack-critical", args.ack_critical]
    results.append(_run("BC-1", bc, cwd))

    # TF-1 (only when the slice is test-first)
    if args.test_first:
        results.append(_run(
            "TF-1",
            [_PY, str(_SCRIPTS / "test_first_audit.py"), slice_folder, "--strict-pre-finish"],
            cwd,
        ))
    else:
        results.append(CheckResult(name="TF-1", status="SKIP",
                                   summary="mission-brief test_first != true"))

    # BRANCH-1
    results.append(_run(
        "BRANCH-1",
        [_PY, str(_SCRIPTS / "branch_workflow_audit.py"), slice_folder],
        cwd,
    ))

    gate = "FAIL" if any(r.status == "FAIL" for r in results) else "PASS"
    return gate, results


def _format_human(gate: str, results: list[CheckResult]) -> str:
    out = [f"=== pre-finish gate: {gate} ===\n"]
    for r in results:
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[r.status]
        out.append(f"  [{mark}] {r.name}")
        if r.status == "FAIL":
            for ln in r.summary.splitlines():
                out.append(f"         {ln}")
    if gate == "FAIL":
        out.append("\nFix or escalate the FAIL check(s) above; do not declare the slice done.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    # ASCII-only output here; sub-audits handle their own stdout encoding.
    parser = argparse.ArgumentParser(
        prog="pre_finish_gate",
        description="Consolidated /build-slice pre-finish gate — one command, one verdict (remediation-plan 2.5).",
    )
    parser.add_argument("--slice", required=True, help="active slice folder")
    parser.add_argument("--worktree", required=True, help="slice worktree ($wt)")
    parser.add_argument("--changed-files", nargs="*", default=[],
                        help="files this slice changed (for BC-1)")
    parser.add_argument("--changed-test-files", nargs="*", default=[],
                        help="changed test files (for LINT-MOCK; skipped if empty)")
    parser.add_argument("--ack-critical", default="",
                        help="attested BC-1 Critical-rule ids (comma/space list)")
    parser.add_argument("--seam-allowlist", default=None,
                        help="path to .cross-chunk-seams for LINT-MOCK")
    parser.add_argument("--test-first", action="store_true",
                        help="run TF-1 (mission-brief test_first == true)")
    parser.add_argument("--strict", action="store_true",
                        help="Heavy mode: pass --strict to LINT-MOCK")
    parser.add_argument("--json", action="store_true", help="emit JSON verdict")
    args = parser.parse_args(argv)

    gate, results = run_gate(args)

    if args.json:
        print(json.dumps({
            "gate": gate,
            "checks": [r.to_dict() for r in results],
            "summary": {
                "pass": sum(r.status == "PASS" for r in results),
                "fail": sum(r.status == "FAIL" for r in results),
                "skip": sum(r.status == "SKIP" for r in results),
            },
        }, indent=2))
    else:
        sys.stdout.write(_format_human(gate, results))

    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
