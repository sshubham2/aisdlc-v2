"""slice-045 — write-boundary artifact_lint gate at the agent->artifact handoffs.

Two kinds of test:

1. BEHAVIOR (regression guards) — pin what artifact_lint already does that the
   slice relies on (it is reused UNCHANGED), plus the hardened NO-CODE-CHANGES shape
   the /code-review skill must emit so the new lint never false-fails an empty review.

2. WIRING (presence) — assert each of the four stations actually invokes artifact_lint
   on its own artifact. Skill bodies are markdown, not executable, so presence in the
   SKILL.md prose is the strongest available proof that the gate is wired (m1). These
   are the genuine red->green drivers for AC1/AC2/AC5: red until the SKILL.md edits land.
"""
from __future__ import annotations

import copy

from scripts.lib import artifact_lint
from scripts.lib.artifact_lint import _load_examples, lint_artifact


# ── Behavior: the production bug + its inverse (AC3) ──────────────────────────────

def test_lint_rejects_dimensions_key():
    """THE production bug: code-review.json carrying `dimensions` instead of the
    required `dimensions_checked` must be rejected with a message naming the missing key.
    Reused UNCHANGED -> this is a regression guard (green from the start), per m1."""
    examples = _load_examples()
    cr = copy.deepcopy(examples["code-review"])
    cr["_schema"] = "aisdlc/code-review@1"
    assert "dimensions_checked" in cr, "fixture precondition: canonical example has dimensions_checked"
    cr["dimensions"] = cr.pop("dimensions_checked")  # the exact production rename
    violations = lint_artifact(cr, "code-review", examples["code-review"], "test")
    assert any("dimensions_checked" in v for v in violations), violations


def test_lint_accepts_canonical():
    """The conforming canonical code-review example lints clean."""
    examples = _load_examples()
    cr = copy.deepcopy(examples["code-review"])
    assert lint_artifact(cr, "code-review", examples["code-review"], "test") == []


# ── Behavior: the hardened NO-CODE-CHANGES shape (M2) ────────────────────────────

def test_no_code_changes_shape_lints_clean():
    """The empty-review exit of /code-review must write a schema-COMPLETE artifact so the
    new write-boundary lint does not false-fail a legitimately empty review (M2). This
    pins the exact key set the SKILL.md:64 hardening must emit."""
    examples = _load_examples()
    no_code = {
        "_schema": "aisdlc/code-review@1",
        "slice": "slice-NNN",
        "reviewed_by": "code-review agent",
        "result": "NO-CODE-CHANGES",
        "findings": [],
        "dimensions_checked": [],
        "triage": None,
    }
    assert lint_artifact(no_code, "code-review", examples["code-review"], "test") == []


# ── Behavior: no regression in the lint's own self-check (AC4) ────────────────────

def test_artifact_lint_self_check_still_passes():
    """AC4 (no regression): the linter's self-check over every canonical example stays green
    -- this slice adds call sites + a test only, it must not perturb artifact_lint itself."""
    assert artifact_lint.main(["--self-check"]) == 0


# ── Wiring: each station invokes artifact_lint on its own artifact ────────────────

def _skill_text(plugin_root, name: str) -> str:
    p = plugin_root / "skills" / name / "SKILL.md"
    assert p.is_file(), f"SKILL.md not found: {p}"
    return p.read_text(encoding="utf-8")


def test_code_review_skill_invokes_artifact_lint(plugin_root):
    """AC1: /code-review runs the in-fork best-effort lint on its own code-review.json."""
    text = _skill_text(plugin_root, "code-review")
    assert "artifact_lint.py" in text, "code-review SKILL.md must invoke artifact_lint.py"
    assert "--type code-review" in text, "the in-fork lint must pin --type code-review"


def test_critique_skill_invokes_artifact_lint(plugin_root):
    """AC2: /critique runs the receiving-inspection lint on critique.json."""
    text = _skill_text(plugin_root, "critique")
    assert "artifact_lint.py" in text, "critique SKILL.md must invoke artifact_lint.py"
    assert "--type critique" in text, "the critique lint must pin --type critique"


def test_critique_review_skill_invokes_artifact_lint(plugin_root):
    """AC2: /critique-review runs the receiving-inspection lint on critique-review.json."""
    text = _skill_text(plugin_root, "critique-review")
    assert "artifact_lint.py" in text, "critique-review SKILL.md must invoke artifact_lint.py"
    assert "--type critique-review" in text, "the critique-review lint must pin --type critique-review"


def test_validate_slice_gate_present(plugin_root):
    """AC5: /validate-slice has the DETERMINISTIC prerequisite gate that lints the slice's
    code-review.json and refuses to proceed on a violation (the independent main-thread
    backstop -- the keystone of M1/M-add-1)."""
    text = _skill_text(plugin_root, "validate-slice")
    assert "artifact_lint.py" in text, "validate-slice SKILL.md must invoke artifact_lint.py"
    assert "--type code-review" in text, "the deterministic gate must pin --type code-review"
