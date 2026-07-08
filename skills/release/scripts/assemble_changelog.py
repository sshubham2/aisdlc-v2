"""assemble_changelog.py — build a version-grouped CHANGELOG.md by MERGING git
history with per-slice changelog.json records (v2; slice-007 overhaul).

The DETERMINISTIC half of /release — no model, no hallucination. CHANGELOG.md is
treated as a derived, disposable PROJECTION recomputed in full every run from two
durable sources, and NEVER read back in (ADR-005):

  1. git history — the project bumps the `version` field in .claude-plugin/plugin.json
     on every pushed commit (there are no git tags), so the version of each commit is
     read from that field. Two git calls total (not one `git show` per commit — M1):
       * `git log` for the full commit list (hash, author-date, subject), and
       * `git cat-file --batch` reading `.claude-plugin/plugin.json` at EVERY commit —
         the TRUE per-commit version. This is correct across merges/rebases, where a
         forward-fill over a flat `git log` order mis-attributes (proven by slice-002
         landing on 2.28.0 instead of 2.29.0 — the design-spike/meta-Critic risk). A
         commit whose manifest is momentarily missing/malformed forward-fills the
         previous readable version (that gap only — M4).
  2. the per-slice `changelog.json` records `/commit-slice` writes — the richer,
     audited overlay (human `intent` + ADR citations) for the versions they cover.

Per version the overlay is laid over the git base (OVERLAY + RESIDUAL): the slice
record's rich entry REPLACES the slice's own git-subject row (no double-list), and the
version's *residual* non-slice / non-merge commits are kept alongside it (so nothing in
a version is hidden — slice-007 design-spike finding INV-3). The slice->version join
goes through git (the commit whose subject carries an EXACT `slice-NNN` token; M2), with
the record's stored `subject` field as a secondary key for a squash-merged slice whose
token was rewritten away (M5).

Idempotence + non-destructiveness (AC4) are structural: the output is a pure function of
(git history, plugin.json-version-per-commit, slice records) with a total deterministic
ordering, so a re-run is byte-identical and there is no prior file to clobber. On a
git-INCOMPLETE run (no git / not a repo / zero commits / shallow clone) it degrades to a
slice-records-only render, and REFUSES to overwrite an existing CHANGELOG.md rather than
shrink it (no read-back — an existence check only; M4 / M-add-1).

Output: default prints the markdown to STDOUT; `--out PATH` writes it (utf-8) + prints PATH.
Exit 0 success (incl. a degraded write where nothing is lost) · 2 usage error · 3 refused
to overwrite on a degraded run (fail-visible, existing file untouched).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
from pathlib import Path

# --- single-skill import bootstrap (cannot use `python -m` for a bundled script) ---
_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<name>/scripts/X.py -> <plugin>
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout  # noqa: E402

PLUGIN_REL = ".claude-plugin/plugin.json"

# conventional-commit type -> Keep-a-Changelog section. Unknown types fall to "Changed".
_SECTION = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "build": "Changed",
    "chore": "Changed",
    "docs": "Changed",
    "style": "Changed",
    "test": "Changed",
    "revert": "Changed",
}
_SECTION_ORDER = ["Added", "Changed", "Fixed"]
_SLICE_NUM = re.compile(r"slice-(\d+)")
_CONV_PREFIX = re.compile(r"^\w+(\([^)]*\))?!?:\s*")
_VERSION_LINE = re.compile(r'"version"\s*:\s*"([^"]+)"')
# NOTE: the git-log field separator must NOT be a char `str.splitlines()` treats as a
# line boundary (it splits on \v \f \x1c \x1d \x1e \x85 …); \x1f (US) is safe.
_UNIT = "\x1f"   # field separator inside a git --format line (safe under splitlines)


# ----------------------------- slice helpers --------------------------------
def _slice_num(rec: dict, folder: str) -> int:
    m = _SLICE_NUM.search(str(rec.get("slice") or folder))
    return int(m.group(1)) if m else -1


def _slice_n(slice_id: str | None) -> int | None:
    m = _SLICE_NUM.search(str(slice_id or ""))
    return int(m.group(1)) if m else None


def _slice_token_re(n: int) -> re.Pattern:
    r"""EXACT slice-id token (M2): ``\bslice-0*N\b`` matches ``slice-006`` / ``slice-6`` /
    ``slice-006-name`` but NOT the substring lookalike ``slice-0060``."""
    return re.compile(rf"\bslice-0*{n}\b")


# ----------------------------- record loading -------------------------------
def _load_changelogs(vault: str) -> list[tuple[int, dict]]:
    out: list[tuple[int, dict]] = []
    for f in glob.glob(os.path.join(vault, "slices", "archive", "*", "changelog.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue  # skip unreadable / malformed — never abort the whole changelog on one bad file
        out.append((_slice_num(rec, os.path.basename(os.path.dirname(f))), rec))
    out.sort(key=lambda t: t[0], reverse=True)  # newest slice first
    return out


def _entry_line(rec: dict) -> str:
    """The rich overlay line for a slice record: prefers the human `intent`."""
    scope = str(rec.get("scope") or "").strip()
    desc = str(rec.get("intent") or "").strip()
    if not desc:
        desc = _CONV_PREFIX.sub("", str(rec.get("subject") or "").strip())
    sl = str(rec.get("slice") or "").strip()
    adrs = rec.get("adrs") or []
    adr_str = f" [{', '.join(adrs)}]" if adrs else ""
    prefix = f"**{scope}:** " if scope else ""
    cite = f" ({sl})" if sl else ""
    return f"- {prefix}{desc}{cite}{adr_str}".rstrip()


def _residual_line(subject: str) -> str:
    """A residual (non-slice) git commit, conventional prefix stripped."""
    return f"- {_CONV_PREFIX.sub('', subject.strip())}".rstrip()


def _subj_section(subject: str) -> str:
    m = re.match(r"^(\w+)(\([^)]*\))?!?:", subject)
    return _SECTION.get(m.group(1).lower(), "Changed") if m else "Changed"


def _vt(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except (ValueError, AttributeError):
        return (0,)


# ----------------------------- git layer (M1) -------------------------------
def _run_git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _git_log(repo_root: str) -> list[dict] | None:
    """ONE call: every commit oldest->newest as {hash, date (YYYY-MM-DD), subject}.
    None on git failure (binary missing / not a repo / zero commits)."""
    try:
        # --topo-order keeps a child after its parents; --date-order is the stable
        # secondary key so parallel branches with no strict ancestry break ties
        # deterministically (M3: identical output across runs on parallel history).
        r = _run_git(repo_root, "log", "--reverse", "--topo-order", "--date-order",
                     "--date=short", f"--format=%H{_UNIT}%ad{_UNIT}%s")
    except (FileNotFoundError, OSError):
        return None
    if r.returncode != 0:
        return None
    commits = []
    for line in r.stdout.splitlines():
        if not line:
            continue
        parts = line.split(_UNIT, 2)
        if len(parts) != 3:
            continue
        h, date, subj = parts
        commits.append({"hash": h, "date": date, "subject": subj})
    return commits


def _versions_at(repo_root: str, commits: list[dict]) -> dict[str, str]:
    """Read the plugin.json ``version`` at EVERY commit in ONE ``git cat-file --batch``
    (M1: NOT one ``git show`` per commit) — the TRUE per-commit file state, so a commit's
    version is correct even across merges/rebases. (Forward-filling over a flat ``git log``
    order mis-attributes there — proven by slice-002 mapping to 2.28.0 instead of 2.29.0;
    the meta-Critic flagged exactly this.) Returns {hash: version}; a commit whose manifest
    is missing/malformed is simply absent (the caller forward-fills only that gap — M4)."""
    if not commits:
        return {}
    refs = "".join(f"{c['hash']}:{PLUGIN_REL}\n" for c in commits).encode("utf-8")
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "cat-file", "--batch"],
                           input=refs, capture_output=True)
    except (FileNotFoundError, OSError):
        return {}
    if r.returncode != 0:
        return {}
    out = r.stdout  # bytes; cat-file emits one response per input ref, IN ORDER
    versions: dict[str, str] = {}
    pos = 0
    for c in commits:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) == 3 and header[1] == "blob":
            size = int(header[2])
            body = out[pos:pos + size]
            pos += size + 1  # skip the blob content + its trailing newline
            m = _VERSION_LINE.search(body.decode("utf-8", "replace"))
            if m:
                versions[c["hash"]] = m.group(1)
        # else: "<ref> missing" / unexpected header — no body to skip; commit absent
    return versions


def _git_history(repo_root: str) -> tuple[str, list[dict] | None]:
    """Returns (status, commits). status: ok | absent | empty | shallow.
    Anything but ``ok`` means the log is incomplete -> the caller degrades."""
    try:
        top = _run_git(repo_root, "rev-parse", "--show-toplevel")
    except (FileNotFoundError, OSError):
        return ("absent", None)
    if top.returncode != 0:
        return ("absent", None)
    commits = _git_log(repo_root)
    if not commits:  # None (git log failed) or [] -> repo with no commits
        return ("empty", [])
    shallow = _run_git(repo_root, "rev-parse", "--is-shallow-repository")
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        return ("shallow", commits)
    return ("ok", commits)


def _attribute(commits: list[dict], versions: dict[str, str]) -> list[dict]:
    """Attribute each commit to its TRUE plugin.json version (read per-commit, so correct
    across merges). A commit whose manifest was momentarily missing/malformed (absent from
    ``versions``) inherits the previous readable version (forward-fill ONLY across that gap
    — M4); a leading run before plugin.json existed back-fills to the earliest version so
    AC2 drops nothing."""
    out = []
    cur = None
    for c in commits:
        v = versions.get(c["hash"])
        if v is not None:
            cur = v
        out.append({**c, "version": v if v is not None else cur})
    first = next((c["version"] for c in out if c["version"]), None)
    if first is not None:
        for c in out:
            if c["version"] is None:
                c["version"] = first
            else:
                break
    return out


# ----------------------------- open-period roll-forward (slice-009) ----------
def _is_noise(subject: str) -> bool:
    """A commit that carries no changelog content of its own: a merge commit or a
    chore(release) version-bump (both filtered from the residual rows). Used both
    to detect an empty-but-for-noise open period and to suppress residual lines."""
    return subject.startswith("Merge ") or subject.startswith("chore(release)")


def _roll_forward(attributed: list[dict], new_version: str | None
                  ) -> tuple[list[dict], int]:
    """Roll the OPEN PERIOD onto the about-to-ship version (slice-009).

    Under the relocated bump, slice commits land at the OLD version (the slice
    commit no longer stages a bump); /release cuts the next version after
    merge. The OPEN PERIOD is every commit AFTER the last version-change commit:
    they all share the head version but did NOT set it, so they are unreleased.

      * ``trailing_run_start`` = the smallest index i such that
        ``attributed[i..end]`` all carry the head version — i.e. the commit that
        SET the head version (the last version-change).
      * the open period = commits at indices > ``trailing_run_start``.

    If ``new_version`` is given (validated > head by the caller), every
    open-period commit is re-attributed to it; the version-change commit and
    everything before KEEP their spot version (historical BYTE-STABLE).

    If ``new_version`` is absent: a no-op (spot attribution stands) — the caller
    has already decided whether the open period is fail-visible.

    Returns ``(attributed, rolled_count)`` (mutates in place; rolled_count is 0
    when nothing rolled).
    """
    if not attributed or not new_version:
        return attributed, 0
    head_v = attributed[-1]["version"]
    if head_v is None:
        return attributed, 0
    # walk back over the trailing run that all share head_v
    trailing_run_start = len(attributed) - 1
    while trailing_run_start > 0 and attributed[trailing_run_start - 1]["version"] == head_v:
        trailing_run_start -= 1
    rolled = 0
    for c in attributed[trailing_run_start + 1:]:
        c["version"] = new_version
        rolled += 1
    return attributed, rolled


def _open_period_has_unreleased_work(attributed: list[dict]) -> bool:
    """True when, with NO --new-version, the open period contains a commit that
    carries real changelog content (a non-merge, non-chore(release) commit) — the
    fail-visible signal that unreleased slices would otherwise be silently filed
    under the old version (slice-009)."""
    if not attributed:
        return False
    head_v = attributed[-1]["version"]
    if head_v is None:
        return False
    trailing_run_start = len(attributed) - 1
    while trailing_run_start > 0 and attributed[trailing_run_start - 1]["version"] == head_v:
        trailing_run_start -= 1
    return any(not _is_noise(c["subject"])
               for c in attributed[trailing_run_start + 1:])


# ----------------------------- merge (M2 / M5) ------------------------------
def _slice_index(records: list[dict], attributed: list[dict]) -> dict[str, dict]:
    """Join each record to a version. Primary: an EXACT slice-NNN token in a commit
    subject (earliest introducing version on multiple matches — M5). Secondary: the
    record's stored ``subject`` field equals a commit subject (squash-merge — M5)."""
    by_subject = {c["subject"]: c for c in attributed if c["version"]}
    idx: dict[str, dict] = {}
    for rec in records:
        slice_id = str(rec.get("slice") or "")
        n = _slice_n(slice_id)
        ver = None
        if n is not None:
            tok = _slice_token_re(n)
            matches = [c for c in attributed if c["version"] and tok.search(c["subject"])]
            if matches:
                ver = min(matches, key=lambda c: _vt(c["version"]))["version"]
        if ver is None:
            subj = str(rec.get("subject") or "").strip()
            if subj and subj in by_subject:
                ver = by_subject[subj]["version"]
        idx[slice_id] = {"version": ver, "record": rec}
    return idx


