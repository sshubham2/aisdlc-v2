"""active_slice_brief.py — readable mission-brief digest of the active slice (v2, NEW).

Shared CLI for `/design-slice`'s "Active slice mission brief" injection. Resolves the
in-flight slice (via `scripts.lib.active_slice.resolve_active_slice`) and prints a
concise, human-readable summary of its `mission-brief.json` — intent, acceptance
criteria, must-not-defer, out-of-scope, verification plan, variant flags — for the
designer to read at load time. Read-only.

CLI: `--vault ROOT [--repo-root .] [--slice slice-NNN]`. With `--slice` it resolves THAT slice
by id (archive-aware, via `active_slice.resolve_slice_by_id`) — mirroring `active_slice.py --slice`
so `/design-slice slice-NNN` resolves the named slice's brief from a main session (slice-031 / AC5);
without it, the active slice. Exit 0 always (an absent slice / brief is a normal early state — print
a clear note and let the skill proceed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md). ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/X.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _prose(s) -> str:
    """Flatten a markdown-valued string field to plain lines (drop `##` headers)."""
    if not isinstance(s, str):
        return ""
    out = []
    for ln in s.splitlines():
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        out.append(t)
    return " ".join(out)


def _format(info: dict, mb: dict) -> str:
    lines = [
        f"ACTIVE SLICE BRIEF — {info['folder']} (stage={info.get('stage')}, via {info['source']})",
        f"title: {mb.get('title')}  | candidate: {mb.get('candidate')} | "
        f"mode: {mb.get('mode')} | risk_tier: {mb.get('risk_tier')}",
    ]
    intent = _prose(mb.get("intent"))
    if intent:
        lines.append(f"intent: {intent}")
    acs = mb.get("acceptance_criteria") or []
    if acs:
        lines.append("acceptance criteria:")
        for ac in acs:
            if isinstance(ac, dict):
                lines.append(f"  {ac.get('id', '?')}: {ac.get('text', '')}")
                if ac.get("verification"):
                    lines.append(f"       (verify: {ac['verification']})")
    if mb.get("must_not_defer"):
        lines.append("must-not-defer: " + "; ".join(str(x) for x in mb["must_not_defer"]))
    if mb.get("out_of_scope"):
        lines.append("out-of-scope: " + "; ".join(str(x) for x in mb["out_of_scope"]))
    vp = _prose(mb.get("verification_plan"))
    if vp:
        lines.append(f"verification plan: {vp}")
    v = mb.get("variants") or {}
    if v:
        lines.append("variants: " + " ".join(f"{k}={v.get(k)}" for k in
                     ("test_first", "walking_skeleton", "exploratory_charter") if k in v))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="active_slice_brief",
        description="Readable mission-brief digest of the active slice for /design-slice. Read-only.",
    )
    p.add_argument("--vault", default=None)
    p.add_argument("--repo-root", "--root", dest="repo_root", default=".")
    p.add_argument("--slice", default=None, metavar="slice-NNN",
                   help="resolve THIS slice by id (archive-aware, via resolve_slice_by_id) -- mirrors "
                        "active_slice.py --slice; for /design-slice slice-NNN. Else the active slice.")
    args = p.parse_args(argv)

    # slice-031 (AC5): an explicit --slice resolves by id (the SAME archive-aware primitive
    # active_slice.py --slice uses), so design-slice's guard then-branch resolves the named slice
    # from a main session; the no-arg path keeps resolve_active_slice (branch-first + exit-4 HALT).
    info = (resolve_slice_by_id(_root(args.vault), args.slice) if args.slice
            else resolve_active_slice(_root(args.vault), args.repo_root))
    if isinstance(info, dict) and info.get("source") == "ambiguous":
        # slice-014 (B1/M-add-1): the AMBIGUOUS sentinel is a TRUTHY dict, so it must be
        # caught BEFORE `if not info` (else `Path(info['path'])` would crash on path=None),
        # and it must NAME the candidates rather than lie 'no active slice'.
        cands = info.get("candidates", []) or []
        ids = ", ".join(str(c.get("slice")) for c in cands)
        print(f"(AMBIGUOUS active slice — {len(cands)} slices in flight: {ids}. This refuses "
              f"to guess which slice you mean: pass --slice <slice-NNN> or work from the "
              f"slice's worktree.)")
        return 0
    if not info:
        print("(no active slice — run /slice first)")
        return 0
    mb_path = Path(info["path"]) / "mission-brief.json"
    if not mb_path.is_file():
        print(f"(active slice {info['folder']} has no mission-brief.json yet)")
        return 0
    try:
        mb = json.loads(mb_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"(mission-brief.json unreadable: {exc})")
        return 0
    sys.stdout.write(_format(info, mb if isinstance(mb, dict) else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
