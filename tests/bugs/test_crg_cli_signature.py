"""
Bug: skills/agents invoke the code-review-graph (CRG) CLI with verbs/signatures
     that do not exist in the pinned CRG 2.3.5.

Expected: every CRG CLI invocation in a SKILL.md / agents/*.md / diagnose pass
          template uses a real CRG 2.3.5 subcommand, and every `build`/`update`
          invocation uses only valid flags (repo via `--repo`, isolated DB via
          `--data-dir` -- never a bare positional path, never `--out`).
Actual:   - `build .` (adopt, triage) and `build <path> --out <dir>` (diagnose,
            bug-hunt) fail at argparse with exit 2 "unrecognized arguments";
            real signature is `build [--repo REPO] [--data-dir DATA_DIR]`.
          - `search`, `impact-radius`, `review-context` are invoked as CLI verbs
            (design-slice, release, 4 agents, diagnose passes) but CRG 2.3.5
            has NO such subcommands -- they are MCP-only tools.

Pure static analysis -- does NOT build a real graph (no CRG runtime needed).
Only validates strings that are genuine command invocations:
  1. the resolved-variable bash form   "${CRG:-code-review-graph}" <verb> ...
  2. a command line that STARTS WITH `code-review-graph <verb>` (the diagnose
     pass examples), optionally behind a `$ ` shell prompt
  3. an inline-backtick-wrapped command  `code-review-graph <verb> ...`
English prose ("code-review-graph integration", "...before wide changes") and
shell redirects (`build 2>/dev/null`) are deliberately NOT treated as
invocations -- so the matcher does not depend on fragile code-fence parity.

This test FAILS until skills/SKILL.md, agents/*.md and the diagnose pass
templates are corrected, then passes.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Frozen ground truth: CRG 2.3.5 `code-review-graph --help` subcommand set ---
VALID_VERBS = {
    "install", "init", "build", "update", "postprocess", "embed", "watch",
    "status", "visualize", "wiki", "register", "unregister", "repos", "eval",
    "detect-changes", "serve", "mcp", "daemon",
}

# Flags `build`/`update` actually accept (CRG 2.3.5 `build|update --help`).
VALID_BUILD_UPDATE_FLAGS = {
    "--repo", "--data-dir", "--skip-flows", "--skip-postprocess",
    "--base", "--brief", "--verify",
}

# strict bash form: "${CRG:-code-review-graph}" <verb> ...
RE_CRG_VAR = re.compile(r'"\$\{CRG:-code-review-graph\}"\s+(.+)')
# bare command line: (optional `$ ` prompt) code-review-graph <verb> ...
RE_CRG_BARE = re.compile(r'(?:\$\s+)?code-review-graph\s+([a-z][a-z-]*)(.*)')
# inline-backtick-wrapped command: `code-review-graph <verb> ...`
RE_CRG_TICK = re.compile(r'`[^`]*?\bcode-review-graph\s+([a-z][a-z-]*)([^`]*)`')


def _scan_targets():
    files = sorted(REPO_ROOT.glob("skills/**/*.md"))
    files += sorted((REPO_ROOT / "agents").glob("*.md"))
    return files


def _strip(tok: str) -> str:
    return tok.strip().strip('`"\'').strip()


def _is_positional_path(tok: str) -> bool:
    """First arg to build/update that is a positional path (repo must be --repo)."""
    t = _strip(tok)
    if not t or t.startswith("-"):
        return False
    if any(op in t for op in (">", "<", "|", "&")):  # shell redirect/operator
        return False
    return t in (".", "..") or t.startswith("$")


def _check(tokens, loc, raw, out):
    if not tokens:
        return
    verb = _strip(tokens[0])
    if not verb or verb.startswith("-"):  # global option (--version/-h), not a verb
        return
    if verb not in VALID_VERBS:
        out.append((loc, raw, f"'{verb}' is not a CRG 2.3.5 CLI verb"))
        return
    if verb in ("build", "update"):
        args = tokens[1:]
        if args and _is_positional_path(args[0]):
            out.append((loc, raw, f"'{verb}' takes no positional path "
                                  f"(use --repo): {_strip(args[0])!r}"))
        for a in args:
            at = _strip(a)
            if at.startswith("--") and at not in VALID_BUILD_UPDATE_FLAGS:
                hint = " (use --data-dir)" if at == "--out" else ""
                out.append((loc, raw, f"'{verb}' has no flag '{at}'{hint}"))


def _collect_violations():
    out = []
    for path in _scan_targets():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            loc = f"{rel}:{i}"
            stripped = line.strip()
            for m in RE_CRG_VAR.finditer(line):
                _check(m.group(1).split(), loc, stripped, out)
            m = RE_CRG_BARE.match(stripped)  # only a line that STARTS with the cmd
            if m:
                _check([m.group(1)] + m.group(2).split(), loc, stripped, out)
            for m in RE_CRG_TICK.finditer(line):
                _check([m.group(1)] + m.group(2).split(), loc, stripped, out)
    # de-dupe exact (loc, reason) repeats
    seen, uniq = set(), []
    for v in out:
        key = (v[0], v[2])
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq


def test_no_invalid_crg_cli_invocations():
    violations = _collect_violations()
    if violations:
        report = "\n".join(f"  {loc}\n    {raw}\n    -> {why}"
                           for loc, raw, why in violations)
        pytest.fail(
            f"{len(violations)} invalid CRG CLI invocation(s) vs the frozen "
            f"CRG 2.3.5 surface:\n{report}"
        )


def _violations_in_line(line: str):
    """Run the scanner's matchers on ONE line — the classifier under test."""
    out = []
    stripped = line.strip()
    for m in RE_CRG_VAR.finditer(line):
        _check(m.group(1).split(), "fixture", stripped, out)
    m = RE_CRG_BARE.match(stripped)
    if m:
        _check([m.group(1)] + m.group(2).split(), "fixture", stripped, out)
    for m in RE_CRG_TICK.finditer(line):
        _check([m.group(1)] + m.group(2).split(), "fixture", stripped, out)
    return out


