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

# Output encoding robustness on Windows (cp1252 stdout): utf-8 to match the terminal,
# errors=replace so a non-ASCII path never raises UnicodeEncodeError. Our literals are
# ASCII; this covers dynamic content (paths).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PY = sys.executable                       # the bootstrap resolved this working interpreter
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
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
    no_mcp = "--no-mcp" in argv
    no_graph = "--no-graph" in argv
    repo = next((a for a in argv if not a.startswith("--")), os.getcwd())
    repo = str(Path(repo).resolve())

    if "--check" in argv:
        return do_check(repo)

    print(f"AI SDLC setup - interpreter: {PY}\n", flush=True)

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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
