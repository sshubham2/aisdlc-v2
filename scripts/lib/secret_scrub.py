"""secret_scrub.py — shared credential patterns + redaction (4.7 VAL-1 vault secret-sweep).

VAL-1's Layer-A credential scan flags secrets in the CODE DIFF, but captured command output
(`/validate-slice`, `/risk-spike`, `/field-recon` evidence) is persisted to the vault under
`~/.aisdlc` as PLAINTEXT and never scanned — a standing exfiltration surface. This module is the
SINGLE SOURCE OF TRUTH for the secret patterns (`validate_slice_layers` imports `SECRET_PATTERNS`
from here) PLUS a redactor the evidence writers pipe captured output through before persisting it.

CLI:
  $PY secret_scrub.py < captured.txt           -> redacted text to stdout (default)
  $PY secret_scrub.py --in F --out G           -> redact F into G
  $PY secret_scrub.py --check < captured.txt   -> no output; exit 1 if ANY secret found (gate)

`redact()` replaces only the matched secret with `[REDACTED:<type>]`, preserving surrounding
context — so `api_key: "AKIA…"` becomes `api_key: "[REDACTED:generic-api-key]"`, still readable.

Exit: 0 clean (or redacted) · 1 (--check only) a secret was found · 2 usage / read error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# --- plugin-root import bootstrap (shared lib invoked by ABSOLUTE PATH from SKILL.md) ---
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.lib import _stdout

# The VAL-1 Layer-A credential patterns (the secret is capture group 1). This dict is the single
# source of truth; skills/validate-slice/scripts/validate_slice_layers.py imports it from here.
SECRET_PATTERNS: dict[str, "re.Pattern[str]"] = {
    "aws-access-key": re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    "github-token-classic": re.compile(r"\b(ghp_[A-Za-z0-9]{36,255})\b"),
    "github-token-fine": re.compile(r"\b(github_pat_[A-Za-z0-9_]{60,255})\b"),
    "github-token-other": re.compile(r"\b(gh[orsu]_[A-Za-z0-9]{36,255})\b"),
    "slack-token": re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
    "private-key": re.compile(
        r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----)"
    ),
    "anthropic-key": re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{40,})\b"),
    "openai-key": re.compile(
        r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{20,}T3BlbkFJ[A-Za-z0-9_-]{20,})\b"
    ),
    "jwt": re.compile(
        r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
    ),
    "generic-api-key": re.compile(
        r"(?i)(?:api[_-]?key|apikey|api[_-]?token|access[_-]?token|"
        r"secret[_-]?key|password)\s*[:=]\s*"
        r"['\"]([A-Za-z0-9_+/=\-]{20,})['\"]"
    ),
}


def scan(text: str) -> list[tuple[str, str]]:
    """Return [(type, matched_secret), ...] for every secret found in ``text``."""
    out: list[tuple[str, str]] = []
    for name, pat in SECRET_PATTERNS.items():
        for m in pat.finditer(text or ""):
            out.append((name, m.group(1)))
    return out


def redact(text: str) -> tuple[str, list[str]]:
    """Replace each matched secret with ``[REDACTED:<type>]`` (preserving surrounding context).
    Returns ``(redacted_text, sorted_unique_types_found)``."""
    found: set[str] = set()
    result = text or ""
    for name, pat in SECRET_PATTERNS.items():
        def _sub(m: "re.Match[str]", _name: str = name) -> str:
            found.add(_name)
            return m.group(0).replace(m.group(1), f"[REDACTED:{_name}]", 1)
        result = pat.sub(_sub, result)
    return result, sorted(found)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    ap = argparse.ArgumentParser(
        prog="secret_scrub",
        description="Redact credentials from captured evidence before it is written to the vault (4.7).")
    ap.add_argument("--in", dest="in_path", type=Path, default=None, help="input file (default: stdin)")
    ap.add_argument("--out", dest="out_path", type=Path, default=None, help="output file (default: stdout)")
    ap.add_argument("--check", action="store_true",
                    help="don't redact; exit 1 if ANY secret is found (gate mode)")
    args = ap.parse_args(argv)

    try:
        text = args.in_path.read_text(encoding="utf-8") if args.in_path else sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"secret_scrub: cannot read input: {exc}\n")
        return 2

    if args.check:
        hits = scan(text)
        if hits:
            types = sorted({t for t, _ in hits})
            sys.stderr.write(f"secret_scrub: {len(hits)} secret(s) found: {', '.join(types)}\n")
            return 1
        return 0

    redacted, found = redact(text)
    if args.out_path:
        try:
            args.out_path.write_text(redacted, encoding="utf-8", newline="")
        except OSError as exc:
            sys.stderr.write(f"secret_scrub: cannot write --out: {exc}\n")
            return 2
    else:
        sys.stdout.write(redacted)
    if found:
        sys.stderr.write(f"secret_scrub: redacted {len(found)} secret type(s): {', '.join(found)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
