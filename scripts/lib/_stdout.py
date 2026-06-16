"""UTF8-STDOUT-1 helper. Idempotent. errors="replace" not "strict".

Per UTF8-STDOUT-1 (methodology-changelog.md v0.37.0). Reconfigures
sys.stdout and sys.stderr to UTF-8 with errors="replace" so audit tools
can emit non-ASCII content (U+2192 arrows, U+2014 em-dashes,
box-drawing, CJK, etc.) without UnicodeEncodeError on Windows cp1252
console.

Idempotent: stdlib TextIOWrapper.reconfigure is safe to call with the
same kwargs repeatedly. Per M6 + M-add-3 ACCEPTED-FIXED at slice-023,
we deliberately do NOT short-circuit on encoding == "utf-8" because a
prior call may have left errors="strict"; unconditionally reconfiguring
with both encoding="utf-8" AND errors="replace" guarantees the post-call
state matches the slice's required contract regardless of prior state.

No-op when streams lack `reconfigure` (test capture, StringIO).

Usage (canonical pinned form per M4 ACCEPTED-FIXED at slice-023):

    from tools import _stdout

    def main(argv: list[str] | None = None) -> int:
        _stdout.reconfigure_stdout_utf8()
        # ... rest of main() body ...
"""

import sys


def reconfigure_stdout_utf8() -> None:
    """Reconfigure sys.stdout and sys.stderr to UTF-8 with errors='replace'."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        reconfigure(encoding="utf-8", errors="replace")


def reconfigure_stdin_utf8() -> None:
    """Reconfigure sys.stdin to UTF-8 with errors='replace' (the stdin twin, SC-015).

    A subprocess that reads a piped record on a Windows cp1252 stdin would
    otherwise mis-decode non-ASCII (an em-dash) and re-encode it as mojibake.
    Call this BEFORE the first stdin read.

    No-op when stdin lacks `reconfigure` (test capture, StringIO). Unlike the
    write-stream twin above, `reconfigure(encoding=...)` on a READ stream raises
    `io.UnsupportedOperation` ("It is not possible to set the encoding ... after
    the first read") if a read has already occurred — `io.UnsupportedOperation`
    subclasses both OSError and ValueError, so the call is guarded and degrades
    to the host default rather than crashing (M1, slice-012). The write-stream
    twin needs no such guard because write streams are exempt from the restriction.
    """
    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        # io.UnsupportedOperation (stdin already read) or otherwise un-reconfigurable
        # -> fall back to the host default; the caller still attempts the read.
        pass
