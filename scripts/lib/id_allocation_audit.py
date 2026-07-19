"""id_allocation_audit — slice-019 enforcement audits (AC3 prose + M1/m3 counters).

Two checks (a property is only real where an audit ENFORCES it — slice-004/014/016):

  PROSE (AC3 / m1): the id-picking instructions are GONE from the loop SKILL.md set
    (slice/repro/discover/design-slice/reflect) — the model can no longer be told to compute or
    hand-pick an id. Flags id-ASSIGNMENT phrasing (`max(existing`, `next available`, `<next>` as a
    value, `next SHIP-NNN`); WHITELISTS the benign create/path `decisions/ADR-NNN.json` filename form
    (m1: a naive grep over-matches it).

  COUNTERS (M1 / m3): for a real vault, each monotonic `counters.<kind>` (when present) is >= the
    max existing id of that kind across live ∪ archive ∪ on-disk, and no kind has a DUPLICATE id.
    Catches a hand-edited-down counter (m3) AND a model-hand-picked ADR filename that disagrees with
    `counters.adr` (M1 — ADR files are raw-written, so the chokepoint-reject can't cover them).
    Audited kinds: sc, slice, adr, ship, ps. A counter can live in ANY of three files, so the holder
    is an explicit per-kind map (`_COUNTERS_HOLDER`) — see the comment at its definition for why a
    ternary would ship a new kind half-audited.

CLI: `[--root <plugin-root>] [--vault <vault>] [--json]`. Runs whichever inputs are given (root ->
prose, vault -> counters; default root only). Exit 0 = clean, 1 = violations. Read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout, id_allocator

# The loop skills AC3 covers (the only places id-picking prose ever lived).
_LOOP_SKILLS = ("slice", "repro", "discover", "design-slice", "reflect")
# id-ASSIGNMENT phrasing to FLAG (the model choosing a number), NOT a benign filename/path/read.
_FLAG = re.compile(r"max\(existing|next available|<next>|next\s+SHIP-NNN")
# m1 WHITELIST: a line that is the legitimate ADR filename/path/count template survives even if a
# flag pattern grazed it (it never should, but this makes the carve-out explicit + future-proof).
_WHITELIST = re.compile(r"decisions/ADR-NNN\.json|ADR-NNN \(count\)")


def prose_violations(root: str | Path) -> list[str]:
    """`skills/<skill>/SKILL.md:LINE — <snippet>` for each surviving id-assignment instruction."""
    root = Path(root)
    out: list[str] = []
    for skill in _LOOP_SKILLS:
        md = root / "skills" / skill / "SKILL.md"
        if not md.is_file():
            continue
        for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if _WHITELIST.search(line):
                continue
            if _FLAG.search(line):
                out.append(f"skills/{skill}/SKILL.md:{i} — {line.strip()[:90]}")
    return out


def _scan(values, kind):
    nums = [n for v in values if (n := id_allocator.parse_num(kind, v)) is not None]
    return nums


def _slice_folder_dups(folder_names):
    """Slice numbers carried by >=2 DISTINCT folder identities -- the genuine-collision signal.

    slice-041 / ADR-027: the on-disk folder name ``slice-NNN-<name>`` is the AUTHORITATIVE identity
    of a slice; bare ``slice-NNN`` references (``candidate.slice`` / ``pick_log[].slice``) are
    pointers SUBSUMED onto a same-number folder, so a slice observed through multiple sources counts
    ONCE (the SC-045 false positive was counting those observations as separate slices). A real
    collision is two genuinely-distinct folder NAMES sharing one number. Authoritative-vs-bare is
    decided by SOURCE -- this helper is fed ONLY the folder globs -- never by a regex on the string
    (``parse_num`` carries no full/bare signal: ``slice-005`` and ``slice-005-foo`` both parse to 5).
    """
    by_num: dict[int, set] = {}
    for name in folder_names:
        n = id_allocator.parse_num("slice", name)
        if n is not None:
            by_num.setdefault(n, set()).add(name)
    return {n for n, names in by_num.items() if len(names) > 1}


def counters_violations(vault: str | Path) -> list[str]:
    """Counter-consistency violations on a real vault (empty = clean / counters not yet seeded)."""
    vault = Path(vault)
    out: list[str] = []

    def load(rel):
        p = vault / rel
        if not p.exists():
            return {}
        try:
            d = json.loads(p.read_text(encoding="utf-8") or "{}")
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _counters_of(d):
        c = d.get("counters")
        return c if isinstance(c, dict) else {}

    cand = load("candidates.json")
    arch = load("archive/candidates.json")
    counters = _counters_of(cand)

    sources = {
        "sc": _scan([c.get("id") for c in cand.get("candidates", []) + arch.get("candidates", [])
                     if isinstance(c, dict)], "sc"),
        "slice": _scan([c.get("slice") for c in cand.get("candidates", []) if isinstance(c, dict)]
                       + [e.get("slice") for e in cand.get("pick_log", []) if isinstance(e, dict)]
                       + [p.name for p in (vault / "slices").glob("slice-*")]
                       + [p.name for p in (vault / "slices" / "archive").glob("slice-*")], "slice"),
        "adr": _scan([p.name for p in (vault / "decisions").glob("ADR-*.json")], "adr"),
    }
    ship = load("shippability.json")
    sources["ship"] = _scan([r.get("id") for r in ship.get("rows", []) if isinstance(r, dict)], "ship")

    # slice-068 / C13 (RPCD-1): `ps` (PS-NNN product-scope items) is a THIRD counters holder --
    # counters.ps lives in product-scope.json, not in candidates.json. The old two-way ternary
    # (`ship_counters if kind == 'ship' else counters`) would have resolved ctr=None for it, the
    # isinstance(ctr, int) test would have failed, and the counter-STALENESS arm would have silently
    # NO-OPPED: `ps` would be checked for duplicate ids only, and a hand-edited-down counters.ps --
    # which RE-ISSUES an existing PS id, the exact collision this audit exists to catch -- would sail
    # through green. So the holder is an explicit per-kind map, not a ternary: adding a kind without
    # its counters holder is now a visible omission rather than a silent half-audit.
    scope = load("product-scope.json")
    # slice-073 / critique B1: union the revisions[] RETIREMENT ledger's cut ids into the ps source
    # multiset. `revise --cut PS-NNN` retires an id, so items[] alone is no longer the full ps
    # history -- and the duplicate arm below works by COUNTING a number in this multiset, so a cut id
    # was invisible to it: a re-issued PS-002 appeared exactly ONCE (the new item) and sailed through
    # GREEN while silently aliasing the shipped candidate the original PS-002 minted. With the cut
    # ids unioned in, a re-issue counts TWICE and is a VISIBLE duplicate -- which is what this audit
    # already promises. The counter arm benefits too: max(nums) now includes retired ids, so a
    # counters.ps below a RETIRED max is a staleness hit rather than a silent floor drop.
    # Pairs with id_allocator.seed_max_for('ps'), which scans the same ledger as its self-heal floor:
    # that is the PREVENTIVE guard, this is the DETECTIVE one, and BC-PROJ-6 is exactly about the two
    # not drifting apart.
    sources["ps"] = _scan([i.get("id") for i in scope.get("items", []) if isinstance(i, dict)]
                          + [c for r in scope.get("revisions", []) or [] if isinstance(r, dict)
                             for c in r.get("cut") or []], "ps")

    _COUNTERS_HOLDER = {
        "ship": _counters_of(ship),      # shippability.json
        "ps": _counters_of(scope),       # product-scope.json
    }   # every other kind's counter lives in candidates.json

    # slice-041 / ADR-027: the slice kind's AUTHORITATIVE identities are its on-disk folder names
    # (slices/ + slices/archive/); a number is a real collision only when >=2 DISTINCT folders carry
    # it. Bare slice-NNN refs (candidate.slice / pick_log[].slice) are subsumed -- decided by SOURCE,
    # not by a string regex -- so a slice observed through multiple sources counts once (SC-045 fix).
    # The full sources["slice"] multiset above is untouched, so the staleness (counters.slice < max)
    # check below is unchanged.
    slice_folders = ([p.name for p in (vault / "slices").glob("slice-*")]
                     + [p.name for p in (vault / "slices" / "archive").glob("slice-*")])
    slice_dups = _slice_folder_dups(slice_folders)
    for kind, nums in sources.items():
        if not nums:
            continue
        # duplicate-id detector. sc/adr/ship: raw number-multiset (single-source / moved-not-copied).
        # slice: DISTINCT folder identities (ADR-027), so multi-source observations of one slice count once.
        dups = slice_dups if kind == "slice" else {n for n in nums if nums.count(n) > 1}
        if dups:
            out.append(f"{kind}: DUPLICATE id number(s) {sorted(dups)} — the collision this slice kills")
        ctr = _COUNTERS_HOLDER.get(kind, counters).get(kind)
        if isinstance(ctr, int) and ctr < max(nums):
            out.append(f"{kind}: counters.{kind}={ctr} < max existing {max(nums)} "
                       f"(hand-edited-down / stale counter — would re-issue an existing id)")
    return out


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="id_allocation_audit",
                                description="slice-019 AC3 prose + M1/m3 counters audit.")
    p.add_argument("--root", default=str(_PLUGIN_ROOT), help="plugin root for the prose audit")
    p.add_argument("--vault", default=None, help="vault root for the counters audit (optional)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    prose = prose_violations(args.root)
    counters = counters_violations(args.vault) if args.vault else []
    clean = not prose and not counters
    if args.json:
        print(json.dumps({"prose": prose, "counters": counters, "clean": clean}, ensure_ascii=False))
    else:
        if prose:
            print("AC3 FAIL -- id-picking prose still in the loop skills (route it to the allocator):")
            for v in prose:
                print(f"  {v}")
        if counters:
            print("M1/m3 FAIL -- counter/id inconsistency in the vault:")
            for v in counters:
                print(f"  {v}")
        if clean:
            print("id_allocation_audit: clean -- no id-picking prose; counters consistent.")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
