"""slice-066 / SC-119 — tournament_convergence: the shared "did the 3 designers
fully agree?" predicate over design.json tournament.approach_divergence.

FULL convergence == approach_divergence present, non-empty, EVERY present pair
well-formed (a mapping whose `divergence` is in {overlapping, identical, disjoint}),
and NO pair `disjoint`. Anything missing / empty / malformed -> INDETERMINATE
(fail-visible), NEVER silently convergent (AC1 + must-not-defer, BC-PROJ-10 / slice-065).

Pair-completeness (M2 decision): we do NOT hard-require exactly 3 pairs -- a 2-designer
tournament is legitimate (the step-0 spike found short-count slices). Convergence needs
every PRESENT pair well-formed; a single malformed element -> indeterminate.

TF-1: written FAILING before the impl (scripts/lib/tournament_convergence.py does not exist yet).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import tournament_convergence as tc  # noqa: E402


def _design(divs):
    """A design.json dict whose approach_divergence carries the given per-pair
    divergence values (each a 2-name pair)."""
    names = ["designer-practice", "designer-crossdomain", "designer-expert"]
    ad = []
    for i, d in enumerate(divs):
        a = names[i % len(names)]
        b = names[(i + 1) % len(names)]
        ad.append({"pair": [a, b], "divergence": d})
    return {"slice": "slice-x", "tournament": {"approach_divergence": ad}}


# ---- classify(): the convergent cases -------------------------------------

def test_all_overlapping_is_convergent():
    c = tc.classify(_design(["overlapping", "overlapping", "overlapping"]))
    assert c.state == "convergent"
    assert c.is_full_convergence is True


def test_all_identical_is_convergent():
    c = tc.classify(_design(["identical", "identical", "identical"]))
    assert c.state == "convergent"
    assert c.is_full_convergence is True


def test_two_designer_all_overlapping_is_convergent():
    # M2 completeness decision: 2 pairs (a 2-designer tournament) is legitimate, not indeterminate.
    c = tc.classify(_design(["overlapping", "overlapping"]))
    assert c.state == "convergent"
    assert c.is_full_convergence is True


# ---- classify(): the not_convergent case ----------------------------------

def test_one_disjoint_is_not_convergent():
    c = tc.classify(_design(["overlapping", "disjoint", "overlapping"]))
    assert c.state == "not_convergent"
    assert c.is_full_convergence is False
    assert c.disjoint_pairs  # the disjoint pair is surfaced


# ---- classify(): the indeterminate cases (fail-visible) --------------------

def test_empty_approach_divergence_is_indeterminate():
    c = tc.classify({"tournament": {"approach_divergence": []}})
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_missing_tournament_block_is_indeterminate():
    c = tc.classify({"slice": "slice-x"})
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_tournament_without_approach_divergence_is_indeterminate():
    c = tc.classify({"tournament": {"tier": "medium"}})
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_out_of_enum_divergence_is_indeterminate_not_convergent():
    # M2: a MODEL-produced out-of-enum value must NOT pass 'no pair == disjoint' as convergent.
    c = tc.classify(_design(["overlapping", "divergent", "overlapping"]))
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False
    assert "divergent" in c.reason or "enum" in c.reason.lower()


def test_non_mapping_pair_is_indeterminate():
    d = {"tournament": {"approach_divergence": ["overlapping", {"pair": ["a", "b"], "divergence": "overlapping"}]}}
    c = tc.classify(d)
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_pair_missing_divergence_key_is_indeterminate():
    d = {"tournament": {"approach_divergence": [{"pair": ["a", "b"]}]}}
    c = tc.classify(d)
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_null_divergence_is_indeterminate():
    d = {"tournament": {"approach_divergence": [{"pair": ["a", "b"], "divergence": None}]}}
    c = tc.classify(d)
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_non_dict_design_is_indeterminate():
    assert tc.classify(None).state == "indeterminate"
    assert tc.classify("not a dict").state == "indeterminate"
    assert tc.classify([]).state == "indeterminate"


def test_non_list_approach_divergence_is_indeterminate():
    c = tc.classify({"tournament": {"approach_divergence": {"pair": ["a", "b"], "divergence": "overlapping"}}})
    assert c.state == "indeterminate"


# ---- from_slice_folder(): IO wrapper, never raises -------------------------

def test_from_slice_folder_missing_design_is_indeterminate(tmp_path: Path):
    c = tc.from_slice_folder(tmp_path)  # no design.json
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_from_slice_folder_malformed_json_is_indeterminate(tmp_path: Path):
    (tmp_path / "design.json").write_text("{not valid json", encoding="utf-8")
    c = tc.from_slice_folder(tmp_path)
    assert c.state == "indeterminate"
    assert c.is_full_convergence is False


def test_from_slice_folder_convergent(tmp_path: Path):
    (tmp_path / "design.json").write_text(
        json.dumps(_design(["overlapping", "overlapping", "overlapping"])), encoding="utf-8")
    c = tc.from_slice_folder(tmp_path)
    assert c.state == "convergent"
    assert c.is_full_convergence is True


def test_from_slice_folder_disjoint(tmp_path: Path):
    (tmp_path / "design.json").write_text(
        json.dumps(_design(["overlapping", "disjoint"])), encoding="utf-8")
    c = tc.from_slice_folder(tmp_path)
    assert c.state == "not_convergent"


def test_from_slice_folder_never_raises_on_directory_missing():
    c = tc.from_slice_folder(Path("Z:/no/such/slice/folder/anywhere"))
    assert c.state == "indeterminate"


# ---- CLI (M1: Step 3.5 invokes this; m3: ASCII + UTF-8 stdout) -------------

def test_cli_json_on_convergent_folder(run_script, tmp_path: Path):
    (tmp_path / "design.json").write_text(
        json.dumps(_design(["overlapping", "overlapping", "overlapping"])), encoding="utf-8")
    r = run_script("scripts/lib/tournament_convergence.py", ["--slice", str(tmp_path), "--json"])
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["state"] == "convergent"
    assert payload["is_full_convergence"] is True
    # m3: reason must be ASCII-only (Windows cp1252 safety)
    assert r.stdout.isascii(), "CLI output must be ASCII-only"


def test_cli_json_on_indeterminate_folder(run_script, tmp_path: Path):
    r = run_script("scripts/lib/tournament_convergence.py", ["--slice", str(tmp_path), "--json"])
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["state"] == "indeterminate"
    assert payload["is_full_convergence"] is False


def test_cli_missing_slice_arg_is_usage_error(run_script):
    r = run_script("scripts/lib/tournament_convergence.py", ["--json"])
    assert r.returncode == 2
