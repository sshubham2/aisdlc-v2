# Runbook — one-time live rename: `uat` → `aisdlc-uat`

**Slice:** slice-061 / SC-114 · **ADR:** ADR-058 (see the M1 mechanism correction in the slice's
`design.json` `critique_corrections` — the ADR is sealed/append-only, so the corrected mechanism
lives here + in design.json, superseding ADR-058's original `push --delete` MIGRATE step).

## Why this is a separate, deliberate step

The code change (EXPAND) is already live: `resolve_integration_branch()` probes `aisdlc-uat` first,
then a genesis-gated legacy `uat`, so **the repo keeps working on `uat` until this rename lands** and
keeps working the instant it lands on `aisdlc-uat`. This runbook is the **MIGRATE** step of the
Parallel Change — run it ONCE, at `/commit-slice`, with explicit go-ahead, OUTSIDE the worktree build.

> The AC5 spike (`spike-aisdlc-uat-live-rename`) rehearsed a rename on a **throwaway sandbox origin**
> and proved the *mechanics* only — the release-genesis descent invariant + tag survive the rename.
> A sandbox clone has **no real open PRs and no branch protection**, so it CANNOT prove the
> PR-retarget / protection-migration behaviour. That is exactly why the live step uses GitHub's
> **native branch-rename** (not push-new + delete-old) and is gated on the real-origin preconditions
> below.

## Mechanism — GitHub NATIVE branch-rename (M1)

Use GitHub's native rename, **never** `git push origin --delete uat`:

- GitHub's native rename **auto-retargets every open PR** whose base is `uat` → `aisdlc-uat` and
  **migrates branch-protection rules** to the new name.
- `git push origin --delete uat` instead **CLOSES** any open PR based on `uat` (no auto-retarget) and
  drops protection — silent data loss under the parallel-slice model, where sibling PRs may be based
  on `origin/uat` at rename time.

## Pre-rename preconditions (verify on the REAL origin — all must hold)

```bash
# (a) NO open PRs based on uat (native rename retargets them, but confirm intent first):
gh pr list --base uat --state open
#     -> expect empty. If any exist, they WILL be auto-retargeted to aisdlc-uat by the native rename;
#        confirm that is intended for each before proceeding.

# (b) NO sibling slice mid `/commit-slice --merge` (no concurrent advance of origin/uat).

# (c) origin/HEAD still points at master (so re-pointing origin/HEAD is a genuine no-op here):
git symbolic-ref refs/remotes/origin/HEAD        # -> refs/remotes/origin/master

# (d) release-genesis intact before the rename:
git rev-parse --verify --quiet release-genesis^{commit}
```

## Rename (run once, with explicit go-ahead)

```bash
OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

# 1. GitHub native rename on origin (auto-retargets open PRs + migrates protection):
gh api -X POST "repos/$OWNER_REPO/branches/uat/rename" -f new_name='aisdlc-uat'

# 2. Local: rename the branch + re-point its upstream:
git branch -m uat aisdlc-uat
git fetch origin
git branch --set-upstream-to=origin/aisdlc-uat aisdlc-uat

# 3. If this working copy was checked out on uat, it is now on aisdlc-uat (git branch -m renames in place).
```

`origin/HEAD → master` (precondition c), so **no `origin/HEAD` re-point is needed**. If a future repo
has `origin/HEAD → uat`, also run `gh api -X PATCH "repos/$OWNER_REPO" -f default_branch='aisdlc-uat'`
(NOT applicable here — master is the served default).

## Post-rename verification (must all pass)

```bash
git rev-parse --verify --quiet release-genesis^{commit}                       # tag intact
git merge-base --is-ancestor release-genesis aisdlc-uat && echo "descent OK"  # genesis descent holds
$PY scripts/lib/release_advance_audit.py --root . --json | \
    python -c "import json,sys; d=json.load(sys.stdin); assert d['clean'] and d.get('integration_branch_checked')=='aisdlc-uat', d; print('release_advance_audit clean on aisdlc-uat')"
$PY scripts/lib/_git_default_branch.py --integration --write --repo-root .    # -> aisdlc-uat, exit 0
```

Expect: tag intact, descent OK, audit clean naming `aisdlc-uat`, write-guard resolves `aisdlc-uat`.
The genesis-gated legacy-`uat` probe means any not-yet-updated clone still resolves correctly until it
renames too.

## Rollback (if needed)

The rename is reversible under dual-read (the resolver still accepts legacy `uat` in an ai-sdlc-managed
repo): `gh api -X POST "repos/$OWNER_REPO/branches/aisdlc-uat/rename" -f new_name='uat'` + `git branch -m
aisdlc-uat uat`. The spike proved the forward + rollback legs both preserve the release-genesis descent
and tag.
