"""write_changelog.py — emit the per-slice changelog.json into the archived slice
folder (v2, NEW).

Single-skill helper for `/commit-slice`. `/reflect` archives the completed slice
BEFORE `/commit-slice` runs, so by commit time the slice lives at
`<vault>/slices/archive/slice-NNN-<name>/`. This writes one structured, audit-grade
record of what the slice changed — the JSON twin of the rendered commit message —
right beside the slice's other artifacts. Single-shot write (per-slice file, single
writer → no SVW-1 append channel needed).

The record is the `/commit-slice` Step 2 dict plus the rendered subject, supplied as
JSON on `--record-file PATH` or stdin. Recognised keys (all optional except `slice`
which `--slice` overrides): type, scope, slice/slice_id, intent/intent_one_line,
body/body_2_3_sentences, subject, ac_pass, ac_total, critic_blockers, adrs[],
shippability_entry_n, shippability_entry_text, deferrals, regressions.

Output: `<slice-dir>/changelog.json` (schema `aisdlc/changelog@1`). Prints the path
(or a `--json` summary). Exit 0 success · 2 usage error (missing record / slice not
found on disk).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `-m`) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]  # skills/commit-slice/scripts -> plugin root
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402

SCHEMA = "aisdlc/changelog@1"


def _load_record(args) -> dict:
    if args.record_file:
        raw = Path(args.record_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raw = ""
    raw = raw.strip()
    if not raw:
        return {}
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("record must be a JSON object")
    return obj


def _first(rec: dict, *keys, default=None):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
    return default


def _resolve_slice_dir(vault: Path, slice_id: str) -> Path | None:
    """Prefer the archived folder (the normal post-/reflect state); fall back to the
    active folder for a mid-slice commit. Return None if neither exists."""
    for cand in (vault / "slices" / "archive" / slice_id, vault / "slices" / slice_id):
        if cand.is_dir():
            return cand
    return None


def build_changelog(rec: dict, slice_id: str, mode: str, at: str) -> dict:
    adrs = _first(rec, "adrs", default=[]) or []
    if isinstance(adrs, str):
        adrs = [a.strip() for a in adrs.replace(",", " ").split() if a.strip()]
    out = {
        "_schema": SCHEMA,
        "slice": slice_id,
        "at": at,
        "type": _first(rec, "type", default="feat"),
        "scope": _first(rec, "scope", default=""),
        "subject": _first(rec, "subject", default=""),
        "intent": _first(rec, "intent", "intent_one_line", default=""),
        "body": _first(rec, "body", "body_2_3_sentences", default=""),
        "acceptance": {
            "pass": _first(rec, "ac_pass", default=None),
            "total": _first(rec, "ac_total", default=None),
        },
        "critic_blockers": _first(rec, "critic_blockers", default="none"),
        "adrs": adrs,
        "shippability_entry": {
            "n": _first(rec, "shippability_entry_n", default=None),
            "text": _first(rec, "shippability_entry_text", default=""),
        },
        "deferrals": _first(rec, "deferrals", default=None),
        "regressions": _first(rec, "regressions", default=None),
        "mode": mode,
        "committed": bool(rec.get("committed", mode in ("merge", "push"))),
        "merged": bool(rec.get("merged", mode == "merge")),
    }
    return out


def main(argv=None) -> int:
    _stdout.reconfigure_stdout_utf8()  # UTF8-STDOUT-1
    _stdout.reconfigure_stdin_utf8()   # SC-015: decode the piped record as utf-8 (stdin twin)
    ap = argparse.ArgumentParser(description="Write the per-slice changelog.json.")
    ap.add_argument("--vault", required=True, help="vault root (<vault>/)")
    ap.add_argument("--slice", required=True, dest="slice_id",
                    help="archived slice folder name, e.g. slice-023-add-receipt-ocr")
    ap.add_argument("--mode", default="none",
                    choices=["none", "merge", "push", "sync-after-pr"],
                    help="commit-slice mode that produced this record")
    ap.add_argument("--record-file", default=None,
                    help="JSON record path (default: read stdin)")
    ap.add_argument("--at", default=None, help="ISO timestamp override (default: now UTC)")
    ap.add_argument("--json", action="store_true", help="emit a JSON summary to stdout")
    args = ap.parse_args(argv)

    try:
        rec = _load_record(args)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"write_changelog: cannot read record: {e}", file=sys.stderr)
        return 2

    vault = Path(args.vault).expanduser()
    slice_dir = _resolve_slice_dir(vault, args.slice_id)
    if slice_dir is None:
        print(f"write_changelog: slice folder not found under "
              f"{vault / 'slices' / 'archive'} or {vault / 'slices'} for "
              f"'{args.slice_id}' (run /reflect to archive the slice first)",
              file=sys.stderr)
        return 2

    at = args.at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    changelog = build_changelog(rec, args.slice_id, args.mode, at)

    out_path = slice_dir / "changelog.json"
    out_path.write_text(json.dumps(changelog, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    if args.json:
        print(json.dumps({"written": str(out_path), "slice": args.slice_id,
                          "mode": args.mode, "at": at}, ensure_ascii=False))
    else:
        print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
