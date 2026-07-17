"""Bug: every project-frame call site relocated the VAULT ROOT to the code repo.

All three SKILL.md call sites invoked ``scripts/lib/project_frame_synth.py`` with
``--repo-root .``:

  - skills/design-slice/SKILL.md   (the design tournament's designer context)
  - skills/critique/SKILL.md       (the Critic's attack-lens)
  - skills/critique-review/SKILL.md (the DR-1 Meta-Critic; also piped stderr to
                                     /dev/null, so its degrade was fully silent)

``--repo-root`` did NOT mean "the code repo". Per the tool's own docstring and its
argparse default (``default=str(VAULT_ROOT)``), the flag RELOCATED THE VAULT ROOT
and already defaulted to the resolved external-store vault. Passing ``.`` pointed
the synthesizer at the code repo, where no vault JSON lives, so every read missed
and the tool degraded -- by design: warn + degrade, never refuse -- to a blank
frame. The design tournament designed blind, the Critic attacked blind, and the
DR-1 Meta-Critic reviewed blind, all while reporting success.

The fix (ADR-083 / ADR-084) is two-sided, and so is this guard:

  * INPUT made inexpressible -- ``--repo-root`` is DELETED from the parser (not
    renamed, not aliased, and with NO ``argparse.SUPPRESS`` tombstone: a
    suppressed action is still registered in ``_actions``, so a tombstone would
    report as a live flag under Arm A's introspection and green-light the very
    regression this guard exists to catch). The vault now resolves ONLY through
    the ``_vault_paths`` seam.
  * OUTPUT made honest -- a healthy v2 vault legitimately has no
    ``methodology-changelog.md``, so its absence became an ``INFO`` note rather
    than a ``project-frame degraded`` WARN. Un-silencing critique-review's
    stderr (AC3) without this would ship a standing false alarm at all three call
    sites and re-arm the suppression that caused the defect in the first place.

Arms:

  A  Every flag on a project_frame_synth invocation across the corpus
     (skills/**/*.md, agents/*.md, .build/manifests/*.json) exists in the LIVE
     parser -- introspected, never hand-frozen. Covers a 4th call site that does
     not exist yet, which a hardcoded call-site list never could.
  B  Anti-circularity anchor: Arm A derives truth FROM the parser, so it cannot
     detect the parser itself regressing. Passing --repo-root MUST exit 2.
  C  Frozen BAD/GOOD battery, so a regex regression fails on a CLEAN tree --
     including a synthetic 4th call site the SCANNER must flag.
  D  No project_frame_synth invocation redirects stderr to /dev/null (AC3 as a
     standing property, not a one-time edit).
  E  Signal integrity (AC5) + the AC1 end-to-end frame, by subprocess.

NOTE on process boundaries: the CLI-default path MUST be exercised via subprocess.
``VAULT_ROOT`` is resolved once at import (the from-import at
project_frame_synth.py's module level), so in-process env injection would read the
REAL populated vault and pass by accident. In-process ``synthesize_frame(vault_root=...)``
remains available for fixture work that does not exercise the CLI default.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTH = REPO_ROOT / "scripts" / "lib" / "project_frame_synth.py"

# The three skills whose designer/critic context is fed by the project-frame.
CALL_SITES = [
    "skills/design-slice/SKILL.md",
    "skills/critique/SKILL.md",
    "skills/critique-review/SKILL.md",
]

_TOOL = "project_frame_synth.py"

# Fixture content: ASCII-only (Windows stdout is cp1252).
_IDENTITY_SENTENCE = "A populated fixture vault for the project-frame repro."
_CANDIDATE_TITLE = "fixture-pending-candidate"
_RISK_ID = "R-42"
_RISK_TITLE = "fixture-open-risk-reaches-the-frame"

_DEGRADE_MARKER = "project-frame degraded"
_NOTE_MARKER = "INFO: project-frame --"


# --------------------------------------------------------------------------
# live-parser introspection (Arm A's truth source)
# --------------------------------------------------------------------------


def _live_parser_flags() -> set[str]:
    """The flag spellings the REAL parser accepts, from `_actions[].option_strings`.

    Introspected rather than frozen: project_frame_synth is IN-TREE, so a
    hardcoded list would be a second source of truth that drifts. `_actions` --
    not `--help` -- because a `help=argparse.SUPPRESS` action is hidden from help
    yet still accepted at runtime; reading help text would make a tombstoned flag
    look deleted when it is not.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.lib.project_frame_synth import _build_parser

    return {
        opt for action in _build_parser()._actions for opt in action.option_strings
    }


