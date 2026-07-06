"""Build-checks audit (BC-1) — v2 JSON.

Loads `<vault>/build-checks.json` (the v2 JSON artifact; v1 parsed the H2-
structured markdown `architecture/build-checks.md`) and surfaces the rules
applicable to the current slice. The keyword-precision audit (BC-1) decides
applicability per rule from its `applies_when` object plus the slice's
mission-brief.json + design.json text.

v2 rule shape (the `rules[]` array of `<vault>/build-checks.json`; schema by
example `skills/reflect/examples/build-checks.json`):

    {
      "id": "BC-PROJ-3",
      "severity": "critical" | "important",
      "applies_when": {
        "always": true,                       # always applies, OR
        "glob": "**/*.py" | ["a/**", "b/*.ts"],  # match vs --changed-files, OR
        "keywords": ["upload", "sse"],        # word-boundary match vs slice text
        "anchors": ["upload"],                # optional subset of keywords
        "negative_anchors": ["mock"]          # optional final-filter suppressors
      },
      "rule": "<what the builder must verify>",
      "rationale": "<why this is permanent>"  # optional
    }

Applicability (OR over the three POSITIVE signals, then a NEGATIVE final filter):
  1. `applies_when.always: true`                -> always applies
  2. any glob in `applies_when.glob` matches any --changed-files entry
  3. keyword path: a keyword word-boundary-matches the slice text; when
     `anchors` is set, at least one anchor must also match (slice-005, ADR-004)
  FINAL FILTER (slice-008, ADR-007): a rule that would otherwise fire is
  SUPPRESSED when ≥1 `negative_anchor` word-boundary-matches the slice text.

Validation (parse violations, Important severity):
  - required fields per rule: `id`, `severity`, `rule`
  - `severity` in {critical, important}
  - each anchor must be in `keywords` (`anchor-not-in-keywords`)
  - no `negative_anchor` may overlap `keywords`/`anchors`
    (`negative-anchor-overlaps-positive`)

Two graphify features are gone in v2; this audit never used them. The global
`~/.claude/build-checks.md` source v1 also read is DROPPED in v2 (it was a
forward-sync to an installed copy; the plugin/vault is the single source of
truth). This audit is project-vault-only.

BCSG-1 strict gate (ADR-072): under `--strict`, each applicable Critical rule
whose id is NOT in `--ack-critical` becomes an `unacknowledged-critical`
violation, so the exit code (`1 if violations else 0`) gates the slice.

MODEL-TIER self-attestation (3.1c): BCSG-1 checks that the Builder *acknowledged*
each applicable Critical rule (echoed its id into `--ack-critical`), NOT that
reality verified it — "was-it-MARKED, not was-it-RUN". So its green is a
`low` reality-contact (model-on-model) green, NOT a reality green. The hard STOP
is KEPT (a forcing function that makes the Builder enumerate + confront each
Critical project rule), but `/build-slice` now gate-logs a `build-checks` row at
`low` contact so the measurement spine (`gate_log.GATE_CONTACT["build-checks"]`,
`/pulse`, `/critic-calibrate`) MEASURES it instead of trusting an unmeasured gate.
A future demote-to-advisory remains a deliberate USER call informed by that data —
it is NOT auto-skippable (a project-author Critical rule is closer to
compliance-mandatory than the discretionary critique spawn).

NFR-1 mtime carry-over was REMOVED (3.9 — it was dead for every post-install user).
`--no-carry-over` is still accepted as a no-op for CLI compatibility ONLY — no
carry-over machinery exists anywhere in this module anymore.

Usage:
    python build_checks_audit.py --slice <slice-folder> [options]
    python build_checks_audit.py --slice <slice-folder> --changed-files <files...>
    python build_checks_audit.py --slice <slice-folder> --json
    python build_checks_audit.py --slice <slice-folder> --no-carry-over
    python build_checks_audit.py --slice <slice-folder> --project-checks <build-checks.json>
    python build_checks_audit.py --slice <slice-folder> --strict --ack-critical BC-PROJ-3

Exit codes:
    0  success — applicable rules surfaced (or none apply)
    1  format violations in build-checks.json (or unacknowledged-critical under --strict)
    2  usage error / unrecoverable failure
"""
from __future__ import annotations

import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT


# Required fields per rule
_REQUIRED_FIELDS: frozenset[str] = frozenset({"id", "severity", "rule"})

# Allowed severity values (case-insensitive comparison)
_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"critical", "important"})


