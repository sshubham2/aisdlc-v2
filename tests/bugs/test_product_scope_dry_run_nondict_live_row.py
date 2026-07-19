"""
Bug (SC-181): `product_scope materialize --dry-run` crashes on a non-dict live candidate row.

_materialize's dry-run read (product_scope.py:1052-1053) passes the live `candidates` list to
`_observed` UNFILTERED. `_observed` filters only its archive half, so a non-dict live row
(e.g. a bare string) reaches `children_by_parent -> owner_refs -> iter_sources`, which calls
`.get("source")` on it.

The sibling live reads at :1323 / :1531 / :1717 all apply `[c for c in live if isinstance(c, dict)]`
first; the dry-run read is the one path that does not.

Expected: dry-run tolerates/filters the malformed row (exit 0, valid envelope) — as robust as the
in-lock write read and the sibling reads.
Actual (pre-fix): AttributeError: 'str' object has no attribute 'get' -> exit 1.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "scripts/lib/product_scope.py"


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_dry_run_tolerates_nondict_live_candidate_row(run_script, tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", {
        "_schema": "aisdlc/product-scope@1", "project": "fixture",
        "items": [{
            "id": "PS-001", "decomposition_label": "core", "title": "build-core",
            "description": "the core", "user_visible_outcome": "it runs", "depends_on": [],
            "assumptions": [{"id": "A1", "statement": "expressible", "blocking": True,
                             "spike_status": "unproven"}],
            "verification_plan": "drive one run",
        }],
    })
    # A non-dict live row — the real aivlc vault has historically carried scalar rows; a whole
    # candidate could be similarly malformed. It must NOT crash the read-only dry-run.
    _write(v / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture",
        "counters": {"sc": 0}, "candidates": ["I-AM-A-BARE-STRING"], "pick_log": [],
    })

    r = run_script(SCRIPT, ["--vault", str(v), "materialize", "--dry-run", "--json"])

    assert "AttributeError" not in r.stderr, r.stderr
    assert r.returncode == 0, f"dry-run crashed on a non-dict live row: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["action"] == "materialize"
    assert out["dry_run"] is True


# ── M1 belt / ADR-096: the NON-dry-run MINT path (:1069) -- the site --dry-run cannot reach ──
#
# AC2 above exercises the dry-run read (:1053), which RETURNS before the mutate() closure that holds the
# mint read (:1069). The single _observed boundary fix (ADR-096 M-add-1) means the two reads cannot
# diverge, but the meta-Critic asked for a mint-path belt: prove the write path also tolerates a non-dict
# live row AND preserves it in the written file (the mint appends the original cands at :1130).

_SCOPE_ITEM = {
    "id": "PS-001", "decomposition_label": "core", "title": "build-core",
    "description": "the core", "user_visible_outcome": "it runs", "depends_on": [],
    "assumptions": [{"id": "A1", "statement": "expressible", "blocking": True,
                     "spike_status": "unproven"}],
    "verification_plan": "drive one run",
}


def _materialize_written(run_script, base: Path, live_candidates: list):
    """Mint (non-dry-run) an unmaterialized PS-001 against a candidates.json carrying `live_candidates`;
    return (CompletedProcess, the written candidates.json dict)."""
    v = base / "vault"
    v.mkdir(parents=True)
    _write(v / "product-scope.json", {
        "_schema": "aisdlc/product-scope@1", "project": "fixture", "items": [dict(_SCOPE_ITEM)],
    })
    _write(v / "candidates.json", {
        "_schema": "aisdlc/slice-candidates@1", "project": "fixture",
        "counters": {"sc": 0}, "candidates": list(live_candidates), "pick_log": [],
    })
    r = run_script(SCRIPT, ["--vault", str(v), "materialize", "--json"])
    written = json.loads((v / "candidates.json").read_text(encoding="utf-8"))
    return r, written


def test_mint_path_tolerates_and_preserves_nondict_live_row(run_script, tmp_path):
    # control: a clean all-dict backlog mints exactly one candidate (SC-001) for the unmaterialized PS-001
    r0, w0 = _materialize_written(run_script, tmp_path / "control", [])
    assert r0.returncode == 0, r0.stderr
    minted0 = [c for c in w0["candidates"] if isinstance(c, dict)]
    assert len(minted0) == 1, "control: exactly one candidate minted for PS-001"

    # test: the SAME scope, but a bare-string live row is present in candidates.json
    r1, w1 = _materialize_written(run_script, tmp_path / "withbad", ["I-AM-A-BARE-STRING"])
    assert "AttributeError" not in r1.stderr, r1.stderr
    assert r1.returncode == 0, f"mint path crashed on a non-dict live row: {r1.stderr}"
    # the malformed row is PRESERVED in the written file (only the OBSERVED view is filtered)
    assert "I-AM-A-BARE-STRING" in w1["candidates"], "the malformed live row must survive in the written file"
    minted1 = [c for c in w1["candidates"] if isinstance(c, dict)]
    assert len(minted1) == 1, "the mint path must still mint exactly one candidate for PS-001"
    refs = [s.get("ref") for s in minted1[0].get("source") or [] if isinstance(s, dict)]
    assert "PS-001" in refs, "the mint must materialize the unmaterialized PS-001"

    # the non-dict row is INERT: the minted record equals the control's (ex the volatile history[].at ts),
    # proving the live-half filter is identity on the dict rows (byte-identical planning; must-not-defer)
    def _stable(rec: dict) -> dict:
        return {k: v for k, v in rec.items() if k != "history"}
    assert _stable(minted1[0]) == _stable(minted0[0]), \
        "a non-dict live row must not alter the minted candidate (filter is identity on dict rows)"
