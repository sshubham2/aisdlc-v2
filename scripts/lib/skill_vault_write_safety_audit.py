"""SVW-1 — skill-driven vault-write-safety ADVISORY check (v2; 3.11 shrink).

This WAS a 733-line regex-NLP engine (directive-verb lexicons, negation lookback,
hyphen-compound handling, op-class/route-class inference, per-(file,reason)
exemption-count pins) grading model-written PROSE. Its own HONEST SCOPE already
admitted it cannot observe the runtime write hazard — it audits a *description of
intent*, not the write op (that is the VWS-1 AST audit's domain) — and it
historically matched nothing real (BB-01). Item 1.5 already evicted it from the
per-slice user gate. So 3.11 reduces it to a dumb, fast, low-noise ADVISORY check.

Scan surface — the v2 PROSE directive surface only: every ``skills/**/SKILL.md`` plus
``agents/**`` (NOT ``scripts/**``: code files hold the ``safe_*_text`` IMPLEMENTATIONS,
not directives — BB-05, so scanning them only false-flagged docstrings).

A NON-FENCED line is a candidate when it (a) names a shared-aggregate vault JSON file
(``_SHARED_BASENAMES``) and (b) carries a RAW-WRITE token (``_RAW_WRITE_RE`` — Write /
Edit / overwrite / truncate / raw-write / O_APPEND; a tiny FIXED set, word-bounded so it
ignores "rewrite" / "vault_edit"), and is NOT cleared by any of: a same-line
``vault_edit`` / ``safe_`` route, a ``vault-write-safe:`` exemption marker, or a
prohibition (``_PROHIBIT_RE`` — "never" / "do not" / "don't"…, i.e. a line WARNING
against raw writes). Fenced ``bash`` / ``!`` blocks (where the real ``vault_edit``
commands live) are skipped as flag sites.

Precision-over-recall on purpose: the bare "mention + no route nearby" heuristic flags
~95 benign reads on the plugin's own files (alert-fatigue, on a check that caught nothing
real — BB-01); the raw-write token + same-line clearing give it signal without the
deleted NLP/op-class/exemption-count machinery. A candidate is a HINT, never proof — the
real control is the cooperative model + the ``vault_edit`` wrapper (ADR-067/088).

ADVISORY: prints candidates and exits 0 by default (``--strict`` exits 1). Shared
basenames (genuinely-concurrent aggregates; per-slice files + distinct-filename ADR
creates are isolated by construction and excluded): risk-register.json, candidates.json,
lessons-learned.json, shippability.json, drift-log.json, build-checks.json, sync-log.json,
critic-calibration-log.json, _index.json.

CLI: --root (default: plugin root, three parents up) · --json · --strict.
Exit: 0 advisory/clean · 1 only with --strict and >=1 candidate · 2 if skills/ absent.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # scripts/lib/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

# Genuinely-concurrent shared-aggregate vault JSON aggregates. ADR-*.json + per-slice
# folder files are isolated by construction and NOT listed.
_SHARED_BASENAMES: frozenset[str] = frozenset({
    "risk-register.json", "candidates.json", "lessons-learned.json",
    "shippability.json", "drift-log.json", "build-checks.json",
    "sync-log.json", "critic-calibration-log.json", "_index.json",
})

# Raw-write intent — a tiny FIXED token set (NOT the deleted directive-verb NLP engine).
# Word-bounded: `\bwrite\b` ignores "rewrite"/"written"; `\bedit\b` ignores "vault_edit".
_RAW_WRITE_RE = re.compile(
    r"\b(write|edit|overwrite|truncate|raw[- ]?write|o_append)\b", re.IGNORECASE)
# A line WARNING against raw writes ("never raw-write X" / "do NOT write X") is the
# OPPOSITE of a hazard — clear it. Tiny fixed set, not the deleted negation lookback.
_PROHIBIT_RE = re.compile(r"\b(never|do not|don'?t|not\s+use|no raw)\b", re.IGNORECASE)
# Same-line safe-write / sanctioned-exemption tokens that clear a mention.
_ROUTE_TOKENS: tuple[str, ...] = ("vault_edit", "safe_", "vault-write-safe")
_SCAN_TEXT_SUFFIXES: frozenset[str] = frozenset({".md", ".txt"})


@dataclass
class Candidate:
    file: str
    line: int  # 1-based
    basename: str
    text: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line,
                "basename": self.basename, "text": self.text}


@dataclass
class AuditResult:
    files_scanned: int = 0
    candidates: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"advisory": True, "files_scanned": self.files_scanned,
                "candidate_count": len(self.candidates),
                "candidates": [c.to_dict() for c in self.candidates]}


def _iter_source_files(root: Path) -> list[Path]:
    """The v2 PROSE directive surface: skills/**/SKILL.md + agents/**.

    NOT scripts/** (BB-05): code files hold the safe_*_text IMPLEMENTATIONS, not
    directives, so scanning them only false-flagged self-describing docstrings.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    cand: list[Path] = []
    if (root / "skills").exists():
        cand.extend(sorted((root / "skills").glob("**/SKILL.md")))
    if (root / "agents").exists():
        cand.extend(sorted((root / "agents").glob("**/*")))
    for p in cand:
        if p in seen or not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() not in _SCAN_TEXT_SUFFIXES:
            continue
        seen.add(p)
        out.append(p)
    return sorted(out)


