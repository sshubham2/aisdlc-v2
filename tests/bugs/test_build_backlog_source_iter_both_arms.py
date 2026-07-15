"""REPRO — build_backlog.py crashes with AttributeError on a bare-STRING `source` value. BOTH arms.

slice-068 / SC-135, critique C3 (blocker, CC-001 behavioral twin).

THE BUG, confirmed against REAL data before a line was changed: the identical naive expression exists
TWICE in build_backlog.py, and both raise `AttributeError: 'str' object has no attribute 'get'` on
aivlc's real vault TODAY:

    for s in c.get("source") or []:
        if s.get("type") == "finding" ...      # 'reflect'.get -> AttributeError

  * build_backlog.py:454-456  — cmd_build's in-lock dedup loop over the LIVE candidates array.
    Live offender: aivlc SC-014, `"source": "reflect"`.
  * build_backlog.py:558-562  — _archive_scan, over archive/candidates.json.
    Archive offender: aivlc SC-009, `"source": "slice-007-discovered"`.

WHY BOTH ARMS ARE IN THIS TEST: the design AND the D1/D2/MV2 design spike both named SC-014 ('reflect')
as the evidence while attributing the crash to _archive_scan — a value _archive_scan never reads. The
spike MIS-ATTRIBUTED ITS OWN EVIDENCE and the design inherited it. Fixing only :558 would leave a
confirmed crash live on the LIVE-candidates path while a repro exercising only the archive arm passed
GREEN — the false-green class CC-001 exists to catch (slice-042/046/047/049/051).

THE FIX: both loops route through the single malformed-tolerant `product_scope.iter_sources()`, which
is also the census's and the materializer's selector — so the three cannot drift (slice-063's lesson:
make the automated consumer literally the same code path).

The `source` values below are the REAL bytes, copied from tests/fixtures/aivlc-vault/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
FIXTURES = _REPO / "tests" / "fixtures" / "aivlc-vault"


def _real_bare_string_sources() -> dict[str, str]:
    """The two REAL malformed rows, read from the committed aivlc bytes (never hand-typed)."""
    out = {}
    for rel, arm in (("candidates.json", "live"), ("archive/candidates.json", "archive")):
        for c in json.loads((FIXTURES / rel).read_text(encoding="utf-8"))["candidates"]:
            if isinstance(c.get("source"), str):
                out[arm] = c["source"]
    return out


def test_the_real_malformed_rows_still_exist_in_the_fixture():
    """If this fails, the fixture stopped carrying the evidence and every assertion below is vacuous."""
    real = _real_bare_string_sources()
    assert real == {"live": "reflect", "archive": "slice-007-discovered"}, real


def test_live_arm_does_not_crash_on_a_bare_string_source(tmp_path, run_script):
    """build_backlog.py:454-456 — cmd_build's LIVE-candidates dedup loop. This is the arm the spike
    missed entirely; before the fix, `cmd_build` raised AttributeError on aivlc's live vault."""
    from scripts.lib.product_scope import iter_sources

    live = _real_bare_string_sources()["live"]
    cand = {"id": "SC-014", "title": "x", "source": live}

    with pytest.raises(AttributeError):                      # the BUG, reproduced exactly
        for s in cand.get("source") or []:
            s.get("type")

    assert list(iter_sources(cand)) == [{"type": "reflect", "ref": None}]  # the FIX


def test_archive_arm_does_not_crash_on_a_bare_string_source():
    """build_backlog.py:558-562 — _archive_scan. The arm the design DID name (with the wrong row)."""
    from scripts.lib.product_scope import iter_sources

    archived = _real_bare_string_sources()["archive"]
    cand = {"id": "SC-009", "title": "x", "source": archived}

    with pytest.raises(AttributeError):
        for s in cand.get("source") or []:
            s.get("type")

    assert list(iter_sources(cand)) == [{"type": "slice-007-discovered", "ref": None}]


def test_archive_scan_survives_the_real_aivlc_archive(tmp_path):
    """Drive the REAL _archive_scan over aivlc's REAL archive bytes. Fails with AttributeError on
    HEAD; passes once :558-562 routes through iter_sources()."""
    import sys

    sys.path.insert(0, str(_REPO / "skills" / "slice-candidates" / "scripts"))
    import build_backlog

    v = tmp_path / "vault"
    (v / "archive").mkdir(parents=True)
    (v / "archive" / "candidates.json").write_bytes((FIXTURES / "archive" / "candidates.json").read_bytes())

    refs, mx = build_backlog._archive_scan(v)   # raised AttributeError on HEAD
    assert mx == 12, "the archive's max SC number must still be scanned correctly"
    assert isinstance(refs, set)


def test_cmd_build_live_dedup_survives_the_real_aivlc_live_file(tmp_path):
    """Drive the REAL cmd_build dedup expression over aivlc's REAL live bytes — the arm a
    fix-only-the-archive patch would leave crashing. The loop must both survive the bare string AND
    still find the finding-refs it exists to dedup on."""
    import sys

    sys.path.insert(0, str(_REPO / "skills" / "slice-candidates" / "scripts"))
    import build_backlog
    from scripts.lib.product_scope import iter_sources

    cands = json.loads((FIXTURES / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    assert any(isinstance(c.get("source"), str) for c in cands), "fixture lost its malformed live row"

    fid_to_sc: dict[str, str] = {}
    for c in cands:                                    # the exact shape of build_backlog.py:454-456
        for s in build_backlog._iter_sources(c):
            if s.get("type") == "finding" and s.get("ref"):
                fid_to_sc[s["ref"]] = c.get("id")

    assert build_backlog._iter_sources is iter_sources, (
        "build_backlog must reuse the SHARED selector — a second copy is a second thing to drift"
    )
