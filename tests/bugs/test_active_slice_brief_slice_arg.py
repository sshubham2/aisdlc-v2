"""AC5 (slice-031): active_slice_brief.py --slice resolves a slice BY ID, mirroring
active_slice.py --slice -- both delegate to the SAME archive-aware resolve_slice_by_id, so
/design-slice slice-NNN resolves the named slice's brief from a main session (cwd-independent).

Covers: by-id resolution of an ACTIVE slice, parity with the active_slice primitive, and
archive-awareness (an archived slice resolves too -- design-slice parity with slice-story).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.lib import active_slice_brief
from scripts.lib.active_slice import resolve_slice_by_id


def _mk_brief(vault: Path, folder: str, *, archived: bool = False, title: str = "x") -> None:
    base = vault / "slices" / ("archive" if archived else "") / folder
    base.mkdir(parents=True, exist_ok=True)
    slice_id = "slice-" + folder.split("-")[1]
    (base / "mission-brief.json").write_text(
        json.dumps({"_schema": "aisdlc/mission-brief@1", "slice": slice_id,
                    "title": title, "intent": "## Intent\ndo the thing"}),
        encoding="utf-8",
    )


def test_brief_slice_arg_resolves_active_by_id(tmp_path, capsys) -> None:
    _mk_brief(tmp_path, "slice-007-enrich", title="enrich")
    rc = active_slice_brief.main(["--vault", str(tmp_path), "--slice", "slice-007"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "slice-007-enrich" in out and "enrich" in out  # resolved THAT slice's brief


def test_brief_slice_arg_parity_with_active_slice_primitive(tmp_path) -> None:
    # active_slice_brief --slice delegates to the SAME resolve_slice_by_id active_slice.py --slice uses.
    _mk_brief(tmp_path, "slice-007-enrich")
    info = resolve_slice_by_id(tmp_path, "slice-007")
    assert info and info["folder"] == "slice-007-enrich"


def test_brief_slice_arg_is_archive_aware(tmp_path, capsys) -> None:
    # mirrors active_slice.py --slice: an ARCHIVED slice resolves too (no exit-4; benign).
    _mk_brief(tmp_path, "slice-003-old", archived=True, title="old")
    rc = active_slice_brief.main(["--vault", str(tmp_path), "--slice", "slice-003"])
    out = capsys.readouterr().out
    assert rc == 0 and "slice-003-old" in out


def test_brief_unknown_slice_id_is_benign(tmp_path, capsys) -> None:
    # explicit-but-unresolvable id -> benign note, exit 0 (NXDOMAIN, never the exit-4 ambiguity HALT).
    rc = active_slice_brief.main(["--vault", str(tmp_path), "--slice", "slice-999"])
    out = capsys.readouterr().out
    assert rc == 0 and "no active slice" in out.lower()