def _fenced_flags(lines: list[str]) -> list[bool]:
    """True for every line inside (or delimiting) a ``` / ~~~ fenced code block.

    Simple toggle on lines whose stripped text opens with >=3 backticks or tildes —
    enough for an advisory heuristic over SKILL.md / agents prose.
    """
    flags = [False] * len(lines)
    in_fence = False
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            flags[i] = True            # the delimiter line counts as fenced
            in_fence = not in_fence
        else:
            flags[i] = in_fence
    return flags


def _is_cleared(line: str) -> bool:
    """A mention is cleared by a same-line route token, exemption marker, or a
    prohibition ("never raw-write X")."""
    return (any(tok in line for tok in _ROUTE_TOKENS)
            or bool(_PROHIBIT_RE.search(line)))


def audit_file(path: Path, rel: str) -> list[Candidate]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    fenced = _fenced_flags(lines)
    found: list[Candidate] = []
    for i, ln in enumerate(lines):
        if fenced[i]:
            continue
        hit = next((b for b in _SHARED_BASENAMES if b in ln), None)
        if hit is None or not _RAW_WRITE_RE.search(ln) or _is_cleared(ln):
            continue
        found.append(Candidate(file=rel, line=i + 1, basename=hit, text=ln.strip()[:200]))
    return found


def audit_root(root: Path) -> AuditResult:
    result = AuditResult()
    for p in _iter_source_files(root):
        result.files_scanned += 1
        result.candidates.extend(audit_file(p, p.relative_to(root).as_posix()))
    return result


def _format_human(result: AuditResult) -> str:
    if not result.candidates:
        return (f"SVW-1 (advisory): clean. {result.files_scanned} source(s) scanned; "
                f"no un-routed raw-write directive on a shared-aggregate file.\n")
    out = [
        f"SVW-1 (advisory): {len(result.candidates)} non-fenced raw-write mention(s) of a "
        f"shared-aggregate file with no same-line vault_edit/safe_/exemption "
        f"({result.files_scanned} source(s) scanned).\n"
        f"  HINTS to eyeball, not proof — SVW-1 grades prose, not the runtime write op\n"
        f"  (that is the VWS-1 AST audit's domain). Advisory only.\n\n"
    ]
    out.extend(f"  {c.file}:{c.line} [{c.basename}] {c.text}\n" for c in result.candidates)
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="skill_vault_write_safety_audit",
        description=(
            "SVW-1 ADVISORY (3.11): flag skills/**/SKILL.md + agents/** non-fenced prose "
            "lines that raw-write (Write/Edit/overwrite) a shared-aggregate vault JSON file "
            "with no same-line vault_edit/safe_/exemption. Advisory — exits 0 by default."
        ),
    )
    parser.add_argument("--root", type=Path, default=None,
                        help="Repo root (default: plugin root, three parents up)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any candidate is found (default: advisory exit 0)")
    args = parser.parse_args(argv)

    root = (args.root.resolve() if args.root is not None
            else Path(__file__).resolve().parent.parent.parent)
    if not (root / "skills").exists():
        sys.stderr.write(f"skills/ directory not found at {root}\n")
        return 2

    result = audit_root(root)
    sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n"
                     if args.json else _format_human(result))
    return 1 if (args.strict and result.candidates) else 0


if __name__ == "__main__":
    sys.exit(main())
