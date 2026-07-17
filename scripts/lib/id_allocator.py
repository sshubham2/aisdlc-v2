"""id_allocator — the single in-lock id/number minter (slice-019 / SC-022 / [[ADR-013]]).

Generalizes build_backlog.py's proven in-lock SC `max+1` into ONE reusable allocator that mints
`slice-NNN` / `SC-NNN` / `SHIP-NNN` / `ADR-NNN` by bumping a monotonic `counters.<kind>` INSIDE
the caller's `safe_mutate_text` critical section. PURE functions over the decoded dict the
per-file lock already holds — it NEVER opens a file itself, so it can't race the lock or double-lock.

Why this is race-free (proven by spike-lock-serialization, slice-019): `safe_mutate_text`
serializes concurrent writers and each mutate re-reads the latest on-disk content INSIDE the lock,
so two parallel claimers each see the other's committed counter -> the bumps never collide. The
counter is monotonic; a skipped number (e.g. a rolled-back claim) is harmless.

Contract:
  next_id(data, kind, *, seed_max=0) -> str
      Bump `data['counters'][kind]` and return the formatted id. SELF-HEALING: the next value is
      `max(current counter, seed_max) + 1`, so a hand-edited-down counter (or a legacy file with no
      counter) can never re-issue an existing number — pass `seed_max` = the true historical max
      (live ∪ archive ∪ on-disk ADRs) when seeding/healing matters. Callable N times in one mutate
      (build_backlog mints a batch).
  reject_supplied_id(kind, element)
      Raise ValueError if a caller supplied an id for a managed kind (the no-explicit-PK guard).
  parse_num(kind, value) / scan_max(values, kind)
      Parse / max the trailing integer of an id of `kind` (tolerant of zero-pad + a trailing -name;
      never raises on garbage — so a vault polluted by the pre-fix bug still seeds correctly).

Managed kinds: slice, sc, ship, adr, bc (build-checks BC-PROJ-N rules — minted in-lock
by the managed append, like sc/ship), plus the calibration-overlay kinds cc/cn/gs
(CC-/CN-/GS- ids in critic-calibration-log.json — minted via `vault_edit alloc`, then
carried in the append payload, mirroring the ADR flow), and `ps` (PS-NNN product-scope
items in product-scope.json — slice-068 / [[ADR-067]]). Unknown kind -> ValueError
(fail-visible, R-7).

Why `ps` exists (slice-068): the model's decomposition of a concept into product scope items is
`outside data` — spike B1 measured only 22% cross-run key agreement between two BLIND decompositions
of the SAME concept, and 5 of 7 semantically-identical items (including the orchestrator itself)
drifted their key. So a model-emitted key can never be a cross-run identity, and `ps` ids are minted
by the RECEIVER, in-lock, exactly like every other managed kind.

`product-scope.json`/`items` IS registered in `vault_edit._MANAGED_KIND` (slice-073 / [[ADR-080]],
which supersedes [[ADR-067]] §3 on this point). The append-side rationale that once justified its
ABSENCE still holds and is unchanged: product_scope.persist must rewrite the model's run-local
depends_on labels into minted ids INSIDE one lock, which `vault_edit append` (which mints internally
and returns nothing to the caller) cannot express — so persist does its own `safe_mutate_text` and
calls `reject_supplied_id` itself, as the first statement in its own mutate closure. It does not
route through vault_edit. What that argument MISSED is that one _MANAGED_KIND entry drives FOUR legs:
`remove` and `set --path` were each deleting a scope item at rc=0 with no record, so the entry is
required for the REMOVAL legs (the `ps` APPEND leg is refused outright there instead of minting —
a raw append would produce a real-id, contract-free item that SKIPS /risk-spike step-0). Registering
it also means `ps` ids are RETIRABLE (`revise --cut`), which is why `seed_max_for('ps')` below scans
the revisions[] ledger and not just items[].
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PREFIX = {"slice": "slice-", "sc": "SC-", "ship": "SHIP-", "adr": "ADR-",
           "cc": "CC-", "cn": "CN-", "gs": "GS-", "bc": "BC-PROJ-", "r": "R-", "ps": "PS-"}
# bc + r pad to 1 (i.e. no zero-pad): the established conventions are BC-PROJ-9, BC-PROJ-10, ...
# and R-1, R-2, ... (the risk ledger was the last shared aggregate with model-minted ids —
# 2026-07 review sweep: parallel slices could collide on "next R-NN").
_PAD = {"slice": 3, "sc": 3, "ship": 3, "adr": 3, "cc": 3, "cn": 3, "gs": 3, "bc": 1, "r": 1, "ps": 3}
# The element key that carries a managed id (for reject_supplied_id): a candidate/ship row keys
# on `id`; a slice's join key on a candidate is `slice`.
_ID_KEY = {"sc": "id", "ship": "id", "adr": "id", "slice": "slice",
           "cc": "id", "cn": "id", "gs": "id", "bc": "id", "r": "id", "ps": "id"}

# The calibration-overlay kinds live in critic-calibration-log.json: kind -> its array.
_CALIBRATION_ARRAYS = {"cc": "active_checks", "cn": "calibration_notes", "gs": "gate_skips"}

COUNTERS_KEY = "counters"
MANAGED_KINDS = frozenset(_PREFIX)


def _check(kind: str) -> None:
    if kind not in _PREFIX:
        raise ValueError(
            f"id_allocator: unknown managed kind {kind!r} (expected one of {sorted(_PREFIX)})"
        )


def fmt(kind: str, n: int) -> str:
    """Format `n` as the canonical id for `kind` (e.g. fmt('sc', 7) -> 'SC-007')."""
    _check(kind)
    return f"{_PREFIX[kind]}{int(n):0{_PAD[kind]}d}"


def parse_num(kind: str, value) -> int | None:
    """Trailing integer of an id of `kind` ('SC-007'->7, 'slice-019-x'->19), else None.
    Tolerant of zero-pad + a trailing -name; never raises on garbage/None."""
    _check(kind)
    m = re.match(rf"^{re.escape(_PREFIX[kind])}0*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def scan_max(values, kind: str) -> int:
    """Max trailing-int over an iterable of id strings of `kind` (0 when none/all-malformed)."""
    mx = 0
    for v in values or ():
        n = parse_num(kind, v)
        if n is not None and n > mx:
            mx = n
    return mx


def next_id(data: dict, kind: str, *, seed_max: int = 0) -> str:
    """Mint the next id of `kind` by bumping `data['counters'][kind]` INSIDE the caller's lock.

    The next value is `max(current counter, seed_max) + 1` — monotonic and self-healing: a
    legacy file with no counter, or one whose counter was hand-edited below reality, still mints
    above every existing id when the caller passes `seed_max` = the true historical max. Returns
    the formatted id; mutates `data` in place (the caller's `safe_mutate_text` commits it)."""
    _check(kind)
    if not isinstance(data, dict):
        raise ValueError("id_allocator.next_id: data must be a dict (the decoded vault file)")
    counters = data.setdefault(COUNTERS_KEY, {})
    if not isinstance(counters, dict):
        raise ValueError(f"id_allocator: {COUNTERS_KEY!r} is not a JSON object")
    cur = counters.get(kind)
    cur = cur if isinstance(cur, int) else 0
    nxt = max(cur, int(seed_max or 0)) + 1
    counters[kind] = nxt
    return fmt(kind, nxt)


def reject_supplied_id(kind: str, element) -> None:
    """Raise if `element` (an append payload) carries an id for a managed kind — the
    no-INSERT-with-explicit-PK guard (AC2). A list payload checks every element. Fail-VISIBLE:
    the caller must omit the id and let the allocator fill it."""
    _check(kind)
    key = _ID_KEY[kind]
    items = element if isinstance(element, list) else [element]
    for it in items:
        if isinstance(it, dict) and it.get(key) is not None:
            raise ValueError(
                f"id_allocator: caller supplied a {kind} id ({key}={it.get(key)!r}) — managed "
                f"ids are minted in-lock, never caller-supplied. Omit {key!r} and let the "
                f"allocator fill it."
            )


def id_key(kind: str) -> str:
    """The element key that carries a managed id for `kind` (for callers minting into a record)."""
    _check(kind)
    return _ID_KEY[kind]


def _archive_max(vault, rel: str, array: str, id_field: str, kind: str) -> int:
    """Max id-number in a read-only archive aggregate (a floor; tolerant of absent/malformed)."""
    p = Path(vault) / rel
    if not p.exists():
        return 0
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return 0
    if not isinstance(d, dict):
        return 0
    return scan_max([r.get(id_field) for r in d.get(array, []) if isinstance(r, dict)], kind)


def seed_max_for(vault, kind: str, data: dict) -> int:
    """Cross-source historical FLOOR for `kind` (does file I/O — for the first in-lock mint /
    self-heal). Combines in-`data` ids with archive + on-disk sources so the counter never
    re-issues a retired / archived / on-disk number. Use as ``next_id(data, kind,
    seed_max=seed_max_for(vault, kind, data))``."""
    _check(kind)
    if not isinstance(data, dict):
        data = {}
    if kind == "sc":
        mx = scan_max([c.get("id") for c in data.get("candidates", []) if isinstance(c, dict)], "sc")
        return max(mx, _archive_max(vault, "archive/candidates.json", "candidates", "id", "sc"))
    if kind == "ship":
        mx = scan_max([r.get("id") for r in data.get("rows", []) if isinstance(r, dict)], "ship")
        return max(mx, _archive_max(vault, "archive/shippability.json", "rows", "id", "ship"))
    if kind == "adr":
        dd = Path(vault) / "decisions"
        names = [p.name for p in dd.glob("ADR-*.json")] if dd.is_dir() else []
        return scan_max(names, "adr")
    if kind == "slice":  # claim_candidate also scans folders; this covers candidates + pick_log
        mx = scan_max([c.get("slice") for c in data.get("candidates", []) if isinstance(c, dict)], "slice")
        return max(mx, scan_max([e.get("slice") for e in data.get("pick_log", []) if isinstance(e, dict)], "slice"))
    if kind == "bc":  # build-checks.json rules[] — no archive aggregate to floor against
        return scan_max([r.get("id") for r in data.get("rules", []) if isinstance(r, dict)], "bc")
    if kind == "r":  # risk-register.json risks[] — retired risks stay in-file (no archive), so
        return scan_max([r.get("id") for r in data.get("risks", []) if isinstance(r, dict)], "r")
    if kind == "ps":
        # product-scope.json items[] PLUS the revisions[] retirement ledger.
        #
        # slice-073 (critique B1, blocker) CHANGED THE PREMISE THIS ARM USED TO REST ON. It read:
        # "create-only + revise-preserves-by-id, so the live items[] IS the full history: an id is
        # never retired and never moves to an archive." `revise --cut PS-NNN` falsifies that — a cut
        # id is RETIRED, a lifecycle state `ps` never had. Left unchanged, the floor silently stopped
        # covering retired ids: the FULL chain was executed through supported verbs only —
        # seed_max_for('ps') = 2 -> cut PS-002 -> seed_max 1 (FLOOR DROPPED) -> `vault_edit set
        # --path counters.ps --value 0` (rc=0; that leg is unguarded and is filed as its own
        # candidate — this scan is defense-in-depth against it, NOT a closure of it) -> next_id ->
        # 'PS-002' RE-ISSUED, aliasing a brand-new capability onto the shipped candidate the original
        # PS-002 minted.
        #
        # So revisions[].cut IS the ps retirement history, and the floor scans it. This mirrors the
        # cc/cn/gs arm below EXACTLY — there, runs[].proposals[] keep a retired check's id past its
        # removal so it is never re-issued. Same law, same shape, different ledger.
        #
        # KNOWN GAP, stated so it is never mistaken for coverage (DR-1's caveat on B1): an item
        # dropped via `vault_edit rewrite` leaves NO revisions[] record, so this floor cannot see it.
        # rewrite is a CAS whole-file replace that never consults _MANAGED_KIND, is symmetric for
        # every vault file, and is deliberately out of scope (ADR-080 #6).
        mx = scan_max([i.get("id") for i in data.get("items", []) if isinstance(i, dict)], "ps")
        return max(mx, scan_max(
            [c for r in data.get("revisions") or [] if isinstance(r, dict)
             for c in r.get("cut") or []], "ps"))
    if kind in _CALIBRATION_ARRAYS:
        # Live overlay elements PLUS the run history: runs[].proposals[] keep the ids past
        # proposals minted (check_id/note_id/...), so a RETIRED check's id is never re-issued
        # after `vault_edit remove` deletes its element. parse_num is prefix-anchored, so
        # scanning every string field of a proposal only ever matches this kind's own ids.
        mx = scan_max([r.get("id") for r in data.get(_CALIBRATION_ARRAYS[kind], [])
                       if isinstance(r, dict)], kind)
        for run in data.get("runs", []) or []:
            if isinstance(run, dict):
                for pr in run.get("proposals", []) or []:
                    if isinstance(pr, dict):
                        mx = max(mx, scan_max(
                            [v for v in pr.values() if isinstance(v, str)], kind))
        return mx
    return 0
