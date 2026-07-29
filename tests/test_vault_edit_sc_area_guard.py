"""slice-098 / SC-212 ([[ADR-125]] sections 4/5/6) — the candidate-level `area` WRITE seams.

AC1: the field is settable through a MEDIATED seam, and the reject set (empty / whitespace-only /
non-string / the reserved `unassigned` sentinel) is REFUSED at a non-zero exit with candidates.json
left BYTE-IDENTICAL. AC5: the mediation is not trivially bypassable — every sibling `vault_edit` verb
that reaches the field is driven here and its ACTUAL behaviour recorded, including the one leg that is
NOT closed.

The mediated seam is the GUARDED GENERIC VERB, not a typed producer ([[ADR-125]] section 2 dropped the
verb from this cut):

    vault_edit update --file candidates.json --array candidates --id SC-NNN --set area=NAME \\
                      --append history '{"event":"area-set",...}'

Both flags land in ONE mutate closure, so the annotation and its witness are atomic. The witness is
CONVENTIONAL, not enforced — a caller who omits the `--append` writes an unwitnessed area and nothing
refuses that. That limitation is asserted here so it stays a stated contract rather than an assumption.
"""
from __future__ import annotations

import hashlib
import json

VE = "scripts/lib/vault_edit.py"

# The reject set, single-sourced from product_scope._valid_area (never re-implemented here — a
# re-implemented set is precisely the SC-185 area-parity drift; these are the CLI-level shapes).
_REJECT_VALUES = ["", "   ", "unassigned", "UNASSIGNED", "UnAsSiGnEd", "123", "[1]", "{}", "true"]


def _ve(run_script, vault, *args, stdin=None):
    return run_script(VE, ["--vault", str(vault), *args], stdin=stdin)


def _sha(vault):
    return hashlib.sha256((vault / "candidates.json").read_bytes()).hexdigest()


def _cands(vault):
    return json.loads((vault / "candidates.json").read_text(encoding="utf-8"))["candidates"]


def _seed(vault, extra=()):
    """A live backlog with one exhaust-sourced chore and one product-sourced candidate."""
    cands = [
        {"id": "SC-001", "title": "chore-one", "status": "candidate", "progress": "not-started",
         "slice": None, "claimed_by": None, "started_at": None,
         "source": [{"type": "risk", "ref": "R-1"}],
         "priority": {"score": 5, "effort": "S"}, "history": []},
        {"id": "SC-002", "title": "cap-one", "status": "candidate", "progress": "not-started",
         "slice": None, "claimed_by": None, "started_at": None,
         "source": [{"type": "product-scope", "ref": "PS-1"}],
         "priority": {"score": 4, "effort": "S"}, "history": []},
        *extra,
    ]
    (vault / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": cands,
         "pick_log": [], "counters": {"sc": 2}}, indent=2), encoding="utf-8")


# ── AC1 — the mediated seam accepts a real area and refuses the reject set byte-identically ──

def test_update_leg_annotates_a_candidate_through_the_sanctioned_seam(run_script, vault):
    _seed(vault)
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-001", "--set", "area=write-seams",
            "--append", "history", '{"event":"area-set","by":"t","at":"<ts>","ref":"write-seams"}')
    assert r.returncode == 0, r.stderr
    rec = next(c for c in _cands(vault) if c["id"] == "SC-001")
    assert rec["area"] == "write-seams"
    # ATOMIC witness: annotation + history event land in the SAME mutate closure, so a written area can
    # never be half-witnessed by a crash between two commands.
    assert rec["history"][-1] == {"event": "area-set", "by": "t", "at": "<ts>", "ref": "write-seams"}


