"""Critique-review structural audit (DR-1) — v2 JSON.

Validates that a slice's `critique-review.json` (produced by the
critique-review meta-Critic agent via the `/critique-review` skill) is
structurally well-formed. v1 parsed the 4 H2 sections + header fields of the
markdown `critique-review.md`; v2 parses the JSON object.

Per DR-1 (methodology-changelog.md v0.17.0). The rule's purpose: ensure the
meta-Critic's output is structurally well-formed so the user's TRI-1 triage
step can reconcile it with the first Critic's findings without parsing
surprises.

Required structural fields (the v2 JSON analogues of the v1 4 sections +
header block):
  - `slice`             which slice this review covers
  - `reviewed_by`       the meta-Critic agent identity
  - `verdict`           one of {accept, adjust, extend}
  - `date`              when the review was produced
  - `assessments`       array of per-finding classifications (each finding from
                        critique.json gets a {finding, classification}) — the v2
                        analogue of v1's Confirmed/Suspicious/Severity sections
  - `missed`            array of findings the first Critic missed — the v2
                        analogue of v1's "Missed findings" section

Each assessment's `classification` must be in {valid, suspicious, severity-wrong}.

Cross-reference (when `critique.json` is present in the same slice folder):
each `assessments[].finding` should reference a finding id that exists in
`critique.json.findings[]` (a dangling assessment is a `dangling-assessment`
violation).

NFR-1 mtime carry-over was REMOVED (3.9 — it was dead for every post-install user).
`--no-carry-over` is still accepted as a no-op for CLI compatibility ONLY — no
carry-over machinery exists anywhere in this module anymore.

v2 shape (schema by example `skills/critique-review/examples/critique-review.json`):

    {
      "_schema": "aisdlc/critique-review@1",
      "slice": "slice-021",
      "reviewed_by": "critique-review agent",
      "verdict": "adjust",
      "date": "<ts>",
      "assessments": [{"finding": "C1", "classification": "valid"}],
      "missed": [{"dimension": "edge-case", "claim": "..."}]
    }

Usage:
    python critique_review_audit.py <slice-folder>
    python critique_review_audit.py <critique-review.json>
    python critique_review_audit.py --json <slice-folder>
    python critique_review_audit.py --no-carry-over <slice-folder>

Exit codes:
    0  clean
    1  violations
    2  usage error
"""
from __future__ import annotations

import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.lib import _stdout

# Required structural fields. `assessments`/`missed` are validated as arrays.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "slice", "reviewed_by", "verdict", "date", "assessments", "missed",
)

# Fields that must be a JSON array
_ARRAY_FIELDS: frozenset[str] = frozenset({"assessments", "missed"})

# Allowed dual-review verdict values (v2 lowercase)
_ALLOWED_VERDICTS: frozenset[str] = frozenset({"accept", "adjust", "extend"})

# Allowed per-finding classification values
_ALLOWED_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"valid", "suspicious", "severity-wrong"}
)


@dataclass(frozen=True)
class CRViolation:
    path: str
    kind: str       # "missing-field" | "wrong-type" | "invalid-verdict" |
                    # "invalid-classification" | "dangling-assessment" |
                    # "no-file" | "format"
    severity: str   # always "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    slice: str = ""
    verdict: str = ""
    assessment_count: int = 0
    missed_count: int = 0
    violations: list[CRViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slice": self.slice,
            "verdict": self.verdict,
            "assessment_count": self.assessment_count,
            "missed_count": self.missed_count,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "violation_count": len(self.violations),
                "consistent": len(self.violations) == 0,
            },
        }


