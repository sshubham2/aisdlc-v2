#!/usr/bin/env python3
"""ai-sdlc SessionStart env resolver - the Python implementation behind the tiny
`hooks/setup-env.sh` bootstrap shim.

Why Python, not bash: every resolution detail here (path normalization, env-file
quoting, dep probing, encoding) is a place where bash-on-Windows breaks -
`${var//\\//}` is a no-op in git-bash, `printf %q` + env-file sourcing eats
backslashes (`C:\\Users` -> `C:Users`), PowerShell-written config files carry a
UTF-8 BOM, and the PATH `C:`-vs-`:` separator collides. In Python these are
`str.replace`, `shlex.quote`, `find_spec`, and `utf-8`/`-sig` - no traps.

Contract (identical OUTPUTS to the old bash hook, so nothing downstream changes):
  - Persists POSIX `export` lines to $CLAUDE_ENV_FILE (git-bash sources it):
      export PY=<forward-slash abs interpreter>
      export CRG=<forward-slash abs code-review-graph entry point>   (when resolvable)
      export AI_SDLC_VAULT_ROOT=<forward-slash vault root>           (when resolvable)
  - Install moved to /ai-sdlc:setup. Here we only PROBE deps; if missing, emit a
    one-line nudge to stdout (SessionStart context Claude relays). Opt back into the
    old silent install with AI_SDLC_AUTO_INSTALL=1.
  - Fail-soft: never raises out, never blocks the session. Stdlib only.
"""
import os
import shlex
import shutil
import subprocess
import sys

# Output encoding robustness on Windows (cp1252 stdout): utf-8 to match the terminal,
# errors=replace so a non-ASCII path never raises UnicodeEncodeError. Our literals are
# ASCII; this covers dynamic content (paths).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

NUDGE_NO_PY = (
    "ai-sdlc: SETUP REQUIRED - no Python 3 found. Install Python 3 (or set AI_SDLC_PY "
    "to one, with forward slashes), then run /ai-sdlc:setup."
)
NUDGE_DEPS = (
    "ai-sdlc: SETUP REQUIRED - Python deps (PyYAML / code-review-graph) are not installed "
    "for $PY. Tell the user to run /ai-sdlc:setup - it installs the deps with visible progress, "
    "registers the code-review-graph MCP server, and builds the code graph - then restart Claude Code."
)
DEP_PROBE = ('import importlib.util as u, sys; '
             'sys.exit(0 if u.find_spec("yaml") and u.find_spec("code_review_graph") else 1)')


def _fwd(p: str) -> str:
    """Backslashes -> forward slashes. git-bash and Windows Python both accept forward
    slashes, and they have nothing to escape - so a forward-slash path survives the
    env-file round-trip that mangles a backslash one."""
    return (p or "").replace("\\", "/")


def _which(cand: str | None) -> str | None:
    if not cand:
        return None
    return shutil.which(cand) or (cand if os.path.isfile(cand) else None)


def _run_ok(interp: str, code: str, timeout: int = 20) -> bool:
    try:
        return subprocess.run([interp, "-c", code], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except Exception:
        return False


def resolve_interpreter() -> str | None:
    """AI_SDLC_PY -> python3 -> python -> py -> sys.executable; prefer one that can
    import yaml (only /diagnose needs it), else the first that exists."""
    seen: list[str] = []
    for cand in (os.environ.get("AI_SDLC_PY"), "python3", "python", "py", sys.executable):
        p = _which(cand)
        if p and p not in seen:
            seen.append(p)
    for p in seen:
        if _run_ok(p, "import yaml"):
            return p
    return seen[0] if seen else None


def resolve_crg(interp: str) -> str | None:
    """Absolute path to the code-review-graph entry point in INTERP's scripts dir
    (AI_SDLC_CRG override -> <scripts>/code-review-graph[.exe] -> bare on PATH)."""
    override = os.environ.get("AI_SDLC_CRG")
    if override:
        return override
    scripts = ""
    try:
        scripts = _fwd(subprocess.run(
            [interp, "-c", "import sysconfig; print(sysconfig.get_path('scripts'))"],
            capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:
        pass
    if scripts:
        for name in ("code-review-graph.exe", "code-review-graph"):
            cand = f"{scripts}/{name}"
            if os.path.isfile(cand):
                return cand
    return "code-review-graph" if shutil.which("code-review-graph") else None


def resolve_vault_root(interp: str, plugin_root: str) -> str | None:
    """Delegate to the leaf resolver `_vault_paths.py --path` (the established
    interface). Captured as BYTES, decoded utf-8, leading BOM stripped defensively."""
    vp = os.path.join(plugin_root, "scripts", "lib", "_vault_paths.py")
    if not os.path.isfile(vp):
        return None
    try:
        out = subprocess.run([interp, vp, "--path"], capture_output=True, timeout=25)
        if out.returncode != 0:
            return None
        val = _fwd(out.stdout.decode("utf-8", "replace").lstrip("\ufeff").strip())
        return val or None
    except Exception:
        return None


def main() -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return  # not a SessionStart context - nothing to persist

    here = os.path.dirname(os.path.abspath(__file__))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(here)

    chosen = resolve_interpreter()
    if not chosen:
        sys.stdout.write(NUDGE_NO_PY + "\n")
        return
    chosen = _fwd(chosen)

    lines = [f"export PY={shlex.quote(chosen)}\n"]

    # --- dependency status: PROBE (install lives in /ai-sdlc:setup) -------------------------
    deps_ok = _run_ok(chosen, DEP_PROBE)
    if not deps_ok and os.environ.get("AI_SDLC_AUTO_INSTALL") == "1":
        req = os.path.join(plugin_root, "requirements.txt")
        if os.path.isfile(req):
            for extra in ([], ["--user"]):
                try:
                    rc = subprocess.run([chosen, "-m", "pip", "install", *extra, "-r", req],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=600).returncode
                    if rc == 0:
                        deps_ok = True
                        break
                except Exception:
                    pass

    crg = resolve_crg(chosen)
    if crg:
        lines.append(f"export CRG={shlex.quote(_fwd(crg))}\n")

    vault = resolve_vault_root(chosen, plugin_root)
    if vault:
        lines.append(f"export AI_SDLC_VAULT_ROOT={shlex.quote(vault)}\n")

    try:
        with open(env_file, "a", encoding="utf-8") as f:  # utf-8, NO bom
            f.writelines(lines)
    except OSError:
        pass

    if not deps_ok:
        sys.stdout.write(NUDGE_DEPS + "\n")  # stdout = SessionStart context for Claude


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-soft: never block the session
