"""catalog_merge -- batch-and-scatter core for the Step-6 shippability catalog
(slice-064 / SC-118 / ADR-061).

The shippability catalog runs every row as its own cold-start subprocess. On a
real project with session-scoped fixtures (app + DB) that boots the whole stack
once PER ROW, so Step 6 grows O(rows) and takes ~an hour. This module lets
`shippability_runner.run_catalog` run the MERGEABLE plain-pytest rows in ONE
pytest session (fixtures boot once) while preserving the EXACT per-row
PASS/FAIL/ABSENT verdicts of the serial runner.

Design (ADR-061), correctness-first:
  * classify(): a row is MERGEABLE only if it is a single ;-segment
    `<interp> -m pytest <present test targets> [verdict-neutral flags]`. ANY
    behavior-changing / non-neutral flag (-x / -k / -m / --maxfail / -s / -n / -c / -o /
    --pdb / --forked, OR ANY explicit -p -- including `-p no:cacheprovider`, which the
    merged argv adds itself, so the row need not carry it), a multi-segment command, a
    non-pytest command, a not-all-present target set, a class-path `::Class::method`
    selector (CR3), or a per-row `isolate: true` => STANDALONE
    (run by the UNCHANGED verification_core.run_verification, exactly as today).
  * build_merged_argv(): ONE `pytest <union of targets> -q --import-mode=importlib
    -p no:cacheprovider --junitxml=<unique tmp> --rootdir=<repo_root>`. The union
    keeps BOTH a whole-file target AND a same-file ::selector target when both are
    cited (M-add-1: NEVER drop the broad whole-file target -- pytest de-dups nodes,
    so all the file's nodes still run exactly once).
  * attribute(): map JUnit results back to each row by (file, selector-set) using
    classname+name -- NOT the JUnit `file` attribute, which is None under
    --import-mode=importlib (proven by the design spike; keying on it silently
    turned a serial FAIL into a merged PASS). A whole-file row (no selector) FAILs
    if ANY node in its file failed; a ::selector row FAILs if its own node failed.
  * FALLBACK LADDER: the merged fast-path is an optimization over the trusted
    serial run. A merged session that times out, exits outside {0,1}, or leaves a
    missing/unparseable JUnit => whole-batch serial fallback. A row that matched
    ZERO nodes => that row re-runs standalone. So the merged path never DECIDES a
    verdict it cannot confidently attribute (must_not_defer #1: no silent PASS).

verification_core is imported (skill-script -> scripts.lib is allowed); it is
UNCHANGED except for the additive below_normal_priority kwarg this module reuses.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]  # skills/validate-slice/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib.verification_core import (  # noqa: E402
    _extract_test_tokens,
    _normalize_interp,
    _priority_kwargs,
    _segments,
)

# Verdict-neutral pytest flags a MERGEABLE row may carry besides its test targets.
# Anything not here (or not a test target) forces the row STANDALONE -- conservative
# by design: an exotic row costs a little speed, never a wrong verdict.
_NEUTRAL_EXACT = frozenset({"-q", "-qq", "-v", "-vv", "-vvv", "--no-header", "--no-summary"})


def _is_neutral_flag(tok: str) -> bool:
    return tok in _NEUTRAL_EXACT or tok.startswith("--tb=")


@dataclass
class RowSpec:
    """A MERGEABLE row: its id/index + the (file, selector|None) targets it cites."""
    row_id: str
    index: int
    command: str
    pairs: list = field(default_factory=list)  # [(file_token, "::selector"|None)]


@dataclass
class MergedOutcome:
    """Result of one merged-session attempt.

    * ``per_row``  -- {row_index: (status, detail)} for rows confidently attributed.
    * ``unresolved`` -- row_indexes that matched ZERO nodes -> caller re-runs each standalone.
    * ``whole_batch_fallback`` -- True when the session itself was anomalous
      (timeout / exit not-in-{0,1} / missing|unparseable JUnit) -> caller re-runs
      EVERY mergeable row standalone.
    * ``reason`` -- human diagnostic (names why a fallback fired; m3)."""
    per_row: dict = field(default_factory=dict)
    unresolved: set = field(default_factory=set)
    whole_batch_fallback: bool = False
    reason: str = ""


def classify(command: str, repo_root: Path, *, isolate: bool = False) -> tuple[str, list]:
    """Return ``(kind, pairs)`` -- kind in {"mergeable", "standalone"}.

    MERGEABLE requires: not isolate; exactly one ;-segment; the segment normalizes
    to ``[<interp>, -m, pytest, ...]``; >=1 cited tests/*.py token; ALL cited files
    present on ``repo_root``; every non-target token a verdict-neutral flag. Any
    other shape (incl. an all-absent or mixed present/absent target set) is
    STANDALONE, where the unchanged run_verification applies the exact ABSENT rule."""
    if isolate:
        return ("standalone", [])
    segs = _segments(command)
    if len(segs) != 1:
        return ("standalone", [])
    try:
        argv = _normalize_interp(shlex.split(segs[0], posix=True))
    except ValueError:
        return ("standalone", [])
    if len(argv) < 3 or argv[0] != sys.executable or argv[1] != "-m" or argv[2] != "pytest":
        return ("standalone", [])
    pairs = _extract_test_tokens(command)  # [(file, "::sel"|None)]
    if not pairs:
        return ("standalone", [])
    # CR3: a class-path selector (`::Class::method`, i.e. a nested `::`) is keyed by leaf
    # name in attribution, so it cannot be matched from the merged JUnit -> it would run
    # ONCE merged AND AGAIN in the per-row fallback (double execution; verdict still
    # correct). Route it STANDALONE up front so it runs exactly once.
    if any(sel and "::" in sel[2:] for _f, sel in pairs):
        return ("standalone", [])
    files = {f for f, _ in pairs}
    if not all((repo_root / f).exists() for f in files):
        return ("standalone", [])  # absent / mixed -> unchanged serial ABSENT handling
    target_strs = set()
    for f, sel in pairs:
        target_strs.add(f)
        target_strs.add(f + (sel or ""))
    for tok in argv[3:]:
        if tok in target_strs or _is_neutral_flag(tok):
            continue
        return ("standalone", [])  # a behavior-changing / unknown flag -> standalone
    return ("mergeable", pairs)


def build_merged_argv(rows: list, junit_path: Path, repo_root: Path) -> list:
    """ONE pytest argv covering the UNION of every row's targets. De-dups only EXACT
    duplicate target strings -- a whole-file target and a same-file ::selector
    target are BOTH kept (M-add-1: never drop the broad target; pytest de-dups the
    overlapping nodes so each test still runs exactly once)."""
    targets: list = []
    seen: set = set()
    for r in rows:
        for f, sel in r.pairs:
            t = f + sel if sel else f
            if t not in seen:
                seen.add(t)
                targets.append(t)
    return [
        sys.executable, "-m", "pytest", *targets, "-q",
        "--import-mode=importlib", "-p", "no:cacheprovider",
        f"--junitxml={junit_path}", f"--rootdir={repo_root}",
    ]


def _prefix_of(file_token: str) -> str:
    """`tests/api/test_x.py` -> `tests.api.test_x` (the JUnit classname prefix under
    --import-mode=importlib + --rootdir=repo_root)."""
    base = file_token[:-3] if file_token.endswith(".py") else file_token
    return base.replace("\\", "/").replace("/", ".")


def _parse_nodes(junit_path: Path):
    """Return [(classname, name, failed_bool)] or None (missing) / "UNPARSEABLE"."""
    if not junit_path.exists():
        return None
    try:
        tree = ET.parse(junit_path)
    except ET.ParseError:
        return "UNPARSEABLE"
    out = []
    for tc in tree.iter("testcase"):
        failed = any(ch.tag in ("failure", "error") for ch in tc)
        out.append((tc.get("classname") or "", tc.get("name") or "", failed))
    return out


def _attribute_row(pairs: list, nodes: list) -> tuple[int, bool]:
    """Return (matched_node_count, row_failed) for ONE row, keying JUnit nodes by
    classname+name CONSTRAINED to the row's own file(s). Whole-file pair (sel None)
    matches every node in the file; ::selector pair matches its own node (incl.
    parametrized `name[...]`)."""
    matched = 0
    row_failed = False
    for f, sel in pairs:
        pre = _prefix_of(f)
        for cls, name, failed in nodes:
            if not (cls == pre or cls.startswith(pre + ".")):
                continue  # a different file's node
            if sel:
                s = sel[2:]
                if name == s or name.startswith(s + "["):
                    matched += 1
                    row_failed = row_failed or failed
            else:
                matched += 1
                row_failed = row_failed or failed
    return matched, row_failed


def run_merged_batch(rows: list, repo_root: Path, *,
                     below_normal: bool = True,
                     session_timeout: float | None = None) -> MergedOutcome:
    """Run every mergeable row in ONE pytest session and attribute results back.

    Fail-safe: any session-level anomaly -> whole_batch_fallback; any row matching
    zero nodes -> unresolved (caller re-runs it standalone). Never raises."""
    if not rows:
        return MergedOutcome()
    tmpd = Path(tempfile.mkdtemp(prefix="aisdlc-merge-"))  # per-invocation unique (m2)
    junit = tmpd / "merged.xml"
    try:
        argv = build_merged_argv(rows, junit, repo_root)
        try:
            proc = subprocess.run(
                argv, cwd=str(repo_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=session_timeout, **_priority_kwargs(below_normal),
            )
        except subprocess.TimeoutExpired:
            return MergedOutcome(whole_batch_fallback=True,
                                 reason=f"merged session timed out after {session_timeout}s "
                                        f"-> whole-batch serial fallback")
        except OSError as exc:
            return MergedOutcome(whole_batch_fallback=True,
                                 reason=f"merged session could not launch ({exc}) "
                                        f"-> whole-batch serial fallback")
        rc = proc.returncode
        if rc not in (0, 1):
            tail = ((proc.stdout or "")[-300:] + (proc.stderr or "")[-300:]).strip()
            return MergedOutcome(whole_batch_fallback=True,
                                 reason=f"merged session exited {rc} (not in {{0,1}}) "
                                        f"-> whole-batch serial fallback: {tail}")
        nodes = _parse_nodes(junit)
        if nodes is None:
            return MergedOutcome(whole_batch_fallback=True,
                                 reason="merged session left no junitxml "
                                        "-> whole-batch serial fallback")
        if nodes == "UNPARSEABLE":
            return MergedOutcome(whole_batch_fallback=True,
                                 reason="merged junitxml was unparseable "
                                        "-> whole-batch serial fallback")
        out = MergedOutcome(reason="merged session ok")
        for r in rows:
            matched, row_failed = _attribute_row(r.pairs, nodes)
            if matched == 0:
                out.unresolved.add(r.index)  # can't attribute -> per-row serial fallback
                continue
            out.per_row[r.index] = (
                "FAIL" if row_failed else "PASS",
                "merged-session FAIL (see merged pytest run)" if row_failed else "",
            )
        return out
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
