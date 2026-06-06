"""SCMD-1 — shippability machine-stable-command audit — v2 JSON (part (a) only).

Reads `<vault>/shippability.json` and verifies every `rows[].machine_cmd` is a
prose-free, interpreter-anchored (or bare-`pytest`) invocation — NOT a narrative
prose cell. A row missing `machine_cmd`, or any `;`-separated segment failing the
anchored full-match, is a violation — never a silent skip (a silent skip would
also disable PTFCD-1 for that row).

This is the SKILL.md Step-6 "SCMD-1 pre-catalog gate" and the source of the
`_segments` / `_machine_cmd_cell` / `_catalog_rows` helpers the SRSC-1 runner
(`shippability_runner.py`) reuses, exactly as v1's runner reused this module.

**v2 change from v1 — part (b) DROPPED (RECOMMEND DROP, coupled to a v2-dropped
model).** v1 SCMD-1 had a SECOND check: an AST classifier that flagged a cited
test function as `incidental` when it read the gitignored in-tree
`architecture/slices/archive/**` / `architecture/build-checks.md` or the
untracked `~/.claude/build-checks.md`, vs `essential` when it read
`~/.claude/methodology-changelog.md` (a forward-sync assertion gated by a
registered-installed allowlist). BOTH coupling vectors are GONE in v2:
  - the in-tree `architecture/` vault is dropped — the vault is the EXTERNAL
    store, never in the code repo's git, so there is no gitignored-archive /
    in-tree-build-checks coupling to police;
  - the `~/.claude/...` forward-sync gates are dropped (the plugin is the single
    source of truth; no installed==repo parity) — so the `essential` /
    `_REGISTERED_INSTALLED_READERS` machinery has nothing to assert.
Part (b)'s entire `incidental`/`essential`/`clean` classification therefore
audits a model that no longer exists in v2 and is not ported. Part (a), the
prose-free machine_cmd grammar, is the live SCMD-1 surface and is fully ported.

v2 catalog shape (`<vault>/shippability.json`; schema by example
`skills/repro/examples/shippability.json`):

    {"rows": [{"id": "SHIP-007", "machine_cmd": "pytest tests/x.py -q", ...}]}

Usage:
    python shippability_decoupling_audit.py <vault>/shippability.json
    python shippability_decoupling_audit.py <vault>/shippability.json --json

Exit codes:
    0  clean (or empty / zero-row catalog)
    1  >=1 SCMD-1 violation (prose / missing machine_cmd)
    2  usage error (catalog missing/unreadable, or not valid JSON)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from shippability_path_audit import _find_repo_root  # noqa: F401 (re-exported for the runner)

# --- (a) machine-stable command grammar -------------------------------------
# Each ;-separated segment, trimmed, must FULL-MATCH this anchored shape. The
# leading anchor (segment starts with an interpreter token OR the bare `pytest`
# keyword) is the mechanism that rejects a prose cell. v2 machine_cmd strings
# may be bare `pytest tests/... -q` (the example form) OR interpreter-anchored
# `<interp>|python|.../python.exe -m pytest tests/...`.
_INTERP = r"(?:<interp>|python3?|[^\s;]*python(?:3)?(?:\.exe)?)"
_SEGMENT_RE = re.compile(
    rf"^(?:{_INTERP}(?:\s+-W\s+\S+)?\s+-m\s+)?pytest\s+"
    r"(?:tests/\S+?\.py(?:::\S+)?|tests/\S+)"  # >=1 tests/-rooted target
    r"(?:\s+\S+)*$"
)


@dataclass(frozen=True)
class Violation:
    kind: str          # "missing-machine-cmd" | "prose-segment"
    row: str           # catalog row id
    detail: str
    index: int = 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "row": self.row,
                "detail": self.detail, "index": self.index}


@dataclass
class AuditResult:
    rows_scanned: int = 0
    violations: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows_scanned": self.rows_scanned,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {"violation_count": len(self.violations)},
        }


# --------------------------------------------------------------------------- #
# Catalog parsing (JSON) — the runner reuses these three helpers.             #
# --------------------------------------------------------------------------- #
def _catalog_rows(catalog_path: Path) -> list[tuple[int, dict, str]]:
    """Return [(0-based-index, row_dict, row_id)] for catalog data rows.

    v2 replacement for v1's markdown-table `_catalog_rows(text)`. The runner
    imports this and iterates `(index, row, row_id)`."""
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("shippability.json top level is not a JSON object")
    rows = data.get("rows", []) or []
    if not isinstance(rows, list):
        raise ValueError("shippability.json `rows` is not a JSON array")
    out: list[tuple[int, dict, str]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or row.get("slice") or i)
        out.append((i, row, row_id))
    return out


def _machine_cmd_cell(row: dict) -> str | None:
    """The row's prose-free command (v2 `machine_cmd` field). None when absent."""
    val = row.get("machine_cmd")
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _segments(machine_cmd: str) -> list[str]:
    """Split on `;`, then strip surrounding markdown backtick fence + ws from
    EACH segment (JSON values rarely fence, but a hand-authored row might)."""
    out: list[str] = []
    for raw in machine_cmd.split(";"):
        seg = raw.strip().strip("`").strip()
        if seg:
            out.append(seg)
    return out


def _check_machine_cmd(result: AuditResult, index: int, row_id: str,
                       row: dict) -> str | None:
    """Check (a). Returns the raw machine_cmd if structurally OK, else records a
    violation and returns None."""
    cell = _machine_cmd_cell(row)
    if cell is None or cell == "":
        result.violations.append(Violation(
            "missing-machine-cmd", row_id,
            "row has no `machine_cmd` field — would silently disable PTFCD-1 "
            "for this row", index))
        return None
    segs = _segments(cell)
    if not segs:
        result.violations.append(Violation(
            "prose-segment", row_id,
            f"machine_cmd has zero parseable segments: {cell!r}", index))
        return None
    for seg in segs:
        if not _SEGMENT_RE.fullmatch(seg):
            result.violations.append(Violation(
                "prose-segment", row_id,
                f"segment is not an interpreter-anchored pytest invocation "
                f"(prose/narrative rejected): {seg!r}", index))
            return None
    return cell


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def audit(catalog_path: Path) -> AuditResult:
    result = AuditResult()
    for index, row, row_id in _catalog_rows(catalog_path):
        result.rows_scanned += 1
        _check_machine_cmd(result, index, row_id, row)
    return result


def _format_human(r: AuditResult) -> str:
    if not r.violations:
        return (f"SCMD-1 audit: clean. {r.rows_scanned} row(s); "
                f"every machine_cmd is a prose-free pytest invocation.\n")
    out = [f"{len(r.violations)} SCMD-1 violation(s):\n\n"]
    for v in r.violations:
        out.append(f"  [Important] shippability.json row {v.row} [{v.kind}]\n"
                   f"    {v.detail}\n\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="shippability_decoupling_audit",
        description="SCMD-1 machine-stable-command audit (v2 JSON; part (a))",
    )
    parser.add_argument("catalog", type=Path, nargs="?",
                        default=VAULT_ROOT / "shippability.json",
                        help="Path to shippability.json (default: <vault>/shippability.json)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    catalog_path: Path = args.catalog
    if not catalog_path.is_file():
        sys.stderr.write(f"usage error: catalog not found: {catalog_path}\n")
        return 2
    try:
        result = audit(catalog_path)
    except OSError as exc:
        sys.stderr.write(f"usage error: cannot read catalog: {exc}\n")
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"usage error: catalog is not valid shippability.json: {exc}\n")
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_format_human(result), end="")
    return 1 if result.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
