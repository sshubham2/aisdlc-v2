#!/usr/bin/env python3
"""ai-sdlc SessionStart env resolver - the Python implementation behind the tiny
`hooks/setup-env.sh` bootstrap shim.

Why Python, not bash: every resolution detail here (path normalization, env-file
quoting, dep probing, encoding) is a place where bash-on-Windows breaks -
`${var//\\//}` is a no-op in git-bash, `printf %q` + env-file sourcing eats
backslashes (`C:\\Users` -> `C:Users`), PowerShell-written config files carry a
UTF-8 BOM, and the PATH `C:`-vs-`:` separator collides. In Python these are
`str.replace`, `shlex.quote`, `find_spec`, and `utf-8`/`-sig` - no traps.

Contract (same exported vars as the old hook, so nothing downstream changes):
  - Persists POSIX `export` lines to $CLAUDE_ENV_FILE (git-bash sources it) inside ONE
    marked, idempotently-rewritten managed block (4.6.2 — the old `open(..., "a")` appended
    duplicates on every clear/compact since the file persists across re-fires):
      # >>> ai-sdlc managed (PY/CRG/vault) >>>
      export PY=<forward-slash abs interpreter>
      export CRG=<forward-slash abs code-review-graph entry point>   (when resolvable)
      export AI_SDLC_VAULT_ROOT=<forward-slash vault root>           (when resolvable)
      # <<< ai-sdlc managed <<<
    Non-ai-sdlc lines other tools wrote to the shared file are preserved.
  - 4.6.3 short-circuit: a re-fire whose managed block already names a PY that still exists
    returns immediately — zero re-probe subprocesses (cuts the per-clear/compact tax).
  - 3.19.8a: the deliberate `WARN` `_vault_paths` emits when keying the vault on a non-git
    cwd is RELAYED to stdout (was captured-and-discarded), so the user sees the nag.
  - 4.6.1 NOTE (deferred): the hook still exports AI_SDLC_VAULT_ROOT (a session-frozen,
    cwd-derived value). Removing it — so the vault resolves per-invocation and mid-session
    work on a different repo can't mis-route — needs ~9 bare-consumer skills moved to the
    `${AI_SDLC_VAULT_ROOT:-$($PY .../_vault_paths.py --path)}` fallback first; tracked, not done.
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

# CLAUDE_ENV_FILE is sourced before every Bash call and PERSISTS across clear/compact, so
# the old `open(..., "a")` re-appended duplicate exports on every SessionStart re-fire (4.6.2).
# We now write a single MARKED managed block and rewrite it in place each fire — idempotent,
# and it preserves any non-ai-sdlc lines other tools wrote to the shared file.
_MANAGED_START = "# >>> ai-sdlc managed (PY/CRG/vault) >>>"
_MANAGED_END = "# <<< ai-sdlc managed <<<"
_OUR_VARS = ("PY", "CRG", "AI_SDLC_VAULT_ROOT")  # also strip legacy un-markered copies on migration


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


def resolve_vault_root(interp: str, plugin_root: str) -> tuple[str | None, str | None]:
    """Delegate to the leaf resolver `_vault_paths.py --path` (the established interface).
    Returns ``(vault_path, warn_to_relay)``. Captured as BYTES, decoded utf-8, leading BOM
    stripped defensively.

    3.19.8a: `_vault_paths` deliberately emits a loud ``WARN`` on stderr when it has to key
    the vault on a NON-git cwd (the "run `git init` or set AI_SDLC_VAULT_ROOT" nag). The old
    hook captured-and-DISCARDED that stderr, swallowing the nag. We now RELAY the WARN line(s)
    to the hook's stdout (the SessionStart context) so the user actually sees it. INFO lines
    (env/config resolution) are NOT relayed \u2014 only the deliberate WARN."""
    vp = os.path.join(plugin_root, "scripts", "lib", "_vault_paths.py")
    if not os.path.isfile(vp):
        return None, None
    try:
        out = subprocess.run([interp, vp, "--path"], capture_output=True, timeout=25)
        if out.returncode != 0:
            return None, None
        val = _fwd(out.stdout.decode("utf-8", "replace").lstrip("\ufeff").strip())
        err = out.stderr.decode("utf-8", "replace")
        warn_lines = [ln for ln in err.splitlines() if "WARN" in ln]
        warn = ("\n".join(warn_lines) + "\n") if warn_lines else None
        return (val or None), warn
    except Exception:
        return None, None


def _existing_managed_py(env_file: str) -> str | None:
    """The `export PY=<path>` value inside an already-written managed block, or None.
    Used by the 4.6.3 short-circuit: a re-fire (clear/compact) whose block already names a
    PY that still exists on disk needs NO re-resolution (cheap file read + isfile, zero
    subprocesses)."""
    try:
        with open(env_file, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    in_block = False
    for line in content.splitlines():
        s = line.strip()
        if s == _MANAGED_START:
            in_block = True
            continue
        if s == _MANAGED_END:
            break
        if in_block and s.startswith("export PY="):
            try:
                parts = shlex.split(s[len("export PY="):].strip())
            except ValueError:
                return None
            return parts[0] if parts else None
    return None


def _write_env_block(env_file: str, body_lines: list[str]) -> None:
    """Rewrite the ai-sdlc managed block in CLAUDE_ENV_FILE idempotently (4.6.2). Reads the
    file, drops any prior managed block AND any legacy un-markered copies of our own vars
    (migration from the old append format), preserves every other line, then appends one
    fresh marked block. N fires -> exactly one block."""
    try:
        with open(env_file, encoding="utf-8") as f:
            existing = f.readlines()
    except OSError:
        existing = []

    def _is_ours(line: str) -> bool:
        s = line.strip()
        return any(s.startswith(f"export {v}=") for v in _OUR_VARS)

    kept: list[str] = []
    skipping = False
    for ln in existing:
        s = ln.strip()
        if s == _MANAGED_START:
            skipping = True
            continue
        if s == _MANAGED_END:
            skipping = False
            continue
        if not skipping and not _is_ours(ln):
            kept.append(ln)

    block = [_MANAGED_START + "\n", *body_lines, _MANAGED_END + "\n"]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    try:
        with open(env_file, "w", encoding="utf-8") as f:  # utf-8, NO bom
            f.writelines(kept + block)
    except OSError:
        pass


def main() -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return  # not a SessionStart context - nothing to persist

    here = os.path.dirname(os.path.abspath(__file__))
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(here)

    # 4.6.3 short-circuit: a clear/compact re-fire whose managed block already names a PY that
    # still exists needs no re-resolution — skip the ~5 probe subprocesses entirely.
    prior_py = _existing_managed_py(env_file)
    if prior_py and os.path.isfile(prior_py):
        return

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

    vault, vault_warn = resolve_vault_root(chosen, plugin_root)
    if vault:
        lines.append(f"export AI_SDLC_VAULT_ROOT={shlex.quote(vault)}\n")

    _write_env_block(env_file, lines)  # 4.6.2 idempotent managed-block write (was blind append)

    if vault_warn:
        sys.stdout.write(vault_warn)  # 3.19.8a relay the deliberate cwd-keyed vault WARN
    if not deps_ok:
        sys.stdout.write(NUDGE_DEPS + "\n")  # stdout = SessionStart context for Claude


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-soft: never block the session
