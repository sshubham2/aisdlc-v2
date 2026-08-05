"""slice-102 / SC-232 (AC4) — `product_scope add-item`, the incremental capability verb.

THE PLACEMENT DECISION ([[ADR-145]], carried through [[ADR-149]]/[[ADR-152]]). The write half COLLAPSES
into the module that owns the lock: `skills/slice/scripts/scope_append.py` was DELETED from the design
and never written. Six round-2 findings were consequences of that ONE placement, so they dissolve rather
than being fixed six times. The payload is composed IN-LOCK from the same `cur` that is written, so the
read-then-compose-then-write TOCTOU is UNREPRESENTABLE rather than merely closed.

THE LOAD-BEARING ADDITION is a PRE-MINT `_plan` check. `safe_mutate_text` leaves the target UNTOUCHED on
a raise and the allocator mutates only the in-closure `data`, so a refused add burns **ZERO PS ids** —
which is what makes must-not-defer #2 ("no silent partial write") STRUCTURAL rather than
detected-after-the-fact. That matters because a PS mint is not cheaply reversible: `revise --cut` retires
the id but leaves its materialized candidate untouched, and this module's own docstring records that the
owner-deletion cascade "is not even expressible". There is no un-mint.

THE ASSERTION THAT CATCHES THE REAL BUG IS ON THE CHILD, NOT THE PARENT (design-spike constraint 2).
`status == 'ok'` plus `counters.ps == before + 1` PASSES while the candidate is silently missing — the
spike's own first pass did exactly that and reported PASS on a run that had minted a PS with no child.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from scripts.lib import area_resolve, product_rollup, product_scope  # noqa: E402

BLOBS = Path(__file__).resolve().parent / "fixtures" / "completion-gap"
SCRIPT = PLUGIN_ROOT / "scripts" / "lib" / "product_scope.py"
API_REF = PLUGIN_ROOT / "docs" / "api-reference.md"


# ── helpers ──────────────────────────────────────────────────────────────────────────────────

def _env():
    import os
    e = dict(os.environ)
    e.pop("AI_SDLC_VAULT_ROOT", None)      # never the developer's real vault
    return e


def _cli(vault, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env=_env())


def _vault(tmp_path, name, dest=None):
    v = tmp_path / (dest or name)
    shutil.copytree(BLOBS / name, v)
    return v


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read(vault, rel):
    return json.loads((vault / rel).read_text(encoding="utf-8"))


def _counters_ps(vault) -> int:
    return int((_read(vault, "product-scope.json").get("counters") or {}).get("ps") or 0)


def _item(title="build-approval-workflow", label="approve-expenses", area=None, **over):
    it = {
        "label": label,
        "title": title,
        "description": "Route an expense to an approver and record the decision.",
        "user_visible_outcome": "A manager can approve or reject an expense and the submitter sees why.",
        "verification_plan": "Submit a real expense, approve it as a real manager, confirm both sides.",
        "assumptions": [{"id": "A1", "statement": "the approver directory is reachable without SSO",
                         "blocking": True, "spike_status": "unproven"}],
    }
    if area is not None:
        it["area"] = area
    it.update(over)
    return it


def _payload(tmp_path, *items, name="item.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"items": list(items)}), encoding="utf-8")
    return p


def _product_children(vault, ps_id):
    """Live candidates whose provenance names this capability — the CHILD, which is what a mint is FOR."""
    return [c for c in _read(vault, "candidates.json")["candidates"]
            if product_scope.owner_ref(c) == ps_id]


def _out_items_projection(items):
    """revise's OWN `out_items` shape. SEMANTIC identity, never byte-identity: round-1 B1 proved raw
    equality unsatisfiable on this repo's 9-key vault, because the projection legitimately BACKFILLS
    `code_components` on a legacy item."""
    return [{"id": i.get("id"), "decomposition_label": i.get("decomposition_label"),
             "title": i.get("title"), "description": i.get("description"),
             "user_visible_outcome": i.get("user_visible_outcome"),
             "area": i.get("area") or i.get("component"),
             "code_components": i.get("code_components") or [],
             "depends_on": i.get("depends_on") or [],
             # the projection's OWN assumption arm -- every shipped writer runs it, so a persisted
             # vault always carries the normalized 8-key shape
             "assumptions": product_scope._normalize_assumptions(i),
             "verification_plan": i.get("verification_plan")} for i in items]


# ── (a) refuse BEFORE the mint — the whole basis for replacing verify-after ───────────────────

@pytest.mark.parametrize("fixture", ["all-built", "nine-key"])
def test_refused_add_burns_no_ps_id_and_successful_add_mints_exactly_one(tmp_path, fixture):
    """(a)+(b). A title colliding with an UNOWNED candidate is refused by `_plan` BEFORE the allocator
    runs, so product-scope.json is BYTE-identical and `counters.ps` is UNCHANGED; then `--acknowledge`
    on the SAME input succeeds and mints exactly one CHILD."""
    v = _vault(tmp_path, fixture)
    before_sha = _sha(v / "product-scope.json")
    before_cands_sha = _sha(v / "candidates.json")
    before_ps = _counters_ps(v)

    # `pin-the-ci-runner-image` is a live candidate with NO product-scope provenance
    colliding = _payload(tmp_path, _item(title="pin-the-ci-runner-image", label="pin-ci-image"))
    r = _cli(v, "add-item", "--item-file", colliding, "--json")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "already carries this scope item's title" in r.stderr, r.stderr
    assert _sha(v / "product-scope.json") == before_sha, "a REFUSED add wrote to product-scope.json"
    assert _sha(v / "candidates.json") == before_cands_sha
    assert _counters_ps(v) == before_ps, "a REFUSED add burned a PS id — there is no un-mint"

    # the remedy the refusal names, available BEFORE the mint rather than only after it
    r = _cli(v, "add-item", "--item-file", colliding, "--acknowledge", "pin-ci-image", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok", out
    assert _counters_ps(v) == before_ps + 1
    # THE ASSERTION THAT CATCHES THE REAL BUG: on the CHILD, not the parent.
    assert len(out["materialize"]["minted"]) == 1, out["materialize"]
    ps_id = out["added"]
    assert _product_children(v, ps_id), (
        "a PS was minted with NO candidate — status+counters would have reported PASS")


# ── (c)+(d) the ordinary add ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fixture,raw_equal", [("all-built", True), ("nine-key", False)])
def test_non_colliding_add_mints_one_ps_one_candidate_and_preserves_every_prior_item(
        tmp_path, fixture, raw_equal):
    """(c) — every pre-existing item is equal under revise's OWN `out_items` projection. The 9-key
    fixture is pinned as the NEGATIVE: raw byte-equality is FALSE there, which is what makes the
    semantic assertion honest rather than vacuous."""
    v = _vault(tmp_path, fixture)
    before_items = _read(v, "product-scope.json")["items"]
    before_ids = {i["id"] for i in before_items}
    before_cands = {c["id"] for c in _read(v, "candidates.json")["candidates"]}

    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item(area="approvals")), "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)

    after = _read(v, "product-scope.json")["items"]
    new_ids = {i["id"] for i in after} - before_ids
    assert len(new_ids) == 1 and out["added"] in new_ids, (out, new_ids)
    after_cands = {c["id"] for c in _read(v, "candidates.json")["candidates"]}
    assert len(after_cands - before_cands) == 1, after_cands - before_cands

    kept = [i for i in after if i["id"] in before_ids]
    assert _out_items_projection(kept) == _out_items_projection(before_items), (
        "a pre-existing item changed SEMANTICALLY")
    assert (kept == before_items) is raw_equal, (
        "the 9-key fixture must NOT be raw-equal -- the projection backfills code_components, which is "
        "exactly why byte-identity was the wrong assertion (round-1 B1)")


def test_the_new_candidate_carries_score_five_a_resolving_owner_ref_and_a_derived_area(tmp_path):
    """(d)+(vii) — the elicited `area` travels in the ITEM FILE (the channel production uses,
    BC-PROJ-10), is written on the SCOPE item, and is DERIVED on the candidate, never written onto it."""
    v = _vault(tmp_path, "all-built")
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item(area="approvals")), "--json")
    assert r.returncode == 0, r.stderr
    ps_id = json.loads(r.stdout)["added"]

    scope = _read(v, "product-scope.json")
    new_item = next(i for i in scope["items"] if i["id"] == ps_id)
    assert new_item["area"] == "approvals"

    child = _product_children(v, ps_id)
    assert len(child) == 1, child
    cand = child[0]
    assert cand["priority"]["score"] == product_scope.PRODUCT_PRIORITY["score"] == 5
    assert product_scope.owner_ref(cand) == ps_id
    assert "area" not in cand, "the mint writes NO area key -- the candidate's area is DERIVED"
    assert area_resolve.resolve(cand, product_rollup.ps_area_map(scope)) == (
        "approvals", area_resolve.SOURCE_PRODUCT_SCOPE)


def test_an_unannotated_add_leaves_the_area_key_ABSENT(tmp_path):
    """`_valid_area` REFUSES the `unassigned` sentinel, so the legal un-annotated shape is an ABSENT
    key -- never the sentinel written verbatim."""
    v = _vault(tmp_path, "all-built")
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--json")
    assert r.returncode == 0, r.stderr
    ps_id = json.loads(r.stdout)["added"]
    new_item = next(i for i in _read(v, "product-scope.json")["items"] if i["id"] == ps_id)
    assert not new_item.get("area"), new_item


# ── (e)+(f)+(ii) the usage refusals, each matching its INTENDED message ───────────────────────

def test_a_two_item_payload_and_an_id_carrying_item_each_exit_two(tmp_path):
    """(e)+(i). argparse's own usage errors ALSO exit 2 on this table, so every assertion pins a
    substring of the INTENDED refusal — a bare `returncode == 2` would pass on a typo'd flag."""
    v = _vault(tmp_path, "all-built")
    before = _sha(v / "product-scope.json")

    two = _payload(tmp_path, _item(), _item(title="build-budget-alerts", label="budget-alerts"),
                   name="two.json")
    r = _cli(v, "add-item", "--item-file", two, "--json")
    assert r.returncode == 2 and "exactly ONE item" in r.stderr, r.stderr

    with_id = _payload(tmp_path, _item(id="PS-001"), name="withid.json")
    r = _cli(v, "add-item", "--item-file", with_id, "--json")
    assert r.returncode == 2 and "identity is minted by the receiver" in r.stderr, r.stderr

    assert _sha(v / "product-scope.json") == before and _counters_ps(v) == 2


