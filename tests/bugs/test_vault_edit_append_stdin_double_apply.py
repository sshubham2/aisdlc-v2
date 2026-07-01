"""
Bug: vault_edit append --stdin double-apply (SC-041 / slice-050)

Observed: a SINGLE `vault_edit append --file candidates.json --array candidates
--stdin <<'JSON' {...} JSON` of ONE object produced TWO identical records under
heavy concurrent vault contention. The same-shape append via --content-file and
via claim_candidate (no stdin) each produced exactly ONE record. The double
correlates specifically with the --stdin read path (an effective re-submission /
lock-timeout retry that re-runs the invocation).

The append code applies exactly once per invocation (safe_mutate_text
reads-mutates-writes once under the sidecar lock), so the defect is the ABSENCE
of a duplicate-application guard: a re-submission of the IDENTICAL element is not
recognized and silently creates a duplicate.

Fix (slice-050, ADR-040 + ADR-043): a BOUNDED (last-K), --stdin-scoped
duplicate guard. An identical element re-submitted on the --stdin path within the
recent window is suppressed as idempotent SUCCESS -- exit 0, a machine-readable
STDOUT signal + a stderr DUPLICATE_SUPPRESSED note, array count +0. Distinct
elements, non-stdin paths (--json/--content-file), and matches outside the window
are NOT suppressed. --allow-duplicate bypasses.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VAULT_EDIT = REPO_ROOT / "scripts" / "lib" / "vault_edit.py"


def _run_append(vault: Path, file_rel: str, array, element, *, via="stdin",
                allow_duplicate=False, tmp_dir=None):
    """Drive `vault_edit append` through the requested input path. `via` is one of
    'stdin' (the bug's path), 'content-file', or 'json'."""
    argv = [sys.executable, str(VAULT_EDIT), "--vault", str(vault),
            "append", "--file", file_rel]
    if array is not None:
        argv += ["--array", array]
    if allow_duplicate:
        argv += ["--allow-duplicate"]
    payload = json.dumps(element)
    stdin_text = None
    if via == "stdin":
        argv += ["--stdin"]
        stdin_text = payload
    elif via == "content-file":
        cf = (tmp_dir or vault) / "elem.json"
        cf.write_text(payload, encoding="utf-8")
        argv += ["--content-file", str(cf)]
    elif via == "json":
        argv += ["--json", payload]
    return subprocess.run(argv, input=stdin_text, capture_output=True, text=True)


def _read(vault: Path, file_rel: str):
    return json.loads((vault / file_rel).read_text(encoding="utf-8"))


def _arr(vault: Path, file_rel: str, array: str):
    return _read(vault, file_rel).get(array, [])


# ── AC1 — the headline repro ────────────────────────────────────────────────
def test_identical_stdin_append_is_idempotent(tmp_path):
    """A re-submitted IDENTICAL element via --stdin must NOT create a duplicate."""
    elem = {"note": "spike feasibility proven", "tag": "x"}
    p1 = _run_append(tmp_path, "t.json", "items", elem)
    assert p1.returncode == 0, f"first append failed: {p1.stderr}"
    p2 = _run_append(tmp_path, "t.json", "items", elem)
    assert p2.returncode == 0, f"second (identical) append failed: {p2.stderr}"
    arr = _arr(tmp_path, "t.json", "items")
    assert len(arr) == 1, f"duplicate-safe append must leave ONE record, got {len(arr)}: {arr}"


# ── AC2 — the suppression must be OBSERVABLE (never silent) ──────────────────
def test_suppression_surfaced_on_stdout(tmp_path):
    """A suppressed duplicate must be distinguishable from a normal append using
    STDOUT + exit code alone (M-add-1): exit stays 0 (never re-triggers a retry),
    a machine-readable token appears on stdout, and a stderr note is present."""
    elem = {"note": "same", "n": 1}
    p1 = _run_append(tmp_path, "t.json", "items", elem)
    assert p1.returncode == 0
    assert p1.stdout.strip() == "", f"a NORMAL append must be silent on stdout, got: {p1.stdout!r}"

    p2 = _run_append(tmp_path, "t.json", "items", elem)
    assert p2.returncode == 0, "suppression MUST return exit 0 (a non-zero re-triggers the retry)"
    # machine-readable stdout signal the caller can branch on
    assert p2.stdout.strip(), "a SUPPRESSED append must emit a machine-readable stdout signal"
    token = json.loads(p2.stdout)
    assert token.get("suppressed") is True, f"stdout token must say suppressed: {p2.stdout!r}"
    assert token.get("count") == 1, f"stdout token must report the +0 post-op count: {token}"
    # human-facing stderr marker (greppable, stable)
    assert "DUPLICATE_SUPPRESSED" in p2.stderr, f"stderr must carry the marker: {p2.stderr!r}"
    assert len(_arr(tmp_path, "t.json", "items")) == 1


# ── AC3 — no over-dedup on genuinely-distinct elements ──────────────────────
def test_distinct_stdin_appends_are_not_deduped(tmp_path):
    p1 = _run_append(tmp_path, "t.json", "items", {"note": "alpha"})
    assert p1.returncode == 0, p1.stderr
    p2 = _run_append(tmp_path, "t.json", "items", {"note": "beta"})
    assert p2.returncode == 0, p2.stderr
    arr = _arr(tmp_path, "t.json", "items")
    assert len(arr) == 2, f"distinct elements must NOT be deduped, got {len(arr)}: {arr}"


# ── M3 — the MANAGED path (candidates.json) where SC-041 was observed ────────
def test_managed_candidates_stdin_dedup_counter_once(tmp_path):
    """Two id-omitted identical candidate appends via --stdin must leave ONE
    record and bump the sc counter exactly ONCE (the id-stripped compare must
    recognize the retry despite the minted id differing)."""
    (tmp_path / "candidates.json").write_text(
        json.dumps({"candidates": [], "counters": {"sc": 0}}), encoding="utf-8")
    cand = {"title": "add-test-for-y", "description": "same follow-up"}
    p1 = _run_append(tmp_path, "candidates.json", "candidates", cand)
    assert p1.returncode == 0, f"first managed append failed: {p1.stderr}"
    p2 = _run_append(tmp_path, "candidates.json", "candidates", cand)
    assert p2.returncode == 0, f"second managed append failed: {p2.stderr}"
    data = _read(tmp_path, "candidates.json")
    assert len(data["candidates"]) == 1, f"managed dedup must leave ONE record: {data['candidates']}"
    assert data["counters"]["sc"] == 1, f"sc counter must bump ONCE, got {data['counters']['sc']}"


# ── M-add-2 — non-stdin paths are PROVEN correct; must NOT be deduped ────────
def test_content_file_path_not_deduped(tmp_path):
    """The bug is --stdin-only; --content-file was proven correct and must keep
    appending identical content as two records."""
    elem = {"note": "cf", "k": 2}
    p1 = _run_append(tmp_path, "t.json", "items", elem, via="content-file", tmp_dir=tmp_path)
    assert p1.returncode == 0, p1.stderr
    p2 = _run_append(tmp_path, "t.json", "items", elem, via="content-file", tmp_dir=tmp_path)
    assert p2.returncode == 0, p2.stderr
    arr = _arr(tmp_path, "t.json", "items")
    assert len(arr) == 2, f"--content-file must NOT be deduped, got {len(arr)}: {arr}"


# ── M2 — the list/--extend path ─────────────────────────────────────────────
def test_list_stdin_identical_resubmission_deduped(tmp_path):
    """A --stdin JSON LIST re-submitted identically must be suppressed as a unit
    (not double-extended), and a distinct list still extends."""
    batch = [{"note": "a"}, {"note": "b"}]
    p1 = _run_append(tmp_path, "t.json", "items", batch)
    assert p1.returncode == 0, p1.stderr
    assert len(_arr(tmp_path, "t.json", "items")) == 2
    p2 = _run_append(tmp_path, "t.json", "items", batch)
    assert p2.returncode == 0, f"identical list re-submission must not error: {p2.stderr}"
    arr = _arr(tmp_path, "t.json", "items")
    assert len(arr) == 2, f"identical list re-submission must be suppressed, got {len(arr)}: {arr}"
