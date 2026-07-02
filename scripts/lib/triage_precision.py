"""triage_precision.py — SSOT for TRI-1 disposition classification + gate-log precision/recall.

slice-052 / SC-088 / [[ADR-045]]. One home for the real/noise rule so the producer
(the critique-review gate-log row) and the consumers (/critic-calibrate 1e, /pulse) can
never drift. THREE responsibilities:

1. ``classify_dispositions(dispositions, select)`` — the SSOT rule. `real` =
   {accepted-fixed, accepted-pending, deferred, escalated}; `noise` = {overridden} — the
   SAME sets the documented first-Critic emission uses (/critique Step 4.5). `select`:
   ``"meta"`` keeps ONLY the meta-Critic ``^M-add-`` findings (ids ``.strip()``ped first, so
   a leading-space id is not misclassified); ``"first-critic"`` keeps the rest. Returns a
   ``Counts`` with ``well_formed=False`` when a selected disposition carries an action outside
   real|noise — the caller then emits **count-only** (never hard-raises to block the append).
   Genuinely malformed structure (non-list / non-dict element) RAISES.

   NOTE (ADR-045 / M4): the first-Critic gate-log emission is NOT routed through this — it
   stays inline/unchanged. This helper is used for the critique-review (meta) row ONLY; the
   "same classification as the first-Critic row" guarantee is proven by test, not by a shared
   call (a possible pre-existing first-Critic double-count of M-add-* is tracked as SC-089).

2. ``critique_review_row(slice_dir)`` — the emission DECISION (M-add-1 phantom-row guard).
   Returns ``None`` when the meta-Critic did NOT run this slice (no ``critique-review.json`` /
   a ``critique-review-skip`` marker in ``milestone.json``) so a DR-1-skipped slice emits ZERO
   rows. Otherwise returns the gate-log row fields ({verdict, findings_count[, findings_real,
   findings_noise]}) computed over the ``^M-add-`` dispositions in ``critique.json``.

3. ``gate_precision_recall(entries, gate)`` — the SHIPPED computation /critic-calibrate 1e +
   /pulse call (M3/AC4), so the measurement is deterministic and testable rather than
   model-followed prose. A verdict row lacking ``findings_real`` is UNKNOWN (excluded), NEVER
   counted as 0; ``kind:"miss"`` rows are recall data (excluded from precision, counted for
   recall). precision = Σreal/(Σreal+Σnoise); recall = Σreal/(Σreal+misses).

CLI (for the skill call-sites):
  --critique-review-args --slice-dir DIR
      prints gate_log.py flags (``--verdict V --findings-count N [--findings-real R
      --findings-noise K]``) when a row should be emitted, or NOTHING when it should not
      (the M-add-1 guard) — the skill emits only when the output is non-empty.
  --gate-precision --gate G --gate-log PATH
      prints the gate_precision_recall(...) JSON for a gate.

Exit 0 success · 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/triage_precision.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout

# The real/noise rule — the SINGLE source of truth, identical to the documented first-Critic
# emission (/critique Step 4.5): a real issue counts even when deferred/blocking; a user
# override is the only "noise" (false positive).
REAL_DISPOSITIONS = frozenset({"accepted-fixed", "accepted-pending", "deferred", "escalated"})
NOISE_DISPOSITIONS = frozenset({"overridden"})
# The meta-Critic (DR-1) finding-id prefix. The hyphen is load-bearing: first-Critic MAJORS
# are `M1`/`M2`/... and MUST NOT match — only `M-add-N` does (verified against the live corpus).
META_PREFIX = "M-add-"


@dataclass(frozen=True)
class Counts:
    count: int
    real: int
    noise: int
    well_formed: bool  # False -> a selected action was outside real|noise; caller emits count-only


def _finding_id(d: dict) -> str:
    return str(d.get("finding", "")).strip()


def classify_dispositions(dispositions, select: str = "meta") -> Counts:
    if not isinstance(dispositions, list):
        raise TypeError("dispositions must be a list")
    if select not in ("meta", "first-critic"):
        raise ValueError("select must be 'meta' or 'first-critic'")
    want_meta = select == "meta"
    selected = []
    for d in dispositions:
        if not isinstance(d, dict):
            raise TypeError(f"each disposition must be a dict, got {type(d).__name__}")
        is_meta = _finding_id(d).startswith(META_PREFIX)
        if is_meta == want_meta:
            selected.append(d)
    real = noise = 0
    well_formed = True
    for d in selected:
        action = str(d.get("action", "")).strip()
        if action in REAL_DISPOSITIONS:
            real += 1
        elif action in NOISE_DISPOSITIONS:
            noise += 1
        else:
            well_formed = False  # unknown/blank action -> degrade to count-only (never block the append)
    return Counts(count=len(selected), real=real, noise=noise, well_formed=well_formed)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def critique_review_row(slice_dir) -> dict | None:
    """The emission decision + fields for the critique-review gate-log row.

    None => the meta-Critic did NOT run this slice (M-add-1 guard) => emit ZERO rows.
    """
    d = Path(slice_dir)
    cr_path = d / "critique-review.json"
    if not cr_path.is_file():
        return None  # DR-1 did not run -> no phantom row

    ms_path = d / "milestone.json"
    if ms_path.is_file():
        try:
            ms = _read_json(ms_path)
            if isinstance(ms, dict) and str(ms.get("critique-review-skip", "")).strip():
                return None  # deliberate skip recorded -> no row
        except (ValueError, OSError):
            pass  # a malformed milestone must not fabricate a skip; fall through to emit

    try:
        cr = _read_json(cr_path)
    except (ValueError, OSError) as exc:
        raise ValueError(f"critique-review.json unreadable at {cr_path}: {exc}")
    verdict = str(cr.get("verdict", "")).strip()

    dispositions: list = []
    cj_path = d / "critique.json"
    if cj_path.is_file():
        try:
            triage = (_read_json(cj_path).get("triage") or {})
            dispositions = triage.get("dispositions") or []
        except (ValueError, OSError):
            dispositions = []

    counts = classify_dispositions(dispositions, select="meta")
    row: dict = {"verdict": verdict, "findings_count": counts.count}
    if counts.well_formed:
        row["findings_real"] = counts.real
        row["findings_noise"] = counts.noise
    # else: count-only (m2) — a stray action never blocks the settlement row
    return row


def gate_precision_recall(entries, gate: str) -> dict:
    """Per-gate precision + recall over gate-log rows. The SHIPPED computation the
    consumers call (M3/AC4). Absent findings_real => UNKNOWN (excluded), never 0."""
    if not isinstance(entries, list):
        raise TypeError("entries must be a list")
    verdict_rows = [e for e in entries
                    if isinstance(e, dict) and e.get("gate") == gate and e.get("kind") != "miss"]
    miss_rows = [e for e in entries
                 if isinstance(e, dict) and e.get("gate") == gate and e.get("kind") == "miss"]
    rows_with_real = [e for e in verdict_rows if "findings_real" in e]
    sum_real = sum(int(e["findings_real"]) for e in rows_with_real)
    # CR3: count noise ONLY from measured rows (those carrying findings_real) — a row with
    # findings_noise but no findings_real cannot occur with current producers (they co-emit),
    # and counting it would deflate precision against an UNKNOWN real numerator.
    sum_noise = sum(int(e.get("findings_noise", 0)) for e in rows_with_real)
    runs = len(verdict_rows)
    raised = sum(1 for e in verdict_rows if int(e.get("findings_count", 0)) > 0)
    denom_p = sum_real + sum_noise
    precision = (sum_real / denom_p) if denom_p > 0 else None  # None => UNKNOWN, never 0
    misses = len(miss_rows)
    denom_r = sum_real + misses
    recall = (sum_real / denom_r) if (rows_with_real and denom_r > 0) else None
    return {
        "gate": gate,
        "runs": runs,
        "raised": raised,
        "catches": sum_real,
        "noise": sum_noise,
        "precision": precision,
        "misses": misses,
        "recall": recall,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="triage_precision",
        description="SSOT disposition classifier + gate-log precision/recall (slice-052).")
    p.add_argument("--critique-review-args", action="store_true",
                   help="print gate_log.py flags for the critique-review row, or NOTHING when "
                        "the meta-Critic did not run (M-add-1 guard)")
    p.add_argument("--gate-precision", action="store_true",
                   help="print gate_precision_recall(...) JSON for --gate over --gate-log")
    p.add_argument("--slice-dir", help="(--critique-review-args) the slice folder")
    p.add_argument("--gate", help="(--gate-precision) gate name")
    p.add_argument("--gate-log", help="(--gate-precision) path to gate-log.json")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    if args.critique_review_args:
        if not args.slice_dir:
            sys.stderr.write("triage_precision: --critique-review-args requires --slice-dir\n")
            return 2
        row = critique_review_row(args.slice_dir)
        if row is None:
            return 0  # emit nothing -> the skill skips the append (M-add-1 guard)
        parts = ["--verdict", row["verdict"], "--findings-count", str(row["findings_count"])]
        if "findings_real" in row:
            parts += ["--findings-real", str(row["findings_real"]),
                      "--findings-noise", str(row["findings_noise"])]
        print(" ".join(parts))
        return 0

    if args.gate_precision:
        if not (args.gate and args.gate_log):
            sys.stderr.write("triage_precision: --gate-precision requires --gate and --gate-log\n")
            return 2
        try:
            data = _read_json(Path(args.gate_log))
        except (ValueError, OSError) as exc:
            sys.stderr.write(f"triage_precision: cannot read --gate-log {args.gate_log}: {exc}\n")
            return 2
        entries = data.get("entries", []) if isinstance(data, dict) else []
        print(json.dumps(gate_precision_recall(entries, args.gate), ensure_ascii=False))
        return 0

    sys.stderr.write("triage_precision: pass --critique-review-args or --gate-precision\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
