"""release_cut.py — the atomic uat->master release transaction (slice-022 AC4).

This is the ONLY path that advances ``master`` (the marketplace-served released
trunk). It composes the version bump + changelog regen + the integration-branch
merge as a SINGLE git commit (the atomic boundary), borrowing the write-ahead-log /
transaction-commit discipline: stage everything, then ONE commit is the point of no
return. Proven on real Windows git by spike-release-cut-atomicity.

Guards (dual-Critic):
  * B2  — REFUSE on a dirty target tree (`git status --porcelain` non-empty) before
          any checkout/merge; a release cut is a quiescent-point operation.
  * M2  — uat-not-ahead (`merge-base --is-ancestor uat master`) is a clean no-op;
          the pre-merge master SHA is CAPTURED and the failure cleanup is an explicit
          `git reset --hard <captured-SHA>` (NOT `merge --abort` alone, which leaves
          the bump/changelog worktree edits, and NOT implicit ORIG_HEAD, which a
          no-op merge may not refresh).
  * m2  — on a post-commit uat sync-back failure, print an explicit remediation hint.

The git seam is the shared ``run_git`` (injectable as ``git=`` for tests); the bump
and changelog steps are injectable hooks (``bump=`` / ``changelog=``) defaulting to
the existing ``bump_plugin_version.py`` + ``assemble_changelog.py`` subprocesses — so
the transaction's atomicity is unit-testable on real git without the full changelog
machinery. Push of the released ref stays with the existing pr_flow machinery
(forward-only); release_cut owns the atomic LOCAL composition.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from pathlib import Path

# --- single-skill import bootstrap ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib._git_default_branch import (  # noqa: E402
    resolve_default_branch,
    resolve_integration_branch,
    run_git,
)

_SCRIPT_DIR = Path(__file__).resolve().parent
_BUMP = _SCRIPT_DIR / "bump_plugin_version.py"
_ASSEMBLE = _SCRIPT_DIR / "assemble_changelog.py"


def _default_bump(repo_root, new_version, level):
    args = [sys.executable, str(_BUMP),
            "--plugin", str(Path(repo_root) / ".claude-plugin" / "plugin.json")]
    if new_version:
        args += ["--new-version", new_version]
    elif level:
        args += ["--level", level]
    r = subprocess.run(args, capture_output=True, text=True)
    return (r.returncode == 0, (r.stdout or r.stderr).strip())


def _make_default_changelog(vault):
    def _cl(repo_root, new_version):
        if not vault:
            return (False, "no --vault provided for changelog regen")
        args = [sys.executable, str(_ASSEMBLE), "--vault", str(vault),
                "--repo-root", str(repo_root),
                "--out", str(Path(repo_root) / "CHANGELOG.md")]
        if new_version:
            args += ["--new-version", new_version]
        r = subprocess.run(args, capture_output=True, text=True)
        return (r.returncode == 0, (r.stdout or r.stderr).strip())
    return _cl


def _no_changelog(repo_root, new_version):
    return (False, "no changelog hook provided")


def run_release_cut(repo_root, new_version=None, *, level=None, source=None,
                    git=run_git, bump=None, changelog=None):
    """Run the atomic release cut. Returns a result dict with ``action`` + ``exit_code``.

    ``action`` is one of: ``released`` (0), ``no-op`` (0), ``refuse-dirty`` (2),
    ``refuse`` (2), ``no-integration-branch`` (2), ``merge-conflict`` /
    ``bump-failed`` / ``changelog-failed`` / ``commit-failed`` (1).
    """
    repo_root = Path(repo_root)
    bump = bump or _default_bump
    changelog = changelog or _no_changelog
    res: dict = {"action": None, "released_branch": None, "source": None,
                 "new_version": new_version, "exit_code": 1}

    # B2 — refuse on a dirty target tree before any state change.
    st = git(repo_root, "status", "--porcelain")
    if st.returncode != 0:
        return {**res, "action": "refuse", "reason": "git status failed", "exit_code": 2}
    if st.stdout.strip():
        return {**res, "action": "refuse-dirty",
                "reason": "target tree is not clean; release_cut refuses to run on a dirty tree (B2). "
                          "Commit or discard local changes, then retry.",
                "exit_code": 2}

    released = resolve_default_branch(repo_root)
    if released is None:
        return {**res, "action": "refuse", "reason": "cannot resolve the released trunk", "exit_code": 2}
    res["released_branch"] = released

    src = source or resolve_integration_branch(repo_root)
    if src is None:
        return {**res, "action": "refuse", "reason": "git unusable", "exit_code": 2}
    if src == released:
        return {**res, "action": "no-integration-branch",
                "reason": f"no integration branch distinct from the released trunk '{released}' "
                          f"(aisdlc-uat/uat absent); nothing to release.",
                "exit_code": 2}
    res["source"] = src

    co = git(repo_root, "checkout", released)
    if co.returncode != 0:
        return {**res, "action": "refuse",
                "reason": f"cannot checkout {released}: {co.stderr.strip()}", "exit_code": 2}

    # M2 — uat-not-ahead is a clean no-op (idempotent re-run).
    if git(repo_root, "merge-base", "--is-ancestor", src, released).returncode == 0:
        return {**res, "action": "no-op",
                "reason": f"{src} is already an ancestor of {released}; nothing to release.",
                "exit_code": 0}

    captured = git(repo_root, "rev-parse", released).stdout.strip()
    res["pre_merge_sha"] = captured

    def _rollback():
        git(repo_root, "reset", "--hard", captured)

    mg = git(repo_root, "merge", "--no-ff", "--no-commit", src)
    if mg.returncode != 0:
        _rollback()
        return {**res, "action": "merge-conflict",
                "reason": f"merge {src} -> {released} conflicted; rolled back to {captured[:10]} "
                          f"(master untouched).", "exit_code": 1}

    ok, bump_msg = bump(repo_root, new_version, level)
    if not ok:
        _rollback()
        return {**res, "action": "bump-failed",
                "reason": f"version bump failed ({bump_msg}); rolled back to {captured[:10]}.", "exit_code": 1}
    # CR1: on a `--level` run new_version is None; bump (bump_plugin_version) prints the RESOLVED
    # version -- capture it so the changelog + commit message use the real version, not "None".
    effective_version = new_version or (bump_msg or "").strip()
    res["new_version"] = effective_version

    ok, cl_msg = changelog(repo_root, effective_version)
    if not ok:
        _rollback()
        return {**res, "action": "changelog-failed",
                "reason": f"changelog regen failed ({cl_msg}); rolled back to {captured[:10]}.", "exit_code": 1}

    git(repo_root, "add", "-A")
    cm = git(repo_root, "commit", "-m",
             f"release {effective_version}: merge {src} -> {released} + bump + changelog")
    if cm.returncode != 0:
        _rollback()
        return {**res, "action": "commit-failed",
                "reason": f"commit failed ({cm.stderr.strip()}); rolled back to {captured[:10]}.", "exit_code": 1}
    res["release_sha"] = git(repo_root, "rev-parse", released).stdout.strip()

    # Post-commit (forward-only): sync the integration branch back to the new release.
    git(repo_root, "checkout", src)
    sb = git(repo_root, "merge", "--ff-only", released)
    git(repo_root, "checkout", released)  # leave the operator on the released trunk
    if sb.returncode != 0:
        res["sync_back"] = "failed"
        res["hint"] = (f"master is released at {new_version} but the {src} sync-back failed; run "
                       f"`git checkout {src} && git merge --ff-only {released}` before the next "
                       f"slice or {src} will re-deliver released work (m2).")
    else:
        res["sync_back"] = "ok"
    res["action"] = "released"
    res["exit_code"] = 0
    return res


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="release_cut",
        description="Atomic uat->master release cut: merge + bump + changelog as ONE commit (slice-022 AC4).")
    ap.add_argument("--repo-root", default=".", help="repo root (the released-trunk tree; default cwd)")
    ap.add_argument("--new-version", dest="new_version", default=None, help="explicit target X.Y.Z")
    ap.add_argument("--level", default=None, choices=["patch", "minor", "major"],
                    help="compute the target by incrementing the current version")
    ap.add_argument("--vault", default=None, help="vault root (for the changelog regen)")
    ap.add_argument("--source", default=None,
                    help="integration branch (default: resolve aisdlc-uat, legacy uat as back-compat)")
    ap.add_argument("--confirmed", action="store_true", help="required human release-confirmation gate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.confirmed:
        sys.stderr.write("release_cut: refusing to run without --confirmed (the human release gate).\n")
        return 2
    if not args.new_version and not args.level:
        sys.stderr.write("release_cut: no target determinable -- pass --new-version X.Y.Z or --level.\n")
        return 2

    r = run_release_cut(args.repo_root, args.new_version, level=args.level, source=args.source,
                        changelog=_make_default_changelog(args.vault))
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"release_cut: {r['action']} -- {r.get('reason', r.get('release_sha', ''))}")
        if r.get("hint"):
            print(r["hint"])
    return r["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