def test_update_leg_rejects_reject_set_byte_identical(run_script, vault):
    """AC1's core: every reject-set shape exits non-zero, names the offending value, and leaves the file
    BYTE-IDENTICAL — the refusal happens inside the mutate closure, so safe_mutate_text writes nothing."""
    _seed(vault)
    before = _sha(vault)
    for bad in _REJECT_VALUES:
        r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
                "--id", "SC-001", "--set", f"area={bad}")
        assert r.returncode == 2, (bad, r.returncode, r.stdout, r.stderr)
        assert "refusing" in r.stderr, (bad, r.stderr)
        assert _sha(vault) == before, f"candidates.json mutated on a refused area {bad!r}"
    assert "area" not in _cands(vault)[0]


def test_append_verb_on_area_is_refused_too(run_script, vault):
    """The slice-093 one-verb-over differential: `--append area` makes the field a LIST, which the
    recognizer's type-guard refuses. A `--set`-only guard would have left this leg open."""
    _seed(vault)
    before = _sha(vault)
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-001", "--append", "area", '"write-seams"')
    assert r.returncode == 2, r.stdout
    assert _sha(vault) == before


def test_supplied_component_on_a_candidate_is_refused_outright(run_script, vault):
    """[[ADR-125]] section 4's CHOSEN COMPONENT CONTRACT. DR-1 proved the shipped kind=='ps' guard accepts
    `--set component=<anything>` at rc=0 (it judges area first and never falls through), so this slice
    must NOT mirror that mechanism. A candidate carries `area`; a stored `component` would be read by
    nothing — the silently-inert JOIN twin."""
    _seed(vault)
    before = _sha(vault)
    for value in ("frontend", "", "null"):
        r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
                "--id", "SC-001", "--set", f"component={value}")
        assert r.returncode == 2, (value, r.stdout, r.stderr)
        assert "component" in r.stderr
        assert _sha(vault) == before, f"candidates.json mutated on a refused component {value!r}"


def test_guard_is_supplied_only_and_does_not_re_judge_a_stored_value(run_script, vault):
    """The shipped SUPPLIED-ONLY pragma (vault_edit.py's ps precedent), stated as a deviation in the
    design: an unrelated update on a candidate carrying a pre-existing invalid area must NOT be blocked —
    the guard mediates INGEST, and the read side is what tolerates a bad stored value."""
    _seed(vault, extra=[{"id": "SC-003", "title": "legacy", "status": "candidate",
                         "progress": "not-started", "source": [{"type": "risk", "ref": "R-2"}],
                         "area": "   ", "priority": {"score": 1, "effort": "S"}, "history": []}])
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-003", "--set", "status=deferred")
    assert r.returncode == 0, r.stderr
    rec = next(c for c in _cands(vault) if c["id"] == "SC-003")
    assert rec["status"] == "deferred" and rec["area"] == "   ", "a stored value must not be re-judged"


def test_assumption_sub_record_edit_is_out_of_the_guard_scope(run_script, vault):
    """An `--assumption` edit writes into the assumption dict, not the candidate record — mirroring the
    ps pragma, the guard does not fire there (and the resolver only ever reads the RECORD's `area`)."""
    _seed(vault, extra=[{"id": "SC-003", "title": "spiked", "status": "candidate",
                         "progress": "not-started", "source": [{"type": "risk", "ref": "R-2"}],
                         "assumptions": [{"id": "A1", "statement": "x", "blocking": True,
                                          "spike_status": "unproven"}],
                         "priority": {"score": 1, "effort": "S"}, "history": []}])
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-003", "--assumption", "A1", "--set", "spike_status=proven")
    assert r.returncode == 0, r.stderr
    rec = next(c for c in _cands(vault) if c["id"] == "SC-003")
    assert rec["assumptions"][0]["spike_status"] == "proven"
    assert "area" not in rec


# ── M3 — `--set area=null` is the SANCTIONED un-annotate seam ──

