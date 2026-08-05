"""slice-102 / SC-232 — the completion-gap detector + its production seam.

WHAT THIS GUARDS. `/slice` had no notion of what is still MISSING for the app to be usable. Its only
product signal was a flat mint-time `score: 5` and a pick-time term deliberately inert at 0, and
`/slice-candidates --product` decomposes the product's scope EXACTLY ONCE by design — so an EXHAUSTED
scope is the steady state of every project past its initial capability list, and in that state the
pipeline falls through to score-ranked pipeline exhaust BY CONSTRUCTION. Every existing backstop misses
it: the `PRODUCT == 0` census notice does not fire (PRODUCT is non-zero), and the completeness governor
fires only at 0-built.

FIXTURE PROVENANCE is stated per fixture in `fixtures/completion-gap/README.md`, not claimed in bulk
(round-3 M10). Six fixtures are built from the committed `persist`-replay decomposition; three are
hand-committed blobs, because the committed replay has ZERO product-sourced rows in either candidates
file and therefore cannot reach an all-built / rejected-only stratum, and no shipped writer emits the
9-key item shape at all.

NO MACHINE-LOCAL VAULT IS A FIXTURE OR A TEST TARGET (round-2 B3 / round-3 M-add-3). `conftest.py`
strips `AI_SDLC_VAULT_ROOT` from every child env precisely so a test can never resolve the developer's
real vault, and `~/.aisdlc/<slug>-<hash>` exists on exactly one machine. The live-vault no-raise smoke
is a NAMED LOCAL-ONLY pre-build step and contributes no row here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "slice" / "scripts"))

import completion_gap  # noqa: E402
from scripts.lib import gate_log, product_rollup, triage_precision  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLAY = FIXTURES / "aivlc-vault"
BLOBS = FIXTURES / "completion-gap"
TOP = PLUGIN_ROOT / "skills" / "slice" / "scripts" / "candidates_top.py"
SCOPE = PLUGIN_ROOT / "scripts" / "lib" / "product_scope.py"
SLICE_SKILL = PLUGIN_ROOT / "skills" / "slice" / "SKILL.md"
SLICE_CANDIDATES_SKILL = PLUGIN_ROOT / "skills" / "slice-candidates" / "SKILL.md"


# ── fixture builders ────────────────────────────────────────────────────────────────────────

def _run(script: Path, *args):
    return subprocess.run([sys.executable, str(script), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env=_env())


def _env():
    import os
    e = dict(os.environ)
    e.pop("AI_SDLC_VAULT_ROOT", None)   # a test may NEVER resolve the developer's real vault
    return e


def _cli(vault, *args):
    """`candidates_top` — the PRODUCTION invocation shape (BC-PROJ-10)."""
    return _run(TOP, "--vault", vault, *args)


def _gap(vault, *args):
    cp = _cli(vault, "--completion-gap", "--json", *args)
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stderr}"
    return json.loads(cp.stdout)["completion_gap"]


def _posix_bash() -> str:
    """A REAL POSIX bash, which on Windows is git-bash — the shell the plugin's skills actually run in.

    `shutil.which("bash")` finds `C:\\Windows\\system32\\bash.exe` there, which is the WSL launcher: it
    fails with `execvpe /bin/bash failed` unless a distro is installed, so a test that trusted it would
    be red for the wrong reason on a working machine. Probed, never assumed.
    """
    import shutil
    import subprocess as sp
    candidates = [c for c in (r"C:\Program Files\Git\bin\bash.exe",
                              r"C:\Program Files\Git\usr\bin\bash.exe",
                              "/bin/bash", shutil.which("bash")) if c]
    for c in candidates:
        try:
            if sp.run([c, "-c", "printf ok"], capture_output=True, text=True,
                      timeout=30).stdout.strip() == "ok":
                return c
        except OSError:
            continue
    pytest.skip("no working POSIX bash on this host (the guard still runs on the Linux CI runner)")


def _replay(tmp_path, name="replay"):
    """Fixtures 1/2/4/5/6/9 — the committed decomposition through the REAL `persist` verb."""
    v = tmp_path / name
    (v / "archive").mkdir(parents=True)
    for rel in ("candidates.json", "archive/candidates.json", "concept.json"):
        shutil.copyfile(REPLAY / rel, v / rel)
    cp = _run(SCOPE, "--vault", v, "persist", "--items-file", REPLAY / "decomposition-run-b.json",
              "--json")
    assert cp.returncode == 0, cp.stderr
    return v


def _blob(tmp_path, name, dest=None):
    """Fixtures 3/7/8 — the hand-committed vaults, copied verbatim."""
    v = tmp_path / (dest or name)
    shutil.copytree(BLOBS / name, v)
    return v


def _read(vault, rel):
    return json.loads((vault / rel).read_text(encoding="utf-8"))


def _write(vault, rel, data):
    (vault / rel).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _product_ids(vault):
    """Live candidate ids carrying product-scope provenance."""
    return [c["id"] for c in _read(vault, "candidates.json")["candidates"]
            if isinstance(c, dict)
            and any(isinstance(s, dict) and s.get("type") == "product-scope"
                    for s in (c.get("source") or []))]


def fx_unbuilt_present(tmp_path):
    """(1) unbuilt capabilities WITH a live pickable product child.

    One deliberate mutation: the orchestrator's candidate (which depends on two still-live
    prerequisites) is given the SMALLEST effort so it OUTRANKS its own prerequisite. Two score-5 rows
    split on effort is exactly how a dependent jumps its blocker, and it is the only way the deps rule
    in AC2 can actually go red.
    """
    v = _replay(tmp_path)
    data = _read(v, "candidates.json")
    for c in data["candidates"]:
        if c.get("title") == "build-pipeline-orchestration-engine":
            c["priority"]["effort"] = "XS"
    _write(v, "candidates.json", data)
    return v


def fx_none_pickable(tmp_path):
    """(2) every product child claimed → pickable_product EMPTY while unbuilt[] is NON-empty."""
    v = _replay(tmp_path)
    data = _read(v, "candidates.json")
    for c in data["candidates"]:
        if any(isinstance(s, dict) and s.get("type") == "product-scope"
               for s in (c.get("source") or [])):
            c["status"] = "active"
            c["progress"] = "build"
            c["slice"] = "slice-900"
            c["claimed_by"] = {"git_user": "Dev Two", "git_email": "dev.two@example.com"}
    _write(v, "candidates.json", data)
    return v


def fx_all_built(tmp_path):
    """(3) every capability archived-and-SHIPPED. Hand-committed: deleting a candidate from a live
    vault can only ever reach `none-pickable`, never `all-built` (round-2 M4)."""
    return _blob(tmp_path, "all-built")


def fx_scope_absent(tmp_path):
    """(4) no product-scope.json — the state of every project that never ran the bootstrap."""
    v = _replay(tmp_path, "no-scope")
    (v / "product-scope.json").unlink()
    return v


def fx_scope_corrupt(tmp_path):
    """(5) a product-scope.json that is not JSON at all."""
    v = _replay(tmp_path, "corrupt-scope")
    (v / "product-scope.json").write_text("{ this is not json", encoding="utf-8")
    return v


def fx_candidates_absent(tmp_path, mode):
    """(6) THREE negative states, all of which must read as an ERROR and never as an empty backlog."""
    v = _replay(tmp_path, f"cand-{mode}")
    p = v / "candidates.json"
    if mode == "absent":
        p.unlink()
    elif mode == "zero-byte":
        p.write_text("", encoding="utf-8")
    elif mode == "empty-list":
        p.write_text(json.dumps({"_schema": "aisdlc/slice-candidates@1", "project": "aivlc",
                                 "candidates": []}), encoding="utf-8")
    else:                                                   # pragma: no cover - test wiring error
        raise AssertionError(mode)
    return v


def fx_nine_key(tmp_path):
    """(7) this repo's own legacy 9-key item shape (no `code_components`)."""
    return _blob(tmp_path, "nine-key")


