"""AC2's standing net: the OWNER's path through EVERY designation arm, WITH a real candidates.json.

Why this file exists (critique M1 / CC-002d — an unpinned preservation claim is a Major):

The ownership gate does owner-lookup FIRST, and **every pre-existing fixture in
tests/test_active_slice.py builds a vault with NO candidates.json** — so the whole existing suite
stays green by short-circuiting to `unowned` and NEVER EXECUTING THE OWNERSHIP COMPARISON AT ALL.
A green suite is therefore evidence that *unowned* resolution is unchanged, and says nothing
whatsoever about AC2's actual claim ("behaves exactly as today when the caller IS the owner").

The design's own error model calls a FALSE REFUSAL "the catastrophic failure mode, worse than the
bug being fixed" — the resolver is called ~10x per /validate-slice across 10 skills, so a single
false refusal bricks the entire loop. That failure mode had no standing net. This is it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.active_slice import resolve_active_slice, resolve_slice_by_id  # noqa: E402

OWNER = {"git_user": "Alice Owner", "git_email": "alice@example.com"}
FOREIGN = ("Bob Other", "bob@example.com")


def _slice_dir(vault: Path, folder: str, slice_id: str, *, stage: str = "build",
               archived: bool = False) -> Path:
    base = vault / "slices" / ("archive" if archived else "")
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "milestone.json").write_text(json.dumps(
        {"_schema": "aisdlc/milestone@1", "slice": slice_id, "stage": stage,
         "next_action": "/code-review", "at": "2026-07-14T10:00:00Z"}), encoding="utf-8")
    return d


def _vault(tmp_path: Path, rows: list[dict], *, archived_rows: list[dict] | None = None) -> Path:
    v = tmp_path / "vault"
    (v / "slices").mkdir(parents=True)
    (v / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "candidates": rows}), encoding="utf-8")
    if archived_rows is not None:
        (v / "archive").mkdir(parents=True, exist_ok=True)
        (v / "archive" / "candidates.json").write_text(json.dumps(
            {"_schema": "aisdlc/slice-candidates@1", "candidates": archived_rows}), encoding="utf-8")
    return v


def _cand(cid: str, slice_id: str, claimed_by: dict) -> dict:
    return {"id": cid, "title": "t", "status": "building", "slice": slice_id, "claimed_by": claimed_by}


def _repo(tmp_path: Path, name: str, email: str, *, branch: str | None = None) -> Path:
    r = tmp_path / "repo"
    r.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, check=True)
    run("init")
    run("config", "user.name", name)
    run("config", "user.email", email)
    if branch:
        run("symbolic-ref", "HEAD", f"refs/heads/{branch}")
    return r


# ── the OWNER resolves normally through all four arms (the no-regression claim, actually pinned) ──

def test_owner_git_branch_arm(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"], branch="slice/101-alpha")
    info = resolve_active_slice(v, r)
    assert info["slice"] == "slice-101" and info["source"] == "git-branch"
    assert info["ownership"]["verdict"] == "owner"


def test_owner_vault_scan_arm(tmp_path: Path) -> None:
    """The 99%-happy-path arm — one live slice, no slice branch checked out."""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"], branch="main")
    info = resolve_active_slice(v, r)
    assert info["slice"] == "slice-101" and info["source"] == "vault-scan"
    assert info["ownership"]["verdict"] == "owner"


def test_owner_by_id_active_arm(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"])
    info = resolve_slice_by_id(v, "slice-101", r)
    assert info["slice"] == "slice-101" and info["source"] == "by-id-active"
    assert info["ownership"]["verdict"] == "owner"


def test_owner_by_id_archive_arm(tmp_path: Path) -> None:
    """/commit-slice auto-emits /slice-story AFTER /reflect archived the folder."""
    v = _vault(tmp_path, [], archived_rows=[_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101", stage="complete", archived=True)
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"])
    info = resolve_slice_by_id(v, "slice-101", r)
    assert info["slice"] == "slice-101" and info["source"] == "by-id-archive"
    assert info["ownership"]["verdict"] == "owner"


# ── the AMBIGUOUS sentinel must still fire WITH candidates.json present (it is not pre-empted) ────

def test_ambiguous_still_fires_with_candidates_present(tmp_path: Path) -> None:
    """slice-014/ADR-010's guard is UNCHANGED by the ownership gate: >=2 live slices and no branch
    capability is still AMBIGUOUS, and the ownership check must NOT pre-empt it (a caller who owns
    BOTH live slices must still be told to disambiguate, not silently handed one of them)."""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER), _cand("SC-901", "slice-102", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    _slice_dir(v, "slice-102-beta", "slice-102")
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"], branch="main")
    info = resolve_active_slice(v, r)
    assert info["source"] == "ambiguous" and info["slice"] is None
    assert {c["slice"] for c in info["candidates"]} == {"slice-101", "slice-102"}


# ── and the mirror: a FOREIGN caller is refused on each LIVE arm, and only WARNED on the archive ──

def test_foreign_refused_on_each_live_arm(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    r = _repo(tmp_path, *FOREIGN, branch="slice/101-alpha")
    assert resolve_active_slice(v, r)["source"] == "ownership-refused"          # git-branch

    r2 = _repo(tmp_path / "b", *FOREIGN, branch="main")
    assert resolve_active_slice(v, r2)["source"] == "ownership-refused"         # vault-scan
    assert resolve_slice_by_id(v, "slice-101", r2)["source"] == "ownership-refused"   # by-id-active


def test_foreign_on_the_ARCHIVE_arm_resolves_with_a_warning(tmp_path: Path) -> None:
    """ADR-072 — the live defect this project actually carries. A terminal slice has no collision to
    guard, and the archive is where identity drift accumulates: refusing here would block the
    rightful owner on his own shipped slices (the real vault holds a stale email for one human)."""
    v = _vault(tmp_path, [], archived_rows=[_cand("SC-001", "slice-001", OWNER)])
    _slice_dir(v, "slice-001-alpha", "slice-001", stage="complete", archived=True)
    r = _repo(tmp_path, *FOREIGN)
    info = resolve_slice_by_id(v, "slice-001", r)
    assert info["slice"] == "slice-001"                       # RESOLVED, not refused
    assert info["source"] == "by-id-archive"
    own = info["ownership"]
    assert own["verdict"] == "foreign" and own["enforced"] is False
    assert "alice@example.com" in own["warning"]              # but it SPEAKS


# ── the audited read-only opt-out (/pulse must still SHOW a teammate's in-flight slice) ───────────

def test_owner_check_false_is_the_read_only_opt_out(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    _slice_dir(v, "slice-101-alpha", "slice-101")
    r = _repo(tmp_path, *FOREIGN, branch="main")
    info = resolve_active_slice(v, r, owner_check=False)
    assert info["slice"] == "slice-101"                        # orientation still sees it
    assert "ownership" not in info                             # and no verdict was computed
