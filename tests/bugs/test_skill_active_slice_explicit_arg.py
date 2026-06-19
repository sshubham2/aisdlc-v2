"""
Bug (SC-030): slice-targeting skills' SKILL.md `!`-injection / state-loading blocks
resolve the active slice by calling scripts/lib/active_slice.py WITHOUT threading the
caller's explicit slice argument (`--slice "$ARG"`).

slice-014 hardened active_slice to FAIL CLOSED (exit 4 AMBIGUOUS) when >=2 slices are
in flight and the call site cannot disambiguate from the master tree. A skill whose
injection runs `active_slice.py --json` / `--path-only` with NO `--slice "$ARG"` fallback
aborts its OWN preamble on that exit-4 EVEN WHEN the caller passed `/skill slice-NNN` --
the explicit id is never used. Live: /critique-review aborted on skill load during
slice-015 with slice-016/017 in flight (critique-review/SKILL.md lines 20/35/82).

Scope (after the slice-021 dual-Critic): the fix threads ONLY the skills whose
`${ARGUMENTS[0]}` is genuinely a slice id -- critique-review (3 sites) and critique (1
site, shape-guarded so a `--force` flag is NOT mis-read as a slice id). The other
slice-targeting skills are NOT threaded: supersede-slice's arg is the ARCHIVED target,
reflect takes no arg, code-review is forked with no arg; their parallel-slice remedy is
run-from-the-worktree (out of scope for threading). active_slice.py is unchanged.

The CORRECT, already-shipped pattern is skills/slice-story/SKILL.md:29 (the positive
control here) --
    if [ -n "$ARG" ]; then ... active_slice.py ... --slice "$ARG" ...;
    else                     ... active_slice.py ... --repo-root . ...; fi
i.e. thread the explicit id when present, and keep the slice-014 fail-closed
`--repo-root` resolution as the no-arg fallback.

M1 (slice-021 first-Critic): an EARLIER version of this test used file-GLOBAL regexes,
so threading only critique-review:L20 greened it while L35/L82 still aborted. This test
is PER-SITE: it asserts NO active_slice.py invocation is left "naked" (a call carrying
neither `--slice "$ARG"` NOR a `--repo-root` fallback -- the exact tell of a partial fix),
that the explicit-arg thread is present, that a guarded fallback exists, and -- for the
multi-site critique-review -- that ALL THREE sites are threaded.

Expected: every active_slice resolution site in critique-review + critique is either
          arg-threaded (`--slice "$ARG"`, guarded) or carries a `--repo-root` fallback;
          critique-review has >=3 threaded sites; slice-story stays the passing control.
Actual (pre-fix): critique-review L20/L35/L82 and critique:26 lack `--slice "$ARG"`
          (L20/L35/L82 are NAKED), so the explicit id is ignored and the skill aborts
          under >=2 active slices.

This is a markdown-config audit: skill-load happens in the harness and cannot be driven
from pytest, so a faithful repro reads the SKILL.md text and asserts the active_slice
invocations are arg-threaded. Pairs with the SHIP-014 family test_active_slice_parallel_mispick.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = <repo>/tests/bugs/<thisfile> -> parents[2] (matches
# tests/bugs/test_active_slice_parallel_mispick.py). Works from the slice worktree too.
REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"

# Skills whose ${ARGUMENTS[0]} is genuinely a slice id -> their active_slice resolution
# MUST thread the explicit id. critique = shape-guarded (its arg may instead be --force).
TARGET_SKILLS = ["critique-review", "critique"]
# critique-review has 3 distinct active_slice resolution sites (L20/L35/L82); all 3 must thread.
MULTISITE = {"critique-review": 3}
# Positive control: slice-story already implements the pattern (slice-story:29/:45). If it
# ever fails these checks, the CHECKS are wrong -- not the target skills.
EXEMPLAR = "slice-story"
# Documented as NOT threaded (no slice-id arg): kept here only so a future reviewer sees
# the deliberate exclusion; the test does not require them to thread.
NOT_THREADED = ["reflect", "supersede-slice", "code-review"]

_ARG = r'"\$\{?ARG\}?"'                                   # "$ARG" or "${ARG}"
RE_SLICE_ARG = re.compile(r"--slice\s+" + _ARG)          # --slice "$ARG"
RE_REPO_ROOT = re.compile(r"--repo-root")                # --repo-root . | --repo-root "$repo_root"
# A guarded conditional on ARG: either [ -n "$ARG" ] or a slice-id SHAPE guard (grep ... slice-).
RE_GUARD = re.compile(r'\[\s*-n\s*' + _ARG + r'\s*\]|grep\s+-q[A-Za-z]*\s+\S*slice-')
# ONE active_slice.py invocation = the call + its args up to the close of its $() (or EOL).
# `[^)\n]*` stops at the call's own ) and never crosses a line, so a wrapping command's flags
# (e.g. project_frame_synth's --repo-root . on the same line) cannot mask the inner call.
RE_INVOCATION = re.compile(r"active_slice\.py[^)\n]*")


def _read(skill: str) -> str:
    p = SKILLS / skill / "SKILL.md"
    assert p.exists(), f"missing {p}"
    return p.read_text(encoding="utf-8")


def _invocations(text: str) -> list[str]:
    return RE_INVOCATION.findall(text)


def _is_naked(inv: str) -> bool:
    """A naked invocation carries NEITHER an explicit --slice "$ARG" NOR a --repo-root
    fallback -- the exact signature of an un-threaded (or partially-threaded) site."""
    return not RE_SLICE_ARG.search(inv) and not RE_REPO_ROOT.search(inv)


def _problems(skill: str, *, require_thread: bool) -> list[str]:
    text = _read(skill)
    invs = _invocations(text)
    out: list[str] = []
    if not invs:
        out.append("no active_slice.py invocation (not a slice-targeting skill?)")
        return out
    naked = [inv.strip() for inv in invs if _is_naked(inv)]
    if naked:
        out.append(f"{len(naked)} NAKED active_slice invocation(s) (no --slice \"$ARG\" and no "
                   f"--repo-root fallback): {naked}")
    if require_thread:
        if not any(RE_SLICE_ARG.search(inv) for inv in invs):
            out.append('no active_slice.py call threads --slice "$ARG"')
        if not any(RE_REPO_ROOT.search(inv) for inv in invs):
            out.append("no active_slice.py call retains a --repo-root fallback (no-arg HALT path)")
        if not RE_GUARD.search(text):
            out.append('no ARG guard ([ -n "$ARG" ] or a slice-id shape guard) found')
        need = MULTISITE.get(skill)
        if need is not None:
            got = sum(1 for inv in invs if RE_SLICE_ARG.search(inv))
            if got < need:
                out.append(f"only {got}/{need} active_slice sites thread --slice \"$ARG\" "
                           f"(a partial fix leaves the rest aborting under parallel slices)")
    return out


def test_exemplar_slice_story_threads_explicit_arg() -> None:
    # The slice-story:29/:45 pattern must satisfy every check, else the checks are broken.
    assert _problems(EXEMPLAR, require_thread=True) == [], _problems(EXEMPLAR, require_thread=True)


@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_skill_threads_explicit_slice_arg(skill: str) -> None:
    problems = _problems(skill, require_thread=True)
    assert not problems, (
        f"{skill}/SKILL.md does not thread the explicit slice arg per-site "
        f"(slice-story:29 pattern). Problems: " + "; ".join(problems)
    )
