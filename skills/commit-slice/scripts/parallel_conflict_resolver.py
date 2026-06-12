"""PCR parallel-conflict-resolution — v2 (shrunk: code-conflict-only).

Per PCR-1 (ADR-069; slice-076) the v1 resolver diagnosed + classified + (for the
SOFT class) AUTO-RESOLVED parallel-slice merge conflicts that surfaced at
``/commit-slice --merge`` Step 5b sub-step 2.5 (``git rebase``). v1's auto-resolve
targeted exactly two IN-TREE vault files — ``architecture/slice-queue.md`` and
``architecture/shippability.md`` (its ``SOFT`` + ``VAULT_CLAIM`` classes, keyed on
``_SOFT_FILE_SET``).

**v2 SHRINK — the SOFT / VAULT_CLAIM machinery is DEAD and removed.** In v2 the
vault is ONE shared EXTERNAL store (``VAULT_ROOT`` resolves OUTSIDE the code repo's
git work tree), and ``slice-queue.md`` / ``shippability.md`` no longer exist (they
became ``candidates.json`` / ``shippability.json`` — external JSON, not git-tracked).
An external/untracked file has NO git rebase stage, so it can NEVER appear in a
``git rebase`` conflict. Therefore:

  REMOVED (all DEAD in v2):
    - the ``SOFT`` + ``VAULT_CLAIM`` + ``MIXED`` ConflictClass members and every
      class-specific auto-resolution path: ``_regen_slice_queue``,
      ``_merge_shippability``, the claim-overlay / claim-diff / claim-merge helpers,
      ``resolve_vault_claim_conflict`` + the timestamp-winner + clock-skew guards,
      ``_verify_soft_equivalence`` + the equivalence/skew/decode STOP-audit appenders,
      and the per-stage ``git show :2:`` / ``git show :3:`` reads.
    - the ``_SOFT_FILE_SET`` constant (no vault file is ever in a rebase).
    - the ``from tools._vault_git import vault_is_external`` import and its sole user
      ``_retire_if_vault_external`` — ``_vault_git`` is DROPPED in v2 and the vault is
      ALWAYS external, so the "retire when external" guard is unconditionally true and
      thus vacuous; the whole code path it guarded is gone.
    - ``--classify`` stays (cheap, still meaningful: HARD vs UNKNOWN) but no longer has
      SOFT/VAULT_CLAIM/MIXED outcomes to report.

  REMOVED in v2.19.2 (3.10 — the degenerate ``--resolve-soft`` surface):
    - the ``--resolve-soft`` CLI flag + its library entry points
      ``resolve_soft_conflict`` / ``resolve_hard_conflict`` + the ``ResolutionResult``
      dataclass. In v2 there was never anything to auto-resolve (the vault is external,
      so every rebase conflict is CODE/HARD), so the entire "resolve" verb was dead
      surface that kept a v1 model alive. ``/commit-slice`` now routes off
      ``--classify`` (HARD → PCR-2b hand-resolve gate; UNKNOWN → SOAD-1 block) directly.

  KEPT (the code-conflict path — the only live case in v2):
    - HARD classification: ANY unmerged path in a v2 rebase is a CODE conflict
      (src / tests / SKILL.md / etc.) → STOP, gate-on-hand-resolve. ``skills/
      commit-slice/SKILL.md`` sub-step 2.5 drives: ``--classify`` (HARD → gate) →
      resolve markers → ``--verify-resolution`` → ``code-review`` agent → TRI-RESOLVE-1
      user gate → ``git rebase --continue`` → ``--record-hard-resolution``. PCR never
      auto-merges.
    - the CLI surface ``/commit-slice`` invokes: ``--diagnose`` / ``--classify`` /
      ``--verify-resolution`` / ``--record-hard-resolution``, ``--json``,
      ``--repo-root``, ``--verdict``, ``--disposition``.
    - the append-only audit log, now JSON at ``<vault>/parallel-conflict-resolution-
      log.json`` (an ``{"entries": [...]}`` object), appended via the SVW-1 locked
      read-modify-write primitive ``scripts.lib._vault_write.safe_mutate_text`` — the
      external-vault-safe replacement for v1's in-tree markdown ``O_APPEND`` log.

Conflict-marker detection (``_verify_resolution_clean``) is unchanged: git-native
``--diff-filter=U`` + a line-anchored ``<<<<<<<``/``>>>>>>>``/``|||||||`` opener scan
(NOT a ``=======`` scan — that false-STOPs on Markdown setext H1 underlines, and HARD
U-files can be markdown).

Read-only except the best-effort audit-log append. Every ``git`` subprocess passes
``encoding="utf-8"`` (slice-090 cp1252 discipline) EXCEPT the byte-mode sites that
decode explicitly.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import enum
import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout, _vault_write
from scripts.lib._vault_paths import VAULT_ROOT


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_AUDIT_LOG_PATH: Path = VAULT_ROOT / "parallel-conflict-resolution-log.json"
"""Append-only audit log — a JSON ``{"entries": [...]}`` object in the shared
EXTERNAL vault (lazy-created on first append). v2 replaces v1's in-tree markdown
``O_APPEND`` log: VAULT_ROOT is now absolute/external, so the log lives with the
vault, and appends route through the SVW-1 locked read-modify-write primitive
(``safe_mutate_text``) which serializes concurrent writers into the JSON array."""

_CONFLICT_MARKER_OPENER_RE = re.compile(r"(?m)^[ +-]?(?:<{7,}|>{7,}|\|{7,})(?:\s|$)")
r"""PCR-2b HARD-resolution leftover-marker detector (unchanged from v1).

