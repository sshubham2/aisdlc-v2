"""Bug: artifact_lint passes a reflection.json whose calibration rows are under the WRONG key (SC-227 / slice-101).

``skills/reflect/SKILL.md`` (:160, :190) instructs the writer to emit a ``calibration[]`` array into
``reflection.json``. The CANONICAL key every consumer reads is ``critic_calibration`` --
``skills/reflect/examples/reflection.json``:31, ``schemas/artifact-examples.json``:229, and the sole
consumer ``/critic-calibrate`` (``skills/critic-calibrate/SKILL.md``:44, :150). A writer obeying BOTH
the prose and the schema produces the HYBRID shape: the structured rows land under ``calibration[]``
while ``critic_calibration`` is satisfied with a prose STRING.

That is not hypothetical. It is the live shape at
``<vault>/slices/archive/slice-075-define-capability-to-candidates-one-to-many-relation/reflection.json``:
``calibration`` holds the ``{finding, verdict, note}`` rows, ``critic_calibration`` holds a paragraph of
prose. ``/critic-calibrate`` therefore text-mines a paragraph for that slice -- precisely the failure the
reflect rule "record the verdicts STRUCTURED, not just prose" exists to prevent -- and nothing reports it.

Why the lint does not catch it today: ``_required_keys`` only checks key PRESENCE, so the prose string
satisfies ``critic_calibration``; there is no type check. The one rule that names the field
(``ENUM_EXCLUSIONS[("reflection", "critic_calibration[].verdict")]``) is an EXCLUSION -- documented,
deliberately unenforced -- and ``_walk`` returns ``[]`` for a ``critic_calibration[]`` hop over a string
anyway. The non-canonical ``calibration`` key is not rejected either.

Expected: artifact_lint REFUSES the hybrid shape, with a violation naming ``critic_calibration``.
Actual:   artifact_lint reports "clean", rc=0.

Measured on the live vault at repro time: 96/96 reflections carry ``critic_calibration``; 1 also carries
the stray ``calibration``; 3 carry ``missed_by_critic`` (a key ``/critic-calibrate`` reads that no producer
prose and no schema-by-example defines).

Note the ONLY shape already caught: rows under ``calibration[]`` with ``critic_calibration`` absent
entirely fails the missing-required-key check. The escape is exactly the hybrid a schema-obedient writer
produces. ``test_pure_prose_shape_stays_caught`` pins that existing coverage so a fix cannot regress it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import artifact_lint  # noqa: E402
from scripts.lib.artifact_lint import _load_examples, lint_artifact  # noqa: E402

_ROWS = [{"finding": "M1", "verdict": "VALIDATED", "note": "the accumulator split shipped as the real control."}]


def _reflection(**overrides) -> dict:
    """A minimal reflection.json carrying every required key, before the calibration overrides."""
    base = {
        "_schema": "aisdlc/reflection@1",
        "slice": "slice-x",
        "validated": [],
        "corrected": [],
        "discovered": [],
        "deferred": [],
        "critic_calibration": _ROWS,
        "lessons": ["a lesson"],
        "supersession": None,
        "at": "<ts>",
    }
    base.update(overrides)
    return base


def _violations(data: dict) -> list[str]:
    return lint_artifact(data, "reflection", _load_examples()["reflection"], "test")


def test_lint_refuses_prose_key_drift_hybrid(tmp_path: Path):
    """THE BUG: rows under `calibration[]` + a prose STRING under `critic_calibration` lints clean."""
    drifted = _reflection(
        calibration=_ROWS,
        critic_calibration="First Critic: 8/9 findings VALIDATED, 1 FALSE-ALARM on the tombstone.",
    )
    violations = _violations(drifted)
    assert any("critic_calibration" in v for v in violations), (
        "artifact_lint accepted a reflection whose calibration rows sit under the non-canonical "
        f"`calibration` key while `critic_calibration` holds a prose string; violations={violations!r}"
    )

    # and the same shape must be refused through the production CLI path (non-zero rc)
    path = tmp_path / "reflection.json"
    path.write_text(json.dumps(drifted, indent=1), encoding="utf-8")
    assert artifact_lint.main([str(path)]) != 0


def test_pure_prose_shape_stays_caught():
    """Regression pin: `calibration[]` with NO `critic_calibration` is already caught (missing key)."""
    prose_only = _reflection(calibration=_ROWS)
    del prose_only["critic_calibration"]
    assert any("critic_calibration" in v for v in _violations(prose_only))


def test_canonical_reflection_stays_clean(tmp_path: Path):
    """Control: the canonical shape must NOT be false-rejected by the fix."""
    canonical = _reflection()
    assert _violations(canonical) == []

    path = tmp_path / "reflection.json"
    path.write_text(json.dumps(canonical, indent=1), encoding="utf-8")
    assert artifact_lint.main([str(path)]) == 0


# ── slice-101 build: the two DECLARATION-DRIFT guards (AC1, AC3) ─────────────────────
#
# Both guards exist because this slice's own fix ships TWO new hand-maintained declarations
# (the reflect prose and the /critic-calibrate read-set marker), and a hand-maintained
# declaration that nothing checks is precisely the defect class the slice is closing. Every
# predicate below was chosen by EXECUTION against the pre-fix file, not by intent (critique
# M1/M2): the obvious bare-word form (`'calibration' not in text`) is permanently RED because
# skills/reflect/SKILL.md legitimately uses the word in non-key prose, and the obvious
# backticked form is already GREEN pre-fix and therefore pins nothing.

REFLECT_SKILL = REPO_ROOT / "skills" / "reflect" / "SKILL.md"
CALIBRATE_SKILL = REPO_ROOT / "skills" / "critic-calibrate" / "SKILL.md"
CANON_ANCHOR = "[aisdlc:reflection-calibration-canonical-key"
READ_SET_ANCHOR = "[aisdlc:reflection-read-set"

# A bare snake_case artifact key, FULL-SPAN anchored. An UNANCHORED search would harvest
# 'vault', 'slices', 'archive', 'folder' and 'reflection' out of the extraction line's path
# span `<vault>/slices/archive/<folder>/reflection.json` and make the guard permanently red.
_BARE_KEY = re.compile(r"^[a-z][a-z0-9_]+$")
_BACKTICK = re.compile(r"`([^`\n]+)`")


def _canonical_calibration_key() -> str:
    """The ONE canonical key, DERIVED rather than hardcoded: the reflection field registered in
    ROW_ARRAY_KEYS, cross-checked against the canonical example. A future schema rename fails
    here (and in row_array_keys_resolve) instead of silently retiring the prose guard."""
    fields = sorted(f for (art, f) in artifact_lint.ROW_ARRAY_KEYS if art == "reflection")
    assert len(fields) == 1, f"expected exactly ONE registered reflection row-array field, got {fields}"
    key = fields[0]
    assert key in _load_examples()["reflection"], (
        f"the registered reflection row-array field {key!r} is not a top-level key of the canonical "
        "reflection example in schemas/artifact-examples.json -- this guard must track the SCHEMA, "
        "never a literal"
    )
    return key


def _reserved_aliases() -> tuple[str, ...]:
    for (art, _field), spec in artifact_lint.ROW_ARRAY_KEYS.items():
        if art == "reflection":
            return tuple(spec.get("aliases", ()))
    return ()


def test_reflect_prose_names_canonical_key():
    """AC1: the producer prose names the canonical key and nothing else (three predicates, each
    independently RED against the pre-fix skills/reflect/SKILL.md -- verified by execution)."""
    text = REFLECT_SKILL.read_text(encoding="utf-8")
    canon = _canonical_calibration_key()

    aliases = _reserved_aliases()
    assert aliases, "the reflection registry entry must reserve at least one non-canonical alias"
    for alias in aliases:
        # Bracket-anchored: the bare word is legitimate non-key prose in this file, and the
        # bracket-suffixed form is exactly the writer INSTRUCTION that has to be gone. The
        # PREDICATE is deliberately this simple literal (design taste_disagreements[3]; ADR-135's
        # no-bracket-suffix corollary is imposed by it). But `critic_calibration[]` CONTAINS
        # `calibration[]`, so the DIAGNOSTIC has to say which of the two actually matched --
        # otherwise a canonical-key-with-suffix reports the wrong cause (code-review CR5).
        token = f"{alias}[]"
        hits = [m.start() for m in re.finditer(re.escape(token), text)]
        suffixed_canon = [h for h in hits if text[:h].endswith(canon[:-len(alias)])]
        assert not hits, (
            f"skills/reflect/SKILL.md writes the canonical key with a bracketed array suffix "
            f"(`{canon}[]`), which CONTAINS the reserved `{token}` token -- drop the suffix "
            f"(ADR-135 corollary); AC1's own `git grep '{token}'` verify command keys on this too"
            if suffixed_canon and len(suffixed_canon) == len(hits) else
            f"skills/reflect/SKILL.md still instructs the writer with the non-canonical "
            f"`{token}` array -- the rows belong under `{canon}`"
        )

    assert canon in text, (
        f"skills/reflect/SKILL.md never names the canonical key `{canon}` -- a writer obeying the "
        "prose lands the rows somewhere the consumer does not read"
    )
    assert CANON_ANCHOR in text, (
        f"the {CANON_ANCHOR} ... ] doc-guard anchor must mark the sentences this test pins, so a "
        "future editor sees they are machine-guarded and why"
    )
    # The anchor must not itself re-introduce the token it exists to forbid.
    anchor_body = text[text.index(CANON_ANCHOR):text.index("]", text.index(CANON_ANCHOR))]
    for alias in aliases:
        assert f"{alias}[]" not in anchor_body


def _extraction_read_set() -> set[str]:
    """DERIVE the read-set from /critic-calibrate's own extraction prose -- never from the
    marker. Trusting the marker would make it a second hand-maintained declaration that
    nothing checks, which is this slice's defect class one level up (critique M2)."""
    for line in CALIBRATE_SKILL.read_text(encoding="utf-8").splitlines():
        if "reflection.json" in line and "Extract" in line:
            return {s for s in _BACKTICK.findall(line) if _BARE_KEY.fullmatch(s)}
    raise AssertionError(
        "no extraction line (containing both 'reflection.json' and 'Extract') in "
        "skills/critic-calibrate/SKILL.md -- the read-set can no longer be derived"
    )


