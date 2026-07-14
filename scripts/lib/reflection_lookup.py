"""reflection_lookup.py -- surface past slices relevant to the active slice (v2).

Shared CLI for `/design-slice`'s prior-context injection. Extracts keywords (from the active slice's
`mission-brief.json` with `--from-mission-brief`, from a named slice with `--slice slice-NNN`, or from
`--keywords`), then surfaces TWO things, both read-only:

1. **Nearest prior slice** (LEG1): the past slice whose MISSION is most relevant (seam-coherence anchor --
   "prefer consistency with slice-NNN unless there's a reason to diverge").
2. **Relevant past reflections** (LEG2): past `reflection.json` lessons relevant to this mission -- so the
   designer reuses prior learnings instead of re-discovering them.

**slice-063 / SC-096**: ranking is now a GRADED relevance score via a pluggable **scorer** (see
`reflection_scoring.py`), replacing the old binary `>=2-exact-keyword` (LEG1) / `>=1-substring` (LEG2) gates that
returned "no match" with dozens of relevant lessons on disk. The default `tfidf-cosine` scorer weights rare,
telling terms heavily and near-universal terms ~0, so a lesson sharing ONE strong keyword now surfaces (ranked),
while the old `lexical` scorer is retained as both the crash-proof fallback and the swap-in proof. A
`[recall: <scorer>]` provenance line prints on every post-keyword branch -- an empty result is never silent again.

CLI: `--vault ROOT [--repo-root .] (--from-mission-brief | --slice slice-NNN | --keywords "a b c")
      [--scorer NAME] [--scores] [--top N]`. Precedence: `--keywords > --slice > --from-mission-brief`.
Exit 0 always (no keywords / no matches / ambiguous active slice is a normal result, printed as a note).
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

from scripts.lib import _stdout, reflection_scoring
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id

# Common English + domain-noise words excluded from keyword extraction.
_STOP = frozenset("""
the and for with this that from which must will when then into your they them than have has had
real user users data code test tests slice slices design build feature change changes make made need
need needs should could would about over under after before across within while where what each only
also more most some such very many much both either neither because been being does done here there
""".split())
# Letters-only, len>=4: splits hyphenated/underscored compounds (so `realtime-presence`
# yields both `realtime` and `presence`) -- substring matching against reflections then
# catches a lesson that mentions only one half of a compound term.
_TOK = re.compile(r"[A-Za-z]{4,}")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _tokens(text: str) -> list[str]:
    """Ordered token LIST (repeats kept, for term-frequency) -- lowercased, len>=4, non-stop."""
    return [t.lower() for t in _TOK.findall(text or "") if t.lower() not in _STOP]


def _keywords(text: str) -> set[str]:
    return set(_tokens(text))


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
    """Yield (slice_folder_name, mission_brief_dict) over live + archived slices --
    the corpus for the nearest-prior-slice match (LEG1)."""
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


def _resolve_keywords(vault: Path, args):
    """Resolve (kws, exclude_folder, pre_note). Precedence: --keywords > --slice > --from-mission-brief
    (slice-063 m4). `pre_note` is a terminating note (no-active / ambiguous / unresolved) to print BEFORE any
    scorer runs, or None to proceed to scoring; the sentinel '__ARGERR__' means no selector was given."""
    if args.keywords is not None:
        return _keywords(args.keywords), None, None
    if args.slice:
        info = resolve_slice_by_id(vault, args.slice, args.repo_root)
        if isinstance(info, dict) and info.get("source") == "ownership-refused":
            # slice-069: refuse rather than feed another owner's mission keywords into the designers.
            owner = info.get("owner") or {}
            return None, None, (f"(ownership refused -- {info.get('refused_slice')} is claimed by "
                                f"{owner.get('git_user') or '?'} <{owner.get('git_email') or '?'}>, "
                                f"not you; no reflections matched)")
        if not info:
            return None, None, f"(no slice matches '{args.slice}' -- nothing to match reflections against)"
        return _load_mission_keywords(info), info.get("folder"), None
    if args.from_mission_brief:
        info = resolve_active_slice(vault, args.repo_root)
        if isinstance(info, dict) and info.get("source") == "ownership-refused":
            owner = info.get("owner") or {}
            return None, None, (f"(ownership refused -- {info.get('refused_slice')} is claimed by "
                                f"{owner.get('git_user') or '?'} <{owner.get('git_email') or '?'}>, "
                                f"not you; no reflections matched)")
        if isinstance(info, dict) and info.get("source") == "ambiguous":
            cands = ", ".join(str(c.get("slice")) for c in (info.get("candidates") or []))
            return None, None, (f"(ambiguous active slice -- {cands} in flight; pass --slice or run from the "
                                f"worktree -- no mission brief to match reflections against)")
        if not info:
            return None, None, "(no active slice -- no mission brief to match reflections against)"
        return _load_mission_keywords(info), info["folder"], None
    return None, None, "__ARGERR__"


def _load_mission_keywords(info: dict) -> set[str]:
    """Read a resolved slice's mission-brief.json keyword set (empty set on any read/parse failure)."""
    mb_path = Path(info["path"]) / "mission-brief.json"
    try:
        mb = json.loads(mb_path.read_text(encoding="utf-8")) if mb_path.is_file() else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        mb = {}
    return _mission_keywords(mb if isinstance(mb, dict) else {})


