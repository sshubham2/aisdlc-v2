"""Evidence-redaction audit (SCRUB-1) — plugin CI self-audit.

The README promises "captured evidence is secret-swept": every skill that runs
commands against REAL environments and persists captured output as vault
`evidence` must route that output through ``scripts/lib/secret_scrub.py``.
That contract lives only in SKILL.md prose, so a prompt edit could silently
drop it and nothing would notice — this audit is the tripwire (bug-bounty B-4).

Static check: each evidence-capturing file below must mention ``secret_scrub``
at least once. Listed files that are absent are reported as failures too (a
rename must update this list, not silently exempt the file).

Run (CI, via .build/plugin_self_audits.py):
    python scripts/lib/evidence_redaction_audit.py [--root <plugin-root>]

Exit codes: 0 clean · 1 violations · 2 usage error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402

# Files whose flow captures real-environment output into vault evidence fields.
# Adding a new evidence-writing skill? Add it here — the audit is the contract.
EVIDENCE_WRITERS: tuple[str, ...] = (
    "skills/validate-slice/SKILL.md",
    "skills/risk-spike/SKILL.md",
)

_MARKER = "secret_scrub"


def audit(root: Path) -> list[str]:
    problems: list[str] = []
    for rel in EVIDENCE_WRITERS:
        p = root / rel
        if not p.is_file():
            problems.append(f"{rel}: MISSING — evidence-writer list is stale (rename must update SCRUB-1)")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if _MARKER not in text:
            problems.append(
                f"{rel}: no reference to {_MARKER}.py — captured evidence is no longer "
                f"documented as secret-swept (README contract; bug-bounty B-4)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="evidence_redaction_audit",
        description="SCRUB-1: evidence-capturing skills must route output through secret_scrub.py.",
    )
    ap.add_argument("--root", type=Path, default=_PLUGIN_ROOT,
                    help="plugin root to audit (default: this install)")
    args = ap.parse_args(argv)
    if not args.root.is_dir():
        sys.stderr.write(f"evidence_redaction_audit: not a directory: {args.root}\n")
        return 2

    problems = audit(args.root)
    if problems:
        for pr in problems:
            print(f"SCRUB-1 FAIL  {pr}")
        return 1
    print(f"SCRUB-1: clean — {len(EVIDENCE_WRITERS)} evidence-writing file(s) reference {_MARKER}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
