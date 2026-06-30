"""runnable_command — the single source of truth for 'is a vault-stored command portable-runnable?'
(slice-046 / SC-081, ADR-035).

A vault-stored runnable command (a shippability `machine_cmd`, and — once SC-083 lands — a
walking_skeleton `architectural_layers[].verification`) must be **interpreter-anchored** so the gate
that runs it does not depend on the ambient PATH namespace. A bare `pytest` console-script resolves
only through PATH (absent on Windows / a venv whose Scripts dir is off PATH — this pipeline's
documented dev setup), so it false-FAILs the reality-touching gates; the `<interp> -m pytest` form
runs against the named interpreter regardless of PATH (Brett Cannon's `python -m` principle).

This module PARSES a single already-`;`-split segment into a typed verdict (parse-don't-validate —
Alexis King): the verdict's `klass` is the single thing both SCMD-1 and a future WS-1 gate branch on,
and it carries a human `reason` so each consumer logs WHY a command was rejected without re-deriving
the explanation. The three classes map exactly to the three distinguishable outcomes the slice's
must_not_defer requires: portable / structured-but-non-portable (bare console-script) / prose.

The grammar is LIFTED from shippability_decoupling_audit (its `_INTERP` + `_SEGMENT_RE` +
`_NONPYTEST_CMD_RE`) and NARROWED by exactly one production: the pytest form's interpreter prefix,
which `_SEGMENT_RE` made OPTIONAL (that optionality accepted the bare console-script — the bug), is
now MANDATORY (`_PORTABLE_PYTEST_RE`). The bare form is split out into `_BARE_PYTEST_RE` so it can be
classified as non-portable rather than silently accepted. `_NONPYTEST_CMD_RE` is preserved verbatim
so interpreter-led prose (`python -c just inspect it by hand`) stays `not_a_command` — i.e. this
validator SUBSUMES the slice-011 prose-rejection grammar, it does not weaken it (critique M1).

STATIC and deterministic: form only — no `shutil.which`, no PATH probe, no subprocess. Reality
contact (actually running the command) stays where it belongs (the SRSC-1 runner; WS-1 `--execute`
in SC-083). Classifying a segment never raises — an unparseable/ambiguous form is the `not_a_command`
verdict, never a silent accept and never an exception to the caller (fail-visible).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Verdict classes (a small closed enum; the single thing consumers branch on).
PORTABLE = "portable"
NON_PORTABLE_CONSOLE_SCRIPT = "non_portable_console_script"
NOT_A_COMMAND = "not_a_command"

# A pinned interpreter token: the literal `<interp>` placeholder, `python` / `python3`, or any
# path ending in `python[3][.exe]`. Lifted verbatim from shippability_decoupling_audit._INTERP.
_INTERP = r"(?:<interp>|python3?|[^\s;]*python(?:3)?(?:\.exe)?)"

# Portable pytest invocation: interpreter-anchored, `-m pytest`, a tests/ target, trailing args.
# This is shippability_decoupling_audit._SEGMENT_RE with the interpreter prefix made MANDATORY
# (the `(?:...)?` around the prefix is removed) — the one-production narrowing (ADR-035).
_PORTABLE_PYTEST_RE = re.compile(
    rf"^{_INTERP}(?:\s+-W\s+\S+)?\s+-m\s+pytest\s+"
    r"(?:tests/\S+?\.py(?:::\S+)?|tests/\S*)"
    r"(?:\s+\S+)*$"
)

# Bare `pytest` console-script (no interpreter prefix) — the NON-PORTABLE form: same tests/ target
# shape as the portable form but launched via the ambient-PATH console-script. Split out so it is
# classified, not accepted.
_BARE_PYTEST_RE = re.compile(
    r"^pytest\s+"
    r"(?:tests/\S+?\.py(?:::\S+)?|tests/\S*)"
    r"(?:\s+\S+)*$"
)

# Non-pytest interpreter command — also portable (already interpreter-led): `<interp> -c "<quoted
# code>"` (the code MUST be a single quoted token, so bare `-c <free text>` is rejected as prose) or
# `<interp> <script>.py [args]` where each trailing arg is flag- or path-like (a bare-word arg like
# `and then review` is rejected as prose). Lifted VERBATIM from shippability_decoupling_audit._NONPYTEST_CMD_RE
# so the slice-011 _REJECTED_PROSE behaviour is preserved (critique M1: subsume, don't weaken).
_NONPYTEST_CMD_RE = re.compile(
    rf"^{_INTERP}\s+"
    r"(?:"
    r"-c\s+(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')(?:\s+(?:-\S+|\S*[/\\.=:]\S*))*"
    r"|"
    r"\S+\.py(?:\s+(?:-\S+|\S*[/\\.=:]\S*))*"
    r")\s*$"
)

_BARE_PYTEST_REASON = (
    "bare `pytest` console-script depends on the ambient PATH (absent on Windows / a venv whose "
    "Scripts dir is off PATH); use the interpreter-anchored `<interp> -m pytest ...` form so it runs "
    "against the named interpreter regardless of PATH"
)
_NOT_A_COMMAND_REASON = (
    "not an interpreter-anchored command (prose/narrative or unparseable); expected "
    "`<interp> -m pytest ...`, `<interp> -c \"...\"`, or `<interp> <script>.py [flag/path args]`"
)


@dataclass(frozen=True)
class CommandVerdict:
    """The typed result of classifying one command segment.

    `klass` is one of {PORTABLE, NON_PORTABLE_CONSOLE_SCRIPT, NOT_A_COMMAND}; `reason` is empty for
    PORTABLE and a human explanation otherwise (so a consumer logs WHY without re-classifying).
    """
    klass: str
    reason: str = ""

    @property
    def is_portable(self) -> bool:
        return self.klass == PORTABLE


def classify(segment: str) -> CommandVerdict:
    """Classify ONE already-`;`-split command segment. Never raises.

    The caller is expected to pass a single top-level segment (e.g. from
    shippability_decoupling_audit._segments, which is quote/escape-aware); a surrounding backtick
    fence + whitespace are stripped defensively here too.
    """
    seg = segment.strip().strip("`").strip()
    if not seg:
        return CommandVerdict(NOT_A_COMMAND, _NOT_A_COMMAND_REASON)
    if _PORTABLE_PYTEST_RE.fullmatch(seg) or _NONPYTEST_CMD_RE.fullmatch(seg):
        return CommandVerdict(PORTABLE)
    if _BARE_PYTEST_RE.fullmatch(seg):
        return CommandVerdict(NON_PORTABLE_CONSOLE_SCRIPT, _BARE_PYTEST_REASON)
    return CommandVerdict(NOT_A_COMMAND, _NOT_A_COMMAND_REASON)
