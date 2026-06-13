"""Tests for scripts/lib/crg_isolate.py — the env-var graph-isolation guard (slice-006, AC3).

Hermetic by design: these tests NEVER write the global CRG registry (the exact hygiene the guard
enforces). The VCS-root check needs nothing; the registry-shadow + verify checks fake the CRG
registry/incremental lookups via sys.modules so no real graph is built and no global state changes.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "lib" / "crg_isolate.py"


def _load():
    spec = importlib.util.spec_from_file_location("crg_isolate_under_test", GUARD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fake_registry(data_dir_for_repo):
    """Inject a fake code_review_graph.registry whose Registry returns data_dir_for_repo."""
    reg = types.SimpleNamespace(
        Registry=lambda: types.SimpleNamespace(get_data_dir_for_repo=lambda p: data_dir_for_repo)
    )
    return reg


def test_check_rejects_non_vcs_target(tmp_path):
    # M-add-3: a non-VCS target is not isolable via CRG_DATA_DIR -> exit 1. Fully hermetic.
    m = _load()
    nonvcs = tmp_path / "plain"
    nonvcs.mkdir()
    assert m._check(nonvcs, tmp_path / "out" / ".code-review-graph") == 1


def test_check_accepts_clean_vcs_target(tmp_path, monkeypatch):
    pytest.importorskip("code_review_graph")
    m = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "code_review_graph.registry", _fake_registry(None))
    assert m._check(repo, tmp_path / "out" / ".code-review-graph") == 0


def test_check_detects_registry_shadow(tmp_path, monkeypatch):
    # M-add-1: a pre-existing registry mapping that differs from CRG_DATA_DIR shadows it -> exit 1.
    pytest.importorskip("code_review_graph")
    m = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    data_dir = (tmp_path / "out" / ".code-review-graph").resolve()
    shadow = str((tmp_path / "elsewhere" / ".code-review-graph").resolve())
    monkeypatch.setitem(sys.modules, "code_review_graph.registry", _fake_registry(shadow))
    assert m._check(repo, data_dir) == 1


def test_verify_match_and_mismatch(tmp_path, monkeypatch):
    # AC3: verify passes only when get_data_dir resolves to the intended isolated dir.
    pytest.importorskip("code_review_graph")
    m = _load()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    data_dir = (tmp_path / "out" / ".code-review-graph").resolve()
    data_dir.mkdir(parents=True)

    monkeypatch.setitem(sys.modules, "code_review_graph.incremental",
                        types.SimpleNamespace(get_data_dir=lambda r: data_dir))
    assert m._verify(repo, data_dir) == 0

    other = (tmp_path / "elsewhere").resolve()
    other.mkdir()
    monkeypatch.setitem(sys.modules, "code_review_graph.incremental",
                        types.SimpleNamespace(get_data_dir=lambda r: other))
    assert m._verify(repo, data_dir) == 1
