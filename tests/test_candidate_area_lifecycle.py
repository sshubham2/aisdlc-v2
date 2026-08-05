"""slice-098 / SC-212 (critique M5, DR-1's addition) — the candidate `area` survives the LIFECYCLE.

The composition spike proved the additive key is inert for the six READERS it drove. It did NOT prove
the WRITER round-trip, and DR-1 named the specific hazard: `/commit-slice` archive-moves a shipped
candidate through `vault_edit rewrite --base-file` (skills/commit-slice/SKILL.md) — the very leg
[[ADR-125]] section 6 records as OUT OF CONTRACT — on EVERY ship. A writer that reconstructs a candidate
record from a whitelist, rather than mutating it in place, would silently DROP the new key somewhere
along claim -> release -> demote -> archive, and every read-side test would still pass.

So this drives the real producers with their PRODUCTION invocation shapes (BC-PROJ-10) and asserts the
annotation is still there at the end.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

VE = "scripts/lib/vault_edit.py"
CLAIM = "skills/slice/scripts/claim_candidate.py"
DEMOTE = "skills/slice-candidates/scripts/demote_candidate.py"

AREA = "write-seams"


def _cands(vault, rel="candidates.json"):
    return json.loads((vault / rel).read_text(encoding="utf-8"))["candidates"]


def _rec(vault, cid, rel="candidates.json"):
    return next((c for c in _cands(vault, rel) if c["id"] == cid), None)


def _seed(vault):
    (vault / "candidates.json").write_text(json.dumps({
        "_schema": "aisdlc/slice-candidates@1", "project": "fx", "counters": {"sc": 1},
        "candidates": [{
            "id": "SC-001", "title": "chore-one", "status": "candidate", "progress": "not-started",
            "slice": None, "claimed_by": None, "started_at": None,
            "source": [{"type": "risk", "ref": "R-1"}],
            "priority": {"score": 5, "severity": "medium", "effort": "S"},
            "assumptions": [{"id": "A1", "statement": "x", "blocking": True,
                             "spike_status": "unproven"}],
            "history": [],
        }],
        "pick_log": [],
    }, indent=2), encoding="utf-8")
    (vault / "archive").mkdir(exist_ok=True)
    (vault / "archive" / "candidates.json").write_text(json.dumps(
        {"_schema": "aisdlc/slice-candidates@1", "project": "fx", "candidates": []}, indent=2),
        encoding="utf-8")


def _git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo — claim_candidate resolves the caller identity from git config."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.name", "Test User"],
                 ["config", "user.email", "test@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                    "commit", "-qm", "init"], check=True, capture_output=True)
    return repo


def test_area_survives_the_candidate_lifecycle(run_script, vault, tmp_path):
    _seed(vault)
    repo = _git_repo(tmp_path)

    # 1. ANNOTATE through the sanctioned seam.
    r = run_script(VE, ["--vault", str(vault), "update", "--file", "candidates.json",
                        "--array", "candidates", "--id", "SC-001", "--set", f"area={AREA}"])
    assert r.returncode == 0, r.stderr
    assert _rec(vault, "SC-001")["area"] == AREA

    # 2. RESERVE + RELEASE (the /slice soft hold and its undo) — both rewrite the record in place.
    r = run_script(CLAIM, ["--vault", str(vault), "--candidate", "SC-001", "--reserve",
                           "--repo-root", str(repo)])
    assert r.returncode == 0, r.stderr
    assert _rec(vault, "SC-001")["area"] == AREA, "area lost at --reserve"
    r = run_script(CLAIM, ["--vault", str(vault), "--candidate", "SC-001", "--release",
                           "--repo-root", str(repo)])
    assert r.returncode == 0, r.stderr
    assert _rec(vault, "SC-001")["area"] == AREA, "area lost at --release"

    # 3. DEMOTE (the other candidate-field producer — a genuine in-place read-modify-write).
    r = run_script(DEMOTE, ["--vault", str(vault), "--candidate", "SC-001",
                            "--reason", "can wait until the data pass lands"])
    assert r.returncode == 0, r.stderr
    rec = _rec(vault, "SC-001")
    assert rec["area"] == AREA, "area lost at demote"
    assert rec.get("demote_reason"), "the demote itself must still have taken effect"

    # 4. CLAIM for real (/slice mints the slice number).
    r = run_script(CLAIM, ["--vault", str(vault), "--candidate", "SC-001", "--name", "do-a-thing",
                           "--repo-root", str(repo)])
    assert r.returncode == 0, r.stderr
    assert _rec(vault, "SC-001")["area"] == AREA, "area lost at claim"

    # 5. The /commit-slice ARCHIVE MOVE — append the shipped copy, then `rewrite --base-file` the live
    #    file without it. This is the exact production shape, including the out-of-contract rewrite hop.
    shipped = dict(_rec(vault, "SC-001"), status="shipped")
    body = tmp_path / "shipped-candidate.json"
    body.write_text(json.dumps(shipped, indent=2), encoding="utf-8")
    r = run_script(VE, ["--vault", str(vault), "append", "--file", "archive/candidates.json",
                        "--array", "candidates", "--unique-key", "id", "--content-file", str(body)])
    assert r.returncode == 0, r.stderr

    base = tmp_path / "base.bin"
    r = run_script(VE, ["--vault", str(vault), "read", "--file", "candidates.json",
                        "--out-file", str(base)])
    assert r.returncode == 0, r.stderr
    live = json.loads((vault / "candidates.json").read_text(encoding="utf-8"))
    live["candidates"] = [c for c in live["candidates"] if c["id"] != "SC-001"]
    updated = tmp_path / "updated.json"
    updated.write_text(json.dumps(live, indent=2), encoding="utf-8")
    r = run_script(VE, ["--vault", str(vault), "rewrite", "--file", "candidates.json",
                        "--base-file", str(base), "--content-file", str(updated)])
    assert r.returncode == 0, r.stderr

    archived = _rec(vault, "SC-001", "archive/candidates.json")
    assert archived is not None, "the shipped candidate never reached the archive"
    assert archived["area"] == AREA, (
        "the area was dropped by the /commit-slice archive move — the annotation must survive the ship, "
        "or the historical record silently loses the axis it was filed under")
    assert _rec(vault, "SC-001") is None, "the shipped candidate must leave the live file"