def test_add_item_against_an_absent_scope_exits_four_no_scope(tmp_path):
    """(f) — which is exactly WHY `scope-absent` must route to /discover and never here."""
    v = _vault(tmp_path, "all-built")
    (v / "product-scope.json").unlink()
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--json")
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "has never been decomposed" in r.stderr or "ABSENT" in r.stderr, r.stderr


@pytest.mark.parametrize("area,needle", [("unassigned", "reserved sentinel"),
                                         (3, "area must be a string")])
def test_a_reserved_or_non_string_area_refuses_through_the_single_sourced_gate(tmp_path, area, needle):
    """(ii) — `_load_items` is the ONE line that restores `_valid_area` on this FOURTH write seam."""
    v = _vault(tmp_path, "all-built")
    before, before_ps = _sha(v / "product-scope.json"), _counters_ps(v)
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item(area=area)), "--json")
    assert r.returncode == 2 and needle in r.stderr, r.stderr
    assert _sha(v / "product-scope.json") == before and _counters_ps(v) == before_ps


# ── (g)+(iv) --dry-run IS the preview ────────────────────────────────────────────────────────

def test_dry_run_writes_nothing_mints_nothing_and_previews_BOTH_files(tmp_path):
    """(g) — the preview is PREDICTIVE because it is the same code path, and it previews both files:
    `would_add` (the PS) alongside `would_mint` (the SC), so the confirmation renders every record that
    would be created, not just the parent."""
    v = _vault(tmp_path, "all-built")
    shas = {rel: _sha(v / rel) for rel in ("product-scope.json", "candidates.json",
                                           "archive/candidates.json")}
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert len(out["would_add"]) == 1 and out["would_add"][0].startswith("PS-")
    assert out["materialize"]["would_mint"] == out["would_add"], out["materialize"]
    assert out["materialize"]["minted"] == []
    for rel, sha in shas.items():
        assert _sha(v / rel) == sha, f"--dry-run wrote to {rel}"
    assert _counters_ps(v) == 2


