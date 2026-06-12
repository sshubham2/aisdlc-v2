"""vault_admin.py export / import — vault durability + team handoff (roadmap §2.2)."""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "scripts/lib/vault_admin.py"


def _seed(vault: Path) -> None:
    (vault / "slices" / "archive").mkdir(parents=True)
    (vault / "risk-register.json").write_text(
        json.dumps({"_schema": "aisdlc/risk-register@1", "risks": [{"id": "R-1", "title": "café-test"}]},
                   ensure_ascii=False), encoding="utf-8")
    (vault / "slices" / "_index.json").write_text('{"active": [], "recent": []}', encoding="utf-8")


def test_export_then_import_roundtrip(run_script, vault, tmp_path):
    _seed(vault)
    out = tmp_path / "backup.tgz"
    r = run_script(SCRIPT, ["export", "--vault", vault, "--out", out], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    assert "exported" in r.stdout

    target = tmp_path / "restored-vault"
    r = run_script(SCRIPT, ["import", out, "--vault", target], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    restored = json.loads((target / "risk-register.json").read_text(encoding="utf-8"))
    assert restored["risks"][0]["title"] == "café-test"  # non-ASCII survives the round-trip
    assert (target / "slices" / "_index.json").is_file()
    assert "write-pin" in r.stdout  # the re-pin reminder


def test_import_refuses_non_empty_target_without_force(run_script, vault, tmp_path):
    _seed(vault)
    out = tmp_path / "backup.tgz"
    run_script(SCRIPT, ["export", "--vault", vault, "--out", out], cwd=tmp_path)

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "precious.json").write_text("{}", encoding="utf-8")
    r = run_script(SCRIPT, ["import", out, "--vault", target], cwd=tmp_path)
    assert r.returncode == 2
    assert "--force" in r.stderr
    assert (target / "precious.json").is_file()  # refused -> untouched

    r = run_script(SCRIPT, ["import", out, "--vault", target, "--force"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not (target / "precious.json").exists()  # --force REPLACES
    assert (target / "risk-register.json").is_file()


def test_export_missing_vault_is_usage_error(run_script, tmp_path):
    r = run_script(SCRIPT, ["export", "--vault", tmp_path / "nope", "--out", tmp_path / "x.tgz"],
                   cwd=tmp_path)
    assert r.returncode == 2
    assert "nothing to export" in r.stderr
