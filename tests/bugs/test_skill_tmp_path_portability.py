"""
Bug (SC-042 / slice-026): SKILL.md bash blocks used non-portable temp paths that diverge
between git-bash (the writer) and the bundled Windows-Python tools (the reader), breaking
CAS ``--base-file`` / ``--content-file`` round-trips. Observed live in /archive; the same
class was live in /commit-slice (the hot ship loop), /reflect and /reduce. TWO antipatterns:

  1. a hardcoded POSIX ``/tmp/...`` path; and
  2. a bare ``mktemp`` / ``mktemp -d`` / ``mktemp -t`` with NO portable-temp prefix -- in
     git-bash these emit ``/tmp/...``, which a Windows-Python tool reads at a DIFFERENT real
     path (``C:\\tmp\\...`` / ``%TEMP%``), so the CAS base/content files never round-trip.

This guard scans ONLY ```bash fenced blocks of every ``skills/*/SKILL.md`` (skipping pure
``#`` comment lines), so a ``/tmp/`` mention in PROSE or a doc comment never false-FAILs CI
(M1: the old guard banned the bare substring on every line -- too narrow AND too broad).

Expected: every skill temp path routes through ONE portable dir the bundled Windows-Python
tools also resolve via ``tempfile.gettempdir()`` -- derived as
``TMPD="$($PY -c 'import tempfile; print(tempfile.gettempdir().replace(chr(92),"/"))')"``
then ``mktemp [-d] "$TMPD/..."`` (or reused as ``$D`` / ``$T``) -- so a git-bash write and a
Windows-Python read land on the SAME real path. The fix makes this pass; it stays the guard.
"""
from __future__ import annotations

import re
from pathlib import Path

# <root>/tests/bugs/test_skill_tmp_path_portability.py -> <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "skills"

# A mktemp is PORTABLE iff its template sits under a temp dir resolved at RUNTIME from $PY's
# tempfile.gettempdir(): $TMPD (the derived root) or $D/$T (per-run dirs made under it). NOT
# $TMPDIR -- in git-bash/MSYS2 it commonly points at the very POSIX /tmp this guard escapes
# (code-review m1), so it is deliberately NOT accepted as a portable prefix.
_PORTABLE_VARS = ("$TMPD", "${TMPD}", "$D", "${D}", "$T", "${T}")
_MKTEMP = re.compile(r"\bmktemp\b")
# A literal POSIX /tmp/ path -- NOT preceded by a word char or '$' (so "$TMPDIR/" / a var
# whose name ends in ...tmp never matches; only a bare hardcoded /tmp/ does).
_TMP_LITERAL = re.compile(r"(?<![\w$])/tmp/")


def _bash_lines(text: str):
    """Yield (lineno, line) for lines INSIDE ```bash|sh|shell fenced blocks only, skipping
    pure ``#`` comment lines -- prose, blockquotes, and doc comments are NOT scanned, so a
    ``/tmp/`` mention outside a runnable command can never false-FAIL (M1)."""
    in_bash = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if not in_bash:
                in_bash = stripped[3:].strip().lower() in ("bash", "sh", "shell")
            else:
                in_bash = False  # closing fence
            continue
        if in_bash and not stripped.startswith("#"):  # skip pure-comment lines
            yield i, line


def _is_portable_mktemp(line: str) -> bool:
    return any(v in line for v in _PORTABLE_VARS)


def _offending_lines() -> list[str]:
    """Every bash-fence line in skills/*/SKILL.md with a non-portable temp usage, as
    ``<repo-rel>:<lineno>: [why] <line>`` (sorted, deterministic)."""
    hits: list[str] = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        rel = skill_md.relative_to(_REPO_ROOT).as_posix()
        for lineno, line in _bash_lines(skill_md.read_text(encoding="utf-8")):
            why = None
            if _TMP_LITERAL.search(line):
                why = "hardcoded /tmp/ path"
            elif _MKTEMP.search(line) and not _is_portable_mktemp(line):
                why = "non-portable mktemp (no $TMPD/$TMPDIR/$D/$T prefix)"
            if why:
                hits.append(f"{rel}:{lineno}: [{why}] {line.strip()}")
    return hits


def test_no_nonportable_temp_path_in_skill_md_bash():
    """No skills/*/SKILL.md bash block may hardcode a /tmp/ path or use a bare (non-portable) mktemp."""
    offenders = _offending_lines()
    assert not offenders, (
        f"non-portable temp usage in SKILL.md bash blocks ({len(offenders)} site(s)) -- route every "
        f"temp file through one portable dir resolved by $PY tempfile.gettempdir() "
        f"(mktemp [-d] \"$TMPD/...\", reused as $D/$T):\n" + "\n".join(offenders)
    )
