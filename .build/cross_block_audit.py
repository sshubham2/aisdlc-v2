#!/usr/bin/env python3
"""Cross-block-var + positional-arg audit for SKILL.md files.

Two bug classes (Session-4 systemic latent bug):

  (A) cross-block var: a shell var assigned in one ```bash / ```! block and
      USED in a *different* later block. Shell state does NOT persist across
      blocks (each block is a fresh Bash tool call), so the use expands empty.

  (B) positional-arg off-by-one: `$1`..`$9` / `${1...}` are 0-based Claude Code
      ARGUMENT substitutions -> `$0` is the FIRST arg, `$1` the SECOND. Any
      `$1` (esp. `${1:-default}`) read as "the first argument" is wrong.

Heuristic, intentionally noisy on (B) (every positional flagged for eyeball);
(A) is precise: a var is SAFE in a block if assigned earlier in the SAME block
(=, export, read, for, while-read, local, mapfile, array, command-sub) or is a
runtime/env name that genuinely persists.
"""
import re
import sys
from pathlib import Path

# Names that DO persist across blocks (env / hook-set / per-block runtime subs)
PERSISTENT = {
    "PY", "AI_SDLC_PY", "AI_SDLC_VAULT_ROOT", "AI_SDLC_HEAVY",
    "CLAUDE_SKILL_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_ENV_FILE",
    "CLAUDE_PROJECT_DIR", "CLAUDE_BASH_TIMEOUT", "ARGUMENTS",
    # ordinary shell/env builtins
    "HOME", "PATH", "PWD", "OLDPWD", "USER", "USERNAME", "TMPDIR", "TMP",
    "TEMP", "SHELL", "HOSTNAME", "LANG", "LC_ALL", "PYTHONUTF8",
    "PYTHONIOENCODING", "IFS", "RANDOM", "LINENO", "SECONDS", "HOSTTYPE",
    "BASH", "BASH_VERSION", "FUNCNAME", "EDITOR", "PAGER", "COLUMNS",
}

# A fenced block we care about: ```bash ... ``` or ```! ... ```  (also ```sh).
# Allow leading whitespace: fenced blocks nested under list items are indented,
# and a column-0-only match silently skips them (e.g. diagnose's write_pass block).
# NOTE: `!`-injection fences (```!) end in a non-word char, so a trailing \b never
# matches (no word boundary between `!` and the newline) — use a negative lookahead so
# bash/sh/! are all detected without swallowing `bashfoo`-style infostrings.
FENCE_RE = re.compile(r"^\s*```(\s*)(bash|sh|!)(?![A-Za-z0-9_])", re.I)
FENCE_END_RE = re.compile(r"^\s*```\s*$")

# assignment forms that introduce a var name within a block
ASSIGN_RES = [
    re.compile(r"^\s*(?:export\s+|local\s+|declare\s+(?:-[a-zA-Z]+\s+)*|readonly\s+)?"
               r"([A-Za-z_][A-Za-z0-9_]*)\s*="),          # VAR=  / export VAR=
    re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*$"),  # export VAR (no =)
    re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b"),    # for VAR in
    re.compile(r"\bread\s+(?:-[a-zA-Z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)"),  # read VAR / while read VAR
    re.compile(r"\bmapfile\s+(?:-[a-zA-Z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\breadarray\s+(?:-[a-zA-Z]+\s+)*([A-Za-z_][A-Za-z0-9_]*)"),
]
# multiple `read A B C`
READ_MULTI_RE = re.compile(r"\b(?:while\s+)?read\b((?:\s+-[a-zA-Z]+)*)((?:\s+[A-Za-z_][A-Za-z0-9_]*)+)")

# var USES: $VAR or ${VAR...}
USE_BRACE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\b")
USE_BARE_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\b")

# positional arg uses: $1.. / ${1..} / ${1:-..}  (digit-led)  -- NOT $0
POS_BRACE_RE = re.compile(r"\$\{([0-9]+)\b")
POS_BARE_RE = re.compile(r"\$([0-9]+)\b")


