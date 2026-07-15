"""AC5 — the REALITY REPLAY. slice-068 / SC-135.

aivlc is a REAL product with 11 shipped slices. Its orchestrator/state-machine — the thing it exists to
be — was NEVER minted as a candidate. It survives only as a line in slice-001's `out_of_scope`, so
`/slice` structurally cannot pick it, and aivlc's entire remaining backlog is 100% exhaust: its next
pick would be more keyring CI hardening. This is the defect, in production, in someone's real project.

This test replays aivlc's REAL vault bytes and asserts the orchestrator is minted AND reaches the
PRODUCTION pick surface.

WHY A COMMITTED FIXTURE AND NOT THE LIVE VAULT (C9): a test rooted at ~/.aisdlc/aivlc-dc06244e runs on
exactly ONE laptop and would be a permanent SKIP on every CI runner and every installed user's machine
— its shippability row would be a permanent skip and the slice's headline reality contact would be
undurable. So aivlc's candidates.json + archive/candidates.json are committed VERBATIM to
tests/fixtures/aivlc-vault/. They carry the actual malformed rows (SC-014 source='reflect' LIVE,
SC-009 source='slice-007-discovered' ARCHIVED) that crash the naive selector, so the evidence stays
REAL while becoming portable. An env-gated live-vault variant (AI_SDLC_AIVLC_VAULT) runs only where the
real vault exists.

WHY --top 5 AND NOT _classify (C1, BC-PROJ-10 — this project's 4th recorded instance): the design spike
imported the REAL candidates_top.py but proved only the WEAK property (_classify == 'pickable') and
stopped. /slice injects `candidates_top.py --top 5`. A candidate with no priority.score scores 0.0 and
ranks LAST — present in the file and INVISIBLE at the pick gate. That is this slice's own bug one level
down, and it is the exact argument used to kill designer-expert's ready-frontier rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "aivlc-vault"
SCRIPT = "scripts/lib/product_scope.py"

#: The orchestrator, in the REAL run-B decomposition captured by spike-product-scope-decomposition.
#: Under run B it depends on TWO unshipped items — which is exactly why spike D1 killed the
#: ready-frontier mint rule: the frontier would never have minted it on run 1.
ORCHESTRATOR_LABEL = "pipeline-orchestration-engine"
ORCHESTRATOR_TITLE = "build-pipeline-orchestration-engine"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _replay_vault(tmp_path: Path) -> Path:
    """A tmp vault carrying aivlc's REAL candidate bytes (live + archive + concept)."""
    v = tmp_path / "aivlc-replay"
    (v / "archive").mkdir(parents=True)
    for rel in ("candidates.json", "archive/candidates.json", "concept.json"):
        shutil.copyfile(FIXTURES / rel, v / rel)
    return v


def test_ac5_orchestrator_is_minted_and_reaches_the_pick_surface(run_script, tmp_path):
    """(a) the orchestrator is minted with source.type 'product-scope';
    (b) it appears in `candidates_top.py --top 5 --json` — the PRODUCTION invocation shape;
    (c) the committed fixture bytes are UNTOUCHED (read-only is a MECHANISM, not a promise — C7)."""
    before = {rel: _sha256(FIXTURES / rel)
              for rel in ("candidates.json", "archive/candidates.json", "concept.json")}

    v = _replay_vault(tmp_path)
    r = run_script(SCRIPT, ["--vault", str(v), "persist",
                            "--items-file", str(FIXTURES / "decomposition-run-b.json"), "--json"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["persisted"] == 9
    assert out["materialize"]["minted_count"] == 9, out["materialize"]

    # (a) minted, with product-scope provenance, from the REAL decomposition
    live = json.loads((v / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    orch = next((c for c in live if c["title"] == ORCHESTRATOR_TITLE), None)
    assert orch is not None, (
        "the orchestrator/state-machine — aivlc's actual product, missing from all 14 candidates it "
        "ever minted — was NOT materialized"
    )
    assert any(s.get("type") == "product-scope" and s.get("ref", "").startswith("PS-")
               for s in orch["source"]), orch["source"]

    # the full DAG is minted (D1): the orchestrator's two dependencies resolve to real SC ids
    assert len(orch["dependencies"]) == 2
    live_ids = {c["id"] for c in live}
    assert all(d in live_ids for d in orch["dependencies"]), orch["dependencies"]

    # (b) THE PRODUCTION SURFACE — /slice injects exactly this
    r = run_script("skills/slice/scripts/candidates_top.py",
                   ["--vault", str(v), "--top", "5", "--json"])
    assert r.returncode == 0, r.stderr
    top = json.loads(r.stdout)["top"]
    top_ids = [t["id"] for t in top]
    assert orch["id"] in top_ids, (
        f"PRESENT BUT INVISIBLE: the orchestrator ({orch['id']}) was minted but does not appear in "
        f"`candidates_top --top 5` — this slice's own bug, one level down. top={top_ids}"
    )
    assert next(t for t in top if t["id"] == orch["id"])["score"] > 0

    # (c) READ-ONLY, proven MECHANICALLY
    after = {rel: _sha256(FIXTURES / rel)
             for rel in ("candidates.json", "archive/candidates.json", "concept.json")}
    assert after == before, "the replay WROTE to the committed fixture bytes"


def test_ac5_tolerant_selector_parses_every_real_candidate(run_script, tmp_path):
    """MV2, against the real bytes: aivlc carries `source` as a bare STRING on two rows. A selector
    that crashes on one malformed object never reconciles the rest (the k8s lens: a controller that
    dies on one bad object). Zero exceptions, zero false product-scope matches."""
    from scripts.lib.product_scope import iter_sources, owner_ref

    total = matched = 0
    for rel in ("candidates.json", "archive/candidates.json"):
        for c in json.loads((FIXTURES / rel).read_text(encoding="utf-8"))["candidates"]:
            total += 1
            for s in iter_sources(c):          # must not raise
                assert isinstance(s, dict) and len(s["type"]) > 1  # never a per-character pseudo-source
            if owner_ref(c) is not None:
                matched += 1
    assert total == 14
    assert matched == 0, "nothing in aivlc is product-sourced yet — any match is a FALSE positive"


@pytest.mark.skipif(
    not os.environ.get("AI_SDLC_AIVLC_VAULT"),
    reason="set AI_SDLC_AIVLC_VAULT to the real aivlc vault to run the live-vault variant",
)
def test_ac5_live_vault_replay_is_byte_for_byte_read_only(run_script):
    """The env-gated LIVE variant. must_not_defer: 'the aivlc vault is READ-ONLY in this slice.'
    C7: a promise is not a guard — so prove it. `--scope-file` implies `--dry-run`, and the real
    vault's bytes are sha256'd before and after."""
    v = Path(os.environ["AI_SDLC_AIVLC_VAULT"])
    targets = [v / "candidates.json", v / "archive" / "candidates.json"]
    before = {p: _sha256(p) for p in targets if p.exists()}
    assert before, f"no candidate files at {v}"

    r = run_script(SCRIPT, ["--vault", str(v), "materialize",
                            "--scope-file", str(FIXTURES / "decomposition-run-b.json"),
                            "--dry-run", "--json"])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert len(out["would_mint"]) == 9

    after = {p: _sha256(p) for p in targets if p.exists()}
    assert after == before, "the LIVE aivlc vault was MUTATED — the read-only guarantee is broken"
