"""slice_index_regen.py — deterministic slice-index regenerator (v2, NEW; slice-030 / SC-008).

Shared library + CLI. The single DETERMINISTIC source of CONTENT for both index files:
`slices/_index.json` (live: active[] + recent-10 + pointers) and `slices/archive/_index.json`
(the full chronological catalog). Both are computed PURELY from the slice folders on disk (the
source of truth), so the index is a pure projection that cannot silently drift (ADR-020 / SC-008).

Read-only: it produces CONTENT (returned dicts, or `--emit` to stdout); the CALLER (/reflect Step
6.2-6.3, /archive Step 3) owns the `vault_edit` CAS write. Defensive: a malformed/missing/legacy
folder yields a best-effort row and is NEVER dropped; a missing/empty `slices/` dir yields an
empty-but-valid index (no crash). Idempotent: the ONLY non-deterministic field, `updated`, is
CALLER-SUPPLIED (`--updated`) so re-runs are byte-identical modulo that stamp.

The per-entry shapes + the 8 live top-level keys conform to the canonical schema-by-example
(`schemas/artifact-examples.json` / the bundled `examples/`); the stage value uses the file-presence
rule shared verbatim with `/archive` Step 3 + `/pulse` (so an active slice can never carry the
out-of-enum legacy `reflect` stage — slice-030 m1).

CLI: `--vault ROOT --emit live|archive [--updated ISO]`. Exit 0 always (an empty vault is a normal
early state, not an error).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md; a skill runs
# in the user's CWD, not the plugin root, and SKILL.md cannot use `python -m` / `${CLAUDE_PLUGIN_ROOT}`). ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/slice_index_regen.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.latest_archived_slice import _read_milestone  # m4: REUSE one named copy, do NOT re-copy

_SLICE_FOLDER_RE = re.compile(r"^slice-(\d+)-(.+)$")
_VALID_MODES = ("minimal", "standard", "heavy")
_DEFAULT_MODE = "standard"
_RECENT_CAP = 10
_ACTION_POINTS_REF = "<vault>/slices/action-points.json"
_ARCHIVE_REF = "<vault>/slices/archive/_index.json"


def _read_json(p: Path) -> dict:
    """Defensive JSON read: missing/malformed/non-dict -> {} (never raises)."""
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _derive_stage(folder: Path) -> str | None:
    """Canonical file-presence stage rule, shared verbatim with /archive Step 3 + /pulse (highest
    present file wins). reflection.json -> 'complete' (a valid milestone-stage enum value). Because
    active slices have NO reflection.json, the 'complete' row is unreachable for active[] -- so this
    can never emit the out-of-enum legacy 'reflect' value an active milestone.json might carry (m1)."""
    if (folder / "reflection.json").is_file():
        return "complete"
    if (folder / "validation.json").is_file():
        return "validate"
    if (folder / "build-log.json").is_file():
        return "build"
    if (folder / "critique.json").is_file():
        return "critique"
    if (folder / "design.json").is_file():
        return "design"
    if (folder / "mission-brief.json").is_file():
        return "spike"
    return None


def _first_sentence(intent, limit: int = 500) -> str:
    """First sentence of the mission-brief `intent`, with any leading markdown header line(s)
    (e.g. '## Intent') stripped, capped at `limit` chars. Splits ONLY on sentence-ending
    punctuation FOLLOWED BY whitespace, so a code-ref token like `slices/_index.json` is never
    truncated mid-token at its internal '.' (m3)."""
    if not isinstance(intent, str):
        return ""
    lines = intent.splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    text = " ".join(ln.strip() for ln in lines).strip()
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first[:limit]


def _scan(dirpath: Path) -> list[Path]:
    """Slice folders directly under `dirpath` (sorted by name; the nested `archive` dir skipped)."""
    if not dirpath.is_dir():
        return []
    return [p for p in sorted(dirpath.iterdir())
            if p.is_dir() and p.name != "archive" and _SLICE_FOLDER_RE.match(p.name)]


def _entry(folder: Path) -> dict:
    """Best-effort derived row for one slice folder. Never raises, never returns None."""
    m = _SLICE_FOLDER_RE.match(folder.name)
    nnn = int(m.group(1))
    name = m.group(2)
    mb = _read_json(folder / "mission-brief.json")
    ms = _read_milestone(folder)
    refl = _read_json(folder / "reflection.json")
    return {
        "slice": f"slice-{m.group(1)}",
        "title": mb.get("title") or name,
        "stage": _derive_stage(folder),
        "shipped": refl.get("at") or ms.get("at") or "",
        "summary": _first_sentence(mb.get("intent")),
        "_nnn": nnn,
    }


def regenerate(vault, updated: str) -> tuple[dict, dict]:
    """Return (live_index, archive_index) dicts derived purely from the slice folders.

    `updated` is the caller-supplied `updated`/idempotency stamp (the one non-deterministic field).
    """
    vault = Path(vault)
    slices_dir = vault / "slices"
    archive_dir = slices_dir / "archive"

    active_raw = [_entry(p) for p in _scan(slices_dir)
                  if not (p / "reflection.json").is_file()]
    archived_raw = [_entry(p) for p in _scan(archive_dir)]

    # deterministic ordering for byte-identical re-runs (M2):
    active_raw.sort(key=lambda e: e["_nnn"])                       # active by NNN asc
    archived_raw.sort(key=lambda e: (e["shipped"], e["_nnn"]), reverse=True)  # at desc, then NNN desc

    active = [{"slice": e["slice"], "title": e["title"], "stage": e["stage"]} for e in active_raw]
    arch_slices = [{"slice": e["slice"], "title": e["title"], "shipped": e["shipped"],
                    "summary": e["summary"]} for e in archived_raw]
    recent = arch_slices[:_RECENT_CAP]

    concept = _read_json(vault / "concept.json")
    triage = _read_json(vault / "triage.json")
    project = concept.get("project") or concept.get("name") or vault.name
    mode = triage.get("mode")
    if mode not in _VALID_MODES:  # degrade-safe: absent/malformed/bogus -> documented default
        mode = _DEFAULT_MODE

    live = {
        "_schema": "aisdlc/slice-index@1",
        "project": project,
        "mode": mode,
        "total": len(active) + len(arch_slices),
        "active_count": len(active),
        "archived_count": len(arch_slices),
        "updated": updated,
        "active": active,
        "recent": recent,
        "action_points_ref": _ACTION_POINTS_REF,
        "archive_ref": _ARCHIVE_REF,
    }
    archive = {
        "_schema": "aisdlc/slice-archive-index@1",
        "total": len(arch_slices),
        "updated": updated,
        "slices": arch_slices,
    }
    return live, archive


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="slice_index_regen",
        description="Deterministically regenerate a slice index from the slice folders. Read-only.",
    )
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--emit", choices=["live", "archive"], required=True,
                   help="which index to print: the live slices/_index.json or the archive catalog")
    p.add_argument("--updated", default=None,
                   help="ISO-8601 stamp for the `updated` field -- caller-pinned for idempotency "
                        "(omit only for a throwaway/preview run)")
    p.add_argument("--out-file", default=None,
                   help="write the emitted JSON to this file (UTF-8, no BOM) instead of stdout -- "
                        "mirrors vault_edit --out-file; avoids a shell redirect's encoding pitfalls")
    args = p.parse_args(argv)

    vault = Path(args.vault) if args.vault else VAULT_ROOT
    live, archive = regenerate(vault, args.updated or "")
    out = live if args.emit == "live" else archive
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    # must-not-defer: log WHAT was regenerated (to stderr -- stdout/--out-file is the consumed content)
    print(f"slice_index_regen: emit={args.emit} active={live['active_count']} "
          f"archived={live['archived_count']} total={live['total']}"
          f"{' -> ' + args.out_file if args.out_file else ''}", file=sys.stderr)
    if args.out_file:
        Path(args.out_file).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
