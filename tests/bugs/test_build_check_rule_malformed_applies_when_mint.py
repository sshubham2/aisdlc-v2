"""
Bug: a malformed build-check rule is MINTED silently (SC-151 / slice-071)

Observed (reality-confirmed against vault_edit at slice-071):
  `vault_edit append --file build-checks.json --array rules --json '{... "applies_when":"always:true" ...}'`
  mints the rule a fresh BC-PROJ-NNN id and returns exit 0 — even though `applies_when`
  is a bare STRING, not the object shape `{"glob": ...}` every real rule uses. The
  managed-kind (build-checks.json, rules) -> "bc" path in vault_edit mints the id but
  performs NO structural validation of the rule's shape.

  The downstream BC-1 parser (build_checks_audit._parse_rules) then sees the non-dict
  `applies_when`, emits a "format" violation, and `continue`s — DROPPING the rule from
  the applied set. So a rule minted this way enforces NOTHING, silently.

  This is exactly the live incident recorded in BC-PROJ-11's `_repair_note`: it was
  minted at slice-068 with `applies_when` == the string "always:true", enforced nothing
  until slice-069 hand-repaired it, and it was the CRITICAL rule that would have caught
  ADR-069's `| head -1` exit-code mask (DR-1 M-add-2). It is the 2nd instance of the
  class (BC-PROJ-10's malformed applies_when is the project's own recorded 1st).

Expected (desired end state — the fix, ADR-075):
  1. MINT (AC1): appending OR updating a build-check rule whose `applies_when` is not the
     object shape is REJECTED at mint on BOTH write legs (non-zero exit, message naming the
     field, malformed rule NOT persisted / update left the record untouched).
  2. AUDIT (AC2): build_checks_audit gives a DROP-causing malformed rule (non-dict
     `applies_when`, or a missing required key — both ALREADY exit 1 today) a distinct
     `malformed-rule` Critical label + "enforces nothing" message; a well-typed-but-INERT
     rule (dict `applies_when` with no fireable trigger — silently `skipped` today) becomes a
     fail-VISIBLE, NON-blocking WARNING (migration-safe: it does NOT by itself red the gate).
  3. The fireable decision is the SAME coercion `_rule_applies` uses — no drift (M3).

The test matrix (test_first_plan rows AC1..AC4):
  - AC1  test_malformed_applies_when_rejected_at_mint        (append leg, headline repro)
         test_update_malformed_applies_when_rejected          (update leg — M1, RED today)
         test_absent_applies_when_rejected_at_mint            (m2 — absent key, RED today)
  - AC2  test_audit_inert_rule_warns_not_blocks               (the genuine red->green: warn-absent -> warn-present)
         test_audit_nondict_applies_when_labeled_malformed_critical  (label/severity escalation — M2)
         test_audit_missing_required_key_labeled_malformed_critical  (label/severity escalation — M2)
         test_fireable_predicate_agrees_with_rule_applies     (coercion agreement — M3)
  - AC3  test_wellformed_rule_mints_ok                        (control: well-formed still mints)
  - AC4  test_wellformed_object_shapes_mint_and_audit_clean   (no-regression across all object shapes)
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VAULT_EDIT = REPO_ROOT / "scripts" / "lib" / "vault_edit.py"
BC_AUDIT = REPO_ROOT / "skills" / "build-slice" / "scripts" / "build_checks_audit.py"


def _append_rule(vault: Path, rule: dict) -> subprocess.CompletedProcess:
    """Drive `vault_edit append` of one rule onto build-checks.json/rules against an
    isolated temp vault (`--vault`), returning the completed process."""
    return subprocess.run(
        [sys.executable, str(VAULT_EDIT), "--vault", str(vault),
         "append", "--file", "build-checks.json", "--array", "rules",
         "--json", json.dumps(rule)],
        capture_output=True, text=True,
    )


def _update_rule(vault: Path, rule_id: str, set_expr: str) -> subprocess.CompletedProcess:
    """Drive `vault_edit update --set` on one bc rule against an isolated temp vault."""
    return subprocess.run(
        [sys.executable, str(VAULT_EDIT), "--vault", str(vault),
         "update", "--file", "build-checks.json", "--array", "rules",
         "--id", rule_id, "--set", set_expr],
        capture_output=True, text=True,
    )


def _rules(vault: Path) -> list:
    return json.loads((vault / "build-checks.json").read_text(encoding="utf-8")).get("rules", [])


def _seed(vault: Path) -> None:
    (vault / "build-checks.json").write_text('{"rules": []}', encoding="utf-8")


def _run_audit(slice_dir: Path, checks: Path) -> dict:
    """Run build_checks_audit --json against a project build-checks.json; return
    (parsed_json, returncode) so callers can assert BOTH the exit code (block vs not)
    and the structured violations/warnings."""
    cp = subprocess.run(
        [sys.executable, str(BC_AUDIT), "--slice", str(slice_dir),
         "--project-checks", str(checks), "--json"],
        capture_output=True, text=True,
    )
    try:
        payload = json.loads(cp.stdout)
    except json.JSONDecodeError:
        payload = {}
    return {"rc": cp.returncode, "json": payload, "stdout": cp.stdout, "stderr": cp.stderr}


def _write_checks(vault: Path, rules: list) -> Path:
    p = vault / "build-checks.json"
    p.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return p


_WELL_FORMED = {
    "severity": "important",
    "applies_when": {"glob": "schemas/**"},
    "rule": "a well-formed control rule",
    "rationale": "control",
}
# The exact historical malformation (BC-PROJ-11 at slice-068): applies_when as a bare string.
_MALFORMED = {
    "severity": "critical",
    "applies_when": "always:true",
    "rule": "a rule whose applies_when is a bare string, not an object",
    "rationale": "repro",
}
# m2: applies_when key entirely ABSENT (None -> not an object at mint).
_ABSENT = {
    "severity": "critical",
    "rule": "a rule with no applies_when key at all",
    "rationale": "repro-absent",
}


# ── AC3 (no-regression control) — a well-formed rule still mints cleanly ─────────
def test_wellformed_rule_mints_ok(tmp_path):
    """A rule with an object-shaped `applies_when` must mint (exit 0) and persist.
    This also proves the harness/id-allocator path works, so the malformed-rule
    failure below cannot be an environment artifact."""
    _seed(tmp_path)
    p = _append_rule(tmp_path, _WELL_FORMED)
    assert p.returncode == 0, f"well-formed rule append must succeed: {p.stderr}"
    rules = _rules(tmp_path)
    assert len(rules) == 1, f"well-formed rule must persist exactly once, got {rules}"
    assert rules[0].get("applies_when") == {"glob": "schemas/**"}


# ── AC1 (the headline repro) — a malformed rule is REJECTED at mint (append) ─────
def test_malformed_applies_when_rejected_at_mint(tmp_path):
    """A rule whose `applies_when` is a bare string (not the object shape) must be
    REJECTED at mint: non-zero exit AND not persisted. FAILS before the fix (the
    rule is accepted, exit 0, and minted a BC-PROJ id)."""
    _seed(tmp_path)
    p = _append_rule(tmp_path, _MALFORMED)
    rules = _rules(tmp_path)
    assert p.returncode != 0, (
        "a build-check rule with a malformed `applies_when` (bare string) must be "
        f"REJECTED at mint, but vault_edit accepted it (exit 0). stdout={p.stdout!r}"
    )
    assert not any(r.get("applies_when") == "always:true" for r in rules), (
        f"the malformed rule must NOT be persisted, but it was minted: {rules}"
    )
    # M-add-3: the mint-tier message names the offending FIELD, never a BC-PROJ id
    # (no id is allocated at mint). Confirm it points at `applies_when`.
    assert "applies_when" in p.stderr, f"reject message must name the field: {p.stderr!r}"


# ── AC1 (update leg) — M1: `update --set applies_when=<string>` is REJECTED ──────
def test_update_malformed_applies_when_rejected(tmp_path):
    """The UPDATE write leg was proven OPEN (`--set applies_when=always:true` -> exit 0,
    live rule corrupted). A post-mutation shape guard must REJECT it (non-zero exit) and
    leave the record's `applies_when` an object. FAILS before the fix."""
    _seed(tmp_path)
    assert _append_rule(tmp_path, _WELL_FORMED).returncode == 0
    rid = _rules(tmp_path)[0]["id"]
    u = _update_rule(tmp_path, rid, "applies_when=always:true")
    assert u.returncode != 0, (
        "`update --set applies_when=<bare string>` on a bc rule must be REJECTED, but "
        f"vault_edit accepted it (exit 0). stderr={u.stderr!r}"
    )
    aw = _rules(tmp_path)[0].get("applies_when")
    assert isinstance(aw, dict), (
        f"applies_when must REMAIN an object after a rejected update, got {aw!r}"
    )


