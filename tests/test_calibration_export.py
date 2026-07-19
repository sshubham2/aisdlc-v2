"""Behavioral test-first suite for the upstream calibration export (slice-087, SC-144).

This is the REAL enforcement of the AC4 redaction contract (ADR-103: the static SCRUB-1
marker only proves the reference exists; the behavioral suite proves the payload is safe).
It MUST live at repo-root ``tests/`` -- CI runs bare ``python -m pytest`` with
``pytest.ini testpaths=tests``, so a suite under ``skills/**/tests`` is NEVER collected (B1).

The export is a DEFAULT-DENY DECLASSIFIER (ADR-103) split by array (ADR-104):
  * calibration_notes / gate_skips -> machine STRUCTURAL floor (closed field+value vocab);
    free text WITHHELD by construction.
  * active_checks -> NOT machine-read from the log at all; only the human-confirmed
    genericized text from the Step-5 ``--approved-checks`` staging file enters the payload.

Coverage map: AC1 (non-hollow real emit path), AC2 (credential mediation on the emit path),
AC3 (non-credential private content redacted-or-refused), AC4 (CI tripwire covers the export),
AC5 (SKILL.md Step 5 invokes the real export). Plus M1/M2/M3/M-add-1/m1/m3 from critique.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_SKILL_SCRIPTS = _REPO / "skills" / "critic-calibrate" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

import calibration_export as ce  # noqa: E402
from scripts.lib import evidence_redaction_audit as era  # noqa: E402

# --- fixtures / helpers ------------------------------------------------------

# A credential that secret_scrub.SECRET_PATTERNS['aws-access-key'] must catch: AKIA + 16 chars.
AKIA = "AKIA1234567890ABCDEF"

# A real-log-shaped active_check carrying its 6 fields incl. the leak-dense `example`
# (embeds _execute_verifications / WS-1 / slice-047 verbatim -- M1).
CC_001 = {
    "id": "CC-001",
    "check": "Before accepting a fix, grep for divergent sibling impls (run_catalog vs "
             "_execute_verifications) and colliding contracts. Route into Dim 9.",
    "category": "behavioral-twin / fix-collision",
    "evidence": ["slice-042", "slice-046", "slice-047", "slice-049", "slice-051"],
    "example": "slice-047 - the static WS-1 portability gate and the runtime "
               "_execute_verifications executor are divergent implementations.",
    "added_at": "2026-07-01T15:59:51Z",
}

# A calibration_note whose free-text `note` embeds a credential + a Windows path, plus an
# UNKNOWN field the default-deny walk must drop. Its structural fields are all valid.
LEAKY_NOTE = {
    "id": "CN-001",
    "target_gate": "critique",
    "target_dimension": "4",
    "signal": "low-precision",
    "window": 15,
    "precision": 0.42,
    "evidence": ["slice-070", "slice-071", "slice-072"],
    "note": f"weight dim 4 lighter; leaked {AKIA} and path C:\\Users\\dev\\secret.py here",
    "confirmed_at": "2026-07-01T00:00:00Z",
    "private_blob": "DROP-ME unknown field mentioning slice-099 and /home/dev/x.py",
}

VALID_GATE_SKIP = {
    "id": "GS-001",
    "target_gate": "critique-review",
    "action": "skip",
    "precision": 0.15,
    "runs_observed": 9,
    "real_blockers_caught": 0,
    "evidence": ["slice-060", "slice-061", "slice-062", "slice-063"],
    "rationale": "PRIVATE rationale text mentioning /var/secret and slice-050",
    "user_accepted_at": "2026-07-01T00:00:00Z",
}


def _log(**arrays) -> dict:
    base = {
        "_schema": "aisdlc/critic-calibration-log@3",
        "active_checks": [],
        "calibration_notes": [],
        "gate_skips": [],
        "runs": [],
    }
    base.update(arrays)
    return base


def _write_log(vault: Path, log: dict) -> Path:
    p = vault / "critic-calibration-log.json"
    p.write_text(json.dumps(log), encoding="utf-8")
    return p


def _staging(tmp_path: Path, items: list[dict]) -> Path:
    p = tmp_path / "approved-checks.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return p


def _run(argv, capsys):
    rc = ce.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# --- error paths (fail-closed) ----------------------------------------------

def test_absent_log_refuses(tmp_path, capsys):
    rc, out, err = _run(["--log", str(tmp_path / "nope.json")], capsys)
    assert rc != 0
    assert out == ""


def test_malformed_log_refuses(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    rc, out, err = _run(["--log", str(p)], capsys)
    assert rc != 0
    assert out == ""


def test_empty_log_hollow_refuses(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc != 0, "empty log + no approved checks must REFUSE (hollow), never emit"
    assert out == ""


# --- AC3 machine floor: free text WITHHELD (M3: NOT counted as free-text AC2/AC3 evidence) --

def test_machine_floor_withholds_free_text_creds_and_unknown_fields(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log(calibration_notes=[LEAKY_NOTE]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc == 0
    # structural fields SURVIVE
    assert "critique" in out
    assert "low-precision" in out
    assert "0.42" in out
    # free text / credential / path / unknown field are WITHHELD by construction
    assert "weight dim 4 lighter" not in out
    assert AKIA not in out
    assert "secret.py" not in out
    assert "DROP-ME" not in out
    assert "private_blob" not in out
    # recurrence emitted as a COUNT, never the slice-ids
    assert "slice-070" not in out and "slice-071" not in out
    assert "3" in out
    # manifest (stderr) reports a dropped unknown field, values never
    assert "private_blob" not in err or "DROP-ME" not in err  # value never leaks to stderr


def test_out_of_vocab_gate_drops_note_entirely(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    bad = dict(LEAKY_NOTE, target_gate="totally-made-up-gate")
    _write_log(vault, _log(calibration_notes=[bad]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    # only signal was this one note; its anchor gate is out-of-vocab -> dropped -> hollow -> refuse
    assert rc != 0
    assert out == ""


def test_out_of_vocab_signal_dropped_gate_kept(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    bad = dict(LEAKY_NOTE, signal="weird-freeform-value")
    _write_log(vault, _log(calibration_notes=[bad]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc == 0
    assert "critique" in out              # valid anchor survives
    assert "weird-freeform-value" not in out  # out-of-vocab value dropped


def test_gate_skips_structural_emit(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log(gate_skips=[VALID_GATE_SKIP]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc == 0
    assert "critique-review" in out
    assert "skip" in out
    assert "0.15" in out
    assert "PRIVATE rationale" not in out
    assert "/var/secret" not in out
    assert "slice-060" not in out
    assert "4" in out  # evidence_count


def test_runs_array_out_of_scope(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    runs = [{"at": "x", "slice_range": "slice-037..slice-051",
             "proposals": [{"text": "PRIVATE proposal prose with /home/dev/x.py"}]}]
    _write_log(vault, _log(calibration_notes=[LEAKY_NOTE], runs=runs))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc == 0
    assert "PRIVATE proposal prose" not in out
    assert "slice-037..slice-051" not in out


# --- AC1 / AC2 / M-add-1: the active_checks emit path (the REAL path on this vault) --------

def test_positive_emit_confirmed_check_exact(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())  # notes=0, skips=0 -> ENTIRE payload rides active_checks
    text = "Verify a fix covers every behavioral twin and collides with no existing contract."
    staging = _staging(tmp_path, [{"text": text, "recurrence_count": 5}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc == 0, "notes/skips empty + N confirmed checks must be NON-HOLLOW (AC1)"
    assert text in out, "the confirmed genericized check text must appear verbatim (AC1)"


def test_confirmed_check_credential_redacted(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    text = f"Verify the fix and note the token {AKIA} was seen."
    staging = _staging(tmp_path, [{"text": text, "recurrence_count": 3}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc == 0
    assert AKIA not in out, "AC2: a credential on the real emit path must NOT survive redact()"
    assert "[REDACTED:aws-access-key]" in out


def test_recurrence_count_present_without_slice_ids(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    text = "Verify the fix covers every twin."
    staging = _staging(tmp_path, [{"text": text, "recurrence_count": 5}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc == 0
    assert "5" in out, "M-add-1: the integer distinct-slice recurrence count must be emitted"
    assert "slice-" not in out, "M-add-1: the slice-ids themselves must NEVER be emitted"


# --- AC3 / M2 structural backstop: un-genericized forwards are REFUSED, not emitted --------

@pytest.mark.parametrize("leaky", [
    "Check scripts/lib/secret_scrub.py before accepting.",   # path-like
    "Verify like in slice-047 the twin is covered.",         # slice-NNN
    "This is basically CC-001 restated.",                    # CC-NNN
    "See SHIP-094 for the regression.",                      # SHIP-NNN
    "As in GS-001 skip.",                                    # GS-NNN
    "As noted in CN-001.",                                   # CN-NNN
    "Handle C:\\Users\\dev\\thing.py explicitly.",           # windows path
])
def test_confirmed_check_leaky_token_refused(tmp_path, capsys, leaky):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    staging = _staging(tmp_path, [{"text": leaky, "recurrence_count": 2}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc != 0, f"M2 backstop must REFUSE an un-genericized forward: {leaky!r}"
    assert out == ""


@pytest.mark.parametrize("leaky", [
    "Check src/handlers/auth before accepting the fix.",     # relative source path, no extension (CR3)
    "The twin lives in scripts/lib next to the runner.",     # known-root relative path (CR3)
    "Compare against tests/bugs coverage first.",            # tests/ root relative path (CR3)
])
def test_confirmed_check_relative_source_path_refused(tmp_path, capsys, leaky):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    staging = _staging(tmp_path, [{"text": leaky, "recurrence_count": 2}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc != 0, f"CR3: a relative source path must REFUSE: {leaky!r}"
    assert out == ""


@pytest.mark.parametrize("generic", [
    "Verify the validator/executor/resolver arms and the code-half + data-half pair.",
    "Confirm the static gate and its runtime twin both apply the fix.",
    "Reconcile the read/write and check/persist parser differential at the merge seam.",
])
def test_backstop_does_not_false_positive_on_generic_prose(tmp_path, capsys, generic):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    staging = _staging(tmp_path, [{"text": generic, "recurrence_count": 2}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc == 0, f"generic slash-joined prose must NOT trip the backstop: {generic!r}"
    assert generic in out


# --- M1: active_checks are NEVER machine-read from the log ---------------------------------

def test_active_checks_never_read_from_log(tmp_path, capsys):
    """A real CC-001 (with its leak-dense `example`) present in the log + a valid note, no
    staging: the payload emits from the note but carries NONE of the example's tokens."""
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log(active_checks=[CC_001], calibration_notes=[LEAKY_NOTE]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc == 0
    assert "_execute_verifications" not in out
    assert "WS-1" not in out
    assert "slice-047" not in out
    assert "behavioral-twin" not in out  # category never forwarded


def test_real_log_shape_active_checks_only_no_staging_refuses(tmp_path, capsys):
    """notes=0 AND skips=0 AND active_checks present but NO --approved-checks -> hollow REFUSE
    (the consent gate defaults to EXCLUDE; nothing rides through un-reviewed)."""
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log(active_checks=[CC_001]))
    rc, out, err = _run(["--vault", str(vault)], capsys)
    assert rc != 0
    assert out == ""
    assert "_execute_verifications" not in out


