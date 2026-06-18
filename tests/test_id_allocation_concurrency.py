"""Concurrency regression for in-lock id/number allocation (slice-019 / SC-022 / ADR-013).

THE BUG this pins: identity (slice-NNN / SC-NNN / SHIP-NNN / ADR-NNN) was chosen ABOVE the
per-file lock (markdown `max(existing)+1` or model hand-fill), then committed by a locked write
that never re-checked uniqueness -> duplicate ids under concurrent slices.

THE GUARANTEE: every number is minted by `scripts.lib.id_allocator` by bumping a monotonic
`counters.<kind>` INSIDE the same `safe_mutate_text` critical section that commits the record,
so N concurrent OS processes can never collide.

Uses real OS SUBPROCESSES (not threads) — the deployment is N independent worktree processes
coordinated only by the OS sidecar lock; a thread test shares the GIL and would mask a
cross-process lock defect (slice-009's lesson: a serialization defect only shows against reality).

Fails on HEAD (no id_allocator; claim_candidate is not claim-first); passes after slice-019.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]  # the worktree root

# ── worker drivers (run in a fresh subprocess each) ──────────────────────────────

# Each allocator worker bumps counters.sc N times via id_allocator.next_id INSIDE
# safe_mutate_text, appending each minted id so we can prove they are all distinct.
_ALLOC_WORKER = r"""
import sys, json
sys.path.insert(0, r"{repo}")
from pathlib import Path
from scripts.lib._vault_write import safe_mutate_text
from scripts.lib import id_allocator

target = Path(sys.argv[1]); iters = int(sys.argv[2])
def mutate(text):
    data = json.loads(text) if text.strip() else {{"candidates": [], "minted": []}}
    for _ in range(iters):
        sc = id_allocator.next_id(data, "sc")
        data["minted"].append(sc)
    return json.dumps(data)
for _ in range(iters):
    safe_mutate_text(target, mutate)
"""

# Each claim worker claims ONE distinct candidate the new claim-first way: it passes
# --candidate + --name (NO pre-decided slice number) and the tool mints + returns slice-NNN.
_CLAIM_WORKER = r"""
import sys, json, subprocess
repo = r"{repo}"
vault = sys.argv[1]; cand = sys.argv[2]; name = sys.argv[3]
cp = subprocess.run([sys.executable,
    repo + r"/skills/slice/scripts/claim_candidate.py",
    "--vault", vault, "--candidate", cand, "--name", name,
    "--repo-root", repo, "--json"],
    capture_output=True, text=True)
