"""New-agent session-restart warning audit (NAW-1) — v2.

Per **NAW-1** (`methodology-changelog.md` v0.66.0; slice-063; [[ADR-061]]).
Warns (never blocks) when a slice adds a new ``agents/*.md`` subagent file:
the Claude Code agent registry is loaded at session start, so a mid-session
write to ``agents/*.md`` is invisible to ``Agent(subagent_type=…)`` calls until
the user restarts Claude Code. NAW-1 surfaces this at ``/build-slice`` Step 6
BEFORE the next slice's chain runs.

**Read mechanism.** A naive ``git diff <base>...HEAD`` is commit-vs-commit only
and returns EMPTY for an uncommitted new agent (slice work lives in the
uncommitted working tree at Step 6 — commits land at ``/commit-slice``). The
audit therefore reads a **union of three git-derived sources** covering all
states a new agent file can occupy at Step 6:

  1. ``git diff --name-only --diff-filter=A {base} -- 'agents/*.md'``
     — working-tree-vs-base; modified + staged-but-uncommitted adds.
  2. ``git ls-files --others --exclude-standard -- 'agents/*.md'``
     — untracked-new agent files.
  3. ``git diff --name-only --diff-filter=A {base}...HEAD -- 'agents/*.md'``
     — commits-vs-base; already-committed-in-branch agents.

Results are unioned and deduplicated by path.

**v2 changes from v1.**
- v1 resolved ``base`` via a locally-defined ``_resolve_default_branch`` that
  duplicated ``branch_workflow_audit``'s logic. v2 imports
  ``resolve_default_branch`` (+ ``run_git``) from the shared
  ``scripts.lib._git_default_branch`` leaf — single source of truth. The shared
  resolver now falls back through ``main``/``master`` refs and the current branch
  (``git symbolic-ref --short HEAD``), so a fresh ``git init`` repo resolves instead
  of spuriously tripping NAW-1; ``None`` (no resolvable default branch — e.g. a
  detached-HEAD CI checkout) ⇒ exit 0 SKIPPED (no slice diff to inspect), not exit 2.
- git subprocess calls route through the shared ``run_git`` (``git -C <root>``)
  helper rather than ad-hoc ``subprocess.run(cwd=…)``.
- ``agents/*.md`` is unchanged in v2 (the agents/ directory still exists).
- The R-18 cross-reference in the WARN line now points at the v2 JSON ledger
  ``risk-register.json`` (was ``architecture/risk-register.md`` in v1).

**Test seams.** ``check(root, *, default_branch_resolver=…, added_files_resolver=…)``
exposes two injectable callables (default to the real resolvers) so a regression
suite can drive controlled fixture sets without depending on live branch state.

**Semantics** (exit contract by construction — never exit 1):
  - no added ``agents/*.md`` ⇒ exit 0, status ``"clean"``, quiet stdout.
  - ≥1 added ``agents/*.md`` ⇒ exit 0, status ``"warn"``, WARN line(s) on
    stdout (each citing agent path + session-restart instruction + R-18).
  - no default-branch diff base ⇒ exit 0, status ``"skipped"`` (NOT APPLICABLE):
    a detached-HEAD / shallow CI checkout or a repo with no default branch has no
    slice diff to inspect, so NAW-1 no-ops. NAW-1 is CI-only (1.5); a hard fail here
    would break plugin CI on every GitHub Actions detached-HEAD checkout.
  - usage error ⇒ exit 2 with stderr message (repo root unresolvable, or
    ``git`` unavailable / a git subprocess call non-zero).

**NEVER exit 1** — a new-agent slice is NOT a slice regression; the WARN is
informational, never a HALT.

Usage::

    python new_agent_warning_audit.py            # --check (default)
    python new_agent_warning_audit.py --check
    python new_agent_warning_audit.py --json
    python new_agent_warning_audit.py --root <repo-root>

Exit codes::

    0  no added `agents/*.md` (clean, quiet) OR ≥1 added (warn, stdout) OR no
       default-branch diff base (skipped / not applicable — detached-HEAD CI checkout)
    2  usage error (repo root unresolvable, `git` unavailable, or a git subprocess
       call non-zero)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402
from scripts.lib._git_default_branch import (  # noqa: E402
    resolve_default_branch,
    run_git,
)

_AGENT_PATHSPEC = "agents/*.md"

# Canonical NAW-1 WARN line — MUST cite (a) the agent path, (b) the
# session-restart-before-next-slice recommendation, and (c) the explicit R-18
# cross-reference (now the v2 JSON ledger `risk-register.json`).
_WARN_LINE_TEMPLATE = (
    "NAW-1 NEW-AGENT WARNING — this slice adds the subagent file "
    "`{agent_path}`. The Claude Code agent registry is loaded at session "
    "start; the new agent is NOT visible to `Agent(subagent_type=…)` "
    "calls in this session. Before invoking the next slice's chain, "
    "restart Claude Code so the new agent is loaded. See "
    "`risk-register.json` R-18. This is a WARN, not a slice regression."
)

_USAGE_REPO_ROOT_MISSING = "NAW-1 usage error: repo root not found: {root}"
_USAGE_GIT_MISSING = (
    "NAW-1 usage error: `git` command not found on PATH; NAW-1 cannot "
    "resolve the slice diff."
)
_USAGE_DEFAULT_BRANCH_UNRESOLVABLE = (
    "NAW-1 usage error: default branch unresolvable — `origin/HEAD`, "
    "`init.defaultBranch`, a `main`/`master` ref, AND the current branch "
    "(`git symbolic-ref --short HEAD`) all failed. Is this a git repo on a branch? "
    "Make an initial commit or set `init.defaultBranch`."
)
_USAGE_GIT_SUBCOMMAND_FAILED = (
    "NAW-1 usage error: `git {subcommand}` exited non-zero ({rc}): {stderr}"
)


class _GitMissing(Exception):
    """The git binary is unavailable on PATH."""


class _GitFailed(Exception):
    """A git subprocess call exited non-zero."""

    def __init__(self, subcommand: str, rc: int, stderr: str) -> None:
        self.subcommand = subcommand
        self.rc = rc
        self.stderr = stderr
        super().__init__(f"git {subcommand} -> {rc}: {stderr}")


def _resolve_added_agent_files(root: Path, base: str) -> list[str]:
    """Compute the union of three git-derived sets of added ``agents/*.md``
    paths covering all states a new agent file can occupy at ``/build-slice``
    Step 6.

    Routes through the shared ``run_git`` (``git -C <root> …``). Raises
    ``_GitMissing`` if the git binary is absent, or ``_GitFailed`` if any of the
    three calls exits non-zero — the caller maps both to exit-2 usage class.
    """
    found: set[str] = set()

    def _run(subcommand_args: list[str]) -> list[str]:
        try:
            proc = run_git(root, *subcommand_args)
        except FileNotFoundError as exc:  # git binary missing
            raise _GitMissing() from exc
        if proc.returncode != 0:
            raise _GitFailed(
                subcommand=" ".join(subcommand_args),
                rc=proc.returncode,
                stderr=(proc.stderr or "").strip(),
            )
        return [ln for ln in proc.stdout.splitlines() if ln.strip()]

    # Source (i): working-tree-vs-base (modified + staged adds)
    found.update(_run([
        "diff", "--name-only", "--diff-filter=A", base, "--", _AGENT_PATHSPEC,
    ]))
    # Source (ii): untracked-new
    found.update(_run([
        "ls-files", "--others", "--exclude-standard", "--", _AGENT_PATHSPEC,
    ]))
    # Source (iii): commits-vs-base (already-committed-in-branch adds)
    found.update(_run([
        "diff", "--name-only", "--diff-filter=A", f"{base}...HEAD",
        "--", _AGENT_PATHSPEC,
    ]))

    return sorted(found)


def _format_warn_line(agent_path: str) -> str:
    """Render the canonical NAW-1 WARN line for a single agent path."""
    return _WARN_LINE_TEMPLATE.format(agent_path=agent_path)


@dataclass
class CheckResult:
    status: str = "clean"            # clean | warn | usage
    exit_code: int = 0
    warnings: list[str] = field(default_factory=list)
    divergences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "warnings": self.warnings,
            "divergences": self.divergences,
        }


def check(
    root: Path,
    *,
    default_branch_resolver: Callable[[Path], str | None] = resolve_default_branch,
    added_files_resolver: Callable[[Path, str], list[str]] = _resolve_added_agent_files,
) -> CheckResult:
    """Assert no new ``agents/*.md`` files have been added in the slice's diff
    vs the resolved default branch.

    Returns a ``CheckResult`` with binary exit contract (0/2; never 1):
      - 0 status ``"clean"``: no added agents (quiet)
      - 0 status ``"warn"``: ≥1 added agents; WARN line(s) populated
      - 2 status ``"usage"``: environment/setup error

    Both resolvers are injectable for the regression suite (seam-driven
    self-application avoids branch-state-dependent assertions).
    """
    result = CheckResult()

    if not root.exists():
        result.status = "usage"
        result.exit_code = 2
        result.divergences.append(_USAGE_REPO_ROOT_MISSING.format(root=root))
        return result

    base = default_branch_resolver(root)
    if base is None:
        # No default-branch diff base — a detached-HEAD / shallow CI checkout (the normal
        # GitHub Actions state), or a fresh repo with no default branch. NAW-1 inspects a
        # SLICE diff; with no base there is no diff to inspect, so it is NOT APPLICABLE
        # (exit 0, no-op) — NOT a usage error. NAW-1 is CI-only (evicted from the per-slice
        # gate, 1.5); a hard fail here would break plugin CI on every detached-HEAD checkout.
        # (A genuinely broken env — git missing / a git subcommand failing — is still exit 2.)
        result.status = "skipped"
        result.exit_code = 0
        result.divergences.append(
            "NAW-1 not applicable: no default-branch diff base (detached-HEAD / shallow "
            "checkout, or no default branch) — nothing to diff. (no-op, exit 0)")
        return result

    try:
        added = added_files_resolver(root, base)
    except _GitMissing:
        result.status = "usage"
        result.exit_code = 2
        result.divergences.append(_USAGE_GIT_MISSING)
        return result
    except _GitFailed as e:
        result.status = "usage"
        result.exit_code = 2
        result.divergences.append(
            _USAGE_GIT_SUBCOMMAND_FAILED.format(
                subcommand=e.subcommand, rc=e.rc, stderr=e.stderr,
            )
        )
        return result

    if added:
        result.status = "warn"
        result.exit_code = 0
        for agent_path in added:
            result.warnings.append(_format_warn_line(agent_path))
        return result

    result.status = "clean"
    result.exit_code = 0
    return result


def _format_human(result: CheckResult) -> str:
    if result.status == "skipped":
        return (
            "NAW-1 new-agent warning audit: not applicable (no-op, exit 0)\n\n"
            + "".join(f"  {d}\n" for d in result.divergences)
        )
    if result.status == "usage":
        return (
            "NAW-1 new-agent warning audit: USAGE ERROR\n\n"
            + "".join(f"  {d}\n" for d in result.divergences)
        )
    if result.status == "warn":
        return (
            "NAW-1 new-agent warning audit: WARN (non-blocking; exit 0)\n\n"
            + "".join(f"  WARN: {w}\n" for w in result.warnings)
        )
    # clean → quiet stdout
    return ""


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="new_agent_warning_audit",
        description=(
            "NAW-1 — emit a non-blocking WARN at /build-slice Step 6 when the "
            "slice diff adds any `agents/*.md` file (Claude Code agent "
            "registry session-restart-before-next-slice recommendation)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for newly-added `agents/*.md` files (default action)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (default: the plugin root inferred from this script)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    if args.root is None:
        root = _REPO
    else:
        root = args.root.resolve()

    if not root.exists():
        sys.stderr.write(f"repo root not found: {root}\n")
        return 2

    result = check(root)

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        out = _format_human(result)
        if out:
            sys.stdout.write(out)

    if result.status == "usage":
        for d in result.divergences:
            sys.stderr.write(d + "\n")

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
