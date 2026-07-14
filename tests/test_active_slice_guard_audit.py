"""The guard audit's ACTUATOR (slice-069 / B2).

The design's substitute for a runtime invariant it honestly records as FAILING ("there is no path to
the scalpel that skips the check" — false: a forked agent holds the Write tool and an absolute path)
is: *the static audit makes a bypass a CI failure*. That claim was itself false. Before this file:

  * `audit_python_consumers` (Arm 2) was asserted by NOTHING — grep the tree, its only caller was
    itself. It has been dead-but-green since slice-019.
  * `main()` — the only place all arms run and the only place a non-zero exit is produced — ran in no
    workflow and had no shippability row.

A control with no actuator is not a control. So all three arms are asserted here, and — the
load-bearing half — each has a **negative fixture**: a synthetic tree that MUST be flagged. A
positive-only assertion cannot tell "the arm passed" from "the arm silently stopped matching", which
is exactly how Arm 2 stayed green while enforcing nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.active_slice_guard_audit import (  # noqa: E402
    audit,
    audit_python_consumers,
    audit_skill_guards,
)


# ── the REAL tree must be clean on all three arms ────────────────────────────────────────────────

def test_arm1_no_skill_swallows_the_resolver_stderr() -> None:
    assert audit(REPO) == [], "a SKILL.md pipes the resolver's stderr to /dev/null — the refusal is lost"


def test_arm2_every_python_consumer_handles_the_refusal_family() -> None:
    assert audit_python_consumers(REPO) == [], (
        "a python consumer calls a resolver but handles neither refusal sentinel — it will TypeError "
        "on the None path (the slice-019 crash class), or worse, act on a slice it was refused"
    )


def test_arm3_every_body_capture_has_a_halting_guard() -> None:
    assert audit_skill_guards(REPO) == [], (
        "a SKILL.md bash BODY block captures the resolver without a HALTING guard — an ownership "
        "refusal (exit 5) or an ambiguity HALT (exit 4) would sail straight past it"
    )


# ── the NEGATIVE fixtures: each arm must actually FLAG its violation ─────────────────────────────

def _skill(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_arm1_FLAGS_a_swallowed_stderr(tmp_path: Path) -> None:
    root = _skill(tmp_path, "bad", "```bash\n"
                  'x="$($PY scripts/lib/active_slice.py --vault "$V" --path-only 2>/dev/null)"\n'
                  "```\n")
    assert audit(root), "Arm 1 did not flag a `2>/dev/null` INSIDE the resolver call"


def test_arm2_FLAGS_a_by_id_only_consumer_with_no_guard(tmp_path: Path) -> None:
    """The exact gap M3 found: the old regex matched only `resolve_active_slice(`, so a consumer that
    calls ONLY `resolve_slice_by_id` was invisible to the audit — and one already exists in-tree."""
    lib = tmp_path / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "sneaky.py").write_text(
        "from scripts.lib.active_slice import resolve_slice_by_id\n"
        "info = resolve_slice_by_id(vault, 'slice-001')\n"
        "print(info['path'])   # no refusal handling at all\n", encoding="utf-8")
    assert "scripts/lib/sneaky.py" in audit_python_consumers(tmp_path), (
        "Arm 2 did not flag a by-id-only consumer that ignores the refusal sentinels"
    )


def test_arm2_ACCEPTS_the_declared_opt_out(tmp_path: Path) -> None:
    """The opt-out is legitimate (read-only orientation) but must be DECLARED, never silent."""
    lib = tmp_path / "scripts" / "lib"
    lib.mkdir(parents=True)
    (lib / "orientation.py").write_text(
        "from scripts.lib.active_slice import resolve_active_slice\n"
        "info = resolve_active_slice(vault, '.', owner_check=False)\n"
        "if info and info.get('source') == 'ambiguous':\n"
        "    info = None\n", encoding="utf-8")
    assert audit_python_consumers(tmp_path) == []


def test_arm3_FLAGS_a_body_capture_with_NO_guard(tmp_path: Path) -> None:
    root = _skill(tmp_path, "bad", "```bash\n"
                  'SDIR="$($PY scripts/lib/active_slice.py --vault "$V" --path-only)"\n'
                  'cat "$SDIR/mission-brief.json"\n'
                  "```\n")
    assert audit_skill_guards(root), "Arm 3 did not flag an unguarded body capture"


def test_arm3_FLAGS_a_NON_HALTING_guard(tmp_path: Path) -> None:
    """THE finding that made Arm 3 worth building (M6). An audit satisfied by the mere PRESENCE of an
    empty-capture check cannot distinguish `|| { exit 1; }` (a HALT) from `else echo "(hint)"` (a
    CONTINUE) — and build-slice:29 is literally the latter. An audit that green-lights a silent
    continue would certify the exact defect reality refuted."""
    root = _skill(tmp_path, "bad", "```bash\n"
                  'SDIR="$($PY scripts/lib/active_slice.py --vault "$V" --path-only)"\n'
                  'if [ -n "$SDIR" ]; then cat "$SDIR/x.json"; else echo "(no unambiguous slice)"; fi\n'
                  "```\n")
    assert audit_skill_guards(root), (
        "Arm 3 accepted a NON-HALTING empty-capture guard — it cannot tell a stop from a shrug"
    )


def test_arm3_ACCEPTS_a_halting_guard(tmp_path: Path) -> None:
    root = _skill(tmp_path, "good", "```bash\n"
                  'SDIR="$($PY scripts/lib/active_slice.py --vault "$V" --path-only)"\n'
                  'rc=$?; if [ "$rc" -ne 0 ] || [ -z "$SDIR" ]; then echo "HALT" >&2; exit "$rc"; fi\n'
                  'cat "$SDIR/mission-brief.json"\n'
                  "```\n")
    assert audit_skill_guards(root) == []


def test_arm3_EXEMPTS_an_injection_phase_capture(tmp_path: Path) -> None:
    """ADR-025/slice-036 is UN-SUPERSEDED and test-pinned: a `!`-injection runs at skill-LOAD, before
    ${ARGUMENTS} binds, so halting there makes `/build-slice slice-NNN` from a main session
    structurally impossible. Injections keep `|| true`; the BODY owns the fail-close. An audit that
    demanded a halt here would turn a green test red on day one (critique B1)."""
    root = _skill(tmp_path, "inj", "```!\n"
                  'SDIR="$($PY scripts/lib/active_slice.py --vault "$V" --path-only || true)"\n'
                  'if [ -n "$SDIR" ]; then cat "$SDIR/x.json"; else echo "(hint)"; fi\n'
                  "```\n")
    assert audit_skill_guards(root) == [], "Arm 3 wrongly demanded a HALT in a load-time injection"


# ── CR2 (code-review): the guard must be WIRED, not merely PRESENT ────────────────────────────────
#
# This audit — which IS this slice's own AC5 mechanism — GREEN-LIT a guard that was a total no-op.
# `/reflect` shipped `rc=$?` AFTER an intervening display `if`, so it read `printf`'s 0 instead of the
# resolver's 5, and (capturing `--json`, whose refusal sentinel goes to stdout) its `-z` arm was false
# too. Both arms dead; /reflect would have archived another owner's slice. The audit's own comment
# warns that an audit "satisfied by mere presence… would certify the very defect reality refuted" —
# and it then did exactly that, one variant over.

def test_arm3_FLAGS_a_guard_separated_from_its_capture(tmp_path: Path) -> None:
    """`rc=$?` reads the LAST command executed. ANY command in between — even a harmless display
    `if` — makes the guard read THAT command's 0, and the guard silently checks nothing."""
    root = _skill(tmp_path, "reflectlike", "```bash\n"
                  'if printf "%s" "$ARG" | grep -q x; then AS="$($PY scripts/lib/active_slice.py --json)"; '
                  'else AS="$($PY scripts/lib/active_slice.py --json)"; fi\n'
                  'if printf "%s" "$AS" | grep -q archive; then echo "(note)"; else printf "%s" "$AS"; fi\n'
                  'rc=$?; if [ "$rc" -ne 0 ] || [ -z "$AS" ]; then echo "HALT" >&2; exit "$rc"; fi\n'
                  "```\n")
    assert audit_skill_guards(root), (
        "Arm 3 accepted a guard whose `rc=$?` reads an INTERVENING command's status -- a total no-op. "
        "This is the exact defect (/reflect, code-review CR1) that this audit green-lit."
    )