# --- m3: secret_scrub unavailable -> fail-closed manifest, never an unredacted emit --------

def test_scrub_unavailable_is_fail_closed(tmp_path, capsys, monkeypatch):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    text = "Verify the fix covers every twin."
    staging = _staging(tmp_path, [{"text": text, "recurrence_count": 2}])

    def _boom():
        raise ImportError("secret_scrub unavailable")
    monkeypatch.setattr(ce, "_load_secret_scrub", _boom)

    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc != 0, "m3: an unavailable secret_scrub must FAIL-CLOSED (refuse), never emit raw"
    assert out == "", "nothing on stdout when the credential backstop cannot run"
    assert text not in out


# --- staging single-shot lifecycle ---------------------------------------------------------

def test_staging_file_removed_after_successful_emit(tmp_path, capsys):
    vault = tmp_path / "v"
    vault.mkdir()
    _write_log(vault, _log())
    staging = _staging(tmp_path, [{"text": "Verify every twin is covered.", "recurrence_count": 2}])
    rc, out, err = _run(["--vault", str(vault), "--approved-checks", str(staging)], capsys)
    assert rc == 0
    assert not staging.exists(), "the --approved-checks staging file is single-shot: removed after emit"


# --- AC4: the SCRUB-1 CI tripwire covers the outbound export path ---------------------------