Keys on the line-anchored ``<<<<<<<`` opener / ``>>>>>>>`` closer / ``|||||||``
diff3 base-separator (runs of 7+), which have NO legitimate Markdown/source
analog — DELIBERATELY NOT ``=======`` (the merge separator), because a 7+ ``=``
run also matches a Markdown setext H1 underline, and HARD U-files CAN be markdown
(SKILL.md), so a ``=======`` scan would false-STOP correct resolutions. The
optional leading ``[ +-]`` consumes the single ``git diff`` body column so a
marker on an added/context line is detected; diff meta lines do not match.
"""


# ---------------------------------------------------------------------------
# ConflictClass enum — SHRUNK: SOFT / VAULT_CLAIM / MIXED removed (dead in v2)
# ---------------------------------------------------------------------------

class ConflictClass(enum.Enum):
    """v2 taxonomy. In v2 every rebase conflict is a CODE conflict (the vault is
    external/untracked → never in a rebase), so only HARD and UNKNOWN remain.
    SOFT / VAULT_CLAIM / MIXED were the in-tree-vault classes — all removed."""

    HARD = "HARD"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class ConcernedSlice:
    """One active slice whose mission-brief references a U-file."""

    slice_id: str
    blast_radius: tuple[str, ...]
    mission_brief_link: str
    last_commit_iso: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ConflictDiagnostic:
    """Structured diagnostic of an in-progress rebase conflict (v2: code-only)."""

    u_files: tuple[str, ...]
    concerned_slices: dict[str, tuple[ConcernedSlice, ...]]


# ---------------------------------------------------------------------------
# Public library API
# ---------------------------------------------------------------------------

def diagnose_conflict(repo_root: Path) -> ConflictDiagnostic:
    """Diagnose the in-progress rebase conflict state at repo_root.

    Reads ``git status --porcelain`` to enumerate U-files; derives concerned-slice
    metadata from the shared external vault's ``slices/<active>/mission-brief.json``
    (a U-file path appearing anywhere in the brief JSON → that slice is concerned).

    Returns an empty-shape ConflictDiagnostic if no rebase is in progress (no
    U-files); ``classify_conflict`` then returns UNKNOWN.

    v2 change: the v1 claim-history extraction (``git show :2:/:3:`` of the in-tree
    ``slice-queue.md``) is GONE — that file is external JSON in v2 and can never be
    a rebase stage.
    """
    u_files = _extract_u_files(repo_root)
    concerned_slices: dict[str, tuple[ConcernedSlice, ...]] = {}
    for u_file in u_files:
        concerned_slices[u_file] = _derive_concerned_slices(repo_root, u_file)
    return ConflictDiagnostic(u_files=u_files, concerned_slices=concerned_slices)


def classify_conflict(diag: ConflictDiagnostic) -> ConflictClass:
    """Classify the conflict.

    v2: every unmerged path in a rebase is a CODE conflict (the vault is external
    and untracked, so no vault file is ever a rebase stage) → HARD. No U-files
    (rebase not in progress / unparseable state) → UNKNOWN, fail-closed.
    """
    if not diag.u_files:
        return ConflictClass.UNKNOWN
    return ConflictClass.HARD


def _verify_resolution_clean(repo_root: Path) -> tuple[bool, str | None]:
    """Verify a hand-resolved HARD rebase has no unresolved conflicts (unchanged
    from v1 PCR-2b).

    Two git-native + line-anchored-opener checks (NOT a ``=======`` scan, NOT
    ``git diff --cached --check`` — both inherit git's ``>=7-=`` heuristic that
    false-STOPs on Markdown setext H1 underlines; HARD U-files ARE markdown):

      (a) ``git diff --name-only --diff-filter=U`` MUST be empty (git itself
          considers every path resolved/staged), and
      (b) no line-anchored ``<<<<<<<`` opener / ``>>>>>>>`` closer / ``|||||||``
          base-separator survives in the staged resolution (``git diff --cached``).

    Conservative fail-closed: a doc legitimately starting a line with the marker
    runs (rare) false-STOPs, which is SAFE (refuses to continue → re-resolve/abort;
    NEVER silently continues). Returns ``(clean, reason)``; ``reason`` is ``None``
    when clean.
    """
    # (a) any path still unmerged?
    try:
        u_proc = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return (False, f"git-state-unreadable: {exc!r}")
    if u_proc.stdout.strip():
        unmerged = ", ".join(u_proc.stdout.split())
        return (False, f"paths-still-unmerged: {unmerged}")

    # (b) leftover line-anchored opener/closer in the staged resolution?
    try:
        d_proc = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return (False, f"git-state-unreadable: {exc!r}")
    if _CONFLICT_MARKER_OPENER_RE.search(d_proc.stdout):
        return (False, "unresolved-markers-present")

    return (True, None)


def _record_hard_resolution(
    repo_root: Path,
    diag: ConflictDiagnostic,
    verdict: str,
    disposition: str,
) -> None:
    """Append a HARD-conflict audit entry to ``<vault>/parallel-conflict-resolution-
    log.json`` (best-effort; caller catches).

    Invoked by ``--record-hard-resolution`` after a ratified TRI-RESOLVE-1 apply,
    still mid-rebase (so ``diag`` carries the U-files + concerned slices),
    immediately before ``git rebase --continue``.

    v2 change: the v1 markdown ``## Hard-conflict resolution`` section + in-tree
    ``O_APPEND`` is replaced by a JSON entry pushed into the external log's
    ``entries`` array via the SVW-1 locked read-modify-write primitive
    ``safe_mutate_text`` (serializes concurrent appenders; never lost-update).
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    head_sha = _head_sha(repo_root)

    concerned_ids = sorted({
        cs.slice_id
        for u in diag.u_files
        for cs in diag.concerned_slices.get(u, ())
    })
    entry = {
        "type": "hard-conflict-resolution",
        "timestamp": timestamp,
        "repo_head_sha_pre_resolution": head_sha,
        "u_files_resolved": list(diag.u_files),
        "concerned_slices": concerned_ids,
        "resolution_mechanism": (
            "gate-on-hand-resolve (PCR-2b) — hand-resolved + code-review agent + "
            "TRI-RESOLVE-1 user gate"
        ),
        "code_review_verdict": verdict,
        "tri_resolve_1_disposition": disposition,
    }
    _append_audit_entry(repo_root, entry)