@dataclass(frozen=True)
class BuildCheckRule:
    """A parsed rule from build-checks.json."""
    source: str          # "project"
    rule_id: str         # e.g., "BC-PROJ-1"
    title: str           # the `rule` text (the builder's check statement)
    severity: str        # "Critical" | "Important"
    applies_to: tuple[str, ...]  # globs OR ("always",) sentinel
    trigger_keywords: tuple[str, ...]  # lowercased keywords
    trigger_anchors: tuple[str, ...]  # lowercased anchor subset (slice-005)
    negative_anchors: tuple[str, ...]  # lowercased tokens (slice-008)
    check: str           # what the builder must verify (== rule text)
    rationale: str       # why this is permanent (may be empty)
    index: int           # 0-based index in rules[]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["applies_to"] = list(self.applies_to)
        d["trigger_keywords"] = list(self.trigger_keywords)
        d["trigger_anchors"] = list(self.trigger_anchors)
        d["negative_anchors"] = list(self.negative_anchors)
        return d


@dataclass(frozen=True)
class BuildCheckViolation:
    """A finding emitted by the audit.

    Usually a parse error (malformed build-checks.json). Under --strict (BCSG-1 /
    ADR-072) the audit ALSO emits an `unacknowledged-critical` finding per
    applicable Critical rule absent from --ack-critical — an applicability-derived
    finding, not a parse error.
    """
    path: str
    index: int    # 0-based rules[] index; -1 for file-level errors
    rule_id: str  # may be empty for file-level errors
    kind: str     # parse: "missing-field" | "invalid-severity" | "format"
                  # | "anchor-not-in-keywords" | "negative-anchor-overlaps-positive";
                  # strict gate (BCSG-1): "unacknowledged-critical"
    severity: str  # "Important" for parse errors; "Critical" for "unacknowledged-critical"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    """Audit output: rules applicable to this slice + any parse violations."""
    applicable: list[BuildCheckRule] = field(default_factory=list)
    skipped: list[BuildCheckRule] = field(default_factory=list)
    violations: list[BuildCheckViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "applicable": [r.to_dict() for r in self.applicable],
            "skipped": [r.to_dict() for r in self.skipped],
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "applicable_count": len(self.applicable),
                "skipped_count": len(self.skipped),
                "violation_count": len(self.violations),
                "critical_applicable": sum(
                    1 for r in self.applicable if r.severity.lower() == "critical"
                ),
            },
        }


def _matches_glob(path: str, pattern: str) -> bool:
    """Glob match with `**` support (multi-segment) and `*` (single-segment).

    Patterns:
      - `src/api/**`        matches `src/api/foo.py`, `src/api/v1/foo.py`
      - `src/api/*.py`      matches `src/api/foo.py` (single segment only)
      - `**/*upload*.py`    matches anywhere
      - `src/services/*upload*.py` matches `src/services/file_upload.py`
    """
    norm_path = path.replace("\\", "/")
    norm_pattern = pattern.replace("\\", "/")
    if norm_path == norm_pattern:
        return True
    # BB-08: tokenize left-to-right so a `**/` segment becomes `(?:.*/)?` (zero-or-more
    # leading dirs — so `**/*.py` ALSO matches a repo-root file like `setup.py`). The old
    # split('**') turned `**/*.py` into `.*` + a literal `/`, which REQUIRED a slash and
    # so silently skipped root files. `**` (not before `/`) → `.*`; `*` → single segment.
    regex = ""
    i, n = 0, len(norm_pattern)
    while i < n:
        if norm_pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif norm_pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif norm_pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        else:
            regex += re.escape(norm_pattern[i])
            i += 1
    return re.fullmatch(regex, norm_path) is not None


