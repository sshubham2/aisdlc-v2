"""Guard: no canonical example / authoring template teaches the now-rejected bare-`pytest`
machine_cmd form (slice-046 / SC-081, AC4 — critique m1 + M-add-2).

After ADR-035, SCMD-1 rejects a bare `pytest tests/...` console-script. If the canonical examples or
the live /repro authoring template still modelled that form, the rule would contradict — and keep
REGENERATING — its own trigger (M-add-2: the template is a recurrence generator, worse than stale
drift). This test fails if a `machine_cmd` value beginning with bare `pytest` survives in any of the
reachable example/template/docstring sites.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib.runnable_command import NON_PORTABLE_CONSOLE_SCRIPT, classify

# Every site the dual-Critic named (critique m1: 3 examples; M-add-2: the /repro template + docstrings;
# code-review m1: the SRSC-1 runner docstring; code-review m2: the /repro confirm command).
_SITES = [
    "schemas/artifact-examples.json",
    "skills/repro/examples/shippability.json",
    "skills/reflect/examples/shippability.json",
    "skills/repro/SKILL.md",
    "skills/validate-slice/scripts/shippability_decoupling_audit.py",
    "skills/validate-slice/scripts/shippability_path_audit.py",
    "skills/validate-slice/scripts/shippability_runner.py",
]

# Two shapes of the rejected bare-`pytest` form in a doc/example/template:
#   (a) a machine_cmd VALUE whose command begins with bare `pytest` (no interpreter prefix);
#   (b) a backtick-fenced `pytest tests/...` EXAMPLE (the "example form" docstring/prose shape that
#       critique m1 / code-review m1 flagged) — a `pytest` immediately preceded by a backtick and
#       targeting tests/. The portable `<interp> -m pytest tests/...` form is never matched (its
#       `pytest` is preceded by `-m `, not a backtick), nor is a backtick-quoted `pytest` mentioned
#       on its own (`` `pytest` ``) without a tests/ target.
_BARE_PATTERNS = [
    re.compile(r'"machine_cmd"\s*:\s*"pytest\b'),
    re.compile(r"`pytest\s+tests/"),
]


def test_canonical_examples_are_interp_anchored():
    offenders = []
    for rel in _SITES:
        p = REPO_ROOT / rel
        assert p.exists(), f"guard site missing (path drift?): {rel}"
        text = p.read_text(encoding="utf-8")
        for pat in _BARE_PATTERNS:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{rel}:{line}")
    assert not offenders, (
        "bare-`pytest` (machine_cmd value or backtick example) still present in canonical "
        f"examples/templates/docstrings — would re-teach the SCMD-1-rejected form: {sorted(offenders)}"
    )


def test_json_example_machine_cmds_classify_portable():
    """The three JSON shippability examples' machine_cmd values are portable per the validator."""
    import json
    for rel in ("schemas/artifact-examples.json",
                "skills/repro/examples/shippability.json",
                "skills/reflect/examples/shippability.json"):
        data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for cmd in _iter_machine_cmds(data):
            assert classify(cmd).klass != NON_PORTABLE_CONSOLE_SCRIPT, (
                f"{rel} models a non-portable machine_cmd: {cmd!r}"
            )


def _iter_machine_cmds(obj):
    """Yield every `machine_cmd` string anywhere in a nested JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "machine_cmd" and isinstance(v, str) and v.strip():
                yield v
            else:
                yield from _iter_machine_cmds(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_machine_cmds(item)
