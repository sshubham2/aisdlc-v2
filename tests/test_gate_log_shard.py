"""Durable regression for the gate-log shard store (slice-088 / SC-193 / ADR-105+ADR-106).

Proves the append-only per-entry shard log + derived cache seam against the 5 ACs AND every
ratified Critic finding (B1, B2, M2, M3, M-add-1, M-add-2, INV3, m2, m3). The AC4/AC5 proofs run
over a FROZEN snapshot of the real gate-log (tests/fixtures/gatelog-real-snapshot.json — the same
data the feasibility + design spikes used), with the entry count read DYNAMICALLY (m1: the live
log grows; never a hardcoded 590/592/595) and compared PARSED-equal (not byte-equal — derive emits
meta-first/entries-last while the original is entries-first).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import _shard_store as S
from scripts.lib import vault_edit

VAULT_EDIT = REPO_ROOT / "scripts" / "lib" / "vault_edit.py"
VAULT_ADMIN = REPO_ROOT / "scripts" / "lib" / "vault_admin.py"
FIXTURE = Path(__file__).parent / "fixtures" / "gatelog-real-snapshot.json"

REL, ARRAY = "gate-log.json", "entries"


# ── helpers ──────────────────────────────────────────────────────────────────────

def _seed_flat(vault: Path, entries: list | None = None, rows: list | None = None) -> dict:
    """Write a small flat gate-log.json under `vault` and return the parsed doc."""
    doc: dict = {"entries": entries if entries is not None else [], "_plugin_version": "9.9.9"}
    if rows is not None:
        doc["rows"] = rows
    (vault / "gate-log.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def _cache(vault: Path) -> dict:
    return json.loads((vault / "gate-log.json").read_text(encoding="utf-8"))


def _shard_names(vault: Path) -> list[str]:
    d = vault / "gate-log"
    return sorted(p.name for p in d.glob("*.json") if p.name != "_meta.json") if d.is_dir() else []


def _append_cli(vault: Path, element, *, via="content-file", allow_duplicate=False):
    """Append via the REAL vault_edit CLI (the faithful invocation path)."""
    argv = [sys.executable, str(VAULT_EDIT), "--vault", str(vault),
            "append", "--file", "gate-log.json", "--array", "entries"]
    if allow_duplicate:
        argv.append("--allow-duplicate")
    payload = json.dumps(element)
    stdin_text = None
    if via == "stdin":
        argv.append("--stdin"); stdin_text = payload
    elif via == "json":
        argv += ["--json", payload]
    else:
        cf = vault / "_elem.json"; cf.write_text(payload, encoding="utf-8")
        argv += ["--content-file", str(cf)]
    return subprocess.run(argv, input=stdin_text, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def _migrate_cli(vault: Path, *, reverse=False):
    argv = [sys.executable, str(VAULT_ADMIN), "migrate", "--vault", str(vault)]
    if reverse:
        argv.append("--reverse")
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


# ── AC1: an append writes a NEW shard, never a whole-file RMW of the flat cache ───

def test_ac1_append_writes_new_shard_not_flat_rmw(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": 0}, {"n": 1}])
    assert S.migrate(tmp_path, REL, ARRAY)["action"] == "migrated"
    before = _shard_names(tmp_path)
    r = _append_cli(tmp_path, {"n": 2, "gate": "critique"})
    assert r.returncode == 0, r.stderr
    after = _shard_names(tmp_path)
    assert len(after) == len(before) + 1, "an append must create exactly one NEW shard file"
    # the shard dir (not the flat file) is the write target: even with the derived cache DELETED,
    # the append still lands (shard written) and the cache self-heals from the shards.
    (tmp_path / "gate-log.json").unlink()
    r2 = _append_cli(tmp_path, {"n": 3})
    assert r2.returncode == 0, r2.stderr
    assert len(_shard_names(tmp_path)) == len(before) + 2
    assert [e["n"] for e in _cache(tmp_path)["entries"]] == [0, 1, 2, 3]


# ── AC2: dual-read — sharded-dir-present derives in order; legacy-flat falls back ─

def test_ac2_dual_read_sharded_and_legacy_flat(tmp_path):
    # (a) legacy-flat branch: NO shard dir -> the append routes to the flat path unchanged.
    v1 = tmp_path / "flat"; v1.mkdir()
    _seed_flat(v1, entries=[{"n": 0}])
    assert not S.is_sharded(v1, REL, ARRAY)
    assert _append_cli(v1, {"n": 1}).returncode == 0
    assert not (v1 / "gate-log").exists(), "no shard dir must be created on the legacy-flat path"
    assert [e["n"] for e in _cache(v1)["entries"]] == [0, 1]

    # (b) sharded branch: after migrate, reads derive from shards IN INSERTION ORDER.
    v2 = tmp_path / "sharded"; v2.mkdir()
    _seed_flat(v2, entries=[{"n": 10}, {"n": 11}, {"n": 12}])
    S.migrate(v2, REL, ARRAY)
    assert S.is_sharded(v2, REL, ARRAY)
    for k in (13, 14):
        assert _append_cli(v2, {"n": k}).returncode == 0
    assert [e["n"] for e in S.derive(v2 / "gate-log")["entries"]] == [10, 11, 12, 13, 14]


# ── AC3: the derived cache is parsed-equal to what a legacy json.loads reader expects ─

def test_ac3_cache_reader_parity_after_appends(tmp_path):
    base = [{"n": i} for i in range(4)]
    _seed_flat(tmp_path, entries=list(base), rows=[{"legacy": True}])
    original_entries = list(base)
    S.migrate(tmp_path, REL, ARRAY)
    appended = [{"n": 100}, {"n": 101}]
    for e in appended:
        assert _append_cli(tmp_path, e).returncode == 0
    # a plain json.loads reader of the cache path sees exactly the pre-migration entries + appends,
    # in order, and the preserved rows[] — ZERO reader code change (AC3).
    cache = _cache(tmp_path)
    assert cache["entries"] == original_entries + appended
    assert cache["rows"] == [{"legacy": True}]
    assert cache["_plugin_version"] == "9.9.9"


# ── AC4: migrate round-trip over the REAL snapshot — parsed-equal, idempotent, reversible ─

def test_ac4_migrate_roundtrip_idempotent_reverse_real(tmp_path):
    assert FIXTURE.is_file(), "frozen real-snapshot fixture missing"
    (tmp_path / "gate-log.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    original = _cache(tmp_path)
    n = len(original["entries"])  # dynamic — never hardcoded (m1)
    assert n > 100 and "rows" in original, "fixture should be the real multi-hundred-entry log"

    # forward via the CLI actuator
    r = _migrate_cli(tmp_path)
    assert r.returncode == 0, r.stderr
    derived = S.derive(tmp_path / "gate-log")
    assert derived == original, "derive(migrate(flat)) must be PARSED-equal to the original"
    assert len(derived["entries"]) == n and derived["rows"] == original["rows"]

    # idempotent re-run = no-op, still parsed-equal
    r2 = _migrate_cli(tmp_path)
    assert r2.returncode == 0 and "noop" in r2.stdout
    assert S.derive(tmp_path / "gate-log") == original

    # reversible: shards -> flat == original; shard dir gone
    r3 = _migrate_cli(tmp_path, reverse=True)
    assert r3.returncode == 0, r3.stderr
    assert not (tmp_path / "gate-log").exists()
    assert _cache(tmp_path) == original, "--reverse must restore the flat file parsed-equal"


# ── AC5: the synthesized shard key is collision-free + order-preserving over real N ──

def test_ac5_shard_keys_unique_and_order_preserving_real(tmp_path):
    (tmp_path / "gate-log.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    original = _cache(tmp_path)
    S.migrate(tmp_path, REL, ARRAY)
    seqs = [int(name[:-5]) for name in _shard_names(tmp_path)]
    assert len(seqs) == len(set(seqs)) == len(original["entries"]), "zero seq collisions over real N"
    # order preservation: derive (ordered by parsed-int seq) reproduces the original insertion order
    assert S.derive(tmp_path / "gate-log")["entries"] == original["entries"]


# ── B1: an O_EXCL shard collision fails-visible, never a silent overwrite ─────────

def test_b1_shard_collision_raises_never_silent_overwrite(tmp_path):
    target = tmp_path / "000000.json"
    S._write_exclusive(target, {"a": 1})
    with pytest.raises(FileExistsError):
        S._write_exclusive(target, {"a": 2})  # a seq collision must RAISE (B1)
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}, "the original is never overwritten"


# ── B2 / M-add-1: a forward migrate racing concurrent appends loses/dups NOTHING ──

def test_b2_madd1_forward_migrate_races_concurrent_appends(tmp_path):
    base = [{"n": i} for i in range(20)]
    _seed_flat(tmp_path, entries=list(base), rows=[{"r": 0}])
    n0 = len(base)
    tags = [f"racer-{i}" for i in range(6)]
    # launch migrate + 6 appends SIMULTANEOUSLY (real subprocess concurrency, one shared lock)
    procs = [subprocess.Popen([sys.executable, str(VAULT_ADMIN), "migrate", "--vault", str(tmp_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)]
    for t in tags:
        cf = tmp_path / f"_r_{t}.json"; cf.write_text(json.dumps({"tag": t}), encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, str(VAULT_EDIT), "--vault", str(tmp_path), "append",
             "--file", "gate-log.json", "--array", "entries", "--content-file", str(cf)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    codes = [p.wait(timeout=120) for p in procs]
    assert all(c == 0 for c in codes), f"a racer failed: {codes}"

    # migrate ran -> sharded. derive is fail-closed (raises on any dup seq), so a clean derive
    # already proves no duplicate shard; assert every entry survives EXACTLY once (no loss/dup).
    final = S.derive(tmp_path / "gate-log")["entries"]
    assert len(final) == n0 + len(tags), f"expected {n0 + len(tags)} entries, got {len(final)}"
    got_tags = sorted(e["tag"] for e in final if "tag" in e)
    assert got_tags == sorted(tags), "every concurrent append must survive exactly once"
    assert _cache(tmp_path)["rows"] == [{"r": 0}], "the legacy rows[] survives the migration"


def test_madd1_reroute_finalizes_route_under_lock(tmp_path, monkeypatch):
    """Deterministic proof of the under-lock re-route: is_sharded returns False at the lock-free
    fast-path (simulating pre-publish) then True inside mutate (a migrate published under us) ->
    the append is re-routed to the shard store, NOT written into the derived cache."""
    _seed_flat(tmp_path, entries=[{"n": 0}])
    S.migrate(tmp_path, REL, ARRAY)  # shard dir really exists so append_entry can write
    n_before = len(_shard_names(tmp_path))
    calls = {"n": 0}
    real = S.is_sharded

    def flaky_is_sharded(root, rel, array):
        calls["n"] += 1
        return False if calls["n"] == 1 else real(root, rel, array)  # False at fast-path, True in mutate

    monkeypatch.setattr(vault_edit._shard_store, "is_sharded", flaky_is_sharded)
    rc = vault_edit.main(["--vault", str(tmp_path), "append", "--file", "gate-log.json",
                          "--array", "entries", "--json", json.dumps({"n": 1, "via": "reroute"})])
    assert rc == 0
    assert calls["n"] >= 2, "both the fast-path and the under-lock re-check must run"
    assert len(_shard_names(tmp_path)) == n_before + 1, "re-routed append must write a SHARD"
    assert [e["n"] for e in S.derive(tmp_path / "gate-log")["entries"]] == [0, 1]


# ── M2: cache-regen under concurrency stays consistent (final cache == derive(shards)) ─

def test_m2_concurrent_appends_cache_equals_derive(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": -1}])
    S.migrate(tmp_path, REL, ARRAY)
    tags = [f"c{i}" for i in range(8)]
    procs = []
    for t in tags:
        cf = tmp_path / f"_c_{t}.json"; cf.write_text(json.dumps({"tag": t}), encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, str(VAULT_EDIT), "--vault", str(tmp_path), "append",
             "--file", "gate-log.json", "--array", "entries", "--content-file", str(cf)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    assert all(p.wait(timeout=120) == 0 for p in procs)
    cache = _cache(tmp_path)
    derived = S.derive(tmp_path / "gate-log")
    assert cache == derived, "the cache must never be stale-by-one vs derive(shards) after concurrent appends"
    assert len(derived["entries"]) == 1 + len(tags)
    assert sorted(e["tag"] for e in derived["entries"] if "tag" in e) == sorted(tags)


def test_m2_dedup_contract_stdin_suppress_content_file_exempt_allow_dup_bypass(tmp_path):
    """The shard path reproduces vault_edit's full --stdin bounded-dedup contract (M2): an identical
    --stdin re-submission is suppressed (exit 0 + {suppressed:true} + count unchanged); --content-file
    is exempt; --allow-duplicate forces through."""
    _seed_flat(tmp_path, entries=[{"n": 0}])
    S.migrate(tmp_path, REL, ARRAY)
    row = {"gate": "critique", "verdict": "clean"}
    r1 = _append_cli(tmp_path, row, via="stdin")
    assert r1.returncode == 0 and r1.stdout.strip() == ""
    n_after_first = len(_shard_names(tmp_path))
    r2 = _append_cli(tmp_path, row, via="stdin")  # identical --stdin within the window -> suppressed
    assert r2.returncode == 0
    sig = json.loads(r2.stdout.strip())
    assert sig["suppressed"] is True and sig["array"] == "entries"
    assert "DUPLICATE_SUPPRESSED" in r2.stderr
    assert len(_shard_names(tmp_path)) == n_after_first, "suppressed append must add NO shard (count +0)"
    # --content-file is exempt from dedup
    assert _append_cli(tmp_path, row, via="content-file").returncode == 0
    assert len(_shard_names(tmp_path)) == n_after_first + 1
    # --allow-duplicate forces a genuine immediate --stdin duplicate through
    assert _append_cli(tmp_path, row, via="stdin", allow_duplicate=True).returncode == 0
    assert len(_shard_names(tmp_path)) == n_after_first + 2


# ── M3: publish is a single no-clobber same-volume rename; a pre-existing dir is a loud stop ─

def test_m3_publish_no_clobber_and_staging_inside_vault(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": 0}])
    # a pre-existing gate-log/ dir (an interrupted prior migrate) must NOT be clobbered.
    (tmp_path / "gate-log").mkdir()
    (tmp_path / "gate-log" / "junk.txt").write_text("stale", encoding="utf-8")
    r = _migrate_cli(tmp_path)
    assert r.returncode == 3, "publish must fail-closed on a pre-existing shard dir"
    assert "already exists" in r.stderr.lower() or "fail-closed" in r.stderr.lower()
    assert _cache(tmp_path) == {"entries": [{"n": 0}], "_plugin_version": "9.9.9"}, "flat file untouched"
    # no leftover staging dir inside the vault
    assert not list(tmp_path.glob(".gate-log.migrating.*")), "staging must be cleaned up on failure"


# ── BC-PROJ-12: the migrate round-trip VERIFY can go red (mutate the protected invariant) ─

def test_migrate_roundtrip_verify_can_fail(tmp_path, monkeypatch):
    """Prove the fail-closed guard is not a no-op: if the staged shards do NOT round-trip to the
    original (an entry is corrupted during staging), migrate ABORTS (RuntimeError), the flat file
    is left intact, and NO shard dir is published. Without this, a green migrate can't be
    distinguished from a verify that silently stopped matching (BC-PROJ-12)."""
    _seed_flat(tmp_path, entries=[{"n": 0}, {"n": 1}, {"n": 2}])
    original = _cache(tmp_path)
    real_write = S._write_exclusive
    calls = {"n": 0}

    def corrupting_write(target, element):
        calls["n"] += 1
        if calls["n"] == 2 and isinstance(element, dict):  # corrupt the 2nd staged entry
            element = {**element, "n": 999}
        return real_write(target, element)

    monkeypatch.setattr(S, "_write_exclusive", corrupting_write)
    with pytest.raises(RuntimeError, match="round-trip verify FAILED"):
        S.migrate(tmp_path, REL, ARRAY)
    assert not (tmp_path / "gate-log").exists(), "a failed verify must NOT publish the shard dir"
    assert _cache(tmp_path) == original, "the flat file must be left intact on a verify failure"
    assert not list(tmp_path.glob(".gate-log.migrating.*")), "staging must be cleaned up"


# ── M-add-2: --reverse is symmetric over the .gitignore (mirror of forward) ───────

def test_madd2_reverse_removes_gitignore_symmetric(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": 0}, {"n": 1}])
    S.migrate(tmp_path, REL, ARRAY)
    gi_after_forward = (tmp_path / ".gitignore").read_text(encoding="utf-8").split()
    assert "/gate-log.json" in gi_after_forward, "forward migrate must git-ignore the derived cache"
    S.migrate(tmp_path, REL, ARRAY, reverse=True)
    gi = (tmp_path / ".gitignore")
    lines = gi.read_text(encoding="utf-8").split() if gi.exists() else []
    assert "/gate-log.json" not in lines, "--reverse must UN-ignore the flat file (symmetric — M-add-2)"
    assert "gate-log/*.lock" not in lines and "gate-log/*.tmp" not in lines


# ── m2: the .gitignore covers the shard dir's coordination cruft ──────────────────

def test_m2_gitignore_covers_lock_and_tmp_cruft(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": 0}])
    S.migrate(tmp_path, REL, ARRAY)
    lines = set((tmp_path / ".gitignore").read_text(encoding="utf-8").split())
    assert {"/gate-log.json", "gate-log/*.lock", "gate-log/*.tmp"} <= lines


# ── INV3: derive is fail-closed on a torn / non-int / duplicate-seq shard; heals a missing cache ─

def test_inv3_derive_fail_closed(tmp_path):
    _seed_flat(tmp_path, entries=[{"n": 0}, {"n": 1}])
    S.migrate(tmp_path, REL, ARRAY)
    sd = tmp_path / "gate-log"
    # missing cache: derive still returns ALL entries (never [])
    (tmp_path / "gate-log.json").unlink()
    assert len(S.derive(sd)["entries"]) == 2
    # torn shard -> raise
    (sd / "000001.json").write_text('{"truncated": ', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        S.derive(sd)
    # non-int filename -> raise (fail-visible, never silently skipped)
    (sd / "000001.json").write_text('{"n": 1}', encoding="utf-8")  # repair
    (sd / "abc.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        S.derive(sd)
    (sd / "abc.json").unlink()
    # duplicate seq -> raise. A second file whose stem parses to an EXISTING int (a 7-digit spelling
    # of 1 alongside the 6-digit 000001.json) is a duplicate seq -> ledger-integrity RuntimeError.
    (sd / "0000001.json").write_text(json.dumps({"n": 1}), encoding="utf-8")  # parses to seq 1
    with pytest.raises(RuntimeError):
        S.derive(sd)


# ── slice-089 / SC-194 (AC1): read_entries derive-on-missing ─────────────────────

def _sharded_vault(vault: Path, entries: list) -> Path:
    """Build a MIGRATED (sharded) vault: flat seed -> migrate -> shard dir + derived cache present."""
    _seed_flat(vault, entries=list(entries), rows=[{"legacy": True}])
    assert S.migrate(vault, REL, ARRAY)["action"] == "migrated"
    return vault


def test_ac1a_fast_path_cache_present_returns_cache_rows(tmp_path):
    # (a) a present, list-valued cache is served on the fast path.
    v = _sharded_vault(tmp_path, [{"n": 0}, {"n": 1}])
    assert (v / "gate-log.json").is_file()
    assert S.read_entries(v) == [{"n": 0}, {"n": 1}]


def test_ac1a2_flat_vault_fast_path_does_not_derive(tmp_path, monkeypatch):
    # (a2 / M3) the DOMINANT real case: a FLAT vault (cache present, NO shard dir) reads via the
    # fast path and NEVER invokes derive() -- pins ADR-107's 'strict superset' claim (Feathers
    # characterization test: lock in the existing behaviour at the seam before changing it).
    v = tmp_path / "flat"; v.mkdir()
    _seed_flat(v, entries=[{"n": 5}, {"n": 6}])
    assert not (v / "gate-log").exists()

    def _spy(*a, **k):
        raise AssertionError("derive() must NOT be called on a flat vault (fast path)")
    monkeypatch.setattr(S, "derive", _spy)

    assert S.read_entries(v) == [{"n": 5}, {"n": 6}]
    assert not (v / "gate-log").exists(), "read_entries must not create a shard dir on a flat vault"


def test_ac1b_cache_absent_shards_present_derives(tmp_path):
    # (b) a synced/cloned vault: the git-ignored derived cache is absent, the shard log is present.
    v = _sharded_vault(tmp_path, [{"n": 0}, {"n": 1}, {"n": 2}])
    cache_rows = S.read_entries(v)  # fast path (cache present)
    (v / "gate-log.json").unlink()  # simulate the clone/sync: cache gone, shards remain
    assert not (v / "gate-log.json").exists()
    derived = S.read_entries(v)
    assert derived == cache_rows == [{"n": 0}, {"n": 1}, {"n": 2}]
    # read-only: the derive path must NOT re-create the cache (no read-repair — ADR-106 B2).
    assert not (v / "gate-log.json").exists(), "read_entries must not write the cache back"


def test_ac1b2_listless_cache_shards_present_derives(tmp_path):
    # (b2 / M2) a JSON-valid but list-less cache ({} / {entries:null} / {entries:non-list}) with
    # shards present must DERIVE, never silently return [] on the fast path.
    v = _sharded_vault(tmp_path, [{"n": 0}, {"n": 1}])
    for bad in ("{}", '{"entries": null}', '{"entries": {"not": "a list"}}'):
        (v / "gate-log.json").write_text(bad, encoding="utf-8")
        assert S.read_entries(v) == [{"n": 0}, {"n": 1}], f"listless cache {bad!r} must derive"


def test_ac1b3_torn_cache_heal_emits_warning(tmp_path, capsys):
    # (b3 / M-add-1) a torn-but-PRESENT cache healed from shards emits a stderr WARNING.
    v = _sharded_vault(tmp_path, [{"n": 0}, {"n": 1}])
    (v / "gate-log.json").write_text('{"entries": [trunc', encoding="utf-8")  # invalid JSON
    assert S.read_entries(v) == [{"n": 0}, {"n": 1}]
    err = capsys.readouterr().err
    assert "WARNING" in err and "heal" in err.lower(), "torn-cache heal must warn on stderr (M-add-1)"


def test_ac1b3b_plain_absent_cache_does_not_warn(tmp_path, capsys):
    # a plain cache-ABSENT derive (fresh clone/replica) is the EXPECTED path -> no warning.
    v = _sharded_vault(tmp_path, [{"n": 0}, {"n": 1}])
    (v / "gate-log.json").unlink()
    capsys.readouterr()  # clear anything from migrate
    assert S.read_entries(v) == [{"n": 0}, {"n": 1}]
    assert "WARNING" not in capsys.readouterr().err, "a plain cache-absent derive must NOT warn"


def test_ac1c_neither_cache_nor_shards_returns_empty(tmp_path):
    # (c) neither a cache nor a shard dir -> [] (legitimate empty log).
    v = tmp_path / "empty"; v.mkdir()
    assert not (v / "gate-log.json").exists() and not (v / "gate-log").exists()
    assert S.read_entries(v) == []


def test_ac1d_torn_without_recovery_raises(tmp_path):
    # (d) torn cache + NO shard dir -> RAISE (fail-visible; [] is NOT legitimate here).
    v1 = tmp_path / "tc"; v1.mkdir()
    (v1 / "gate-log.json").write_text('{"entries": [trunc', encoding="utf-8")
    assert not (v1 / "gate-log").exists()
    with pytest.raises((ValueError, OSError)):
        S.read_entries(v1)
    # torn shard (cache absent) -> derive() RAISES, propagated.
    v2 = tmp_path / "ts"; v2.mkdir()
    _sharded_vault(v2, [{"n": 0}, {"n": 1}])
    (v2 / "gate-log.json").unlink()
    shard = next(p for p in (v2 / "gate-log").glob("*.json") if p.name != "_meta.json")
    shard.write_text('{"truncated": ', encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        S.read_entries(v2)


def test_ac1_array_guard_rejects_non_entries(tmp_path):
    # m2: the `array` param is guarded to 'entries' until derive() is parameterized.
    v = tmp_path / "g"; v.mkdir()
    with pytest.raises(ValueError):
        S.read_entries(v, "gate-log.json", "rows")


# ── m3: the sharding route predicate keys on the vault-relative POSIX path (SC-046) ─

def test_m3_routing_keys_on_vault_relative_path(tmp_path):
    # the live root-level gate-log.json IS sharded once its dir exists ...
    _seed_flat(tmp_path, entries=[{"n": 0}])
    S.migrate(tmp_path, REL, ARRAY)
    assert S.is_sharded(tmp_path, "gate-log.json", "entries")
    # ... but a NESTED/relocated copy (archive/gate-log.json) is NOT routed, even with a sibling dir,
    # because the allowlist keys on the vault-relative POSIX path, never the basename (SC-046 / m3).
    assert S.sharded_dir_name("archive/gate-log.json", "entries") is None
    assert not S.is_sharded(tmp_path, "archive/gate-log.json", "entries")
