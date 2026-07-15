"""
Bug (SC-141): slice resolution does NOT check claim ownership. A slice-targeting skill,
driven under a git identity DIFFERENT from the identity that CLAIMED the slice, resolves
that slice confidently and is free to write vault artifacts into it.

Live event (slice-011): a forked /code-review resolved slice-010 and wrote slice-010's
code-review.json + milestone.json + a gate-log row while the session was working slice-011.
The candidate schema already carries `claimed_by {git_user, git_email}` (minted in-lock by
claim_candidate.py), but NOTHING reads it back at resolution time -- the only control is
procedural ("drive slice-targeting skills with an explicit slice id"), i.e. it lives in the
operator's head and is enforceable by exactly one head. With multi-dev as a real target
(second machine, second clock, no shared working memory) that control is void.

Reproduction faithfulness: the vault carries a live slice (slice-101-alpha) whose candidate
was claimed by identity A (a@example.com). The caller's repo is a git worktree on that
slice's own branch (slice/101-alpha) but configured with identity B (b@example.com) -- the
two-humans-one-shared-vault shape. Branch-first resolution (active_slice.py) matches and
returns the slice with full confidence; no code anywhere compares the caller's git identity
against the candidate's claimed_by.

Expected: resolution of a slice owned by ANOTHER git identity must REFUSE -- fail-visibly,
          naming the owner and naming the explicit override -- BEFORE any vault artifact
          byte is written. The MECHANISM is the fix slice's job (a refusal sentinel, a
          distinct non-zero exit, a dedicated ownership gate); this test only asserts
          "not a silent, confident hand-back of a foreign-owned slice", at BOTH the library
          API and the CLI shape the skills actually invoke.
Actual:   resolve_active_slice() returns slice-101 (source="git-branch") as if certain, and
          the CLI exits 0 and prints it.

Same-identity resolution MUST stay unchanged -- the third test pins that (no regression).

This test PASSES when slice resolution enforces claim ownership.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Self-contained import bootstrap (robust when the shippability row is run standalone,
# not only under tests/conftest.py): add the plugin root so `from scripts.lib ...` resolves.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.active_slice import resolve_active_slice

OWNER = {"git_user": "Owner A", "git_email": "a@example.com"}
INTRUDER = ("Intruder B", "b@example.com")

CLI = REPO / "scripts" / "lib" / "active_slice.py"


def _make_vault(tmp_path: Path, claimed_by: dict) -> Path:
    """A vault with ONE live slice (slice-101-alpha) whose candidate is claimed by `claimed_by`."""
    vault = tmp_path / "vault"
    d = vault / "slices" / "slice-101-alpha"
    d.mkdir(parents=True)
    (d / "milestone.json").write_text(
        json.dumps({"_schema": "aisdlc/milestone@1", "slice": "slice-101", "stage": "build",
                    "next_action": "/code-review", "at": "2026-07-13T10:00:00Z"}),
        encoding="utf-8",
    )
    (vault / "candidates.json").write_text(
        json.dumps({
            "_schema": "aisdlc/slice-candidates@1",
            "project": "repro",
            "candidates": [{
                "id": "SC-900",
                "title": "alpha",
                "status": "building",
                "progress": "build",
                "slice": "slice-101",
                "claimed_by": claimed_by,
                "started_at": "2026-07-13T09:00:00Z",
            }],
            "pick_log": [],
            "counters": {"slice": 101},
        }),
        encoding="utf-8",
    )
    return vault


def _make_repo(tmp_path: Path, name: str, email: str) -> Path:
    """A git repo on branch slice/101-alpha (unborn HEAD is fine -- active_slice reads
    `symbolic-ref --short HEAD`) configured with the given identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, check=True)
    run("init")
    run("config", "user.name", name)
    run("config", "user.email", email)
    run("symbolic-ref", "HEAD", "refs/heads/slice/101-alpha")
    return repo


def _resolved_slice(info) -> str | None:
    """The slice a caller would ACT on: None when the resolver refused / returned nothing."""
    return info.get("slice") if isinstance(info, dict) else None


def test_library_refuses_slice_claimed_by_another_identity(tmp_path: Path) -> None:
    """resolve_active_slice() must not hand back a slice owned by a DIFFERENT git identity."""
    vault = _make_vault(tmp_path, OWNER)
    repo = _make_repo(tmp_path, *INTRUDER)

    info = resolve_active_slice(vault, repo)

    assert _resolved_slice(info) != "slice-101", (
        "resolve_active_slice handed slice-101 (claimed by Owner A <a@example.com>) to a caller "
        f"running as Intruder B <b@example.com> -- no claim-ownership check. Got: {info!r}"
    )


def test_cli_refuses_slice_claimed_by_another_identity(tmp_path: Path) -> None:
    """The CLI shape the skills actually invoke must FAIL VISIBLY (non-zero) and name the owner."""
    vault = _make_vault(tmp_path, OWNER)
    repo = _make_repo(tmp_path, *INTRUDER)

    cp = subprocess.run(
        [sys.executable, str(CLI), "--vault", str(vault), "--repo-root", str(repo), "--json"],
        capture_output=True, text=True,
    )

    assert cp.returncode != 0, (
        "active_slice CLI exited 0 for a slice claimed by another identity -- a foreign-owned "
        f"slice must be a fail-visible refusal, not a green light. stdout={cp.stdout!r}"
    )
    payload = json.loads(cp.stdout) if cp.stdout.strip() else {}
    assert payload.get("slice") != "slice-101", (
        f"CLI still emitted the foreign-owned slice as resolved: {cp.stdout!r}"
    )
    assert "a@example.com" in (cp.stdout + cp.stderr), (
        "the refusal must NAME the owning identity so the operator knows who to coordinate with; "
        f"stderr={cp.stderr!r}"
    )


def test_same_identity_resolution_is_unchanged(tmp_path: Path) -> None:
    """No regression: the OWNER resolving their OWN slice still gets it (this passes today)."""
    vault = _make_vault(tmp_path, OWNER)
    repo = _make_repo(tmp_path, OWNER["git_user"], OWNER["git_email"])

    info = resolve_active_slice(vault, repo)

    assert _resolved_slice(info) == "slice-101", f"owner lost access to their own slice: {info!r}"
