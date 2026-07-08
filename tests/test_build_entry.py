"""scripts/lib/build_entry.py — the ONE foldable drift-log entry shape (2026-07 review sweep).

Promoted to scripts/lib when /sync became a second writer: both /drift-check and /sync must
emit the `{at, trigger, category, finding, …}` shape that drift_status.py's fold reads — a
divergent hand-rolled shape (e.g. /sync's old `{at, claim, reality, rationale}`) is
permanently UNFOLDABLE. These tests pin the shared contract at the new home.
"""
from __future__ import annotations

import json

SCRIPT = "scripts/lib/build_entry.py"


def test_emits_foldable_shape_with_canonical_slice_trigger(run_script, tmp_path):
    r = run_script(SCRIPT, ["--category", "drift", "--finding", "ADR-008 chose sendgrid; code uses resend",
                            "--trigger", "slice-042-fix-transport"])
    assert r.returncode == 0, r.stderr
    entry = json.loads(r.stdout)
    assert entry["category"] == "drift"
    assert entry["trigger"] == "slice-042"  # DCE-1 canonicalization
    assert entry["finding"].startswith("ADR-008")
    assert "at" in entry
    assert "resolution" not in entry and "action" not in entry  # empty optionals omitted, never null


def test_non_slice_trigger_passes_verbatim(run_script):
    r = run_script(SCRIPT, ["--category", "stale-claim", "--finding", "f", "--trigger", "sync"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["trigger"] == "sync"


def test_accept_drift_without_rationale_fail_closes(run_script):
    r = run_script(SCRIPT, ["--category", "drift", "--finding", "f", "--action", "accept-drift"])
    assert r.returncode == 2
    assert "rationale" in r.stderr


def test_bad_category_rejected(run_script):
    r = run_script(SCRIPT, ["--category", "vibes", "--finding", "f"])
    assert r.returncode == 2


def test_out_mode_writes_file_and_prints_path(run_script, tmp_path):
    out = tmp_path / "entry.json"
    r = run_script(SCRIPT, ["--category", "stale-doc", "--finding", "f", "--out", out])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(out)
    assert json.loads(out.read_text(encoding="utf-8"))["category"] == "stale-doc"
