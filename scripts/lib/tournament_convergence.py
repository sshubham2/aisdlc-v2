"""tournament_convergence.py - slice-066 / SC-119 / ADR-064 (supersedes ADR-063).

The shared "did the three blind designers fully AGREE?" predicate over a slice's
design.json ``tournament.approach_divergence``. It is the SINGLE source of truth
for FULL convergence, consumed by the CRP-1 pre-build gate
(critique_review_prerequisite_audit.py) AND by /critique Step 3.5 (via the --json
CLI), so both homes share ONE computation (no model-eyeball twin -- critique M1).

FULL convergence == ``tournament.approach_divergence`` is present, a non-empty list,
EVERY present pair is well-formed (a mapping whose ``divergence`` is one of the closed
set {overlapping, identical, disjoint}), and NO pair is ``disjoint``.

Tri-state (fail-visible; AC1 + must-not-defer, BC-PROJ-10 / slice-065 class):
  - ``convergent``      : well-formed AND no pair disjoint  -> is_full_convergence True
  - ``not_convergent``  : well-formed AND >=1 pair disjoint  -> is_full_convergence False
  - ``indeterminate``   : design not a dict / no tournament / approach_divergence
                          missing / not-a-list / empty / any pair non-mapping or
                          out-of-enum -> is_full_convergence False. NEVER read as
                          convergent, and the reason names WHY (fail-visible).

Pair-completeness (critique M2 decision): we do NOT hard-require exactly three pairs.
A two-designer tournament is legitimate (the step-0 spike found short-count slices),
so convergence needs every PRESENT pair well-formed; a single malformed element makes
the whole reading ``indeterminate`` rather than silently dropping it.

``classify(dict)`` is pure (no IO) so AC4 regressions run without disk;
``from_slice_folder(path)`` is the thin IO wrapper that never raises (an absent /
unreadable / non-JSON design.json -> ``indeterminate``). The gate that consumes this
never crashes on a missing design.json (critique must-not-defer #3).

Reason strings are ASCII-only and the CLI reconfigures stdout to UTF-8 (critique m3 /
Windows cp1252 rule).
"""
from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass, field

# --- shared-lib import bootstrap (a scripts/lib module: parents[2] == repo root) ---
_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# The closed set of divergence values design-slice's synthesis may write per pair.
_DIVERGENCE_ENUM = {"overlapping", "identical", "disjoint"}

_STATE_CONVERGENT = "convergent"
_STATE_NOT_CONVERGENT = "not_convergent"
_STATE_INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class Convergence:
    """A tri-state convergence verdict over one design's approach_divergence."""

    state: str                              # convergent | not_convergent | indeterminate
    is_full_convergence: bool               # True iff state == convergent
    reason: str                             # ASCII, names WHY (fail-visible)
    disjoint_pairs: list = field(default_factory=list)  # pairs classified disjoint

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "is_full_convergence": self.is_full_convergence,
            "reason": self.reason,
            "disjoint_pairs": list(self.disjoint_pairs),
        }


def _indeterminate(reason: str) -> Convergence:
    return Convergence(_STATE_INDETERMINATE, False, reason, [])


def classify(design: object) -> Convergence:
    """Classify FULL convergence from a design.json dict. Pure; never raises."""
    if not isinstance(design, dict):
        return _indeterminate("design is not a JSON object")
    tournament = design.get("tournament")
    if not isinstance(tournament, dict):
        return _indeterminate("no 'tournament' object in design.json")
    ad = tournament.get("approach_divergence")
    if not isinstance(ad, list):
        return _indeterminate("tournament.approach_divergence is missing or not a list")
    if len(ad) == 0:
        return _indeterminate("tournament.approach_divergence is an empty list")

    disjoint_pairs: list = []
    for i, pair in enumerate(ad):
        if not isinstance(pair, dict):
            return _indeterminate(f"approach_divergence[{i}] is not a mapping (bad measurement)")
        div = pair.get("divergence")
        if not isinstance(div, str) or div not in _DIVERGENCE_ENUM:
            return _indeterminate(
                f"approach_divergence[{i}] has out-of-enum divergence {div!r} "
                f"(expected one of overlapping/identical/disjoint) -- bad measurement"
            )
        if div == "disjoint":
            disjoint_pairs.append(pair.get("pair"))

    if disjoint_pairs:
        return Convergence(
            _STATE_NOT_CONVERGENT, False,
            f"{len(disjoint_pairs)} designer pair(s) classified disjoint",
            disjoint_pairs,
        )
    return Convergence(
        _STATE_CONVERGENT, True,
        f"all {len(ad)} designer pair(s) overlapping/identical -- no disjoint pair",
        [],
    )


def from_slice_folder(slice_folder: object) -> Convergence:
    """Read ``<slice_folder>/design.json`` and classify. Never raises: an absent /
    unreadable / non-JSON design.json -> ``indeterminate`` (fail-visible)."""
    try:
        path = pathlib.Path(slice_folder) / "design.json"
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError):
        return _indeterminate("design.json is absent or unreadable")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _indeterminate("design.json is not valid JSON")
    return classify(data)


def main(argv: list[str] | None = None) -> int:
    from scripts.lib import _stdout  # lazy: keep library import lightweight

    import argparse

    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="tournament_convergence",
        description="Report whether a slice's design tournament fully converged (no disjoint pair).",
    )
    parser.add_argument("--slice", dest="slice_folder", required=True,
                        help="Path to the slice folder containing design.json.")
    parser.add_argument("--json", action="store_true", help="Emit the verdict as JSON.")
    args = parser.parse_args(argv)  # missing --slice -> argparse exits 2 (usage)

    conv = from_slice_folder(args.slice_folder)
    if args.json:
        print(json.dumps(conv.to_dict()))
    else:
        print(f"convergence: {conv.state} "
              f"(is_full_convergence={conv.is_full_convergence}) -- {conv.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
