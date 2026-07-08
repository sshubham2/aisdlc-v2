"""
Bug (SC-082): pre_finish_gate.py forwards --ack-critical to build_checks_audit as a
SINGLE un-split token.

skills/build-slice/scripts/pre_finish_gate.py builds the BC-1 (build_checks_audit) command
and forwards the attested Critical-rule acks like:

    if args.ack_critical:
        bc += ["--ack-critical", args.ack_critical]          # <-- the bug

`--ack-critical` is declared `default=""` (a single-value string arg) whose help says
"comma/space list". So a real ack of two ids -- `--ack-critical "BC-PROJ-1,BC-PROJ-3"` --
is forwarded as the ONE token "BC-PROJ-1,BC-PROJ-3". build_checks_audit declares
`--ack-critical nargs='*'` and does `set(ack_critical)` with no comma-splitting, so it
receives the single set element {"BC-PROJ-1,BC-PROJ-3"} which matches NEITHER rule id.

Expected: the two acked ids reach build_checks_audit as SEPARATE argv tokens
          "BC-PROJ-1" and "BC-PROJ-3" (so its nargs='*' set contains both ids).
Actual:   they arrive joined as one token "BC-PROJ-1,BC-PROJ-3", so any slice with >=2
          applicable Critical rules can never pass the consolidated BC-1 sub-check via
          the wrapper (only bypassing it works).

Fix (in the slice): split on comma/whitespace before forwarding, e.g.
    bc += ["--ack-critical", *args.ack_critical.replace(",", " ").split()]
(build_checks_audit's nargs='*' already accepts the resulting tokens; no audit change.)

This test drives pre_finish_gate.run_gate with a monkeypatched `_run` that captures the
BC-1 argv, and asserts the ids arrive split. It does NOT execute any subprocess.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]  # tests/bugs/ -> tests/ -> repo root
_GATE_PATH = _REPO / "skills" / "build-slice" / "scripts" / "pre_finish_gate.py"
_BCA_PATH = _REPO / "skills" / "build-slice" / "scripts" / "build_checks_audit.py"


def _load_gate():
    """Import the worktree's pre_finish_gate.py by file path (single-skill script)."""
    spec = importlib.util.spec_from_file_location("pre_finish_gate_under_test", _GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the @dataclass CheckResult (with `from __future__ import
    # annotations`) resolves its field types via sys.modules[cls.__module__] at class-
    # creation time, which fails if the module isn't registered under its own name.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ns(ack_critical: str, **over) -> argparse.Namespace:
    base = dict(
        slice="slice-x",
        worktree=".",
        changed_files=[],
        changed_test_files=[],
        ack_critical=ack_critical,
        seam_allowlist=None,
        strict=True,
        test_first=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _capture_bc_argv(monkeypatch, ack_critical: str) -> list[str]:
    """Run the gate with _run stubbed; return the argv forwarded for the BC-1 check."""
    gate = _load_gate()
    captured: dict[str, list[str]] = {}

    def fake_run(name, argv, cwd):
        captured[name] = list(argv)
        return gate.CheckResult(name=name, status="PASS", exit_code=0,
                                summary="(stubbed)", command=list(argv))

    monkeypatch.setattr(gate, "_run", fake_run)
    gate.run_gate(_ns(ack_critical))
    assert "BC-1" in captured, "BC-1 check was never assembled"
    return captured["BC-1"]


def test_multi_ack_forwarded_as_separate_tokens(monkeypatch):
    """AC1/AC3: a comma-separated --ack-critical reaches build_checks_audit split.

    Fails on HEAD: the BC-1 argv contains the joined token 'BC-PROJ-1,BC-PROJ-3'
    instead of the two ids as separate elements.
    """
    argv = _capture_bc_argv(monkeypatch, "BC-PROJ-1,BC-PROJ-3")
    assert "--ack-critical" in argv, "BC-1 should forward --ack-critical when acks are present"

    # The joined token must NOT survive into the forwarded argv.
    assert "BC-PROJ-1,BC-PROJ-3" not in argv, (
        "ack ids were forwarded as a single un-split token "
        "'BC-PROJ-1,BC-PROJ-3' -- build_checks_audit's set() will match neither id"
    )
    # Both ids must arrive as their own argv tokens.
    assert "BC-PROJ-1" in argv and "BC-PROJ-3" in argv, (
        "both acked Critical-rule ids must reach build_checks_audit as separate tokens"
    )


def test_space_separated_ack_also_split(monkeypatch):
    """AC1: a whitespace-separated ack ('BC-PROJ-1 BC-PROJ-3') is also forwarded split."""
    argv = _capture_bc_argv(monkeypatch, "BC-PROJ-1 BC-PROJ-3")
    assert "BC-PROJ-1 BC-PROJ-3" not in argv
    assert "BC-PROJ-1" in argv and "BC-PROJ-3" in argv


def test_empty_ack_forwards_no_token(monkeypatch):
    """Must-not-defer: an empty --ack-critical forwards NO --ack-critical token
    (no spurious empty value). Already correct on HEAD; pins it against the fix."""
    argv = _capture_bc_argv(monkeypatch, "")
    assert "--ack-critical" not in argv


# --- AC4: regression breadth on the single-id / whitespace-only forms ---------

def test_single_id_forwarded(monkeypatch):
    """AC4: a single acked id is still forwarded as its own token (no regression)."""
    argv = _capture_bc_argv(monkeypatch, "BC-PROJ-1")
    assert "--ack-critical" in argv
    assert "BC-PROJ-1" in argv


def test_whitespace_only_forwards_no_token(monkeypatch):
    """AC4 / must-not-defer: a whitespace-only ack collapses to the no-ack path
    (no spurious bare flag, no empty token)."""
    argv = _capture_bc_argv(monkeypatch, "   ")
    assert "--ack-critical" not in argv


# --- AC3 / M-add-1: end-to-end through the REAL build_checks_audit -------------

def _load_build_checks_audit():
    """Import the worktree's build_checks_audit.py by file path (single-skill script)."""
    spec = importlib.util.spec_from_file_location("build_checks_audit_under_test", _BCA_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # registered before exec (frozen dataclasses resolve field types)
    spec.loader.exec_module(mod)
    return mod


def _crit_rule(rule_id: str) -> dict:
    """A minimal Critical build-checks rule that applies to any changed .py file."""
    return {
        "id": rule_id,
        "severity": "critical",
        "rule": f"fixture critical rule {rule_id}",
        "applies_when": {"glob": "**/*.py"},
    }


def test_multi_critical_ack_recognized_end_to_end(tmp_path):
    """AC3 / M-add-1: the END-STATE the bug broke -- two acknowledged Critical
    rules forwarded through the gate's split helper are BOTH recognized by the
    real build_checks_audit (the `acknowledged` set), with zero
    unacknowledged-critical violations. Proven through the actual audit, not by
    argv-level implication.

    The contrast case (the pre-fix JOINED single token) demonstrates the bug
    end-to-end: neither id matches -> both Criticals counted unacknowledged.
    """
    gate = _load_gate()
    bca = _load_build_checks_audit()

    checks = tmp_path / "build-checks.json"
    checks.write_text(json.dumps({"rules": [_crit_rule("BC-FIXTURE-1"),
                                            _crit_rule("BC-FIXTURE-2")]}),
                      encoding="utf-8")
    slice_dir = tmp_path / "slice"
    slice_dir.mkdir()
    changed = ["skills/build-slice/scripts/pre_finish_gate.py"]

    def _unacked(result):
        return [v for v in result.violations if v.kind == "unacknowledged-critical"]

    def _applicable_crit(result):
        return {r.rule_id for r in result.applicable if r.severity.lower() == "critical"}

    # The builder types the comma list; the gate's helper splits it before it
    # would reach build_checks_audit. Feed the SPLIT result to the real audit.
    ids = gate._split_ack_critical("BC-FIXTURE-1,BC-FIXTURE-2")
    assert ids == ["BC-FIXTURE-1", "BC-FIXTURE-2"]
    result = bca.audit_slice(slice_dir, project_checks=checks,
                             changed_files=changed, strict=True,
                             ack_critical=tuple(ids))
    assert _applicable_crit(result) == {"BC-FIXTURE-1", "BC-FIXTURE-2"}, \
        "both fixture Critical rules must apply via the .py glob"
    assert _unacked(result) == [], (
        "both acked Critical ids must be recognized end-to-end through "
        f"build_checks_audit; got unacknowledged: {[v.rule_id for v in _unacked(result)]}"
    )

    # Contrast: forwarding the JOINED token (the pre-fix bug) leaves BOTH
    # Criticals unacknowledged -- the exact failure this slice fixes.
    joined = bca.audit_slice(slice_dir, project_checks=checks,
                             changed_files=changed, strict=True,
                             ack_critical=("BC-FIXTURE-1,BC-FIXTURE-2",))
    assert {v.rule_id for v in _unacked(joined)} == {"BC-FIXTURE-1", "BC-FIXTURE-2"}, (
        "the joined single-token ack (pre-fix behavior) must fail to match either "
        "rule id -- proving the bug the split fixes"
    )
