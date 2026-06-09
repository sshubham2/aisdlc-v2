"""build_entry.py — serialize a drift-log entry for `vault_edit append` (v2, NEW).

Single-skill helper for `/drift-check` (full mode + `--resolve` accept-drift). Builds a
well-formed `drift-log.json` entry (schema `aisdlc/drift-log@1`) with a real timestamp
and a canonical `slice-NNN` trigger, then emits it for the SVW-1 append channel — so
the skill never hand-rolls JSON with fragile shell quoting.

Two output modes:
  - default: print the entry JSON to STDOUT → pipe straight into
    `vault_edit append --file drift-log.json --array entries --stdin` (no temp file).
  - `--out PATH`: write the entry to PATH and print PATH (for the
    `vault_edit append --content-file <path>` form).

Entry shape: `{at, trigger, category, finding[, resolution][, action][, rationale]}`.
`category` ∈ {drift, unspecified-code, stale-claim, stale-doc}. `trigger` is canonicalized to
`slice-NNN` (DCE-1 matches that pattern); a non-slice trigger passes through verbatim.
Optional fields are omitted (never written as null). `--action accept-drift` REQUIRES
`--rationale` (must reference a next-action slice per the /drift-check contract).

Exit 0 success · 2 usage error (bad category, missing required, accept-drift w/o rationale).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

_CATEGORIES = {"drift", "unspecified-code", "stale-claim", "stale-doc"}
_SLICE_RE = re.compile(r"^(slice-\d+)(?:-.+)?$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon_trigger(trigger: str | None) -> str | None:
    if not trigger:
        return None
    m = _SLICE_RE.match(trigger.strip())
    return m.group(1) if m else trigger.strip()  # slice-NNN-name -> slice-NNN; else verbatim


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_entry",
        description="Serialize a drift-log.json entry for vault_edit append (/drift-check).",
    )
    p.add_argument("--category", required=True, help="drift | unspecified-code | stale-claim | stale-doc")
    p.add_argument("--finding", required=True, help="the drift finding text")
    p.add_argument("--trigger", default=None, help="slice-NNN (canonicalized) or other trigger label")
    p.add_argument("--resolution", default=None, help="how it was resolved (optional)")
    p.add_argument("--action", default=None, help="e.g. accept-drift (optional)")
    p.add_argument("--rationale", default=None,
                   help="rationale (REQUIRED with --action accept-drift; reference a next-action slice)")
    p.add_argument("--at", default=None, help="ISO-8601 timestamp (default: now, UTC)")
    p.add_argument("--out", default=None, help="write the entry to this file and print the path "
                                               "(default: print the entry JSON to stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    category = args.category.strip()
    if category not in _CATEGORIES:
        sys.stderr.write(f"build_entry: --category must be one of {sorted(_CATEGORIES)} (got {category!r})\n")
        return 2
    if args.action == "accept-drift" and not (args.rationale and args.rationale.strip()):
        sys.stderr.write("build_entry: --action accept-drift requires --rationale "
                         "(reference a planned reconciliation slice)\n")
        return 2

    entry: dict = {
        "at": (args.at.strip() if args.at else _now_iso()),
        "trigger": _canon_trigger(args.trigger),
        "category": category,
        "finding": args.finding,
    }
    if entry["trigger"] is None:
        del entry["trigger"]
    for k in ("resolution", "action", "rationale"):
        v = getattr(args, k)
        if v is not None and str(v).strip():
            entry[k] = v

    payload = json.dumps(entry, ensure_ascii=False)
    if args.out:
        try:
            Path(args.out).write_text(payload + "\n", encoding="utf-8", newline="")
        except OSError as exc:
            sys.stderr.write(f"build_entry: cannot write --out {args.out}: {exc}\n")
            return 2
        print(args.out)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
