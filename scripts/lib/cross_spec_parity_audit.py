"""Cross-spec parity audit (CSP-1) — v2 JSON.

Walks the Heavy-mode artifacts (`<vault>/threat-model.json`,
`<vault>/requirements.json`, `<vault>/non-functional.json`) and validates
that each item is structurally well-formed AND its cross-reference
(`implementation_ref` for threats/requirements, `verification_ref` for NFRs)
points to a real file under the project root.

v1 parsed H2-structured markdown (`architecture/threat-model.md`,
`requirements.md`, `nfrs.md`) and read a single `**Implementation**:` /
`**Verification**:` field line per item. v2 loads JSON arrays and reads a
dedicated cross-reference field per item. The validation rules, status
vocabulary, path-resolution semantics, CLI surface, and exit codes are
preserved verbatim.

Per CSP-1 (methodology-changelog.md v0.18.0). The rule's purpose:
keep human-authored Heavy artifacts in parity with code-derived
facts. Threats / requirements / NFRs that claim mitigations or
verifications must reference paths that actually exist; otherwise
the artifact is decoration, not discipline.

Heavy-mode-only. In Minimal / Standard mode the artifacts don't
exist (or triage declares a non-Heavy mode); the audit returns clean (no-op).

v2 item shapes (schema-by-example `skills/*/examples/{threat-model,requirements,
non-functional}.json`):

    threat-model.json   { "threats": [ {id, status, implementation_ref, ...} ] }
    requirements.json   { "items":   [ {id, status, implementation_ref, ...} ] }
    non-functional.json { "nfrs":    [ {id, status, verification_ref,    ...} ] }

The cross-reference field, by artifact:
  - threats:       `implementation_ref`  (TM-*)
  - requirements:  `implementation_ref`  (REQ-*)
  - NFRs:          `verification_ref`     (NFR-*)

Status vocabulary (KEPT from v1):
  - Threats:       mitigated | accepted | open
  - Requirements:  planned | implemented | pending | deferred
  - NFRs:          met | unmet | unverified

Statuses that REQUIRE a non-empty file path:
  - mitigated, implemented, met

Statuses that ACCEPT an empty / `null` / `n/a` cross-reference:
  - accepted, open, pending, deferred, unmet, unverified

Path resolution: the cross-reference value is parsed as `<file>:<func>` (or just
`<file>`). The file part is resolved relative to --root (default: cwd).
Existence of the file is verified. Function names within the file are NOT
verified (unchanged from v1).

Usage:
    python -m scripts.lib.cross_spec_parity_audit
    python -m scripts.lib.cross_spec_parity_audit --root /path/to/project
    python -m scripts.lib.cross_spec_parity_audit --threats <p> --requirements <p> --nfrs <p>
    python -m scripts.lib.cross_spec_parity_audit --json

Exit codes:
    0  clean (or non-Heavy / no artifacts found)
    1  violations
    2  usage error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON
# hooks/MCP). Shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" ...`, which puts scripts/lib
# (not the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib import
# ...` resolves, mirroring the single-skill parents[3] bootstrap. No-op under `-m`.
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

# Status vocabulary by artifact prefix (v1 sets + the v2 `planned` REQ state used
# by the v2 requirements.json example; `planned` is not-yet-done so it does NOT
# require an implementation_ref).
_STATUS_BY_PREFIX: dict[str, frozenset[str]] = {
    "TM": frozenset({"mitigated", "accepted", "open"}),
    "REQ": frozenset({"planned", "implemented", "pending", "deferred"}),
    "NFR": frozenset({"met", "unmet", "unverified"}),
}

# Statuses that REQUIRE a non-empty cross-reference path (KEPT from v1).
_REQUIRES_PATH: frozenset[str] = frozenset({"mitigated", "implemented", "met"})

# Sentinel string values treated as "no path provided" (KEPT from v1; `null`
# JSON values are handled separately in `_ref_value`).
_PATH_SENTINELS = frozenset({"", "—", "-", "n/a", "none", "(none)", "tbd"})

# The JSON field holding the cross-reference, by prefix. v1 read a single
# `Implementation` (TM/REQ) / `Verification` (NFR) markdown field; v2 reads the
# dedicated `*_ref` JSON field, matching the example schemas.
_REF_FIELD_BY_PREFIX: dict[str, str] = {
    "TM": "implementation_ref",
    "REQ": "implementation_ref",
    "NFR": "verification_ref",
}

# v2 artifact descriptor: (prefix, vault-relative filename, JSON array key).
# The array key differs per artifact (`threats` / `items` / `nfrs`) — taken from
# the example schemas.
_ARTIFACTS: dict[str, tuple[str, str]] = {
    "TM": ("threat-model.json", "threats"),
    "REQ": ("requirements.json", "items"),
    "NFR": ("non-functional.json", "nfrs"),
}


@dataclass(frozen=True)
class CSPItem:
    artifact: str       # "threat-model.json" | "requirements.json" | "non-functional.json"
    item_id: str        # "TM-1", "REQ-7", "NFR-3"
    prefix: str         # "TM" | "REQ" | "NFR"
    title: str
    status: str
    ref_field: str      # "implementation_ref" | "verification_ref"
    ref_value: str      # file:func form or sentinel
    index: int          # position in the JSON array

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CSPViolation:
    artifact: str
    index: int          # JSON-array index; -1 for artifact-level errors
    item_id: str        # "" for artifact-level errors
    kind: str           # "missing-field" | "invalid-status" | "broken-ref" |
                        # "missing-ref" | "format"
    severity: str       # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    items: list[CSPItem] = field(default_factory=list)
    violations: list[CSPViolation] = field(default_factory=list)
    artifacts_scanned: list[str] = field(default_factory=list)
    heavy_mode: bool = False  # whether we detected Heavy mode

    def to_dict(self) -> dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "violations": [v.to_dict() for v in self.violations],
            "artifacts_scanned": list(self.artifacts_scanned),
            "heavy_mode": self.heavy_mode,
            "summary": {
                "item_count": len(self.items),
                "violation_count": len(self.violations),
                "by_prefix": {
                    p: sum(1 for i in self.items if i.prefix == p)
                    for p in ("TM", "REQ", "NFR")
                },
            },
        }


def _detect_heavy_mode(root: Path) -> bool:
    """True if `<vault>/triage.json` declares `mode: heavy` (case-insensitive).

    v1 grepped `architecture/triage.md` for `**Mode**: Heavy`; v2 loads the JSON
    and reads the top-level `mode` field (`triage.json` example uses lowercase
    `"standard"` / would use `"heavy"`). A missing/unreadable/malformed triage
    file → not Heavy (silent no-op), matching v1's missing-file behavior.
    """
    triage = root / VAULT_ROOT / "triage.json"  # VAULT_ROOT-routed
    if not triage.exists():
        return False
    try:
        text = triage.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return str(data.get("mode", "")).strip().lower() == "heavy"


def _normalize_id(raw: str) -> str:
    """Normalize 'TM-01' / 'TM01' / 'tm-1' -> 'TM-1'."""
    m = re.match(r"^(TM|REQ|NFR)-?(\d+)$", raw.strip(), re.IGNORECASE)
    if not m:
        return raw.strip()
    prefix = m.group(1).upper()
    num = int(m.group(2))
    return f"{prefix}-{num}"


def _ref_value(entry: dict, ref_field_name: str) -> str:
    """Extract the cross-reference value as a string. A JSON `null` (or absent
    key) → empty string (the requirements example uses `null` for unset refs)."""
    raw = entry.get(ref_field_name)
    if raw is None:
        return ""
    return str(raw).strip()


def _is_empty_path(value: str) -> bool:
    return value.strip().lower() in _PATH_SENTINELS


def _parse_artifact(
    path: Path,
    project_root: Path,
    prefix: str,
    array_key: str,
) -> tuple[list[CSPItem], list[CSPViolation]]:
    """Parse + validate a v2 Heavy JSON artifact."""
    items: list[CSPItem] = []
    violations: list[CSPViolation] = []

    if not path.exists():
        return items, violations  # silent — artifact may not exist yet

    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError) as exc:
        violations.append(CSPViolation(
            artifact=str(path), index=-1, item_id="",
            kind="format", severity="Important",
            message=f"cannot read artifact: {exc}",
        ))
        return items, violations
    except json.JSONDecodeError as exc:
        violations.append(CSPViolation(
            artifact=str(path), index=-1, item_id="",
            kind="format", severity="Important",
            message=f"{path.name} is not valid JSON: {exc}",
        ))
        return items, violations

    raw = data.get(array_key) if isinstance(data, dict) else None
    if raw is None:
        return items, violations  # no array key -> empty artifact (silent)
    if not isinstance(raw, list):
        violations.append(CSPViolation(
            artifact=str(path), index=-1, item_id="",
            kind="format", severity="Important",
            message=f"`{array_key}` is not a JSON array.",
        ))
        return items, violations

    ref_field_name = _REF_FIELD_BY_PREFIX[prefix]
    allowed_statuses = _STATUS_BY_PREFIX[prefix]

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            violations.append(CSPViolation(
                artifact=str(path), index=idx, item_id="",
                kind="format", severity="Important",
                message=f"{array_key}[{idx}] is not a JSON object.",
            ))
            continue

        item_id = _normalize_id(str(entry.get("id", "")))
        title = str(entry.get("threat") or entry.get("statement")
                    or entry.get("target") or entry.get("title") or "").strip()

        # Validate Status presence
        status_raw = entry.get("status")
        if status_raw is None or not str(status_raw).strip():
            violations.append(CSPViolation(
                artifact=str(path), index=idx, item_id=item_id,
                kind="missing-field", severity="Important",
                message=(
                    f"item {item_id or '?'}: missing required `status` field. "
                    f"Allowed values for {prefix}: "
                    f"{sorted(allowed_statuses)}."
                ),
            ))
            continue

        status = str(status_raw).strip().lower()
        if status not in allowed_statuses:
            violations.append(CSPViolation(
                artifact=str(path), index=idx, item_id=item_id,
                kind="invalid-status", severity="Important",
                message=(
                    f"item {item_id or '?'}: status '{status}' not in "
                    f"{sorted(allowed_statuses)}."
                ),
            ))
            continue

        # Validate the cross-reference field (implementation_ref / verification_ref)
        ref_value = _ref_value(entry, ref_field_name)
        ref_empty = _is_empty_path(ref_value)

        if status in _REQUIRES_PATH and ref_empty:
            violations.append(CSPViolation(
                artifact=str(path), index=idx, item_id=item_id,
                kind="missing-ref", severity="Important",
                message=(
                    f"item {item_id or '?'} (status={status}) requires a "
                    f"non-empty `{ref_field_name}` field. For {status} items, "
                    f"the implementation/verification reference must point at "
                    f"real code or test paths."
                ),
            ))
            continue

        items.append(CSPItem(
            artifact=str(path), item_id=item_id, prefix=prefix,
            title=title, status=status,
            ref_field=ref_field_name, ref_value=ref_value,
            index=idx,
        ))

        # Path existence check (only when ref is non-empty)
        if not ref_empty:
            file_part = ref_value.split(":", 1)[0].strip()
            file_part = file_part.split("#", 1)[0].strip()  # strip URL fragment
            if file_part:
                resolved = (project_root / file_part).resolve()
                if not resolved.exists():
                    violations.append(CSPViolation(
                        artifact=str(path), index=idx, item_id=item_id,
                        kind="broken-ref", severity="Important",
                        message=(
                            f"item {item_id or '?'}: "
                            f"`{ref_field_name}: {ref_value}` references a path "
                            f"that does not exist (resolved to {resolved}). "
                            f"Either fix the path, change the status, or use "
                            f"`n/a`."
                        ),
                    ))

    return items, violations


def run_audit(
    project_root: Path,
    threats_path: Path | None = None,
    requirements_path: Path | None = None,
    nfrs_path: Path | None = None,
    skip_heavy_check: bool = False,
) -> AuditResult:
    """Run the CSP-1 audit across all three Heavy JSON artifacts."""
    result = AuditResult()

    is_heavy = skip_heavy_check or _detect_heavy_mode(project_root)
    result.heavy_mode = is_heavy

    if not is_heavy:
        return result  # silent in Minimal / Standard modes

    if threats_path is None:
        threats_path = project_root / VAULT_ROOT / _ARTIFACTS["TM"][0]
    if requirements_path is None:
        requirements_path = project_root / VAULT_ROOT / _ARTIFACTS["REQ"][0]
    if nfrs_path is None:
        nfrs_path = project_root / VAULT_ROOT / _ARTIFACTS["NFR"][0]

    scan_plan: list[tuple[Path, str, str]] = [
        (threats_path, "TM", _ARTIFACTS["TM"][1]),
        (requirements_path, "REQ", _ARTIFACTS["REQ"][1]),
        (nfrs_path, "NFR", _ARTIFACTS["NFR"][1]),
    ]

    for path, prefix, array_key in scan_plan:
        if path.exists():
            result.artifacts_scanned.append(str(path))
        items, violations = _parse_artifact(path, project_root, prefix, array_key)
        result.items.extend(items)
        result.violations.extend(violations)

    return result


def _format_human(result: AuditResult) -> str:
    if not result.heavy_mode:
        return (
            "CSP-1 cross-spec parity audit: not Heavy mode "
            "(no <vault>/triage.json or mode != heavy). Skipped.\n"
        )

    if not result.artifacts_scanned:
        return (
            "CSP-1 cross-spec parity audit: no Heavy artifacts found "
            "(threat-model.json / requirements.json / non-functional.json "
            "absent under the vault).\n"
        )

    if not result.violations:
        by_prefix = {
            p: sum(1 for i in result.items if i.prefix == p)
            for p in ("TM", "REQ", "NFR")
        }
        return (
            f"CSP-1 cross-spec parity audit: clean. "
            f"{len(result.items)} item(s) — TM={by_prefix['TM']}, "
            f"REQ={by_prefix['REQ']}, NFR={by_prefix['NFR']}.\n"
        )

    out: list[str] = [
        f"{len(result.violations)} cross-spec parity violation(s):\n\n"
    ]
    for v in result.violations:
        loc = f"{v.artifact}[{v.index}]" if v.index >= 0 else v.artifact
        out.append(
            f"  [{v.severity}] {loc} ({v.kind}) "
            f"{f'item {v.item_id}' if v.item_id else ''}\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="cross_spec_parity_audit",
        description="CSP-1 cross-spec parity audit (Heavy mode only, v2 JSON)",
    )
    parser.add_argument(
        "--root", type=Path, default=Path("."),
        help="Project root for resolving cross-reference paths (default: cwd)",
    )
    parser.add_argument(
        "--threats", type=Path, default=None,
        help="Path to threat-model.json (default: <vault>/threat-model.json)",
    )
    parser.add_argument(
        "--requirements", type=Path, default=None,
        help="Path to requirements.json (default: <vault>/requirements.json)",
    )
    parser.add_argument(
        "--nfrs", type=Path, default=None,
        help="Path to non-functional.json (default: <vault>/non-functional.json)",
    )
    parser.add_argument(
        "--skip-heavy-check", action="store_true",
        help="Force-run even if triage.json doesn't declare Heavy mode (testing/CI)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    result = run_audit(
        project_root=args.root,
        threats_path=args.threats,
        requirements_path=args.requirements,
        nfrs_path=args.nfrs,
        skip_heavy_check=args.skip_heavy_check,
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
