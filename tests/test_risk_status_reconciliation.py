"""tests/test_risk_status_reconciliation.py — lock the risk-register `status` enum to ONE
source (scripts/lib/risk_status.RISK_STATUSES) across every enforcer (slice-010 / ADR-008).

This is the cross-enforcer containment test that turns "the tools agree" from documentation
into an ENFORCED property. It checks four things:

  (a) behavior: each canonical status is ACCEPTED by BOTH validators (artifact_lint and
      risk_register_audit) via their real entrypoints, and an out-of-set 'bogus' status is
      REJECTED by BOTH (the reconciliation widens the set, it never disables the check);
  (b) drift tripwire: both validators' allowed-sets ARE the canonical set (a future re-hardcode
      to a different literal fails here);
  (c) producer containment (critique M1): the risk-spike step writes the status as SKILL.md
      prose, not importable code, so the test EXTRACTS the producer alphabet from that prose
      and asserts it is a subset of the canonical set -- the text source can't silently drift;
  (d) stale-pin recognition (AC5 / critique M-add-1): the stale-pin audit's status regex,
      derived from the canonical set, now recognizes the elevated `blocking`/`conditional`
      statuses (which it was structurally blind to before this slice).
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINT = REPO / "scripts" / "lib" / "artifact_lint.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.risk_status import RISK_STATUSES          # noqa: E402
from scripts.lib.artifact_lint import KNOWN_ENUMS          # noqa: E402
from scripts.lib import risk_register_audit as rra         # noqa: E402

CANONICAL = sorted(RISK_STATUSES)


def _load(relpath, name):
    """Load a (possibly hyphenated-dir) single-skill script by path — repo convention."""
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _register(status):
    """A minimal, otherwise-valid risk-register.json carrying one risk with `status`."""
    return {
        "_schema": "aisdlc/risk-register@1",
        "project": "t",
        "risks": [{"id": "R-1", "title": "x", "likelihood": "low", "impact": "low", "status": status}],
        "updated": "2026-06-15T00:00:00Z",
    }


def _artifact_lint_rejects_status(status):
    """True iff artifact_lint (real CLI entrypoint) reports a status violation for `status`."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "risk-register.json"
        f.write_text(json.dumps(_register(status)), encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(LINT), str(f), "--json"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        res = json.loads(out.stdout)
    return any("status" in v for v in res["violations"])


def _audit_rejects_status(status):
    """True iff risk_register_audit reports a status violation for `status`. Isolated from
    score/band noise: an invalid status short-circuits before those checks, and a minimal
    fixture with absent score/band produces no other violation for a canonical status."""
    _, violations = rra._parse_risks(_register(status), "test")
    return any("status '" in v.message for v in violations)


# (a) behavior ---------------------------------------------------------------
@pytest.mark.parametrize("status", CANONICAL)
def test_every_canonical_status_accepted_by_both(status):
    assert not _artifact_lint_rejects_status(status), f"artifact_lint wrongly rejects '{status}'"
    assert not _audit_rejects_status(status), f"risk_register_audit wrongly rejects '{status}'"


def test_bogus_status_rejected_by_both():
    assert _artifact_lint_rejects_status("bogus"), "artifact_lint must reject an out-of-set status"
    assert _audit_rejects_status("bogus"), "risk_register_audit must reject an out-of-set status"


# (b) drift tripwire ---------------------------------------------------------
def test_both_validators_source_the_canonical_set():
    assert KNOWN_ENUMS[("risk-register", "risks[].status")] == RISK_STATUSES, \
        "artifact_lint's risk-status enum has drifted from the canonical set"
    assert rra._ALLOWED_STATUSES == RISK_STATUSES, \
        "risk_register_audit's _ALLOWED_STATUSES has drifted from the canonical set"


# (c) producer containment — risk-spike SKILL.md prose subset of canonical (M1) ----
def test_producer_alphabet_subset_of_canonical():
    skill = (REPO / "skills" / "risk-spike" / "SKILL.md").read_text(encoding="utf-8")
    # Step 5 writes the RISK-register status as an angle-bracket alternation:
    #   --set status=<retired|blocking|conditional>
    # (the literal `--set status=active/blocked` lines write CANDIDATE status, not risk status,
    #  and carry no `<...>`, so this extractor correctly ignores them.)
    matches = re.findall(r"--set status=<([^>]+)>", skill)
    assert matches, "no `--set status=<...>` producer line in risk-spike SKILL.md (doc structure changed?)"
    produced = {s.strip() for m in matches for s in m.split("|")}
    assert produced, "extracted an empty producer alphabet"
    assert produced <= RISK_STATUSES, \
        f"risk-spike writes statuses outside the canonical set: {sorted(produced - RISK_STATUSES)}"


# (d) stale-pin audit recognizes the elevated statuses (AC5 / M-add-1) --------
def test_pin_audit_recognizes_elevated_statuses():
    stp = _load("skills/build-slice/scripts/state_transition_pin_audit.py", "stp_audit_slice010")
    rx = stp._RISK_STATUS_FN_RE
    assert rx.search("test_r5_stays_blocking"), "pin-audit is blind to a 'blocking' stale-pin"
    assert rx.search("test_r5_stays_conditional"), "pin-audit is blind to a 'conditional' stale-pin"
    assert rx.search("test_r3_stays_open"), "pin-audit regressed on the pre-existing 'open'"
    assert not rx.search("test_r5_stays_bogus"), "pin-audit must not match a non-status word"
