"""Layered /validate-slice safety checks (VAL-1) — v2.

Adds two defensive layers to the per-slice validation flow:

Layer A — Credential scan (regex-based)
    Walks --changed-files for known secret patterns: AWS access keys,
    GitHub PATs (classic + fine-grained + bot tokens), Slack tokens, JWTs, PEM
    private keys, Anthropic / OpenAI API keys, generic `api_key = "..."`
    literals. Each detected secret is a Critical finding (cannot defer). An
    optional allowlist file (`<vault>/.secrets-allowlist`) holds regex patterns;
    matches that satisfy any allowlist pattern are silently suppressed.

Layer B — Dependency hallucination check (Python)
    For every Python import in --changed-files, verifies the top-level package
    is stdlib / declared in pyproject.toml or requirements.txt / a known alias /
    in --imports-allowlist. Otherwise emits an Important finding
    (`hallucinated-import`).

Per VAL-1.

**v2 change from v1.** The NFR-1 mtime carry-over exemption is REMOVED (3.9 — it was
dead for every post-install user; `--no-carry-over` is accepted as a no-op for CLI
compat). The secrets-allowlist default routes through the EXTERNAL `VAULT_ROOT`
(`<vault>/.secrets-allowlist`). Layer logic, secret patterns, import resolution, exit
codes, and CLI are otherwise preserved verbatim.

Usage:
    python validate_slice_layers.py --slice <slice-folder> --changed-files <files...>
    python validate_slice_layers.py --slice <slice-folder> --changed-files <files...> --json
    python validate_slice_layers.py --slice <slice-folder> --changed-files <files...> --skip-secrets
    python validate_slice_layers.py --slice <slice-folder> --changed-files <files...> --imports-allowlist tests

Exit codes:
    0  clean (or no changed files)
    1  findings (Critical or Important)
    2  usage error
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
import sys as _sys_module
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

_REPO = pathlib.Path(__file__).resolve().parents[3]  # skills/<skill>/scripts/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout
from scripts.lib._vault_paths import VAULT_ROOT
# 4.7: the VAL-1 secret patterns now live in scripts.lib.secret_scrub (single source of truth),
# so the diff scan here and the vault evidence-redactor pipe the SAME regexes.
from scripts.lib.secret_scrub import SECRET_PATTERNS as _SECRET_PATTERNS

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

# Standard library module names — frozenset(stdlib) in Python 3.10+.
_STDLIB: frozenset[str] = getattr(_sys_module, "stdlib_module_names", frozenset())

# --- Layer A: secret patterns (imported from scripts.lib.secret_scrub above, 4.7) ---

# --- Layer B: known aliases (import name -> package name as in pyproject) ---

_KNOWN_ALIASES: dict[str, str] = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "magic": "python-magic",
    "dateutil": "python-dateutil",
    "Crypto": "pycryptodome",
    "OpenSSL": "pyopenssl",
    "git": "gitpython",
    "jwt": "pyjwt",
    "lxml": "lxml",
    "pkg_resources": "setuptools",
    "tree_sitter_typescript": "tree-sitter-typescript",
    "tree_sitter_go": "tree-sitter-go",
}


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    pattern_name: str
    snippet: str
    kind: str       # "secret-detected"
    severity: str   # "Critical"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ImportFinding:
    path: str
    line: int
    import_name: str
    kind: str       # "hallucinated-import"
    severity: str   # "Important"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LayersResult:
    secret_findings: list[SecretFinding] = field(default_factory=list)
    import_findings: list[ImportFinding] = field(default_factory=list)
    suppressed_secrets: int = 0
    declared_deps: list[str] = field(default_factory=list)
    # SC-084 / m1: audit ledger of imports resolved as the project's OWN modules
    # (one {path,line,import_name,via} row per internal resolution) so future
    # over-suppression stays detectable. NEVER feeds total_findings.
    resolved_internal: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "secret_findings": [f.to_dict() for f in self.secret_findings],
            "import_findings": [f.to_dict() for f in self.import_findings],
            "suppressed_secrets": self.suppressed_secrets,
            "declared_deps": list(self.declared_deps),
            "resolved_internal": list(self.resolved_internal),
            "summary": {
                "critical_count": sum(
                    1 for f in self.secret_findings if f.severity == "Critical"
                ),
                "important_count": sum(
                    1 for f in self.import_findings if f.severity == "Important"
                ),
                "total_findings": (
                    len(self.secret_findings) + len(self.import_findings)
                ),
            },
        }


def _read_allowlist(path: Path | None) -> list[re.Pattern[str]]:
    """Read allowlist regex patterns from a file (one per line; # comments)."""
    if path is None or not path.exists():
        return []
    patterns: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error:
            continue  # skip malformed entries silently
    return patterns


def scan_secrets(
    file_paths: list[Path],
    allowlist_patterns: list[re.Pattern[str]],
) -> tuple[list[SecretFinding], int]:
    """Walk files for secret patterns. Returns (findings, suppressed_count)."""
    findings: list[SecretFinding] = []
    suppressed = 0
    for path in file_paths:
        if not path.exists() or path.is_dir():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in _SECRET_PATTERNS.items():
                for m in pattern.finditer(line):
                    snippet = m.group(0)
                    is_allowed = any(p.search(snippet) for p in allowlist_patterns)
                    if is_allowed:
                        suppressed += 1
                        continue
                    short = snippet[:80] + ("..." if len(snippet) > 80 else "")
                    findings.append(SecretFinding(
                        path=str(path), line=line_no,
                        pattern_name=name,
                        snippet=short,
                        kind="secret-detected",
                        severity="Critical",
                        message=(
                            f"possible {name} detected at line {line_no}. "
                            f"Critical (cannot defer). If this is a false "
                            f"positive (test fixture, public-docs example), "
                            f"add a regex to <vault>/.secrets-allowlist."
                        ),
                    ))
    return findings, suppressed


def _extract_pkg_name(spec: str) -> str:
    """Extract a package name from a dep spec like 'anthropic>=0.34.0[extras]'."""
    cleaned = spec.split("#", 1)[0].strip()
    if not cleaned or cleaned.startswith("-"):
        return ""
    earliest = len(cleaned)
    for sep in ("<", ">", "=", "!", "~", "[", ";", " ", "@"):
        idx = cleaned.find(sep)
        if 0 <= idx < earliest:
            earliest = idx
    return cleaned[:earliest].strip()


def _normalize_pkg(name: str) -> str:
    """PEP 503 normalization: lowercase, hyphens/underscores/dots -> single underscore."""
    return re.sub(r"[-_.]+", "_", name.strip().lower())


def parse_declared_deps(
    pyproject_path: Path | None,
    requirements_path: Path | None,
) -> set[str]:
    """Parse declared dependencies from pyproject.toml + requirements.txt."""
    deps: set[str] = set()

    if pyproject_path and pyproject_path.exists() and tomllib is not None:
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}

        # PEP 621
        project = data.get("project", {})
        for spec in project.get("dependencies", []) or []:
            name = _extract_pkg_name(spec)
            if name:
                deps.add(_normalize_pkg(name))
        opt_deps = project.get("optional-dependencies", {}) or {}
        for group in opt_deps.values():
            for spec in group:
                name = _extract_pkg_name(spec)
                if name:
                    deps.add(_normalize_pkg(name))

        # Poetry
        poetry_deps = (
            data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
        )
        for name in poetry_deps:
            if name == "python":
                continue
            deps.add(_normalize_pkg(name))
        poetry_dev = (
            data.get("tool", {}).get("poetry", {}).get("dev-dependencies", {}) or {}
        )
        for name in poetry_dev:
            deps.add(_normalize_pkg(name))

        # setuptools — explicit-list form only.
        setuptools_packages = (
            data.get("tool", {}).get("setuptools", {}).get("packages", [])
        )
        if isinstance(setuptools_packages, list):
            for name in setuptools_packages:
                if isinstance(name, str) and name:
                    deps.add(_normalize_pkg(name))

    if requirements_path and requirements_path.exists():
        for raw in requirements_path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = _extract_pkg_name(line)
            if name:
                deps.add(_normalize_pkg(name))

    return deps


