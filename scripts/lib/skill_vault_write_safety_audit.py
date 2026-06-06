"""SVW-1 — skill-driven vault-write-safety audit (v1 slice-095 / [[ADR-087]];
op-class-aware RMW enforcement v1 slice-097 / [[ADR-088]]; ported to v2 JSON +
`scripts.lib` channel).

The skill-driven counterpart of v1 slice-094's VWS-1 (which AST-audits Python
writers). SVW-1 statically scans the methodology surface — every
``skills/**/SKILL.md`` (v2 layout; v1 was ``skills/*/SKILL.md``), plus
``agents/**`` and ``scripts/**`` — for any *directive* that mutates a
**shared-aggregate** vault file without routing through an OP-CLASS-CORRECT
safe channel (``vault_edit append`` for the append class, ``vault_edit rewrite``
for the read-modify-write class) or carrying a sanctioned exemption marker.

V2 PORT NOTES (vs v1 ``tools.skill_vault_write_safety_audit``):
  - Imports ``from scripts.lib import _stdout`` (v1: ``from tools import _stdout``).
  - The shared-aggregate vault files are now **JSON** (v1 guarded the ``.md``
    equivalents). The guarded basenames are the v2 JSON aggregates:
    ``risk-register.json``, ``candidates.json``, ``lessons-learned.json``,
    ``shippability.json``, ``drift-log.json``, ``build-checks.json``,
    ``sync-log.json``, ``critic-calibration-log.json``, ``_index.json``
    (covers ``slices/_index.json`` + ``slices/archive/_index.json``). v1's
    ``slice-queue.md`` is GONE (absorbed into ``candidates.json``);
    ``methodology-changelog.md`` is NOT a v2 vault aggregate (it survives only
    as a forward-sync *gate* name, never as a mutated artifact) and is dropped.
  - The safe-write channel is now ``scripts.lib.vault_edit`` /
    ``scripts.lib._vault_write.safe_*`` (v1: ``tools.vault_edit`` /
    ``tools._vault_write``). Route tokens are channel-agnostic substrings
    (``vault_edit append`` / ``safe_append_text`` / ``vault_edit rewrite`` /
    ``safe_rewrite_text``), so they match the v2 ``$PY -m scripts.lib.vault_edit
    append ...`` / `` `vault_edit append` `` corpus form unchanged.
  - The shared-file reference path-prefix is ``<vault>/`` (v2 external store),
    not v1's in-repo ``architecture/`` (zero ``architecture/`` refs remain).
  - ADR-*.json is EXCLUDED from the shared set, as in v1: each ADR is a
    distinct-filename, append-only NEW create (isolated by construction, like
    per-slice-folder files) — never a concurrently-appended shared aggregate.

HONEST SCOPE (Critic B1 / [[ADR-029]]): SVW-1's guarantee is over the
**prose-detection surface** — no SKILL.md *prescribes* an unsafe raw mutation
of a shared file — NOT a completeness guarantee over runtime writes. Unlike
VWS-1 (an AST scan where the write op IS the ground truth), this audits a
*description of intent*; it cannot observe Claude invoking the raw
``Write``/``Edit`` tool at runtime in violation of correct prose (the R-2 class
— structurally unreachable by a static audit). There is no content-oracle for
LLM-authored vault appends; prose honesty + the wrapper + the cooperative model
([[ADR-067]]) are the controls.

DETECTION MODEL (fail-closed for RECOGNIZED sites; Critic M1/M3):
  1. Shared-file set (``_SHARED_BASENAMES``) — the genuinely-concurrent vault
     JSON aggregates. Per-slice-folder files + distinct-filename ADR creates
     are NOT in the set (isolated by construction).
  2. Mutation-site detector — a non-fenced line where a directive verb
     (``_DIRECTIVE_VERBS``) governs (appears before) a backticked-or-
     ``<vault>/``-path reference to a shared file. Bare mentions (no directive
     verb, or an un-backticked filename) are NOT sites; a verb used as a NOUN
     immediately after a code span is excluded. Recognition is verb-LEXICON-
     bounded — the guarantee is "fail-closed for recognized directive verbs",
     not a completeness oracle over every English phrasing. Fenced regions are
     tracked CommonMark-style (char + length).
  3. Verdict per site (fail-closed, OP-CLASS-AWARE — ``_route_class`` +
     ``_verdict``): a route reference counts only inside a backtick code span or
     ``<!-- route: ... -->`` marker, un-negated, and must NAME its subcommand. A
     REWRITE route → CLEAN (safe for any class). An APPEND route → CLEAN UNLESS a
     rewrite-class verb (``regenerate``/``rewrite``) governs the site, in which
     case it is a ``channel-mismatch`` VIOLATION (an RMW down the unsafe append
     channel). No route + valid ``<!-- vault-write-safe: <reason> -->`` (reason
     in the closed ``_EXEMPT_REASONS`` enum) → exempted; else → unrouted
     VIOLATION. The exempt-site *allowlist* ``_REGISTERED_SKILL_EXEMPTIONS`` is
     pinned at per-(file, reason) COUNT granularity.

Usage:
    python -m scripts.lib.skill_vault_write_safety_audit
    python -m scripts.lib.skill_vault_write_safety_audit --json
    python -m scripts.lib.skill_vault_write_safety_audit --root <repo-root>

Exit codes:
    0  clean (every shared-vault mutation site is routed or validly exempted)
    1  violation (>=1 unrouted/unexempted/unknown-reason site)
    2  usage error (methodology surface missing / unreadable)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
# A skill's shell command runs in the USER's CWD, not the plugin root, and SKILL.md
# cannot use `python -m` or `${CLAUDE_PLUGIN_ROOT}` (the latter only expands in JSON
# hooks/MCP). Shared tools are invoked as
# `$PY "${CLAUDE_SKILL_DIR}/../../scripts/lib/<name>.py" ...`, which puts scripts/lib
# (not the plugin root) on sys.path[0]; add the plugin root so `from scripts.lib import
# ...` resolves, mirroring the single-skill parents[3] bootstrap. No-op under `-m`.
import sys as _sys
import pathlib as _pathlib
_PLUGIN_ROOT = _pathlib.Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PLUGIN_ROOT))
# --- end plugin-root bootstrap ---

from scripts.lib import _stdout

# --- the shared-aggregate vault file set (genuinely concurrent mutation
# targets), v2 JSON. ADR-*.json is EXCLUDED: each ADR is a distinct-filename NEW
# create (isolated by construction, like per-slice files), not a shared-file
# append — and append-only by convention (supersede, never edit in place).
# v1's `slice-queue.md` is GONE (absorbed into candidates.json);
# `methodology-changelog.md` is NOT a v2 vault aggregate (forward-sync gate name
# only), so it is dropped.
_SHARED_BASENAMES: tuple[str, ...] = (
    "risk-register.json",
    "candidates.json",
    "lessons-learned.json",
    "shippability.json",
    "drift-log.json",
    "build-checks.json",
    "sync-log.json",
    "critic-calibration-log.json",
    # covers slices/_index.json AND slices/archive/_index.json (both shared,
    # concurrently CAS-rewritten on archival).
    "_index.json",
)
_SHARED_ALT = "|".join(re.escape(b) for b in _SHARED_BASENAMES)
# A shared-file reference: a backticked path ending in a shared basename, OR a
# `<vault>/`-prefixed path ending in one (v2 external-store prefix; v1 used the
# in-repo `architecture/` prefix). A BARE filename (no backtick, no `<vault>/`
# prefix) is NOT a reference (excludes example/descriptive prose).
_SHARED_REF_RE = re.compile(
    r"`[^`\n]*(?:" + _SHARED_ALT + r")`"
    r"|<vault>/[\w./-]*(?:" + _SHARED_ALT + r")"
)

# Directive verbs that, when they GOVERN a shared-file reference (appear before
# it on the line), denote a mutation. "edit" is included but a noun-usage right
# after a code span is filtered in _is_mutation_site.
_DIRECTIVE_VERBS: tuple[str, ...] = (
    # original six
    "append", "add", "write", "update", "regenerate", "edit",
    # common UNAMBIGUOUS mutation verbs the 6-verb lexicon missed.
    "insert", "replace", "prepend", "modify", "amend", "create",
    # "rewrite" — in _REWRITE_CLASS_VERBS (op-class) but must ALSO be a directive
    # verb so a "Rewrite ... in place" site is DETECTED as a mutation site at all
    # (`\bwrite\b` does not match inside "Rewrite").
    "rewrite",
)
# LEXICON-BOUND RESIDUAL (honest scope): recognition is verb-lexicon-bounded, so
# the "fail-closed" guarantee is over RECOGNIZED directive verbs — NOT a
# completeness guarantee over every English phrasing. Verbs that are commonly
# NOUNS adjacent to a file reference in this corpus — `note`, `record`, `set`,
# `log`, `mark`, `put` — are deliberately EXCLUDED: adding them false-positives
# on descriptive prose without a fragile noun/verb disambiguator. (`register` is
# excluded for a harder reason: `\bregister\b` matches inside
# `risk-register.json` itself.)
_DIRECTIVE_RE = re.compile(
    r"\b(?:" + "|".join(_DIRECTIVE_VERBS) + r")\b", re.IGNORECASE
)

# Line-local CLEAN signals, OP-CLASS-AWARE. A safe-route reference is a route
# token INSIDE a backtick code span (corpus convention `vault_edit append` /
# `$PY -m scripts.lib.vault_edit rewrite ...`) OR an HTML `<!-- route: ... -->`
# marker, un-negated. Tokens are OP-CLASSED so the audit distinguishes an APPEND
# route from a REWRITE route:
#   - APPEND-class  → safe for the append sub-class (O_APPEND, non-clobbering).
#   - REWRITE-class → safe for the read-modify-write sub-class (compare-and-swap;
#     also safe for an append, just heavier).
# A route reference must NAME its subcommand to be op-class-classifiable; a bare
# `vault_edit` / `_vault_write` token is NOT a standalone clean signal (it names
# no subcommand). The v2 corpus cites `vault_edit append` / `vault_edit rewrite`
# explicitly, matching these tokens unchanged.
# A BARE prose mention still does NOT clean a site: "do NOT use
# `vault_edit append`", "NOT via `safe_append_text`" stay VIOLATION via the
# negation look-back below. (NB the v2 reflect:224 "NOT `vault_edit append` —
# appending at EOF would put the row at the oldest position" is such a negated
# mention; it sits on an `_index.json` rewrite-class site that is ALSO routed via
# `vault_edit rewrite` two lines up, so the site is independently CLEAN.)
_APPEND_ROUTE_TOKENS: tuple[str, ...] = ("vault_edit append", "safe_append_text")
# v2 gains `vault_edit update` — a JSON-native LOCKED read-modify-write (lock
# serializes concurrent writers; the README's "JSON-native append/update are
# SVW-1 locked read-modify-write"). It is therefore a REWRITE-class (RMW-safe)
# route, alongside `vault_edit rewrite` (the explicit compare-and-swap form) and
# the `safe_*_text` primitives. (v1 had no `update` subcommand.)
_REWRITE_ROUTE_TOKENS: tuple[str, ...] = (
    "vault_edit rewrite", "vault_edit update", "safe_rewrite_text", "safe_mutate_text",
)


def _codespan_re(tokens: tuple[str, ...]) -> "re.Pattern[str]":
    alt = "|".join(re.escape(t) for t in tokens)
    return re.compile(r"`[^`\n]*(?:" + alt + r")[^`\n]*`")


def _marker_re(tokens: tuple[str, ...]) -> "re.Pattern[str]":
    alt = "|".join(re.escape(t) for t in tokens)
    return re.compile(r"<!--\s*route:[^>]*(?:" + alt + r")[^>]*-->")


_APPEND_CODESPAN_RE = _codespan_re(_APPEND_ROUTE_TOKENS)
_APPEND_MARKER_RE = _marker_re(_APPEND_ROUTE_TOKENS)
_REWRITE_CODESPAN_RE = _codespan_re(_REWRITE_ROUTE_TOKENS)
_REWRITE_MARKER_RE = _marker_re(_REWRITE_ROUTE_TOKENS)
# Unambiguous read-modify-write directive verbs (a subset of _DIRECTIVE_VERBS). A
# site governed by one of these REQUIRES a REWRITE-class route — an append route
# is a channel-mismatch VIOLATION. Ambiguous verbs (`update`/`write`/`edit`) are
# NOT in this set (the documented lexical ceiling): an RMW phrased with them and
# mis-routed via append is the honest residual, not silently closed.
_REWRITE_CLASS_VERBS: tuple[str, ...] = ("regenerate", "rewrite")
_REWRITE_VERB_RE = re.compile(
    r"\b(?:" + "|".join(_REWRITE_CLASS_VERBS) + r")\b", re.IGNORECASE
)
# A negation GOVERNING a route reference (within the ~2 words immediately before
# it) demotes that reference: "do NOT use `vault_edit append`" /
# "NOT via `safe_append_text`" are not routes. The look-back is deliberately
# SHORT so a trailing safety assertion that governs the RAW write — "never a
# raw `Write`/`Edit`", which sits AFTER the route token — never demotes a
# genuine route.
_NEG_LOOKBACK_WORDS = 2
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|none|without|bypass(?:es|ing)?|predates)\b"
    r"|n't|\binstead\s+of\b",
    re.IGNORECASE,
)
_EXEMPTION_RE = re.compile(r"<!--\s*vault-write-safe:\s*([a-z0-9-]+)\s*-->")
_EXEMPT_REASONS: frozenset[str] = frozenset({
    # "deferred-rmw" RETIRED at v1 slice-097 ([[ADR-088]]): the read-modify-write
    # sub-class is ENFORCED (route via `vault_edit rewrite` / compare-and-swap),
    # not deferred. A lingering `deferred-rmw` marker is an
    # unknown-exemption-reason VIOLATION — the deferral is un-re-claimable.
    "project-open-single-shot",  # project-lifecycle writer, not a parallel hazard
})

# Pinned allowlist of exempt sites at per-(file, reason) COUNT granularity (M3).
# Pinning the COUNT — not just the (file, reason) PAIR — means adding an N+1-th
# exemption marker to an ALREADY-listed file trips the regression that consumes
# registered_exemption_counts(). The keys are repo-relative SKILL.md paths.
# v2: the project-open single-shot risk-register writers are the two PIPELINE
# OPENERS — triage (greenfield) + adopt (brownfield). v1 also listed discover +
# risk-spike, but in v2 those route risk-register mutations through
# scripts.lib.vault_edit (append/update), so they carry no exemption marker.
_REGISTERED_SKILL_EXEMPTIONS: dict[tuple[str, str], int] = {
    ("skills/triage/SKILL.md", "project-open-single-shot"): 1,  # risk-register (greenfield open)
    ("skills/adopt/SKILL.md", "project-open-single-shot"): 1,   # risk-register (brownfield open)
}

# CommonMark fenced code block: 0+ leading spaces, then a run of >=3 backticks
# or >=3 tildes, then an optional info string. Track the opener's fence char +
# length so a fence closes only on the SAME char at >= the opener length with no
# trailing content.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")

# A markdown ATX heading line (`#`..`######`). A heading that names a mutation
# ("### … update `X`", "## Step 3 — Regenerate `X`") is a SECTION LABEL, not a
# single executable directive: the routed write lives somewhere in the section
# body, possibly several sub-steps below. A heading site is therefore routed if
# a write-route fence appears anywhere before the NEXT same-or-higher-level
# heading (its section), not just in the tight following-fence window.
_HEADING_RE = re.compile(r"^(#{1,6})\s")

# v2 prose shape: a directive PROSE line ("Append … to X via:") introduces a
# ```bash fence two-or-so lines later that carries the actual
# `$PY -m scripts.lib.vault_edit <subcmd>` route. A non-heading directive site
# that has no INLINE route is still ROUTED if the immediately-following fenced
# code block (its opener within this many lines after the site) names a route.
# The window absorbs the corpus's "directive line / blank line / ```bash"
# spacing and short intro sentences without spanning to an unrelated later block.
_FENCE_LOOKAHEAD = 4

# v2 methodology surface globs (v1 scanned only skills/*/SKILL.md). The audit
# surface is `skills/**/SKILL.md`, `agents/**`, `scripts/**` — every place a
# directive that mutates a shared aggregate could live. Only text files with a
# markdown/python/shell/text extension are scanned (binaries skipped).
_SCAN_TEXT_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".py", ".sh", ".txt", ".json", "",  # "" = extensionless text (rare)
})


@dataclass(frozen=True)
class Violation:
    file: str       # repo-relative source path
    line: int
    kind: str       # "unrouted" | "unknown-exemption-reason" | "channel-mismatch"
    message: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "kind": self.kind,
                "message": self.message}


@dataclass(frozen=True)
class Exemption:
    file: str
    line: int
    reason: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "reason": self.reason}


@dataclass
class AuditResult:
    skills_scanned: int = 0
    sites_found: int = 0
    sites_routed: int = 0
    violations: list[Violation] = field(default_factory=list)
    exemptions: list[Exemption] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "clean" if not self.violations else "violation"

    def to_dict(self) -> dict:
        return {
            "skills_scanned": self.skills_scanned,
            "sites_found": self.sites_found,
            "sites_routed": self.sites_routed,
            "violations": [v.to_dict() for v in self.violations],
            "exemptions": [e.to_dict() for e in self.exemptions],
            "status": self.status,
        }


def _is_mutation_site(line: str) -> bool:
    """A non-fenced line where a directive verb GOVERNS (precedes) a vault-scoped
    shared-file reference.

    Excludes:
      - a ``~/.claude/`` GLOBAL file ref (the cross-project global, NOT the
        ``<vault>/`` store).
      - a verb that is part of a hyphen-compound — either the verb FOLLOWS a
        hyphen (``post-write`` / ``read-modify-write``) or PRECEDES one
        (``append-mutated`` files; v2 SVW-1 policy-description prose) — the verb
        is a noun/adjective there, not a directive.
      - a verb used as a NOUN right after a code span.
      - a verb governed by a leading NEGATION within ~2 words ("Do NOT write
        ``drift-log.json``", "never write ``backlog.md``") — a PROHIBITION, not a
        mutation directive (v2 prose shape; v1 negation handling was route-only).
    """
    for ref in _SHARED_REF_RE.finditer(line):
        if ".claude" in ref.group():
            continue  # global ~/.claude/ file, not the <vault>/ store
        for m in _DIRECTIVE_RE.finditer(line):
            if m.start() >= ref.start():
                continue  # verb must GOVERN (precede) the file reference
            if m.start() > 0 and line[m.start() - 1] == "-":
                continue  # hyphen-compound, verb after hyphen (read-modify-write)
            if m.end() < len(line) and line[m.end()] == "-":
                continue  # hyphen-compound, verb before hyphen (append-mutated)
            if "`" in line[max(0, m.start() - 2):m.start()]:
                continue  # noun-usage right after a code span (`X.json` edit)
            preceding = " ".join(line[: m.start()].split()[-_NEG_LOOKBACK_WORDS:])
            if _NEGATION_RE.search(preceding):
                continue  # negated directive ("Do NOT write …") — a prohibition
            return True
    return False


def _route_class(line: str) -> str | None:
    """Return the OP-CLASS of a genuine (un-negated, in-codespan-or-marker) route
    reference on the line: ``"rewrite"`` | ``"append"`` | ``None``.

    A REWRITE route is reported even if an APPEND token also appears (a rewrite
    channel is safe for both classes). The ~2-word negation look-back demotes a
    described-not-prescribed reference ("do NOT use ``vault_edit append``")."""
    for cls, codespan_re, marker_re in (
        ("rewrite", _REWRITE_CODESPAN_RE, _REWRITE_MARKER_RE),
        ("append", _APPEND_CODESPAN_RE, _APPEND_MARKER_RE),
    ):
        for m in list(codespan_re.finditer(line)) + list(marker_re.finditer(line)):
            preceding = " ".join(line[: m.start()].split()[-_NEG_LOOKBACK_WORDS:])
            if _NEGATION_RE.search(preceding):
                continue  # negation governs this route reference — not a real route
            return cls
    return None


def _route_class_in_block(block_lines: list[str]) -> str | None:
    """Return the OP-CLASS of a route reference appearing inside a fenced CODE
    BLOCK's content lines: ``"rewrite"`` | ``"append"`` | ``None``.

    The v2 corpus convention is a directive PROSE line ("Append … to X via:" /
    "Update `X` (CAS-rewrite):") followed immediately by a ```bash fence whose
    body runs ``$PY -m scripts.lib.vault_edit <subcmd> …``. Inside the fence the
    whole region is already a code span, so route tokens appear BARE (not wrapped
    in inline backticks) — hence this scans the raw token, unlike ``_route_class``
    which requires a backtick code span. A REWRITE-class token wins over an APPEND
    one (rewrite is safe for both classes). A bare-`vault_edit` line with no named
    subcommand is NOT a route (must name its subcommand to be op-classable)."""
    blob = "\n".join(block_lines)
    for tok in _REWRITE_ROUTE_TOKENS:
        if tok in blob:
            return "rewrite"
    for tok in _APPEND_ROUTE_TOKENS:
        if tok in blob:
            return "append"
    return None


def _site_verb_is_rewrite_class(line: str) -> bool:
    """True iff an unambiguous REWRITE-class verb (``regenerate``/``rewrite``)
    GOVERNS a shared-file reference on the line — mirrors ``_is_mutation_site``'s
    governing rule (precede + not hyphen-compound + not noun-after-codespan),
    restricted to the rewrite-class lexicon. Such a site REQUIRES a rewrite-class
    route; an append route on it is a channel-mismatch VIOLATION."""
    for ref in _SHARED_REF_RE.finditer(line):
        if ".claude" in ref.group():
            continue
        for m in _REWRITE_VERB_RE.finditer(line):
            if m.start() >= ref.start():
                continue  # verb must GOVERN (precede) the file reference
            if m.start() > 0 and line[m.start() - 1] == "-":
                continue  # hyphen-compound (read-modify-write)
            if "`" in line[max(0, m.start() - 2):m.start()]:
                continue  # noun-usage right after a code span
            return True
    return False


def _verdict(line: str) -> tuple[str, str | None]:
    """Return (verdict, detail) for a mutation-site line, OP-CLASS-AWARE.

    ("routed", None) | ("exempted", reason) | ("violation", kind), kind ∈
    {"unrouted", "unknown-exemption-reason", "channel-mismatch"}.

    Rules (asymmetric — only the UNSAFE direction is a violation):
      - REWRITE route present                 → routed (safe for any op-class).
      - APPEND route + rewrite-class verb      → channel-mismatch VIOLATION (an RMW
        routed through the lost-update-UNSAFE append channel).
      - APPEND route + non-rewrite-class verb  → routed (append verb, append route).
      - no route + valid exemption             → exempted.
      - no route                               → unrouted VIOLATION.
    """
    route_cls = _route_class(line)
    if route_cls == "rewrite":
        return ("routed", None)
    if route_cls == "append":
        if _site_verb_is_rewrite_class(line):
            return ("violation", "channel-mismatch")
        return ("routed", None)
    m = _EXEMPTION_RE.search(line)
    if m:
        reason = m.group(1)
        if reason in _EXEMPT_REASONS:
            return ("exempted", reason)
        return ("violation", "unknown-exemption-reason")
    return ("violation", "unrouted")


def _iter_source_files(root: Path) -> list[Path]:
    """The v2 methodology surface: skills/**/SKILL.md + agents/** + scripts/**.

    (v1 scanned only skills/*/SKILL.md.) Returns a sorted, de-duplicated list of
    text files; binaries and __pycache__ are skipped.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    candidates: list[Path] = []
    skills_dir = root / "skills"
    if skills_dir.exists():
        candidates.extend(sorted(skills_dir.glob("**/SKILL.md")))
    for sub in ("agents", "scripts"):
        d = root / sub
        if d.exists():
            candidates.extend(sorted(d.glob("**/*")))
    for p in candidates:
        if p in seen:
            continue
        if not p.is_file():
            continue
        if "__pycache__" in p.parts:
            continue
        if p.suffix.lower() not in _SCAN_TEXT_SUFFIXES:
            continue
        seen.add(p)
        out.append(p)
    return sorted(out)


def _fence_spans(lines: list[str]) -> tuple[list[tuple[int, list[str]]], list[bool]]:
    """Single CommonMark fence pass over a file's lines (0-based).

    Returns ``(blocks, line_fenced)`` where:
      - ``blocks`` is a list of ``(open_idx, content_lines)`` — ``open_idx`` is the
        0-based index of the opening fence line; ``content_lines`` are the raw
        lines strictly BETWEEN the opener and its closer (the code-block body).
      - ``line_fenced[idx]`` is True for every line that is the fence delimiter
        itself OR interior content (so the caller skips it as a prose site).

    Fence tracking mirrors the original inline scanner: char + length aware
    (a `~~~` line inside a ``` block, or a ```lang info-string content line, does
    not invert parity), and a backtick opener carrying a backtick in its info
    string is treated as content, not an opener."""
    blocks: list[tuple[int, list[str]]] = []
    line_fenced = [False] * len(lines)
    in_fence = False
    fence_marker = ""
    open_idx = -1
    body: list[str] = []
    for idx, line in enumerate(lines):
        fm = _FENCE_RE.match(line)
        if fm:
            ticks, rest = fm.group(1), fm.group(2)
            if not in_fence:
                if ticks[0] == "`" and "`" in rest:
                    pass  # not a valid opener — inline code span, treat as prose
                else:
                    in_fence = True
                    fence_marker = ticks
                    open_idx = idx
                    body = []
                    line_fenced[idx] = True  # the opener delimiter
                    continue
            elif (
                ticks[0] == fence_marker[0]
                and len(ticks) >= len(fence_marker)
                and rest.strip() == ""
            ):
                line_fenced[idx] = True  # the closer delimiter
                blocks.append((open_idx, body))
                in_fence = False
                fence_marker = ""
                open_idx = -1
                body = []
                continue
            else:
                # fence-shaped line that is content of the open fence
                line_fenced[idx] = True
                body.append(line)
                continue
        if in_fence:
            line_fenced[idx] = True
            body.append(line)
    if in_fence:  # unterminated fence at EOF — flush what we have
        blocks.append((open_idx, body))
    return blocks, line_fenced


def _following_block(site_idx: int, blocks: list[tuple[int, list[str]]]) -> list[str] | None:
    """The content lines of the first fenced block whose opener falls within
    ``_FENCE_LOOKAHEAD`` lines AFTER ``site_idx`` (0-based). ``None`` if no fence
    opens in that window — the directive's route, if any, must be inline."""
    for open_idx, body in blocks:
        if open_idx <= site_idx:
            continue
        if open_idx - site_idx <= _FENCE_LOOKAHEAD:
            return body
        break  # blocks are in file order — the next one is even farther
    return None


def _section_route_class(
    heading_idx: int, lines: list[str], blocks: list[tuple[int, list[str]]]
) -> str | None:
    """The OP-CLASS of the first write route appearing in the SECTION body of a
    heading at ``heading_idx`` (0-based) — i.e. inside a fenced block whose opener
    is after the heading and before the next same-or-higher-level heading.
    ``None`` if the section names no write route.

    REWRITE wins over APPEND across the whole section (a rewrite route anywhere in
    the section makes the section's aggregate write RMW-safe)."""
    hm = _HEADING_RE.match(lines[heading_idx])
    level = len(hm.group(1)) if hm else 6
    section_end = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        m = _HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= level:
            section_end = j
            break
    saw_append = False
    for open_idx, body in blocks:
        if open_idx <= heading_idx or open_idx >= section_end:
            continue
        cls = _route_class_in_block(body)
        if cls == "rewrite":
            return "rewrite"
        if cls == "append":
            saw_append = True
    return "append" if saw_append else None


def audit_root(root: Path) -> AuditResult:
    """Scan the v2 methodology surface; classify each shared-vault mutation site."""
    result = AuditResult()
    for path in _iter_source_files(root):
        result.skills_scanned += 1
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # unreadable / non-utf-8 binary — skip (not a prose source)
        lines = text.splitlines()
        fence_blocks, line_fenced = _fence_spans(lines)
        for idx, line in enumerate(lines):
            i = idx + 1  # 1-based reported line number
            if line_fenced[idx]:
                continue  # inside a fenced code block — not a prose directive site
            if not _is_mutation_site(line):
                continue
            result.sites_found += 1
            verdict, detail = _verdict(line)
            # v2 look-ahead: an unrouted directive whose route lives in a fenced
            # block (the dominant v2 shape) is ROUTED. A HEADING site looks across
            # its whole section body; a non-heading directive line looks only at
            # its immediately-following fence (the tight window).
            if verdict == "violation" and detail == "unrouted":
                if _HEADING_RE.match(line):
                    route_cls = _section_route_class(idx, lines, fence_blocks)
                else:
                    blk = _following_block(idx, fence_blocks)
                    route_cls = _route_class_in_block(blk) if blk is not None else None
                if route_cls == "rewrite":
                    verdict, detail = ("routed", None)
                elif route_cls == "append":
                    if _site_verb_is_rewrite_class(line):
                        verdict, detail = ("violation", "channel-mismatch")
                    else:
                        verdict, detail = ("routed", None)
            if verdict == "routed":
                result.sites_routed += 1
            elif verdict == "exempted":
                result.exemptions.append(Exemption(file=rel, line=i, reason=detail))  # type: ignore[arg-type]
            else:  # violation
                if detail == "unknown-exemption-reason":
                    msg = (
                        f"exemption marker with reason not in {sorted(_EXEMPT_REASONS)} "
                        f"(note: `deferred-rmw` was RETIRED — route the RMW site "
                        f"through `vault_edit rewrite`, do not re-defer)"
                    )
                elif detail == "channel-mismatch":
                    msg = (
                        "rewrite-class mutation (regenerate/rewrite of a shared-aggregate "
                        "vault file) routed through the lost-update-UNSAFE `vault_edit "
                        "append` channel — route it through `vault_edit rewrite` "
                        "(compare-and-swap; R-32 RMW class, [[ADR-088]])"
                    )
                else:
                    msg = (
                        "unrouted skill-driven mutation of a shared-aggregate vault "
                        "file — route through `vault_edit append` (append class) / "
                        "`vault_edit rewrite` (read-modify-write class) or add "
                        "`<!-- vault-write-safe: project-open-single-shot -->`"
                    )
                result.violations.append(
                    Violation(file=rel, line=i, kind=detail or "unrouted", message=msg)  # type: ignore[arg-type]
                )
    return result


def registered_exemption_counts(root: Path) -> dict[tuple[str, str], int]:
    """The per-(source, reason) COUNT of exemptions actually present in the tree —
    consumed by a regression test to pin against _REGISTERED_SKILL_EXEMPTIONS at
    site-count granularity (M3). Adding an N+1-th exemption marker to an
    already-listed (source, reason) changes its count here and trips the pin."""
    counts: dict[tuple[str, str], int] = {}
    for e in audit_root(root).exemptions:
        key = (e.file, e.reason)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _format_human(result: AuditResult) -> str:
    if not result.violations:
        return (
            f"SVW-1 skill-vault-write audit: clean. "
            f"{result.skills_scanned} source(s) scanned; "
            f"{result.sites_found} mutation site(s) "
            f"({result.sites_routed} routed, {len(result.exemptions)} exempted).\n"
        )
    out = [
        f"SVW-1 skill-vault-write audit: {len(result.violations)} violation(s):\n\n"
    ]
    for v in result.violations:
        out.append(f"  {v.file}:{v.line} [{v.kind}] {v.message}\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="skill_vault_write_safety_audit",
        description=(
            "SVW-1 audit: every skills/**/SKILL.md (+ agents/**, scripts/**) "
            "directive mutating a shared-aggregate vault JSON file must route "
            "through `vault_edit append` / `vault_edit rewrite` or carry a "
            "sanctioned exemption marker."
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Repo root (defaults to parent of the scripts/lib/ dir containing this script)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    # this file lives at <root>/scripts/lib/skill_vault_write_safety_audit.py;
    # the repo root is three parents up.
    root = (
        args.root.resolve()
        if args.root is not None
        else Path(__file__).resolve().parent.parent.parent
    )
    if not (root / "skills").exists():
        sys.stderr.write(f"skills/ directory not found at {root}\n")
        return 2

    result = audit_root(root)
    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
