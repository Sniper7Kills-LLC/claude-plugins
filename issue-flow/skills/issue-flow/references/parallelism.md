# Parallelism & subagents

issue-flow runs faster by fanning work out to subagents and by carrying **multiple
issues in flight at once**, each isolated in its own git worktree. This file is the
reference for *how* and *when* to parallelize. The phase instructions in `SKILL.md`
point here.

## The one rule that makes hybrid safe

**Decisions never run inside a background Workflow.** Workflow subagents are
non-interactive — they cannot stop and ask the user mid-run. Anything that needs a
human judgment call (product behavior, ambiguous requirement, irreversible choice)
must be resolved on the **main thread**, where you can `AskUserQuestion` or park the
issue `status:needs-feedback` immediately.

A Workflow is a **pure, decision-free executor**. If a Workflow agent encounters a
real decision, it does **not** guess: it returns
`{ needsFeedback: true, question: "<the specific question>" }` for that work unit and
stops that unit. The main thread reads this at the `<task-notification>` when the
Workflow finishes, then surfaces it to the user / labels the issue.

Consequence for alerting: work placed in a Workflow only reports "needs input" when
that Workflow **completes**, not the instant the decision arises. So keep
decision-prone work on the main thread; push only mechanical, already-decided work
into Workflows.

## What is safe to parallelize

| Stage | Work | Parallel? | Mechanism |
|---|---|---|---|
| plan (PM, Stage B) | Locate files, map call sites | ✅ read-only | `Agent` fan-out (`Explore`) |
| implement (worker) | Edit code | ⚠️ only if file sets are **disjoint** | worktree-per-issue; within an issue, sequential unless partitioned |
| self-review (worker) | Review diff by dimension | ✅ read-only | Workflow fan-out: correctness / security / maintainability / frontend / backend |
| verify (worker/batch CI) | Read failing logs | ✅ read-only | `Agent` fan-out, one per failing job |
| across issues | Multiple issues at once | ✅ with isolation | one git worktree + branch per issue, concurrency cap |
| across batches | Multiple integration branches live | ✅ but prefer finishing one | batch-level conflicts resolved once at the batch gate |
| within a dependency chain | Sequenced members | ❌ sequential by design | each forks the integration branch after its predecessor sub-merges |

Writes to the **same worktree** must never run concurrently — that corrupts the tree.
Two write-agents are safe only when they touch **provably disjoint** paths (e.g. one
in `frontend/`, one in `backend/`), or when each gets its **own** worktree.

## Multiple issues at once (worktree isolation)

The old "one issue in `status:in-progress` at a time" rule is replaced by a
**concurrency cap** `concurrency` (default 3; let the user override), counted
**across all live batches**. Up to `concurrency` issues may be `status:in-progress`
simultaneously, **each in its own worktree**. Members of a dependency chain are the one
exception to "launch whenever a slot frees": they run **sequentially** within their
batch — each launches after its predecessor sub-merges into the integration branch.

Setup per issue: the PM creates no worktree. It launches the worker with
`isolation: "worktree"` and the harness makes one under `.claude/worktrees/`, pinned to
that worker alone (see [worktrees.md](worktrees.md)). The worker then points its worktree
at its base (the batch's integration branch; `<remote>/<dev>` only for standalone/hotfix
work — see [batching.md](batching.md)):

```bash
# inside the worker's own worktree
git fetch <remote>
git checkout -B issue/<number>-<slug> <remote>/epic/<n>-<slug>
```

On a re-spawn for rework the base is the issue's **own published branch** instead
(`<remote>/issue/<number>-<slug>`) — the worker checks for it before falling back, so a
second attempt cannot reset away the first attempt's commits.

- `<remote>` is detected in preflight (`git remote` — usually `origin` but never
  hardcode it).
- Each worktree is a fully independent checkout: build, test, commit, push there.
- The main thread owns the loop and the gates; it may launch a background implement
  Workflow per issue and react to each `<task-notification>` as issues complete.

