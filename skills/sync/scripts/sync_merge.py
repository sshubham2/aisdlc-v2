"""sync_merge.py — mechanical preserve-merge for /sync's regenerated vault files (v2, NEW).

Single-skill helper for `/sync` Step 2/5a. The regenerate-vs-preserve table in SKILL.md is
the CONTRACT; this script makes it MECHANICAL instead of behavioral: it replaces ONLY the
whitelisted code-derived keys of an existing vault artifact and REFUSES a derived file that
touches anything else — so a model slip can never destroy human-authored content (these are
create-semantics files with no CAS/append channel to catch a bad whole-file Write).

Whitelist (from the SKILL.md table, keyed on the artifact's top-level vault dir):
  components/  -> public_surface, depends_on
  contracts/   -> endpoints, event, payload_schema, delivery_guarantee
  schemas/     -> fields, constraints

Usage:
  sync_merge.py --file components/orders.json --derived-file D.json (--write | --out-file P) [--vault ROOT]

The merge NEVER deletes or rewrites non-whitelisted keys — they pass through from the
existing file byte-for-byte (modulo JSON re-serialization). A MISSING/empty base is a
refusal, not an implicit create: new artifacts have no human content to protect and are
authored fresh via Write; routing them here would hide that distinction.

Exit 0 merged (summary JSON on stdout) · 2 refusal/usage (non-whitelisted derived key,
missing/malformed base, unknown artifact dir, path escape).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from pathlib import Path, PurePosixPath

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_write_text
from scripts.lib.vault_edit import _resolve_in_vault

_WHITELIST: dict[str, frozenset[str]] = {
    "components": frozenset({"public_surface", "depends_on"}),
    "contracts": frozenset({"endpoints", "event", "payload_schema", "delivery_guarantee"}),
    "schemas": frozenset({"fields", "constraints"}),
}


def _err(msg: str) -> None:
    sys.stderr.write(f"sync_merge: {msg}\n")


def _load_dict(path: Path, label: str) -> dict | None:
    """Read a JSON object; None (with stderr) on missing/empty/malformed/non-dict."""
    if not path.is_file():
        _err(f"{label} not found: {path}")
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _err(f"cannot read {label} {path}: {exc}")
        return None
    if not raw.strip():
        _err(f"{label} is empty: {path}")
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _err(f"{label} is not valid JSON ({exc}): {path}")
        return None
    if not isinstance(data, dict):
        _err(f"{label} must be a JSON object, got {type(data).__name__}: {path}")
        return None
    return data


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_merge",
        description="Merge code-derived fields into an existing vault artifact, "
                    "preserving all human-authored fields (whitelist-enforced).",
    )
    p.add_argument("--file", required=True,
                   help="vault-relative artifact path (components/X.json | contracts/X.json | schemas/X.json)")
    p.add_argument("--derived-file", required=True,
                   help="JSON object holding ONLY the re-derived whitelisted fields")
    p.add_argument("--vault", default=None, help="vault root (default: resolved VAULT_ROOT)")
    out = p.add_mutually_exclusive_group(required=True)
    out.add_argument("--write", action="store_true", help="write the merged result to the vault file")
    out.add_argument("--out-file", default=None, help="write the merged result here (preview; vault untouched)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    root = Path(args.vault) if args.vault else VAULT_ROOT
    try:
        target = _resolve_in_vault(root, args.file)
    except ValueError as exc:
        _err(str(exc))
        return 2

    parts = PurePosixPath(args.file.replace("\\", "/")).parts
    top = parts[0] if parts else ""
    whitelist = _WHITELIST.get(top)
    if whitelist is None:
        _err(f"--file must live under one of {sorted(_WHITELIST)} (got {args.file!r}); "
             f"other vault files are not sync-regenerated")
        return 2

    base = _load_dict(target, "base artifact")
    if base is None:
        _err("refusing: sync_merge only protects EXISTING artifacts -- author a NEW artifact "
             "fresh via Write (nothing human-authored exists there to preserve)")
        return 2

    derived = _load_dict(Path(args.derived_file), "--derived-file")
    if derived is None:
        return 2
    if not derived:
        _err("--derived-file holds no fields -- nothing to merge")
        return 2

    offenders = sorted(set(derived) - whitelist)
    if offenders:
        _err(f"refusing: derived file touches non-whitelisted (human-authored) key(s) {offenders}; "
             f"the {top}/ whitelist is {sorted(whitelist)} -- the regenerate-vs-preserve table is "
             f"a hard contract, not a suggestion")
        return 2

    merged = dict(base)
    merged.update(derived)
    changed = sorted(k for k in derived if base.get(k) != derived[k])
    payload = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"

    if args.write:
        try:
            safe_write_text(target, payload)
        except OSError as exc:
            _err(f"cannot write {target}: {exc}")
            return 2
        written = str(target)
    else:
        try:
            Path(args.out_file).write_text(payload, encoding="utf-8", newline="")
        except OSError as exc:
            _err(f"cannot write --out-file {args.out_file}: {exc}")
            return 2
        written = args.out_file

    print(json.dumps({
        "file": args.file,
        "replaced": sorted(derived),
        "changed": changed,
        "preserved": sorted(set(base) - set(derived)),
        "written": written,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
