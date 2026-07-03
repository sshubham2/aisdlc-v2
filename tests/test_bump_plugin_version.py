"""tests for skills/release/scripts/bump_plugin_version.py (slice-009).

The version bump is RELOCATED out of the per-commit convention and into
/release: a single validated helper that sets .claude-plugin/plugin.json's
``version`` field to a chosen target, refusing a non-increasing or
undeterminable bump and never guessing on a malformed manifest.

Written test-first (TF-1). The helper is exercised end-to-end via subprocess
(the faithful invocation path) using the shared ``run_script`` fixture from
conftest.py, mirroring test_assemble_changelog.py's style.

Covers: valid bump (file rewritten, exit 0, new version printed); reject a
NON-INCREASING target (current unchanged); NO-OP idempotence (M4: re-run safe);
fail-VISIBLE when no target is determinable; refuse a malformed plugin.json
(don't guess).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SCRIPT = "skills/release/scripts/bump_plugin_version.py"


def _write_plugin(path: Path, version: str, *, name: str = "ai-sdlc") -> None:
    """Write a plugin.json in the repo's real 2-space-indent + trailing-newline style."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": name, "version": version}, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _bump(run_script, plugin: Path, *extra):
    return run_script(SCRIPT, ["--plugin", str(plugin), *extra])


# ---- valid bump ------------------------------------------------------------
def test_valid_bump_rewrites_and_prints(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.0.0")
    r = _bump(run_script, plugin, "--new-version", "2.1.0")
    assert r.returncode == 0, r.stderr
    assert _read_version(plugin) == "2.1.0"        # file now 2.1.0
    assert "2.1.0" in r.stdout                      # resolved new version printed


def test_valid_bump_preserves_style(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.0.0")
    _bump(run_script, plugin, "--new-version", "2.1.0")
    text = plugin.read_text(encoding="utf-8")
    assert text.endswith("\n")                      # trailing newline preserved
    assert '\n  "version"' in text                  # 2-space indent preserved
    # the rest of the document survives (name field intact)
    assert json.loads(text)["name"] == "ai-sdlc"


# ---- CR1 regression: non-ASCII fields survive a bump (ensure_ascii=False) ---
def test_valid_bump_preserves_nonascii_fields(run_script, tmp_path):
    # The live plugin.json `description` carries an em-dash (U+2014). A bump must
    # change ONLY the version line; default json.dumps (ensure_ascii=True) would
    # escape it to \\u2014 and corrupt the manifest on every release cut. Guards CR1.
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {"name": "ai-sdlc", "version": "2.0.0", "description": "spec-driven — reality-grounded"},
        indent=2, ensure_ascii=False) + "\n"
    plugin.write_text(original, encoding="utf-8")
    r = _bump(run_script, plugin, "--new-version", "2.1.0")
    assert r.returncode == 0, r.stderr
    after = plugin.read_text(encoding="utf-8")
    assert "\\u2014" not in after            # em-dash NOT escaped/mangled
    assert "—" in after                  # the literal em-dash survives
    assert _read_version(plugin) == "2.1.0"
    # every non-version line is byte-identical to the original
    o = [ln for ln in original.splitlines() if '"version"' not in ln]
    a = [ln for ln in after.splitlines() if '"version"' not in ln]
    assert a == o


# ---- reject NON-INCREASING -------------------------------------------------
def test_reject_lower_target_leaves_file_unchanged(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.0")
    before = plugin.read_bytes()
    r = _bump(run_script, plugin, "--new-version", "2.0.0")
    assert r.returncode != 0
    assert plugin.read_bytes() == before            # plugin.json UNCHANGED


def test_reject_equal_when_strict_increase_not_noop(run_script, tmp_path):
    # equal target is the NO-OP case (covered separately) — the non-increasing
    # REJECT path is specifically a STRICTLY-LOWER target.
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.10.0")
    before = plugin.read_bytes()
    r = _bump(run_script, plugin, "--new-version", "2.9.0")  # 2.9 < 2.10 (semver tuple)
    assert r.returncode != 0
    assert plugin.read_bytes() == before


# ---- NO-OP idempotence (M4) ------------------------------------------------
def test_noop_when_target_equals_current(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.0")
    before = plugin.read_bytes()
    r = _bump(run_script, plugin, "--new-version", "2.1.0")  # already there
    assert r.returncode == 0, r.stderr                       # exit 0 (re-run is safe)
    assert plugin.read_bytes() == before                     # file unchanged
    assert "2.1.0" in r.stdout                                # still reports the version


# ---- fail-VISIBLE when no target determinable ------------------------------
def test_no_target_fails_visibly(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.0")
    before = plugin.read_bytes()
    r = _bump(run_script, plugin)  # no --new-version, no --level
    assert r.returncode != 0
    assert r.stderr.strip()                          # a clear message on stderr
    assert plugin.read_bytes() == before             # file unchanged


# ---- malformed plugin.json: refuse, don't guess ----------------------------
def test_malformed_plugin_json_refused(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("{ this is : not json", encoding="utf-8")
    before = plugin.read_bytes()
    r = _bump(run_script, plugin, "--new-version", "2.1.0")
    assert r.returncode != 0
    assert plugin.read_bytes() == before             # refused, not rewritten


def test_missing_plugin_json_refused(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"  # never created
    r = _bump(run_script, plugin, "--new-version", "2.1.0")
    assert r.returncode != 0
    assert not plugin.exists()


# ---- optional --level convenience (compute from current) -------------------
def test_level_minor_increments(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.3")
    r = _bump(run_script, plugin, "--level", "minor")
    assert r.returncode == 0, r.stderr
    assert _read_version(plugin) == "2.2.0"          # minor bump resets patch


def test_level_patch_increments(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.3")
    r = _bump(run_script, plugin, "--level", "patch")
    assert r.returncode == 0, r.stderr
    assert _read_version(plugin) == "2.1.4"


def test_level_major_increments(run_script, tmp_path):
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    _write_plugin(plugin, "2.1.3")
    r = _bump(run_script, plugin, "--level", "major")
    assert r.returncode == 0, r.stderr
    assert _read_version(plugin) == "3.0.0"