def test_arm3_ACCEPTS_a_guard_immediately_after_its_capture(tmp_path: Path) -> None:
    """The fix shape: the guard is the FIRST statement after the capture's construct."""
    root = _skill(tmp_path, "good2", "```bash\n"
                  'if printf "%s" "$ARG" | grep -q x; then AS="$($PY scripts/lib/active_slice.py --json)"; '
                  'else AS="$($PY scripts/lib/active_slice.py --json)"; fi\n'
                  'rc=$?; if [ "$rc" -ne 0 ]; then echo "HALT" >&2; exit "$rc"; fi\n'
                  'if [ -z "$AS" ]; then echo "HALT" >&2; exit 1; fi\n'
                  'printf "%s" "$AS"\n'
                  "```\n")
    assert audit_skill_guards(root) == []


def test_arm3_is_not_fooled_by_a_trailing_comment_on_the_capture(tmp_path: Path) -> None:
    """The bug that hid CR1 from the audit: the capture line ended in `fi   # comment`, so the
    construct-end scan skipped past it and latched onto the NEXT construct's `fi` — making the
    intervening display line look like part of the capture."""
    root = _skill(tmp_path, "cmt", "```bash\n"
                  'if printf "%s" "$A" | grep -q x; then S="$($PY scripts/lib/active_slice.py --path-only)"; '
                  'else S="$($PY scripts/lib/active_slice.py --path-only)"; fi   # a trailing comment\n'
                  'echo "some intervening command"\n'
                  'rc=$?; if [ "$rc" -ne 0 ] || [ -z "$S" ]; then echo "HALT" >&2; exit "$rc"; fi\n'
                  "```\n")
    assert audit_skill_guards(root), "a trailing comment on the capture line hid an unwired guard"


