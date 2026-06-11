"""artifact_lint.py — enforce schema-by-example on vault JSON artifacts (3.18.7).

Driven DIRECTLY by schemas/artifact-examples.json: each artifact type's canonical
example defines (a) its `_schema` tag and (b) its required top-level keys — every
non-`_`-prefixed key whose value is NOT an optional-marker object (a dict carrying a
`_note`, the convention used for tournament / cross_domain_transfer). This lints an
artifact against that shape plus a small KNOWN-ENUMS table:
  - it MUST carry a `_schema` tag;
  - it MUST have every required top-level key the canonical example has;
  - known enum fields (mode / risk_tier / verdict / result / dispositions[].action …)
    must hold an allowed value.

Converts schema-by-example from decorative to ENFORCED — the `--self-check` mode lints
the canonical examples themselves, so a bad enum in artifact-examples.json (the 1.4
`action: fix-now` bug) fails CI before it ships.

Modes:
  --self-check         lint every canonical example in artifact-examples.json (CI).
  <file> [<file>...]   lint given vault artifact JSON files (type inferred from `_schema`,
                       or forced with --type <key>).
  --dir <d> --type <k> lint every *.json in <d> as artifact type <k>.

Exit: 0 clean · 1 violations · 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent  # scripts/lib/X.py -> repo root
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.lib import _stdout

_EXAMPLES_PATH = _REPO / "schemas" / "artifact-examples.json"

# (artifact_key | "*", dotted-path) -> allowed values. Path supports `a.b` and the
# list-of-dicts hop `a[].b`. "*" applies to every artifact type. Kept deliberately
# small — the load-bearing enums (incl. the 1.4 triage `action` case).
_PIPELINE_MODE = frozenset({"minimal", "standard", "heavy"})
KNOWN_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    # `mode` is the pipeline mode ONLY on these artifacts; changelog.mode (merge/push/
    # none) and user-test.mode (prototype/mockup/working-slice) are different fields.
    ("triage", "mode"): _PIPELINE_MODE,
    ("concept", "mode"): _PIPELINE_MODE,
    ("mission-brief", "mode"): _PIPELINE_MODE,
    ("slice-index", "mode"): _PIPELINE_MODE,
    ("mission-brief", "risk_tier"): frozenset({"low", "medium", "high"}),
    ("critique", "verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    ("critique", "triage.verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    ("critique", "triage.dispositions[].action"):
        frozenset({"accepted-fixed", "accepted-pending", "overridden", "deferred", "escalated"}),
    ("critique-review", "verdict"): frozenset({"accept", "adjust", "extend"}),
    ("validation", "result"): frozenset({"pass", "fail", "partial"}),
    ("validation", "criteria[].result"): frozenset({"pass", "fail", "partial"}),
    ("spike", "verdict"): frozenset({"go", "no-go", "conditional"}),
    ("code-review", "verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    ("risk-register", "risks[].status"): frozenset({"open", "mitigated", "accepted", "closed", "retired"}),
    ("mission-brief", "architectural_layers[].status"): frozenset({"pending", "exercised"}),
    ("mission-brief", "exploratory_charters[].status"):
        frozenset({"pending", "in-progress", "completed", "deferred"}),
}

# Top-level keys that appear in a canonical example but are genuinely OPTIONAL on a
# real artifact (array-shaped optionals can't carry the dict-with-`_note` marker). Keyed
# by artifact type. e.g. the variant blocks a mission-brief carries only when opted in.
OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "mission-brief": frozenset({"architectural_layers", "exploratory_charters"}),
}


def _load_examples() -> dict:
    with open(_EXAMPLES_PATH, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def _required_keys(example: dict, key: str) -> list[str]:
    """Non-`_` top-level keys, excluding optional-marker objects (dict with `_note`) and
    keys listed as optional for this artifact type (OPTIONAL_KEYS — array optionals)."""
    optional = OPTIONAL_KEYS.get(key, frozenset())
    return [k for k, v in example.items()
            if not k.startswith("_")
            and k not in optional
            and not (isinstance(v, dict) and "_note" in v)]


def _walk(data, dotted: str) -> list:
    """Resolve a dotted path with `[]` list hops -> the list of leaf values present."""
    if not dotted:
        return [data]
    head, _, rest = dotted.partition(".")
    is_list = head.endswith("[]")
    head = head[:-2] if is_list else head
    if not isinstance(data, dict) or head not in data:
        return []
    nxt = data[head]
    if is_list:
        if not isinstance(nxt, list):
            return []
        out: list = []
        for item in nxt:
            out.extend(_walk(item, rest))
        return out
    return _walk(nxt, rest)


def lint_artifact(data: dict, key: str, example: dict, label: str) -> list[str]:
    """Return a list of violation strings ([] = clean)."""
    v: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: top level is not a JSON object"]
    if not data.get("_schema"):
        v.append(f"{label}: missing required `_schema` tag")
    for rk in _required_keys(example, key):
        if rk not in data:
            v.append(f"{label}: missing required key `{rk}` (per the {key} example)")
    for (ak, path), allowed in KNOWN_ENUMS.items():
        if ak not in ("*", key):
            continue
        for val in _walk(data, path):
            if val is not None and val not in allowed:
                v.append(f"{label}: `{path}` = {val!r} not in {sorted(allowed)}")
    return v


def _type_for(data: dict, examples: dict, forced: str | None) -> str | None:
    if forced:
        return forced if forced in examples else None
    schema = (data.get("_schema") or "") if isinstance(data, dict) else ""
    # `_schema` is "aisdlc/<key>@N" -> <key>
    if schema.startswith("aisdlc/"):
        key = schema[len("aisdlc/"):].split("@")[0]
        if key in examples:
            return key
    return None


def main(argv: list[str] | None = None) -> int:
    _stdout.reconfigure_stdout_utf8()
    p = argparse.ArgumentParser(
        prog="artifact_lint",
        description="Enforce schema-by-example (required keys + known enums) on vault JSON artifacts (3.18.7).")
    p.add_argument("files", nargs="*", type=Path, help="artifact JSON file(s) to lint")
    p.add_argument("--self-check", action="store_true",
                   help="lint every canonical example in artifact-examples.json (CI)")
    p.add_argument("--dir", type=Path, default=None, help="lint every *.json in this dir")
    p.add_argument("--type", default=None, help="force the artifact type (example key)")
    p.add_argument("--skip-unknown", action="store_true",
                   help="skip (don't fail) files whose artifact type can't be determined "
                        "— for a dir sweep that includes files this lint doesn't model")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    examples = _load_examples()
    violations: list[str] = []
    checked = 0

    if args.self_check:
        for key, ex in examples.items():
            checked += 1
            violations.extend(lint_artifact(ex, key, ex, f"example:{key}"))
    else:
        targets = list(args.files)
        if args.dir is not None:
            targets.extend(sorted(args.dir.glob("*.json")))
        if not targets:
            sys.stderr.write("artifact_lint: pass file(s), --dir, or --self-check\n")
            return 2
        for f in targets:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                violations.append(f"{f}: unreadable / invalid JSON ({exc})")
                continue
            key = _type_for(data, examples, args.type)
            if key is None:
                if args.skip_unknown:
                    continue  # a dir sweep hits files this lint doesn't model — skip, don't fail
                violations.append(f"{f}: cannot determine artifact type "
                                  f"(no recognized `_schema`; pass --type)")
                continue
            checked += 1
            violations.extend(lint_artifact(data, key, examples[key], str(f)))

    if args.json:
        print(json.dumps({"checked": checked, "violations": violations}, indent=2))
    elif violations:
        print(f"artifact_lint: {len(violations)} violation(s) over {checked} artifact(s):")
        for vi in violations:
            print(f"  - {vi}")
    else:
        print(f"artifact_lint: clean. {checked} artifact(s) conform to schema-by-example.")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
