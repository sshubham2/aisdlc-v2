"""adr_append_only_audit.py - slice-035 / SC-019 / ADR-023.

Enforce 'ADRs are append-only' by content-hash, migrating a convention-only CLAUDE.md
rule into a deterministic pipeline gate. The external vault is NOT git-tracked, so
immutability is baselined by a SHA-256 over each ADR's NFC-normalized IMMUTABLE field
subset, kept in a sidecar <decisions>/.adr-baseline.json. `status` and `superseded_by`
are EXCLUDED from the hash, so the one legitimate post-seal change -- a forward
supersession (superseded_by null->ADR-NNN, optional status accepted->superseded) -- is
hash-invariant by construction (no special-case rule; matches the live ADR-005/017 where
superseded_by is set while status stays 'accepted').

Modes:
  VERIFY (default, read-only): recompute each ADR's hash, compare to the baseline.
  --seal <ADR-id>            : baseline exactly one id (SCOPED -- the continuous mint-time
                               path; never blanket, so minting one ADR cannot launder an
                               unrelated unsealed/edited ADR -- critique M-add-1). Idempotent on
                               an UNCHANGED id; REFUSES (exit 2) to overwrite a CHANGED already-
                               sealed id (code-review M1 -- an edit+reseal-same-id would launder).
  --backfill                 : baseline every currently-UNBASELINED id (the SOLE blanket
                               entry, run once at gate-adoption; never re-seals an already-
                               baselined id, so it cannot launder a tamper).

Exit codes (precedence 2 > 1 > 3 > 0):
  0  clean (all sealed ADRs match) OR NO-OP PASS (no decisions/ dir) OR a successful seal/backfill
  1  tamper: a SEALED ADR's immutable field changed in place (names the ADR id + field)
  2  usage/degrade: decisions/ unreadable, an ADR is non-JSON / missing immutable fields,
     an ADR's filename != its 'id', or the baseline manifest is corrupt -- never a silent pass
  3  unsealed: an ADR is present on disk with no baseline entry ('run --backfill') -- a
     DISTINCT code from tamper, so a gate that aggregates exit codes can tell 'needs one-time
     backfill' from 'someone edited an ADR' (critique B1)

Out of scope (code-review m1): the threat model is in-place EDITS, not deletion -- a sealed ADR
REMOVED from disk is NOT flagged (verify iterates on-disk ADRs, not baseline keys). A conscious
exclusion, not a silent gap; a future slice can fold in a missing-from-disk signal if warranted.

Mirrors the release_advance_audit.py idiom. VERIFY is read-only and NEVER seals (else an
edit+reseal would launder a tamper). Reads/writes utf-8.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import pathlib
import sys
import unicodedata
from pathlib import Path

# --- shared-leaf import bootstrap (scripts/lib/X.py -> <plugin>) ---
_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

BASELINE_NAME = ".adr-baseline.json"

# The IMMUTABLE field subset -- the single source of truth, bound to the ADR schema-by-example
# (skills/design-slice/examples/adr.json keys minus the meta {_schema,_note} and the mutable
# overlay {status, superseded_by}). A tripwire test (test_immut_set_matches_schema) fails if a
# new immutable schema field is added without updating this list -- critique m2.
IMMUTABLE_FIELDS = (
    "id", "title", "reversibility", "supersedes",
    "slice", "date", "context", "decision", "consequences",
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nfc(v):
    """NFC-normalize string values before hashing (critique M4: a composed-vs-decomposed
    re-serialization of the same glyph must hash identically). Non-str values pass through."""
    return unicodedata.normalize("NFC", v) if isinstance(v, str) else v


def _field_hash(value) -> str:
    blob = json.dumps(_nfc(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_hash(adr: dict) -> str:
    """SHA-256 over the canonicalized, NFC-normalized IMMUTABLE field subset."""
    subset = {k: _nfc(adr.get(k)) for k in IMMUTABLE_FIELDS}
    blob = json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _field_hashes(adr: dict) -> dict:
    """Per-field hashes, stored alongside the overall hash so VERIFY can NAME the divergent
    field on a tamper (the overall hash alone cannot localize the change) -- critique AC1."""
    return {k: _field_hash(adr.get(k)) for k in IMMUTABLE_FIELDS}


class _Degrade(Exception):
    """A fail-visible degrade condition (exit 2) -- never a silent pass."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _load_adrs(decisions: Path) -> list[tuple[str, dict]]:
    """[(adr_id, adr_dict)] for every ADR-*.json (the .adr-baseline.json dotfile does NOT
    match this glob). Raises _Degrade on malformed JSON / missing immutable fields / id<->filename
    mismatch (critique m3)."""
    out: list[tuple[str, dict]] = []
    for p in sorted(glob.glob(str(decisions / "ADR-*.json"))):
        path = Path(p)
        try:
            adr = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise _Degrade(f"ADR file {path.name} is not readable JSON: {exc}")
        if not isinstance(adr, dict):
            raise _Degrade(f"ADR file {path.name} is not a JSON object")
        missing = [k for k in IMMUTABLE_FIELDS if k not in adr]
        if missing:
            raise _Degrade(f"ADR file {path.name} is missing immutable field(s): {', '.join(missing)}")
        if adr.get("id") != path.stem:
            raise _Degrade(f"ADR file {path.name} carries id '{adr.get('id')}' "
                           f"(filename<->id mismatch)")
        out.append((path.stem, adr))
    return out