def test_cli_main_reports_ALL_THREE_arms(tmp_path: Path, capsys) -> None:
    """SC-149 (found by /validate-slice's mutation test): `main()` ran Arms 1+2 only, so the CLI
    printed "clean" with a HALT guard DELETED — while the shipped code comments cite this CLI as the
    enforcer. A checker that cannot report the thing it claims to check is the exact class this slice
    exists to kill, and it was sitting inside this slice's own mechanism."""
    from scripts.lib.active_slice_guard_audit import main
    root = _skill(tmp_path, "unguarded", "```bash\n"
                  'SDIR="$($PY scripts/lib/active_slice.py --vault "$V" --path-only)"\n'
                  'cat "$SDIR/mission-brief.json"\n'
                  "```\n")
    rc = main(["--root", str(root), "--json"])
    out = capsys.readouterr().out
    assert rc == 1, "the CLI reported success with an UNGUARDED body capture"
    assert "skill_guards_missing" in out and "unguarded/SKILL.md" in out


def test_arm3_REJECTS_an_emptiness_guard_on_a_json_capture(tmp_path: Path) -> None:
    """A `--json` capture can only be guarded by the EXIT CODE: the refusal SENTINEL is printed to
    stdout (that IS the --json contract), so the variable is NON-EMPTY on a refusal and `[ -z "$x" ]`
    is provably useless. /reflect had exactly this shape and exactly this false comfort — it is half
    of CR1. A mutation test (delete the rc-guard, watch the audit still say "clean") is what exposed
    that the rule was still too weak at the one site whose bug started all this."""
    root = _skill(tmp_path, "jsoncap", "```bash\n"
                  'AS="$($PY scripts/lib/active_slice.py --vault "$V" --json)"\n'
                  'if [ -z "$AS" ]; then echo "HALT" >&2; exit 1; fi\n'
                  'printf "%s" "$AS"\n'
                  "```\n")
    assert audit_skill_guards(root), (
        "an emptiness test was accepted as the guard for a --json capture, where it can never fire"
    )


def test_arm3_ACCEPTS_an_rc_guard_on_a_json_capture(tmp_path: Path) -> None:
    root = _skill(tmp_path, "jsonok", "```bash\n"
                  'AS="$($PY scripts/lib/active_slice.py --vault "$V" --json)"\n'
                  'rc=$?; if [ "$rc" -ne 0 ]; then echo "HALT" >&2; exit "$rc"; fi\n'
                  'printf "%s" "$AS"\n'
                  "```\n")
    assert audit_skill_guards(root) == []
