"""Pipeline-chain auto-advance audit (PCA-1) — v2.

Verifies the per-slice pipeline loop is correctly wired for auto-advance:
every covered skill carries a well-formed ``## Pipeline position`` block, the
declared successor edges match the canonical chain, and the terminal boundary
(``reflect`` -> ``/commit-slice``, and ``commit-slice`` itself) is marked
auto-advance ``false`` so ``/commit-slice`` is never auto-invoked.

Per PCA-1 (methodology-changelog.md v0.41.0).

**v2 canonical chain — INSERTS ``/risk-spike`` in-loop.** v1 went
``slice -> /design-slice`` directly; v2 inserts the in-loop blocking spike gate
between them: ``slice -> /risk-spike -> /design-slice``. ``(successor,
auto-advance)``::

    slice           -> /risk-spike      (auto-advance: true)
    risk-spike      -> /design-slice    (auto-advance: true)
    design-slice    -> /critique        (auto-advance: true)
    critique        -> /critique-review (auto-advance: true)   [1]
    critique-review -> /critique        (auto-advance: true)   [2]
    build-slice     -> /code-review     (auto-advance: true)
    code-review     -> /validate-slice  (auto-advance: true)
    validate-slice  -> /reflect         (auto-advance: true)   [3]
    reflect         -> /commit-slice    (auto-advance: false)  [4]
    commit-slice    -> /slice           (auto-advance: false)  [5]

[1] ``/critique``'s flat ``successor`` is ``/critique-review`` (the post-TRI-1
    hop to ``/build-slice`` and the BLOCKED self-loop are verdict-dependent and
    expressed in ``on-clean-completion`` prose, not the ``successor`` field).
[2] ``/critique-review`` hands back to ``/critique`` (Step 4.5 TRI-1).
[3] ``/validate-slice`` declares ``auto-advance: conditional`` in its v2
    SKILL.md (it advances on aggregate pass, halts on any FAIL/PARTIAL). This
    audit treats ``conditional`` / ``yes`` as the canonical true-family (it DOES
    auto-advance on the clean path) — the load-bearing assertion is the terminal
    boundary [4]/[5], which must be a hard ``false``.
[4] ``reflect`` is the terminal-before-commit: ``auto-advance: false``. Its
    successor names ``/commit-slice`` but the chain HALTS.
[5] ``commit-slice`` is out of the auto-advance loop entirely (``false``).

**v2 block-format change (load-bearing).** v1 ``## Pipeline position`` blocks used
``- **key**: value`` bold-field lines. The v2 SKILL.md blocks use a freeform
inline format where fields are written as plain ``key: value`` pairs, multiple
per line separated by ``·`` (mid-dot) OR split across separate ``-`` bullet
lines, e.g.::

    - predecessor: `/reflect` · successor: **`/risk-spike`** · auto-advance: true
    - predecessor: `/slice` · successor: `/design-slice`
    - auto-advance: YES — all assumptions proven → ...

The parser therefore splits the section into segments on both newlines AND
``·`` and extracts ``predecessor`` / ``successor`` / ``auto-advance`` /
``on-clean-completion`` ``key: value`` pairs from those segments. ``successor``
is normalised to its first ``/skill`` token; ``auto-advance`` is normalised via
the true-family / false mapping above.

Refuse conditions (exit 1):
    malformed-block      : ``## Pipeline position`` section absent or a required
                           field missing/unparseable.
    successor-mismatch   : declared successor != canonical successor.
    auto-advance-mismatch: declared auto-advance != canonical value (the
                           ``/commit-slice``-never-auto-invoked terminal
                           guarantee).

Usage:
    python pipeline_chain_audit.py
    python pipeline_chain_audit.py --json
    python pipeline_chain_audit.py --root <repo-root>

Exit codes:
    0  clean (all 10 blocks well-formed + edges match canonical chain)
    1  violations (malformed block / successor mismatch / auto-advance mismatch)
    2  usage error (repo root unresolvable, skills/ dir or a SKILL.md missing)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field

# --- single-skill import bootstrap (cannot use `-m`) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pathlib import Path  # noqa: E402

from scripts.lib import _stdout  # noqa: E402

# Canonical v2 per-slice loop. Order is the chain order (used for output).
# value = (skill, canonical successor command, canonical auto-advance bool).
# NOTE: risk-spike is INSERTED between slice and design-slice (the v2 change).
_CANONICAL_CHAIN: tuple[tuple[str, str, bool], ...] = (
    ("slice", "/risk-spike", True),          # v2: was "/design-slice" in v1
    ("risk-spike", "/design-slice", True),   # v2: NEW in-loop spike gate
    ("design-slice", "/critique", True),
    ("critique", "/critique-review", True),
    ("critique-review", "/critique", True),
    ("build-slice", "/code-review", True),
    ("code-review", "/validate-slice", True),
    ("validate-slice", "/reflect", True),
    ("reflect", "/commit-slice", False),
    ("commit-slice", "/slice", False),
)

_SECTION_RE = re.compile(r"^##\s+Pipeline position\s*$", re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^##\s+", re.MULTILINE)

# The field keys we extract from `key: value` segments (longest-first so
# `auto-advance` is matched before any shorter prefix would be).
_FIELD_KEYS = ("on-clean-completion", "auto-advance", "predecessor", "successor")
# A `key: value` pair inside one segment. `key` must be at a word boundary so
# `on-clean-completion`'s embedded "completion" never matches as a stray key.
_FIELD_RE = re.compile(
    r"(?<![\w-])(?P<key>on-clean-completion|auto-advance|predecessor|successor)\s*:\s*(?P<val>.*)",
    re.IGNORECASE,
)

# The fields PCA-1 requires the block to declare.
_REQUIRED_FIELDS = ("predecessor", "successor", "auto-advance", "on-clean-completion")
# Required-field PRESENCE is checked by label search (NOT by the strict
# `key: value` extractor), because some v2 blocks attach a parenthetical
# qualifier between the key and its colon — e.g.
# `on-clean-completion (aggregate result: pass, ...): the main thread advances`
# and `user-input gates (halt auto-advance — ...):`. Such a field is genuinely
# DECLARED (the v1 audit already treated `user-input gates` this way); a strict
# `<key>:` match would false-positive on the qualifier. Value EXTRACTION
# (successor / auto-advance) still uses the strict `key: value` form — those
# values never carry a pre-colon qualifier in the v2 corpus.
_LABEL_RES: dict[str, re.Pattern[str]] = {
    "predecessor": re.compile(r"(?<![\w-])predecessor\b", re.IGNORECASE),
    "successor": re.compile(r"(?<![\w-])successor\b", re.IGNORECASE),
    "auto-advance": re.compile(r"(?<![\w-])auto-advance\b", re.IGNORECASE),
    "on-clean-completion": re.compile(r"(?<![\w-])on-clean-completion\b", re.IGNORECASE),
}
# The `user-input gates` label is a parent label, not a key:value pair.
_USER_INPUT_GATES_RE = re.compile(r"user-input gates", re.IGNORECASE)


@dataclass(frozen=True)
class PCAViolation:
    kind: str       # "malformed-block" | "successor-mismatch" |
                    # "auto-advance-mismatch" | "usage-error"
    severity: str   # "Important" (all PCA-1 violations refuse)
    skill: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    repo_root: str = ""
    skills_checked: list[str] = field(default_factory=list)
    violations: list[PCAViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule": "PCA-1",
            "repo_root": self.repo_root,
            "skills_checked": self.skills_checked,
            "violations": [v.to_dict() for v in self.violations],
            "summary": {
                "violation_count": len(self.violations),
                "clean": not self.violations,
            },
        }


def _norm_cmd(raw: str) -> str:
    """Normalize a successor value to a `/skill` command token.

    Strips markdown backticks/bold and surrounding prose, keeps the first
    `/skill`-shaped token. E.g. "**`/critique-review`** (then `/build-slice`)"
    -> "/critique-review".
    """
    cleaned = raw.replace("`", "").replace("*", "")
    m = re.search(r"/([a-z][a-z0-9-]+)", cleaned)
    return f"/{m.group(1)}" if m else cleaned.strip()


def _norm_bool(raw: str) -> bool | None:
    """Parse an auto-advance value to a canonical bool, or None if unparseable.

    Normalisation (v2): the leading token (after stripping markdown) is mapped:
      - ``true`` / ``yes`` / ``conditional`` -> ``True`` (the true-family: it
        DOES auto-advance on the clean path; ``conditional`` halts only on a
        FAIL/PARTIAL gate, which is itself a user-input gate, not a chain-shape
        change).
      - ``false`` -> ``False``.
      - anything else -> ``None`` (unparseable).
    """
    cleaned = raw.replace("`", "").replace("*", "").strip().lower()
    m = re.match(r"([a-z]+)", cleaned)
    if not m:
        return None
    token = m.group(1)
    if token in ("true", "yes", "conditional"):
        return True
    if token == "false":
        return False
    return None


def _extract_section(text: str) -> str | None:
    """Return the ``## Pipeline position`` section body, or None if absent."""
    m = _SECTION_RE.search(text)
    if not m:
        return None
    start = m.end()
    nxt = _NEXT_H2_RE.search(text, start)
    return text[start:nxt.start()] if nxt else text[start:]