# --------------------------------------------------------------------------
# the scanner (shared by Arms A, C, D)
# --------------------------------------------------------------------------


def _join_continuations(text: str) -> list[tuple[int, str]]:
    """Collapse backslash-continued lines, keeping the FIRST line's number.

    Line-wrap tolerance is load-bearing here: this slice's own call-site count had
    to be re-verified by a wrap-tolerant method, so wrapping is a KNOWN evasion in
    this corpus. A scanner that reads raw lines would miss an invocation whose
    flag sits on the continuation.
    """
    out: list[tuple[int, str]] = []
    buf: str | None = None
    start = 0
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.rstrip()
        if buf is None:
            start, buf = i, stripped
        else:
            buf = buf + " " + stripped.lstrip()
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append((start, buf))
        buf = None
    if buf is not None:
        out.append((start, buf))
    return out


def _flags_in_invocation(line: str) -> list[str] | None:
    """The flags a project_frame_synth invocation passes, or None if not one.

    Keyed on the TOOL, never on the string '--repo-root': `stranded_slice_audit`,
    `_worktree_paths` and `active_slice` all pass `--repo-root .` LEGITIMATELY --
    active_slice.py does so three lines above the buggy line in
    critique-review/SKILL.md. A string-keyed rule is a false-positive machine.
    """
    if _TOOL not in line:
        return None
    # Drop a shell tail so `|| echo ...` / `2>&1` tokens are not read as flags.
    head = re.split(r"\s+2>|\s+\|\|?", line)[0]
    try:
        tokens = shlex.split(head)
    except ValueError:
        tokens = head.split()
    idx = next((i for i, t in enumerate(tokens) if _TOOL in t), None)
    if idx is None:
        return None
    return [t for t in tokens[idx + 1 :] if t.startswith("--")]


# Redirections that ACTUALLY discard stderr. Order matters in the shell, so the
# rule reads the real semantics rather than the presence of the tokens:
#   2>/dev/null | 2> /dev/null | 2>>/dev/null   -- stderr straight to the void
#   &>/dev/null | &>>/dev/null                  -- bash shorthand, BOTH streams
#   >/dev/null 2>&1 | 1>>/dev/null 2>&1         -- stdout first, THEN stderr onto it
# Deliberately NOT here: `2>&1 >/dev/null`, which dups stderr onto stdout's CURRENT
# target (the terminal) and only THEN redirects stdout -- stderr still reaches the
# user, so flagging it would be a false positive.
_SUPPRESS_FORMS = (
    re.compile(r"2>>?\s*/dev/null"),
    re.compile(r"&>>?\s*/dev/null"),
    re.compile(r"1?>>?\s*/dev/null\s+2>&1"),
)


def _suppresses_stderr(line: str) -> bool:
    """True if THIS project_frame_synth invocation sends stderr to /dev/null.

    Tool-scoped on purpose: `_vault_paths.py --path 2>/dev/null` is the APPROVED
    idiom at design-slice:79 and must never be flagged. Arm D and
    scripts/lib/active_slice_guard_audit.py encode OPPOSITE verdicts on this same
    construct class; they coexist legitimately BECAUSE each is tool-scoped.
    """
    if _TOOL not in line:
        return False
    head = re.split(r"\s+\|\|", line)[0]
    return any(p.search(head) for p in _SUPPRESS_FORMS)


