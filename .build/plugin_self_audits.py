#!/usr/bin/env python3
"""Plugin self-audits — the six model-on-static-file checks that grade the PLUGIN's
own source, relocated here out of the per-slice /build-slice pre-finish gate
(remediation-plan item 1.5).

Why they moved: in a USER project these six are either no-ops (BCI-1/STP-1/NAW-1 find
no user-side artifacts) or re-scan the plugin install on every slice (UTF8-STDOUT-1 /
PCA-1 / SVW-1 — a constant result per plugin version, zero user value, ~109 KB of
script + 6 subprocess spawns per slice). They belong in plugin CI, run ONCE per change
to the plugin's own files — not on a user's build.

Each underlying script defaults its own --root to the plugin install dir (parents[3]
of the script), so no arguments are needed. This aggregator exits non-zero if ANY of
the six fails, so CI (remediation-plan item 4.4) can gate on a single command.

Run:  python .build/plugin_self_audits.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # <plugin>/.build/plugin_self_audits.py -> <plugin>
PY = sys.executable

# (label, script path relative to the plugin root). These are the six evicted from
# skills/build-slice/SKILL.md Step 6.
AUDITS: list[tuple[str, str]] = [
    ("UTF8-STDOUT-1", "skills/build-slice/scripts/utf8_stdout_audit.py"),
    ("PCA-1",         "skills/build-slice/scripts/pipeline_chain_audit.py"),
    ("BCI-1",         "scripts/lib/build_checks_integrity.py"),
    ("STP-1",         "skills/build-slice/scripts/state_transition_pin_audit.py"),
    ("NAW-1",         "skills/build-slice/scripts/new_agent_warning_audit.py"),
    ("SVW-1",         "scripts/lib/skill_vault_write_safety_audit.py"),
]


def main() -> int:
    failures: list[tuple[str, int]] = []
    missing: list[str] = []
    for label, rel in AUDITS:
        script = ROOT / rel
        print(f"\n=== {label} :: {rel} ===", flush=True)
        if not script.is_file():
            print(f"  MISSING: {script}")
            missing.append(label)
            continue
        cp = subprocess.run([PY, str(script)])
        if cp.returncode != 0:
            failures.append((label, cp.returncode))

    print("\n" + "=" * 60)
    if missing:
        for label in missing:
            print(f"MISSING  {label}")
    for label, rc in failures:
        print(f"FAIL  {label}  (exit {rc})")
    bad = len(failures) + len(missing)
    if bad:
        print(f"\n{bad} of {len(AUDITS)} plugin self-audit(s) did not pass.")
        return 1
    print(f"All {len(AUDITS)} plugin self-audits PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
