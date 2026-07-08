"""scripts/lib/artifact_lint.py — schema-by-example enforcement (4.4 item 2 + the 1.4 regression).

The examples-pass-their-own-audits regression: every bundled example must conform to
its schema-by-example. This is the check that would have caught the 1.4
`action: fix-now` enum bug before it shipped.
"""
from __future__ import annotations

import copy
import json

import pytest

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


def test_validation_criteria_empty_evidence_flagged():
    # Review sweep 2026-07: the per-criterion evidence discipline is mechanical —
    # a validation criteria[] row with empty/missing evidence must fail the lint
    # ("it worked" without evidence is not a PASS).
    examples = _load_examples()
    val = copy.deepcopy(examples["validation"])
    assert val.get("criteria"), "the canonical validation example has no criteria rows"
    val["criteria"][0]["evidence"] = "   "
    violations = lint_artifact(val, "validation", examples["validation"], "test")
    assert any("evidence" in v for v in violations)
    # and the canonical example itself stays clean (non-empty evidence)
    assert not lint_artifact(examples["validation"], "validation",
                             examples["validation"], "test")


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


# ── slice-013 (ADR-009): documented-enum coverage enforcement ─────────────────────


def _set_path(obj, path):
    """Descend a dotted path with `[]` list hops (into element 0) to the parent of the
    leaf, returning (parent_dict, leaf_name). Assumes the example already populates the
    path (we only test enums the canonical example contains)."""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        is_list = part.endswith("[]")
        name = part[:-2] if is_list else part
        cur = cur[name]
        if is_list:
            cur = cur[0]
    return cur, parts[-1]


# Representative newly-enforced enums whose path is present in the canonical example.
_ENUM_REJECT_ACCEPT = [
    ("build-log", "gates[].status", "bogus", "pass"),
    ("drift-log", "entries[].category", "bogus", "stale-claim"),
    ("design", "tournament.proposals[].selected", "maybe", "core"),
    ("design", "tournament.approach_divergence[].divergence", "kinda", "disjoint"),
    ("design", "cross_domain_transfer.invariants[].status", "perhaps", "holds"),
    ("critique", "findings[].severity", "huge", "major"),
    ("code-review", "findings[].severity", "huge", "minor"),
    ("milestone", "stage", "bogus", "build"),
    ("changelog", "mode", "bogus", "merge"),
    ("doc-manifest", "docs[].kind", "bogus", "readme"),
    ("adr", "reversibility", "bogus", "expensive"),
    ("validation", "reality_contact", "bogus", "high"),
    ("critique-review", "assessments[].classification", "bogus", "valid"),
    ("user-test", "mode", "bogus", "prototype"),
    # CR1: dedicated fixtures for the remaining newly-enforced enum paths (AC3 "each").
    ("concept", "constraints.stack[].reversibility", "bogus", "cheap"),
    ("risk-register", "risks[].reversibility", "bogus", "cheap"),
    ("code-review", "triage.dispositions[].action", "bogus", "fixed"),
    ("critic-calibration-log", "gate_skips[].action", "bogus", "skip"),
    ("milestone", "progress[].step", "bogus", "build"),
    ("design", "tournament.decidable_disagreements[].verdict", "bogus", "go"),
]


@pytest.mark.parametrize("key,path,bad,good", _ENUM_REJECT_ACCEPT)
def test_documented_enum_reject_accept(key, path, bad, good):
    # AC3: each newly-enforced enum rejects a non-canonical value, accepts the canonical.
    examples = _load_examples()
    ex = examples[key]
    bad_art = copy.deepcopy(ex)
    parent, leaf = _set_path(bad_art, path)
    parent[leaf] = bad
    v = lint_artifact(bad_art, key, ex, "t")
    assert any(path in x for x in v), f"{key}.{path}={bad} not flagged: {v}"
    good_art = copy.deepcopy(ex)
    parent, leaf = _set_path(good_art, path)
    parent[leaf] = good
    v2 = lint_artifact(good_art, key, ex, "t")
    assert not any(path in x for x in v2), f"{key}.{path}={good} wrongly flagged: {v2}"


