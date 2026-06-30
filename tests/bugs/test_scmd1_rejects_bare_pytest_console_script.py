"""BFRD-1 repro for slice-046 / SC-081, TOOL-3 (SCMD-1 bare-pytest false-FAIL on Windows).

Bug. SCMD-1 (skills/validate-slice/scripts/shippability_decoupling_audit.py) ACCEPTS a
bare `pytest tests/...` machine_cmd (the bare pytest *console-script*). On a host where
that console-script is not on PATH (Windows; a venv / pinned-$PY whose Scripts dir is off
PATH), the shippability runner can't execute it (WinError 2) and the row false-FAILs the
regression check -- masquerading as an unrelated slice's regression (this bit slice-007's
SHIP-006 row). The portable form is `<interp> -m pytest tests/...`, which always runs.

Repro shape. A DIFFERENTIAL assertion at the SCMD-1 audit boundary, so it is deterministic
(no subprocess, independent of whether `pytest` happens to be on PATH on the test host) and
NOT coupled to the exact grammar /design-slice chooses: a NON-portable bare-pytest
console-script command must produce strictly MORE SCMD-1 violations than the portable
`<interp> -m pytest` form.

Current (pre-fix) behaviour: SCMD-1's grammar accepts BOTH forms -> both yield 0 violations
-> `len(bare) > len(portable)` is `0 > 0` == False -> this test FAILS (red), reproducing the
bug. After the fix (a shared portable-runnable-command validator wired into SCMD-1) the bare
console-script yields >=1 violation and the portable form 0 -> green.

RECONCILIATION NOTE for the builder (AC2): tests/bugs/test_shippability_nonpytest_cmd.py
(slice-011) DELIBERATELY asserts bare `pytest tests/x.py` is ACCEPTED. The fix that turns
bare-pytest into a violation here WILL break that slice-011 case -- that is the load-bearing
design tension; reconcile the slice-011 battery coherently (per the recorded ADR), do not
silently delete it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
for _p in (REPO_ROOT, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec so @dataclass can resolve __module__
    spec.loader.exec_module(mod)
    return mod


scmd = _load("skills/validate-slice/scripts/shippability_decoupling_audit.py",
             "shippability_decoupling_audit")


def _catalog_with(tmp_path: Path, machine_cmd: str) -> Path:
    cat = {
        "_schema": "aisdlc/shippability@1",
        "rows": [{
            "id": "SHIP-REPRO",
            "slice": "slice-046",
            "what": "a single machine_cmd row under test",
            "machine_cmd": machine_cmd,
            "added_at": "2026-06-30T00:00:00Z",
        }],
    }
    p = tmp_path / "shippability.json"
    p.write_text(json.dumps(cat, indent=2), encoding="utf-8")
    return p


_BARE_PYTEST = "pytest tests/x.py -q"
_PORTABLE = "python -m pytest tests/x.py -q"


def test_scmd1_flags_bare_pytest_console_script_as_nonportable(tmp_path: Path):
    """A bare-pytest console-script command must produce MORE SCMD-1 violations than the
    portable `<interp> -m pytest` form. FAILS pre-fix (both forms accepted -> 0 == 0)."""
    bare = scmd.audit(_catalog_with(tmp_path, _BARE_PYTEST)).violations
    portable = scmd.audit(_catalog_with(tmp_path, _PORTABLE)).violations
    assert len(bare) > len(portable), (
        "SCMD-1 did not distinguish the non-portable bare-pytest console-script from the "
        f"portable `<interp> -m pytest` form: bare={len(bare)} violation(s), "
        f"portable={len(portable)} violation(s). The bare console-script is not guaranteed "
        "on PATH (Windows / venv) and false-FAILs the regression; SCMD-1 must flag it."
    )


def test_scmd1_still_accepts_portable_interp_anchored_form(tmp_path: Path):
    """Regression guard: the portable `<interp> -m pytest` form stays accepted (0 violations)."""
    portable = scmd.audit(_catalog_with(tmp_path, _PORTABLE)).violations
    assert portable == [], (
        "the portable `<interp> -m pytest` form must remain accepted, but SCMD-1 flagged it: "
        + "; ".join(v.detail for v in portable)
    )
