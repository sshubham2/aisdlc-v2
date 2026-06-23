"""Two-phase claim: --reserve (soft HOLD) + same-owner upgrade + cross-owner refusal
(slice-027 / SC-053 / ADR-016).

THE GAP this pins: /slice claimed only at Step 5.1, so a picked candidate stayed `candidate`
(pickable) through the whole interactive define window -> a parallel /slice re-listed it. The fix
adds a soft RESERVE on pick (status=reserved, NO slice number, NO counter bump) that a parallel
session sees as in-flight, then a CONFIRM that upgrades a SAME-OWNER reservation to spiking (mint
slice-NNN once); a reservation held by a DIFFERENT git identity is refused (no IDOR -- M2).

Fails on HEAD (no --reserve mode; the claim path refuses a reserved candidate as 'not pickable').
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]  # the worktree root
_CLAIM = _REPO / "skills" / "slice" / "scripts" / "claim_candidate.py"

A = ("Owner A", "a@test")
B = ("Owner B", "b@test")


def _write_candidates(vault: Path, cands: list, counters: dict | None = None) -> None:
    doc = {"_schema": "aisdlc/slice-candidates@1", "project": "t",
           "candidates": cands, "pick_log": []}
    if counters is not None:
        doc["counters"] = counters
    (vault / "candidates.json").write_text(json.dumps(doc), encoding="utf-8")


def _cand(cid="SC-001", status="candidate", **extra) -> dict:
    base = {"id": cid, "title": cid.lower(), "status": status, "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None, "history": []}
    base.update(extra)
    return base


def _id_env(tmp_path: Path, name, email) -> dict:
    """A git identity isolated from the dev machine: GIT_CONFIG_NOSYSTEM=1 + a private
    GIT_CONFIG_GLOBAL. name/email None => unset identity (claim_candidate must exit 1)."""
    cfg = tmp_path / f"gitconfig_{name or 'none'}".replace(" ", "_")
    lines = ["[user]"]
    if name is not None:
        lines.append(f"\tname = {name}")
    if email is not None:
        lines.append(f"\temail = {email}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}


def _run(vault: Path, args: list, env_extra: dict, repo_root: Path):
    child = dict(os.environ)
    child.pop("AI_SDLC_VAULT_ROOT", None)
    child.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_CLAIM), "--vault", str(vault),
         "--repo-root", str(repo_root), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=child)


def _load(vault):
    return json.loads((vault / "candidates.json").read_text(encoding="utf-8"))


def _rec(vault, cid):
    return next(c for c in _load(vault)["candidates"] if c["id"] == cid)


def test_reserve_sets_fields_no_slice_no_counter(tmp_path):
    """AC1: --reserve sets reserved+claimed_by+started_at+progress+history, NO slice, NO counter."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")], counters={"slice": 0, "sc": 1})
    cp = _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, *A), tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-001")
    assert r["status"] == "reserved"
    assert r["claimed_by"] == {"git_user": "Owner A", "git_email": "a@test"}
    assert r["started_at"]
    assert r["progress"] == "reserved"
    assert r["slice"] is None
    assert any(e.get("event") == "reserved" for e in r["history"])
    assert _load(vault)["counters"]["slice"] == 0, "reserve must NOT bump counters.slice"


def test_reserve_unset_identity_exits_1(tmp_path):
    """AC1: an unset git identity fails visibly (exit 1) and mutates nothing."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    cp = _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, None, None), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    assert _rec(vault, "SC-001")["status"] == "candidate"


def test_reserve_idempotent_same_owner_one_history(tmp_path):
    """AC1/M4: a same-owner re-reserve is a no-op success with NO duplicate 'reserved' event."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    env = _id_env(tmp_path, *A)
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], env, tmp_path).returncode == 0
    cp2 = _run(vault, ["--candidate", "SC-001", "--reserve"], env, tmp_path)
    assert cp2.returncode == 0, cp2.stderr
    events = [e for e in _rec(vault, "SC-001")["history"] if e.get("event") == "reserved"]
    assert len(events) == 1, f"duplicate reserved history events: {events}"


def test_reserve_cross_owner_refused(tmp_path):
    """M4: a reservation held by a different identity refuses a re-reserve (exit 1)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, *A), tmp_path).returncode == 0
    cp = _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 1
    assert _rec(vault, "SC-001")["claimed_by"]["git_email"] == "a@test", "owner unchanged"


def test_reserve_not_reservable_status(tmp_path):
    """M4: reserving a non-pickable (e.g. spiking) candidate exits 1."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001", status="spiking",
                                    claimed_by={"git_user": "Owner A", "git_email": "a@test"})])
    cp = _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, *A), tmp_path)
    assert cp.returncode == 1
    assert "reservable" in (cp.stdout + cp.stderr).lower()