def _append_audit_entry(repo_root: Path, entry: dict) -> None:
    """Push one entry into the external audit log's ``entries`` array under the
    SVW-1 sidecar lock (``safe_mutate_text`` — locked read-modify-write). Lazy-
    creates ``{"_schema": ..., "entries": []}`` on first append. ``repo_root`` is
    accepted for signature symmetry but VAULT_ROOT is absolute/external so the log
    path is repo-independent."""
    def _mutate(current: str) -> str:
        if current.strip():
            try:
                data = json.loads(current)
                if not isinstance(data, dict):
                    data = {}
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
        data.setdefault("_schema", "aisdlc/parallel-conflict-resolution-log@1")
        entries = data.get("entries")
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        data["entries"] = entries
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    _vault_write.safe_mutate_text(_AUDIT_LOG_PATH, _mutate)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _head_sha(repo_root: Path) -> str:
    try:
        head_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return head_proc.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "(unavailable)"


def _extract_u_files(repo_root: Path) -> tuple[str, ...]:
    """Extract U-prefixed file paths from ``git status --porcelain``.

    Returns forward-slash strings (NOT Path objects). git status --porcelain emits
    forward-slash on all OSes. Returns empty tuple on subprocess failure (no rebase
    in progress, git unavailable) — caller's classify_conflict returns UNKNOWN.

    A rename-with-conflict (``ORIG -> NEW``) escalates to UNKNOWN (returns ()) per
    APED-1 loud-malformed — semantics unclear, manual resolution required.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ()

    u_files: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        # Unmerged states per `git status` porcelain v1: DD, AU, UD, UA, DU, AA, UU.
        x, y = line[0], line[1]
        if x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
            path = line[3:].strip()
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if " -> " in path:
                print(
                    f"parallel-conflict-resolver: rename-with-conflict on "
                    f"{path!r} — escalating to UNKNOWN per APED-1 loud-malformed "
                    f"(semantics unclear; manual resolution required)",
                    file=sys.stderr,
                )
                return ()
            u_files.append(path)
    return tuple(u_files)


def _derive_concerned_slices(
    repo_root: Path, u_file: str
) -> tuple[ConcernedSlice, ...]:
    """Map a U-file to its concerned active slices via mission-brief scan.

    Walks the SHARED external vault's ``slices/slice-*-*/`` (active slices; NOT
    ``archive/``). For each active slice, reads ``mission-brief.json`` and treats it
    as a concerned slice if the U-file path string appears anywhere in the brief
    JSON text (typically a ``files_changed`` / blast-radius entry).

    Last-commit ISO is best-effort via ``git log -1 --format=%cI`` on the slice's
    branch (``slice/NNN-<name>``); None on failure.

    v2 changes from v1: reads ``mission-brief.json`` (was ``.md``) from the external
    ``VAULT_ROOT`` (was in-tree ``architecture/``).
    """
    slices_dir = repo_root / VAULT_ROOT / "slices"
    if not slices_dir.is_dir():
        return ()

    matches: list[ConcernedSlice] = []
    for entry in sorted(slices_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if not name.startswith("slice-") or name == "archive":
            continue
        brief_path = entry / "mission-brief.json"
        if not brief_path.is_file():
            continue
        try:
            brief_text = brief_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if u_file not in brief_text:
            continue
        last_commit_iso = _last_commit_iso_for_slice(repo_root, name)
        matches.append(ConcernedSlice(
            slice_id=name,
            blast_radius=(u_file,),
            mission_brief_link=f"slices/{name}/mission-brief.json",
            last_commit_iso=last_commit_iso,
        ))
    return tuple(matches)


def _last_commit_iso_for_slice(repo_root: Path, slice_dir_name: str) -> str | None:
    """Best-effort last-commit ISO for a slice's branch (``slice/NNN-<name>``).
    None on failure. Slice dir ``slice-NNN-<name>`` -> branch ``slice/NNN-<name>``
    (ADR-046)."""
    if not slice_dir_name.startswith("slice-"):
        return None
    branch = "slice/" + slice_dir_name[len("slice-"):]
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cI", branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    out = proc.stdout.strip()
    return out or None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _to_jsonable(obj):
    """Convert dataclasses / enums / tuples to JSON-serializable dicts."""
    if isinstance(obj, ConflictClass):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    """CLI: --diagnose | --classify | --verify-resolution | --record-hard-resolution."""
    _stdout.reconfigure_stdout_utf8()

    parser = argparse.ArgumentParser(
        prog="parallel_conflict_resolver",
        description="PCR parallel-conflict resolver — v2 code-conflict-only (ADR-069/075).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diagnose", action="store_true", help="Emit ConflictDiagnostic")
    mode.add_argument("--classify", action="store_true", help="Emit ConflictClass (HARD|UNKNOWN)")
    mode.add_argument(
        "--verify-resolution",
        action="store_true",
        help="PCR-2b: verify a hand-resolved HARD rebase has no unresolved conflicts",
    )
    mode.add_argument(
        "--record-hard-resolution",
        action="store_true",
        help="PCR-2b: append a HARD-conflict audit entry (post-ratification, pre-continue)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repo root (default: cwd)")
    parser.add_argument("--verdict", default=None, help="code-review verdict (--record-hard-resolution)")
    parser.add_argument("--disposition", default=None, help="TRI-RESOLVE-1 disposition (--record-hard-resolution)")

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    diag = diagnose_conflict(repo_root)

    if args.diagnose:
        if args.json:
            payload = {"action": "DIAGNOSE", "diagnostic": _to_jsonable(diag)}
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f"U-files: {list(diag.u_files)}")
            for u in diag.u_files:
                concerned = diag.concerned_slices.get(u, ())
                print(f"  {u}: {len(concerned)} concerned slice(s)")
                for cs in concerned:
                    print(
                        f"    - {cs.slice_id} (last-commit: {cs.last_commit_iso}; "
                        f"mission-brief: {cs.mission_brief_link})"
                    )
        return 0

    if args.classify:
        cls = classify_conflict(diag)
        if args.json:
            print(json.dumps({"action": "CLASSIFY", "conflict_class": cls.value}, indent=2, ensure_ascii=False))
        else:
            print(f"ConflictClass: {cls.value}")
        return 0

    if args.verify_resolution:
        clean, reason = _verify_resolution_clean(repo_root)
        action = "CLEAN" if clean else "STOP"
        if args.json:
            print(json.dumps({"action": action, "reason": reason}, indent=2, ensure_ascii=False))
        else:
            print(f"Action: {action}" + (f" ({reason})" if reason else ""))
        # exit 1 ONLY on unreadable git state (UNKNOWN-equivalent fail-closed);
        # a clean CLEAN or a coherent STOP is exit 0.
        if not clean and reason and reason.startswith("git-state-unreadable"):
            return 1
        return 0

    if args.record_hard_resolution:
        verdict = args.verdict or "(unspecified)"
        disposition = args.disposition or "(unspecified)"
        recorded = True
        err: str | None = None
        try:
            _record_hard_resolution(repo_root, diag, verdict, disposition)
        except Exception as exc:  # noqa: BLE001 - best-effort by design
            recorded = False
            err = repr(exc)
            print(
                f"parallel-conflict-resolver: HARD audit append failed "
                f"(non-blocking): {err}",
                file=sys.stderr,
            )
        action = "RECORDED" if recorded else "RECORD-FAILED"
        if args.json:
            print(json.dumps({"action": action, "reason": err}, indent=2, ensure_ascii=False))
        else:
            print(f"Action: {action}")
        return 0

    # argparse's mutually_exclusive_group(required=True) prevents reaching here.
    return 2


if __name__ == "__main__":
    sys.exit(main())
