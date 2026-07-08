"""
Bug: WS-1 --execute does not reality-check / fail-closed on a NON-PORTABLE verification.

A walking_skeleton mission-brief whose architectural_layers row is marked
status:'exercised' (an assertion that reality WAS contacted) but whose
`verification` is a non-portable bare-`pytest` command is NOT gated.
`brief_variants_audit._execute_verifications` demotes a not-runnable verification
to a non-gating ADVISORY (M2: a silent pass) and never interpreter-normalizes the
bare interpreter (B1) -- unlike `shippability_runner.run_catalog`, which normalizes
bare python/pytest via `_normalize_interp` and fails closed. The same non-portability
that SCMD-1 already rejects for a shippability `machine_cmd` (slice-046 / SC-081's
`scripts/lib/runnable_command.py`) sails through the WS-1 walking-skeleton gate.

Expected (post shared fail-closed execution-core + static WS-1 portability gate,
reusing runnable_command.py): a GATING violation (STOP) is raised for a non-portable
'exercised' verification -- the marker claims reality contact the command cannot
guarantee, so the audit must fail closed, not silently pass / demote to an advisory.

Actual (current): NO gating violation. The layer either passes (pytest console-script
on PATH) or is demoted to a non-gating advisory (pytest absent). Either way
`result.violations` is empty.

Runs in <10s (one audit() call over an in-memory brief; no real test execution needed
because the contract the fix introduces is a STATIC portability check).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Plugin root on sys.path so `scripts.lib.*` imports resolve when this test is run
# directly (mirrors tests/conftest.py for the tests/ tree).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.lib.brief_variants_audit import SPECS, audit


def _write_brief(tmp_path: Path, verification: str) -> Path:
    brief = {
        "_schema": "aisdlc/mission-brief@1",
        "slice": "slice-047",
        "variants": {"walking_skeleton": True},
        "architectural_layers": [
            {
                "layer": "service",
                "component": "demo",
                "verification": verification,
                "status": "exercised",
            }
        ],
    }
    p = tmp_path / "mission-brief.json"
    p.write_text(json.dumps(brief), encoding="utf-8")
    return p


def test_ws1_nonportable_exercised_verification_is_gated(tmp_path: Path) -> None:
    # A bare-`pytest tests/...` verification is non-portable: it depends on the ambient
    # PATH (absent on Windows / a venv whose Scripts dir is off PATH -- the documented
    # dev setup) instead of being interpreter-anchored (`<interp> -m pytest ...`). This
    # is the form runnable_command.classify flags as `non_portable_console_script`, so
    # the corrected STATIC WS-1 gate (slice-047) catches it BEFORE/independent of
    # execution -- deterministically, regardless of PATH (B2/critique).
    #
    # The target is a REAL, PASSING test file on purpose: against the CURRENT (buggy)
    # code the brief yields ZERO gating violations whether or not pytest is on PATH --
    # off PATH -> FileNotFoundError -> non-gating advisory; on PATH -> the file's tests
    # pass (exit 0) -> verified, no violation. Either way result.violations is empty
    # today, so this test FAILS now and PASSES once the static gate lands.
    brief = _write_brief(tmp_path, "pytest tests/test_brief_variants.py")
    spec = SPECS["walking_skeleton"]

    result = audit(brief, spec, execute=True, root=tmp_path)

    # The 'exercised' marker asserts reality was contacted; a non-portable verification
    # cannot guarantee that, so the audit MUST raise a gating violation (fail-closed) --
    # NOT silently pass and NOT demote to a non-gating advisory.
    assert result.violations, (
        "expected a GATING violation for the non-portable bare-pytest 'exercised' "
        f"verification; got violations={[v.to_dict() for v in result.violations]} "
        f"advisories={result.advisories} executions={result.executions}"
    )
    implicating = [
        v
        for v in result.violations
        if "verif" in (v.message or "").lower()
        or "portab" in (v.message or "").lower()
        or "verif" in (v.kind or "").lower()
        or "portab" in (v.kind or "").lower()
    ]
    assert implicating, (
        "a violation was raised but none implicates the non-portable verification; "
        f"got {[v.to_dict() for v in result.violations]}"
    )
