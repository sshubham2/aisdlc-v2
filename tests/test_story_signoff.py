"""story_signoff.py — derive story.html's signoff panel from the trust ledger (slice-086 / [[ADR-102]]).

test_first: authored BEFORE story_signoff.py's behavior was final; pins the four mission-brief ACs plus the
accepted-pending Critic findings against reality:
  AC1  panel is DERIVED from the ledger, not the narrator (+ M6 forged-narrator overwrite)
  AC2  gate ids / verdict enums render as plain English; no raw token leaks (+ unknown-verdict pass-through)
  AC3  green / grey / warn classification matches the gate-log facts (+ M1 non-positive-reality-not-green,
       + M-add-1 shippability failed/green)
  AC4  absent OR malformed ledger is fail-visible, no green column (+ M-add-2 malformed gate-log)
  M5   not_checked / reality_surprises translated from STRUCTURE; free-form surprise text never leaks
  m2   the closed gate->English table is set-equal to the projected gate universe
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SS_PATH = ROOT / "skills" / "slice-story" / "scripts" / "story_signoff.py"
RS_PATH = ROOT / "skills" / "slice-story" / "scripts" / "render_story.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


story_signoff = _load("story_signoff", SS_PATH)
render_story = _load("render_story", RS_PATH)
project = story_signoff.project_ledger_for_signoff
SRC = story_signoff.INJECT_SOURCE

# the REAL per-gate verdict vocabulary + contact, enumerated from the live gate-log.json (APED-1/m2).
REAL_VOCAB = {
    "risk-spike": ["go", "conditional", "no-go"],
    "validate-slice": ["pass", "partial"],
    "drift-check": ["warn", "clean"],
    "code-review": ["findings", "clean", "pass", "needs-fixes"],
    "critique": ["clean", "needs-fixes", "blocked"],
    "critique-review": ["extend", "accept"],
    "build-checks": ["clean"],
}
CONTACT = {"risk-spike": "high", "validate-slice": "high", "drift-check": "medium",
           "code-review": "low", "critique": "low", "critique-review": "low", "build-checks": "low"}


# ── ledger builders (hand-built, for the pure projection surface) ──────────────────────────────

def _row(gate: str, verdict: str, contact: str) -> dict:
    return {"text": f"{gate}: {verdict}", "source": {"file": "gate-log.json", "locator": "x"},
            "gate": gate, "verdict": verdict, "reality_contact": contact}


def _ledger(*, reality=None, model=None, not_checked=None, surprises=None,
            shippability=None, gatelog_status="ok") -> dict:
    return {
        "_schema": "aisdlc/trust-ledger@1",
        "reality_confirmed": reality or [],
        "model_only": model or [],
        "not_checked": not_checked or [],
        "reality_surprises": surprises or [],
        "shippability": shippability or [],
        "availability": [{"source": "gate-log.json", "status": gatelog_status, "reason": ""}],
    }


def _all_what(block: dict) -> list[str]:
    return [it.get("what", "") for k in ("reality_approved", "model_approved", "not_yet")
            for it in (block.get(k) or [])]


# ── m2: closed gate->English table drift-guard ─────────────────────────────────────────────────

def test_gate_english_covers_projected_gate_universe():
    projected = set(story_signoff.GATE_CONTACT) - set(story_signoff.INFORMATIONAL_GATES)
    assert set(story_signoff.GATE_ENGLISH) == projected     # set-equal: no dead row, no missing name
    assert "design-tournament" not in story_signoff.GATE_ENGLISH   # informational never reaches the panel


def test_gate_english_drift_guard_fires_on_drift():
    # BC-PROJ-12: prove the shipped drift-guard can FAIL. A matching pair is silent; a missing/extra gate raises.
    story_signoff._check_gate_english_drift({"a": "x", "b": "y"}, {"a", "b"})   # in sync -> no raise
    with pytest.raises(RuntimeError):
        story_signoff._check_gate_english_drift({"a": "x"}, {"a", "b"})          # missing a name -> loud
    with pytest.raises(RuntimeError):
        story_signoff._check_gate_english_drift({"a": "x", "z": "?"}, {"a"})     # stray dead row -> loud


# ── AC3: green / grey / warn classification matches the gate-log facts ──────────────────────────

def test_ac3_classification_green_grey_warn():
    led = _ledger(
        reality=[_row("validate-slice", "pass", "high")],
        model=[_row("critique", "needs-fixes", "low")],
        not_checked=[{"text": "AC2: no validation criterion recorded",
                      "source": {"file": "mission-brief.json", "locator": "acceptance_criteria[1]"},
                      "reason": "criterion-absent", "ac": "AC2"}],
    )
    b = project(led)
    assert b["state"] == "ok"
    assert any("Reality test" in i["what"] and "succeeded" in i["what"] for i in b["reality_approved"])
    assert any("Design review by the model" in i["what"] for i in b["model_approved"])
    assert any("AC2" in i["what"] for i in b["not_yet"])
    # disjoint: the validate row is green, NOT also grey/not_yet
    assert not any("Reality test" in i["what"] for i in b["model_approved"] + b["not_yet"])


def test_m1_non_positive_reality_is_not_green():
    # every reality-CONTACT row here is a non-clean outcome -> NONE may render as 'Proven against reality'
    led = _ledger(reality=[
        _row("validate-slice", "partial", "high"),
        _row("drift-check", "warn", "medium"),
        _row("risk-spike", "no-go", "high"),
    ])
    b = project(led)
    assert b["reality_approved"] == []                       # M1: contact != proof
    flagged = [i["what"] for i in b["not_yet"]]
    assert len(flagged) == 3 and all(w.startswith(story_signoff._FLAG) for w in flagged)


def test_m1_positive_reality_verdicts_are_green():
    led = _ledger(reality=[
        _row("risk-spike", "go", "high"),
        _row("risk-spike", "conditional", "high"),
        _row("validate-slice", "pass", "high"),
        _row("drift-check", "clean", "medium"),
    ])
    b = project(led)
    assert len(b["reality_approved"]) == 4 and b["not_yet"] == []


def test_m_add_1_shippability_failed_flagged_green_proven():
    failed = project(_ledger(shippability=[{"text": "shippability regression FAILED",
                                            "source": {"file": "validation.json", "locator": "x"},
                                            "state": "failed"}]))
    assert failed["reality_approved"] == []
    assert any(i["what"].startswith(story_signoff._FLAG) and "regression" in i["what"]
               for i in failed["not_yet"])                    # M-add-1: never silently dropped

    green = project(_ledger(shippability=[{"text": "shippability regression: ran, 0 failed",
                                           "source": {"file": "validation.json", "locator": "x"},
                                           "state": "green"}]))
    assert any("regression test suite passed" in i["what"] for i in green["reality_approved"])

    # catalog-membership is a DEFINITION, not a pass/fail -> excluded from every column
    memb = project(_ledger(shippability=[{"text": "catalog row", "source": {"file": "shippability.json",
                                          "locator": "rows"}, "state": "catalog-membership"}]))
    assert memb["reality_approved"] == [] and memb["not_yet"] == [] and memb["model_approved"] == []


# ── AC2: plain English — no raw gate id / verdict enum leaks (over the FULL real vocabulary) ─────

def test_ac2_no_raw_gate_id_or_verdict_enum_in_panel():
    gate_ids = list(story_signoff.GATE_CONTACT)               # every gate id, incl. hyphenated
    for gate, verdicts in REAL_VOCAB.items():
        for verdict in verdicts:
            contact = CONTACT[gate]
            row = _row(gate, verdict, contact)
            led = _ledger(reality=[row]) if contact in ("high", "medium") else _ledger(model=[row])
            whats = _all_what(project(led))
            assert len(whats) == 1, (gate, verdict, whats)
            what = whats[0]
            assert what.strip() and what[0].isupper()        # reads as an English phrase
            assert gate not in what, ("raw gate id leaked", gate, what)
            assert verdict not in what, ("raw verdict enum leaked", gate, verdict, what)

    # belt-and-braces: render the FULL vocabulary into the panel HTML; no hyphenated gate id survives.
    reality, model = [], []
    for gate, verdicts in REAL_VOCAB.items():
        for verdict in verdicts:
            (reality if CONTACT[gate] in ("high", "medium") else model).append(
                _row(gate, verdict, CONTACT[gate]))
    block = project(_ledger(reality=reality, model=model))
    block["_source"] = SRC
    panel = render_story._render_trust_signoff(block)
    m = re.search(r'<section class="signoff">.*?</section>', panel, re.DOTALL)
    assert m
    for gid in gate_ids:
        assert gid not in m.group(0), ("raw gate id in panel HTML", gid)


def test_ac2_unknown_verdict_passthrough_flagged_and_renders(tmp_path):
    # CONSTRAINT-2: an unknown verdict degrades LOUDLY (pass-through + flagged), NOT invented into false
    # English, and must NOT block the render (the M2 exit-3-vs-fail-visible contradiction is resolved).
    led = _ledger(model=[_row("code-review", "quantum-flux", "low")])
    b = project(led)
    assert any("quantum-flux" in i["what"] for i in b["model_approved"])   # raw token surfaced, flagged
    # and it still renders (exit 0), NOT exit 3
    b["_source"] = SRC
    src = tmp_path / "story-sections.json"
    src.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "slice-050",
                               "sections": [{"heading": "h", "body_md": "b"}], "trust_signoff": b}),
                   encoding="utf-8")
    cp = subprocess.run([sys.executable, str(RS_PATH), "--sections-file", str(src),
                         "--out", str(tmp_path / "story.html")],
                        capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, cp.stderr
    assert "quantum-flux" in (tmp_path / "story.html").read_text(encoding="utf-8")


# ── M5: not_checked + reality_surprises from STRUCTURE; free-form surprise text never leaks ──────

def test_m5_not_checked_reasons_translated_from_structure():
    nc = [
        {"text": "no criteria recorded in validation.json", "source": {"file": "validation.json",
         "locator": "criteria"}, "reason": "no-criteria"},
        {"text": "shippability regression not run", "source": {"file": "validation.json",
         "locator": "shippability_regression"}, "reason": "regression-not-run"},
        {"text": "AC3: no validation criterion recorded", "source": {"file": "mission-brief.json",
         "locator": "acceptance_criteria[2]"}, "reason": "criterion-absent", "ac": "AC3"},
    ]
    b = project(_ledger(not_checked=nc))
    whats = " || ".join(i["what"] for i in b["not_yet"])
    assert "no criteria recorded in validation.json" not in whats     # engineer text NOT echoed (M5)
    assert "acceptance checks were recorded" in whats                 # reason-derived English
    assert "regression test suite has not been run" in whats
    assert any(i.get("ref") == "AC3" for i in b["not_yet"])           # AC id -> ref, not raw prose


def test_projected_panel_text_is_ascii_only():
    # CR1: every panel string is WRITTEN into the vault artifact story-sections.json, whose writes are
    # ASCII-only (the em-dash the build fixed in _FLAG lurked again in _REASON_ENGLISH['no-criteria']).
    # Exercise the reason-enum + flagged + gate paths and assert no char > 127 survives.
    led = _ledger(
        reality=[_row("drift-check", "warn", "medium"), _row("validate-slice", "pass", "high")],
        model=[_row("critique", "needs-fixes", "low")],
        not_checked=[{"text": "x", "source": {"file": "validation.json", "locator": "criteria"},
                      "reason": "no-criteria"},
                     {"text": "AC2: ...", "source": {"file": "mission-brief.json", "locator": "a[1]"},
                      "reason": "criterion-absent", "ac": "AC2"}],
        surprises=[{"text": "reality surprise: x", "source": {"file": "validation.json", "locator": "rs[0]"},
                    "reason": "un-eliminated-defeater"}],
        shippability=[{"text": "regression FAILED", "source": {"file": "validation.json", "locator": "sr"},
                       "state": "failed"}],
    )
    b = project(led)
    for what in _all_what(b):
        what.encode("ascii")   # raises UnicodeEncodeError on any non-ASCII char


def test_m5_reality_surprise_is_a_count_never_verbatim_text():
    # a surprise note commonly embeds a trace id (ADR-014, R-27) — it must NEVER reach the panel verbatim.
    surprises = [
        {"text": "reality surprise: ADR-014 assumption broke on the real device",
         "source": {"file": "validation.json", "locator": "reality_surprises[0]"},
         "reason": "un-eliminated-defeater"},
        {"text": "reality surprise: SC-031 batch probe timed out",
         "source": {"file": "validation.json", "locator": "reality_surprises[1]"},
         "reason": "un-eliminated-defeater"},
    ]
    b = project(_ledger(surprises=surprises))
    blob = json.dumps(b)
    assert "ADR-014" not in blob and "SC-031" not in blob and "batch probe" not in blob
    assert any("raised 2 surprises" in i["what"] and i["what"].startswith(story_signoff._FLAG)
               for i in b["not_yet"])


# ── AC4 / M-add-2: absent OR malformed ledger is fail-visible (no green column) ──────────────────

def test_m_add_2_malformed_gate_log_is_unavailable():
    b = project(_ledger(reality=[_row("validate-slice", "pass", "high")], gatelog_status="malformed"))
    assert b["state"] == "unavailable" and b["reality_approved"] == []
    assert b.get("unavailable_reason")


def test_empty_but_valid_gate_log_is_available_not_unavailable():
    # the M-add-2 distinction: an empty-but-VALID gate-log is 'no reviews ran yet', NOT 'unreadable'.
    b = project(_ledger(gatelog_status="ok"))
    assert b["state"] == "ok"                                 # available; columns simply empty


def test_ac4_notice_has_no_green_column():
    b = project(_ledger(gatelog_status="empty"))
    b["_source"] = SRC
    panel = render_story._render_trust_signoff(b)
    assert "so-notyet" in panel and "Trust classification unavailable" in panel
    assert "so-reality" not in panel                          # AC4: never a populated green column


# ── AC1 + M6: the panel is DERIVED, and inject overwrites any forged narrator/trust_signoff ──────

def _seed_vault(root: Path, *, canon="slice-050", name="slice-050-x", gate_log_raw=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sdir = root / "slices" / name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "mission-brief.json").write_text(json.dumps(
        {"_schema": "aisdlc/mission-brief@1", "slice": canon,
         "acceptance_criteria": [{"id": "AC1"}, {"id": "AC2"}]}), encoding="utf-8")
    (sdir / "validation.json").write_text(json.dumps(
        {"_schema": "aisdlc/validation@1", "slice": canon, "result": "pass", "reality_contact": "high",
         "criteria": [{"id": "AC1", "result": "pass", "reality_proxy": "local-real-data"},
                      {"id": "AC2", "result": "pass"}],
         "reality_surprises": [], "shippability_regression": {"ran": True, "failed_rows": []}}),
        encoding="utf-8")
    gl = gate_log_raw if gate_log_raw is not None else json.dumps({"entries": [
        {"at": "2026-01-01T00:00:00Z", "slice": canon, "gate": "validate-slice", "verdict": "pass",
         "findings_count": 0, "reality_contact": "high", "reality_proxy": "local-real-data"},
        {"at": "2026-01-01T00:01:00Z", "slice": canon, "gate": "critique", "verdict": "needs-fixes",
         "findings_count": 3, "reality_contact": "low"},
    ]})
    (root / "gate-log.json").write_text(gl, encoding="utf-8")
    (root / "shippability.json").write_text(json.dumps({"rows": [], "counters": {}}), encoding="utf-8")
    return root


def _inject(vault: Path, slice_arg: str, sections: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SS_PATH), "inject", "--sections-file", str(sections),
                           "--slice", slice_arg, "--vault", str(vault)],
                          capture_output=True, text=True, encoding="utf-8")


def _render(sections: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RS_PATH), "--sections-file", str(sections), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8")


def test_ac1_and_m6_panel_derived_forged_content_overwritten(tmp_path):
    vault = _seed_vault(tmp_path / "v")
    sections = tmp_path / "story-sections.json"
    # a story with a DISTINCT narrator signoff AND a FORGED pre-existing trust_signoff (bogus stamp+members)
    sections.write_text(json.dumps({
        "_schema": "aisdlc/story-sections@1", "slice": "slice-050",
        "sections": [{"heading": "h", "body_md": "b"}],
        "signoff": {"reality_approved": [{"what": "NARRATOR FORGERY reality proof"}]},
        "trust_signoff": {"_source": SRC, "state": "ok",
                          "reality_approved": [{"what": "FORGED REALITY PROOF"}],
                          "model_approved": [], "not_yet": []},
    }), encoding="utf-8")

    ci = _inject(vault, "slice-050", sections)
    assert ci.returncode == 0, ci.stderr
    injected = json.loads(sections.read_text(encoding="utf-8"))["trust_signoff"]
    assert injected["_source"] == SRC and injected["derivation"] == "trust-ledger"   # audit: derivation logged
    # inject UNCONDITIONALLY overwrote the forged block with ledger-derived members
    assert any("Reality test" in i["what"] for i in injected["reality_approved"])
    assert all("FORGED" not in i["what"] for i in injected["reality_approved"])

    cr = _render(sections, tmp_path / "story.html")
    assert cr.returncode == 0, cr.stderr
    page = (tmp_path / "story.html").read_text(encoding="utf-8")
    assert "Reality test (real device or real data) succeeded." in page
    assert "NARRATOR FORGERY" not in page     # AC1: narrator signoff key never rendered
    assert "FORGED REALITY PROOF" not in page  # M6: forged trust_signoff overwritten


def test_ac4_absent_slice_injects_unavailable(tmp_path):
    vault = _seed_vault(tmp_path / "v")
    sections = tmp_path / "story-sections.json"
    sections.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "slice-777",
                                    "sections": [{"heading": "h", "body_md": "b"}]}), encoding="utf-8")
    ci = _inject(vault, "slice-777", sections)               # slice not in the vault
    assert ci.returncode == 0, ci.stderr
    block = json.loads(sections.read_text(encoding="utf-8"))["trust_signoff"]
    assert block["state"] == "unavailable" and block["reality_approved"] == []
    cr = _render(sections, tmp_path / "story.html")
    assert cr.returncode == 0, cr.stderr
    page = (tmp_path / "story.html").read_text(encoding="utf-8")
    # 'so-reality' also appears in the CSS; assert no rendered green COLUMN (div), not the stylesheet token.
    assert "Trust classification unavailable" in page and 'so-col so-reality' not in page


def test_inject_preserves_non_ascii_bytes_verbatim(tmp_path):
    # BC-PROJ-3: inject re-serializes story-sections.json; a non-ASCII field elsewhere in the file must
    # round-trip as the literal char (ensure_ascii=False + utf-8), never a \\uXXXX escape.
    vault = _seed_vault(tmp_path / "v")
    sections = tmp_path / "story-sections.json"
    sections.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "slice-050",
                                    "title": "café — résumé",
                                    "sections": [{"heading": "h", "body_md": "b"}]},
                                   ensure_ascii=False), encoding="utf-8")
    ci = _inject(vault, "slice-050", sections)
    assert ci.returncode == 0, ci.stderr
    raw = sections.read_text(encoding="utf-8")
    assert "café — résumé" in raw and "\\u00e9" not in raw    # literal chars, not escapes


def test_ac4_malformed_gate_log_injects_unavailable(tmp_path):
    # M-add-2 integration: slice PRESENT but gate-log.json malformed -> unavailable (distinct from absent-slice)
    vault = _seed_vault(tmp_path / "v", gate_log_raw="{ not : valid json ")
    sections = tmp_path / "story-sections.json"
    sections.write_text(json.dumps({"_schema": "aisdlc/story-sections@1", "slice": "slice-050",
                                    "sections": [{"heading": "h", "body_md": "b"}]}), encoding="utf-8")
    ci = _inject(vault, "slice-050", sections)
    assert ci.returncode == 0, ci.stderr
    block = json.loads(sections.read_text(encoding="utf-8"))["trust_signoff"]
    assert block["state"] == "unavailable" and block["reality_approved"] == []
