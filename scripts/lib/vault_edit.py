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

from scripts.lib import _stdout, id_allocator
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
}

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


def _cmd_append(args: argparse.Namespace) -> int:
    try:
        target = _resolve_in_vault(_root(args), args.file)
        element = _element_source(args)
    except ValueError as exc:
        _err(str(exc)); return 2
    except OSError as exc:
        _err(f"cannot read content: {exc}"); return 2

    def mutate(text: str) -> str:
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
        # slice-019 / AC2: a managed-kind array (candidates -> SC, rows -> SHIP) mints its id
        # IN-LOCK and REJECTS any caller-supplied id (the no-explicit-PK guard). The seed floor is
        # computed once from live ∪ archive; the persisted counter is authoritative thereafter.
        kind = _managed_kind_for(_root(args), target, key)
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
                    "update/rewrite/move/list/count under VAULT_ROOT via the "
                    "_vault_write lock (R-32).",
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
    ac.add_argument("--kind", required=True, choices=["adr"],
                    help="managed id kind to mint OUT-OF-ARRAY via this CLI — only 'adr' is wired "
                         "(ADR files are raw-written one-per-id under decisions/). sc/ship/slice are "
                         "minted in-lock by their own append/claim path and must NEVER be alloc'd here "
                         "(slice-019/CR2: alloc --kind slice would burn a slice number out of band)")

    return p


_DISPATCH = {
    "read": _cmd_read, "get": _cmd_get, "query": _cmd_query, "append": _cmd_append,
    "update": _cmd_update, "rewrite": _cmd_rewrite, "move": _cmd_move,
    "list": _cmd_list, "count": _cmd_count, "alloc": _cmd_alloc,
}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
