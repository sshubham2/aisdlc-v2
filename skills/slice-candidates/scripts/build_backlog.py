"""build_backlog.py — diagnose findings -> <vault>/candidates.json (v2 port of v1's backlog builder).

Single-skill tool for `/slice-candidates`. Reads an owner-annotated `diagnosis.html`
(the embedded `<script id="diagnose-data">` JSON; falls back to `findings/*.yaml`),
keeps only `confirmed == "yes"` findings, couples them (shared evidence files +
best-effort code-review-graph blast-radius), groups coupled findings into
"must-do-together" clusters, orders by priority, and **atomically appends** one
candidate per confirmed finding to `<vault>/candidates.json` via the SVW-1 locked
read-modify-write (`_vault_write.safe_mutate_text`) — assigning `SC-NNN` ids and
de-duplicating against findings already turned into candidates, all inside the lock.

v2 changes from v1 (`temp/skills/slice-candidates/build_backlog.py`):
  - OUTPUT is `<vault>/candidates.json` (JSON candidate objects), NOT `backlog.md`.
  - blast-radius via **code-review-graph** (CRG), NOT graphify — best-effort; absent/
    failed CRG degrades to shared-evidence-only and is flagged in the summary.
  - Coupling is UNDIRECTED (both signals) → clusters; diagnose candidates carry
    `dependencies: []` (a confirmed finding is an independent fix, coupled-not-blocked),
    so candidates.json stays a valid DAG (no cycles for /slice's dep-met check to choke on).
  - The obo round-trip (`--obo-extract` / `--obo-peek` / `--obo-write`) is ported verbatim
    in intent: annotated-HTML span-rewrite with byte-unchanged assertion on the original,
    ADR-054 allow-set-gated evidence reads, `obo-run.log` audit.

Modes (mutually exclusive):
  (default)        --in DIR --vault ROOT [--crg-graph DIR]   batch build + append
  --obo-extract    --in DIR                                  emit findings JSON for guided review
  --obo-peek       --in DIR --finding ID --file PATH         allow-set-gated evidence read -> stdout
  --obo-write      --in DIR --decisions FILE                 bake decisions into diagnosis.annotated.html

Exit codes: 0 success · 1 runtime error (fail-visible) · 2 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
from scripts.lib._vault_write import safe_mutate_text

# ── ranks (v1 verbatim) ──────────────────────────────────────────────────────────
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_BLAST_RANK = {"large": 3, "medium": 2, "small": 1}
_EFFORT_RANK = {"large": 3, "medium": 2, "small": 1}
_EFFORT_SML = {"small": "S", "medium": "M", "large": "L"}
# candidate.priority.score on the SHARED 1-10 scale (so diagnose candidates rank fairly
# in candidates_top against risk/discover-sourced ones — NOT v1's 0-42 internal score).
_SEV_SCORE = {"critical": 9, "high": 7, "medium": 4, "low": 2}

_DATA_BLOCK_RE = re.compile(
    r'(<script[^>]*\bid=["\']diagnose-data["\'][^>]*>)(.*?)(</script>)',
    re.DOTALL | re.IGNORECASE,
)


class _Err(RuntimeError):
    """Fail-visible error → CLI exit 1."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(vault_arg: str | None) -> Path:
    return Path(vault_arg) if vault_arg else VAULT_ROOT


def _err(msg: str) -> None:
    sys.stderr.write(f"build_backlog: {msg}\n")


# ── shared parsing (used by every mode) ──────────────────────────────────────────

def _diagnosis_html(diagnose_out: Path) -> Path:
    p = diagnose_out / "diagnosis.html"
    if not p.exists():
        raise _Err(f"{p} not found — run /diagnose first (and place the owner's annotated copy here)")
    return p


def _extract_data_block(html_text: str) -> dict | None:
    """Parse the embedded `<script id="diagnose-data">…</script>` JSON, or None if absent."""
    m = _DATA_BLOCK_RE.search(html_text)
    if not m:
        return None
    raw = m.group(2).strip()
    # mirror v1's escape of `<\/` (script-tag safety) on the way back in
    raw = raw.replace("<\\/", "</")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _Err(f"diagnose-data block is not valid JSON: {exc}") from exc


