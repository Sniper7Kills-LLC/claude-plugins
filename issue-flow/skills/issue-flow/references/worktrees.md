# Worktree isolation (why workers use `isolation: "worktree"`)

issue-flow runs several workers at once against one checkout. Isolation between them is
not advisory — it is the thing that makes concurrency safe. This file records how the
harness models it, and the failure mode that made this rule necessary.

## The rule

- The PM launches every worker with `isolation: "worktree"` and passes **no worktree path**.
- Nobody in the run calls `EnterWorktree` or `ExitWorktree` — not the PM, not a worker,
  not a worker's child. Plain `git worktree add` is a different thing and stays fine.
- A worker's children are spawned with **no `isolation` parameter**; they inherit the
  worker's worktree.
- Rework goes back to the **same** worker by `SendMessage`, not a new spawn — see
  [Rework](#rework-and-re-spawn) below. **A `checkpoint` verdict is the exception**: it is
  not rework, and it is re-spawned deliberately to discard the context a `SendMessage`
  would keep.
- The PM removes each worktree at sub-merge, from the path in the worker's verdict — see
  [Teardown](#teardown-is-still-the-pms-job) — **and immediately on `checkpoint`**, before
  the replacement worker is spawned.

All harness behavior recorded here was measured on **Claude Code 2.1.224**; re-verify
against the running version if something reads as stale.

## Why: two different pins

The harness resolves the worktree a command is confined to as
`agentWorktree ?? session.worktreePath`. Those two are not the same thing:

| | set by | scope |
|---|---|---|
| `agentWorktree` | `isolation: "worktree"` at spawn | **that agent only** |
| `session.worktreePath` | `EnterWorktree` | **the whole session** — PM and every live worker |

`EnterWorktree` writes the session-scoped one. In a run with N concurrent workers that is
one variable with N+1 writers, and the last caller wins.

You can tell which one fired from the refusal text: `This agent is isolated in the
worktree …` is the per-agent pin working correctly. `This session is isolated in the
worktree …` means someone called `EnterWorktree` and the whole run is now pinned to one
worker's directory.

## The failure mode this prevents

Measured in a real run (ja4plus, 2026-08-07, Claude Code 2.1.224) where workers were
spawned without `isolation` and created their own worktrees with `EnterWorktree`:

- The session started clean at the repo root and stayed clean for 26 minutes.
- A worker called `EnterWorktree` 5 seconds after it was spawned. The **PM's** working
  directory moved into that worker's tree at the same instant.
- 20 seconds later a second worker called `EnterWorktree`, and the **first worker's** own
  working directory moved into the second worker's tree.
- From then on the PM's `git -C <checkout>` commands were refused, and the PM had to run
  an `EnterWorktree` → `ExitWorktree(keep)` bounce four times to keep working.
- One merged PR carried duplicated code because the guard refused the worker's writes to
  the file the shared helper belonged in.

Renaming worktrees does not help: the pin is on the session, not on the path.

## What `isolation: "worktree"` does instead

Verified by direct measurement on 2.1.224, with two workers running concurrently:

- Each agent gets its own worktree at `.claude/worktrees/agent-<id>`, on a harness-made
  branch `worktree-agent-<id>` cut from the **default branch** — not from the branch the
  session has checked out. The harness removes the worktree again **only if it is
  unchanged** — see [Teardown](#teardown-is-still-the-pms-job).
- The worktree is `locked` **only while the agent is running**; the lock is released when
  it returns, and re-taken if it is resumed. This matters for teardown: see below.
- The worktree and its commits **survive the agent returning**. Verified: an agent
  committed, returned, and its tree and commit were still there afterwards.
- The completion notification carries the path: a `<worktree>` block with `worktreePath`
  and `worktreeBranch`. The PM does not have to be told the path by the worker.
- `SendMessage` to an **already-completed** worker resumes it with its context, its
  worktree, and its branch intact, and its earlier commits still in place. This is what
  makes the rework path below safe.
- `run_in_background: true` and `isolation: "worktree"` compose — both take.
- Neither agent's directory moved when the other started or finished.
- The PM's working directory never moved, and `git -C <checkout>` from the PM returned
  exit 0 throughout — including compound commands.
- Each agent's own `git -C <checkout>` was refused with the **`This agent is isolated`**
  wording. Cross-worktree writes stay blocked; that is the guard working as intended.
- A child agent spawned with no `isolation` parameter ran in its parent's worktree, on
  the parent's branch, and could write there.

## The one thing you must do yourself

The harness branches a new worktree from the **default branch**, governed by the
`worktree.baseRef` setting (`fresh`, the default, or `head`). It does not know about the
batch's integration branch. So a worker's first git action points its worktree at the
base from its brief:

```bash
git fetch <remote>
git checkout -B issue/<number>-<slug> <base>
```

This was verified to succeed inside a harness-created worktree.

`-B` **resets** the branch to `<base>`, which is right on a first attempt and destructive
on a second one, so the worker checks for a published branch of the same name first and
continues that instead (`agents/issue-worker.md`). `isolation` takes no base parameter —
the brief is the only place the base can be chosen, which is why the PM has to get it
right on a re-spawn.

## Rework and re-spawn

A gate that sends an issue back to the worker has two mechanisms, and they behave
differently:

| | what it is | worktree | branch |
|---|---|---|---|
| `SendMessage` to the same worker | the same agent, context intact | **its own**, still pinned | already checked out |
| a new `Agent` call | a fresh agent | a **new empty** one | default branch until it checks out |

So rework goes back by `SendMessage` (name workers `worker-<issue>` at launch to keep them
addressable). Re-spawn is the fallback for when the worker is gone — after a session
restart, for instance — and it needs `base: <remote>/issue/<number>-<slug>` in the brief so
the new worker continues the published branch rather than resetting it.

**`checkpoint` inverts this.** A worker that hit its turn budget with work pushed and
nothing wrong is re-spawned, never `SendMessage`d: the whole point of the verdict is to
drop a context that has grown expensive, and resuming the same agent keeps exactly the
thing being discarded. Same brief, `base: <remote>/issue/<number>-<slug>`, the verdict's
`remaining` text appended — and reap the old worktree first (see
[Teardown](#teardown-is-still-the-pms-job)); the replacement gets a fresh one from the
harness and re-checks-out the published branch. See Stage C1 in `SKILL.md`.

This also means a leftover worktree from a previous session **cannot be resumed in place**:
no new agent can be placed into it, and its own agent is gone. Adopt that work from the
branch and the PR, then remove the directory.

## Teardown is still the PM's job

The harness auto-removes an isolated worktree only when it is **unchanged**; a worker's
holds commits, so it survives the agent. Its own removal path refuses outright while
commits or dirty files are present (`Removing will discard this work permanently`), which
is the right default and also means nothing cleans up behind a worker but the PM.

The path comes from the worker's **completion notification** (`<worktree><worktreePath>`),
with the `worktree` field of its verdict as the fallback source. The PM passes no path in
either way.

```bash
git worktree remove --force <path>          # at sub-merge or checkpoint, worker returned
git branch -D worktree-agent-<id>           # the harness branch it left behind
git worktree list --porcelain               # sweep for earlier sessions' leftovers
git worktree prune
```

Two things measured on git 2.53 that the plain `--force` form gets wrong:

- **`--force` alone fails on a *locked* worktree** — `fatal: cannot remove a locked
  working tree; use 'remove -f -f' to override or unlock first`. A worker's tree is locked
  only while it runs, so sub-merge teardown is fine, but **stopping a live worker**
  (`collaboration.md`) or cleaning up after a session that was killed mid-run hits a
  locked tree. Use `git worktree remove -f -f <path>` there, or `git worktree unlock`
  first.
- **Removing the worktree leaves the branch.** The harness's `worktree-agent-<id>` branch
  survives teardown and accumulates one dead ref per worker. Delete it with the worktree.
  The worker's own `issue/<n>-<slug>` branch is deleted by the sub-merge instead.

## What the PM may still do itself

The banned tool is `EnterWorktree`, not the git command. The PM stays unpinned, and a
`git -C <path>` from the PM into any checkout was measured as exit 0 throughout — so for
its own sequential integration-branch work (local sub-merges, the empty commit that
re-triggers CI) the PM may `git worktree add .claude/worktrees/<integration-branch>` and
drive it with `git -C`. It just never *enters* it. Delegated integration-branch work
(suite runs, fix workers) goes to a subagent with `isolation: "worktree"` and
`base: <remote>/<integration-branch>` instead.

## Related settings

Project `.claude/settings.json`. All four names below were confirmed present in the
2.1.224 CLI. To re-check on another version, note the CLI ships as a **compiled binary** —
`grep` over it finds nothing and that is not evidence of absence; use
`strings -a "$(readlink -f "$(which claude)")" | grep baseRef`:

- `worktree.baseRef` — `fresh` (default, branches from `origin/<default-branch>`) or
  `head` (branches from local HEAD, carrying unpushed work).
- `worktree.symlinkDirectories` — directories symlinked from the main checkout instead of
  re-materialized per worktree (`node_modules`, `.venv`, `.cache`). Off by default.
- `worktree.sparsePaths` — sparse-checkout cone for large monorepos.
- `worktree.bgIsolation` — `worktree` (default) blocks Edit/Write in the main checkout
  from background sessions until they isolate; `none` disables that guard for the repo.

`.worktreeinclude` at the repo root is read by the harness, which copies the matching
gitignored files (`.env`, local secrets) into each worktree it creates. Keep it accurate
instead of copying those files by hand.
