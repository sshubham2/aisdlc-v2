#!/usr/bin/env python3
"""/ai-sdlc:setup - dependency doctor + installer (Python implementation).

Launched by the skill's one-line bash bootstrap as:
    $PYX "${CLAUDE_SKILL_DIR}/scripts/setup.py" [--check] [--no-mcp] [--no-graph] [repo]

All logic lives here - interpreter is sys.executable (the bootstrap already resolved a
working one), and path/git/gitignore work uses os/pathlib/subprocess. The bash block is
just a launcher, so the Windows/git-bash traps don't apply. Subprocess output STREAMS to
the terminal (visible progress - the whole reason this is a skill, not the silent hook).

Modes:
  --check        print current toolchain state and exit (no changes; used for live-state)
  (default)      install deps -> verify -> register MCP -> gitignore -> build graph -> report
  --no-mcp       skip MCP registration
  --no-graph     skip the graph build
  [repo]         target repo (default: CWD)
"""
import os
import subprocess
import sys
from pathlib import Path

PY = sys.executable                       # the bootstrap resolved this working interpreter
PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# UTF8-STDOUT-1: cp1252 stdout robustness (errors=replace so a non-ASCII path never
# raises UnicodeEncodeError). _stdout is pure-stdlib and ships in the plugin, so the
# import is safe even pre-install; reconfigure runs as main()'s first statement.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402
REQ = PLUGIN_ROOT / "requirements.txt"