def _corpus_lines() -> list[tuple[str, int, str]]:
    """(repo-rel path, lineno, joined line) for every corpus line naming the tool.

    Markdown + agent prompts are scanned as text (wrap-joined). Manifests are
    walked as PARSED JSON -- their invocation lives in a string field, so parsing
    is both wrap-immune and free of JSON-escaping noise.
    """
    out: list[tuple[str, int, str]] = []

    md_files = sorted(REPO_ROOT.glob("skills/**/*.md")) + sorted(
        REPO_ROOT.glob("agents/*.md")
    )
    for path in md_files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in _join_continuations(path.read_text(encoding="utf-8")):
            if _TOOL in line:
                out.append((rel, lineno, line))

    for path in sorted(REPO_ROOT.glob(".build/manifests/*.json")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        found: list[str] = []

        def walk(node) -> None:
            if isinstance(node, str):
                if _TOOL in node:
                    found.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(path.read_text(encoding="utf-8")))
        for value in found:
            out.append((rel, 0, value))

    return out


# --------------------------------------------------------------------------
# Arm A -- every corpus flag exists in the live parser
# --------------------------------------------------------------------------


def test_arm_a_every_callsite_flag_exists_in_the_live_parser():
    """No call site may pass a flag the real parser does not define (AC1, AC4).

    This is the recurrence guard: it enumerates call sites DYNAMICALLY, so a 4th
    one added later -- in a skill, an agent prompt, or a manifest -- is covered
    without touching this test. It fails today on any `--repo-root`, because
    ADR-083 deleted that flag with no tombstone.
    """
    valid = _live_parser_flags()
    offenders = [
        f"{rel}:{lineno}: passes {sorted(set(flags) - valid)} -- not in the live "
        f"parser {sorted(valid)}: {line.strip()}"
        for rel, lineno, line in _corpus_lines()
        if (flags := _flags_in_invocation(line)) and set(flags) - valid
    ]
    assert not offenders, (
        f"{len(offenders)} project_frame_synth call site(s) pass a flag the parser "
        f"does not define. `--repo-root` was DELETED (ADR-083): the vault resolves "
        f"only via the _vault_paths seam, so pass NO root flag.\n"
        + "\n".join(offenders)
    )


def test_arm_a_covers_the_known_callsites():
    """The scanner must actually SEE the three known call sites.

    Without this, a corpus glob that silently matched nothing would make Arm A
    vacuously green -- the "0 findings because we looked nowhere" failure.
    """
    seen = {rel for rel, _, line in _corpus_lines() if _flags_in_invocation(line)}
    missing = [site for site in CALL_SITES if site not in seen]
    assert not missing, f"scanner did not reach known call site(s): {missing}"


# --------------------------------------------------------------------------
# Arm B -- anti-circularity anchor
# --------------------------------------------------------------------------


