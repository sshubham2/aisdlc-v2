"""
slice-042 (SC-047 / ADR-029): /critique's Step-2 spawn must thread the active slice's WORKTREE into the
forked Critic's prompt, so the Critic (whose file tools default to the MAIN repo root) reads the
ADR-012-relocated repro test from the worktree instead of false-flagging it 'missing' (the slice-020 M1
recurring main-tree-vs-worktree vantage gap).

This test is a location-pinned SITE guard (slice-025 / slice-034 lesson: a property is only real where
something enforces the SITE; a presence-anywhere grep passes even if the field drifts out of the Step-2
block into prose). It parses skills/critique/SKILL.md into heading sections and asserts the worktree field
+ ADR-012 behavioral note + repro-test listing live WITHIN the Step-2 section, that a bash BODY block
resolves the worktree ABOVE the Step-2 spawn, and that every pre-existing Step-2 field sentinel survives
(must-not-defer #2). It also unit-tests the worktree_ctx helper: the canonical full-folder string join
(M-add-2: the '042' vs '42' silent-regression guard), the 'main tree' degrade (AC3), and the repro-test
DATA listing (M-add-1).

Controls (M4 -- avoid a self-referential green): the section parser is checked against a SYNTHETIC inline
fixture, not a real SKILL.md, so the oracle is never also the subject.
"""
from __future__ import annotations

import re
import sys
from collections import namedtuple
from pathlib import Path

# tests/bugs/<thisfile> -> parents[2] = repo (works from the slice worktree too)
REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"
CRITIQUE = SKILLS / "critique" / "SKILL.md"

sys.path.insert(0, str(SKILLS / "critique" / "scripts"))
import worktree_ctx  # noqa: E402  (the implementation under test, created in T2)


# --------------------------------------------------------------------------------------------------------
# Section parser (heading-aware): the Step-2 section is "## Step 2 ..." up to the next "## " heading.
# --------------------------------------------------------------------------------------------------------
def section(text: str, heading_prefix: str) -> str:
    """Return the slice of `text` from the heading line starting with `heading_prefix` up to the next
    top-level "## " heading (exclusive). Empty string if the heading is absent."""
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for ln in lines:
        if ln.startswith("## "):
            if capturing:
                break
            if ln.startswith(heading_prefix):
                capturing = True
                continue
        if capturing:
            out.append(ln)
    return "\n".join(out)


def _critique_text() -> str:
    assert CRITIQUE.exists(), f"missing {CRITIQUE}"
    return CRITIQUE.read_text(encoding="utf-8")


def _step2(text: str) -> str:
    s = section(text, "## Step 2")
    assert s, "could not locate the '## Step 2' section in critique/SKILL.md"
    return s


# ===================== SITE assertions on the Step-2 prompt template =====================
def test_step2_block_has_worktree_field() -> None:
    """AC1: the Step-2 agent-prompt section carries an Active-worktree field."""
    s = _step2(_critique_text())
    assert re.search(r"Active worktree|Worktree:", s), (
        "critique/SKILL.md Step-2 section carries no worktree field (AC1) -- the forked Critic gets no "
        "worktree pointer and will read the main tree (slice-020 M1)."
    )


def test_step2_block_has_adr012_repro_note() -> None:
    """AC2: the Step-2 section carries the ADR-012 behavioral repro-relocation note."""
    s = _step2(_critique_text())
    assert "ADR-012" in s, "Step-2 section is missing the ADR-012 reference (AC2)."
    assert "tests/bugs" in s and re.search(r"repro", s), (
        "Step-2 section is missing the behavioral repro note (read/run repro tests from "
        "<wt>/tests/bugs/; AC2)."
    )


