"""
Bug (SC-050): /build-slice dynamic-injection `cat` aborts the whole skill load on a skipped critique.

skills/build-slice/SKILL.md pre-loads per-slice files into the prompt via fenced `!`-injection blocks like:

    SDIR="$($PY ".../active_slice.py" ... --path-only)"
    [ -n "$SDIR" ] && cat "$SDIR/critique.json" 2>/dev/null

When critique.json is ABSENT -- the legitimate case of a deliberately-skipped low-tier critique, where
milestone.json records {step:critique, done:'skipped'} instead of writing critique.json -- `cat` exits
non-zero. The `2>/dev/null` suppresses stderr but NOT the exit code, so the `&&` chain exits non-zero and
the harness aborts the ENTIRE /build-slice skill load with 'Shell command failed'. Observed live in slice-023.

This module pins THREE behaviours of every optional-file injection block, run as the real extracted block:
  * absent file       -> exit 0   (the SC-050 bug; AC1/AC2/AC4)
  * present file       -> exit 0 AND content still emitted, for EVERY block (happy-path guard; AC3 / m1)
  * EMPTY $SDIR        -> exit 0   (slice-036/ADR-025: at-a-glance tolerance; the fail-closed HALT MOVED to the body)

slice-036 / ADR-025 UPDATE: the empty-$SDIR case now exits 0 (it was exit NON-ZERO under slice-014/SC-050). A
`!`-injection runs at skill-LOAD before ${ARGUMENTS} binds (SC-064/ADR-022), so a `/build-slice slice-NNN` from
a main session would abort AT the injection if it HALTed on the (load-blind) ambiguity -- defeating named-from-
main, which is slice-036's whole purpose. So the injection is now EXIT-0 TOLERANT (an `else echo <hint>` branch),
and the fail-closed "never build the wrong slice" goal MOVES to the BODY resolution: a no-arg `--repo-root .`
(NO 2>/dev/null) -> exit-4 AMBIGUOUS + empty slice_folder -> the build STOPs (C3 `[ -d "$wt" ]` / BRANCH-2/3).
The body fail-close is pinned by test_code_acting_skills_thread_slice_arg.py::{test_exit4_repo_root_fallback_
retained, test_no_active_slice_invocation_swallows_stderr}. The absent-file exit-0 (the original SC-050 fix) and
the no-`2>/dev/null` invariant (active_slice_guard_audit) are UNCHANGED -- the tolerance is `|| true` / `else echo`,
NEVER a stderr-swallow.

Blocks are discovered by the executable ` ```! ` fence marker (m2) -- a plain ```bash/```json/```text doc
fence that merely *contains* the `cat "$SDIR/...` literal is documentation, never sourced, and is ignored.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "skills" / "build-slice" / "SKILL.md"

_CAT_SDIR = 'cat "$SDIR/'
_SDIR_ASSIGN = re.compile(r"^\s*SDIR=")
_CAT_TARGET = re.compile(r'cat\s+"\$SDIR/([^"]+)"')


def _bash():
    for name in ("bash", "sh"):
        found = shutil.which(name)
        if found:
            return found
    # Windows git-bash fallbacks (venv-python PATH may not include git's bin).
    for cand in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if os.path.exists(cand):
            return cand
    return None


def _injection_blocks():
    """Each EXECUTABLE `!`-injection fenced block in SKILL.md that cats an optional $SDIR file.

    Only a ` ```! ` fence is sourced by the Claude Code harness; a plain ```bash / ```json / ```text fence is
    documentation and is never executed. Restricting discovery to the `!` marker (m2) keeps a future prose
    code-fence that merely *contains* the `cat "$SDIR/...` literal from being mistaken for a real injection
    and run under bash.
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    blocks, cur, in_block, executable = [], [], False, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_block:
                if executable and any(_CAT_SDIR in l for l in cur):
                    blocks.append(cur)
                cur, in_block, executable = [], False, False
            else:
                cur, in_block = [], True
                executable = line.strip().startswith("```!")  # only `!`-opened fences are sourced
            continue
        if in_block:
            cur.append(line)
    return blocks


def _stub_sdir(block_lines, sdir):
    """Replace the env-dependent `SDIR=$(...)` resolution with a literal value; keep the rest verbatim.

    Pass a non-empty dir to exercise the resolved cases, or "" to exercise the AMBIGUOUS/unresolved case.
    """
    out = []
    for l in block_lines:
        out.append(f'SDIR="{sdir}"' if _SDIR_ASSIGN.match(l) else l)
    return "\n".join(out)


def _run(script):
    return subprocess.run(
        [_bash(), "-c", script], capture_output=True, text=True, env=dict(os.environ)
    )


def _targets(block):
    return [m.group(1) for l in block for m in [_CAT_TARGET.search(l)] if m]


