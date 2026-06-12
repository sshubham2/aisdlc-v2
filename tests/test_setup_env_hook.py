"""hooks/setup_env.py — the SessionStart env resolver's pure managed-block logic (4.6.2/4.6.3).

The hook can't be fired outside a live Claude Code SessionStart, but its load-bearing NEW
logic — the idempotent managed-block rewrite (no more duplicate appends) and the
short-circuit PY probe — is pure and is locked in here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "ai_sdlc_setup_env_hook", PLUGIN_ROOT / "hooks" / "setup_env.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_sdlc_setup_env_hook"] = mod
    spec.loader.exec_module(mod)
    return mod


hook = _load_hook()


def _body():
    return ["export PY=/usr/bin/python3\n", "export CRG=/usr/bin/code-review-graph\n"]


# ── _write_env_block (4.6.2 idempotent managed block) ─────────────────────────────

def test_write_block_idempotent(tmp_path):
    env = tmp_path / "env"
    for _ in range(3):
        hook._write_env_block(str(env), _body())
    text = env.read_text()
    assert text.count(hook._MANAGED_START) == 1
    assert text.count(hook._MANAGED_END) == 1
    assert text.count("export PY=") == 1


def test_write_block_preserves_foreign_lines(tmp_path):
    env = tmp_path / "env"
    env.write_text("export OTHER_TOOL=1\n")
    hook._write_env_block(str(env), _body())
    hook._write_env_block(str(env), _body())
    text = env.read_text()
    assert text.count("export OTHER_TOOL=1") == 1
    assert text.count("export PY=") == 1


def test_write_block_strips_legacy_unmarkered(tmp_path):
    # migration: a pre-upgrade env file had bare appended exports (no markers)
    env = tmp_path / "env"
    env.write_text("export PY=/old/python\nexport CRG=/old/crg\nexport AI_SDLC_VAULT_ROOT=/old/v\n")
    hook._write_env_block(str(env), ["export PY=/new/python\n"])
    text = env.read_text()
    assert "/old/python" not in text
    assert text.count("export PY=") == 1
    assert "/new/python" in text


# ── _existing_managed_py (4.6.3 short-circuit probe) ──────────────────────────────

def test_existing_managed_py_extracts(tmp_path):
    env = tmp_path / "env"
    hook._write_env_block(str(env), ["export PY=/usr/bin/python3\n"])
    assert hook._existing_managed_py(str(env)) == "/usr/bin/python3"


def test_existing_managed_py_none_when_absent(tmp_path):
    env = tmp_path / "env"
    env.write_text("export SOMETHING=1\n")
    assert hook._existing_managed_py(str(env)) is None


def test_existing_managed_py_none_on_legacy_unmarkered(tmp_path):
    # a legacy bare `export PY=` (no managed block) must NOT short-circuit -> None, so the
    # first post-upgrade fire does a full resolution + cleanup instead of trusting stale text.
    env = tmp_path / "env"
    env.write_text("export PY=/legacy/python\n")
    assert hook._existing_managed_py(str(env)) is None


def test_existing_managed_py_quoted_path(tmp_path):
    env = tmp_path / "env"
    hook._write_env_block(str(env), ["export PY='/path with space/python'\n"])
    assert hook._existing_managed_py(str(env)) == "/path with space/python"