def _load_findings(diagnose_out: Path) -> tuple[list[dict], dict, str]:
    """Return (findings, annotations, source). Primary: the embedded JSON.
    Fallback: findings/*.yaml (no annotations there → empty)."""
    html = _diagnosis_html(diagnose_out)
    data = _extract_data_block(html.read_text(encoding="utf-8"))
    if data is not None and data.get("findings"):
        findings = data.get("findings") or []
        annotations = data.get("annotations") or {}
        return findings, annotations, "embedded-json"
    # fallback: per-pass YAML (lazy yaml import — only needed on this path)
    fdir = diagnose_out / "findings"
    yamls = sorted(fdir.glob("*.yaml")) if fdir.is_dir() else []
    if not yamls:
        raise _Err(
            f"no findings: {html} has no embedded diagnose-data findings and "
            f"{fdir}/*.yaml is empty. Confirm this is the owner's SAVED annotated HTML."
        )
    try:
        import yaml  # noqa: PLC0415 — lazy; embedded-JSON path needs no yaml
    except ImportError as exc:
        raise _Err(
            f"findings/*.yaml fallback needs PyYAML but it is not installed ({exc}); "
            f"use the embedded-JSON diagnosis.html instead"
        ) from exc
    findings: list[dict] = []
    for y in yamls:
        doc = yaml.safe_load(y.read_text(encoding="utf-8"))
        if isinstance(doc, list):
            findings.extend(f for f in doc if isinstance(f, dict))
        elif isinstance(doc, dict) and isinstance(doc.get("findings"), list):
            findings.extend(f for f in doc["findings"] if isinstance(f, dict))
        elif isinstance(doc, dict):
            findings.append(doc)
    return findings, {}, "findings-yaml"


def _annotation(annotations: dict, fid: str) -> dict:
    a = annotations.get(fid)
    return a if isinstance(a, dict) else {}


def _is_confirmed(annotations: dict, fid: str) -> bool:
    return str(_annotation(annotations, fid).get("confirmed", "")).strip().lower() == "yes"


def _evidence_paths(finding: dict) -> list[str]:
    out = []
    for ev in finding.get("evidence") or []:
        if isinstance(ev, dict) and ev.get("path"):
            out.append(str(ev["path"]))
        elif isinstance(ev, str):
            out.append(ev)
    return out


def _severity(f: dict) -> str:
    return str(f.get("severity") or "medium").strip().lower()


def _blast(f: dict) -> str:
    return str(f.get("blast_radius") or "medium").strip().lower()


def _effort(f: dict) -> str:
    return str(f.get("effort_estimate") or "medium").strip().lower()


# ── batch build ──────────────────────────────────────────────────────────────────

def _relnorm(p: str) -> str:
    """Repo-relative, forward-slash form for comparing evidence paths to CRG output."""
    s = str(p).replace("\\", "/")
    return s[2:] if s.startswith("./") else s


def _crg_prober(graph_dir: Path | None, repo_root: Path):
    """Return a (query_fn, state) for best-effort CRG blast-radius. query_fn(path)->set|None.

    Pinned against code-review-graph 2.3.x: there is NO `blast-radius` CLI verb. The per-file
    blast radius is the Python MCP-tool impl `code_review_graph.tools.query.get_impact_radius`,
    which we invoke through the bundled `_crg_impact.py` helper run as a SUBPROCESS under the
    current interpreter — isolating CRG's heavy imports + logging from build_backlog's stdout
    JSON contract. The helper emits repo-relative forward-slash `impacted_files`.

    Skips entirely (mode 'absent') when no graph is reachable, so a CRG-less run spawns zero
    subprocesses. Probes lazily; the FIRST hard failure (CRG uninstalled / graph broken) marks
    CRG degraded and short-circuits all later calls, per the /slice-candidates contract."""
    helper = Path(__file__).resolve().parent / "_crg_impact.py"
    graph_present = bool(graph_dir and graph_dir.exists()) or \
        (repo_root / ".code-review-graph").exists()
    state = {"mode": "untried" if (helper.exists() and graph_present) else "absent",
             "cache": {}}

    def query(evidence_path: str) -> set[str] | None:
        if state["mode"] in ("absent", "degraded"):
            return None
        if evidence_path in state["cache"]:
            return state["cache"][evidence_path]
        cmd = [sys.executable, str(helper), evidence_path, str(repo_root)]
        if graph_dir:
            cmd.append(str(graph_dir))
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            if cp.returncode != 0 or not cp.stdout.strip():
                raise RuntimeError(cp.stderr.strip() or f"exit {cp.returncode}")
            files = _coerce_blast_files(json.loads(cp.stdout))
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
            state["mode"] = "degraded"
            return None
        state["mode"] = "used"
        state["cache"][evidence_path] = files
        return files

    return query, state


