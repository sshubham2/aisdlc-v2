---
name: query-design
description: "Read-only, grounded Q&A conversation about the existing codebase. Answers questions by reading actual code via Grep, Read, and code-review-graph (CRG) queries — never from recall. Every answer cites specific file/line/symbol evidence. Changes nothing: no source edits, no vault writes, no candidate files. If the conversation surfaces a concrete actionable finding, offers (but never forces) a single delegated handoff to /slice."
when_to_use: "Trigger phrases: /query-design, 'query the design', 'ask about the codebase', 'how does X work in this repo', 'explain this subsystem', 'is my assumption about X correct'. Use to interrogate the codebase before committing to work, understand a subsystem, or sanity-check an assumption. Out-of-loop — invoke any time, not just at pipeline boundaries. Distinct from /discover (greenfield, vault-writing), /diagnose (heavyweight HTML deliverable), /pulse (one-shot macro-state), /slice-candidates (needs the diagnose round-trip)."
argument-hint: "[<question about the codebase>]"
allowed-tools: Read, Grep, Skill
---

# /query-design — Read-Only Codebase Q&A

Grounded, conversational answers about the **existing** codebase. You read; you never write.

> Vault root `<vault>/` resolves to the external store `~/.aisdlc/<project>-<hash>/` (`$AI_SDLC_VAULT_ROOT`).

## The read-only invariant (load-bearing)

While running this skill you **MUST NOT**:
- Use Write, Edit, or NotebookEdit against any file.
- Create, rename, move, or delete any file.
- Run any shell command that mutates state (`git commit`/`checkout`/`branch`, file redirection, `pip install`, formatters).
- Invoke any skill that writes (`/slice`, `/design-slice`, `/build-slice`, `/reflect`, etc.) **except** the single explicit user-accepted handoff described below.

No exceptions, no escape hatch, no "may edit if…". If answering a question seems to require a change, describe what is needed and offer the handoff instead.

## Grounding contract

Every answer **must** be grounded in actual repository evidence read this session:

1. **Locate** with Grep — find the symbol, pattern, or file.
2. **Inspect** with Read — read the actual definition, not memory.
3. **Structure** with CRG (optional) — use the `code-review-graph` MCP tools for reachability and
   blast-radius when a question involves call chains or dependencies. The REAL tool names (there is no
   `search` / `impact-radius` / `review-context` verb in CRG 2.3.x): keyword/code search →
   `mcp__code-review-graph__semantic_search_nodes_tool`; blast-radius →
   `mcp__code-review-graph__get_impact_radius_tool`; call-chain / component structure (the nearest thing to
   "review context" — no such tool exists) → `mcp__code-review-graph__query_graph` /
   `mcp__code-review-graph__traverse_graph_func`.
4. **Cite** in the answer — every substantive claim carries a `path/to/file:line`, symbol name, or ADR id.

"It works roughly like X" with no citation is not an acceptable answer. If the code does not contain enough to answer truthfully, say so explicitly and state what is undetermined — do not speculate.

**Vault reads are in-scope** (they are read-only): `<vault>/decisions/ADR-*.json` is often the ONLY place a
"why is it this way?" answer lives — read and cite ADR ids freely, along with any other vault artifact that
grounds an answer. The read-only invariant forbids *writes*, not vault *reads*.

If a doc/vault claim and the code disagree, say so and cite both. Code wins (brownfield discipline).

## Conversation loop

1. Take the user's question from `$ARGUMENTS` or the prompt.
2. Locate and read the relevant evidence (Grep → Read → CRG as needed).
3. Answer concisely with specific evidence citations.
4. Continue — follow-ups, drill-downs — read-only and grounded throughout.
5. At natural session end, apply the handoff rule below if actionable work emerged.

## Handoff (offer, never author, never force)

This skill never authors a fix, a slice, a candidate file, or a `candidates.json` entry.

- **One concrete requirement or defect** found: present a one-paragraph distilled summary and **offer** to invoke `/slice "<distilled intent>"`. State it as a declinable offer ("Want me to open a slice for this? I won't unless you say so.").
- **Multiple or structural findings**: recommend the `/diagnose` → `/slice-candidates` route; do not run it yourself.
- **User accepts**: invoke `/slice` once via the Skill tool with the distilled intent. That is the single permitted write-path.
- **User declines or no response**: end the session with zero side effects. No file is created, nothing is logged, nothing is queued.

## Error model

| Situation | Response |
|---|---|
| CRG graph absent or clearly stale | Instruct user to rebuild: `"${CRG:-code-review-graph}" build --repo .` (the `${CRG:-…}` form — on the documented Windows venv setup the bare name is off PATH; if CRG itself is missing, point at `/ai-sdlc:setup`, whose doctor resolves it). Answer from Read/Grep in the meantime. Never answer from a stale graph or recall. |
| Question unanswerable from repo evidence | Say so explicitly — "the code doesn't determine this; here's what I can see and what's undetermined." Never speculate. |
| Handoff declined | Terminate with zero side effects. |

## Critical rules

- READ-ONLY, no exceptions. The moment an answer would require a write, stop and offer the handoff instead.
- GROUND every answer in evidence read this session; cite specific files/symbols/IDs.
- OFFER the handoff; never author a slice or candidate file; never auto-invoke `/slice`.
- NEVER speculate when the repo can't answer — say what's undetermined.

## Pipeline position

- predecessor: none (out-of-loop, user-invoked any time)
- successor: `/slice` (offered, never forced — user-input gate)
- auto-advance: false
- user-input gates: handoff acceptance (user must explicitly accept before `/slice` is invoked)
