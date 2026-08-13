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

## The findings log — what one member learns, the batch keeps

Members of a batch are chosen because they share an area, files, or a dependency chain,
so they keep meeting the same surprises: a helper whose signature contradicts its
docstring, a fixture that has to be seeded before the suite passes, an interface one
member is establishing that another is about to consume. Each worker is isolated, so by
default that knowledge dies in its worktree and the next member pays for it again — or
worse, builds against the stale assumption and the two only disagree at the sub-merge
gate.

The fix is a per-batch log, kept as **comments on the batch's tracking issue** (the epic
issue, or the `type:batch` issue). Comments, not the issue body: workers append
concurrently, and the body is a PM-owned block that a worker must not clobber (see
[collaboration.md](collaboration.md)).

**Workers write.** When a worker learns something a sibling would want, it posts one
comment on the tracking issue, first line exactly:

```
finding: <one line — the fact, not the story>
```

followed by a short paragraph with the evidence (`file:line`, the command, the error).
What qualifies: a documented or spec'd behavior that turns out to be wrong; a shared
interface it is creating or changing; a non-obvious setup/test prerequisite; a
constraint discovered the hard way. What does not: progress updates, anything already
in the plan, and anything specific to its own issue — those belong on its own issue.

**Workers read.** Every worker reads the tracking issue's `finding:` comments as its
first research action, before it plans its edits, and again on a rework or replacement
spawn — a checkpoint replacement inherits none of the original's context, so the log is
how the batch's knowledge outlives it.

**The PM relays the urgent ones.** A worker reads the log when it starts, so the log only
ever reaches workers that start *after* a finding lands. For anyone already running, the
push is the only channel — and the PM decides on the **targeted tracking-issue read that follows every worker completion**
(SKILL.md, Stage A) whether a live sibling needs it now.

Push it if either is true:

- It **invalidates an assumption a live sibling is working from** — a correctness problem.
- It **would save a live sibling from rediscovering it** — a cost problem. Setup
  prerequisites, environment traps, a tool invocation that has to be shaped a particular
  way. These are the cheapest wins and the easiest to miss, because nothing is *wrong*
  until the sibling wastes the same hour. Measured in a live run (a five-member epic batch):
  one worker logged that a fresh worktree has no `node_modules` and no running Postgres;
  the sibling that was already building rediscovered the identical wall minutes later,
  because nobody pushed it.

Push by `SendMessage` to that `worker-<n>`, with the finding quoted and what to do about it —
delivery to a running worker is measured and costs it no turn (see
[worktrees.md](worktrees.md#messaging-a-worker) and the correction path in
[collaboration.md](collaboration.md#corrections-reach-work-in-flight)). The PM decides
who is affected; it does not broadcast every finding to every worker.

**Carry forward what constrains someone else.** The log dies with the batch, so a finding
that limits work **outside** it needs a home where that work will look:

- It changes what the shipped product is → the repo (spec, README, a code comment), as part
  of the change.
- It constrains **another epic or a specific open issue** → comment it **on that issue**,
  headed `Carried forward from <this batch> — <the constraint>`, naming what it rules out
  and what the options are. Do not decide for that issue; record the constraint and leave
  the decision to whoever works it.

Recording it only where it was found buries it: nobody planning the other epic reads a
sibling's closed sub-issue. This is worth stating because workers invent it on their own —
a later epic in the dogfooded repo carries a `Carried forward from …` note that a worker
wrote unprompted — and a practice that useful should not depend on being reinvented.

Cost: one extra issue read per worker start, one comment per genuine discovery.

## Keeping sub-issue PRs CI-free

Two mechanisms, layered:

1. **`[skip ci]` in the head commit message.** GitHub Actions natively skips `push` and
   `pull_request` triggered workflows when the head commit message contains `[skip ci]`
   (or `[ci skip]`); Gitea Actions does the same from Gitea 1.20. Both match the token
   **anywhere in the message, body included** — measured on GitHub Actions and on Gitea
   1.25.3, where a commit whose subject was clean and whose body held the token registered
   no run. Workers append it to **every pushed head commit**.

   That covers commits a worker writes. It does **not** cover the merge commit, which the
   forge composes: on Gitea a squash merge's default message is the pull request title
   alone, so the token does not survive the sub-merge and the integration branch runs CI
   once per member. Write the merge message explicitly (step 3 of the sub-merge checklist
   below) rather than trusting either forge's default.
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
3. `forge.pr.ready` then `forge.pr.merge.squash` **with the message written out** —
   `--subject "<title> (#<pr>)" --body "[skip ci]"` (`--title`/`--message` on `tea`,
   `title`/`message` on the MCP) — then `forge.branch.delete` on Gitea. One clean squashed
   commit per member on the integration branch, and it stays CI-free because *you* put the
   token there. Neither default is safe: GitHub folds the member's commits into the body
   (the token arrives by luck, and a repository setting can withdraw it), Gitea folds
   nothing and starts a run per sub-merge.
3b. Read the integration branch head afterwards —
   `git log -1 --format='%s%n%b' <remote>/<integration-branch> | grep -ciE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true`
   — where `0` means a run just started. **If the batch PR is already open, push a fresh
   subject-only trigger commit now** and re-anchor the watch: the sub-merge replaced the
   head the batch gate was watching.
4. Member → `forge.issue.status.set <m> status:batched`, which **removes `status:in-review`
   in the same operation** (at most one `status:` label per issue — a bare
   `forge.issue.label.add` leaves it in two states and breaks status queries); tick the
   tracking checklist; `git worktree remove --force`
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
   integration-branch worktree) and push. **One `-m`, subject only, no body:** both
   providers match the token anywhere in the message, body included, so a body that
   *explains* the token suppresses the run just as well as one that uses it. Verify
   before pushing — `git log -1 --format='%s%n%b' | grep -ciE 'skip|no ci' || true` must
   print `0` (the alternation covers `[no ci]`, which a bare `skip` match misses; the
   `|| true` absorbs `grep -c`'s exit 1 on a zero count, which is the *passing* case) —
   then poll `ci-watch` to a terminal verdict, anchored to the SHA you just pushed.
   `no-run-registered` means the trigger did not take; it is never a pass. Grep the
   commit to tell the two causes apart (`… | grep -niE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true`, the `|| true` keeping the no-token
   branch reachable under `set -e`): a token in the message → push one clean subject-only
   trigger and re-watch; a clean message → runner or workflow problem, which a re-push
   does not fix. **Repeat this step after every later push to the integration branch** —
   a late member's sub-merge writes a new head carrying the token (sub-merge step 3) and
   quietly suppresses the run again.
3. Optional **batch review**: one subagent reviews the whole integration→dev diff for
   cross-member integration problems (interface drift between members, duplicate
   migrations, conflicting config). Cheap — no CI involved.
4. CI failure → fix worker, `isolation: "worktree"` with `base: <remote>/<integration-branch>`; interim commits may
   `[skip ci]`; final push re-runs CI. CI red for pre-existing/base reasons → `blocked`.
5. Conflict vs dev (another batch landed first) → resolve once here; semantic → park.
6. Merge `--merge` (preserves per-member squashed commits; `--squash` only if the
   project's history style demands one commit), always with an explicit subject and an
   **empty** body, then check `<remote>/dev`'s head for the token whatever the style — a
   repository can compose merge-commit messages from the PR description too. A suppressed
   head means no post-merge run and no deploy: remedy with one clean subject-only empty
   commit on dev (not a rewrite of the merge commit), or start the deploy at the provider
   if dev is protected ([deploy.md](deploy.md)). Delete the branch.
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
