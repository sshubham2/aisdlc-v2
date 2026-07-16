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


# ── shared build-check-rule shape validator (slice-071 / SC-151 / ADR-075) ──────
# NOTE (M-add-2): ADR-075 is sealed/append-only; its prose framing corrections live in
# the slice's design.json, NOT in the ADR body. `_identity` above stays a SEPARATE
# structural encoder — colocation here does not wire it to this validator, so ADR-075's
# "removes the 3-site drift" claim is corrected (in design.json) to "removes mint<->audit
# drift only" (P3). What IS de-drifted: `_as_str_list` + `applies_when_is_fireable` below
# are the ONE coercion the audit parser (`build_checks_audit._parse_rules` imports
# `_as_str_list`) AND the fireable predicate share, so 'can this ever fire' cannot diverge
# from how `_rule_applies` actually reads a rule (M3).

def _as_str_list(value) -> list:
    """Coerce a JSON field that may be a string or list of strings into a clean list of
    non-empty stripped strings. THE single coercion shared by the audit's rule parser
    (`build_checks_audit._parse_rules` imports it) and `applies_when_is_fireable` below, so
    a rule's 'can this ever fire' decision cannot drift from how `_rule_applies` reads it
    (slice-071 / M3 — e.g. ``{"keywords": [""]}`` coerces to ``[]`` in BOTH places)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def applies_when_is_fireable(applies_when) -> bool:
    """True iff an `applies_when` object carries at least one trigger that can EVER make
    ``build_checks_audit._rule_applies`` return True: ``always`` truthy, OR >=1 non-empty
    ``glob``, OR >=1 non-empty ``keyword``. Anchors alone never fire (they gate keywords);
    negative_anchors only suppress. Uses ``_as_str_list`` — the SAME coercion the parser
    uses — so this predicate cannot drift from real firing (slice-071 / M3)."""
    if not isinstance(applies_when, dict):
        return False
    if bool(applies_when.get("always")):
        return True
    if _as_str_list(applies_when.get("glob")):
        return True
    if _as_str_list(applies_when.get("keywords")):
        return True
    return False


def validate_rule_shape(rule, *, tier: str) -> list:
    """Shared structural validator for a build-check rule (slice-071 / ADR-075).

    ``tier='mint'``  — the PRODUCER check at ``vault_edit`` append/update: ``applies_when``
                       must be a JSON object. A non-object (bare string, list, or
                       ABSENT/None) enforces NOTHING downstream (build_checks_audit drops
                       it), so it is rejected at mint. Fireability is NOT checked at mint (a
                       new ``{}`` is a dict — it mints, and the audit warns; graduated +
                       migration-safe, M-add-1).
    ``tier='audit'`` — the CONSUMER endpoint check at ``build_checks_audit``: a non-object
                       ``applies_when`` is a DROP-causing ``non-object-applies-when`` problem
                       (hard block), AND a well-typed-but-INERT rule (dict ``applies_when``
                       with no fireable trigger) is an ``inert`` problem (surfaced, NON-block).

    Returns a list of problem dicts (empty == clean); it NEVER raises — each caller decides
    the disposition (mint raises ``ValueError`` -> exit 2; audit -> a Critical violation for
    a drop-causing problem, a visible warning for an inert one). Per M-add-3 a problem names
    the offending FIELD + the rule's own ``rule``/title text; the ``id`` is populated ONLY
    when one already exists (an audit-tier rule) — at mint the id is unallocated, so callers
    must name the field + rule-text + array index, never a BC-PROJ id."""
    problems: list = []
    if not isinstance(rule, dict):
        problems.append({
            "kind": "non-object-rule", "field": "(rule)", "id": "", "rule_text": str(rule),
            "message": f"rule is not a JSON object ({type(rule).__name__}); it enforces NOTHING",
        })
        return problems
    rid = str(rule.get("id", "")).strip()
    text = str(rule.get("rule", "")).strip()
    aw = rule.get("applies_when")
    if not isinstance(aw, dict):
        problems.append({
            "kind": "non-object-applies-when", "field": "applies_when", "id": rid,
            "rule_text": text,
            "message": (f"`applies_when` is not a JSON object "
                        f"({'absent' if aw is None else type(aw).__name__}); a rule with a "
                        f"non-object `applies_when` enforces NOTHING until repaired"),
        })
        return problems  # cannot assess fireability on a non-object
    if tier == "audit" and not applies_when_is_fireable(aw):
        problems.append({
            "kind": "inert", "field": "applies_when", "id": rid, "rule_text": text,
            "message": (f"`applies_when` {aw!r} has no fireable trigger (no `always`, "
                        f"non-empty `glob`, or non-empty `keywords`); the rule can never "
                        f"fire and enforces nothing until a trigger is added"),
        })
    return problems


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