def _ok(code: str) -> bool:
    return subprocess.run([PY, "-c", code], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def _ver() -> str:
    try:
        return subprocess.run([PY, "-m", "code_review_graph", "--version"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _is_git(repo: str) -> bool:
    return subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _stream(cmd: list[str]) -> int:
    """Run, inheriting stdout/stderr so the user SEES progress. Returns the return code."""
    print(">> " + " ".join(cmd), flush=True)
    try:
        return subprocess.run(cmd).returncode
    except Exception as exc:
        print(f"   (failed to launch: {exc})", flush=True)
        return 1


# The repo-TRACKED config files /setup creates or modifies -- ai-sdlc's OWN artifacts,
# meant to be committed (M-add-2) so declared reality-gates reach teammates + CI and the
# main tree stays clean for a later slice build (an uncommitted file here trips WT-ROOT-1,
# the pristine-main-tree check in scripts/lib/wt_root_audit.py). `.mcp.json` is deliberately
# EXCLUDED -- it is machine-specific + gitignored, so it must NOT be committed.
_SCAFFOLD_PATHS = (".aisdlc/reality-gates.json", ".gitignore")


def _uncommitted_scaffold(repo: str) -> list[str]:
    """Which of the _SCAFFOLD_PATHS are currently uncommitted (untracked OR modified).
    Empty when not a git repo or all already committed (idempotent re-run)."""
    if not _is_git(repo):
        return []
    out = []
    for rel in _SCAFFOLD_PATHS:
        r = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--", rel],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            out.append(rel)
    return out


def commit_scaffold(repo: str) -> int:
    """Stage + commit ONLY the ai-sdlc config files /setup created/modified, so they reach
    teammates + CI and don't trip WT-ROOT-1 during a later slice build. Consent lives in the
    SKILL.md; this is the mechanical actuator (`/setup --commit`).

    Guarded + idempotent -- a visible no-op (exit 0) when: not a git repo, HEAD is detached /
    mid-merge (committing there is surprising), or nothing is uncommitted. NEVER `git add -A`
    and the commit is pathspec-scoped, so the user's OTHER staged work is never swept in."""
    if not _is_git(repo):
        print("commit skipped: not a git repo (nothing to commit).", flush=True)
        return 0
    if subprocess.run(["git", "-C", repo, "symbolic-ref", "-q", "HEAD"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        print("commit skipped: HEAD is detached / mid-merge -- commit the ai-sdlc config "
              "manually once you are back on a branch.", flush=True)
        return 0
    paths = _uncommitted_scaffold(repo)
    if not paths:
        print("commit skipped: ai-sdlc config already committed (nothing to do).", flush=True)
        return 0
    if subprocess.run(["git", "-C", repo, "add", "--", *paths]).returncode != 0:
        print("commit FAILED: could not stage the ai-sdlc config.", flush=True)
        return 1
    msg = ("chore(ai-sdlc): commit setup scaffolding\n\n"
           "Commits ai-sdlc's own config so declared reality-gates travel to teammates + CI\n"
           "and the tree stays clean for slice builds (WT-ROOT-1):\n"
           "  " + ", ".join(paths))
    # Pathspec-scoped commit: records ONLY these paths, ignoring any other staged changes.
    rc = subprocess.run(["git", "-C", repo, "commit", "-m", msg, "--", *paths]).returncode
    if rc != 0:
        print("commit FAILED: `git commit` returned non-zero (see output above).", flush=True)
        return 1
    print("committed ai-sdlc config: " + ", ".join(paths), flush=True)
    return 0


def hook_health() -> tuple[bool, list[str]]:
    """Diagnose whether the SessionStart hook set its env vars cleanly THIS session.
    A skill cannot write $CLAUDE_ENV_FILE (it is handed only to hooks), so /setup can
    only DIAGNOSE these -- repairing them needs the hook to re-run, i.e. a restart.

    4.6.1: the hook exports ONLY PY + CRG. AI_SDLC_VAULT_ROOT is deliberately NOT
    exported (skills resolve the vault per-invocation), so UNSET is the healthy norm --
    flagging it made every healthy install read DEGRADED (2026-07 review sweep). Only a
    set-but-BOM'd legacy value is an issue."""
    issues = []
    py = os.environ.get("PY", "")
    crg = os.environ.get("CRG", "")
    if not py and not crg:
        # Name the root cause, not just the symptoms: both hook-exported vars missing
        # means the hook itself never ran.
        issues.append("SessionStart hook did NOT fire this session (PY and CRG both unset) -- "
                      "every skill that calls $PY will fail. Check the plugin's hooks/hooks.json "
                      "is installed, then RESTART Claude Code; /setup cannot re-fire the hook")
    else:
        if not py:
            issues.append("PY unset -- every skill that calls $PY will fail")
        elif not os.path.isfile(py):
            issues.append(f"PY is not a valid file ({py!r}) -- a mangled path (old-hook bug, fixed 2.5.1)")
        if not crg:
            issues.append("CRG unset -- CRG calls fall back to a bare PATH lookup (works only if on PATH)")
    if os.environ.get("AI_SDLC_VAULT_ROOT", "").startswith("\ufeff"):
        issues.append("AI_SDLC_VAULT_ROOT has a leading BOM -- old-hook bug (fixed 2.5.1)")
    return (not issues, issues)


def _vault_surface() -> None:
    """READ-ONLY surface of the resolved vault base + this-repo vault path + the persisted sync
    backend (slice-097 AC1/AC5). Writes NOTHING — every call here reads (resolve_base / git-common-dir
    / config-read), so `--check` stays side-effect-free. The base-location + backend PICKERS live in
    SKILL.md (consented AskUserQuestion); this only reports current state so a re-run is informed."""
    try:
        from scripts.lib import _sync_config
        from scripts.lib._vault_paths import (
            _read_config_at, external_store_path, git_common_dir, resolve_base,
        )
    except Exception as exc:  # pre-install / import failure: never crash the doctor
        print(f"vault       : (surface unavailable: {exc})")
        return
    print(f"vault base  : {resolve_base()}")
    common = git_common_dir()
    if common:
        pinned = _read_config_at(common)
        print(f"vault (repo): {pinned or external_store_path(common)}"
              f"{'  [pinned]' if pinned else '  [computed default]'}")
    else:
        print("vault (repo): (not a git tree — set AI_SDLC_VAULT_ROOT or run `git init` to pin)")
    try:
        cfg = _sync_config.load(common)
    except _sync_config.SyncConfigError as exc:
        print(f"sync backend: INVALID config — {exc}")
        return
    if not cfg:
        print("sync backend: not configured (sync defaults to git for back-compat; "
              "run /setup's backend picker to set local|git|s3)")
    elif cfg["backend"] == "s3":
        s3 = cfg.get("s3", {})
        endpoint = s3.get("endpoint") or "AWS S3"
        print(f"sync backend: s3 (bucket={s3.get('bucket', '?')}, endpoint={endpoint}, "
              f"region={s3.get('region', 'us-east-1')}, project={s3.get('project', '?')}; "
              f"credentials via boto3 default chain)")
    elif cfg["backend"] == "git":
        remote = cfg.get("git", {}).get("remote")
        print(f"sync backend: git{f' (remote={remote})' if remote else ''}")
    else:
        print("sync backend: local (no remote sync configured)")


def do_check(repo: str) -> int:
    print(f"interpreter : {PY}")
    print(f"python      : {sys.version.split()[0]}")
    print(f"pyyaml      : {'OK' if _ok('import yaml') else 'MISSING'}")
    crg = _ok("import code_review_graph")
    print(f"crg-module  : {_ver() if crg else 'MISSING'}")
    mcp = Path(repo) / ".mcp.json"
    registered = mcp.is_file() and "code-review-graph" in mcp.read_text(encoding="utf-8-sig", errors="replace")
    print(f"mcp         : {'registered (./.mcp.json)' if registered else 'NOT registered'}")
    print(f"graph       : {'built (./.code-review-graph)' if (Path(repo) / '.code-review-graph').is_dir() else 'NOT built'}")
    print(f"git         : {'repo' if _is_git(repo) else 'NOT a git repo'}")
    _vault_surface()
    ok, issues = hook_health()
    print(f"hook env    : {'OK (hook fired: PY + CRG set; vault resolves per-invocation by design)' if ok else 'DEGRADED'}")
    for i in issues:
        print(f"  ! {i}")
    return 0


def gitignore_mcp(repo: str) -> None:
    """Ensure .mcp.json is gitignored - the generated config hardcodes a machine-specific
    interpreter path, so committing it would break teammates / leak a local path."""
    if not _is_git(repo):
        return
    gi = Path(repo) / ".gitignore"
    existing = gi.read_text(encoding="utf-8-sig").splitlines() if gi.exists() else []
    if ".mcp.json" in [ln.strip() for ln in existing]:
        return
    with gi.open("a", encoding="utf-8") as f:
        f.write("\n# ai-sdlc: machine-specific MCP config (abs interpreter path)\n.mcp.json\n")
    print("ensured .mcp.json is gitignored")


def main(argv: list[str]) -> int:
    _stdout.reconfigure_stdout_utf8()  # UTF8-STDOUT-1
    no_mcp = "--no-mcp" in argv
    no_graph = "--no-graph" in argv
    repo = next((a for a in argv if not a.startswith("--")), os.getcwd())
    repo = str(Path(repo).resolve())

    if "--check" in argv:
        return do_check(repo)

    if "--commit" in argv:
        return commit_scaffold(repo)

    print(f"AI SDLC setup - interpreter: {PY}\n", flush=True)

    # Step 0 - scaffold the empty reality-gates manifest (slice-062 / SC-095 / ADR-059).
    # A pure repo-file write, run on the DEFAULT (non---check) path BEFORE the deps install so
    # a deps FATAL never skips it (DR-1 m3). Idempotent: never clobbers a populated manifest.
    try:
        from scripts.lib.scaffold_reality_gates import scaffold as _scaffold_reality_gates
        _rg = _scaffold_reality_gates(repo)
        print(f"reality-gates manifest : {_rg['action']} ({_rg['path']})", flush=True)
        if _rg.get("added"):
            print(f"  + seeded security gates: {', '.join(_rg['added'])} "
                  f"(guard {_rg.get('guard', 'vendored')} -> .aisdlc/gates/py_security_gate.py)", flush=True)
        # must-not-defer (b): surface a VISIBLE install hint on a Python frame; NO force-install --
        # a missing tool fails the gate loudly (TOOL-MISSING), it is never a silent pass.
        _surface = _rg.get("surface") or {}
        if _rg.get("added") or _surface.get("source") or _surface.get("deps"):
            print("  ! security gates require: python -m pip install bandit pip-audit "
                  "(not auto-installed; a missing tool fails the gate VISIBLY)", flush=True)
        if _rg.get("gitignore_hint"):
            print("  ! " + _rg["gitignore_hint"], flush=True)
    except Exception as exc:  # visible, non-fatal: the runner no-ops without a manifest anyway
        print(f"reality-gates manifest : scaffold skipped ({exc})", flush=True)

    # Step 1 - install deps (VISIBLE)
    if not REQ.is_file():
        print(f"FATAL: requirements.txt not found at {REQ}", flush=True)
        return 1
    rc = _stream([PY, "-m", "pip", "install", "-r", str(REQ)])
    if rc != 0:
        rc = _stream([PY, "-m", "pip", "install", "--user", "-r", str(REQ)])
    if rc != 0:
        print("\nFATAL: dependency install failed (offline / externally-managed env / no pip).\n"
              "Create a venv and pin AI_SDLC_PY (forward slashes), or run "
              "`pip install -r requirements.txt` manually, then re-run /ai-sdlc:setup.", flush=True)
        return 1

    # Step 2 - verify
    ok_yaml, ok_crg = _ok("import yaml"), _ok("import code_review_graph")
    if not (ok_yaml and ok_crg):
        print(f"\nFATAL: deps did not import after install (pyyaml={ok_yaml}, crg={ok_crg}). "
              "Re-read the pip output above.", flush=True)
        return 1
    crg_ver = _ver()

    # Step 3 - register the code-review-graph MCP server
    mcp_status = "skipped (--no-mcp)"
    if not no_mcp:
        # 4.3 supply-chain trust boundary: code-review-graph is THIRD-PARTY
        # (github.com/tirth8205/code-review-graph, not this author's) and we are about to
        # register it as a TRUSTED MCP server. Surface the exact resolved version FIRST so
        # the user can vet what they're trusting (requirements.txt bounds it to >=2.3,<3).
        print(f"\ncode-review-graph is third-party (github.com/tirth8205/code-review-graph); "
              f"resolved version: {crg_ver}", flush=True)
        print("  -> registering it as a project-scoped MCP server; approve the trust prompt.",
              flush=True)
        if not _is_git(repo):
            print(f"NOTE: {repo} is not a git repo. CRG works best in one (run `git init`).", flush=True)
        rc = _stream([PY, "-m", "code_review_graph", "install", "--platform", "claude-code",
                      "--repo", repo, "--no-instructions", "--no-skills", "--no-hooks", "-y"])
        if rc == 0 and (Path(repo) / ".mcp.json").is_file():
            gitignore_mcp(repo)
            mcp_status = "registered (./.mcp.json, gitignored)"
        else:
            mcp_status = "FAILED - see output above"

    # Step 4 - build the code graph
    graph_status = "skipped (--no-graph)"
    if not no_graph:
        rc = _stream([PY, "-m", "code_review_graph", "build", "--repo", repo])
        graph_status = "built (./.code-review-graph)" if rc == 0 else "build FAILED (rebuild later; MCP still registered)"

    # Step 5 - report + next steps
    print("\n" + "=" * 64)
    print("AI SDLC setup complete.\n")
    print(f"  interpreter        {PY}")
    print(f"  pyyaml             {'OK' if ok_yaml else 'MISSING'}")
    print(f"  code-review-graph  {crg_ver or 'OK'}")
    print(f"  MCP                {mcp_status}")
    print(f"  graph              {graph_status}")
    print("""
NEXT STEPS
  1. RESTART Claude Code. The code-review-graph MCP server is read at startup - it will NOT
     appear in this session no matter what. (If CRG was only just installed now, this restart
     is the "second launch" that finally lets the MCP server start.)
  2. On restart, APPROVE the one-time trust prompt for `code-review-graph` (stdio servers need
     consent). Confirm with /mcp - it should show "connected".
  3. Then start the pipeline:  /triage (greenfield) | /adopt (brownfield) | /pulse (state).""")

    # Step 5.5 - surface uncommitted ai-sdlc config so the SKILL.md can offer to commit it.
    # This block's marker line ("UNCOMMITTED AI-SDLC CONFIG") is what the skill keys its
    # consent gate on; committing runs `/setup --commit` (the commit_scaffold actuator).
    scaffold_paths = _uncommitted_scaffold(repo)
    if scaffold_paths:
        print("\n" + "-" * 64)
        print("UNCOMMITTED AI-SDLC CONFIG (commit before your first slice):")
        for p in scaffold_paths:
            print(f"  ? {p}")
        print("\nThese are ai-sdlc's OWN config files. Committing them sends your declared")
        print("reality-gates to teammates + CI and keeps the main tree clean for slice builds")
        print("(an uncommitted file here trips the WT-ROOT-1 pristine-tree check). `.mcp.json`")
        print("is intentionally left gitignored -- its interpreter path is machine-specific.")
        print("To commit now:  /setup --commit   (or git add + commit them yourself).")

    ok, issues = hook_health()
    if not ok:
        print("\n" + "-" * 64)
        print("HOOK ENV: DEGRADED -- the SessionStart hook did not set its vars cleanly this session:")
        for i in issues:
            print(f"  ! {i}")
        print("\n/setup self-resolved its own interpreter and ran fine, but OTHER skills rely on")
        print("$PY / $CRG / $AI_SDLC_VAULT_ROOT. A skill CANNOT set these for the session -- only the")
        print("SessionStart hook can, at startup. To fix the whole session:")
        print("  1. RESTART Claude Code so the hook re-runs.")
        print("  2. If still broken after restart: reinstall the plugin (/plugin -> ai-sdlc) and/or")
        print("     set AI_SDLC_PY to a Python 3 with FORWARD slashes before launching Claude Code.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