def fx_rejected_only(tmp_path):
    """(8) one shipped capability + one whose archived children are ALL rejected."""
    return _blob(tmp_path, "rejected-only")


def fx_two_area(tmp_path):
    """(9) two areas, one of them COMPLETE — the area-scoped denominator's only honest fixture."""
    v = _replay(tmp_path, "two-area")
    core = ["PS-001", "PS-002", "PS-003"]
    for ps in core:
        assert _run(SCOPE, "--vault", v, "set-area", "--item", ps, "--area", "core").returncode == 0
    for ps in ("PS-004", "PS-005", "PS-006", "PS-007", "PS-008", "PS-009"):
        assert _run(SCOPE, "--vault", v, "set-area", "--item", ps, "--area", "runtime").returncode == 0
    # ship `core`: MOVE its children live -> archive, which is what makes a capability `done`
    live = _read(v, "candidates.json")
    arch = _read(v, "archive/candidates.json")
    keep, moved = [], []
    for c in live["candidates"]:
        refs = [s.get("ref") for s in (c.get("source") or []) if isinstance(s, dict)]
        if any(r in core for r in refs):
            c["status"] = "shipped"
            c["progress"] = "shipped"
            moved.append(c)
        else:
            keep.append(c)
    assert len(moved) == 3, moved
    live["candidates"] = keep
    arch["candidates"] = list(arch["candidates"]) + moved
    _write(v, "candidates.json", live)
    _write(v, "archive/candidates.json", arch)
    return v


ALL_FIXTURES = {
    "unbuilt-present": fx_unbuilt_present,
    "none-pickable": fx_none_pickable,
    "all-built": fx_all_built,
    "scope-absent": fx_scope_absent,
    "scope-corrupt": fx_scope_corrupt,
    "candidates-absent": lambda tp: fx_candidates_absent(tp, "absent"),
    "candidates-zero-byte": lambda tp: fx_candidates_absent(tp, "zero-byte"),
    "candidates-empty-list": lambda tp: fx_candidates_absent(tp, "empty-list"),
    "nine-key": fx_nine_key,
    "rejected-only": fx_rejected_only,
    "two-area": fx_two_area,
}


# ── AC1 — the verdict matrix, over the NINE cases ────────────────────────────────────────────

def test_no_fixture_path_resolves_outside_tests_fixtures():
    """Round-2 B3: the previous plan pinned expectations to values living only in another product's
    machine-local store — absent on CI and on every other machine. Asserted STRUCTURALLY."""
    for p in (REPLAY, BLOBS):
        assert p.is_dir(), p
        assert FIXTURES in p.resolve().parents or p.resolve() == FIXTURES.resolve()
    src = Path(__file__).read_text(encoding="utf-8")
    # A fixture may only be reached through a path ROOTED at tests/fixtures/. Anything resolving a user
    # home or a drive-letter literal would run on exactly one machine and be a permanent SKIP
    # everywhere else, which is what makes the slice's headline reality contact undurable (round-2 B3).
    #
    # The needles are ASSEMBLED at runtime on purpose: this guard scans the file it lives in, so a
    # literal list would match ITSELF and the check would be unfalsifiable.
    banned = ("expand" + "user", "Path." + "home(", "aivlc-" + "dc06244e",
              "C:" + "\\Users", "C:" + "/Users")
    for needle in banned:
        assert needle not in src, f"a machine-local path form leaked into the fixture set: {needle}"
    assert 'e.pop("AI_SDLC_VAULT" + "_ROOT", None)'.replace('" + "', "") in src, (
        "every child process must be denied the developer's real vault")


@pytest.mark.parametrize("name,verdict,reason", [
    ("unbuilt-present", "product-work-available", "unbuilt-present"),
    ("none-pickable", "scope-exhausted", "none-pickable"),
    ("all-built", "scope-exhausted", "all-built"),
    ("scope-absent", "scope-absent", "absent-no-file"),
    ("rejected-only", "scope-exhausted", "none-pickable"),
    ("nine-key", "scope-exhausted", "all-built"),
])
def test_classify_fixture_verdict_matrix(tmp_path, name, verdict, reason):
    """AC1 — the verdict + reason on every DECIDABLE fixture, driven through the production seam."""
    v = ALL_FIXTURES[name](tmp_path)
    gap = _gap(v)
    assert gap["verdict"] == verdict, gap
    assert gap["reason"] == reason, gap
    # every emitted reason is a DECLARED member of its verdict's set
    assert gap["reason"] in completion_gap.REASONS[gap["verdict"]]
    # `unbuilt[]`'s membership predicate, asserted rather than assumed
    assert all(c["bucket"] != "done" for c in gap["unbuilt"]), gap["unbuilt"]
    assert gap["done_definition"] == product_rollup.DONE_DEFINITION