def collect_assigns(line: str, into: set) -> None:
    for rx in ASSIGN_RES:
        for m in rx.finditer(line):
            into.add(m.group(1))
    m = READ_MULTI_RE.search(line)
    if m:
        for tok in m.group(2).split():
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
                into.add(tok)


def strip_noise(line: str) -> str:
    # drop single-quoted spans (no expansion inside) and trailing comments
    line = re.sub(r"'[^']*'", "''", line)
    # a comment starting with # not inside an expansion (best-effort)
    line = re.sub(r"(^|\s)#.*$", r"\1", line)
    return line


def audit_file(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    blocks = []          # list of dict(start_line, kind, lines=[(lineno, text)])
    in_block = False
    cur = None
    for i, raw in enumerate(lines, 1):
        if not in_block:
            m = FENCE_RE.match(raw)
            if m:
                in_block = True
                cur = {"start": i, "kind": m.group(2), "lines": []}
            continue
        if FENCE_END_RE.match(raw):
            in_block = False
            blocks.append(cur)
            cur = None
            continue
        cur["lines"].append((i, raw))
    if in_block and cur:
        blocks.append(cur)  # unterminated, still audit

    # PASS 1: assignments per block
    assigns_per_block = []
    for b in blocks:
        s = set()
        for _, text in b["lines"]:
            collect_assigns(strip_noise(text), s)
        assigns_per_block.append(s)

    cross = []   # (block_idx, lineno, var, "first assigned in block #j")
    positional = []  # (block_idx, lineno, token, text)

    for bi, b in enumerate(blocks):
        seen_in_block = set()
        for lineno, raw in b["lines"]:
            text = strip_noise(raw)
            # record assignments BEFORE uses on the same line are evaluated as
            # "available" only if assignment precedes — but simplest: collect
            # this line's assignments first, treat as available from this line on.
            # positional args
            for rx in (POS_BRACE_RE, POS_BARE_RE):
                for m in rx.finditer(text):
                    positional.append((bi, lineno, m.group(0), raw.strip()))
            # var uses
            used = set()
            for rx in (USE_BRACE_RE, USE_BARE_RE):
                for m in rx.finditer(text):
                    used.add(m.group(1))
            line_assigns = set()
            collect_assigns(text, line_assigns)
            for v in used:
                if v in PERSISTENT or v in line_assigns or v in seen_in_block:
                    continue
                # is it assigned anywhere earlier in THIS block?
                if v in assigns_per_block[bi]:
                    # assigned later in same block (forward ref) -> still suspect
                    # but most likely fine if defined above; we already checked
                    # seen_in_block. If not yet seen, it's a forward/within ok-ish.
                    continue
                # assigned in a DIFFERENT block?
                origin = [j for j, a in enumerate(assigns_per_block) if v in a]
                if origin:
                    cross.append((bi, lineno, v, origin))
                # else: undefined entirely (env we don't know / typo) -> skip
            seen_in_block |= line_assigns

    return blocks, cross, positional


def main(argv):
    files = [Path(a) for a in argv[1:]]
    any_cross = False
    for f in files:
        blocks, cross, positional = audit_file(f)
        rel = f.as_posix()
        if not cross and not positional:
            print(f"CLEAN  {rel}  ({len(blocks)} blocks)")
            continue
        print(f"\n===== {rel}  ({len(blocks)} blocks) =====")
        if cross:
            any_cross = True
            print("  [A] CROSS-BLOCK VAR USE (var assigned in a different block):")
            for bi, lineno, var, origin in cross:
                ob = ", ".join(f"#{o} @L{blocks[o]['start']}" for o in origin)
                print(f"      L{lineno}  block#{bi}(@L{blocks[bi]['start']})  ${var}"
                      f"   <- assigned in block(s) {ob}")
        if positional:
            print("  [B] POSITIONAL ARG (0-based: $1 = SECOND arg):")
            for bi, lineno, tok, text in positional:
                print(f"      L{lineno}  block#{bi}  {tok}   |  {text[:90]}")
    print("\n--- summary ---")
    print(f"files audited: {len(files)}   cross-block hits: "
          f"{'YES' if any_cross else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
