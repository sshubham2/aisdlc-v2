"""adr_append_only_audit (slice-035 / SC-019 / ADR-023) — enforce ADR append-only via
a content-hash baseline sidecar, proven over fixture decision sets.

The vault is NOT git-tracked, so immutability is baselined by a SHA-256 over each ADR's
NFC-normalized IMMUTABLE field subset, kept in a sidecar decisions/.adr-baseline.json.
VERIFY (default, read-only): exit 0 clean / 1 tamper (sealed ADR's immutable field changed)
/ 2 usage-or-degrade / 3 unsealed (present-but-unbaselined) / 4 deleted (sealed baseline id
missing from disk -- ADR-049 / SC-068). Precedence 2 > 4 > 1 > 3 > 0. --seal <id> is scoped;
--backfill is the sole blanket seal. status + superseded_by are EXCLUDED from the hash so a
legitimate supersession is hash-invariant.

TF-1: written FAILING before the impl (the audit module does not exist yet).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT = ROOT / "scripts" / "lib" / "adr_append_only_audit.py"
ADR_EXAMPLE = ROOT / "skills" / "design-slice" / "examples" / "adr.json"
PRE_FINISH_GATE = ROOT / "skills" / "build-slice" / "scripts" / "pre_finish_gate.py"
PY = sys.executable


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mod():
    return _load(AUDIT, "adr_append_only_audit")


def _write_adr(decisions: Path, adr_id: str, **over) -> Path:
    adr = {
        "_schema": "aisdlc/adr@1",
        "_note": "Append-only.",
        "id": adr_id,
        "title": f"Title for {adr_id}",
        "status": "accepted",
        "reversibility": "cheap",
        "supersedes": None,
        "superseded_by": None,
        "slice": "slice-001",
        "date": "2026-01-01T00:00:00Z",
        "context": "## Context\nsome context",
        "decision": "## Decision\nsome decision",
        "consequences": "## Consequences\nsome consequences",
    }
    adr.update(over)
    p = decisions / f"{adr_id}.json"
    p.write_text(json.dumps(adr, indent=2), encoding="utf-8")
    return p


def _decisions(tmp_path: Path) -> Path:
    d = tmp_path / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(AUDIT), *args], capture_output=True, text=True)


# ── clean: backfilled set verifies clean ─────────────────────────────────────
def test_clean_passes_exit0(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    assert _run("--decisions", str(d), "--backfill").returncode == 0
    assert _run("--decisions", str(d)).returncode == 0


# ── AC1: an in-place edit of a SEALED ADR's immutable field is tamper (exit 1) ─
def test_tamper_detected_exit1(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    _write_adr(d, "ADR-002", decision="## Decision\nQUIETLY REWRITTEN")  # in-place edit
    r = _run("--decisions", str(d))
    assert r.returncode == 1, r.stdout + r.stderr
    out = (r.stdout + r.stderr)
    assert "ADR-002" in out and ("decision" in out or "field" in out)


# ── AC2: a legitimate supersession (status/superseded_by flip) is hash-invariant ─
def test_supersede_flip_passes_clean(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    # mark ADR-001 superseded by ADR-002 -- the ONLY legitimate post-seal change
    _write_adr(d, "ADR-001", status="superseded", superseded_by="ADR-002")
    r = _run("--decisions", str(d))
    assert r.returncode == 0, r.stdout + r.stderr
    # and the looser real-world form: superseded_by set while status stays 'accepted' (ADR-005/017)
    _write_adr(d, "ADR-002", superseded_by="ADR-001")  # status left 'accepted'
    assert _run("--decisions", str(d)).returncode == 0


# ── AC4: a present-but-unbaselined ADR is the DISTINCT exit 3 (not tamper) ────
def test_unsealed_present_but_unbaselined_exit3(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")
    _write_adr(d, "ADR-002")  # NEW, never sealed
    r = _run("--decisions", str(d))
    assert r.returncode == 3, r.stdout + r.stderr
    out = (r.stdout + r.stderr)
    assert "ADR-002" in out and "backfill" in out.lower()


# ── AC4: NO-OP PASS when there is no decisions dir ───────────────────────────
def test_noop_absent_decisions_exit0(tmp_path):
    empty_vault = tmp_path / "empty_vault"
    empty_vault.mkdir()
    assert _run("--vault", str(empty_vault)).returncode == 0


# ── AC4 degrade: a malformed ADR JSON is a visible exit 2, never a silent pass ─
def test_degrade_malformed_exit2(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")
    (d / "ADR-099.json").write_text("{ this is not valid json", encoding="utf-8")
    assert _run("--decisions", str(d)).returncode == 2


# ── AC4 degrade: a corrupt baseline is a visible exit 2 ───────────────────────
def test_degrade_corrupt_baseline_exit2(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")
    (d / ".adr-baseline.json").write_text("{ corrupt", encoding="utf-8")
    assert _run("--decisions", str(d)).returncode == 2


# ── AC4 (M4): an NFC/NFD normalization-form change must NOT false-FAIL ────────
def test_nfc_nfd_normalization_stable(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001", title="Café presence")     # composed (NFC: e-acute U+00E9)
    _run("--decisions", str(d), "--backfill")
    _write_adr(d, "ADR-001", title="Café presence")    # decomposed (NFD: e + U+0301)
    r = _run("--decisions", str(d))
    assert r.returncode == 0, "NFC/NFD of the same text must verify clean: " + r.stdout + r.stderr


# ── AC3 (M-add-1): a SCOPED --seal must not launder an unrelated tampered ADR ─
def test_seal_scoped_does_not_launder_unrelated_unsealed(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    _write_adr(d, "ADR-002", decision="## Decision\nTAMPERED")     # ADR-002 now diverges (tamper)
    _write_adr(d, "ADR-003")                                       # a NEW unrelated ADR
    _run("--decisions", str(d), "--seal", "ADR-003")               # scoped seal of ADR-003 ONLY
    r = _run("--decisions", str(d))
    assert r.returncode == 1, "scoped --seal of ADR-003 must NOT bless ADR-002's tamper: " + r.stdout + r.stderr
    assert "ADR-002" in (r.stdout + r.stderr)


# ── m2: the immutable-field set is a single source of truth bound to the schema ─
def test_immut_set_matches_schema(tmp_path):
    mod = _mod()
    example = json.loads(ADR_EXAMPLE.read_text(encoding="utf-8"))
    mutable_or_meta = {"_schema", "_note", "status", "superseded_by"}
    expected = {k for k in example.keys() if k not in mutable_or_meta}
    assert set(mod.IMMUTABLE_FIELDS) == expected, (
        "IMMUTABLE_FIELDS drifted from the ADR schema-by-example (a new immutable field "
        "would be silently unprotected): " + str(set(mod.IMMUTABLE_FIELDS) ^ expected)
    )


# ── m3: an ADR file whose internal id != filename is a visible exit 2 ─────────
def test_id_filename_key_mismatch_exit2(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")
    _write_adr(d, "ADR-005", id="ADR-999")  # file ADR-005.json carries id ADR-999
    assert _run("--decisions", str(d)).returncode == 2


# ── AC5: the consuming-project CLAUDE.md templates NAME the enforcing gate ────
def test_consuming_claudemd_names_gate(tmp_path):
    triage = (ROOT / "skills" / "triage" / "SKILL.md").read_text(encoding="utf-8")
    adopt = (ROOT / "skills" / "adopt" / "SKILL.md").read_text(encoding="utf-8")
    token = "adr-append-only"  # the gate is named (not just the append-only rule)
    assert token in triage.lower(), "triage CLAUDE.md template must name the enforcing gate"
    assert token in adopt.lower(), "adopt CLAUDE.md template must name the enforcing gate"


# ── AC3 wiring: pre_finish_gate runs ADR-APPEND-1, deriving decisions from --slice ─
def test_pre_finish_gate_includes_adr_append(tmp_path):
    import inspect
    gate = _load(PRE_FINISH_GATE, "pre_finish_gate")
    src = inspect.getsource(gate.run_gate)
    assert "ADR-APPEND-1" in src, "pre_finish_gate must wire the ADR-APPEND-1 check"
    assert "adr_append_only_audit" in src, "ADR-APPEND-1 must invoke adr_append_only_audit"
    assert "parents[1]" in src, "decisions dir must be derived from --slice parents[1] (no new flag)"


# ── M1 (code-review): scoped --seal must REFUSE to overwrite a CHANGED already-sealed id ──
# (else edit-a-sealed-ADR + --seal that id launders the tamper -- must_not_defer #4).
def test_seal_refuses_reseal_of_changed_id(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")                 # seal ADR-001
    _write_adr(d, "ADR-001", decision="## Decision\nTAMPERED")  # in-place edit of a SEALED ADR
    r = _run("--decisions", str(d), "--seal", "ADR-001")       # attempt edit+reseal
    assert r.returncode != 0, "scoped --seal of a CHANGED already-sealed id must refuse: " + r.stdout + r.stderr
    # the baseline must NOT have been overwritten -> VERIFY still catches the tamper
    assert _run("--decisions", str(d)).returncode == 1, "the tamper must remain detectable (not laundered)"


# ── M1: re-sealing an UNCHANGED already-sealed id is an idempotent no-op (exit 0) ─────────
def test_seal_idempotent_unchanged(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _run("--decisions", str(d), "--backfill")
    assert _run("--decisions", str(d), "--seal", "ADR-001").returncode == 0  # unchanged -> no-op
    assert _run("--decisions", str(d)).returncode == 0


# ── m2 (code-review): pre_finish_gate ACTUALLY RUNS ADR-APPEND-1 (behavioral, not source-string) ──
def test_pre_finish_gate_adr_append_behavioral(tmp_path):
    import argparse
    gate = _load(PRE_FINISH_GATE, "pre_finish_gate")
    vault = tmp_path / "vault"
    sl = vault / "slices" / "slice-001-x"; sl.mkdir(parents=True)
    dec = vault / "decisions"; dec.mkdir()
    _write_adr(dec, "ADR-001")  # present but UNBASELINED -> VERIFY exit 3 -> FAIL (proves it ran)
    ns = argparse.Namespace(slice=str(sl), worktree=str(tmp_path), changed_files=[],
                            changed_test_files=[], ack_critical="", seam_allowlist=None,
                            test_first=False, strict=False)
    _g, results = gate.run_gate(ns)
    by = {r.name: r for r in results}
    assert "ADR-APPEND-1" in by, "ADR-APPEND-1 must be a real aggregated gate check"
    assert by["ADR-APPEND-1"].status == "FAIL", (
        "the gate must actually RUN the audit (decisions present + unbaselined -> exit 3 -> FAIL), "
        "not no-op/skip: " + by["ADR-APPEND-1"].summary)


# ═══ slice-055 / SC-068 / ADR-049: a SEALED ADR deleted from disk is a DISTINCT exit 4 ═══

# ── AC2: a sealed baseline id with no on-disk file is exit 4, named in result['deleted'] ──
def test_deleted_sealed_adr_exit4(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")          # seal both
    (d / "ADR-002.json").unlink()                      # delete a SEALED ADR
    r = _run("--decisions", str(d), "--json")
    assert r.returncode == 4, "a deleted sealed ADR must be exit 4: " + r.stdout + r.stderr
    parsed = json.loads(r.stdout)
    assert parsed["deleted"] == ["ADR-002"], parsed
    assert parsed.get("clean") is not True


# ── AC4: BOTH the human ([adr-deleted] stderr) AND the --json output surface the deletion ──
def test_deleted_surfaces_human_and_json(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    (d / "ADR-001.json").unlink()
    # human mode: the [adr-deleted] signal goes to STDERR (fail-visible), naming the id
    human = _run("--decisions", str(d))
    assert human.returncode == 4
    assert "[adr-deleted]" in human.stderr and "ADR-001" in human.stderr, human.stdout + human.stderr
    assert "clean --" not in human.stdout
    # json mode: the machine-readable output carries the deleted list
    j = json.loads(_run("--decisions", str(d), "--json").stdout)
    assert j["deleted"] == ["ADR-001"]


# ── M2 / must_not_defer #1: degrade (exit 2) DOMINATES a co-occurring deletion ──
def test_degrade_dominates_deletion_exit2(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    (d / "ADR-002.json").unlink()                      # a deletion (would be exit 4 alone)...
    (d / "ADR-099.json").write_text("{ not json", encoding="utf-8")  # ...AND an unreadable ADR
    r = _run("--decisions", str(d))
    assert r.returncode == 2, ("an unreadable/corrupt decisions set must dominate the deletion "
                               "signal (can't judge completeness of a set you can't read): "
                               + r.stdout + r.stderr)


# ── M2: deletion(4) dominates the scalar over tamper(1), but the tamper is NOT lost from --json ──
def test_deletion_dominates_tamper_but_tamper_still_listed(tmp_path):
    d = _decisions(tmp_path)
    _write_adr(d, "ADR-001")
    _write_adr(d, "ADR-002")
    _run("--decisions", str(d), "--backfill")
    (d / "ADR-001.json").unlink()                                  # deletion
    _write_adr(d, "ADR-002", decision="## Decision\nTAMPERED")     # in-place edit of a SEALED ADR
    r = _run("--decisions", str(d), "--json")
    assert r.returncode == 4, "deletion must win the scalar exit over tamper: " + r.stdout + r.stderr
    parsed = json.loads(r.stdout)
    assert parsed["deleted"] == ["ADR-001"], parsed
    assert [t["id"] for t in parsed["tampered"]] == ["ADR-002"], (
        "the co-occurring tamper must still be reported in result['tampered'] even though the "
        "scalar exit is 4 (no signal lost): " + str(parsed))


# ── m1 + AC5: the exit-code TABLE names code 4 + new precedence, and the stale 'not flagged'
#    Out-of-scope claim is GONE (deletion is now detected, not a conscious exclusion) ──
def test_exit_code_table_docstring_names_deletion():
    src = AUDIT.read_text(encoding="utf-8")
    assert "2 > 4 > 1 > 3 > 0" in src, "the exit-code precedence string must be updated to 2 > 4 > 1 > 3 > 0"
    assert "4  deleted:" in src, "the exit-code table must enumerate code 4 (deleted)"
    # AC5: the old conscious-exclusion claim must be removed
    assert "REMOVED from disk is NOT flagged" not in src, (
        "the 'Out of scope' note must no longer claim a removed sealed ADR is NOT flagged -- "
        "deletion is now detected (exit 4)")
