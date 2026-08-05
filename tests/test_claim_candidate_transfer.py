"""claim_candidate.py `transfer` verb — ownership TRANSFER of a claimed slice (slice-096 / SC-146 /
ADR-120 -> ADR-122).

THE GAP this pins: a claimed in-flight slice (dev A goes on leave) could only be taken over via the
standing global AI_SDLC_ALLOW_FOREIGN_SLICE suppression the advisory guard (ADR-068) warns against.
`transfer` is the durable, single-object, attributed, append-only substitute: it re-mints ONLY
`claimed_by` for the target candidate in one SVW-1 in-lock read-modify-write, appends an append-only
`pick_log` + candidate-`history` `transferred` memorial, sets `data['updated']`, and leaves
status/progress/slice/started_at + counters byte-identical. Open to ANY identified caller (anonymous
refused), NOT current-owner-only (ADR-122): a third-party transfer proceeds with a LOUD owner-naming
warning.

Fails on HEAD (no `transfer` verb; the positional cannot parse the --candidate-required parser).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]  # the worktree root
_CLAIM = _REPO / "skills" / "slice" / "scripts" / "claim_candidate.py"

A = ("Owner A", "a@test")   # the on-leave current owner
B = ("Owner B", "b@test")   # the take-over performer (third party)
Y = ("Owner Y", "y@test")   # an arbitrary identified caller
Z = ("Owner Z", "z@test")   # the transfer target (--to)


def _write_candidates(vault: Path, cands: list, counters: dict | None = None) -> None:
    doc = {"_schema": "aisdlc/slice-candidates@1", "project": "t",
           "candidates": cands, "pick_log": []}
    if counters is not None:
        doc["counters"] = counters
    (vault / "candidates.json").write_text(json.dumps(doc), encoding="utf-8")


def _cand(cid="SC-146", status="candidate", **extra) -> dict:
    base = {"id": cid, "title": cid.lower(), "status": status, "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None, "history": []}
    base.update(extra)
    return base


def _claimed(cid="SC-146", slice_id="slice-096", owner=A, status="spiking",
             progress="spike") -> dict:
    """A candidate already claimed by `owner` (the case reserve/claim/release all refuse)."""
    return _cand(cid, status=status, slice=slice_id, progress=progress,
                 claimed_by={"git_user": owner[0], "git_email": owner[1]},
                 started_at="2026-01-01T00:00:00Z",
                 history=[{"event": "picked", "by": "slice", "at": "2026-01-01T00:00:00Z",
                           "ref": slice_id}])


def _id_env(tmp_path: Path, name, email) -> dict:
    """A hermetic git identity: GIT_CONFIG_NOSYSTEM=1 + a private GIT_CONFIG_GLOBAL.
    name/email None => unset identity (transfer must exit 1)."""
    cfg = tmp_path / f"gitconfig_{name or 'none'}".replace(" ", "_")
    lines = ["[user]"]
    if name is not None:
        lines.append(f"\tname = {name}")
    if email is not None:
        lines.append(f"\temail = {email}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"}


def _run_transfer(vault: Path, args: list, env_extra: dict, repo_root: Path):
    """Invoke with `transfer` as the leading positional (top-of-main intercept, M2)."""
    child = dict(os.environ)
    child.pop("AI_SDLC_VAULT_ROOT", None)
    child.pop("AI_SDLC_ALLOW_FOREIGN_SLICE", None)  # AC3: prove no override is needed
    child.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_CLAIM), "transfer", "--vault", str(vault),
         "--repo-root", str(repo_root), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=child)


def _run_plain(vault: Path, args: list, env_extra: dict, repo_root: Path):
    """Invoke WITHOUT the transfer keyword (reserve/claim/release path — M2 characterization)."""
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


def _raw(vault) -> bytes:
    return (vault / "candidates.json").read_bytes()


# ── AC1: the re-mint mechanics ────────────────────────────────────────────────────────────────────

def test_transfer_remints_claimed_by_and_appends_pick_log(tmp_path):
    """AC1: transfer --slice re-mints claimed_by, appends one pick_log transfer entry, prior entries
    intact; status/progress/slice/started_at unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)], counters={"slice": 96, "sc": 150})
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-146")
    assert r["claimed_by"] == {"git_user": "Owner Z", "git_email": "z@test"}
    assert r["status"] == "spiking" and r["progress"] == "spike"
    assert r["slice"] == "slice-096"
    assert r["started_at"] == "2026-01-01T00:00:00Z"
    plog = _load(vault)["pick_log"]
    assert len(plog) == 1 and plog[-1]["event"] == "transferred"
    hist = [e for e in r["history"] if e.get("event") == "transferred"]
    assert len(hist) == 1
    # prior history preserved
    assert any(e.get("event") == "picked" for e in r["history"])