@pytest.mark.skipif(_bash() is None, reason="no POSIX shell available")
def test_optional_file_injections_exit_zero_when_file_absent():
    """Every `cat "$SDIR/<file>"` injection block in build-slice/SKILL.md must exit 0 when the file is absent."""
    blocks = _injection_blocks()
    assert blocks, f"no executable `cat \"$SDIR/...\"` injection block found in {SKILL_MD} (test stale?)"

    tmp = Path(tempfile.mkdtemp(prefix="sc050-")).as_posix()
    try:
        failures = []
        for block in blocks:
            for t in _targets(block):  # the file must genuinely be absent in the stubbed SDIR
                assert not Path(tmp, t).exists()
            proc = _run(_stub_sdir(block, tmp))
            if proc.returncode != 0:
                failures.append((_targets(block), proc.returncode, proc.stderr.strip()))
        assert not failures, (
            "build-slice optional-file injection block(s) exit non-zero when the file is absent "
            "-> /build-slice skill load aborts:\n"
            + "\n".join(f"  targets={t} rc={rc} stderr={err!r}" for t, rc, err in failures)
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.skipif(_bash() is None, reason="no POSIX shell available")
def test_present_file_content_is_still_emitted():
    """Regression guard for EVERY block (m1): the fix must not gut the happy path -- a PRESENT optional file is
    still pre-loaded (rc 0 AND its content emitted), for both critique.json and its mission-brief.json sibling."""
    blocks = _injection_blocks()
    assert blocks, "no executable `cat \"$SDIR/...\"` injection block found (test stale?)"

    for block in blocks:
        targets = _targets(block)
        assert targets, f"injection block cats no $SDIR file: {block!r}"
        tmp = Path(tempfile.mkdtemp(prefix="sc050-ok-"))
        try:
            sentinels = {}
            for t in targets:
                sent = f"SC050-PRESENT-{t}"
                fp = tmp / t
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(sent, encoding="utf-8")
                sentinels[t] = sent
            proc = _run(_stub_sdir(block, tmp.as_posix()))
            assert proc.returncode == 0, proc.stderr
            for t, sent in sentinels.items():
                assert sent in proc.stdout, f"{t} content not pre-loaded when present: {proc.stdout!r}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.skipif(_bash() is None, reason="no POSIX shell available")
def test_optional_file_injections_tolerate_empty_sdir_body_owns_halt():
    """slice-036 / ADR-025: each build-slice optional-file injection block must EXIT 0 when $SDIR is the EMPTY
    string (the load-blind AMBIGUOUS case) -- it shows an at-a-glance hint and does NOT abort skill-load, so a
    main-launched `/build-slice slice-NNN` survives to the BODY where ${ARGUMENTS} binds and the named slice is
    resolved. This SUPERSEDES the slice-014/SC-050 injection-HALT (which required exit NON-ZERO here): an
    injection runs at skill-LOAD before ${ARGUMENTS} binds, so HALTing on the ambiguity it cannot disambiguate
    would make named-from-main structurally impossible. The fail-closed 'never build the wrong slice' goal MOVED
    to the body (a no-arg `--repo-root .` -> exit-4 + empty slice_folder -> STOP), pinned by
    test_code_acting_skills_thread_slice_arg.py::{test_exit4_repo_root_fallback_retained,
    test_no_active_slice_invocation_swallows_stderr}. The tolerance must be `|| true` / `else echo` and NEVER a
    `2>/dev/null` on the active_slice resolution (active_slice_guard_audit still enforces no-stderr-swallow)."""
    blocks = _injection_blocks()
    assert blocks, "no executable `cat \"$SDIR/...\"` injection block found (test stale?)"

    aborts = []
    for block in blocks:
        proc = _run(_stub_sdir(block, ""))  # empty $SDIR -- the load-blind AMBIGUOUS case
        if proc.returncode != 0:
            aborts.append((_targets(block), proc.returncode, proc.stderr.strip()))
    assert not aborts, (
        "build-slice optional-file injection block(s) ABORT skill-load on empty $SDIR -> a main-launched "
        "`/build-slice slice-NNN` aborts at the injection BEFORE the body can resolve the named slice "
        "(ADR-025: the injection must be exit-0 tolerant; the body owns the fail-closed HALT):\n"
        + "\n".join(f"  targets={t} rc={rc} stderr={err!r}" for t, rc, err in aborts)
    )


def test_bash_is_resolvable_on_ci():
    """M-add-1: the exit-safety tests above SKIP when no POSIX shell resolves. On CI (where bash is guaranteed --
    ubuntu-latest + windows-latest both ship it) a skip would SILENTLY stop enforcing the fix. Fail loudly if CI
    is set but bash is unresolved, so a degraded runner turns RED instead of green-but-empty. (Not skipif-gated:
    its whole job is to catch the missing-bash case the other tests would silently skip.)"""
    if os.environ.get("CI") and _bash() is None:
        pytest.fail(
            "CI environment but no POSIX shell resolved (_bash() is None) -- the build-slice injection "
            "exit-safety tests would SILENTLY SKIP instead of enforcing the SC-050 fix."
        )
