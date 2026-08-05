"""slice-098 / SC-212 ([[ADR-124]] + [[ADR-125]]) — the candidate-level `area` READ side.

AC2 the /slice lens surfaces a NON-product candidate carrying an area (and default-OFF output is
byte-identical to before the field existed), AC3 the capability-progress rollup is UNCHANGED by any
candidate-level area (a candidate is not a capability), AC4 the precedence rule is explicit and tested
across the full own x parents matrix at BOTH the resolve() unit level and through the candidates_top CLI.

Also pins the two invariants the design carried as build-time obligations:
  * MV2 (spike-A1 constraint 4) — EXACTLY ONE precedence site: candidates_top derives no area itself.
  * read-side TOTALITY ([[ADR-124]] section 5) — the compensating control for the named-open `rewrite`
    leg: an invalid STORED area degrades to absent, never surfacing and never raising.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from scripts.lib import area_resolve, product_rollup  # noqa: E402

TOP = PLUGIN_ROOT / "skills" / "slice" / "scripts" / "candidates_top.py"
UNASSIGNED = product_rollup.UNASSIGNED


# ── fixture builders ───────────────────────────────────────────────────────────────

def _ps_item(iid, area=None):
    it = {"id": iid, "title": iid.lower(), "assumptions": [
        {"id": "A1", "statement": "x", "blocking": True, "spike_status": "unproven"}]}
    if area is not None:
        it["area"] = area
    return it


def _cand(cid, refs=(), *, area=None, status="candidate", score=5, source_type="product-scope"):
    c = {"id": cid, "title": cid.lower(), "status": status, "progress": "not-started",
         "slice": None, "claimed_by": None, "started_at": None,
         "source": [{"type": source_type, "ref": r} for r in refs],
         "priority": {"score": score, "severity": "medium", "effort": "M"}}
    if area is not None:
        c["area"] = area
    return c


def _chore(cid, *, area=None, score=5):
    """A pipeline-EXHAUST candidate: no product-scope parent at all. Before this slice it could never
    appear in the area lens under any value (slice-084 A1 source-scoping)."""
    return _cand(cid, ["R-1"], area=area, score=score, source_type="risk")


def _write(vault: Path, scope_items, live, archive=()):
    (vault / "archive").mkdir(parents=True, exist_ok=True)
    (vault / "product-scope.json").write_text(json.dumps(
        {"_schema": "aisdlc/product-scope@1", "project": "fx", "items": list(scope_items)}),
        encoding="utf-8")
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": list(live)}),
        encoding="utf-8")
    (vault / "archive" / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": list(archive)}),
        encoding="utf-8")


def _scannable(src: str) -> str:
    """Source with comment lines dropped — a comment naming a forbidden shape is documentation."""
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


# Reading a CANDIDATE's own area, or calling either half of the derived arm, is what a second precedence
# rule would look like in the lens. (`read_area_map` stays legal — handing the PS map to the resolver is
# the lens's job; DERIVING an area from it is not. `area_lens['area']` is the lens's OWN requested name,
# not a candidate record, so the scan targets the record-read shapes specifically.)
_OWN_DERIVATION_SHAPES = ('.get("area")', "cand['area']", 'cand["area"]', "c['area']", 'c["area"]',
                          "owner_refs(", "candidate_area(")


def _own_derivation_hits(src: str) -> list[str]:
    body = _scannable(src)
    return [shape for shape in _OWN_DERIVATION_SHAPES if shape in body]


def _run(vault, *args):
    env = dict(os.environ); env.pop("AI_SDLC_VAULT_ROOT", None)
    return subprocess.run([sys.executable, str(TOP), "--vault", str(vault), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def _ids(vault, *args):
    cp = _run(vault, "--json", *args)
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    return sorted(t["id"] for t in payload["top"]), payload


# ── AC2 — the lens surfaces a NON-product candidate carrying that area ──

def test_lens_surfaces_annotated_non_product_candidate(tmp_path):
    """The whole point of the slice: an exhaust-sourced chore with its own `area` is pickable through
    `--top N --area <NAME>` alone — no new flag, the annotation IS the opt-in ([[ADR-124]] section 2)."""
    _write(tmp_path, [_ps_item("PS-100", "payments")],
           [_cand("SC-001", ["PS-100"]),                    # product-sourced -> payments (derived)
            _chore("SC-002", area="write-seams"),           # NON-product, annotated -> admitted
            _chore("SC-003")])                              # NON-product, un-annotated -> excluded
    ids, payload = _ids(tmp_path, "--top", "5", "--area", "write-seams")
    assert ids == ["SC-002"], ids
    assert payload["area_lens"]["known"] is True, "an asserted area must widen `known`, not read UNKNOWN"
    assert "write-seams" in payload["area_lens"]["areas"]
    assert payload["area_lens"]["sources"] == {"SC-002": area_resolve.SOURCE_CANDIDATE}


def test_a1_anti_conflation_survives_un_annotated_chores_stay_out(tmp_path):
    """slice-084 A1's rationale must survive the widening: an UN-annotated chore has no area source and
    is excluded from EVERY area value, including the residual `unassigned` bucket its exclusion was
    originally about (the ~88-chore leak)."""
    _write(tmp_path, [_ps_item("PS-100")],                  # PS with NO area -> unassigned
           [_cand("SC-001", ["PS-100"]), _chore("SC-002"), _chore("SC-003", area="write-seams")])
    unassigned, _ = _ids(tmp_path, "--area", UNASSIGNED)
    assert unassigned == ["SC-001"], "only the product capability with no area belongs in `unassigned`"
    for area in ("payments", UNASSIGNED, "write-seams", "does-not-exist"):
        ids, _ = _ids(tmp_path, "--area", area)
        assert "SC-002" not in ids, f"un-annotated chore SC-002 leaked into --area {area}"


def test_ac2_default_off_output_is_byte_identical_after_annotation(tmp_path):
    """Default-OFF (no `--area`) must be byte-identical whether or not candidates carry an area — the
    conservative-extension guarantee, on BOTH the text and json surfaces."""
    live = [_cand("SC-001", ["PS-100"]), _chore("SC-002"), _chore("SC-003")]
    _write(tmp_path, [_ps_item("PS-100", "payments")], live)
    before_text = _run(tmp_path, "--top", "5")
    before_json = _run(tmp_path, "--top", "5", "--json")
    assert before_text.returncode == 0 and before_json.returncode == 0

    for c in live:                                          # annotate EVERY live candidate
        c["area"] = f"area-{c['id']}"
    _write(tmp_path, [_ps_item("PS-100", "payments")], live)
    after_text = _run(tmp_path, "--top", "5")
    after_json = _run(tmp_path, "--top", "5", "--json")
    assert after_text.stdout == before_text.stdout, "default-OFF TEXT output perturbed by candidate areas"
    assert after_json.stdout == before_json.stdout, "default-OFF JSON output perturbed by candidate areas"


# ── AC3 — the capability rollup is UNCHANGED by any candidate-level area ──

def _five_state_vault(vault: Path, *, annotate=False):
    """Exercises all five rollup strata across three areas (the slice-080 shape). `annotate` gives EVERY
    candidate, live and archived, a DISTINCT bogus area — if any of it reached the capability path the
    envelope would move."""
    scope = [_ps_item("PS-100", "payments"), _ps_item("PS-101", "payments"),
             _ps_item("PS-102", "billing"), _ps_item("PS-103", "billing"),
             _ps_item("PS-104"), _ps_item("PS-105")]
    live = [_cand("SC-903", ["PS-102"]), _cand("SC-906", ["PS-104", "PS-105"]),
            _chore("SC-907"), _chore("SC-908")]
    archive = [_cand("SC-901", ["PS-100"], status="shipped"),
               _cand("SC-902", ["PS-101"], status="rejected")]
    if annotate:
        for c in (*live, *archive):
            c["area"] = f"bogus-{c['id']}"
    _write(vault, scope, live, archive)


def test_rollup_envelope_byte_identical_with_all_candidates_annotated(tmp_path):
    """AC3, the PRIMARY (behavioural) proof — stronger than the AST guard: with EVERY candidate carrying
    a distinct bogus area, the ENTIRE product_rollup envelope is byte-identical. A candidate is not a
    capability, and the counting path structurally cannot see a candidate's own fields (it joins children
    via children_by_parent(owner_refs))."""
    # ONE vault path, rewritten in place, so the compare cannot be perturbed by a path echoed into the
    # envelope — the only variable between the two reads is the candidate-level annotation.
    _five_state_vault(tmp_path, annotate=False)
    before = json.dumps(product_rollup.compute_rollup(tmp_path), sort_keys=True, ensure_ascii=False)
    _five_state_vault(tmp_path, annotate=True)
    after = json.dumps(product_rollup.compute_rollup(tmp_path), sort_keys=True, ensure_ascii=False)
    assert after == before, "a candidate-level area moved the CAPABILITY rollup — AC3 breached"

    env = json.loads(after)
    for s in ("done", "rejected_only", "in_progress", "no_children", "unknown", "total"):
        strata = env["unassigned"][s] + sum(a[s] for a in env["areas"])
        assert strata == env["whole_app"][s], f"strata do not conserve on {s}"


def test_rollup_area_names_never_include_a_candidate_asserted_area(tmp_path):
    """The stratifier is the PS area map. A candidate-asserted area must never mint a rollup stratum —
    that would be counting candidates as capabilities."""
    _five_state_vault(tmp_path, annotate=True)
    env = product_rollup.compute_rollup(tmp_path)
    names = {a["name"] for a in env["areas"]}
    assert names == {"payments", "billing"}, names
    assert not any(n.startswith("bogus-") for n in names)


# ── AC4 — the precedence matrix, at the resolve() unit level AND through the CLI ──

_AREA_MAP = {"PS-A": "alpha", "PS-B": "beta", "PS-N": None}

# (own, parent_refs) -> (expected area, expected source, admitted?)
_MATRIX = [
    ("absent", (),                 None,        UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      False),
    ("absent", ("PS-N",),          None,        UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      True),
    ("absent", ("PS-A",),          None,        "alpha",    area_resolve.SOURCE_PRODUCT_SCOPE, True),
    ("absent", ("PS-A", "PS-B"),   None,        UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      True),
    ("valid",  (),                 "own",       "own",      area_resolve.SOURCE_CANDIDATE,     True),
    ("valid",  ("PS-N",),          "own",       "own",      area_resolve.SOURCE_CANDIDATE,     True),
    ("valid",  ("PS-A",),          "own",       "own",      area_resolve.SOURCE_CANDIDATE,     True),
    ("valid",  ("PS-A", "PS-B"),   "own",       "own",      area_resolve.SOURCE_CANDIDATE,     True),
    ("invalid", (),                "   ",       UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      False),
    ("invalid", ("PS-N",),         UNASSIGNED,  UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      True),
    ("invalid", ("PS-A",),         "",          "alpha",    area_resolve.SOURCE_PRODUCT_SCOPE, True),
    ("invalid", ("PS-A", "PS-B"),  123,         UNASSIGNED, area_resolve.SOURCE_RESIDUAL,      True),
]


def test_precedence_matrix_own_x_parents():
    """ASSERTED BEATS DERIVED ([[ADR-124]] section 1, the user-decided design fork): an own VALID area
    wins over the owner_refs join INCLUDING over the 2+-parent ambiguity fallback. With no own area (or
    an INVALID one, which degrades to absent) today's derived rule is byte-unchanged: exactly one parent
    carrying an area resolves to it; 0 or 2+ parents resolve to the residual sentinel."""
    area_map = {k: v for k, v in _AREA_MAP.items() if v is not None}
    for label, refs, own, want_area, want_source, want_admitted in _MATRIX:
        cand = _cand(f"SC-{label}", refs, area=own)
        got_area, got_source = area_resolve.resolve(cand, area_map)
        assert (got_area, got_source) == (want_area, want_source), (label, refs, own, got_area, got_source)
        assert area_resolve.has_area_source(cand) is want_admitted, (label, refs, own)


def test_precedence_matrix_through_the_candidates_top_cli(tmp_path):
    """The SAME matrix through the lens entry point — MV2's concrete obligation: the rule must hold
    identically at the unit level and at the only surface a user actually picks from."""
    scope = [_ps_item("PS-A", "alpha"), _ps_item("PS-B", "beta"), _ps_item("PS-N")]
    live = [_cand(f"SC-{i:03d}", refs, area=own)
            for i, (_label, refs, own, _a, _s, _adm) in enumerate(_MATRIX, 1)]
    _write(tmp_path, scope, live)

    expected_by_area: dict[str, set[str]] = {}
    expected_source: dict[str, str] = {}
    for i, (_label, _refs, _own, want_area, want_source, want_admitted) in enumerate(_MATRIX, 1):
        if want_admitted:
            expected_by_area.setdefault(want_area, set()).add(f"SC-{i:03d}")
            expected_source[f"SC-{i:03d}"] = want_source

    for area in ("alpha", "beta", "own", UNASSIGNED):
        ids, payload = _ids(tmp_path, "--top", "0", "--area", area)
        assert set(ids) == expected_by_area.get(area, set()), (area, ids)
        for cid in ids:
            # M6: the rendered provenance must agree with the unit-level resolution, row for row —
            # `residual` is legitimate for the `unassigned` bucket and ONLY for it.
            assert payload["area_lens"]["sources"][cid] == expected_source[cid], (area, cid)
            if payload["area_lens"]["sources"][cid] == area_resolve.SOURCE_RESIDUAL:
                assert area == UNASSIGNED, (area, cid)


def test_two_parent_candidate_with_own_area_overrides_the_ambiguity_fallback(tmp_path):
    """The single most contestable cell, called out explicitly because the tournament SPLIT on it and the
    user decided override: a 2-parent candidate that ALSO asserts its own area files under its OWN area,
    NOT `unassigned`. The ambiguous case WITHOUT an own area still lands in `unassigned`."""
    _write(tmp_path, [_ps_item("PS-A", "alpha"), _ps_item("PS-B", "beta")],
           [_cand("SC-001", ["PS-A", "PS-B"], area="own"),      # own wins over the ambiguity fallback
            _cand("SC-002", ["PS-A", "PS-B"])])                 # still ambiguous -> unassigned
    own, _ = _ids(tmp_path, "--area", "own")
    assert own == ["SC-001"], own
    unassigned, _ = _ids(tmp_path, "--area", UNASSIGNED)
    assert unassigned == ["SC-002"], unassigned


# ── read-side TOTALITY — the compensating control for the named-open `rewrite` leg ──

def test_own_area_is_total_over_every_stored_value():
    """`vault_edit rewrite` bypasses every write guard (SC-168) and `/commit-slice` runs it on EVERY
    ship, so a garbage area CAN reach the store. own_area must degrade it to absent — never raise, never
    surface it ([[ADR-124]] section 5)."""
    for garbage in ("", "   ", "unassigned", "UNASSIGNED", 123, [], {}, ["x"], 1.5, True, None):
        assert area_resolve.own_area({"area": garbage}) is None, garbage
    for weird in ("not a dict", 7, None, []):
        assert area_resolve.own_area(weird) is None, weird
    assert area_resolve.own_area({"area": "  billing  "}) == "billing", "the MATCHED value is stripped"


def test_invalid_stored_area_never_surfaces_in_the_lens(tmp_path):
    """End to end: a rewrite-injected invalid area neither crashes the pick digest nor mints a bucket."""
    _write(tmp_path, [_ps_item("PS-100", "payments")],
           [_chore("SC-001", area="   "), _chore("SC-002", area=42), _chore("SC-003", area="unassigned")])
    cp = _run(tmp_path, "--top", "5")                  # the /slice injection surface: must not traceback
    assert cp.returncode == 0, cp.stderr
    for area in ("payments", UNASSIGNED, "42", "   "):
        ids, payload = _ids(tmp_path, "--area", area)
        assert ids == [], (area, ids)
        assert not any(a in ("", "   ", "42", 42) for a in payload["area_lens"]["areas"])


# ── critique m2 — the split-bucket near-match signal (advisory, never a filter) ──

def test_near_match_surfaces_a_case_variant_bucket(tmp_path):
    _write(tmp_path, [], [_chore("SC-001", area="write-seams")])
    ids, payload = _ids(tmp_path, "--area", "Write-Seams")
    assert ids == [], "a case variant is a DIFFERENT bucket — it must not silently match"
    assert payload["area_lens"]["near_matches"] == ["write-seams"]
    cp = _run(tmp_path, "--area", "Write-Seams")       # text path carries the WARN
    assert "WARN" in cp.stdout and "write-seams" in cp.stdout, cp.stdout
    exact, payload_exact = _ids(tmp_path, "--area", "write-seams")
    assert exact == ["SC-001"] and payload_exact["area_lens"]["near_matches"] == []


# ── MV2 (spike-A1 constraint 4) — EXACTLY ONE precedence site ──

def test_candidates_top_derives_no_area_of_its_own():
    """The invariant the design carried as an OPEN build obligation: the lens must delegate ALL area
    logic to area_resolve, so a second precedence rule cannot appear here. Enforced as a source scan
    over the consumer: it may not read a candidate's `area` key, and it may not call either half of the
    derived arm (owner_refs / candidate_area) directly."""
    hits = _own_derivation_hits(TOP.read_text(encoding="utf-8"))
    assert hits == [], (
        f"candidates_top.py derives an area itself ({hits}) — all area logic must route through "
        f"scripts/lib/area_resolve.py (MV2: exactly one precedence site)")
    body = _scannable(TOP.read_text(encoding="utf-8"))
    assert "area_resolve.resolve(" in body and "area_resolve.has_area_source(" in body


def test_the_single_precedence_site_scan_fires_on_a_mutated_copy():
    """BC-PROJ-12's negative control: a positive-only assertion cannot distinguish "the scan passed"
    from "the scan silently stopped matching". Each way a second precedence rule could reappear here
    must actually trip it."""
    clean = TOP.read_text(encoding="utf-8")
    for mutation in ('    own = c.get("area")\n',
                     '    own = cand["area"]\n',
                     "    refs = product_scope.owner_refs(c)\n",
                     "    derived = product_rollup.candidate_area(c, area_map)\n"):
        mutated = clean.replace("    area_lens = None\n",
                                "    area_lens = None\n" + mutation, 1)
        assert mutated != clean, "the control could not inject its mutation — re-anchor it"
        assert _own_derivation_hits(mutated), f"the scan did NOT fire on: {mutation.strip()}"


def test_default_off_lens_takes_no_area_import(tmp_path):
    """Default-OFF is a no-import, no-filter path — the structural half of the AC2 byte-identity claim."""
    src = TOP.read_text(encoding="utf-8")
    assert "from scripts.lib import area_resolve" in src
    assert "\nfrom scripts.lib import area_resolve" not in src, (
        "the area_resolve import must stay INSIDE the `if args.area is not None:` block, never module-level")
