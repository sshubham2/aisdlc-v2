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
  TF-1       brief_variants_audit  <slice> --variant test_first --strict-pre-finish  (only with --test-first)
  BRANCH-1   branch_workflow_audit <slice>
  ARTIFACT-LINT artifact_lint      --dir <slice> --skip-unknown     (3.18.7 schema-by-example)
  STUB-DEAD-1 stub_dead_audit      --worktree <wt> [--base <ref>]   (diff-scoped stub/dead-code)

The BC-1 *enumerate* pass (`--json`, no `--strict`) that lists the applicable Critical
rules for the Builder to attest stays a manual PRE-step — this gate runs BC-1 once, in
strict mode, with the attested `--ack-critical` ids (deduplicates the old double BC-1).

Usage (run from the slice worktree):
    python pre_finish_gate.py --slice <slice-folder> --worktree <wt> \
        [--changed-files a.py b.ts ...] [--changed-test-files t_a.py ...] \
        [--changed-from-git <base>] \
        [--ack-critical BC-PROJ-1,BC-PROJ-3] [--seam-allowlist <path>] \
        [--test-first] [--strict] [--json]

An INVALID --worktree is a HARD exit-2 usage error — the gate REFUSES to audit
anything else (it used to silently fall back to cwd, which could green-light a
main-tree audit of the wrong tree; the guard now lives in the script itself, not
just the SKILL's bash prose).

--changed-from-git <base> derives --changed-files / --changed-test-files INSIDE the
gate (``git -C <worktree> diff --name-only <base>`` + untracked), removing the
cross-bash-block model-memory transcription of the changed list ("the gate's
coverage is exactly as good as this list"). Mutually exclusive with passing the
lists explicitly.

Exit codes:
    0  gate PASS (every non-skipped check passed)
    1  gate FAIL (>=1 check failed)
    2  usage error (incl. an invalid --worktree / a failed --changed-from-git derivation)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent              # skills/build-slice/scripts
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]      # plugin root
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_PY = sys.executable

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402


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


def _split_ack_critical(raw: str | None) -> list[str]:
    """Split a comma/whitespace-separated --ack-critical value into rule-id tokens.

    The attested Critical-rule acks arrive as the documented "comma/space list"
    (e.g. "BC-PROJ-1,BC-PROJ-3"). build_checks_audit declares --ack-critical
    nargs='*' and does set(ack_critical), so it expects ONE token per id. This
    forwards each id as its own token instead of one joined element matching no
    rule id (SC-082 / ADR-036).

    TOTAL by construction: ``(raw or "").replace(",", " ").split()`` returns []
    for None / "" / whitespace-only / comma-only input (str.split() with no args
    splits on runs of whitespace and drops empties), so the empty/None case needs
    no separate guard and the helper never raises — satisfying must-not-defer #1.
    """
    return (raw or "").replace(",", " ").split()


# The project test layout the SKILL documents for --changed-test-files:
# tests/** (any tests/ or test/ path segment), *_test.*, *.test.*, test_*.py-style names.
_TEST_FILE_RE = re.compile(
    r"(^|/)tests?/|_test\.[^/]*$|\.test\.[^/]*$|(^|/)test_[^/]*$"
)


def _derive_changed(worktree: Path, base: str) -> tuple[list[str], list[str]]:
    """Derive (changed_files, changed_test_files) from git inside the gate
    (--changed-from-git): committed diff vs ``base`` plus untracked files.
    Raises ValueError on any git failure — fail-visible, never a silent empty list
    (an empty list would SKIP LINT-MOCK and blind BC-1's scoping)."""
    def _git(*a: str) -> list[str]:
        cp = subprocess.run(
            ["git", "-C", str(worktree), *a], capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        if cp.returncode != 0:
            raise ValueError(
                f"--changed-from-git: `git {' '.join(a)}` failed in {worktree}: "
                f"{(cp.stderr or '').strip()}")
        return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]

    changed = sorted(set(_git("diff", "--name-only", base)
                         + _git("ls-files", "--others", "--exclude-standard")))
    tests = [f for f in changed if _TEST_FILE_RE.search(f.replace("\\", "/"))]
    return changed, tests


def run_gate(args: argparse.Namespace) -> tuple[str, list[CheckResult]]:
    slice_folder = str(Path(args.slice).resolve())
    worktree = str(Path(args.worktree).resolve())
    # Guard IN THE SCRIPT, not just the SKILL's bash prose: a nonexistent worktree used
    # to silently fall back to cwd — a green gate that audited the WRONG tree. main()
    # validates before calling here; this assert is the belt for direct API callers.
    if not Path(worktree).is_dir():
        raise ValueError(f"--worktree {worktree!r} is not a directory — refusing to "
                         f"audit anything else (no cwd fallback)")
    cwd = Path(worktree)
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
    ack_ids = _split_ack_critical(args.ack_critical)
    if ack_ids:
        bc += ["--ack-critical", *ack_ids]
    results.append(_run("BC-1", bc, cwd))

    # TF-1 (only when the slice is test-first)
    if args.test_first:
        results.append(_run(
            "TF-1",
            [_PY, str(_LIB / "brief_variants_audit.py"), slice_folder,
             "--variant", "test_first", "--strict-pre-finish"],
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

    # ARTIFACT-LINT (3.18.7) — schema-by-example over this slice's JSON artifacts
    # (required keys + known enums; --skip-unknown so non-modeled files don't fail).
    results.append(_run(
        "ARTIFACT-LINT",
        [_PY, str(_LIB / "artifact_lint.py"), "--dir", slice_folder, "--skip-unknown"],
        cwd,
    ))

    # REALITY-GATES (slice-062 / SC-095 / ADR-059) — run the project's DECLARED deterministic
    # reality gates (<repo-root>/.aisdlc/reality-gates.json) against the worktree. --repo-root
    # is passed EXPLICITLY (never ambient cwd; the env-lucky wrong-checkout class), and --json
    # is the accepted no-op the runner emits its result on (M2: the exact argv is pinned so a
    # flag mismatch can't exit-2 -> FAIL every slice). Absent/empty manifest -> the runner exits
    # 0 -> PASS (no-op safety); a declared-gate FAIL / malformed manifest -> nonzero -> this
    # check FAILs, which the gate-level FAIL fold below then blocks the finish on.
    results.append(_run(
        "REALITY-GATES",
        [_PY, str(_LIB / "reality_gate_runner.py"), "--repo-root", worktree, "--json"],
        cwd,
    ))

    # ADR-APPEND-1 (SC-019 / ADR-023) — VERIFY ADR append-only via the content-hash baseline.
    # No new --vault flag: derive the decisions dir from the existing --slice arg, since
    # slice_folder == <vault>/slices/slice-NNN, so parents[1] == <vault> (critique M3).
    # SKIP cleanly when the project has no decisions/ dir.
    decisions_dir = Path(slice_folder).resolve().parents[1] / "decisions"
    if decisions_dir.is_dir():
        results.append(_run(
            "ADR-APPEND-1",
            [_PY, str(_LIB / "adr_append_only_audit.py"), "--decisions", str(decisions_dir)],
            cwd,
        ))
    else:
        results.append(CheckResult(name="ADR-APPEND-1", status="SKIP",
                                   summary="no decisions/ dir for this vault"))

    # STUB-DEAD-1 (slice-085 / ADR-099 + ADR-100) — deterministic, diff-scoped stub/dead-code check.
    # Reads the slice diff and BLOCKS a newly-introduced stub body / broad silent-except /
    # unreachable-after-terminal at the exact path:line. Its non-zero exit folds into the all-pass
    # gate verdict below. M-add-3: thread the base the gate already resolved (--changed-from-git)
    # via --base so STUB-DEAD-1 shares the EXACT scope of BC-1/LINT-MOCK instead of re-resolving it;
    # when the caller passed the lists explicitly (no --changed-from-git), the detector self-resolves.
    sd = [_PY, str(_SCRIPTS / "stub_dead_audit.py"), "--worktree", worktree]
    # getattr, not args.changed_from_git: run_gate is called directly with a hand-built Namespace
    # by sibling tests (test_adr_append_only_audit, test_pre_finish_gate_multi_critical_ack_split)
    # that predate this field, so a bare attribute access would AttributeError them. Absent/None ->
    # the detector self-resolves its base (M-add-3 fallback).
    base_ref = getattr(args, "changed_from_git", None)
    if base_ref:
        sd += ["--base", base_ref]
    results.append(_run("STUB-DEAD-1", sd, cwd))

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
    _stdout.reconfigure_stdout_utf8()  # UTF8-STDOUT-1 (sub-audits also handle their own)
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
    parser.add_argument("--changed-from-git", default=None, metavar="BASE",
                        help="derive --changed-files/--changed-test-files INSIDE the gate: "
                             "git diff --name-only BASE + untracked, run in --worktree "
                             "(mutually exclusive with passing the lists explicitly)")
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

    wt = Path(args.worktree).resolve()
    if not wt.is_dir():
        sys.stderr.write(
            f"pre_finish_gate: --worktree {args.worktree!r} does not exist or is not a "
            f"directory — refusing to run the gate (no cwd fallback: a silent main-tree "
            f"audit is a green gate on the WRONG tree).\n")
        return 2

    if args.changed_from_git:
        if args.changed_files or args.changed_test_files:
            sys.stderr.write(
                "pre_finish_gate: --changed-from-git is mutually exclusive with "
                "--changed-files/--changed-test-files — pass one or the other.\n")
            return 2
        try:
            args.changed_files, args.changed_test_files = _derive_changed(
                wt, args.changed_from_git)
        except ValueError as exc:
            sys.stderr.write(f"pre_finish_gate: {exc}\n")
            return 2

    try:
        gate, results = run_gate(args)
    except ValueError as exc:
        sys.stderr.write(f"pre_finish_gate: {exc}\n")
        return 2

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
