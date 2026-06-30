"""SRSC-1 shared verification execution core (slice-047 / ADR-038).

ONE fail-closed engine that runs a verification command string and returns a
TOTAL three-valued verdict (PASS | FAIL | ABSENT). Both the mature shippability
catalog runner (`shippability_runner.run_catalog`) and the walking-skeleton
checker (`brief_variants_audit._execute_verifications`) import and call this --
previously they were two divergent implementations of the same job, and the
immature one silently demoted a not-runnable verification to a non-gating
advisory (the M2 wrong-side failure this slice closes).

Layering note (the reason this lives in scripts/lib): the canonical helpers used
to live in skills/validate-slice/scripts/, which scripts/lib/ CANNOT import (off
its sys.path) -- which is exactly why brief_variants_audit re-implemented the loop
inline. Promoting them here (with re-export shims left at the old homes) is the
only layering-correct way both consumers share one engine.

Verdict semantics (parse-don't-validate -- the M2 fail-closed change and the
ADR-021 ABSENT carve-out are branches of ONE total function, not two flags):
  * ABSENT  -- the command cites >=1 `tests/...py` token AND every cited token is
    absent on `repo_root`. Decided STRICTLY pre-execution by FILE existence, never
    by a pytest exit code (exit 4 is ambiguous). A sibling slice's not-yet-merged
    repro is unobservable here, not a regression (ADR-021 / SC-021).
  * FAIL    -- any post-launch outcome that is not a clean exit-0, INCLUDING a
    not-runnable command (FileNotFoundError). `subkind` distinguishes the cause so
    a consumer can apply its own policy (e.g. ADR-038 / M-add-1: the WS consumer
    treats `not-runnable` as undecidable -> a loud advisory, everything else as a
    decidable STOP). subkind is for logging/policy ONLY -- never a 4th status.
  * PASS    -- every segment exits 0 (or the command parses to no runnable argv).

Nothing raises to the caller: shlex.ValueError / FileNotFoundError / OSError /
TimeoutExpired all collapse to a FAIL verdict, so a malformed segment NEVER bubbles
as an exit-2 catalog usage error (slice-011). Stdlib-only; no scripts.lib imports.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ── relocated: interpreter normalization (B1; was shippability_runner._normalize_interp) ──
# Tokens that introduce the canonical interpreter placeholder. SCMD-1 permits
# `<interp>` (the SKILL.md-prose convention), a bare `python`, or an absolute
# `.../python.exe`. The core normalizes them to the live interpreter so the
# command never embeds a machine-specific path.
_INTERP_TOKENS = frozenset({"<interp>", "python", "python.exe", "python3"})


def _normalize_interp(tokens: list[str]) -> list[str]:
    """Replace a leading interpreter token with the live interpreter.

    `<interp> -m pytest ...` / `python -m pytest ...` /
    `C:/.../python.exe -m pytest ...` all become `<sys.executable> -m pytest ...`.
    A bare `pytest ...` segment (no leading interpreter token) is left as-is --
    bare-pytest portability is enforced UPSTREAM by the static gate (WS) / SCMD-1
    (catalog), not by normalization (meta-Critic note, ADR-038)."""
    if not tokens:
        return tokens
    head = tokens[0]
    is_interp = (
        head in _INTERP_TOKENS
        or head.endswith("python")
        or head.endswith("python.exe")
        or head.endswith("python3")
    )
    if is_interp:
        return [sys.executable, *tokens[1:]]
    return tokens


# ── relocated: canonical segmentation (M3.2; was shippability_decoupling_audit) ──
def _split_top_level(machine_cmd: str) -> list[str]:
    """Split on `;` ONLY at quote-depth 0, honoring single-quote, double-quote,
    and POSIX backslash-escape rules so the boundaries match
    `shlex.split(posix=True)` (this core's tokenizer). A `;` inside a quoted span
    -- or a backslash-escaped `\\;` outside quotes -- is part of the command, NOT a
    separator. This is the slice-011 fix for the naive `machine_cmd.split(";")`
    that shredded a `python -c "...;...;..."` row."""
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None       # None | "'" | '"'
    escaped = False                # previous char was an unescaped backslash
    for ch in machine_cmd:
        if escaped:
            buf.append(ch)
            escaped = False
        elif quote is None:
            if ch == "\\":
                buf.append(ch)
                escaped = True
            elif ch in ("'", '"'):
                quote = ch
                buf.append(ch)
            elif ch == ";":
                out.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        elif quote == "'":          # single quotes: literal, no escapes (POSIX)
            buf.append(ch)
            if ch == "'":
                quote = None
        else:                       # double quotes: backslash escapes the next char
            if ch == "\\":
                buf.append(ch)
                escaped = True
            else:
                buf.append(ch)
                if ch == '"':
                    quote = None
    out.append("".join(buf))
    return out


def _segments(machine_cmd: str) -> list[str]:
    """Split a command into its TOP-LEVEL `;`-separated segments (quote- and
    escape-aware -- see `_split_top_level`), then strip a surrounding markdown
    backtick fence + ws from EACH segment. A `;` inside quotes is NEVER a
    separator, so a single `python -c "import sys; a=1; b=2"` is ONE segment."""
    out: list[str] = []
    for raw in _split_top_level(machine_cmd):
        seg = raw.strip().strip("`").strip()
        if seg:
            out.append(seg)
    return out


# ── relocated: pytest test-token extraction (m2; was shippability_path_audit) ──
_TEST_PATH_RE = re.compile(r"tests/\S+?\.py")
_SELECTOR_RE = re.compile(r"""\A(::[^\s`"']+)""")


def _extract_test_tokens(command: str) -> list[tuple[str, str | None]]:
    """Return `(file_token, raw_selector|None)` pairs after the `pytest` kw.

    Scope to the post-`pytest` segment so an interpreter path and `-m pytest`
    prefix are never mistaken for test paths. The `::`-selector is CAPTURED (not
    split away) so the function-level layer can verify the cited test function
    exists. The file token itself is backtick/quote-stripped and `::`-free.
    """
    idx = command.find("pytest")
    if idx == -1:
        return []
    segment = command[idx + len("pytest"):]
    pairs: list[tuple[str, str | None]] = []
    for m in _TEST_PATH_RE.finditer(segment):
        tok = m.group(0).strip("`").strip().strip('"').strip("'")
        tok = tok.split("::", 1)[0].strip()
        if not tok:
            continue
        sel_match = _SELECTOR_RE.match(segment[m.end():])
        selector = sel_match.group(1) if sel_match else None
        pairs.append((tok, selector))
    return pairs


# ── the three-valued verdict ──
_Status = Literal["PASS", "FAIL", "ABSENT"]
_STATUSES = ("PASS", "FAIL", "ABSENT")


@dataclass(frozen=True)
class ExecVerdict:
    """A total, fail-closed verdict from running ONE verification command.

    `status` is the gate-relevant outcome; `subkind` carries WHY (for logging +
    each consumer's policy -- e.g. ADR-038 routes `not-runnable` to a loud advisory
    in the WS consumer while every other FAIL subkind is a hard STOP). `subkind`
    is NEVER a fourth status."""
    status: _Status
    reason: str = ""
    subkind: str = ""

    def __post_init__(self) -> None:
        # Python can only APPROXIMATE 'make illegal states unrepresentable'
        # (runtime dataclass, not a compiler sum type) -- this assert is the
        # backstop the meta-Critic asked for (no 4th status ever constructed).
        if self.status not in _STATUSES:
            raise ValueError(
                f"ExecVerdict.status must be one of {_STATUSES}, got {self.status!r}")


def run_verification(command: str, repo_root: Path | str, *,
                     timeout: float | None = None) -> ExecVerdict:
    """Run ONE verification command string and return a three-valued ExecVerdict.

    `repo_root` is the checkout the command runs against (the ABSENT existence
    check + the subprocess cwd). It is ALWAYS supplied by the caller and NEVER
    re-derived here (the two consumers compute it differently -- run_catalog from
    the catalog path, brief_variants from --repo-root; M2)."""
    repo_root = Path(repo_root)

    # ── ABSENT pre-check (ADR-021): decided by FILE existence, pre-execution ──
    # If the command cites >=1 tests/...py token AND every cited token is absent on
    # this checkout, it is ABSENT (a sibling's not-yet-merged repro), not run, not a
    # regression. A command with a present token, or NO extractable test token
    # (e.g. a `python -c` row or a `curl` smoke), falls through to execution so a
    # real failure still FAILs.
    test_tokens = [tok for tok, _sel in _extract_test_tokens(command)]
    if test_tokens and all(not (repo_root / tok).exists() for tok in test_tokens):
        return ExecVerdict(
            "ABSENT",
            "cited test file(s) not on this checkout: " + ", ".join(test_tokens),
            "absent-tests")

    # ── execute each top-level segment; first failing segment ends the run ──
    for seg in _segments(command):
        try:
            argv = _normalize_interp(shlex.split(seg, posix=True))
        except ValueError as exc:
            # A genuinely malformed segment (e.g. an unterminated quote). FAIL
            # this command; NEVER let the ValueError bubble (slice-011 exit-2
            # catalog-abort bug).
            return ExecVerdict(
                "FAIL", f"segment is not a parseable command ({exc}): {seg!r}",
                "unparseable")
        if not argv:
            continue
        try:
            proc = subprocess.run(
                argv, cwd=str(repo_root),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",  # BB-25: avoid cp1252 reader UnicodeDecodeError
                timeout=timeout,
            )
        except FileNotFoundError:
            # The command's program is not on PATH / not installed. This is the
            # genuinely-UNDECIDABLE case (a prose phantom vs a missing foreign
            # tool look identical from the string) -- tagged so the WS consumer
            # can demote it to a loud advisory rather than a hard STOP (ADR-038).
            return ExecVerdict(
                "FAIL", f"command not found (not runnable): {seg!r}", "not-runnable")
        except subprocess.TimeoutExpired:
            return ExecVerdict(
                "FAIL", f"segment timed out after {timeout}s: {seg!r}", "timeout")
        except OSError as exc:
            return ExecVerdict(
                "FAIL", f"segment could not be executed ({exc}): {seg!r}", "exec-error")
        if proc.returncode != 0:
            tail = ((proc.stdout or "")[-500:] + (proc.stderr or "")[-500:]).strip()
            return ExecVerdict(
                "FAIL", f"segment exited {proc.returncode}: {seg!r}\n{tail}",
                "exited-nonzero")

    return ExecVerdict("PASS", "", "ok")
