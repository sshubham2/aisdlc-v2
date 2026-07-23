"""slice_ownership — the ownership DECISION module (slice-069 / ADR-068, ADR-069, ADR-072).

Pins the verdict table, the accept-set, and every degenerate disposition. The load-bearing
property under test is asymmetric and deliberate: **a false REFUSAL of the rightful owner is
catastrophic (it bricks all 10 loop skills); a missed catch merely restores today's behaviour.**
So every ambiguity must resolve toward "do not refuse the owner".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib.slice_ownership import (  # noqa: E402
    EXIT_OWNERSHIP,
    OVERRIDE_ENV,
    check_ownership,
    is_refusal,
    owner_of,
)

OWNER = {"git_user": "Alice Owner", "git_email": "alice@example.com"}


def _vault(tmp_path: Path, candidates: list[dict], *, archived: list[dict] | None = None) -> Path:
    v = tmp_path / "vault"
    (v / "slices").mkdir(parents=True)
    (v / "candidates.json").write_text(
        json.dumps({"_schema": "aisdlc/slice-candidates@1", "candidates": candidates}), encoding="utf-8")
    if archived is not None:
        (v / "archive").mkdir()
        (v / "archive" / "candidates.json").write_text(
            json.dumps({"_schema": "aisdlc/slice-candidates@1", "candidates": archived}), encoding="utf-8")
    return v


def _repo(tmp_path: Path, name: str | None, email: str | None) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, check=True)
    run("init")
    # a repo-local identity that shadows any global one (so the test is hermetic)
    if name is not None:
        run("config", "user.name", name)
    if email is not None:
        run("config", "user.email", email)
    return r


def _cand(cid: str, slice_id: str, claimed_by) -> dict:
    return {"id": cid, "title": "t", "status": "building", "slice": slice_id, "claimed_by": claimed_by}


# ── owner_of: the lookup walks live -> archive, joined on the candidate's `slice` field ──────────

def test_owner_of_finds_a_live_candidate(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    status, owner = owner_of(v, "slice-101")
    assert status == "owner-found"
    assert owner["git_email"] == "alice@example.com"
    assert owner["candidate"] == "SC-900"


def test_owner_of_walks_into_the_archive(tmp_path: Path) -> None:
    """The mark must survive discharge: /commit-slice auto-emits /slice-story AFTER /reflect archived
    the folder, so a live-only lookup would report `unclaimed` on every legitimate post-ship run."""
    v = _vault(tmp_path, [], archived=[_cand("SC-900", "slice-101", OWNER)])
    status, owner = owner_of(v, "slice-101")
    assert status == "owner-found"
    assert owner["git_email"] == "alice@example.com"


def test_owner_of_no_candidate_row(tmp_path: Path) -> None:
    v = _vault(tmp_path, [])
    assert owner_of(v, "slice-101") == ("no-candidate", None)


def test_owner_of_survives_a_malformed_candidates_file(tmp_path: Path) -> None:
    """The resolver is the hottest shared path in the pipeline -- an exception here bricks all 10
    loop skills. A malformed vault degrades to `no-candidate`, never a crash."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "candidates.json").write_text("{ not json", encoding="utf-8")
    assert owner_of(v, "slice-101") == ("no-candidate", None)


# ── the degenerate claimed_by shapes (M5): ALL must ALLOW+WARN. None may refuse; none may raise ──

@pytest.mark.parametrize("claimed_by", [None, {}, {"git_user": "", "git_email": ""},
                                        {"git_user": "A", "git_email": "   "}, "someone", 42, []])
def test_malformed_claimed_by_is_legacy_never_foreign(tmp_path: Path, claimed_by) -> None:
    """An owner email that is blank/absent/wrong-typed matches NO projection -- so a naive
    'refuse when nothing matches' rule would REFUSE EVERYONE, PERMANENTLY, for that slice."""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", claimed_by)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "legacy", res
    assert not is_refusal(res["verdict"])
    assert not res["enforced"]


# ── the verdict table ────────────────────────────────────────────────────────────────────────────

def test_owner_is_allowed(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, OWNER["git_user"], OWNER["git_email"])
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "owner"
    assert not res["enforced"]


def test_owner_match_is_casefolded_and_stripped(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", {"git_user": "Alice", "git_email": " ALICE@Example.COM "})])
    r = _repo(tmp_path, "Alice", "alice@example.com")
    assert check_ownership(v, "slice-101", repo_root=r)["verdict"] == "owner"


def test_foreign_identity_is_refused(tmp_path: Path) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "foreign"
    assert is_refusal(res["verdict"]) and res["enforced"]
    # AC3: the refusal NAMES the owner AND names the override
    assert "alice@example.com" in res["message"]
    assert OVERRIDE_ENV in res["message"]


def test_refusal_message_names_the_real_transfer_verb(tmp_path: Path) -> None:
    """slice-096 AC4 / M4: the take-over remedy in the refusal message must name the real,
    logged, append-only `transfer` verb — NOT the retired 'does not exist yet -- filed follow-up'
    placeholder. (test_slice_ownership.py previously asserted NOTHING about this sentence — the
    wiring_matrix 'already covered' claim was false.)"""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    msg = check_ownership(v, "slice-101", repo_root=r)["message"]
    assert "does not exist yet" not in msg
    assert "filed follow-up" not in msg
    assert "transfer" in msg.lower(), "the message must name the transfer verb as the take-over remedy"


def test_no_candidate_row_allows_with_a_warning(tmp_path: Path) -> None:
    """Every existing resolver fixture builds a vault with NO candidates.json -- this disposition is
    what keeps the whole existing suite green, and it is a DELIBERATE fail-open: there is no
    collision without a recorded owner."""
    v = _vault(tmp_path, [])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "unowned"
    assert not res["enforced"]
    assert res["warning"]


# ── identity dispositions: UNSET and UNREADABLE stay DISTINCT from wrong-owner ────────────────────

def test_identity_unset_refuses_only_when_an_owner_is_on_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nonexistent-global"))  # no global fallback
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "nonexistent-system"))
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, None, None)
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "identity-unset"
    assert is_refusal(res["verdict"]) and res["enforced"]
    assert "user.email" in res["message"]        # names the remedy, not just the problem