sys.stdout.write(cp.stdout); sys.stderr.write(cp.stderr)
sys.exit(cp.returncode)
"""


def _run(code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code, *args],
                          capture_output=True, text=True)


def test_allocator_distinct_under_concurrent_processes(tmp_path):
    """N processes minting SC ids concurrently -> all distinct, none lost."""
    target = tmp_path / "counters.json"
    target.write_text(json.dumps({"candidates": [], "minted": []}), encoding="utf-8")
    n_proc, iters = 8, 30
    code = _ALLOC_WORKER.format(repo=str(_REPO))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(target), str(iters)])
             for _ in range(n_proc)]
    for p in procs:
        assert p.wait() == 0
    minted = json.loads(target.read_text(encoding="utf-8"))["minted"]
    expected = n_proc * iters * iters
    assert len(minted) == expected, f"lost mints: {len(minted)} != {expected}"
    assert len(set(minted)) == expected, "DUPLICATE SC ids minted under concurrency"


def test_claim_candidate_distinct_slice_numbers(tmp_path):
    """N processes each claim a distinct candidate -> N distinct slice-NNN, none lost."""
    vault = tmp_path / "vault"
    vault.mkdir()
    n = 6
    cands = [{"id": f"SC-{i:03d}", "title": f"c{i}", "status": "candidate",
              "progress": "not-started", "slice": None, "claimed_by": None,
              "started_at": None, "history": []} for i in range(1, n + 1)]
    (vault / "candidates.json").write_text(
        json.dumps({"_schema": "aisdlc/slice-candidates@1", "project": "t",
                    "candidates": cands, "pick_log": []}), encoding="utf-8")
    code = _CLAIM_WORKER.format(repo=str(_REPO))
    procs = [subprocess.Popen(
        [sys.executable, "-c", code, str(vault), f"SC-{i:03d}", f"fix-thing-{i}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(1, n + 1)]
    outs = [p.communicate() for p in procs]
    for (out, err), p in zip(outs, procs):
        assert p.returncode == 0, f"claim failed: {err}"
    slices = [json.loads(out)["slice"] for out, _ in outs]
    assert len(set(slices)) == n, f"DUPLICATE slice numbers: {slices}"
    data = json.loads((vault / "candidates.json").read_text(encoding="utf-8"))
    claimed = [c for c in data["candidates"] if c["status"] == "spiking"]
    assert len(claimed) == n, "lost claims (lost-update clobber)"


def _vault_edit(vault: Path, content: dict):
    cf = vault / "_payload.json"
    cf.write_text(json.dumps(content), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "lib" / "vault_edit.py"),
         "--vault", str(vault), "append", "--file", "candidates.json",
         "--array", "candidates", "--content-file", str(cf)],
        capture_output=True, text=True)


def test_vault_edit_mints_omitted_id_and_rejects_supplied(tmp_path):
    """AC2: a managed-kind append mints an OMITTED id in-lock; a SUPPLIED id is rejected."""
    vault = tmp_path
    (vault / "candidates.json").write_text(json.dumps({
        "candidates": [{"id": "SC-005", "title": "old"}], "pick_log": []}), encoding="utf-8")

    # omitted id -> auto-minted above the existing max (SC-005 -> SC-006)
    cp = _vault_edit(vault, {"title": "new", "status": "candidate"})
    assert cp.returncode == 0, cp.stderr
    data = json.loads((vault / "candidates.json").read_text(encoding="utf-8"))
    minted = [c["id"] for c in data["candidates"] if c["title"] == "new"]
    assert minted == ["SC-006"], f"expected SC-006, got {minted}"

    # caller-supplied id -> fail-visible reject (exit 2), file unchanged
    cp = _vault_edit(vault, {"id": "SC-099", "title": "sneaky"})
    assert cp.returncode != 0, "a caller-supplied managed id must be rejected"
    assert "supplied" in cp.stderr.lower() or "id" in cp.stderr.lower()
    data = json.loads((vault / "candidates.json").read_text(encoding="utf-8"))
    assert not any(c.get("id") == "SC-099" for c in data["candidates"]), "rejected append leaked"


def _make_slice(vault: Path, folder: str, stage: str):
    d = vault / "slices" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "milestone.json").write_text(json.dumps({"slice": folder, "stage": stage}), encoding="utf-8")


def test_reflection_lookup_degrades_on_ambiguous(tmp_path):
    """AC4 (SC-029 crash site): with >=2 in-flight slices from a non-branch context, the resolver
    returns the AMBIGUOUS sentinel and reflection_lookup degrades cleanly — no TypeError, exit 0."""
    vault = tmp_path / "v"; vault.mkdir()
    _make_slice(vault, "slice-001-a", "design")
    _make_slice(vault, "slice-002-b", "build")
    nongit = tmp_path / "nongit"; nongit.mkdir()
    cp = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "lib" / "reflection_lookup.py"),
         "--vault", str(vault), "--from-mission-brief", "--repo-root", str(nongit)],
        capture_output=True, text=True)
    assert cp.returncode == 0, f"reflection_lookup crashed on the ambiguous sentinel: {cp.stderr}"
    assert "TypeError" not in cp.stderr, cp.stderr
    assert "ambiguous" in (cp.stdout + cp.stderr).lower()


def test_counters_audit_flags_stale_and_clean(tmp_path):
    """M1/m3: the counters audit flags a hand-edited-down counter; clean when consistent."""
    sys.path.insert(0, str(_REPO))
    from scripts.lib import id_allocation_audit
    vault = tmp_path / "v"; vault.mkdir()
    (vault / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-009"}], "counters": {"sc": 3}}), encoding="utf-8")
    assert any("counters.sc" in v for v in id_allocation_audit.counters_violations(vault))
    (vault / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "SC-009"}], "counters": {"sc": 9}}), encoding="utf-8")
    assert id_allocation_audit.counters_violations(vault) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