def test_build_log_result_enforced():
    # AC2 headline + the live 'in-progress' value the first draft would have rejected.
    examples = _load_examples()
    ex = examples["build-log"]
    for good in ("shipped", "in-progress"):
        art = copy.deepcopy(ex)
        art["result"] = good
        assert lint_artifact(art, "build-log", ex, "t") == [], f"result={good} wrongly flagged"
    bad = copy.deepcopy(ex)
    bad["result"] = "built"
    v = lint_artifact(bad, "build-log", ex, "t")
    assert any("result" in x and "built" in x for x in v), f"result='built' not flagged: {v}"


def test_documented_enum_coverage():
    # AC1: every documented enum is enforced or explicitly excluded; no orphan exclusions.
    assert artifact_lint.coverage_gaps() == []


def test_no_dead_enum_rows():
    # AC1: every enforced/documented (artifact, path) resolves to a real field. This is
    # what removed the dead (code-review, "verdict") row (code-review uses `result`).
    assert artifact_lint.enum_path_resolves() == []


def test_self_check_runs_coverage_and_dead_row_guards():
    # the new guards have a real CI home: artifact_lint --self-check runs them (no flag, m1).
    assert artifact_lint.main(["--self-check"]) == 0


def test_dead_code_review_verdict_row_removed():
    # the dead row pointed at a non-existent field; it is gone.
    assert ("code-review", "verdict") not in artifact_lint.KNOWN_ENUMS


def test_code_review_result_uppercase_not_enforced():
    # code-review.result is UPPERCASE (off-convention) -> excluded, not enforced.
    examples = _load_examples()
    ex = examples["code-review"]  # result == "FINDINGS"
    assert not any("result" in x for x in lint_artifact(copy.deepcopy(ex), "code-review", ex, "t"))


def test_critique_disposition_annotation_not_flagged():
    # findings[].disposition carries a free-text annotation suffix -> excluded (B1).
    examples = _load_examples()
    ex = examples["critique"]
    art = copy.deepcopy(ex)
    art["findings"][0]["disposition"] = "accepted-fixed - a long free-text rationale here"
    assert not any("disposition" in x for x in lint_artifact(art, "critique", ex, "t"))


def test_coverage_gaps_detects_unaccounted(monkeypatch):
    # teeth: a documented enum that is neither enforced nor excluded must be flagged.
    fake = dict(artifact_lint.DOCUMENTED_ENUMS)
    fake[("triage", "classification.audience")] = "triage skill (test-injected)"
    monkeypatch.setattr(artifact_lint, "DOCUMENTED_ENUMS", fake)
    assert any("audience" in g for g in artifact_lint.coverage_gaps())


def test_coverage_gaps_detects_orphan_exclusion(monkeypatch):
    # teeth: an exclusion not present in DOCUMENTED_ENUMS is an orphan and must be flagged.
    fake = dict(artifact_lint.ENUM_EXCLUSIONS)
    fake[("triage", "made.up.path")] = {"category": "x", "rationale": "test-injected orphan"}
    monkeypatch.setattr(artifact_lint, "ENUM_EXCLUSIONS", fake)
    assert any("made.up.path" in g for g in artifact_lint.coverage_gaps())


def test_enum_path_resolves_detects_dead_row(monkeypatch):
    # teeth: a rule pointing at a non-existent field must be flagged.
    fake = dict(artifact_lint.KNOWN_ENUMS)
    fake[("code-review", "no_such_field")] = frozenset({"x"})
    monkeypatch.setattr(artifact_lint, "KNOWN_ENUMS", fake)
    assert any("no_such_field" in d for d in artifact_lint.enum_path_resolves())


def test_public_surface_unverified_reason_enum_enforced():
    # BC-PROJ-1 / M2 (slice-040): the new public_surface_unverified[].reason enum is only real
    # where the linter enforces it -- a bogus value must fail lint; the canonical value passes.
    examples = _load_examples()
    ex = examples["doc-manifest"]
    bad = copy.deepcopy(ex)
    bad["public_surface_unverified"] = [{"token": "x", "reason": "BOGUS-REASON"}]
    assert any("BOGUS-REASON" in v or "public_surface_unverified" in v
               for v in lint_artifact(bad, "doc-manifest", ex, "test"))
    good = copy.deepcopy(ex)
    good["public_surface_unverified"] = [{"token": "x", "reason": "not-indexed"}]
    assert not any("public_surface_unverified" in v
                   for v in lint_artifact(good, "doc-manifest", ex, "test"))
