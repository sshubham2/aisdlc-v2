"""scripts/lib/assemble.py — CSS/JS extracted to sibling assets (3.19.6).

Guards the extraction: assemble.py loads its <style>/<script> payloads from the
sibling assemble.css / assemble.js at import. A missing / renamed / empty asset
would break /diagnose + /bug-hunt HTML with no other test catching it (nothing
else imports assemble), so these tests pin the contract.
"""
from __future__ import annotations

from pathlib import Path

from scripts.lib import assemble

_HERE = Path(assemble.__file__).resolve().parent


def test_assets_exist_next_to_module():
    assert (_HERE / "assemble.css").is_file()
    assert (_HERE / "assemble.js").is_file()


def test_css_loaded_nonempty_with_sentinels():
    css = assemble.CSS
    assert isinstance(css, str) and len(css) > 5000
    assert "@import" in css and "@media" in css
    assert '"""' not in css  # no triple-quote leak from the extraction


def test_js_loaded_nonempty_with_sentinels():
    js = assemble.JS_TEMPLATE
    assert isinstance(js, str) and len(js) > 2000
    assert "addEventListener" in js
    # the asset holds the COMPUTED string value, so JS escapes are single-backslash
    # (e.g. the download blob's '<!DOCTYPE html>\n') — not Python's doubled source form.
    assert "\\n" in js
    assert '"""' not in js


def test_read_asset_matches_loaded_module_constant():
    # what the module inlined at render == what the sibling file holds (universal
    # newlines, so a CRLF checkout still equals the LF module value).
    assert assemble.CSS == (_HERE / "assemble.css").read_text(encoding="utf-8")
    assert assemble.JS_TEMPLATE == (_HERE / "assemble.js").read_text(encoding="utf-8")