def test_all_built_can_never_fire_while_done_is_less_than_total(tmp_path):
    """(8) the rejected_only pin. A killed capability reads `state: done` from cmd_done but buckets as
    `rejected_only` — carry only one of the two axes and it falls out of `done` AND out of `unbuilt[]`,
    at which point `all-built` fires on an incomplete product."""
    gap = _gap(fx_rejected_only(tmp_path))
    assert gap["done"] == 1 and gap["total"] == 2
    assert gap["reason"] != "all-built"
    unbuilt = {c["id"]: c for c in gap["unbuilt"]}
    assert set(unbuilt) == {"PS-002"}
    assert unbuilt["PS-002"]["bucket"] == "rejected_only"
    assert unbuilt["PS-002"]["state"] == "done", "cmd_done's four-valued state travels UNCHANGED"


def test_corrupt_scope_is_verdict_less_and_never_scope_absent(tmp_path):
    """(5) must-not-defer #1. A silent degrade to `scope-absent` would read as 'this project has no
    product' and SUPPRESS the very gate this slice adds."""
    gap = _gap(fx_scope_corrupt(tmp_path))
    assert "verdict" not in gap, gap
    assert "error" in gap and gap["cause_kind"] in completion_gap.CAUSE_KINDS
    assert gap["cause_kind"] != "candidates-absent"


@pytest.mark.parametrize("mode", ["absent", "zero-byte", "empty-list"])
def test_candidates_absent_in_all_three_negative_states(tmp_path, mode):
    """(6) AC1 — detected on the CONDITION at the backlog-load branch, NEVER on the `note: no-backlog`
    marker, which does not fire on `{"candidates": []}` at all."""
    gap = _gap(fx_candidates_absent(tmp_path, mode))
    assert "verdict" not in gap, gap
    assert gap["cause_kind"] == "candidates-absent", gap


def test_every_emitted_cause_kind_and_reason_is_a_declared_member(tmp_path):
    """The enums are declared ONCE, with a producer for every member (round-3 m1)."""
    seen_reasons, seen_causes = set(), set()
    for name, build in ALL_FIXTURES.items():
        gap = _gap(build(tmp_path / name))
        if "verdict" in gap:
            assert gap["verdict"] in completion_gap.VERDICTS
            seen_reasons.add(gap["reason"])
            assert gap["reason"] in completion_gap.REASONS[gap["verdict"]]
        else:
            seen_causes.add(gap["cause_kind"])
            assert gap["cause_kind"] in completion_gap.CAUSE_KINDS
    assert seen_reasons >= {"unbuilt-present", "none-pickable", "all-built", "absent-no-file"}
    assert seen_causes >= {"candidates-absent", "scope-malformed"}


def test_cli_scope_absent_and_corrupt_exit_zero_without_traceback(tmp_path):
    """Round-3 M-add-1, THE assertion that goes red on the unguarded `_Refuse`. `cmd_materialize`
    resolves items via `_scope(required=True)` and RAISES `_Refuse(4, no-scope)` on the branch this
    design declares NORMAL; `main()` has no handler and the `!`-injection carries no `||` fallback, so
    the traceback would render exactly where the ranked digest belongs."""
    for fx in (fx_scope_absent, fx_scope_corrupt):
        v = fx(tmp_path / fx.__name__)
        cp = _cli(v, "--completion-gap", "--json")
        assert cp.returncode == 0, cp.stderr
        assert "Traceback" not in cp.stderr, cp.stderr
        gap = json.loads(cp.stdout)["completion_gap"]
        if fx is fx_scope_absent:
            assert gap["verdict"] == "scope-absent", "verdict must be PRESENT on the normal branch"
        else:
            assert "verdict" not in gap and "error" in gap


def test_empty_scope_maps_under_scope_exhausted_not_scope_absent(tmp_path):
    """[[ADR-146]] d9 / INV-8. A PRESENT-but-empty scope is MAPPED-BUT-NOT-RESIDENT: `add-item` accepts
    it and refuses only an ABSENT one, so the verdict alone must keep selecting the handler."""
    v = fx_all_built(tmp_path)
    scope = _read(v, "product-scope.json")
    scope["items"] = []
    _write(v, "product-scope.json", scope)
    gap = _gap(v)
    assert gap["verdict"] == "scope-exhausted" and gap["reason"] == "empty-scope", gap
    assert gap["unbuilt"] == [] and gap["total"] == 0


# ── AC1 — the mid-read cross-checks ──────────────────────────────────────────────────────────

def test_cross_check_equal_on_quiescent_vault_and_reports_a_forced_delta(tmp_path):
    """[[ADR-148]] d1, the round-3 BLOCKER. ADR-146 d13's second capture point was `_observed` — live
    UNION archive — compared against a LIVE-ONLY first capture: 119 vs 224 on a quiescent vault, so the
    gate would have refused on EVERY run and shipped INERT. The corrected pair is ONE population read
    TWICE from the same file. Asserted BOTH ways."""
    v = fx_unbuilt_present(tmp_path)
    gap = _gap(v)
    assert "verdict" in gap and "error" not in gap, "quiescent vault must NOT report a mid-read delta"

    # the forced delta: a real writer landing between the two captures is REPORTED, not swallowed
    import completion_gap as cg
    a = {"SC-001": "candidate", "SC-002": "candidate"}
    b = {"SC-001": "candidate", "SC-002": "active"}
    assert cg.cross_check(a, a) is None
    delta = cg.cross_check(a, b)
    assert delta is not None and "SC-002" in delta


def test_scope_change_mid_read_is_named_and_is_not_scope_absent():
    """DD2's negative control, at the unit boundary: the same interleave WITHOUT the check emitted a
    confident WRONG verdict (`total: 15` where read A saw 14)."""
    import completion_gap as cg
    assert cg.cross_check({"PS-001"}, {"PS-001"}) is None
    delta = cg.cross_check({"PS-001"}, {"PS-001", "PS-015"})
    assert delta is not None and "PS-015" in delta


# ── AC5 — the honest headline ────────────────────────────────────────────────────────────────