def test_worktree_field_located_in_step2_block() -> None:
    """AC4 (location pin -- slice-034/slice-025): the 'Active worktree (ADR-012)' field is located WITHIN
    the Step-2 section, NOT merely present somewhere in the file (which a prose drift would satisfy)."""
    s = _step2(_critique_text())
    # the agent-prompt TEMPLATE is the first fenced block in the Step-2 section. The field must live INSIDE
    # that template block (so the orchestrator pastes it INTO the prompt) -- a prose mention in Step-1.9 or
    # Step-2 prose does NOT satisfy the pin (slice-034/slice-025: pin the SITE, not mere presence).
    m = re.search(r"```.*?\n(.*?)\n```", s, re.DOTALL)
    assert m, "no fenced agent-prompt template block found inside the Step-2 section."
    block = m.group(1)
    assert "Active worktree (ADR-012)" in block, (
        "the 'Active worktree (ADR-012)' field is not located inside the Step-2 prompt TEMPLATE fence "
        "(AC4 location pin) -- a prose mention elsewhere does not satisfy it."
    )


def test_step2_existing_fields_preserved() -> None:
    """must-not-defer #2: the additive worktree block drops NO pre-existing Step-2 field sentinel."""
    s = _step2(_critique_text())
    for sentinel in ("# mission-brief.json", "# design.json", "# project-frame",
                     "# New ADRs this slice", "# Tournament block", "# Cross-domain transfer block"):
        assert sentinel in s, f"Step-2 section dropped the pre-existing field {sentinel!r} (must-not-defer #2)."


def test_step19_body_resolves_worktree_above_step2() -> None:
    """T3/AC1 wiring: a `bash` BODY block (not a `!`-injection) invokes worktree_ctx.py, located ABOVE the
    Step-2 agent spawn -- so the resolved value is in context when the orchestrator builds the prompt."""
    text = _critique_text()
    idx_step2 = text.find("## Step 2")
    assert idx_step2 != -1, "could not find the Step-2 heading."
    # find the worktree_ctx.py CALL ($PY ... invocation), not a prose mention of the filename.
    call = re.search(r"\$PY[^\n]*worktree_ctx\.py", text)
    assert call, "no `$PY ... worktree_ctx.py` invocation found in critique/SKILL.md (T3 wiring)."
    assert call.start() < idx_step2, (
        "the worktree_ctx.py resolution must be ABOVE the Step-2 spawn (else the spawned prompt cannot "
        "carry the resolved worktree)."
    )
    # the call must sit inside an OPEN `bash`/`sh` body fence, never a `!`-injection (SC-064/ADR-022). Toggle
    # through every fence line before the call; the still-open fence's info string is the enclosing block.
    open_info = None
    for fm in re.finditer(r"^\s*```(.*)$", text[: call.start()], re.MULTILINE):
        open_info = fm.group(1).strip() if open_info is None else None
    assert open_info is not None and open_info.split()[:1] in (["bash"], ["sh"]), (
        f"worktree_ctx.py resolution is in a ```{open_info!r} block -- it MUST be a `bash` BODY block "
        f"(a `!`-injection runs at skill-load before $ARGUMENTS binds; SC-064/ADR-022)."
    )


# ===================== worktree_ctx helper unit tests =====================
WT = namedtuple("WT", "slice_num slice_name path")


def test_canonical_join_rejects_unpadded() -> None:
    """M-add-2: the join is the resolver's OWN canonical full-folder string equality -- '042' worktree
    matches the 'slice-042-...' folder but NEVER the unpadded 'slice-42-...' (the silent-regression guard)."""
    wt = WT(slice_num="042", slice_name="thread-worktree-ctx-into-critic-prompt",
            path="/wt/slice-042-thread-worktree-ctx-into-critic-prompt")
    assert worktree_ctx._match("slice-042-thread-worktree-ctx-into-critic-prompt", [wt]) is wt
    assert worktree_ctx._match("slice-42-thread-worktree-ctx-into-critic-prompt", [wt]) is None, (
        "unpadded 'slice-42-...' must NOT match the canonical zero-padded '042' worktree (M-add-2: a "
        "numeric/unpadded compare would silently degrade to 'main tree' and the false-flag would recur)."
    )
    assert worktree_ctx._match("slice-042-other-name", [wt]) is None, "name must also match, not just number."


