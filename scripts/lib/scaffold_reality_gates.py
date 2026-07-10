"""scaffold_reality_gates.py -- the reality-gates manifest scaffolder + frame-conditional
security-gate seeder (slice-062 / SC-095 / ADR-059; extended slice-067 / SC-097 / ADR-065).
Mirrors scaffold_test_first_plan.py's same-directory atomic write.

Ensures <repo-root>/.aisdlc/reality-gates.json exists (empty skeleton on a fresh repo) and,
on a PYTHON project frame, id-keyed MERGES the deterministic security gates into gates.security[]
and VENDORS the guard to <repo-root>/.aisdlc/gates/py_security_gate.py so the committed manifest
command stays portable (no plugin path). Invoked by /setup's default (non---check) path.

Idempotency contract (slice-067 refines slice-062's 'never overwrites'):
  * The MANIFEST is an ID-KEYED idempotent MERGE. A gate whose `id` is absent is appended; an
    EXISTING gate (incl. a user-customized command) is PRESERVED byte-for-byte -- never clobbered.
    The manifest is rewritten ONLY when the merge actually changes it; an unchanged re-run is a
    byte-identical no-op (AC5).
  * The VENDORED GUARD is plugin code, NOT user-editable (M4): it is re-vendored when the plugin
    guard's GUARD_VERSION/content differs from the repo copy (the ONE exception to preserve-existing,
    so a security fix to the guard propagates). Users customize reality-gates.json commands, never
    the guard. An unchanged guard is a byte-identical no-op.

Frame detection (spike A2, bounded + deterministic):
  * SOURCE surface = a pruned *.py walk finds >=1 Python file (excludes vendored/build trees) -> seed `bandit`.
  * DEPS   surface = a requirements*.txt file is present (the target pip-audit -r can audit) -> seed `pip-audit`.
    (pyproject/lockfile-only auditing is a documented follow-up; the guard fails-VISIBLE INCOMPLETE if a
    deps gate is declared but no requirements file exists at runtime.)
  A repo with NEITHER surface gets NO security gate -> the runner is a structural no-op (AC3).

m3: mkdir's the .aisdlc/ parent FIRST (a greenfield repo has no dir). M-add-2: the repo-tracked
.aisdlc/reality-gates.json is DISTINCT from the external per-machine ~/.aisdlc vault; the scaffolder
warns with a force-include hint if the path is git-ignored (else the manifest never reaches CI).

Exit codes: 0 = created / updated / no-op (success) · 2 = usage / IO error (fail-visible).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_REPO = Path(__file__).resolve().parents[2]  # scripts/lib/X.py -> plugin root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

_AISDLC_DIRNAME = ".aisdlc"
_MANIFEST_NAME = "reality-gates.json"
_GATES_DIRNAME = "gates"
_VENDORED_GUARD_NAME = "py_security_gate.py"
_GUARD_SOURCE = Path(__file__).resolve().parent / "security_gate.py"  # plugin source of truth

_EMPTY_SKELETON = {
    "_schema": "aisdlc/reality-gates@1",
    "gates": {"security": [], "nfr": [], "ops": []},
}

# The frame-conditional security gate specs. `command` is repo-relative + `python`-anchored so the
# committed manifest is portable to CI/teammates (verification_core._normalize_interp maps the bare
# `python` to the live interpreter; the guard runs whatever python raw CI provides). ADR-065(b).
_SECURITY_GATES = {
    "bandit": {
        "id": "bandit",
        "command": f"python {_AISDLC_DIRNAME}/{_GATES_DIRNAME}/{_VENDORED_GUARD_NAME} --tool bandit",
        "description": "Python SAST -- fail-closed on a HIGH finding (vendored deterministic guard, ADR-065)",
    },
    "pip-audit": {
        "id": "pip-audit",
        "command": f"python {_AISDLC_DIRNAME}/{_GATES_DIRNAME}/{_VENDORED_GUARD_NAME} --tool pip-audit",
        "description": "dependency CVEs -- fail-closed on a known-vulnerable requirement (ADR-065)",
    },
}

# Dirs pruned from the bounded source walk (mirror the guard's scan excludes so detection and
# scanning agree). Names, matched against each directory basename.
_PRUNE_DIRS = frozenset({
    ".venv", "venv", "env", ".env", "node_modules", "build", "dist",
    ".git", ".hg", ".svn", ".tox", ".eggs", "__pycache__", ".mypy_cache",
    ".pytest_cache", _AISDLC_DIRNAME,
})


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically via a temp file in ``path``'s OWN directory
    (same-filesystem -> os.replace is genuinely atomic). The parent dir MUST already exist (m3)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _manifest_text(data: dict) -> str:
    """The canonical, deterministic serialization -- so an unchanged merge is byte-identical (AC5)."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _gitignore_hint(repo_root: Path, manifest: Path) -> str | None:
    """Best-effort: if the manifest path is git-ignored in this repo, return a force-include
    hint (M-add-2). Tolerates a non-git dir / absent git (returns None)."""
    try:
        rel = manifest.relative_to(repo_root)
    except ValueError:
        rel = manifest
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "-q", str(rel)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return None
    if cp.returncode == 0:  # 0 = the path IS ignored
        return (f"{_AISDLC_DIRNAME}/{_MANIFEST_NAME} is git-IGNORED -- the reality-gates manifest "
                f"must be committed to reach teammates + CI. Add `!{_AISDLC_DIRNAME}/{_MANIFEST_NAME}` "
                f"to .gitignore (force-include). NOTE: this repo-tracked file is DISTINCT from the "
                f"external ~/{_AISDLC_DIRNAME} vault.")
    return None