# --- Layer B: internal-import resolution (slice-049 / ADR-044) ---
# Resolve the PROJECT-under-validation's OWN modules -- anchored at project_root
# (CWD by default: the same root the declared-deps arm resolves pyproject/
# requirements from, NOT this file's install location) -- by SOURCE-TREE
# EXISTENCE, never importlib.find_spec (which would resolve any installed package
# and swallow a genuinely-undeclared external import -- the AC4 false negative).
# Fail-closed: every probe is wrapped and _resolve_internal is TOTAL -- any error
# yields "not internal" so the import is still flagged; it never crashes the gate
# nor silently suppresses (must_not_defer #1).
_DEV_DEPS_CACHE: dict[str, frozenset[str]] = {}
_SCRIPT_ROOTS_CACHE: dict[str, tuple[Path, ...]] = {}


def _read_dev_deps(project_root: Path) -> frozenset[str]:
    """Normalized dev-dependency names declared in the project's requirements-dev.txt.

    Read INSIDE the resolver (not merged into the caller-supplied `declared`) so
    dev-dep resolution is caller-INDEPENDENT: scan_imports(files, set()) still
    resolves e.g. pytest (SC-084 / M-add-2). Missing file / any error -> empty.
    """
    key = str(project_root)
    cached = _DEV_DEPS_CACHE.get(key)
    if cached is not None:
        return cached
    deps: set[str] = set()
    try:
        for fname in (
            "requirements-dev.txt", "requirements_dev.txt", "dev-requirements.txt",
        ):
            f = project_root / fname
            if not f.exists():
                continue
            for raw in f.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                name = _extract_pkg_name(line)
                if name:
                    deps.add(_normalize_pkg(name))
    except Exception:
        deps = set()  # fail toward flagging: an unreadable dev-dep file trusts nothing
    frozen = frozenset(deps)
    _DEV_DEPS_CACHE[key] = frozen
    return frozen