def _marker_read_set() -> set[str]:
    text = CALIBRATE_SKILL.read_text(encoding="utf-8")
    assert READ_SET_ANCHOR in text, (
        f"skills/critic-calibrate/SKILL.md carries no {READ_SET_ANCHOR} ... ] marker declaring "
        "which reflection.json keys this consumer reads"
    )
    start = text.index(READ_SET_ANCHOR)
    close = text.index("]", start)
    return {s for s in _BACKTICK.findall(text[start:close]) if _BARE_KEY.fullmatch(s)}


def test_critic_calibrate_read_set_is_published():
    """AC3: no consumer reads a reflection key no producer defines -- and the declaration that
    says so is pinned to the extraction prose itself, not hand-copied beside it."""
    extracted = _extraction_read_set()
    assert extracted, "the extraction line names no snake_case reflection key"

    assert _marker_read_set() == extracted, (
        "the read-set marker and the extraction prose have diverged: marker="
        f"{sorted(_marker_read_set())} vs extraction={sorted(extracted)} -- a key added to the "
        "extraction sentence without updating the marker would otherwise go unnoticed forever"
    )

    published = set(_load_examples()["reflection"])
    missing = sorted(extracted - published)
    assert not missing, (
        f"/critic-calibrate extracts {missing} from reflection.json, but no producer defines "
        "them: they are absent from the canonical reflection example in "
        "schemas/artifact-examples.json"
    )


