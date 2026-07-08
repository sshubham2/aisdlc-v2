"""release_advance_audit.py — slice-022 AC4 teeth.

Assert the structural integrity rule of the uat/master release model: **master
(the marketplace-served released trunk) advanced ONLY via versioned release cuts.**

It walks ``git rev-list --first-parent <GENESIS>..<released>`` from a DURABLE
recorded genesis (default: a ``release-genesis`` tag at the release baseline), NOT
the live ``merge-base(uat,master)`` — which advances on every release+sync-back and
would silently collapse the audit window to "since the last release", a false-GREEN
(M1). Every first-parent advance of the released trunk since genesis MUST change
``.claude-plugin/plugin.json``'s ``version`` line; an unversioned advance — or a
split bump/changelog where the changelog commit carries no version change — is
flagged. It also asserts the resolved integration branch (``aisdlc-uat``, or legacy
``uat`` in an ai-sdlc-managed repo) descends from the recorded genesis (M4: the
integration branch was rooted at the release baseline).

Mirrors the existing audit idiom (``branch_workflow_audit``): exit 0 clean / 1
violation / 2 usage (git unusable / genesis absent). NO-OP PASS on a non-methodology
repo (no ``.claude-plugin/plugin.json``), so it is safe to run in any repo's CI.

Read-only — never mutates git or files.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path

# --- shared-leaf import bootstrap ---
_REPO = pathlib.Path(__file__).resolve().parents[2]  # scripts/lib/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib._git_default_branch import (  # noqa: E402
    existing_integration_branch,
    resolve_default_branch,
    run_git,
)

PLUGIN_REL = ".claude-plugin/plugin.json"


def _version_changed(repo_root: Path, commit: str) -> bool:
    """True iff this commit changed plugin.json's ``"version"`` line vs its first parent."""
    d = run_git(repo_root, "diff", f"{commit}^", commit, "--", PLUGIN_REL)
    if d.returncode != 0:
        return False
    for line in d.stdout.splitlines():
        if not line or line[0] not in "+-" or line.startswith(("+++", "---")):
            continue
        if '"version"' in line:
            return True
    return False


def audit(repo_root: Path | str, genesis_ref: str = "release-genesis") -> dict:
    repo_root = Path(repo_root)
    result: dict = {
        "rule": "RELEASE-ADVANCE",
        "genesis_ref": genesis_ref,
        "released_branch": None,
        "genesis_sha": None,
        "violations": [],
        "clean": False,
        "exit_code": 1,
    }

    if run_git(repo_root, "rev-parse", "--git-dir").returncode != 0:
        result["violations"].append({"kind": "usage-error",
                                      "message": f"{repo_root} is not a git repository"})
        result["exit_code"] = 2
        return result

    # NO-OP PASS on a non-methodology repo.
    if not (repo_root / PLUGIN_REL).exists():
        result.update(noop=True, clean=True, exit_code=0,
                      message="non-methodology repo (no .claude-plugin/plugin.json) -> NO-OP PASS")
        return result

    released = resolve_default_branch(repo_root)
    if released is None:
        result["violations"].append({"kind": "default-branch-unresolvable",
                                      "message": "cannot resolve the released trunk (default branch)"})
        result["exit_code"] = 2
        return result
    result["released_branch"] = released

    g = run_git(repo_root, "rev-parse", "--verify", "--quiet", f"{genesis_ref}^{{commit}}")
    if g.returncode != 0 or not g.stdout.strip():
        result["violations"].append({
            "kind": "genesis-absent",
            "message": (f"durable genesis ref '{genesis_ref}' not found; establish it at the "
                        f"release baseline (e.g. `git tag {genesis_ref} <master@baseline>`)."),
        })
        result["exit_code"] = 2
        return result
    genesis_sha = g.stdout.strip()
    result["genesis_sha"] = genesis_sha

    rl = run_git(repo_root, "rev-list", "--first-parent", f"{genesis_sha}..{released}")
    if rl.returncode != 0:
        result["violations"].append({"kind": "usage-error",
                                      "message": f"cannot rev-list {genesis_ref}..{released}: {rl.stderr.strip()}"})
        result["exit_code"] = 2
        return result

    for c in rl.stdout.split():
        if not _version_changed(repo_root, c):
            result["violations"].append({
                "kind": "unbumped-advance", "commit": c,
                "message": (f"{released} commit {c[:10]} advanced the released trunk without a "
                            f"plugin.json version bump (unversioned advance / split release)."),
            })

    # M4: the resolved integration branch (aisdlc-uat / legacy uat) must descend from the
    # recorded genesis. Resolve via the shared non-degrading probe (slice-061) so this check
    # stays in lockstep with the resolver + write guard; skip it when no integration branch
    # exists (preserves the fresh-repo behavior -- never false-flag the released trunk).
    integration = existing_integration_branch(repo_root)
    if integration is not None:
        result["integration_branch_checked"] = integration
        if run_git(repo_root, "merge-base", "--is-ancestor", genesis_sha, integration).returncode != 0:
            result["violations"].append({
                "kind": "integration-genesis-mismatch",
                "message": (f"integration branch '{integration}' does not descend from the recorded "
                            f"genesis {genesis_sha[:10]} (M4: the integration branch must be rooted "
                            f"at the release baseline)."),
            })

    result["clean"] = not result["violations"]
    result["exit_code"] = 0 if result["clean"] else 1
    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="release_advance_audit",
        description="Assert master advanced ONLY via versioned release cuts (slice-022 AC4).")
    ap.add_argument("--root", default=".", help="repo root to inspect (default: cwd)")
    ap.add_argument("--genesis", default="release-genesis",
                    help="durable genesis ref/tag (default: release-genesis)")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = ap.parse_args(argv)

    r = audit(args.root, genesis_ref=args.genesis)
    if args.json:
        print(json.dumps(r, indent=2))
    elif r.get("noop"):
        print(f"release_advance_audit: {r['message']}")
    elif r["exit_code"] == 0:
        print(f"release_advance_audit: clean. {r['released_branch']} advanced only via "
              f"versioned cuts since {args.genesis}.")
    else:
        for v in r["violations"]:
            print(f"[{v['kind']}] {v['message']}")
    return r["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
