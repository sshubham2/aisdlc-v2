"""drift_status.py — fold the append-only drift-log into CURRENT accepted-drift state.

`/drift-check --status` (slice-002). The drift-log is an append-only audit; this is the
deterministic read-only fold answering "what is knowingly divergent RIGHT NOW?".

Fold contract (ratified at TRI-1, slice-002):
- Group by ``finding.strip()`` (case-sensitive — case differences in claims are meaningful;
  internal whitespace/punctuation differences do NOT join).
- Sort each group by PARSED timezone-aware datetime (trailing ``Z`` normalized; offsetless
  assumed UTC) — never raw string compare (lexicographic != chronological off the zulu subset).
- Classify by the latest SIGNAL entry with ASYMMETRIC supersession:
    * acceptance (``action: accept-drift``) covers recurrence — a bare re-detection NEWER than
      the acceptance annotates ("re-detected, N since acceptance") but NEVER revokes it; the
      original rationale + accepted-at are preserved;
    * resolution (non-empty ``resolution``) claims the drift GONE — a detection NEWER than it
      falsifies that and RE-OPENS the finding;
    * one entry carrying BOTH accept-drift and resolution classifies RESOLVED (terminal meaning
      beats interim acceptance) and is flagged ``ambiguous``;
    * no signal at all -> OPEN.
- Entries that cannot participate honestly go to ``unfoldable[]`` VERBATIM, never dropped:
  finding absent / strip()=='' OR ``at`` missing/unparseable (they cannot take part in
  latest-wins ordering — this covers the canonical example's literal ``<ts>`` placeholder).

Read-only. Exit 0 on absent/empty log ("no drift recorded") and on a clean fold (even with
unfoldable entries — they are surfaced, not errors); exit 2 on usage errors / malformed JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402


def _root(vault_arg: str | None) -> Path:
    if vault_arg:
        return Path(vault_arg)
    from scripts.lib._vault_paths import VAULT_ROOT  # lazy (PEP 562)
    return VAULT_ROOT


def _parse_at(value) -> datetime | None:
    """Aware-UTC datetime, or None if missing/unparseable (entry -> unfoldable)."""
    if not isinstance(value, str) or not value.strip():
        return None
    t = value.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # offsetless -> assume UTC (aware by construction)
    return dt


def _kind(entry: dict) -> str:
    """resolution > acceptance > detection. A both-fields entry counts as resolution (M2)."""
    if str(entry.get("resolution") or "").strip():
        return "resolution"
    if str(entry.get("action") or "").strip() == "accept-drift":
        return "acceptance"
    return "detection"


def fold(entries: list) -> dict:
    unfoldable: list = []
    # Sort key is (parsed_at, ARRAY INDEX): build_entry stamps second-granularity
    # timestamps, so same-second writes are realistic — the log is append-only, so
    # array order IS chronological order within a tie (code-review M1: a strict
    # at-only compare silently reported a same-second re-detection as RESOLVED).
    groups: dict[str, list[tuple[datetime, int, dict]]] = {}
    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            unfoldable.append(e)
            continue
        finding = str(e.get("finding") or "").strip()
        at = _parse_at(e.get("at"))
        if not finding or at is None:
            unfoldable.append(e)
            continue
        groups.setdefault(finding, []).append((at, idx, e))

    out = {"accepted_drift": [], "open": [], "resolved": [], "unfoldable": unfoldable}
    now = datetime.now(timezone.utc)
    for finding, rows in sorted(groups.items()):
        rows.sort(key=lambda r: (r[0], r[1]))
        signals = [(at, idx, e) for at, idx, e in rows if _kind(e) != "detection"]
        detections = [(at, idx, e) for at, idx, e in rows if _kind(e) == "detection"]
        first_seen, last_at = rows[0][0], rows[-1][0]

        if not signals:
            out["open"].append({
                "finding": finding,
                "first_seen": first_seen.isoformat(),
                "last_seen": last_at.isoformat(),
                "categories": sorted({str(e.get("category") or "?") for _, _, e in rows}),
            })
            continue

        sig_at, sig_idx, sig = signals[-1]
        later_detections = [at for at, idx, _ in detections if (at, idx) > (sig_at, sig_idx)]

        if _kind(sig) == "acceptance":
            row = {
                "finding": finding,
                "accepted_at": sig_at.isoformat(),
                "age_days": max(0, (now - sig_at).days),
                "rationale": str(sig.get("rationale") or "").strip(),
                "redetections": len(later_detections),
            }
            if later_detections:  # annotate, NEVER revoke (asymmetric supersession)
                row["last_redetected_at"] = max(later_detections).isoformat()
            out["accepted_drift"].append(row)
        else:  # resolution
            if later_detections:  # resolution falsified -> re-opened
                out["open"].append({
                    "finding": finding,
                    "first_seen": first_seen.isoformat(),
                    "last_seen": max(later_detections).isoformat(),
                    "previously_resolved_at": sig_at.isoformat(),
                    "categories": sorted({str(e.get("category") or "?") for _, _, e in rows}),
                })
            else:
                row = {"finding": finding, "resolved_at": sig_at.isoformat(),
                       "resolution": str(sig.get("resolution") or "").strip()}
                if str(sig.get("action") or "").strip() == "accept-drift":
                    row["ambiguous"] = True  # both-fields entry (M2): RESOLVED wins, flagged
                out["resolved"].append(row)
    return out


def _disp(finding: str) -> str:
    """Display form for human mode: collapse newlines/whitespace runs so one finding
    stays one line (code-review m2). JSON mode keeps the verbatim text."""
    return " ".join(str(finding).split())


def _human(out: dict) -> str:
    lines = []
    acc, op, res, unf = (out["accepted_drift"], out["open"], out["resolved"], out["unfoldable"])
    lines.append(f"drift status: {len(acc)} accepted-drift · {len(op)} open · "
                 f"{len(res)} resolved · {len(unf)} unfoldable")
    if acc:
        lines.append("\nACCEPTED-DRIFT (knowingly divergent right now):")
        for r in acc:
            note = (f"  [re-detected x{r['redetections']}, last {r['last_redetected_at'][:10]}]"
                    if r["redetections"] else "")
            lines.append(f"  - {_disp(r['finding'])}\n      accepted {r['accepted_at'][:10]} "
                         f"({r['age_days']}d ago) — rationale: {r['rationale'] or '(none)'}{note}")
    if op:
        lines.append("\nOPEN (unresolved, unaccepted):")
        for r in op:
            re_note = (f"  [previously resolved {r['previously_resolved_at'][:10]} — RE-OPENED]"
                       if r.get("previously_resolved_at") else "")
            lines.append(f"  - {_disp(r['finding'])}  (last seen {r['last_seen'][:10]}){re_note}")
    if unf:
        lines.append("\nUNFOLDABLE (no finding identity or unparseable timestamp — verbatim):")
        for e in unf:
            lines.append("  - " + json.dumps(e, ensure_ascii=False))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="drift_status",
        description="Fold drift-log.json into CURRENT accepted-drift / open / resolved state. Read-only.")
    ap.add_argument("--vault", default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    log = _root(args.vault) / "drift-log.json"
    if not log.is_file():
        print("no drift recorded (drift-log.json absent).")
        return 0
    try:
        data = json.loads(log.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        sys.stderr.write(f"drift_status: cannot read {log}: {e}\n")
        return 2
    if not isinstance(data, dict):
        # code-review m1: a top-level array/string parses fine but is a malformed log —
        # the documented exit-2 path, not an AttributeError.
        sys.stderr.write(f"drift_status: {log} root is {type(data).__name__}, expected an object "
                         f"with an entries[] array.\n")
        return 2
    entries = data.get("entries") or []
    if not entries:
        print("no drift recorded (empty log).")
        return 0

    out = fold(entries)
    print(json.dumps(out, indent=2, ensure_ascii=False) if args.as_json else _human(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

