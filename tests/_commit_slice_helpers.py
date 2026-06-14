"""Shared test helpers for the slice-008 commit-slice --push / --sync-after-pr suite.

The new commit-slice scripts (``pr_flow.py``, ``resolve_sync_target.py``,
``forbidden_flag_audit.py``) live under ``skills/commit-slice/scripts/`` — a
hyphenated directory that is NOT an importable Python package. The bundled scripts
self-bootstrap ``sys.path`` off ``__file__`` (so ``from scripts.lib import …``
resolves), so we load them BY FILE PATH via importlib and call their pure
in-process API directly (the faithful unit seam: the design isolates a pure
``decide()`` core + ONE injected ``runner(argv) -> CompletedProcess``).

``FakeRunner`` is that injected seam: it maps a recorded command (argv) to a
canned ``subprocess.CompletedProcess`` (or raises), records every call, and lets a
test assert the EXACT gh/git argv the ladder issued — including the must-not-defer
"no forbidden flag ever reaches the runtime command set" invariant (AC1 / M1
belt-and-suspenders).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRIPTS = ROOT / "skills" / "commit-slice" / "scripts"


def load_script(stem: str):
    """Load a single-skill commit-slice script BY PATH and return the module.

    Faithful to the runtime: the module's own sys.path bootstrap fires on import,
    so its ``from scripts.lib import …`` calls resolve exactly as under the CLI.
    """
    path = _SCRIPTS / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"commit_slice_{stem}", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def cp(args, returncode=0, stdout="", stderr="") -> subprocess.CompletedProcess:
    """Build a canned CompletedProcess as a stub runner would return it."""
    return subprocess.CompletedProcess(args=list(args), returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class FakeRunner:
    """An injectable ``runner(argv) -> CompletedProcess`` test double.

    Construct with a ``handler(argv) -> CompletedProcess | Exception`` callback.
    Every call is recorded in ``.calls`` (a list of argv lists). If the handler
    returns (or is) an ``Exception`` instance, the runner RAISES it (models a
    mid-ladder crash, e.g. M-add-1's post-push raise). If the handler returns
    ``None`` the runner returns a generic success CompletedProcess.
    """

    def __init__(self, handler: Callable[[list[str]], object]):
        self._handler = handler
        self.calls: list[list[str]] = []

    def __call__(self, argv) -> subprocess.CompletedProcess:
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        result = self._handler(argv)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            return cp(argv, 0)
        return result

    # -- convenience assertions ------------------------------------------------
    def argv_contains(self, *tokens: str) -> bool:
        """True iff some recorded call contains ALL of ``tokens`` in order-free."""
        for call in self.calls:
            if all(t in call for t in tokens):
                return True
        return False

    def flat(self) -> list[str]:
        """Every token across every recorded call (for forbidden-flag scans)."""
        return [tok for call in self.calls for tok in call]
