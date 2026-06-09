"""phase0_manifest.py — wire the Phase 0 gate-log measurement spine into the
design-record manifests (reproducible; run once, idempotent).

Adds, to each of the six GATE skills' `.build/manifests/batch*.json` entry:
  - a `gate_log` methodology tool (emit one gate-outcome row),
  - a `vault_edit` methodology tool (SVW-1 append of the row) — only when the skill
    does not already reference vault_edit (aggregate dedups tools by name anyway),
  - a `<vault>/gate-log.json` file_access entry (access=append, vault_edit-append).
And to `pulse`: a `<vault>/gate-log.json` read file_access entry (Phase 0.3 hit-rate).

Idempotent: re-running makes no further change. After this, run `.build/aggregate.py`
to regenerate skill.json + examples/ + skill-graph.json from the updated manifests.
"""
from __future__ import annotations

import json
import pathlib

MANIFESTS = pathlib.Path(__file__).resolve().parent / "manifests"
GATES = {"risk-spike", "critique", "critique-review", "code-review",
         "validate-slice", "drift-check"}
GATELOG_PATH = "<vault>/gate-log.json"


def gate_log_tool(gate: str) -> dict:
    return {
        "name": "gate_log",
        "invocation": (
            f'$PY "${{CLAUDE_SKILL_DIR}}/../../scripts/lib/gate_log.py" '
            f'--gate {gate} --slice slice-NNN --verdict <verdict> --findings-count <N> '
            f'| $PY "${{CLAUDE_SKILL_DIR}}/../../scripts/lib/vault_edit.py" '
            f'append --file gate-log.json --array entries --stdin'
        ),
        "purpose": ("Emit + SVW-1-append one gate-outcome row to gate-log.json "
                    "(measurement spine, roadmap Theme 8 / plan Phase 0)"),
    }


VAULT_EDIT_TOOL = {
    "name": "vault_edit",
    "invocation": ('$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" '
                   'append --file gate-log.json --array entries --stdin'),
    "purpose": "SVW-1 locked append of the gate-outcome row to gate-log.json (Phase 0)",
}

GATELOG_FA_APPEND = {
    "path": GATELOG_PATH,
    "kind": "content-file",
    "access": "append",
    "mechanism": "vault_edit-append",
    "safety_marker": "<!-- route: scripts.lib.vault_edit append -->",
    "modes": ["minimal", "standard", "heavy"],
    "format": "json",
    "purpose": ("Append one gate-outcome row per slice "
                "(measurement spine, roadmap Theme 8 / plan Phase 0)"),
}

GATELOG_FA_READ = {
    "path": GATELOG_PATH,
    "kind": "content-file",
    "access": "read",
    "mechanism": "read",
    "safety_marker": None,
    "modes": ["minimal", "standard", "heavy"],
    "format": "json",
    "purpose": ("Per-gate outcome log — read fully to compute the per-gate "
                "hit-rate in /pulse (Phase 0.3)"),
}


def _has_tool(m: dict, name: str) -> bool:
    return any(t.get("name") == name for t in m.get("methodology_tools", []))


def _has_fa(m: dict, path: str) -> bool:
    return any(e.get("path") == path for e in m.get("file_access", []))


def main() -> int:
    changed_files = []
    for bf in sorted(MANIFESTS.glob("batch*.json")):
        data = json.loads(bf.read_text(encoding="utf-8"))
        dirty = False
        for m in data:
            name = m.get("name")
            if name in GATES:
                mt = m.setdefault("methodology_tools", [])
                if not _has_tool(m, "gate_log"):
                    mt.append(gate_log_tool(name)); dirty = True
                if not _has_tool(m, "vault_edit"):
                    mt.append(dict(VAULT_EDIT_TOOL)); dirty = True
                if not _has_fa(m, GATELOG_PATH):
                    m.setdefault("file_access", []).append(dict(GATELOG_FA_APPEND)); dirty = True
            elif name == "pulse":
                if not _has_fa(m, GATELOG_PATH):
                    m.setdefault("file_access", []).append(dict(GATELOG_FA_READ)); dirty = True
        if dirty:
            # manifests are CRLF on disk — preserve EOL so the diff is the inserts only
            bf.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\r\n")
            changed_files.append(bf.name)
            print(f"updated {bf.name}")
    if not changed_files:
        print("no changes — already wired (idempotent no-op)")
    else:
        print(f"\n{len(changed_files)} batch file(s) updated: {', '.join(changed_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
