"""scaffold_test_first_plan.py — the deterministic test_first_plan producer CLI (slice-051 /
SC-062 / ADR-042).

Reads a mission-brief.json, runs ``brief_variants_audit.scaffold_pending_plan`` (per-AC MERGE:
append a PENDING stub row for each declared AC not yet covered, prune only scaffolder-created
PENDING orphans, never touch a builder row), and writes the result back SAFELY. Emits a JSON
action summary so the scaffold is observable (must_not_defer), and fails VISIBLY (non-zero +
stderr) on an unreadable / invalid-JSON / missing brief — never a silent partial write.

SHARED tool (>1 skill): invoked by ABSOLUTE PATH off ``${CLAUDE_SKILL_DIR}`` —
    $PY ".../scripts/lib/scaffold_test_first_plan.py" <mission-brief.json>
by ``/slice`` Step 5.3 (PRIMARY producer, the moment test_first is chosen) and by
``/build-slice`` Step 1 (idempotent BACKSTOP for a slice opened before this existed). Both call
sites must surface/halt on a non-zero exit.

Atomic write (M-add-3): the temp file is created in the TARGET brief's OWN directory
(``tempfile.mkstemp(dir=brief.parent)``) and ``os.replace``d within it. mission-brief.json lives
in the EXTERNAL vault (``~/.aisdlc/<project>-<hash>/``), frequently on a different filesystem
than the system temp dir — and ``os.replace`` is atomic ONLY within one filesystem. A temp in
the system scratch dir would raise a cross-device ``OSError`` on Windows and the feature could
never scaffold. Same-directory temp keeps the replace genuinely atomic on any disk layout.

Exit codes: 0 = scaffolded or no-op (success) · 2 = usage / IO / parse error (fail-visible).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_REPO = pathlib.Path(__file__).resolve().parents[2]  # scripts/lib/X.py -> plugin root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402
from scripts.lib.brief_variants_audit import scaffold_pending_plan  # noqa: E402


def _atomic_write_json(path: pathlib.Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` atomically, via a temp file in ``path``'s OWN
    directory (same-filesystem -> os.replace is genuinely atomic; M-add-3)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="scaffold_test_first_plan",
        description="Scaffold the test_first_plan[] PENDING stub into a mission-brief.json "
                    "(per-AC merge; slice-051 / ADR-042).")
    p.add_argument("brief", type=pathlib.Path,
                   help="path to the slice's mission-brief.json")
    p.add_argument("--json", action="store_true", help="emit the action summary as JSON only")
    args = p.parse_args(argv)

    brief_path: pathlib.Path = args.brief
    if not brief_path.exists():
        sys.stderr.write(f"scaffold_test_first_plan: brief not found: {brief_path}\n")
        return 2
    try:
        text = brief_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"scaffold_test_first_plan: cannot read/parse {brief_path}: {exc}\n")
        return 2
    if not isinstance(data, dict):
        sys.stderr.write(f"scaffold_test_first_plan: {brief_path} top level is not a JSON object.\n")
        return 2

    before = json.dumps(data.get("test_first_plan"), sort_keys=True, ensure_ascii=False)
    data, notes = scaffold_pending_plan(data)
    after = json.dumps(data.get("test_first_plan"), sort_keys=True, ensure_ascii=False)
    changed = before != after

    if changed:
        _atomic_write_json(brief_path, data)

    plan = data.get("test_first_plan")
    summary = {
        "action": "scaffolded" if changed else "noop",
        "brief": str(brief_path),
        "rows": len(plan) if isinstance(plan, list) else 0,
        "notes": notes,
    }
    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"scaffold_test_first_plan: {summary['action']} — "
                         f"{summary['rows']} row(s). " + "; ".join(notes) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