def test_dry_run_names_a_contract_invalid_pre_existing_item_at_preview_time(tmp_path):
    """(g) — `_check_contract` runs over the WHOLE composed list, so a legacy item that would fail the
    crossing is named at PREVIEW time rather than at write time."""
    v = _vault(tmp_path, "all-built")
    scope = _read(v, "product-scope.json")
    scope["items"][1]["verification_plan"] = "   "        # present but empty -> Phase-1 refusal
    (v / "product-scope.json").write_text(json.dumps(scope, indent=2), encoding="utf-8")
    r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--dry-run", "--json")
    assert r.returncode == 2, (r.returncode, r.stdout)
    assert "verification_plan" in r.stderr and "export-expenses" in r.stderr, r.stderr


def test_add_item_mints_only_its_own_child(tmp_path):
    """(iv) — `_plan`'s no-children fall-through would ALSO mint candidates for OTHER pre-existing
    capabilities, turning "add one capability" into a bulk materialize nobody asked for, against an
    append-only file with no un-mint. 'Exactly one' is a property of the VERB, not of the call context.

    Asserted on BOTH modes: the refusal NAMES every would-be mint (that list IS the preview), and it
    fires identically under `--dry-run` — a preview that exited 0 where the real run exits 2 would be
    worse than no preview, because the driver BRANCHES on that exit code.
    """
    v = _vault(tmp_path, "all-built", dest="no-children")
    arch = _read(v, "archive/candidates.json")
    arch["candidates"] = [c for c in arch["candidates"] if c["id"] != "SC-002"]
    (v / "archive" / "candidates.json").write_text(json.dumps(arch, indent=2), encoding="utf-8")
    before, before_ps = _sha(v / "product-scope.json"), _counters_ps(v)

    for mode in (["--dry-run"], []):
        r = _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), *mode, "--json")
        assert r.returncode == 2, (mode, r.returncode, r.stdout)
        # BOTH would-be candidates are named: the pre-existing orphan AND this item's own
        assert "PS-002" in r.stderr and "approve-expenses" in r.stderr, (mode, r.stderr)
        assert "materialize" in r.stderr, (mode, r.stderr)
        assert _sha(v / "product-scope.json") == before and _counters_ps(v) == before_ps