def test_arm_b_repo_root_is_rejected_by_the_real_cli(tmp_path: Path):
    """Passing --repo-root MUST be a usage error (rc=2).

    Arm A derives truth FROM the parser, so it cannot detect the parser
    regressing: re-add `--repo-root` and every call site's flag becomes "valid"
    again and Arm A goes green. This arm pins the parser to reality, so that
    regression fails HERE.
    """
    proc = subprocess.run(
        [sys.executable, str(SYNTH), "--repo-root", ".", "--slice-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 2, (
        "--repo-root must be rejected as a usage error (rc=2), not accepted. "
        f"Got rc={proc.returncode}.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "unrecognized arguments" in proc.stderr, (
        "expected argparse's own unrecognized-arguments error -- a hidden "
        "SUPPRESS tombstone would still ACCEPT the flag and defeat Arm A.\n"
        f"stderr:\n{proc.stderr}"
    )


# --------------------------------------------------------------------------
# Arm C -- frozen BAD/GOOD battery
# --------------------------------------------------------------------------

_BAD_FORMS = [
    # The defect itself, at each real call-site shape.
    '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$VAULT/slices/<active-slice>"',
    '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir <active-slice-folder>',
    '$PY ".../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$SDIR" 2>/dev/null || echo "(project-frame unavailable)"',
    # A SYNTHETIC 4th call site: the scanner must flag a site that does not exist
    # in the tree at all. This is what pins recurrence-proofing to a test rather
    # than to a claim.
    '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --repo-root . --slice-dir "$VAULT/slices/x"',
    # A plausible near-miss rename -- also not in the parser.
    '$PY ".../project_frame_synth.py" --vault-root "$VAULT" --slice-dir "$SDIR"',
]

_GOOD_FORMS = [
    # The corrected call sites.
    '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --slice-dir "$VAULT/slices/<active-slice>"',
    '$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/project_frame_synth.py" --slice-dir <active-slice-folder>',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" || echo "(project-frame unavailable)"',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" --max-lines 40',
    # OTHER tools legitimately taking --repo-root. active_slice.py sits three
    # lines above the buggy line in critique-review/SKILL.md.
    '$PY ".../scripts/lib/active_slice.py" --vault "$VAULT" --repo-root . --path-only',
    '$PY ".../scripts/lib/stranded_slice_audit.py" --repo-root . --json',
    '$PY ".../scripts/lib/_worktree_paths.py" --slice-folder "$SF" --repo-root "$repo_root" --print path',
    # Prose naming the tool without invoking it.
    "<stdout of project_frame_synth, or \"(project-frame unavailable)\">",
    "| `project_frame_synth` | design-slice, critique, critique-review | ephemeral project-frame (PFS-1) |",
]


def test_arm_c_scanner_catches_known_bad_forms():
    """A regex regression must fail on a CLEAN tree, not wait for a real defect."""
    valid = _live_parser_flags()
    missed = [
        bad
        for bad in _BAD_FORMS
        if not ((flags := _flags_in_invocation(bad)) and set(flags) - valid)
    ]
    assert not missed, f"scanner FAILED to flag known-bad forms (regression): {missed}"


def test_arm_c_scanner_passes_known_good_forms():
    """The scanner must not false-flag valid invocations or other tools' flags."""
    valid = _live_parser_flags()
    flagged = [
        (good, sorted(set(flags) - valid))
        for good in _GOOD_FORMS
        if (flags := _flags_in_invocation(good)) and set(flags) - valid
    ]
    assert not flagged, f"scanner FALSE-flagged valid forms: {flagged}"


# --------------------------------------------------------------------------
# Arm D -- no swallowed stderr at a project-frame call site (AC3)
# --------------------------------------------------------------------------

_BAD_SUPPRESSION_FORMS = [
    # The defect's own spelling, plus the equivalents that discard stderr just as
    # completely. A rule that pins only the one spelling it already handles proves
    # nothing (/code-review CR2).
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" 2>/dev/null || echo "(project-frame unavailable)"',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" 2> /dev/null',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" 2>>/dev/null',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" >/dev/null 2>&1',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" &>/dev/null',
]
_GOOD_SUPPRESSION_FORMS = [
    # The APPROVED idiom at design-slice:79 -- a DIFFERENT tool. A naive
    # string-keyed Arm-D rule would false-flag this.
    'VAULT="${AI_SDLC_VAULT_ROOT:-$("$PY" ".../scripts/lib/_vault_paths.py" --path 2>/dev/null)}"',
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" || echo "(project-frame unavailable)"',
    # `2>&1` BEFORE `>/dev/null` dups stderr onto stdout's CURRENT target (the
    # terminal) and only then sends stdout to the void -- stderr SURVIVES, so this
    # must NOT be flagged.
    '$PY ".../project_frame_synth.py" --slice-dir "$SDIR" 2>&1 >/dev/null',
]


def test_arm_d_no_callsite_suppresses_the_degrade_warn():
    """A degraded project-frame must be OBSERVABLE at every call site (AC3).

    A silent degrade is the whole defect: critique-review's `2>/dev/null` meant
    the DR-1 Meta-Critic reviewed a blank frame while reporting success.
    """
    offenders = [
        f"{rel}:{lineno}: {line.strip()}"
        for rel, lineno, line in _corpus_lines()
        if _suppresses_stderr(line)
    ]
    assert not offenders, (
        "project_frame_synth call site(s) send stderr to /dev/null, hiding the "
        "degrade WARN:\n" + "\n".join(offenders)
    )


def test_arm_d_suppression_rule_is_tool_scoped():
    """Frozen battery for Arm D's rule: catches the bad, spares _vault_paths."""
    missed = [f for f in _BAD_SUPPRESSION_FORMS if not _suppresses_stderr(f)]
    assert not missed, f"stderr-suppression rule MISSED: {missed}"
    flagged = [f for f in _GOOD_SUPPRESSION_FORMS if _suppresses_stderr(f)]
    assert not flagged, f"stderr-suppression rule FALSE-flagged: {flagged}"


# --------------------------------------------------------------------------
# Arm E -- signal integrity + the AC1 end-to-end frame
# --------------------------------------------------------------------------


def _write_vault(root: Path, *, concept=True, risk_register=True) -> Path:
    """A vault holding the artifacts the frame's Identity/Trajectory/Impact read.

    `methodology-changelog.md` is deliberately ABSENT -- that is what a healthy v2
    project vault looks like, and it is the condition ADR-084 reclassifies. All
    five REQUIRED sources (concept, triage, candidates, risk-register,
    mission-brief) ARE written, so "healthy" means healthy.
    """
    slice_dir = root / "slices" / "slice-999-fixture"
    slice_dir.mkdir(parents=True)
    (slice_dir / "mission-brief.json").write_text(
        json.dumps({"intent": "## Intent\nA fixture slice for the project-frame guard."}),
        encoding="utf-8",
    )
    if concept:
        (root / "concept.json").write_text(
            json.dumps({"what": f"## What\n{_IDENTITY_SENTENCE}"}), encoding="utf-8"
        )
    (root / "triage.json").write_text(json.dumps({"mode": "standard"}), encoding="utf-8")
    (root / "candidates.json").write_text(
        json.dumps({"candidates": [{"status": "candidate", "title": _CANDIDATE_TITLE}]}),
        encoding="utf-8",
    )
    if risk_register:
        # One OPEN, SCORED risk: AC1's "open risks" clause is only exercised if the
        # fixture actually has one to surface. The shape MIRRORS the production
        # register (`likelihood`/`impact` are the low|medium|high enum, NOT ints;
        # `band` accompanies `score`) -- a fixture that invents its own shape tests
        # the fixture, not the tool, and would have let this arm pass vacuously.
        (root / "risk-register.json").write_text(
            json.dumps(
                {
                    "_schema": "aisdlc/risk-register@1",
                    "project": "fixture",
                    "risks": [
                        {
                            "id": _RISK_ID,
                            "title": _RISK_TITLE,
                            "likelihood": "high",
                            "impact": "high",
                            "status": "open",
                            "reversibility": "cheap",
                            "score": 9,
                            "band": "high",
                            "mitigation": "covered by the SHIP-081 guard",
                            "discovered": {
                                "phase": "triage",
                                "at": "2026-07-17T00:00:00Z",
                                "ref": None,
                            },
                            "candidate_ref": None,
                            "notes": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    return root


def _retarget_slice_dir(flags: list[str], slice_dir: Path) -> list[str]:
    """Resolve a SKILL's `--slice-dir` placeholder to a real fixture path.

    `--slice-dir` is correctly derived from $VAULT at every call site -- it is not
    the bug -- so only its VALUE is substituted. Every other flag the SKILL
    prescribes is passed THROUGH untouched, which is what makes the arm execute
    the real invocation instead of a hand-written one.
    """
    out = list(flags)
    try:
        idx = out.index("--slice-dir")
    except ValueError:  # pragma: no cover - guarded by the caller's assert
        raise AssertionError(f"invocation has no --slice-dir: {flags}") from None
    if idx + 1 < len(out):
        out[idx + 1] = str(slice_dir)
    else:
        out.append(str(slice_dir))
    return out


def _run_synth(
    vault: Path,
    cwd: Path,
    slice_dir: Path | None = None,
    flags: list[str] | None = None,
):
    """Run the CLI in a subprocess with the vault injected via AI_SDLC_VAULT_ROOT.

    Subprocess is mandatory for the CLI-default path: VAULT_ROOT is resolved once
    at import, so in-process injection would read the REAL vault and pass by
    accident.

    `flags`, when given, is the argv the SKILL.md actually prescribes (with
    `--slice-dir` retargeted) -- so the arm runs the call site's own command. When
    omitted it falls back to the canonical minimal invocation.
    """
    import os

    env = dict(os.environ)
    env["AI_SDLC_VAULT_ROOT"] = str(vault)
    target = slice_dir or (vault / "slices" / "slice-999-fixture")
    argv = list(flags) if flags is not None else ["--slice-dir", str(target)]
    return subprocess.run(
        [sys.executable, str(SYNTH), *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def code_repo(tmp_path: Path) -> Path:
    """A stand-in for the user's code repo -- the CWD skills actually run in.

    It holds no vault JSON, which is the whole point: `--repo-root .` used to
    resolve to THIS directory at runtime.
    """
    repo = tmp_path / "code-repo"
    repo.mkdir()
    return repo


@pytest.mark.parametrize("skill_rel", CALL_SITES)
def test_arm_e_prescribed_invocation_yields_non_degraded_frame(
    skill_rel: str, tmp_path: Path, code_repo: Path
) -> None:
    """The invocation each SKILL.md prescribes must read the REAL vault (AC1).

    The flags are parsed out of the live SKILL.md and PASSED THROUGH to the
    subprocess, so this executes the corrected command rather than a stale copy --
    and fails again if this call site reintroduces a vault-relocating flag.
    """
    skill_md = REPO_ROOT / skill_rel
    assert skill_md.is_file(), f"missing call site: {skill_md}"

    # Resolve THIS skill's invocation line from the corpus scan.
    line = next(
        ln
        for rel, _, ln in _corpus_lines()
        if rel == skill_rel and _flags_in_invocation(ln) is not None
    )
    # Re-tokenize the FULL argv (not just the `--` flags Arm A inspects): the
    # invocation's values matter here, because this arm RUNS it.
    head = re.split(r"\s+2>|\s+\|\|?", line)[0]
    tokens = shlex.split(head)
    idx = next(i for i, t in enumerate(tokens) if _TOOL in t)
    argv = tokens[idx + 1 :]
    assert "--slice-dir" in argv, f"{skill_rel}: invocation lost --slice-dir: {argv}"

    vault = _write_vault(tmp_path / "vault")
    argv = _retarget_slice_dir(argv, vault / "slices" / "slice-999-fixture")
    proc = _run_synth(vault, code_repo, flags=argv)

    detail = (
        f"\ncall site: {skill_rel}"
        f"\nexecuted argv: {argv}"
        f"\nstdout:\n{proc.stdout}"
        f"\nstderr:\n{proc.stderr}"
    )

    assert proc.returncode == 0, f"frame synth failed: {proc.stderr}"
    # What "non-degraded" actually means -- widened from the single-source check.
    assert _DEGRADE_MARKER not in proc.stderr, (
        "project-frame degraded: the call site did not read the real vault." + detail
    )
    assert "concept.json unavailable" not in proc.stdout, (
        "frame degraded to a blank Identity." + detail
    )
    assert _IDENTITY_SENTENCE in proc.stdout, (
        "frame is missing the vault's real identity." + detail
    )
    assert _CANDIDATE_TITLE in proc.stdout, (
        "frame is missing the vault's pending candidates." + detail
    )
    assert _RISK_ID in proc.stdout and _RISK_TITLE[:20] in proc.stdout, (
        "frame is missing the vault's open risks." + detail
    )


def test_arm_e_healthy_vault_emits_no_degrade_but_keeps_the_note(
    tmp_path: Path, code_repo: Path
) -> None:
    """A healthy v2 vault must not cry wolf, and must not go silent either (AC5).

    Three distinct claims, because two of them were nearly shipped wrong:
      * NO degrade line -- else AC3's un-silencing ships a standing false alarm.
      * the INFO note IS on stderr -- the original design spike's throwaway patch
        appended notes to a list it never printed, so its "stderr is clean" GO was
        cashed for a design that silently dropped the note.
      * the note is NOT on stdout -- the frame is piped verbatim into the
        designer/Critic prompt and counts against the line budget.
    """
    vault = _write_vault(tmp_path / "vault")
    proc = _run_synth(vault, code_repo)
    detail = f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    assert proc.returncode == 0, detail
    assert _DEGRADE_MARKER not in proc.stderr, (
        "a healthy v2 vault emitted a degrade WARN -- an alarm that fires on the "
        "normal condition trains its own suppression, which is how `--repo-root .` "
        "survived at three call sites." + detail
    )
    assert _NOTE_MARKER in proc.stderr and "methodology-changelog.md" in proc.stderr, (
        "the optional source's absence went fully silent -- one silent degrade "
        "must not be traded for another." + detail
    )
    assert "methodology-changelog" not in proc.stdout, (
        "the INFO note leaked into the frame on stdout." + detail
    )


def test_arm_e_missing_required_source_still_degrades(
    tmp_path: Path, code_repo: Path
) -> None:
    """A genuinely missing REQUIRED source must STILL fire the degrade WARN (AC5)."""
    vault = _write_vault(tmp_path / "vault", concept=False)
    proc = _run_synth(vault, code_repo)
    detail = f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    assert proc.returncode == 0, detail
    assert _DEGRADE_MARKER in proc.stderr, (
        "a missing REQUIRED source (concept.json) did not degrade -- the "
        "reclassification must not disarm the real alarm." + detail
    )
    assert "concept.json missing" in proc.stderr, detail


def test_arm_e_missing_mission_brief_degrades(tmp_path: Path, code_repo: Path) -> None:
    """mission-brief.json is REQUIRED: its absence degrades (AC5).

    It was a 6th source in neither the REQUIRED nor the OPTIONAL bucket. This pins
    the opt-OUT DEFAULT: a source degrades unless deliberately opted out.
    """
    vault = _write_vault(tmp_path / "vault")
    slice_dir = vault / "slices" / "slice-999-fixture"
    (slice_dir / "mission-brief.json").unlink(missing_ok=True)

    proc = _run_synth(vault, code_repo, slice_dir=slice_dir)
    detail = f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    assert proc.returncode == 0, detail
    assert _DEGRADE_MARKER in proc.stderr, (
        "an absent mission-brief.json did not degrade -- it would be silently "
        "noted instead of reported." + detail
    )
    assert "mission-brief.json missing" in proc.stderr, detail


@pytest.mark.parametrize(
    "concept,drop_brief",
    [(True, False), (False, False), (True, True)],
    ids=["healthy", "missing-required", "missing-mission-brief"],
)
def test_arm_e_never_exits_one_across_the_degrade_matrix(
    concept: bool, drop_brief: bool, tmp_path: Path, code_repo: Path
) -> None:
    """degrade-never-refuse, pinned by an rc matrix rather than asserted.

    "NEVER 1" is an explicit clause of the tool's exit contract that no test
    touched. The frame is advisory context, never a gate: a frame-synth problem
    must not read as a slice regression.
    """
    vault = _write_vault(tmp_path / "vault", concept=concept)
    slice_dir = vault / "slices" / "slice-999-fixture"
    if drop_brief:
        (slice_dir / "mission-brief.json").unlink(missing_ok=True)

    proc = _run_synth(vault, code_repo, slice_dir=slice_dir)
    assert proc.returncode == 0, (
        f"degrade-never-refuse violated: rc={proc.returncode} (must be 0; NEVER 1)"
        f"\nstderr:\n{proc.stderr}"
    )
