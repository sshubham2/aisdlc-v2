#!/usr/bin/env python3
"""area_code_link_audit.py — slice-084 C1c: the area <-> code-component link backstop for /drift-check.

Reconciles the two axes that share the word "component" but are deliberately kept separate (slice-084):
  * the PRODUCT axis — a product-scope item's `area` (concept #1) + its OPTIONAL `code_components[]` link;
  * the CODE axis   — Heavy `components/*.json` (concept #2), AST-derived by /sync, each carrying a `name`.

It flags a STALE LINK: a product-scope item whose `code_components` names a code component that does NOT
exist in the vault's `components/*.json` inventory — the mapping rotted (a component was renamed or removed
by /sync while the area still claims it). This is the "ground the product grouping to reality when reality
exists" half of the reconciliation; it never FORCES a link (empty is legal).

DEGRADES CLEANLY, by design (the reason `code_components` is OPTIONAL + empty-legal):
  * no product-scope.json              -> nothing to audit          (status: no-scope,          exit 0)
  * no item declares a code link        -> nothing to check          (status: no-links,          exit 0)
  * links declared but no components/    -> SKIP: pre-code / Minimal / not-yet-synced is NOT drift, and a
                                            link cannot be resolved without an inventory to resolve it
                                            against                    (status: no-code-inventory, exit 0)
  * scope + inventory both present       -> every declared entry must resolve to a real component `name`;
                                            an unresolved one is a finding (status: findings/clean, exit 1/0)

The REVERSE direction (a code component implementing a shipped capability that no area claims) needs a
capability->code map the vault does not track deterministically, so it is left to the LLM drift pass
(a best-effort prose row in drift-check/SKILL.md) and is deliberately NOT asserted here — this helper only
gives the checkable half teeth.

Exit 0 = clean or degraded (status names which); 1 = >=1 stale link; 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

SCOPE_FILE = "product-scope.json"
COMPONENTS_DIR = "components"


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _declared_links(scope: dict) -> list[dict]:
    """Every (item, area, code_component) triple a product-scope item declares. Malformed-tolerant."""
    out: list[dict] = []
    for it in (scope.get("items") if isinstance(scope, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        links = it.get("code_components")
        if not isinstance(links, list):
            continue
        area = it.get("area") if isinstance(it.get("area"), str) else it.get("component")
        for c in links:
            if isinstance(c, str) and c.strip():
                out.append({"item": str(it.get("id")), "area": area, "code_component": c.strip()})
    return out


def _component_names(vault: Path) -> set[str] | None:
    """The set of real code-component names from <vault>/components/*.json — each file's `name`, falling
    back to the filename stem. None when the inventory directory is ABSENT (the skip signal)."""
    d = vault / COMPONENTS_DIR
    if not d.is_dir():
        return None
    names: set[str] = set()
    for p in sorted(d.glob("*.json")):
        data = _load_json(p)
        name = data.get("name") if isinstance(data, dict) else None
        names.add(str(name).strip() if isinstance(name, str) and name.strip() else p.stem)
    return names


def audit(vault: Path) -> dict:
    scope_p = vault / SCOPE_FILE
    if not scope_p.exists():
        return {"status": "no-scope", "findings": [], "checked": 0,
                "note": f"{SCOPE_FILE} absent — no product areas to reconcile against code."}
    scope = _load_json(scope_p)
    if not isinstance(scope, dict):
        return {"status": "no-scope", "findings": [], "checked": 0,
                "note": f"{SCOPE_FILE} unreadable/malformed — nothing to audit."}

    links = _declared_links(scope)
    if not links:
        return {"status": "no-links", "findings": [], "checked": 0,
                "note": "no product-scope item declares a code_components link (all empty/absent — legal)."}

    names = _component_names(vault)
    if names is None:
        return {"status": "no-code-inventory", "findings": [], "checked": len(links),
                "note": (f"{COMPONENTS_DIR}/ inventory absent (pre-code / Minimal / /sync not run) — a "
                         f"declared link cannot be resolved yet; skipping (not drift).")}

    findings = []
    for lk in links:
        if lk["code_component"] not in names:
            findings.append({
                **lk,
                "reason": (f"area {lk['area']!r} (item {lk['item']}) links code component "
                           f"{lk['code_component']!r}, which no {COMPONENTS_DIR}/*.json carries "
                           f"(known: {', '.join(sorted(names)) or 'none'}). The mapping is STALE — the "
                           f"component was renamed/removed by /sync, or the link is a typo."),
                "resolve": (f"update the product-scope item's code_components (via `product_scope revise`) "
                            f"to the current component name, or re-run /sync."),
            })
    return {"status": "findings" if findings else "clean", "findings": findings,
            "checked": len(links), "components_known": sorted(names)}


def _render_text(res: dict) -> str:
    st = res.get("status")
    if st in ("no-scope", "no-links", "no-code-inventory"):
        return f"area<->code link audit: {st} — {res.get('note', '')}".rstrip(" —")
    if not res["findings"]:
        return f"area<->code link audit: clean ({res['checked']} link(s) all resolve)."
    lines = [f"area<->code link audit: {len(res['findings'])} STALE LINK(s) of {res['checked']} checked:"]
    for f in res["findings"]:
        lines.append(f"  [STALE LINK] {f['reason']}")
        lines.append(f"               Resolve: {f['resolve']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()               # an area / component name may be non-ASCII
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--vault", default=None, help="vault root (defaults to the resolved VAULT_ROOT)")
    ap.add_argument("--json", action="store_true", help="emit JSON (default: human-readable text)")
    args = ap.parse_args(argv)
    vault = Path(args.vault) if args.vault else VAULT_ROOT
    if not vault:
        sys.stderr.write("area_code_link_audit: could not resolve the vault root\n")
        return 2
    res = audit(Path(vault))
    print(json.dumps(res, ensure_ascii=False) if args.json else _render_text(res))
    return 1 if res.get("findings") else 0          # exit 1 ONLY on a real stale link; degrades are exit 0


if __name__ == "__main__":
    raise SystemExit(main())
