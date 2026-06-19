"""
Bug (SC-046): vault_edit's managed-kind id-reject keys _MANAGED_KIND on the BASENAME
(target.name), so archive/candidates.json (basename 'candidates.json') collides with the
LIVE managed kind 'sc' and WRONGLY rejects an id-bearing append.

/commit-slice Step 6.2 moves a SHIPPED candidate to archive/candidates.json PRESERVING its
existing id (e.g. SC-022) via `vault_edit append`. Post-slice-019 that append is rejected
("caller supplied a sc id"), breaking the archive-move for every shipped slice. The same
basename collision hits any archive/<managed-file> id-bearing append.

Expected: appending an id-bearing candidate to archive/candidates.json SUCCEEDS and preserves
          the id (archive moves are id-preserving by design).
Actual:   exit != 0, "caller supplied a sc id" -- file untouched.

The real guard must remain intact: a LIVE candidates.json id-bearing append still rejects.

Confirmed live during slice-019's own /commit-slice --merge (worked around with a CAS-rewrite).
Fails on master (the bug is present); passes after the fix (key _MANAGED_KIND on the
vault-relative path, or exempt archive/**).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_VE = _REPO / "scripts" / "lib" / "vault_edit.py"


def _append(vault: Path, rel: str, array: str, obj: dict) -> subprocess.CompletedProcess:
    cf = vault / "_payload.json"
    cf.write_text(json.dumps(obj), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_VE), "--vault", str(vault), "append",
         "--file", rel, "--array", array, "--content-file", str(cf)],
        capture_output=True, text=True)


def test_archive_candidates_append_preserves_id(tmp_path):
    """The archive MOVE-append must succeed and preserve the shipped candidate's id."""
    vault = tmp_path
    (vault / "archive").mkdir()
    (vault / "archive" / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-001", "title": "old"}]}), encoding="utf-8")

    cp = _append(vault, "archive/candidates.json", "candidates",
                 {"id": "SC-022", "title": "shipped", "status": "shipped"})
    assert cp.returncode == 0, (
        f"archive move-append was wrongly rejected (SC-046 basename collision): {cp.stderr}")
    data = json.loads((vault / "archive" / "candidates.json").read_text(encoding="utf-8"))
    assert any(c.get("id") == "SC-022" for c in data["candidates"]), \
        "shipped candidate id not preserved in archive/candidates.json"


def test_live_candidates_append_still_rejects_supplied_id(tmp_path):
    """The real guard is intact: the LIVE candidates.json still rejects a caller-supplied id."""
    vault = tmp_path
    (vault / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-001"}]}), encoding="utf-8")
    cp = _append(vault, "candidates.json", "candidates", {"id": "SC-099", "title": "x"})
    assert cp.returncode != 0, "live candidates.json must still reject a caller-supplied managed id"


# --- M-add-1 (slice-020 /critique-review DR-1): discharge AC3 "any archive/<managed-file>, BOTH legs"
# with TESTS, not just code-reading. _MANAGED_KIND has TWO entries (candidates->sc, shippability->ship),
# and there are TWO write legs (append + update). The repro above covers the sc kind on the append leg;
# these cover the ship kind on the append leg and the managed-guard's update leg on an archive path.

def _update(vault: Path, rel: str, array: str, rec_id: str, set_kv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_VE), "--vault", str(vault), "update",
         "--file", rel, "--array", array, "--id", rec_id, "--set", set_kv],
        capture_output=True, text=True)


def test_archive_shippability_append_preserves_id(tmp_path):
    """The OTHER managed kind: an archive ship-leg move-append must also succeed and preserve the id."""
    vault = tmp_path
    (vault / "archive").mkdir()
    (vault / "archive" / "shippability.json").write_text(
        json.dumps({"rows": [{"id": "SHIP-001"}]}), encoding="utf-8")

    cp = _append(vault, "archive/shippability.json", "rows",
                 {"id": "SHIP-009", "description": "shipped"})
    assert cp.returncode == 0, (
        f"archive ship-leg move-append was wrongly rejected (SC-046 basename collision): {cp.stderr}")
    data = json.loads((vault / "archive" / "shippability.json").read_text(encoding="utf-8"))
    assert any(r.get("id") == "SHIP-009" for r in data["rows"]), \
        "shipped row id not preserved in archive/shippability.json"


def test_live_shippability_append_still_rejects_supplied_id(tmp_path):
    """The guard is intact for the ship kind too: the LIVE shippability.json still rejects a supplied id."""
    vault = tmp_path
    (vault / "shippability.json").write_text(
        json.dumps({"rows": [{"id": "SHIP-001"}]}), encoding="utf-8")
    cp = _append(vault, "shippability.json", "rows", {"id": "SHIP-099", "description": "x"})
    assert cp.returncode != 0, "live shippability.json must still reject a caller-supplied managed id"


def test_update_leg_archive_path_not_blocked(tmp_path):
    """The UPDATE leg's managed-id-reassign guard must NOT fire on an archive path (BC-PROJ-6: both legs)."""
    vault = tmp_path
    (vault / "archive").mkdir()
    (vault / "archive" / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-001", "title": "old"}]}), encoding="utf-8")

    cp = _update(vault, "archive/candidates.json", "candidates", "SC-001", "id=SC-099")
    assert cp.returncode == 0, (
        f"update-leg wrongly blocked a managed id-reassign on an ARCHIVE path (SC-046, both legs): {cp.stderr}")
    data = json.loads((vault / "archive" / "candidates.json").read_text(encoding="utf-8"))
    assert any(c.get("id") == "SC-099" for c in data["candidates"]), \
        "archive id reassignment was not applied"


def test_update_leg_live_candidates_still_rejects_id_reassign(tmp_path):
    """The update-leg guard stays intact on the LIVE file: reassigning the managed id is still refused."""
    vault = tmp_path
    (vault / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-001", "title": "old"}]}), encoding="utf-8")
    cp = _update(vault, "candidates.json", "candidates", "SC-001", "id=SC-099")
    assert cp.returncode != 0, "live candidates.json must still refuse a managed id-reassign on the update leg"
