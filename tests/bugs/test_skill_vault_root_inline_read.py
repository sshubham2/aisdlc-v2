"""
Bug (user-reported, 2026-06-23): starting a slice raised
``FileNotFoundError: '/shippability.json'`` / ``'/candidates.json'``.

Root cause: the 4.6.1 remediation made the SessionStart hook STOP exporting
``AI_SDLC_VAULT_ROOT`` (it was leaking one repo's vault into another). 15 skills were
converted to resolve the vault per-invocation via the fallback assignment

    VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" ".../scripts/lib/_vault_paths.py" --path)}"

and then read files via ``"$VAULT/<file>"``. But ``skills/slice/SKILL.md`` (the loop
entry) was missed: Step 6 still did ``json.load(open('$AI_SDLC_VAULT_ROOT/shippability.json'))``,
so with the var unset it opened ``/shippability.json`` and crashed. Passing
``--vault "$AI_SDLC_VAULT_ROOT"`` to a script is FINE (the scripts self-resolve via
git-common-dir on an empty ``--vault``) -- only INLINE path construction from the bare,
now-unset env var breaks.

This guard scans ONLY runnable fenced blocks (```bash / ```sh / ```shell / ```! injections)
of every ``skills/*/SKILL.md`` and fails if any line builds a filesystem path from the bare
``AI_SDLC_VAULT_ROOT`` env var -- i.e.

  * ``$AI_SDLC_VAULT_ROOT/...`` or ``"$AI_SDLC_VAULT_ROOT"/...`` (bash, slash after the var),
  * ``${AI_SDLC_VAULT_ROOT}/...`` (braced, slash after the var), or
  * ``os.environ[...'AI_SDLC_VAULT_ROOT'...]`` (python env read inside a code block).

The approved ``${AI_SDLC_VAULT_ROOT:-...}`` fallback assignment is NOT flagged (it never has
``}`` or ``/`` immediately after the name), and neither is ``--vault "$AI_SDLC_VAULT_ROOT"``
(var followed by a close-quote, no slash). PROSE mentions (e.g. triage's UX-2 cautionary note
documenting the old anti-pattern, or CLAUDE.md's dev-override docs) live outside fenced code
blocks and are never scanned, so they can't false-FAIL CI.
"""
from __future__ import annotations

import re
from pathlib import Path

# <root>/tests/bugs/test_skill_vault_root_inline_read.py -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"

# Var used to build a path inline: bare or braced, then an optional close-quote, then '/'.
# The braced form requires '}' right after the name, so the ${...:-...} fallback never matches.
_BARE_PATH = re.compile(r'\$AI_SDLC_VAULT_ROOT"?/|\$\{AI_SDLC_VAULT_ROOT\}"?/')
# A python env read of the var inside a code block (os.environ['..'] / os.environ.get('..')).
_PY_ENVIRON = re.compile(r'os\.environ(?:\.get\(|\[)\s*["\']AI_SDLC_VAULT_ROOT')

_RUNNABLE_FENCES = ("bash", "sh", "shell", "!")


def _runnable_lines(text: str):
    """Yield (lineno, line) for lines INSIDE runnable fenced blocks (```bash|sh|shell|!),
    skipping pure ``#`` comment lines -- prose and blockquotes are NOT scanned."""
    in_block = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if not in_block:
                in_block = stripped[3:].strip().lower() in _RUNNABLE_FENCES
            else:
                in_block = False  # closing fence
            continue
        if in_block and not stripped.startswith("#"):  # skip pure-comment lines
            yield i, line


def _offending_lines() -> list[str]:
    """Every runnable-fence line in skills/*/SKILL.md that inline-reads the bare vault env
    var, as ``<repo-rel>:<lineno>: [why] <line>`` (sorted, deterministic)."""
    hits: list[str] = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        rel = skill_md.relative_to(_REPO_ROOT).as_posix()
        for lineno, line in _runnable_lines(skill_md.read_text(encoding="utf-8")):
            why = None
            if _BARE_PATH.search(line):
                why = "path built from bare $AI_SDLC_VAULT_ROOT (var is unset post-4.6.1)"
            elif _PY_ENVIRON.search(line):
                why = "os.environ read of AI_SDLC_VAULT_ROOT (var is unset post-4.6.1)"
            if why:
                hits.append(f"{rel}:{lineno}: [{why}] {line.strip()}")
    return hits


def test_no_inline_vault_root_read_in_skill_md():
    """No skills/*/SKILL.md runnable block may build a path from the bare AI_SDLC_VAULT_ROOT.

    Resolve the vault first via the fallback assignment
    ``VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" ".../scripts/lib/_vault_paths.py" --path)}"`` and
    read through ``"$VAULT/..."`` -- so a session whose SessionStart hook never exported the
    (intentionally de-leaked) env var still resolves the vault via git-common-dir.
    """
    offenders = _offending_lines()
    assert not offenders, (
        f"inline read of the bare AI_SDLC_VAULT_ROOT in SKILL.md runnable blocks "
        f"({len(offenders)} site(s)) -- resolve via "
        f"VAULT=\"${{AI_SDLC_VAULT_ROOT:-$(\"$PY\" \".../scripts/lib/_vault_paths.py\" --path)}}\" "
        f"then read \"$VAULT/...\":\n" + "\n".join(offenders)
    )
