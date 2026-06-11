"""scripts/lib/artifact_lint.py — schema-by-example enforcement (4.4 item 2 + the 1.4 regression).

The examples-pass-their-own-audits regression: every bundled example must conform to
its schema-by-example. This is the check that would have caught the 1.4
`action: fix-now` enum bug before it shipped.
"""
from __future__ import annotations

from scripts.lib import artifact_lint
from scripts.lib.artifact_lint import _load_examples, lint_artifact


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
