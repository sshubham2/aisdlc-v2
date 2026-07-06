"""scripts/lib/grounding_verify.py + _crg_grounding_probe.py (promoted from skills/release/scripts)
— independently verify /release's self-attested grounding tokens against
reality before doc-manifest.json records them (slice-015).

Written test-first (TF-1 / AC4). Exercises the verifier end-to-end via subprocess
(stdin JSON in, JSON report out), against a REAL tiny CRG graph built in a fixture
repo so the deterministic file_summary resolution is exercised for real.

Covers:
  AC1/AC2  : a real path-based token (file + symbol present) -> verified;
             a fabricated token (missing file / missing symbol) -> NOT verified.
  AC3      : two distinct unreachable fixtures (CRG import absent; graph missing)
             -> reason=source-unavailable + grounding_check.crg_reachable=false.
  m1       : symbol membership filters out the File node (absolute backslash path).
  M2       : healthy graph + unindexed path -> reason=not-indexed (distinct from
             symbol-absent) + grounding_check carries graph staleness.
  M3       : per-scheme semantics (vault: existence-only) + path-traversal reject.
  must-not-defer: malformed grounding -> all-unverified, never a crash/silent-pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# review sweep 2026-07: the grounding-verify pair was PROMOTED to scripts/lib
# (shared by /release + /drift-check; cross-skill reach forbidden).
VERIFY = REPO / "scripts" / "lib" / "grounding_verify.py"
PY = sys.executable
_BASE_GIT = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
}


def _crg_exe() -> str | None:
    cand = os.environ.get("AI_SDLC_CRG")
    if cand and Path(cand).exists():
        return cand
    scripts = Path(PY).parent
    for name in ("code-review-graph.exe", "code-review-graph"):
        if (scripts / name).exists():
            return str(scripts / name)
    return shutil.which("code-review-graph")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env={**os.environ, **_BASE_GIT})


def _run_verify(grounding, repo_root, vault_root=None, env=None):
    payload = json.dumps({"grounding": grounding, "repo_root": str(repo_root),
                          "vault_root": str(vault_root or repo_root)})
    p = subprocess.run([PY, str(VERIFY)], input=payload, capture_output=True,
                       text=True, env={**os.environ, **(env or {})})
    assert p.returncode == 0, f"verify exited {p.returncode}: {p.stderr}"
    return json.loads(p.stdout)


@pytest.fixture(scope="module")
def built_repo(tmp_path_factory):
    """A tiny git repo with a real CRG graph built in it."""
    crg = _crg_exe()
    if not crg:
        pytest.skip("code-review-graph CLI not found")
    root = tmp_path_factory.mktemp("crgrepo")
    (root / "pkg").mkdir()
    (root / "pkg" / "widget.py").write_text(
        "def make_widget(name):\n    return {'name': name}\n\n"
        "class Gadget:\n    def spin(self):\n        return 1\n",
        encoding="utf-8")
    # a second file with the SAME basename -> bare-filename ambiguity (m3/ambiguous)
    (root / "other").mkdir()
    (root / "other" / "widget.py").write_text("def twin():\n    return 2\n", encoding="utf-8")
    # slice-040: a UNIQUE file stem ('toolbox') whose only symbol is 'helper' -> exercises a
    # public_surface export that is a FILE STEM, not a symbol (the design-spike's load-bearing
    # build_backlog/vault_edit case: symbols UNION file-stems is required, symbols-only misses it).
    (root / "pkg" / "toolbox.py").write_text("def helper():\n    return 3\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    r = subprocess.run([crg, "build", "--repo", str(root)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"CRG build failed: {r.stderr[-400:]}")
    return root


# ---- graph-dependent (real CRG) --------------------------------------------

def test_real_token_verified(built_repo):
    g = {"readme": ["crg:pkg/widget.py::make_widget", "vault:concept.json"]}
    out = _run_verify(g, built_repo)
    doc = out["docs"]["readme"]
    assert "crg:pkg/widget.py::make_widget" in doc["verified"]
    assert out["grounding_check"]["crg_reachable"] is True
    assert out["grounding_check"]["public_surface_verified"] is False
    # graph built right after the commit -> not stale (tz-naive vs tz-aware compare guarded)
    assert out["grounding_check"]["graph_stale"] is False


def test_fabricated_symbol_not_verified(built_repo):
    g = {"readme": ["crg:pkg/widget.py::ghostFlag"]}
    out = _run_verify(g, built_repo)
    doc = out["docs"]["readme"]
    assert "crg:pkg/widget.py::ghostFlag" not in doc["verified"]
    reasons = {u["reason"] for u in doc["grounding_unverified"]}
    assert "symbol-absent" in reasons


def test_fabricated_file_not_indexed(built_repo):
    g = {"readme": ["crg:pkg/NOPE.py::whatever"]}
    out = _run_verify(g, built_repo)
    doc = out["docs"]["readme"]
    assert not doc["verified"]
    reasons = {u["reason"] for u in doc["grounding_unverified"]}
    assert "not-indexed" in reasons  # healthy graph, file absent -> NOT source-unavailable


def test_bare_filename_ambiguous(built_repo):
    g = {"readme": ["crg:widget.py::twin"]}
    out = _run_verify(g, built_repo)
    doc = out["docs"]["readme"]
    assert not doc["verified"]
    assert "ambiguous-match" in {u["reason"] for u in doc["grounding_unverified"]}


def test_file_node_not_counted_as_symbol(built_repo):
    # m1: the absolute-path File node must NOT satisfy a ::symbol membership check
    g = {"readme": ["crg:pkg/widget.py::widget.py"]}
    out = _run_verify(g, built_repo)
    assert "crg:pkg/widget.py::widget.py" not in out["docs"]["readme"]["verified"]


def test_graph_stale_when_head_newer(tmp_path):
    # M2: a commit dated AFTER the build -> graph_stale true (a visible WARNING, not a drop).
    crg = _crg_exe()
    if not crg:
        pytest.skip("code-review-graph CLI not found")
    root = tmp_path / "stale"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    if subprocess.run([crg, "build", "--repo", str(root)], capture_output=True).returncode != 0:
        pytest.skip("CRG build failed")
    (root / "pkg" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    fut = {**os.environ, **_BASE_GIT,
           "GIT_AUTHOR_DATE": "2030-01-01T00:00:00", "GIT_COMMITTER_DATE": "2030-01-01T00:00:00"}
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True, env={**os.environ, **_BASE_GIT})
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "future"], check=True, capture_output=True, env=fut)
    out = _run_verify({"readme": ["crg:pkg/a.py::f"]}, root)
    assert out["grounding_check"]["graph_stale"] is True
    # stale is a WARNING surfaced in grounding_check, NOT a reason to drop a still-resolvable token
    assert "crg:pkg/a.py::f" in out["docs"]["readme"]["verified"]


# ---- unreachable: two distinct fixtures (AC3) ------------------------------

def test_unreachable_graph_missing(tmp_path):
    root = tmp_path / "nograph"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "x.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    g = {"readme": ["crg:pkg/x.py::f"]}
    out = _run_verify(g, root)  # no CRG graph built -> unreachable
    assert out["grounding_check"]["crg_reachable"] is False
    assert "source-unavailable" in {u["reason"] for u in out["docs"]["readme"]["grounding_unverified"]}


def test_unreachable_crg_import_absent(tmp_path):
    # shadow code_review_graph with a module that raises ImportError, first on PYTHONPATH
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "code_review_graph.py").write_text('raise ImportError("forced absent")\n', encoding="utf-8")
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "x.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    env = {"PYTHONPATH": str(shadow) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    g = {"readme": ["crg:pkg/x.py::f"]}
    out = _run_verify(g, root, env=env)
    assert out["grounding_check"]["crg_reachable"] is False
    assert "source-unavailable" in {u["reason"] for u in out["docs"]["readme"]["grounding_unverified"]}


# ---- no-graph-needed: shape + per-scheme + traversal -----------------------

def test_malformed_grounding_all_unverified_no_crash(tmp_path):
    # grounding is a LIST not a map -> all-unverified, never crash
    p = subprocess.run([PY, str(VERIFY)],
                       input=json.dumps({"grounding": ["crg:pkg/x.py::f"],
                                         "repo_root": str(tmp_path)}),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["grounding_check"]["ran"] is True
    # nothing verified; the malformed input is reported
    flat = json.dumps(out)
    assert "malformed" in flat
    assert '"verified": [' not in flat or out.get("docs", {}) == {} or \
        all(not d.get("verified") for d in out.get("docs", {}).values())


def test_vault_token_existence_only(tmp_path):
    root = tmp_path / "r"
    (root / "vault").mkdir(parents=True)
    (root / "vault" / "concept.json").write_text("{}", encoding="utf-8")
    g = {"readme": ["vault:concept.json"]}
    out = _run_verify(g, root, vault_root=root / "vault")
    assert "vault:concept.json" in out["docs"]["readme"]["verified"]
    # a vault token WITH a ::symbol is malformed (no symbol to contain)
    g2 = {"readme": ["vault:concept.json::foo"]}
    out2 = _run_verify(g2, root, vault_root=root / "vault")
    assert "malformed" in {u["reason"] for u in out2["docs"]["readme"]["grounding_unverified"]}


def test_path_traversal_rejected(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    g = {"readme": ["file:../../../../etc/passwd"]}
    out = _run_verify(g, root)
    assert not out["docs"]["readme"]["verified"]
    assert "malformed" in {u["reason"] for u in out["docs"]["readme"]["grounding_unverified"]}


def test_file_symbol_word_boundary(tmp_path):
    # M1: file: symbol membership is WORD-BOUNDARY, not raw substring. A fragment of a real
    # identifier must NOT verify (raw `in` would false-accept); the exact word must.
    root = tmp_path / "r"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def real_func():\n    return 1\n", encoding="utf-8")
    out = _run_verify({"readme": ["file:pkg/a.py::eal_fun"]}, root)  # fragment of real_func
    doc = out["docs"]["readme"]
    assert "file:pkg/a.py::eal_fun" not in doc["verified"]
    assert "symbol-absent" in {u["reason"] for u in doc["grounding_unverified"]}
    out2 = _run_verify({"readme": ["file:pkg/a.py::real_func"]}, root)  # exact identifier
    assert "file:pkg/a.py::real_func" in out2["docs"]["readme"]["verified"]


def test_vault_root_unresolved_source_unavailable(tmp_path):
    # M2: an empty/missing vault_root must NOT silently retarget vault: tokens to repo_root
    # (a same-named repo file would be a false-accept) -> source-unavailable, fail-visible.
    root = tmp_path / "r"
    root.mkdir()
    (root / "concept.json").write_text("{}", encoding="utf-8")  # same-named file AT the repo root
    # construct the payload directly with an EMPTY vault_root (bypass _run_verify's repo_root default)
    payload = json.dumps({"grounding": {"readme": ["vault:concept.json"]},
                          "repo_root": str(root), "vault_root": ""})
    p = subprocess.run([PY, str(VERIFY)], input=payload, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)["docs"]["readme"]
    assert "vault:concept.json" not in doc["verified"]  # NOT resolved against repo_root
    assert "source-unavailable" in {u["reason"] for u in doc["grounding_unverified"]}


# ============================================================================
# slice-040: public_surface verification (exact membership, symbols UNION file-stems)
#   AC1  : verify() accepts a public_surface input and runs the SAME exact membership check.
#   AC2  : a fabricated export -> unverified with a reason from the existing enum.
#   AC3  : a genuine export/entry_point (symbol OR file stem) -> verified.
#   AC4  : public_surface_verified is true ONLY when the check ran against a reachable graph;
#          unsupplied / unreachable / malformed each keep it false, never crash (fail-closed).
#   AC5  : /release routes public_surface through verification before the manifest write.
#   m1   : entry_point label-strip ('cli: x' -> 'x'); empty residual -> malformed.
#   M1   : an unresolved label entry_point reads unverified but verified EXPORTS still flip the gate.
#   m2   : --names set_ready derives from the total_nodes>0 health gate, not a non-raising call.
#   M-add-2: a pure file-stem match (no symbol of that name) still verifies (the union is required).
# ============================================================================

PROBE = REPO / "scripts" / "lib" / "_crg_grounding_probe.py"


def _run_ps(public_surface, repo_root, grounding=None, env=None):
    """Run the verifier with a public_surface key in the payload."""
    payload = json.dumps({"grounding": grounding or {}, "public_surface": public_surface,
                          "repo_root": str(repo_root), "vault_root": str(repo_root)})
    p = subprocess.run([PY, str(VERIFY)], input=payload, capture_output=True,
                       text=True, env={**os.environ, **(env or {})})
    assert p.returncode == 0, f"verify exited {p.returncode}: {p.stderr}"
    return json.loads(p.stdout)


def _run_names_probe(repo_root, env=None):
    return subprocess.run([PY, str(PROBE), "--repo-root", str(repo_root), "--names"],
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


# ---- AC1 + AC3 + AC4(true): the anchor row (genuinely FAILS before impl) ----

def test_public_surface_real_export_verified(built_repo):
    ps = {"exports": ["make_widget", "Gadget"], "entry_points": []}
    out = _run_ps(ps, built_repo)
    block = out["public_surface"]
    assert "make_widget" in block["verified"]
    assert "Gadget" in block["verified"]
    assert out["grounding_check"]["public_surface_verified"] is True


# ---- AC3 / M-add-2: an export that is a FILE STEM (not a symbol) verifies ----

def test_public_surface_file_stem_export_verified(built_repo):
    # 'toolbox' is a unique file stem (pkg/toolbox.py) with NO symbol of that name; symbols-only
    # would false-negative it. This is the design-spike's load-bearing build_backlog/vault_edit case.
    out = _run_ps({"exports": ["toolbox"], "entry_points": []}, built_repo)
    assert "toolbox" in out["public_surface"]["verified"]
    assert out["grounding_check"]["public_surface_verified"] is True


# ---- AC2: a fabricated export is dropped with an enum reason (check still ran) ----

def test_public_surface_fabricated_unverified(built_repo):
    out = _run_ps({"exports": ["ghostExport"], "entry_points": []}, built_repo)
    block = out["public_surface"]
    assert "ghostExport" not in block["verified"]
    reasons = {u["reason"] for u in block["unverified"]}
    assert "not-indexed" in reasons
    assert reasons <= {"source-unavailable", "symbol-absent", "ambiguous-match",
                       "malformed", "file-absent", "not-indexed"}
    # 'check ran' semantics: a fabricated entry does NOT sink the gate.
    assert out["grounding_check"]["public_surface_verified"] is True


# ---- ambiguity: a name with >1 referent is dropped, not guessed ----

def test_public_surface_ambiguous_dropped(built_repo):
    # 'widget' is a file stem shared by pkg/widget.py + other/widget.py -> ambiguous.
    out = _run_ps({"exports": ["widget"], "entry_points": []}, built_repo)
    block = out["public_surface"]
    assert "widget" not in block["verified"]
    assert "ambiguous-match" in {u["reason"] for u in block["unverified"]}


# ---- m1: entry_point label strip; empty residual -> malformed ----

def test_public_surface_entry_point_label_strip(built_repo):
    out = _run_ps({"exports": [], "entry_points": ["cli: make_widget"]}, built_repo)
    assert "cli: make_widget" in out["public_surface"]["verified"]
    out2 = _run_ps({"exports": [], "entry_points": ["cli: "]}, built_repo)
    assert "malformed" in {u["reason"] for u in out2["public_surface"]["unverified"]}


# ---- M1: an unresolved label entry_point does not sink verified EXPORTS ----

def test_public_surface_entry_point_unresolved_keeps_flag(built_repo):
    out = _run_ps({"exports": ["make_widget"], "entry_points": ["cli: nonexistent_cmd"]}, built_repo)
    block = out["public_surface"]
    assert "make_widget" in block["verified"]
    assert "cli: nonexistent_cmd" not in block["verified"]
    assert out["grounding_check"]["public_surface_verified"] is True


# ---- AC4 / must-not-defer: unsupplied | unreachable | malformed -> false, no crash ----

def test_public_surface_verified_false_on_failures(built_repo, tmp_path):
    # (a) unsupplied (no public_surface key) -> stays false, backward compatible
    out_a = _run_verify({"readme": ["crg:pkg/widget.py::make_widget"]}, built_repo)
    assert out_a["grounding_check"]["public_surface_verified"] is False
    # (b) CRG unreachable (no graph) -> false + source-unavailable
    root = tmp_path / "nograph"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "x.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    out_b = _run_ps({"exports": ["f"], "entry_points": []}, root)
    assert out_b["grounding_check"]["public_surface_verified"] is False
    assert "source-unavailable" in {u["reason"] for u in out_b["public_surface"]["unverified"]}
    # (c) malformed public_surface (a list, not a dict) -> false, malformed, NEVER crash
    payload = json.dumps({"grounding": {}, "public_surface": ["not", "a", "dict"],
                          "repo_root": str(built_repo), "vault_root": str(built_repo)})
    p = subprocess.run([PY, str(VERIFY)], input=payload, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out_c = json.loads(p.stdout)
    assert out_c["grounding_check"]["public_surface_verified"] is False


# ---- m2: --names builds the reality set; set_ready via the total_nodes>0 health gate ----

def test_names_probe_real_graph(built_repo):
    p = _run_names_probe(built_repo)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["reachable"] is True
    assert "make_widget" in out["names"]          # a function symbol
    assert "Gadget" in out["names"]               # a class symbol
    assert "toolbox" in out["stems"]              # a unique file stem
    assert "widget" in out["ambiguous_names"]     # stem shared by two files


def test_names_probe_unbuilt_graph_not_ready(tmp_path):
    # m2: GraphStore(missing-db) silently returns 0 nodes -> reachable MUST be false via the
    # total_nodes>0 health gate, NOT 'get_all_nodes did not raise'.
    # Requires the code_review_graph package; without it the probe exits 3 (AC3 early-exit) before
    # reaching the health-gate logic this test exercises.
    try:
        import code_review_graph  # noqa: F401
    except ImportError:
        pytest.skip("code_review_graph not installed")
    root = tmp_path / "nograph"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "x.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    p = _run_names_probe(root)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["reachable"] is False


# ---- AC5: /release wires public_surface through verification + persists the sibling ----

def test_product_doc_wires_public_surface():
    skill = (REPO / "skills" / "release" / "SKILL.md").read_text(encoding="utf-8")
    # Step 4 / M-add-1: the manifest write persists the unverified sibling (a NEW token; not
    # present before this slice) -> proves the wiring was updated end-to-end.
    assert "public_surface_unverified" in skill


# ---- m3: an 'X.py'-shaped export resolves to the real stem; a dotted non-code name stays fail-closed ----

def test_public_surface_export_with_extension_verified(built_repo):
    out = _run_ps({"exports": ["toolbox.py"], "entry_points": []}, built_repo)
    assert "toolbox.py" in out["public_surface"]["verified"]   # toolbox.py -> stem 'toolbox' (real)
    out2 = _run_ps({"exports": ["toolbox.nonexistent"], "entry_points": []}, built_repo)
    assert "toolbox.nonexistent" not in out2["public_surface"]["verified"]  # non-code ext NOT stripped
    assert "not-indexed" in {u["reason"] for u in out2["public_surface"]["unverified"]}


# ---- m4: endpoints are out of scope -- never enter the verified anchor ----

def test_public_surface_endpoints_not_verified(built_repo):
    # endpoints are runtime routes, not code symbols -> the verifier ignores them; they never appear
    # in verified[], and a verified export still flips the gate (the full snapshot keeps endpoints, M-add-1).
    out = _run_ps({"exports": ["make_widget"], "entry_points": [], "endpoints": ["GET /widgets"]}, built_repo)
    block = out["public_surface"]
    assert "GET /widgets" not in block["verified"]
    assert "make_widget" in block["verified"]
    assert out["grounding_check"]["public_surface_verified"] is True


# ============================================================================
# slice-053: batch grounding probe (ONE CRG import + one stats call for N crg tokens)
#   AC1  : _crg_grounding_probe.py --batch resolves N>1 tokens in ONE process -> a per-token map.
#   AC2/AC5: verify() issues exactly ONE --batch subprocess for N>=2 crg tokens (0 when none).
#   AC3  : behavior-preserving -- the whole existing golden suite above stays green; malformed /
#          path-traversal tokens are classified BEFORE any batch probe (never reach it).
#   AC4  : fail-closed -- batch unreachable / None -> every crg token source-unavailable.
#   M1   : the batch map key is the FULL token; two tokens sharing a path but differing symbol each
#          get their own verdict.
#   M2   : a per-request exception -> present key (not-indexed under a reachable batch); an ABSENT
#          key -> source-unavailable (a bug, fail-closed).
#   M-add-1: Phase C checks batch.reachable FIRST -> all deferred source-unavailable before any lookup.
#   m1   : no crg tokens -> the batch probe is skipped entirely (zero spawns).
#   m2   : the batch queries query_graph ONCE per DISTINCT path (same-path/2-symbol collapse).
#   M-add-2: --path stdout byte-shape is locked (no 'results' key; the batch-of-one wrapper).
# ============================================================================

# in-process import of both scripts (their own bootstrap adds the plugin root to sys.path for
# scripts.lib._stdout; inserting the scripts dir lets us import the modules by bare name).
_SCRIPTS = REPO / "scripts" / "lib"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import grounding_verify as gv          # noqa: E402
import _crg_grounding_probe as probe   # noqa: E402


class _FakeProc:
    def __init__(self, stdout, rc=0):
        self.stdout = stdout
        self.returncode = rc
        self.stderr = ""


def _make_fake_run(counter, batch_results=None):
    """A subprocess.run stand-in for grounding_verify: a healthy --health, a counted --batch that
    (by default) verifies every requested token, an unreachable --names, and an empty git proc."""
    def fake_run(cmd, **kw):
        args = list(cmd)
        if "--health" in args:
            return _FakeProc(json.dumps({"reachable": True, "total_nodes": 10, "last_updated": None}))
        if "--batch" in args:
            counter["batch"] += 1
            reqs = json.loads(kw.get("input") or "[]")
            if batch_results is not None:
                return _FakeProc(json.dumps(batch_results))
            results = {r["token"]: {"file_resolved": True, "symbol_present": True, "ambiguous": False}
                       for r in reqs}
            return _FakeProc(json.dumps({"reachable": True, "results": results}))
        if "--names" in args:
            return _FakeProc(json.dumps({"reachable": False, "names": [], "stems": [],
                                         "ambiguous_names": [], "total_nodes": 10}))
        return _FakeProc("", rc=0)  # git / anything else
    return fake_run


# ---- AC2 / AC5 / M3: exactly ONE --batch subprocess for N>=2 crg tokens across docs ----

def test_one_batch_for_n_tokens(monkeypatch):
    counter = {"batch": 0}
    monkeypatch.setattr(gv.subprocess, "run", _make_fake_run(counter))
    grounding = {"readme": ["crg:pkg/a.py::x", "crg:pkg/b.py::y"], "guide": ["crg:pkg/c.py::z"]}
    out = gv.verify({"grounding": grounding, "repo_root": ".", "vault_root": "."})
    assert counter["batch"] == 1  # M3: one --batch for 3 crg tokens over 2 docs
    assert "crg:pkg/a.py::x" in out["docs"]["readme"]["verified"]
    assert "crg:pkg/c.py::z" in out["docs"]["guide"]["verified"]


# ---- m1: no crg tokens -> the batch probe is skipped entirely ----

def test_zero_batch_when_no_crg_tokens(monkeypatch):
    counter = {"batch": 0}
    monkeypatch.setattr(gv.subprocess, "run", _make_fake_run(counter))
    gv.verify({"grounding": {"readme": ["vault:concept.json", "file:pkg/a.py"]},
               "repo_root": ".", "vault_root": "."})
    assert counter["batch"] == 0


# ---- AC3: malformed / path-traversal tokens are classified BEFORE any batch probe ----

def test_malformed_and_traversal_never_batch(monkeypatch):
    counter = {"batch": 0}
    monkeypatch.setattr(gv.subprocess, "run", _make_fake_run(counter))
    grounding = {"readme": ["crg:../../etc/passwd::x", "crg:*.py", "notoken"]}
    out = gv.verify({"grounding": grounding, "repo_root": ".", "vault_root": "."})
    assert counter["batch"] == 0  # every token short-circuits to malformed pre-probe
    assert {u["reason"] for u in out["docs"]["readme"]["grounding_unverified"]} == {"malformed"}


# ---- AC4 / M-add-1: batch unreachable -> EVERY deferred crg token source-unavailable ----

def test_batch_unreachable_all_source_unavailable(monkeypatch):
    monkeypatch.setattr(gv, "_probe",
                        lambda root, args: {"reachable": True, "total_nodes": 5, "last_updated": None}
                        if "--health" in args else None)
    monkeypatch.setattr(gv, "_batch_probe", lambda root, reqs: {"reachable": False, "results": {}})
    out = gv.verify({"grounding": {"d": ["crg:pkg/a.py::x", "crg:pkg/b.py::y"]},
                     "repo_root": ".", "vault_root": "."})
    reasons = [u["reason"] for u in out["docs"]["d"]["grounding_unverified"]]
    assert reasons == ["source-unavailable", "source-unavailable"]
    assert not out["docs"]["d"]["verified"]


def test_batch_probe_none_all_source_unavailable(monkeypatch):
    # AC4: the batch probe itself failing (None -> non-zero exit / empty stdout) is also fail-closed.
    monkeypatch.setattr(gv, "_probe",
                        lambda root, args: {"reachable": True, "total_nodes": 5, "last_updated": None}
                        if "--health" in args else None)
    monkeypatch.setattr(gv, "_batch_probe", lambda root, reqs: None)
    out = gv.verify({"grounding": {"d": ["crg:pkg/a.py::x"]}, "repo_root": ".", "vault_root": "."})
    assert out["docs"]["d"]["grounding_unverified"][0]["reason"] == "source-unavailable"
    assert not out["docs"]["d"]["verified"]


# ---- M2: per-request exception -> not-indexed (present key); absent key -> source-unavailable ----

def test_batch_per_request_exception_not_indexed(monkeypatch):
    monkeypatch.setattr(gv, "_probe",
                        lambda root, args: {"reachable": True, "total_nodes": 5, "last_updated": None}
                        if "--health" in args else None)
    monkeypatch.setattr(gv, "_batch_probe", lambda root, reqs: {"reachable": True, "results": {
        "crg:pkg/a.py::x": {"file_resolved": False, "symbol_present": None, "ambiguous": False},
        "crg:pkg/b.py::y": {"file_resolved": True, "symbol_present": True, "ambiguous": False},
    }})
    out = gv.verify({"grounding": {"d": ["crg:pkg/a.py::x", "crg:pkg/b.py::y"]},
                     "repo_root": ".", "vault_root": "."})
    doc = out["docs"]["d"]
    assert "crg:pkg/b.py::y" in doc["verified"]
    assert {"token": "crg:pkg/a.py::x", "reason": "not-indexed"} in doc["grounding_unverified"]


def test_batch_absent_key_source_unavailable(monkeypatch):
    monkeypatch.setattr(gv, "_probe",
                        lambda root, args: {"reachable": True, "total_nodes": 5, "last_updated": None}
                        if "--health" in args else None)
    monkeypatch.setattr(gv, "_batch_probe", lambda root, reqs: {"reachable": True, "results": {}})
    out = gv.verify({"grounding": {"d": ["crg:pkg/a.py::x"]}, "repo_root": ".", "vault_root": "."})
    assert out["docs"]["d"]["grounding_unverified"][0]["reason"] == "source-unavailable"


# ---- AC1 / M1: real db, N>1, same path + two symbols -> a per-token map, each own verdict ----

def test_batch_probe_same_path_two_symbols(built_repo):
    reqs = [{"token": "T1", "path": "pkg/widget.py", "symbol": "make_widget"},
            {"token": "T2", "path": "pkg/widget.py", "symbol": "ghostFlag"}]
    p = subprocess.run([PY, str(PROBE), "--repo-root", str(built_repo), "--batch"],
                       input=json.dumps(reqs), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["reachable"] is True
    assert set(out["results"].keys()) == {"T1", "T2"}       # keyed by the caller-minted token
    assert out["results"]["T1"]["symbol_present"] is True   # make_widget present
    assert out["results"]["T2"]["symbol_present"] is False  # ghostFlag absent, SAME file
    assert out["results"]["T1"]["file_resolved"] is True
    assert out["results"]["T2"]["file_resolved"] is True


# ---- m2: the batch queries query_graph ONCE per DISTINCT path ----

def test_batch_queries_once_per_distinct_path(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "_query_path", lambda q, root, target: calls.append(target) or [])
    reqs = [{"token": "T1", "path": "pkg/widget.py", "symbol": "a"},
            {"token": "T2", "path": "pkg/widget.py", "symbol": "b"},
            {"token": "T3", "path": "pkg/other.py", "symbol": "c"}]
    out = probe.resolve_batch(object(), "/repo", reqs, reachable=True)
    assert calls.count("pkg/widget.py") == 1  # same path -> queried once despite two requests
    assert calls.count("pkg/other.py") == 1
    assert set(out["results"].keys()) == {"T1", "T2", "T3"}


def test_batch_unreachable_empty_results():
    out = probe.resolve_batch(object(), "/repo",
                              [{"token": "T1", "path": "pkg/a.py", "symbol": "x"}], reachable=False)
    assert out == {"reachable": False, "results": {}}


# ---- M-add-2: --path stdout byte-shape is locked (no 'results' key) ----

def test_path_probe_byte_shape(built_repo):
    p = subprocess.run([PY, str(PROBE), "--repo-root", str(built_repo),
                        "--path", "pkg/widget.py", "--symbol", "make_widget"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert set(out.keys()) == {"reachable", "file_resolved", "symbol_present",
                               "ambiguous", "total_nodes", "last_updated"}
    assert "results" not in out