def test_headline_is_honest_and_rollup_arithmetic_moves(tmp_path):
    """AC5. The headline names the `done` DEFINITION (a count of shipped slices, NOT verified-working
    capabilities) and names the unbuilt capabilities from the SAME array `unbuilt[]` is derived from,
    so the two cannot disagree."""
    v = fx_unbuilt_present(tmp_path)
    gap = _gap(v)
    head = gap["headline"]
    assert product_rollup.DONE_DEFINITION in head, head
    assert gap["done_definition"] == product_rollup.DONE_DEFINITION, (
        "never a literal in completion_gap.py — SC-233 redefines this constant")
    named = {c["id"] for c in gap["unbuilt"]}
    assert named, "fixture (1) has unbuilt capabilities"
    for c in gap["unbuilt"]:
        assert c["id"] in head and (c["title"] or "") in head, (c, head)
    # headline/list agreement, asserted directly
    assert {ps for ps in named if ps in head} == named

    # the post-append rollup is arithmetically SOUND — asserted on what can actually go red
    before = product_rollup.compute_rollup(v)["whole_app"]
    items = _read(v, "product-scope.json")["items"]
    new = dict(items[0])
    new.update({"id": "PS-900", "decomposition_label": "brand-new", "title": "build-brand-new",
                "depends_on": []})
    scope = _read(v, "product-scope.json")
    scope["items"] = items + [new]
    _write(v, "product-scope.json", scope)
    cands = _read(v, "candidates.json")
    cands["candidates"].append({"id": "SC-900", "title": "build-brand-new", "status": "candidate",
                                "source": [{"type": "product-scope", "ref": "PS-900"}]})
    _write(v, "candidates.json", cands)
    env = product_rollup.compute_rollup(v)
    assert "error" not in env, env
    assert env["whole_app"]["total"] == before["total"] + 1
    assert env["whole_app"]["done"] == before["done"]
    assert env["whole_app"]["in_progress"] == before["in_progress"] + 1


def test_headline_is_labelled_area_scoped_under_the_area_lens(tmp_path):
    v = fx_two_area(tmp_path)
    whole = _gap(v)
    scoped = _gap(v, "--area", "core")
    assert "core" in scoped["headline"], scoped["headline"]
    assert scoped["area_scope"] == "core" and whole["area_scope"] is None
    # AC2: done/total DIFFER between /slice and /slice --area on the SAME vault
    assert (scoped["done"], scoped["total"]) != (whole["done"], whole["total"])
    assert (scoped["done"], scoped["total"]) == (3, 3), scoped
    assert (whole["done"], whole["total"]) == (3, 9), whole


# ── AC2 — the pick rule + the hoisted row ────────────────────────────────────────────────────

def test_pick_id_is_the_top_ranked_product_candidate_with_no_unmet_deps(tmp_path):
    """AC2 — the deps rule. Two score-5 rows split on EFFORT, so a small dependent otherwise outranks
    its own still-live prerequisite and the gate would recommend blocked work."""
    v = fx_unbuilt_present(tmp_path)
    gap = _gap(v)
    rec = gap["recommendation"]
    assert rec["mode"] == "product-pick", gap
    rows = {r["id"]: r for r in gap["pickable_product"]}
    orch = next(c["id"] for c in _read(v, "candidates.json")["candidates"]
                if c.get("title") == "build-pipeline-orchestration-engine")
    assert rows[orch]["rank"] < rows[rec["pick_id"]]["rank"], (
        "the fixture must actually exercise the rule: the dependent has to OUTRANK its prerequisite")
    assert rec["pick_id"] != orch, "a candidate with unmet deps was recommended"
    assert gap["pick_row"]["deps_unmet"] == [], gap["pick_row"]


def test_pickable_product_membership_is_owner_ref_never_path_class(tmp_path):
    """[[ADR-148]] d9. `path_class` returns OFF_PATH for a DEMOTED candidate BEFORE it ever tests
    owner_ref, and it is per-candidate — so blocked/in-flight product rows are `on-path` too while
    belonging to neither array."""
    v = fx_unbuilt_present(tmp_path)
    data = _read(v, "candidates.json")
    target = next(c for c in data["candidates"]
                  if any(isinstance(s, dict) and s.get("type") == "product-scope"
                         for s in (c.get("source") or [])))
    target["demoted_at"] = "2026-08-01T00:00:00Z"
    target["demote_reason"] = "good enough for now"
    _write(v, "candidates.json", data)
    gap = _gap(v)
    assert target["id"] in {r["id"] for r in gap["pickable_product"]}, (
        "a DEMOTED product candidate is still product work — path_class would have dropped it")


def test_product_pick_hoisted_outside_top_n_in_its_own_section(tmp_path):
    """AC2 — the row is hoisted into its OWN labelled section carrying its REAL rank, never renumbered
    into `Top picks` (whose documented meaning as a PREFIX of the ranking is preserved). Measured: a
    score-5 / effort-L mint ranks ~11th of 117 on a real vault, outside any --top 5 window."""
    v = fx_unbuilt_present(tmp_path)
    # bury the product rows under higher-scoring exhaust so the pick really is outside --top 3
    data = _read(v, "candidates.json")
    for i in range(6):
        data["candidates"].append({
            "id": f"SC-8{i:02d}", "title": f"tidy-thing-{i}", "status": "candidate",
            "source": [{"type": "reflection-residue", "ref": "slice-004"}],
            "priority": {"score": 9, "severity": "high", "effort": "XS", "blast_radius": "scripts"},
            "dependencies": [],
        })
    _write(v, "candidates.json", data)

    cp = _cli(v, "--completion-gap", "--top", "3")
    assert cp.returncode == 0, cp.stderr
    gap = _gap(v, "--top", "3")
    pick = gap["recommendation"]["pick_id"]
    row = gap["pick_row"]
    assert row["rank"] > 3, ("the fixture must place the pick OUTSIDE the window", row)

    text = cp.stdout
    assert "Completion pick" in text and "hoisted by the completion gate" in text, text
    assert f"(rank {row['rank']} of {row['of']})" in text, text
    assert pick in text and row["title"] in text
    assert f"effort {row['effort']}" in text and f"score {row['score']:g}" in text

    # NOT numbered inside `Top picks` — the digest prefix keeps its documented meaning
    top_block = text.split("Completion pick")[0]
    numbered = [ln for ln in top_block.splitlines() if ln.strip().startswith(("1.", "2.", "3.", "4."))]
    assert all(pick not in ln for ln in numbered), numbered
    assert len([ln for ln in numbered if ln.strip()[0].isdigit()]) == 3


