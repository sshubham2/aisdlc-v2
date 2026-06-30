"""brief_variants_audit.py — the three mission-brief variant audits, merged (3.7).

ONE parameterizable audit replacing `test_first_audit.py` (TF-1) +
`walking_skeleton_audit.py` (WS-1) + `exploratory_charter_audit.py` (ETC-1). They are
one shape:

    variants.<flag> gate -> a rows array -> per-row required field + status enum ->
    --strict-pre-finish refuses non-terminal rows -> exit 0 (clean/off) / 1 / 2 (usage).

A declarative `VariantSpec` table (`SPECS`) captures the differences; the genuinely
special logic is expressed as hooks:
  - TF-1: AC-coverage cross-check (every `acceptance_criteria[].id` needs a row) + the
    PTFCD-1 / PTFFD-1 on-disk `test_path` + `test_function` checks (`--root`, via
    `scripts.lib._pyfn`).
  - WS-1: `--execute` actually RUNS each layer's `verification` as a command (reality
    contact, 3.1) — non-zero exit = `verification-failed` violation; prose / unrunnable
    verification degrades to a non-gating advisory.
  - ETC-1: `findings` is a status-conditional required field (completed/deferred need it).

Normalizations applied while merging (the plan's "normalize the status-case drift"):
  - status case folds per-variant: TF UPPER {PENDING, WRITTEN-FAILING, PASSING}; WS/ETC
    lower. A string flag is parsed by VALUE (so a literal `"false"` is off, not truthy).
  - a missing TARGET is a fail-visible usage error (exit 2) for ALL variants (was TF-only;
    WS/ETC silently exited 0). Real call sites always pass an existing slice, so this only
    bites a mistyped path. A missing brief INSIDE an existing slice = variant N/A (exit 0).
  - a non-dict row is kind `format` for all variants (TF previously said `missing-cells`).

Invoked as a SHARED tool (>1 skill) — by absolute path off `${CLAUDE_SKILL_DIR}`:
    $PY ".../scripts/lib/brief_variants_audit.py" <slice|brief> --variant <name> [flags]
      test_first          /build-slice pre-finish   (--strict-pre-finish [--root <wt>])
      walking_skeleton    /validate-slice WS-1 gate  (--execute --repo-root <wt>)
      exploratory_charter /validate-slice ETC-1 gate (--strict-pre-finish)

Exit codes:
    0  clean (or variant default-off)
    1  violations
    2  usage error (target missing, unknown --variant)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_REPO = pathlib.Path(__file__).resolve().parents[2]  # scripts/lib/X.py -> plugin root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _pyfn, _stdout  # noqa: E402
# slice-047/ADR-038: the WS-1 checker now shares the mature execution core instead
# of re-implementing the loop inline, and gains a STATIC portability gate.
from scripts.lib.runnable_command import NON_PORTABLE_CONSOLE_SCRIPT, classify  # noqa: E402
from scripts.lib.verification_core import _segments, run_verification  # noqa: E402

_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class VariantSpec:
    name: str
    flag_key: str                                  # variants.<flag_key>
    array_key: str                                 # the rows array field
    noun: str                                      # "row" | "layer" | "charter"
    statuses: frozenset[str]                        # allowed (already case-folded)
    strict_accepted: frozenset[str]                # terminal statuses under --strict-pre-finish
    case: str                                      # "upper" | "lower" status folding
    required_field: str                            # the per-row field that must be non-empty
    required_kind: str                             # violation kind when it is empty
    non_terminal_kind: str                         # violation kind under --strict-pre-finish
    top_level_flag_fallback: bool = False          # TF: also honour a top-level <flag>
    skip_empty_rows: bool = False                  # WS/ETC: skip a WHOLLY-empty entry (BB-26)
    findings_field: str = ""                       # ETC: status-conditional required field
    findings_required_when: frozenset[str] = frozenset()


SPECS: dict[str, VariantSpec] = {
    "test_first": VariantSpec(
        name="test_first", flag_key="test_first", array_key="test_first_plan", noun="row",
        statuses=frozenset({"PENDING", "WRITTEN-FAILING", "PASSING"}),
        strict_accepted=frozenset({"PASSING"}), case="upper",
        required_field="ac", required_kind="missing-cells",
        non_terminal_kind="non-passing-pre-finish",
        top_level_flag_fallback=True, skip_empty_rows=False,
    ),
    "walking_skeleton": VariantSpec(
        name="walking_skeleton", flag_key="walking_skeleton",
        array_key="architectural_layers", noun="layer",
        statuses=frozenset({"pending", "exercised"}),
        strict_accepted=frozenset({"exercised"}), case="lower",
        required_field="verification", required_kind="missing-verification",
        non_terminal_kind="non-exercised-pre-finish", skip_empty_rows=True,
    ),
    "exploratory_charter": VariantSpec(
        name="exploratory_charter", flag_key="exploratory_charter",
        array_key="exploratory_charters", noun="charter",
        statuses=frozenset({"pending", "in-progress", "completed", "deferred"}),
        strict_accepted=frozenset({"completed", "deferred"}), case="lower",
        required_field="mission", required_kind="missing-mission",
        non_terminal_kind="non-final-pre-finish", skip_empty_rows=True,
        findings_field="findings", findings_required_when=frozenset({"completed", "deferred"}),
    ),
}


@dataclass
class Violation:
    path: str
    index: str          # "" for section-level
    kind: str
    severity: str
    message: str

    def to_dict(self) -> dict:
        return {"path": self.path, "index": self.index, "kind": self.kind,
                "severity": self.severity, "message": self.message}


@dataclass
class Row:
    index: str
    status: str         # case-normalized
    entry: dict

    def to_dict(self) -> dict:
        return {**self.entry, "index": self.index, "status": self.status}


@dataclass
class AuditResult:
    variant: str
    enabled: bool = False
    rows: list = field(default_factory=list)        # list[Row]
    violations: list = field(default_factory=list)  # list[Violation]
    advisories: list = field(default_factory=list)  # WS --execute non-gating notes
    executions: list = field(default_factory=list)  # WS --execute per-layer results
    skip_notes: list = field(default_factory=list)  # TF PTFFD unparseable-file notes

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "enabled": self.enabled,
            "rows": [r.to_dict() for r in self.rows],
            "violations": [v.to_dict() for v in self.violations],
            "advisories": list(self.advisories),
            "executions": list(self.executions),
            "skip_notes": list(self.skip_notes),
            "summary": {
                "row_count": len(self.rows),
                "violation_count": len(self.violations),
                "advisory_count": len(self.advisories),
            },
        }


def _empty(cell) -> bool:
    return str(cell).strip().lower() in _EMPTY_SENTINELS


def _flag_enabled(data: dict, spec: VariantSpec) -> bool:
    variants = data.get("variants") if isinstance(data.get("variants"), dict) else {}
    val = variants.get(spec.flag_key)
    if val is None and spec.top_level_flag_fallback:
        val = data.get(spec.flag_key)
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() == "true"


def _normalize_ac_label(raw: str) -> str:
    """'AC#1' / 'AC 1' / 'AC-1' / 'ac1' / '1' -> '1'; a text label ('place-order') is
    preserved (the 'ac' strip is LEADING-only)."""
    s = raw.strip().lower().replace("#", "").replace(" ", "")
    if s.startswith("ac"):
        s = s[2:].lstrip("-_")
    return s


def _resolve_test_path(test_path: str, root: Path) -> Path | None:
    """Resolve a TF-1 row test_path to an on-disk Path, or None to skip."""
    raw = test_path.strip()
    if raw.lower() in _EMPTY_SENTINELS:
        return None
    raw = raw.strip("`").strip().split("::", 1)[0].strip()
    if not raw or raw.lower() in _EMPTY_SENTINELS:
        return None
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (root / candidate)


def audit(
    brief_path: Path,
    spec: VariantSpec,
    *,
    strict: bool = False,
    root: Path | None = None,
    execute: bool = False,
    timeout: float = 120.0,
) -> AuditResult:
    """Audit a mission-brief.json against one variant spec. Caller maps a missing TARGET
    to exit 2; a missing brief in an existing slice is variant-N/A (clean)."""
    result = AuditResult(variant=spec.name)

    if not brief_path.exists():
        return result
    try:
        text = brief_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.violations.append(Violation(str(brief_path), "", "format", "Important",
            f"mission-brief.json is not readable/valid JSON: {exc}"))
        return result
    if not isinstance(data, dict):
        result.violations.append(Violation(str(brief_path), "", "format", "Important",
            "mission-brief.json top level is not a JSON object."))
        return result

    result.enabled = _flag_enabled(data, spec)
    if not result.enabled:
        return result  # default-off — clean

    arr = data.get(spec.array_key)
    if arr is None:
        result.violations.append(Violation(str(brief_path), "", "missing-section", "Important",
            f"`variants.{spec.flag_key}` is true but no `{spec.array_key}` array was found. "
            f"Per {spec.name}, the brief must list its {spec.noun}(s)."))
        return result
    if not isinstance(arr, list):
        result.violations.append(Violation(str(brief_path), "", "format", "Important",
            f"`{spec.array_key}` is not a JSON array."))
        return result
    if not arr:
        result.violations.append(Violation(str(brief_path), "", "empty-table", "Important",
            f"`{spec.array_key}` has no entries — list at least one {spec.noun} or set "
            f"`variants.{spec.flag_key}` to false."))
        return result

    for idx, entry in enumerate(arr, start=1):
        index_cell = str(idx)
        if not isinstance(entry, dict):
            result.violations.append(Violation(str(brief_path), index_cell, "format", "Important",
                f"{spec.noun} {idx} is not a JSON object."))
            continue
        if spec.skip_empty_rows and not any(str(v).strip() for v in entry.values()):
            continue  # BB-26: a wholly-empty entry is skipped (the index is synthetic)
        if _empty(entry.get(spec.required_field, "")):
            result.violations.append(Violation(str(brief_path), index_cell, spec.required_kind,
                "Important", f"{spec.noun} {idx}: `{spec.required_field}` is empty."))
            continue
        status_raw = str(entry.get("status", "")).strip()
        status = status_raw.upper() if spec.case == "upper" else status_raw.lower()
        if status not in spec.statuses:
            result.violations.append(Violation(str(brief_path), index_cell, "invalid-status",
                "Important", f"{spec.noun} {idx}: status '{status_raw}' not in {sorted(spec.statuses)}."))
            continue
        if (spec.findings_field and status in spec.findings_required_when
                and _empty(entry.get(spec.findings_field, ""))):
            result.violations.append(Violation(str(brief_path), index_cell, "missing-findings",
                "Important", f"{spec.noun} {idx}: status '{status}' requires non-empty "
                f"`{spec.findings_field}` (a completed/deferred charter without captured "
                f"findings/rationale defeats the discipline)."))
            continue
        result.rows.append(Row(index=index_cell, status=status, entry=entry))

    # ── variant hook: TF-1 AC-coverage cross-check (every declared AC needs a row) ──
    if spec.name == "test_first":
        _hook_ac_coverage(result, data, brief_path)

    # ── --strict-pre-finish: non-terminal status is a violation (all variants) ──
    if strict:
        for row in result.rows:
            if row.status not in spec.strict_accepted:
                result.violations.append(Violation(str(brief_path), row.index, spec.non_terminal_kind,
                    "Important", f"{spec.noun} {row.index} status is {row.status}; "
                    f"--strict-pre-finish requires {' or '.join(sorted(spec.strict_accepted))}."))
        # ── variant hook: TF-1 PTFCD-1 / PTFFD-1 on-disk test path + function checks ──
        if spec.name == "test_first":
            _hook_test_first_disk(result, brief_path, (root or Path.cwd()))

    # ── variant hook: WS-1 --execute reality run (3.1) ──
    if execute and spec.name == "walking_skeleton" and result.rows:
        # slice-047/ADR-038 (M3): the STATIC portability gate fires WITHIN the
        # --execute path, BEFORE the runtime run, so the repro's direct
        # audit(execute=True) triggers it. It does NOT fire on a non-execute
        # --strict-pre-finish call (a non-portable verification is caught at
        # /validate-slice, not earlier).
        _hook_ws_portability(result, str(brief_path))
        _execute_verifications(result, (root or Path(".")).resolve(), str(brief_path), timeout)

    return result


def _hook_ac_coverage(result: AuditResult, data: dict, brief_path: Path) -> None:
    acs = data.get("acceptance_criteria")
    declared: list[str] = []
    if isinstance(acs, list):
        for e in acs:
            raw = (e.get("id") or e.get("ac") or "") if isinstance(e, dict) else e
            norm = _normalize_ac_label(str(raw))
            if norm:
                declared.append(norm)
    covered = {_normalize_ac_label(str(r.entry.get("ac", ""))) for r in result.rows}
    for ac in declared:
        if ac not in covered:
            result.violations.append(Violation(str(brief_path), "", "ac-without-row", "Important",
                f"AC#{ac} is declared in the brief but has no test-first row. Per TF-1, every "
                f"AC must map to at least one test."))


def _hook_test_first_disk(result: AuditResult, brief_path: Path, root: Path) -> None:
    root = Path(root).resolve()
    for row in result.rows:
        if row.status != "PASSING":
            continue
        test_path = str(row.entry.get("test_path", ""))
        resolved = _resolve_test_path(test_path, root)
        if resolved is None:
            continue
        ac = _normalize_ac_label(str(row.entry.get("ac", "")))
        if not resolved.exists():
            result.violations.append(Violation(str(brief_path), row.index, "missing-test-path-file",
                "Important", f"AC#{ac}: PASSING row test_path '{test_path}' resolves to '{resolved}', "
                f"which does not exist on disk. PTFCD-1: a PASSING row may not cite a phantom test "
                f"file. (Pass the code worktree via --root if the path is repo-root-relative.)"))
            continue
        # PTFFD-1: the cited test function must exist in the (parseable) file.
        fn: str | None = None
        if _pyfn.is_checkable_function_name(str(row.entry.get("test_function", ""))):
            fn = _pyfn.selector_terminal_name(str(row.entry.get("test_function", "")))
        if fn is None:
            fn = _pyfn.selector_terminal_name(test_path)
        if fn is None:
            continue
        verdict = _pyfn.function_defined_in_file(resolved, fn)
        if verdict is False:
            result.violations.append(Violation(str(brief_path), row.index, "missing-test-function",
                "Important", f"AC#{ac}: PASSING row test_path '{test_path}' resolves to an existing "
                f"file but no test function '{fn}' is defined in it. PTFFD-1: a PASSING row may not "
                f"cite a phantom test function."))
        elif verdict is None:
            result.skip_notes.append(
                f"AC#{ac} (row {row.index}): function-check skipped (file unparseable) for "
                f"'{fn}' in '{resolved}'.")


def _hook_ws_portability(result: AuditResult, brief_path_str: str) -> None:
    """STATIC WS-1 portability gate (slice-047/ADR-038, B1+m1). Classify EACH
    `;`-segment of every layer's `verification` via runnable_command.classify and
    flag the layer when a segment is NON_PORTABLE_CONSOLE_SCRIPT -- a bare
    `pytest tests/...` that depends on the ambient PATH and should be interpreter-
    anchored. Decided BEFORE/independent of execution.

    Scoped DELIBERATELY to non_portable_console_script ONLY (B1): architectural_
    layers verifications are an OPEN domain (curl/node/docker/`python --version`
    all classify as not_a_command), so gating not_a_command would cry-wolf on
    every legit non-pytest smoke command. The open not_a_command class is governed
    at RUNTIME instead (decidable-wrong -> STOP, undecidable not-runnable -> loud
    advisory). NOT a general 'is this command portable?' gate."""
    for row in result.rows:
        layer = str(row.entry.get("layer", ""))
        verification = str(row.entry.get("verification", "")).strip()
        exercised = row.status == "exercised"  # CR1: symmetric with the runtime pending policy
        for seg in _segments(verification):  # m1: per top-level segment
            if classify(seg).klass != NON_PORTABLE_CONSOLE_SCRIPT:
                continue
            if exercised:
                result.violations.append(Violation(
                    brief_path_str, row.index, "non-portable-verification", "Important",
                    f"layer '{layer}': non-portable verification {seg!r} depends on the ambient "
                    f"PATH (bare `pytest`); use the interpreter-anchored `<interp> -m pytest ...` "
                    f"form so the WS-1 check runs regardless of PATH."))
            else:
                # CR1: a pending layer makes no reality claim -> a non-gating advisory,
                # never a hard STOP (the static gate now agrees with the runtime policy).
                result.advisories.append(
                    f"layer '{layer}' (pending): non-portable verification {seg!r} depends on the "
                    f"ambient PATH (bare `pytest`) — anchor it (`<interp> -m pytest ...`) before "
                    f"marking the layer 'exercised'.")


def _execute_verifications(result: AuditResult, repo_root: Path, brief_path_str: str,
                           timeout: float | None) -> None:
    """WS-1 3.1 (slice-047/ADR-038): RUN each layer's `verification` through the
    SHARED fail-closed core (verification_core.run_verification) and apply the
    M-add-1 option-(a) gating policy on an EXERCISED layer:
      * PASS                       -> verified=True
      * ABSENT (cited test absent) -> STOP (a layer claiming reality contact whose
                                      cited test is not on this checkout did not
                                      exercise anything -- decidable)
      * FAIL, decidable-wrong      -> STOP (exited-nonzero / unparseable / timeout
                                      / exec-error)
      * FAIL, subkind not-runnable -> LOUD, LOGGED advisory, NOT a hard STOP. A
                                      command-not-found is genuinely UNDECIDABLE (a
                                      prose phantom and a missing foreign tool look
                                      identical), so blocking it would false-fail a
                                      legit env-dependent skeleton.
    A `pending` layer makes no reality claim -> nothing is gating (informational).
    The blanket advisory-demote the old immature loop applied to EVERY not-runnable
    case (the M2 wrong-side failure) is gone -- decidable failures now STOP."""
    for row in result.rows:
        layer = str(row.entry.get("layer", ""))
        verification = str(row.entry.get("verification", "")).strip()
        exercised = row.status == "exercised"
        if not verification:
            result.advisories.append(
                f"layer '{layer}': verification is empty — cannot reality-check; "
                f"falling back to the status marker ({row.status}).")
            result.executions.append({"layer": layer, "verified": None, "reason": "empty-verification"})
            continue

        verdict = run_verification(verification, repo_root, timeout=timeout)

        if verdict.status == "PASS":
            result.executions.append({"layer": layer, "verified": True})
            continue

        if verdict.status == "ABSENT":
            if exercised:
                result.violations.append(Violation(
                    brief_path_str, row.index, "verification-absent", "Important",
                    f"layer '{layer}': marked 'exercised' but its verification cites a test absent "
                    f"on this checkout — {verdict.reason}. It cannot have exercised the layer."))
                result.executions.append(
                    {"layer": layer, "verified": False, "reason": "absent-tests", "detail": verdict.reason})
            else:
                result.advisories.append(f"layer '{layer}' (pending): {verdict.reason}")
                result.executions.append(
                    {"layer": layer, "verified": None, "reason": "absent-tests", "detail": verdict.reason})
            continue

        # verdict.status == "FAIL"
        if verdict.subkind == "not-runnable":
            # UNDECIDABLE (M-add-1 a): a LOUD advisory, never a hard STOP.
            result.advisories.append(
                f"layer '{layer}': NOT-RUNNABLE (loud advisory, NOT a STOP — a prose phantom and a "
                f"missing tool are indistinguishable from the command string) — {verdict.reason}")
            result.executions.append(
                {"layer": layer, "verified": None, "reason": "not-runnable", "detail": verdict.reason})
            continue

        # decidable-wrong FAIL (exited-nonzero / unparseable / timeout / exec-error)
        if exercised:
            result.violations.append(Violation(
                brief_path_str, row.index, "verification-failed", "Important",
                f"layer '{layer}': REALITY check failed — {verdict.reason}. WS-1 --execute ran "
                f"the verification and it did not pass."))
            result.executions.append({"layer": layer, "verified": False, "detail": verdict.reason})
        else:
            result.advisories.append(f"layer '{layer}' (pending): {verdict.reason}")
            result.executions.append(
                {"layer": layer, "verified": None, "reason": verdict.subkind, "detail": verdict.reason})


def _format_human(result: AuditResult, spec: VariantSpec) -> str:
    if not result.enabled:
        return f"{spec.name} audit: not enabled (`variants.{spec.flag_key}` absent or false).\n"
    lines: list[str] = []
    if not result.violations:
        head = f"{spec.name} audit: clean. {len(result.rows)} {spec.noun}(s)."
        if result.executions:
            verified = sum(1 for e in result.executions if e.get("verified") is True)
            head += f" reality-verified={verified}/{len(result.executions)} (--execute)."
        lines.append(head)
    else:
        lines.append(f"{len(result.violations)} {spec.name} violation(s):")
        for v in result.violations:
            tag = f"{spec.noun} #{v.index}" if v.index else ""
            lines.append(f"  [{v.severity}] ({v.kind}) {tag} {v.message}")
    if result.advisories:
        lines.append(f"{len(result.advisories)} advisory(ies) — could not reality-check (non-gating):")
        lines.extend(f"  - {a}" for a in result.advisories)
    if result.skip_notes:
        lines.extend(f"  (skip-note) {n}" for n in result.skip_notes)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="brief_variants_audit",
        description="Merged mission-brief variant audit (TF-1 / WS-1 / ETC-1) — 3.7.")
    p.add_argument("target", type=Path,
                   help="Slice folder (auto-finds mission-brief.json inside) OR a mission-brief.json file")
    p.add_argument("--variant", required=True, choices=sorted(SPECS),
                   help="which variant audit to run")
    p.add_argument("--strict-pre-finish", action="store_true",
                   help="refuse non-terminal rows (the pre-finish / validate-slice gate)")
    p.add_argument("--execute", action="store_true",
                   help="WS-1 only: RUN each layer's verification (reality contact, 3.1). Implies "
                        "--strict-pre-finish. Non-zero exit -> violation; prose/unrunnable -> advisory.")
    p.add_argument("--root", "--repo-root", dest="root", type=Path, default=Path("."),
                   help="code worktree root: TF-1 test-path resolution / WS-1 --execute cwd (default: cwd)")
    p.add_argument("--timeout", type=float, default=120.0,
                   help="WS-1 --execute per-segment timeout in seconds (default 120)")
    p.add_argument("--no-carry-over", action="store_true",
                   help="accepted for v1 CLI compatibility; no-op (no mtime carry-over in v2)")
    p.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = p.parse_args(argv)

    spec = SPECS[args.variant]
    target: Path = args.target
    if not target.exists():  # fail-visible: a mistyped path / wrong slice is a usage error, not a clean pass
        sys.stderr.write(f"brief_variants_audit: target does not exist: {target}\n")
        return 2
    brief_path = target / "mission-brief.json" if target.is_dir() else target

    result = audit(
        brief_path, spec,
        strict=args.strict_pre_finish or (args.execute and args.variant == "walking_skeleton"),
        root=args.root, execute=args.execute, timeout=args.timeout,
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result, spec))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