def _detect_python_surface(repo_root: Path) -> dict:
    """Return {'source': bool, 'deps': bool} for the repo frame (spike A2, bounded).

    SOURCE = a pruned os.walk finds >=1 *.py (early-exit). DEPS = a requirements*.txt is present."""
    source = False
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if any(f.endswith(".py") for f in filenames):
            source = True
            break
    deps = bool(_find_requirements(repo_root))
    return {"source": source, "deps": deps}


def _find_requirements(repo_root: Path) -> Path | None:
    primary = repo_root / "requirements.txt"
    if primary.is_file():
        return primary
    for cand in sorted(repo_root.glob("requirements*.txt")):
        if cand.is_file():
            return cand
    reqdir = repo_root / "requirements"
    if reqdir.is_dir():
        for cand in sorted(reqdir.glob("*.txt")):
            if cand.is_file():
                return cand
    return None


def _security_ids_for_surface(surface: dict) -> list[str]:
    ids: list[str] = []
    if surface.get("source"):
        ids.append("bandit")
    if surface.get("deps"):
        ids.append("pip-audit")
    return ids


def _merge_security_gates(data: dict, want_ids: list[str]) -> bool:
    """ID-keyed merge: append each wanted security gate whose id is ABSENT; preserve every
    existing entry byte-for-byte. Returns True iff ``data`` was mutated."""
    gates = data.setdefault("gates", {})
    if not isinstance(gates, dict):
        return False
    sec = gates.setdefault("security", [])
    if not isinstance(sec, list):
        return False
    have = {e.get("id") for e in sec if isinstance(e, dict)}
    changed = False
    for gid in want_ids:
        if gid not in have:
            sec.append(dict(_SECURITY_GATES[gid]))  # a copy -- never share the spec object
            changed = True
    return changed


def _security_references_guard(data: dict) -> bool:
    """True if any declared security gate command references the vendored guard file -- so a
    re-run keeps the guard fresh even when frame detection would not re-seed it."""
    sec = ((data.get("gates") or {}).get("security") or [])
    return any(isinstance(e, dict) and _VENDORED_GUARD_NAME in str(e.get("command", "")) for e in sec)


def _vendor_guard(repo_root: Path) -> str:
    """Vendor the plugin guard to <repo>/.aisdlc/gates/py_security_gate.py. Re-vendor when the
    repo copy's content differs from the plugin source (M4: propagate a guard security fix; the
    embedded GUARD_VERSION rides in the content, so a content compare subsumes the version check).
    Returns 'vendored' | 're-vendored' | 'noop'."""
    src_text = _GUARD_SOURCE.read_text(encoding="utf-8")
    gates_dir = repo_root / _AISDLC_DIRNAME / _GATES_DIRNAME
    target = gates_dir / _VENDORED_GUARD_NAME
    if target.is_file():
        if target.read_text(encoding="utf-8") == src_text:
            return "noop"
        gates_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, src_text)
        return "re-vendored"
    gates_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, src_text)
    return "vendored"


