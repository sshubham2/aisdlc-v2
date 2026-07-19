#!/usr/bin/env python3
"""orphaned_candidates.py — read-only out-of-scope (orphaned) candidate surface for /pulse.

slice-078 / SC-163 / [[ADR-089]]. The FIRST pulse-owned script.

product_scope.py already computes an `orphaned` set — candidates (live UNION archive) whose
product-scope provenance `ref` points at a PS id no longer in the persisted scope — and
`materialize` surfaces it. No consumer rendered it, so a capability deliberately cut from the product
scope kept surfacing at /slice's pick gate as ordinary pickable work. This thin adapter exposes that
set to /pulse, READ-ONLY.

Mark-WITHOUT-sweep (the GC leak-detection transfer, ADR-089): the backlog is append-only (no un-mint),
so the orphan is made VISIBLE and NEVER retracted, deleted, or status-changed. This REPORTS the leak.

Contract — a stable tri-state stdout envelope, EXIT 0 ALWAYS:

    {"scope_present": bool, "orphaned": [{"candidate": id, "ref": ps_id}], "error"?: str}

Design decisions the /critique + /critique-review fixes pin:
  * B1 — key on the CLI EXIT CODE, never on its `status` string: product_scope sets status='ok' only
    when `minted` is non-empty, and --dry-run always passes minted=[], so status is NEVER 'ok' here.
    exit 0 -> read out['orphaned'] regardless of status; exit 4 -> no scope; other -> error.
  * M1 — FILTER the CLI's orphaned set (computed over live u archive) to candidates present in the
    LIVE candidates.json. A shipped/archived orphan is a real but DIFFERENT signal (dead shipped code
    for a cut capability), not pick-gate work, and would grow unbounded.
  * m1 — capture the subprocess as UTF-8 (product_scope reconfigures its stdout to UTF-8 + prints
    ensure_ascii=False); the Windows cp1252 default would mojibake / raise on a stray byte.
  * m2 — invoke with sys.executable + a list argv (never shell=True) + an explicit --vault so the
    subprocess targets the SAME vault /pulse scans.
  * M3 — EXIT 0 ALWAYS: the fail-visible signal rides stdout (an `error` field), so the injection's
    2>/dev/null cannot swallow it. A LAUNCH failure of the adapter ITSELF (no stdout) is caught one
    layer up by the SKILL.md injection's `|| echo '{...error...}'` fallback — unambiguous precisely
    because this adapter is exit-0-always.

Writes nothing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT

PRODUCT_SCOPE = _REPO / "scripts" / "lib" / "product_scope.py"

# product_scope's absent-scope refusal (`_scope(required=True)` -> _Refuse(4, "no-scope")).
_NO_SCOPE_EXIT = 4

# A ceiling on the read: product_scope's dry-run is a fast local read, so a stall means something is
# wedged. Bound it so /pulse's render can never hang on the orphan surface (code-review m-1).
_SUBPROCESS_TIMEOUT_S = 20


def _err(msg: str) -> dict:
    """A fail-visible envelope: scope is assumed present so the WARN reaches the render, never a
    silent no-line (must_not_defer #2). Exit code stays 0 — the signal rides stdout."""
    return {"scope_present": True, "orphaned": [], "error": msg}


def _live_ids(vault: Path) -> set[str]:
    """The candidate ids present in the LIVE candidates.json (the M1 filter set).

    Missing/empty/malformed candidates.json -> empty set -> no orphan line. must_not_defer #1: a
    missing live backlog must degrade, never crash. (A malformed file also fails the product_scope
    subprocess below, which routes to a fail-visible error before we even reach the filter.)"""
    p = vault / "candidates.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list):
        return set()
    return {str(c["id"]) for c in cands if isinstance(c, dict) and c.get("id")}


def derive(vault: Path) -> dict:
    """Compute the tri-state envelope by subprocessing the read-only product_scope dry-run CLI."""
    try:
        proc = subprocess.run(
            [sys.executable, str(PRODUCT_SCOPE), "--vault", str(vault),
             "materialize", "--dry-run", "--json"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # LAUNCH failure OR a wedged/timed-out product_scope -> fail-visible on our stdout, never a
        # stalled /pulse render (subprocess.TimeoutExpired is a SubprocessError subclass, so a ceiling
        # breach routes to the same error envelope, keeping the read exit-0-always).
        return _err(f"orphan adapter could not run product_scope: {exc}")

    if proc.returncode == _NO_SCOPE_EXIT:
        # No roots -> no mark. product-scope.json is absent; /pulse emits no orphan line.
        return {"scope_present": False, "orphaned": []}

    if proc.returncode != 0:
        return _err(f"product_scope materialize exited {proc.returncode}: "
                    f"{(proc.stderr or '').strip()[:200]}")

    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return _err("product_scope materialize produced unparseable stdout")

    raw = out.get("orphaned") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        return _err("product_scope materialize output carried no orphaned array")

    live = _live_ids(vault)
    orphaned = [{"candidate": e.get("candidate"), "ref": e.get("ref")}
                for e in raw
                if isinstance(e, dict) and e.get("candidate") in live]
    return {"scope_present": True, "orphaned": orphaned}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None,
                    help="vault root to scan (defaults to the resolved VAULT_ROOT)")
    args = ap.parse_args(argv)
    vault = Path(args.vault) if args.vault else VAULT_ROOT
    if not vault:
        print(json.dumps(_err("could not resolve the vault root"), ensure_ascii=False))
        return 0
    print(json.dumps(derive(Path(vault)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
