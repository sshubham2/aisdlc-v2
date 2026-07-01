"""Durable regression for the vault_edit append duplicate-safe guard (slice-050,
SC-041, ADR-040 + ADR-043). The tests/bugs/ file pins the SC-041 repro; this file
is the lasting home for the guard's full contract: the check is BOUNDED to the
last-K elements (not whole-array), applies ONLY to --stdin, and --allow-duplicate
bypasses it."""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.vault_edit import _DEDUP_WINDOW  # the bounded-window K (ADR-043)

VAULT_EDIT = REPO_ROOT / "scripts" / "lib" / "vault_edit.py"


def _append(vault, element, *, via="stdin", allow_duplicate=False, tmp_dir=None):
    argv = [sys.executable, str(VAULT_EDIT), "--vault", str(vault),
            "append", "--file", "t.json", "--array", "items"]
    if allow_duplicate:
        argv += ["--allow-duplicate"]
    payload = json.dumps(element)
    stdin_text = None
    if via == "stdin":
        argv += ["--stdin"]; stdin_text = payload
    elif via == "json":
        argv += ["--json", payload]
    elif via == "content-file":
        cf = (tmp_dir or vault) / "e.json"; cf.write_text(payload, encoding="utf-8")
        argv += ["--content-file", str(cf)]
    return subprocess.run(argv, input=stdin_text, capture_output=True, text=True)


def _arr(vault):
    return json.loads((vault / "t.json").read_text(encoding="utf-8")).get("items", [])


def test_bounded_window_and_non_stdin_exempt(tmp_path):
    """Within the window an identical --stdin re-submission is suppressed; the same
    element via --json (non-stdin) is NOT deduped (M-add-2)."""
    x = {"note": "windowed"}
    assert _append(tmp_path, x).returncode == 0
    assert _append(tmp_path, x).returncode == 0
    assert len(_arr(tmp_path)) == 1, "identical --stdin within window must suppress"
    # non-stdin path is exempt
    assert _append(tmp_path, x, via="json").returncode == 0
    assert len(_arr(tmp_path)) == 2, "--json must NOT be deduped (non-stdin exempt)"


def test_beyond_window_not_deduped(tmp_path):
    """The guard is BOUNDED, not whole-array: an identical element pushed beyond
    the last-K window by K distinct appends is NOT suppressed (a whole-array scan
    would wrongly suppress it -- the B1 over-dedup the design was corrected to avoid)."""
    x = {"note": "old", "id": 0}
    assert _append(tmp_path, x).returncode == 0
    for i in range(1, _DEDUP_WINDOW + 1):
        assert _append(tmp_path, {"note": "old", "id": i}).returncode == 0
    # x is now older than the last-K elements
    r = _append(tmp_path, x)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", "an out-of-window append must be a NORMAL append (no suppression token)"
    arr = _arr(tmp_path)
    assert len(arr) == _DEDUP_WINDOW + 2, (
        f"an identical element beyond the last-{_DEDUP_WINDOW} window must append, got {len(arr)}")


def test_allow_duplicate_bypasses(tmp_path):
    """--allow-duplicate forces a genuine immediate duplicate through the guard."""
    x = {"note": "forced"}
    assert _append(tmp_path, x).returncode == 0
    assert _append(tmp_path, x, allow_duplicate=True).returncode == 0
    assert len(_arr(tmp_path)) == 2, "--allow-duplicate must bypass the dedup guard"