def test_the_hoisted_row_keeps_the_deps_unmet_warning_the_digest_shows(tmp_path):
    """[[ADR-148]] d5 — the gate must not STRIP a pick-time warning the ordinary digest renders."""
    v = fx_unbuilt_present(tmp_path)
    data = _read(v, "candidates.json")
    # claim every deps-clear product row so the only remaining pick carries unmet deps
    clear = [c for c in data["candidates"]
             if any(isinstance(s, dict) and s.get("type") == "product-scope"
                    for s in (c.get("source") or []))
             and not (c.get("dependencies") or [])]
    for c in clear:
        c["status"] = "active"
    _write(v, "candidates.json", data)
    cp = _cli(v, "--completion-gap", "--top", "1")
    gap = _gap(v, "--top", "1")
    assert gap["recommendation"]["mode"] == "product-pick"
    assert gap["pick_row"]["deps_unmet"], "the fixture must leave only deps-unmet product rows"
    assert "[deps-unmet:" in cp.stdout.split("Completion pick")[1], cp.stdout


def test_projection_parity_with_what_candidates_top_emits_at_its_render_point(tmp_path):
    """AC2's projection-parity pin, asserted ACROSS TWO INDEPENDENT RENDERS of the same vault: the
    gate's rows must carry the digest's own values, and its `rank` must be the row's real 1-based
    position in the full ranking. A rank-11 row can never render as position 6."""
    v = fx_unbuilt_present(tmp_path)
    full = json.loads(_cli(v, "--top", "0", "--json").stdout)["top"]
    order = {r["id"]: i + 1 for i, r in enumerate(full)}
    by_id = {r["id"]: r for r in full}

    gap = _gap(v, "--top", "5")
    assert gap["pickable_product"], gap
    for row in gap["pickable_product"]:
        ref = by_id[row["id"]]
        assert row["rank"] == order[row["id"]], (row, order[row["id"]])
        assert row["score"] == ref["score"] and row["effective_score"] == ref["effective_score"]
        assert row["title"] == ref["title"]
    pick = gap["pick_row"]
    ref = by_id[pick["id"]]
    assert pick["rank"] == order[pick["id"]] and pick["of"] == len(full)
    assert pick["effort"] == ref["effort"] and pick["deps_unmet"] == ref["deps_unmet"]
    assert pick["blast_radius"] == ref["blast_radius"]


def test_a_misspelled_area_is_undecidable_never_a_finished_product(tmp_path):
    """code-review M2 — THE TYPO TRAP. A misspelled `--area` filters `capabilities[]` to empty, the
    classifier maps `total == 0` to `empty-scope`, and the route table sends `empty-scope` to
    `route-add-item`: a typo would have told the user "every declared capability is built" and pointed
    them at the ONE irreversible act in this module. `area_lens.known` was computed and never consulted.

    Reproduced end-to-end at the production surface, then pinned in BOTH directions."""
    v = fx_two_area(tmp_path)
    typo = _gap(v, "--area", "corr")                    # near-miss of the real area 'core'
    assert "verdict" not in typo, typo
    assert typo["cause_kind"] == "area-unresolvable", typo
    assert "not a known area" in typo["error"], typo["error"]

    cp = _cli(v, "--completion-gap", "--area", "corr", "--top", "5")
    assert cp.returncode == 0, cp.stderr
    assert "route-add-item" not in cp.stdout, cp.stdout
    assert "UNDECIDABLE" in cp.stdout and "area-unresolvable" in cp.stdout, cp.stdout

    # the SPELLED-CORRECTLY area still classifies -- the guard must not blanket-refuse the lens
    assert _gap(v, "--area", "core")["verdict"] == "scope-exhausted"


def test_a_known_area_with_no_capabilities_is_undecidable_too(tmp_path):
    """The same wrong answer from a spelling that is NOT a typo: `known` unions the PS areas with the
    areas CANDIDATES assert, so an area only a chore asserts is `known` AND carries zero capabilities.
    Completeness of a thing with no members is not `all-built`."""
    v = fx_two_area(tmp_path)
    data = _read(v, "candidates.json")
    data["candidates"].append({
        "id": "SC-960", "title": "tidy-the-logs", "status": "candidate", "area": "observability",
        "source": [{"type": "reflection-residue", "ref": "slice-004"}],
        "priority": {"score": 3, "severity": "low", "effort": "XS"}, "dependencies": [],
    })
    _write(v, "candidates.json", data)
    payload = json.loads(_cli(v, "--completion-gap", "--area", "observability", "--json").stdout)
    assert payload["area_lens"]["known"] is True, "the fixture must make the area genuinely KNOWN"
    gap = payload["completion_gap"]
    assert "verdict" not in gap and gap["cause_kind"] == "area-unresolvable", gap
    assert "nothing for it to be complete about" in gap["error"], gap["error"]


def test_a_structurally_invalid_scope_never_reads_as_a_finished_product(tmp_path):
    """code-review m1 (must-not-defer #1) — the SILENT DEGRADE. product-scope.json can be perfectly
    valid JSON and the wrong SHAPE. Every reader filters non-dicts, so they AGREE on the empty set, the
    rollup's own id-set guard passes, `total` reads 0, and the gate would map that to `empty-scope` ->
    `route-add-item`. A scope it could not read must never report as a FINISHED product."""
    for broken in ({"items": {"PS-001": {}}},
                   {"items": "PS-001,PS-002"},
                   {"items": [{"id": "PS-001"}, "PS-002", 3]}):
        v = _replay(tmp_path / f"shape-{abs(hash(str(broken))) % 10000}")
        _write(v, "product-scope.json", broken)
        gap = _gap(v)
        assert "verdict" not in gap, (broken, gap)
        assert gap["cause_kind"] == "scope-malformed", (broken, gap)
        assert "structurally invalid" in gap["error"], gap["error"]


def test_the_rollup_cli_does_not_ship_capabilities_to_the_injections_that_never_read_it(tmp_path):
    """code-review m2 — `capabilities[]` is for the IN-PROCESS consumer. On the CLI it rode two
    skill-LOAD injections (/pulse, /slice) that read only the aggregates, at 27-32% of the envelope and
    growing linearly with the product. The in-process contract is untouched."""
    v = fx_unbuilt_present(tmp_path)
    script = PLUGIN_ROOT / "scripts" / "lib" / "product_rollup.py"
    default = json.loads(_run(script, "--vault", v, "--json").stdout)
    assert "capabilities" not in default, "the default CLI envelope must not carry the array"
    assert default["whole_app"]["total"] == 9, "the aggregates the injections DO read are unchanged"
    opted_in = json.loads(_run(script, "--vault", v, "--json", "--capabilities").stdout)
    assert len(opted_in["capabilities"]) == 9
    # the in-process path -- the one the gate actually uses -- is unaffected
    assert len(product_rollup.compute_rollup(v)["capabilities"]) == 9


