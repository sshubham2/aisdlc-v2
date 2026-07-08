"""pr_flow.py — the non-interactive PR ladder for `/commit-slice --push` (slice-008).

A monotone degradation ladder over irreversible remote steps (the cross-domain
transfer from fault-tolerant capability negotiation):

    REBASED  <  PUSHED  <  PR_CREATED  <  AUTOMERGE_ENABLED

SKILL.md's shared rebase section reaches REBASED (interactive PCR-2b — NOT here).
This script, invoked with ``--confirmed`` after the single human push/PR
confirmation, executes the non-interactive remainder: push -> create the PR ->
(when the user has merge rights) enable NON-BLOCKING auto-merge -> VERIFY it.

Load-bearing invariants (from the design + the Critic):
  * **Verify the OUTCOME, never the exit code** — auto-merge is only "enabled" when a
    read-back of ``autoMergeRequest`` is non-null (gh can exit 0 yet not enable it,
    cli #3367/#8792).
  * **Never enable auto-merge without a confirmed merge-permission signal**
    (``.permissions.push == true``); missing/null/false -> never attempt (a
    fine-grained PAT may omit the field — that is "unknown", not "no", but we still
    do not auto-merge on unknown; the human finishes manually).
  * **Forward-recovery only** — every git/gh failure halts at the highest reached
    rung (a still-correct terminal state) and prints how to finish; nothing rolls
    back; we NEVER force-push, force-delete, or skip hooks.
  * **AUTO-MERGE ONLY** — the direct-merge path was dropped at slice-008 TRI-1; this
    ladder never merges locally and never deletes a branch.
  * **M-add-1** — the instant the (irreversible) push succeeds, the PUSHED breadcrumb
    is flushed to stdout so a later internal error still leaves the push on the record.

The runner is the SOLE git/gh seam: ``runner(argv: list[str]) -> CompletedProcess``.
The real CLI binds it to ``subprocess.run(..., cwd=repo_root)``; tests inject a fake.
``run_pr_flow`` returns a ``Verdict``; it writes NO files and mutates no local branch
state beyond the push (the vault Events are returned for SKILL.md to append).

Exit codes (CLI): 0 = any graceful degradation (verdict in JSON) · 1 = internal
error (a non-degradation exception) · 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = Path(__file__).resolve().parents[3]  # <plugin>/skills/commit-slice/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._git_default_branch import resolve_integration_branch

__all__ = ["Verdict", "run_pr_flow", "parse_github_remote", "main"]

Runner = Callable[[list], subprocess.CompletedProcess]

# Rung ladder, lowest -> highest.
REBASED, PUSHED, PR_CREATED, AUTOMERGE_ENABLED = "REBASED", "PUSHED", "PR_CREATED", "AUTOMERGE_ENABLED"

_GITHUB_RE = re.compile(r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?/?$")
# Push-rejection signatures that mean "remote diverged" (resolve by inspection, NEVER force).
_NONFF_RE = re.compile(r"non-fast-forward|\[rejected\]|fetch first|Updates were rejected", re.I)
# PR-create signatures that mean the head isn't pushed / has no commits (halt at PUSHED).
_PR_HEAD_RE = re.compile(r"no commits between|must first push|no commits on", re.I)


@dataclass
class Verdict:
    rung_reached: str = REBASED
    action: str = ""
    pr_url: str | None = None
    can_merge: str = "unknown"          # "true" | "false" | "unknown"
    automerge_confirmed: bool = False
    reason: str = ""
    stderr: str = ""
    events: list[dict] = field(default_factory=list)
    internal_error: bool = False

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "rung_reached": self.rung_reached,
            "pr_url": self.pr_url,
            "can_merge": self.can_merge,
            "automerge_confirmed": self.automerge_confirmed,
            "reason": self.reason,
            "stderr": self.stderr,
            "events": self.events,
            "internal_error": self.internal_error,
        }


def parse_github_remote(url: str) -> tuple[str, str] | None:
    """Return ``(owner, repo)`` for a github.com origin URL, else None (non-GitHub)."""
    m = _GITHUB_RE.match((url or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _event(rung: str, note: str) -> dict:
    return {"rung": rung, "note": note}


def _gh_present(runner: Runner) -> bool:
    """True iff `gh` is invocable. A missing binary raises FileNotFoundError -> False."""
    try:
        return runner(["gh", "--version"]).returncode == 0
    except FileNotFoundError:
        return False


def _origin_github(runner: Runner) -> tuple[str, str] | None:
    cp = runner(["git", "remote", "get-url", "origin"])
    if cp.returncode != 0:
        return None
    return parse_github_remote(cp.stdout)


def run_pr_flow(
    *,
    runner: Runner,
    branch: str,
    default: str,
    merge_method: str = "merge",
    emit: Callable[[dict], None] | None = None,
) -> Verdict:
    """Drive the ladder. ``emit`` (optional) receives the PUSHED breadcrumb the instant
    the push lands (M-add-1) — the CLI wires it to a flushed stdout line; tests record it."""
    v = Verdict()

    # --- rung 1: push (irreversible) ------------------------------------------
    push = runner(["git", "push", "-u", "origin", branch])
    if push.returncode != 0:
        v.rung_reached = REBASED
        v.stderr = push.stderr or ""
        if _NONFF_RE.search(v.stderr):
            v.action = "push-rejected-nonff"
            v.reason = ("push rejected as non-fast-forward — the remote diverged. "
                        "Inspect `git log origin/{0}..{0}` and reconcile; NEVER force-push.".format(branch))
        else:
            v.action = "push-failed"
            v.reason = "git push failed — see stderr; resolve and re-run --push."
        v.events.append(_event(REBASED, f"push failed: {v.action}"))
        return v

    v.rung_reached = PUSHED
    v.events.append(_event(PUSHED, f"pushed {branch} to origin"))
    if emit is not None:                       # M-add-1: flush the irreversible push NOW
        emit(_event(PUSHED, f"pushed {branch} to origin"))

    # --- rungs 2-4: gh ladder (degrade gracefully; an exception -> internal-error) ---
    try:
        _gh_ladder(v, runner, branch, default, merge_method)
    except FileNotFoundError:
        # gh vanished mid-ladder — degrade to the printed-hint floor (still graceful).
        v.action = "fallback-hint"
        v.reason = "gh unavailable after push — PR not created; create it manually."
    except Exception as exc:  # noqa: BLE001 — any non-degradation crash is an internal error
        v.internal_error = True
        v.action = "internal-error"
        v.reason = f"internal error after push (rung={v.rung_reached}): {exc}"
        v.events.append(_event(v.rung_reached, f"internal error: {exc}"))
    return v


def _gh_ladder(v: Verdict, runner: Runner, branch: str, default: str, merge_method: str) -> None:
    """Mutates ``v`` in place. Each decision point sets the action + reason and returns;
    the floor is always the PUSHED rung + a printed hint."""
    if not _gh_present(runner):
        v.action = "fallback-hint"
        v.reason = (f"gh not installed — branch pushed. Open the PR: "
                    f"gh pr create --base {default} --head {branch}  (or via your hosting UI).")
        return

    owner_repo = _origin_github(runner)
    if owner_repo is None:
        v.action = "fallback-hint"
        v.reason = (f"non-GitHub origin — branch pushed. Open the PR via your hosting UI "
                    f"(base {default}, head {branch}).")
        return
    owner, repo = owner_repo

    # --- rung 3: create the PR ------------------------------------------------
    # `--fill` is REQUIRED, not cosmetic: `gh pr create` is interactive by default
    # (it prompts for title/body); `--fill` derives them from the slice commits so the
    # call is non-interactive (asserted by the AC2 test).
    create = runner(["gh", "pr", "create", "--base", default, "--head", branch, "--fill"])
    if create.returncode == 0:
        v.pr_url = (create.stdout or "").strip() or None
        v.rung_reached = PR_CREATED
        v.action = "pr-created"
        v.events.append(_event(PR_CREATED, f"created PR {v.pr_url}"))
    elif re.search(r"already exists", create.stderr or "", re.I):
        view = runner(["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"])
        v.pr_url = (view.stdout or "").strip() or None
        v.rung_reached = PR_CREATED
        v.action = "pr-exists"
        v.events.append(_event(PR_CREATED, f"PR already exists: {v.pr_url}"))
    else:
        v.stderr = create.stderr or ""
        v.rung_reached = PUSHED
        v.action = "pr-create-failed"
        if _PR_HEAD_RE.search(v.stderr):
            v.reason = (f"gh pr create reported the head is not pushed / has no commits. "
                        f"Verify the push, then: gh pr create --base {default} --head {branch}")
        else:
            v.reason = (f"gh pr create failed — see stderr. Finish manually: "
                        f"gh pr create --base {default} --head {branch}")
        return

    # --- rung 4a: merge-permission probe (never hard-block on unknown) ---------
    perm = runner(["gh", "api", f"repos/{owner}/{repo}", "--jq", ".permissions.push"])
    answer = (perm.stdout or "").strip().lower()
    if perm.returncode == 0 and answer == "true":
        v.can_merge = "true"
    elif perm.returncode == 0 and answer == "false":
        v.can_merge = "false"
    else:
        v.can_merge = "unknown"

    if v.can_merge != "true":
        v.action = "automerge-skipped-no-permission"
        v.reason = (f"PR created ({v.pr_url}); no confirmed merge permission "
                    f"(.permissions.push={answer or 'missing'}). Merge via the UI or re-run after "
                    f"CI, then `/commit-slice --sync-after-pr`.")
        return

    # --- rung 4b: enable non-blocking auto-merge ------------------------------
    enable = runner(["gh", "pr", "merge", "--auto", f"--{merge_method}", branch])
    if enable.returncode != 0:
        # The EXPECTED common fallback (HTTP 422 while checks pending / not allowed).
        v.stderr = enable.stderr or ""
        v.action = "automerge-unavailable"
        v.reason = (f"PR created ({v.pr_url}); auto-merge could not be enabled (often pending "
                    f"required checks, or not allowed on this repo). Merge via the UI or re-run "
                    f"after CI, then `/commit-slice --sync-after-pr`.")
        return

    # --- rung 4c: VERIFY the outcome (never trust exit 0) ----------------------
    view = runner(["gh", "pr", "view", branch, "--json", "autoMergeRequest", "--jq", ".autoMergeRequest"])
    confirmed = (view.stdout or "").strip()
    if confirmed and confirmed.lower() != "null":
        v.automerge_confirmed = True
        v.rung_reached = AUTOMERGE_ENABLED
        v.action = "automerge-enabled"
        v.events.append(_event(AUTOMERGE_ENABLED, f"auto-merge enabled + verified for {v.pr_url}"))
    else:
        # gh exited 0 but the request did not stick (silent false-success).
        v.action = "automerge-unverified"
        v.reason = (f"PR created ({v.pr_url}); gh reported success but autoMergeRequest is null — "
                    f"treating auto-merge as NOT enabled. Merge via the UI, then `--sync-after-pr`.")


# ----------------------------- CLI -----------------------------


def _real_runner(repo_root: str) -> Runner:
    def run(argv):
        return subprocess.run([str(a) for a in argv], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    return run


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pr_flow",
        description="Non-interactive PR ladder for /commit-slice --push (auto-merge only). "
                    "Requires --confirmed (the human push/PR confirmation gate).",
    )
    p.add_argument("--branch", required=True, help="the slice branch to push (slice/NNN-name)")
    p.add_argument("--default", default=None, help="default branch (resolved from repo if omitted)")
    p.add_argument("--repo-root", default=".", help="worktree root (gh/git cwd; default cwd)")
    p.add_argument("--merge-method", default="merge", choices=("merge", "squash", "rebase"),
                   help="auto-merge method passed to `gh pr merge --auto` (default: merge)")
    p.add_argument("--confirmed", action="store_true",
                   help="REQUIRED — confirms the human approved push + PR; refuses to run without it")
    p.add_argument("--json", action="store_true", help="emit the verdict as JSON (default: JSON)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    if not args.confirmed:
        sys.stderr.write("pr_flow: refusing to run without --confirmed "
                         "(the human push/PR confirmation gate).\n")
        return 2

    # slice-022/061: PRs base on the INTEGRATION branch (aisdlc-uat), not the released trunk.
    default = args.default or resolve_integration_branch(args.repo_root)
    if not default:
        sys.stderr.write("pr_flow: could not resolve the integration branch.\n")
        return 2

    def _flush(breadcrumb: dict) -> None:
        sys.stdout.write(json.dumps({"breadcrumb": breadcrumb}) + "\n")
        sys.stdout.flush()

    v = run_pr_flow(runner=_real_runner(args.repo_root), branch=args.branch,
                    default=default, merge_method=args.merge_method, emit=_flush)

    sys.stdout.write(json.dumps(v.as_dict()) + "\n")   # authoritative verdict = LAST line
    return 1 if v.internal_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