def _coerce_blast_files(payload) -> set[str]:
    """Pull a flat set of repo-relative paths out of _crg_impact.py's JSON (or a bare list)."""
    if isinstance(payload, list):
        return {_relnorm(x) for x in payload}
    if isinstance(payload, dict):
        for key in ("impacted_files", "files", "nodes", "blast_radius", "affected"):
            v = payload.get(key)
            if isinstance(v, list):
                return {_relnorm(x.get("path") if isinstance(x, dict) else x) for x in v}
    return set()


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _cluster(protos: list[dict], crg_query) -> list[list[int]]:
    """Group proto indices into coupled clusters via shared evidence files (+ best-effort
    CRG blast-radius overlap). Returns a list of clusters (each a list of proto indices)."""
    n = len(protos)
    uf = _UnionFind(n)
    # shared-evidence coupling
    file_to_idx: dict[str, list[int]] = {}
    for i, p in enumerate(protos):
        for f in p["evidence"]:
            file_to_idx.setdefault(f, []).append(i)
    for idxs in file_to_idx.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)
    # best-effort CRG overlap: i's evidence appears in j's blast-radius -> couple
    if crg_query is not None:
        blast = {}
        for i, p in enumerate(protos):
            acc: set[str] = set()
            for f in p["evidence"]:
                br = crg_query(f)
                if br:
                    acc |= br
            blast[i] = acc
        for i, p in enumerate(protos):
            for j in range(n):
                # CRG returns repo-relative forward-slash paths; normalize evidence the
                # same way so the membership test actually matches on Windows.
                if i != j and any(_relnorm(f) in blast[i] for f in protos[j]["evidence"]):
                    uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())


def _blast_area(evidence: list[str]) -> str:
    """A human 'area' string for candidate.priority.blast_radius from evidence dirs."""
    dirs = []
    for f in evidence:
        parent = str(Path(f).parent).replace("\\", "/")
        if parent and parent != "." and parent not in dirs:
            dirs.append(parent)
    return ", ".join(dirs[:4]) if dirs else "(isolated)"


def _candidate_from(proto: dict, sc_id: str, mate_ids: list[str], ts: str) -> dict:
    f = proto["finding"]
    sev, blast, effort = proto["sev"], proto["blast"], proto["effort"]
    note = ""
    if mate_ids:
        note = f" Must do together with {', '.join(mate_ids)} (coupled via shared/adjacent code)."
    desc = (f.get("suggested_action") or f.get("description") or proto["title"]).strip()
    return {
        "id": sc_id,
        "title": proto["title"],
        "status": "candidate",
        "progress": "not-started",
        "slice": None,
        "claimed_by": None,
        "started_at": None,
        "source": [{"type": "finding", "ref": proto["finding_id"]}],
        "description": desc,
        "rationale": f"Confirmed {sev}-severity {f.get('category', 'finding')} "
                     f"(blast {blast}, effort {effort}).{note}",
        "user_visible_outcome": None,
        "dependencies": [],
        "priority": {
            "score": proto["score"],
            "severity": sev,
            "effort": _EFFORT_SML.get(effort, "M"),
            "blast_radius": _blast_area(proto["evidence"]),
        },
        "assumptions": [],
        "verification_plan": f"Confirm the {f.get('category', 'issue')} no longer reproduces at "
                             f"{(proto['evidence'] or ['the cited evidence'])[0]}; full test suite green.",
        "history": [{"event": "created", "by": "slice-candidates", "at": ts, "ref": proto["finding_id"]}],
    }