def _load_baseline(baseline_path: Path) -> dict:
    if not baseline_path.exists():
        return {"_schema": "aisdlc/adr-baseline@1", "adrs": {}}
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise _Degrade(f"baseline manifest {baseline_path.name} is corrupt: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("adrs"), dict):
        raise _Degrade(f"baseline manifest {baseline_path.name} is malformed (no 'adrs' object)")
    return data


def _write_baseline(baseline_path: Path, data: dict) -> None:
    # BC-PROJ-3: artifact writers use ensure_ascii=False + utf-8 (the cp1252 file-write leg).
    # The baseline carries only ASCII (sha256 hex, ADR ids, ISO timestamps), so this is
    # byte-identical here -- but it keeps the writer compliant if a future field ever holds
    # non-ASCII. The HASH canonicalization (canonical_hash) deliberately keeps ensure_ascii=True:
    # that is the deterministic hash-input scheme (paired with NFC-normalize), NOT an artifact write.
    baseline_path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def verify(decisions: Path) -> dict:
    result: dict = {"mode": "verify", "decisions": str(decisions),
                    "tampered": [], "unsealed": [], "clean": False, "exit_code": 0}
    try:
        adrs = _load_adrs(decisions)
        baseline = _load_baseline(decisions / BASELINE_NAME)
    except _Degrade as d:
        result.update(exit_code=2, degrade=d.message)
        return result
    sealed = baseline.get("adrs", {})
    for adr_id, adr in adrs:
        if adr_id not in sealed:
            result["unsealed"].append(adr_id)
            continue
        entry = sealed[adr_id]
        if canonical_hash(adr) != entry.get("hash"):
            cur = _field_hashes(adr)
            old = entry.get("fields", {})
            diverged = [f for f in IMMUTABLE_FIELDS if cur.get(f) != old.get(f)] or ["(immutable field)"]
            result["tampered"].append({"id": adr_id, "fields": diverged})
    if result["tampered"]:
        result["exit_code"] = 1
    elif result["unsealed"]:
        result["exit_code"] = 3
    else:
        result["clean"] = True
        result["exit_code"] = 0
    return result


def _seal_ids(decisions: Path, ids_to_seal: list[str], *, only_unbaselined: bool) -> dict:
    mode = "backfill" if only_unbaselined else "seal"
    result: dict = {"mode": mode, "decisions": str(decisions), "sealed": [], "exit_code": 0}
    try:
        adrs = dict(_load_adrs(decisions))
        baseline = _load_baseline(decisions / BASELINE_NAME)
    except _Degrade as d:
        result.update(exit_code=2, degrade=d.message)
        return result
    sealed = baseline.setdefault("adrs", {})
    for adr_id in ids_to_seal:
        if adr_id not in adrs:
            result.update(exit_code=2,
                          degrade=f"cannot seal {adr_id}: no such ADR file in {decisions}")
            return result
        adr = adrs[adr_id]
        existing = sealed.get(adr_id)
        if existing is not None:
            if only_unbaselined:
                continue  # --backfill never re-seals an already-baselined id
            # Scoped --seal: idempotent when the content is UNCHANGED; REFUSE when it CHANGED.
            # (code-review M1) Overwriting a differing sealed entry would launder an edit+reseal
            # -- mission-brief must_not_defer #4 ("an edit+reseal must not be able to launder a
            # tamper"). The only legitimate change to a committed ADR is hash-invariant (status/
            # superseded_by); a changed hash means the immutable body was edited -> supersede instead.
            if existing.get("hash") == canonical_hash(adr):
                continue  # already sealed, immutable content unchanged -> idempotent no-op
            result.update(exit_code=2, degrade=(
                f"refusing to re-seal {adr_id}: it is already baselined and its immutable content "
                f"has CHANGED -- an edit+reseal would launder a tamper. Supersede with a NEW ADR "
                f"(append-only); never edit a committed ADR and re-seal it."))
            return result
        sealed[adr_id] = {"hash": canonical_hash(adr),
                          "fields": _field_hashes(adr),
                          "baselined_at": _now()}
        result["sealed"].append(adr_id)
    _write_baseline(decisions / BASELINE_NAME, baseline)
    return result


def _decisions_dir(args: argparse.Namespace) -> Path | None:
    if args.decisions:
        return Path(args.decisions)
    if args.vault:
        return Path(args.vault) / "decisions"
    return None


def _print_human(r: dict) -> None:
    mode = r.get("mode")
    if r.get("exit_code") == 2:
        print(f"[adr-degrade] {r.get('degrade', 'usage error')}", file=sys.stderr)
        return
    if mode in ("seal", "backfill"):
        n = len(r.get("sealed", []))
        print(f"adr_append_only_audit: {mode} ok -- {n} ADR(s) baselined"
              + (f" ({', '.join(r['sealed'])})" if r["sealed"] else " (nothing to seal)"))
        return
    if mode == "noop":
        print(f"adr_append_only_audit: {r['message']}")
        return
    # verify
    if r.get("clean"):
        print("adr_append_only_audit: clean -- all ADRs match the append-only baseline.")
        return
    for t in r.get("tampered", []):
        print(f"[adr-tamper] {t['id']}: immutable field(s) {', '.join(t['fields'])} changed in "
              f"place -- append-only: supersede with a NEW ADR, never edit.", file=sys.stderr)
    if r.get("unsealed"):
        print(f"[adr-unsealed] {', '.join(r['unsealed'])} present with no baseline entry -- "
              f"run adr_append_only_audit --backfill to baseline existing ADRs.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="adr_append_only_audit",
        description="Enforce ADR append-only via a content-hash baseline sidecar (SC-019 / ADR-023).")
    ap.add_argument("--vault", default=None, help="vault root (decisions resolved as <vault>/decisions)")
    ap.add_argument("--decisions", default=None, help="explicit decisions/ dir (overrides --vault)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--seal", metavar="ADR-ID", default=None,
                   help="baseline exactly one ADR id (SCOPED -- the mint-time path)")
    g.add_argument("--backfill", action="store_true",
                   help="baseline every currently-unbaselined ADR (the SOLE blanket entry)")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = ap.parse_args(argv)

    decisions = _decisions_dir(args)
    if decisions is None:
        print("adr_append_only_audit: usage error -- pass --vault <vault> or --decisions <dir>",
              file=sys.stderr)
        return 2
    if not decisions.is_dir():
        r = {"mode": "noop", "clean": True, "exit_code": 0,
             "message": f"no decisions/ dir at {decisions} -> NO-OP PASS"}
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            _print_human(r)
        return 0

    if args.seal:
        r = _seal_ids(decisions, [args.seal], only_unbaselined=False)
    elif args.backfill:
        try:
            ids = [i for i, _ in _load_adrs(decisions)]
        except _Degrade as d:
            r = {"mode": "backfill", "exit_code": 2, "degrade": d.message}
        else:
            r = _seal_ids(decisions, ids, only_unbaselined=True)
    else:
        r = verify(decisions)

    if args.json:
        print(json.dumps(r, indent=2))
    else:
        _print_human(r)
    return r["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