def test_set_area_null_is_the_sanctioned_un_annotate_seam(run_script, vault):
    """Closing every seam without an un-annotate path would leave the field ONE-WAY (critique M3): the
    reject set refuses `""`, `remove` and `set --path` refuse managed arrays, and `rewrite` is out of
    contract. `--set area=null` passes because the guard judges only a NON-None value, and it is
    sanctioned rather than left as an accidental side channel.

    CONTRACT, pinned here because it is the surprising half: the key REMAINS PRESENT with value null
    (`--set` cannot delete a key). `own_area`'s totality reads that as absent, but a reader comparing
    `'area' in cand` would see it differently."""
    _seed(vault)
    assert _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
               "--id", "SC-001", "--set", "area=payments").returncode == 0
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-001", "--set", "area=null")
    assert r.returncode == 0, r.stderr
    rec = next(c for c in _cands(vault) if c["id"] == "SC-001")
    assert "area" in rec and rec["area"] is None, "the un-annotate seam leaves the key present as null"


def test_un_annotated_candidate_drops_out_of_the_lens(run_script, vault):
    """The un-annotate seam is only meaningful if it actually un-files the candidate."""
    from scripts.lib import area_resolve
    _seed(vault)
    _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
        "--id", "SC-001", "--set", "area=payments")
    _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
        "--id", "SC-001", "--set", "area=null")
    rec = next(c for c in _cands(vault) if c["id"] == "SC-001")
    assert area_resolve.own_area(rec) is None
    assert area_resolve.has_area_source(rec) is False, "an un-annotated chore leaves the lens population"


# ── M2 — the MINT leg iterates LIST payloads ──

def test_mint_leg_rejects_invalid_area_in_a_LIST_payload(run_script, vault):
    """`_cmd_append` accepts a LIST (`arr.extend(element)`), minting one id per element — the bc mint
    guard loops for exactly this reason. The bad value sits in the SECOND element on purpose: a guard
    that inspects `element.get('area')` (or only element[0]) passes a naive first-element test while
    silently minting a real SC id with an unvalidated area at rc=0."""
    _seed(vault)
    before = _sha(vault)
    payload = json.dumps([
        {"title": "ok-one", "source": [{"type": "risk", "ref": "R-9"}], "area": "good"},
        {"title": "bad-two", "source": [{"type": "risk", "ref": "R-9"}], "area": ""},
    ])
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", payload)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert _sha(vault) == before, "a refused LIST mint must leave candidates.json byte-identical"
    assert len(_cands(vault)) == 2, "no element of a refused list payload may be minted"


def test_mint_leg_rejects_every_reject_value_and_a_supplied_component(run_script, vault):
    _seed(vault)
    before = _sha(vault)
    for bad in _REJECT_VALUES:
        elem = {"title": "x", "source": [{"type": "risk", "ref": "R-9"}], "area": json.loads(bad)
                if bad not in ("", "   ", "unassigned", "UNASSIGNED", "UnAsSiGnEd") else bad}
        r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
                "--json", json.dumps(elem))
        assert r.returncode == 2, (bad, r.stdout, r.stderr)
        assert _sha(vault) == before, f"mint leg mutated the file on a refused area {bad!r}"
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", json.dumps({"title": "x", "source": [{"type": "risk", "ref": "R-9"}],
                                  "component": "frontend"}))
    assert r.returncode == 2 and _sha(vault) == before


def test_mint_leg_does_not_over_tighten_existing_producers(run_script, vault):
    """DD1's gate condition: the guard must not break the producers that already append candidates
    (/discover, /slice-candidates, residue_disposition, build_backlog) — none of which supplies an area
    at all. A plain candidate still mints, and a VALID area still mints."""
    _seed(vault)
    plain = {"title": "plain", "source": [{"type": "reflection-deferred", "ref": "slice-072"}],
             "ejected_from": "slice-072", "ejection_reason": "out of budget"}
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", json.dumps(plain))
    assert r.returncode == 0, r.stderr
    r2 = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
             "--json", json.dumps({"title": "annotated", "source": [{"type": "risk", "ref": "R-9"}],
                                   "area": "resilience"}))
    assert r2.returncode == 0, r2.stderr
    minted = _cands(vault)
    assert [c["id"] for c in minted[-2:]] == ["SC-003", "SC-004"], "the allocator still mints in-lock"
    assert minted[-1]["area"] == "resilience"


