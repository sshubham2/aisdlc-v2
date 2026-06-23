"""skills/product-doc/scripts/harvest_degrade.py -- deterministic prose-doc
harvest-degrade classifier (slice-029, SC-013).

Written test-first (TF-1 / AC4). The degrade is decided DETERMINISTICALLY from
code_review_graph.tools.query.list_graph_stats counts (no MCP, no embedding
provider) -- ADR-019, after critique v1 BLOCKED the original MCP-only premise.

Covers (pure classify(), plain ints -- the real decision, always runs):
  AC1/AC2 : the four causes --
            graph-unavailable (CRG unimportable / total_nodes==0),
            embeddings-absent (public>0 & embeddings==0; remedy: crg embed),
            genuinely-empty   (public==0; NOT degraded),
            harvestable       (public>0 & embeddings>0; NOT degraded).
  M2      : public_nodes = total - File - Test catches a Type-only surface
            (a Type-heavy nodes_by_kind classifies embeddings-absent, not empty).
  M-add-1 : FAIL-CLOSED -- a missing / non-int total/public/embeddings (a stale
            cached probe omitting the new keys) -> graph-unavailable, never a
            silent pass / harvestable.
  AC4 CLI : end-to-end vs a real crg-build fixture graph for the degrade causes
            (embeddings-absent + graph-unavailable). The harvestable branch needs
            an embedding provider (absent in CI) -> covered by pure classify() only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # tests/bugs/X.py -> tests -> repo
SCRIPTS = REPO / "skills" / "product-doc" / "scripts"
HARVEST = SCRIPTS / "harvest_degrade.py"
PY = sys.executable
_BASE_GIT = {
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
}

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _classify(*args):
    # imported lazily so collection does not hard-fail before the module exists (test-first)
    import harvest_degrade
    return harvest_degrade.classify(*args)


# ---- pure classify(): the four causes (always runs) ------------------------

def test_graph_unavailable_total_zero():
    v = _classify(0, 0, 0)
    assert v["degraded"] is True
    assert v["cause"] == "graph-unavailable"


def test_embeddings_absent_is_a_degrade():
    # public symbols exist but no embeddings -> the semantic harvest can't retrieve them
    v = _classify(1033, 752, 0)
    assert v["degraded"] is True
    assert v["cause"] == "embeddings-absent"
    assert "embed" in v["message"].lower()  # actionable remedy named


def test_genuinely_empty_is_not_a_degrade():
    # a real graph with zero public symbols (docs/config-only) -> not degraded
    v = _classify(5, 0, 0)
    assert v["degraded"] is False
    assert v["cause"] == "genuinely-empty"


def test_harvestable_is_not_a_degrade():
    v = _classify(1033, 752, 900)
    assert v["degraded"] is False
    assert v["cause"] is None


def test_type_only_surface_classifies_embeddings_absent_not_empty():
    # M2: public_nodes = total - File - Test, so a Type-heavy surface (public>0) with no
    # embeddings is an embeddings-absent DEGRADE, not a silent genuinely-empty pass.
    # (caller computes public_nodes; here public=8 stands in for a Type-only nodes_by_kind)
    v = _classify(10, 8, 0)
    assert v["degraded"] is True
    assert v["cause"] == "embeddings-absent"


# ---- M-add-1: FAIL-CLOSED on missing / non-int keys ------------------------

@pytest.mark.parametrize("args", [
    (None, 5, 0),     # total absent (stale cached probe)
    (10, None, 0),    # public absent
    (10, 5, None),    # embeddings absent
    ("10", 5, 0),     # non-int (json string)
    (10, 5, True),    # bool is NOT a valid count (isinstance(True,int) trap)
])
def test_fail_closed_on_missing_or_non_int(args):
    v = _classify(*args)
    assert v["degraded"] is True
    assert v["cause"] == "graph-unavailable"


# ---- banner hygiene (m3): the message carries no machine-local paths -------

def test_message_has_no_absolute_paths():
    for v in (_classify(0, 0, 0), _classify(10, 5, 0), _classify(5, 0, 0)):
        assert ":\\" not in v["message"] and "/Users/" not in v["message"]


# ---- CLI end-to-end vs a real CRG fixture graph (degrade causes) -----------

def _crg_exe():
    cand = os.environ.get("AI_SDLC_CRG")
    if cand and Path(cand).exists():
        return cand
    scripts = Path(PY).parent
    for name in ("code-review-graph.exe", "code-review-graph"):
        if (scripts / name).exists():
            return str(scripts / name)
    return shutil.which("code-review-graph")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env={**os.environ, **_BASE_GIT})


def _run_cli(repo_root):
    p = subprocess.run([PY, str(HARVEST), "--repo-root", str(repo_root)],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"harvest_degrade exited {p.returncode}: {p.stderr}"
    return json.loads(p.stdout)


def test_cli_graph_unavailable_no_graph(tmp_path):
    # a real repo with NO crg graph built -> fail-closed graph-unavailable
    root = tmp_path / "nograph"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "x.py").write_text("def f():\n    return 0\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    out = _run_cli(root)
    assert out["degraded"] is True
    assert out["cause"] == "graph-unavailable"


def test_cli_embeddings_absent_on_built_graph(tmp_path):
    # a real crg-build graph (no embeddings produced by default) with public symbols
    # -> embeddings-absent degrade
    crg = _crg_exe()
    if not crg:
        pytest.skip("code-review-graph CLI not found")
    root = tmp_path / "built"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "widget.py").write_text(
        "def make_widget(name):\n    return {'name': name}\n\n"
        "class Gadget:\n    def spin(self):\n        return 1\n", encoding="utf-8")
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "i")
    if subprocess.run([crg, "build", "--repo", str(root)], capture_output=True).returncode != 0:
        pytest.skip("CRG build failed")
    out = _run_cli(root)
    assert out["degraded"] is True
    assert out["cause"] == "embeddings-absent"
    assert out["public_nodes"] > 0 and out["embeddings_count"] == 0