# ── slice-101 build: the error-path matrix (must-not-defer #3) ───────────────────────
#
# artifact_lint runs INSIDE /build-slice's pre-finish gate, where a traceback is a hard stop
# rather than a finding. Every malformed shape below must produce a violation STRING (or a
# clean []), never an exception. The matrix is the spiked 17-case set pinned as a permanent
# regression, plus the two alias cases critique m1 required.

# JSON-representable malformed shapes — these are what a real vault file can actually hold, so
# they run through BOTH the in-memory helper and the production CLI.
_MALFORMED_JSON = [
    None, "prose", 0, 1, 1.5, True, False, {"finding": "M1"},
    ["a string row"], [None], [["nested"]], [{"finding": "M1"}],           # row missing `verdict`
    [{"finding": "M1", "verdict": "   "}],                                 # whitespace-only verdict
    [{"finding": "M1", "verdict": None}],
]
# A non-list SEQUENCE reaches the helper only via a direct Python call: json.dumps turns a tuple
# into `[]`, which is a LEGAL empty list, so this case is deliberately in-memory-only. Pinning it
# on the CLI leg would be testing json.dumps, not the rule.
_MALFORMED = _MALFORMED_JSON + [()]


@pytest.mark.parametrize("value", _MALFORMED)
def test_malformed_calibration_never_raises(value):
    """DIAGNOSE, never abort: every branch returns violation strings."""
    out = _violations(_reflection(critic_calibration=value))
    assert isinstance(out, list) and all(isinstance(s, str) for s in out)
    assert any("critic_calibration" in s for s in out), (
        f"a malformed critic_calibration ({value!r}) produced no violation naming the field: {out!r}"
    )