def _internal_script_roots(project_root: Path) -> tuple[Path, ...]:
    """The project's bootstrap script roots -- dirs whose single-file modules are
    importable bare via the project's sys.path bootstrap. Discovered from the
    PROJECT root, so a non-plugin project (no scripts/ or skills/*/scripts) simply
    has none -> nothing extra is trusted. Any glob/stat error -> empty tuple
    (internal resolution then falls through to flagging -- noisy, never blind).
    """
    key = str(project_root)
    cached = _SCRIPT_ROOTS_CACHE.get(key)
    if cached is not None:
        return cached
    result: tuple[Path, ...] = ()
    try:
        roots: list[Path] = [
            project_root / "scripts", project_root / "scripts" / "lib",
        ]
        roots.extend(sorted(project_root.glob("skills/*/scripts")))
        result = tuple(r for r in roots if r.is_dir())
    except Exception:
        result = ()
    _SCRIPT_ROOTS_CACHE[key] = result
    return result


def _resolve_internal(import_top: str, project_root: Path) -> str | None:
    """Return the internal-resolution ZONE for import_top, or None if not internal.

    TOTAL: any unexpected error -> None (fail toward flagging -- must_not_defer #1).
    Zones (source-tree existence only, never find_spec):
      - "dev-dep"             declared in the project's requirements-dev.txt
      - "first-party-package" a top-level package/module under project_root
      - "sibling-script"      a single-file module under a project script root
    """
    try:
        if not import_top:
            return None
        if _normalize_pkg(import_top) in _read_dev_deps(project_root):
            return "dev-dep"
        if (project_root / import_top / "__init__.py").exists() or \
                (project_root / f"{import_top}.py").exists():
            return "first-party-package"
        for root in _internal_script_roots(project_root):
            if (root / f"{import_top}.py").exists() or \
                    (root / import_top / "__init__.py").exists():
                return "sibling-script"
        return None
    except Exception:
        # A resolution error must NOT crash the gate nor silently suppress the
        # whole layer (must_not_defer #1). Fall through to flagging.
        return None


def _check_import_resolves(
    import_top: str,
    declared: set[str],
) -> bool:
    """True if the import resolves to stdlib, declared dep, or known alias.

    EXTERNAL-only (SC-084 / M3): internal resolution is a SEPARATE arm applied by
    scan_imports AFTER this one, so the audit ledger's `via` is always the first
    resolution reason and a stdlib/declared name matching a sibling basename is
    never mislabeled internal.
    """
    if not import_top:
        return True
    if import_top in _STDLIB:
        return True
    if _normalize_pkg(import_top) in declared:
        return True
    alias = _KNOWN_ALIASES.get(import_top)
    if alias and _normalize_pkg(alias) in declared:
        return True
    return False


