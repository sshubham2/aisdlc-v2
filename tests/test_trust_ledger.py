"""trust_ledger.py — per-slice trust ledger compose + render (SC-143 / PS-002).

test_first: these tests were authored BEFORE trust_ledger.py's behavior was final and
pin the mission-brief ACs against reality. The real shipped corpus is monochromatic-green
(all validation.json result==pass, slice-level reality_contact in {high,medium}), so the
AC3 FALSIFICATION clause cannot be exercised by reading a real slice — it needs a
CONSTRUCTED not-green fixture (M1). Every mutation below (no-validation, result->fail,
criterion->fail, contact->low, failed_rows, empty-criteria, non-empty reality_surprises)
flips green->False or annotates the headline, proving the ledger cannot read fully green
while a reality gate was in fact skipped/failed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lib.gate_log import _PROXIES
from scripts.lib.trust_ledger import (
    LedgerNotFound,
    PROXY_RANK,
    compose,
    render,
)

SCRIPT = "scripts/lib/trust_ledger.py"
_LINE_ARRAYS = ("reality_confirmed", "model_only", "not_checked", "reality_surprises",
                "known_escapes", "informational", "shippability")


def _default_gate_rows(canon: str) -> list[dict]:
    return [
        {"at": "2026-01-01T00:00:00Z", "slice": canon, "gate": "validate-slice", "verdict": "pass",
         "findings_count": 0, "reality_contact": "high", "reality_proxy": "local-real-data"},
        {"at": "2026-01-01T00:01:00Z", "slice": canon, "gate": "critique", "verdict": "needs-fixes",
         "findings_count": 3, "reality_contact": "low"},                       # model-only
        {"at": "2026-01-01T00:02:00Z", "slice": canon, "gate": "design-tournament", "verdict": "overlapping",
         "findings_count": 0, "reality_contact": "low", "approach_divergence": "p~c:overlapping"},  # informational
        {"at": "2026-01-01T00:03:00Z", "slice": canon, "gate": "critique", "kind": "miss",
         "reality_contact": "low", "severity": "major", "caught_by": "validate", "ref": "escaped X"},  # known-escape
        {"at": "2026-01-01T00:04:00Z", "slice": "slice-999", "gate": "validate-slice", "verdict": "pass",
         "findings_count": 0, "reality_contact": "high"},                      # OTHER slice — excluded
    ]


def _seed(root: Path, *, canon="slice-050", name="slice-050-trust-ledger",
          result="pass", contact="high", criteria=..., reality_surprises=...,
          shippability_regression=..., gate_rows=None, acs=..., catalog_rows=...,
          write_validation=True, validation_raw=None) -> Path:
    """Seed a fresh vault under ``root`` and return it (as the --vault). Defaults compose a
    fully-green slice with all partitions exercised (a reality-confirmed validate-slice row,
    a model-only critique row, an informational design-tournament row, and a miss row)."""
    root.mkdir(parents=True, exist_ok=True)
    if criteria is ...:
        criteria = [{"id": "AC1", "result": "pass", "reality_proxy": "local-real-data"},
                    {"id": "AC2", "result": "pass"}]
    if acs is ...:
        acs = [{"id": "AC1", "text": "criterion one"}, {"id": "AC2", "text": "criterion two"}]
    if reality_surprises is ...:
        reality_surprises = []
    if shippability_regression is ...:
        shippability_regression = {"ran": True, "failed_rows": [], "deferral": None}
    if catalog_rows is ...:
        catalog_rows = [{"id": 1, "slice": canon, "what": "a catalog definition", "machine_cmd": "true"}]

    sdir = root / "slices" / "archive" / name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "mission-brief.json").write_text(json.dumps(
        {"_schema": "aisdlc/mission-brief@1", "slice": canon, "candidate": "SC-143",
         "acceptance_criteria": acs}), encoding="utf-8")

    if validation_raw is not None:
        (sdir / "validation.json").write_text(validation_raw, encoding="utf-8")
    elif write_validation:
        v = {"_schema": "aisdlc/validation@1", "slice": canon, "result": result,
             "reality_contact": contact, "criteria": criteria,
             "reality_surprises": reality_surprises,
             "shippability_regression": shippability_regression}
        (sdir / "validation.json").write_text(json.dumps(v), encoding="utf-8")

    (root / "gate-log.json").write_text(json.dumps(
        {"entries": gate_rows if gate_rows is not None else _default_gate_rows(canon),
         "_plugin_version": "test",
         "rows": [{"at": "t", "slice": canon, "gate": "STRAY-CONTAMINATION",
                   "verdict": "go", "reality_contact": "high"}]}), encoding="utf-8")  # m1: must be ignored
    (root / "shippability.json").write_text(json.dumps(
        {"_schema": "aisdlc/shippability@1", "rows": catalog_rows, "counters": {}}), encoding="utf-8")
    return root


# --- AC1: zero model authorship + per-line provenance + determinism ---------------------

def test_ac1_zero_authorship_and_provenance(tmp_path):
    v = _seed(tmp_path / "green")
    led = compose(v, "slice-050")
    assert led["_schema"] == "aisdlc/trust-ledger@1"
    assert led["_composed_by"] == "trust_ledger.py"
    assert led["reads_fully_green"] is True

    # every rendered line carries required provenance source={file,locator}
    for key in _LINE_ARRAYS:
        for line in led[key]:
            src = line.get("source") or {}
            assert src.get("file") and src.get("locator"), (key, line)

    # a reality-confirmed line traces to a REAL gate-log row (not a model summary)
    assert any(l["source"]["file"] == "gate-log.json" and "validate-slice" in l["text"]
               for l in led["reality_confirmed"])

    # ZERO model authorship: every line field is a structured key — no free-text prose field.
    # slice-086 (M3): gate/verdict/ac added CONSCIOUSLY to the closed set — each a structured
    # gate-log enum / mission-brief id, not free text — so the zero-authorship invariant is
    # EXTENDED, not weakened (story_signoff projects from these instead of parsing `text`).
    allowed = {"text", "source", "reality_contact", "reality_proxy", "at", "reason", "state",
               "gate", "verdict", "ac"}
    for key in _LINE_ARRAYS:
        for line in led[key]:
            assert set(line).issubset(allowed), (key, set(line) - allowed)

    # mechanical == deterministic: compose twice, identical save the timestamp
    led2 = compose(v, "slice-050")
    led.pop("at"); led2.pop("at")
    assert led == led2


# --- AC2: three structurally-distinct, correctly-populated partitions -------------------

def test_ac2_three_partitions_distinct(tmp_path):
    # AC1 reality-verified pass; AC2 only PARTIAL -> lands in not_checked; a low critique row -> model_only
    v = _seed(tmp_path / "mixed", result="partial",
              criteria=[{"id": "AC1", "result": "pass", "reality_proxy": "local-real-data"},
                        {"id": "AC2", "result": "partial"}])
    led = compose(v, "slice-050")

    assert led["reality_confirmed"] and all(l["reality_contact"] in ("high", "medium")
                                            for l in led["reality_confirmed"])
    assert any("validate-slice" in l["text"] for l in led["reality_confirmed"])

    assert led["model_only"] and all(l["reality_contact"] == "low" for l in led["model_only"])
    assert any("critique" in l["text"] for l in led["model_only"])

    assert any("AC2" in l["text"] for l in led["not_checked"])
    # the three partitions are disjoint (no line object shared across them)
    ids = [id(l) for k in ("reality_confirmed", "model_only", "not_checked") for l in led[k]]
    assert len(ids) == len(set(ids))


# --- AC3: falsification — a constructed not-green fixture per mutation (M1 + M-add-1) ----

def test_ac3_falsification_constructed_not_green(tmp_path):
    base = tmp_path
    assert compose(_seed(base / "baseline"), "slice-050")["reads_fully_green"] is True

    # (1) validation.json ABSENT -> not green + availability=missing
    led = compose(_seed(base / "noval", write_validation=False), "slice-050")
    assert led["reads_fully_green"] is False
    assert any(a["source"] == "validation.json" and a["status"] == "missing" for a in led["availability"])

    # (2) result -> fail
    assert compose(_seed(base / "resfail", result="fail"), "slice-050")["reads_fully_green"] is False

    # (3) a single criterion -> fail, and it is NAMED in not_checked
    led = compose(_seed(base / "critfail",
                        criteria=[{"id": "AC1", "result": "fail"}, {"id": "AC2", "result": "pass"}]),
                  "slice-050")
    assert led["reads_fully_green"] is False
    assert any("AC1" in l["text"] for l in led["not_checked"])

    # (4) slice-level reality_contact -> low (model-only validate-slice)
    assert compose(_seed(base / "lowcontact", contact="low"), "slice-050")["reads_fully_green"] is False

    # (5) shippability regression FAILED -> not green + state=failed
    led = compose(_seed(base / "regfail",
                        shippability_regression={"ran": True, "failed_rows": [{"id": "R1"}], "deferral": None}),
                  "slice-050")
    assert led["reads_fully_green"] is False
    assert any(l.get("state") == "failed" for l in led["shippability"])

    # (5b) a DEFERRED-but-failed regression STILL reads not-green (M4 honesty divergence)
    led = compose(_seed(base / "regfaildef",
                        shippability_regression={"ran": True, "failed_rows": [{"id": "R1"}],
                                                 "deferral": {"approved": True}}), "slice-050")
    assert led["reads_fully_green"] is False

    # (6) EMPTY criteria[] is NOT vacuously green (M2) -> not green + 'no criteria' note
    led = compose(_seed(base / "emptycrit", criteria=[]), "slice-050")
    assert led["reads_fully_green"] is False
    assert any(l.get("reason") == "no-criteria" for l in led["not_checked"])


def test_cr1_partial_ac_coverage_blocks_unqualified_green(tmp_path):
    # CR1: N mission-brief ACs but only M<N recorded passing criteria. Every OTHER conjunct is
    # green (result=pass, contact=high, no failed regression, criteria all pass), so the ONLY
    # thing that must stop a fully-green headline is the uncovered expected ACs.
    v = _seed(tmp_path / "cr1",
              acs=[{"id": "AC1"}, {"id": "AC2"}, {"id": "AC3"}],
              criteria=[{"id": "AC1", "result": "pass", "reality_proxy": "local-real-data"}])
    led = compose(v, "slice-050")
    assert led["reads_fully_green"] is False               # uncovered AC2/AC3 -> not green
    covered = {l["text"][:3] for l in led["not_checked"]}
    assert "AC2" in covered and "AC3" in covered           # both uncovered ACs surfaced
    r = render(led, "text")
    assert "READS FULLY GREEN" not in r                    # headline never unqualified-green


def test_cr1_regression_not_run_caveats_but_does_not_block(tmp_path):
    # ADR-098 pt 3: a regression that did NOT run keeps green (no historical false-alarm) but the
    # headline must be caveated (GREEN*), never an unqualified green. Full AC coverage otherwise.
    v = _seed(tmp_path / "regnorun",
              shippability_regression={"ran": False, "failed_rows": [], "deferral": None})
    led = compose(v, "slice-050")
    assert led["reads_fully_green"] is True                # not blocked
    assert any("did not run" in c for c in led["green_caveats"])
    assert "GREEN*" in render(led, "text") and "READS FULLY GREEN" not in render(led, "text")


def test_ac3_reality_surprises_caveat_headline(tmp_path):
    # M-add-1: a non-empty reality_surprises does NOT hard-gate green, but the HEADLINE is caveated —
    # never an UNQUALIFIED green — and each surprise renders as a first-class defeater line.
    v = _seed(tmp_path / "surprise",
              reality_surprises=[{"note": "ARM-3 IS ABSENT FROM ITS OWN CLI"},
                                 {"note": "build-log understates the diff by 2 files"}])
    led = compose(v, "slice-050")
    assert led["reads_fully_green"] is True          # not hard-gated
    assert led["green_caveats"]                       # but caveated
    assert len(led["reality_surprises"]) == 2
    assert all(l["source"]["file"] == "validation.json" for l in led["reality_surprises"])
    r = render(led, "text")
    assert "GREEN*" in r and "READS FULLY GREEN" not in r   # headline is qualified, not unqualified


# --- AC4: fail-visible — missing/malformed source, never silent green, never a crash ----

def test_ac4_fail_visible_malformed_and_missing(tmp_path):
    # malformed validation.json -> availability=malformed, not green, no crash, no fabricated pass
    led = compose(_seed(tmp_path / "malf", validation_raw="{ this is : not json "), "slice-050")
    assert led["reads_fully_green"] is False
    assert any(a["source"] == "validation.json" and a["status"] == "malformed" for a in led["availability"])
    assert not any(l.get("state") == "green" for l in led["shippability"])   # no fabricated green line
    render(led, "text"); render(led, "md")                                    # renders without crashing

    # empty validation FILE -> availability=empty, not green
    led2 = compose(_seed(tmp_path / "empt", validation_raw="   \n  "), "slice-050")
    assert led2["reads_fully_green"] is False
    assert any(a["source"] == "validation.json" and a["status"] == "empty" for a in led2["availability"])


# --- M3: miss (recall) + informational (design-tournament) rows out of the trust sections

def test_m3_miss_and_informational_excluded_from_trust(tmp_path):
    led = compose(_seed(tmp_path / "m3"), "slice-050")
    trust = led["reality_confirmed"] + led["model_only"]
    assert not any("MISSED" in l["text"] for l in trust)          # miss row not trust-affirming
    assert any("MISSED" in l["text"] for l in led["known_escapes"])
    assert not any("design-tournament" in l["text"] for l in trust)
    assert any("design-tournament" in l["text"] for l in led["informational"])


# --- m1: read canonical `entries`, ignore stray `rows`; render per-gate multiplicity -----

def test_m1_entries_only_and_gate_multiplicity(tmp_path):
    rows = [
        {"at": "2026-01-01T00:00:00Z", "slice": "slice-050", "gate": "risk-spike",
         "verdict": "conditional", "findings_count": 0, "reality_contact": "high"},
        {"at": "2026-01-01T00:05:00Z", "slice": "slice-050", "gate": "risk-spike",
         "verdict": "go", "findings_count": 0, "reality_contact": "high"},
    ]
    led = compose(_seed(tmp_path / "m1", gate_rows=rows), "slice-050")
    everywhere = (led["reality_confirmed"] + led["model_only"] + led["informational"]
                  + led["known_escapes"])
    assert not any("STRAY-CONTAMINATION" in l["text"] for l in everywhere)   # stray `rows` ignored
    spikes = [l for l in led["reality_confirmed"] if "risk-spike" in l["text"]]
    assert len(spikes) == 2                                                   # both rows kept
    assert spikes[0]["source"]["locator"] != spikes[1]["source"]["locator"]  # legible by `at`


# --- m2: weakest_proxy scoped to recorded proxies, honestly labelled --------------------

def test_m2_weakest_proxy_scoped_and_labeled(tmp_path):
    # AC1 proxy real-sandbox (crit) + validate-slice gate proxy local-real-data -> weakest = local-real-data
    v = _seed(tmp_path / "m2",
              criteria=[{"id": "AC1", "result": "pass", "reality_proxy": "real-sandbox"},
                        {"id": "AC2", "result": "pass"}])
    wp = compose(v, "slice-050")["weakest_proxy"]
    assert wp["value"] == "local-real-data"          # weakest of the two recorded
    assert wp["recorded"] == 2
    assert "recorded none" in wp["coverage"]

    # a slice that recorded NO proxy -> value None + honest label (never silently ranked strong)
    v2 = _seed(tmp_path / "m2none",
               criteria=[{"id": "AC1", "result": "pass"}, {"id": "AC2", "result": "pass"}],
               gate_rows=[{"at": "t", "slice": "slice-050", "gate": "validate-slice",
                           "verdict": "pass", "findings_count": 0, "reality_contact": "high"}])
    wp2 = compose(v2, "slice-050")["weakest_proxy"]
    assert wp2["value"] is None
    assert "no reality_proxy recorded" in wp2["coverage"]


# --- drift guard: PROXY_RANK set-equal to gate_log._PROXIES (a new proxy fails loudly) ---

def test_proxy_rank_set_equal_to_gate_log():
    assert set(PROXY_RANK) == set(_PROXIES)
    assert len(PROXY_RANK) == len(_PROXIES)          # no duplicate rank entries


# --- M3 (slice-086): the structured gate/verdict/ac enrichment does NOT change render() ---

def test_enrichment_does_not_change_render(tmp_path):
    """TF-1 characterization pin: render() reads only text/source/reality_contact/reality_proxy,
    so the additive gate/verdict/ac fields are invisible to it — the human-facing view (and the
    ship_receipt twin's contract, which is `text`) is byte-identical with or without enrichment."""
    led = compose(_seed(tmp_path / "enrich"), "slice-050")
    # a reality-confirmed gate line DOES carry the new structured fields
    assert any(l.get("gate") and l.get("verdict") for l in led["reality_confirmed"])
    stripped = json.loads(json.dumps(led))
    for key in _LINE_ARRAYS:
        for line in stripped[key]:
            for k in ("gate", "verdict", "ac"):
                line.pop(k, None)
    for fmt in ("text", "md"):
        assert render(stripped, fmt) == render(led, fmt)   # enrichment invisible to the view


# --- render is a PURE function of the composed JSON --------------------------------------

def test_render_is_pure_function_of_json(tmp_path):
    led = compose(_seed(tmp_path / "r"), "slice-050")
    r1 = render(led, "text")
    assert "TRUST LEDGER slice-050" in r1
    # a JSON round-trip renders identically (render re-derives nothing)
    assert render(json.loads(json.dumps(led)), "text") == r1
    # md variant uses markdown headers
    assert render(led, "md").startswith("# TRUST LEDGER")


# --- slice-not-found + CLI contract -----------------------------------------------------

def test_slice_not_found_raises(tmp_path):
    v = tmp_path / "empty_vault"
    v.mkdir()
    with pytest.raises(LedgerNotFound):
        compose(v, "slice-999")


def test_cli_compose_render_and_exit_codes(run_script, tmp_path):
    v = _seed(tmp_path / "cli")
    r = run_script(SCRIPT, ["compose", "--slice", "slice-050", "--vault", v])
    assert r.returncode == 0, r.stderr
    led = json.loads(r.stdout)
    assert led["slice"] == "slice-050" and led["reads_fully_green"] is True

    out = tmp_path / "ledger.json"
    r = run_script(SCRIPT, ["compose", "--slice", "slice-050", "--vault", v, "--out", out])
    assert r.returncode == 0
    r2 = run_script(SCRIPT, ["render", "--from", out, "--format", "text"])
    assert r2.returncode == 0 and "TRUST LEDGER slice-050" in r2.stdout

    # slice not found -> exit 2 (fail-visible)
    r3 = run_script(SCRIPT, ["compose", "--slice", "slice-777", "--vault", v])
    assert r3.returncode == 2 and "not found" in r3.stderr
