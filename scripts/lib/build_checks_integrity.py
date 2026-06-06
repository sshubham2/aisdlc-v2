"""Build-checks integrity gate (BCI-1) — v2 (JSON, project-only).

Asserts that the live ``<vault>/build-checks.json`` rule set matches the git-tracked
canonical fixture on full per-rule structural identity — the downstream detector for
R-4's witnessed silent truncation of build-checks during ``/reflect``'s non-deterministic
promotion step.

**v2 changes from v1 (BCI-1 / ADR-028+029):**
- **JSON, not markdown.** v1 parsed ``build-checks.md`` via ``build_checks_audit._parse_rules``;
  v2 loads ``build-checks.json`` (rules array) directly — no markdown parser dependency.
- **The ``~/.claude/build-checks.md`` "global" surface is DROPPED.** It was the v1 forward-sync /
  installed-copy parity model, removed in v2 (the plugin is the distribution unit). Project surface only.
- **Absent canonical fixture → NO-OP PASS** (exit 0), not a usage error. v1 treated an absent fixture as
  exit-2 "repo malformed", because the methodology's own build-checks ALWAYS had one. In v2 the fixture is a
  SELF-DEVELOPMENT oracle: an arbitrary USER project has no fixture and its build-checks grow freely, so BCI-1
  is simply not applicable there. The primary anti-truncation guard for ALL projects is now ``vault_edit``'s
  SVW-1 locked append (``build-checks.json`` is only mutated through it); BCI-1 is the structural-identity oracle
  the methodology pins on ITSELF.

Per-rule structural identity (v2 schema ``{id, severity, applies_when, rule, rationale}``):
    (id, severity, applies_when, rule-text)   + non-empty ``rule``
A present-but-truncated/empty/mismatched live file → HALT.

Usage:
    python -m scripts.lib.build_checks_integrity            # --check-live (default)
    python -m scripts.lib.build_checks_integrity --json
    python -m scripts.lib.build_checks_integrity --root <repo-root>

Exit codes:
    0  conformant, OR no-op (no canonical fixture → not applicable)
    1  LOCAL VAULT DRIFT — a present fixture's rule set diverges from live (missing/extra/mismatch/empty)
       or the project build-checks.json is absent/malformed while a fixture exists
    2  usage error (canonical fixture itself malformed/unreadable, root unresolvable)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

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

_FIXTURE_REL = "tests/methodology/fixtures/build_checks/canonical_project_checks.json"
_LIVE_REL = VAULT_ROOT / "build-checks.json"  # absolute VAULT_ROOT → the shared external vault
_ATTRIB = "LOCAL VAULT DRIFT — reconstruct from {fixture}; this is NOT a slice regression"


def _identity(rule: dict) -> tuple:
    """Full per-rule structural identity for the v2 schema."""
    return (
        str(rule.get("id", "")),
        str(rule.get("severity", "")).strip().lower(),
        json.dumps(rule.get("applies_when"), sort_keys=True, ensure_ascii=False),
        str(rule.get("rule", "")).strip(),
    )


@dataclass
class CheckResult:
    status: str = "conformant"   # conformant | drift | warn | usage
    exit_code: int = 0
    warnings: list = field(default_factory=list)
    divergences: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status, "exit_code": self.exit_code,
            "warnings": self.warnings, "divergences": self.divergences,
        }


def _load_rules(path: Path) -> tuple[list[dict] | None, str | None]:
    """``(rules, error)`` — rules is the ``rules`` array; error is a message on bad JSON / shape."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"cannot read {path}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"{path} is not valid JSON: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return None, f"{path} has no 'rules' array"
    return data["rules"], None


def check_live(root: Path) -> CheckResult:
    """Assert the live build-checks.json matches the canonical fixture (project-only)."""
    result = CheckResult()
    fixture = root / _FIXTURE_REL

    # v2: an absent fixture is the NORMAL user-project case → NO-OP PASS (not a usage error).
    if not fixture.exists():
        result.status = "warn"
        result.warnings.append(
            f"no canonical build-checks fixture at {fixture} — BCI-1 not applicable "
            f"(user project / self-development fixture absent). build-checks.json mutations are "
            f"guarded by vault_edit's SVW-1 locked append. PASS (no-op)."
        )
        return result

    fixture_rules, ferr = _load_rules(fixture)
    if ferr:
        result.status = "usage"
        result.exit_code = 2
        result.divergences.append(f"canonical fixture is the oracle and must be clean: {ferr}")
        return result

    attributed = _ATTRIB.format(fixture=fixture)
    live = root / _LIVE_REL
    if not live.exists():
        result.status = "drift"
        result.exit_code = 1
        result.divergences.append(f"project build-checks.json absent at {live} — {attributed}")
        return result

    live_rules, lerr = _load_rules(live)
    if lerr:
        result.status = "drift"
        result.exit_code = 1
        result.divergences.append(f"live build-checks.json malformed: {lerr} — {attributed}")
        return result

    fixture_by_id = {str(r.get("id", "")): _identity(r) for r in fixture_rules}
    live_by_id = {str(r.get("id", "")): _identity(r) for r in live_rules}
    live_rule_text = {str(r.get("id", "")): str(r.get("rule", "")).strip() for r in live_rules}

    drift: list[str] = []
    missing = sorted(set(fixture_by_id) - set(live_by_id))
    extra = sorted(set(live_by_id) - set(fixture_by_id))
    if missing:
        drift.append(f"live MISSING canonical rule(s) {missing} (present-but-truncated ⇒ HALT)")
    if extra:
        drift.append(f"live has NON-canonical rule(s) {extra}")
    for rid in sorted(set(fixture_by_id) & set(live_by_id)):
        if live_by_id[rid] != fixture_by_id[rid]:
            drift.append(
                f"rule {rid} structural-identity mismatch\n"
                f"      fixture={fixture_by_id[rid]}\n      live   ={live_by_id[rid]}"
            )
        if not live_rule_text.get(rid):
            drift.append(f"rule {rid} has an empty `rule` body")

    if drift:
        result.status = "drift"
        result.exit_code = 1
        result.divergences.extend(drift)
        result.divergences.append(f"  → {attributed}")
    return result


def _format_human(result: CheckResult) -> str:
    if result.status == "usage":
        return "BCI-1 build-checks integrity: USAGE ERROR\n\n" + "".join(
            f"  {d}\n" for d in result.divergences)
    if result.status == "drift":
        return "BCI-1 build-checks integrity: DRIFT (HALT)\n\n" + "".join(
            f"  {d}\n" for d in result.divergences)
    if result.status == "warn":
        return "BCI-1 build-checks integrity: PASS (no-op)\n\n" + "".join(
            f"  {w}\n" for w in result.warnings)
    return ("BCI-1 build-checks integrity: PASS — live build-checks.json matches the "
            "git-tracked canonical fixture on full per-rule structural identity.\n")


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="build_checks_integrity",
        description="BCI-1 — assert live build-checks.json matches the canonical fixture (v2 JSON, project-only)",
    )
    parser.add_argument("--check-live", action="store_true", help="Check live vs fixture (default action)")
    parser.add_argument("--root", type=Path, default=None, help="Repo root (default: two parents up from this file)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    root = (Path(__file__).resolve().parent.parent.parent if args.root is None else args.root.resolve())
    if not root.exists():
        sys.stderr.write(f"repo root not found: {root}\n")
        return 2

    result = check_live(root)
    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
