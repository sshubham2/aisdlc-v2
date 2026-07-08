"""Tests for scripts/lib/expert_provenance.py (slice-039 / ADR-026).

Pins the anti-hallucination contract: the offline 3-way verdict, the HONEST badge wording (M2: never
"verified"/"proven real"), the three live `channeled_experts` shapes (M1), and the M3 URL edge cases.
"""
from __future__ import annotations

from scripts.lib.expert_provenance import (
    BADGE_LABEL,
    SELF_ATTESTED,
    UNVERIFIABLE,
    VERIFIED,
    classify_experts,
    classify_source,
    expert_proposal,
)


# ----------------------------------------------------------------- classify_source
def test_real_url_is_verified():
    verdict, _ = classify_source("https://martinfowler.com/bliki/ArchitectureDecisionRecord.html")
    assert verdict == VERIFIED


def test_training_knowledge_is_self_attested():
    assert classify_source("training-knowledge")[0] == SELF_ATTESTED
    # case / whitespace insensitive
    assert classify_source("  Training-Knowledge  ")[0] == SELF_ATTESTED


def test_missing_or_empty_source_is_unverifiable():
    assert classify_source(None)[0] == UNVERIFIABLE
    assert classify_source("")[0] == UNVERIFIABLE
    assert classify_source("   ")[0] == UNVERIFIABLE
    assert classify_source(123)[0] == UNVERIFIABLE  # non-str never raises


def test_bare_non_url_citations_are_unverifiable_url_only_scope():
    # M3 (URL-only scope, documented in ADR-026): DOI / arXiv-token / ISBN read unverifiable.
    for src in ("doi:10.1145/3290605", "10.1145/3290605", "arXiv:2606.04990", "ISBN 978-0131177055"):
        assert classify_source(src)[0] == UNVERIFIABLE, src


def test_compound_and_annotated_urls_still_cite_a_source():
    # M3: a real multi-URL citation and a trailing-annotation URL both genuinely cite a source.
    assert classify_source("https://git-cliff.org/docs/ + https://github.com/orhun/git-cliff")[0] == VERIFIED
    assert classify_source("https://martinfowler.com/x.html (verified 2026-06-26)")[0] == VERIFIED


def test_non_http_schemes_are_not_verified():
    # fail-closed: javascript:/data:/ftp: are not a citable external web source
    for src in ("javascript:alert(1)", "data:text/html,<b>x", "ftp://example.com/f"):
        assert classify_source(src)[0] == UNVERIFIABLE, src


def test_badge_labels_are_honest_M2():
    # The owner-facing label must never claim more than presence.
    assert BADGE_LABEL[VERIFIED] == "cites a source"
    assert "verified" not in BADGE_LABEL[VERIFIED].lower()
    assert "proven" not in BADGE_LABEL[VERIFIED].lower()
    assert BADGE_LABEL[SELF_ATTESTED] == "self-attested"
    assert BADGE_LABEL[UNVERIFIABLE] == "no source"


# ----------------------------------------------------------------- classify_experts (M1: 3 live shapes)
def test_shape_list_of_dicts_with_source():
    experts = [
        {"name": "Michael Nygard", "source": "https://martinfowler.com/bliki/ArchitectureDecisionRecord.html"},
        {"name": "Some One", "source": "training-knowledge"},
    ]
    rows = classify_experts(experts)
    assert [r["verdict"] for r in rows] == [VERIFIED, SELF_ATTESTED]
    assert rows[0]["badge"] == "cites a source"
    assert rows[0]["name"] == "Michael Nygard"


def test_shape_list_of_bare_strings_is_unverifiable():
    # slice-019 / slice-034 shape: bare names, no source recorded.
    rows = classify_experts(["Leslie Lamport", "Michael Feathers"])
    assert all(r["verdict"] == UNVERIFIABLE for r in rows)
    assert {r["name"] for r in rows} == {"Leslie Lamport", "Michael Feathers"}


def test_shape_dict_without_source_key_is_unverifiable():
    # slice-031 shape: {name, via} dicts that never carry a `.source`.
    rows = classify_experts([{"name": "Eric Raymond", "via": "designer-expert"}])
    assert rows[0]["verdict"] == UNVERIFIABLE
    assert rows[0]["name"] == "Eric Raymond"


def test_non_dict_entries_are_surfaced_not_dropped():
    rows = classify_experts(["a-name", {"name": "b", "source": "https://example.com/p"}, 42])
    assert len(rows) == 3  # nothing dropped (AC5 / must-not-defer)
    assert rows[2]["verdict"] == UNVERIFIABLE


def test_non_list_channeled_experts_yields_empty():
    assert classify_experts(None) == []
    assert classify_experts("not a list") == []


# ----------------------------------------------------------------- expert_proposal
def test_expert_proposal_selects_designer_expert():
    proposals = [
        {"designer": "designer-practice", "approach": "x"},
        {"designer": "designer-expert", "channeled_experts": [{"name": "n", "source": "https://e.com/p"}]},
    ]
    proposal = expert_proposal(proposals)
    assert proposal is not None and proposal["designer"] == "designer-expert"


def test_expert_proposal_absent_returns_none():
    assert expert_proposal([{"designer": "designer-practice"}]) is None
    assert expert_proposal(None) is None