def test_identity_unset_with_NO_owner_on_record_allows(tmp_path: Path, monkeypatch) -> None:
    """You only need to know WHO YOU ARE when there is someone to collide WITH."""
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nonexistent-global"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "nonexistent-system"))
    v = _vault(tmp_path, [])
    r = _repo(tmp_path, None, None)
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "unowned"
    assert not res["enforced"]


# ── the accept-set: env / git-var projections must not FALSE-REFUSE the owner (m3) ───────────────

def test_env_author_email_is_an_accepting_projection(tmp_path: Path, monkeypatch) -> None:
    """`git config` reads config FILES only and is blind to GIT_AUTHOR_EMAIL -- which is exactly the
    kind of environment a CI runner or a forked subagent injects. The accept-set is deliberately
    WIDER than the mint side (claim_candidate reads config only). Do NOT 'harmonize' the two ends."""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")     # config says Bob ...
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")   # ... but git would stamp Alice
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "owner", res


# ── the override is SLICE-SCOPED, never a boolean (ADR-068 property: no ambient authority) ───────

def test_slice_scoped_override_is_honoured_loudly(tmp_path: Path, monkeypatch) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    monkeypatch.setenv(OVERRIDE_ENV, "slice-101")
    res = check_ownership(v, "slice-101", repo_root=r)
    assert res["verdict"] == "overridden"
    assert not res["enforced"]
    assert res["warning"] and "alice@example.com" in res["warning"]


def test_boolean_override_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """A boolean override is ambient authority by another name -- it would be exported once and
    forgotten, re-creating the very ambient authority this gate removes."""
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    assert check_ownership(v, "slice-101", repo_root=r)["verdict"] == "foreign"


def test_override_for_a_DIFFERENT_slice_does_not_leak(tmp_path: Path, monkeypatch) -> None:
    v = _vault(tmp_path, [_cand("SC-900", "slice-101", OWNER)])
    r = _repo(tmp_path, "Bob Other", "bob@example.com")
    monkeypatch.setenv(OVERRIDE_ENV, "slice-999")
    assert check_ownership(v, "slice-101", repo_root=r)["verdict"] == "foreign"


# ── ADR-072: the ARCHIVE arm WARNS, it never REFUSES (the identity-drift false-refusal) ──────────

def test_archive_arm_warns_but_never_enforces(tmp_path: Path) -> None:
    """THE LIVE DEFECT this project actually carries: the real vault holds a STALE owner email for
    the SAME human (an identity that drifted over time), so gating the archive arm would REFUSE the
    rightful owner on his own shipped slices. A terminal slice has no live collision to guard, and
    the archive is BY CONSTRUCTION where identity drift accumulates -- so it WARNS and allows."""
    v = _vault(tmp_path, [], archived=[_cand("SC-001", "slice-001", {"git_user": "Alice Owner",
                                                                     "git_email": "old-address@example.com"})])
    r = _repo(tmp_path, "Alice Owner", "alice@example.com")     # same human, new address
    res = check_ownership(v, "slice-001", repo_root=r, arm="by-id-archive")
    assert res["verdict"] == "foreign"          # the VERDICT is still honest ...
    assert not res["enforced"]                  # ... but it is NOT enforced on a terminal slice
    assert res["warning"] and "old-address@example.com" in res["warning"]


def test_live_arm_with_the_same_drifted_identity_IS_enforced(tmp_path: Path) -> None:
    """The mirror of the above: on a LIVE arm the refusal stands (a real collision is possible)."""
    v = _vault(tmp_path, [_cand("SC-001", "slice-001", {"git_user": "Alice Owner",
                                                        "git_email": "old-address@example.com"})])
    r = _repo(tmp_path, "Alice Owner", "alice@example.com")
    res = check_ownership(v, "slice-001", repo_root=r, arm="vault-scan")
    assert res["verdict"] == "foreign" and res["enforced"]


def test_exit_code_is_5_and_distinct_from_the_ambiguous_4() -> None:
    """3 = the reserved retryable-CAS signal, 4 = AMBIGUOUS (ADR-010). 5 must stay its own code so a
    consumer can tell 'refuse, name the owner' from 'refuse, disambiguate'."""
    assert EXIT_OWNERSHIP == 5
