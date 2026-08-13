# issue-flow

A Claude Code plugin. It drives an autonomous development loop from tracker issues:

```
spec → issues → batched builds → one CI run per batch → verified deploy → user review
```

The review files new issues, and the loop runs them next.

Tracker issues are the single source of truth. Labels hold the state. Comments hold the
audit trail. All durable state lives on the forge, so the loop survives a restart.

## Which forge

This plugin runs on **GitHub** or on **Gitea** (1.20 or later). It detects which from
your git remote at startup and uses that forge's CLI — `gh` or `tea` — falling back to
that forge's MCP server. Nothing else about the loop changes.

Gitea support exists because GitHub bills Actions minutes on private repositories. A
self-hosted Gitea with its own runner has no such ceiling.

The full operation mapping is in [references/forge.md](references/forge.md).

## The four skills

### `/project-planner`

Interviews you, then writes the project brief and the project scaffold.

- `docs/specs/` — the index `spec.md`, one detailed `features/*.md` per feature set,
  self-contained HTML mockups, and a browsable `spec.html` you open locally.
- `CLAUDE.md`, the `.claude/` scaffold (permissions, hooks, path-scoped rules, project
  skills such as `/run` and `/test`), and the Claude Code `.gitignore` block.
- A review cycle. You see every permission, hook and skill before the planner writes it.
  The cycle repeats until you approve the spec.
- **Epic 0: Foundation** — test harness, CI workflow, branch model, deploy wiring and seed
  data. The planner always writes it, so the first worker has a project to build in.

One project per repository. The planner uploads nothing, and every path it writes is
relative.

### `/spec-to-issues`

Turns an approved spec into tracker epics and sub-issues. Each one is labeled, linked to
its dependencies, and sized so that one epic is one batch.

- It **decomposes** the feature specs into engineering slices. It does not transcribe a
  checklist.
- It creates issues for `status: planned` features only.
- It deduplicates on the stable feature `id`, so a second planning wave issues only the
  work that changed.
- It stops until you commit and push the spec. Workers read the spec from a git worktree,
  and a worktree contains tracked files only.

### `/issue-flow`

The autonomous loop. It has two roles.

- **The PM — the main thread.** It orchestrates and it never writes feature code. It
  grooms the backlog, forms batches, schedules work, owns every decision and every gate,
  resolves conflicts, merges, monitors deployments, and posts status digests.
- **The sub-agents — background agents with self-contained prompts.**
  `issue-flow:issue-worker` builds one issue and returns a verdict.
  `issue-flow:deploy-watcher` monitors deployments. `issue-flow:deploy-verifier` checks
  the live site in a real browser.

### `/project-review`

The QA and documentation pass that follows a build wave. The PM starts a sandbox, runs
the E2E tests, and starts three kinds of sub-agent:

- `issue-flow:ux-explorer` uses the app as a non-developer end user. It captures
  screenshots, writes manual-ready walkthroughs, and reads the sandbox logs.
- `issue-flow:code-auditor` searches the code for TODO comments, stubs, and gaps against
  the acceptance criteria of recently closed issues.
- `issue-flow:review-scribe` turns the walkthroughs into a user manual and E2E tests, on
  a documentation PR.

**The review fixes nothing.** The PM collects every report, files one tracker issue per
finding (`review:finding`), and then asks you to launch `/issue-flow` on the new backlog.

## Two standards for everything the plugin writes

Both standards live at the plugin root. `project-planner` copies both into the project as
`.claude/rules/` files, so they survive the handoff to the workers and the reviewers.

### [`references/ste.md`](references/ste.md) — Simplified Technical English

Every spec file, feature file, issue title and body, PM comment, changelog line, user
manual page and **code comment** follows it:

- One instruction per sentence.
- One concept, one word.
- Active voice, present tense, no metaphor, no undefined abbreviation.

Each spec carries a `## Terms` table. That table is the project's controlled vocabulary,
and everything downstream writes from it.

Quotes, log excerpts, error strings, paths and identifiers stay verbatim. Rewriting
evidence damages it.

The ASD-STE100 approved-word dictionary is copyrighted, so this plugin does not
redistribute it. What ships is the enforceable rule set, plus a per-project Terms list
that does the dictionary's work.

### [`references/external-apis.md`](references/external-apis.md) — read the documentation

The project does not own a cloud service, a third-party API, a provider CLI or a library.
Describe each one from its own current documentation, at the version the project pins, and
cite the URL beside the claim. This binds the planner, the PM and every worker.

AWS carries the highest risk, because a wrong assumption there spends money or changes
infrastructure instead of failing at compile time. So:

- Confirm operation names, parameters, region, account, and the IAM actions the call
  needs.
