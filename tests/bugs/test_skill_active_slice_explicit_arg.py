"""
Bug (SC-064 / slice-034): the active-slice resolution a slice-targeting skill CONSUMES must live in a
`bash` BODY block, never a `!`-injection.

slice-031 (SC-056) reality-proved that `!`-injection blocks run at skill-LOAD, BEFORE the harness binds
${ARGUMENTS} into the skill content -- so a `--slice "$ARG"` threaded into a `!`-injection is INERT: it
falls to the `--repo-root .` else-branch -> AMBIGUOUS under >=2 in-flight slices, and the typed
`/<skill> slice-NNN` is silently ignored. Only `bash` BODY blocks bind ${ARGUMENTS}. slice-021 AND
slice-031 both shipped guards that PASSED a presence-only audit yet were inert, because that audit could
not distinguish a ```! injection from a ```bash body. This is the fix: pin resolution LOCATION, not mere
presence.

This test is FENCE-AWARE. It parses each SKILL.md into fenced blocks tagged by their opening info-string
(`!` = injection, `bash`/`sh` = body) and asserts, per slice-targeting skill:

  1. the guarded, arg-threaded resolution (`--slice "$ARG"`) lives in a `bash` BODY block, AND
  2. NO `!`-injection block threads `--slice "$ARG"` (the inert pattern is gone -- a retained injection may
     show at-a-glance state but must never carry the explicit-arg resolution the skill consumes), AND
  3. that body resolution is shape-guarded (`[ -n "$ARG" ]` or a `grep ... ^slice-` shape guard).

Carve-outs folded from the slice-034 critique (M1/M2):
  - The exit-4 fail-closed `--repo-root .` no-arg fallback is required ONLY for the active_slice.py skills
    (critique, critique-review, reflect, risk-spike). design-slice uses active_slice_brief.py
    (exit-0-ALWAYS by deliberate slice-031 M-add-2 design -- it cannot raise exit-4), and slice-story is
    ARCHIVE-AWARE (`--repo-root .`/resolve_active_slice EXCLUDES archive/, but slice-story is legitimately
    invoked on an archived slice). Both assert body-sited + guarded `--slice`, NOT `--repo-root`.
  - critique-review (M3): the body resolver must sit ABOVE the Step-2 agent spawn (ORDER, not just type),
    else the spawned inputs derive from the inert L20 injection.

Controls (M4 -- avoid a self-referential green): the PARSER's correctness is checked against SYNTHETIC
inline fixtures (a hand-written body block + injection block, in both one-line and two-line if/else form),
NOT a real SKILL.md -- so the oracle is never also a subject. risk-spike stays a real TARGET (it carries
the proven body-sited shape + the slice-031 token-scan + argument-hint guarantees) but is NOT the parser
oracle. Markdown-config audit: skill-load happens in the harness and cannot be driven from pytest, so a
faithful repro reads the SKILL.md text and asserts on its fenced structure. Pairs with the SHIP-014 family
test_active_slice_parallel_mispick.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = <repo>/tests/bugs/<thisfile> -> parents[2]. Works from the slice worktree too.
REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"

# Slice-targeting skills whose load-bearing active-slice resolution MUST be body-sited. risk-spike is the
# proven reference (already body-sited) and is kept as a TARGET so it stays a regression guard -- but it is
# NOT the parser oracle (M4: the oracle is the synthetic fixture below).
TARGET_SKILLS = ["critique", "critique-review", "design-slice", "reflect", "slice-story", "risk-spike"]
# The active_slice.py consumers whose no-arg branch must keep the slice-014 exit-4 fail-closed HALT
# (--repo-root .). design-slice (active_slice_brief, exit-0-always) + slice-story (archive-aware) are carved out.
REPO_ROOT_REQUIRED = ["critique", "critique-review", "reflect", "risk-spike"]
# design-slice (active_slice_brief, exit-0-always) + slice-story (archive-aware) are CARVED OUT of the exit-4
# HALT requirement (M1/M2): design-slice via its ABSENCE from REPO_ROOT_REQUIRED; slice-story's archive-aware
# write-target block is guarded against --repo-root by test_slice_story_write_target_block_has_no_repo_root.

# slice-031 (m2): risk-spike's slice id can appear at ANY positional (arg0 is often --mode), so its guard
# MUST derive ARG by SCANNING $ARGUMENTS, not ${ARGUMENTS[0]} alone.
SCAN_REQUIRED = ["risk-spike"]
# slice-031 (AC3): each threaded analytical skill advertises the optional slice-NNN arg in its frontmatter.
ARG_HINT_REQUIRED = ["risk-spike", "design-slice", "reflect"]

# critique-review's body resolver must precede the Step-2 meta-Critic spawn (M3 -- the composition seam).
RE_SPAWN_MARKER = re.compile(r'subagent_type[^\n]*critique-review|Spawn the meta-Critic')

_ARG = r'"\$\{?ARG\}?"'                                    # "$ARG" or "${ARG}"
RE_SLICE_ARG = re.compile(r"--slice\s+" + _ARG)           # --slice "$ARG"  (the explicit-arg thread)
RE_REPO_ROOT = re.compile(r"--repo-root")                 # --repo-root . | --repo-root "$repo_root"
# A guarded conditional on ARG: [ -n "$ARG" ] OR a slice-id SHAPE guard (grep ... ^slice-).
RE_GUARD = re.compile(r'\[\s*-n\s*' + _ARG + r'\s*\]|grep\s+-q[A-Za-z]*\s+\S*\^?slice-')
# A REAL active_slice resolution invocation -- anchored on scripts/lib/ so a back-ticked PROSE mention of
# `active_slice.py` (e.g. risk-spike:240, reflect:191) never matches (the M1/slice-031 lesson). `(?:_brief)?`
# also matches active_slice_brief.py (design-slice's exit-0 resolver).
RE_INV = re.compile(r"scripts/lib/active_slice(?:_brief)?\.py")
RE_TOKEN_SCAN = re.compile(r"for\s+\w+\s+in\s+\$ARGUMENTS")
RE_ARG_HINT = re.compile(r"^argument-hint:.*slice", re.MULTILINE | re.IGNORECASE)

FENCE = re.compile(r"^\s*```(.*)$")                        # opening/closing fence; group(1) = info string


def parse_blocks(text: str) -> list[dict]:
    """Fence state-machine: split text into fenced blocks tagged by opening info-string.

    Returns one dict per block: {phase: 'injection'|'body'|'other', start, end, text}. `!` => injection
    (load-time), `bash`/`sh` => body (step-time, binds $ARGUMENTS), anything else => other. A flat
    ```lang ... ``` grammar (no markdown library needed; SKILL.md fences do not nest in the resolution
    regions -- verified by the slice-034 design-spike against all 6 real files).
    """
    blocks: list[dict] = []
    info: str | None = None
    start = 0
    buf: list[str] = []
    for ln, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if info is None:                              # opening a fence
                info, start, buf = m.group(1).strip(), ln, []
            else:                                         # closing the current fence
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


def _threaded(b: dict) -> bool:
    return bool(RE_INV.search(b["text"]) and RE_SLICE_ARG.search(b["text"]))


# --------------------------------------------------------------------------------------------------------
# M4 / m3: parser self-check against SYNTHETIC fixtures (the oracle -- independent of any real SKILL.md).
# Covers BOTH a one-line if/else (design-slice's form: one match carrying --slice AND --repo-root) and a
# two-line if/else (the per-LINE count asymmetry the design flagged) -- the test asserts PHASE, not counts.
# --------------------------------------------------------------------------------------------------------
_F = "`" * 3
ONE_LINE_BODY = (
    _F + 'bash\n'
    'ARG="${ARGUMENTS[0]:-}"; if [ -n "$ARG" ]; then '
    'SDIR=$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --slice "$ARG" --path-only); '
    'else SDIR=$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --repo-root . --path-only); fi\n'
    + _F
)
TWO_LINE_BODY = (
    _F + 'bash\n'
    'ARG="${ARGUMENTS[0]:-}"\n'
    'if [ -n "$ARG" ]; then\n'
    'SDIR=$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --slice "$ARG" --path-only)\n'
    'else\n'
    'SDIR=$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --repo-root . --path-only)\n'
    'fi\n' + _F
)
INJECTION_FIXTURE = (
    _F + '!\n'
    'ARG="${ARGUMENTS[0]:-}"; '
    'SDIR=$("$PY" "${CLAUDE_SKILL_DIR}/../../scripts/lib/active_slice.py" --slice "$ARG" --path-only)\n'
    + _F
)


def test_parser_self_check_synthetic_fixtures() -> None:
    """The fence parser correctly classifies a one-line body, a two-line body, and an injection -- and
    detects the threaded+guarded resolution per PHASE (M4 independent oracle; m3 count-asymmetry)."""
    for fixture, name in ((ONE_LINE_BODY, "one-line body"), (TWO_LINE_BODY, "two-line body")):
        blocks = parse_blocks(fixture)
        body = [b for b in blocks if b["phase"] == "body"]
        assert len(body) == 1, f"{name}: expected 1 body block, got {[b['phase'] for b in blocks]}"
        b = body[0]
        assert _threaded(b), f"{name}: body block must carry a --slice \"$ARG\" thread"
        assert RE_GUARD.search(b["text"]), f"{name}: body block must be guarded"
        assert RE_REPO_ROOT.search(b["text"]), f"{name}: body block must carry the --repo-root fallback"
    inj = parse_blocks(INJECTION_FIXTURE)
    assert len(inj) == 1 and inj[0]["phase"] == "injection", "injection fixture must classify as injection"
    assert _threaded(inj[0]), ("injection fixture (control) does carry a --slice thread -- so a real "
        "injection that threads --slice would be DETECTED as injection-sited (the bug this test catches)")


@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_resolution_is_body_sited(skill: str) -> None:
    """AC1: the guarded, arg-threaded resolution the skill consumes lives in a `bash` BODY block."""
    blocks = _blocks(skill)
    body_threaded = [b for b in blocks if b["phase"] == "body" and _threaded(b)]
    assert body_threaded, (
        f"{skill}/SKILL.md has NO `bash` BODY block threading --slice \"$ARG\" -- its load-bearing "
        f"active-slice resolution is not body-sited (it binds $ARGUMENTS only in a body block). "
        f"Resolution blocks found: {[(b['phase'], b['start']) for b in blocks if RE_INV.search(b['text'])]}"
    )
    assert any(RE_GUARD.search(b["text"]) for b in body_threaded), (
        f"{skill}/SKILL.md: the body-sited --slice resolution is not shape-guarded "
        f"([ -n \"$ARG\" ] or a grep ^slice- shape guard)."
    )


@pytest.mark.parametrize("skill", TARGET_SKILLS)
def test_no_injection_threads_explicit_arg(skill: str) -> None:
    """AC1/AC2: the explicit-arg thread must NOT appear in a `!`-injection (where it is INERT). A retained
    injection may show state, but must never carry the --slice "$ARG" resolution the skill consumes."""
    offenders = [b["start"] for b in _blocks(skill)
                 if b["phase"] == "injection" and RE_SLICE_ARG.search(b["text"])]
    assert not offenders, (
        f"{skill}/SKILL.md threads --slice \"$ARG\" inside a `!`-injection at line(s) {offenders} -- "
        f"INERT (skill-load runs before ${{ARGUMENTS}} binds). Move it to a `bash` BODY step."
    )


@pytest.mark.parametrize("skill", REPO_ROOT_REQUIRED)
def test_exit4_repo_root_fallback_body_sited(skill: str) -> None:
    """AC1: the active_slice.py consumers keep the slice-014 exit-4 fail-closed HALT (--repo-root . no-arg
    fallback) in a BODY block. design-slice (active_slice_brief, exit-0) + slice-story (archive-aware) are
    carved out (M1/M2) and are NOT in this set."""
    assert any(b["phase"] == "body" and RE_INV.search(b["text"]) and RE_REPO_ROOT.search(b["text"])
               for b in _blocks(skill)), (
        f"{skill}/SKILL.md has no `bash` BODY active_slice resolution carrying the --repo-root . no-arg "
        f"fallback (the slice-014 exit-4 AMBIGUOUS HALT path)."
    )


def test_slice_story_write_target_block_has_no_repo_root() -> None:
    """M2 (slice-034 code-review): slice-story's WRITE-TARGET resolver — the archive-aware `--slice` block that
    assigns TARGET (used for the /commit-slice on-ship auto-emit, which can target an ALREADY-ARCHIVED slice) —
    must NEVER gain a `--repo-root .` fallback. resolve_active_slice (--repo-root) EXCLUDES archive/, so
    --repo-root there would silently break the archived on-ship path; the no-arg authority is the Step-0
    active_slice_dir. (The SEPARATE Step-0 section-select block legitimately uses --repo-root for the no-arg
    ACTIVE slice — this guard targets ONLY the TARGET= write-target block, closing the M2 regression hole.)"""
    target_blocks = [b for b in _blocks("slice-story")
                     if b["phase"] == "body" and "TARGET=" in b["text"] and RE_SLICE_ARG.search(b["text"])]
    assert target_blocks, ("slice-story has no archive-aware write-target body block (a TARGET= bash body "
                           "threading --slice \"$ARG\") — the M2 carve-out cannot be checked.")
    for b in target_blocks:
        # Strip inline bash comments first — the block's own comment literally says "NEVER --repo-root"
        # (documentation, not a flag); no `#` appears inside the resolution literals, so this is safe.
        code = "\n".join(ln.split("#", 1)[0] for ln in b["text"].splitlines())
        assert not RE_REPO_ROOT.search(code), (
            f"slice-story's write-target block (line {b['start']}) carries an actual --repo-root flag — that "
            f"EXCLUDES archive/, breaking the /commit-slice on-ship auto-emit on an already-archived slice (M2). "
            f"Keep archive-aware --slice; the no-arg authority is the Step-0 active_slice_dir."
        )


def test_critique_review_resolver_precedes_agent_spawn() -> None:
    """M3 (the composition seam): critique-review's guarded body resolver must sit ABOVE the Step-2
    meta-Critic spawn, else the spawned inputs derive from the inert L20 injection. A fence-TYPE-only
    check is satisfiable by the post-spawn Step-3 body resolver; this ORDER check is not."""
    text = _read("critique-review")
    blocks = parse_blocks(text)
    body_threaded = [b for b in blocks if b["phase"] == "body" and _threaded(b)]
    assert body_threaded, "critique-review has no body-sited --slice resolution at all"
    first_body = min(b["start"] for b in body_threaded)
    spawn = RE_SPAWN_MARKER.search(text)
    assert spawn, "could not locate the Step-2 meta-Critic spawn marker in critique-review/SKILL.md"
    spawn_line = text[: spawn.start()].count("\n") + 1
    assert first_body < spawn_line, (
        f"critique-review's body-sited resolver is at line {first_body}, AT/AFTER the Step-2 agent spawn "
        f"(line {spawn_line}). The resolver MUST be hoisted ABOVE the spawn so the spawned inputs come "
        f"from a body-resolved slice, not the inert L20 injection (M3)."
    )


@pytest.mark.parametrize("skill", SCAN_REQUIRED)
def test_skill_scans_positionals_for_slice_id(skill: str) -> None:
    """m2 (slice-031): risk-spike's slice id can appear at any positional (arg0 is often --mode), so its
    guard must derive ARG by SCANNING $ARGUMENTS, not ${ARGUMENTS[0]} alone."""
    assert RE_TOKEN_SCAN.search(_read(skill)), (
        f"{skill}/SKILL.md must derive the explicit slice arg by SCANNING $ARGUMENTS "
        f"(for a in $ARGUMENTS ...), not ${{ARGUMENTS[0]}} alone -- a flag may precede the slice id."
    )


@pytest.mark.parametrize("skill", ARG_HINT_REQUIRED)
def test_skill_advertises_argument_hint(skill: str) -> None:
    """AC3 (slice-031): each threaded analytical skill advertises the optional slice-NNN arg in its
    frontmatter (argument-hint: ... slice ...), so `/<skill> slice-NNN` is discoverable."""
    assert RE_ARG_HINT.search(_read(skill)), (
        f"{skill}/SKILL.md frontmatter must carry an argument-hint advertising the slice-NNN arg (AC3)."
    )
