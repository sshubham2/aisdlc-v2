"""
slice-036 (SC-063): thread the explicit slice-arg into the CODE-ACTING loop skills.

Sibling of test_skill_active_slice_explicit_arg.py (slice-034, which covers the ANALYTICAL skills:
critique/critique-review/design-slice/reflect/slice-story/risk-spike). This file is the COMPLEMENT --
it covers the three code-acting skills (build-slice / code-review / validate-slice) so that
`/<skill> slice-NNN` resolves the NAMED slice from a main-launched session.

Why a SIBLING file rather than extending the slice-034-owned test (m4, ratified): test ISOLATION --
this file vendors its OWN minimal fence-parser + regexes (a deliberate ~30-line copy) so it stays
merge-independent of the slice-034 file and a future edit to either cannot silently break the other.

What it pins (the slice-036 critique findings, as executable assertions):
  M1  every BODY block that invokes active_slice.py with --repo-root in build-slice MUST also carry the
      shape-guarded `--slice "$ARG"` -- ALL such sites, not count>=1 (build-slice has FOUR: the design
      had named only two). A future omission fails RED here.
  inj the active-slice resolution `!`-injections (build-slice L26/L32) must be exit-0-TOLERANT (degrade
      visibly under ambiguity, never abort the skill launch -- mirror slice-031 M-add-2). Under parallel
      slices a `--repo-root .` injection with no tolerance exit-4s and ABORTS the launch from main.
  #2  NO `!`-injection threads `--slice "$ARG"` (the inert pattern -- injections don't bind ${ARGUMENTS}).
  M2  build-slice's BRANCH-2/3 worktree-state gate keys off the NAMED slice (treats session!=slice branch
      as the expected main-launch case, not the case-3 STOP).
  M3  build-slice's /code-review handoff PASSES the resolved slice id (text presence; arg ARRIVAL at the
      forked fork is a build-time live test, not a static assertion -- slice-031: a text audit proves
      presence, never that the harness binds the arg).
  m3  the C3 `[ -d "$wt" ]` fail-visible guard precedes the cwd-dependent pre_finish_gate invocation.
  m2  validate-slice's active_slice_info.py grows a `--slice` passthrough.
  AC  argument-hint (mentioning slice) on all three SKILL.md frontmatter.

Markdown-config audit: skill-load runs in the harness and cannot be driven from pytest, so a faithful
repro reads the SKILL.md TEXT and asserts on its fenced structure (the slice-021/031/034 idiom). A text
audit proves a guard PRESENT, NOT that the harness binds ${ARGUMENTS} -- the arg-ARRIVAL claims (typed
build-slice; forked code-review) are verified live at build, not here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = <repo>/tests/bugs/<thisfile> -> parents[2]. Works from the slice worktree too.
REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"

# The three code-acting skills this slice threads. All three resolve the active slice via active_slice.py
# (build-slice + validate-slice in BODY blocks; code-review's resolution moves from a !-injection to a
# body block so the FORK binds ${ARGUMENTS}).
TARGET_SKILLS = ["build-slice", "code-review", "validate-slice"]

# ---- vendored minimal fence parser + regexes (deliberate isolation copy; m4) -----------------------
_ARG = r'"\$\{?ARG\}?"'                                       # "$ARG" or "${ARG}"
RE_SLICE_ARG = re.compile(r"--slice\s+" + _ARG)              # --slice "$ARG"  (the explicit-arg thread)
RE_REPO_ROOT = re.compile(r"--repo-root")                    # --repo-root . | --repo-root "$repo_root"
# guarded conditional on ARG: [ -n "$ARG" ] OR a slice-id shape guard (grep ... ^slice-)
RE_GUARD = re.compile(r'\[\s*-n\s*' + _ARG + r'\s*\]|grep\s+-q[A-Za-z]*\s+\S*\^?slice-')
# a REAL active_slice resolution invocation -- anchored on scripts/lib/ so a back-ticked prose mention
# never matches; (?:_brief)? also matches active_slice_brief.py.
RE_INV = re.compile(r"scripts/lib/active_slice(?:_brief)?\.py")
RE_ARG_HINT = re.compile(r"^argument-hint:.*slice", re.MULTILINE | re.IGNORECASE)
FENCE = re.compile(r"^\s*```(.*)$")


def parse_blocks(text: str) -> list[dict]:
    """Fence state-machine: split text into fenced blocks tagged by opening info-string.
    `!` => injection (load-time, NO ${ARGUMENTS} bind); bash/sh => body (step-time, binds ${ARGUMENTS});
    anything else => other."""
    blocks: list[dict] = []
    info: str | None = None
    start = 0
    buf: list[str] = []
    for ln, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if info is None:
                info, start, buf = m.group(1).strip(), ln, []
            else:
                toks = info.split()
                phase = ("injection" if info.startswith("!")
                         else "body" if (toks and toks[0] in ("bash", "sh"))
                         else "other")
                blocks.append({"phase": phase, "start": start, "end": ln, "text": "\n".join(buf)})
                info = None
        elif info is not None:
            buf.append(line)
    return blocks


def _read(skill: str) -> str:
    p = SKILLS / skill / "SKILL.md"
    assert p.exists(), f"missing {p}"
    return p.read_text(encoding="utf-8")


def _blocks(skill: str) -> list[dict]:
    return parse_blocks(_read(skill))


def _active_slice_repo_root_body_blocks(skill: str) -> list[dict]:
    """Body blocks that invoke active_slice(.py) with --repo-root (the resolution sites this slice guards)."""
    return [b for b in _blocks(skill)
            if b["phase"] == "body" and RE_INV.search(b["text"]) and RE_REPO_ROOT.search(b["text"])]


# --------------------------------------------------------------------------------------------------------
# Parser self-check against a SYNTHETIC fixture (the oracle is never also a subject).
# --------------------------------------------------------------------------------------------------------
_FIXTURE = """
prose line
```!
SDIR="$(... active_slice.py --repo-root . --path-only)"
```
```bash
ARG="${ARGUMENTS[0]:-}"
if printf '%s' "$ARG" | grep -qE '^slice-[0-9]'; then
  X="$(... scripts/lib/active_slice.py --slice "$ARG" --folder-only)"
else
  X="$(... scripts/lib/active_slice.py --repo-root "$repo_root" --folder-only)"
fi
```
"""


def test_parser_oracle():
    bs = parse_blocks(_FIXTURE)
    inj = [b for b in bs if b["phase"] == "injection"]
    body = [b for b in bs if b["phase"] == "body"]
    assert len(inj) == 1 and len(body) == 1
    assert not RE_SLICE_ARG.search(inj[0]["text"])             # the injection carries no --slice
    assert RE_SLICE_ARG.search(body[0]["text"])                # the body threads --slice "$ARG"
    assert RE_GUARD.search(body[0]["text"])                    # ...behind a shape guard
    assert RE_REPO_ROOT.search(body[0]["text"])                # ...with the --repo-root else-branch retained


# --------------------------------------------------------------------------------------------------------
# M1 (+ general): every active_slice --repo-root BODY site in each target is shape-guarded with --slice.
# build-slice has FOUR such sites; the durable assertion is "EVERY site", not "at least one".
# --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_every_repo_root_body_site_is_slice_guarded(skill):
    sites = _active_slice_repo_root_body_blocks(skill)
    assert sites, f"{skill}: expected >=1 body active_slice --repo-root site (resolution moved to body)"
    unguarded = [b["start"] for b in sites if not (RE_SLICE_ARG.search(b["text"]) and RE_GUARD.search(b["text"]))]
    assert not unguarded, (
        f"{skill}: body active_slice --repo-root sites missing the shape-guarded --slice thread "
        f"at block(s) starting line {unguarded} (M1: ALL sites must be guarded, not just one)"
    )


def test_build_slice_has_all_four_repo_root_sites_guarded():
    # M1 explicit: build-slice's design under-counted (named 2 of 4). Pin the floor so a regression that
    # drops a site below four fails here too.
    sites = _active_slice_repo_root_body_blocks("build-slice")
    assert len(sites) >= 4, f"build-slice: expected >=4 active_slice --repo-root body sites, found {len(sites)}"


# --------------------------------------------------------------------------------------------------------
# #2: NO !-injection threads --slice "$ARG" (inert pattern -- injections don't bind ${ARGUMENTS}).
# --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_no_injection_threads_slice_arg(skill):
    bad = [b["start"] for b in _blocks(skill)
           if b["phase"] == "injection" and RE_SLICE_ARG.search(b["text"])]
    assert not bad, f"{skill}: !-injection threads --slice (INERT -- ${{ARGUMENTS}} unbound at load) at line {bad}"


# --------------------------------------------------------------------------------------------------------
# inj: build-slice's active-slice resolution !-injections are exit-0-TOLERANT (never abort the launch).
# A resolution injection that runs active_slice --repo-root must carry a tolerance (|| ... or 2>/dev/null
# || true) so an exit-4 AMBIGUOUS under parallel slices degrades visibly instead of aborting skill-load.
# --------------------------------------------------------------------------------------------------------
def test_build_slice_resolution_injections_are_exit0_tolerant():
    res_injs = [b for b in _blocks("build-slice")
                if b["phase"] == "injection" and RE_INV.search(b["text"]) and RE_REPO_ROOT.search(b["text"])]
    assert res_injs, "build-slice: expected the at-a-glance resolution injections to still exist"
    # An exit-0 at-a-glance fallback: an explicit `else echo/printf ...` (the empty-$SDIR / ambiguous branch
    # must NOT leave the injection's last command non-zero -- a `[ -n "$SDIR" ] && {...}` with no else exits 1
    # on empty and ABORTS skill-load from main under parallel slices). A trailing `|| echo`/`|| printf` on the
    # final command also qualifies. (The SC-050 inner `|| true` on the cat does NOT -- it is not the last word.)
    def tolerant(t: str) -> bool:
        return bool(re.search(r"\belse\b[\s\S]*?(echo|printf)", t) or re.search(r"\|\|\s*(echo|printf)\b", t))
    not_tolerant = [b["start"] for b in res_injs if not tolerant(b["text"])]
    assert not not_tolerant, (
        f"build-slice: resolution injection(s) at line {not_tolerant} can abort the launch on an exit-4 "
        f"AMBIGUOUS (parallel slices, from main). Add an explicit `else echo ...` at-a-glance fallback so the "
        f"injection exits 0 and the BODY owns the named-slice resolution + fail-closed HALT (slice-031 M-add-2)."
    )


# --------------------------------------------------------------------------------------------------------
# exit-4 fallback retained: build-slice + validate-slice keep --repo-root on the no-arg branch (fail-closed).
# (code-review carved out -- it is forked/auto-advanced; its no-arg behavior is the build-time concern.)
# --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skill", ["build-slice", "validate-slice"])
def test_exit4_repo_root_fallback_retained(skill):
    for b in _active_slice_repo_root_body_blocks(skill):
        if RE_SLICE_ARG.search(b["text"]):
            assert RE_REPO_ROOT.search(b["text"]), (
                f"{skill}: guarded resolution at line {b['start']} dropped the --repo-root no-arg fallback "
                f"(the slice-014 fail-closed exit-4 HALT must survive)"
            )


# --------------------------------------------------------------------------------------------------------
# argument-hint (mentioning slice) on all three frontmatter.
# --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_argument_hint_advertises_slice(skill):
    assert RE_ARG_HINT.search(_read(skill)), f"{skill}: frontmatter argument-hint must advertise a slice id"


# --------------------------------------------------------------------------------------------------------
# M2: build-slice's BRANCH-2/3 worktree-state gate keys off the NAMED slice (main-launch is expected,
# not the case-3 STOP). Text presence: the gate region must reference the explicit/named-slice main path.
# --------------------------------------------------------------------------------------------------------
def test_build_slice_branch_gate_handles_named_from_main():
    text = _read("build-slice")
    # the BRANCH-2/3 region must acknowledge the named-from-main case (resolve the named slice's worktree;
    # session branch != slice branch is expected) rather than only the case-3 STOP.
    assert re.search(r"named[ -]slice|named-from-main|explicit (slice|--slice|arg).*main|main-launch", text, re.IGNORECASE), (
        "build-slice: BRANCH-2/3 gate does not handle the explicit-named-slice-from-main case (M2)"
    )


# --------------------------------------------------------------------------------------------------------
# m3: the C3 [ -d "$wt" ] fail-visible guard precedes the cwd-dependent pre_finish_gate invocation.
# --------------------------------------------------------------------------------------------------------
def test_build_slice_c3_worktree_guard_before_prefinish():
    text = _read("build-slice")
    guard = re.search(r'\[\s*-d\s+"\$wt"\s*\]', text)
    # anchor on the actual INVOCATION (a `$PY ... pre_finish_gate.py` call), NOT a prose mention of the
    # gate's name (the consolidated-gate description mentions pre_finish_gate.py well above the call site).
    invocation = re.search(r'\$PY\s+"\$\{CLAUDE_SKILL_DIR\}/scripts/pre_finish_gate\.py"', text)
    assert guard, 'build-slice: missing the C3 [ -d "$wt" ] fail-visible worktree guard (m3)'
    assert invocation, "build-slice: the pre_finish_gate.py INVOCATION ($PY ...) was not found"
    assert guard.start() < invocation.start(), (
        'build-slice: the [ -d "$wt" ] guard must PRECEDE the pre_finish_gate invocation (m3)'
    )


# --------------------------------------------------------------------------------------------------------
# M3: build-slice's /code-review handoff passes the resolved slice id (text presence; arrival = live test).
# --------------------------------------------------------------------------------------------------------
def test_build_slice_code_review_handoff_passes_slice_id():
    text = _read("build-slice")
    # the handoff line must invoke /code-review WITH a slice id (e.g. `/code-review slice-NNN` /
    # `/code-review <slice>` / "pass the slice id"), not the bare `/code-review`.
    assert re.search(r"/code-review\s+(slice-|\$|<slice|\{)", text) or re.search(r"code-review.*pass.*slice", text, re.IGNORECASE), (
        "build-slice: the /code-review handoff must pass the resolved slice id (M3)"
    )


# --------------------------------------------------------------------------------------------------------
# m2: validate-slice's single-skill active_slice_info.py grows a --slice passthrough.
# --------------------------------------------------------------------------------------------------------
def test_active_slice_info_has_slice_flag():
    p = SKILLS / "validate-slice" / "scripts" / "active_slice_info.py"
    assert p.exists(), f"missing {p}"
    assert re.search(r'add_argument\(\s*["\']--slice', p.read_text(encoding="utf-8")), (
        "active_slice_info.py: missing the --slice passthrough (m2)"
    )


# --------------------------------------------------------------------------------------------------------
# M1 (code-review): validate-slice's active_slice_info.py prerequisite-digest must thread --slice "$ARG" in a
# BODY block (so /validate-slice slice-NNN resolves the NAMED slice's digest) -- not a load-time !-injection
# (which cannot bind ${ARGUMENTS}; the added --slice passthrough would then have no caller).
# --------------------------------------------------------------------------------------------------------
RE_INFO_INV = re.compile(r"active_slice_info\.py")


def test_validate_slice_digest_threads_slice():
    blocks = _blocks("validate-slice")
    body_threaded = [b for b in blocks if b["phase"] == "body"
                     and RE_INFO_INV.search(b["text"]) and RE_SLICE_ARG.search(b["text"]) and RE_GUARD.search(b["text"])]
    assert body_threaded, (
        "validate-slice: the active_slice_info.py prerequisite-digest must thread --slice \"$ARG\" in a "
        "guarded BODY block so /validate-slice slice-NNN resolves the NAMED slice's digest (M1)"
    )
    inj_threaded = [b["start"] for b in blocks
                    if b["phase"] == "injection" and RE_INFO_INV.search(b["text"]) and RE_SLICE_ARG.search(b["text"])]
    assert not inj_threaded, (
        f"validate-slice: active_slice_info.py --slice in a !-injection (INERT -- ${{ARGUMENTS}} unbound) at line {inj_threaded}"
    )


# --------------------------------------------------------------------------------------------------------
# m1 (code-review): no active_slice resolution (any phase, any target skill) may carry 2>/dev/null -- that
# would swallow the exit-4 AMBIGUOUS HALT (slice-014 / must_not_defer #1). Pins the invariant in the slice's
# OWN RED net (the shared scripts/lib/active_slice_guard_audit.py is the broader backstop). Comment text such
# as "# slice-014: NO 2>/dev/null" is stripped before the check so the reminder comments don't false-positive.
# --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_no_active_slice_invocation_swallows_stderr(skill):
    for b in _blocks(skill):
        for raw in b["text"].splitlines():
            code = raw.split("#", 1)[0]   # strip inline comment (the NO-2>/dev/null reminders live there)
            if (RE_INV.search(code) or RE_INFO_INV.search(code)) and "2>/dev/null" in code:
                pytest.fail(
                    f"{skill}: active_slice resolution swallows stderr (2>/dev/null) at block line {b['start']} "
                    f"-- the slice-014 exit-4 AMBIGUOUS HALT would be discarded: {code.strip()[:90]}"
                )