def test_the_explicit_intent_trigger_is_a_non_flag_argument(tmp_path):
    """code-review M1 — no test pinned the trigger condition, and the guard was `[ -n "$ARG" ]`, i.e.
    ANY argument. `/slice --area <NAME>` is a documented invocation of the same skill, so
    `${ARGUMENTS[0]}` is routinely the literal `--area`: the guard fired, the gate returned
    `headline-only`, and the routing was disabled on the whole area-scoped pick path.

    Driven as SHELL, against the real SKILL.md block, over the invocations /slice actually receives."""
    import re
    import subprocess as sp
    bash = _posix_bash()
    text = SLICE_SKILL.read_text(encoding="utf-8")
    block = next(b for b in re.findall(r"```bash\n(.*?)```", text, re.DOTALL) if "HAS_INTENT=" in b)
    probe = block.split('if [ "$HAS_INTENT"')[0] + '\nprintf "%s" "$HAS_INTENT"\n'

    def fires_for(*argv):
        script = "set -- " + " ".join(f"'{a}'" for a in argv) + "\nARGUMENTS=(\"$@\")\n" + probe
        cp = sp.run([bash, "-c", script], capture_output=True, text=True, timeout=60)
        assert cp.returncode == 0, cp.stderr
        return cp.stdout.strip() == "1"

    assert fires_for("--area", "payments") is False, (
        "a value-taking flag's VALUE is not a description -- /slice --area payments must NOT read as "
        "explicit intent, or the routing is dead on the whole area-scoped pick path")
    assert fires_for("--area") is False
    assert fires_for("--component", "payments") is False
    assert fires_for("--area=payments") is False
    assert fires_for() is False
    assert fires_for("fix the thumbnail orientation bug") is True
    assert fires_for("--area", "payments", "add refunds") is True, (
        "an area lens AND a description together is still explicit intent")


def test_area_scoped_population_is_computed_over_the_filtered_set(tmp_path):
    """AC2 — under `--area` the WHOLE population is filtered: pickable_product, unbuilt, done, total
    and the headline. `/slice --area <complete-area>` must NOT route on another area's capability."""
    v = fx_two_area(tmp_path)
    scoped = _gap(v, "--area", "core")
    assert scoped["verdict"] == "scope-exhausted" and scoped["reason"] == "all-built", scoped
    assert scoped["unbuilt"] == []
    assert scoped["recommendation"]["mode"] == "route-add-item"
    assert all(c["id"].startswith("PS-00") for c in scoped["unbuilt"])
    runtime = _gap(v, "--area", "runtime")
    assert runtime["verdict"] == "product-work-available", runtime
    assert {c["id"] for c in runtime["unbuilt"]} == {"PS-004", "PS-005", "PS-006", "PS-007",
                                                     "PS-008", "PS-009"}


# ── AC3 — the declared route table ───────────────────────────────────────────────────────────

def _env_for(verdict, reason, unbuilt=(), pickable=()):
    return {"verdict": verdict, "reason": reason, "done": 0, "total": len(unbuilt),
            "done_definition": product_rollup.DONE_DEFINITION,
            "unbuilt": [dict(u) for u in unbuilt], "pickable_product": [dict(p) for p in pickable],
            "dangling": [], "headline": "x", "area_scope": None}


def _cap(iid, bucket, state=None, title="t"):
    return {"id": iid, "title": title, "state": state or bucket, "bucket": bucket}


ROUTE_TABLE = [
    ("product-work-available", "unbuilt-present", [_cap("PS-001", "in_progress")], False, "product-pick"),
    ("scope-exhausted", "all-built", [], False, "route-add-item"),
    ("scope-exhausted", "empty-scope", [], False, "route-add-item"),
    ("scope-exhausted", "none-pickable", [_cap("PS-001", "no_children")], False, "route-materialize"),
    ("scope-exhausted", "none-pickable", [_cap("PS-001", "in_progress")], False, "route-coordinate"),
    ("scope-exhausted", "none-pickable", [_cap("PS-001", "unknown")], False, "route-repair"),
    ("scope-exhausted", "none-pickable", [_cap("PS-001", "rejected_only", state="done")], False,
     "route-rescope"),
    ("scope-absent", "absent-no-file", [], False, "route-discover"),
    ("scope-exhausted", "all-built", [], True, "headline-only"),
    ("product-work-available", "unbuilt-present", [_cap("PS-001", "in_progress")], True, "headline-only"),
]


@pytest.mark.parametrize("verdict,reason,unbuilt,intent,mode", ROUTE_TABLE)
def test_route_table_and_decline_always_offered(verdict, reason, unbuilt, intent, mode):
    """AC3 — one row per (verdict, reason, worst-unbuilt-state, explicit_intent) combination."""
    pickable = ([{"id": "SC-001", "title": "t", "score": 5, "effective_score": 5, "rank": 1}]
                if verdict == "product-work-available" else [])
    rows = ([{"id": "SC-001", "title": "t", "score": 5, "effective_score": 5, "effort": "L",
              "rank": 1, "owner_ref": "PS-001", "area_source": None, "unmet_deps": []}]
            if pickable else [])
    rec = completion_gap.recommend(_env_for(verdict, reason, unbuilt, pickable), rows,
                                   explicit_intent=intent)
    assert rec["mode"] == mode, rec
    assert set(rec) == {"mode", "pick_id", "offer_decline", "rationale"}, rec
    if mode in completion_gap.HALTING_MODES:
        assert rec["offer_decline"] is True, (
            "must-not-defer #3: a gate that cannot be declined is a LOCK on the user's own backlog")
        assert completion_gap.suppress_governor(mode) is True
    else:
        assert rec["offer_decline"] is False
        assert completion_gap.suppress_governor(mode) is False
    assert rec["rationale"], "a halt whose reasoning is invisible trains the user to click through it"


def test_mixed_unbuilt_states_route_on_the_worst_state(tmp_path):
    """B2's MIXED-state rule: unknown > rejected_only > no_children > in_progress, ties broken by the
    rollup's own capabilities[] order."""
    mixed = [_cap("PS-001", "in_progress"), _cap("PS-002", "no_children"), _cap("PS-003", "unknown")]
    rec = completion_gap.recommend(_env_for("scope-exhausted", "none-pickable", mixed), [],
                                   explicit_intent=False)
    assert rec["mode"] == "route-repair", rec
    rec2 = completion_gap.recommend(
        _env_for("scope-exhausted", "none-pickable",
                 [_cap("PS-001", "in_progress"), _cap("PS-002", "no_children")]), [],
        explicit_intent=False)
    assert rec2["mode"] == "route-materialize", rec2
    assert completion_gap.STATE_PRECEDENCE == ("unknown", "rejected_only", "no_children",
                                               "in_progress")


