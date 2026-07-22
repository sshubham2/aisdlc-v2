"""
Bug (SC-186 / slice-094): /validate-slice forks (`context: fork`) but its harness-budget
guidance told the fork it may run the shippability catalog with `run_in_background` for a
long catalog. A forked context RETURNS before a `run_in_background` task completes,
orphaning it -- so validation.json is NOT written on the first pass (observed on slice-083;
recovery required draining the orphaned catalog process, then a manual re-invoke of
/validate-slice, which only completed because the first run had already done the heavy work).
The fork cannot consume the background task's completion notification once the fork ends, and
the main thread cannot see the fork-internal task id.

Expected: skills/validate-slice/SKILL.md carries an explicit, greppable fork-safety contract
  for the catalog run -- a doc-guard marker `aisdlc:catalog-fork-synchronous` documenting
  that a `context: fork` run MUST execute the shippability catalog SYNCHRONOUSLY (inline /
  awaited) and NEVER run_in_background-and-return -- so validation.json is deterministically
  written on the first pass and a future edit cannot silently reintroduce the orphan-prone
  instruction.
Actual (pre-fix): no such contract marker exists; the harness-budget note (SKILL.md
  lines ~355-361) offers `run_in_background` (and a phantom `--no-merge` + row-subset
  chunking) as a remedy for a long catalog from the forked context.

This is a STATIC documentation-contract assertion on the SKILL.md text: the defect is a
fork/harness orchestration instruction, not a runtime code path a pytest can drive (a pytest
cannot spawn a fork and observe it orphaning a background task). It follows this repo's
doc-guard idiom (cf. the `aisdlc:step6-runner-invocation` doc-guard already in the same file).

Slice-094 hardening (Critic M1 / M2 / M-add-1 / M-add-2 -- DR-1 extend): the positive
"marker exists" check is NOT enough -- a Builder could add the marker while leaving a
`run_in_background` recommendation elsewhere (Case B) and still pass. So this suite ALSO pins
the negatives: (M1) every `run_in_background` occurrence must live INSIDE the marker's
prohibition body; (M-add-1) no `chunk`/`subset` row-selection escape may survive (the runner
has no row-subset selector); (M2) the adjacent `aisdlc:step6-runner-invocation` guard +
`--session-timeout 1800` invocation must be preserved; (M-add-2) the >600s tool-kill branch
must carry an explicit `result: 'fail'` + never-advance instruction so the orphan bug cannot
relocate from `run_in_background` to the tool-kill path.
"""
import re
from pathlib import Path

# tests/bugs/<this file> -> parents[2] == repo root (works from the worktree OR the main tree,
# regardless of cwd, because it anchors off the test file's own location).
SKILL = Path(__file__).resolve().parents[2] / "skills" / "validate-slice" / "SKILL.md"
MARKER = "aisdlc:catalog-fork-synchronous"


def _skill_text():
    return SKILL.read_text(encoding="utf-8")


def _prohibition_body(text):
    """Return (start, close, body) for the `[aisdlc:catalog-fork-synchronous -- ... ]` doc-guard.

    `start` is the index of the marker token; `close` is the index of the doc-guard's closing
    `]`; `body` is the prohibition text between them. Using the marker's own closing bracket as
    the boundary keeps the negative assertions free of magic-number windows.
    """
    start = text.index(MARKER)
    close = text.index("]", start)
    return start, close, text[start:close]


def test_validate_slice_skill_md_exists():
    assert SKILL.is_file(), f"expected {SKILL} to exist"


def test_validate_slice_is_a_forked_skill():
    # Precondition / self-doc: the orphan hazard exists precisely BECAUSE this skill forks.
    # If /validate-slice ever stops forking, this repro's rationale must be revisited.
    text = _skill_text()
    assert "context: fork" in text, (
        "premise void: /validate-slice is expected to be a `context: fork` skill; "
        "if it no longer forks, the run_in_background-orphan rationale no longer applies"
    )


