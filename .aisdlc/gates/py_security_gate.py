"""security_gate.py -- deterministic security reality-gate guard (slice-067 / SC-097 / ADR-065).

STANDALONE + stdlib-only BY CONTRACT (M4). This file is VENDORED verbatim to a consumer
repo's <repo>/.aisdlc/gates/py_security_gate.py and MUST run there with NO plugin on
sys.path -- so it imports NO plugin modules (nothing from the shared scripts/lib package),
inlines its own utf-8 stdout handling, and prints an ASCII-only banner. (This repo's own
scripts/lib/ would accidentally resolve a plugin-relative import, masking the consumer-repo
failure -- see the standalone-import test.) The plugin's scaffold_reality_gates.py re-vendors
this file when GUARD_VERSION / its content differs (the one exception to the manifest's
preserve-existing idempotency).

WHY A GUARD, NOT A RAW COMMAND (the reality that forced it -- ADR-065): a raw
`python -m bandit` exits 0 while scanning NOTHING (empty dir, clean tree, even a nonexistent
path -- only bandit metrics._totals.loc==0 distinguishes it), and raw `pip-audit` exits 0 on
zero dependencies. Both are FALSE GREENS -- a gate that runs nothing must FAIL as hard as one
that runs and fails (must-not-defer a). So the exit code alone cannot carry the verdict.

OUT-OF-BAND VALIDITY (NAMUR NE43 cross-domain transfer): the tool's STRUCTURED output is the
out-of-band channel; this guard separates 'measurement invalid' (could-not-complete) from
'defect present' (a real HIGH finding / CVE) from 'completed clean', FAILS CLOSED (exit 1) on
everything that is not a real clean analysis, and annunciates the cause DISTINCTLY in a
{PASS|FINDING|INFRA|INCOMPLETE|ZERO-SCAN|TOOL-MISSING} banner emitted as the LAST stdout line
(so the label survives run_verification's 500-char reason tail; the count leads the detail so
a truncated multi-finding still conveys magnitude -- m2).

Fail-closed contract (the engine reads ONLY the exit code; the bucket rides the banner):
  exit 0  = PASS  -- a real clean analysis of >0 targets.
  exit 1  = every other bucket:
    FINDING      -- a HIGH bandit result / a pip-audit vuln (advisory id + CVE in evidence).
    ZERO-SCAN    -- scanned nothing (bandit loc==0 / pip-audit 0 deps) -- the false-green.
    INCOMPLETE   -- pip-audit skipped an unauditable dep (VCS/URL/local/hash) with no vuln (m1).
    INFRA        -- unparseable output, a network/DB failure, or a bounded-timeout (M-add-2).
    TOOL-MISSING -- bandit/pip-audit not installed ('No module named ...'), fail-VISIBLE (M5).

Anti-laundering (Wheeler-strict): bandit runs --severity-level high, --ignore-nosec (an inline
`# nosec` cannot hide a HIGH -- m3), NO -b baseline, NO -c config; unparseable output defaults
to FAIL, never clean. Scan scope excludes vendored/third-party/build trees so an in-tree venv
cannot inject a false HIGH (M1).

Exit codes: 0 = clean PASS · 1 = any FAIL bucket (fail-closed) · 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Bump on ANY behavioral change: the scaffolder re-vendors the repo copy when this version
# (or the file content) differs from the plugin source of truth (M4 security-fix propagation).
GUARD_VERSION = "1.0.0"

# ── bucket labels (the distinct out-of-band annunciation channel) ──
PASS = "PASS"
FINDING = "FINDING"
INFRA = "INFRA"
INCOMPLETE = "INCOMPLETE"
ZERO_SCAN = "ZERO-SCAN"
TOOL_MISSING = "TOOL-MISSING"

# M1: never scan vendored/third-party/build output or the committed gate dir itself -- else an
# in-tree .venv injects a false HIGH and a whole-repo scan slows to a crawl. bandit --exclude
# REPLACES its defaults, so the common VCS/cache dirs are re-listed. A bare dir name is matched
# INCONSISTENTLY by bandit (it excluded `.venv` but NOT `node_modules` in testing), so each dir
# is emitted BOTH as a `*/<d>/*` glob (matches at any depth, incl. directly under the scan root)
# AND as a bare name -- belt and braces.
_EXCLUDE_DIRS = (
    ".venv", "venv", "env", ".env", "node_modules", "build", "dist",
    ".git", ".hg", ".svn", ".tox", ".eggs", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".aisdlc", "tests/bugs",
)
_BANDIT_EXCLUDES = ",".join([f"*/{d}/*" for d in _EXCLUDE_DIRS] + list(_EXCLUDE_DIRS))

_DEFAULT_TIMEOUT_BANDIT = 300.0      # no network; bounds a pathological huge tree
_DEFAULT_TIMEOUT_PIP_AUDIT = 90.0    # network-bounded: fail FAST offline, never hang (M-add-2)

_BANNER_MAX = 200                    # <= run_verification's 500-char tail, by construction (m2)


# ── inlined utf-8 stdout (M4: no plugin _stdout import in a vendored standalone file) ──
def _reconfigure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _parse_json(raw: str):
    """Lenient parse: try the whole string, then from the first '{' (M2: bandit prints a
    'Working... 100%' progress line to STDOUT before the JSON on multi-file scans; -q usually
    suppresses it but parse-from-first-brace is the belt-and-braces). Returns None on failure."""
    raw = raw or ""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    i = raw.find("{")
    if i > 0:
        try:
            return json.loads(raw[i:])
        except (ValueError, TypeError):
            pass
    return None


def _is_tool_missing(stderr: str, module: str) -> bool:
    s = stderr or ""
    return "No module named" in s and module.split(".")[0] in s


def _tail(text: str, n: int = 160) -> str:
    """ASCII-safe, single-line, bounded tail of a stream for evidence."""
    s = (text or "").replace("\r", " ").replace("\n", " ").strip()
    s = s.encode("ascii", "replace").decode("ascii")
    return s[-n:]


# ── PURE classifiers (take recorded subprocess outputs -> (bucket, detail); unit-testable) ──
def classify_bandit(returncode: int, stdout: str, stderr: str, timed_out: bool) -> tuple[str, str]:
    if timed_out:
        return INFRA, f"bandit timed out (fail-fast); {_tail(stderr)}"
    if _is_tool_missing(stderr, "bandit"):
        return TOOL_MISSING, "bandit not installed: install with `pip install bandit`"
    data = _parse_json(stdout)
    if data is None:
        return INFRA, f"bandit output unparseable (exit {returncode}); {_tail(stderr)}"
    totals = ((data.get("metrics") or {}).get("_totals") or {})
    try:
        loc = int(totals.get("loc", 0) or 0)
    except (TypeError, ValueError):
        loc = 0
    nosec = totals.get("nosec", 0)
    results = data.get("results") or []
    if loc == 0:
        return ZERO_SCAN, "scanned 0 loc (no Python source at target) -- false-green blocked"
    if results:
        first = results[0]
        loc0 = f"{first.get('filename', '?')}:{first.get('line_number', '?')}"
        more = f" (+{len(results) - 1} more)" if len(results) > 1 else ""
        return FINDING, f"{len(results)} HIGH: first={loc0}{more} [loc={loc} nosec={nosec}]"
    return PASS, f"0 HIGH [loc={loc} nosec={nosec}]"


def classify_pip_audit(returncode: int, stdout: str, stderr: str, timed_out: bool) -> tuple[str, str]:
    if timed_out:
        return INFRA, f"pip-audit timed out (fail-fast; offline/flaky network?); {_tail(stderr)}"
    if _is_tool_missing(stderr, "pip_audit"):
        return TOOL_MISSING, "pip-audit not installed: install with `pip install pip-audit`"
    data = _parse_json(stdout)
    if data is None:
        # No JSON on stdout -> the audit could not complete (typically network/DB unreachable).
        return INFRA, f"pip-audit could not complete (exit {returncode}; network/DB?); {_tail(stderr)}"
    deps = data.get("dependencies") or []
    vuln_deps = [d for d in deps if isinstance(d, dict) and d.get("vulns")]
    skipped = [d for d in deps if isinstance(d, dict) and d.get("skip_reason") and not d.get("vulns")]
    # Precedence (m1): a present vuln DOMINATES -> FINDING; else an unauditable skip -> INCOMPLETE;
    # else zero deps -> ZERO-SCAN (false-green); else clean PASS.
    if vuln_deps:
        total = sum(len(d.get("vulns") or []) for d in vuln_deps)
        d0 = vuln_deps[0]
        v0 = (d0.get("vulns") or [{}])[0]
        aliases = v0.get("aliases") or []
        cve = next((a for a in aliases if str(a).startswith("CVE-")), (aliases[0] if aliases else ""))
        ident = "/".join(x for x in (str(v0.get("id", "")), str(cve)) if x)
        name0 = f"{d0.get('name', '?')}=={d0.get('version', '?')}"
        more = f" (+{total - 1} more)" if total > 1 else ""
        return FINDING, f"{total} vuln in {len(vuln_deps)} dep(s): first={name0} {ident}{more}"
    if skipped:
        s0 = skipped[0]
        return INCOMPLETE, (f"{len(skipped)} dep(s) unauditable (VCS/URL/local/hash-pinned): "
                            f"first={s0.get('name', '?')} ({_tail(str(s0.get('skip_reason', '')), 60)})")
    if not deps:
        return ZERO_SCAN, "0 dependencies audited (no auditable deps at target) -- false-green blocked"
    return PASS, f"{len(deps)} dep(s) audited, 0 vuln"


# ── subprocess shells ──
def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str, str, bool]:
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, out, err, True
    except (OSError, ValueError) as exc:
        return 1, "", f"could not launch tool: {exc}", False


def run_bandit(target: Path, timeout: float) -> tuple[str, str]:
    if not target.is_dir():
        return INFRA, f"bandit scan target is not a directory: {target}"
    # Scan '.' with cwd=target (NOT an absolute `-r <path>`): bandit --exclude matches the walked
    # RELATIVE paths, so a relative exclude list reliably prunes an in-tree .venv / node_modules
    # regardless of where the target sits (M1 -- an absolute `-r` + relative `-x` does NOT exclude).
    argv = [sys.executable, "-m", "bandit", "-q", "--ignore-nosec",
            "--severity-level", "high", "-f", "json", "-r", ".", "-x", _BANDIT_EXCLUDES]
    rc, out, err, to = _run(argv, target.resolve(), timeout)
    return classify_bandit(rc, out, err, to)


def _detect_requirements(target: Path) -> Path | None:
    primary = target / "requirements.txt"
    if primary.is_file():
        return primary
    for cand in sorted(target.glob("requirements*.txt")):
        if cand.is_file():
            return cand
    reqdir = target / "requirements"
    if reqdir.is_dir():
        for cand in sorted(reqdir.glob("*.txt")):
            if cand.is_file():
                return cand
    return None


def run_pip_audit(target: Path, timeout: float, requirements: Path | None = None) -> tuple[str, str]:
    req = requirements or _detect_requirements(target)
    if req is None or not req.is_file():
        # deps surface was declared but no requirements file to audit at runtime -- fail-VISIBLE,
        # never a silent pass (pyproject/lockfile-only auditing is a documented follow-up).
        return INCOMPLETE, f"no requirements*.txt to audit under {target} -- declared deps unauditable"
    argv = [sys.executable, "-m", "pip_audit", "-f", "json", "--skip-editable",
            "--progress-spinner", "off", "-r", str(req)]
    rc, out, err, to = _run(argv, target.resolve() if target.is_dir() else Path.cwd(), timeout)
    return classify_pip_audit(rc, out, err, to)


# ── CLI ──
_TOOLS = {"bandit", "pip-audit", "pip_audit"}


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdout()
    p = argparse.ArgumentParser(
        prog="py_security_gate",
        description="Deterministic security reality-gate guard (bandit / pip-audit); fail-closed. "
                    "exit 0 = clean PASS, 1 = any FAIL bucket, 2 = usage.")
    p.add_argument("--tool", required=True, choices=sorted(_TOOLS),
                   help="which deterministic security tool to run as the gate")
    p.add_argument("target", nargs="?", default=".",
                   help="the directory to scan/audit (default: cwd -- the target repo root)")
    p.add_argument("--requirements", default=None,
                   help="pip-audit only: explicit requirements file (default: auto-detect in target)")
    p.add_argument("--timeout", type=float, default=None,
                   help="bounded subprocess timeout in seconds (default: tool-specific; a timeout "
                        "-> INFRA fail-fast, never a hang)")
    p.add_argument("--print-version", action="store_true",
                   help="print GUARD_VERSION and exit 0 (used by the re-vendor integrity check)")
    args = p.parse_args(argv)

    if args.print_version:
        sys.stdout.write(GUARD_VERSION + "\n")
        return 0

    target = Path(args.target)
    tool = "pip-audit" if args.tool in ("pip-audit", "pip_audit") else "bandit"

    if tool == "bandit":
        timeout = args.timeout if args.timeout is not None else _DEFAULT_TIMEOUT_BANDIT
        bucket, detail = run_bandit(target, timeout)
    else:
        timeout = args.timeout if args.timeout is not None else _DEFAULT_TIMEOUT_PIP_AUDIT
        req = Path(args.requirements) if args.requirements else None
        bucket, detail = run_pip_audit(target, timeout, req)

    banner = f"[SECURITY-GATE {tool}] {bucket}: {detail}"[:_BANNER_MAX]
    # The banner is the LAST stdout line (must-not-defer c) -- survives the 500-char reason tail.
    sys.stdout.write(banner + "\n")
    return 0 if bucket == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
