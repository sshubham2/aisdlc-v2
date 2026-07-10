"""Unit + hermetic coverage for security_gate.py -- the deterministic security reality-gate
guard (slice-067 / SC-097 / ADR-065).

The PURE classify_* cores are exercised with RECORDED tool output (no subprocess, no network):
every bucket {PASS|FINDING|INFRA|INCOMPLETE|ZERO-SCAN|TOOL-MISSING} for both tools, plus the
must-not-defer edges (M2 stdout pollution, M5 tool-missing, M-add-2 timeout, m1 vuln>skip
precedence, m3 nosec-surfaced). The guard CLI is then driven against REAL bandit over multi-file
tmp fixtures (M2 real-corpus; M1 .venv-exclusion) -- bandit needs no network. The real pip-audit
path is network-gated (skipped offline).
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plugin root -> scripts.lib

from scripts.lib import security_gate as sg  # noqa: E402


# ── recorded tool output builders ──
def _bandit_json(loc, results, nosec=0):
    return json.dumps({
        "errors": [],
        "metrics": {"_totals": {"loc": loc, "nosec": nosec, "SEVERITY.HIGH": len(results)}},
        "results": results,
    })


_HIGH_RESULT = {"test_id": "B602", "filename": "app/run.py", "line_number": 42, "issue_severity": "HIGH"}


def _pipaudit_json(deps):
    return json.dumps({"dependencies": deps, "fixes": []})


# ══════════════════════ bandit classify (pure) ══════════════════════
def test_bandit_clean_is_pass():
    bucket, detail = sg.classify_bandit(0, _bandit_json(120, []), "", False)
    assert bucket == sg.PASS and "0 HIGH" in detail and "loc=120" in detail


def test_bandit_high_finding_fails_with_count_and_location():
    out = _bandit_json(200, [_HIGH_RESULT, dict(_HIGH_RESULT, line_number=7)])
    bucket, detail = sg.classify_bandit(1, out, "", False)
    assert bucket == sg.FINDING
    assert detail.startswith("2 HIGH")               # m2: count leads
    assert "app/run.py:42" in detail and "+1 more" in detail


def test_bandit_zero_loc_is_zero_scan_false_green_blocked():
    # must-not-defer (a): raw bandit exits 0 scanning nothing -> the guard FAILS it.
    bucket, detail = sg.classify_bandit(0, _bandit_json(0, []), "", False)
    assert bucket == sg.ZERO_SCAN


def test_bandit_progress_polluted_stdout_still_parses_M2():
    # M2: a 'Working... 100%' progress line precedes the JSON on multi-file scans.
    polluted = "[main]\tWorking... ---- 100%\n" + _bandit_json(50, [_HIGH_RESULT])
    bucket, _ = sg.classify_bandit(1, polluted, "", False)
    assert bucket == sg.FINDING                       # parse-from-first-brace recovered it


def test_bandit_unparseable_output_is_infra_never_clean():
    bucket, _ = sg.classify_bandit(2, "traceback: boom, not json", "some error", False)
    assert bucket == sg.INFRA


def test_bandit_tool_missing_is_visible_M5():
    bucket, detail = sg.classify_bandit(1, "", "C:/py.exe: No module named bandit", False)
    assert bucket == sg.TOOL_MISSING and "install" in detail.lower()


def test_bandit_timeout_is_infra_fast_M_add_2():
    bucket, _ = sg.classify_bandit(124, "", "", True)
    assert bucket == sg.INFRA


def test_bandit_nosec_count_surfaced_m3():
    bucket, detail = sg.classify_bandit(0, _bandit_json(30, [], nosec=4), "", False)
    assert bucket == sg.PASS and "nosec=4" in detail


# ══════════════════════ pip-audit classify (pure) ══════════════════════
def test_pipaudit_clean_is_pass():
    bucket, detail = sg.classify_pip_audit(0, _pipaudit_json([
        {"name": "certifi", "version": "2026.6.17", "vulns": []}]), "", False)
    assert bucket == sg.PASS and "1 dep(s) audited" in detail


def test_pipaudit_vuln_fails_with_cve():
    deps = [{"name": "jinja2", "version": "2.11.2", "vulns": [
        {"id": "PYSEC-2021-66", "aliases": ["CVE-2020-28493", "GHSA-g3rq"], "fix_versions": ["2.11.3"]}]}]
    bucket, detail = sg.classify_pip_audit(1, _pipaudit_json(deps), "", False)
    assert bucket == sg.FINDING
    assert detail.startswith("1 vuln")                # count leads
    assert "jinja2==2.11.2" in detail and "CVE-2020-28493" in detail


def test_pipaudit_zero_dep_is_zero_scan_false_green_blocked():
    # must-not-defer (a): pip-audit exits 0 on zero deps -> the guard FAILS it.
    bucket, _ = sg.classify_pip_audit(0, _pipaudit_json([]), "", False)
    assert bucket == sg.ZERO_SCAN


def test_pipaudit_skip_without_vuln_is_incomplete_m1():
    deps = [{"name": "localpkg", "version": "1.0", "vulns": [],
             "skip_reason": "Dependency not found on PyPI and could not be audited"}]
    bucket, detail = sg.classify_pip_audit(0, _pipaudit_json(deps), "", False)
    assert bucket == sg.INCOMPLETE and "localpkg" in detail


def test_pipaudit_vuln_dominates_skip_precedence_m1():
    # m1: a present vuln DOMINATES a co-occurring skip_reason -> FINDING, not INCOMPLETE.
    deps = [{"name": "vulnpkg", "version": "1.0", "vulns": [
                {"id": "PYSEC-X", "aliases": ["CVE-2020-1"]}]},
            {"name": "localpkg", "version": "1.0", "vulns": [], "skip_reason": "local path"}]
    bucket, _ = sg.classify_pip_audit(1, _pipaudit_json(deps), "", False)
    assert bucket == sg.FINDING


def test_pipaudit_offline_nonjson_is_infra_M3():
    bucket, _ = sg.classify_pip_audit(1, "", "Error: could not reach https://pypi.org", False)
    assert bucket == sg.INFRA


def test_pipaudit_tool_missing_is_visible_M5():
    bucket, _ = sg.classify_pip_audit(1, "", "No module named pip_audit", False)
    assert bucket == sg.TOOL_MISSING


def test_pipaudit_timeout_is_infra_fast_M_add_2():
    bucket, _ = sg.classify_pip_audit(124, "", "", True)
    assert bucket == sg.INFRA


# ══════════════════════ guard CLI over REAL bandit (no network) ══════════════════════
def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


_CLEAN_MOD = "def f(x):\n    return x + 1\n"
# hashlib.md5 without usedforsecurity=False -> bandit B324 HIGH (the same class we fixed in-repo)
_HIGH_MOD = "import hashlib\ndef digest(x):\n    return hashlib.md5(x).hexdigest()\n"


def test_cli_bandit_multifile_clean_passes(tmp_path, capsys):
    for n in ("a.py", "b.py", "c.py"):
        _write(tmp_path / n, _CLEAN_MOD)
    rc = sg.main(["--tool", "bandit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 0 and "PASS" in banner


def test_cli_bandit_multifile_high_fails_closed(tmp_path, capsys):
    _write(tmp_path / "a.py", _CLEAN_MOD)
    _write(tmp_path / "b.py", _CLEAN_MOD)
    _write(tmp_path / "evil.py", _HIGH_MOD)
    rc = sg.main(["--tool", "bandit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 1 and "FINDING" in banner and "HIGH" in banner


def test_cli_bandit_empty_dir_is_zero_scan_fail(tmp_path, capsys):
    rc = sg.main(["--tool", "bandit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 1 and "ZERO-SCAN" in banner


def test_cli_bandit_ignores_in_tree_venv_M1(tmp_path, capsys):
    # M1: an in-tree .venv-shaped dir with a HIGH module must NOT be scanned (else a false HIGH
    # from third-party code blocks every slice). Only the clean top-level module is real source.
    _write(tmp_path / "main.py", _CLEAN_MOD)
    _write(tmp_path / ".venv" / "site-packages" / "thirdparty.py", _HIGH_MOD)
    _write(tmp_path / "node_modules" / "pkg" / "shim.py", _HIGH_MOD)
    rc = sg.main(["--tool", "bandit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 0 and "PASS" in banner


def test_cli_bandit_nosec_cannot_launder_high_m3(tmp_path, capsys):
    # m3: an inline `# nosec` must NOT hide a HIGH -- the guard passes --ignore-nosec.
    _write(tmp_path / "a.py", _CLEAN_MOD)
    _write(tmp_path / "laundered.py",
           "import hashlib\ndef d(x):\n    return hashlib.md5(x).hexdigest()  # nosec\n")
    rc = sg.main(["--tool", "bandit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 1 and "FINDING" in banner


def test_cli_banner_is_last_stdout_line_and_ascii(tmp_path, capsys):
    _write(tmp_path / "a.py", _CLEAN_MOD)
    sg.main(["--tool", "bandit", str(tmp_path)])
    out = capsys.readouterr().out
    last = out.strip().splitlines()[-1]
    assert last.startswith("[SECURITY-GATE bandit]")
    assert len(last) <= sg._BANNER_MAX
    last.encode("ascii")  # raises if non-ASCII slipped in


def test_cli_pip_audit_no_requirements_is_incomplete(tmp_path, capsys):
    # deps gate declared but no requirements file at runtime -> fail-VISIBLE INCOMPLETE, never silent pass.
    rc = sg.main(["--tool", "pip-audit", str(tmp_path)])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 1 and "INCOMPLETE" in banner


# ══════════════════════ real pip-audit (network-gated reality contact) ══════════════════════
def _online() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=4).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _online(), reason="pip-audit needs network (PyPA #698); offline -> skip")
def test_cli_pip_audit_known_vuln_fails_closed_real(tmp_path, capsys):
    _write(tmp_path / "requirements.txt", "jinja2==2.11.2\n")
    rc = sg.main(["--tool", "pip-audit", str(tmp_path), "--timeout", "60"])
    banner = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 1 and ("FINDING" in banner or "INFRA" in banner)
    if "FINDING" in banner:
        assert "jinja2" in banner
