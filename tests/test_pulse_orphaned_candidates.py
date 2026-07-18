"""slice-078 / SC-163 — surface out-of-scope (orphaned) candidates read-only in /pulse.

CONSUMER + unit tests for the pulse-side adapter (skills/pulse/scripts/orphaned_candidates.py).

Driven from ONE scope-PRESENT fixture (M-add-3) so the exit-0 orphan branch is actually
exercised — NOT the exit-4 no-scope path, which would launder green (B1 / M-add-3). The fixture is
a deliberately-cut capability:

    product-scope.json        items PS-001, PS-002        (LIVE scope; PS-003 was CUT)
    candidates.json (LIVE)    SC-001 -> PS-001 (parented) + SC-003 -> PS-003 (ORPHAN, live -> surfaces)
    archive/candidates.json   SC-002 -> PS-003 (ORPHAN but SHIPPED -> must NOT surface, M1)

Covers AC1 (names the live orphan), AC2 (read-only whole-vault byte-identity), AC3 (all-parented ->
no line), M1 (shipped orphan not surfaced), the exit-4 no-scope no-line path, must_not_defer #1
(missing candidates.json degrades, never crashes), and must_not_defer #2 (a derivation/launch error
is fail-visible on stdout, never a silent no-line — B1 keys on exit code, M3 stays fail-visible).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ADAPTER_REL = "skills/pulse/scripts/orphaned_candidates.py"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


# ── fixture builders ──────────────────────────────────────────────────────────────

def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _scope_item(iid: str, label: str) -> dict:
    return {"id": iid, "label": label, "title": f"build-{label}",
            "description": f"The {label} capability.", "user_visible_outcome": "It works.",
            "depends_on": [], "verification_plan": "Drive it once."}


def _cand(cid: str, ps_ref: str) -> dict:
    return {"id": cid, "title": f"materialize-{ps_ref}",
            "source": [{"type": "product-scope", "ref": ps_ref}],
            "status": "not-started", "priority": {"score": 1.0}}


def _candidates_doc(*cands: dict) -> dict:
    return {"_schema": "aisdlc/slice-candidates@1", "project": "fixture",
            "counters": {"sc": len(cands)}, "candidates": list(cands), "pick_log": []}


def _scope_doc(*items: dict) -> dict:
    return {"_schema": "aisdlc/product-scope@1", "items": list(items)}


@pytest.fixture
def orphan_vault(tmp_path: Path) -> Path:
    """Scope-present: exactly one LIVE orphan (@PS-003) and one SHIPPED orphan (@PS-003)."""
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", _scope_doc(_scope_item("PS-001", "alpha"),
                                                _scope_item("PS-002", "beta")))
    _write(v / "candidates.json", _candidates_doc(_cand("SC-001", "PS-001"),
                                                  _cand("SC-003", "PS-003")))
    _write(v / "archive" / "candidates.json", _candidates_doc(_cand("SC-002", "PS-003")))
    return v


def _load_adapter():
    """Import the bundled adapter by path (it is not a package)."""
    path = PLUGIN_ROOT / ADAPTER_REL
    spec = importlib.util.spec_from_file_location("orphaned_candidates_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _envelope(run_script, vault: Path) -> dict:
    r = run_script(ADAPTER_REL, ["--vault", str(vault)])
    assert r.returncode == 0, f"adapter must be exit-0-always; got {r.returncode}\n{r.stderr}"
    return json.loads(r.stdout)


# ── AC1 — after a scope cut, the adapter NAMES the live orphaned candidate ──────────

def test_ac1_adapter_names_live_orphan(run_script, orphan_vault):
    env = _envelope(run_script, orphan_vault)
    assert env["scope_present"] is True
    assert "error" not in env, env
    ids = {o["candidate"] for o in env["orphaned"]}
    assert "SC-003" in ids, f"the cut capability's live candidate must be named: {env}"
    orphan = next(o for o in env["orphaned"] if o["candidate"] == "SC-003")
    assert orphan["ref"] == "PS-003", f"each orphan pairs id with its cut ref (AC4 distinguisher): {orphan}"


# ── M1 — a SHIPPED (archived) orphan is NOT surfaced as pick-gate work ──────────────

def test_m1_shipped_orphan_not_surfaced(run_script, orphan_vault):
    env = _envelope(run_script, orphan_vault)
    ids = {o["candidate"] for o in env["orphaned"]}
    assert "SC-002" not in ids, f"archived orphan must be filtered out to LIVE (M1): {env}"
    assert ids == {"SC-003"}, f"exactly the live orphan surfaces: {env}"


# ── AC2 — surfacing orphans is strictly READ-ONLY (whole-vault byte-identity) ───────

def _vault_digest(v: Path) -> dict:
    return {str(p.relative_to(v)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(v.rglob("*")) if p.is_file()}


def test_ac2_readonly_byte_identity(run_script, orphan_vault):
    before = _vault_digest(orphan_vault)
    env = _envelope(run_script, orphan_vault)
    assert env["orphaned"], "AC2 must run where the adapter does REAL work (an orphan present)"
    after = _vault_digest(orphan_vault)
    assert before == after, "the orphan surface mutated the vault -- READ BARRIER violated"


# ── AC3 — all-parented scope -> NO orphan line (no false positive) ──────────────────

def test_ac3_all_parented_no_orphan_line(run_script, tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", _scope_doc(_scope_item("PS-001", "alpha"),
                                                _scope_item("PS-002", "beta")))
    _write(v / "candidates.json", _candidates_doc(_cand("SC-001", "PS-001"),
                                                  _cand("SC-002", "PS-002")))
    _write(v / "archive" / "candidates.json", _candidates_doc())
    env = _envelope(run_script, v)
    assert env["scope_present"] is True
    assert env["orphaned"] == [], f"all candidates parented -> no orphan line: {env}"


# ── exit-4 no-scope: absent product-scope.json -> scope_present:false, clean no-line ─

def test_no_scope_reports_clean_no_line(run_script, tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "candidates.json", _candidates_doc())
    env = _envelope(run_script, v)
    assert env["scope_present"] is False
    assert env["orphaned"] == []
    assert "error" not in env, "an absent scope is a clean no-line, NOT an error"


# ── must_not_defer #1 — missing candidates.json must NOT crash; degrade to no line ──

def test_missing_candidates_degrades_no_crash(run_script, tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    _write(v / "product-scope.json", _scope_doc(_scope_item("PS-001", "alpha")))
    env = _envelope(run_script, v)  # no candidates.json at all
    assert env["scope_present"] is True
    assert env["orphaned"] == [], "missing candidates.json -> live set empty -> no orphan line (mnd#1)"
    assert "error" not in env, "a missing live backlog is a normal degrade, not a derivation error"


# ── must_not_defer #2 — a derivation/launch error is FAIL-VISIBLE on stdout ──────────
#    (unit-level: force each product_scope-subprocess failure mode deterministically)

class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_non_zero_exit_is_fail_visible(monkeypatch, tmp_path):
    mod = _load_adapter()
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(2, stdout="", stderr="boom"))
    env = mod.derive(tmp_path)
    assert env["scope_present"] is True and env["orphaned"] == []
    assert env.get("error"), "a non-zero (non-4) exit must surface an error, never a silent no-line"


def test_unparseable_stdout_is_fail_visible(monkeypatch, tmp_path):
    mod = _load_adapter()
    _write(tmp_path / "candidates.json", _candidates_doc())
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(0, stdout="not json at all"))
    env = mod.derive(tmp_path)
    assert env.get("error"), "unparseable CLI stdout must surface an error (fail-visible)"


def test_subprocess_launch_exception_is_fail_visible(monkeypatch, tmp_path):
    mod = _load_adapter()

    def _boom(*a, **k):
        raise OSError("could not spawn interpreter")

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    env = mod.derive(tmp_path)
    assert env["scope_present"] is True and env["orphaned"] == []
    assert env.get("error"), "a subprocess LAUNCH failure must surface an error, never crash silently"


def test_subprocess_timeout_is_fail_visible(monkeypatch, tmp_path):
    """code-review m-1: a wedged product_scope must not stall /pulse -- a TimeoutExpired (a
    SubprocessError subclass) routes to the same fail-visible error envelope, exit-0-always."""
    import subprocess as _sp
    mod = _load_adapter()

    def _timeout(*a, **k):
        assert k.get("timeout"), "the adapter must pass a timeout= ceiling to bound the read"
        raise _sp.TimeoutExpired(cmd="product_scope", timeout=k["timeout"])

    monkeypatch.setattr(mod.subprocess, "run", _timeout)
    env = mod.derive(tmp_path)
    assert env["scope_present"] is True and env["orphaned"] == []
    assert env.get("error"), "a product_scope timeout must surface an error, never a silent stall"


def test_missing_orphaned_key_is_fail_visible(monkeypatch, tmp_path):
    mod = _load_adapter()
    _write(tmp_path / "candidates.json", _candidates_doc())
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: _Proc(0, stdout=json.dumps({"status": "nothing-to-mint"})))
    env = mod.derive(tmp_path)
    assert env.get("error"), "exit-0 output lacking the orphaned array must surface an error"
