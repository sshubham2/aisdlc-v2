"""reflection_lookup.py — surface past slices relevant to the active slice (v2, NEW).

Shared CLI for `/design-slice`'s prior-context injection. Extracts keywords (from the
active slice's `mission-brief.json` with `--from-mission-brief`, or from `--keywords`),
then surfaces TWO things, both read-only:

1. **Nearest prior slice** (Phase 2.2 / roadmap Theme 4): the past slice whose MISSION is
   most lexically similar (most shared keywords across title/intent/ACs), printed as a
   seam-coherence anchor — "prefer consistency with slice-NNN unless there's a reason to
   diverge." Lexical only (no embeddings), >=2 shared keywords to avoid spurious matches.
2. **Relevant past reflections**: scans every past `reflection.json` (live + archived,
   active excluded), scores by keyword hits across `lessons` / `discovered` / `corrected`
   / `validated` / `deferred` text, prints the top matches — so the designer reuses prior
   learnings instead of re-discovering them.

CLI: `--vault ROOT [--repo-root .] (--from-mission-brief | --keywords "a b c") [--top N]`.
Exit 0 always (no keywords / no matches is a normal result, printed as a note).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md). ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/X.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.active_slice import resolve_active_slice

# Common English + domain-noise words excluded from keyword extraction.
_STOP = frozenset("""
the and for with this that from which must will when then into your they them than have has had
real user users data code test tests slice slices design build feature change changes make made need
need needs should could would about over under after before across within while where what each only
also more most some such very many much both either neither because been being does done here there
""".split())
# Letters-only, len>=4: splits hyphenated/underscored compounds (so `realtime-presence`
# yields both `realtime` and `presence`) — substring matching against reflections then
# catches a lesson that mentions only one half of a compound term.
_TOK = re.compile(r"[A-Za-z]{4,}")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _keywords(text: str) -> set[str]:
    return {t.lower() for t in _TOK.findall(text or "") if t.lower() not in _STOP}


def _strip_md(s) -> str:
    """Drop markdown `#` header lines so 'intent'/'verification' header words don't
    leak into the keyword set."""
    return " ".join(ln for ln in str(s or "").splitlines() if not ln.strip().startswith("#"))


def _mission_keywords(mb: dict) -> set[str]:
    parts = [str(mb.get("title") or ""), _strip_md(mb.get("intent"))]
    for ac in mb.get("acceptance_criteria") or []:
        if isinstance(ac, dict):
            parts.append(str(ac.get("text") or ""))
    return _keywords(" ".join(parts))


def _reflection_strings(refl: dict) -> list[str]:
    """Every searchable text fragment in a reflection.json."""
    out: list[str] = [str(x) for x in (refl.get("lessons") or []) if x]
    for d in refl.get("discovered") or []:
        if isinstance(d, dict) and d.get("item"):
            out.append(str(d["item"]))
    for c in refl.get("corrected") or []:
        if isinstance(c, dict):
            out.append(f"{c.get('was', '')} {c.get('now', '')}")
    for v in refl.get("validated") or []:
        if isinstance(v, dict) and v.get("claim"):
            out.append(str(v["claim"]))
    for d in refl.get("deferred") or []:
        if isinstance(d, dict) and d.get("item"):
            out.append(str(d["item"]))
    return out


def _iter_reflections(vault: Path, exclude_folder: str | None):
    """Yield (slice_folder_name, reflection_dict) over live + archived slices."""
    roots = [vault / "slices", vault / "slices" / "archive"]
    for base in roots:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name == "archive" or d.name == exclude_folder:
                continue
            rp = d / "reflection.json"
            if not rp.is_file():
                continue
            try:
                refl = json.loads(rp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(refl, dict):
                yield d.name, refl


def _iter_mission_briefs(vault: Path, exclude_folder: str | None):
    """Yield (slice_folder_name, mission_brief_dict) over live + archived slices —
    the corpus for the nearest-prior-slice match (Phase 2.2)."""
    roots = [vault / "slices", vault / "slices" / "archive"]
    for base in roots:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if not d.is_dir() or d.name == "archive" or d.name == exclude_folder:
                continue
            mp = d / "mission-brief.json"
            if not mp.is_file():
                continue
            try:
                mb = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(mb, dict):
                yield d.name, mb


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="reflection_lookup",
        description="Surface past reflections relevant to the active slice for /design-slice. Read-only.",
    )
    p.add_argument("--vault", default=None)
    p.add_argument("--repo-root", "--root", dest="repo_root", default=".")
    p.add_argument("--from-mission-brief", action="store_true",
                   help="extract keywords from the active slice's mission-brief.json")
    p.add_argument("--keywords", default=None, help="explicit space-separated keywords")
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args(argv)

    vault = _root(args.vault)
    exclude = None
    if args.keywords:
        kws = _keywords(args.keywords)
    elif args.from_mission_brief:
        info = resolve_active_slice(vault, args.repo_root)
        if not info:
            print("(no active slice — no mission brief to match reflections against)")
            return 0
        exclude = info["folder"]
        mb_path = Path(info["path"]) / "mission-brief.json"
        try:
            mb = json.loads(mb_path.read_text(encoding="utf-8")) if mb_path.is_file() else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            mb = {}
        kws = _mission_keywords(mb if isinstance(mb, dict) else {})
    else:
        print("specify --from-mission-brief or --keywords", file=sys.stderr)
        return 2

    if not kws:
        print("(no keywords to match — proceeding without prior-reflection context)")
        return 0

    # --- NEAREST PRIOR SLICE (Phase 2.2 / roadmap Theme 4) ---
    # The past slice whose MISSION is most lexically similar to this one — a seam-
    # coherence anchor for design-slice ("prefer consistency with slice-NNN unless
    # there's a reason to diverge"). Lexical (shared keyword count), no embeddings.
    brief_scored = []
    for folder, mb in _iter_mission_briefs(vault, exclude):
        shared = sorted(kws & _mission_keywords(mb if isinstance(mb, dict) else {}))
        if len(shared) >= 2:  # >=2 shared keywords to avoid spurious single-word matches
            brief_scored.append((len(shared), folder, shared, str(mb.get("title") or "")))
    brief_scored.sort(key=lambda t: (-t[0], t[1]))

    # --- relevant past reflections (reuse prior learnings) ---
    scored = []
    for folder, refl in _iter_reflections(vault, exclude):
        blob = " ".join(_reflection_strings(refl)).lower()
        hits = sorted(k for k in kws if k in blob)
        if hits:
            matched_lessons = [s for s in (refl.get("lessons") or [])
                               if any(k in str(s).lower() for k in kws)]
            scored.append((len(hits), folder, hits, matched_lessons))
    scored.sort(key=lambda t: (-t[0], t[1]))

    out: list[str] = []
    if brief_scored:
        _, folder, shared, title = brief_scored[0]
        title_s = f" - {title}" if title else ""  # ASCII only (Windows cp1252-safe stdout)
        out.append(f"NEAREST PRIOR SLICE (most similar mission): {folder}{title_s}  "
                   f"[shared: {', '.join(shared)}]")
        out.append("   Prefer consistency with its approach (interfaces, boundaries, naming) unless THIS "
                   "slice has a concrete reason to diverge. (Seam coherence - Phase 2.2 / Theme 4.)")
        if len(brief_scored) > 1:
            out.append("   (other related: " + ", ".join(f for _, f, _, _ in brief_scored[1:4]) + ")")
        out.append("")
    if scored:
        out.append(f"RELEVANT PAST REFLECTIONS (matched on: {', '.join(sorted(kws))}):")
        for _, folder, hits, lessons in scored[:args.top]:
            out.append(f"  {folder}  [matched: {', '.join(hits)}]")
            for les in (lessons or [])[:3]:
                out.append(f"     - {les}")

    if not out:
        print(f"(no past slice matches the mission keywords: {', '.join(sorted(kws))})")
        return 0
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