def _score(scorer, query, docs, leg):
    """Run `scorer` over one leg; on ANY error degrade THIS run to the lexical fallback (AC2 / must-not-defer:
    never crash the CLI on a bad scorer). Returns (ranked, fell_back_reason|None)."""
    try:
        return scorer(query, docs, leg=leg), None
    except Exception as exc:  # noqa: BLE001 -- the whole point is to never crash on a misbehaving scorer
        lex = reflection_scoring.get_scorer(reflection_scoring.FALLBACK_SCORER)
        return lex(query, docs, leg=leg), f"{type(exc).__name__}: {exc}"


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
    p.add_argument("--slice", default=None, metavar="slice-NNN",
                   help="extract keywords from THIS named slice's mission-brief.json (archive-aware; "
                        "resolves an ambiguous multi-in-flight context -- slice-063 AC4)")
    p.add_argument("--keywords", default=None, help="explicit space-separated keywords")
    p.add_argument("--scorer", default=reflection_scoring.DEFAULT_SCORER,
                   help=f"relevance scorer (default: {reflection_scoring.DEFAULT_SCORER}; "
                        f"available: {', '.join(reflection_scoring.list_scorers())})")
    p.add_argument("--scores", action="store_true",
                   help="append the numeric relevance score to each line (debug; OFF by default so the "
                        "designer-injected block stays corpus-relative-number-free -- slice-063 m5)")
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args(argv)

    vault = _root(args.vault)
    kws, exclude, pre_note = _resolve_keywords(vault, args)

    if pre_note == "__ARGERR__":
        print("specify --from-mission-brief, --slice, or --keywords", file=sys.stderr)
        return 2
    if pre_note is not None:
        # no-active / ambiguous / unresolved-slice: an honest visible note BEFORE any scorer runs (already
        # fail-visible, not the silent-empty incident). Printed as-is for output-shape stability (AC3).
        print(pre_note)
        return 0
    if not kws:
        print("(no keywords to match -- proceeding without prior-reflection context)")
        return 0

    # --- resolve the scorer (unknown name -> lexical fallback, never a crash: AC2) ---
    scorer_name = args.scorer or reflection_scoring.DEFAULT_SCORER
    scorer = reflection_scoring.get_scorer(scorer_name)
    fell_back = None
    if scorer is None:
        fell_back = f"unknown scorer '{scorer_name}'"
        scorer_name = reflection_scoring.FALLBACK_SCORER
        scorer = reflection_scoring.get_scorer(scorer_name)

    query = {"tokens": sorted(kws), "text": " ".join(sorted(kws))}

    # --- LEG1 corpus (mission-briefs): docs carry the mission keyword SET (TF=1; slice-063 M-add-2) ---
    leg1_docs = []
    for folder, mb in _iter_mission_briefs(vault, exclude):
        mkw = sorted(_mission_keywords(mb if isinstance(mb, dict) else {}))
        leg1_docs.append({"folder": folder, "title": str(mb.get("title") or ""),
                          "tokens": mkw, "text": " ".join(mkw)})
    # --- LEG2 corpus (reflections): docs carry the full token list (TF real) + raw blob for substring ---
    leg2_docs = []
    for folder, refl in _iter_reflections(vault, exclude):
        blob = " ".join(_reflection_strings(refl)).lower()
        leg2_docs.append({"folder": folder, "lessons": (refl.get("lessons") or []),
                          "tokens": _tokens(blob), "text": blob})

    leg1, fb1 = _score(scorer, query, leg1_docs, "nearest_slice")
    leg2, fb2 = _score(scorer, query, leg2_docs, "reflections")
    fell_back = fell_back or fb1 or fb2

    def _sfx(score: float) -> str:
        return f"  (score {score:.3f})" if args.scores else ""

    out: list[str] = []
    if leg1:
        i0, s0 = leg1[0]
        d0 = leg1_docs[i0]
        title_s = f" - {d0['title']}" if d0["title"] else ""  # ASCII only (Windows cp1252-safe stdout)
        shared = sorted(kws & set(d0["tokens"]))
        out.append(f"NEAREST PRIOR SLICE (most similar mission): {d0['folder']}{title_s}  "
                   f"[shared: {', '.join(shared)}]{_sfx(s0)}")
        out.append("   Prefer consistency with its approach (interfaces, boundaries, naming) unless THIS "
                   "slice has a concrete reason to diverge. (Seam coherence - Phase 2.2 / Theme 4.)")
        if len(leg1) > 1:
            out.append("   (other related: " + ", ".join(leg1_docs[i]["folder"] for i, _ in leg1[1:4]) + ")")
        out.append("")
    if leg2:
        out.append(f"RELEVANT PAST REFLECTIONS (matched on: {', '.join(sorted(kws))}):")
        for i, s in leg2[:args.top]:
            d = leg2_docs[i]
            hits = sorted(k for k in kws if k in d["text"])
            out.append(f"  {d['folder']}  [matched: {', '.join(hits)}]{_sfx(s)}")
            lessons = [les for les in (d["lessons"] or [])
                       if any(k in str(les).lower() for k in kws)]
            for les in lessons[:3]:
                out.append(f"     - {les}")

    # Provenance on EVERY post-keyword terminating branch, incl. the no-match branch (slice-063 M4):
    # a graded run that scores everything below the floor must NOT reproduce the silent-empty incident.
    prov = (f"[recall: {scorer_name} fallback -- {fell_back}]" if fell_back
            else f"[recall: {scorer_name}]")
    if not out:
        out.append(f"(no past slice matches the mission keywords: {', '.join(sorted(kws))})")
    out.append(prov)
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
