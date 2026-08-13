# Working with issue-workers (PM-facing)

The worker's own runbook and limits live in its **self-contained agent definition**
(`agents/issue-worker.md`, spawned as `issue-flow:issue-worker`) — the PM does not pass
that prompt. This file is the PM's side: how to launch a worker, what to hand it, and how
to react to what it returns.

## Launch

Spawn with `Agent`, `agentType: "issue-flow:issue-worker"`, `run_in_background: true`,
**`isolation: "worktree"`**, one per claimed issue, up to `concurrency` at once (across
all live batches). `isolation: "worktree"` is not optional — it is what keeps concurrent
workers apart. See [worktrees.md](worktrees.md) for what goes wrong without it.
Sequenced batch members (dependency chains) launch only after their predecessor
sub-merges. The PM is notified when each finishes — it does **not** sit and wait.
(Fallback if the type won't resolve: spawn `general-purpose` and prepend the brief with
"You are a decision-free issue-worker; never merge; return the verdict JSON.")

The worker is an **independent Opus engineer**: it may research (web), use available MCP
servers (via `ToolSearch`), and spawn its own child agents/Workflows — all on the
**Sonnet** tier and all **confined to that issue's worktree**. The PM sets none of this;
it lives in the worker's agent definition. A worker may also **file new untriaged issues**
for out-of-scope discoveries — which is one reason the PM re-triages (Stage A) on every
worker completion.

## Handoff brief (the only thing the PM passes in)

```
issue:        #<number> — <title>
branch:       issue/<number>-<slug>   (the worker checks this out in the worktree the
                                       harness gave it; the PM passes no worktree path)
base:         <remote>/epic/<n>-<slug>   (the integration branch; <remote>/<dev> only for standalone/hotfix.
                                       On a re-spawn for rework, pass <remote>/issue/<number>-<slug> —
                                       the branch already has commits and a PR. See Rework below.)
ci:           skip | run               (skip = batch member: draft PR, [skip ci] commits, local checks.
                                        run = standalone/hotfix: normal PR, watch provider CI)
batch:        epic #<n> | batch #<n> | standalone
crossCheck:   <URL of the batch cross-check comment on the tracking issue>
              (required whenever `batch` names an epic/batch with other members;
               `n/a` only for standalone/hotfix. The worker validates this as its
               first action and returns `blocked` without doing any work if it is
               absent, empty, "pending", or unresolvable — see SKILL.md Stage B
               step 5.)
remote:       <remote>
forge:        the run configuration's forge block, passed verbatim: {type, host, owner,
              repo, interface}. The worker uses it to pick gh or tea. Never omit it; a worker
              that has to guess the forge is a worker that fails on its first tracker call.
plan:         <the plan the PM already commented on the issue>
conventions:  <test cmd, lint cmd, merge style, repo specifics>
practices:    tdd: true|false            (tests land with or before the implementation)
              ddd: true|false            (model the domain concepts the plan names)
              e2e: none|user-facing|all  (when an E2E spec is required)
              coverage: <n>|null         (threshold to report in localChecks)
              commitStyle: <e.g. conventional>
              docs: none|public-api|all
steRule:      <path to the writing standard: .claude/rules/ste.md when the project has one
               (the planner writes it), else this plugin's references/ste.md>
```

`crossCheck` is not optional on a multi-member batch: it is the field that makes the step-4
gate un-skippable, because you cannot fill it in before the comment exists. A worker that
receives no `crossCheck` line cannot tell "the check was skipped" from "the PM forgot the
field", so it treats both as `blocked`.

`steRule` is not optional decoration: the worker writes **code comments, docstrings, test
names, and its PR body** to that standard, using the spec's `## Terms` vocabulary. Pass
the project's own copy whenever it exists — it is scoped to the source globs, so it loads
while the worker edits code.

`practices` comes from the session's [run configuration](session-config.md) and is part
of the worker's **definition of done** — not advice. A worker that cannot satisfy one
returns `needs-feedback` naming the practice; the PM checks them at the sub-merge gate
and does not waive them there.

**Do not create the worktree.** `isolation: "worktree"` makes the harness create it under
`.claude/worktrees/`, pin the worker to it, and copy the project's `.worktreeinclude`
matches in. The PM learns the path from the worker's **completion notification** (a
`<worktree>` block carrying `worktreePath` and `worktreeBranch`); the worker also reports
its `pwd` as `worktree` in its verdict as the fallback source. Either way the PM never
needs (and never passes) a path in. The PM's job is
to keep `.worktreeinclude` accurate — a worktree is a fresh checkout of *tracked* files,
so `.env` and local secrets are otherwise missing and env-dependent suites fail as
`blocked`. And remember the worker **cannot answer a permission prompt**: every command
it needs must already be in the committed `.claude/settings.json` allow-list.

## Rework: message the same worker, don't spawn a new one

Several gates send an issue **back to the worker** — an unevidenced criterion, a missed
practice, a review comment, a conflict to resolve. Two mechanisms, and they are not
equivalent:

