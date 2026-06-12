"""skills/build-slice/scripts/new_agent_warning_audit.py — NAW-1 graceful no-op (CI fix).

NAW-1 is a per-slice DIFF audit relocated to plugin CI (1.5). A GitHub Actions checkout is
detached-HEAD with no default-branch ref, so the diff base is unresolvable — which must be a
no-op (exit 0), not a usage error (exit 2). Driven via the audit's injectable resolver seams.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


naw = _load("skills/build-slice/scripts/new_agent_warning_audit.py", "ai_sdlc_naw_audit")


def test_no_default_branch_is_skipped_not_usage():
    # detached-HEAD / shallow CI checkout: no diff base -> exit 0 (no-op), NOT exit 2
    r = naw.check(PLUGIN_ROOT, default_branch_resolver=lambda _root: None)
    assert r.status == "skipped"
    assert r.exit_code == 0


def test_added_agents_warns_exit0():
    r = naw.check(
        PLUGIN_ROOT,
        default_branch_resolver=lambda _root: "master",
        added_files_resolver=lambda _root, _base: ["agents/new-one.md"],
    )
    assert r.status == "warn"
    assert r.exit_code == 0
    assert any("new-one.md" in w for w in r.warnings)


def test_clean_when_no_agents_added():
    r = naw.check(
        PLUGIN_ROOT,
        default_branch_resolver=lambda _root: "master",
        added_files_resolver=lambda _root, _base: [],
    )
    assert r.status == "clean"
    assert r.exit_code == 0
