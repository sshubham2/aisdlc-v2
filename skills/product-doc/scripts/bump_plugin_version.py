"""bump_plugin_version.py — set .claude-plugin/plugin.json's ``version`` to a
validated target (slice-009).

The plugin version bump is RELOCATED out of the per-commit convention into
/product-doc: instead of every pushed slice commit staging its own bump (which
made parallel slices conflict on the plugin.json version line and mis-grouped
the CHANGELOG), the version is cut ONCE — here — after merge, at the release.

This helper is the minimal read-validate-write primitive:

  * ``--new-version X.Y.Z``  — set the version explicitly (the primary form).
  * ``--level patch|minor|major`` — compute the target from the current version.

It REFUSES (non-zero exit, file left untouched) to:
  * lower or otherwise non-INCREASE the version (semver tuple compare),
  * proceed when no target is determinable (no --new-version / --level),
  * touch a missing or malformed plugin.json (don't guess).

It is a NO-OP (exit 0, no write) when the current version already equals the
requested --new-version, so a second /product-doc run is idempotent (M4).

NO conventional-commit inference, NO tags, NO store — that is intentionally out
of scope (YAGNI). On success it prints the resolved new version to stdout.

Exit codes: 0 ok (incl. the no-op) · non-zero on reject / undeterminable /
malformed / missing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

DEFAULT_PLUGIN_REL = ".claude-plugin/plugin.json"


def _vt(v: str) -> tuple:
    """Semver tuple for comparison: ``"2.10.0"`` -> ``(2, 10, 0)``."""
    return tuple(int(x) for x in str(v).split("."))


def _bump_level(current: str, level: str) -> str:
    major, minor, patch = (list(_vt(current)) + [0, 0, 0])[:3]
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"  # patch


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bump_plugin_version",
        description="Set .claude-plugin/plugin.json's version to a validated target "
                    "(the relocated, post-merge /product-doc version cut).",
    )
    p.add_argument("--plugin", default=None,
                   help=f"path to plugin.json (default: <cwd>/{DEFAULT_PLUGIN_REL})")
    p.add_argument("--new-version", default=None, dest="new_version",
                   help="explicit target version X.Y.Z (the primary form)")
    p.add_argument("--level", default=None, choices=["patch", "minor", "major"],
                   help="compute the target by incrementing the current version")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    plugin = Path(args.plugin) if args.plugin else Path.cwd() / DEFAULT_PLUGIN_REL

    # --- read + validate the manifest (refuse, don't guess) ---
    try:
        raw = plugin.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"bump_plugin_version: cannot read {plugin}: {exc}\n")
        return 2
    try:
        data = json.loads(raw)
    except ValueError as exc:
        sys.stderr.write(
            f"bump_plugin_version: {plugin} is not valid JSON ({exc}); refusing to "
            "guess — fix the manifest and retry.\n")
        return 2
    if not isinstance(data, dict) or "version" not in data:
        sys.stderr.write(
            f"bump_plugin_version: {plugin} has no 'version' field; refusing.\n")
        return 2
    current = str(data["version"])
    try:
        cur_t = _vt(current)
    except (ValueError, AttributeError):
        sys.stderr.write(
            f"bump_plugin_version: current version {current!r} is not semver; refusing.\n")
        return 2

    # --- resolve the target (fail-visible if undeterminable) ---
    if args.new_version:
        target = args.new_version.strip()
    elif args.level:
        target = _bump_level(current, args.level)
    else:
        sys.stderr.write(
            "bump_plugin_version: no target determinable — pass --new-version X.Y.Z "
            "(or --level patch|minor|major). Refusing to bump silently.\n")
        return 2

    try:
        tgt_t = _vt(target)
    except (ValueError, AttributeError):
        sys.stderr.write(
            f"bump_plugin_version: target version {target!r} is not semver; refusing.\n")
        return 2

    # --- NO-OP idempotence (M4): already at the target -> success, no write ---
    if tgt_t == cur_t:
        print(current)
        return 0

    # --- reject non-INCREASING (strictly-greater required) ---
    if tgt_t < cur_t:
        sys.stderr.write(
            f"bump_plugin_version: target {target} is not greater than current "
            f"{current}; refusing a non-increasing bump (plugin.json unchanged).\n")
        return 2

    # --- write, preserving the file's 2-space indent + trailing newline style ---
    data["version"] = target
    try:
        plugin.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"bump_plugin_version: cannot write {plugin}: {exc}\n")
        return 2
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