- **Preferred — `SendMessage` to the worker that returned the verdict** (by its agent id
  or name). It keeps its context, its per-agent worktree, and its branch already checked
  out, so nothing is re-pointed and nothing can be lost. Name workers predictably at
  launch (`worker-<issue>`) so they stay addressable.
- **Fallback — re-spawn**, when the worker is gone (session restarted, or it is no longer
  addressable). A re-spawn is a **new agent in a new empty worktree on the default
  branch**, so the brief must carry `base: <remote>/issue/<number>-<slug>` — the published
  branch — plus the plan, the PR number, what the gate rejected, and **the same `crossCheck`
  URL the original brief carried**: a re-spawn runs the First action exactly like a fresh
  launch and returns `blocked` without it. Passing the integration branch as `base` here
  would reset the issue branch and orphan the PR's commits.

Either way the PM tears the worktree down only once the issue is `status:batched` or
terminally parked — not between rework rounds.

## Return contract

The worker returns exactly this object as its final message:

```json
{
  "type": "object",
  "required": ["issue", "outcome", "detail"],
  "properties": {
    "issue":      { "type": "number" },
    "branch":     { "type": "string" },
    "worktree":   { "type": "string", "description": "the worker's own worktree path (its pwd) — the PM's fallback handle for teardown when the completion notification is unavailable" },
    "prNumber":   { "type": "number" },
    "outcome":    { "type": "string", "enum": ["ready-to-merge", "checkpoint", "needs-feedback", "blocked"] },
    "detail":     { "type": "string" },
    "remaining":  { "type": "string", "description": "what is done, what is left, the next concrete step (required when outcome is checkpoint)" },
    "localChecks":{ "type": "string", "description": "what was run and the result, e.g. 'pytest 212 passed; ruff clean; build ok' (required when ci: skip)" },
    "criteria":   { "type": "array", "description": "one entry per acceptance criterion in the issue — required for ready-to-merge",
                    "items": { "type": "object", "required": ["text", "met", "evidence"],
                               "properties": { "text": {"type":"string"}, "met": {"type":"boolean"}, "evidence": {"type":"string"} } } },
    "question":   { "type": "string" },
    "blocker":    { "type": "string" },
    "openThreads":{ "type": "number" }
  }
}
```

## How the PM reacts to each verdict (Stage C1)

| outcome | PM action (the gate) |
|---|---|
| `ready-to-merge` | Verify threads resolved + `localChecks` green (or CI green when `ci: run`) + **every acceptance criterion in `criteria` met and evidenced** (missing/unmet/unevidenced → back to the worker via `SendMessage`, see Rework; disputed → `needs-feedback`); resolve any conflict vs the integration branch; `forge.pr.ready` then `forge.pr.merge.squash` **with the message written out — `--subject "<title> (#<pr>)" --body "[skip ci]"`, never the forge's default** (SKILL.md C1 step 4: GitHub's default carries the token only by luck, Gitea's carries none and starts a full CI run per sub-merge); then **check what landed** — fetch the integration branch and grep its head, re-triggering only per C1 step 4b (SKILL.md C1 step 4b); **`forge.issue.status.set <member> status:batched` as its own step, before any bookkeeping** (SKILL.md C1 step 5 — never a bare label add); then tick the tracking checklist, remove the worktree it reported (`git worktree remove --force <worktree>`; `git worktree prune`), launch any sequenced successor. When the batch completes → batch gate (Stage C2). Standalone/hotfix: merge to dev, Stage D directly. |
| `checkpoint` | The worker hit its turn budget with work pushed; nothing is wrong. Re-spawn a **fresh** worker (not `SendMessage` — that reuses the context the checkpoint exists to discard) with the same brief, `base: <remote>/issue/<n>-<slug>`, and `remaining` appended to the plan. Remove the checkpointed worktree (`git worktree remove --force <worktree>`; `git branch -D worktree-agent-<id>`) — the replacement gets a fresh one and re-checks-out the published branch. **Leave the status label untouched** — `status:in-review` if the worker had opened its PR, `status:in-progress` if it checkpointed before that; both are correct and the replacement adopts whatever PR exists. Post one terse comment recording the checkpoint (the chain cap counts these). **Does not free the slot** — the issue is still in flight. No gate, no digest line. |
| `needs-feedback` | Label `status:needs-feedback`, post `question` as an issue comment, park per the feedback policy (notify; ask interactively only when it gates work). Free the slot. |
| `blocked` | Label `status:blocked`, comment naming `blocker`. Free the slot. |

After handling any verdict, top the pipeline back up to `concurrency`.

## Why this keeps the PM thin

The worker returns a few lines, not the diff/logs/files it churned through. The PM holds
only pointers + the verdict — the context discipline in
[parallelism.md](parallelism.md). The expensive context lives and dies inside disposable
workers, so a single session can clear far more issues than one context window could
hold.