@pytest.mark.parametrize("value", _MALFORMED_JSON)
def test_malformed_calibration_is_refused_through_the_cli(tmp_path: Path, value):
    """Same matrix through the production entry point: non-zero rc, no traceback. No `default=`
    fallback on the dump — a silent stringification would launder a case into a different one."""
    path = tmp_path / "reflection.json"
    path.write_text(json.dumps(_reflection(critic_calibration=value), indent=1), encoding="utf-8")
    assert artifact_lint.main([str(path)]) == 1


def test_absent_key_is_reported_exactly_once():
    """The seam with `_required_keys`: an ABSENT canonical key is its business, not this
    family's. Double-reporting would flip test_pure_prose_shape_stays_caught."""
    absent = _reflection()
    del absent["critic_calibration"]
    hits = [v for v in _violations(absent) if "critic_calibration" in v]
    assert len(hits) == 1, f"expected exactly one violation for the absent key, got {hits!r}"
    assert "missing required key" in hits[0]


def test_empty_calibration_list_is_legal():
    """The genuinely Critic-skipped case (pinned by the live slice-023 record) stays clean."""
    assert _violations(_reflection(critic_calibration=[])) == []


@pytest.mark.parametrize("alias_value", [None, [], "", 0, _ROWS])
def test_reserved_alias_is_presence_keyed_not_truthiness_keyed(alias_value):
    """critique m1: a reserved NAME is about the name EXISTING, not its value — deliberately
    unlike the PRESENCE_SYMMETRIC family. A half-repair leaving `calibration: null` behind must
    still be refused, exactly like the half-repair that ruled out `vault_edit set` for AC4."""
    out = _violations(_reflection(calibration=alias_value))
    assert any("`calibration`" in v for v in out), (
        f"a stray `calibration` key holding {alias_value!r} was accepted; violations={out!r}"
    )


def test_non_dict_top_level_does_not_raise():
    for junk in (None, "prose", [1, 2], 7):
        assert isinstance(artifact_lint._row_array_violations(junk, "reflection", "t"), list)


# ── slice-101 build: live-corpus shape fixtures + the AC4 repaired shape ─────────────

def test_observed_corpus_row_shapes_stay_clean():
    """Both row shapes this vault actually carries, and the FOREIGN vault shape that refuted the
    `finding` requirement at the design spike (ADR-137): a row spelling the field `finding_id`
    is legitimate and must NOT be red — 10 such rows live in generic_workflow-07cc8bc5."""
    rows = [
        {"finding": "M1", "verdict": "VALIDATED"},
        {"finding": "M2", "verdict": "FALSE-ALARM", "note": "over-reach on the tombstone"},
        {"finding_id": "M3", "verdict": "MISSED", "note": "a foreign vault's spelling"},
    ]
    assert _violations(_reflection(critic_calibration=rows)) == []


def test_published_missed_by_critic_is_optional_and_unenforced():
    """ADR-138: PUBLISH, do not enforce. The 93 reflections that omit the key stay green, and
    the three divergent live shapes (absent / [] / list-of-str / list-of-dict) all lint clean —
    registering it would red slice-088 on contact (must-not-defer #1)."""
    assert "missed_by_critic" in _load_examples()["reflection"], "the key must be published"
    for shape in (None, [], ["a bare string miss (slice-088)"],
                  [{"id": "MISSED-1", "gate": "critique", "severity": "major",
                    "caught_by": "build", "note": "slice-100 shape"}]):
        rec = _reflection()
        if shape is not None:
            rec["missed_by_critic"] = shape
        assert _violations(rec) == [], f"missed_by_critic={shape!r} must not be enforced"


def test_repaired_record_shape_round_trips():
    """AC4's shipped shape: rows under the canonical key, NO stray alias, and the original prose
    preserved verbatim under an underscore-prefixed `_calibration_narrative` — structurally
    invisible to `_required_keys` (which skips `_`-prefixed keys) and to this family, so the
    paragraph survives with zero schema growth."""
    narrative = "First Critic: 8/9 findings VALIDATED, 1 FALSE-ALARM on the tombstone."
    repaired = _reflection(critic_calibration=_ROWS)
    repaired["_calibration_narrative"] = narrative
    assert "calibration" not in repaired, "the stray alias key must be DELETED, not blanked"
    assert _violations(repaired) == []

    round_tripped = json.loads(json.dumps(repaired))
    assert round_tripped["_calibration_narrative"] == narrative
    assert [set(r) >= {"finding", "verdict"} for r in round_tripped["critic_calibration"]] == [True]