def test_worktree_ctx_degrades_to_main_tree() -> None:
    """AC3: no registered worktree for the active slice -> 'main tree', never a crash or garbage path."""
    ctx = worktree_ctx.resolve(slice_dir="/no/such/slice-999-nope", repo_root=str(REPO))
    assert ctx["worktree"] is None
    rendered = worktree_ctx.render(ctx)
    assert "main tree" in rendered and "Worktree:" in rendered
    # never raises on a junk repo_root either
    assert worktree_ctx.resolve(slice_dir="x", repo_root="/no/such/repo")["worktree"] is None


def test_worktree_ctx_renders_repro_listing() -> None:
    """M-add-1: when worktree-backed, render() emits the repro-test DATA listing (the test's EXISTENCE as a
    high-signal token), plus the ADR-012 behavioral note -- not just the path pointer."""
    rendered = worktree_ctx.render({"worktree": "/wt/slice-042-x",
                                    "repro_tests": ["test_thumbnail_orientation.py"]})
    assert "Worktree: /wt/slice-042-x" in rendered
    assert "/wt/slice-042-x/tests/bugs/test_thumbnail_orientation.py" in rendered
    assert "ADR-012" in rendered and "tests/bugs" in rendered, "render() must carry the ADR-012 note."


# ===================== M4 parser self-check (synthetic oracle) =====================
def test_section_parser_self_check() -> None:
    """M4: the heading-aware section parser is validated on a SYNTHETIC fixture, so the oracle is never the
    subject. It must capture exactly the target section, bounded by the next '## ' heading."""
    fixture = "\n".join([
        "## Step 1", "alpha", "## Step 2", "BETA-LINE", "more beta", "## Step 3", "gamma",
    ])
    s = section(fixture, "## Step 2")
    assert "BETA-LINE" in s and "more beta" in s
    assert "alpha" not in s and "gamma" not in s, "section() leaked across the '## ' boundaries."
    assert section(fixture, "## Nope") == "", "absent heading must yield empty."


def test_folder_strips_cr() -> None:
    """m1 (code-review): the canonical join must be robust to the documented Windows `$(...)` trailing-CR trap.
    A `\\r`-suffixed slice_dir (from a `SDIR=$(active_slice.py ... --path-only)` capture, which keeps the CR)
    must yield the SAME folder as the clean one, so the join still matches instead of SILENTLY degrading to
    'main tree' and recurring the slice-020 false-flag while believed fixed."""
    assert worktree_ctx._folder("a/b/slice-042-x\r") == "slice-042-x"
    assert worktree_ctx._folder("a/b/slice-042-x\r\n") == "slice-042-x"
    assert worktree_ctx._folder("  slice-042-x  ") == "slice-042-x"
    assert worktree_ctx._folder("a/b/slice-042-x") == "slice-042-x", "the clean path must still resolve."


def test_step19_body_has_degrade_net() -> None:
    """M1 (code-review): the worktree_ctx.py invocation in the Step-1.9 body block must carry a shell-level
    degrade net (`|| echo ... main tree`), so a non-zero exit (e.g. an import/bootstrap failure OUTSIDE
    resolve()'s try) degrades the Step-2 field cleanly instead of emitting a traceback into the prompt-build
    context (must-not-defer #1). This is the body-block SITE assertion the m1 disposition asked for."""
    text = _critique_text()
    m = re.search(r"\$PY[^\n]*worktree_ctx\.py[^\n]*", text)
    assert m, "no `$PY ... worktree_ctx.py` invocation found in critique/SKILL.md."
    assert "|| echo" in m.group(0) and "main tree" in m.group(0), (
        "the Step-1.9 worktree_ctx.py call has no `|| echo ... main tree` shell-level degrade net (M1) -- a "
        "non-zero exit (import/bootstrap failure outside resolve()'s try) would emit a traceback into the prompt."
    )