def test_mint_leg_tolerates_an_explicit_null_area(run_script, vault):
    """absent/null is the legal un-annotated state, so a payload carrying `area: null` mints."""
    _seed(vault)
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", json.dumps({"title": "x", "source": [{"type": "risk", "ref": "R-9"}],
                                  "area": None}))
    assert r.returncode == 0, r.stderr


# ── critique m2 — the write-time split-bucket advisory (WARN, never a refusal) ──

def test_case_variant_area_warns_but_is_written(run_script, vault):
    _seed(vault)
    assert _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
               "--id", "SC-001", "--set", "area=write-seams").returncode == 0
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-002", "--set", "area=Write-Seams")
    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "write-seams" in r.stderr, r.stderr
    assert next(c for c in _cands(vault) if c["id"] == "SC-002")["area"] == "Write-Seams", \
        "the advisory must not silently rewrite or refuse the value"


def test_mint_leg_warns_on_a_case_variant_too(run_script, vault):
    """code-review CR1: the advisory must cover EVERY leg that can create the split. /reflect's residue
    capture and /build-slice's mint-split are the two production appenders and this slice invites both to
    carry an area, so a WARN only on the update leg would let the mint path silently create the second
    bucket the advisory exists to prevent."""
    _seed(vault)
    assert _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
               "--id", "SC-001", "--set", "area=write-seams").returncode == 0
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", json.dumps({"title": "m", "source": [{"type": "risk", "ref": "R-9"}],
                                  "area": "Write-Seams"}))
    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "write-seams" in r.stderr, r.stderr
    assert _cands(vault)[-1]["area"] == "Write-Seams", "advisory, not a refusal or a silent rewrite"


def test_mint_leg_warns_within_a_single_list_payload(run_script, vault):
    """The population grows as the payload is walked: element 2 must see element 1's area."""
    _seed(vault)
    r = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
            "--json", json.dumps([
                {"title": "a", "source": [{"type": "risk", "ref": "R-9"}], "area": "write-seams"},
                {"title": "b", "source": [{"type": "risk", "ref": "R-9"}], "area": "Write-Seams"},
            ]))
    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "write-seams" in r.stderr, r.stderr


def test_both_legs_persist_the_NORMALIZED_area(run_script, vault):
    """code-review CR2: `_valid_area`'s contract is check == persisted-value (slice-076) and the PS seam
    honours it. Storing the raw string would leave a check/write differential that only the read side
    collapses — a reader comparing `cand['area']` directly would disagree with the lens."""
    _seed(vault)
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-001", "--set", "area=  billing  ")
    assert r.returncode == 0, r.stderr
    assert next(c for c in _cands(vault) if c["id"] == "SC-001")["area"] == "billing"
    r2 = _ve(run_script, vault, "append", "--file", "candidates.json", "--array", "candidates",
             "--json", json.dumps({"title": "m", "source": [{"type": "risk", "ref": "R-9"}],
                                   "area": "  Payments  "}))
    assert r2.returncode == 0, r2.stderr
    assert _cands(vault)[-1]["area"] == "Payments"