def test_catalog_run_carries_fork_synchronous_contract():
    text = _skill_text()
    assert MARKER in text, (
        f"missing fork-safety contract marker `{MARKER}` in validate-slice/SKILL.md: a "
        "`context: fork` run must execute the shippability catalog synchronously (inline / "
        "awaited) and never run_in_background-and-return, or it orphans the background task "
        "and validation.json is unwritten on the first pass (SC-186 / slice-083)."
    )
    # Non-cosmetic guard: the doc-guard body must actually state the contract -- a
    # synchronous/inline/awaited directive AND name the prohibited orphan-prone pattern --
    # not just carry a bare tag.
    _, _, body = _prohibition_body(text)
    lb = body.lower()
    assert any(w in lb for w in ("synchronous", "inline", "await")), (
        f"`{MARKER}` present but its guidance must require a SYNCHRONOUS / inline / awaited "
        "catalog run inside the fork"
    )
    assert "run_in_background" in lb, (
        f"`{MARKER}` guidance must explicitly name run_in_background as the prohibited "
        "orphan-prone pattern it guards against"
    )


def test_no_run_in_background_recommendation_outside_the_prohibition_body():
    # M1 (slice-094): AC1 requires the SKILL.md to NO LONGER instruct a run_in_background
    # catalog run. The marker naming the pattern is not enough -- Case B (marker added, the
    # 'run it with run_in_background' recommendation retained elsewhere) must FAIL. So every
    # run_in_background occurrence in the file must sit INSIDE the doc-guard prohibition body
    # (the only place it may appear is where it is forbidden).
    text = _skill_text()
    start, close, _ = _prohibition_body(text)
    stray = [m.start() for m in re.finditer("run_in_background", text)
             if not (start <= m.start() < close)]
    assert not stray, (
        "run_in_background appears OUTSIDE the aisdlc:catalog-fork-synchronous prohibition body "
        f"(offsets {stray}) -- the forked catalog run must be synchronous; every run_in_background "
        "mention must live inside the doc-guard that forbids it, never as a recommendation."
    )


def test_no_row_subset_chunking_escape_survives():
    # M-add-1 (slice-094 DR-1): shippability_runner.py argparse exposes only
    # catalog/--timeout/--session-timeout/--no-merge/--repo-root/--json -- there is NO
    # row-subset selector, so a 'chunk the catalog (--no-merge + row subsets)' escape is a
    # phantom capability. It must not survive the rewrite (both tokens live ONLY in the old
    # removed block, so their absence is a clean negative assertion).
    lt = _skill_text().lower()
    assert "chunk" not in lt, (
        "phantom 'chunk the catalog' escape must be removed -- the runner has no row-subset "
        "selector, so chunking cannot be expressed against a runner flag"
    )
    assert "subset" not in lt, (
        "phantom 'row subset' chunking escape must be removed -- the runner has no row-subset "
        "selector"
    )


def test_step6_runner_invocation_guard_and_session_timeout_preserved():
    # M2 (slice-094): must_not_defer #2 -- the adjacent aisdlc:step6-runner-invocation guard
    # and the --session-timeout 1800 invocation (directly above the edited block) must survive
    # the rewrite untouched. design.json contract error_cases[3] claims this enforcement; this
    # assertion is what makes that claim true.
    text = _skill_text()
    assert "aisdlc:step6-runner-invocation" in text, (
        "preserve-guard: the aisdlc:step6-runner-invocation doc-guard must remain in SKILL.md"
    )
    assert "--session-timeout 1800" in text, (
        "preserve-guard: the runner's --session-timeout 1800 invocation must remain in SKILL.md"
    )


def test_tool_kill_branch_writes_fail_and_never_advances():
    # M-add-2 (slice-094 DR-1): a >600s catalog is hard-killed by the Bash tool at 600s with no
    # output and no catchable exception; the SKILL.md must carry an EXPLICIT fork instruction for
    # that branch -- write validation.json result:'fail' (a valid enum; never the invalid
    # 'blocked') and never advance -- so the orphan bug does not relocate from run_in_background
    # to the tool-kill path (must_not_defer #3/#4).
    text = _skill_text()
    lt = text.lower()
    assert "result: 'fail'" in text, (
        "tool-kill branch must instruct writing validation.json result: 'fail' (a valid enum)"
    )
    assert "never advance" in lt, (
        "tool-kill branch must instruct the fork to never advance on a killed / no-output run"
    )
    assert ("no-output" in lt) or ("killed" in lt), (
        "tool-kill branch must name the killed / no-output catalog condition it applies to"
    )
