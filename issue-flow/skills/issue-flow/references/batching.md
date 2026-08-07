# Batching — integration branches, CI-free sub-PRs, one CI run per batch

Why: per-issue PRs into dev meant every merge forced a rebase + full CI run on every
other open PR, and dependency chains became stacks of PRs each burning CI. The batch
model spends **one CI run per batch** and resolves conflicts **once, locally**, against
a stable integration branch.

## Shapes

| Work | Batch | Integration branch | Tracking |
|---|---|---|---|
| Epic with sub-issues | the epic's ready sub-issues | `epic/<epicnum>-<slug>` | the epic issue itself (checklist in body) |
| Loose ready issues | PM-grouped, ≤ `batchSize` (default 4) | `batch/<tracknum>-<slug>` | a `type:batch` tracking issue (member checklist) |
| `type:hotfix` / urgent `priority:high` singleton | none — standalone | `issue/<n>-<slug>` off dev | the issue itself |

Grouping heuristics for loose batches: same area/subsystem first, shared files second,
then dependency chains (a chain **always** lands in one batch, sequenced). Don't pad a
batch just to hit `batchSize`; a singleton batch is fine. Don't mix an experiment-risk
issue (likely to be reverted) into a batch of safe work.

## Branch lifecycle

Create the integration branch off dev **on the remote** (no local checkout needed):

```bash
git fetch <remote>
git push <remote> <remote>/<dev>:refs/heads/epic/<n>-<slug>
```

Member branches fork the integration branch. The worker is already inside a worktree the
harness made for it, so it switches that worktree onto its branch rather than adding one
(the harness branches from the default branch, not from the member's base):

```bash
git fetch <remote>
git checkout -B issue/<m>-<slug> <remote>/epic/<n>-<slug>
```

On a **re-spawn for rework** the branch already exists and carries the PR's commits, so
the base is the branch itself — `git checkout -B issue/<m>-<slug> <remote>/issue/<m>-<slug>`.
The worker checks for the published branch before falling back to the integration branch;
pointing an existing member branch at the integration branch would discard its commits.

Sequenced members (dependency chains) launch only after their predecessor sub-merges,
so they fork the **updated** integration branch and never conflict with it.

Teardown: member branches are deleted at sub-merge (`--delete-branch`); the integration
branch is deleted at batch merge. A worker's worktree always holds commits, so the harness
never auto-removes it — the PM removes the path from the worker's completion notification
(`worktreePath`, or its verdict) at sub-merge (`git worktree remove --force`, plus
`git branch -D worktree-agent-<id>` for the branch the removal leaves) and sweeps
`git worktree list --porcelain` at batch merge, removing leftovers with `-f -f` because a
killed session leaves them locked, then `git worktree prune`.

**"An integration-branch worktree" below always means one of two things**, never
`EnterWorktree`: for the PM's own sequential work (local sub-merges, the empty CI commit)
a plain `git worktree add .claude/worktrees/<integration-branch>` driven with
`git -C <path>` — measured safe from the PM, which stays unpinned; for delegated work
(suite runs, fix workers) a subagent spawned with `isolation: "worktree"` and
`base: <remote>/<integration-branch>`, which lands there via its own
`git checkout -B <integration-branch> <remote>/<integration-branch>`.

## Keeping sub-issue PRs CI-free

Two mechanisms, layered:

1. **`[skip ci]` in the head commit message.** GitHub Actions natively skips `push` and
   `pull_request` triggered workflows when the head commit message contains `[skip ci]`
   (or `[ci skip]`); Gitea Actions does the same from Gitea 1.20. Workers append it to
   **every pushed head commit**; that keeps the draft PR CI-free through readying and
   merging too.
2. **Draft PRs.** Sub-PRs open as drafts — signals "not for dev" to humans and to any
   bot keyed on ready state. (Draft alone does **not** stop either provider — `[skip ci]`
   does the work.)

Phase 0 sanity-checks that the project's CI honors `[skip ci]`. If it can't (other
provider, or triggers that ignore it), propose **one** workflow edit to the user:

```yaml
on:
  push:
    branches-ignore: ['epic/**', 'batch/**', 'issue/**']
  pull_request:
    branches: [dev, main]        # PRs into integration branches don't match
```

If the user declines any workflow change, fall back to **no sub-PRs at all**: workers
push their branch and the PM merges it into the integration branch locally
(`git merge --no-ff issue/<m>-...` in an integration-branch worktree, push); the audit
trail is then issue comments + the branch history. Everything else in the flow is
unchanged.

## Sub-merge gate checklist (PM, per member — Stage C1)

1. Worker verdict `ready-to-merge`; `localChecks` green; all PR threads resolved; PR
   targets the integration branch (never dev).
2. Behind/conflicting with the integration branch (a sibling landed) → update the
   branch; mechanical conflicts resolved directly or by a short-lived worker with both
   issues' context; **semantic** conflicts → `status:needs-feedback` on both issues.
3. `forge.pr.ready` then `forge.pr.merge.squash`, then `forge.branch.delete` on Gitea —
   one clean squashed commit per member on the integration branch. Head commit carries
   `[skip ci]`, so this stays CI-free.
4. Member → `status:batched`; tick the tracking checklist; `git worktree remove --force`
   the path from the worker's completion notification (or its verdict) and `git branch -D`
   its `worktree-agent-<id>`; launch any sequenced successor. Anything sent back
   to the worker instead goes by `SendMessage` — see
   [issue-worker.md](issue-worker.md#rework-message-the-same-worker-dont-spawn-a-new-one).

## Batch gate checklist (PM, per batch — Stage C2)

Batch complete = every member `status:batched` or terminally parked.

- **Ship-partial decision** (PM): parked members that stand alone → ship the rest now,
  move parked members to a future batch, comment the decision on the tracking issue.
  Entangled → hold the batch and surface per the feedback policy.

1. Open **one PR integration branch → dev**. Title `Epic #<n>: <title>` /
   `Batch #<n>: <summary>`. Body: member table + one `Closes #<m>` line per member.
2. Trigger CI: the last commit likely carries `[skip ci]`, so push an empty commit
   without it — `git commit --allow-empty -m "ci: run full suite for batch #<n>"` (in an
   integration-branch worktree) and push.
3. Optional **batch review**: one subagent reviews the whole integration→dev diff for
   cross-member integration problems (interface drift between members, duplicate
   migrations, conflicting config). Cheap — no CI involved.
4. CI failure → fix worker, `isolation: "worktree"` with `base: <remote>/<integration-branch>`; interim commits may
   `[skip ci]`; final push re-runs CI. CI red for pre-existing/base reasons → `blocked`.
5. Conflict vs dev (another batch landed first) → resolve once here; semantic → park.
6. Merge `--merge` (preserves per-member squashed commits; `--squash` only if the
   project's history style demands one commit). Delete the branch.
7. Close members: automatic via `Closes #` **only when dev is the default branch**;
   otherwise close each manually with a comment linking the batch PR. Close the `type:batch`
   tracking issue; an epic closes when its last child closes.
8. Digest (terminal + status issue + push notification), then Stage D.

## Cost/latency notes

- CI runs per batch: **1** (plus re-runs on genuine failures) vs N per-issue runs + up
  to N·(N−1)/2 rebase-triggered re-runs in the old model.
- Conflict work: members conflict against a **frozen-ish** integration branch at
  sub-merge (small, local, immediate) instead of racing a moving dev.
- Latency tradeoff: a member's code reaches dev only when its batch lands. Anything
  urgent goes standalone — that's the hotfix/priority:high exception, not a batch.
