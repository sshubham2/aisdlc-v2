"""Walking-skeleton slice audit (WS-1) — v2 JSON.

Reads a slice's `mission-brief.json` and validates:
  - The opt-in flag `variants.walking_skeleton` (v1: `**Walking-skeleton**: true`
    field in the markdown brief) gates the audit
  - When true, an `architectural_layers` array must be present and non-empty
    (a walking skeleton with no layers is meaningless — that's a standard slice)
  - Each layer object carries `layer`, `component`, `verification`, `status`
  - Each layer's `verification` is non-empty
  - Each layer's `status` is one of {pending, exercised}
  - With --strict-pre-finish, any non-`exercised` layer is a violation
    (used at /validate-slice Step 5 WS-1 gate)
  - With --execute (3.1 — "add reality or demote"), each layer's `verification`
    is actually RUN as a command (subprocess, like shippability_runner) so the gate
    touches REALITY instead of trusting a model-written status marker: a non-zero
    exit is a `verification-failed` violation; a `verification` that is prose / not
    runnable degrades to an ADVISORY (we could not reality-check it — fall back to
    the marker, never a hard fail). --execute implies --strict-pre-finish.

Per WS-1. The walking-skeleton discipline (Cockburn): the smallest possible
end-to-end implementation that exercises every architectural layer.

Default-off semantics: a brief without `variants.walking_skeleton: true` is
unaffected. WS-1 is opt-in per slice.

**v2 change from v1.** The brief is JSON, not markdown. The boolean flag is
`variants.walking_skeleton` (was the `**Walking-skeleton**: true` field line);
the 5-column markdown table `# | Layer | Component | Verification | Status`
becomes the `architectural_layers[]` array of objects. Statuses are lowercase
JSON tokens (`pending` / `exercised`) rather than UPPER markdown cells. The NFR-1
mtime carry-over exemption is REMOVED (3.9 — it was dead for every post-install
user; `--no-carry-over` is accepted as a no-op for CLI compat). Audit semantics,
violation kinds, exit codes, and `--strict-pre-finish` are otherwise preserved.

v2 brief shape (the relevant fields of `mission-brief.json`):

    {
      "variants": {"walking_skeleton": true, ...},
      "architectural_layers": [
        {"layer": "API", "component": "routes.py",
         "verification": "curl -sf localhost:8000/health", "status": "exercised"}
      ]
    }

    With --execute (3.1), `verification` is RUN as a command — write it as a runnable
    machine command (like a shippability machine_cmd), not a prose sentence, so the gate
    can touch reality. A prose verification degrades to a non-gating advisory.

Usage:
    python walking_skeleton_audit.py <slice-folder>
    python walking_skeleton_audit.py <mission-brief.json>
    python walking_skeleton_audit.py <slice-folder> --strict-pre-finish
    python walking_skeleton_audit.py <slice-folder> --json
    python walking_skeleton_audit.py <slice-folder> --no-carry-over

Exit codes:
    0  clean (or default-off / carry-over exempt)
    1  violations
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

# Allowed statuses (lowercase JSON tokens; v1 markdown cells were UPPER).
_ALLOWED_STATUSES: frozenset[str] = frozenset({"pending", "exercised"})

_EMPTY_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)"})


@dataclass(frozen=True)
class LayerRow:
    index: str       # the layer's 1-based position (free-form string)
    layer: str       # name of the architectural layer
    component: str   # the component / file / module touched
    verification: str  # how this layer's exercise is verified
    status: str      # "pending" | "exercised"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WSViolation:
    path: str
    layer_index: str  # "" for section-level errors
    kind: str         # "missing-section" | "empty-table" | "missing-verification" |
                      # "invalid-status" | "format" | "non-exercised-pre-finish"
    severity: str     # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    walking_skeleton_enabled: bool = False
    rows: list[LayerRow] = field(default_factory=list)
    violations: list[WSViolation] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)   # 3.1: non-gating "could not reality-check" notes
    executions: list[dict] = field(default_factory=list)  # 3.1: per-layer --execute results

    def to_dict(self) -> dict:
        return {
            "walking_skeleton_enabled": self.walking_skeleton_enabled,
            "rows": [r.to_dict() for r in self.rows],
            "violations": [v.to_dict() for v in self.violations],
            "advisories": list(self.advisories),
            "executions": list(self.executions),
            "summary": {
                "row_count": len(self.rows),
                "by_status": {
                    s: sum(1 for r in self.rows if r.status == s)
                    for s in _ALLOWED_STATUSES
                },
                "violation_count": len(self.violations),
                "advisory_count": len(self.advisories),
            },
        }


def _cell_is_empty(cell: str) -> bool:
    return cell.strip().lower() in _EMPTY_SENTINELS


def audit_brief_file(
    brief_path: Path,
    strict_pre_finish: bool = False,
) -> AuditResult:
    """Audit a mission-brief.json against WS-1."""
    result = AuditResult()

    if not brief_path.exists():
        return result

    try:
        data = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message=f"mission-brief.json is not readable/valid JSON: {exc}",
        ))
        return result
    if not isinstance(data, dict):
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message="mission-brief.json top level is not a JSON object.",
        ))
        return result

    variants = data.get("variants") if isinstance(data.get("variants"), dict) else {}
    enabled = bool(variants.get("walking_skeleton", False))
    result.walking_skeleton_enabled = enabled

    if not enabled:
        return result  # default-off; nothing else to check

    layers = data.get("architectural_layers")
    if layers is None:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="missing-section",
            severity="Important",
            message=(
                "`variants.walking_skeleton` is true but no "
                "`architectural_layers` array was found. Per WS-1, when "
                "walking-skeleton is enabled the brief must list every "
                "architectural layer the slice touches end-to-end."
            ),
        ))
        return result

    if not isinstance(layers, list):
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="format",
            severity="Important",
            message="`architectural_layers` is not a JSON array.",
        ))
        return result

    if not layers:
        result.violations.append(WSViolation(
            path=str(brief_path), layer_index="", kind="empty-table",
            severity="Important",
            message=(
                "`architectural_layers` has no entries. A walking skeleton "
                "with zero layers is meaningless — that's a standard slice. "
                "Per WS-1, list every architectural layer the slice touches."
            ),
        ))
        return result

    for idx, entry in enumerate(layers, start=1):
        index_cell = str(idx)
        if not isinstance(entry, dict):
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell, kind="format",
                severity="Important",
                message=f"layer {idx} is not a JSON object.",
            ))
            continue

        layer = str(entry.get("layer", "")).strip()
        component = str(entry.get("component", "")).strip()
        verification = str(entry.get("verification", "")).strip()
        status_raw = str(entry.get("status", "")).strip()

        if not any(str(v).strip() for v in entry.values()):  # BB-26: skip a WHOLLY-empty entry (index_cell is a synthetic counter, never empty)
            continue

        if _cell_is_empty(verification):
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell,
                kind="missing-verification", severity="Important",
                message=(
                    f"layer {idx} ('{layer}'): `verification` is empty. Per "
                    f"WS-1, every layer must declare HOW its exercise is "
                    f"verified at runtime."
                ),
            ))
            continue

        status = status_raw.lower().strip()
        if status not in _ALLOWED_STATUSES:
            result.violations.append(WSViolation(
                path=str(brief_path), layer_index=index_cell,
                kind="invalid-status", severity="Important",
                message=(
                    f"layer {idx} ('{layer}'): status '{status_raw}' not in "
                    f"{sorted(_ALLOWED_STATUSES)}."
                ),
            ))
            continue

        result.rows.append(LayerRow(
            index=index_cell, layer=layer, component=component,
            verification=verification, status=status,
        ))

    if strict_pre_finish:
        for row in result.rows:
            if row.status != "exercised":
                result.violations.append(WSViolation(
                    path=str(brief_path), layer_index=row.index,
                    kind="non-exercised-pre-finish", severity="Important",
                    message=(
                        f"layer '{row.layer}' status is {row.status}; "
                        f"--strict-pre-finish requires exercised. The "
                        f"walking-skeleton hasn't actually reached this layer "
                        f"yet — fix the implementation or remove "
                        f"--strict-pre-finish (only used at /validate-slice "
                        f"WS-1 gate)."
                    ),
                ))

    return result


def execute_verifications(
    result: AuditResult,
    repo_root: Path,
    brief_path_str: str,
    timeout: float | None = 120.0,
) -> None:
    """3.1 — actually RUN each layer's `verification` as a command (reality contact).

    Mirrors shippability_runner: split on `;`, strip backticks per segment, ``shlex.split``,
    ``subprocess.run`` with utf-8/replace. A layer is reality-verified iff every segment of
    its verification exits 0. Outcomes per layer:
      - all segments exit 0       -> execution {verified: true}; no violation.
      - a segment exits non-zero  -> execution {verified: false} + a ``verification-failed``
                                     VIOLATION (reality said no — the gate fails).
      - verification not runnable -> execution {verified: null} + an ADVISORY (we could NOT
        (empty / shlex error /       reality-check it — fall back to the status marker, NEVER
         command not found)          a hard fail; "add reality OR demote").
    Only runs over rows that passed structural validation (``result.rows``).
    """
    for row in result.rows:
        segments = [s.strip().strip("`").strip() for s in row.verification.split(";")]
        segments = [s for s in segments if s]
        if not segments:
            result.advisories.append(
                f"layer '{row.layer}': verification is empty after parsing — cannot "
                f"reality-check; falling back to the status marker ({row.status})."
            )
            result.executions.append(
                {"layer": row.layer, "verified": None, "reason": "empty-verification"})
            continue

        ok, runnable, detail = True, True, ""
        for seg in segments:
            try:
                argv = shlex.split(seg, posix=True)
            except ValueError as exc:
                runnable = False
                detail = f"verification not parseable as a command ({exc}): {seg!r}"
                break
            if not argv:
                continue
            try:
                proc = subprocess.run(
                    argv, cwd=str(repo_root),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=timeout,
                )
            except FileNotFoundError:
                runnable = False
                detail = (f"verification command not found (looks like prose, not a "
                          f"runnable command): {seg!r}")
                break
            except (OSError, subprocess.TimeoutExpired) as exc:
                ok, detail = False, f"verification could not complete ({exc}): {seg!r}"
                break
            if proc.returncode != 0:
                tail = ((proc.stdout or "")[-300:] + (proc.stderr or "")[-300:]).strip()
                ok, detail = False, f"verification exited {proc.returncode}: {seg!r}\n{tail}"
                break

        if not runnable:
            # Could not reach reality for this layer -> ADVISORY, not a violation (demote).
            result.advisories.append(f"layer '{row.layer}': {detail}")
            result.executions.append(
                {"layer": row.layer, "verified": None, "reason": "not-runnable", "detail": detail})
        elif ok:
            result.executions.append({"layer": row.layer, "verified": True})
        else:
            result.violations.append(WSViolation(
                path=brief_path_str, layer_index=row.index, kind="verification-failed",
                severity="Important",
                message=(f"layer '{row.layer}': REALITY check failed — {detail}. WS-1 "
                         f"--execute ran the verification and it did not pass."),
            ))
            result.executions.append(
                {"layer": row.layer, "verified": False, "detail": detail})


def _format_human(result: AuditResult) -> str:
    if not result.walking_skeleton_enabled:
        return (
            "Walking-skeleton audit: not enabled "
            "(`variants.walking_skeleton` absent or false).\n"
        )

    if not result.violations:
        by_status = {
            s: sum(1 for r in result.rows if r.status == s)
            for s in _ALLOWED_STATUSES
        }
        verified = sum(1 for e in result.executions if e.get("verified") is True)
        exec_note = (
            f" reality-verified={verified}/{len(result.executions)} (--execute)."
            if result.executions else ""
        )
        head = (
            f"Walking-skeleton audit: clean. {len(result.rows)} layer(s) — "
            f"exercised={by_status['exercised']}, pending={by_status['pending']}.{exec_note}\n"
        )
        if result.advisories:
            head += (f"{len(result.advisories)} advisory(ies) — could not reality-check "
                     f"(non-gating):\n" + "".join(f"  - {a}\n" for a in result.advisories))
        return head

    out: list[str] = [
        f"{len(result.violations)} walking-skeleton violation(s):\n\n"
    ]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind}) "
            f"{f'layer #{v.layer_index}' if v.layer_index else ''}\n"
            f"    {v.message}\n\n"
        )
    if result.advisories:
        out.append(
            f"{len(result.advisories)} advisory(ies) — could not reality-check "
            f"(non-gating; fell back to the status marker):\n"
        )
        out.extend(f"  - {a}\n" for a in result.advisories)
        out.append("\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="walking_skeleton_audit",
        description="WS-1 walking-skeleton slice variant audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds mission-brief.json inside) OR a mission-brief.json file",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--strict-pre-finish", action="store_true",
        help="Refuse non-exercised layers (use at /validate-slice WS-1 gate)",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="3.1: actually RUN each layer's verification command (reality contact). "
             "Implies --strict-pre-finish. Non-zero exit -> violation; prose/unrunnable "
             "verification -> advisory (degrade, never a hard fail).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path("."),
        help="Working directory for --execute verification commands (default: cwd)",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0,
        help="Per-segment timeout (seconds) for --execute verification commands",
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted for v1 CLI compatibility; no-op (the NFR-1 mtime carry-over "
             "exemption was removed in 3.9).",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    brief_path = target / "mission-brief.json" if target.is_dir() else target

    result = audit_brief_file(
        brief_path,
        strict_pre_finish=args.strict_pre_finish or args.execute,
    )

    # 3.1 reality contact: run the verification commands over structurally-valid rows.
    if args.execute and result.walking_skeleton_enabled and result.rows:
        execute_verifications(
            result, repo_root=args.repo_root.resolve(),
            brief_path_str=str(brief_path), timeout=args.timeout,
        )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
