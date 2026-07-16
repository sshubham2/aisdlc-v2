"""slice-072 / SC-137 — AC1 (fail-closed / fail-visible helper) + AC5 (end-to-end
helper -> schema -> artifact_lint co-constraint) + the AC3/AC4 helper assertions.

The gate helper `residue_disposition.build_eject_payload` builds a residue-born candidate
payload stamping `ejected_from` + `ejection_reason`; it FAIL-CLOSES on an empty/whitespace/
missing reason and FAILS VISIBLY on a malformed source / unclassifiable item / supplied id /
missing owning slice. AC5 chains the real executable layers: helper builds a reasoned payload
that passes the artifact_lint co-constraint, while a reason-less ejected row is REJECTED.

TF-1: written FAILING before scripts/lib/residue_disposition.py exists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib import residue_disposition as rd  # noqa: E402

PY = sys.executable
LINT = ROOT / "scripts" / "lib" / "artifact_lint.py"
RD = ROOT / "scripts" / "lib" / "residue_disposition.py"


def _item(**over):
    base = {
        "title": "extract-webhook-retry-helper",
        "description": "Pull the retry backoff into a shared helper.",
        "source": [{"type": "reflection-discovered", "ref": "slice-072"}],
    }
    base.update(over)
    return base


# ── AC1: build ─────────────────────────────────────────────────────────────
def test_build_eject_payload_stamps_both_fields():
    p = rd.build_eject_payload(_item(), "slice-072", "out of budget for this cut")
    assert p["ejected_from"] == "slice-072"
    assert p["ejection_reason"] == "out of budget for this cut"
    assert "id" not in p  # allocator mints SC-NNN in-lock (ADR-013)
    assert p["title"] == "extract-webhook-retry-helper"


def test_build_eject_payload_returns_valid_candidate_shape():
    p = rd.build_eject_payload(_item(), "slice-072", "deferred by scope")
    for k in ("status", "progress", "slice", "claimed_by", "started_at"):
        assert k in p, f"missing lifecycle field {k}"


def test_payloads_do_not_alias_the_default_assumptions_list():
    # CR2: two payloads that both defaulted `assumptions` must NOT share one list.
    a = rd.build_eject_payload(_item(), "slice-072", "reason a")
    b = rd.build_eject_payload(_item(), "slice-072", "reason b")
    a["assumptions"].append({"id": "A1"})
    assert b["assumptions"] == [], "payloads must not alias one shared default assumptions list"


def test_reason_is_stripped_on_the_payload():
    p = rd.build_eject_payload(_item(), "slice-072", "  padded reason  ")
    assert p["ejection_reason"] == "padded reason"


# ── AC1: fail-closed reason ────────────────────────────────────────────────
@pytest.mark.parametrize("reason", ["", "   ", "\t\n", None])
def test_reject_empty_or_missing_reason_fail_closed(reason):
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload(_item(), "slice-072", reason)


# ── AC1: fail-visible source / classification ──────────────────────────────
@pytest.mark.parametrize("bad", [None, "not-a-dict", 42, []])
def test_malformed_source_fails_visibly(bad):
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload(bad, "slice-072", "a real reason")


def test_unclassifiable_item_no_title_fails_visibly():
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload({"description": "no title here"}, "slice-072", "a real reason")


def test_supplied_id_rejected():
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload(_item(id="SC-999"), "slice-072", "a real reason")


@pytest.mark.parametrize("owner", ["", "   ", None, 5])
def test_missing_or_malformed_owning_slice_fails_visibly(owner):
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload(_item(), owner, "a real reason")


def test_malformed_source_field_fails_visibly():
    with pytest.raises(rd.ResidueError):
        rd.build_eject_payload(_item(source="not-a-list"), "slice-072", "a real reason")


# ── AC3 helper: reflect record-on-capture carries the reason ───────────────
def test_capture_payload_carries_ejected_from_and_reason():
    # reflect's record-on-capture uses this SAME helper -> every capture is provenance-stamped.
    p = rd.build_eject_payload(_item(), "slice-072", "out-of-scope deliberate cut")
    assert p["ejected_from"] and p["ejection_reason"].strip()


# ── AC4 helper: build-slice mint-split case ────────────────────────────────
def test_build_slice_mint_payload_carries_provenance():
    split = {"title": "part-B-of-the-split", "description": "the second half of the cut"}
    p = rd.build_eject_payload(split, "slice-072", "this is 2 slices; B minted as a candidate")
    assert p["ejected_from"] == "slice-072" and p["ejection_reason"]


# ── AC5: end-to-end helper -> schema -> artifact_lint co-constraint ─────────
def _candidates_file(tmp_path, rows):
    doc = {
        "_schema": "aisdlc/slice-candidates@1",
        "project": "x",
        "updated": "ts",
        "candidates": rows,
        "pick_log": [],
    }
    f = tmp_path / "candidates.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f


def test_end_to_end_reasoned_payload_passes_co_constraint(tmp_path):
    p = rd.build_eject_payload(_item(), "slice-072", "genuine recorded reason")
    p["id"] = "SC-500"  # the allocator would mint one; give the fixture a value
    f = _candidates_file(tmp_path, [p])
    r = subprocess.run(
        [PY, str(LINT), "--type", "slice-candidates", str(f), "--json"],
        capture_output=True, text=True,
    )
    out = json.loads(r.stdout)
    assert out["violations"] == [], out
    assert r.returncode == 0


def test_end_to_end_reasonless_row_rejected_by_co_constraint(tmp_path):
    # A reason-less ejected row: the helper REFUSES to build one, so construct it directly
    # to prove the independent co-constraint audit catches what bypasses the helper.
    row = {
        "id": "SC-501", "title": "t", "status": "candidate", "progress": "not-started",
        "ejected_from": "slice-072",  # ejection_reason MISSING
    }
    f = _candidates_file(tmp_path, [row])
    r = subprocess.run(
        [PY, str(LINT), "--type", "slice-candidates", str(f), "--json"],
        capture_output=True, text=True,
    )
    out = json.loads(r.stdout)
    assert any("ejection_reason" in v for v in out["violations"]), out
    assert r.returncode == 1


# ── M5: the --co-constraint-gate is legacy-TOLERANT ────────────────────────
def _legacy_rows():
    # mirrors the live candidates.json's 13 legacy spike_status='pending' violations (M5)
    return [
        {"id": f"SC-{n}", "title": f"legacy-{n}", "status": "candidate", "progress": "not-started",
         "assumptions": [{"id": "A1", "statement": "x", "blocking": True, "spike_status": "pending"}]}
        for n in range(600, 613)
    ]


def _gate(tmp_path, rows):
    f = _candidates_file(tmp_path, rows)
    r = subprocess.run(
        [PY, str(LINT), "--type", "slice-candidates", "--co-constraint-gate", str(f), "--json"],
        capture_output=True, text=True,
    )
    return r, json.loads(r.stdout)


def test_co_constraint_gate_tolerates_legacy_spike_status(tmp_path):
    reasoned = rd.build_eject_payload(_item(), "slice-072", "recorded reason")
    reasoned["id"] = "SC-700"
    r, out = _gate(tmp_path, _legacy_rows() + [reasoned])
    assert out["violations"] == [], out          # ejected-provenance clean -> no hard fail
    assert any("spike_status" in w for w in out["warnings"]), out  # legacy drift only warns
    assert r.returncode == 0


def test_co_constraint_gate_blocks_reasonless_eject_despite_legacy(tmp_path):
    reasonless = {"id": "SC-701", "title": "t", "status": "candidate",
                  "progress": "not-started", "ejected_from": "slice-072"}
    r, out = _gate(tmp_path, _legacy_rows() + [reasonless])
    assert any("ejection_reason" in v for v in out["violations"]), out  # hard-fails on provenance
    assert any("spike_status" in w for w in out["warnings"]), out       # legacy still only warns
    assert r.returncode == 1


# ── BC-PROJ-9 / BC-PROJ-10: exercise the EXACT consumer CLI shape the SKILL.md invokes ──
# reflect/build-slice call: residue_disposition.py --item-file X --ejected-from Y --ejection-reason Z --json
def _cli(tmp_path, item, ejected_from, reason):
    itemf = tmp_path / "item.json"
    itemf.write_text(json.dumps(item), encoding="utf-8")
    return subprocess.run(
        [PY, str(RD), "--item-file", str(itemf),
         "--ejected-from", ejected_from, "--ejection-reason", reason, "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_consumer_cli_shape_builds_payload(tmp_path):
    r = _cli(tmp_path, _item(), "slice-072", "a recorded reason")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["ejected_from"] == "slice-072"
    assert payload["ejection_reason"] == "a recorded reason"
    assert "id" not in payload


def test_consumer_cli_shape_fail_closes_on_empty_reason(tmp_path):
    r = _cli(tmp_path, _item(), "slice-072", "   ")
    assert r.returncode != 0, "fail-closed: an empty reason must exit non-zero"
    assert r.stdout.strip() == "", "no payload may be emitted on the fail-closed path"
    assert "ejection_reason" in r.stderr


# ── BC-PROJ-3: a non-ASCII ejection_reason round-trips as the LITERAL char via --json ──
def test_non_ascii_reason_round_trips_literal(tmp_path):
    reason = "deferred — out of budget (café)"  # em-dash + e-acute
    r = _cli(tmp_path, _item(), "slice-072", reason)
    assert r.returncode == 0, r.stderr
    assert "—" in r.stdout and "\\u2014" not in r.stdout, "em-dash must be literal, not escaped"
    assert json.loads(r.stdout)["ejection_reason"] == reason
