"""PFS-1 project-frame synthesizer — v2 JSON.

Per **PFS-1** (`methodology-changelog.md` v0.78.0; slice-088; [[ADR-080]];
mints a rule on the review-context axis). Emits an **ephemeral**,
**deterministic**, **tight** project-frame to **stdout** so `/design-slice`
(shift-left, Step 0.5) and both Critic layers (`/critique`,
`/critique-review`) review a slice against the project's *deliberate forward
direction* — not only its static current artifacts. The frame is the input
the already-shipped `agents/critique.md` Dim-7 strategic-direction probe
consumes.

Three required sections, synthesized (selected + compressed + ranked), NOT
concatenated:

  - **Identity**  — what this project is (`concept.json` "What it does" +
                    mode from `triage.json`).
  - **Trajectory**— where it is deliberately heading: deduped active rule
                    FAMILIES (recent `methodology-changelog.md` entry titles —
                    a v1 self-development artifact; the one OPTIONAL source, so
                    its absence is an `INFO` note, NOT a degrade — see ADR-084),
                    pending `candidates.json` candidate names (live statuses
                    only), open `risk-register.json` entries score-ranked with
                    score shown.
  - **Impact**    — this slice's name + one-line intent + whether `design.json`
                    exists yet (degraded to mission-brief-only at Step 0.5).

v2 mapping (the 4 rollouts — see CLAUDE.md):
  - **JSON, not markdown.** Every vault artifact `<vault>/X.md` -> `<vault>/X.json`;
    field access matches the schemas-by-example in `skills/*/examples/`.
  - **External vault.** Reads route through `VAULT_ROOT`
    (`scripts.lib._vault_paths`), the external-store seam — never a hardcoded
    in-repo `architecture/`. That seam is the CLI's ONLY vault source: there is
    no root flag (ADR-083 deleted `--repo-root`, which relocated the vault and
    which all three call sites were passing `.` — pointing the synthesizer at
    the code repo and degrading every frame to blank). To relocate the vault,
    set `AI_SDLC_VAULT_ROOT` (precedence #1 in `_vault_paths`) or call
    `synthesize_frame(vault_root=...)` directly.
  - **`slice-queue.md` is gone.** Pending candidate NAMES come from
    `candidates.json` `candidates[].title`, filtered to LIVE statuses
    (candidate / spiking / active / blocked / deferred).
  - Open risks reuse `risk_register_audit.audit_register()` +
    `filter_and_sort(..., filter_status="open", sort_by="score")` rather than
    re-parsing the register here.

Design decisions (ADR-080), unchanged in v2:
  - A **deterministic tool**, NOT an LLM `/frame` skill — judgment of
    direction-fit stays with the Critic; the tool only assembles evidence.
  - **Ephemeral stdout-only** — nothing tracked is written; regenerated each
    invocation, so it cannot drift.
  - cp1252 stdout safety via `_stdout.reconfigure_stdout_utf8()`
    (UTF8-STDOUT-1) — em-dash-laden extracted text emits safely as UTF-8;
    NO ASCII-fold (the codebase-standard mechanism, consistent with siblings).

Exit codes (binary contract):
  0  frame emitted (clean OR degraded-with-stderr-WARN on a missing REQUIRED
     source)
  2  usage error (missing/invalid required `--slice-dir`, bad `--max-lines`, or
     any unrecognized flag — including a re-introduced `--repo-root`)
  NEVER 1 — frame-synth failure is not a slice-regression class; the frame is
  advisory context to /design-slice + /critique, never a gate.

Usage::

    # Library (preferred; called from skill prose via Bash capture)
    from scripts.lib.project_frame_synth import synthesize_frame
    frame = synthesize_frame(VAULT_ROOT, VAULT_ROOT / "slices" / "slice-NNN-x")
    # (the library entry still takes the vault root directly — it is the
    #  in-process relocation seam the deleted CLI flag never legitimately served)

    # CLI
    python -m scripts.lib.project_frame_synth \\
        --slice-dir <vault>/slices/slice-NNN-x

Rule reference: PFS-1 (slice-088; ADR-080).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

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
from scripts.lib import risk_register_audit as rra
from scripts.lib._vault_paths import VAULT_ROOT  # external-store seam (route vault reads through it)

_MAX_FRAME_LINES = 40

_ATTACK_LENS = (
    "ATTACK-LENS -- use this to find where the slice fights the project's "
    "direction; do NOT nod along"
)
_TRUNCATION_MARKER = "... (frame truncated to budget)"
_NONE = "_(none)_"

# A rule-id entry-title line, e.g. ``**PFS-1 — project-frame ...``,
# ``**TRI-RESOLVE-1 — ...``, or a LETTER-SUFFIXED id ``**PCR-2b — ...`` /
# ``**PCR-2a — ...`` (the codebase actively uses these — changelog v0.77.0/v0.74.0).
# Each segment tolerates a single trailing lowercase letter (`[a-z]?`); the `\b`
# anchor is intentionally OMITTED — with it, the word-boundary between `2` and `b`
# in `PCR-2b` failed, collapsing the capture to `PCR` (no `-<digit>`) so the entry
# was silently dropped from the family scan (slice-088 /code-review M1).
_RULE_TITLE_RE = re.compile(r"^\*\*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+[a-z]?)*)")
# Family = the rule id with its trailing numeric-bearing segment stripped:
# PFS-1 -> PFS, PCR-2b -> PCR, BC-PROJ-9 -> BC-PROJ, TRI-RESOLVE-1 -> TRI-RESOLVE.
_FAMILY_RE = re.compile(r"^([A-Z][A-Z-]*?)-\d")

_MAX_FAMILIES = 6
_MAX_RISKS = 3
_MAX_CANDIDATES = 6

# Bold `-<digit>` tokens that are NOT methodology rule families: decision
# records (ADR-NNN), risk ids (R-N), and diagnose candidates (SC-NNN).
_NON_RULE_FAMILIES = {"ADR", "R", "SC"}

# Live candidate lifecycle states (schemas/slice-candidates.example.json `_fields`):
# a shipped/rejected candidate is MOVED out to <vault>/archive/candidates.json, so
# the live file holds only these. The pending-candidate scan filters to this set,
# matching how v1's slice-queue.md only ever listed pending candidates.
_LIVE_CANDIDATE_STATUSES = frozenset(
    # slice-027: `reserved` is a live, claimed-in-intent soft HOLD (pre-confirm, no slice number
    # minted yet, ADR-016) -- include it so a held candidate still appears in the project-frame backlog.
    {"candidate", "reserved", "spiking", "active", "blocked", "deferred"}
)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_json(path: Path, warn, label: str) -> Any | None:
    """Read + parse a vault JSON artifact. Degrades (warn + None) on a missing
    file OR malformed JSON — the frame is advisory, never a gate, so a broken
    source costs that one section, never the whole frame."""
    text = _read(path)
    if text is None:
        warn(f"{label} missing")
        return None
    try:
        return json.loads(text) if text.strip() else None
    except json.JSONDecodeError as exc:
        warn(f"{label} unparseable ({exc})")
        return None


def _rule_family(rule_id: str) -> str:
    m = _FAMILY_RE.match(rule_id)
    return m.group(1) if m else rule_id


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.search(r"^(.*?\.)(?:\s|$)", text)
    return (m.group(1) if m else text).strip()


def _strip_md_heading(text: str) -> str:
    """A v2 prose-valued JSON field carries its own markdown heading, e.g.
    ``"## What\\nLets small sellers ..."`` (concept.json `what`) or
    ``"## Intent\\nShow which ..."`` (mission-brief.json `intent`). Drop a
    leading ``#``-heading line so the first SENTENCE is the body, not the
    heading — mirrors v1's `_section_body` which returned the text AFTER the
    `## <heading>` line."""
    lines = text.lstrip("\n").splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _identity(vault_root: Path, warn) -> list[str]:
    concept = _load_json(vault_root / "concept.json", warn, "concept.json")
    triage = _load_json(vault_root / "triage.json", warn, "triage.json")

    one_liner = "(concept.json unavailable)"
    if isinstance(concept, dict):
        what = concept.get("what")
        if isinstance(what, str) and what.strip():
            body = _strip_md_heading(what)
            if body:
                one_liner = _first_sentence(body)

    mode = "?"
    if isinstance(triage, dict):
        raw_mode = triage.get("mode")
        if isinstance(raw_mode, str) and raw_mode.strip():
            mode = raw_mode.strip().capitalize()

    return ["## Identity", f"{one_liner} (Mode: {mode})"]


_VERSION_HEADER_RE = re.compile(r"^##\s+v\d")


def _active_families(vault_root: Path, note) -> list[str]:
    """Deduped active rule FAMILIES from the recent changelog.

    `methodology-changelog.md` is a v1 SELF-DEVELOPMENT artifact — it tracks the
    pipeline's own rule evolution, NOT a normal v2 project deliverable, so a v2
    project vault legitimately never has one. It is therefore the ONE source that
    opts OUT of the degrade channel (ADR-084): its absence yields an empty list
    and an `INFO` note, NOT a `project-frame degraded` WARN. Every other source
    takes `warn` — degrade is the DEFAULT, and a new source added here degrades
    unless someone deliberately opts it out at this call site.

    Kept as a `.md` read (NOT converted to `.json`) because, when it DOES exist,
    it is the same H2-structured markdown self-development log v1 parsed;
    converting it is out of scope for this tool (this port only consumes it).

    Synthesis, not concatenation: takes the FIRST real rule-id entry-title per
    `## vN.N.N` version block only, maps each to its family, dedups preserving
    recency order, caps at _MAX_FAMILIES. A bold token is a rule-id only if it
    has a `-<digit>` component (`_FAMILY_RE` matches) — this filters body words
    like SKILL / SOFT / Mechanism."""
    changelog = _read(vault_root / "methodology-changelog.md")
    if not changelog:
        note("methodology-changelog.md absent (optional self-dev artifact)")
        return []
    lines = changelog.splitlines()
    n = len(lines)
    families: list[str] = []
    i = 0
    while i < n and len(families) < _MAX_FAMILIES:
        if not _VERSION_HEADER_RE.match(lines[i]):
            i += 1
            continue
        # Scan this entry for its first valid rule-id title line.
        j = i + 1
        while j < n and not _VERSION_HEADER_RE.match(lines[j]):
            m = _RULE_TITLE_RE.match(lines[j].strip())
            if m and _FAMILY_RE.match(m.group(1)):
                fam = _rule_family(m.group(1))
                if fam in _NON_RULE_FAMILIES:
                    break  # decision/risk/candidate ref, not a rule — skip entry
                if fam not in families:
                    families.append(fam)
                break  # only the entry title — not the body
            j += 1
        i = j
    return families


def _pending_candidates(vault_root: Path, warn) -> list[str]:
    """Pending candidate NAMES from `candidates.json` (v2; replaces v1's
    `slice-queue.md` `### <name>` scan). Uses `candidates[].title`, filtered to
    LIVE statuses — the live file already excludes shipped/rejected (those are
    moved to archive/candidates.json), but filtering on the documented live-status
    set is defensive + matches the schema `_fields` contract."""
    data = _load_json(vault_root / "candidates.json", warn, "candidates.json")
    if not isinstance(data, dict):
        return []
    raw = data.get("candidates")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "")).strip().lower()
        if status and status not in _LIVE_CANDIDATE_STATUSES:
            continue
        title = entry.get("title")
        if isinstance(title, str) and title.strip():
            names.append(title.strip())
        if len(names) >= _MAX_CANDIDATES:
            break
    return names


def _open_risks(vault_root: Path, warn) -> list[str]:
    """Open risks, score-ranked, via the shared `risk_register_audit` API (v2;
    replaces v1's coupling to the private `_parse_risks`). `audit_register`
    silently returns an empty result on a missing register, so a degrade WARN is
    emitted explicitly here for parity with the sibling sections."""
    path = vault_root / "risk-register.json"
    if not path.exists():
        warn("risk-register.json missing")
        return []
    try:
        result = rra.audit_register(path)
        view = rra.filter_and_sort(
            result, filter_status="open", sort_by="score", top=_MAX_RISKS
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the frame
        warn(f"risk-register audit failed ({exc})")
        return []
    out: list[str] = []
    for r in view:
        title = r.title if len(r.title) <= 48 else r.title[:45] + "..."
        out.append(f"{r.id} (score {r.score}) {title}")
    return out


def _trajectory(vault_root: Path, warn, note) -> list[str]:
    families = _active_families(vault_root, note)
    candidates = _pending_candidates(vault_root, warn)
    risks = _open_risks(vault_root, warn)
    return [
        "## Trajectory",
        "- Active rule families: " + (", ".join(families) if families else _NONE),
        "- Pending candidates: " + (", ".join(candidates) if candidates else _NONE),
        "- Open risks (by score): " + ("; ".join(risks) if risks else _NONE),
    ]


def _impact(slice_dir: Path, warn) -> list[str]:
    name = slice_dir.name
    brief = _load_json(slice_dir / "mission-brief.json", warn, "mission-brief.json")
    intent = "(mission-brief.json unavailable)"
    if isinstance(brief, dict):
        raw_intent = brief.get("intent")
        if isinstance(raw_intent, str) and raw_intent.strip():
            body = _strip_md_heading(raw_intent)
            if body:
                intent = _first_sentence(body)
    design_exists = (slice_dir / "design.json").is_file()
    design_line = (
        "present" if design_exists else "mission-brief-only (design.json not yet written)"
    )
    return [
        "## Impact",
        f"- Slice: {name}",
        f"- Intent: {intent}",
        f"- Design: {design_line}",
    ]


def synthesize_frame(
    vault_root: Path, slice_dir: Path, max_lines: int = _MAX_FRAME_LINES
) -> str:
    """Synthesize the ephemeral project-frame as a markdown string.

    `vault_root` is the directory holding the vault JSON artifacts
    (`concept.json`, `triage.json`, `candidates.json`, `risk-register.json`).
    In normal use this is `VAULT_ROOT` (the external store), which is what the
    CLI always passes — this arg is the in-process relocation seam (fixtures,
    explicit override). It was named `repo_root` until ADR-083, a name that
    invited `.` — the code repo — from every caller and blanked the frame.
    v1 composed `repo_root / VAULT_ROOT / X` because v1's `VAULT_ROOT` was a
    RELATIVE in-tree `architecture/`; in v2 `VAULT_ROOT` is an ABSOLUTE external
    path, so that composition collapses — the vault root is passed directly.

    Deterministic: no wall-clock, no randomness, stable ordering. Degrades
    section-by-section (with a stderr WARN) on a missing REQUIRED source rather
    than raising; the one OPTIONAL source's absence is an `INFO` note on a
    separate stderr line (ADR-084). Truncates to `max_lines` with a marker."""
    warnings: list[str] = []
    notes: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)

    def note(msg: str) -> None:
        notes.append(msg)

    vault_root = Path(vault_root)
    slice_dir = Path(slice_dir)

    lines: list[str] = [_ATTACK_LENS, ""]
    lines += _identity(vault_root, warn) + [""]
    lines += _trajectory(vault_root, warn, note) + [""]
    lines += _impact(slice_dir, warn)

    if warnings:
        # WARN to stderr (visibility per the error model); never to the frame.
        print(
            "WARN: project-frame degraded — " + "; ".join(warnings),
            file=sys.stderr,
        )
    if notes:
        # ADR-084: a benign absence is NOT a degrade. Separate, un-prefixed line
        # so the degrade channel keeps its discriminating power — a WARN that
        # fires on every healthy vault trains its own suppression, which is
        # exactly how `--repo-root .` survived at three call sites. Still stderr,
        # never stdout: the frame is piped verbatim into the designer/Critic
        # prompt and counts against _MAX_FRAME_LINES.
        print(
            "INFO: project-frame -- " + "; ".join(notes),
            file=sys.stderr,
        )

    # Clamp a degenerate budget (slice-088 /code-review M2): max_lines < 1 would
    # make `max_lines - 1` a negative slice index, dropping only the tail instead
    # of truncating — a 0/negative budget silently emitted a near-full frame,
    # the opposite of the tight-frame must-not-defer. A budget of 1 -> marker only.
    if max_lines < 1:
        max_lines = 1
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [_TRUNCATION_MARKER]
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """The CLI surface, as an introspectable object.

    Extracted from `main` so the SHIP-081 guard can derive the set of valid flags
    from the LIVE parser (`_actions[].option_strings`) instead of a hand-frozen
    list that drifts. There is deliberately NO vault-root flag and NO hidden
    tombstone for the deleted `--repo-root`: a `help=argparse.SUPPRESS` action is
    still registered in `_actions`, so a tombstone would report as a live flag
    under introspection and green-light the exact regression the guard exists to
    catch. Plain absence makes argparse reject the spelling at rc=2, everywhere —
    including call sites outside any scanner's corpus."""
    parser = argparse.ArgumentParser(
        prog="project_frame_synth",
        description="Emit the ephemeral PFS-1 project-frame to stdout (v2 JSON).",
    )
    parser.add_argument(
        "--slice-dir",
        required=True,
        help="active slice folder, e.g. <vault>/slices/slice-NNN-<name>",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=_MAX_FRAME_LINES,
        help=f"line budget (default: {_MAX_FRAME_LINES})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = _build_parser()
    args = parser.parse_args(argv)  # argparse exits 2 on missing --slice-dir
    if args.max_lines < 1:
        parser.error("--max-lines must be >= 1")  # exit 2 (usage); never a budget breach

    # The vault resolves ONLY through the _vault_paths seam (ADR-083).
    frame = synthesize_frame(
        Path(VAULT_ROOT), Path(args.slice_dir), max_lines=args.max_lines
    )
    print(frame)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
