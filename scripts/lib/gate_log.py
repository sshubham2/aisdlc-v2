"""gate_log.py — serialize one gate-outcome row for `vault_edit append` (v2, NEW).

SHARED helper for the AI SDLC measurement spine (roadmap Theme 8 / plan Phase 0).
Every verification GATE — `risk-spike`, `critique`, `critique-review`, `code-review`,
`validate-slice`, `drift-check` — appends ONE row per slice to `<vault>/gate-log.json`
so per-gate outcomes are measurable instead of vibes. This emitter builds the row
(real timestamp, canonical `slice-NNN`, gate->reality_contact mapping owned HERE so
the six call-sites never drift) and prints it for the SVW-1 append channel:

    $PY ".../scripts/lib/gate_log.py" --gate critique --slice slice-007 \
        --verdict needs-fixes --findings-count 4 --findings-real 3 --findings-noise 1 \
        --mode standard --tier medium \
      | $PY ".../scripts/lib/vault_edit.py" append \
            --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin

`reality_contact` (plan Phase 1.1) is derived from the gate, NOT hand-passed: a gate
that can say "no" because *reality* said no (a spike on the real environment, a real
device/data validation) scores `high`; a code-graph check (`drift-check`) scores
`medium`; a model grading the model (`critique`/`critique-review`/`code-review`) scores
`low`. Pass `--reality-contact` only to override a one-off (kept for forward-compat;
the default is correct for every current gate). The single source of truth is
``GATE_CONTACT`` below — Phase 1 reads the same field this stamps.

Verdict row shape (default `--kind verdict`; optional fields OMITTED, never written
as null — matches the vault's "omit empty" convention so absence reads cleanly):
    {at, slice, gate, verdict, findings_count, reality_contact
     [, findings_real][, findings_noise][, mode][, tier][, cross_domain]}

`--kind miss` (plan Phase 0.2 RECALL half / roadmap Theme 8) emits a recall row: a
real issue this gate SHOULD have caught but MISSED, surfaced later. No verdict /
findings_count (a miss is not a raised finding); it carries severity + where the
escaped issue was finally caught. Per-gate RECALL = catches / (catches + misses),
catches = Σ findings_real on the gate's verdict rows. Readers (`/pulse`,
`/critic-calibrate`) MUST filter `kind == "miss"` OUT of the precision/raised math.
Miss row shape:
    {at, slice, gate, kind:"miss", reality_contact, severity, caught_by
     [, ref][, mode][, tier]}
Emitted by `/reflect` (Step 3, per MISSED critique finding; caught_by build/validate),
or for a post-ship escape attributed to the introducing slice:

    $PY ".../scripts/lib/gate_log.py" --kind miss --gate critique --slice slice-007 \
        --severity major --caught-by validate --ref "AC4 race not flagged" \
      | $PY ".../scripts/lib/vault_edit.py" append \
            --vault "$AI_SDLC_VAULT_ROOT" --file gate-log.json --array entries --stdin

`findings_real` + `findings_noise` (plan Phase 0.2) make per-gate PRECISION
computable. They are known at append time for the gates that triage their own
findings (today: `critique`, from the TRI-1 dispositions — accepted-* = real,
overridden = noise); other gates omit them.

Two output modes (mirrors drift-check/build_entry.py):
  - default: print the row JSON to STDOUT -> pipe into `vault_edit append --stdin`.
  - `--out PATH`: write the row to PATH and print PATH (for `--content-file <path>`).

Exit 0 success · 2 usage error (unknown gate / bad reality-contact / negative count /
real+noise exceeding findings_count / non-int counts / verdict-kind missing verdict or
findings-count / miss-kind bad-or-missing severity or caught-by).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD; SKILL.md cannot use `python -m` or
# `${CLAUDE_PLUGIN_ROOT}`, so this is invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/gate_log.py" ...`, which puts scripts/lib
# (NOT the plugin root) on sys.path[0]. Add the plugin root so `from scripts.lib ...`
# resolves. No-op under `-m scripts.lib.gate_log` from the plugin root.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # <plugin>/scripts/lib/gate_log.py -> <plugin>
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout

# The reality-contact spine (plan Phase 1.1 / roadmap Theme 2). Trust a gate exactly
# as much as it touches something that is NOT the model: reality > code-graph > model.
GATE_CONTACT: dict[str, str] = {
    "risk-spike": "high",        # throwaway code on the real environment
    "validate-slice": "high",    # real device / real user / real data
    "drift-check": "medium",     # claims vs real code (code-graph / CRG)
    "code-review": "low",        # the model grading a diff
    "critique": "low",           # the model grading the model
    "critique-review": "low",    # the model grading the model (meta)
}
_CONTACTS = {"high", "medium", "low"}
_SLICE_RE = re.compile(r"^(slice-\d+)(?:-.+)?$")

# Recall rows (plan Phase 0.2 recall half / roadmap Theme 8). A `--kind miss` row
# records a real issue this gate SHOULD have caught but MISSED — surfaced later
# (same-slice build/validate, or post-ship). It carries no verdict / findings_count
# (a miss is not a raised finding); it carries the issue's severity + where it was
# finally caught. Per-gate RECALL = catches / (catches + misses), where catches =
# Σ findings_real on the gate's verdict rows. Readers MUST filter `kind == "miss"`
# OUT of the precision/raised math (it is recall data, not a gate run).
_SEVERITIES = {"blocker", "major", "minor", "critical", "high", "medium", "low"}
_CAUGHT_BY = {"build", "validate", "post-ship", "bug-hunt", "user", "repro", "drift"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon_slice(slice_arg: str) -> str:
    """slice-NNN-name -> slice-NNN; any other label passes through verbatim."""
    m = _SLICE_RE.match(slice_arg.strip())
    return m.group(1) if m else slice_arg.strip()


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gate_log",
        description="Serialize one gate-outcome row for vault_edit append (measurement spine).",
    )
    p.add_argument("--gate", required=True,
                   help="one of: " + ", ".join(sorted(GATE_CONTACT)))
    p.add_argument("--slice", required=True, dest="slice_id",
                   help="slice id (canonicalized to slice-NNN)")
    p.add_argument("--kind", default="verdict", choices=("verdict", "miss"),
                   help="verdict (default) = one gate-run row; miss = a recall row "
                        "for an issue this gate should have caught but didn't (Phase 0.2 recall)")
    p.add_argument("--verdict", default=None,
                   help="(verdict kind) the gate's verdict (e.g. go/no-go, clean/needs-fixes/"
                        "blocked, accept/adjust/extend, pass/partial/fail)")
    p.add_argument("--findings-count", type=int, default=None,
                   help="(verdict kind) number of findings this gate raised (>= 0)")
    p.add_argument("--severity", default=None,
                   help="(miss kind) severity of the missed issue: " + ", ".join(sorted(_SEVERITIES)))
    p.add_argument("--caught-by", default=None, dest="caught_by",
                   help="(miss kind) where the escaped issue was finally caught: "
                        + ", ".join(sorted(_CAUGHT_BY)))
    p.add_argument("--ref", default=None,
                   help="(miss kind, optional) pointer to the issue — finding id / bug id / "
                        "shippability row / introducing-slice note")
    p.add_argument("--findings-real", type=int, default=None,
                   help="findings ratified as real (Phase 0.2; e.g. critique accepted-*)")
    p.add_argument("--findings-noise", type=int, default=None,
                   help="findings ruled non-issues (Phase 0.2; e.g. critique overridden)")
    p.add_argument("--mode", default=None, help="minimal | standard | heavy (context)")
    p.add_argument("--tier", default=None, help="low | medium | high (slice risk tier, context)")
    p.add_argument("--reality-contact", default=None, dest="reality_contact",
                   help="override the gate->contact default (high|medium|low); normally omit")
    p.add_argument("--cross-domain", action="store_true", dest="cross_domain",
                   help="mark this row as a cross-domain-transfer outcome — set by risk-spike / "
                        "validate-slice when the slice's design.json carries a cross_domain_transfer "
                        "(Phase 2.3 validity ratio: did reality confirm the borrowed pattern?)")
    p.add_argument("--at", default=None, help="ISO-8601 timestamp (default: now, UTC)")
    p.add_argument("--out", default=None,
                   help="write the row to this file and print the path "
                        "(default: print the row JSON to stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    gate = args.gate.strip()
    if gate not in GATE_CONTACT:
        sys.stderr.write(
            f"gate_log: --gate must be one of {sorted(GATE_CONTACT)} (got {gate!r})\n")
        return 2

    contact = (args.reality_contact.strip() if args.reality_contact else GATE_CONTACT[gate])
    if contact not in _CONTACTS:
        sys.stderr.write(
            f"gate_log: --reality-contact must be one of {sorted(_CONTACTS)} (got {contact!r})\n")
        return 2

    if args.kind == "miss":
        # Recall row — no verdict / findings_count (a miss is not a raised finding).
        sev = (args.severity or "").strip().lower()
        if sev not in _SEVERITIES:
            sys.stderr.write(
                f"gate_log: --kind miss requires --severity in {sorted(_SEVERITIES)} (got {sev!r})\n")
            return 2
        caught = (args.caught_by or "").strip().lower()
        if caught not in _CAUGHT_BY:
            sys.stderr.write(
                f"gate_log: --kind miss requires --caught-by in {sorted(_CAUGHT_BY)} (got {caught!r})\n")
            return 2
        row: dict = {
            "at": (args.at.strip() if args.at else _now_iso()),
            "slice": _canon_slice(args.slice_id),
            "gate": gate,
            "kind": "miss",
            "reality_contact": contact,
            "severity": sev,
            "caught_by": caught,
        }
        if args.ref and args.ref.strip():
            row["ref"] = args.ref.strip()
        for k in ("mode", "tier"):
            v = getattr(args, k)
            if v is not None and str(v).strip():
                row[k] = v.strip()
        # findings_count / findings_real/noise / verdict / cross_domain do not apply to a miss
    else:
        # Verdict row (default) — one gate-run, unchanged behavior.
        if args.verdict is None or not args.verdict.strip():
            sys.stderr.write("gate_log: --kind verdict requires --verdict\n")
            return 2
        if args.findings_count is None:
            sys.stderr.write("gate_log: --kind verdict requires --findings-count\n")
            return 2
        if args.findings_count < 0:
            sys.stderr.write("gate_log: --findings-count must be >= 0\n")
            return 2
        for name, val in (("--findings-real", args.findings_real),
                          ("--findings-noise", args.findings_noise)):
            if val is not None and val < 0:
                sys.stderr.write(f"gate_log: {name} must be >= 0\n")
                return 2
        real = args.findings_real or 0
        noise = args.findings_noise or 0
        if (args.findings_real is not None or args.findings_noise is not None) \
                and real + noise > args.findings_count:
            sys.stderr.write(
                "gate_log: findings_real + findings_noise cannot exceed findings_count "
                f"({real} + {noise} > {args.findings_count})\n")
            return 2

        row = {
            "at": (args.at.strip() if args.at else _now_iso()),
            "slice": _canon_slice(args.slice_id),
            "gate": gate,
            "verdict": args.verdict.strip(),
            "findings_count": args.findings_count,
            "reality_contact": contact,
        }
        if args.findings_real is not None:
            row["findings_real"] = args.findings_real
        if args.findings_noise is not None:
            row["findings_noise"] = args.findings_noise
        for k in ("mode", "tier"):
            v = getattr(args, k)
            if v is not None and str(v).strip():
                row[k] = v.strip()
        if args.cross_domain:
            row["cross_domain"] = True

    payload = json.dumps(row, ensure_ascii=False)
    if args.out:
        try:
            Path(args.out).write_text(payload + "\n", encoding="utf-8", newline="")
        except OSError as exc:
            sys.stderr.write(f"gate_log: cannot write --out {args.out}: {exc}\n")
            return 2
        print(args.out)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
