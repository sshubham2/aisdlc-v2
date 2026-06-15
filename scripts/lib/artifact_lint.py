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
from scripts.lib.risk_status import RISK_STATUSES

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
    # slice-004 (ADR-002): assumption-level spike fields. spike_status stays the BINARY
    # gate — `conditional` is deliberately NOT allowed here; the ternary verdict lives in
    # the sibling spike_verdict. Legacy rows lack both fields (absent/None passes).
    ("slice-candidates", "candidates[].assumptions[].spike_status"):
        frozenset({"unproven", "proving", "proven", "failed"}),
    ("slice-candidates", "candidates[].assumptions[].spike_verdict"):
        frozenset({"go", "no-go", "conditional"}),
    # a no-go assumption never passes through into a design's assumptions_proven
    ("design", "assumptions_proven[].verdict"): frozenset({"go", "conditional"}),
    ("code-review", "verdict"): frozenset({"clean", "needs-fixes", "blocked"}),
    # Canonical risk-status set sourced from the ONE shared definition (slice-010 / ADR-008) —
    # NOT a hand-kept literal. Reconciles with risk_register_audit._ALLOWED_STATUSES (same import).
    ("risk-register", "risks[].status"): RISK_STATUSES,
    ("mission-brief", "architectural_layers[].status"): frozenset({"pending", "exercised"}),
    ("mission-brief", "exploratory_charters[].status"):
        frozenset({"pending", "in-progress", "completed", "deferred"}),
}

# Top-level keys that appear in a canonical example but are genuinely OPTIONAL on a
# real artifact (array-shaped optionals can't carry the dict-with-`_note` marker). Keyed
# by artifact type. e.g. the variant blocks a mission-brief carries only when opted in.
OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "mission-brief": frozenset({"architectural_layers", "exploratory_charters"}),
    # slice-001: the spike->design evidence cross-ref is array-shaped, so it NEEDS this
    # entry — without it every design.json that omits the block fails lint (critique B1).
    "design": frozenset({"assumptions_proven"}),
    # slice-004: structured constraints[] on a spike artifact (non-empty iff
    # verdict=conditional) — array-shaped optional; legacy spike files lack it.
    "spike": frozenset({"constraints"}),
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


# slice-004 (ADR-002): per-row verdict<->constraints co-constraint. The flat _walk
# above cannot deliver this — it FLATTENS list hops into leaf values, discarding which
# row a value came from, so a record with one conditional-without-constraints row and
# one go-with-constraints row would hide BOTH problems from any count-based pairing.
# This check walks list ELEMENTS instead (row identity preserved).
# (artifact_key, list-parent path; "" = the top-level object) -> (verdict_field,
# constraints_field). Rules per element:
#   verdict == "conditional"  => constraints MUST be a non-empty LIST (type-checked:
#                                a malformed vault_edit --set can store a bare string);
#   any other verdict present => a non-empty constraints list is a STALE LEAK (writers
#                                re-set constraints=[] on non-conditional writes).
CO_CONSTRAINTS: dict[tuple[str, str], tuple[str, str]] = {
    ("slice-candidates", "candidates[].assumptions[]"): ("spike_verdict", "spike_constraints"),
    ("design", "assumptions_proven[]"): ("verdict", "constraints"),
    ("spike", ""): ("verdict", "constraints"),
}


def _walk_elements(data, dotted: str) -> list:
    """Like _walk, but returns the list ELEMENTS (dicts) at an `a[].b[]`-style path —
    row identity preserved. "" resolves to the top-level object itself."""
    if not dotted:
        return [data] if isinstance(data, dict) else []
    head, _, rest = dotted.partition(".")
    is_list = head.endswith("[]")
    head = head[:-2] if is_list else head
    if not isinstance(data, dict) or head not in data:
        return []
    nxt = data[head]
    if not is_list:
        return _walk_elements(nxt, rest)
    if not isinstance(nxt, list):
        return []
    if not rest:
        return [item for item in nxt if isinstance(item, dict)]
    out: list = []
    for item in nxt:
        out.extend(_walk_elements(item, rest))
    return out


def _co_constraint_violations(data: dict, key: str, label: str) -> list[str]:
    v: list[str] = []
    for (ak, parent), (vf, cf) in CO_CONSTRAINTS.items():
        if ak != key:
            continue
        loc = parent or "<top-level>"
        for row in _walk_elements(data, parent):
            verdict = row.get(vf)
            cons = row.get(cf)
            if verdict == "conditional":
                if not isinstance(cons, list) or not cons:
                    v.append(f"{label}: {loc} row with {vf}='conditional' must carry a "
                             f"non-empty list `{cf}` (got {type(cons).__name__})")
            elif verdict is not None:
                if isinstance(cons, list) and cons:
                    v.append(f"{label}: {loc} row with {vf}={verdict!r} carries non-empty "
                             f"`{cf}` -- stale constraints must be cleared to []")
    return v


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
    v.extend(_co_constraint_violations(data, key, label))
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


# ── 4.5 artifact version-skew detection (reader side) ────────────────────────────

def _plugin_version() -> str | None:
    """The running plugin's version from .claude-plugin/plugin.json (or None)."""
    try:
        with open(_REPO / ".claude-plugin" / "plugin.json", encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _ver_tuple(v) -> tuple:
    """'2.22.4' -> (2, 22, 4); leading-digit-tolerant, missing parts -> 0."""
    out = []
    for part in str(v).split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _schema_major(schema) -> int | None:
    """The integer N from an `aisdlc/<key>@N` schema tag, or None."""
    if not isinstance(schema, str) or "@" not in schema:
        return None
    digits = ""
    for ch in schema.split("@", 1)[1].strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def schema_skew(data: dict, key: str, example: dict, plugin_ver: str | None) -> list[str]:
    """Non-fatal version-skew WARNings (4.5): the artifact's `_schema` major is NEWER than the
    one this plugin's canonical example defines (a vault written by a newer plugin), or its
    `_plugin_version` is newer than the running plugin. Older-than-current is the benign
    archived-artifact case and is NOT warned."""
    warns: list[str] = []
    if not isinstance(data, dict):
        return warns
    got = _schema_major(data.get("_schema"))
    known = _schema_major(example.get("_schema"))
    if got is not None and known is not None and got > known:
        warns.append(f"`_schema` is {key}@{got} but this plugin knows {key}@{known} — artifact "
                     f"written by a NEWER plugin; upgrade the plugin (vault/plugin skew).")
    stamped = data.get("_plugin_version")
    if stamped and plugin_ver and _ver_tuple(stamped) > _ver_tuple(plugin_ver):
        warns.append(f"`_plugin_version` {stamped} is newer than the running plugin {plugin_ver} "
                     f"— artifact written by a newer plugin (vault/plugin skew).")
    return warns


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
    warnings: list[str] = []          # 4.5 version-skew (non-fatal)
    plugin_ver = _plugin_version()
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
            for w in schema_skew(data, key, examples[key], plugin_ver):
                warnings.append(f"{f}: {w}")

    if args.json:
        print(json.dumps({"checked": checked, "violations": violations, "warnings": warnings}, indent=2))
    else:
        if violations:
            print(f"artifact_lint: {len(violations)} violation(s) over {checked} artifact(s):")
            for vi in violations:
                print(f"  - {vi}")
        elif not warnings:
            print(f"artifact_lint: clean. {checked} artifact(s) conform to schema-by-example.")
        if warnings:  # non-fatal: surfaced, but never fails the gate
            print(f"artifact_lint: {len(warnings)} version-skew warning(s) (non-fatal):")
            for w in warnings:
                print(f"  ! {w}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