def scan_imports(
    file_paths: list[Path],
    declared_deps: set[str],
    project_root: Path | None = None,
    audit_sink: list[dict] | None = None,
) -> list[ImportFinding]:
    """ast-parse Python files; flag imports not in stdlib/declared/aliases/internal.

    Per import the resolution ORDER is (SC-084 / M3): EXTERNAL arm first
    (_check_import_resolves: stdlib / declared / known-alias) -> then the INTERNAL
    arm (_resolve_internal: the project's own modules, anchored at `project_root`,
    defaulting to CWD -- the project under validation, the same anchor the
    declared-deps arm uses). An import resolved as internal is recorded in
    `audit_sink` (the resolved_internal ledger) and NOT flagged; only a genuinely-
    undeclared EXTERNAL import becomes a hallucinated-import finding.
    """
    root = project_root if project_root is not None else Path(".")
    findings: list[ImportFinding] = []
    seen: set[tuple[str, int, str]] = set()
    seen_internal: set[tuple[str, int, str]] = set()

    def _record_internal(path: Path, lineno: int, top: str, via: str) -> None:
        if audit_sink is None:
            return
        key = (str(path), lineno, top)
        if key in seen_internal:
            return
        seen_internal.add(key)
        audit_sink.append({
            "path": str(path), "line": lineno, "import_name": top, "via": via,
        })

    for path in file_paths:
        if path.suffix != ".py" or not path.exists() or path.is_dir():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if _check_import_resolves(top, declared_deps):
                        continue
                    via = _resolve_internal(top, root)
                    if via is not None:
                        _record_internal(path, node.lineno, top, via)
                        continue
                    key = (str(path), node.lineno, top)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(ImportFinding(
                        path=str(path), line=node.lineno,
                        import_name=top,
                        kind="hallucinated-import",
                        severity="Important",
                        message=(
                            f"`import {alias.name}` references package "
                            f"'{top}' not declared in pyproject.toml or "
                            f"requirements.txt. Possible AI hallucination — "
                            f"if real, add to project deps."
                        ),
                    ))
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or node.module is None:
                    continue  # relative or `from . import x`
                top = node.module.split(".", 1)[0]
                if _check_import_resolves(top, declared_deps):
                    continue
                via = _resolve_internal(top, root)
                if via is not None:
                    _record_internal(path, node.lineno, top, via)
                    continue
                key = (str(path), node.lineno, top)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(ImportFinding(
                    path=str(path), line=node.lineno,
                    import_name=top,
                    kind="hallucinated-import",
                    severity="Important",
                    message=(
                        f"`from {node.module} import ...` references package "
                        f"'{top}' not declared in pyproject.toml or "
                        f"requirements.txt. Possible AI hallucination — "
                        f"if real, add to project deps."
                    ),
                ))
    return findings


def run_layers(
    slice_folder: Path,
    changed_files: list[Path],
    secrets_allowlist: Path | None = None,
    pyproject: Path | None = None,
    requirements: Path | None = None,
    skip_secrets: bool = False,
    skip_deps: bool = False,
    imports_allowlist: list[str] | None = None,
    project_root: Path | None = None,
) -> LayersResult:
    """Run both VAL-1 layers against the changed files.

    `imports_allowlist`: optional list of additional package names to treat as
    resolved by Layer B. Lenient: entries that name-normalize to empty are
    silently skipped (the CLI is the strict boundary that rejects empties).
    `project_root`: the project under validation (defaults to CWD) -- Layer B's
    internal-resolution arm resolves that project's OWN modules relative to it.
    """
    result = LayersResult()
    root = project_root if project_root is not None else Path(".")

    if not skip_secrets:
        allowlist = _read_allowlist(secrets_allowlist)
        secrets, suppressed = scan_secrets(changed_files, allowlist)
        result.secret_findings = secrets
        result.suppressed_secrets = suppressed

    if not skip_deps:
        declared = parse_declared_deps(pyproject, requirements)
        for name in imports_allowlist or []:
            normalized = _normalize_pkg(name)
            if normalized:
                declared.add(normalized)
        result.declared_deps = sorted(declared)
        sink: list[dict] = []
        result.import_findings = scan_imports(changed_files, declared, root, sink)
        result.resolved_internal = sink

    return result


