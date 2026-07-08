"""slice-064 / SC-118 / ADR-061 -- the merged-catalog verdict-identity + attribution suite.

Proves the batch-and-scatter run_catalog (MERGEABLE pytest rows in ONE session) is
verdict-identical to the serial per-row runner, attributes failures to the exact
row, and never turns a real failure into a silent PASS -- across the shapes the
critique + DR-1 flagged: multi-file rows (B1), same-file whole+selector overlap
(M-add-1), an order-dependent shared-session pair (M1/M2), an interleaved partition
(M-add-2), a hung row (AC3), below-normal priority (AC4), and the
integration_health_gate cross-skill JSON contract through the merged path (AC5/M4).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "skills" / "validate-slice" / "scripts"
for _p in (REPO_ROOT, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("skills/validate-slice/scripts/shippability_decoupling_audit.py", "shippability_decoupling_audit")
_load("skills/validate-slice/scripts/shippability_path_audit.py", "shippability_path_audit")
_load("skills/validate-slice/scripts/catalog_merge.py", "catalog_merge")
_runner = _load("skills/validate-slice/scripts/shippability_runner.py", "ai_sdlc_sr_batch")
run_catalog = _runner.run_catalog


# ----------------------------- fixture helpers -----------------------------

def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _catalog(repo: Path, rows: list[dict]) -> Path:
    p = repo / "shippability.json"
    p.write_text(json.dumps({"_schema": "aisdlc/shippability@1", "rows": rows}, indent=2),
                 encoding="utf-8")
    return p


def _row(rid: str, cmd: str, **extra) -> dict:
    r = {"id": rid, "slice": "s064", "kind": "test", "description": rid,
         "machine_cmd": cmd, "added": "2026-01-01T00:00:00Z"}
    r.update(extra)
    return r


def _by_index(result) -> dict:
    return {r.index: r.status for r in result.rows}


# ----------------------------- AC1: differential -----------------------------

def test_ac1_merged_equals_serial_on_session_safe_catalog(tmp_path: Path):
    """AC1: merged == serial per-row verdict across ::selector / whole-file / MULTI-FILE (B1)
    / same-file overlap (M-add-1) / absent / non-pytest rows on a session-safe suite."""
    repo = tmp_path
    _write(repo, "tests/test_a.py", "def test_p():\n    assert True\ndef test_q():\n    assert True\n")
    _write(repo, "tests/test_b.py", "def test_r():\n    assert True\ndef test_s():\n    assert False\n")
    _write(repo, "tests/test_c.py", "def test_t():\n    assert True\n")
    rows = [
        _row("R-sel-pass", "python -m pytest tests/test_a.py::test_p -q"),
        _row("R-wholefile-fail", "python -m pytest tests/test_b.py -q"),          # test_s fails
        _row("R-multifile", "python -m pytest tests/test_a.py::test_q tests/test_c.py::test_t -q"),  # B1
        _row("R-overlap-whole", "python -m pytest tests/test_a.py -q"),           # M-add-1 (overlaps R-sel-pass file)
        _row("R-absent", "python -m pytest tests/test_absent.py::test_x -q"),     # ABSENT
        _row("R-nonpytest", 'python -c "print(1)"'),                              # standalone
    ]
    cat = _catalog(repo, rows)

    merged = _by_index(run_catalog(cat, repo_root=repo, merge=True))
    serial = _by_index(run_catalog(cat, repo_root=repo, merge=False))

    assert merged == serial, f"merged != serial: {merged} vs {serial}"
    # and the verdicts are the CORRECT ones
    by_id = {r.row: r.status for r in run_catalog(cat, repo_root=repo, merge=True).rows}
    assert by_id["R-sel-pass"] == "PASS"
    assert by_id["R-wholefile-fail"] == "FAIL"
    assert by_id["R-multifile"] == "PASS"
    assert by_id["R-overlap-whole"] == "PASS"   # test_a's tests all pass
    assert by_id["R-absent"] == "ABSENT"
    assert by_id["R-nonpytest"] == "PASS"


# ----------------------------- AC2: attribution -----------------------------

def test_ac2_single_seeded_failure_attributed(tmp_path: Path):
    """AC2: a single failing mergeable row FAILs only itself; others keep PASS/ABSENT."""
    repo = tmp_path
    _write(repo, "tests/test_ok.py", "def test_a():\n    assert True\n")
    _write(repo, "tests/test_bad.py", "def test_b():\n    assert False\n")
    _write(repo, "tests/test_ok2.py", "def test_c():\n    assert True\n")
    cat = _catalog(repo, [
        _row("OK", "python -m pytest tests/test_ok.py::test_a -q"),
        _row("BAD", "python -m pytest tests/test_bad.py::test_b -q"),
        _row("OK2", "python -m pytest tests/test_ok2.py::test_c -q"),
        _row("GONE", "python -m pytest tests/test_gone.py -q"),
    ])
    res = run_catalog(cat, repo_root=repo, merge=True)
    by_id = {r.row: r.status for r in res.rows}
    failed_rows = [r.row for r in res.rows if r.status == "FAIL"]
    assert failed_rows == ["BAD"]
    assert by_id == {"OK": "PASS", "BAD": "FAIL", "OK2": "PASS", "GONE": "ABSENT"}
    assert res.failed == 1 and res.passed == 2 and res.absent == 1


def test_ac2_multifile_failure_in_second_file(tmp_path: Path):
    """B1: a row citing TWO files with the failure in the SECOND file FAILs (not a silent PASS)."""
    repo = tmp_path
    _write(repo, "tests/test_first.py", "def test_ok():\n    assert True\n")
    _write(repo, "tests/test_second.py", "def test_boom():\n    assert False\n")
    cat = _catalog(repo, [
        _row("MULTI", "python -m pytest tests/test_first.py::test_ok tests/test_second.py::test_boom -q"),
    ])
    merged = run_catalog(cat, repo_root=repo, merge=True)
    serial = run_catalog(cat, repo_root=repo, merge=False)
    assert merged.rows[0].status == "FAIL", "second-file failure must not be a silent PASS (B1)"
    assert serial.rows[0].status == "FAIL"


def test_ac2_samefile_overlap_whole_and_selector(tmp_path: Path):
    """M-add-1: a whole-file row + a selector row on the SAME file merge; a failure OUTSIDE
    the selector's scope must FAIL the whole-file row and NOT the selector row (and the
    whole-file row must actually run ALL its nodes -- the broad target is never dropped)."""
    repo = tmp_path
    _write(repo, "tests/test_shared.py",
           "def test_selected():\n    assert True\ndef test_other():\n    assert False\n")
    cat = _catalog(repo, [
        _row("WHOLE", "python -m pytest tests/test_shared.py -q"),               # covers test_other (fails)
        _row("SELECTED", "python -m pytest tests/test_shared.py::test_selected -q"),  # only test_selected (passes)
    ])
    merged = run_catalog(cat, repo_root=repo, merge=True)
    serial = run_catalog(cat, repo_root=repo, merge=False)
    m = {r.row: r.status for r in merged.rows}
    s = {r.row: r.status for r in serial.rows}
    assert m == {"WHOLE": "FAIL", "SELECTED": "PASS"}, f"same-file overlap misattributed: {m}"
    assert m == s, "merged must equal serial on same-file overlap"


def test_ac2_interleaved_partition_preserves_order_index_counts(tmp_path: Path):
    """M-add-2: an interleaved [ABSENT, MERGEABLE, STANDALONE, MERGEABLE, fallback-forced]
    catalog reassembles rows[] in CATALOG order with each original index, and counts match serial."""
    repo = tmp_path
    _write(repo, "tests/test_m1.py", "def test_a():\n    assert True\n")
    _write(repo, "tests/test_m2.py", "def test_b():\n    assert True\n")
    # a mergeable row that matches ZERO nodes at merge -> forced per-row fallback (bad selector)
    _write(repo, "tests/test_fb.py", "def test_real():\n    assert True\n")
    cat = _catalog(repo, [
        _row("i0-ABSENT", "python -m pytest tests/test_absent.py -q"),
        _row("i1-MERGE", "python -m pytest tests/test_m1.py::test_a -q"),
        _row("i2-STANDALONE", 'python -c "raise SystemExit(0)"'),
        _row("i3-MERGE", "python -m pytest tests/test_m2.py::test_b -q"),
        _row("i4-ISOLATE", "python -m pytest tests/test_fb.py::test_real -q", isolate=True),
    ])
    merged = run_catalog(cat, repo_root=repo, merge=True)
    serial = run_catalog(cat, repo_root=repo, merge=False)
    # catalog order + original index preserved
    assert [r.row for r in merged.rows] == ["i0-ABSENT", "i1-MERGE", "i2-STANDALONE", "i3-MERGE", "i4-ISOLATE"]
    assert [r.index for r in merged.rows] == [0, 1, 2, 3, 4]
    # counts identical to serial
    assert (merged.passed, merged.failed, merged.absent) == (serial.passed, serial.failed, serial.absent)
    assert _by_index(merged) == _by_index(serial)


# ----------------------------- M1/M2: order-dependence is detectable -----------------------------

def test_m1_m2_order_dependent_divergence_is_detectable(tmp_path: Path):
    """M1/M2 teeth: on an ORDER-DEPENDENT shared-session suite, the merged run diverges from
    the serial run -- the AC1 differential (merge vs --no-merge) CATCHES it, so a real
    order-dependent host is not silently wrong: --no-merge / isolate is the escape."""
    repo = tmp_path
    # a session-scoped fixture with mutable state shared across the ONE merged session,
    # but fresh in each separate serial process:
    _write(repo, "conftest.py",
           "import pytest\n\n@pytest.fixture(scope='session')\ndef state():\n    return {'first': None}\n")
    _write(repo, "tests/test_od1.py",
           "def test_a(state):\n    if state['first'] is None:\n        state['first'] = 'a'\n    assert state['first'] == 'a'\n")
    _write(repo, "tests/test_od2.py",
           "def test_b(state):\n    if state['first'] is None:\n        state['first'] = 'b'\n    assert state['first'] == 'b'\n")
    cat = _catalog(repo, [
        _row("OD1", "python -m pytest tests/test_od1.py::test_a -q"),
        _row("OD2", "python -m pytest tests/test_od2.py::test_b -q"),
    ])
    merged = {r.row: r.status for r in run_catalog(cat, repo_root=repo, merge=True).rows}
    serial = {r.row: r.status for r in run_catalog(cat, repo_root=repo, merge=False).rows}
    # serial: each row runs alone with a fresh session -> both PASS
    assert serial == {"OD1": "PASS", "OD2": "PASS"}
    # merged: shared session -> OD1 sets 'first'='a' first, OD2 then sees 'a' != 'b' -> FAIL
    assert merged == {"OD1": "PASS", "OD2": "FAIL"}
    # THE POINT: the differential surfaces the divergence (it is NOT silently identical).
    assert merged != serial


# ----------------------------- AC3: session timeout -----------------------------

def test_ac3_session_timeout_bounds_a_hung_row(tmp_path: Path):
    """AC3 in the PRODUCTION shape (code-review CR1): validate-slice Step 6 passes ONLY
    --session-timeout (no --timeout), so this test passes ONLY session_timeout. A hung
    row must still be bounded + FAIL(subkind=timeout) via the effective-timeout fix --
    for BOTH a MERGEABLE pytest row (merged session times out -> per-row fallback bounded
    by eff_timeout=session_timeout) AND a STANDALONE non-pytest row (bounded directly).
    Never unbounded (must_not_defer #3)."""
    repo = tmp_path
    _write(repo, "tests/test_slow.py", "import time\ndef test_hang():\n    time.sleep(120)\n")
    cat = _catalog(repo, [
        _row("HANG-MERGE", "python -m pytest tests/test_slow.py::test_hang -q"),
        _row("HANG-STANDALONE", 'python -c "import time; time.sleep(120)"'),
    ])
    t0 = time.time()
    res = run_catalog(cat, repo_root=repo, merge=True, session_timeout=2.0)  # PRODUCTION: no --timeout
    dt = time.time() - t0
    by_id = {r.row: r for r in res.rows}
    assert by_id["HANG-MERGE"].status == "FAIL" and by_id["HANG-MERGE"].subkind == "timeout", by_id["HANG-MERGE"]
    assert by_id["HANG-STANDALONE"].status == "FAIL" and by_id["HANG-STANDALONE"].subkind == "timeout", by_id["HANG-STANDALONE"]
    assert dt < 40, f"hung rows not bounded by --session-timeout alone (took {dt:.1f}s)"


# ----------------------------- AC4: below-normal priority -----------------------------

def test_ac4_priority_kwargs_and_verdict_neutral(tmp_path: Path):
    """AC4: _priority_kwargs yields the right platform primitive, and requesting below-normal
    priority is verdict-neutral (a passing row still PASSes, identical to normal priority)."""
    import catalog_merge as _cm
    from scripts.lib import verification_core as _vc
    kw = _vc._priority_kwargs(True)
    if sys.platform == "win32":
        assert kw.get("creationflags") == subprocess.BELOW_NORMAL_PRIORITY_CLASS
    else:
        assert "preexec_fn" in kw
    assert _vc._priority_kwargs(False) == {}, "default off must be a no-op (byte-identical, AC5)"

    repo = tmp_path
    _write(repo, "tests/test_p.py", "def test_ok():\n    assert True\n")
    normal = _vc.run_verification("python -m pytest tests/test_p.py -q", repo, below_normal_priority=False)
    low = _vc.run_verification("python -m pytest tests/test_p.py -q", repo, below_normal_priority=True)
    assert normal.status == low.status == "PASS"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows priority-class assertion")
def test_ac4_windows_child_runs_below_normal():
    """AC4 (Windows): a child spawned with the runner's priority kwargs actually runs at
    BELOW_NORMAL_PRIORITY_CLASS (asserted via ctypes GetPriorityClass -- the exact class)."""
    import ctypes
    from scripts.lib import verification_core as _vc
    BELOW_NORMAL = 0x00004000
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"],
                         **_vc._priority_kwargs(True))
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0400, False, p.pid)  # PROCESS_QUERY_INFORMATION
        pc = k.GetPriorityClass(h)
        k.CloseHandle(h)
        assert pc == BELOW_NORMAL, f"child priority class {hex(pc)} != BELOW_NORMAL"
    finally:
        p.terminate()
        p.wait()