# ── AC1 (absent key) — m2: applies_when entirely absent is REJECTED at mint ──────
def test_absent_applies_when_rejected_at_mint(tmp_path):
    """A rule with NO `applies_when` key (None -> not an object) must be REJECTED at mint,
    consistent with the non-dict case — not silently accepted (exit 0 today)."""
    _seed(tmp_path)
    p = _append_rule(tmp_path, _ABSENT)
    assert p.returncode != 0, (
        "a rule with an absent `applies_when` must be REJECTED at mint, but vault_edit "
        f"accepted it (exit 0). stdout={p.stdout!r}"
    )
    assert _rules(tmp_path) == [], f"the absent-applies_when rule must NOT persist: {_rules(tmp_path)}"


# ── AC2 (the genuine red->green) — an INERT rule WARNS visibly but does NOT block ─
def test_audit_inert_rule_warns_not_blocks(tmp_path):
    """A well-typed but INERT rule (dict `applies_when` with no fireable trigger, e.g. {})
    is silently `skipped` at exit 0 today. After the fix it must be a fail-VISIBLE,
    NON-blocking WARNING: the audit STILL exits 0 (migration-safe, M-add-1) but surfaces
    the rule in a `warnings` channel (warning-absent -> warning-present is the red->green)."""
    inert = {"id": "BC-TEST-INERT", "severity": "important",
             "applies_when": {}, "rule": "an inert rule that can never fire"}
    fireable = {"id": "BC-TEST-OK", "severity": "important",
                "applies_when": {"glob": "**/*.py"}, "rule": "a fireable control rule"}
    checks = _write_checks(tmp_path, [fireable, inert])
    res = _run_audit(tmp_path, checks)
    assert res["rc"] == 0, (
        "an inert rule must NOT hard-fail the audit (migration-safe warn-not-block, "
        f"M-add-1); got exit {res['rc']}. stderr={res['stderr']!r}"
    )
    warnings = res["json"].get("warnings") or []
    assert any("BC-TEST-INERT" in str(w) for w in warnings), (
        f"the inert rule must be surfaced as a visible warning, got warnings={warnings!r}"
    )
    # the fireable control never trips the warning
    assert not any("BC-TEST-OK" in str(w) for w in warnings), (
        f"a fireable rule must NOT warn as inert: {warnings!r}"
    )