def test_ac4_export_is_in_evidence_writers():
    assert "skills/critic-calibrate/scripts/calibration_export.py" in era.EVIDENCE_WRITERS
    # and the export actually references the marker, so the live audit is clean on this install
    assert era.audit(_REPO) == []


def test_ac4_tripwire_fails_when_scrub_reference_removed(tmp_path):
    """Reconstruct a fake plugin root: drop the secret_scrub reference from the export copy ->
    the audit MUST flag it (the contract fails closed on a future edit that strips the scrub)."""
    root = tmp_path / "root"
    for rel in era.EVIDENCE_WRITERS:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        marker = "" if rel.endswith("calibration_export.py") else "secret_scrub"
        f.write_text(f"stub file {marker}", encoding="utf-8")
    problems = era.audit(root)
    assert any("calibration_export.py" in p for p in problems), \
        "AC4: removing the secret_scrub reference from the export must FAIL the SCRUB-1 audit"


# --- AC5: SKILL.md Step 5 invokes the real export (no bare echo of the log path) ------------

def test_ac5_skill_step5_invokes_export_not_bare_echo():
    skill = (_REPO / "skills" / "critic-calibrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "calibration_export.py" in skill, "AC5: Step 5 must invoke the real export command"
    assert 'echo "${VAULT}/critic-calibration-log.json"' not in skill, \
        "AC5: the bare echo of the raw log path must be gone (the mail attaches the REDACTED export)"