def _claimed_hashes(idx: dict[str, dict], attributed: list[dict]) -> set[str]:
    """Commits represented by a record's overlay — excluded from the residual rows
    (so a slice's own commit, or a residual commit that references a record's slice,
    is never double-listed — M2)."""
    claimed: set[str] = set()
    for slice_id, info in idx.items():
        if info["version"] is None:
            continue
        n = _slice_n(slice_id)
        if n is not None:
            tok = _slice_token_re(n)
            claimed.update(c["hash"] for c in attributed if tok.search(c["subject"]))
        subj = str(info["record"].get("subject") or "").strip()
        if subj:
            claimed.update(c["hash"] for c in attributed if c["subject"] == subj)
    return claimed


# ----------------------------- render ---------------------------------------
_HEADER = [
    "# Changelog",
    "",
    "<!-- Generated by /release: rebuilt from git history "
    "(.claude-plugin/plugin.json version per commit) merged with the per-slice "
    "changelog.json records /commit-slice writes. Do not hand-edit — regenerate. -->",
    "",
    "All notable changes, newest first. Versions are reconstructed from git history; "
    "per-slice records enrich the versions they cover.",
    "",
]


def _bucket_lines(buckets: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for sec in _SECTION_ORDER:
        if buckets.get(sec):
            out += [f"### {sec}", ""] + buckets[sec] + [""]
    return out


def _render(attributed: list[dict], records: list[dict]) -> str:
    idx = _slice_index(records, attributed)
    claimed = _claimed_hashes(idx, attributed)

    releases: dict[str, list[dict]] = {}
    ver_date: dict[str, str] = {}
    for c in attributed:
        v = c["version"]
        if not v:
            continue
        # Register the version (so its section header + date render even when its
        # only commit is noise — a chore(release) bump). Noise commits are dropped
        # from the residual ROWS below (constraint 1 + m4a), not from the version map.
        releases.setdefault(v, []).append(c)
        if v not in ver_date:  # oldest->newest: a version's first commit dates it
            ver_date[v] = c["date"]

    overlay_by_ver: dict[str, list[tuple[str, dict]]] = {}
    for slice_id, info in idx.items():
        if info["version"]:
            overlay_by_ver.setdefault(info["version"], []).append((slice_id, info["record"]))

    lines = list(_HEADER)
    lines += ["## [Unreleased]", ""]
    unplaceable = [info["record"] for info in idx.values() if info["version"] is None]
    if unplaceable:
        buckets: dict[str, list[str]] = {s: [] for s in _SECTION_ORDER}
        for rec in sorted(unplaceable, key=lambda r: _slice_n(r.get("slice")) or 0):
            buckets.setdefault(_SECTION.get(str(rec.get("type") or "").lower(), "Changed"),
                               []).append(_entry_line(rec))
        lines += _bucket_lines(buckets)
        lines += ["_(records above could not be matched to a released version)_", ""]
    else:
        lines += ["_Nothing unreleased._", ""]

    for v in sorted(releases.keys(), key=_vt, reverse=True):
        lines.append(f"## [{v}] — {ver_date.get(v, '')}".rstrip())
        lines.append("")
        buckets = {s: [] for s in _SECTION_ORDER}
        for slice_id, rec in sorted(overlay_by_ver.get(v, []),
                                    key=lambda t: _slice_n(t[0]) or 0):
            sec = _SECTION.get(str(rec.get("type") or "").lower(), "Changed")
            buckets.setdefault(sec, []).append(_entry_line(rec))
        for c in sorted(releases[v], key=lambda c: (c["date"], c["hash"])):
            # constraint 1: drop merge noise; m4a: drop chore(release) bump commits —
            # their residual line is suppressed (the header above still rendered).
            if c["hash"] in claimed or _is_noise(c["subject"]):
                continue
            buckets.setdefault(_subj_section(c["subject"]), []).append(
                _residual_line(c["subject"]))
        lines += _bucket_lines(buckets)
    return "\n".join(lines).rstrip() + "\n"


def _render_slices_only(records: list[dict]) -> str:
    """The degraded fallback (git unavailable): the legacy flat slice-records-only
    render under a visible banner. Never invents history — only what the records hold."""
    lines = list(_HEADER)
    lines += [
        "> NOTE: git history was unavailable — this CHANGELOG was reconstructed from "
        "per-slice records ONLY and omits versions that predate them.",
        "",
        "## [Unreleased]",
        "",
    ]
    if not records:
        return "\n".join(lines + ["_No shipped slices yet._", ""]).rstrip() + "\n"
    buckets: dict[str, list[str]] = {s: [] for s in _SECTION_ORDER}
    for rec in records:
        buckets.setdefault(_SECTION.get(str(rec.get("type") or "").lower(), "Changed"),
                           []).append(_entry_line(rec))
    lines += _bucket_lines(buckets)
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------- CLI ------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="assemble_changelog",
        description="Merge git history + per-slice changelog.json records into a "
                    "version-grouped CHANGELOG.md (/release).",
    )
    p.add_argument("--vault", required=True, help="vault root (contains slices/archive/*/changelog.json)")
    p.add_argument("--out", default=None, help="write CHANGELOG markdown to this file and print the path "
                                               "(default: print the markdown to stdout)")
    p.add_argument("--repo-root", default=None, dest="repo_root",
                   help="the code repo to read git history from (default: cwd / git rev-parse)")
    p.add_argument("--new-version", default=None, dest="new_version",
                   help="the version being cut now (slice-009): roll the OPEN PERIOD "
                        "(commits after the last version-change) forward onto it. Must be "
                        "strictly greater than the current head version. Omit when there is "
                        "no unreleased work (an open period of merges only).")
    return p


