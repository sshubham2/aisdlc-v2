# skills/slice/scripts — single-skill tools for /slice

Tools used ONLY by `/slice` live here (shared tools stay in `scripts/lib/`). Referenced from SKILL.md via
`${CLAUDE_SKILL_DIR}/scripts/<name>.py`.

| script | role | ports from |
|---|---|---|
| `candidates_top.py` | read `<vault>/candidates.json`, return the top-N live candidates ranked by priority, with blocked-on-spike flags — for dynamic injection into SKILL.md | NEW (replaces v1 multi-source fan-out + `slice_queue_writer` ## Candidates regen) |
| `claim_candidate.py` | claim a candidate in `candidates.json` via vault_edit: set status/progress/`claimed_by {git_user,git_email}`/`started_at`, append `pick_log` (fail-visible on unset git identity) | ports `slice_queue_claim` + `slice_queue_writer.record_pick` (v1), retargeted from slice-queue.md to candidates.json |

Shared tools `/slice` also calls (from `scripts/lib/`, NOT copied here): `risk_register_audit`,
`_worktree_paths`, `stranded_slice_audit`.