# ── AC2 (label escalation) — non-dict applies_when -> malformed-rule Critical ─────
def test_audit_nondict_applies_when_labeled_malformed_critical(tmp_path):
    """A non-dict `applies_when` already exits 1 today (a `format`/Important drop). The fix
    ESCALATES its label to a distinct `malformed-rule` Critical violation with an
    'enforces nothing' message (M2 — diagnosability, gate outcome unchanged)."""
    bad = {"id": "BC-TEST-BAD", "severity": "critical",
           "applies_when": "always:true", "rule": "non-dict applies_when"}
    checks = _write_checks(tmp_path, [bad])
    res = _run_audit(tmp_path, checks)
    assert res["rc"] != 0, "a non-dict applies_when must still hard-fail the audit"
    viols = res["json"].get("violations") or []
    hit = [v for v in viols if v.get("kind") == "malformed-rule" and v.get("rule_id") == "BC-TEST-BAD"]
    assert hit, f"expected a `malformed-rule` violation for BC-TEST-BAD, got {viols!r}"
    assert hit[0].get("severity") == "Critical", f"malformed-rule must be Critical: {hit[0]!r}"
    assert "enforce" in hit[0].get("message", "").lower(), (
        f"message must state the rule enforces nothing: {hit[0]!r}"
    )