def test_same_owner_upgrade_mints_once(tmp_path):
    """AC3: A reserves then claims -> spiking + slice minted + counters.slice bumped ONCE."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")], counters={"slice": 0, "sc": 1})
    env = _id_env(tmp_path, *A)
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], env, tmp_path).returncode == 0
    cp = _run(vault, ["--candidate", "SC-001", "--name", "do-thing", "--json"], env, tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-001")
    assert r["status"] == "spiking"
    assert r["slice"] == "slice-001"
    assert _load(vault)["counters"]["slice"] == 1, "exactly one slice minted on upgrade"


def test_cross_owner_upgrade_refused_no_counter_bump(tmp_path):
    """M2/AC3: B cannot upgrade A's reservation -> exit 1, status unchanged, counter NOT bumped (no IDOR)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")], counters={"slice": 0, "sc": 1})
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], _id_env(tmp_path, *A), tmp_path).returncode == 0
    cp = _run(vault, ["--candidate", "SC-001", "--name", "steal-it", "--json"], _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    r = _rec(vault, "SC-001")
    assert r["status"] == "reserved", "a refused cross-owner upgrade must not change status"
    assert r["claimed_by"]["git_email"] == "a@test", "owner unchanged"
    assert r["slice"] is None
    assert _load(vault)["counters"]["slice"] == 0, "refusal must short-circuit BEFORE next_id"


def test_reserve_then_release_reverts(tmp_path):
    """must-not-defer: an abandoned reservation stays releasable by the owner."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    env = _id_env(tmp_path, *A)
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], env, tmp_path).returncode == 0
    cp = _run(vault, ["--candidate", "SC-001", "--release"], env, tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-001")
    assert r["status"] == "candidate"
    assert r["claimed_by"] is None


def test_reserve_xor_release_usage_exit_2(tmp_path):
    """m1: --reserve and --release are mutually exclusive (argparse usage exit 2)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    cp = _run(vault, ["--candidate", "SC-001", "--reserve", "--release"], _id_env(tmp_path, *A), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    # m2 (code-review): must fail via the mutual-exclusion GROUP, not 'unrecognized arguments'
    # (which is green-on-HEAD where --reserve does not exist) -- assert the actual argparse cause.
    assert "not allowed with" in cp.stderr.lower(), cp.stderr


def test_release_of_reservation_records_reservation_reason(tmp_path):
    """code-review m1: releasing a reserved soft HOLD records a reservation-abandon reason, NOT the
    post-claim 'worktree create failed' saga-compensation text (audit-trail accuracy)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    env = _id_env(tmp_path, *A)
    assert _run(vault, ["--candidate", "SC-001", "--reserve"], env, tmp_path).returncode == 0
    assert _run(vault, ["--candidate", "SC-001", "--release"], env, tmp_path).returncode == 0
    released = [e for e in _rec(vault, "SC-001")["history"] if e.get("event") == "released"]
    assert released, "a released event must be recorded"
    reason = released[-1].get("reason", "").lower()
    assert "reservation" in reason, f"a reserved-release reason should name the reservation, got: {reason!r}"
    assert "worktree create failed" not in reason, "must not record the post-claim saga reason for a reservation"


def test_reserved_status_documented_and_open_set():
    """AC4: `reserved` is documented in the schema-by-example AND candidates[].status stays an
    open-set in artifact_lint (ENUM_EXCLUSIONS, NOT KNOWN_ENUMS) -- so adding it rejects no
    previously-valid candidate, and a future KNOWN_ENUMS pin cannot silently regress it."""
    sys.path.insert(0, str(_REPO))
    from scripts.lib import artifact_lint
    key = ("slice-candidates", "candidates[].status")
    assert key in artifact_lint.ENUM_EXCLUSIONS, "candidates[].status must stay open-set (reserved-safe)"
    assert key not in artifact_lint.KNOWN_ENUMS, "candidates[].status must NOT be a closed enum"
    doc = json.loads((_REPO / "schemas" / "slice-candidates.example.json").read_text(encoding="utf-8"))
    assert "reserved" in doc["_fields"]["status"], "schema _fields.status must document 'reserved'"
    assert "reserved" in doc["_doc"], "schema _doc must list 'reserved'"


def test_slice_skill_wires_reserve():
    """AC5: /slice SKILL.md wires reserve-on-pick (a --reserve step after Step 1) and --release on
    the pre-claim abandon paths."""
    skill = (_REPO / "skills" / "slice" / "SKILL.md").read_text(encoding="utf-8")
    assert "--reserve" in skill, "SKILL.md must invoke claim_candidate --reserve on pick"
    assert "reserve the pick" in skill.lower(), "a reserve-on-pick step must follow Step 1"
    assert skill.count("--release") >= 2, "--release must be wired on the pre-claim abandon paths"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