def test_transfer_by_candidate_id(tmp_path):
    """AC1: --candidate SC-NNN (unique id) is an equivalent key to --slice."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)], counters={"slice": 96, "sc": 150})
    cp = _run_transfer(vault, ["--candidate", "SC-146", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert _rec(vault, "SC-146")["claimed_by"]["git_email"] == "z@test"


# ── M5: the exact success delta + refusal byte-compare (characterization) ──────────────────────────

def test_transfer_success_delta_is_exact(tmp_path):
    """M5: the ONLY changes are {claimed_by, pick_log+1, history+1, updated}; counters + every
    other candidate field byte-identical (no accidental counter bump / id_allocator call)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)], counters={"slice": 96, "sc": 150})
    before = _load(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    after = _load(vault)

    assert after["counters"] == before["counters"], "counters must be byte-identical (no mint)"
    rb, ra = before["candidates"][0], after["candidates"][0]
    for k in ("id", "title", "status", "progress", "slice", "started_at"):
        assert ra[k] == rb[k], f"{k} must be unchanged"
    assert ra["claimed_by"] == {"git_user": "Owner Z", "git_email": "z@test"}
    assert ra["history"][:-1] == rb["history"], "prior history entries intact"
    assert ra["history"][-1]["event"] == "transferred"
    assert after["pick_log"][:-1] == before["pick_log"], "prior pick_log entries intact"
    assert after["pick_log"][-1]["event"] == "transferred"
    assert after["updated"] != before.get("updated")
    # nothing else at top level changed
    for k in set(before) | set(after):
        if k in ("candidates", "pick_log", "updated"):
            continue
        assert after.get(k) == before.get(k), f"top-level {k} changed unexpectedly"


def test_transfer_unknown_slice_zero_write(tmp_path):
    """AC2: an unknown slice refuses (exit 1) and candidates.json is BYTE-unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-999", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    assert _raw(vault) == raw, "refusal must not write"


def test_transfer_unset_identity_zero_write(tmp_path):
    """AC2: an unset caller git identity refuses (exit 1), byte-unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, None, None), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    assert _raw(vault) == raw


def test_transfer_empty_to_zero_write(tmp_path):
    """AC2: empty --to is a usage refusal (exit 2), byte-unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", ""],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert _raw(vault) == raw


import pytest  # noqa: E402


@pytest.mark.parametrize("bad", ["foo@", "<a@b>", "a@b.com c@d.com", "no-at-sign",
                                 "   ", "Na@me <b@test>", "@test", "a@"])
def test_transfer_malformed_to_usage_exit_2(tmp_path, bad):
    """M1/AC2: the strict --to grammar rejects the adversarial battery with a usage refusal
    (exit 2), byte-unchanged. NEVER email.utils.parseaddr (which never fails)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", bad],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (bad, cp.stdout, cp.stderr)
    assert _raw(vault) == raw, f"refusal for {bad!r} must not write"


def test_transfer_not_currently_claimed_refuses(tmp_path):
    """contract: an un-owned candidate is 'use claim, not transfer' (exit 1), byte-unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-146", status="candidate")])  # claimed_by None
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--candidate", "SC-146", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    assert _raw(vault) == raw


# ── AC3: succeeds on an ALREADY-CLAIMED candidate with NO override ─────────────────────────────────

def test_transfer_already_claimed_no_override(tmp_path):
    """AC3: caller Y (neither owner nor target) transfers A's LIVE claim to Z with NO
    AI_SDLC_ALLOW_FOREIGN_SLICE — the exact case reserve/claim/release refuse."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)], counters={"slice": 96, "sc": 150})
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *Y), tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert _rec(vault, "SC-146")["claimed_by"] == {"git_user": "Owner Z", "git_email": "z@test"}