def _parse_fields(section: str) -> dict[str, str]:
    """Extract `key: value` fields from the freeform v2 block.

    The block is split into segments on both newlines AND ``·`` (mid-dot); each
    segment is scanned for a leading-ish ``key: value`` pair. The first
    occurrence of each key wins. A segment's value runs to the end of that
    segment (so ``·``-separation correctly bounds same-line fields).
    """
    out: dict[str, str] = {}
    # Split on newlines and the mid-dot field separator used in the v2 blocks.
    segments = re.split(r"·|\n", section)
    for seg in segments:
        seg = seg.strip().lstrip("-").strip()
        if not seg:
            continue
        fm = _FIELD_RE.match(seg)
        if not fm:
            continue
        key = fm.group("key").strip().lower()
        if key not in out:
            out[key] = fm.group("val").strip()
    return out


def audit(repo_root: Path | None = None) -> AuditResult:
    """Run the PCA-1 audit against the in-repo skills/ tree."""
    if repo_root is None:
        here = Path(__file__).resolve()
        for parent in [here] + list(here.parents):
            if (parent / ".git").exists():
                repo_root = parent
                break
        else:
            # No .git ancestor — fall back to the plugin root.
            repo_root = _REPO

    repo_root = Path(repo_root).resolve()
    result = AuditResult(repo_root=str(repo_root))

    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        result.violations.append(
            PCAViolation(
                kind="usage-error",
                severity="Important",
                skill="",
                message=f"skills/ directory not found at {skills_dir}",
            )
        )
        return result

    for skill, exp_succ, exp_auto in _CANONICAL_CHAIN:
        result.skills_checked.append(skill)
        skill_md = skills_dir / skill / "SKILL.md"
        if not skill_md.is_file():
            result.violations.append(
                PCAViolation(
                    kind="usage-error",
                    severity="Important",
                    skill=skill,
                    message=f"SKILL.md not found: {skill_md}",
                )
            )
            continue

        text = skill_md.read_text(encoding="utf-8")
        section = _extract_section(text)
        if section is None:
            result.violations.append(
                PCAViolation(
                    kind="malformed-block",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"`## Pipeline position` section absent in "
                        f"skills/{skill}/SKILL.md (PCA-1 requires it on all "
                        f"{len(_CANONICAL_CHAIN)} covered skills)"
                    ),
                )
            )
            continue

        fields = _parse_fields(section)
        # Required-field PRESENCE by label search (qualifier-tolerant).
        missing = [k for k in _REQUIRED_FIELDS if not _LABEL_RES[k].search(section)]
        if not _USER_INPUT_GATES_RE.search(section):
            missing.append("user-input gates")
        if missing:
            result.violations.append(
                PCAViolation(
                    kind="malformed-block",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"`## Pipeline position` block in skills/{skill}/"
                        f"SKILL.md missing required field(s): "
                        f"{', '.join(missing)}"
                    ),
                )
            )
            continue

        # Value extraction uses the strict `key: value` parse. The label is
        # present (checked above); if the strict extractor still couldn't recover
        # a value, the field is malformed (declared-but-unparseable).
        succ_raw = fields.get("successor")
        if succ_raw is None:
            result.violations.append(
                PCAViolation(
                    kind="malformed-block",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"skills/{skill}/SKILL.md `## Pipeline position` "
                        f"successor field is present but its `successor: <value>` "
                        f"value is unparseable"
                    ),
                )
            )
            continue

        got_succ = _norm_cmd(succ_raw)
        if got_succ != exp_succ:
            result.violations.append(
                PCAViolation(
                    kind="successor-mismatch",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"skills/{skill}/SKILL.md `## Pipeline position` "
                        f"successor is {got_succ!r}; canonical chain "
                        f"requires {exp_succ!r}"
                    ),
                )
            )

        auto_raw = fields.get("auto-advance")
        got_auto = _norm_bool(auto_raw) if auto_raw is not None else None
        if got_auto is None:
            result.violations.append(
                PCAViolation(
                    kind="malformed-block",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"skills/{skill}/SKILL.md `## Pipeline position` "
                        f"auto-advance value {auto_raw!r} is "
                        f"not parseable as true|false (true-family: "
                        f"true/yes/conditional)"
                    ),
                )
            )
        elif got_auto != exp_auto:
            result.violations.append(
                PCAViolation(
                    kind="auto-advance-mismatch",
                    severity="Important",
                    skill=skill,
                    message=(
                        f"skills/{skill}/SKILL.md `## Pipeline position` "
                        f"auto-advance is {got_auto}; canonical requires "
                        f"{exp_auto} "
                        + (
                            "(terminal boundary — /commit-slice must never "
                            "be auto-invoked)"
                            if not exp_auto
                            else ""
                        )
                    ),
                )
            )

    return result


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="pipeline_chain_audit",
        description=(
            "PCA-1 audit: verify the 10-skill v2 pipeline-chain auto-advance "
            "directives match the canonical loop (with /risk-spike in-loop)."
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=None, help="Repo root (default: ancestor with .git / plugin root)."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    args = parser.parse_args(argv)

    try:
        result = audit(repo_root=args.root)
    except Exception as e:  # noqa: BLE001 — top-level CLI guard
        print(f"pipeline_chain_audit: error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        if result.violations:
            for v in result.violations:
                tag = f"{v.skill}: " if v.skill else ""
                print(f"[{v.severity}] {v.kind}: {tag}{v.message}")
        else:
            print(
                f"PCA-1 audit: clean. {len(result.skills_checked)} skills "
                f"checked; pipeline chain matches canonical loop."
            )

    if any(v.kind == "usage-error" for v in result.violations):
        return 2
    if result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