def _sc_num(sc_id: str) -> int:
    m = re.match(r"SC-(\d+)$", str(sc_id))
    return int(m.group(1)) if m else 0


def cmd_build(args: argparse.Namespace) -> int:
    diagnose_out = Path(args.in_dir).resolve()
    findings, annotations, source = _load_findings(diagnose_out)
    confirmed = [f for f in findings if f.get("id") and _is_confirmed(annotations, f["id"])]
    if not confirmed:
        _err(f"no Confirmed: yes findings in {diagnose_out}/diagnosis.html (source={source}); "
             f"nothing to append. Confirm this is the owner's SAVED annotated copy.")
        return 1

    # build proto-candidates
    protos = []
    for f in confirmed:
        sev, blast, effort = _severity(f), _blast(f), _effort(f)
        protos.append({
            "finding": f,
            "finding_id": str(f["id"]),
            "title": str(f.get("title") or f["id"])[:80],
            "evidence": _evidence_paths(f),
            "sev": sev, "blast": blast, "effort": effort,
            "score": max(1, min(10, _SEV_SCORE.get(sev, 4)
                                 + (1 if blast == "large" else 0)
                                 - (1 if effort == "large" else 0))),
            "order_key": _SEV_RANK.get(sev, 2) * 10 + _BLAST_RANK.get(blast, 2) - _EFFORT_RANK.get(effort, 2),
        })

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    crg_query, crg_state = _crg_prober(
        Path(args.crg_graph).resolve() if args.crg_graph else None, repo_root)
    clusters = _cluster(protos, crg_query)

    # order: clusters by max member order_key desc; within cluster by order_key desc, id asc
    for cl in clusters:
        cl.sort(key=lambda i: (-protos[i]["order_key"], protos[i]["finding_id"]))
    clusters.sort(key=lambda cl: (-max(protos[i]["order_key"] for i in cl), protos[cl[0]]["finding_id"]))
    ordered_idx = [i for cl in clusters for i in cl]
    cluster_of = {i: cl for cl in clusters for i in cl}

    project = args.project or diagnose_out.parent.name
    # archive refs/max read OUTSIDE the lock (archive is low-churn, read-only here)
    archive_refs, archive_max = _archive_scan(_root(args.vault))

    result: dict = {}

    def mutate(text: str) -> str:
        if text.strip():
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise _Err(f"candidates.json is not valid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise _Err("candidates.json top-level is not a JSON object")
        else:
            data = {"_schema": "aisdlc/slice-candidates@1", "project": project,
                    "updated": ts, "candidates": [], "pick_log": []}
        cands = data.setdefault("candidates", [])
        if not isinstance(cands, list):
            raise _Err("candidates.json 'candidates' is not an array")

        # existing finding->SC map + max id (live ∪ archive)
        fid_to_sc: dict[str, str] = {}
        maxnum = archive_max
        for c in cands:
            maxnum = max(maxnum, _sc_num(c.get("id")))
            for s in c.get("source") or []:
                if s.get("type") == "finding" and s.get("ref"):
                    fid_to_sc[s["ref"]] = c.get("id")
        existing = set(fid_to_sc) | archive_refs

        # pass 1: assign SC ids to NEW (non-dup) protos, in recommended order
        n = maxnum
        assigned: dict[int, str] = {}
        for i in ordered_idx:
            fid = protos[i]["finding_id"]
            if fid in existing or fid in fid_to_sc:
                continue
            n += 1
            sc = f"SC-{n:03d}"
            assigned[i] = sc
            fid_to_sc[fid] = sc

        # pass 2: build candidates (cluster notes reference any mate's SC id, new or existing)
        new_cands = []
        for i in ordered_idx:
            if i not in assigned:
                continue
            mates = [fid_to_sc.get(protos[j]["finding_id"])
                     for j in cluster_of[i] if j != i and protos[j]["finding_id"] in fid_to_sc]
            mates = [m for m in mates if m]
            new_cands.append(_candidate_from(protos[i], assigned[i], mates, ts))

        cands.extend(new_cands)
        data["updated"] = ts
        if not data.get("project"):
            data["project"] = project

        result["appended"] = [c["id"] for c in new_cands]
        result["skipped"] = sum(1 for i in ordered_idx if i not in assigned)
        result["clusters"] = [
            sorted(fid_to_sc.get(protos[j]["finding_id"]) for j in cl
                   if fid_to_sc.get(protos[j]["finding_id"]))
            for cl in clusters if len(cl) > 1
        ]
        result["order"] = [assigned[i] for i in ordered_idx if i in assigned]
        result["top"] = ({"id": new_cands[0]["id"], "title": new_cands[0]["title"],
                          "rationale": new_cands[0]["rationale"]} if new_cands else None)
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    ts = _now_iso()
    path = _root(args.vault) / "candidates.json"
    try:
        safe_mutate_text(path, mutate)
    except _Err as exc:
        _err(str(exc)); return 1
    except (OSError, TimeoutError) as exc:
        _err(f"write to {path} failed (fail-visible per R-7): {exc}"); return 1

    summary = {
        "action": "build-backlog",
        "vault_file": str(path),
        "source": source,
        "confirmed_findings": len(confirmed),
        "appended": len(result["appended"]),
        "appended_ids": result["appended"],
        "skipped_existing": result["skipped"],
        "clusters": result["clusters"],
        "crg": crg_state["mode"],
        "order": result["order"],
        "top": result["top"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _archive_scan(vault: Path) -> tuple[set[str], int]:
    """Finding refs + max SC number already in <vault>/archive/candidates.json (read-only)."""
    p = vault / "archive" / "candidates.json"
    if not p.exists():
        return set(), 0
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return set(), 0
    refs, mx = set(), 0
    for c in (data.get("candidates") or []):
        mx = max(mx, _sc_num(c.get("id")))
        for s in c.get("source") or []:
            if s.get("type") == "finding" and s.get("ref"):
                refs.add(s["ref"])
    return refs, mx


# ── obo: extract ─────────────────────────────────────────────────────────────────

def cmd_obo_extract(args: argparse.Namespace) -> int:
    diagnose_out = Path(args.in_dir).resolve()
    repo_root = diagnose_out.parent
    findings, annotations, _ = _load_findings(diagnose_out)
    ordered = sorted(findings, key=lambda f: (-_SEV_RANK.get(_severity(f), 2), str(f.get("id"))))
    out = []
    for f in ordered:
        fid = str(f.get("id"))
        ann = _annotation(annotations, fid)
        ev = _evidence_paths(f)
        out.append({
            "id": fid,
            "title": f.get("title"),
            "severity": _severity(f),
            "category": f.get("category"),
            "description": f.get("description"),
            "suggested_action": f.get("suggested_action"),
            "reviewed": fid in annotations,
            "current": {"confirmed": ann.get("confirmed", ""), "notes": ann.get("notes", "")},
            "evidence_paths": [str((repo_root / e).resolve()) for e in ev],
        })
    print(json.dumps({"total": len(out),
                      "reviewed": sum(1 for f in out if f["reviewed"]),
                      "findings": out}, ensure_ascii=False))
    return 0


# ── obo: peek (ADR-054 allow-set-gated evidence read) ────────────────────────────

def _obo_log(diagnose_out: Path, line: str) -> None:
    try:
        with open(diagnose_out / "obo-run.log", "a", encoding="utf-8") as fh:
            fh.write(f"{_now_iso()} {line}\n")
    except OSError:
        pass  # audit log is best-effort; never blocks the operation


def cmd_obo_peek(args: argparse.Namespace) -> int:
    diagnose_out = Path(args.in_dir).resolve()
    repo_root = diagnose_out.parent
    findings, _, _ = _load_findings(diagnose_out)
    finding = next((f for f in findings if str(f.get("id")) == args.finding), None)
    if finding is None:
        _err(f"no finding with id {args.finding!r}"); return 1
    allow = {(repo_root / e).resolve() for e in _evidence_paths(finding)}
    target = Path(args.file).resolve()
    if target not in allow:
        _obo_log(diagnose_out, f"PEEK refused: {target} not in finding {args.finding} allow-set")
        _err(f"refused: {target} is not in finding {args.finding}'s evidence allow-set (ADR-054)")
        return 1
    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        _err(f"cannot read {target}: {exc}"); return 1
    _obo_log(diagnose_out, f"PEEK ok: {args.finding} -> {target.name}")
    sys.stdout.write(content)
    return 0


# ── obo: write (bake decisions into diagnosis.annotated.html) ────────────────────

def cmd_obo_write(args: argparse.Namespace) -> int:
    diagnose_out = Path(args.in_dir).resolve()
    html_path = _diagnosis_html(diagnose_out)
    original_bytes = html_path.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    html_text = original_bytes.decode("utf-8")

    m = _DATA_BLOCK_RE.search(html_text)
    if not m:
        _err(f"{html_path} has no <script id=\"diagnose-data\"> block to annotate"); return 1
    data = _extract_data_block(html_text) or {}

    try:
        decisions = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _err(f"cannot read --decisions: {exc}"); return 2
    if not isinstance(decisions, dict):
        _err("--decisions must be a JSON object {finding_id: {confirmed, notes}}"); return 2

    annotations = data.setdefault("annotations", {})
    applied = 0
    for fid, dec in decisions.items():
        if not isinstance(dec, dict):
            continue
        confirmed = str(dec.get("confirmed", "")).strip()
        notes = str(dec.get("notes", "")).strip()
        if not confirmed and not notes:
            continue  # drop empty entries (v1 _collect parity)
        annotations[fid] = {"confirmed": confirmed, "notes": notes}
        applied += 1

    new_json = json.dumps(data, indent=2, ensure_ascii=False).replace("</", "<\\/")
    # span-insertion (NOT re.sub — preserves the rest of the HTML byte-for-byte)
    new_html = html_text[:m.start(2)] + "\n" + new_json + "\n" + html_text[m.end(2):]
    annotated = diagnose_out / "diagnosis.annotated.html"
    try:
        annotated.write_text(new_html, encoding="utf-8", newline="")
    except OSError as exc:
        _err(f"cannot write {annotated}: {exc}"); return 1

    # hard rule #3: the original diagnosis.html must be byte-unchanged
    if hashlib.sha256(html_path.read_bytes()).hexdigest() != original_sha:
        _err(f"ABORT: {html_path} changed during obo-write (must stay byte-unchanged)"); return 1

    _obo_log(diagnose_out, f"WRITE ok annotations={applied} -> diagnosis.annotated.html")
    print(json.dumps({"action": "obo-write", "annotated": str(annotated),
                      "applied": applied, "original_unchanged": True}, ensure_ascii=False))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_backlog",
        description="Turn confirmed /diagnose findings into <vault>/candidates.json slice candidates.",
    )
    p.add_argument("--in", dest="in_dir", default="./diagnose-out",
                   help="diagnose-out directory (default ./diagnose-out)")
    p.add_argument("--vault", default=None,
                   help="vault root (overrides $AI_SDLC_VAULT_ROOT / the computed default)")
    p.add_argument("--crg-graph", default=None, help="path to diagnose-out/.code-review-graph/")
    p.add_argument("--repo-root", default=None,
                   help="analyzed-repo root for CRG blast-radius (default: CWD); "
                        "evidence paths are resolved relative to it")
    p.add_argument("--project", default=None, help="project name for a fresh candidates.json")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--obo-extract", action="store_true", help="emit findings JSON for guided review")
    mode.add_argument("--obo-peek", action="store_true", help="allow-set-gated evidence read")
    mode.add_argument("--obo-write", action="store_true", help="bake decisions into diagnosis.annotated.html")
    p.add_argument("--finding", default=None, help="(--obo-peek) finding id")
    p.add_argument("--file", default=None, help="(--obo-peek) evidence file to read")
    p.add_argument("--decisions", default=None, help="(--obo-write) decisions JSON file")
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)
    try:
        if args.obo_extract:
            return cmd_obo_extract(args)
        if args.obo_peek:
            if not args.finding or not args.file:
                _err("--obo-peek requires --finding and --file"); return 2
            return cmd_obo_peek(args)
        if args.obo_write:
            if not args.decisions:
                _err("--obo-write requires --decisions"); return 2
            return cmd_obo_write(args)
        return cmd_build(args)
    except _Err as exc:
        _err(str(exc)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
