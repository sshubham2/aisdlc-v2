"""Shared pytest fixtures for the ai-sdlc plugin's own test suite (remediation 4.4).

The plugin ships ~70 Python scripts (gate audits, the SVW-1 write path, finding
dedup, assemble) with — until now — zero tests and zero CI. This suite exercises
the load-bearing libraries directly and the CLI tools via subprocess (the faithful
invocation path: every shared script self-bootstraps sys.path off ``__file__`` and
the vault module docstring explicitly notes cross-process env override is the
test seam).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(scope="session")
def plugin_root() -> Path:
    return PLUGIN_ROOT


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A fresh, empty vault root under tmp_path."""
    v = tmp_path / "vault"
    v.mkdir()
    return v


def _run_script(relpath, args, *, cwd=None, env=None, stdin=None, timeout=120):
    """Run a bundled plugin script via subprocess and return the CompletedProcess.

    ``AI_SDLC_VAULT_ROOT`` is stripped from the child env so a test can never
    resolve (and write to) the developer's real vault; pass ``--vault`` explicitly.
    """
    script = PLUGIN_ROOT / relpath
    assert script.is_file(), f"script not found: {script}"
    child_env = dict(os.environ)
    child_env.pop("AI_SDLC_VAULT_ROOT", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, str(script), *[str(a) for a in args]],
        cwd=str(cwd) if cwd else None,
        env=child_env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@pytest.fixture
def run_script():
    return _run_script
