"""slice-002 — /drift-check --status fold semantics (TF-1: written BEFORE drift_status.py).

The fold contract (ratified at TRI-1, slice-002 critique B1/M1/M2/M3/M-add-1):
group by finding.strip() (case-sensitive); sort by parsed aware-UTC datetime; classify
by the latest SIGNAL with ASYMMETRIC supersession — acceptance covers recurrence
(re-detection annotates, never revokes), resolution is falsified by a newer detection
(re-opens); both-fields entry = RESOLVED + ambiguous note; no-signal = OPEN; entries
without finding identity or a parseable `at` -> unfoldable[] verbatim.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPT = "skills/drift-check/scripts/drift_status.py"
ROOT = Path(__file__).resolve().parents[1]


def _vault_with(tmp_path: Path, entries: list) -> Path:
    v = tmp_path / "vault"
    v.mkdir(parents=True, exist_ok=True)
    (v / "drift-log.json").write_text(
        json.dumps({"_schema": "aisdlc/drift-log@1", "entries": entries}, ensure_ascii=False),
        encoding="utf-8")
    return v


def _status(run_script, vault: Path):
    r = run_script(SCRIPT, ["--vault", vault, "--json"])
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _find(out: dict, state: str, finding: str) -> dict:
    rows = [g for g in out[state] if g["finding"] == finding]
    assert rows, f"{finding!r} not in {state}: {out}"
    return rows[0]


def test_fold_sequences_signal_precedence(run_script, tmp_path):
    e = [
        # detect -> accept  => ACCEPTED-DRIFT with rationale
        {"at": "2026-01-01T00:00:00Z", "trigger": "slice-010", "category": "drift",
         "finding": "F-accept", "action": None},
        {"at": "2026-01-02T00:00:00Z", "trigger": "slice-010", "category": "drift",
         "finding": "F-accept", "action": "accept-drift", "rationale": "known debt; fix in SC-031"},
        # detect -> resolve => RESOLVED
        {"at": "2026-01-01T00:00:00Z", "trigger": "slice-010", "category": "stale-claim",
         "finding": "F-resolve"},
        {"at": "2026-01-03T00:00:00Z", "trigger": "slice-011", "category": "stale-claim",
         "finding": "F-resolve", "resolution": "superseded by ADR-020"},
        # detect-only => OPEN
        {"at": "2026-01-04T00:00:00Z", "trigger": "slice-011", "category": "drift",
         "finding": "F-open"},
        # accept -> re-detect => STAYS ACCEPTED-DRIFT, rationale preserved, annotated (M-add-1)
        {"at": "2026-02-01T00:00:00Z", "trigger": "slice-012", "category": "drift",
         "finding": "F-recur", "action": "accept-drift", "rationale": "perf budget accepted"},
        {"at": "2026-02-10T00:00:00Z", "trigger": "slice-013", "category": "drift",
         "finding": "F-recur"},
        # resolve -> re-detect => RE-OPENED (resolution falsified)
        {"at": "2026-03-01T00:00:00Z", "trigger": "slice-014", "category": "drift",
         "finding": "F-reopen", "resolution": "fixed in slice-014"},
        {"at": "2026-03-05T00:00:00Z", "trigger": "slice-015", "category": "drift",
         "finding": "F-reopen"},
        # one entry with BOTH accept-drift AND resolution => RESOLVED + ambiguous (M2)
        {"at": "2026-04-01T00:00:00Z", "trigger": "slice-016", "category": "drift",
         "finding": "F-both", "action": "accept-drift", "rationale": "r", "resolution": "also resolved"},
    ]
    out = _status(run_script, _vault_with(tmp_path, e))

    acc = _find(out, "accepted_drift", "F-accept")
    assert acc["rationale"] == "known debt; fix in SC-031"

    assert any(g["finding"] == "F-resolve" for g in out["resolved"])
    assert any(g["finding"] == "F-open" for g in out["open"])

    rec = _find(out, "accepted_drift", "F-recur")        # acceptance NOT revoked
    assert rec["rationale"] == "perf budget accepted"     # rationale survives re-detection
    assert rec["redetections"] == 1
    assert "2026-02-10" in rec["last_redetected_at"]

    rop = _find(out, "open", "F-reopen")                  # resolution falsified
    assert rop.get("previously_resolved_at", "").startswith("2026-03-01")

    both = _find(out, "resolved", "F-both")
    assert both.get("ambiguous") is True


def test_skill_md_documents_status_mode():
    text = (ROOT / "skills" / "drift-check" / "SKILL.md").read_text(encoding="utf-8")
    hint_line = next(ln for ln in text.splitlines() if ln.startswith("argument-hint:"))
    assert "--status" in hint_line
    assert "--status" in text.split("argument-hint:", 1)[1]
    assert "drift_status.py" in text


def test_unfoldable_and_identity_edges(run_script, tmp_path):
    e = [
        {"at": "2026-01-01T00:00:00Z", "trigger": "s", "category": "drift"},                       # no finding
        {"at": "<ts>", "trigger": "s", "category": "drift", "finding": "F-badts"},                 # unparseable at
        # trailing-whitespace variant JOINS (identity = finding.strip())
        {"at": "2026-01-01T00:00:00Z", "trigger": "s", "category": "drift", "finding": "F-id"},
        {"at": "2026-01-02T00:00:00+00:00", "trigger": "s", "category": "drift",
         "finding": "F-id  ", "action": "accept-drift", "rationale": "ok"},
        # trailing-period variant does NOT join
        {"at": "2026-01-03T00:00:00", "trigger": "s", "category": "drift", "finding": "F-id."},    # offsetless -> UTC
    ]
    out = _status(run_script, _vault_with(tmp_path, e))
    assert len(out["unfoldable"]) == 2
    acc = _find(out, "accepted_drift", "F-id")   # whitespace variant joined; +00:00 newest -> accepted
    assert acc["rationale"] == "ok"
    assert any(g["finding"] == "F-id." for g in out["open"])  # period variant separate, offsetless parsed


def test_same_second_tiebreak_and_malformed_root(run_script, tmp_path):
    # code-review M1: resolution and a re-detection in the SAME second — append order
    # (array index) breaks the tie, so the later entry re-opens the finding.
    e = [
        {"at": "2026-05-01T10:00:00Z", "trigger": "s", "category": "drift",
         "finding": "F-tie", "resolution": "fixed"},
        {"at": "2026-05-01T10:00:00Z", "trigger": "s", "category": "drift",
         "finding": "F-tie"},  # same second, later index -> re-opens
    ]
    out = _status(run_script, _vault_with(tmp_path, e))
    tie = _find(out, "open", "F-tie")
    assert tie.get("previously_resolved_at", "").startswith("2026-05-01")

    # code-review m1: a top-level ARRAY drift-log is malformed -> documented exit 2, no crash
    v = tmp_path / "vault-arr"
    v.mkdir()
    (v / "drift-log.json").write_text('[{"at": "x"}]', encoding="utf-8")
    r = run_script("skills/drift-check/scripts/drift_status.py", ["--vault", v])
    assert r.returncode == 2
    assert "Traceback" not in r.stderr and "expected an object" in r.stderr

    # code-review m2: multi-line finding renders as ONE line in human mode
    e = [{"at": "2026-05-02T10:00:00Z", "trigger": "s", "category": "drift",
          "finding": "line one\nline two   spaced"}]
    r = run_script("skills/drift-check/scripts/drift_status.py",
                   ["--vault", _vault_with(tmp_path / "ml", e)])
    assert r.returncode == 0
    assert "line one line two spaced" in r.stdout


def test_script_stdlib_and_utf8_hygiene(run_script, tmp_path):
    src = (ROOT / SCRIPT).read_text(encoding="utf-8")
    assert "reconfigure_stdout_utf8" in src
    assert 'encoding="utf-8"' in src or "encoding='utf-8'" in src
    for forbidden in ("import requests", "import yaml", "import pandas"):
        assert forbidden not in src
    # absent log -> exit 0, friendly note
    v = tmp_path / "vault-empty"
    v.mkdir()
    r = run_script(SCRIPT, ["--vault", v])
    assert r.returncode == 0
    assert "no drift recorded" in r.stdout.lower()
