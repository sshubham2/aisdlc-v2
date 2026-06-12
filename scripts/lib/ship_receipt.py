"""ship_receipt.py — CI merge-gate receipt: emit + verify (roadmap §2.1).

THE gap between "discipline you maintain" and "discipline the system maintains":
the vault is external, so CI cannot see that a slice was reality-validated. The
receipt is a slim, committed evidence record — written into the CODE repo at
``.aisdlc/receipts/<slice-NNN>.json`` by ``/commit-slice`` (OPT-IN: only when the
repo carries the merge-gate workflow), so a PR from ``slice/NNN-*`` carries its
own proof and the workflow can refuse to merge without it.

The receipt is a RECORD, not an authority: it points back at the vault artifacts
(validation.json, gate-log rows); a receipt can be forged by a determined human,
but so can a green test — the gate's job is to make *skipping the pipeline* loud,
not to be tamper-proof.

Subcommands:
  emit    --slice <slice-NNN-name> [--vault V] [--repo-root R]
          Read the slice's validation.json (+ mission-brief + gate-log rows) from
          the vault (active OR archive location) and write the receipt.
  verify  (--branch slice/NNN-x | --slice slice-NNN) [--repo-root R]
          CI/local check: receipt exists, result == pass, any shippability
          regression carries an approved deferral. A non-slice --branch exits 0
          ("gate not applicable") so the workflow can run on every PR.

Exit: 0 ok / gate pass / not-applicable · 1 gate FAIL · 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout  # noqa: E402

_SLICE_RE = re.compile(r"^(slice-\d+)(?:-.+)?$")
_BRANCH_RE = re.compile(r"^slice/(\d+)(?:-.+)?$")
RECEIPTS_DIR = Path(".aisdlc") / "receipts"


def _canon(slice_id: str) -> str | None:
    m = _SLICE_RE.match(slice_id.strip())
    return m.group(1) if m else None


def _vault_root(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    from scripts.lib._vault_paths import VAULT_ROOT  # lazy (PEP 562): no git probe unless needed
    return VAULT_ROOT


def _find_slice_dir(vault: Path, slice_arg: str) -> Path | None:
    """Locate the slice folder in slices/ or slices/archive/ (exact name or slice-NNN prefix)."""
    for base in (vault / "slices", vault / "slices" / "archive"):
        p = base / slice_arg
        if p.is_dir():
            return p
        canon = _canon(slice_arg)
        if canon and base.is_dir():
            hits = sorted(d for d in base.iterdir()
                          if d.is_dir() and (d.name == canon or d.name.startswith(canon + "-")))
            if hits:
                return hits[0]
    return None


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def cmd_emit(args: argparse.Namespace) -> int:
    vault = _vault_root(args.vault)
    sdir = _find_slice_dir(vault, args.slice)
    if sdir is None:
        sys.stderr.write(f"ship_receipt: slice {args.slice!r} not found under {vault}/slices[/archive]\n")
        return 2
    canon = _canon(sdir.name) or sdir.name

    validation = _load(sdir / "validation.json")
    if not validation:
        sys.stderr.write(f"ship_receipt: {sdir.name} has no readable validation.json — "
                         "run /validate-slice before emitting a receipt.\n")
        return 2
    brief = _load(sdir / "mission-brief.json")

    counts = {"pass": 0, "fail": 0, "partial": 0}
    for c in validation.get("criteria") or []:
        r = str((c or {}).get("result", "")).lower()
        if r in counts:
            counts[r] += 1
    ship = validation.get("shippability_regression") or {}
    deferral = ship.get("deferral") or {}

    gates: list[dict] = []
    gl = _load(vault / "gate-log.json")
    for row in gl.get("entries") or []:
        if isinstance(row, dict) and str(row.get("slice", "")) == canon and "verdict" in row:
            gates.append({k: row[k] for k in ("gate", "verdict", "reality_contact", "reality_proxy", "at")
                          if k in row})

    receipt = {
        "_schema": "aisdlc/ship-receipt@1",
        "slice": canon,
        "slice_folder": sdir.name,
        "candidate": brief.get("candidate"),
        "result": validation.get("result"),
        "criteria": counts,
        "shippability_regression": {
            "ran": bool(ship.get("ran")),
            "failed_rows": len(ship.get("failed_rows") or []),
            "deferral_approved": bool(deferral.get("approved")),
        },
        "gates": gates,
        "vault": vault.name,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    out = Path(args.repo_root) / RECEIPTS_DIR / f"{canon}.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"ship_receipt: cannot write {out}: {e}\n")
        return 2
    print(str(out))
    return 0


def verify_receipt(receipt: dict, canon: str) -> list[str]:
    """Pure check — mirrored INLINE in assets/aisdlc-merge-gate.yml (keep the two in sync)."""
    problems: list[str] = []
    if str(receipt.get("slice", "")) != canon:
        problems.append(f"receipt slice {receipt.get('slice')!r} does not match branch slice {canon!r}")
    if str(receipt.get("result", "")).lower() != "pass":
        problems.append(f"validation result is {receipt.get('result')!r}, not 'pass'")
    ship = receipt.get("shippability_regression") or {}
    if int(ship.get("failed_rows") or 0) > 0 and not ship.get("deferral_approved"):
        problems.append(f"{ship.get('failed_rows')} shippability regression row(s) without an approved deferral")
    return problems


def cmd_verify(args: argparse.Namespace) -> int:
    if args.branch:
        m = _BRANCH_RE.match(args.branch.strip())
        if not m:
            print(f"ship_receipt: branch {args.branch!r} is not a slice/* branch — gate not applicable.")
            return 0
        canon = f"slice-{m.group(1)}"
    elif args.slice:
        canon = _canon(args.slice) or ""
        if not canon:
            sys.stderr.write(f"ship_receipt: --slice {args.slice!r} is not slice-NNN[-name]\n")
            return 2
    else:
        sys.stderr.write("ship_receipt verify: pass --branch or --slice\n")
        return 2

    path = Path(args.repo_root) / RECEIPTS_DIR / f"{canon}.json"
    if not path.is_file():
        print(f"GATE FAIL  no ship receipt at {path} — run the pipeline through /validate-slice and "
              f"/commit-slice (the receipt is emitted when the merge-gate workflow is installed).")
        return 1
    receipt = _load(path)
    problems = verify_receipt(receipt, canon)
    if problems:
        for pr in problems:
            print(f"GATE FAIL  {pr}")
        return 1
    g = receipt.get("gates") or []
    print(f"GATE PASS  {canon}: validation result=pass, "
          f"criteria={receipt.get('criteria')}, {len(g)} gate row(s) attached.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(prog="ship_receipt", description="CI merge-gate receipt: emit + verify (§2.1).")
    sub = p.add_subparsers(dest="cmd", required=True)
    em = sub.add_parser("emit", help="write .aisdlc/receipts/<slice>.json from the vault evidence")
    em.add_argument("--slice", required=True, help="slice folder name or canonical slice-NNN")
    em.add_argument("--vault", default=None, help="vault root (default: resolved for this repo)")
    em.add_argument("--repo-root", default=".", help="code repo root (default: cwd)")
    vf = sub.add_parser("verify", help="check the receipt for a slice branch (CI / local)")
    vf.add_argument("--branch", default=None, help="branch name (slice/NNN-…; non-slice → not applicable)")
    vf.add_argument("--slice", default=None, help="canonical slice id instead of --branch")
    vf.add_argument("--repo-root", default=".", help="code repo root (default: cwd)")
    args = p.parse_args(argv)
    return {"emit": cmd_emit, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
