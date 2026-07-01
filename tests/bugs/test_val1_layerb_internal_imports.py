"""
Bug (SC-084 / slice-049): VAL-1 Layer B false-flags the project's OWN internal
imports as a 'hallucinated-import'.

skills/validate-slice/scripts/validate_slice_layers.py::scan_imports resolved an
import's top-level name only as stdlib / a declared dependency / a known alias. It
had NO notion of the project's own modules made importable by the sys.path
bootstrap -- the internal ``scripts`` package, a bare sibling single-skill script,
or the ``pytest`` dev-dependency -- so it false-flagged every internal import as a
'Possible AI hallucination' (4 such false positives surfaced on slice-046).

The fix adds a narrow internal-resolution arm keyed on SOURCE-TREE EXISTENCE
(never importlib.find_spec) anchored at the PROJECT under validation (project_root),
so a genuinely-undeclared EXTERNAL package is still flagged (AC4) while the
project's own modules and its declared dev-deps are recognized.

These tests pin the behavioral contract (AC1-AC5). They pass project_root
EXPLICITLY (cwd-independent) and declared_deps=set() (EMPTY) -- proving the
internal / dev-dep resolution is caller-independent (SC-084 / M-add-2): it does
NOT rely on the caller supplying the names in `declared`.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import validate_slice_layers as vsl  # noqa: E402

_SIBLING = "shippability_path_audit"
_EXTERNAL = "zzz_phantom_external_pkg_xyz"          # genuinely undeclared -> must flag

# a real sibling single-skill script must exist for the AC2 fixture to be honest
assert (_SKILL_SCRIPTS / f"{_SIBLING}.py").exists(), (
    f"fixture invalid: expected a real sibling script {_SIBLING}.py to exist"
)


def _scan(fixture_src: str):
    """Write a fixture .py under tests/bugs/, scan it with an EMPTY declared set
    and project_root=REPO_ROOT (explicit -> cwd-independent), return
    (flagged import names, {import_name: via} ledger map)."""
    bugs_dir = REPO_ROOT / "tests" / "bugs"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", dir=str(bugs_dir), delete=False, encoding="utf-8"
    ) as fh:
        fh.write(fixture_src)
        fixture_path = Path(fh.name)
    try:
        sink: list[dict] = []
        findings = vsl.scan_imports(
            [fixture_path], set(), project_root=REPO_ROOT, audit_sink=sink,
        )
        flagged = {f.import_name for f in findings}
        resolved = {r["import_name"]: r["via"] for r in sink}
        return flagged, resolved
    finally:
        fixture_path.unlink(missing_ok=True)


def test_internal_package_not_flagged():
    """AC1: a change importing the project's own internal package produces NO
    Layer B finding, and is recorded as first-party in the audit ledger."""
    flagged, resolved = _scan("from scripts.lib import vault_edit\n")
    assert "scripts" not in flagged, (
        f"internal package 'scripts' was false-flagged (flagged={sorted(flagged)})"
    )
    assert resolved.get("scripts") == "first-party-package"


def test_sibling_script_not_flagged():
    """AC2: a change importing a sibling single-skill script produces NO Layer B
    finding, and is recorded as a sibling in the audit ledger."""
    flagged, resolved = _scan(f"import {_SIBLING}\n")
    assert _SIBLING not in flagged, (
        f"sibling script '{_SIBLING}' was false-flagged (flagged={sorted(flagged)})"
    )
    assert resolved.get(_SIBLING) == "sibling-script"


def test_pytest_devdep_not_flagged():
    """AC3: a change importing the pytest dev-dep (declared in requirements-dev.txt,
    NOT in the caller's declared set) produces NO Layer B finding."""
    flagged, resolved = _scan("import pytest\n")
    assert "pytest" not in flagged, (
        f"dev-dep 'pytest' was false-flagged (flagged={sorted(flagged)})"
    )
    assert resolved.get("pytest") == "dev-dep"


def test_undeclared_external_still_flagged():
    """AC4 (SAFETY, load-bearing): a genuinely-undeclared EXTERNAL package is STILL
    flagged -- no over-suppression / no false negative."""
    flagged, resolved = _scan(f"import {_EXTERNAL}\n")
    assert _EXTERNAL in flagged, (
        f"safety regression: undeclared external '{_EXTERNAL}' was NOT flagged "
        f"(flagged={sorted(flagged)})"
    )
    assert _EXTERNAL not in resolved


def test_repro_layerb_internal_imports_passes():
    """AC5: the original SHIP-046 reproduction -- all internal/dev imports resolved,
    only the genuinely-undeclared external flagged -- in one fixture."""
    flagged, resolved = _scan(
        "from scripts.lib import vault_edit\n"
        f"import {_SIBLING}\n"
        "import pytest\n"
        f"import {_EXTERNAL}\n"
    )
    assert flagged == {_EXTERNAL}, (
        f"expected only the undeclared external flagged, got {sorted(flagged)}"
    )
    assert set(resolved) == {"scripts", _SIBLING, "pytest"}