def test_the_near_match_rule_is_not_re_derived(run_script, vault):
    """code-review CR4: exactly ONE function owns "is this the same bucket?", the same way the reject set
    is single-sourced from _valid_area. A second copy is the SC-185 area-parity hazard by name."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "lib" / "vault_edit.py").read_text(
        encoding="utf-8")
    body = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "area_resolve.near_matches(" in body
    assert ".casefold()" not in body, (
        "vault_edit re-derives the casefold near-match rule that area_resolve.near_matches owns")


def test_no_warn_when_the_spelling_matches_exactly(run_script, vault):
    _seed(vault)
    _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
        "--id", "SC-001", "--set", "area=write-seams")
    r = _ve(run_script, vault, "update", "--file", "candidates.json", "--array", "candidates",
            "--id", "SC-002", "--set", "area=write-seams")
    assert r.returncode == 0 and "WARN" not in r.stderr, r.stderr


# ── AC5 — the seam ledger: every sibling verb driven, its ACTUAL behaviour recorded ──

def test_ac5_sibling_verbs_that_already_refuse_a_managed_array(run_script, vault):
    """ALREADY REFUSED before this slice, re-driven so the claim rests on execution rather than on
    reading the guard: `set --path` and `remove` both refuse a managed array outright."""
    _seed(vault)
    before = _sha(vault)
    r_set = _ve(run_script, vault, "set", "--file", "candidates.json",
                "--path", "candidates[0].area", "--value", "junk")
    assert r_set.returncode != 0, r_set.stdout
    r_rm = _ve(run_script, vault, "remove", "--file", "candidates.json", "--array", "candidates",
               "--id", "SC-001")
    assert r_rm.returncode != 0, r_rm.stdout
    assert _sha(vault) == before


def test_ac5_rewrite_is_the_NAMED_OPEN_leg(run_script, vault):
    """The honest half of AC5, pinned as a CHARACTERIZATION test rather than asserted away.

    `vault_edit rewrite` bypasses the managed-kind guards entirely (SC-168) — proven live on the real
    candidates.json (101 -> 100 rows at rc=0). This is NOT a hypothetical hand-edit path: `/commit-slice`
    runs `rewrite --base-file` on the live candidates.json on EVERY ship, so a PRODUCTION path is out of
    contract. The shipped claim is therefore "validated at every `vault_edit` leg EXCEPT `rewrite`",
    never "the field cannot be written invalid".

    If this test ever FAILS, SC-168 has been fixed and the claim can be widened — that is the point of
    pinning the open behaviour instead of leaving it undocumented."""
    _seed(vault)
    doctored = json.loads((vault / "candidates.json").read_text(encoding="utf-8"))
    doctored["candidates"][0]["area"] = ""            # a reject-set value the update leg refuses
    body = vault / "rewrite-body.json"
    body.write_text(json.dumps(doctored, indent=2), encoding="utf-8")
    r = _ve(run_script, vault, "rewrite", "--file", "candidates.json", "--content-file", str(body),
            "--base-file", str(vault / "candidates.json"))
    assert r.returncode == 0, f"characterization: rewrite is expected to be OPEN today — {r.stderr}"
    assert _cands(vault)[0]["area"] == "", "characterization: rewrite writes an unvalidated area"

    # ...and the COMPENSATING CONTROL holds: the read side degrades that value to absent, so the
    # open leg cannot poison the /slice pick digest ([[ADR-124]] section 5).
    from scripts.lib import area_resolve
    assert area_resolve.own_area(_cands(vault)[0]) is None
    assert area_resolve.has_area_source(_cands(vault)[0]) is False


def test_ac5_archive_arm_is_unguarded_by_design(run_script, vault):
    """`_MANAGED_KIND` keys on the vault-RELATIVE path (SC-046), so `archive/candidates.json` resolves to
    kind None and takes no area guard. Deliberate, not an oversight: an archived row's area is historical
    and is never read by the lens. Recorded so the seam table is complete."""
    (vault / "archive").mkdir(exist_ok=True)
    (vault / "archive" / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx",
         "candidates": [{"id": "SC-900", "title": "shipped", "status": "shipped"}]}, indent=2),
        encoding="utf-8")
    r = _ve(run_script, vault, "update", "--file", "archive/candidates.json", "--array", "candidates",
            "--id", "SC-900", "--set", "area=")
    assert r.returncode == 0, f"characterization: the archive arm is unguarded by design — {r.stderr}"
