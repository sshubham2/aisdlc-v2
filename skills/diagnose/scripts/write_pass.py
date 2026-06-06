"""write_pass.py — orchestrator-side writer for /diagnose subagent output.

Slice-001 / ADR-001. Replaces the prior "subagent writes its own files"
contract with: subagent returns three 4-backtick fenced blocks in its
result message; main thread invokes this helper with --raw-file pointing
to the subagent's raw text; helper parses, normalizes, validates, and
writes the three pass output files.

Block delimiters use 4-backtick outer fences (per critique B1) so subagent
prose may freely contain nested 3-backtick fences (```bash, ```yaml, etc.)
without parser collisions. Closing fence matching follows CommonMark §4.5
length-distinguished rule.

Exit codes:
  0  all three files written cleanly
  1  validation failure (missing required field, empty section/summary,
     irrecoverably malformed finding entry)
  2  parse failure (missing fenced block, malformed YAML inside findings
     fence, empty raw file)

Per critique M4, only --raw-file is supported (no stdin path). The
orchestrator always saves the subagent's raw text to a tmp file before
invoking this helper.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# Import normalize_finding + REQUIRED_FIELDS from the sibling assemble.py.
# When invoked as a script (`python skills/diagnose/write_pass.py ...`),
# the script's own directory is on sys.path so this resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assemble import REQUIRED_FIELDS, normalize_finding  # noqa: E402


_FENCE_OPEN = re.compile(r"^(`{4,})(section|findings|summary)\s*$")


def _extract_blocks(raw: str) -> dict[str, str]:
    """Extract section/findings/summary blocks from 4-backtick fenced text.

    Returns a dict {block_name: content_str}. Missing blocks are absent
    from the dict; the caller decides whether that's an error.

    Closing fence: a line of >=N backticks where N is the opener's count
    (CommonMark length-distinguished closing fence). This lets inner
    content contain ```bash``` (3-backtick) without collision.
    """
    blocks: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        opener_len = len(m.group(1))
        block_name = m.group(2)
        # Find closing fence: line of >=opener_len backticks (only backticks)
        j = i + 1
        close_pat = re.compile(rf"^`{{{opener_len},}}\s*$")
        while j < len(lines):
            if close_pat.match(lines[j]):
                break
            j += 1
        # j is either the closing line index, or len(lines) if unterminated
        content = "\n".join(lines[i + 1 : j])
        blocks[block_name] = content
        i = j + 1
    return blocks


def _err(msg: str, code: int) -> int:
    """Print to stderr and return exit code."""
    print(msg, file=sys.stderr)
    return code


def write_pass(pass_name: str, out_dir: Path, raw_text: str) -> int:
    """Parse raw subagent text and write three pass files. Return exit code."""
    if not raw_text.strip():
        return _err(f"[{pass_name}] empty --raw-file content", 2)

    blocks = _extract_blocks(raw_text)
    expected = {"section", "findings", "summary"}
    missing_fences = expected - blocks.keys()
    if missing_fences:
        return _err(
            f"[{pass_name}] missing fenced block(s): "
            f"{sorted(missing_fences)} (expected 4-backtick "
            f"`section`, `findings`, `summary`)",
            2,
        )

    # Validate non-empty section + summary (findings can be empty)
    if not blocks["section"].strip():
        return _err(f"[{pass_name}] `section` block is empty", 1)
    if not blocks["summary"].strip():
        return _err(f"[{pass_name}] `summary` block is empty", 1)

    # Parse findings YAML
    findings_text = blocks["findings"].strip()
    if not findings_text or findings_text in ("[]", "null"):
        findings: list = []
    else:
        try:
            parsed = yaml.safe_load(findings_text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                return _err(
                    f"[{pass_name}] findings YAML parse failure at "
                    f"line {mark.line + 1}, column {mark.column + 1}: {exc}",
                    2,
                )
            return _err(
                f"[{pass_name}] findings YAML parse failure (line/column "
                f"unknown): {exc}",
                2,
            )
        if parsed is None:
            findings = []
        elif isinstance(parsed, list):
            findings = parsed
        elif isinstance(parsed, dict) and "findings" in parsed:
            # tolerate {findings: [...]} top-level wrapper
            wrapped = parsed["findings"]
            findings = wrapped if isinstance(wrapped, list) else []
        else:
            return _err(
                f"[{pass_name}] findings block must be a YAML list (or `[]`); "
                f"got {type(parsed).__name__}",
                2,
            )

    # Normalize + validate each finding
    normalized: list[dict] = []
    for idx, raw_finding in enumerate(findings):
        if not isinstance(raw_finding, dict):
            return _err(
                f"[{pass_name}] findings[{idx}] is not a dict: "
                f"{type(raw_finding).__name__}",
                1,
            )
        norm = normalize_finding(raw_finding, pass_name)
        if norm is None:
            return _err(
                f"[{pass_name}] findings[{idx}] is irrecoverably malformed "
                f"(no usable evidence after normalization)",
                1,
            )
        missing = [f for f in REQUIRED_FIELDS if f not in norm]
        if missing:
            return _err(
                f"[{pass_name}] findings[{idx}] missing required fields "
                f"after normalization: {missing} "
                f"(id={norm.get('id', '<no-id>')!r}, "
                f"title={norm.get('title', '<no-title>')!r})",
                1,
            )
        normalized.append(norm)

    # Write the three files
    sections_dir = out_dir / "sections"
    findings_dir = out_dir / "findings"
    summary_dir = out_dir / "summary"
    sections_dir.mkdir(parents=True, exist_ok=True)
    findings_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    section_path = sections_dir / f"{pass_name}.md"
    findings_path = findings_dir / f"{pass_name}.yaml"
    summary_path = summary_dir / f"{pass_name}.md"

    section_text = blocks["section"].strip() + "\n"
    summary_text = blocks["summary"].strip() + "\n"

    section_path.write_text(section_text, encoding="utf-8")
    summary_path.write_text(summary_text, encoding="utf-8")
    findings_yaml = yaml.safe_dump(
        normalized,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    if not normalized:
        findings_yaml = "[]\n"
    findings_path.write_text(findings_yaml, encoding="utf-8")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write /diagnose pass output files from a subagent's raw response. "
            "Subagent returns 4-backtick fenced section/findings/summary "
            "blocks; this helper parses, normalizes, validates, and writes."
        ),
    )
    parser.add_argument(
        "--pass",
        dest="pass_name",
        required=True,
        help="Pass name, e.g., 03a-dead-code (used as filename prefix).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="diagnose-out/ directory; will create sections/, findings/, summary/.",
    )
    parser.add_argument(
        "--raw-file",
        type=Path,
        required=True,
        help="Path to the subagent's raw response text. Stdin not supported (M4).",
    )
    args = parser.parse_args(argv)

    if not args.raw_file.exists():
        return _err(f"--raw-file does not exist: {args.raw_file}", 2)

    raw_text = args.raw_file.read_text(encoding="utf-8")
    return write_pass(args.pass_name, args.out, raw_text)


if __name__ == "__main__":
    sys.exit(main())