Cleanup on merge (or abandonment) — the harness auto-removes an isolated worktree only if
it is *unchanged*, and a worker's is never unchanged, so the PM removes it. Use the path
from the worker's completion notification (`worktreePath`) or its verdict, and sweep for
leftovers from earlier sessions:

```bash
git worktree remove --force <path from the completion notification>  # after the branch is merged/deleted
git branch -D worktree-agent-<id>                                   # the harness branch the removal leaves
git worktree list --porcelain                                       # leftovers on issue/* branches
git worktree remove -f -f <leftover>                                # -f -f: a killed session leaves the lock
git worktree prune
```

**Two different issues touching the same files is allowed and expected.** Their edits
are isolated by separate worktrees, so concurrent work never corrupts anything. The only
consequence is a possible merge conflict when the second one sub-merges into the
integration branch — and that is the PM's job to resolve at the sub-merge gate (Stage
C1), not a reason to avoid scheduling the issues together. That conflict is resolved
**once, locally, against a stable integration branch** — no CI re-runs, no rebase storm
across sibling PRs. Do **not** steer the schedule to dodge file overlap; isolation +
PM-side conflict resolution is the design. (Mechanical conflicts the PM resolves directly
or via a short-lived worker; semantic conflicts — two intents on the same logic — are a
decision and go to `status:needs-feedback`. When overlap is *known and heavy*, prefer
putting both issues in the same batch sequenced, so the second builds on the first.)

## Claim race (compare-and-set)

With several issues (and possibly several machines) in flight, picking and claiming
must be atomic-ish. Just before swapping `status:ready → status:in-progress`,
**re-read** the issue's current labels and assignees. If another worker already moved
it (label gone, or assignee set), abandon it and pick the next. Assign `@me` as part
of the claim so the assignee acts as the lock signal.

## Specialist reviewers (worker self-review)

Run reviewers as a Workflow fan-out over the PR diff. Default lenses, pruned to what
the diff actually touches:

- **correctness** — logic, edge cases, regressions
- **security** — injection, authz, secrets, unsafe deserialization
- **maintainability** — always on: the slop a green suite never catches. Check the diff
  **rule by rule** against the project's `.claude/rules/quality.md` when it exists (the
  planner scaffolds it), reporting a pass/fail verdict per rule; else use this default
  list: try/catch that only rethrows or logs-and-continues; defensive casts or null
  checks against states the types already exclude; abstractions with a single caller;
  configuration for a value nothing varies; dead or commented-out code; comments that
  restate the code. This lens exists because test-passing does not measure
  maintainability, so nothing else in the loop penalizes it — and a per-rule verdict is
  auditable where "looks clean" is not.
- **frontend** — only if the diff touches UI/client code: a11y, state, render cost
- **backend** — only if the diff touches server/data code: API contracts, queries, migrations

Each reviewer returns structured findings; the main thread dedupes, posts them as PR
comments, and fixes the real ones. A reviewer that wants a product decision returns
`needsFeedback` rather than inventing intent.

**Check what the diff does not contain before you trust a clean review.** A reviewer can
only see what you hand it, and a diff silently omits things: a file git classifies as
**binary** appears as `+0/-0` with a byte-size change and no content at all, so a lens over
that diff reports nothing and reads as a pass. Measured in a live run — a test file
carrying NUL bytes as a deliberate key separator was binary to git, and six added tests
were invisible to every reviewer and to the sub-merge gate. Generated files, lockfiles and
anything above the host's diff-size cap fail the same way.

So: compare the PR's **file list** against what the diff actually shows. Any file with a
size change but no visible content is **unreviewed, not clean** — review it by reading both
versions directly, and say in the review which files were handled that way. This is the
same failure the loop meets elsewhere in other clothes: a self-skipping test suite exits 0,
a CI job that never started reports a check, a binary file shows an empty diff. **Absence of
signal is not a pass.** Wherever a gate reports green, confirm the thing it was gating
actually ran or was actually read.

