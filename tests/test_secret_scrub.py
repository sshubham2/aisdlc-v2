"""scripts/lib/secret_scrub.py — the VAL-1 vault evidence redactor (4.7)."""
from __future__ import annotations

from scripts.lib.secret_scrub import redact, scan

_AWS = "AKIAIOSFODNN7EXAMPLE"
_GH = "ghp_" + "a" * 36


def test_redact_aws_key():
    out, found = redact(f"export AWS_ACCESS_KEY_ID={_AWS}")
    assert _AWS not in out
    assert "[REDACTED:aws-access-key]" in out
    assert "aws-access-key" in found


def test_redact_preserves_context():
    out, _ = redact('api_key: "abcdefghij1234567890XYZ"')
    assert out.startswith("api_key:")            # surrounding context kept
    assert "[REDACTED:generic-api-key]" in out


def test_redact_multiple_types():
    text = f"k={_AWS}\ntoken={_GH}\n"
    out, found = redact(text)
    assert _AWS not in out and _GH not in out
    assert set(found) >= {"aws-access-key", "github-token-classic"}


def test_clean_text_unchanged():
    text = "just a normal log line: 200 OK, 12 rows, /health\n"
    out, found = redact(text)
    assert out == text
    assert found == []


def test_scan_reports_hits():
    hits = scan(f"a={_AWS}")
    assert ("aws-access-key", _AWS) in hits


def test_cli_check_exit1_on_secret(run_script):
    r = run_script("scripts/lib/secret_scrub.py", ["--check"], stdin=_AWS)
    assert r.returncode == 1


def test_cli_check_exit0_on_clean(run_script):
    r = run_script("scripts/lib/secret_scrub.py", ["--check"], stdin="nothing secret here\n")
    assert r.returncode == 0


def test_cli_default_redacts_stdout(run_script):
    r = run_script("scripts/lib/secret_scrub.py", [], stdin=f"key={_AWS}\n")
    assert r.returncode == 0
    assert _AWS not in r.stdout
    assert "[REDACTED:aws-access-key]" in r.stdout
