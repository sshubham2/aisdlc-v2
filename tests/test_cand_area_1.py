"""slice-098 / SC-212 — CAND-AREA-1: the capability rollup must never read a CANDIDATE's `area` key.

RETARGETED at TRI-1 ([[ADR-125]] section 7 / critique M8). The design originally specified an
IMPORT-DIRECTION scan (assert product_rollup never imports area_resolve), which was green BY
CONSTRUCTION and therefore worthless: the declared direction is area_resolve -> product_rollup, so the
scanned edge would be a circular import and could never appear. Meanwhile the ACTUAL regression this
guard exists to catch — someone adding `cand.get("area")` inside product_rollup so the capability counts
"pick up" candidate annotations — needs no import at all.

So the guard scans product_rollup.py's AST for a CANDIDATE-RECORD read of the `area` key, and the
NEGATIVE CONTROL is mandatory: a mutated copy that ADDS such a read MUST fail the scan. Without the
control, a scan that silently matches nothing looks identical to a scan that is working.

This guard is SECONDARY by explicit decision (ADR-124's own consequences): an AST scan cannot prove
absence of a read (a lazy in-function import, an intermediary module, or a dict comprehension over
arbitrary keys all evade it). The PRIMARY AC3 proof is the behavioural conservation test in
tests/test_area_resolve.py — annotate every live row, assert the whole rollup envelope byte-identical.
"""
from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ROLLUP = PLUGIN_ROOT / "scripts" / "lib" / "product_rollup.py"

# The PS-item area reads product_rollup legitimately makes (`ps_area_map` iterates scope['items'] and
# reads it.area / it.component). They are keyed by the LOOP VARIABLE name, which is how a scope item is
# distinguished from a candidate here — coarse on purpose: a NEW reader that binds a candidate to one of
# these names trips the guard and has to justify itself, which is the intended failure direction.
_SCOPE_ITEM_NAMES = {"it", "item", "scope", "entry"}


def _area_key_reads(tree: ast.AST) -> list[tuple[str, int]]:
    """Every `<x>.get("area")` / `<x>["area"]` read, as (receiver-name, lineno)."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        # <recv>.get("area") / <recv>.get("area", ...)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "area"):
            recv = node.func.value
            found.append((recv.id if isinstance(recv, ast.Name) else "<expr>", node.lineno))
        # <recv>["area"]
        elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value == "area"):
            recv = node.value
            found.append((recv.id if isinstance(recv, ast.Name) else "<expr>", node.lineno))
    return found


def _candidate_area_reads(source: str) -> list[tuple[str, int]]:
    """Area-key reads whose receiver is NOT a recognised product-scope item binding."""
    return [(name, line) for name, line in _area_key_reads(ast.parse(source))
            if name not in _SCOPE_ITEM_NAMES]


def test_product_rollup_does_not_read_candidate_area_key():
    """AC3's structural half: the capability path counts CAPABILITIES, so it must never consult a
    candidate's own `area`. It joins children through children_by_parent(owner_refs) and reads the
    stratifier from the PS item — nothing else."""
    hits = _candidate_area_reads(ROLLUP.read_text(encoding="utf-8"))
    assert hits == [], (
        f"product_rollup.py reads an `area` key off a non-scope-item receiver at {hits} — a candidate "
        f"is NOT a capability. The rollup's stratifier is the product-scope item's area; a candidate's "
        f"own area belongs to the /slice pick lens only (scripts/lib/area_resolve.py). "
        f"See [[ADR-124]] section 3 / [[ADR-125]] section 7.")


def test_guard_fails_on_mutated_copy_adding_cand_get_area():
    """The MANDATORY negative control ([[ADR-125]] section 7). A scan that matches nothing is
    indistinguishable from a scan that is broken — this proves the guard actually fires on the exact
    regression it is written for."""
    source = ROLLUP.read_text(encoding="utf-8")
    mutated = source.replace(
        "    refs = product_scope.owner_refs(cand)",
        "    if cand.get(\"area\"):\n"
        "        return cand[\"area\"]\n"
        "    refs = product_scope.owner_refs(cand)",
        1,
    )
    assert mutated != source, (
        "the negative control could not inject its mutation — the anchor line moved; re-anchor it "
        "rather than deleting the control")
    hits = _candidate_area_reads(mutated)
    assert hits, "the guard did NOT fire on a mutated copy that reads a candidate's own area"
    assert any(name == "cand" for name, _ in hits), hits


def test_guard_recognises_both_read_shapes():
    """Both `.get("area")` and `["area"]` must trip it — a regression written either way is the same
    regression."""
    for snippet in ('def f(cand):\n    return cand.get("area")\n',
                    'def f(cand):\n    return cand["area"]\n',
                    'def f(cand):\n    return cand.get("area", None)\n'):
        assert _candidate_area_reads(snippet), snippet
    # ...and the legitimate PS-item read does NOT trip it (or the guard would be unusable).
    assert _candidate_area_reads('def f(items):\n    return [it.get("area") for it in items]\n') == []
