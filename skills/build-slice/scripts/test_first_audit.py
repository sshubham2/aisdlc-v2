"""Test-first slice audit (TF-1) — v2 JSON.

Reads a slice's ``mission-brief.json`` and validates:
  - The opt-in flag ``variants.test_first`` (bool) is recognized
  - When true, a ``test_first_plan`` array must exist with rows of
    ``{ac, test_type, test_path, test_function, status}``
  - Every AC declared in ``acceptance_criteria[].id`` must have >=1 test-first row
  - Each row's ``status`` is one of {PENDING, WRITTEN-FAILING, PASSING}
  - With --strict-pre-finish, any non-PASSING row is a violation (/build-slice Step 6)
  - With --strict-pre-finish, any PASSING row whose ``test_path`` file does not
    exist on disk is a violation (PTFCD-1; kind=missing-test-path-file)
  - With --strict-pre-finish, a PASSING row whose cited ``test_function`` is absent
    from an existing+parseable file is a violation (PTFFD-1; missing-test-function)

Per TF-1 (methodology-changelog.md v0.13.0). Opt-in TDD discipline.

**v2 changes from v1.**
- ``mission-brief.md`` (markdown `**Test-first**:` field + `## Test-first plan`
  5-column table + numbered AC list) -> ``mission-brief.json``. The flag is
  ``variants.test_first`` (bool); ACs are ``acceptance_criteria[].id``; the plan
  is the ``test_first_plan`` array. All markdown-table format checks (heading,
  separator-row, column-count, malformed-field detection) DISSOLVE — a JSON bool
  cannot be "malformed", and a JSON array has no separator row.
- NFR-1 mtime carry-over is GONE (keyed on a v1 ``mission-brief.md`` file + fixed
  v1 release date that no longer apply). ``--no-carry-over`` is accepted (no-op)
  for CLI compatibility.
- The PTFCD-1 / PTFFD-1 on-disk path + function existence checks survive, using
  the shared ``scripts.lib._pyfn`` helper. Test paths resolve against ``--root``
  (default cwd) — the slice folder lives in the external vault, so a ``.git``
  walk-up cannot reach the code worktree where the tests live.

Usage:
    python test_first_audit.py <slice-folder>
    python test_first_audit.py <mission-brief.json>
    python test_first_audit.py <slice-folder> --strict-pre-finish
    python test_first_audit.py <slice-folder> --root <code-worktree> --json

Exit codes:
    0  clean (or test-first=false)
    1  violations
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import asdict, dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _pyfn, _stdout  # noqa: E402

_ALLOWED_STATUSES: frozenset[str] = frozenset({"PENDING", "WRITTEN-FAILING", "PASSING"})
_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class TestFirstRow:
    ac: str
    test_type: str
    test_path: str
    test_function: str
    status: str
    index: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TestFirstViolation:
    path: str
    row_index: int    # 1-based row index; 0 for section-level
    ac: str
    kind: str         # "missing-section" | "invalid-status" | "ac-without-row" |
                      # "format" | "non-passing-pre-finish" | "missing-cells" |
                      # "missing-test-path-file" | "missing-test-function"
    severity: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    test_first_enabled: bool = False
    acs_in_brief: list[str] = field(default_factory=list)
    rows: list[TestFirstRow] = field(default_factory=list)
    violations: list[TestFirstViolation] = field(default_factory=list)
    skip_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "test_first_enabled": self.test_first_enabled,
            "acs_in_brief": list(self.acs_in_brief),
            "rows": [r.to_dict() for r in self.rows],
            "violations": [v.to_dict() for v in self.violations],
            "skip_notes": list(self.skip_notes),
            "summary": {
                "row_count": len(self.rows),
                "by_status": {
                    s: sum(1 for r in self.rows if r.status == s)
                    for s in _ALLOWED_STATUSES
                },
                "violation_count": len(self.violations),
            },
        }


def _detect_test_first_flag(data: dict) -> bool:
    """Return True iff ``variants.test_first`` is truthy (bool or 'true' string)."""
    variants = data.get("variants")
    val = variants.get("test_first") if isinstance(variants, dict) else data.get("test_first")
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() == "true"


def _find_acs(data: dict) -> list[str]:
    """Return AC labels from ``acceptance_criteria[].id`` (normalized: 'AC1' -> '1')."""
    acs = data.get("acceptance_criteria")
    out: list[str] = []
    if isinstance(acs, list):
        for entry in acs:
            if isinstance(entry, dict):
                raw = entry.get("id") or entry.get("ac") or ""
            else:
                raw = entry
            norm = _normalize_ac_label(str(raw))
            if norm:
                out.append(norm)
    return out


def _normalize_ac_label(raw: str) -> str:
    """Normalize AC labels: 'AC#1', 'AC 1', 'AC-1', 'ac1', '1' all -> '1'. The 'ac' prefix
    is stripped only when LEADING (then any '-'/'_' separator), NOT anywhere in the string —
    so text labels like 'place-order' / 'backend-auth' are not corrupted to 'ple-order'/'bkend-auth'."""
    s = raw.strip().lower().replace("#", "").replace(" ", "")
    if s.startswith("ac"):
        s = s[2:].lstrip("-_")
    return s


def _resolve_test_path(test_path: str, repo_root: Path) -> Path | None:
    """Resolve a TF-1 row test_path to an on-disk Path, or None to skip."""
    raw = test_path.strip()
    if raw.lower() in _EMPTY_SENTINELS:
        return None
    raw = raw.strip("`").strip()
    raw = raw.split("::", 1)[0].strip()
    if not raw or raw.lower() in _EMPTY_SENTINELS:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


def audit_brief_file(
    brief_path: Path,
    strict_pre_finish: bool = False,
    repo_root: Path | None = None,
) -> AuditResult:
    """Audit a mission-brief.json against TF-1."""
    result = AuditResult()

    if not brief_path.exists():
        # Missing brief is silent — TF-1 is opt-in; absence isn't a violation.
        return result

    try:
        text = brief_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.violations.append(TestFirstViolation(
            path=str(brief_path), row_index=0, ac="",
            kind="format", severity="Important",
            message=f"cannot read mission-brief.json: {exc}",
        ))
        return result
    if not isinstance(data, dict):
        result.violations.append(TestFirstViolation(
            path=str(brief_path), row_index=0, ac="",
            kind="format", severity="Important",
            message="mission-brief.json top-level is not a JSON object.",
        ))
        return result

    enabled = _detect_test_first_flag(data)
    result.test_first_enabled = enabled
    result.acs_in_brief = _find_acs(data)

    if not enabled:
        return result  # default-off — clean

    plan = data.get("test_first_plan")
    if plan is None:
        result.violations.append(TestFirstViolation(
            path=str(brief_path), row_index=0, ac="",
            kind="missing-section", severity="Important",
            message=(
                "`variants.test_first` is true but no `test_first_plan` array was found. "
                "Per TF-1, when test-first is enabled the brief must include rows of "
                "{ac, test_type, test_path, test_function, status} mapping every AC to its tests."
            ),
        ))
        return result
    if not isinstance(plan, list):
        result.violations.append(TestFirstViolation(
            path=str(brief_path), row_index=0, ac="",
            kind="format", severity="Important",
            message="`test_first_plan` is not a JSON array.",
        ))
        return result

    rows_by_ac: dict[str, list[TestFirstRow]] = {}
    for idx, raw in enumerate(plan, start=1):
        if not isinstance(raw, dict):
            result.violations.append(TestFirstViolation(
                path=str(brief_path), row_index=idx, ac="",
                kind="missing-cells", severity="Important",
                message=f"test_first_plan[{idx - 1}] is not a JSON object.",
            ))
            continue
        ac_raw = str(raw.get("ac", "")).strip()
        if ac_raw.lower() in _EMPTY_SENTINELS:
            result.violations.append(TestFirstViolation(
                path=str(brief_path), row_index=idx, ac="",
                kind="missing-cells", severity="Important",
                message=f"test_first_plan row {idx}: `ac` is empty.",
            ))
            continue
        ac_norm = _normalize_ac_label(ac_raw)
        status = str(raw.get("status", "")).upper().strip()

        if status not in _ALLOWED_STATUSES:
            result.violations.append(TestFirstViolation(
                path=str(brief_path), row_index=idx, ac=ac_norm,
                kind="invalid-status", severity="Important",
                message=(
                    f"row for AC#{ac_norm}: status '{raw.get('status')}' not in "
                    f"{sorted(_ALLOWED_STATUSES)}."
                ),
            ))
            continue

        row = TestFirstRow(
            ac=ac_norm,
            test_type=str(raw.get("test_type", "")),
            test_path=str(raw.get("test_path", "")),
            test_function=str(raw.get("test_function", "")),
            status=status,
            index=idx,
        )
        result.rows.append(row)
        rows_by_ac.setdefault(ac_norm, []).append(row)

    # Every AC in the brief must have at least one row.
    for ac in result.acs_in_brief:
        if ac not in rows_by_ac:
            result.violations.append(TestFirstViolation(
                path=str(brief_path), row_index=0, ac=ac,
                kind="ac-without-row", severity="Important",
                message=(
                    f"AC#{ac} is declared in the brief but has no test-first row. "
                    f"Per TF-1, every AC must map to at least one test."
                ),
            ))

    if strict_pre_finish:
        for row in result.rows:
            if row.status != "PASSING":
                result.violations.append(TestFirstViolation(
                    path=str(brief_path), row_index=row.index, ac=row.ac,
                    kind="non-passing-pre-finish", severity="Important",
                    message=(
                        f"row for AC#{row.ac} ({row.test_function}) status is "
                        f"{row.status}; --strict-pre-finish requires PASSING."
                    ),
                ))

        root = (repo_root or Path.cwd()).resolve()
        for row in result.rows:
            if row.status != "PASSING":
                continue
            resolved = _resolve_test_path(row.test_path, root)
            if resolved is None:
                continue
            if not resolved.exists():
                result.violations.append(TestFirstViolation(
                    path=str(brief_path), row_index=row.index, ac=row.ac,
                    kind="missing-test-path-file", severity="Important",
                    message=(
                        f"row for AC#{row.ac} ({row.test_function}) is PASSING but its "
                        f"test_path '{row.test_path}' resolves to '{resolved}', which does "
                        f"not exist on disk. PTFCD-1: a PASSING row may not cite a phantom "
                        f"test file. (Pass the code worktree root via --root if the path is "
                        f"repo-root-relative.)"
                    ),
                ))
                continue

            # PTFFD-1: verify the cited test function exists.
            fn_name: str | None = None
            if _pyfn.is_checkable_function_name(row.test_function):
                fn_name = _pyfn.selector_terminal_name(row.test_function)
            if fn_name is None:
                fn_name = _pyfn.selector_terminal_name(row.test_path)
            if fn_name is None:
                continue
            verdict = _pyfn.function_defined_in_file(resolved, fn_name)
            if verdict is False:
                result.violations.append(TestFirstViolation(
                    path=str(brief_path), row_index=row.index, ac=row.ac,
                    kind="missing-test-function", severity="Important",
                    message=(
                        f"row for AC#{row.ac} is PASSING and its test_path '{row.test_path}' "
                        f"resolves to an existing file '{resolved}', but no test function "
                        f"'{fn_name}' is defined in it. PTFFD-1: a PASSING row may not cite a "
                        f"phantom test function."
                    ),
                ))
            elif verdict is None:
                result.skip_notes.append(
                    f"AC#{row.ac} ({brief_path}:row {row.index}): function-check skipped "
                    f"(file unparseable) for '{fn_name}' in '{resolved}'."
                )

    return result


def _format_human(result: AuditResult) -> str:
    if not result.test_first_enabled and not result.violations:
        return "Test-first audit: not enabled (`variants.test_first` is false/absent).\n"

    skip_block = ""
    if result.skip_notes:
        skip_block = (
            f"\n{len(result.skip_notes)} function-check skip-note(s) "
            f"(PTFFD-1; no violation — ADR-037 skip-with-note):\n"
            + "".join(f"  - {n}\n" for n in result.skip_notes)
        )

    if not result.violations:
        by_status = {s: sum(1 for r in result.rows if r.status == s) for s in _ALLOWED_STATUSES}
        return (
            f"Test-first audit: clean. {len(result.rows)} row(s) — "
            f"PASSING={by_status['PASSING']}, "
            f"WRITTEN-FAILING={by_status['WRITTEN-FAILING']}, "
            f"PENDING={by_status['PENDING']}.\n" + skip_block
        )

    out: list[str] = [f"{len(result.violations)} test-first violation(s):\n\n"]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} row {v.row_index} ({v.kind}) "
            f"{f'AC#{v.ac}' if v.ac else ''}\n    {v.message}\n\n"
        )
    out.append(skip_block)
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="test_first_audit",
        description="TF-1 test-first slice variant audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds mission-brief.json inside) OR a mission-brief.json file",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--strict-pre-finish", action="store_true",
        help="Refuse non-PASSING rows (use at /build-slice Step 6)",
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Code worktree root for resolving repo-relative test paths (default: cwd).",
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted for v1 CLI compatibility; no-op in v2 (no mtime carry-over).",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    if not target.exists():  # BB-24: a mistyped path / wrong slice folder is a usage error (exit 2), not a clean pass
        sys.stderr.write(f"test_first_audit: target does not exist: {target}\n")
        return 2
    brief_path = target / "mission-brief.json" if target.is_dir() else target

    result = audit_brief_file(
        brief_path,
        strict_pre_finish=args.strict_pre_finish,
        repo_root=args.root,
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