def scaffold(repo_root: str | Path) -> dict:
    """Ensure the reality-gates manifest exists and, on a Python frame, seed + vendor the
    security gates. Returns {action, path, gitignore_hint, surface, added, guard}.

    * action: 'created' (manifest newly written) | 'updated' (gates merged in) | 'noop'
      (byte-identical) -- the slice-062 return keys (action/path/gitignore_hint) are PRESERVED
      for setup.py's consumer (M6).
    * added: the list of security gate ids newly seeded this run.
    * guard: the vendor outcome ('vendored'|'re-vendored'|'noop'|'skipped').
    Never clobbers a user-customized manifest entry; mkdir's the .aisdlc/ parent first (m3)."""
    repo_root = Path(repo_root)
    d = repo_root / _AISDLC_DIRNAME
    manifest = d / _MANIFEST_NAME

    existed = manifest.is_file()
    if existed:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                data = json.loads(json.dumps(_EMPTY_SKELETON))
        except (OSError, ValueError):
            # Unreadable/corrupt -> the runner already REFUSES it fail-closed; do not silently
            # overwrite a user's file here. Report noop and let the runner surface the fault.
            return {"action": "noop", "path": str(manifest),
                    "gitignore_hint": _gitignore_hint(repo_root, manifest),
                    "surface": {}, "added": [], "guard": "skipped"}
    else:
        data = json.loads(json.dumps(_EMPTY_SKELETON))  # fresh deep copy

    surface = _detect_python_surface(repo_root)
    want_ids = _security_ids_for_surface(surface)
    have_before = {e.get("id") for e in ((data.get("gates") or {}).get("security") or [])
                   if isinstance(e, dict)}
    changed = _merge_security_gates(data, want_ids)
    added = [gid for gid in want_ids if gid not in have_before]

    # Vendor the guard whenever the merged manifest actually uses it (a fresh seed OR an existing
    # guard-referencing gate) so the committed command always has its guard, kept fresh (M4).
    guard = "skipped"
    if want_ids or _security_references_guard(data):
        d.mkdir(parents=True, exist_ok=True)
        guard = _vendor_guard(repo_root)

    # Write the manifest ONLY when its serialization actually changed (byte-identical no-op else).
    new_text = _manifest_text(data)
    if existed and manifest.read_text(encoding="utf-8") == new_text:
        action = "noop"
    else:
        d.mkdir(parents=True, exist_ok=True)   # m3: greenfield has no .aisdlc/ dir
        _atomic_write_text(manifest, new_text)
        action = "created" if not existed else ("updated" if changed else "noop")

    return {"action": action, "path": str(manifest),
            "gitignore_hint": _gitignore_hint(repo_root, manifest),
            "surface": surface, "added": added, "guard": guard}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="scaffold_reality_gates",
        description="Scaffold <repo-root>/.aisdlc/reality-gates.json and, on a Python frame, id-merge "
                    "the deterministic security gates + vendor the guard (slice-062/067; ADR-059/065). "
                    "Never clobbers a user-customized manifest entry.")
    p.add_argument("repo", help="the project repo root to scaffold the manifest in")
    p.add_argument("--json", action="store_true", help="emit the action summary as JSON only")
    args = p.parse_args(argv)   # argparse exits 2 on a missing repo arg

    repo = Path(args.repo)
    if not repo.is_dir():
        sys.stderr.write(f"scaffold_reality_gates: repo root not found: {repo}\n")
        return 2
    try:
        summary = scaffold(repo)
    except OSError as exc:
        sys.stderr.write(f"scaffold_reality_gates: cannot write manifest: {exc}\n")
        return 2

    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    else:
        added = (", seeded: " + ", ".join(summary["added"])) if summary.get("added") else ""
        guard = f", guard: {summary['guard']}" if summary.get("guard") not in (None, "skipped") else ""
        sys.stdout.write(f"scaffold_reality_gates: {summary['action']} — {summary['path']}{added}{guard}\n")
    if summary.get("gitignore_hint"):
        sys.stderr.write("WARN: " + summary["gitignore_hint"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
