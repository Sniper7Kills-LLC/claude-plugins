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
- The worktree is `locked` while the agent is running; the lock is normally released when
  it returns, and re-taken if it is resumed. Normally, not always — see the 2.1.284 note
  under [Seeing and reaping peer sessions](#seeing-and-reaping-peer-sessions) — which is why
  every teardown uses `-f -f`.
- The worktree and its commits **survive the agent returning**. Verified: an agent
  committed, returned, and its tree and commit were still there afterwards.
- The completion notification carries the path: a `<worktree>` block with `worktreePath`
  and `worktreeBranch`. The PM does not have to be told the path by the worker.
- `SendMessage` to an **already-completed** worker resumes it with its context, its
  worktree, and its branch intact, and its earlier commits still in place. This is what
  makes the rework path below safe.
- `SendMessage` to a **still-running** worker is delivered too — see
  [Messaging a worker](#messaging-a-worker).
- **Do not pass `run_in_background` to `Agent`.** It composed with `isolation` when this
  section was first measured on 2.1.224, but on 2.1.232 it is present in the tool schema
  on some sessions and absent on others, and where it is present it changes nothing —
  including on the named un-isolated spawn below, which it does **not** rescue. Background
  is the default for a subagent either way. It stays correct on `Bash` calls, which is a
  different tool and a different parameter. <!-- spawn-lint: ok -->
- Neither agent's directory moved when the other started or finished.
- The PM's working directory never moved, and `git -C <checkout>` from the PM returned
  exit 0 throughout — including compound commands.
- Each agent's own `git -C <checkout>` was refused with the **`This agent is isolated`**
  wording. Cross-worktree writes stay blocked; that is the guard working as intended.
- A child agent spawned with no `isolation` parameter ran in its parent's worktree, on
  the parent's branch, and could write there.

Measured on 2.1.284 (isolated parents; unnamed un-isolated children that slept 20, 50 and
60 s; a background `Bash` that slept 40 s):

- **The scratchpad is not isolated.** The parent and its child both reported the
  session's own scratchpad path — the same one the PM sees. Worktree isolation covers the
  checkout, not scratch files, so the worker makes its own directory under it
  (`agents/issue-worker.md`, Worktree boundary).
- **The scratchpad is writable from an isolated agent.** `Write` into it and a bare
  `mktemp -d` both succeeded.
- **A parent's completion waits for its children.** The parent sent its final reply
  five seconds in; its completion notification fired only after the child finished a
  minute later. With two children, it was woken once per child as each finished, and
  one notification fired, after the last.
- **The late child result replaces the parent's answer.** The harness woke the parent
  with the child's result, the parent replied again, and that second reply is what the
  notification carried — the first final reply never reached the caller. This is why a
  worker must not send its verdict while a child is running (`agents/issue-worker.md`,
  hard rule 4).
- **A background shell does not hold the completion.** An agent that ended its turn with
  a background `Bash` still running notified at once, marked interim ("stopped with
  background work of its own still running"). It was woken when the shell exited, and a
  second notification carried its new reply. So a worker waiting on its CI watch produces
  an interim notification the PM must not read as a verdict (SKILL.md, *Reading a worker
  notification*). This replaces
  the earlier note that an ended worker is never re-invoked when its watch finishes.
- **The worktree guard reads every `Bash` command an isolated agent runs, and refuses
  what it cannot prove stays in the tree.** Measured with no user hooks
  (`--setting-sources project,local`), one command per call:

  | Allowed | Refused |
  |---|---|
  | plain `git …`, `&&` / `;` chains, `git` inside `if` | `$(git …)` anywhere, e.g. `base=$(git merge-base …)` |
  | `cd <own root> \|\| exit 1; …`, `git -C <own root>` | `cd` into the shared checkout, `git -C <shared checkout>` |
  | literal `tea api '…' \| jq …`, also inside `for` | `tea` with a URL built at runtime (`page=$page`), or inside `$(…)` or a `while` loop |
  | `git checkout <sha> -- <paths>`, `bash <script>`, `rm` (even outside the tree) | a `trap '…'` string |

  A `PreToolUse` hook that rewrites commands changes what the guard sees. With a hook
  that turns every `git …` into `<wrapper> git …`, every `git` call a worker made was
  refused ("runs <wrapper> with a git command among its operands"), and one worker could
  only proceed through `/usr/bin/git`. The runbook forms in `agents/issue-worker.md` are
  the allowed shapes; the PM runs on the main thread and is not guarded this way.

## Messaging a worker

A worker does not have to finish before the PM can tell it something. Measured directly on
**Claude Code 2.1.228**, with a background worker spawned `isolation: "worktree"` and
stepping through twenty separate turns:

- A `SendMessage` sent while the worker was **mid-run** arrived. The tool result says so
  explicitly: *"Message queued for delivery to `<name>` at its next tool round."*
- Delivery is at a **turn boundary, not an interrupt**. The message landed cleanly between
  two steps, roughly two turns (~11s) after it was sent. The worker read it, acted on it,
  and carried on with the rest of its work — no restart, no lost context, no step spent
  waiting to receive.
- On 2.1.228 this section also recorded that an agent spawned **without** `isolation`
  never received a message at all. **That does not hold on 2.1.232** and the difference is
  what was addressed: an *unnamed* un-isolated agent, messaged by its `agentId`, took the
  message on its **first** tool round and answered in 6.7s. What was really being measured
  was a *named* un-isolated spawn, which is a different animal — see below.

Two consequences the PM should treat as load-bearing:

1. **`name:` without `isolation:` is the one spawn shape that strands you.** It does not
   produce a background subagent at all. It produces a **peer session**: no completion
   notification, ever; absent from both sections of `ListAgents`; and — the part that
   actually costs you the run — **its final text is delivered to nobody**. The peer does
   the work and answers into the void. (Measured 2.1.232, four spawn cells, two backends;
   the peer's own transcript shows it received the task, answered in plain text, and had
   to `ToolSearch` for `SendMessage` before it could deliver anything at all.) So: spawn
   **unnamed**, or pass `name:` **with** `isolation:`. Never `name:` alone.
   This shape exists only when the session has `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`:
   measured on 2.1.285, the `Agent` tool's `name`, `team_name` and `mode` parameters are
   absent without it, and a requested `name` is silently dropped. That is why issue-flow
   addresses workers by `agentId`, which `SendMessage` accepts either way (issue #65).
   <!-- spawn-lint: ok -->

   This one is enforced, not just documented. The plugin ships a `PreToolUse` guard
   (`hooks/guard-spawn.py`) that denies the shape with the fix in the message — measured
   to fire for the PM's own calls **and** for anything a worker spawns, which is what
   covers a worker naming its review children. It reads `ISSUE_FLOW_SPAWN_GUARD` from the
   environment **at session start**: put `"env": {"ISSUE_FLOW_SPAWN_GUARD": "off"}` in a
   project's `.claude/settings.json` where named peer sessions are wanted deliberately
   (exporting it from a tool call does nothing). `ask` exists but is interactive-only —
   a background agent hangs on the prompt, which is worse than the refusal. The same
   hook also denies a `subagent_type: "fork"` spawn that carries the handoff brief
   (SKILL.md Stage B step 5), under its own switch, `ISSUE_FLOW_FORK_GUARD`, so turning
   named peers back on does not turn fork dispatch back on.
2. **Unnamed un-isolated helpers are reachable and do notify.** `code-auditor`,
   `ux-explorer` and `deploy-verifier` are spawned without isolation and
   are *not* peer sessions: they fire completion notifications and they accept a mid-run
   `SendMessage` addressed by `agentId`. What they lack is a stable name, so keep the
   `agentId` from the spawn result if you intend to push anything to them.
   `review-scribe` is spawned unnamed **with** isolation — it writes a branch, so it gets
   its own tree like a worker (`project-review` Phase 4) — and notifies the same way. (Stage D's
   deploy watch is a background `Bash` command, not an agent — `run_in_background: true`
   stays correct there.)

Because delivery costs the worker no turn of its own, a correction that arrives while it
works is cheap. Use it: see the mid-flight push in
[collaboration.md](collaboration.md) and the batch findings log in
[batching.md](batching.md).

## Seeing and reaping peer sessions

If one is created anyway — an older session, a helper spawned before the guard, a hand-run
`Agent` call with the guard off — the PM cannot find it through `ListAgents`, which shows
neither the agent nor a hint that it exists. It is on disk, and the plugin ships the reader:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/peers.py"          # live peers, stale rosters summarized
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/peers.py" --json   # same, machine-readable
```

It reads `~/.claude/teams/<session>/config.json` (the roster, with each member's backend)
and `inboxes/<name>.json` (mail the peer has not drained). Two cautions the script prints
and the PM should believe:

- **A roster entry is not proof of life.** Team directories outlive their sessions — one
  machine in this investigation carried 72 entries from a session five days dead, and a
  named grandchild was still listed half an hour after its parent exited while
  `ListAgents` showed nothing. Rosters untouched for a day are counted, not listed.
- **Reaping takes two steps, and the first `shutdown_request` is often ignored.** A peer
  that has never been messaged drains nothing: send it an ordinary message first, then
  `{"type": "shutdown_request"}`, then re-run the script and confirm the roster shrank.
  Only the `tmux` backend leaves an OS process behind; in-process peers leave none.

Worktrees are the other thing that accumulates. An agent's worktree survives its
completion, and removal leaves the `worktree-agent-<id>` branch behind — which is why the
Stage C2 teardown sweeps with `-f -f` and then deletes dead harness branches. (`-f -f`
because a lock can outlive its agent: on 2.1.284 a returned agent's tree was unlocked in
two clean measurements, including after a `SendMessage` resume, but one tree was still
locked right after its notification and plain `--force` was refused. A session killed
mid-run leaves its lock too.)

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

So rework goes back by `SendMessage`, addressed by the `agentId` the spawn result returned
(`worker-<issue>` too, where the `Agent` tool has a `name` parameter). Re-spawn is the fallback for when the worker is gone — after a session
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
git worktree remove -f -f <path>            # at sub-merge or checkpoint, worker returned
git branch -D <worktreeBranch>              # the harness branch it left behind, named in the notification
git worktree list --porcelain               # sweep for earlier sessions' leftovers
git worktree prune
```

**Take the harness branch's name from the notification, never from `git worktree list`.**
The notification's `<worktree>` block names it (`worktreeBranch`). The worker's first git
action is `git checkout -B issue/<n>-<slug> …`, and from then on the list reports the
issue branch; the harness branch appears nowhere in it. Measured on git 2.53: once the
worker has checked out its issue branch, nothing references the harness branch, and
`git branch -D` on it succeeds even while the worker's tree is live.

That makes the sweep rule simple: **a `worktree-agent-*` branch that no worktree has
checked out is dead**, whoever made it. Matching one to a batch is impossible and not
needed. Delete them all at the batch-gate and Stop sweeps, and wherever a notification
was never received (a stopped worker, a previous session's leftover). Prune first: a tree
whose directory is gone still reports its path until `prune` drops it, and its branch
would survive the sweep.

```bash
git worktree prune
git for-each-ref --format='%(refname:lstrip=2) %(worktreepath)' 'refs/heads/worktree-agent-*' \
  | awk '$2 == "" {print $1}' | xargs -r git branch -D
```

(`lstrip=2`, not `short`: `short` prints `heads/<name>` when a tag has the same name, and
`git branch -D` then fails.)

A worker spawned seconds ago that has not yet run its checkout still has its tree on the
harness branch, so `%(worktreepath)` is set and the sweep keeps it.

### Parked standalone worktrees

A batch member parked on `needs-feedback` or `blocked` keeps its tree until the batch
gate's sweep. A standalone or hotfix issue has no batch gate, so without a rule its tree
waits for the Stop sweep, and a long session collects one per parked issue. Remove it
when it parks (SKILL.md C1, `needs-feedback` and `blocked`):

```bash
[ -z "$(git -C <worktree> status --porcelain)" ] \
  && [ "$(git -C <worktree> rev-list --count HEAD --not --remotes)" = 0 ] \
  && git worktree remove -f -f <worktree> && git branch -D <worktreeBranch>
```

Where the answer goes depends on what happened to the tree:

- **Removed** → the issue goes back to `status:ready` and Stage B spawns a **fresh**
  worker, which continues the **published** `issue/<n>-<slug>` branch — or, when none was
  published, resets the branch to its base. That is why removal needs both checks: it
  would lose uncommitted edits and never-pushed commits (the second check counts commits
  no remote-tracking ref contains).
- **Kept** (a check failed) → `SendMessage` the answer to that worker (its `agentId`). Only that worker
  can commit and push what its tree holds, and while the tree has the branch checked out a
  fresh worker's checkout fails (`already used by worktree`). Say in the park comment that
  the tree was kept.
- **Kept, and the worker is gone** (a restart) → Phase 0 and the Stop sweep skip parked
  trees and list them in the digest. Before re-spawning that issue, commit and push what
  the tree holds from the main checkout (`git -C <tree>`), or ask the user; then remove the
  tree and spawn fresh.

Two things measured on git 2.53 that the plain `--force` form gets wrong:

- **`--force` alone fails on a *locked* worktree** — `fatal: cannot remove a locked
  working tree; use 'remove -f -f' to override or unlock first`. A worker's tree is locked
  while it runs, a session killed mid-run leaves the lock, and on 2.1.284 one returned
  agent's tree was still locked right after its notification. So every teardown in this
  plugin uses `git worktree remove -f -f <path>`.
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
