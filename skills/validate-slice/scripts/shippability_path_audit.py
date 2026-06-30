"""Shippability-catalog test-path existence audit (PTFCD-1, sub-mode (b)) — v2 JSON.

Reads `<vault>/shippability.json` (v2 JSON; v1 parsed the `architecture/
shippability.md` markdown table). For each row in `rows[]`, extracts the
`tests/<...>.py` path tokens cited in the row's `machine_cmd` field —
specifically the tokens that appear AFTER the `pytest` keyword — strips a
trailing `::selector`, resolves repo-root-relative, and flags any token whose
file does not exist on disk. When a `::`-selector is present and the file
exists, additionally verifies the cited test FUNCTION exists (PTFFD-1).

v2 shape (`<vault>/shippability.json`; schema by example
`skills/repro/examples/shippability.json`):

    {
      "_schema": "aisdlc/shippability@1",
      "rows": [
        {
          "id": "SHIP-007", "slice": "slice-019", "kind": "test",
          "description": "...",
          "machine_cmd": "python -m pytest tests/bugs/test_webhook_sig.py -q",
          "critical_path": true, "added": "<ts>"
        }
      ]
    }

**v2 change from v1.** The catalog is JSON, not a markdown table — there is no
`Machine-cmd` 6th column to locate, no backtick fences, no separator rows. Each
row's command is the `machine_cmd` string field directly. The post-`pytest`
token-extraction predicate (`_TEST_PATH_RE`) + the `_pyfn` function-resolution
tri-state are preserved verbatim from v1. `_TEST_PATH_RE`, `_extract_test_tokens`,
and `_find_repo_root` remain importable (consumed by the SCMD-1 decoupling audit
and the SRSC-1 runner, the same private cross-tool reuse pattern v1 used).

The catalog now lives in the EXTERNAL vault store (`~/.aisdlc/<project>/`), which
is OUTSIDE the repo, so the cited `tests/...` are repo-relative — resolved via
`_find_repo_root` (catalog-anchored sentinel walk → external-vault discriminator
→ cwd walk).

Usage:
    python shippability_path_audit.py <vault>/shippability.json
    python shippability_path_audit.py <vault>/shippability.json --json

Exit codes:
    0  clean (or empty / zero-row catalog)
    1  one or more phantom test-path / function citations
    2  usage error (catalog file missing, unreadable, or not valid JSON)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _pyfn, _stdout
from scripts.lib._vault_paths import VAULT_ROOT

# `tests/<...>.py` token (repo-relative test path). Backticks/quotes are
# stripped per-token before this is applied to the post-`pytest` segment.
_TEST_PATH_RE = re.compile(r"tests/\S+?\.py")


@dataclass(frozen=True)
class PhantomCitation:
    row: str           # the catalog row id (v2 `id`, or row index when absent)
    token: str         # the offending test-path token as written
    resolved: str      # the absolute path that was tried
    index: int         # 0-based index in rows[]
    # "missing-test-file" (FILE-level, default) | "missing-test-function"
    # (function-level layer — PTFFD-1). Additive field.
    kind: str = "missing-test-file"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    rows_scanned: int = 0
    tokens_checked: int = 0
    violations: list[PhantomCitation] = field(default_factory=list)
    # PTFFD-1 visible skip-notes for tokens whose cited file could not be
    # AST-parsed — NO violation, but not silent.
    skip_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows_scanned": self.rows_scanned,
            "tokens_checked": self.tokens_checked,
            "violations": [v.to_dict() for v in self.violations],
            "skip_notes": list(self.skip_notes),
            "summary": {
                "violation_count": len(self.violations),
            },
        }


def _walk_for_repo_sentinel(origin: Path) -> Path | None:
    """Walk up from `origin` for a `.git` dir/file or `VERSION` file sentinel."""
    cur = origin.resolve()
    if cur.is_file():
        cur = cur.parent
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists() or (cand / "VERSION").exists():
            return cand
    return None


def _catalog_under_external_vault(catalog_path: Path) -> bool:
    """True when `catalog_path` lives under the resolved EXTERNAL vault root.

    The v2 vault is the external store (`~/.aisdlc/<project>/`), OUTSIDE the
    repo — so the cited `tests/...` are repo-relative, not catalog-relative.
    Consulted ONLY when the catalog-anchored walk fails to find a repo, so it
    cleanly distinguishes the external store's catalog (absolute VAULT_ROOT)
    from a tmp_path fixture catalog (not under VAULT_ROOT).
    """
    try:
        vroot = Path(VAULT_ROOT)
        if not vroot.is_absolute():
            return False
        vroot = vroot.resolve()
        cat = catalog_path.resolve()
        return cat == vroot or vroot in cat.parents
    except OSError:
        return False


def _find_repo_root(start: Path) -> Path:
    """Resolve the repo root that `tests/...` citations are relative to.

    The catalog sits in the EXTERNAL vault store (not inside the repo), so a
    sentinel walk up from it normally fails; we then resolve from the invocation
    cwd (the repo worktree). tmp_path fixtures that place fixture tests under the
    catalog dir keep the legacy catalog-parent fallback (their catalog is not
    under the external VAULT_ROOT).
    """
    # BB-09: when the catalog lives in the EXTERNAL vault (the normal case), the repo is
    # the invocation CWD — NOT an ancestor of ~/.aisdlc. Walking up from the catalog FIRST
    # wrongly matched a $HOME/.git (dotfiles) or a VERSION above ~/.aisdlc, so resolve from
    # CWD first in that case; only tmp_path fixtures (catalog NOT under VAULT_ROOT) keep the
    # catalog-anchored walk.
    if _catalog_under_external_vault(start):
        from_cwd = _walk_for_repo_sentinel(Path.cwd())
        if from_cwd is not None:
            return from_cwd
        from_catalog = _walk_for_repo_sentinel(start)
        if from_catalog is not None:
            return from_catalog
        return Path.cwd().resolve()
    from_catalog = _walk_for_repo_sentinel(start)
    if from_catalog is not None:
        return from_catalog
    return start.resolve().parent


# A pytest `::`-selector immediately following a matched test path, e.g.
# `::TestClass::test_method` or `::test_fn[case]`. Bounded by whitespace /
# backtick / quote.
_SELECTOR_RE = re.compile(r"""\A(::[^\s`"']+)""")


def _extract_test_tokens(command: str) -> list[tuple[str, str | None]]:
    """Return `(file_token, raw_selector|None)` pairs after the `pytest` kw.

    Scope to the post-`pytest` segment so an interpreter path and `-m pytest`
    prefix are never mistaken for test paths. The `::`-selector is CAPTURED (not
    split away) so the function-level layer can verify the cited test function
    exists. The file token itself is backtick/quote-stripped and `::`-free.
    """
    idx = command.find("pytest")
    if idx == -1:
        return []
    segment = command[idx + len("pytest"):]
    pairs: list[tuple[str, str | None]] = []
    for m in _TEST_PATH_RE.finditer(segment):
        tok = m.group(0).strip("`").strip().strip('"').strip("'")
        tok = tok.split("::", 1)[0].strip()
        if not tok:
            continue
        sel_match = _SELECTOR_RE.match(segment[m.end():])
        selector = sel_match.group(1) if sel_match else None
        pairs.append((tok, selector))
    return pairs


def _load_rows(catalog_path: Path) -> list[dict]:
    """Load `rows[]` from a shippability.json. Raises ValueError on bad shape."""
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("shippability.json top level is not a JSON object")
    rows = data.get("rows", [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("shippability.json `rows` is not a JSON array")
    return [r for r in rows if isinstance(r, dict)]


def audit_catalog_file(catalog_path: Path) -> AuditResult:
    """Audit <vault>/shippability.json against PTFCD-1 / PTFFD-1 sub-mode (b)."""
    result = AuditResult()
    rows = _load_rows(catalog_path)
    repo_root = _find_repo_root(catalog_path)

    for index, row in enumerate(rows):
        command = str(row.get("machine_cmd", "")).strip()
        if not command:
            continue  # SCMD-1 pre-catalog gate owns the missing-machine_cmd case
        row_id = str(row.get("id") or row.get("slice") or index)
        result.rows_scanned += 1
        for tok, selector in _extract_test_tokens(command):
            result.tokens_checked += 1
            candidate = Path(tok)
            if not candidate.is_absolute():
                candidate = repo_root / candidate
            if not candidate.exists():
                result.violations.append(PhantomCitation(
                    row=row_id, token=tok, resolved=str(candidate),
                    index=index, kind="missing-test-file",
                ))
                continue

            # FILE exists — if the citation carries a `::`-selector, verify the
            # cited test FUNCTION (terminal `::`-segment, `[param-id]` stripped)
            # exists (PTFFD-1).
            if selector is None:
                continue
            fn_name = _pyfn.selector_terminal_name(selector)
            if fn_name is None:
                continue  # no checkable terminal name → FILE-level-only
            verdict = _pyfn.function_defined_in_file(candidate, fn_name)
            if verdict is False:
                result.violations.append(PhantomCitation(
                    row=row_id, token=f"{tok}{selector}", resolved=str(candidate),
                    index=index, kind="missing-test-function",
                ))
            elif verdict is None:
                result.skip_notes.append(
                    f"shippability.json row {row_id}: function-check skipped "
                    f"(file unparseable) for '{fn_name}' in '{candidate}'."
                )
    return result


def _format_human(result: AuditResult) -> str:
    skip_block = ""
    if result.skip_notes:
        skip_block = (
            f"\n{len(result.skip_notes)} function-check skip-note(s) "
            f"(PTFFD-1; no violation):\n"
            + "".join(f"  - {n}\n" for n in result.skip_notes)
        )

    if not result.violations:
        return (
            f"Shippability path audit (PTFCD-1/PTFFD-1): clean. "
            f"{result.rows_scanned} row(s), "
            f"{result.tokens_checked} test-path token(s) — all files and "
            f"cited functions exist.\n"
            + skip_block
        )
    out = [
        f"{len(result.violations)} phantom test-path/function citation(s) "
        f"in shippability.json (PTFCD-1/PTFFD-1 sub-mode (b)):\n\n"
    ]
    for v in result.violations:
        if v.kind == "missing-test-function":
            detail = (
                f"resolves to existing file '{v.resolved}', but the cited "
                f"test function does not exist in it (PTFFD-1)."
            )
        else:
            detail = (
                f"resolves to '{v.resolved}', which does not exist on disk "
                f"(PTFCD-1)."
            )
        out.append(
            f"  [Important] shippability.json row {v.row} "
            f"({v.kind}) — token '{v.token}'\n"
            f"    {detail}\n\n"
        )
    out.append(skip_block)
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="shippability_path_audit",
        description="PTFCD-1 shippability-catalog test-path existence audit (v2 JSON)",
    )
    parser.add_argument(
        "catalog", type=Path, nargs="?",
        default=VAULT_ROOT / "shippability.json",
        help="Path to shippability.json (default: <vault>/shippability.json)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    catalog_path: Path = args.catalog
    if not catalog_path.is_file():
        sys.stderr.write(
            f"shippability_path_audit: catalog file not found or not a file: "
            f"{catalog_path}\n"
        )
        return 2

    try:
        result = audit_catalog_file(catalog_path)
    except OSError as exc:
        sys.stderr.write(f"shippability_path_audit: cannot read catalog: {exc}\n")
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(
            f"shippability_path_audit: catalog is not valid shippability.json: {exc}\n"
        )
        return 2

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