# ── (h)+(v) the shared `_apply_items` boundary ───────────────────────────────────────────────

def test_revise_roundtrip_and_add_item_produce_identical_out_items_for_preexisting(tmp_path):
    """(h) — the extraction is BEHAVIOUR-PRESERVING. `revise` with a round-tripped payload and
    `add-item` must agree on every pre-existing item, or the shared helper has drifted."""
    a = _vault(tmp_path, "nine-key", dest="via-add-item")
    b = _vault(tmp_path, "nine-key", dest="via-revise")

    r = _cli(a, "add-item", "--item-file", _payload(tmp_path, _item()), "--json")
    assert r.returncode == 0, r.stderr

    kept = copy.deepcopy(_read(b, "product-scope.json")["items"])
    round_trip = _payload(tmp_path, *kept, _item(), name="revise.json")
    r = _cli(b, "revise", "--items-file", round_trip, "--json")
    assert r.returncode == 0, r.stderr

    ids = {i["id"] for i in _read(b, "product-scope.json")["items"]}
    a_items = [i for i in _read(a, "product-scope.json")["items"] if i["id"] in ids]
    b_items = [i for i in _read(b, "product-scope.json")["items"] if i["id"] in ids]
    assert a_items == b_items, "add-item and revise disagree on a pre-existing item"


def test_characterization_revise_pre_lock_refusals_are_unmoved(tmp_path):
    """(v) — `_apply_items` must not swallow `cmd_revise`'s PRE-LOCK refusals. Driven THROUGH THE CLI,
    so the pre-lock path is what actually runs."""
    v = _vault(tmp_path, "all-built")
    before = _sha(v / "product-scope.json")
    kept = copy.deepcopy(_read(v, "product-scope.json")["items"])
    kept[0]["depends_on"] = ["PS-does-not-exist"]
    r = _cli(v, "revise", "--items-file", _payload(tmp_path, *kept, name="bad.json"), "--json")
    assert r.returncode == 2, (r.returncode, r.stdout)
    assert "not an item in this decomposition" in r.stderr, r.stderr
    assert _sha(v / "product-scope.json") == before


def test_add_item_refuses_an_unresolvable_dependency_in_lock(tmp_path):
    """The same CR3 gate on the NEW verb. It runs IN-LOCK here (unlike revise's pre-lock placement)
    because the composed list is only knowable once `cur` has been read under the lock — and a raise
    inside `safe_mutate_text` leaves the target UNTOUCHED, so the refusal is byte-identical either way."""
    v = _vault(tmp_path, "all-built")
    before = _sha(v / "product-scope.json")
    r = _cli(v, "add-item", "--item-file",
             _payload(tmp_path, _item(depends_on=["nope"]), name="dep.json"), "--json")
    assert r.returncode == 2 and "not an item in this decomposition" in r.stderr, r.stderr
    assert _sha(v / "product-scope.json") == before and _counters_ps(v) == 2


# ── (iii) the DERIVED status ─────────────────────────────────────────────────────────────────

