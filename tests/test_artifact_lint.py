"""scripts/lib/artifact_lint.py — schema-by-example enforcement (4.4 item 2 + the 1.4 regression).

The examples-pass-their-own-audits regression: every bundled example must conform to
its schema-by-example. This is the check that would have caught the 1.4
`action: fix-now` enum bug before it shipped.
"""
from __future__ import annotations

import json

from scripts.lib import artifact_lint
from scripts.lib.artifact_lint import _load_examples, lint_artifact, schema_skew


def test_self_check_passes_on_canonical_examples():
    # the canonical examples in schemas/artifact-examples.json conform to their own shape
    assert artifact_lint.main(["--self-check"]) == 0


def test_every_bundled_example_lints_clean(plugin_root):
    examples = sorted((plugin_root / "skills").glob("*/examples/*.json"))
    assert examples, "no bundled examples found"
    rc = artifact_lint.main([*[str(p) for p in examples], "--skip-unknown"])
    assert rc == 0


def test_fix_now_disposition_is_flagged():
    # THE 1.4 regression: a triage disposition action outside the enum must fail.
    examples = _load_examples()
    crit = dict(examples["critique"])
    crit["_schema"] = "aisdlc/critique@1"
    crit["triage"] = {
        "verdict": "needs-fixes", "ratified_by": "u", "at": "t",
        "dispositions": [{"finding": "C1", "action": "fix-now"}],
    }
    violations = lint_artifact(crit, "critique", examples["critique"], "test")
    assert any("fix-now" in v for v in violations)


def test_missing_schema_tag_flagged():
    examples = _load_examples()
    key = next(iter(examples))
    data = {k: v for k, v in examples[key].items() if k != "_schema"}
    violations = lint_artifact(data, key, examples[key], "test")
    assert any("_schema" in v for v in violations)


def test_unknown_risk_tier_flagged():
    examples = _load_examples()
    mb = dict(examples["mission-brief"])
    mb["risk_tier"] = "extreme"  # not in {low, medium, high}
    violations = lint_artifact(mb, "mission-brief", examples["mission-brief"], "test")
    assert any("risk_tier" in v for v in violations)


# ── 4.5 version-skew detection (non-fatal) ────────────────────────────────────────

def test_schema_skew_newer_major_warns():
    examples = _load_examples()
    art = dict(examples["critique"])
    art["_schema"] = "aisdlc/critique@9"  # newer than the example's @1
    warns = schema_skew(art, "critique", examples["critique"], "2.0.0")
    assert any("_schema" in w and "NEWER" in w for w in warns)


def test_schema_skew_current_no_warn():
    examples = _load_examples()
    art = dict(examples["critique"])  # same @N as the example
    assert schema_skew(art, "critique", examples["critique"], "2.0.0") == []


def test_plugin_version_skew_warns():
    examples = _load_examples()
    art = dict(examples["critique"])
    art["_plugin_version"] = "99.0.0"  # newer than the running plugin
    warns = schema_skew(art, "critique", examples["critique"], "2.22.4")
    assert any("_plugin_version" in w for w in warns)


def test_skew_is_non_fatal_in_cli(tmp_path):
    # a newer-schema artifact WARNs but artifact_lint still exits 0 (no real violation)
    examples = _load_examples()
    art = dict(examples["critique"])
    art["_schema"] = "aisdlc/critique@9"
    p = tmp_path / "critique.json"
    p.write_text(json.dumps(art), encoding="utf-8")
    assert artifact_lint.main([str(p)]) == 0


# ── slice-004 (ADR-002): spike_verdict / spike_constraints enforcement ────────────


def _cands(assumptions):
    return {
        "_schema": "aisdlc/slice-candidates@1", "project": "p", "updated": "t",
        "candidates": [{"id": "SC-1", "assumptions": assumptions}], "pick_log": [],
    }


def _lint_cands(assumptions):
    examples = _load_examples()
    return lint_artifact(_cands(assumptions), "slice-candidates",
                         examples["slice-candidates"], "test")


def test_conditional_with_constraints_clean():
    assert _lint_cands([{"id": "A1", "spike_status": "proven",
                         "spike_verdict": "conditional",
                         "spike_constraints": ["payloads < 1MB"]}]) == []


def test_bogus_spike_verdict_flagged():
    v = _lint_cands([{"id": "A1", "spike_verdict": "maybe"}])
    assert any("spike_verdict" in x and "maybe" in x for x in v)


def test_conditional_is_not_a_spike_status():
    # ADR-002: the binary gate must never absorb the ternary verdict.
    v = _lint_cands([{"id": "A1", "spike_status": "conditional"}])
    assert any("spike_status" in x for x in v)


def test_conditional_without_constraints_flagged():
    v = _lint_cands([{"id": "A1", "spike_verdict": "conditional"}])
    assert any("non-empty list" in x for x in v)


def test_conditional_with_string_constraints_flagged():
    # vault_edit --set stores a bare string on malformed JSON; the type is pinned.
    v = _lint_cands([{"id": "A1", "spike_verdict": "conditional",
                      "spike_constraints": "not-a-list"}])
    assert any("non-empty list" in x for x in v)


def test_row_identity_two_row_record_yields_both_violations():
    # critique M1 pin: a flat (count-based) pairing sees one conditional + one
    # constraints list across the record and passes; the per-row walk flags BOTH rows.
    v = _lint_cands([
        {"id": "A1", "spike_verdict": "conditional"},
        {"id": "A2", "spike_verdict": "go", "spike_constraints": ["leak"]},
    ])
    assert len(v) == 2


def test_stale_constraints_on_non_conditional_flagged():
    v = _lint_cands([{"id": "A1", "spike_verdict": "no-go",
                      "spike_constraints": ["stale"]}])
    assert any("stale" in x for x in v)


def test_legacy_binary_rows_stay_clean():
    # AC4: records written before slice-004 carry neither new field.
    assert _lint_cands([{"id": "A1", "spike_status": "unproven",
                         "spike_ref": None, "spike_evidence": None}]) == []


def test_design_assumptions_proven_verdicts():
    examples = _load_examples()
    base = {"_schema": "aisdlc/design@1"}
    base.update({k: v for k, v in examples["design"].items() if not k.startswith("_")})
    ok = dict(base)
    ok["assumptions_proven"] = [
        {"assumption": "A1", "statement": "s", "spike_ref": "sp", "verdict": "go"},
        {"assumption": "A2", "statement": "s", "spike_ref": "sp",
         "verdict": "conditional", "constraints": ["c1"]},
    ]
    assert lint_artifact(ok, "design", examples["design"], "test") == []
    bad = dict(base)
    bad["assumptions_proven"] = [{"assumption": "A1", "statement": "s",
                                  "spike_ref": "sp", "verdict": "no-go"}]
    v = lint_artifact(bad, "design", examples["design"], "test")
    assert any("no-go" in x for x in v)


def test_spike_artifact_conditional_requires_constraints():
    examples = _load_examples()
    sp = dict(examples["spike"])
    sp["verdict"] = "conditional"
    sp.pop("constraints", None)
    v = lint_artifact(sp, "spike", examples["spike"], "test")
    assert any("non-empty list" in x for x in v)
    sp["constraints"] = ["holds under X"]
    assert lint_artifact(sp, "spike", examples["spike"], "test") == []


def test_legacy_spike_file_without_constraints_clean():
    # OPTIONAL_KEYS: pre-slice-004 spike files lack constraints[] and stay valid.
    examples = _load_examples()
    sp = {k: v for k, v in examples["spike"].items() if k != "constraints"}
    assert lint_artifact(sp, "spike", examples["spike"], "test") == []