### Example Workflow script (self-review fan-out)

The main thread can lift this and pass it to the `Workflow` tool. It is decision-free:
it only *finds and reports*, it never edits or merges.

```js
export const meta = {
  name: 'issue-flow-review',
  description: 'Parallel specialist review of a PR diff for issue-flow',
  phases: [{ title: 'Review' }],
}
const FINDINGS = {
  type: 'object',
  required: ['lens', 'findings'],
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'file', 'line', 'problem', 'fix'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          file: { type: 'string' }, line: { type: 'number' },
          problem: { type: 'string' }, fix: { type: 'string' },
          needsFeedback: { type: 'boolean' },
        },
      },
    },
  },
}
// args = { diff: "<unified diff, from `forge.pr.diff`>", lenses: ["correctness","security","maintainability","frontend","backend"] }
// For the maintainability lens, append the project's .claude/rules/quality.md to the
// prompt when it exists, and ask for a pass/fail verdict per rule.
phase('Review')
const reviews = await parallel(args.lenses.map(lens => () =>
  agent(
    `Review this PR diff through the ${lens} lens ONLY. Report real, actionable issues.\n` +
    `Do NOT invent product intent — if a finding needs a human decision, set needsFeedback:true.\n\n` +
    args.diff,
    { label: `review:${lens}`, phase: 'Review', schema: FINDINGS })
))
return reviews.filter(Boolean)
```

## How many issues per session?

There is **no technical limit on issues per session.** The binding constraint is the
**main thread's context**, not a counter. The session's `runLength` — confirmed with the
user at startup ([session-config.md](session-config.md)) — is a *scope and cost choice*
(one batch / N issues / until the backlog empties / until you stop me), not the real
ceiling.

What actually extends the session is **context discipline** (next section) plus the
fact that **all durable state lives on the forge**, not in context. Labels, comments, and
PRs are the source of truth. So when the harness compacts context, or you start a
fresh session, **Phase 0 state recovery rebuilds everything from the tracker** and the loop
continues. A "session" can therefore span many context compactions and process far
more than any single context window could hold — provided each issue leaves a clean
trail on the tracker and consumes little main-thread context.

Stop conditions are: the session's `runLength` limit is reached, no workable issues
remain, the user says stop, everything left is `status:blocked` /
`status:needs-feedback` / `status:awaiting-review`, or the token budget (if one is set)
is spent. Let in-flight workers finish and gate their results before stopping, and
always report what was skipped — never silently truncate the queue.

## Context discipline — what lets you pass any cap

Subagents only buy a longer session if the **main thread stays thin per issue.** If
the main thread reads full diffs, full CI logs, or full file contents inline, context
fills no matter how much you parallelize. So:

- **Never read a large artifact inline on the main thread.** Delegate it to a subagent
  (an `Explore` agent for code maps, a reviewer subagent for diffs, a log reader for
  CI) and take back only a **short structured summary** — findings, root cause, the
  decision, and pointers (`issue#`, `PR#`, file:line). The compressed return is the
  point.
- **Keep `rtk` in the loop when the operator has it installed** (`rtk --version`
  succeeds). Its hook rewrites shell commands transparently (`git status` →
  `rtk git status`) and filters token-heavy output for the PM and every worker alike.
  Do not bypass it with `rtk proxy` except to debug an output the filter mangled, and do
  not re-add filters it already applies.
- **Hold pointers, not payloads.** The main thread's per-issue footprint should be a
  handful of lines: issue number, branch/worktree path, PR number, current phase, open
  decisions. Everything reconstructable from the tracker stays *on* the tracker.
- **Leave a complete tracker trail every phase** (label + comment). This is also your
  crash-recovery: if context is compacted mid-flight, Phase 0 reads the trail back.
- **Drop finished issues from working memory.** Once merged and the worktree is torn
  down, the issue is fully recorded on the tracker — carry nothing forward but the running
  session summary (counts + blocked/needs-feedback list).