def test_route_add_item_fires_only_on_scope_exhausted_with_empty_unbuilt():
    """AC3 — `scope-absent` NEVER offers the mint: `add-item` exits 4 `no-scope` there (measured), so
    the halt must route to /discover + /slice-candidates --product instead."""
    absent = completion_gap.recommend(_env_for("scope-absent", "absent-no-file"), [],
                                      explicit_intent=False)
    assert absent["mode"] == "route-discover"
    assert "add-item" not in absent["rationale"]
    exhausted = completion_gap.recommend(_env_for("scope-exhausted", "all-built"), [],
                                         explicit_intent=False)
    assert exhausted["mode"] == "route-add-item"
    assert "/slice-candidates --add-item" in exhausted["rationale"]


def test_the_decline_path_yields_the_ordinary_ranked_list(tmp_path):
    """The halt is OVERRIDABLE by construction: the ranked digest is rendered on EVERY halting mode, so
    declining costs nothing and returns the ordinary pick."""
    cp = _cli(fx_all_built(tmp_path), "--completion-gap", "--top", "5")
    assert cp.returncode == 0, cp.stderr
    assert "Top picks" in cp.stdout, cp.stdout
    assert "SC-010" in cp.stdout


def test_top_scored_exhaust_is_never_the_recommendation_on_a_halting_mode(tmp_path):
    v = fx_all_built(tmp_path)
    gap = _gap(v)
    assert gap["recommendation"]["mode"] in completion_gap.HALTING_MODES
    assert gap["recommendation"]["pick_id"] is None, (
        "a halting mode must recommend NO candidate — SC-010 is the top-scored EXHAUST row")


def test_rendered_payload_carries_offer_decline_true_on_every_halting_mode(tmp_path):
    """AC3 — asserted on the RENDERED payload, not only on the unit return. That is what makes
    must-not-defer #3 enforceable rather than a sentence in prose."""
    for name in ("all-built", "none-pickable", "scope-absent", "rejected-only"):
        gap = _gap(ALL_FIXTURES[name](tmp_path / f"halt-{name}"))
        rec = gap["recommendation"]
        assert rec["mode"] in completion_gap.HALTING_MODES, (name, rec)
        assert rec["offer_decline"] is True, (name, rec)
        assert gap["suppress_governor"] is True, (name, gap)


def test_exactly_one_instruction_reaches_the_pick_surface(tmp_path):
    """M14 — at `done == 0` with `pickable_product[] == []` the completeness governor AND the route
    would both fire. The gate suppresses the governor, so the surface carries exactly ONE."""
    v = fx_none_pickable(tmp_path)
    gap = _gap(v)
    assert gap["done"] == 0 and gap["pickable_product"] == []
    assert gap["suppress_governor"] is True
    env = product_rollup.compute_rollup(v)
    assert env.get("governor"), "the fixture must actually trip the governor, else this is vacuous"
    cp = _cli(v, "--completion-gap", "--top", "5")
    assert "completeness governor" not in cp.stdout, cp.stdout
    assert "route-coordinate" in cp.stdout or "in flight" in cp.stdout, cp.stdout


def test_explicit_intent_raises_no_question(tmp_path):
    """The alert-fatigue carve-out (INV-7 `fails` on the HUMAN axis): the user already named the work."""
    gap = _gap(fx_all_built(tmp_path), "--explicit-intent")
    assert gap["recommendation"]["mode"] == "headline-only"
    assert gap["suppress_governor"] is False
    assert gap["headline"], "the headline is still rendered — only the QUESTION is suppressed"


# ── AC2/AC3 — the seam is actually wired (BC-PROJ-10) ────────────────────────────────────────

def test_every_slice_invocation_passes_the_flag():
    """slice-080's own lesson: an additive-optional surface is INERT until something PRODUCES it. Both
    `candidates_top` invocations in /slice must carry the flag, or the gate ships dead."""
    text = SLICE_SKILL.read_text(encoding="utf-8")
    invocations = [ln for ln in text.splitlines() if "candidates_top.py" in ln]
    assert invocations, "no candidates_top invocation found — the guard would pass vacuously"
    missing = [ln.strip() for ln in invocations if "--completion-gap" not in ln]
    assert not missing, f"/slice invokes candidates_top WITHOUT --completion-gap: {missing}"


def test_the_text_header_carries_the_gate_not_only_json(tmp_path):
    """`candidates_top.py`'s own comment pins the premise: EVERY production invocation of this digest is
    text-mode (the /slice `!`-injection). A verdict that exists only in --json is a verdict the pick
    surface never shows (must-not-defer #5)."""
    cp = _cli(fx_all_built(tmp_path), "--completion-gap", "--top", "5")
    assert cp.returncode == 0, cp.stderr
    assert "COMPLETION GAP" in cp.stdout, cp.stdout
    assert "2/2" in cp.stdout and product_rollup.DONE_DEFINITION in cp.stdout
    assert "/slice-candidates --add-item" in cp.stdout


def test_default_off_is_byte_identical(tmp_path):
    """The gate is an OPT-IN flag (DD1 variant B, GO 5/5): default-OFF must add no key and change no
    byte, exactly as `--area`/`area_lens` already does."""
    v = fx_unbuilt_present(tmp_path)
    text = _cli(v, "--top", "5")
    js = _cli(v, "--top", "5", "--json")
    assert "completion_gap" not in js.stdout and "COMPLETION GAP" not in text.stdout
    assert json.loads(js.stdout).keys() == {"action", "project", "counts", "top", "blocked",
                                            "in_flight"}


