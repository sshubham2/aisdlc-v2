"""
Unit coverage for VAL-1 Layer B internal-import resolution (SC-084 / slice-049 /
ADR-044) -- the safety + auditability + anchoring + crash-safety properties the
dual-Critic required (beyond the happy-path repro in tests/bugs/):

- m1  : the resolved_internal ledger records the correct `via` per zone.
- M2  : the accepted GLOBAL residual (an undeclared external shadowed by a
        same-named project sibling is resolved-internal) is REAL but stays
        VISIBLE in the ledger; and external-arm-first ordering means a DECLARED
        external is never mislabeled internal.
- M-add-1: resolution is anchored at project_root -- one project's modules do NOT
        leak as a trust set when validating a DIFFERENT project.
- M4  : _resolve_internal is TOTAL -- a probe error yields "not internal" (the
        import is flagged), never a crash / traceback (must_not_defer #1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILL_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import validate_slice_layers as vsl  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches():
    """The resolver memoizes per project_root; clear between tests so a rebuilt
    fixture tree under the same path is not masked by a stale cache."""
    vsl._DEV_DEPS_CACHE.clear()
    vsl._SCRIPT_ROOTS_CACHE.clear()
    yield
    vsl._DEV_DEPS_CACHE.clear()
    vsl._SCRIPT_ROOTS_CACHE.clear()


def _make_project(root: Path, *, package=None, sibling=None, dev_deps=None):
    """Build a synthetic project tree under `root`."""
    if package:
        pkgdir = root / package
        pkgdir.mkdir(parents=True, exist_ok=True)
        (pkgdir / "__init__.py").write_text("", encoding="utf-8")
    if sibling:
        sdir = root / "skills" / "demo" / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / f"{sibling}.py").write_text("x = 1\n", encoding="utf-8")
    if dev_deps:
        (root / "requirements-dev.txt").write_text(
            "\n".join(dev_deps) + "\n", encoding="utf-8"
        )


def _scan_src(root: Path, src: str, declared=None, sink=None):
    """Write src into root/tests/probe.py and scan it anchored at `root`."""
    probe_dir = root / "tests"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe = probe_dir / "probe.py"
    probe.write_text(src, encoding="utf-8")
    return vsl.scan_imports(
        [probe], declared if declared is not None else set(),
        project_root=root, audit_sink=sink,
    )


# --- m1: per-zone via labels -------------------------------------------------

def test_ledger_records_first_party_via(tmp_path):
    _make_project(tmp_path, package="mypkg")
    sink: list[dict] = []
    findings = _scan_src(tmp_path, "import mypkg\n", sink=sink)
    assert not findings
    assert sink and sink[0]["via"] == "first-party-package"
    assert sink[0]["import_name"] == "mypkg"


def test_ledger_records_sibling_via(tmp_path):
    _make_project(tmp_path, sibling="myhelper")
    sink: list[dict] = []
    findings = _scan_src(tmp_path, "import myhelper\n", sink=sink)
    assert not findings
    assert sink and sink[0]["via"] == "sibling-script"


def test_ledger_records_dev_dep_via(tmp_path):
    _make_project(tmp_path, dev_deps=["hypothesis>=6", "freezegun"])
    sink: list[dict] = []
    findings = _scan_src(tmp_path, "import hypothesis\n", sink=sink)
    assert not findings
    assert sink and sink[0]["via"] == "dev-dep"


# --- M2 + M3: shadow residual is real-but-visible; external-first ordering ----

def test_declared_external_wins_over_same_named_sibling(tmp_path):
    """M3/M2: a DECLARED external whose name matches a sibling resolves via the
    EXTERNAL arm -- it is NOT flagged and is NOT mislabeled internal in the
    ledger (external arm runs strictly first)."""
    _make_project(tmp_path, sibling="requests")
    sink: list[dict] = []
    findings = _scan_src(
        tmp_path, "import requests\n", declared={"requests"}, sink=sink,
    )
    assert not findings                      # resolved (declared) -> no finding
    assert sink == []                        # NOT recorded as internal -> no mislabel


def test_undeclared_external_shadowed_by_sibling_is_resolved_but_visible(tmp_path):
    """M2 (accepted residual, eyes-open): an UNDECLARED external whose name
    collides with a project sibling IS resolved-internal (the accepted cost of
    the project-scoped membership rule) -- but it stays VISIBLE in the audit
    ledger, so over-suppression is detectable. This test documents the residual;
    it does not pretend the fix closes it."""
    _make_project(tmp_path, sibling="requests")
    sink: list[dict] = []
    findings = _scan_src(tmp_path, "import requests\n", declared=set(), sink=sink)
    assert not findings                      # suppressed (matches a real sibling)
    assert sink and sink[0]["import_name"] == "requests"
    assert sink[0]["via"] == "sibling-script"   # visible in the ledger


# --- M-add-1: anchoring -- no cross-project trust leak ------------------------

def test_no_cross_project_leak(tmp_path):
    """A module that exists under project A must NOT be trusted when validating a
    DIFFERENT project B (the resolver anchors on project_root, not on its own
    install location)."""
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    _make_project(proj_a, sibling="ahelper", package="apkg")
    _make_project(proj_b)  # empty project
    # Validating B: A's modules are unknown externals -> flagged.
    findings = _scan_src(proj_b, "import ahelper\nimport apkg\n", declared=set())
    flagged = {f.import_name for f in findings}
    assert flagged == {"ahelper", "apkg"}
    # Validating A: they resolve.
    vsl._SCRIPT_ROOTS_CACHE.clear()
    findings_a = _scan_src(proj_a, "import ahelper\nimport apkg\n", declared=set())
    assert findings_a == []


# --- M4: total / crash-safe --------------------------------------------------

def test_resolve_internal_total_on_probe_error(tmp_path, monkeypatch):
    """_resolve_internal must be TOTAL: an unexpected error inside a probe yields
    None (not internal -> flagged), never a propagated exception."""
    def _boom(_root):
        raise RuntimeError("injected probe failure")

    monkeypatch.setattr(vsl, "_read_dev_deps", _boom)
    # Direct call: returns None, does not raise.
    assert vsl._resolve_internal("pytest", tmp_path) is None


def test_scan_imports_crash_safe_on_resolver_probe_error(tmp_path, monkeypatch):
    """A probe raising an UNEXPECTED error inside _resolve_internal must not crash
    the scan -- the TOTAL guard swallows it and the import falls through to a
    finding (fail toward flagging), no traceback (must_not_defer #1 crash clause).

    We raise from _read_dev_deps (called ONLY by _resolve_internal, so the
    external arm _check_import_resolves is unaffected and still runs first)."""
    def _boom(_root):
        raise RuntimeError("injected dev-deps read failure")

    monkeypatch.setattr(vsl, "_read_dev_deps", _boom)
    _make_project(tmp_path, sibling="myhelper")
    # 'myhelper' would normally resolve as a sibling; with _read_dev_deps raising
    # inside _resolve_internal, its TOTAL except returns None -> the import is
    # flagged instead of crashing the gate.
    findings = _scan_src(tmp_path, "import myhelper\n")
    assert {f.import_name for f in findings} == {"myhelper"}
