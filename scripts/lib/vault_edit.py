"""vault_edit — the v2 canonical, JSON-native vault-write CLI (SVW-1).

The single concurrency-safe channel for skill-driven mutation of a shared-aggregate
vault file, so a skill's write never bypasses the ``_vault_write`` lock (R-32). v2
replaces v1's raw-text ``vault_edit`` (append/rewrite/read/move on ``.md`` byte
streams) with a JSON-aware interface, while keeping v1's proven safe-write
primitives (sidecar lock, EOL-preserving atomic replace, EPERM retry).

Subcommands (a global ``--vault ROOT`` overrides ``$AI_SDLC_VAULT_ROOT`` / the
computed default; every path resolves UNDER the vault root — an absolute path is
accepted iff it lands under the root, a ``..``-escape is a usage error):

  read    --file F [--out-file B]
      Emit the target's current RAW bytes — the byte-exact CAS base for ``rewrite``.
      Prefer ``--out-file`` over shell ``>`` (PowerShell ``>`` emits UTF-16LE+BOM →
      CAS livelock). A missing target emits nothing (the create-case base = empty).

  get     --file F [--path .a.b[0].c]
      Read a JSON subtree/scalar at the dotted ``--path`` (default: the whole doc)
      → stdout. A string value prints raw; anything else prints as compact JSON. A
      missing file or missing path is a usage error (exit 2) so a ``|| fallback``
      fires.

  query   --file F --array A [--where k=v ...]
      Filter array ``A``'s elements (all ``--where`` equalities must match) →
      stdout as a pretty JSON list.

  append  --file F [--array A] (--json S | --content-file C | --stdin) [--allow-duplicate]
          [--unique-key K ...]
      SVW-1 LOCKED read-modify-write: append the element to array ``A`` (auto-
      detected when the doc has exactly one list field). A list element EXTENDS;
      an object/scalar APPENDS. Creates the file/array when absent.
      DUPLICATE-SAFE (SC-041 / ADR-040 + ADR-043): on the ``--stdin`` path ONLY, an
      element byte-identical (canonically) to one of the last ``_DEDUP_WINDOW``
      entries (id-stripped for managed kinds) is SUPPRESSED as idempotent success —
      exit 0, the array UNCHANGED (count +0), a machine-readable ``{"suppressed":true,
      "array":…,"count":…}`` line on stdout (a normal append prints nothing to stdout)
      + a ``DUPLICATE_SUPPRESSED`` note on stderr. ``--json``/``--content-file`` are
      never deduped; ``--allow-duplicate`` forces a genuine immediate duplicate through.
      UNIQUE-KEY guard: each repeatable ``--unique-key K`` names a payload field; if an
      existing element matches the payload on ALL named keys, the append is REFUSED
      (exit 2, fail-visible) — the mechanical dedup for keyed overlays (e.g. one
      gate_skips entry per target_gate). Dict payloads only.

  remove  --file F --array A --id ID [--id-key K]
      SVW-1 LOCKED read-modify-write: remove EXACTLY ONE element of ``A`` whose
      ``--id-key`` (default ``id``) == ``ID``. Fail-visible when no such element
      exists. REFUSED on a managed-kind array (candidates/shippability rows have
      their own lifecycle — archive-move, never in-place delete). This is the wired
      mechanism for retiring an overlay element (e.g. a FALSE-ALARM active_check).

  set     --file F --path .a.b.c (--json S | --value V)
      SVW-1 LOCKED read-modify-write: set the value at the dotted ``--path``
      (``[N]`` index segments must already exist; missing intermediate OBJECT keys
      are created, mkdir -p style; traversing through a non-object is refused).
      ``--json`` parses strictly; ``--value`` parses as JSON, else a string. This
      is the wired mechanism for a nested-field mutation on a NON-array target —
      e.g. /validate-slice's main-thread deferral write
      (``--path .shippability_regression.deferral``). Refused when the path's
      first segment is a managed array of this file (managed rows mutate only via
      update/append — ids stay allocator-minted).

  update  --file F --array A --id ID [--id-key K] [--assumption AID]
          (--set k=v ...) [--append FIELD JSON ...]
      SVW-1 LOCKED read-modify-write: find the record in ``A`` whose ``--id-key``
      (default ``id``) == ``ID``; optionally descend into its ``assumptions[]`` to
      the ``--assumption`` id; apply each ``--set`` (value parsed as JSON, else a
      string) and append each ``--append FIELD JSON`` to a nested array.

  rewrite --file F --base-file B (--content-file C | --stdin)
      Compare-and-swap whole-file rewrite (the read-modify-write class where the
      skill regenerates the whole file). Writes only if on-disk bytes still match
      ``--base-file`` (EOL-normalized compare / EOL-preserving write). Stale base
      → exit 3 (retryable: re-read + re-apply + retry).

  move    --from X --to Y
      Seam-routed directory/file MOVE (the in-loop archive ``mv``; both endpoints
      under the vault root). Refuses a pre-existing landing path (no clobber).

  list    --dir D [--count]
      List immediate child entry names of vault dir ``D`` (or print the count).

  count   --file F [--array A]
      Print the length of array ``A`` (auto-detected when single).

Exit codes:
    0  success
    2  usage error — bad/escaping path, missing/locked content, malformed JSON,
       missing id/path, a non-array target, a missing/clobbering move, a write
       failure (fail-VISIBLE per R-7; never a silent no-op)
    3  ``rewrite`` ONLY — compare-and-swap CONFLICT (retryable signal, distinct
       from usage exit 2)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON
# hooks/MCP, not markdown). So shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/vault_edit.py" ...`, which puts
# scripts/lib (NOT the plugin root) on sys.path[0] — `from scripts.lib import ...`
# would then fail. Add the plugin root here, mirroring the single-skill scripts'
# parents[3] bootstrap. No-op under `-m scripts.lib.vault_edit` from the plugin root.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/vault_edit.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _shard_store, _stdout, id_allocator
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import (
    DuplicateAppendSuppressed,
    StaleVaultBaseError,
    safe_append_text,
    safe_mutate_text,
    safe_rewrite_text,
)

_JSON_DUMP = {"indent": 2, "ensure_ascii": False, "sort_keys": False}

# slice-019 / [[ADR-013]]: (file, array) -> managed id kind. An append to one of these mints the
# id IN-LOCK via id_allocator (AC2) instead of accepting a caller-supplied one — so a hand-authored
# `vault_edit append --json {id:...}` can no longer bypass the allocator and race on an id.
_MANAGED_KIND = {
    ("candidates.json", "candidates"): "sc",
    ("shippability.json", "rows"): "ship",
    # review sweep 2026-07: /reflect's BC-PROJ promotion used to hand-mint the id against
    # this file's own conventions (two parallel reflects could collide) — now allocator-minted.
    ("build-checks.json", "rules"): "bc",
    # NOTE (review sweep 2026-07, /user-test pass): risk-register risks[] deliberately does
    # NOT mint-on-append — risk appenders cross-reference the new R-N (e.g. a candidate's
    # source ref), so they PRE-mint via `alloc --kind r` and carry the id in the payload
    # (the cc/cn/gs pattern), which mint-on-append would reject.
    #
    # slice-073 / [[ADR-080]] (SUPERSEDES [[ADR-067]] §3 on this line; ADR-066 said register, ADR-067
    # said don't, this is the third and final position — read ADR-080 before touching it again).
    #
    # ('product-scope.json', 'items') -> 'ps' IS registered, and the reason the old carve-out gave for
    # omitting it is STILL TRUE — it was just an argument about ONE leg, silently inherited by four.
    #
    # WHAT IS STILL TRUE (ADR-067 §3's first half, retained verbatim): product_scope.persist must
    # REWRITE the model's run-local depends_on labels into minted PS ids inside ONE lock, and `append`
    # mints internally and returns nothing to the caller, so persist could never learn the ids it must
    # substitute. persist therefore does its OWN safe_mutate_text and calls
    # id_allocator.reject_supplied_id('ps', items) as the FIRST statement in its mutate closure. It
    # does not route through vault_edit and never will. Nothing here changes that.
    #
    # WHY IT IS REGISTERED ANYWAY: _MANAGED_KIND is NOT an append-only mechanism. ONE entry is
    # consulted by _managed_kind_for on FOUR legs — append (:446), update (:538), set --path (:655),
    # remove (:603). ADR-067 reasoned about `append` ("it would guard only a hypothetical hand-authored
    # append no production writer takes" — true) and generalized that to the ENTRY. But `remove` and
    # `set --path` are neither hypothetical nor append: they are the generic, DISCOVERABLE, documented
    # way a model deletes a vault array element, and executed against the live shape they each DELETED
    # a scope item at rc=0 with no record and no refusal — walking straight around cmd_revise's
    # omission gate, which would have made that gate theatre. So the entry is REQUIRED for the
    # removal legs, and is belt-and-braces on the persist path that never reaches it.
    #
    # SWEEP THE VERBS, NOT THE HEADLINE (this is the whole lesson — see ADR-080's consequences): the
    # `ps` APPEND leg is refused explicitly in _cmd_append rather than minting, because mint-on-append
    # here would hand a real PS id to an item with no assumptions, `_check_contract` would never run,
    # and the resulting candidate SKIPS /risk-spike step-0 — ADR-067 §5's bypass, reopened by one
    # supported command. Any FUTURE entry (or non-entry) must be argued leg by leg, not headline by
    # headline. `alloc --kind ps` still covers hand-authored pre-minting.
    #
    # WHAT THIS ENTRY DOES **NOT** CLOSE — stated precisely, because an over-claimed sweep is the same
    # defect one level up (code-review CR3, and the honest correction of this very comment's first
    # draft, which asserted a four-leg sweep it had not earned):
    #   * `update` is swept for the ID KEY ONLY (_cmd_update rejects `--set id=...`). It does NOT
    #     enforce the decomposition contract on other fields: `vault_edit update --file
    #     product-scope.json --array items --id PS-001 --set assumptions=[]` -> rc=0, assumptions
    #     STRIPPED, and `materialize` then mints a candidate with `assumptions: []` that SKIPS
    #     /risk-spike step-0. REPRODUCED BY EXECUTION. Not a regression (this leg was equally open
    #     before the registration) and deliberately NOT closed here — closing it is new, unreviewed
    #     behaviour on a leg no production writer takes, which is the M1/`rewrite` precedent (a
    #     pre-existing non-regression escape is NAMED, not closed mid-slice). Filed as its own
    #     candidate.
    #   * `rewrite` never consults this map at all (ADR-080 #6).
    # So the accurate claim is: this entry closes `remove` and `set --path items`, refuses `append`,
    # and guards `update`'s id key. It is NOT a whole-contract guard on every write verb.
    ("product-scope.json", "items"): "ps",
}

# slice-073 / [[ADR-080]] #2: managed kinds whose APPEND leg must REFUSE outright instead of minting.
# Registering a kind normally means "mint the id in-lock"; for `ps` it means "this array has ONE
# guarded writer" — scope items are minted by product_scope persist/revise, which enforce the
# decomposition contract (ADR-067 §5). A raw append cannot, and after registration it would look
# LEGITIMATE (real id, real counter bump) rather than crashing loudly the way it does unregistered.
# A named set, not an `if kind == "ps"` in the append leg: the next kind with a guarded writer must
# be a VISIBLE one-line decision here, not a re-derivation.
_APPEND_REFUSED_KINDS = {"ps"}

# slice-050 / SC-041 (ADR-040 + ADR-043): the bounded, --stdin-scoped duplicate-append guard.
# K = how many trailing elements an identical re-submission is checked against. Small on purpose:
# the bug is an IMMEDIATE re-submission (a lock-timeout retry / heredoc re-run), so a short window
# catches it while a legitimately-identical entry appended much later -- or via a non-stdin path --
# is NEVER suppressed (the over-dedup that critique B1 corrected the earlier whole-array design to avoid).
_DEDUP_WINDOW = 5


def _canon(elem: Any) -> str:
    """Canonical JSON string for duplicate comparison (sort_keys, compact, unicode preserved).
    A DIRECT string compare -- no hash: nothing is persisted/indexed here, so a fingerprint would
    only add a collision surface (slice-050 m2)."""
    return json.dumps(elem, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _dedup_key(existing: Any, kind: str | None) -> str:
    """Canonical key of an ALREADY-STORED element with the managed-kind minted id stripped, so it
    compares equal to the PRE-mint supplied element (managed ids are minted AFTER the element is
    read, so an unstripped compare would never match a retry)."""
    if kind is not None and isinstance(existing, dict):
        idk = id_allocator.id_key(kind)
        if idk in existing:
            existing = {k: v for k, v in existing.items() if k != idk}
    return _canon(existing)


def _is_bounded_duplicate(element: Any, arr: list, kind: str | None) -> bool:
    """True iff `element` (pre-mint, supplied on --stdin) duplicates a RECENT existing entry within
    the last-K window. A LIST payload (--extend) is compared as a UNIT against the immediately-
    preceding block of the same length (M2); a dict/scalar against the last K elements."""
    if isinstance(element, list):
        n = len(element)
        if n == 0 or len(arr) < n:
            return False
        return [_dedup_key(e, kind) for e in arr[-n:]] == [_canon(x) for x in element]
    target = _canon(element)
    return any(_dedup_key(e, kind) == target for e in arr[-_DEDUP_WINDOW:])


# ── path + JSON helpers ────────────────────────────────────────────────────────

def _root(args: argparse.Namespace) -> Path:
    """The vault root: ``--vault`` when given, else the resolved ``VAULT_ROOT``."""
    v = getattr(args, "vault", None)
    return Path(v) if v else VAULT_ROOT


def _resolve_in_vault(root: Path, file_arg: str, *, arg_name: str = "--file") -> Path:
    """Resolve ``file_arg`` under ``root``. Accepts a vault-relative path OR an
    absolute path that lands under ``root``; rejects an empty path, the root dir
    itself, or any ``..``/absolute escape (exit-2 usage errors, fail-VISIBLE)."""
    root_r = Path(root).resolve()
    if not file_arg.strip():
        raise ValueError(f"{arg_name} must name a vault file (got an empty path)")
    fp = Path(file_arg)
    target = (fp if fp.is_absolute() else (Path(root) / fp)).resolve()
    if target == root_r:
        raise ValueError(
            f"{arg_name} {file_arg!r} resolves to the vault root itself, not a file "
            f"under it"
        )
    if root_r not in target.parents:
        raise ValueError(
            f"{arg_name} {file_arg!r} resolves outside the vault root "
            f"({target} is not under {root_r})"
        )
    return target


def _vault_rel_key(root: Path, target: Path) -> str:
    """The managed-kind lookup key for ``target``: its vault-relative path, POSIX-normalised.

    SC-046: key on the RELATIVE path (e.g. ``archive/candidates.json``), NOT ``target.name`` (the
    basename ``candidates.json``) — else an archived copy collides with the LIVE root-level managed
    file and its id-bearing write is wrongly rejected. The ``_MANAGED_KIND`` keys ARE the vault-relative
    paths of the root-level live files, so keying on the relative path keeps the live guard matching
    exactly while ``archive/<managed-file>`` (and any nested path) no longer collides.

    ``.as_posix()`` (forward slashes), NEVER ``str()`` — on Windows ``str()`` yields backslashes and
    would silently mis-key. Relate against ``Path(root).resolve()`` — the SAME resolved root
    ``_resolve_in_vault`` validated ``target`` under — so ``relative_to`` cannot raise; do NOT wrap it in
    ``try/except`` (a swallow would silently disable the live managed-id guard)."""
    return target.relative_to(Path(root).resolve()).as_posix()


def _managed_kind_for(root: Path, target: Path, array: str) -> str | None:
    """The managed id kind (``sc``/``ship``) for a write to ``target``'s ``array``, or ``None``.

    The SINGLE consult point for BOTH write legs (``_cmd_append`` and ``_cmd_update``) — keyed on the
    vault-relative POSIX path (``_vault_rel_key``) so the SC-046 basename collision can never recur and
    the two legs cannot drift apart (BC-PROJ-6: the only consumers of ``_MANAGED_KIND``)."""
    return _MANAGED_KIND.get((_vault_rel_key(root, target), array))


def _candidate_area_near_matches(arr: list, rec: Any, value: str) -> list[str]:
    """Areas OTHER live candidates already assert that casefold-match ``value`` without being byte-equal
    (slice-098, critique m2). Advisory input only — the caller WARNs, never refuses.

    ``_valid_area`` normalizes solely by ``.strip()``, so 'Verification-Gates' and 'verification-gates'
    are two DISTINCT buckets that both read ``known: true`` at the ``/slice --area`` lens, silently
    splitting one area's picks in two with no signal. Today that is contained (4 areas set by one
    deliberate verb); once every candidate can be annotated it is a free-text surface.

    DELEGATES to ``area_resolve`` (code-review CR4) rather than re-deriving the rule: one function owns
    "is this the same bucket?", exactly as the reject set is single-sourced from ``_valid_area``
    (re-deriving either is the SC-185 area-parity hazard). The import is acyclic — ``area_resolve``
    imports ``product_rollup``/``product_scope`` and neither imports this module — and adds no new
    resolution cost, since this CLI already resolves the vault root.

    POPULATION, deliberately candidates-only: this runs entirely on the IN-LOCK array, taking no
    cross-file ``product-scope.json`` read inside the lock for an advisory line. A candidate whose area
    near-matches a PRODUCT-SCOPE area is therefore not flagged here — that half of the signal is
    reported at the read surface, where the lens compares against PS ∪ candidate-asserted areas."""
    from scripts.lib import area_resolve
    siblings = [o for o in arr if isinstance(o, dict) and o is not rec]
    return area_resolve.near_matches(value, area_resolve.asserted_areas(siblings))


def _load_json(target: Path) -> Any:
    """Parse ``target`` as JSON (``{}`` when absent/empty). Raises ValueError on
    malformed JSON (mapped to exit 2)."""
    if not target.exists():
        return {}
    text = target.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target} is not valid JSON: {exc}") from exc


def _dump(data: Any) -> str:
    return json.dumps(data, **_JSON_DUMP) + "\n"


def _current_plugin_version() -> str | None:
    """The running plugin version from .claude-plugin/plugin.json (4.5 artifact stamping).
    Read lazily — only when CREATING a vault file — so vault_edit's hot path is untouched."""
    try:
        with open(_PLUGIN_ROOT / ".claude-plugin" / "plugin.json", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _detect_array_key(data: Any) -> str | None:
    """The sole top-level list-valued key, or ``None`` when zero or multiple."""
    if not isinstance(data, dict):
        return None
    lists = [k for k, v in data.items() if isinstance(v, list)]
    return lists[0] if len(lists) == 1 else None


def _find_by_id(arr: list, id_val: str, *, id_key: str = "id") -> dict | None:
    for e in arr:
        if isinstance(e, dict) and str(e.get(id_key)) == str(id_val):
            return e
    return None


_SEG = re.compile(r"^([^\[\]]+)(?:\[(\d+)\])?$")


def _navigate(obj: Any, path: str) -> Any:
    """Descend a dotted path like ``.a.b[0].c`` (leading dot optional). Raises
    KeyError/IndexError/TypeError on a miss (mapped to exit 2)."""
    p = path.strip()
    if p.startswith("."):
        p = p[1:]
    if not p:
        return obj
    cur = obj
    for raw in p.split("."):
        m = _SEG.match(raw)
        if not m:
            raise KeyError(f"bad path segment {raw!r}")
        key, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError(f"no key {key!r} at this level")
        cur = cur[key]
        if idx is not None:
            cur = cur[int(idx)]  # IndexError/TypeError → caught by caller
    return cur


def _set_value(raw: str) -> Any:
    """``--set``/``--where`` value: parse as JSON (numbers, true/false/null,
    quoted strings, objects), falling back to the bare string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _element_source(args: argparse.Namespace) -> Any:
    """Load the append element from ``--json`` / ``--content-file`` / ``--stdin``."""
    if getattr(args, "json", None) is not None:
        src, label = args.json, "--json"
    elif getattr(args, "content_file", None) is not None:
        src, label = Path(args.content_file).read_text(encoding="utf-8"), "--content-file"
    else:
        src, label = sys.stdin.read(), "--stdin"
    try:
        return json.loads(src)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} content is not valid JSON: {exc}") from exc


def _err(msg: str) -> None:
    sys.stderr.write(f"vault_edit: {msg}\n")


# ── subcommands ────────────────────────────────────────────────────────────────

def _cmd_read(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2
    try:
        data = target.read_bytes() if target.exists() else b""
    except OSError as exc:
        _err(f"cannot read {target}: {exc}"); return 2
    if args.out_file is not None:
        try:
            Path(args.out_file).write_bytes(data)  # byte-safe; avoids PowerShell `>` (B1)
        except OSError as exc:
            _err(f"cannot write --out-file: {exc}"); return 2
        return 0
    sys.stdout.buffer.write(data)  # RAW bytes — bypass the UTF-8 text wrapper
    sys.stdout.buffer.flush()
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
        if not target.exists():
            raise ValueError(f"{target} does not exist")
        data = _load_json(target)
        value = _navigate(data, args.path or ".")
    except ValueError as exc:
        _err(str(exc)); return 2
    except (KeyError, IndexError, TypeError) as exc:
        _err(f"path {args.path!r} not found: {exc}"); return 2
    print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
        data = _load_json(target)
    except ValueError as exc:
        _err(str(exc)); return 2
    arr = data.get(args.array, []) if isinstance(data, dict) else []
    if not isinstance(arr, list):
        _err(f"{args.array} is not a JSON array in {target}"); return 2
    wheres = []
    for w in args.where or []:
        if "=" not in w:
            _err(f"--where {w!r} must be key=value"); return 2
        k, v = w.split("=", 1)
        wheres.append((k, _set_value(v)))
    out = [e for e in arr
           if isinstance(e, dict) and all(e.get(k) == v for k, v in wheres)]
    print(json.dumps(out, **_JSON_DUMP))
    return 0


class _ReRouteToShard(Exception):
    """slice-088 (B2 / M-add-1): raised INSIDE the flat append mutate (UNDER the single lock) when a
    concurrent forward migrate has published the shard dir since the lock-free fast-path route check.
    Writing the row to the (now-derived) cache would strand it there — the next regen rebuilds the
    cache from shards and drops it (the silent lost-update B2 flagged). So ``_cmd_append`` catches
    this and re-routes to the shard store: the flat-vs-shard route is FINALIZED under the lock."""


def _sharded_append(args: argparse.Namespace, root: Path, target: Path, array: str,
                    element: Any) -> int:
    """slice-088 (ADR-106 / M2): the sharded-aggregate append path — the faithful behavioral twin
    (CC-001) of the flat ``--stdin`` bounded-dedup contract, routed to ``_shard_store`` instead of a
    whole-file RMW of the derived cache. gate-log is NOT a managed-kind file, so id-minting /
    ``--unique-key`` / bc-shape guards do not apply here; the bounded dedup guard + fail-visible
    error posture do. The dedup predicate is evaluated UNDER the shard store's single lock, so the
    check + write are atomic (no concurrent append can slip between them)."""
    rel = _vault_rel_key(root, target)
    dedup_check = None
    dedup_tail = 0
    if args.stdin and not args.allow_duplicate:
        # same predicate + window K as the flat path; recent = the bounded seq-ordered tail.
        dedup_tail = len(element) if isinstance(element, list) else _DEDUP_WINDOW
        dedup_check = lambda recent: _is_bounded_duplicate(element, recent, None)  # noqa: E731
    try:
        _shard_store.append_entry(root, rel, array, element,
                                  dedup_check=dedup_check, dedup_tail=dedup_tail)
    except DuplicateAppendSuppressed as dup:
        # idempotent SUCCESS — identical contract to the flat path (exit 0 + machine-readable stdout
        # signal + greppable stderr note; array count +0). A non-zero would re-trigger the harness
        # retry the guard exists to absorb.
        print(json.dumps({"suppressed": True, "array": dup.array, "count": dup.count},
                         ensure_ascii=False))
        _err(f"DUPLICATE_SUPPRESSED array={dup.array} count={dup.count} — identical --stdin "
             f"element already present in the last {_DEDUP_WINDOW}; append skipped "
             f"(array unchanged; use --allow-duplicate to force)")
        return 0
    except FileExistsError as exc:
        # O_EXCL seq collision — the fail-visible ledger-integrity stop (B1), never a silent drop.
        _err(f"sharded append to {target} failed — shard seq collision (fail-visible per R-7): {exc}")
        return 2
    except TimeoutError as exc:
        _err(f"sharded append to {target} timed out — another process holds the gate-log lock; "
             f"wait a moment or check for a stalled session, then retry: {exc}")
        return 2
    except (ValueError, RuntimeError, OSError) as exc:
        _err(f"sharded append to {target} failed (fail-visible per R-7): {exc}")
        return 2
    return 0


def _cmd_append(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        target = _resolve_in_vault(root, args.file)
        element = _element_source(args)
    except ValueError as exc:
        _err(str(exc)); return 2
    except OSError as exc:
        _err(f"cannot read content: {exc}"); return 2

    # slice-088 (ADR-106): route an allowlisted, ALREADY-MIGRATED sharded aggregate's append to the
    # record-level shard store instead of a whole-file RMW of the derived cache. A sharded aggregate
    # requires an EXPLICIT --array (gate-log has two list fields, so auto-detect returns None); the
    # predicate keys on the vault-relative POSIX path (m3) AND shard-dir-exists (pre-migration →
    # the flat safe_mutate_text path below, unchanged — the AC2 backward-compatible fallback).
    # NOTE: a distinct name (`shard_rel`) — the mutate() closure below LOCALLY assigns `rel` in its
    # managed-kind branches, so reusing `rel` here would shadow into an UnboundLocalError.
    shard_rel = _vault_rel_key(root, target)
    shardable = bool(args.array) and _shard_store.sharded_dir_name(shard_rel, args.array) is not None
    # Fast path: an already-migrated sharded aggregate routes straight to the shard store (avoids
    # loading the whole derived cache — the M4 hot path). The under-lock re-check in mutate() below
    # closes the residual forward-migrate-vs-append race the lock-free check leaves open.
    if shardable and _shard_store.is_sharded(root, shard_rel, args.array):
        return _sharded_append(args, root, target, args.array, element)

    def mutate(text: str) -> str:
        # slice-088 (B2 / M-add-1): FINALIZE the flat-vs-shard route UNDER the lock. If a concurrent
        # migrate published the shard dir since the lock-free fast-path check, do NOT write the row
        # into the now-derived cache (the next regen would drop it) — re-route to the shard store.
        if shardable and _shard_store.is_sharded(root, shard_rel, args.array):
            raise _ReRouteToShard()
        was_create = not text.strip()  # 4.5: a brand-new file gets a _plugin_version stamp
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{target} top-level is not a JSON object")
        key = args.array or _detect_array_key(data)
        if key is None:
            raise ValueError(
                "no --array given and the doc has zero or multiple array fields — "
                "name the target array with --array"
            )
        arr = data.setdefault(key, [])
        if not isinstance(arr, list):
            raise ValueError(f"target field {key!r} is not a JSON array")
        # --unique-key guard (keyed-overlay dedup): refuse the append when an existing
        # element matches the payload on ALL named keys. Fail-VISIBLE (exit 2), never a
        # silent no-op — the caller should update/remove the existing element instead.
        if getattr(args, "unique_key", None):
            if not isinstance(element, dict):
                raise ValueError("--unique-key requires a single JSON-object payload "
                                 "(append one element at a time)")
            missing = [k for k in args.unique_key if k not in element]
            if missing:
                raise ValueError(f"--unique-key field(s) missing from the payload: "
                                 f"{', '.join(missing)}")
            clash = next((e for e in arr if isinstance(e, dict)
                          and all(e.get(k) == element.get(k) for k in args.unique_key)), None)
            if clash is not None:
                keys = ", ".join(f"{k}={element.get(k)!r}" for k in args.unique_key)
                raise ValueError(
                    f"append --unique-key conflict: {key!r} already has an element with "
                    f"{keys} — update or remove it instead of appending a duplicate")
        # slice-019 / AC2: a managed-kind array (candidates -> SC, rows -> SHIP) mints its id
        # IN-LOCK and REJECTS any caller-supplied id (the no-explicit-PK guard). The seed floor is
        # computed once from live ∪ archive; the persisted counter is authoritative thereafter.
        kind = _managed_kind_for(_root(args), target, key)
        # slice-073 / [[ADR-080]] #2: a managed kind whose array has ONE guarded writer refuses the
        # raw append outright rather than minting. Placed FIRST -- before the duplicate-suppressor
        # and before id allocation -- so no path can return a false success (the slice-071 idiom, and
        # the same reason it sits there: an exit-0 suppression on a write that must never happen is
        # the worst of both). raise ValueError -> _run_mutate exit 2, target UNTOUCHED (no temp, no
        # replace), so counters.ps is not bumped either.
        if kind in _APPEND_REFUSED_KINDS:
            rel = _vault_rel_key(_root(args), target)
            raise ValueError(
                f"vault_edit append: refusing to append to the managed {rel}/{key} array -- scope "
                f"items are minted by `product_scope persist` / `product_scope revise`, which "
                f"enforce the decomposition contract (every item carries a BLOCKING assumption, "
                f"ADR-067 section 5), never appended raw. An item appended here would carry a real, "
                f"monotonic PS id with NO assumptions, so its candidate would SKIP /risk-spike "
                f"step-0 -- the pipeline's reality gate -- on exactly the least-understood work in "
                f"the product. To ADD a capability: re-run `product_scope revise --items-file <the "
                f"FULL item list, new item last, no `id` on it>`. Nothing was written."
            )
        # slice-071 / SC-151 (ADR-075): mint-time shape guard for build-check rules. A rule
        # whose `applies_when` is not a JSON object silently enforces NOTHING downstream
        # (build_checks_audit drops it), so reject it at MINT. Placed right after the kind
        # lookup and BEFORE the --stdin duplicate-suppressor + id allocation (m2) so no path
        # can return a false success for a malformed rule. The message names the field +
        # rule-text + array index, NEVER a BC-PROJ id (unallocated here — M-add-3). raise
        # ValueError -> _run_mutate exit 2 + safe_mutate_text leaves the file untouched. The
        # UPDATE write leg has its own post-mutation guard (_cmd_update — M1).
        if kind == "bc":
            from scripts.lib.build_checks_integrity import validate_rule_shape
            for _off, _rule in enumerate(element if isinstance(element, list) else [element]):
                _problems = validate_rule_shape(_rule, tier="mint")
                if _problems:
                    _p = _problems[0]
                    raise ValueError(
                        f"vault_edit append: refusing to mint a malformed build-check rule "
                        f"at rules[{len(arr) + _off}] — {_p['message']}. "
                        f"(rule text: {_p['rule_text']!r}). Fix the `applies_when` shape "
                        f"(an object, e.g. {{\"glob\": \"**/*.py\"}} or {{\"always\": true}}) "
                        f"and re-append; nothing was written."
                    )
        # slice-098 / SC-212 ([[ADR-125]] sections 4 + 5): the MINT leg of the candidate area guard —
        # `_APPEND_REFUSED_KINDS` is {"ps"}, so `sc` MINTS on append and a payload carrying an invalid
        # `area` would have landed a real allocator-minted SC id at rc=0, a leg the A2 spike never covered.
        #
        # ITERATES list payloads, byte-for-byte mirroring the bc precedent above (:593) — `_element_source`
        # json-loads ANY shape and the leg does `arr.extend(element)` for a list, minting one id per
        # element (:611-617). A guard written to inspect `element.get('area')` alone would silently pass
        # `append --json '[{...},{"title":"x","area":""}]'` (M2: a first-element-only test passes too,
        # which is why the enforcing test puts the bad value in the SECOND element).
        #
        # Placed AFTER the bc guard and BEFORE the --stdin duplicate-suppressor + id allocation, for the
        # same reason the bc guard is: no path may return a false success, and nothing is written on a
        # raise (safe_mutate_text leaves the target untouched, so counters.sc is not bumped either).
        if kind == "sc":
            from scripts.lib import product_scope as _ps
            # The near-match population grows as the payload is walked, so element N is compared against
            # the existing rows AND the earlier elements of this same append (code-review CR1).
            _seen_rows = list(arr)
            for _off, _cand in enumerate(element if isinstance(element, list) else [element]):
                if not isinstance(_cand, dict):
                    continue                  # a non-dict element is the allocator's problem, not the area's
                if "component" in _cand:
                    raise ValueError(
                        f"vault_edit append: refusing to mint candidate at candidates[{len(arr) + _off}] "
                        f"carrying `component` — a candidate carries `area`, never `component` "
                        f"([[ADR-125]] section 4; `component` is the slice-084 back-compat alias for "
                        f"product-scope ITEMS only, and would be read by nothing here). Use `area` "
                        f"instead; nothing was written."
                    )
                _area = _cand.get("area")
                if _area is None:
                    continue                  # absent/null = a legal un-annotated candidate (the norm)
                try:
                    _valid = _ps._valid_area(_area)
                except _ps._Refuse as exc:
                    raise ValueError(
                        f"vault_edit append: refusing to mint candidate at "
                        f"candidates[{len(arr) + _off}] with an invalid area {_area!r} — {exc} "
                        f"Nothing was written."
                    ) from exc
                _cand["area"] = _valid        # persist the NORMALIZED value (CR2), as the PS seam does
                # The m2 split-bucket advisory belongs on EVERY leg that can create the split, and this
                # diff newly invites the mint path to carry an area (/reflect's residue capture). A WARN
                # only on the update leg would leave the mint path silently minting the second bucket
                # (code-review CR1). Advisory: rc unchanged, the row still mints.
                _near = _candidate_area_near_matches(_seen_rows, None, _valid)
                if _near:
                    _err(f"WARN vault_edit append: minted candidate area {_valid!r} case-matches "
                         f"existing candidate area(s) {', '.join(repr(n) for n in _near)} but is not "
                         f"byte-equal — this mints a SECOND bucket the `/slice --area` lens reports as "
                         f"known, splitting the area's picks in two. Minted anyway (advisory); "
                         f"re-annotate to the existing spelling if that was a typo.")
                _seen_rows.append(_cand)
        # slice-050 / SC-041 (ADR-040 + ADR-043): bounded, --stdin-scoped duplicate guard. Runs
        # BEFORE the id-mint so the PRE-mint element compares against the id-stripped existing
        # records. On a hit, raise DuplicateAppendSuppressed — safe_mutate_text leaves the target
        # UNTOUCHED on a raise (no temp, no replace), and _cmd_append maps the raise to exit 0.
        if args.stdin and not args.allow_duplicate and _is_bounded_duplicate(element, arr, kind):
            raise DuplicateAppendSuppressed(array=key, count=len(arr))
        if kind is not None:
            id_allocator.reject_supplied_id(kind, element)
            seed = id_allocator.seed_max_for(_root(args), kind, data)
            for it in (element if isinstance(element, list) else [element]):
                if isinstance(it, dict):
                    it[id_allocator.id_key(kind)] = id_allocator.next_id(data, kind, seed_max=seed)
        if isinstance(element, list):
            arr.extend(element)
        else:
            arr.append(element)
        if was_create and "_plugin_version" not in data:
            ver = _current_plugin_version()
            if ver:
                data["_plugin_version"] = ver  # skew detection (4.5); readers WARN on a newer stamp
        return _dump(data)

    try:
        return _run_mutate(target, mutate)
    except _ReRouteToShard:
        # slice-088 (B2/M-add-1): a concurrent migrate published the shard dir while we held the lock
        # for a flat write; the lock is now released — re-route this append to the shard store.
        return _sharded_append(args, root, target, args.array, element)
    except DuplicateAppendSuppressed as dup:
        # slice-050 / SC-041 (M-add-1): idempotent SUCCESS. The identical --stdin element is
        # already present within the recent window, so the desired end state (one record) holds.
        # Exit 0 — a non-zero would re-trigger the harness retry that CAUSED the bug. The
        # suppression is surfaced on BOTH a machine-readable STDOUT signal (callers that discard
        # stderr can still branch on it; a normal append prints nothing to stdout) AND a greppable
        # stderr note. The array count is +0 (the documented count-observable contract, m1).
        print(json.dumps({"suppressed": True, "array": dup.array, "count": dup.count},
                         ensure_ascii=False))
        _err(f"DUPLICATE_SUPPRESSED array={dup.array} count={dup.count} — identical --stdin "
             f"element already present in the last {_DEDUP_WINDOW}; append skipped "
             f"(array unchanged; use --allow-duplicate to force)")
        return 0


def _cmd_update(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2
    sets = []
    for s in args.set or []:
        if "=" not in s:
            _err(f"--set {s!r} must be key=value"); return 2
        k, v = s.split("=", 1)
        sets.append((k, _set_value(v)))
    appends = []
    for field, raw in args.append or []:
        try:
            appends.append((field, json.loads(raw)))
        except json.JSONDecodeError as exc:
            _err(f"--append {field} value is not valid JSON: {exc}"); return 2

    def mutate(text: str) -> str:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} is not valid JSON: {exc}") from exc
        arr = data.get(args.array) if isinstance(data, dict) else None
        if not isinstance(arr, list):
            raise ValueError(f"{args.array!r} is not a JSON array in {target}")
        # slice-019 / AC2 (CR1): the update path must not REASSIGN a managed id out of band.
        # `update --set <id-key>=...` on a managed-kind file/array would bypass the in-lock
        # allocator exactly like a caller-supplied append id — so reject it (the no-explicit-PK
        # guard's update leg; the design's "append/update id-rejection" enforcement, not prose).
        # Other field updates (status/progress/slice/...) are unaffected.
        kind = _managed_kind_for(_root(args), target, args.array)
        if kind is not None:
            idk = id_allocator.id_key(kind)
            if any(k == idk for k, _ in sets):
                rel = _vault_rel_key(_root(args), target)
                raise ValueError(
                    f"vault_edit update: refusing to set the managed {kind} id key {idk!r} on "
                    f"{rel}/{args.array} — managed ids are minted in-lock by the allocator, "
                    f"never reassigned out of band (slice-019/AC2). Update other fields, not the id."
                )
        rec = _find_by_id(arr, args.id, id_key=args.id_key)
        if rec is None:
            raise ValueError(f"no {args.array} record with {args.id_key}={args.id!r}")
        tgt = rec
        if args.assumption:
            subs = rec.get("assumptions")
            if not isinstance(subs, list):
                raise ValueError(f"record {args.id!r} has no assumptions[] array")
            tgt = _find_by_id(subs, args.assumption, id_key="id")
            if tgt is None:
                raise ValueError(f"no assumption id={args.assumption!r} in {args.id!r}")
        for k, v in sets:
            tgt[k] = v
        for field, elem in appends:
            lst = tgt.setdefault(field, [])
            if not isinstance(lst, list):
                raise ValueError(f"--append target {field!r} is not a JSON array")
            lst.append(elem)
        # slice-071 / SC-151 (ADR-075 / M1): POST-mutation shape guard on the update leg. The
        # managed guard above refuses only the id KEY; `update --set applies_when=<string>` was
        # proven OPEN (exit 0, live rule corrupted). Re-validate the RESULTING record and raise
        # if `applies_when` is no longer a JSON object -> _run_mutate exit 2, file untouched. The
        # record already carries an id here, so the message may name it (M-add-3, audit-side).
        if kind == "bc":
            from scripts.lib.build_checks_integrity import validate_rule_shape
            _problems = validate_rule_shape(rec, tier="mint")
            if _problems:
                _p = _problems[0]
                raise ValueError(
                    f"vault_edit update: refusing to write a malformed build-check rule "
                    f"{rec.get('id', '?')!r} — {_p['message']}. The update is rejected and "
                    f"{target.name} is left unchanged; fix the `applies_when` shape and retry."
                )
        # slice-093 / SC-170 + M-add-2 ([[ADR-118]]): `update` is the LAST open managed write leg
        # (append/remove/set --path already refuse a managed kind). Mirror the kind=='bc' precedent
        # above: validate the RESULTING product-scope record and raise (-> _run_mutate exit 2, file
        # byte-untouched) if it ends up (a) with no blocking assumption -- SC-170: an assumptionless
        # capability mints a candidate that SKIPS /risk-spike step-0 (ADR-067 §5) -- or (b) with a
        # SUPPLIED present area that fails the typed recognizer -- M-add-2: the fourth area seam,
        # symmetric with persist/revise/set-area. Lazy import: product_scope does not import vault_edit,
        # so the cross-module import is acyclic (mirrors the bc guard importing validate_rule_shape).
        if kind == "ps":
            from scripts.lib import product_scope as _ps
            if not _ps.has_blocking_assumption(rec):
                raise ValueError(
                    f"vault_edit update: refusing to write product-scope item {rec.get('id', '?')!r} "
                    f"with no BLOCKING assumption — an assumptionless capability mints a candidate that "
                    f"SKIPS /risk-spike step-0 (ADR-067 section 5, the pipeline's reality gate). "
                    f"{target.name} is left unchanged; keep at least one `blocking: true` assumption "
                    f"and retry."
                )
            # M-add-2: mediate ONLY a SUPPLIED present area (area/component supplied by THIS update, and
            # not a --assumption sub-record edit) via the same _valid_area the typed seams use, so a
            # pre-existing legacy area on an unrelated update is NOT re-judged (no over-tightening — the
            # honest mirror of _load_items' "only re-SUPPLIED malformed refuses"). Absent/None stays legal.
            # "Supplied" spans BOTH write verbs: `--set area=<v>` AND `--append area <elem>` (code-review
            # slice-093/m1) — the append leg makes rec['area'] a list, which _valid_area's type-guard
            # refuses; without it, `update --append area '"junk"'` wrote an unvalidated non-string area at
            # rc=0, the same fourth-seam differential this guard exists to close, one verb over.
            _area_supplied = (any(k in ("area", "component") for k, _ in sets)
                              or any(f in ("area", "component") for f, _ in appends))
            if not args.assumption and _area_supplied:
                _area = rec.get("area")
                if _area is None:
                    _area = rec.get("component")
                if _area is not None:
                    try:
                        _ps._valid_area(_area)
                    except _ps._Refuse as exc:
                        raise ValueError(
                            f"vault_edit update: refusing to write product-scope item "
                            f"{rec.get('id', '?')!r} with an invalid area — {exc}. {target.name} is "
                            f"left unchanged."
                        ) from exc
        # slice-098 / SC-212 ([[ADR-125]] section 4): the CANDIDATE twin of the ps guard above. `update` is
        # the SANCTIONED annotation seam for a candidate's own `area` (ADR-125 section 2 dropped the typed
        # producer verb), so this IS the mediation point AC1 names — there is no other typed door to guard.
        #
        # It JUDGES THE SUPPLIED KEY, deliberately NOT the ps guard's area-then-component fallback: DR-1
        # proved BY EXECUTION that the shipped ps guard accepts `--set component=<anything>` at rc=0,
        # because it detects a supplied area OR component and then judges `rec['area']` first — so a
        # supplied component is detected and never judged. That hole is PRE-EXISTING and out of scope here
        # (to be filed as its own candidate at /reflect), but this slice must not inherit it, and the ps
        # guard above must stop being
        # cited as a proven-complete precedent.
        #
        # SUPPLIED-ONLY, like the ps pragma: fire only when THIS update writes the key, never re-judging a
        # pre-existing value on an unrelated edit, and never on an `--assumption` sub-record edit (which
        # writes into the assumption dict, not the candidate record). "Supplied" spans BOTH write verbs —
        # `--append area` makes the field a list, which _valid_area's type-guard refuses (the slice-093
        # one-verb-over differential).
        if kind == "sc" and not args.assumption:
            from scripts.lib import product_scope as _ps
            _supplied = {k for k, _ in sets} | {f for f, _ in appends}
            # CHOSEN COMPONENT CONTRACT for kind=='sc' (ADR-125 section 4): a supplied `component` on a
            # candidate is REFUSED OUTRIGHT. Candidates carry `area`; `component` is the slice-084
            # back-compat alias for PS ITEMS only, so accepting it here would store a key nothing ever
            # reads — the silently-inert JOIN twin slice-080's M-add-1 caught. ADR-092 section 2's
            # "candidates carry no component" survives this slice verbatim on that key.
            if "component" in _supplied:
                raise ValueError(
                    f"vault_edit update: refusing to write `component` on candidate "
                    f"{rec.get('id', '?')!r} — a candidate carries `area`, never `component` "
                    f"([[ADR-125]] section 4; `component` is the slice-084 back-compat alias for "
                    f"product-scope ITEMS only). A `component` stored here would be read by nothing. "
                    f"Use `--set area=<NAME>` instead; {target.name} is left unchanged."
                )
            if "area" in _supplied:
                _area = rec.get("area")
                # `--set area=null` is the SANCTIONED un-annotate seam (ADR-125 section 6 / critique M3):
                # _set_value json-parses `null` to None, and None is a legal un-annotated state, so it
                # passes at rc=0. CONTRACT: the key REMAINS PRESENT with value null (`--set` cannot delete
                # a key) — own_area's totality reads that as absent, but a reader comparing `'area' in
                # cand` would see it differently, so the seam is DOCUMENTED, not merely allowed.
                if _area is not None:
                    try:
                        _valid = _ps._valid_area(_area)
                    except _ps._Refuse as exc:
                        raise ValueError(
                            f"vault_edit update: refusing to write candidate {rec.get('id', '?')!r} "
                            f"with an invalid area {_area!r} — {exc}. {target.name} is left unchanged."
                        ) from exc
                    # PERSIST THE NORMALIZED value, not the raw one (code-review CR2). `_valid_area`'s
                    # contract is check == persisted-value (slice-076) and the PS seam honours it
                    # (product_scope.py `it["area"] = _valid_area(grp)`); storing the raw string would
                    # leave a check/write differential that only the read side collapses, so a reader
                    # comparing `cand['area']` directly would disagree with the lens.
                    rec["area"] = _valid
                    _near = _candidate_area_near_matches(arr, rec, _valid)
                    if _near:
                        # critique m2 — SPLIT-BUCKET advisory, never a refusal (ADR-124 section 6's
                        # visibility-not-refusal stance). Warn at ANNOTATION time so the split never
                        # accumulates; the lens carries the read-time half of the same signal.
                        _err(f"WARN vault_edit update: candidate {rec.get('id', '?')!r} area "
                             f"{_valid!r} case-matches existing candidate area(s) "
                             f"{', '.join(repr(n) for n in _near)} but is not byte-equal — this mints a "
                             f"SECOND bucket the `/slice --area` lens reports as known, splitting the "
                             f"area's picks in two. Written anyway (advisory); re-annotate to the "
                             f"existing spelling if that was a typo.")
        return _dump(data)

    return _run_mutate(target, mutate)


def _cmd_remove(args: argparse.Namespace) -> int:
    """Remove EXACTLY ONE element by id — the wired 'retire' mechanism for overlay
    arrays (e.g. a FALSE-ALARM active_check in critic-calibration-log.json). Managed
    arrays refuse: candidate/shippability rows are moved to archive, never deleted."""
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2

    def mutate(text: str) -> str:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} is not valid JSON: {exc}") from exc
        arr = data.get(args.array) if isinstance(data, dict) else None
        if not isinstance(arr, list):
            raise ValueError(f"{args.array!r} is not a JSON array in {target}")
        if _managed_kind_for(_root(args), target, args.array) is not None:
            rel = _vault_rel_key(_root(args), target)
            raise ValueError(
                f"vault_edit remove: refusing to delete from the managed {rel}/{args.array} "
                f"array — managed records have their own lifecycle (archive-move on "
                f"ship/reject), never an in-place delete.")
        rec = _find_by_id(arr, args.id, id_key=args.id_key)
        if rec is None:
            raise ValueError(
                f"no {args.array} record with {args.id_key}={args.id!r} to remove "
                f"(fail-visible per R-7; nothing was changed)")
        arr.remove(rec)
        return _dump(data)

    return _run_mutate(target, mutate)


def _cmd_set(args: argparse.Namespace) -> int:
    """Set a nested value at a dotted --path — the wired mechanism for non-array
    field mutations (e.g. validation.json's shippability_regression.deferral)."""
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2
    if args.json is not None:
        try:
            value = json.loads(args.json)
        except json.JSONDecodeError as exc:
            _err(f"--json is not valid JSON: {exc}"); return 2
    else:
        value = _set_value(args.value)

    p = (args.path or "").strip()
    if p.startswith("."):
        p = p[1:]
    segs: list[tuple[str, str | None]] = []
    for raw in p.split(".") if p else []:
        m = _SEG.match(raw)
        if not m:
            _err(f"set: bad --path segment {raw!r}"); return 2
        segs.append((m.group(1), m.group(2)))
    if not segs:
        _err("set: --path must name at least one key (e.g. .shippability_regression.deferral)")
        return 2

    def mutate(text: str) -> str:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{target} top-level is not a JSON object")
        if _managed_kind_for(_root(args), target, segs[0][0]) is not None:
            rel = _vault_rel_key(_root(args), target)
            raise ValueError(
                f"vault_edit set: refusing --path into the managed {rel}/{segs[0][0]} "
                f"array — managed rows mutate only via update/append (ids stay "
                f"allocator-minted).")
        cur: Any = data
        for i, (key, idx) in enumerate(segs):
            last = i == len(segs) - 1
            if not isinstance(cur, dict):
                raise ValueError(f"set: cannot descend into {key!r} — parent is not a JSON object")
            if idx is None:
                if last:
                    cur[key] = value
                else:
                    nxt = cur.get(key)
                    if nxt is None:
                        nxt = cur[key] = {}  # create missing intermediate OBJECTS only
                    cur = nxt
            else:
                lst = cur.get(key)
                if not isinstance(lst, list):
                    raise ValueError(f"set: {key!r} is not a JSON array (needed for [{idx}])")
                n = int(idx)
                if n >= len(lst):
                    raise ValueError(
                        f"set: index [{n}] out of range for {key!r} (len {len(lst)}) — "
                        f"list slots are never created, only objects")
                if last:
                    lst[n] = value
                else:
                    cur = lst[n]
        return _dump(data)

    return _run_mutate(target, mutate)


def _run_mutate(target: Path, mutate) -> int:
    try:
        safe_mutate_text(target, mutate)
    except ValueError as exc:
        _err(str(exc)); return 2
    except TimeoutError as exc:
        # A lock timeout is a concurrency signal, not a transient I/O failure — an
        # immediate retry will just time out again against the same holder.
        _err(f"write to {target} timed out — another process holds the vault lock; "
             f"wait a moment or check for a stalled session/editor, then retry: {exc}")
        return 2
    except OSError as exc:
        _err(f"write to {target} failed (fail-visible per R-7): {exc}"); return 2
    return 0


def _cmd_rewrite(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2
    try:
        base = Path(args.base_file).read_bytes()
    except OSError as exc:
        _err(f"cannot read --base-file: {exc}"); return 2
    try:
        content = (Path(args.content_file).read_text(encoding="utf-8")
                   if args.content_file is not None else sys.stdin.read())
    except (OSError, ValueError) as exc:  # BB-03: ValueError covers UnicodeDecodeError (non-UTF-8 content/stdin)
        _err(f"cannot read content: {exc}"); return 2
    try:
        safe_rewrite_text(target, content, expected_base=base)
    except StaleVaultBaseError as exc:
        _err(f"rewrite CONFLICT (exit 3) — {exc}"); return 3
    except (OSError, TimeoutError) as exc:
        _err(f"rewrite of {target} failed (fail-visible per R-7): {exc}"); return 2
    return 0


def _cmd_move(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        src = _resolve_in_vault(root, args.src, arg_name="--from")
        dst = _resolve_in_vault(root, args.dst, arg_name="--to")
    except ValueError as exc:
        _err(str(exc)); return 2
    if src == dst:
        _err(f"move --from and --to resolve to the same path ({src}) — refusing a no-op"); return 2
    if not src.exists():
        _err(f"move source {src} does not exist (fail-visible per R-7)"); return 2
    landing = dst / src.name if dst.is_dir() else dst
    if landing.exists():
        _err(f"move landing path {landing} already exists — refusing to overwrite "
             f"(preserves /archive 'stop if already archived')"); return 2
    try:
        shutil.move(str(src), str(dst))
    except (OSError, shutil.Error) as exc:
        _err(f"move {src} -> {dst} failed (fail-visible per R-7): {exc}"); return 2
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.dir, arg_name="--dir")
    except ValueError as exc:
        _err(str(exc)); return 2
    if not target.is_dir():
        # Fail-visible (R-7): a missing / typo'd --dir was previously indistinguishable
        # from an empty one (both printed nothing + exited 0). An EXISTING empty dir still
        # prints nothing and exits 0; a non-existent / non-directory target exits 2.
        _err(f"--dir {target} does not exist or is not a directory — cannot list."); return 2
    entries = sorted(p.name for p in target.iterdir())
    if args.count:
        print(len(entries))
    else:
        for e in entries:
            print(e)
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
        data = _load_json(target)
    except ValueError as exc:
        _err(str(exc)); return 2
    key = args.array or _detect_array_key(data)
    if key is None:
        _err("specify --array (the doc has zero or multiple array fields)"); return 2
    arr = data.get(key) if isinstance(data, dict) else None
    if not isinstance(arr, list):
        # Fail-visible (R-7): printing `0` for a non-array field hid typos and schema
        # drift (the field was scalar/dict/absent, not an empty array).
        found = "no such field" if arr is None else f"a {type(arr).__name__}"
        _err(f"field `{key}` is not a JSON array in {target} (found {found}) — "
             f"count needs an array field."); return 2
    print(len(arr))
    return 0


def _cmd_alloc(args: argparse.Namespace) -> int:
    """Mint the next id of --kind IN-LOCK (bump counters.<kind> on --file, seeded from
    live ∪ archive ∪ on-disk) and print it — the allocator CLI for a record WRITTEN OUTSIDE
    vault_edit (an ADR file). slice-019 / AC2: the only race-free way to reserve such a number."""
    try:
        target = _resolve_in_vault(_root(args), args.file)
    except ValueError as exc:
        _err(str(exc)); return 2
    if args.kind not in id_allocator.MANAGED_KINDS:
        _err(f"--kind {args.kind!r} is not managed (expected one of {sorted(id_allocator.MANAGED_KINDS)})")
        return 2
    holder: dict = {}

    def mutate(text: str) -> str:
        data = json.loads(text) if text.strip() else {}
        if not isinstance(data, dict):
            raise ValueError(f"{target} top-level is not a JSON object")
        seed = id_allocator.seed_max_for(_root(args), args.kind, data)
        holder["id"] = id_allocator.next_id(data, args.kind, seed_max=seed)
        return _dump(data)

    rc = _run_mutate(target, mutate)
    if rc == 0:
        print(holder["id"])
    return rc


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    # --vault is accepted in EITHER position (before the subcommand on the top
    # parser, or after it on each subparser). The subparser copy uses SUPPRESS so
    # an omitted --vault never clobbers a value the top parser already captured.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--vault", default=argparse.SUPPRESS,
        help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)",
    )
    p = argparse.ArgumentParser(
        prog="vault_edit",
        description="v2 JSON-native vault-write CLI (SVW-1): read/get/query/append/"
                    "remove/update/rewrite/move/list/count/alloc under VAULT_ROOT via "
                    "the _vault_write lock (R-32).",
    )
    p.add_argument(
        "--vault", default=None,
        help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    rd = sub.add_parser("read", parents=[common], help="raw bytes → CAS base")
    rd.add_argument("--file", required=True)
    rd.add_argument("--out-file", default=None,
                    help="write raw bytes here (byte-safe; avoids PowerShell `>`)")

    gt = sub.add_parser("get", parents=[common], help="JSON subtree/scalar at --path → stdout")
    gt.add_argument("--file", required=True)
    gt.add_argument("--path", default=".", help="dotted path, e.g. .mode or .a.b[0].c")

    qy = sub.add_parser("query", parents=[common], help="filter an array → stdout")
    qy.add_argument("--file", required=True)
    qy.add_argument("--array", required=True)
    qy.add_argument("--where", action="append", metavar="KEY=VALUE",
                    help="equality filter (repeatable)")

    ap = sub.add_parser("append", parents=[common], help="SVW-1 locked array append")
    ap.add_argument("--file", required=True)
    ap.add_argument("--array", default=None, help="target array (auto-detected when single)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", default=None, help="the element as a JSON string")
    g.add_argument("--content-file", default=None, help="read the element JSON from this file")
    g.add_argument("--stdin", action="store_true", help="read the element JSON from stdin")
    ap.add_argument("--allow-duplicate", action="store_true",
                    help="bypass the bounded --stdin duplicate guard (force a genuine "
                         "immediate duplicate through; SC-041 / ADR-043)")
    ap.add_argument("--unique-key", action="append", metavar="KEY",
                    help="refuse the append (exit 2) when an existing element matches the "
                         "payload on ALL named keys (repeatable; keyed-overlay dedup, e.g. "
                         "one gate_skips entry per target_gate)")

    rm = sub.add_parser("remove", parents=[common],
                        help="SVW-1 locked one-element removal by id (retire an overlay "
                             "element; refused on managed arrays)")
    rm.add_argument("--file", required=True)
    rm.add_argument("--array", required=True)
    rm.add_argument("--id", required=True)
    rm.add_argument("--id-key", default="id", help="match key (default: id)")

    st = sub.add_parser("set", parents=[common],
                        help="SVW-1 locked nested-path set (e.g. --path "
                             ".shippability_regression.deferral)")
    st.add_argument("--file", required=True)
    st.add_argument("--path", required=True,
                    help="dotted path, e.g. .shippability_regression.deferral or .a.b[0].c "
                         "([N] must exist; missing intermediate object keys are created)")
    gst = st.add_mutually_exclusive_group(required=True)
    gst.add_argument("--json", default=None, help="the value as strict JSON")
    gst.add_argument("--value", default=None, help="the value (parsed as JSON, else a string)")

    up = sub.add_parser("update", parents=[common], help="SVW-1 locked record update")
    up.add_argument("--file", required=True)
    up.add_argument("--array", required=True)
    up.add_argument("--id", required=True)
    up.add_argument("--id-key", default="id", help="match key (default: id)")
    up.add_argument("--assumption", default=None,
                    help="descend into the record's assumptions[] to this id")
    up.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="set a field (value parsed as JSON, else string; repeatable)")
    up.add_argument("--append", action="append", nargs=2, metavar=("FIELD", "JSON"),
                    help="append a JSON element to a nested array field (repeatable)")

    rw = sub.add_parser("rewrite", parents=[common], help="CAS whole-file rewrite (exit 3 on conflict)")
    rw.add_argument("--file", required=True)
    rw.add_argument("--base-file", required=True, help="bytes the skill read (CAS precondition)")
    grw = rw.add_mutually_exclusive_group(required=True)
    grw.add_argument("--content-file", default=None)
    grw.add_argument("--stdin", action="store_true")

    mv = sub.add_parser("move", parents=[common], help="seam-routed MOVE under the vault root")
    mv.add_argument("--from", dest="src", required=True)
    mv.add_argument("--to", dest="dst", required=True)

    ls = sub.add_parser("list", parents=[common], help="list a vault dir's entries")
    ls.add_argument("--dir", required=True)
    ls.add_argument("--count", action="store_true", help="print the entry count")

    ct = sub.add_parser("count", parents=[common], help="count an array's elements")
    ct.add_argument("--file", required=True)
    ct.add_argument("--array", default=None, help="array to count (auto-detected when single)")

    ac = sub.add_parser("alloc", parents=[common],
                        help="mint the next id of --kind in-lock (bumps counters), print it")
    ac.add_argument("--file", required=True)
    ac.add_argument("--kind", required=True, choices=["adr", "cc", "cn", "gs", "r", "ps"],
                    help="managed id kind to mint OUT-OF-ARRAY via this CLI: 'adr' (ADR files are "
                         "raw-written one-per-id under decisions/), the calibration-overlay kinds "
                         "'cc'/'cn'/'gs' (CC-/CN-/GS- ids for critic-calibration-log.json — minted "
                         "here, then carried in the append payload), 'r' (R-N risk ids for "
                         "risk-register.json — pre-minted so the appender can cross-reference the new "
                         "risk; replaces the collision-prone model-minted 'next R-NN'), and 'ps' "
                         "(PS-NNN product-scope items — product_scope.py mints them in its OWN lock, "
                         "so this is the hand-authored pre-mint path; slice-068/ADR-067). sc/ship/slice "
                         "are minted in-lock "
                         "by their own append/claim path and must NEVER be alloc'd here (slice-019/CR2: "
                         "alloc --kind slice would burn a slice number out of band)")

    return p


_DISPATCH = {
    "read": _cmd_read, "get": _cmd_get, "query": _cmd_query, "append": _cmd_append,
    "remove": _cmd_remove, "set": _cmd_set, "update": _cmd_update, "rewrite": _cmd_rewrite,
    "move": _cmd_move, "list": _cmd_list, "count": _cmd_count, "alloc": _cmd_alloc,
}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