def test_route_add_item_halt_names_the_skill_and_slice_invokes_no_mutating_verb(tmp_path):
    """[[ADR-152]] — /slice ROUTES. It elicits nothing, stages no payload, runs NO `product_scope` verb
    in any form (not even --dry-run), prints no command and writes nothing on this branch. That is what
    lets the ADR-067 section-1 guard stay a PLAIN unconditional ban ([[ADR-153]])."""
    gap = _gap(fx_all_built(tmp_path))
    assert gap["recommendation"]["mode"] == "route-add-item"
    assert "/slice-candidates --add-item" in gap["recommendation"]["rationale"]

    text = SLICE_SKILL.read_text(encoding="utf-8")
    assert "/slice-candidates --add-item" in text, "the halt must NAME the skill it routes to"
    # AC3's actual clause: no `add-item` token ADJACENT to `product_scope.py`, IN ANY FORM. Asserted at
    # LINE level rather than as three quoted spellings, so a variant spacing cannot slip past.
    offenders = [ln.strip() for ln in text.splitlines()
                 if "add-item" in ln and "product_scope" in ln]
    assert not offenders, (
        f"/slice names the mutating verb: {offenders}. Under [[ADR-152]] it ROUTES and never invokes "
        f"it, which is what lets the ADR-067 section-1 guard stay a PLAIN unconditional ban.")

    # THE POSITIVE TWIN (BC-PROJ-10): the receiving skill really owns the mode, or /slice routes into a
    # dead end. Asserted at LINE level, because the shipped invocation carries `--vault` BETWEEN the
    # script path and the verb -- see `_mutating_hits`' recorded limitation in test_product_scope_wiring.
    sc = SLICE_CANDIDATES_SKILL.read_text(encoding="utf-8")
    assert "--add-item" in sc and "ADDITEM=1" in sc, "one parsing mechanism, same as --product/--obo"
    invocations = [ln for ln in sc.splitlines() if "product_scope.py" in ln and "add-item" in ln]
    assert invocations, "/slice-candidates must actually INVOKE the verb it is routed to"
    assert any("--dry-run" in ln for ln in invocations), "the preview must be a real invocation"
    assert any("--dry-run" not in ln for ln in invocations), "and so must the mutating call"
    assert all('--vault "$VAULT"' in ln for ln in invocations), (
        "[[ADR-152]] d5: --vault is resolved IN-BLOCK and never leaves the harness -- without it the "
        "one production invocation of an irreversible verb can resolve ANOTHER project's vault")


# ── the gate registry (measurement spine) ────────────────────────────────────────────────────

def test_completion_gap_is_registered_as_a_low_contact_informational_gate():
    assert gate_log.GATE_CONTACT["completion-gap"] == "low"
    assert "completion-gap" in gate_log.INFORMATIONAL_GATES
    assert "completion-gap" in triage_precision.INFORMATIONAL_GATES, (
        "a missing entry is NOT an error — it is a silent /pulse mis-render, hence this parity test")


def test_second_informational_gate_does_not_inflate_design_tournament_runs():
    """The regression a second informational gate would otherwise cause: `gate_summary` keyed the
    design-tournament aggregate on INFORMATIONAL_GATES, which was a set of one."""
    entries = [
        {"gate": "design-tournament", "slice": "slice-001", "approach_divergence": "overlapping"},
        {"gate": "design-tournament", "slice": "slice-002", "approach_divergence": "disjoint"},
        {"gate": "completion-gap", "slice": "slice-003", "verdict": "scope-exhausted",
         "findings_count": 0},
    ]
    out = triage_precision.gate_summary(entries)
    assert out["design_tournament"]["runs"] == 2, out["design_tournament"]
    assert out["design_tournament"]["divergence"] == {"overlapping": 1, "disjoint": 1}
    assert triage_precision._DIVERGENCE_GATES == frozenset({"design-tournament"})
    # still EXCLUDED from the per-gate precision table (it raises no findings by design)
    assert "completion-gap" not in {g["gate"] for g in out["gates"]}


def test_the_gate_row_shape_is_acceptable_to_gate_log(tmp_path):
    """Step 5.7 ([[ADR-147]]) — POST-CLAIM. An unregistered gate exits 2, which is why the
    GATE_CONTACT key is load-bearing rather than decorative."""
    out = tmp_path / "row.json"
    cp = _run(PLUGIN_ROOT / "scripts" / "lib" / "gate_log.py",
              "--gate", "completion-gap", "--slice", "slice-102", "--verdict", "scope-exhausted",
              "--findings-count", "0", "--note", "decision=declined; reason=all-built; done=2/2",
              "--out", out)
    assert cp.returncode == 0, cp.stderr
    row = json.loads(out.read_text(encoding="utf-8"))
    assert row["gate"] == "completion-gap" and row["reality_contact"] == "low"


def test_the_decision_enum_retires_new_capability():
    """[[ADR-152]] d7 (round-4 M2). The row is POST-CLAIM and a user who follows ANY route-* mode —
    INCLUDING route-add-item — leaves /slice WITHOUT claiming, so the member had no producer. FBCD-1
    sweep: it must be gone from the skill's own contract too."""
    assert completion_gap.GATE_DECISIONS == ("product-pick", "declined", "explicit-intent")
    assert "new-capability" not in SLICE_SKILL.read_text(encoding="utf-8")


def test_no_route_mode_emits_a_gate_row():
    """The measurement residue, stated rather than hidden: the ABSENCE of a row is the honest record
    for every route-* mode. must-not-defer #6 is met for firing-vs-decline and NOT for accept-rate."""
    for mode in completion_gap.HALTING_MODES:
        assert completion_gap.emits_gate_row(mode) is False, mode
    assert completion_gap.emits_gate_row("product-pick") is True
    assert completion_gap.emits_gate_row("headline-only") is True


# ── dangling[] (INV-9) ───────────────────────────────────────────────────────────────────────

def test_dangling_is_live_filtered_and_never_counted_as_available_product_work(tmp_path):
    """A cut scope item leaves a permanent orphan (`product_scope.py`: the owner-deletion cascade 'is
    not even expressible'). Report the leak, never retract the candidate."""
    v = fx_unbuilt_present(tmp_path)
    data = _read(v, "candidates.json")
    data["candidates"].append({
        "id": "SC-950", "title": "orphan-row", "status": "candidate",
        "source": [{"type": "product-scope", "ref": "PS-777"}],
        "priority": {"score": 5, "severity": "medium", "effort": "L"}, "dependencies": [],
    })
    _write(v, "candidates.json", data)
    gap = _gap(v)
    assert {d["candidate"] for d in gap["dangling"]} == {"SC-950"}, gap["dangling"]
    assert all(set(d) == {"candidate", "ref"} for d in gap["dangling"])
    assert "SC-950" not in {r["id"] for r in gap["pickable_product"]}, (
        "a dangling owner_ref is never counted as available product work")
