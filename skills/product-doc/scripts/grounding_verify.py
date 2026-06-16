"""grounding_verify.py — independently verify /product-doc's self-attested grounding
tokens against reality BEFORE doc-manifest.json records them (slice-015).

The forked product-doc agent returns a `grounding` map {doc -> [token, ...]} claiming
each documented command/flag/endpoint/export traces to a real CRG node or a cited file.
Nothing checked those claims, so a hallucinated flag could ship with false provenance.
This verifier re-derives each claim against reality (the PKI/OCSP discipline, ADR-011):
trust nothing on the presenter's word; fail-CLOSED when the authority is unreachable.

Token grammar (path-based, B1):
  crg:<repo-rel-path>[::<symbol>]   -> the code map (deterministic file_summary lookup)
  file:<repo-rel-path>[::<symbol>]  -> a repo file (existence + optional symbol: textual WORD-BOUNDARY
                                       membership, NOT AST — a comment-word match is the known residual; M1)
  vault:<vault-rel-path>            -> a vault provenance pointer (EXISTENCE only; ::symbol is malformed;
                                       an UNRESOLVED vault root -> source-unavailable, never repo_root; M2)

Per-token verdict -> the token is either `verified` (recorded as grounded provenance)
or listed in `grounding_unverified` with a reason:
  source-unavailable | symbol-absent | ambiguous-match | malformed | file-absent | not-indexed
Reasons enforced as an enum by scripts/lib/artifact_lint.py (doc-manifest.docs[].grounding_unverified[].reason).

IO: reads a JSON object on stdin {"grounding": <agent grounding>, "repo_root": "...",
"vault_root": "..."} and writes a JSON report on stdout:
  {"docs": {<doc>: {"verified": [...], "grounding_unverified": [{token, reason}]}},
   "grounding_check": {"ran": true, "crg_reachable": bool, "graph_last_updated": str|null,
                       "graph_stale": bool, "public_surface_verified": false},
   "log": [...]}
NEVER crashes on malformed input and NEVER maps a failure to verified (OCSP soft-fail rule).
public_surface stays Step-0 fuzzy-harvested + UNVERIFIED -> public_surface_verified=false (M-add-1).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import sys

# --- single-skill import bootstrap ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

_PROBE = pathlib.Path(__file__).resolve().parent / "_crg_grounding_probe.py"
_GLOB = ("*", "?", "[")
_VALID_REASONS = {"source-unavailable", "symbol-absent", "ambiguous-match",
                  "malformed", "file-absent", "not-indexed"}


def _norm(path: str) -> str:
    p = path.replace("\\", "/").strip()
    return p[2:] if p.startswith("./") else p


def _under(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    """Resolve rel under root; None if it escapes the root (path-traversal guard, M3)."""
    try:
        root_r = root.resolve()
        target = (root_r / rel).resolve()
    except (OSError, ValueError):
        return None
    if target == root_r or root_r in target.parents:
        return target
    return None


def _probe(repo_root: str, args: list[str]) -> dict | None:
    """Run _crg_grounding_probe.py as a subprocess. None on non-zero exit (CRG absent)."""
    try:
        p = subprocess.run([sys.executable, str(_PROBE), "--repo-root", repo_root, *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=dict(os.environ))  # m2: match the child's UTF-8
    except Exception:
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def _head_iso(repo_root: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", repo_root, "log", "-1", "--format=%cI"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")  # m2
        return p.stdout.strip() or None if p.returncode == 0 else None
    except Exception:
        return None


def _wall(ts: str | None) -> _dt.datetime | None:
    """Parse an ISO timestamp to a tz-NAIVE wall-clock datetime. CRG's last_updated is
    tz-naive local; git %cI is tz-aware local — comparing the wall-clock portions of both
    (tz stripped) measures 'graph built before the latest commit' without a naive-vs-aware
    string-compare bug (which always flags stale because the tz suffix sorts greater).

    ASSUMPTION (m3): both timestamps are the SAME machine's LOCAL wall clock. If a future CRG
    emitted last_updated in a different zone, the compare would be off by the offset; the caller
    flags stale only when HEAD is > 1 day newer than the graph, so a sub-day zone offset is
    absorbed as fresh. Advisory only — graph_stale never affects a verify/drop, only a warning."""
    if not ts:
        return None
    try:
        return _dt.datetime.fromisoformat(ts).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _classify(token, repo_root: pathlib.Path, vault_root: pathlib.Path | None,
              crg_reachable: bool) -> str | None:
    """Return None if verified, else an unverified reason."""
    if not isinstance(token, str) or ":" not in token:
        return "malformed"
    scheme, _, rest = token.partition(":")
    path, sep, symbol = rest.partition("::")
    path = _norm(path)
    symbol = symbol if sep else None

    if scheme == "vault":
        if symbol is not None or not path or any(c in path for c in _GLOB):
            return "malformed"  # a provenance pointer has no symbol to contain; globs aren't a ground
        if vault_root is None:
            return "source-unavailable"  # M2: vault root unresolved -> fail-visible, never repo_root
        tgt = _under(vault_root, path)
        return None if (tgt and tgt.exists()) else "file-absent"

    if scheme == "file":
        if not path or any(c in path for c in _GLOB):
            return "malformed"
        tgt = _under(repo_root, path)
        if tgt is None:
            return "malformed"  # path-traversal
        if not tgt.exists():
            return "file-absent"
        if symbol is not None:
            try:
                text = tgt.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return "file-absent"
            # M1: word-boundary membership (textual, NOT AST) — kills the comment-fragment and
            # substring-of-identifier false-accepts a raw `in` allows. crg: tokens use true CRG
            # node membership; for the file: scheme this is the strongest cheap check.
            if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(symbol) + r"(?![A-Za-z0-9_])", text):
                return "symbol-absent"
        return None

    if scheme == "crg":
        if not path or any(c in path for c in _GLOB):
            return "malformed"
        if _under(repo_root, path) is None:
            return "malformed"  # path-traversal
        if not crg_reachable:
            return "source-unavailable"
        pargs = ["--path", path] + (["--symbol", symbol] if symbol is not None else [])
        res = _probe(str(repo_root), pargs)
        if res is None or not res.get("reachable"):
            return "source-unavailable"
        if res.get("ambiguous"):
            return "ambiguous-match"
        if not res.get("file_resolved"):
            return "not-indexed"  # healthy graph, path absent -> not hallucination-vs-unreachable ambiguous
        if symbol is not None and not res.get("symbol_present"):
            return "symbol-absent"
        return None

    return "malformed"


def verify(payload: dict) -> dict:
    grounding = payload.get("grounding")
    repo_root = pathlib.Path(payload.get("repo_root") or ".").resolve()
    # M2: do NOT silently fall back to repo_root when the vault root is unset/empty — a vault:
    # token would then resolve against the WRONG root (a same-named repo file = a silent
    # false-accept). An unresolved vault root makes every vault: token fail-VISIBLE.
    _vr = payload.get("vault_root")
    vault_root = pathlib.Path(_vr).resolve() if _vr else None
    log: list[str] = []

    health = _probe(str(repo_root), ["--health"])
    crg_reachable = bool(health and health.get("reachable"))
    graph_last_updated = health.get("last_updated") if health else None
    g_wall, h_wall = _wall(graph_last_updated), _wall(_head_iso(str(repo_root)))
    # m3: advisory-only. Flag stale ONLY when HEAD is genuinely > 1 day newer than the graph; a
    # sub-day apparent lag (which absorbs any clock/zone offset) is treated as fresh, so a tz
    # mismatch can't raise a bogus stale (see _wall).
    graph_stale = bool(g_wall and h_wall and (h_wall - g_wall) > _dt.timedelta(days=1))
    grounding_check = {"ran": True, "crg_reachable": crg_reachable,
                       "graph_last_updated": graph_last_updated, "graph_stale": graph_stale,
                       "public_surface_verified": False}

    docs: dict[str, dict] = {}

    def _do_doc(name, tokens):
        verified, unverified = [], []
        if not isinstance(tokens, list):
            unverified.append({"token": json.dumps(tokens)[:120], "reason": "malformed"})
            log.append(f"{name}: grounding value is not a list -> malformed")
        else:
            for tok in tokens:
                reason = _classify(tok, repo_root, vault_root, crg_reachable)
                if reason is None:
                    verified.append(tok)
                else:
                    unverified.append({"token": tok if isinstance(tok, str) else json.dumps(tok)[:120],
                                       "reason": reason})
        docs[name] = {"verified": verified, "grounding_unverified": unverified}

    if isinstance(grounding, dict):
        for name, tokens in grounding.items():
            _do_doc(str(name), tokens)
    elif isinstance(grounding, list):
        _do_doc("_malformed", grounding)  # not a per-doc map -> every entry malformed
        for u in docs["_malformed"]["grounding_unverified"]:
            u["reason"] = "malformed"
        log.append("grounding is a list, not a {doc: [tokens]} map -> all-unverified (malformed)")
    else:
        docs["_malformed"] = {"verified": [],
                              "grounding_unverified": [{"token": json.dumps(grounding)[:120],
                                                        "reason": "malformed"}]}
        log.append("grounding is neither a map nor a list -> malformed")

    return {"docs": docs, "grounding_check": grounding_check, "log": log}


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    _stdout.reconfigure_stdin_utf8()
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"grounding": None}
    json.dump(verify(payload), sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
