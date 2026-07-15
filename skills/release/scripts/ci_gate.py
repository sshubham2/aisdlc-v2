"""ci_gate.py — /release pre-flight: is the integration branch's HEAD green on CI?

`/release` must NOT cut `uat -> master` while the integration branch's latest CI run
is red or still running. That is exactly the failure that shipped slices 066-069 RED
into 2.39.0: they were merged to `aisdlc-uat` locally and never pushed, so CI first saw
them in the release cut itself — on `master`, the marketplace-served trunk.

This queries GitHub Actions (via `gh`) for the CI run matching the integration branch's
HEAD SHA and classifies it:

  green          — a completed run for this exact SHA, no failing jobs  -> exit 0 (proceed)
  red            — a completed run for this SHA with >=1 failing job     -> exit 1 (BLOCK)
  pending        — a run for this SHA still queued / in progress         -> exit 1 (BLOCK)
  no-run-for-sha — no CI run found for this SHA (push it + let CI run)    -> exit 1 (BLOCK)
  gh-absent      — the `gh` CLI is not installed                         -> exit 3 (DEGRADE)
  not-github     — not a GitHub repo / gh not authed / query failed      -> exit 3 (DEGRADE)

Fail-CLOSED on red / pending / no-run (the cut must not advance the released trunk on
unverified code). DEGRADE (advisory; the caller proceeds with a warning) ONLY when there
is no GitHub CI to consult — `/release` must still work in a non-GitHub context. The gate
keys on the EXACT HEAD SHA, so a green run for an OLDER uat commit never passes newer,
unverified work.

CLI:
  ci_gate.py --repo-root <dir> --branch <integration-branch> [--sha <sha>] [--limit N]
JSON on stdout: {gate, sha, branch, conclusion, run_id, workflow, message}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

# gh `conclusion` values that mean the run did NOT pass (skipped/neutral are NOT failures).
FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out", "startup_failure", "action_required", "stale"}


def _run(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 — any spawn failure degrades, never crashes
        return 1, "", str(e)
    return p.returncode, p.stdout, p.stderr


def classify_ci(runs: list[dict], head_sha: str) -> dict:
    """Pure: classify the CI state for `head_sha` from a `gh run list --json` array.

    `runs` items carry headSha / status / conclusion / workflowName / databaseId.
    Only runs whose headSha == head_sha count — a green run for an older SHA must not
    vouch for newer commits."""
    sha = (head_sha or "").strip()
    matching = [r for r in runs if isinstance(r, dict) and str(r.get("headSha", "")).strip() == sha and sha]
    if not matching:
        return {"gate": "no-run-for-sha", "conclusion": None, "run_id": None, "workflow": None,
                "message": f"no CI run found for {sha[:9] or '(empty)'} — push the integration branch and let CI run"}
    pending = [r for r in matching if str(r.get("status")) != "completed"]
    if pending:
        w = pending[0].get("workflowName")
        return {"gate": "pending", "conclusion": None, "run_id": pending[0].get("databaseId"), "workflow": w,
                "message": f"CI for {sha[:9]} is still running ({w}) — wait for it to finish"}
    failed = [r for r in matching if str(r.get("conclusion")) in FAIL_CONCLUSIONS]
    if failed:
        w = failed[0].get("workflowName")
        return {"gate": "red", "conclusion": failed[0].get("conclusion"), "run_id": failed[0].get("databaseId"),
                "workflow": w, "message": f"CI for {sha[:9]} is RED ({w}: {failed[0].get('conclusion')}) — fix it before releasing"}
    # all completed, none failed (success / skipped / neutral) -> green
    ok = next((r for r in matching if str(r.get("conclusion")) == "success"), matching[0])
    return {"gate": "green", "conclusion": ok.get("conclusion"), "run_id": ok.get("databaseId"),
            "workflow": ok.get("workflowName"), "message": f"CI for {sha[:9]} is green"}


def _head_sha(repo_root: str, branch: str) -> str | None:
    rc, out, _ = _run(["git", "-C", repo_root, "rev-parse", branch])
    return out.strip() if rc == 0 and out.strip() else None


def gather(repo_root: str, branch: str, sha: str | None, limit: int) -> dict:
    if shutil.which("gh") is None:
        return {"gate": "gh-absent", "sha": sha, "branch": branch, "conclusion": None, "run_id": None,
                "workflow": None, "message": "gh CLI not installed — cannot verify CI; proceeding is a DEGRADE"}
    head = (sha or _head_sha(repo_root, branch) or "").strip()
    if not head:
        return {"gate": "not-github", "sha": None, "branch": branch, "conclusion": None, "run_id": None,
                "workflow": None, "message": f"could not resolve HEAD of {branch}"}
    rc, out, err = _run(["gh", "run", "list", "--branch", branch, "--limit", str(limit),
                         "--json", "headSha,status,conclusion,workflowName,databaseId"], cwd=repo_root)
    if rc != 0:
        return {"gate": "not-github", "sha": head, "branch": branch, "conclusion": None, "run_id": None,
                "workflow": None, "message": f"gh could not list runs (not a GitHub repo / not authed): {err.strip()[:160]}"}
    try:
        runs = json.loads(out or "[]")
    except json.JSONDecodeError:
        return {"gate": "not-github", "sha": head, "branch": branch, "conclusion": None, "run_id": None,
                "workflow": None, "message": "gh returned unparseable JSON"}
    verdict = classify_ci(runs, head)
    verdict["sha"] = head
    verdict["branch"] = branch
    return verdict


_EXIT = {"green": 0, "red": 1, "pending": 1, "no-run-for-sha": 1, "gh-absent": 3, "not-github": 3}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(prog="ci_gate")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--branch", required=True, help="integration branch to check (e.g. aisdlc-uat)")
    ap.add_argument("--sha", default=None, help="explicit SHA (default: resolve --branch HEAD)")
    ap.add_argument("--limit", type=int, default=25)
    ns = ap.parse_args(argv)
    verdict = gather(ns.repo_root, ns.branch, ns.sha, ns.limit)
    print(json.dumps(verdict))
    return _EXIT.get(verdict.get("gate"), 1)


if __name__ == "__main__":
    sys.exit(main())
