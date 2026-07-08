"""/sync sync_merge.py — mechanical preserve-merge contract (2026-07 review sweep).

The regenerate-vs-preserve table in skills/sync/SKILL.md is a hard contract; sync_merge
enforces it mechanically: ONLY whitelisted code-derived keys are replaced, everything
else passes through from the existing artifact, and a derived file touching a
non-whitelisted (human-authored) key is a refusal — never a partial merge.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "skills/sync/scripts/sync_merge.py"


def _vault_with(tmp_path: Path, rel: str, obj: dict) -> Path:
    v = tmp_path / "vault"
    target = v / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return v


def _derived(tmp_path: Path, obj: dict) -> Path:
    p = tmp_path / "derived.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


BASE_COMPONENT = {
    "_schema": "aisdlc/component@1",
    "name": "orders",
    "responsibility": "HUMAN: owns order lifecycle",
    "failure_modes": "HUMAN: double-charge on retry",
    "public_surface": ["create_order"],
    "depends_on": ["billing"],
}


def test_merge_replaces_only_derived_keys_and_preserves_human_fields(run_script, tmp_path):
    v = _vault_with(tmp_path, "components/orders.json", BASE_COMPONENT)
    d = _derived(tmp_path, {"public_surface": ["create_order", "cancel_order"], "depends_on": []})
    out = tmp_path / "merged.json"
    r = run_script(SCRIPT, ["--vault", v, "--file", "components/orders.json",
                            "--derived-file", d, "--out-file", out])
    assert r.returncode == 0, r.stderr
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["public_surface"] == ["create_order", "cancel_order"]
    assert merged["depends_on"] == []
    assert merged["responsibility"] == "HUMAN: owns order lifecycle"
    assert merged["failure_modes"] == "HUMAN: double-charge on retry"
    summary = json.loads(r.stdout)
    assert summary["changed"] == ["depends_on", "public_surface"]
    assert "responsibility" in summary["preserved"]
    # preview mode never touches the vault file
    assert json.loads((v / "components/orders.json").read_text(encoding="utf-8")) == BASE_COMPONENT


def test_refuses_non_whitelisted_key_without_partial_merge(run_script, tmp_path):
    v = _vault_with(tmp_path, "components/orders.json", BASE_COMPONENT)
    d = _derived(tmp_path, {"public_surface": [], "responsibility": "CLOBBER"})
    r = run_script(SCRIPT, ["--vault", v, "--file", "components/orders.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 2
    assert "responsibility" in r.stderr
    assert json.loads((v / "components/orders.json").read_text(encoding="utf-8")) == BASE_COMPONENT


def test_refuses_missing_base_new_artifacts_are_authored_fresh(run_script, tmp_path):
    v = tmp_path / "vault"
    (v / "components").mkdir(parents=True)
    d = _derived(tmp_path, {"public_surface": []})
    r = run_script(SCRIPT, ["--vault", v, "--file", "components/new.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 2
    assert "fresh via Write" in r.stderr


def test_refuses_unknown_top_dir(run_script, tmp_path):
    v = _vault_with(tmp_path, "decisions/ADR-001.json", {"id": "ADR-001"})
    d = _derived(tmp_path, {"fields": []})
    r = run_script(SCRIPT, ["--vault", v, "--file", "decisions/ADR-001.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 2
    assert "components" in r.stderr


def test_write_mode_writes_merged_into_vault(run_script, tmp_path):
    v = _vault_with(tmp_path, "schemas/user.json", {
        "_schema": "aisdlc/schema@1", "name": "user",
        "state_diagram": "HUMAN: mermaid here",
        "fields": [{"name": "id"}], "constraints": [],
    })
    d = _derived(tmp_path, {"fields": [{"name": "id"}, {"name": "last_login_at"}]})
    r = run_script(SCRIPT, ["--vault", v, "--file", "schemas/user.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 0, r.stderr
    merged = json.loads((v / "schemas/user.json").read_text(encoding="utf-8"))
    assert [f["name"] for f in merged["fields"]] == ["id", "last_login_at"]
    assert merged["state_diagram"] == "HUMAN: mermaid here"
    assert merged["constraints"] == []


def test_contracts_whitelist_covers_http_and_event_fields(run_script, tmp_path):
    v = _vault_with(tmp_path, "contracts/webhook.json", {
        "_schema": "aisdlc/contract@1", "name": "webhook",
        "notes": "HUMAN: idempotent via event id",
        "endpoints": [],
    })
    d = _derived(tmp_path, {"endpoints": [{"method": "POST", "path": "/webhook/stripe"}],
                            "event": "stripe.webhook", "payload_schema": {"type": "object"},
                            "delivery_guarantee": "at-least-once"})
    r = run_script(SCRIPT, ["--vault", v, "--file", "contracts/webhook.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 0, r.stderr
    merged = json.loads((v / "contracts/webhook.json").read_text(encoding="utf-8"))
    assert merged["notes"] == "HUMAN: idempotent via event id"
    assert merged["delivery_guarantee"] == "at-least-once"


def test_refuses_path_escape(run_script, tmp_path):
    v = _vault_with(tmp_path, "components/orders.json", BASE_COMPONENT)
    d = _derived(tmp_path, {"public_surface": []})
    r = run_script(SCRIPT, ["--vault", v, "--file", "../components/orders.json",
                            "--derived-file", d, "--write"])
    assert r.returncode == 2
