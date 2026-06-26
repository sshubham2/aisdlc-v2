"""expert_provenance.py -- offline provenance classification of a channeled expert's recorded source.

slice-039 / ADR-026. The design tournament's expert designer records each channeled expert with a
`source` (a citable URL where its position was verified at generation, or the literal 'training-knowledge').
This module classifies that source OFFLINE (no network) into a 3-way provenance verdict so a reader can
flag when a named "expert" is not backed by an external source the model could not have authored itself.

It is the anti-hallucination core of the design-tournament view (slice-039 AC2): a citable external URL
is an external trust anchor (`verified`); 'training-knowledge' is the model citing itself (`self-attested`);
a missing / empty / non-URL source is an unverifiable chain (`unverifiable`). FAIL-CLOSED: anything that is
not a clean cited URL is NOT `verified`.

HONEST LABEL (M2 / ADR-026): a `verified` verdict means a citable source is PRESENT, NOT that the expert or
source was confirmed real/live. A model-written URL has no cryptographic binding; a live-existence check
(resolve the URL, content-match the expert) is deliberately out of scope (a real source can 403 an
automated fetch -- proven at the slice-039 design spike) and is a future enhancement. The human-facing
badge therefore reads "cites a source", never "verified"/"proven real".

The SOLE provenance source-of-truth (M1) is design-proposals.json
`proposals[designer == 'designer-expert'].channeled_experts[]` -- the only artifact that carries `.source`
(design.json.tournament.channeled_experts is a lossy name-only copy). `channeled_experts` is heterogeneous
across the real corpus: a list of dicts-with-source, a list of bare strings, or dicts without a `source`
key -- every shape without a clean cited URL classifies UNVERIFIABLE, never a fabricated all-clear.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

VERIFIED = "verified"
SELF_ATTESTED = "self-attested"
UNVERIFIABLE = "unverifiable"

# Human-facing badge labels (M2: never "verified"/"proven real" -- only that a source is present).
BADGE_LABEL = {
    VERIFIED: "cites a source",
    SELF_ATTESTED: "self-attested",
    UNVERIFIABLE: "no source",
}

_TRAINING = "training-knowledge"
_TRAILING_PUNCT = ".,;:)]}>\"'"


def _is_clean_url(token: str) -> bool:
    """True iff `token` is a single well-formed http(s) URL with a host and NO inner whitespace.

    M3: this is the whole-token test -- it never falls back to "parse the first URL's netloc", so a
    compound or annotated string is only `verified` when one of its whitespace-split tokens is itself a
    complete http(s) URL.
    """
    if not token or any(ch.isspace() for ch in token):
        return False
    try:
        parsed = urlparse(token)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def classify_source(source) -> tuple[str, str]:
    """Classify a recorded expert `source` -> (verdict, reason). Offline, total, never raises.

    verdict is one of VERIFIED | SELF_ATTESTED | UNVERIFIABLE. FAIL-CLOSED: only a source containing a
    clean http(s) URL token is VERIFIED; the literal 'training-knowledge' is SELF_ATTESTED; everything
    else (None, empty, non-str, a non-URL citation such as a bare DOI/ISBN, a malformed string) is
    UNVERIFIABLE. M3: the source is split on whitespace and each token is checked as a WHOLE URL (after
    stripping trailing punctuation), so a real multi-URL citation still reads as citing a source while a
    bare non-URL citation reads UNVERIFIABLE (a documented URL-only scope; ADR-026).
    """
    if not isinstance(source, str):
        return UNVERIFIABLE, "no source recorded"
    stripped = source.strip()
    if not stripped:
        return UNVERIFIABLE, "empty source"
    if stripped.lower() == _TRAINING:
        return SELF_ATTESTED, "self-attested from training knowledge (no external source)"
    for token in re.split(r"\s+", stripped):
        if _is_clean_url(token.strip(_TRAILING_PUNCT)):
            return VERIFIED, "cites an external source (presence-checked, not confirmed live/real)"
    return UNVERIFIABLE, "no citable URL in the recorded source"


def classify_experts(channeled_experts) -> list[dict]:
    """Classify every entry in a design-proposal expert's `channeled_experts` (M1: the SOLE source).

    Tolerates the heterogeneous live shapes -- a list of dicts (with or without `.source`) or bare
    strings. Returns one row per entry: {name, source, verdict, reason, badge}. A non-dict entry, or a
    dict with no non-empty string `source`, classifies UNVERIFIABLE (a named-but-unsourced expert) and is
    surfaced, never dropped and never given a fabricated badge (AC5 / must-not-defer).
    """
    rows: list[dict] = []
    items = channeled_experts if isinstance(channeled_experts, list) else []
    for entry in items:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip() or "(unnamed expert)"
            src = entry.get("source")
        else:  # a bare string is a name with no recorded source
            name = str(entry).strip() or "(unnamed expert)"
            src = None
        verdict, reason = classify_source(src)
        rows.append({
            "name": name,
            "source": src if isinstance(src, str) else "",
            "verdict": verdict,
            "reason": reason,
            "badge": BADGE_LABEL[verdict],
        })
    return rows


def expert_proposal(proposals) -> dict | None:
    """Return the `designer-expert` proposal from a design-proposals.json `proposals[]` list, or None."""
    if not isinstance(proposals, list):
        return None
    for proposal in proposals:
        if isinstance(proposal, dict) and proposal.get("designer") == "designer-expert":
            return proposal
    return None


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from scripts.lib import _stdout  # noqa: E402
    _stdout.reconfigure_stdout_utf8()

    parser = argparse.ArgumentParser(
        prog="expert_provenance",
        description="Classify a channeled-expert source's provenance, offline (verified | self-attested | unverifiable).")
    parser.add_argument("--source", help="classify a single source string")
    parser.add_argument("--proposals-file", help="classify every expert in a design-proposals.json")
    args = parser.parse_args(argv)

    if args.source is not None:
        verdict, reason = classify_source(args.source)
        print(json.dumps({"source": args.source, "verdict": verdict,
                          "badge": BADGE_LABEL[verdict], "reason": reason}, ensure_ascii=False))
        return 0
    if args.proposals_file:
        try:
            data = json.loads(Path(args.proposals_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"expert_provenance: cannot read {args.proposals_file}: {exc}\n")
            return 1
        proposal = expert_proposal(data.get("proposals") if isinstance(data, dict) else None)
        rows = classify_experts(proposal.get("channeled_experts") if proposal else [])
        print(json.dumps({"experts": rows}, ensure_ascii=False, indent=2))
        return 0
    parser.error("pass --source or --proposals-file")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
