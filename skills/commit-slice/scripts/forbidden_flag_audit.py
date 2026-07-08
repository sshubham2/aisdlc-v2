"""forbidden_flag_audit.py — static source scanner for AC1's safety floor (slice-008).

slice-004 principle: *a safety property is only real where something enforces it.*
``/commit-slice``'s `--push` / `--sync-after-pr` flows must NEVER force-push, never
force-delete a branch, and never skip git hooks. The runtime argv assertion in
``test_commit_slice_push`` covers ``pr_flow.py``'s EXECUTED commands — but it cannot
see a forbidden flag written into SKILL.md PROSE (a bash block, an inline command)
or into a helper script's own command construction. This static scanner is that
second belt: it word-boundary-scans the source text of

  - skills/commit-slice/SKILL.md
  - skills/commit-slice/scripts/pr_flow.py
  - skills/commit-slice/scripts/resolve_sync_target.py

for any whole-token occurrence of the four forbidden flags and FAILS (exit 1) if one
is present. The scanner does NOT scan itself (its own regex literals would trivially
trip it) — that exclusion is by construction (it is not in the default file list).

APED-1 (the build-time bash-executed battery the Critic mandated):
  - over-match guard: ``--force`` must NOT match inside the benign ``--force-window``.
  - under-match guard: the longer real flag ``--force-with-lease`` MUST be caught.
  - ``-D`` (force-delete) is caught; ``-d`` (safe-delete) and ``-DDEBUG`` are not.

CSP-1 CLI shape: ``--root`` (default: the plugin root, resolved off __file__),
``--files`` (override the scan set — used by tests to plant a positive), ``--json``;
exit 0 clean / 1 findings / 2 malformed args.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = Path(__file__).resolve().parents[3]  # <plugin>/skills/commit-slice/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

__all__ = ["Finding", "scan_text", "scan_file", "DEFAULT_TARGETS", "main"]

# The scanned source set, relative to --root. The scanner itself is deliberately
# absent (its regex literals are not violations).
DEFAULT_TARGETS = (
    "skills/commit-slice/SKILL.md",
    "skills/commit-slice/scripts/pr_flow.py",
    "skills/commit-slice/scripts/resolve_sync_target.py",
    # slice-057/SC-092: the local force-delete actuator is scanned too, so the floor
    # covers a previously-blind destructive script. Its ONE legitimate `-D` carries the
    # line-scoped, single-use exception below (AC3 / M-add-1).
    "skills/commit-slice/scripts/local_branch_delete.py",
)

# --- AC3 scoped exception (slice-057 / SC-092) -------------------------------------
# The ONE gh-MERGED-gated force-delete in the actuator is permitted WITHOUT weakening
# detection anywhere else. The exception is intentionally minimal and provably one line:
#   * token ``-D`` ONLY (never --force / --force-with-lease / --no-verify);
#   * ONLY in the blessed actuator file (basename below) -- a copied sentinel in SKILL.md /
#     pr_flow.py / resolve_sync_target.py is NOT honored and still FAILs (cross-file scope);
#   * ONLY on a line carrying the exact allow marker;
#   * BUDGET OF ONE across the whole scan -- a 2nd exempted ``-D`` (or an UNMARKED ``-D`` in
#     the actuator) is itself a finding (non-widening).
_BLESSED_BASENAME = "local_branch_delete.py"
_ALLOW_MARKER = "forbidden-flag-audit:allow=branch_force_delete"


def _is_blessed(finding: "Finding") -> bool:
    """True iff ``finding`` is the single permitted force-delete: token -D, in the blessed
    actuator file, on a line carrying the allow marker. All three must hold."""
    return (
        finding.token == "-D"
        and Path(finding.path).name == _BLESSED_BASENAME
        and _ALLOW_MARKER in finding.text
    )


def apply_scoped_suppressions(findings: list["Finding"]) -> list[Finding]:
    """Drop AT MOST ONE blessed force-delete finding (budget of one); everything else --
    including a 2nd blessed occurrence, an unmarked -D in the actuator, or any -D elsewhere --
    survives. Pure; order-preserving."""
    kept: list[Finding] = []
    spent = False
    for f in findings:
        if not spent and _is_blessed(f):
            spent = True
            continue
        kept.append(f)
    return kept

# Forbidden flags, longest-alternative-first so `--force-with-lease` is matched whole
# before the bare `--force` alternative can claim its prefix. The surrounding
# boundaries `(?<![\w-])` / `(?![\w-])` make each a WHOLE token: they stop `--force`
# from matching inside `--force-window` (trailing `-w`) or `--force-with-lease`
# (trailing `-w`), and stop `-D` from matching inside `--Dry` / `-DDEBUG` /
# (lowercase) `-d`.
_FORBIDDEN = re.compile(
    r"(?<![\w-])("
    r"--force-with-lease"
    r"|--force"
    r"|--no-verify"
    r"|-D"
    r")(?![\w-])"
)


class Finding(NamedTuple):
    path: str   # source file ("" for scan_text)
    line: int   # 1-based line number
    token: str  # the forbidden flag matched
    text: str   # the offending line (stripped)


def scan_text(text: str, path: str = "") -> list[Finding]:
    """Return one Finding per forbidden-flag occurrence (multiple per line possible)."""
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _FORBIDDEN.finditer(line):
            findings.append(Finding(path=path, line=lineno, token=m.group(1), text=line.strip()))
    return findings


def scan_file(path: Path) -> list[Finding]:
    """Scan one file. A missing file yields no findings (the default set may include
    a script not yet authored); an unreadable file degrades to U+FFFD, never raises."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    return scan_text(text, path=str(path))


def _resolve_targets(root: Path, files: list[str] | None) -> list[Path]:
    if files:
        return [Path(f) for f in files]
    return [root / rel for rel in DEFAULT_TARGETS]


# ----------------------------- CLI -----------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="forbidden_flag_audit",
        description=(
            "Static scanner forbidding --force / --force-with-lease / -D / --no-verify "
            "in the commit-slice source (AC1 safety floor, slice-008). Read-only."
        ),
    )
    p.add_argument("--root", type=Path, default=_REPO,
                   help="plugin root the default targets are resolved against (default: bundled root)")
    p.add_argument("--files", nargs="+", default=None,
                   help="explicit files to scan instead of the default commit-slice set")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    targets = _resolve_targets(args.root.resolve(), args.files)
    findings: list[Finding] = []
    for t in targets:
        findings.extend(scan_file(t))
    # AC3: permit the single blessed force-delete in the actuator (budget of one), never
    # weakening any other -D / --force / --force-with-lease / --no-verify occurrence.
    findings = apply_scoped_suppressions(findings)

    if args.json:
        payload = {
            "action": "forbidden-flag-audit",
            "clean": not findings,
            "findings": [f._asdict() for f in findings],
        }
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        if findings:
            sys.stdout.write("forbidden-flag-audit: FAIL\n")
            for f in findings:
                loc = f.path or "<text>"
                sys.stdout.write(f"  {loc}:{f.line}: {f.token}  |  {f.text}\n")
        else:
            sys.stdout.write("forbidden-flag-audit: clean\n")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