def _resolve_repo_root(arg: str | None) -> str:
    if arg:
        return arg
    r = _run_git(".", "rev-parse", "--show-toplevel")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return os.getcwd()


def _emit(md: str, out: str | None) -> int:
    """Write (full happy-path render) or print. Used only when git history is OK."""
    if out:
        try:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(md, encoding="utf-8", newline="\n")
        except OSError as exc:
            sys.stderr.write(f"assemble_changelog: cannot write --out {out}: {exc}\n")
            return 2
        print(out)
    else:
        print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    args = _build_arg_parser().parse_args(argv)

    vault = args.vault.strip()
    if not vault:
        sys.stderr.write("assemble_changelog: --vault is required\n")
        return 2

    records = [rec for _, rec in _load_changelogs(vault)]
    repo_root = _resolve_repo_root(args.repo_root)
    status, commits = _git_history(repo_root)

    if status != "ok":
        # Degraded run — the git log is absent/incomplete, so a full reconstruction is
        # impossible. NEVER overwrite an existing CHANGELOG.md with a lesser document
        # (M4 / M-add-1): refuse-to-write, existence check only (no read-back; ADR-005).
        reason = {
            "absent": "git unavailable or not a git repository",
            "empty": "repository has no commits yet",
            "shallow": "shallow clone — git history is truncated",
        }.get(status, status)
        sys.stderr.write(
            f"assemble_changelog: {reason}; cannot reconstruct full version history "
            f"from git, falling back to slice-records-only.\n")
        md = _render_slices_only(records)
        if args.out:
            if Path(args.out).exists():
                sys.stderr.write(
                    "assemble_changelog: refusing to overwrite the existing "
                    f"{args.out} with a slice-records-only render — would lose "
                    "reconstructed history. Existing file left untouched. "
                    "(restore git history, then regenerate)\n")
                return 3
            try:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(md, encoding="utf-8", newline="\n")
            except OSError as exc:
                sys.stderr.write(f"assemble_changelog: cannot write --out {args.out}: {exc}\n")
                return 2
            print(args.out)
            return 0
        print(md)
        return 0

    versions = _versions_at(repo_root, commits)
    attributed = _attribute(commits, versions)

    # --- open-period roll-forward (slice-009) ---
    new_version = (args.new_version or "").strip() or None
    head_v = attributed[-1]["version"] if attributed else None
    if new_version is not None:
        if head_v is not None and _vt(new_version) <= _vt(head_v):
            sys.stderr.write(
                f"assemble_changelog: --new-version {new_version} is not greater than the "
                f"current head version {head_v}; refusing to roll the open period backward.\n")
            return 2
        attributed, rolled = _roll_forward(attributed, new_version)
        # M-must-not-defer: log the version cut + how many commits rolled forward, to
        # STDERR (so --out/stdout markdown stays clean).
        sys.stderr.write(
            f"assemble_changelog: cut {new_version}; rolled {rolled} open-period "
            f"commit(s) forward from {head_v} onto {new_version}.\n")
    elif _open_period_has_unreleased_work(attributed):
        # fail-visible: unreleased non-merge commits sit after the last version-change,
        # and no --new-version was given to cut them onto. NEVER silently file them under
        # the old version.
        sys.stderr.write(
            f"assemble_changelog: unreleased commits present after the last version cut "
            f"({head_v}) but no --new-version was supplied — refusing to file them under "
            f"the old version. Re-run with --new-version <X.Y.Z> to cut the open period.\n")
        return 2

    md = _render(attributed, records)
    return _emit(md, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