# Frozen battery (M4/m2): a regex regression that stops flagging a BAD form — or starts flagging a
# GOOD one — fails HERE even when the live tree is clean. Assert the classifier, not just a live count.
_BAD_FORMS = [
    '"${CRG:-code-review-graph}" build .',                                          # positional
    '"${CRG:-code-review-graph}" build "$TARGET" --out "$OUT/.code-review-graph"',   # positional + --out
    '"${CRG:-code-review-graph}" search "<concept>"',                               # phantom verb (quoted form)
    '"${CRG:-code-review-graph}" impact-radius --node "<file>"',                    # phantom verb
    'code-review-graph search "main entry routes" --out $OUT/.code-review-graph',    # phantom verb (bare, line-start)
    'code-review-graph review-context "x" --out $OUT/.code-review-graph',            # phantom verb (bare)
    '| rebuild (`code-review-graph build .`); answer from Read/Grep |',             # positional (backtick / table cell)
]
_GOOD_FORMS = [
    '"${CRG:-code-review-graph}" build',                                            # bare build (auto-detect)
    '"${CRG:-code-review-graph}" build --repo .',                                   # --repo flag
    '"${CRG:-code-review-graph}" build --repo "$TARGET"',                           # --repo with value
    '"${CRG:-code-review-graph}" build 2>/dev/null || echo "CRG unavailable"',      # shell redirect, not a positional
    '"${CRG:-code-review-graph}" install --platform claude-code',                   # valid verb + flag
    'call mcp__code-review-graph__semantic_search_nodes_tool with query "x"',       # MCP tool name, not a CLI verb
    'from code_review_graph.tools.query import semantic_search_nodes',              # python module (underscores)
    'query via the `review-context` / `search` MCP tools',                          # bare MCP-tool mentions (no prefix)
]


def test_classifier_catches_known_bad_forms():
    missed = [b for b in _BAD_FORMS if not _violations_in_line(b)]
    assert not missed, f"scanner FAILED to flag known-bad CRG forms (regex regression): {missed}"


def test_classifier_passes_known_good_forms():
    flagged = [(g, _violations_in_line(g)) for g in _GOOD_FORMS if _violations_in_line(g)]
    assert not flagged, f"scanner FALSE-flagged valid CRG forms: {flagged}"
