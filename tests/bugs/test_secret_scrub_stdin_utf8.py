"""
Bug: secret_scrub.py mis-decodes non-ASCII read from stdin (SC-026).

scripts/lib/secret_scrub.py reconfigures only STDOUT
(`_stdout.reconfigure_stdout_utf8()` at main(), UTF8-STDOUT-1) but reads the
captured evidence from `sys.stdin.read()` (line 93) WITHOUT reconfiguring stdin
to utf-8. secret_scrub scrubs captured spike / validate / field-recon evidence,
which can carry non-ASCII (em-dashes, arrows, CJK). On a cp1252 stdin (the
Windows default) the utf-8 evidence bytes are decoded as cp1252 BEFORE scrubbing,
so the redacted output stores mojibake instead of the faithful character.

Expected: an em-dash (U+2014, "—") in the piped evidence round-trips faithfully
          into the scrubbed stdout (while embedded secrets are still redacted).
Actual:   the em-dash's utf-8 bytes (e2 80 94) are decoded as cp1252 into the
          mojibake "â€" + U+201D (a-circumflex / euro / right-double-quote), so
          the scrubbed output carries the mangled characters, not the em-dash.

This is the STDIN twin of the already-fixed UTF8-STDOUT-1 — the SAME gap
slice-012 (SC-015) closed in write_changelog.py, here unfixed in secret_scrub.

The cp1252 stdin is simulated DETERMINISTICALLY (a TextIOWrapper over utf-8 bytes
with encoding="cp1252") rather than relying on the host's default stdin encoding,
so the primary test fails on every platform with the unfixed code — including a
utf-8 Linux CI runner — and passes once stdin is reconfigured to utf-8 before the
read. A real TextIOWrapper (not a StringIO) is used so the fix's `.reconfigure(...)`
has a stream to act on (the reconfigure helper no-ops on streams lacking reconfigure).
The embedded AWS key is pure ASCII, so it is redacted regardless of the stdin bug —
isolating the failure to the non-ASCII handling.

FAILS until secret_scrub.main() calls _stdout.reconfigure_stdin_utf8() before the
stdin read; then passes.
"""
import io
import sys

from scripts.lib import _stdout, secret_scrub

EM_DASH = "—"            # — (U+2014)
MOJIBAKE_MARK = "â"  # â — the cp1252 mis-decode of the em-dash's LEAD byte 0xe2; DETERMINISTIC
                          # (appears iff the em-dash was mis-decoded, never in clean utf-8 output). m1:
                          # anchor on the lead byte, not the codec-position-dependent middle bytes (0x80/0x94).
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # the canonical test AWS access key (matches test_secret_scrub.py)


def test_secret_scrub_stdin_nonascii_not_mojibake(monkeypatch):
    """PRIMARY repro: non-ASCII evidence piped over a cp1252 stdin must survive
    the scrub faithfully. The AWS key (pure ASCII) is still redacted either way;
    the em-dash is the canary for the unreconfigured-stdin decode bug."""
    payload = f"spike evidence {EM_DASH} aws_key={AWS_KEY} captured".encode("utf-8")
    fake_stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    out_buf = io.StringIO()                      # StringIO -> reconfigure_stdout no-ops, captures text
    monkeypatch.setattr(sys, "stdout", out_buf)

    rc = secret_scrub.main([])                   # default mode: read stdin, redact, write stdout
    assert rc == 0, f"secret_scrub.main() exited {rc}"

    out = out_buf.getvalue()
    # ASCII secret is redacted regardless of the stdin-decode bug
    assert AWS_KEY not in out, f"AWS key leaked unredacted: {out!r}"
    assert "[REDACTED:aws-access-key]" in out, f"AWS key not redacted: {out!r}"
    # the bug: a cp1252-decoded em-dash becomes mojibake; the fix keeps it faithful
    assert EM_DASH in out, f"faithful em-dash missing (mis-decoded before scrub): {out!r}"
    assert MOJIBAKE_MARK not in out, f"em-dash mojibake leaked into scrubbed output: {out!r}"


def test_secret_scrub_in_file_em_dash_faithful(tmp_path, monkeypatch):
    """Regression guard: the --in file path (already read as utf-8) keeps
    round-tripping an em-dash faithfully and must be unaffected by the stdin fix."""
    src = tmp_path / "evidence.txt"
    src.write_text(f"note {EM_DASH} key={AWS_KEY}", encoding="utf-8")
    out_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)

    rc = secret_scrub.main(["--in", str(src)])
    assert rc == 0, f"secret_scrub.main() exited {rc}"

    out = out_buf.getvalue()
    assert AWS_KEY not in out and "[REDACTED:aws-access-key]" in out, f"--in path: secret not redacted: {out!r}"
    assert EM_DASH in out and MOJIBAKE_MARK not in out, f"--in path mangled the em-dash: {out!r}"


def test_reconfigure_stdin_utf8_noop_on_non_reconfigurable(monkeypatch):
    """The helper is a clean no-op when stdin lacks reconfigure (a StringIO /
    test-capture stream) — it never raises and leaves stdin usable."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    _stdout.reconfigure_stdin_utf8()             # StringIO has no reconfigure -> guarded no-op
    assert sys.stdin.read() == "hello"


def test_reconfigure_stdin_utf8_idempotent(monkeypatch):
    """Calling the helper repeatedly is safe (idempotent) and leaves stdin
    decoding as utf-8 — the fix may invoke it once but must not be fragile twice."""
    payload = f"x {EM_DASH} y".encode("utf-8")
    fake_stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    _stdout.reconfigure_stdin_utf8()
    _stdout.reconfigure_stdin_utf8()             # second call must not raise
    assert sys.stdin.encoding.lower().replace("-", "") == "utf8"
    assert sys.stdin.read() == f"x {EM_DASH} y"
