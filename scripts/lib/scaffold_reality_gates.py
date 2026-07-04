"""scaffold_reality_gates.py -- the idempotent reality-gates manifest scaffolder
(slice-062 / SC-095 / ADR-059). Mirrors scaffold_test_first_plan.py's same-directory
atomic write.

Creates <repo-root>/.aisdlc/reality-gates.json with an EMPTY declared set so a project
that declares nothing is a structural no-op. Invoked by /setup's default (non---check)
path. Idempotent: it NEVER overwrites an existing manifest (a populated one keeps its
declared gates untouched); a second run is a byte-identical no-op.

m3: the scaffolder mkdir's the .aisdlc/ parent FIRST -- on a greenfield repo the dir does
not exist, so tempfile.mkstemp(dir=<repo>/.aisdlc) would raise FileNotFoundError on exactly
the case this tool serves.

M-add-2: <repo-root>/.aisdlc/reality-gates.json is a COMMITTED, repo-tracked file -- it is
DISTINCT from the external per-machine ~/.aisdlc/<slug>-<hash>/ vault (same basename,
opposite intent). Because a maintainer could reason 'the .aisdlc vault must not be
committed' and gitignore it -- which would silently stop the security-gate manifest
travelling to CI -- the scaffolder CHECKS whether the path is git-ignored and, if so, emits
an explicit `!.aisdlc/reality-gates.json` force-include hint (best-effort; git absence is
tolerated).

Exit codes: 0 = created or no-op (success) · 2 = usage / IO error (fail-visible).
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
_EMPTY_SKELETON = {
    "_schema": "aisdlc/reality-gates@1",
    "gates": {"security": [], "nfr": [], "ops": []},
}


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` atomically, via a temp file in ``path``'s
    OWN directory (same-filesystem -> os.replace is genuinely atomic; mirrors
    scaffold_test_first_plan._atomic_write_json). The parent dir MUST already exist (m3)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


def scaffold(repo_root: str | Path) -> dict:
    """Ensure <repo_root>/.aisdlc/reality-gates.json exists with an empty declared set.

    Returns {action: 'created'|'noop', path, gitignore_hint}. Never clobbers a populated
    manifest. mkdir's the .aisdlc/ parent first (m3)."""
    repo_root = Path(repo_root)
    d = repo_root / _AISDLC_DIRNAME
    manifest = d / _MANIFEST_NAME

    if manifest.is_file():
        return {"action": "noop", "path": str(manifest),
                "gitignore_hint": _gitignore_hint(repo_root, manifest)}

    d.mkdir(parents=True, exist_ok=True)   # m3: greenfield has no .aisdlc/ dir
    _atomic_write_json(manifest, _EMPTY_SKELETON)
    return {"action": "created", "path": str(manifest),
            "gitignore_hint": _gitignore_hint(repo_root, manifest)}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="scaffold_reality_gates",
        description="Idempotently scaffold <repo-root>/.aisdlc/reality-gates.json with an empty "
                    "declared set (slice-062 / ADR-059). Never clobbers a populated manifest.")
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
        sys.stdout.write(f"scaffold_reality_gates: {summary['action']} — {summary['path']}\n")
    if summary.get("gitignore_hint"):
        sys.stderr.write("WARN: " + summary["gitignore_hint"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