# ----------------------------- AC5 / M4: integration_health_gate through the merged path -----------------------------

def test_ac5_integration_health_gate_through_merged_path(tmp_path: Path):
    """AC5/M4: the /commit-slice integration-health gate drives the MERGED runner via its
    --json contract. It passes NO timeout (session_timeout stays None -> m5 preserved) yet
    the merged run's RunResult JSON PROCEEDs on green and REFUSEs on a seeded failure."""
    ihg = _load("skills/commit-slice/scripts/integration_health_gate.py", "ai_sdlc_ihg_merged")
    runner_path = REPO_ROOT / "skills" / "validate-slice" / "scripts" / "shippability_runner.py"

    def _real_runner(argv):
        return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    # green catalog (mergeable rows) -> PROCEED
    repo = tmp_path / "green"
    _write(repo, "tests/test_g1.py", "def test_a():\n    assert True\n")
    _write(repo, "tests/test_g2.py", "def test_b():\n    assert True\n")
    cat = _catalog(repo, [
        _row("G1", "python -m pytest tests/test_g1.py::test_a -q"),
        _row("G2", "python -m pytest tests/test_g2.py::test_b -q"),
    ])
    res = ihg.run_gate(runner=_real_runner, catalog=str(cat), repo_root=str(repo),
                       runner_path=runner_path)
    assert res["action"] == ihg.PROCEED, res

    # red catalog (seeded fail) -> REFUSE, naming the row
    repo2 = tmp_path / "red"
    _write(repo2, "tests/test_r1.py", "def test_a():\n    assert True\n")
    _write(repo2, "tests/test_r2.py", "def test_b():\n    assert False\n")
    cat2 = _catalog(repo2, [
        _row("R1", "python -m pytest tests/test_r1.py::test_a -q"),
        _row("R2", "python -m pytest tests/test_r2.py::test_b -q"),
    ])
    res2 = ihg.run_gate(runner=_real_runner, catalog=str(cat2), repo_root=str(repo2),
                        runner_path=runner_path)
    assert res2["action"] == ihg.REFUSE, res2
    assert any(r["row"] == "R2" for r in res2["failing_rows"]), res2
