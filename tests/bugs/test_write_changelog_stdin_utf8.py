"""
Bug: write_changelog.py double-encodes non-ASCII read from stdin (SC-015).

skills/commit-slice/scripts/write_changelog.py reconfigures only STDOUT
(`_stdout.reconfigure_stdout_utf8()` at main(), UTF8-STDOUT-1) but `_load_record`
reads the record JSON from `sys.stdin.read()` WITHOUT reconfiguring stdin to
utf-8. On a cp1252 stdin (the Windows default), utf-8 record bytes are decoded as
cp1252, then re-encoded utf-8 when the changelog.json is written -> DOUBLE-ENCODED.

Expected: an em-dash (U+2014, "—") in the piped record's `intent` round-trips
          faithfully into the written changelog.json.
Actual:   the em-dash's utf-8 bytes (e2 80 94) are decoded as cp1252 into the
          mojibake "â€”" (a-circumflex / euro / right-double-quote),
          then stored utf-8-encoded as bytes c3 a2 e2 82 ac e2 80 9d.
          Proven LIVE at slice-007 (its intent em-dash landed mangled in CHANGELOG.md).

This is the STDIN twin of the already-fixed UTF8-STDOUT-1.

The cp1252 stdin is simulated DETERMINISTICALLY (a TextIOWrapper over utf-8 bytes
with encoding="cp1252") rather than relying on the host's default stdin encoding,
so the test fails on every platform with the unfixed code -- including a utf-8
Linux CI runner -- and passes once stdin is reconfigured to utf-8 before the read.
A real TextIOWrapper (not a StringIO) is used so the fix's `.reconfigure(...)` has
a stream to act on (the reconfigure helper no-ops on streams lacking reconfigure).

FAILS until write_changelog reconfigures stdin to utf-8 before reading; then passes.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITE_CHANGELOG = REPO_ROOT / "skills" / "commit-slice" / "scripts" / "write_changelog.py"

EM_DASH = "—"                      # — (U+2014)
MOJIBAKE = "â€”"         # â€"  — cp1252 decode of utf-8 em-dash bytes e2 80 94


def _load_write_changelog():
    """Import the single-skill script by path (it self-bootstraps the plugin root)."""
    spec = importlib.util.spec_from_file_location("write_changelog_under_test", WRITE_CHANGELOG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_write_changelog_stdin_em_dash_not_double_encoded(tmp_path, monkeypatch):
    mod = _load_write_changelog()

    # An (archived) slice folder so _resolve_slice_dir() finds a place to write.
    slice_id = "slice-999-stdin-utf8"
    slice_dir = tmp_path / "slices" / "archive" / slice_id
    slice_dir.mkdir(parents=True)

    record = {
        "type": "fix",
        "scope": "commit-slice",
        "intent": f"fix stdin {EM_DASH} double-encoding",
        "subject": "x",
    }
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8")

    # Simulate a Windows cp1252 stdin carrying utf-8 bytes: the text layer decodes
    # as cp1252 (the bug) UNLESS the code reconfigures stdin to utf-8 (the fix).
    fake_stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    rc = mod.main(["--vault", str(tmp_path), "--slice", slice_id, "--mode", "merge"])
    assert rc == 0, f"write_changelog.main() exited {rc}"

    written = json.loads((slice_dir / "changelog.json").read_text(encoding="utf-8"))
    intent = written["intent"]

    assert MOJIBAKE not in intent, f"em-dash double-encoded as mojibake: {intent!r}"
    assert EM_DASH in intent, f"faithful em-dash missing from stored intent: {intent!r}"


def test_write_changelog_record_file_em_dash_faithful(tmp_path):
    """AC2: the --record-file input path (read as utf-8) keeps round-tripping an
    em-dash faithfully and is unaffected by the new stdin reconfigure. Guards
    against a regression in the file path that the stdin fix must not touch."""
    mod = _load_write_changelog()

    slice_id = "slice-999-stdin-utf8"
    slice_dir = tmp_path / "slices" / "archive" / slice_id
    slice_dir.mkdir(parents=True)

    record = {"type": "fix", "scope": "commit-slice",
              "intent": f"fix file {EM_DASH} path", "subject": "x"}
    rec_file = tmp_path / "record.json"
    rec_file.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    rc = mod.main(["--vault", str(tmp_path), "--slice", slice_id, "--mode", "merge",
                   "--record-file", str(rec_file)])
    assert rc == 0, f"write_changelog.main() exited {rc}"

    intent = json.loads((slice_dir / "changelog.json").read_text(encoding="utf-8"))["intent"]
    assert MOJIBAKE not in intent and EM_DASH in intent, f"--record-file mangled the em-dash: {intent!r}"


def test_reconfigure_stdin_utf8_noop_on_non_reconfigurable(monkeypatch):
    """AC3: reconfigure_stdin_utf8() is a clean no-op when stdin lacks reconfigure
    (a StringIO / test-capture stream) -- it never raises and leaves stdin usable."""
    _stdout = _load_write_changelog()._stdout
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    _stdout.reconfigure_stdin_utf8()          # StringIO has no reconfigure -> guarded no-op
    assert sys.stdin.read() == "hello"


def test_reconfigure_stdin_utf8_idempotent(monkeypatch):
    """AC3: calling reconfigure_stdin_utf8() repeatedly is safe (idempotent) and
    leaves stdin decoding as utf-8."""
    _stdout = _load_write_changelog()._stdout
    payload = json.dumps({"x": EM_DASH}, ensure_ascii=False).encode("utf-8")
    fake_stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    _stdout.reconfigure_stdin_utf8()
    _stdout.reconfigure_stdin_utf8()          # second call must not raise
    assert sys.stdin.encoding.lower().replace("-", "") == "utf8"
    assert json.loads(sys.stdin.read())["x"] == EM_DASH


def test_reconfigure_stdin_utf8_noop_when_already_read(monkeypatch):
    """M1: reconfigure_stdin_utf8() must NOT raise io.UnsupportedOperation when
    sys.stdin was already partially read -- the guard degrades to the host default
    rather than crashing. (TextIOWrapper.reconfigure(encoding=) raises after the
    first read on a READ stream; the stdout twin is exempt, the stdin twin is not.)"""
    _stdout = _load_write_changelog()._stdout
    fake_stdin = io.TextIOWrapper(io.BytesIO(b"hello world"), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    fake_stdin.read(1)                        # consume 1 char -> reconfigure(encoding=) now raises
    _stdout.reconfigure_stdin_utf8()          # the guard must swallow io.UnsupportedOperation
    assert fake_stdin.read() == "ello world"  # stream still usable after the guarded no-op
