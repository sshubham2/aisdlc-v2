"""slice-061 AC4/AC5 — doc + runbook verification (the non-unit ACs made checkable).

AC4: the integration-branch docs name ``aisdlc-uat`` and document the legacy-``uat``
     back-compat fallback (verification: "grep the updated docs for aisdlc-uat + the
     fallback note").
AC5: the one-time live-rename runbook is committed and specifies the NATIVE GitHub
     branch-rename mechanism (M1), not push-delete, with its pre-rename preconditions.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_docs_updated_to_aisdlc_uat():
    # AC4: every integration-branch doc names the namespaced integration branch.
    for rel in (
        "CLAUDE.md",
        "skills/build-slice/SKILL.md",
        "skills/commit-slice/SKILL.md",
        "skills/slice/SKILL.md",
        "skills/release/SKILL.md",
    ):
        assert "aisdlc-uat" in _read(rel), f"{rel} must name the aisdlc-uat integration branch"

    # AC4: the genesis-gated legacy-uat back-compat fallback is documented (CLAUDE.md is canonical).
    claude = _read("CLAUDE.md").lower()
    assert "release-genesis" in claude and "legacy" in claude and "back-compat" in claude, \
        "CLAUDE.md must document the genesis-gated legacy-uat back-compat fallback"


def test_rename_runbook_present_and_uses_native_rename():
    # AC5 / M1: the runbook is committed and uses the NATIVE GitHub branch-rename, not push-delete.
    rb = _read("docs/runbooks/aisdlc-uat-rename.md")
    assert "branches/uat/rename" in rb, "runbook must specify the native GitHub branch-rename API (M1)"
    assert "native" in rb.lower(), "runbook must frame the mechanism as the native rename"
    # M1 pre-rename preconditions: open uat-based PRs + genesis-descent invariant.
    assert "gh pr list --base uat" in rb, "runbook must gate on open uat-based PRs (M1 precondition)"
    assert "release-genesis" in rb, "runbook must verify the release-genesis invariant survives the rename"
