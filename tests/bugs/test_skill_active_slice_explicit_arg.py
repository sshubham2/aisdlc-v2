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

Scope: slice-021 threaded critique-review (3 sites) + critique (1 site, shape-guarded). slice-031
(SC-056) extends the SAME shape-aware guard to the analytical loop trio -- risk-spike (design-spike
Step D1 BODY site), design-slice (active_slice_brief INJECTION), reflect (active_slice --json
INJECTION) -- so each is invokable as `/<skill> slice-NNN` from a main session. risk-spike derives
the id by SCANNING $ARGUMENTS (its arg0 is often --mode, so a token-scan -- not ${ARGUMENTS[0]} --
is required; see test_skill_scans_positionals_for_slice_id). active_slice_brief.py gains --slice
(delegating to resolve_slice_by_id) so design-slice's then-branch resolves. STILL NOT threaded:
supersede-slice (arg is the ARCHIVED target), code-review (forked, no arg) -- the code-acting trio
build/code-review/validate is split to SC-063. active_slice.py resolution semantics are unchanged
(only --slice threading is ADDED at call sites; active_slice_brief gains a --slice passthrough).

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

# Skills whose first slice-shaped positional is genuinely a slice id -> their active_slice resolution
# MUST thread the explicit id. critique = shape-guarded (its arg may instead be --force). slice-031
# (SC-056) added the analytical loop trio: risk-spike + design-slice were in NEITHER list before;
# reflect was in NOT_THREADED (the exclusion this slice deliberately reverses).
TARGET_SKILLS = ["critique-review", "critique", "risk-spike", "design-slice", "reflect"]
# critique-review has 3 distinct active_slice resolution sites (L20/L35/L82); all 3 must thread.
MULTISITE = {"critique-review": 3}
# Positive control: slice-story already implements the pattern (slice-story:29/:45). If it
# ever fails these checks, the CHECKS are wrong -- not the target skills.
EXEMPLAR = "slice-story"
# Documented as NOT threaded (no slice-id arg): kept here only so a future reviewer sees
# the deliberate exclusion; the test does not require them to thread. (slice-031 removed reflect --
# now threaded; supersede-slice's arg is the ARCHIVED target, code-review is forked with no arg.
# The code-acting trio build/code-review/validate is split to SC-063.)
NOT_THREADED = ["supersede-slice", "code-review"]

_ARG = r'"\$\{?ARG\}?"'                                   # "$ARG" or "${ARG}"
RE_SLICE_ARG = re.compile(r"--slice\s+" + _ARG)          # --slice "$ARG"
RE_REPO_ROOT = re.compile(r"--repo-root")                # --repo-root . | --repo-root "$repo_root"
# A guarded conditional on ARG: either [ -n "$ARG" ] or a slice-id SHAPE guard (grep ... slice-).
RE_GUARD = re.compile(r'\[\s*-n\s*' + _ARG + r'\s*\]|grep\s+-q[A-Za-z]*\s+\S*slice-')
# ONE active_slice resolution invocation = the scripts/lib path + the call's args up to its $()
# close (or EOL). Anchoring on `scripts/lib/` (M1, slice-031) matches only REAL invocations, NEVER
# back-ticked PROSE mentions of `active_slice.py` (e.g. risk-spike:240, reflect:187). `(?:_brief)?`
# also matches active_slice_brief.py (design-slice's resolver). `[^)\n]*` never crosses a line, so a
# wrapping command's flags cannot mask the inner call.
RE_INVOCATION = re.compile(r"scripts/lib/active_slice(?:_brief)?\.py[^)\n]*")

# slice-031 (m2): risk-spike's slice id can appear at ANY positional (its arg0 is often --mode), so its
# guard MUST derive ARG by SCANNING $ARGUMENTS, not reading ${ARGUMENTS[0]} alone. A build shipped
# arg0-only would PASS the per-site shape checks yet fail `/risk-spike --mode design slice-NNN`.
RE_TOKEN_SCAN = re.compile(r"for\s+\w+\s+in\s+\$ARGUMENTS")
SCAN_REQUIRED = ["risk-spike"]

# slice-031 (AC3): each newly-threaded analytical skill must advertise the optional slice-NNN arg
# in its SKILL.md frontmatter so `/<skill> slice-NNN` is discoverable.
RE_ARG_HINT = re.compile(r"^argument-hint:.*slice", re.MULTILINE | re.IGNORECASE)
ARG_HINT_REQUIRED = ["risk-spike", "design-slice", "reflect"]


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


@pytest.mark.parametrize("skill", SCAN_REQUIRED)
def test_skill_scans_positionals_for_slice_id(skill: str) -> None:
    # m2 (slice-031): arg0-only (${ARGUMENTS[0]}) silently fails when a flag precedes the slice id
    # (e.g. /risk-spike --mode design slice-NNN). Require a $ARGUMENTS token-scan (bug-hunt:85 /
    # diagnose:49 idiom) so the id is found at any positional.
    text = _read(skill)
    assert RE_TOKEN_SCAN.search(text), (
        f"{skill}/SKILL.md must derive the explicit slice arg by SCANNING $ARGUMENTS "
        f"(for a in $ARGUMENTS ...), not ${{ARGUMENTS[0]}} alone -- a flag may precede the slice id."
    )


@pytest.mark.parametrize("skill", ARG_HINT_REQUIRED)
def test_skill_advertises_argument_hint(skill: str) -> None:
    # AC3 (slice-031): each threaded analytical skill advertises the optional slice-NNN arg in
    # its frontmatter (argument-hint: ... slice ...), so `/<skill> slice-NNN` is discoverable.
    text = _read(skill)
    assert RE_ARG_HINT.search(text), (
        f"{skill}/SKILL.md frontmatter must carry an argument-hint advertising the slice-NNN arg (AC3)."
    )