def _as_str_list(value: Any) -> list[str]:
    """Coerce a JSON field that may be a string or list of strings into a clean
    list of non-empty stripped strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def _dedup_lower(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        low = v.lower()
        if low and low not in seen:
            seen.add(low)
            out.append(low)
    return tuple(out)


def _parse_rules(
    data: Any,
    source: str,
    path: str,
) -> tuple[list[BuildCheckRule], list[BuildCheckViolation]]:
    """Parse rules + violations from a loaded build-checks.json document."""
    rules: list[BuildCheckRule] = []
    violations: list[BuildCheckViolation] = []

    raw = data.get("rules") if isinstance(data, dict) else None
    if raw is None:
        return rules, violations  # no rules key -> empty (silent)
    if not isinstance(raw, list):
        violations.append(BuildCheckViolation(
            path=path, index=-1, rule_id="", kind="format", severity="Important",
            message="`rules` is not a JSON array.",
        ))
        return rules, violations

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            violations.append(BuildCheckViolation(
                path=path, index=idx, rule_id="", kind="format", severity="Important",
                message=f"rules[{idx}] is not a JSON object.",
            ))
            continue

        rule_id = str(entry.get("id", "")).strip()

        # Required-field check
        missing = sorted(
            f for f in _REQUIRED_FIELDS if not str(entry.get(f, "")).strip()
        )
        if missing:
            violations.append(BuildCheckViolation(
                path=path, index=idx, rule_id=rule_id, kind="missing-field",
                severity="Important",
                message=(
                    f"rules[{idx}] (id={rule_id or '?'}): missing required "
                    f"field(s): {', '.join(missing)}. Required: "
                    f"{', '.join(sorted(_REQUIRED_FIELDS))}."
                ),
            ))
            continue

        severity_raw = str(entry.get("severity", ""))
        if severity_raw.lower() not in _ALLOWED_SEVERITIES:
            violations.append(BuildCheckViolation(
                path=path, index=idx, rule_id=rule_id, kind="invalid-severity",
                severity="Important",
                message=(
                    f"rule {rule_id}: severity '{severity_raw}' not allowed. "
                    f"Use one of: {', '.join(sorted(_ALLOWED_SEVERITIES))} "
                    f"(case-insensitive)."
                ),
            ))
            continue

        severity = severity_raw.title()

        applies_when = entry.get("applies_when")
        if applies_when is None:
            applies_when = {}
        if not isinstance(applies_when, dict):
            violations.append(BuildCheckViolation(
                path=path, index=idx, rule_id=rule_id, kind="format",
                severity="Important",
                message=f"rule {rule_id}: `applies_when` is not a JSON object.",
            ))
            continue

        if bool(applies_when.get("always")):
            applies_to: tuple[str, ...] = ("always",)
        else:
            applies_to = tuple(_as_str_list(applies_when.get("glob")))

        trigger_keywords = _dedup_lower(_as_str_list(applies_when.get("keywords")))
        trigger_anchors = _dedup_lower(_as_str_list(applies_when.get("anchors")))
        negative_anchors = _dedup_lower(_as_str_list(applies_when.get("negative_anchors")))

        # slice-005: anchors must be a subset of keywords.
        for anchor in trigger_anchors:
            if anchor not in trigger_keywords:
                violations.append(BuildCheckViolation(
                    path=path, index=idx, rule_id=rule_id,
                    kind="anchor-not-in-keywords", severity="Important",
                    message=(
                        f"rule {rule_id}: anchor '{anchor}' is not in keywords "
                        f"{sorted(trigger_keywords)}. Anchors must be a subset "
                        f"of the keyword vocabulary."
                    ),
                ))

        # slice-008: negative anchors must not overlap positive vocabulary.
        for negative_anchor in negative_anchors:
            if negative_anchor in trigger_keywords or negative_anchor in trigger_anchors:
                violations.append(BuildCheckViolation(
                    path=path, index=idx, rule_id=rule_id,
                    kind="negative-anchor-overlaps-positive", severity="Important",
                    message=(
                        f"rule {rule_id}: negative anchor '{negative_anchor}' "
                        f"overlaps positive keywords/anchors. Negative anchors "
                        f"must be exclusionary (no overlap with positive vocabulary)."
                    ),
                ))

        rule_text = str(entry.get("rule", "")).strip()
        rules.append(BuildCheckRule(
            source=source,
            rule_id=rule_id,
            title=rule_text,
            severity=severity,
            applies_to=applies_to,
            trigger_keywords=trigger_keywords,
            trigger_anchors=trigger_anchors,
            negative_anchors=negative_anchors,
            check=rule_text,
            rationale=str(entry.get("rationale", "")),
            index=idx,
        ))

    return rules, violations


def _negative_anchor_match(rule: BuildCheckRule, slice_text: str) -> bool:
    """True iff >=1 negative anchor word-boundary-matches slice text (slice-008).

    Negative anchors act as the FINAL FILTER on positive applicability decisions
    per ADR-007: a rule that would otherwise fire is suppressed when at least one
    negative anchor matches the slice's mission-brief + design text. Returns
    False (does not suppress) when the rule has no negative_anchors or slice_text
    is empty.
    """
    if not rule.negative_anchors or not slice_text:
        return False
    haystack = slice_text.lower()
    return any(
        re.search(rf"\b{re.escape(na)}\b", haystack)
        for na in rule.negative_anchors
    )


def _rule_applies(
    rule: BuildCheckRule,
    changed_files: list[str],
    slice_text: str,
) -> bool:
    """Decide whether a rule applies to the current slice.

    Applicability is OR over the three POSITIVE signals (always / glob /
    keyword+anchor), then every positive decision is gated through
    `not _negative_anchor_match()` (the slice-008 final filter; composes
    uniformly across all three positive paths).
    """
    if rule.applies_to == ("always",):
        return not _negative_anchor_match(rule, slice_text)

    for pattern in rule.applies_to:
        for changed in changed_files:
            if _matches_glob(changed, pattern):
                return not _negative_anchor_match(rule, slice_text)

    if rule.trigger_keywords and slice_text:
        haystack = slice_text.lower()
        matched = {
            kw for kw in rule.trigger_keywords
            if re.search(rf"\b{re.escape(kw)}\b", haystack)
        }
        if not matched:
            return False
        if rule.trigger_anchors and not any(a in matched for a in rule.trigger_anchors):
            return False
        return not _negative_anchor_match(rule, slice_text)

    return False


def _flatten_json_text(value: Any, parts: list[str]) -> None:
    """Recursively collect every string scalar from a JSON value into `parts`."""
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _flatten_json_text(v, parts)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _flatten_json_text(v, parts)


def _read_slice_text(slice_folder: Path) -> str:
    """Concatenate mission-brief.json + design.json string content for keyword
    matching. JSON artifacts are flattened to their string scalars (v1 read the
    raw markdown text)."""
    parts: list[str] = []
    for fname in ("mission-brief.json", "design.json"):
        path = slice_folder / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        _flatten_json_text(data, parts)
    return "\n".join(parts)


def audit_slice(
    slice_folder: Path,
    project_checks: Path | None = None,
    changed_files: list[str] | None = None,
    strict: bool = False,
    ack_critical: tuple[str, ...] = (),
) -> AuditResult:
    """Audit a slice against the project build-checks.json.

    Args:
        slice_folder: path to the slice folder (design.json etc. for keyword match).
        project_checks: path to project build-checks.json (defaults to
            <vault>/build-checks.json).
        changed_files: list of files this slice changed (for glob match); empty
            means glob match never fires (keyword-only).
        strict: if True (BCSG-1 / ADR-072), each applicable Critical rule whose
            id is NOT in ack_critical is appended as an `unacknowledged-critical`
            violation, so main()'s exit code becomes a gate-failure.
        ack_critical: rule IDs the builder has addressed + attests. Only consulted
            when strict=True. An ID matching no applicable Critical rule is ignored
            (lenient ack; surfaced as a diagnostic, never silently green).
    """
    result = AuditResult()
    changed_files = changed_files or []

    slice_text = _read_slice_text(slice_folder)

    if project_checks is None:
        project_checks = VAULT_ROOT / "build-checks.json"

    if project_checks.exists():
        try:
            text = project_checks.read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else {}
        except (OSError, UnicodeDecodeError) as exc:
            result.violations.append(BuildCheckViolation(
                path=str(project_checks), index=-1, rule_id="", kind="format",
                severity="Important", message=f"cannot read build-checks.json: {exc}",
            ))
            data = None
        except json.JSONDecodeError as exc:
            result.violations.append(BuildCheckViolation(
                path=str(project_checks), index=-1, rule_id="", kind="format",
                severity="Important",
                message=f"build-checks.json is not valid JSON: {exc}",
            ))
            data = None

        if data is not None:
            rules, violations = _parse_rules(
                data, source="project", path=str(project_checks),
            )
            result.violations.extend(violations)
            for r in rules:
                if _rule_applies(r, changed_files, slice_text):
                    result.applicable.append(r)
                else:
                    result.skipped.append(r)

    # BCSG-1 (ADR-072): under --strict, an applicable Critical rule that is NOT
    # acknowledged via --ack-critical becomes a violation (gate-failure exit 1).
    if strict:
        acked = set(ack_critical)
        for r in result.applicable:
            if r.severity.lower() == "critical" and r.rule_id not in acked:
                result.violations.append(BuildCheckViolation(
                    path=str(project_checks) if project_checks else "",
                    index=r.index,
                    rule_id=r.rule_id,
                    kind="unacknowledged-critical",
                    severity="Critical",
                    message=(
                        f"applicable Critical rule {r.rule_id} not acknowledged "
                        f"via --ack-critical; address it (document in build-log.json) "
                        f"and pass --ack-critical {r.rule_id}"
                    ),
                ))

    return result


def _format_human(
    result: AuditResult,
    strict: bool = False,
    ack_critical: tuple[str, ...] = (),
) -> str:
    out: list[str] = []

    parse_violations = [
        v for v in result.violations if v.kind != "unacknowledged-critical"
    ]
    if parse_violations:
        out.append(f"{len(parse_violations)} build-checks parse violation(s):\n\n")
        for v in parse_violations:
            loc = f"rules[{v.index}]" if v.index >= 0 else v.path
            out.append(
                f"  [{v.severity}] {v.path} {loc} ({v.kind})\n"
                f"    {v.message}\n\n"
            )

    if not result.applicable:
        out.append("No build-checks rules apply to this slice.\n")
        return "".join(out)

    critical_count = sum(
        1 for r in result.applicable if r.severity.lower() == "critical"
    )
    out.append(
        f"{len(result.applicable)} build-checks rule(s) apply to this slice "
        f"({critical_count} Critical):\n\n"
    )
    for r in result.applicable:
        out.append(
            f"  [{r.severity}] {r.rule_id} ({r.source})\n"
            f"    Check: {r.check}\n"
        )
        if r.rationale:
            out.append(f"    Rationale: {r.rationale}\n")
        out.append("\n")

    if critical_count > 0:
        out.append(
            "Per BC-1, Critical rules MUST be addressed before /build-slice "
            "declares the slice done. Important rules surface here for builder "
            "review; defer-with-rationale is allowed.\n"
        )

    if strict:
        applicable_crit_ids = {
            r.rule_id for r in result.applicable if r.severity.lower() == "critical"
        }
        acked = set(ack_critical)
        unacked = sorted(applicable_crit_ids - acked)
        acknowledged = sorted(applicable_crit_ids & acked)
        unmatched = sorted(acked - applicable_crit_ids)
        out.append("\n--strict (BCSG-1) acknowledgment gate:\n")
        if acknowledged:
            out.append(f"  acknowledged Critical rules: {', '.join(acknowledged)}\n")
        if unacked:
            out.append(
                "  UNACKNOWLEDGED applicable Critical rules (gate-failure): "
                f"{', '.join(unacked)}\n"
            )
        else:
            out.append("  all applicable Critical rules acknowledged (or none apply).\n")
        for stale in unmatched:
            out.append(
                f"  note: --ack-critical '{stale}' matched no applicable Critical "
                "rule (typo or stale ack?)\n"
            )

    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="build_checks_audit",
        description="BC-1 build-checks audit — surface applicable rules at /build-slice (v2 JSON)",
    )
    parser.add_argument(
        "--slice", type=Path, required=True,
        help="Path to the slice folder (containing mission-brief.json + design.json)",
    )
    parser.add_argument(
        "--changed-files", nargs="*", default=None,
        help="Files changed by this slice (for applies_when.glob matching)",
    )
    parser.add_argument(
        "--project-checks", type=Path, default=None,
        help="Path to project build-checks.json (default: <vault>/build-checks.json)",
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted as a NO-OP for CLI compatibility (carry-over was removed in 3.9)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output result as JSON (machine-readable)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help=(
            "BCSG-1 gate: treat each applicable Critical rule not in "
            "--ack-critical as a violation (gate-failure exit 1). Opt-in; "
            "default off = legacy surface-only behavior."
        ),
    )
    parser.add_argument(
        "--ack-critical", nargs="*", default=[], metavar="RULE-ID",
        help=(
            "Critical rule IDs the builder has addressed + attests (e.g. "
            "--ack-critical BC-PROJ-3). Only consulted with --strict. An ID "
            "matching no applicable Critical rule is ignored (surfaced as a "
            "diagnostic). Place LAST or immediately before another --flag."
        ),
    )
    args = parser.parse_args(argv)

    slice_folder: Path = args.slice
    if not slice_folder.exists():
        sys.stderr.write(f"slice folder not found: {slice_folder}\n")
        return 2

    result = audit_slice(
        slice_folder=slice_folder,
        project_checks=args.project_checks,
        changed_files=args.changed_files,
        strict=args.strict,
        ack_critical=tuple(args.ack_critical),
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(
            _format_human(result, strict=args.strict, ack_critical=tuple(args.ack_critical))
        )

    # Exit 1 on parse violations (malformed build-checks.json) or, under --strict,
    # unacknowledged applicable Critical rules. Applicable rules are otherwise
    # informational, not failures.
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
