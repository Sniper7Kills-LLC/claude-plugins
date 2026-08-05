# Working with issue-workers (PM-facing)

The worker's own runbook and limits live in its **self-contained agent definition**
(`agents/issue-worker.md`, spawned as `issue-flow:issue-worker`) — the PM does not pass
that prompt. This file is the PM's side: how to launch a worker, what to hand it, and how
to react to what it returns.

## Launch

Spawn with `Agent`, `agentType: "issue-flow:issue-worker"`, `run_in_background: true`,
one per claimed issue, up to `concurrency` at once (across all live batches).
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
worktree:     .claude/worktrees/issue-<number>   (worker creates it if missing)
branch:       issue/<number>-<slug>
base:         <remote>/epic/<n>-<slug>   (the integration branch; <remote>/<dev> only for standalone/hotfix)
ci:           skip | run               (skip = batch member: draft PR, [skip ci] commits, local checks.
                                        run = standalone/hotfix: normal PR, watch provider CI)
batch:        epic #<n> | batch #<n> | standalone
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

`steRule` is not optional decoration: the worker writes **code comments, docstrings, test
names, and its PR body** to that standard, using the spec's `## Terms` vocabulary. Pass
the project's own copy whenever it exists — it is scoped to the source globs, so it loads
while the worker edits code.

`practices` comes from the session's [run configuration](session-config.md) and is part
of the worker's **definition of done** — not advice. A worker that cannot satisfy one
returns `needs-feedback` naming the practice; the PM checks them at the sub-merge gate
and does not waive them there.

**Before launching, make the worktree usable.** Create it under `.claude/worktrees/`
(inside the checkout, gitignored, and inside the project root so the sandbox permits
writes), then copy the project's `.worktreeinclude` matches into it — a worktree is a
fresh checkout of *tracked* files, so `.env` and local secrets are otherwise missing and
env-dependent suites fail as `blocked`. And remember the worker **cannot answer a
permission prompt**: every command it needs must already be in the committed
`.claude/settings.json` allow-list.

## Return contract

The worker returns exactly this object as its final message:

```json
{
  "type": "object",
  "required": ["issue", "outcome", "detail"],
  "properties": {
    "issue":      { "type": "number" },
    "branch":     { "type": "string" },
    "prNumber":   { "type": "number" },
    "outcome":    { "type": "string", "enum": ["ready-to-merge", "needs-feedback", "blocked"] },
    "detail":     { "type": "string" },
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
| `ready-to-merge` | Verify threads resolved + `localChecks` green (or CI green when `ci: run`) + **every acceptance criterion in `criteria` met and evidenced** (missing/unmet/unevidenced → back to the worker; disputed → `needs-feedback`); resolve any conflict vs the integration branch; `forge.pr.ready` then `forge.pr.merge.squash`; label `status:batched`, tick the tracking checklist, tear down the worktree, launch any sequenced successor. When the batch completes → batch gate (Stage C2). Standalone/hotfix: merge to dev, Stage D directly. |
| `needs-feedback` | Label `status:needs-feedback`, post `question` as an issue comment, park per the feedback policy (notify; ask interactively only when it gates work). Free the slot. |
| `blocked` | Label `status:blocked`, comment naming `blocker`. Free the slot. |

After handling any verdict, top the pipeline back up to `concurrency`.

## Why this keeps the PM thin

The worker returns a few lines, not the diff/logs/files it churned through. The PM holds
only pointers + the verdict — the context discipline in
[parallelism.md](parallelism.md). The expensive context lives and dies inside disposable
workers, so a single session can clear far more issues than one context window could
hold.