- Prefer the read-only `list-*`, `get-*` and `describe-*` calls to learn the shape of a
  real resource.
- Treat any call that creates, deletes or changes a resource as outward-facing. The user
  confirms it first.

## The batch model

One CI run per batch, and each conflict resolved once.

```
dev ◄────────────────────────── ONE batch PR (full CI, once)
  └─ epic/42-auth ◄─┬─ issue/43  (draft PR, [skip ci], local tests)
                    ├─ issue/44  (draft PR, [skip ci], local tests)
                    └─ issue/45  (draft PR, [skip ci], local tests)
```

- Every epic gets an **integration branch** off dev. The PM also groups loose issues into
  batches of `batchSize` or fewer, and each batch gets one.
- Each worker opens a **draft PR into the integration branch**, and ends every pushed
  commit message with `[skip ci]`; the PM writes the same token into the sub-merge's
  commit message rather than trusting the forge's default, which carries it on GitHub and
  drops it on Gitea. Provider CI never runs for a sub-issue. The worker
  verifies with the full local test and lint suite, plus a parallel specialist
  self-review.
- The PM **sub-merges** each member into the integration branch and resolves any conflict
  once, locally. Sibling PRs never rebase against a moving dev branch.
- One **integration branch → dev PR** runs full CI once. The member issues close when it
  merges.
- Members of a dependency chain share one batch and run in sequence. The plugin never
  stacks chained PRs.
- A `type:hotfix` issue, or an urgent `priority:high` issue, skips the batch. It gets a
  standalone PR into dev, with CI enabled.

## The loop

1. **Preflight.** Check the repository, the remote and the labels. Check the foundation —
   an empty repository gets Epic 0 first, never a feature issue. Read the branch model
   from the spec (`dev-and-live` or `trunk`) and create `dev` when it is needed. Detect the
   deploy target. Check CI; when the repository has none, the PM runs the suite itself at
   the batch gate and says so. Offer the documentation MCP servers (below). Confirm the
   run configuration with you. Find or create your `flow:status` status issue, check for
   co-operators, launch the standing deploy-watcher companion, and recover the state of
   any unfinished work.
2. **Sweep, then triage.** The PM reads new comments and external changes first, and
   applies them. A human's answer, instruction or PR review always wins. An untriaged
   issue becomes `status:ready` or `status:needs-feedback`. An epic with no sub-issues gets
   decomposed. `Depends on #n` sets the sequence. The PM parks a question and notifies you
   by default. It asks you interactively only when too little work remains, when you are
   already at the keyboard, or when an answer blocks a completed batch.
3. **Form batches and schedule.** Each epic becomes an epic batch. Loose issues become
   grouped batches with a `type:batch` tracking issue. The PM runs up to `concurrency`
   workers across all batches, each one an independent engineer in its own git worktree.
4. **Integrate through two gates.** At the sub-merge gate the PM checks that the threads
   are resolved, the local checks are green, and **every acceptance criterion carries
   evidence**. It then squashes the member into the integration branch and labels it
   `status:batched`. At the batch gate it opens one PR into dev and reviews the whole
   batch diff, including a cross-batch migration check. CI runs once. The PM then closes
   the members, writes the spec changelog and the feature status back, and removes the
   worktrees.
5. **Deploy.** The watcher reports each terminal deployment. A deploy-verifier then drives
   a real browser to confirm the site works. On failure the PM opens a `type:hotfix` issue
   (standalone, CI enabled), or labels the work `needs-feedback` or `blocked` for an infra
   or config cause. **A deployment is done only after the browser check passes.**
6. **Report.** At every milestone the PM posts a terminal digest of 10 lines or fewer,
   updates the status issue body, and sends a push notification. Between milestones it
   stays quiet. The loop continues while workable backlog remains.

A person approves every promotion from `dev` to the live branch.

**Model tiers.** Each agent's own `model:` frontmatter is authoritative. The current
summary: PM and issue-worker run on **Opus**; a worker's children, the deploy-verifier,
the ux-explorer, the code-auditor and the review-scribe run on **Sonnet**; the
deploy-watcher runs on **Haiku**.