def test_co_constraint_gate_degrades_the_new_rule(tmp_path: Path):
    """must-not-defer #4: under the legacy-tolerant residue gate ONLY the presence-symmetric
    co-constraint hard-fails. The new family must degrade to a non-fatal warning by
    construction (it is reached via lint_artifact), never strand a legitimate write."""
    drifted = _reflection(calibration=_ROWS, critic_calibration="prose")
    path = tmp_path / "reflection.json"
    path.write_text(json.dumps(drifted, indent=1), encoding="utf-8")
    assert artifact_lint.main([str(path)]) == 1                       # normal mode: hard fail
    assert artifact_lint.main([str(path), "--co-constraint-gate"]) == 0  # gate mode: warn only


def test_row_array_registry_has_no_dead_rows():
    """The registry's own dead-row guard, and its wiring into --self-check."""
    assert artifact_lint.row_array_keys_resolve() == []


def test_self_check_runs_the_row_array_dead_row_guard(monkeypatch):
    """The DEAD-FIELD half. Note the field name is deliberately absent from the example AND the
    spec is `{}`, so this alone cannot distinguish the two halves — hence the separate
    entry-contract tests below (code-review CR2: this test was masking the spec gap)."""
    monkeypatch.setitem(artifact_lint.ROW_ARRAY_KEYS, ("reflection", "no_such_field"), {})
    assert artifact_lint.main(["--self-check"]) == 1


# ── code-review CR2: the registry ENTRY CONTRACT ────────────────────────────────────
#
# `_row_array_violations` reads `consumer` on every violation path. Indexing it directly made a
# malformed registry entry raise KeyError INSIDE /build-slice's pre-finish gate — a hard stop,
# and a direct contradiction of the helper's own never-raises docstring (must-not-defer #3).
# The fix is two-sided: the helper degrades, and --self-check refuses the malformed entry.

@pytest.mark.parametrize("bad_spec", [
    {},                                                    # `consumer` missing entirely
    {"consumer": ""},                                      # present but empty
    {"consumer": "   "},                                   # whitespace-only
    {"consumer": None},
    {"consumer": "ok", "aliases": "calibration"},           # a bare str, not a sequence of str
    {"consumer": "ok", "row_required": [None]},
])
def test_malformed_registry_spec_is_caught_by_self_check(monkeypatch, bad_spec):
    """A malformed entry on a REAL field: the dead-field branch cannot fire, so only the
    entry-contract half can catch it."""
    monkeypatch.setitem(artifact_lint.ROW_ARRAY_KEYS,
                        ("reflection", "critic_calibration"), bad_spec)
    assert artifact_lint.row_array_keys_resolve() != []
    assert artifact_lint.main(["--self-check"]) == 1


@pytest.mark.parametrize("bad_spec", [
    {}, {"consumer": ""}, {"consumer": None}, {"aliases": ("calibration",)}, "not-a-dict",
])
def test_malformed_registry_spec_never_raises_in_the_gate(monkeypatch, bad_spec):
    """Whatever --self-check says about it, the LINT path must still return strings, never raise:
    it runs inside the pre-finish gate where a traceback is a hard stop, not a finding."""
    monkeypatch.setitem(artifact_lint.ROW_ARRAY_KEYS,
                        ("reflection", "critic_calibration"), bad_spec)
    for record in (_reflection(), _reflection(critic_calibration="prose"),
                   _reflection(calibration=_ROWS, critic_calibration="prose")):
        out = _violations(record)
        assert isinstance(out, list) and all(isinstance(s, str) for s in out)


def test_violation_text_degrades_without_a_consumer(monkeypatch):
    """The degrade is a readable sentence, not a `None` spliced into the message."""
    monkeypatch.setitem(artifact_lint.ROW_ARRAY_KEYS,
                        ("reflection", "critic_calibration"), {"aliases": ("calibration",)})
    out = _violations(_reflection(calibration=_ROWS, critic_calibration="prose"))
    assert out and all("None" not in v for v in out), out
    assert any(artifact_lint._CONSUMER_UNKNOWN in v for v in out), out