def _format_human(result: LayersResult) -> str:
    out: list[str] = []
    out.append(
        f"VAL-1 layered safety checks: "
        f"{len(result.secret_findings)} secret(s), "
        f"{len(result.import_findings)} import finding(s), "
        f"{result.suppressed_secrets} suppressed (allowlisted).\n\n"
    )

    if result.secret_findings:
        out.append("Critical (Layer A — credential scan):\n")
        for f in result.secret_findings:
            out.append(
                f"  [{f.severity}] {f.path}:{f.line} "
                f"({f.pattern_name}) snippet={f.snippet}\n"
                f"    {f.message}\n"
            )
        out.append("\n")

    if result.import_findings:
        out.append("Important (Layer B — dependency hallucination):\n")
        for f in result.import_findings:
            out.append(
                f"  [{f.severity}] {f.path}:{f.line} "
                f"({f.kind}) `{f.import_name}`\n"
                f"    {f.message}\n"
            )
        out.append("\n")

    if result.resolved_internal:
        # m1/m2: keep the internal-resolution widening VISIBLE so over-suppression
        # stays auditable. Count by zone; full rows are in --json (resolved_internal).
        by_zone: dict[str, int] = {}
        for row in result.resolved_internal:
            by_zone[row.get("via", "?")] = by_zone.get(row.get("via", "?"), 0) + 1
        zones = ", ".join(f"{v} {k}" for k, v in sorted(by_zone.items()))
        out.append(
            f"Layer B: {len(result.resolved_internal)} import(s) resolved as the "
            f"project's own internal modules — not flagged ({zones}). "
            f"(audit: resolved_internal in --json)\n\n"
        )

    if not result.secret_findings and not result.import_findings:
        out.append("Clean — both layers passed.\n")

    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="validate_slice_layers",
        description="VAL-1 layered /validate-slice safety checks (v2)",
    )
    parser.add_argument(
        "--slice", type=Path, required=True,
        help="Path to the slice folder (for carry-over check)",
    )
    parser.add_argument(
        "--changed-files", nargs="*", default=[], type=Path,
        help="Files changed by this slice (Layer A walks these; Layer B parses .py)",
    )
    parser.add_argument(
        "--secrets-allowlist", type=Path, default=None,
        help="Path to .secrets-allowlist regex file (default: <vault>/.secrets-allowlist if it exists)",
    )
    parser.add_argument(
        "--pyproject", type=Path, default=None,
        help="Path to pyproject.toml (default: ./pyproject.toml if exists)",
    )
    parser.add_argument(
        "--requirements", type=Path, default=None,
        help="Path to requirements.txt (default: ./requirements.txt if exists)",
    )
    parser.add_argument(
        "--skip-secrets", action="store_true",
        help="Disable Layer A (credential scan)",
    )
    parser.add_argument(
        "--skip-deps", action="store_true",
        help="Disable Layer B (dependency hallucination check)",
    )
    parser.add_argument(
        "--imports-allowlist", action="append", default=None,
        metavar="NAME",
        help=(
            "Additional package name to treat as resolved by Layer B "
            "(repeatable). Empty / whitespace-only values are rejected."
        ),
    )
    parser.add_argument(
        "--no-carry-over", action="store_true",
        help="Accepted as a NO-OP for CLI compatibility (carry-over was removed in 3.9)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output result as JSON",
    )
    args = parser.parse_args(argv)

    # Strict CLI boundary: reject empty / whitespace-only --imports-allowlist
    # values. The Python API's run_layers stays lenient.
    if args.imports_allowlist is not None:
        cleaned = [s.strip() for s in args.imports_allowlist]
        if any(not c for c in cleaned):
            parser.error(
                "--imports-allowlist requires a non-empty package name"
            )
        args.imports_allowlist = cleaned

    slice_folder: Path = args.slice
    if not slice_folder.exists():
        sys.stderr.write(f"slice folder not found: {slice_folder}\n")
        return 2

    secrets_allowlist = args.secrets_allowlist
    if secrets_allowlist is None:
        candidate = VAULT_ROOT / ".secrets-allowlist"  # VAULT_ROOT-routed
        if candidate.exists():
            secrets_allowlist = candidate

    pyproject = args.pyproject
    if pyproject is None:
        candidate = Path("pyproject.toml")
        if candidate.exists():
            pyproject = candidate

    requirements = args.requirements
    if requirements is None:
        candidate = Path("requirements.txt")
        if candidate.exists():
            requirements = candidate

    result = run_layers(
        slice_folder=slice_folder,
        changed_files=list(args.changed_files),
        secrets_allowlist=secrets_allowlist,
        pyproject=pyproject,
        requirements=requirements,
        skip_secrets=args.skip_secrets,
        skip_deps=args.skip_deps,
        imports_allowlist=args.imports_allowlist,
        project_root=Path("."),  # the project under validation (CWD-relative,
        # the same anchor as the pyproject/requirements resolution above)
    )

    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(_format_human(result))

    has_findings = bool(result.secret_findings or result.import_findings)
    return 1 if has_findings else 0


if __name__ == "__main__":
    sys.exit(main())