def test_a_mintless_materialize_reports_partial_at_exit_two_never_ok(tmp_path, monkeypatch):
    """(iii) — `status` is DERIVED from the mint, never asserted ([[ADR-149]] d1). A minted PS whose
    child never appeared is named in the SESSION THAT CAUSED IT rather than reported as success.

    Driven IN-PROCESS with `_materialize` stubbed: the real trigger is the residual two-lock window
    (product-scope.json is written, then candidates.json is locked separately), which cannot be produced
    deterministically inside one process. The DERIVATION is what this pins, and the derivation is the
    thing that was wrong — the spike's own T2 asserted only `status`+`counters` and reported PASS on a
    run that had minted a PS with no child.
    """
    v = _vault(tmp_path, "all-built")
    real = product_scope._materialize

    def _mintless(vault, items, *, dry_run, acknowledge, ts):
        out = real(vault, items, dry_run=True, acknowledge=acknowledge, ts=ts)
        out.update({"minted": [], "minted_count": 0, "dry_run": False,
                    "refused": [{"item": "PS-003", "title": "build-approval-workflow",
                                 "reason": "a candidate (SC-777) already carries this scope item's "
                                           "title but does NOT carry its product-scope provenance."}]})
        return out
    monkeypatch.setattr(product_scope, "_materialize", _mintless)

    from types import SimpleNamespace
    args = SimpleNamespace(item_file=str(_payload(tmp_path, _item())), area=None,
                           dry_run=False, acknowledge=[])
    with pytest.raises(product_scope._Refuse) as exc:
        product_scope.cmd_add_item(v, args)
    assert exc.value.code == 2 and exc.value.status == "partial", exc.value
    assert "SC-777" in exc.value.message, "the child's remedy must be echoed VERBATIM"
    assert _counters_ps(v) == 3, "the PS really was minted -- that is what makes this state PARTIAL"


# ── (vi) the exit-code branch the DRIVER keys on ─────────────────────────────────────────────

def test_refused_preview_never_reaches_the_confirmation_or_the_mint(tmp_path):
    """(vi) / [[ADR-152]] d4. `/slice-candidates --add-item` BRANCHES on the preview's exit code:
    0 -> preview + confirm; 2 -> render the refusal VERBATIM, re-elicit, re-preview, NEVER confirm and
    NEVER mint. Round-4 M-add-2: the previous design printed unconditionally, with no branch at all."""
    v = _vault(tmp_path, "all-built")
    shas = {rel: _sha(v / rel) for rel in ("product-scope.json", "candidates.json")}
    bad = _payload(tmp_path, _item(verification_plan="  "), name="novp.json")

    preview = _cli(v, "add-item", "--item-file", bad, "--dry-run", "--json")
    assert preview.returncode == 2, (preview.returncode, preview.stdout)
    assert "verification_plan" in preview.stderr, preview.stderr
    # the refusal's own message reaches the SURFACE (--json echoes it as `error`)
    assert "verification_plan" in json.loads(preview.stdout)["error"]
    for rel, sha in shas.items():
        assert _sha(v / rel) == sha, f"a REFUSED preview touched {rel}"
    assert _counters_ps(v) == 2

    # and the exit codes the driver branches on are the SHIPPED table -- no new code, no exit 5
    assert _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--dry-run").returncode == 0
    (v / "product-scope.json").unlink()
    assert _cli(v, "add-item", "--item-file", _payload(tmp_path, _item()), "--dry-run").returncode == 4


# ── (viii) FBCD-1 — the two VERIFIED verb enumerations ───────────────────────────────────────

def test_the_verb_enumerations_name_add_item():
    """Round-4 M4, trimmed by DR-1 to the two enumerations that were actually verified. `user-guide` and
    `README` document `set-area` SPECIFICALLY — they are not verb enumerations and are out of scope."""
    doc = SCRIPT.read_text(encoding="utf-8")
    synopsis = doc.split('"""')[1]
    assert "add-item --item-file" in synopsis, "the module docstring's usage synopsis"
    exit4 = next(ln for ln in synopsis.splitlines() if "4  product-scope.json ABSENT" in ln)
    assert "add-item" in exit4, f"the exit-4 verb list still omits add-item: {exit4}"

    api = API_REF.read_text(encoding="utf-8")
    assert "| `add-item` |" in api, "docs/api-reference.md's per-verb table"
    exit_line = next(ln for ln in api.splitlines()
                     if ln.startswith("Exit codes:") and "product-scope.json" in ln)
    assert "add-item" in exit_line, f"the api-reference exit-code line still omits add-item: {exit_line}"


def test_the_cli_surface_takes_vault_which_is_what_keeps_a_write_off_another_project(tmp_path):
    """`parents=[common]` is MANDATORY: it is what gives the verb `--vault`, the flag that prevents the
    RECORDED slice-081 accident where a producer smoke without it wrote to the real shared vault."""
    r = _cli(_vault(tmp_path, "all-built"), "add-item", "--help")
    assert r.returncode == 0, r.stderr
    for flag in ("--vault", "--item-file", "--area", "--dry-run", "--acknowledge", "--json"):
        assert flag in r.stdout, (flag, r.stdout)
    assert "add-item" in product_scope._DISPATCH
