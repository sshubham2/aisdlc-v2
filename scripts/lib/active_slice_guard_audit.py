"""active_slice_guard_audit.py — AC4 guard (slice-014 / SC-23).

The hardened resolver (`active_slice.py`) fail-visibly **exits 4** and prints an
AMBIGUOUS HALT on **stderr** when >=2 slices are in flight and no slice is designated
(ADR-010). A SKILL.md injection that pipes the resolver call to `2>/dev/null` would
DISCARD that HALT and silently skip -- recreating the silent no-op the slice exists to
kill. This audit asserts that NO `active_slice.py` injection swallows the resolver's
stderr, so the fail-visible refusal always reaches the agent.

It flags a `2>/dev/null` that lands INSIDE the `active_slice.py $(...)` call (before its
closing paren). A `2>/dev/null` on a WRAPPING command (after the `)`, e.g. a project-frame
or audit call that consumes the resolved path) is fine and is NOT flagged.

CLI: `[--root <plugin-root>] [--json]`. Exit 0 = clean, 1 = violations found. Read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# <plugin>/scripts/lib/active_slice_guard_audit.py -> <plugin>
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout

# The swallow: `2>/dev/null` reached without first closing the active_slice.py `$(...)`
# (i.e. `[^)]*` consumes only up to the first ')' which is the call's own close).
_SWALLOW = re.compile(r"active_slice\.py[^)]*2>\s*/dev/null")


def audit(root: str | Path) -> list[str]:
    """Return a sorted list of `skills/<name>/SKILL.md:LINE` sites where an
    `active_slice.py` injection swallows the resolver stderr. Empty list = clean."""
    root = Path(root)
    violations: list[str] = []
    for md in sorted((root / "skills").glob("*/SKILL.md")):
        for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            # strip an inline ` # ...` comment first so prose mentioning 2>/dev/null in a
            # comment is never mistaken for an actual stderr-swallowing redirect.
            code = re.sub(r"\s+#.*$", "", line)
            if _SWALLOW.search(code):
                violations.append(f"{md.relative_to(root).as_posix()}:{i}")
    return violations


# slice-019 (AC4 / M4): the PYTHON consumer family of resolve_active_slice. A file that CALLS the
# resolver MUST handle the AMBIGUOUS sentinel (it checks source=='ambiguous' before dereferencing
# info['path']); a caller that never mentions 'ambiguous' TypeErrors on the truthy None-path sentinel
# (the slice-019 reflection_lookup/vault_snapshot crash). The roster is derived PROGRAMMATICALLY (every
# importer), so a FUTURE consumer can't silently skip the guard -- slice-016: audit the family, don't hope.
# slice-069 / M3: the family is BOTH resolvers. The refusal sentinel now flows through
# `resolve_slice_by_id` too, so an Arm-2 regex that only matched `resolve_active_slice(` audited the
# wrong family: a by-id-ONLY consumer (skills/commit-slice/scripts/resolve_sync_target.py is already
# one) was invisible to it, and the next one would TypeError on the None path -- the slice-019 crash
# class this arm exists to prevent.
_CALL = re.compile(r"\b(resolve_active_slice|resolve_slice_by_id)\s*\(")
# A consumer must handle the REFUSAL FAMILY -- either sentinel. `is_refused(...)` is the one predicate
# that covers both, so mentioning it satisfies the check; naming both raw tokens does too.
_GUARD_TOKENS = ("is_refused", "ownership-refused")
_AMBIGUOUS_TOKEN = "ambiguous"
# The AUDITED opt-out: a consumer may skip the ownership check ONLY by declaring owner_check=False
# (read-only orientation, or an explicitly deferred write designation). Declared, never silent.
_OPT_OUT = re.compile(r"owner_check\s*=\s*False")
_NOT_CONSUMERS = {"active_slice.py", "active_slice_guard_audit.py", "slice_ownership.py"}


def audit_python_consumers(root: str | Path) -> list[str]:
    """Sorted list of `<relpath>` python files that CALL a resolver but never handle a refusal.

    A consumer must handle BOTH refusal sentinels — AMBIGUOUS (slice-014) and OWNERSHIP-REFUSED
    (slice-069) — because both are TRUTHY dicts carrying `path: None`. It satisfies this by using the
    shared `is_refused(...)` predicate, or by naming the tokens, or (for the ownership half) by
    explicitly declaring the audited `owner_check=False` opt-out. Empty list = clean.
    """
    root = Path(root)
    pyfiles = list((root / "scripts" / "lib").glob("*.py")) + list((root / "skills").glob("*/scripts/*.py"))
    out: list[str] = []
    for py in sorted(pyfiles):
        if py.name in _NOT_CONSUMERS:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _CALL.search(text):
            continue
        handles_ambiguous = _AMBIGUOUS_TOKEN in text
        handles_ownership = any(t in text for t in _GUARD_TOKENS) or _OPT_OUT.search(text)
        if not (handles_ambiguous and handles_ownership):
            out.append(py.relative_to(root).as_posix())
    return out



# ── ARM 3: the CALL-SITE HALT GUARD (slice-069 / ADR-072; critique B1 + M6) ──────────────────────
#
# The refusal channel (empty machine-mode stdout + a distinct exit code) is INAUDIBLE on its own --
# the round-1 design spike proved it end-to-end: nothing checks the exit code, `_worktree_paths.py`'s
# honest exit-2 is masked by `| head -1` (a pipeline's status is the LAST command's), `slice_diff_base`
# used to hand back a valid base for a bogus worktree, and `git -C ""` silently operates on the MAIN
# REPO. A refusal became a review of the wrong tree, reported clean. So the guard at the call site --
# not the exit code -- is what makes the refusal real, and THIS arm is what keeps the guard from
# rotting the moment someone adds the next call site.
#
# The rule is PHASE-aware AND HALT-aware, and both halves are load-bearing:
#
#   PHASE  -- only `bash`/`sh` BODY blocks are required to halt. A ```! INJECTION runs at skill-LOAD,
#             before ${ARGUMENTS} binds; halting there aborts the load and makes `/build-slice
#             slice-NNN` from a main session structurally impossible. That is ADR-025/slice-036, it
#             is un-superseded, and it is pinned by a live test. Injections keep `|| true` + a
#             non-halting hint branch, and the BODY owns the fail-close.
#
#   HALT   -- the guard's refusal branch must actually EXIT. An audit satisfied by the mere presence
#             of `[ -n "$SDIR" ]` cannot tell `|| { echo ...; exit 1; }` (a HALT) from
#             `else echo "(hint)"` (a CONTINUE) -- and build-slice:29 is exactly the latter. An audit
#             that green-lights a silent continue would certify the very defect reality refuted.
FENCE = re.compile(r"^\s*```(.*)$")
RE_INV = re.compile(r"scripts/lib/active_slice(?:_brief)?\.py")
# a capture: `x="$(... active_slice.py ...)"`. A BARE invocation (no capture) prints for the AGENT to
# read; it has no variable to guard, so it is exempt from the capture rule (its SKILL.md prose must
# tell the agent to STOP on a refusal -- prose, not a shell guard).
RE_CAPTURE = re.compile(r"=\s*\"?\$\(")
RE_HALT = re.compile(r"\b(exit|return)\b")
# The guard itself: it must READ the captured status (`rc=$?`) or test the captured VAR for
# emptiness. Merely containing an `exit` somewhere nearby is not a guard (CR2).
RE_GUARD = re.compile(r"rc=\$\?|\[\s+-z\s+\"?\$")
# A `--json` capture can ONLY be guarded by the exit code: its refusal SENTINEL goes to stdout,
# so the variable is NON-EMPTY on a refusal and `[ -z "$var" ]` is provably useless there.
RE_RC = re.compile(r"rc=\$\?")
# where a capture's construct ends: a trailing `fi`, or a line that IS `fi`.
RE_FI_END = re.compile(r"\bfi\b\s*$")
# if/else scaffolding a capture legitimately sits inside -- skipped when looking for the guard.
RE_SCAFFOLD = re.compile(r"^(else|elif\b|fi\b|then\b|\{|\})")
RE_FI_LINE = re.compile(r"^\s*fi\b")


def _parse_blocks(text: str) -> list[dict]:
    """Fence state-machine (lifted verbatim from tests/bugs/test_skill_active_slice_explicit_arg.py,
    where it is already trusted and proven against every real SKILL.md): tag each fenced block by its
    opening info-string. `!` => injection (load-time), `bash`/`sh` => body (step-time), else other."""
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
                blocks.append({"phase": phase, "start": start, "text": "\n".join(buf)})
                info = None
        elif info is not None:
            buf.append(line)
    return blocks


def audit_skill_guards(root: str | Path) -> list[str]:
    """Sorted `skills/<name>/SKILL.md:LINE` sites where a BODY-block resolver CAPTURE is not followed
    by a HALTING guard. Empty list = clean.

    A site is clean when, within the same body block, the capture is followed by a guard whose
    refusal branch contains `exit` or `return` -- i.e. the block genuinely stops. Injection-phase
    captures are EXEMPT BY PHASE (ADR-025). Bare invocations are exempt (nothing is captured).
    """
    root = Path(root)
    out: list[str] = []
    for md in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = md.relative_to(root).as_posix()
        for blk in _parse_blocks(text):
            if blk["phase"] != "body":
                continue                      # injections: ADR-025 exempt; other: not shell
            lines = blk["text"].splitlines()
            for i, line in enumerate(lines):
                if not (RE_INV.search(line) and RE_CAPTURE.search(line)):
                    continue
                # The guard must be WIRED, not merely PRESENT (slice-069 /code-review CR2 — this audit
                # green-lit a guard that was a total no-op, which is the exact failure its own comment
                # warns about; a nearby `exit` proves nothing).
                #
                # `rc=$?` reads the status of the LAST COMMAND EXECUTED. If ANY command runs between
                # the capture and the guard -- even a harmless display `if`/`printf` -- then `rc` is
                # that command's status (0), and the guard silently checks nothing. /reflect shipped
                # exactly that: a display branch sat in between, so its guard read `printf`'s 0 while
                # the resolver had exited 5.
                #
                # So: find where the capture's construct ENDS (its own line, or the closing `fi` of the
                # if/else it sits in), then require the very NEXT executable line to BE the guard.
                # The guard must be WIRED, not merely PRESENT (slice-069 /code-review CR2 -- this
                # audit GREEN-LIT a guard that was a total no-op, which is the exact failure its own
                # comment warns about; an `exit` somewhere nearby proves nothing).
                #
                # `rc=$?` reads the status of the LAST COMMAND EXECUTED. If ANY command runs between
                # the capture and the guard -- even a harmless display `if`/`printf` -- then `rc` is
                # THAT command's status (0), and the guard silently checks nothing. /reflect shipped
                # exactly that: a display branch sat in between, so its guard read `printf`'s 0 while
                # the resolver had exited 5.
                #
                # So: walk forward past the if/else SCAFFOLDING that the capture sits inside (`else`,
                # `elif`, a bare closing `fi`, a sibling capture of the same variable) and require the
                # first REAL statement after it to BE the guard.
                nxt = None
                for j in range(i + 1, len(lines)):
                    code = re.sub(r"\s+#.*$", "", lines[j]).strip()
                    if not code or code.startswith("#"):
                        continue
                    if RE_SCAFFOLD.match(code):
                        continue
                    if RE_INV.search(code) and RE_CAPTURE.search(code):
                        continue          # the else-branch's sibling capture
                    nxt = code
                    break
                # A `--json` capture may ONLY be guarded by the EXIT CODE. Its refusal SENTINEL is
                # printed to stdout (that is the --json contract), so the variable is NON-EMPTY on a
                # refusal and an `[ -z "$var" ]` test is provably useless there. /reflect had exactly
                # this shape and exactly this false comfort -- it is half of CR1, and a mutation test
                # (deleting its rc-guard and watching this audit still say "clean") is what exposed
                # that the rule was too weak at the one site whose bug started all this.
                need = RE_RC if "--json" in line else RE_GUARD
                if not (nxt and need.search(nxt) and RE_HALT.search(nxt)):
                    out.append(f"{rel}:{blk['start'] + 1 + i}")
    return sorted(set(out))

def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="active_slice_guard_audit",
        description="AC4 guard: no SKILL.md injection may 2>/dev/null-swallow the active_slice resolver stderr.",
    )
    p.add_argument("--root", default=str(_PLUGIN_ROOT), help="plugin root (default: derived from this file)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    # ARM 3 IS RUN HERE TOO (slice-069 / SC-149, found by /validate-slice's mutation test).
    # main() used to call Arms 1+2 only -- so the CLI printed "clean" with a HALT guard deleted, while
    # the shipped code comments cite THIS CLI as the enforcer. Nothing in the loop invokes it (pytest
    # is the actuator, and the mutation test proved that ratchet IS engaged), so it was never a
    # false-green in a gate -- but a checker that cannot report the thing it claims to check is the
    # exact class this slice exists to kill, and it was sitting inside this slice's own mechanism.
    violations = audit(args.root)
    py_unguarded = audit_python_consumers(args.root)
    unguarded_sites = audit_skill_guards(args.root)
    clean = not violations and not py_unguarded and not unguarded_sites
    if args.json:
        print(json.dumps({"stderr_swallow": violations, "python_unguarded": py_unguarded,
                          "skill_guards_missing": unguarded_sites, "clean": clean}, ensure_ascii=False))
    else:
        if violations:
            print("AC4 guard FAIL -- active_slice.py injections that SWALLOW the resolver stderr "
                  "(an AMBIGUOUS exit-4 HALT would be discarded -> silent skip):")
            for v in violations:
                print(f"  {v}")
        if py_unguarded:
            print("AC4 guard FAIL -- resolver PYTHON consumers missing the refusal guard "
                  "(they would TypeError on the truthy None-path sentinel, or act on a refused slice):")
            for v in py_unguarded:
                print(f"  {v}")
        if unguarded_sites:
            print("AC5 guard FAIL -- SKILL.md bash BODY captures with no WIRED halting guard "
                  "(an ownership refusal (exit 5) or an AMBIGUOUS HALT (exit 4) would sail past):")
            for v in unguarded_sites:
                print(f"  {v}")
        if clean:
            print("active-slice guards: clean -- no stderr-swallow; every resolver python consumer "
                  "handles the refusal family; every SKILL.md body capture has a wired halting guard.")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