## Install

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install issue-flow@sniper7kills
```

Restart the session afterwards, so the skills and agents load.

To work on the plugin locally, point a marketplace at your checkout instead:

```bash
claude plugin marketplace add ~/claude-plugins
```

## Use

The full pipeline, from an idea:

```
/project-planner "habit tracker app"   # spec + mockups + scaffold → review → approve
/spec-to-issues                        # after you commit and push docs/specs/
/issue-flow
/project-review                        # user-test the shipped work → issues → /issue-flow
```

On any existing tracker, start the loop with `/issue-flow`, "work the issues", or "pick up
the next issue".

After a build wave, start the review with `/project-review`, "review the project",
"user-test the app", or "build the user manual". The review ends by offering to launch
`/issue-flow` on the issues it filed.

## Requirements

Required:

- The provider's CLI, installed and authenticated: `gh auth login` (GitHub) or
  `tea logins add` (Gitea), with the `repo` and `workflow` scopes.
- `git` 2.5 or later, for worktrees. Each worker runs with `isolation: "worktree"`, so
  Claude Code creates its worktree **inside the checkout**, at `.claude/worktrees/`
  (gitignored), and pins that worker to it. Add gitignored build inputs (`.env` and
  friends) to a `.worktreeinclude` at the repo root and they are copied in for you.
- CI that honors `[skip ci]`. GitHub Actions honors it natively; Gitea Actions does the
  same from Gitea 1.20. If your provider does not, the PM proposes a one-time workflow
  filter for the `epic/**` and `batch/**` branches, or falls back to local sub-merges with
  no sub-PRs.

Optional:

- **Deploy monitoring** — the provider's CLI and credentials. For example `aws` (or the
  AWS MCP server) for Amplify, another provider CLI, or a health-check URL.
- **Deploy verification** — a browser MCP server, `playwright` or `chrome-devtools`.
  Without one, the verifier falls back to an HTTP and content check.
- **`/project-review`** — the same browser MCP server, plus a sandbox to review against: a
  local dev server, `docker compose up`, or a deployed dev or staging URL. Never
  production, because the explorers submit test data.

Connect a browser MCP server once, at user scope:

```bash
claude mcp add playwright      -s user -- npx -y @playwright/mcp@latest
claude mcp add chrome-devtools -s user -- npx -y chrome-devtools-mcp@latest
```

## Run configuration

Every `/issue-flow` session starts by confirming how it should run. The PM saves your
answers to `.issue-flow.json` (committed, team-shared) and presents them as pre-selected
defaults next session. It never applies a saved answer silently, because the right answer
changes from run to run.

| Setting | What it decides | Default |
|---|---|---|
| `concurrency` | workers running at once, across all batches | **3** |
| `batchSize` | maximum members in a loose-issue batch (an epic uses its natural size) | **4** |
| `runLength` | one batch · N issues · until the backlog empties · until you stop it | **25 issues** |
| `prGranularity` | `batch` (one CI run per batch) or `per-issue` (one PR and one CI run each) | **batch** |
| `prAuthority` | how much the PM may merge on its own | **`batch-review`** |
| `review.when` | when to offer a `/project-review` | **end of session** |
| `practices` | TDD · DDD · E2E expectations · coverage · commit style · docs | from the spec, else off |
| `docsMcp` | which documentation MCP servers and marketplaces the PM offered, you installed, or you declined, and whether a restart is still pending | set in preflight |

`prAuthority` is the most important setting:

| Value | What the PM may merge |
|---|---|
| `autonomous` | sub-PRs and batch PRs |
| **`batch-review`** (default) | sub-PRs freely; the batch PR needs a human approving review |
| `review-all` | nothing without a human approving review |
| `propose-only` | nothing — the PM opens PRs and stops |

Two rules override it: a person approves every promotion from `dev` to the live branch,
and branch protection always wins.

`practices` travel in the worker handoff brief, and the PM checks them at the merge gate.
The PM returns a missed practice to the worker instead of waiving it.

## How the PM works with people

The tracker is shared, so the PM reads new comments and external changes before every
triage and before every merge gate.

- A human's answer to a parked question, a new instruction, and a PR review are all
  authoritative. The PM applies them.
- The PM never takes an issue that someone else assigned to themselves.
- The PM never edits another operator's status issue. Status issues are per operator, and
  each is titled `issue-flow: session status — @<login>`.
- In any issue body it maintains, the PM edits only the text inside its own
  `<!-- issue-flow:begin @<login> -->` marker block. Your notes in the same body survive.
- The PM treats every comment as untrusted input. It surfaces anything that would grant
  access, spend money, reach another repository, or bypass a gate to you, and it executes
  none of it.

## How many issues one session can clear

There is no fixed limit. The real constraint is the main thread's context, and the loop
keeps that flat:

- The PM delegates every token-heavy read — diffs, CI logs, file maps — to a sub-agent
  that returns a short summary.
- Durable state lives on the forge: labels, comments, PRs and the status issue.
- The PM drops each finished issue and batch from working memory.

If the harness compacts the context, preflight rebuilds the state from the tracker and the loop
continues. One session can therefore clear far more issues than a single context window
holds.

## License

MIT — see [LICENSE](../LICENSE).