def test_transfer_third_party_warns_owner_naming(tmp_path):
    """ADR-122: a third-party transfer (caller != current owner) proceeds but emits a LOUD
    owner-naming warning naming the prior owner AND the performer."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    err = cp.stderr.lower()
    assert "a@test" in err, "warning must name the prior owner"
    assert "b@test" in err, "warning must name the performer"


# ── M-add-1: the WRITE-side slice multi-match uniqueness guard (R-5 RED vault) ─────────────────────

def test_transfer_slice_multimatch_refuses_ambiguous(tmp_path):
    """M-add-1: >1 LIVE candidate carrying the same slice -> REFUSE fail-visible (exit 1), echo
    both candidate ids, never first-match-write. Byte-unchanged."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A),
                              _claimed("SC-200", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 1, (cp.stdout, cp.stderr)
    out = (cp.stdout + cp.stderr)
    assert "SC-146" in out and "SC-200" in out, "must echo both colliding ids"
    assert "--candidate" in out, "must suggest disambiguating with --candidate"
    assert _raw(vault) == raw, "ambiguous refusal must not write"


def test_transfer_candidate_disambiguates_multimatch(tmp_path):
    """M-add-1: --candidate keys by unique id, so it resolves the R-5 collision deterministically;
    the sibling colliding candidate is untouched."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A),
                              _claimed("SC-200", "slice-096", A)])
    cp = _run_transfer(vault, ["--candidate", "SC-146", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert _rec(vault, "SC-146")["claimed_by"]["git_email"] == "z@test"
    assert _rec(vault, "SC-200")["claimed_by"]["git_email"] == "a@test", "sibling untouched"


# ── M-add-2: rescue a RESERVED (slice==None) candidate via --candidate ─────────────────────────────

def test_transfer_rescues_reserved_via_candidate(tmp_path):
    """M-add-2: a reserved candidate (slice==None, owner set) is unrescuable via --slice; --candidate
    reassigns it. status/slice stay reserved/None; only claimed_by moves."""
    vault = tmp_path / "v"; vault.mkdir()
    reserved = _cand("SC-146", status="reserved", progress="reserved",
                     claimed_by={"git_user": A[0], "git_email": A[1]},
                     started_at="2026-01-01T00:00:00Z",
                     history=[{"event": "reserved", "by": "slice", "at": "2026-01-01T00:00:00Z"}])
    _write_candidates(vault, [reserved])
    cp = _run_transfer(vault, ["--candidate", "SC-146", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-146")
    assert r["claimed_by"] == {"git_user": "Owner Z", "git_email": "z@test"}
    assert r["status"] == "reserved" and r["slice"] is None


# ── m4: --slice must be the exact zero-padded 3-digit form ─────────────────────────────────────────

def test_transfer_unpadded_slice_usage_exit_2(tmp_path):
    """m4: --slice slice-96 (unpadded) is a usage refusal echoing the expected 3-digit shape,
    byte-unchanged — never a silent false 'unknown slice' first-match."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    raw = _raw(vault)
    cp = _run_transfer(vault, ["--slice", "slice-96", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert "slice-" in (cp.stdout + cp.stderr).lower()
    assert _raw(vault) == raw


# ── m2: from/to/by are all {git_user, git_email} dicts ────────────────────────────────────────────

def test_transfer_entry_shapes_are_dicts(tmp_path):
    """m2: pick_log + history transfer entries carry from/to/by as {git_user, git_email} dicts
    (match claimed_by), not the sibling joined-string picked_by."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    entry = _load(vault)["pick_log"][-1]
    for fld in ("from", "to", "by"):
        assert isinstance(entry[fld], dict), f"{fld} must be a dict"
        assert set(entry[fld]) == {"git_user", "git_email"}, f"{fld} keys"
    assert entry["from"]["git_email"] == "a@test"
    assert entry["to"]["git_email"] == "z@test"
    assert entry["by"]["git_email"] == "b@test"
    assert entry["candidate"] == "SC-146" and entry["slice"] == "slice-096"
    hist = [e for e in _rec(vault, "SC-146")["history"] if e.get("event") == "transferred"][-1]
    for fld in ("from", "to", "by"):
        assert isinstance(hist[fld], dict)


# ── m3: data['updated'] is set ────────────────────────────────────────────────────────────────────

def test_transfer_sets_top_level_updated(tmp_path):
    """m3: transfer sets data['updated']=ts (consistent with claim/reserve/release siblings)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    assert "updated" not in _load(vault)
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert _load(vault).get("updated"), "data['updated'] must be set on a successful transfer"


# ── BC-PROJ-3: a non-ASCII --to name round-trips as literal UTF-8 (ensure_ascii=False) ────────────

def test_transfer_preserves_non_ascii_owner_name(tmp_path):
    """BC-PROJ-3: a transfer to a unicode-named owner must persist the LITERAL char (ensure_ascii=
    False), not a \\uXXXX escape, and round-trip through json.load intact."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    cp = _run_transfer(vault, ["--slice", "slice-096", "--to", "Ünïcödé Nàme <u@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 0, cp.stderr
    text = (vault / "candidates.json").read_text(encoding="utf-8")
    assert "Ünïcödé Nàme" in text, "literal non-ASCII name must be written verbatim"
    assert "\\u00fc" not in text, "must NOT escape non-ASCII (ensure_ascii=False)"
    assert _rec(vault, "SC-146")["claimed_by"]["git_user"] == "Ünïcödé Nàme"


# ── usage: --slice/--candidate are one-of-required ────────────────────────────────────────────────

def test_transfer_neither_key_usage_exit_2(tmp_path):
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    cp = _run_transfer(vault, ["--to", "Owner Z <z@test>"], _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)


@pytest.mark.parametrize("key,val", [("--slice", ""), ("--slice", "   "),
                                     ("--candidate", ""), ("--candidate", "  ")])
def test_transfer_empty_key_value_usage_exit_2_zero_write(tmp_path, key, val):
    """CR1 (code-review): an empty/whitespace --slice/--candidate value must be a usage refusal
    (exit 2), NOT collapse to None and re-mint a reserved (slice==None) candidate. The regression
    is a WRONG-TARGET write, so it must be byte-unchanged. A reserved candidate is the trap victim."""
    vault = tmp_path / "v"; vault.mkdir()
    reserved = _cand("SC-146", status="reserved", progress="reserved",
                     claimed_by={"git_user": A[0], "git_email": A[1]},
                     started_at="2026-01-01T00:00:00Z",
                     history=[{"event": "reserved", "by": "slice", "at": "2026-01-01T00:00:00Z"}])
    _write_candidates(vault, [reserved])
    raw = _raw(vault)
    cp = _run_transfer(vault, [key, val, "--to", "Owner Z <z@test>"],
                       _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (key, val, cp.stdout, cp.stderr)
    assert _raw(vault) == raw, "an empty-key refusal must not re-mint any candidate"


def test_transfer_both_keys_usage_exit_2(tmp_path):
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_claimed("SC-146", "slice-096", A)])
    cp = _run_transfer(vault, ["--slice", "slice-096", "--candidate", "SC-146",
                               "--to", "Owner Z <z@test>"], _id_env(tmp_path, *B), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)


# ── M2: reserve/claim/release parsing is byte-identical (the transfer intercept is transparent) ────

def test_plain_claim_still_works(tmp_path):
    """M2 characterization: the top-of-main transfer intercept leaves the normal claim path
    unchanged (a fresh candidate still claims + mints slice-NNN)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")], counters={"slice": 0, "sc": 1})
    cp = _run_plain(vault, ["--candidate", "SC-001", "--name", "do-thing", "--json"],
                    _id_env(tmp_path, *A), tmp_path)
    assert cp.returncode == 0, cp.stderr
    r = _rec(vault, "SC-001")
    assert r["status"] == "spiking" and r["slice"] == "slice-001"


def test_plain_missing_candidate_still_usage_exit_2(tmp_path):
    """M2: the non-transfer parser still requires --candidate (required=True unchanged)."""
    vault = tmp_path / "v"; vault.mkdir()
    _write_candidates(vault, [_cand("SC-001")])
    cp = _run_plain(vault, ["--name", "do-thing"], _id_env(tmp_path, *A), tmp_path)
    assert cp.returncode == 2, (cp.stdout, cp.stderr)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