def _load_critique_finding_ids(slice_folder: Path) -> set[str] | None:
    """Finding ids declared in the sibling critique.json, or None if absent/unreadable.

    Returns None (not an empty set) when the file is missing or malformed so the
    caller skips the cross-reference rather than flagging every assessment as
    dangling.
    """
    critique_path = slice_folder / "critique.json"
    if not critique_path.exists():
        return None
    try:
        data = json.loads(critique_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ids: set[str] = set()
    for entry in data.get("findings", []) or []:
        if isinstance(entry, dict):
            fid = str(entry.get("id", "")).strip()
            if fid:
                ids.add(fid)
    return ids


def audit_review_file(review_path: Path) -> AuditResult:
    """Audit a critique-review.json file against DR-1."""
    result = AuditResult()

    if not review_path.exists():
        result.violations.append(CRViolation(
            path=str(review_path), kind="no-file", severity="Important",
            message=f"critique-review.json not found: {review_path}",
        ))
        return result

    try:
        text = review_path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError) as exc:
        result.violations.append(CRViolation(
            path=str(review_path), kind="format", severity="Important",
            message=f"cannot read critique-review.json: {exc}",
        ))
        return result
    except json.JSONDecodeError as exc:
        result.violations.append(CRViolation(
            path=str(review_path), kind="format", severity="Important",
            message=f"critique-review.json is not valid JSON: {exc}",
        ))
        return result

    if not isinstance(data, dict):
        result.violations.append(CRViolation(
            path=str(review_path), kind="format", severity="Important",
            message="critique-review.json top level is not a JSON object.",
        ))
        return result

    # Required fields
    for fname in _REQUIRED_FIELDS:
        if fname in _ARRAY_FIELDS:
            if fname not in data:
                result.violations.append(CRViolation(
                    path=str(review_path), kind="missing-field", severity="Important",
                    message=(
                        f"required field `{fname}` is missing. Per DR-1, "
                        f"critique-review.json must include: "
                        f"{', '.join(_REQUIRED_FIELDS)}."
                    ),
                ))
            elif not isinstance(data[fname], list):
                result.violations.append(CRViolation(
                    path=str(review_path), kind="wrong-type", severity="Important",
                    message=f"field `{fname}` must be a JSON array.",
                ))
        else:
            if not str(data.get(fname, "")).strip():
                result.violations.append(CRViolation(
                    path=str(review_path), kind="missing-field", severity="Important",
                    message=(
                        f"required field `{fname}` is missing or empty. Per "
                        f"DR-1, critique-review.json must include: "
                        f"{', '.join(_REQUIRED_FIELDS)}."
                    ),
                ))

    result.slice = str(data.get("slice", "")).strip()
    verdict = str(data.get("verdict", "")).strip().lower()
    result.verdict = verdict

    if verdict and verdict not in _ALLOWED_VERDICTS:
        result.violations.append(CRViolation(
            path=str(review_path), kind="invalid-verdict", severity="Important",
            message=(
                f"Dual-review verdict '{verdict}' not in "
                f"{sorted(_ALLOWED_VERDICTS)}. accept (first Critic sound), "
                f"adjust (existing findings need modification), or extend "
                f"(missed findings surface)."
            ),
        ))

    # Per-finding assessments
    assessments = data.get("assessments")
    critique_ids = _load_critique_finding_ids(review_path.parent)
    if isinstance(assessments, list):
        result.assessment_count = len(assessments)
        for idx, row in enumerate(assessments):
            if not isinstance(row, dict):
                result.violations.append(CRViolation(
                    path=str(review_path), kind="format", severity="Important",
                    message=f"assessments[{idx}] is not a JSON object.",
                ))
                continue
            classification = str(row.get("classification", "")).strip().lower()
            if classification and classification not in _ALLOWED_CLASSIFICATIONS:
                result.violations.append(CRViolation(
                    path=str(review_path), kind="invalid-classification",
                    severity="Important",
                    message=(
                        f"assessments[{idx}] classification '{classification}' "
                        f"not in {sorted(_ALLOWED_CLASSIFICATIONS)}."
                    ),
                ))
            finding = str(row.get("finding", "")).strip()
            if critique_ids is not None and finding and finding not in critique_ids:
                result.violations.append(CRViolation(
                    path=str(review_path), kind="dangling-assessment",
                    severity="Important",
                    message=(
                        f"assessments[{idx}] references finding '{finding}' "
                        f"which is not declared in critique.json findings[] "
                        f"{sorted(critique_ids)}."
                    ),
                ))

    missed = data.get("missed")
    if isinstance(missed, list):
        result.missed_count = len(missed)

    return result


def _format_human(result: AuditResult) -> str:
    if not result.violations:
        return (
            f"Critique-review audit: clean. Verdict: {result.verdict or '?'} "
            f"({result.assessment_count} assessment(s), "
            f"{result.missed_count} missed).\n"
        )

    out: list[str] = [f"{len(result.violations)} critique-review violation(s):\n\n"]
    for v in result.violations:
        out.append(
            f"  [{v.severity}] {v.path} ({v.kind})\n"
            f"    {v.message}\n\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="critique_review_audit",
        description="DR-1 critique-review structural audit (v2 JSON)",
    )
    parser.add_argument(
        "target", type=Path,
        help="Slice folder (auto-finds critique-review.json inside) OR a critique-review.json file",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted as a NO-OP for CLI compatibility (carry-over was removed in 3.9)",
    )
    args = parser.parse_args(argv)

    target: Path = args.target
    review_path = target / "critique-review.json" if target.is_dir() else target

    result = audit_review_file(review_path)

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
