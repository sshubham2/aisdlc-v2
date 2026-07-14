"""active_slice_info.py — active-slice + validate-prerequisite digest (v2, NEW).

Single-skill tool for `/validate-slice`'s "Active slice + inputs" injection. Resolves
the in-flight slice (shared `scripts.lib.active_slice.resolve_active_slice`) and emits
the facts `/validate-slice` needs for its PREREQUISITE GATE — whether `build-log.json`
exists and its `result` (must be `shipped`), the acceptance criteria to check, the
files changed (for `--changed-files`), and the walking-skeleton / exploratory-charter /
test-first variant flags (which gate the WS-1 / ETC-1 / TF audits). Read-only.

Invoked `$PY "${CLAUDE_SKILL_DIR}/scripts/active_slice_info.py" --vault ROOT --json`.
Exit 0 always (an absent slice / missing build-log is the gate's concern, surfaced in
the payload via `ready_to_validate=false`, not a tool error).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def _info(vault: Path, repo_root: str, slice_id: str | None = None) -> dict:
    # slice-036 (m2): an explicit --slice resolves THAT slice by id (ARCHIVE-AWARE, via resolve_slice_by_id),
    # mirroring active_slice_brief.py / active_slice.py --slice -- so `/validate-slice slice-NNN` resolves the
    # named slice from a main session; the no-arg path keeps resolve_active_slice (branch-first + exit-4 HALT).
    # A named-but-missing id -> None -> the `if not slc` branch below -> ready_to_validate=false (exit-0 preserved).
    slc = (resolve_slice_by_id(vault, slice_id, repo_root) if slice_id
           else resolve_active_slice(vault, repo_root))
    if isinstance(slc, dict) and slc.get("source") == "ownership-refused":
        # slice-069: /validate-slice WRITES validation.json into the slice folder -- refuse before
        # any byte, and NAME the owner (exit-0 preserved: ready_to_validate=false is the signal).
        owner = slc.get("owner") or {}
        return {"slice": None, "source": "ownership-refused", "owner": owner,
                "refused_slice": slc.get("refused_slice"), "ready_to_validate": False,
                "note": (f"OWNERSHIP REFUSED: {slc.get('refused_slice')} is claimed by "
                         f"{owner.get('git_user') or '?'} <{owner.get('git_email') or '?'}>, not you. "
                         f"Do NOT validate (or write to) another owner's slice.")}
    if isinstance(slc, dict) and slc.get("source") == "ambiguous":
        # slice-014 (B1/M-add-1): catch the TRUTHY AMBIGUOUS sentinel BEFORE `if not slc`
        # (else `Path(slc['path'])` crashes on path=None), and NAME the candidates rather
        # than lie 'no active slice — run /slice first'.
        cands = slc.get("candidates", []) or []
        ids = ", ".join(str(c.get("slice")) for c in cands)
        return {"slice": None, "source": "ambiguous", "candidates": cands,
                "ready_to_validate": False,
                "reason": f"ambiguous active slice — {len(cands)} in flight: {ids}. "
                          f"Pass --slice <slice-NNN> or work from the slice's worktree."}
    if not slc:
        return {"slice": None, "ready_to_validate": False,
                "reason": "no active slice — run /slice first"}
    folder = Path(slc["path"])
    mb = _load(folder / "mission-brief.json")
    bl = _load(folder / "build-log.json")
    variants = (mb or {}).get("variants") or {}
    acs = [{"id": a.get("id"), "text": a.get("text"), "verification": a.get("verification")}
           for a in ((mb or {}).get("acceptance_criteria") or []) if isinstance(a, dict)]
    build_result = bl.get("result") if bl else None
    ready = bool(bl) and build_result == "shipped"
    out = {
        "slice": slc["slice"],
        "folder": slc["folder"],
        "path": slc["path"],
        "stage": slc.get("stage"),
        "source": slc["source"],
        "mission_brief_exists": mb is not None,
        "build_log_exists": bl is not None,
        "build_log_result": build_result,
        "files_changed": (bl or {}).get("files_changed", []),
        "acceptance_criteria": acs,
        "walking_skeleton": bool(variants.get("walking_skeleton")),
        "exploratory_charter": bool(variants.get("exploratory_charter")),
        "test_first": bool(variants.get("test_first")),
        "ready_to_validate": ready,
    }
    if not ready:
        out["reason"] = ("build-log.json missing — slice not built yet"
                         if bl is None else
                         f"build-log result is {build_result!r}, not 'shipped'")
    return out


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="active_slice_info",
        description="Active-slice + validate-prerequisite digest for /validate-slice. Read-only.",
    )
    p.add_argument("--vault", default=None)
    p.add_argument("--repo-root", "--root", dest="repo_root", default=".")
    p.add_argument("--slice", default=None, metavar="slice-NNN",
                   help="resolve THIS slice by id (archive-aware, via resolve_slice_by_id) -- mirrors "
                        "active_slice_brief.py --slice; for `/validate-slice slice-NNN`. Else the active slice.")
    p.add_argument("--json", action="store_true", help="emit JSON (default: text)")
    args = p.parse_args(argv)

    info = _info(_root(args.vault), args.repo_root, args.slice)
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    elif info["slice"] is None:
        print(f"active slice: {info.get('reason', 'none — run /slice first')}")
    else:
        print(f"active slice: {info['folder']} (stage={info['stage']}) — "
              f"ready_to_validate={info['ready_to_validate']}"
              + (f" ({info['reason']})" if not info["ready_to_validate"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
