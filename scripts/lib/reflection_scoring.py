"""reflection_scoring.py -- pluggable relevance scorers for in-loop lesson recall (v2, NEW; slice-063 / SC-096).

The design-time knowledge recall that feeds `/design-slice` (see ``reflection_lookup.py``) used to match past
work with a BINARY, exact-keyword rule: LEG1 (nearest prior slice) needed ``>=2`` exactly-shared mission
keywords, LEG2 (relevant reflections) needed ``>=1`` substring keyword hit. On a real run that returned "no
match" with dozens of relevant lessons on disk (the ``>=2`` cliff), because it counted a match on a near-universal
term ('review', in 46/59 docs) the same as a match on a rare, telling term ('cp1252', df=1).

This module replaces that cliff with a GRADED ranking behind a tiny pluggable **scorer registry** -- so the
relevance engine is swappable by name and a smarter one can drop in later (SC-116's model2vec tier) with ZERO
call-site edits. A "scorer" is any callable
``rank(query, docs, *, leg) -> list[(doc_index, score)]`` (already filtered + sorted desc) where:

- ``query`` and each ``docs`` element is a record ``{"tokens": [str, ...], "text": "<raw lowercased text>"}``.
- ``leg`` is ``"nearest_slice"`` (LEG1) or ``"reflections"`` (LEG2) -- only the ``lexical`` scorer's match rule
  differs by leg (LEG1 set-count vs LEG2 substring); ``tfidf-cosine`` is leg-agnostic.
- **Each scorer OWNS its own tokenization + threshold + zero-norm handling** (slice-063 M1): the caller never
  applies a threshold, so the ``lexical`` fallback preserves today's TWO distinct behaviors byte-for-byte while
  the default ``tfidf-cosine`` applies its own epsilon floor.

Registered scorers:

- ``tfidf-cosine`` (DEFAULT) -- pure-stdlib (``math`` + ``collections``; NO numpy, NO embedding libraries): SMART
  'ltc' weighting -- sublinear TF ``1+log(tf)``, SMOOTHED NON-NEGATIVE IDF ``log((1+N)/(1+df))+1`` (the smoothing
  is load-bearing on the tiny, half-hapax corpus: naive ``log(N/df)`` is unstable / can go negative -- proven on
  the real 59-doc corpus by ``spike-graded-recall-composition``), L2-normalized cosine, an ``EPSILON`` relevance
  floor, and a zero-norm guard so an empty/all-stopword document scores 0 and is skipped (never a crash -- M2).
  NOTE (M-add-2): LEG1 scores over the deduplicated mission-keyword SET, so every term has TF=1 and the sublinear
  TF is inert -- LEG1 degenerates to IDF-weighted set-cosine (still strictly better than the binary ``>=2`` count).

- ``lexical`` -- today's exact behavior, preserved VERBATIM. Serves DOUBLE DUTY (slice-063): the crash-proof
  degrade target when the chosen scorer errors/absent (AC2), AND the second registered scorer that proves the
  seam is real (AC5) -- a genuinely different weighting registering with zero call-site edits.

Adding a new relevance engine (e.g. BM25, or SC-116's model2vec static-embedding tier): write a
``@register("<name>") def <name>(query, docs, *, leg): ...`` returning the ranked list -- no change to
``reflection_lookup.py``'s call sites. An optional-dependency scorer (model2vec) must import its dep lazily and
raise ``ImportError`` cleanly when absent so ``reflection_lookup`` degrades to ``lexical`` (never crashes the CLI).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Callable

# Relevance floor for the graded (tfidf-cosine) scorer. Calibrated on the REAL corpus at N=59 documents
# (spike-graded-recall-composition: relevant matches clustered 0.10-0.46, noise <0.05). Smoothed IDF
# log((1+N)/(1+df)) grows with N, so re-check this floor at /validate-slice as the corpus grows past ~2x
# (slice-063 m2). NOT a magic 0 (re-admits near-noise) and NOT >0.15 (recreates the >=2-keyword cliff).
EPSILON = 0.05
_CALIBRATION_N = 59

# name -> rank(query, docs, *, leg) -> list[(idx, score)]
_SCORERS: dict[str, Callable] = {}


def register(name: str):
    """Decorator: register a scorer callable under ``name`` in the process-wide registry.
    A second/future scorer registers with this ONE call and touches no call site (AC5)."""
    def _deco(fn: Callable) -> Callable:
        _SCORERS[name] = fn
        return fn
    return _deco


def get_scorer(name: str) -> Callable | None:
    """Resolve a registered scorer by name, or ``None`` for an unknown name (the caller then
    degrades to the ``lexical`` fallback with a visible note -- never a crash)."""
    return _SCORERS.get(name)


def list_scorers() -> list[str]:
    """Registered scorer names, sorted (for --help / diagnostics)."""
    return sorted(_SCORERS)


DEFAULT_SCORER = "tfidf-cosine"
FALLBACK_SCORER = "lexical"


def _vec(tokens, idf: dict[str, float]):
    """Sublinear-TF x IDF weighted vector over the CORPUS vocabulary (terms absent from ``idf`` are
    dropped -- a query-only term contributes nothing), plus its L2 norm."""
    tf = Counter(tokens)
    v: dict[str, float] = {}
    for w, c in tf.items():
        iw = idf.get(w)
        if iw is None:
            continue
        v[w] = (1.0 + math.log(c)) * iw
    norm = math.sqrt(sum(x * x for x in v.values()))
    return v, norm


@register("tfidf-cosine")
def tfidf_cosine(query: dict, docs: list[dict], *, leg: str | None = None) -> list[tuple[int, float]]:
    """Graded relevance: smoothed-IDF sublinear-TF L2-cosine of the query against each doc, floored at
    ``EPSILON``. leg-agnostic (LEG1 vs LEG2 differ only in the corpus the caller builds). Pure stdlib."""
    n = len(docs)
    if n == 0:
        return []
    df: Counter = Counter()
    for d in docs:
        for w in set(d.get("tokens") or ()):
            df[w] += 1
    # Smoothed NON-NEGATIVE IDF: >= 1 always, so a shared rare term never subtracts; common terms decay toward ~0.
    idf = {w: math.log((1 + n) / (1 + dfw)) + 1.0 for w, dfw in df.items()}
    qv, qn = _vec(query.get("tokens") or (), idf)
    if qn == 0.0:  # query shares no corpus vocabulary -> nothing to rank
        return []
    out: list[tuple[int, float]] = []
    for i, d in enumerate(docs):
        dv, dn = _vec(d.get("tokens") or (), idf)
        if dn == 0.0:  # zero-norm doc (empty / all-stopword) -> score 0, skip. NEVER a ZeroDivisionError (M2).
            continue
        num = sum(qv.get(w, 0.0) * dv[w] for w in dv)
        s = num / (qn * dn)
        if s > EPSILON:
            out.append((i, s))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


@register("lexical")
def lexical(query: dict, docs: list[dict], *, leg: str | None = None) -> list[tuple[int, float]]:
    """The pre-slice-063 behavior, preserved VERBATIM (the crash-proof fallback AND the AC5 witness):
    LEG1 nearest_slice = count of exactly-shared mission keywords, kept at ``>=2``;
    LEG2 reflections    = count of query keywords SUBSTRING-present in the doc text, kept at ``>=1``.
    Score = the integer hit count (matches today's ranking key exactly)."""
    qk = set(query.get("tokens") or ())
    out: list[tuple[int, float]] = []
    if leg == "reflections":
        for i, d in enumerate(docs):
            blob = d.get("text") or ""
            hits = [k for k in qk if k in blob]
            if hits:  # >=1 substring hit (today's LEG2 gate)
                out.append((i, float(len(hits))))
    else:  # nearest_slice (LEG1)
        for i, d in enumerate(docs):
            shared = qk & set(d.get("tokens") or ())
            if len(shared) >= 2:  # today's LEG1 gate
                out.append((i, float(len(shared))))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out