# ── AC2 (label escalation) — missing required key -> malformed-rule Critical ──────
def test_audit_missing_required_key_labeled_malformed_critical(tmp_path):
    """A rule missing a required key (`rule`) is drop-causing and already exits 1 today;
    the fix labels it a `malformed-rule` Critical violation (M2)."""
    bad = {"id": "BC-TEST-MISS", "severity": "critical", "applies_when": {"always": True}}
    checks = _write_checks(tmp_path, [bad])
    res = _run_audit(tmp_path, checks)
    assert res["rc"] != 0, "a rule missing a required key must hard-fail the audit"
    viols = res["json"].get("violations") or []
    hit = [v for v in viols if v.get("kind") == "malformed-rule" and v.get("rule_id") == "BC-TEST-MISS"]
    assert hit, f"expected a `malformed-rule` violation for BC-TEST-MISS, got {viols!r}"
    assert hit[0].get("severity") == "Critical", f"malformed-rule must be Critical: {hit[0]!r}"


# ── AC2 (M3) — the fireable predicate uses the SAME coercion _rule_applies uses ───
def test_fireable_predicate_agrees_with_rule_applies():
    """The audit-tier 'fireable' decision must derive from the SAME coercion `_rule_applies`
    uses (M3), or `{keywords: [""]}` slips: raw truthiness sees a non-empty list while
    `_rule_applies` coerces it to () and never fires. Assert `applies_when_is_fireable`
    agrees with `_rule_applies` on the degenerate edge inputs."""
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "skills" / "build-slice" / "scripts"))
    from scripts.lib import build_checks_integrity as bci
    import build_checks_audit as bca

    inert_shapes = [
        {},
        {"keywords": [""]},
        {"glob": [""]},
        {"glob": ""},
        {"always": False},
        {"anchors": ["x"]},           # anchors alone never fire (need keywords)
        {"negative_anchors": ["y"]},
    ]
    for aw in inert_shapes:
        assert bci.applies_when_is_fireable(aw) is False, f"{aw!r} must be classified inert"
        rule = {"id": "BC-X", "severity": "critical", "rule": "r", "applies_when": aw}
        parsed, _, _ = bca._parse_rules({"rules": [rule]}, "project", "x")
        assert parsed, f"{aw!r} should still parse into a rule (well-typed, just inert)"
        # never fires regardless of changed-files / slice-text inputs
        assert bca._rule_applies(parsed[0], ["", "x.py", "schemas/a"], "x y foo") is False, (
            f"{aw!r} must never fire, but _rule_applies returned True"
        )

    # positive control: a genuinely fireable rule agrees the other way
    aw = {"glob": "**/*.py"}
    assert bci.applies_when_is_fireable(aw) is True
    rule = {"id": "BC-Y", "severity": "critical", "rule": "r", "applies_when": aw}
    parsed, _, _ = bca._parse_rules({"rules": [rule]}, "project", "x")
    assert bca._rule_applies(parsed[0], ["foo.py"], "") is True


# ── AC4 (no-regression) — every well-formed object shape mints + audits clean ─────
def test_wellformed_object_shapes_mint_and_audit_clean(tmp_path):
    """All legitimate object-shaped `applies_when` forms (glob string, glob list, always,
    keywords) must mint (exit 0) and produce NO malformed-rule violations and NO inert
    warnings — the guard rejects only malformed/inert shapes (AC4 no-regression)."""
    shapes = [
        {"glob": "schemas/**"},
        {"glob": ["a/**", "b/*.ts"]},
        {"always": True},
        {"keywords": ["upload", "sse"]},
    ]
    _seed(tmp_path)
    for i, aw in enumerate(shapes):
        rule = {"severity": "important", "applies_when": aw, "rule": f"well-formed {i}"}
        p = _append_rule(tmp_path, rule)
        assert p.returncode == 0, f"well-formed shape {aw!r} must mint: {p.stderr}"
    assert len(_rules(tmp_path)) == len(shapes)
    res = _run_audit(tmp_path, tmp_path / "build-checks.json")
    assert res["rc"] == 0, f"well-formed rules must not fail the audit: {res['stdout']!r}"
    assert not (res["json"].get("warnings") or []), (
        f"well-formed fireable rules must not warn as inert: {res['json'].get('warnings')!r}"
    )
    mal = [v for v in (res["json"].get("violations") or []) if v.get("kind") == "malformed-rule"]
    assert not mal, f"well-formed rules must produce no malformed-rule violations: {mal!r}"
