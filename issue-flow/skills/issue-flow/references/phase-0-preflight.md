# Phase 0 — Preflight (run once per session)

**Look for the preflight digest before spending a single turn on mechanics.** The plugin
ships a `SessionStart` hook (`hooks/preflight.py`) that, in a repo with a committed
`.issue-flow.json`, has already run `git fetch` and put an `issue-flow preflight:` block
into your context: remote name, default branch, live `epic/*`/`batch/*` branches,
worktrees from an earlier session (check each issue's status before removing one — step 12), missing standard labels, and — when the forge CLI answered — open PRs
and parked issues. Hooks cost CPU, not inference, and the fetch it already ran is what
kills the stale-ref failure class measured in live runs. When the block is present, do
not re-derive those facts with your own tool calls: verify anything surprising, then
spend the saved turns on the judgment steps (foundation check, branch model, run
configuration). When it is absent — hook disabled, no `.issue-flow.json` yet, CLI not
authenticated — run every step below yourself; the hook is an accelerator, never a
dependency.

1. **Repo and forge check.** `git rev-parse --is-inside-work-tree`, then work out which
   forge this repo lives on — see [../../references/forge.md](../../../references/forge.md).
   - Not a git repo → ask the user to initialize; if yes, `git init` + initial commit.
   - Git repo, no forge remote → ask whether to create one, then `forge.repo.create`
     (confirm public/private first — outward-facing).
   - Get the owner and repository name from `git remote get-url <remote>` first, then
     read the repo with `forge.repo.view` to get the default branch.
   - CLI not authenticated (`forge.auth.check`) → tell the user to run `! gh auth login`
     or `! tea logins add`, whichever this forge needs, and stop until done.
   - **Record `forge.type`, `forge.host`, `forge.owner` and `forge.repo`** in the run
     configuration now. The MCP interfaces need owner and repo on every call, and a
     worker that has them never has to re-derive them.
2. **Detect the remote name.** `git remote` — use the actual name (usually `origin`, never hardcode) for all fetch/branch/push. Call it `<remote>`.
3. **Label bootstrap.** Ensure the standard labels exist (idempotent). See [references/labels.md](labels.md). Create missing ones with `forge.label.create`; never delete/rename labels the project already uses.
4. **Foundation check — is there a project to build in?** Before anything else, look at
   what the repo actually contains: any commits, a package manifest / build file, a test
   command, a CI workflow. A repo with none of these cannot support a feature issue —
   workers land with nothing to run, `practices` are unenforceable, and the batch CI gate
   is a no-op.
   - Spec-driven project → the spec's **`Epic 0: Foundation`** is the answer. Schedule it
     first and let nothing else start until it merges.
   - No spec, or a spec without a foundation epic → say so plainly and offer to generate
     a foundation epic (repo scaffold, test harness, lint/typecheck, CI workflow, branch
     model, deploy wiring, seed data) as issues before feature work. Do not silently
     schedule feature issues into an empty repo.
   - Existing codebase with the pieces already present → note what's missing (no CI, no
     test command) and carry it into steps 5–7 rather than assuming.
5. **Branch model.** The model is a **planning decision**, not something to infer from
   whatever branches happen to exist.
   - Read `branch_model` from `docs/specs/spec.md` front-matter when a spec exists
     (`dev-and-live` | `trunk`). No spec, or no field → **ask the user once**: a `dev`
     integration branch with user-approved promotion to live, or trunk-only where every
     batch merges to the default branch and deploys.
   - **live** = the repo default branch (usually `main`/`master`).
   - **dev-and-live**: use the existing `dev` / `develop` / `development` branch, or
     **offer to create `dev`** off the default branch now (Epic 0 does this on a
     greenfield project). Integration branches fork off dev, batch PRs target dev, and
     promotion dev→live is a separate user-approved action.
   - **trunk**: live == dev; integration branches fork off and batch PRs target the
     default branch. Say once, plainly, that every batch merge is a production change.
   - Note whether **dev is the default branch** — `Closes #` keywords only auto-close on the default branch; when dev ≠ default the PM closes member issues manually at batch merge (Stage C).
6. **Deploy target detection.** Does a merge to the deploy branch trigger a deployment
   you must monitor in Stage D? The plugin ships **no provider integrations** — a hosting
   platform (Amplify, Vercel, a Kubernetes rollout) is a project architecture choice, and
   the project supplies the way to query it. Work through these in order; the command
   contract and the watch loop are in [references/deploy.md](deploy.md).
   1. **An explicit `deploy` block in `.issue-flow.json` wins.**
   2. **A deploy workflow in the forge's own Actions** (a workflow under
      `.github/workflows/` or `.gitea/workflows/` that deploys on push to the deploy
      branch) → deploy status *is* a run; Stage D watches it with the same
      commit-anchored background loop as CI (`mode: actions`).
   3. **A status command the project ships or the user supplies** —
      `scripts/deploy-status.sh`, a `deploy-check` project skill, or a command given
      once at preflight — printing `<state> <jobId> <sha>` (`mode: command`). This is
      where a platform the project chose gets wired: by the project (Epic 0), never by
      this plugin.
   4. **Capture the deployed URL.** Record the production (default-branch) URL. Record the
      PR-preview URL pattern too, when the platform builds previews. Stage D cannot
      browser-verify a deployment without this.
   5. **Ask when it is ambiguous.** Two candidates, or a platform with no status command
      wired, is a question for the user — not a guess.

   Record the answers in the run configuration's `deploy` block (step 11). No deploy
   target found → skip Stage D, and tell the user once that deployments are not
   monitored.
7. **CI check.** Two questions, in order.
   - **Is there any CI at all?** If the repo has no workflows, say so plainly — *"there is
     no CI in this repo, so the batch gate verifies nothing on its own"* — and repeat it
     in the first digest. Until a CI workflow exists (Epic 0 lands one), **the PM runs the
     project's full suite itself on the integration branch before merging any batch**:
     an isolated subagent (`isolation: "worktree"`, `base: <remote>/<integration-branch>`)
     runs the project's test/lint/typecheck/build commands and returns a short pass/fail
     summary. That independent run — not a worker's self-reported `localChecks` — is the
     batch gate.
   - **Does the CI honor `[skip ci]`?** (GitHub Actions does natively; Gitea Actions does natively from Gitea 1.20. Both key on the head commit message.) If the provider doesn't (or workflows use `workflow_dispatch`/schedule triggers that don't care), fall back to the alternatives in [references/batching.md](batching.md) (path filters, branch filters excluding `epic/**`+`batch/**` — proposing that workflow edit to the user once).
8. **Documentation access — offer the MCP servers (and marketplaces) that provide it.** Workers must
   read real API docs rather than assume ([references/external-apis.md](../../../references/external-apis.md)),
   and a connected documentation MCP server is far better at that than `WebFetch`. So
   before any work starts:
   - **See what's already connected.** `claude mcp list`, plus a `ToolSearch` for
     doc-serving tools. Anything already there needs no offer — note it and move on.
   - **Work out what the project actually depends on.** The spec's
     `Architecture & stack` / `Interfaces` sections when there is a spec; otherwise the
     package manifest, lockfile, IaC files and `amplify.yml`-style config. You want the
     named external services: cloud provider, payment, auth, data store, major framework.
   - **Search the marketplaces the user has configured.** `claude plugin marketplace list`,
     then read each listing for a server covering one of those services.
   - **Offer a marketplace when the one you need is not added yet.** A doc server often
     ships inside a plugin from a marketplace the user has never added. You may offer to
     add it — `claude plugin marketplace add <source>` — **only when you have a concrete
     source**: one the user just gave you, or one named in this repo (its README,
     `CLAUDE.md`, spec, or `.claude/settings.json`). **Never invent a marketplace URL, a
     plugin name, a server name, or an install command, and never present a guess as
     available.** No concrete source → say you found nothing and move on.
   - **Offer with `AskUserQuestion`** — one option per server (or marketplace) you actually
     found, each naming the service it covers and the exact command, plus an explicit
     "none of these". **Adding a marketplace or installing an MCP server runs third-party
     code and changes the user's configuration. They run the command, or they explicitly
     approve it. Never do it silently.**
   - **Say plainly that new servers need a session restart.** A marketplace added, a
     plugin installed, or an MCP server connected during this session usually does not
     appear until the session restarts (`/mcp` shows what is live now). Tell the user
     that, and tell them the loop keeps running meanwhile on the `WebFetch` fallback.
     **Never stop the loop waiting for a restart** — record the decision and carry on.
   - Record the answer as `docsMcp` in the run configuration (step 11) so a decline is not
     re-asked every session. Nothing found, or all declined → say so once, plainly: doc
     lookups fall back to `WebFetch` against the vendors' own documentation, which is
     still required, just slower.
9. **Identity & session status issue.** `forge.user.login` → `<me>`.
   Find-or-create an open issue titled `issue-flow: session status — @<me>` labeled
   `flow:status`. The PM keeps its **body** updated with the current digest (shipped /
   in-flight / blocked / awaiting-feedback / active config) at every milestone — readable
   from anywhere, survives restarts, and is a recovery input. The digest lives between
   `<!-- issue-flow:begin @<me> -->` / `<!-- issue-flow:end @<me> -->` markers so human
   notes in the same body survive your edits. Never assign it to a worker; triage skips
   `flow:status`.
10. **Co-operator check.** List other open `flow:status` issues updated in the last 24h —
   each is another person running this loop on the same repo. Read their in-flight issue
   numbers and integration branches, treat them as taken, and never edit their status
   issue. See [references/collaboration.md](collaboration.md). This runs
   **before** the run configuration on purpose: whether a co-operator is active decides
   which file step 11 writes.
11. **Run configuration — confirm with the user, every session.** Load `.issue-flow.json`
   (repo root, committed) if present, seed missing values from `docs/specs/spec.md` when
   there is a spec, and **re-confirm interactively** in two batched `AskUserQuestion`
   rounds with the saved values pre-selected. Never apply a saved config silently. It
   covers: `concurrency` (workers in flight, default 3), `batchSize` (max members in a
   loose batch, default 4), `runLength` (one batch / N issues / until the backlog is
   empty / until stopped), `prGranularity` (batch vs per-issue PRs), **`prAuthority`**
   (how much may the PM merge on its own — default `batch-review`: the batch PR needs a
   human approving review; a standalone/hotfix/per-issue PR into dev follows the same
   batch-PR column), **`planReview`** (hold each batch's plans for operator approval
   before its first launch — Stage B step 4b), **`reviewWipLimit`** (soft cap on batches
   parked `status:awaiting-review` before the PM stops forming new ones — Stage B step
   1), `review.when` (when to offer a `/project-review`), `practices`
   (TDD, DDD, E2E expectations, coverage, commit style, docs), and the **`deploy`** block
   step 6 detected. Write the
   answers back to `.issue-flow.json` (a gitignored `.issue-flow.local.json` overrides it
   per operator, so concurrent sessions don't fight over the committed file — write the
   local file when step 10 found a co-operator, the committed file otherwise, and say
   which). When the preflight digest reported **plugin-version drift**, ask about every
   option the saved file lacks a value for instead of defaulting it, and stamp the
   installed version as `pluginVersion` when you write the committed file. Full option
   tables, the authority matrix and how practices are enforced:
   [references/session-config.md](session-config.md).
   **Worker worktrees are created by the harness, not by you.** Launch every worker with
   `isolation: "worktree"` (Stage B step 5) and the harness makes its worktree under
   `.claude/worktrees/agent-<id>`, pinned to that worker alone. **You learn the path from
   the worker's completion notification** — it carries a `<worktree>` block with
   `worktreePath` and `worktreeBranch` (measured on 2.1.224). The worker also reports its
   `pwd` as `worktree` in its verdict; use that when the notification is unavailable — on
   the `general-purpose` fallback path, or after a session restart. Either way you pass no
   path in.
   **Never call `EnterWorktree` in the PM, and never let a worker call it.** `EnterWorktree`
   writes a *session-scoped* variable shared by the PM and every live worker, so the last
   caller wins: the PM and its siblings are all dragged into one worker's tree and
   cross-worktree `git -C` starts being refused. `isolation: "worktree"` sets a
   *per-agent* root instead, which no sibling can overwrite. Plain `git worktree add` is
   **not** the hazard and stays available to you for integration-branch work (sub-merges,
   suite runs) — drive it with `git -C <path>`, never by entering it. See
   [references/worktrees.md](worktrees.md).
   **Env files:** worktrees are fresh checkouts of *tracked* files, so gitignored `.env`s
   are missing and env-dependent suites fail as `blocked`. The harness copies the
   project's `.worktreeinclude` matches in natively — keep that file accurate rather than
   copying by hand. If there is no `.worktreeinclude` but the repo obviously needs env
   files, ask the user once, write the file, and record the answer.
   **Permissions:** background workers cannot answer a permission prompt — nobody is
   there to ask. Every command they need must already be in the committed
   `.claude/settings.json` allow-list. If a worker returns `blocked` on a permission
   prompt, that is a settings gap: surface the exact command to the user, get it
   allow-listed, and re-run the issue rather than retrying blindly.
12. **State recovery.** Re-adopt unfinished work before picking new work:
   - `git branch -r` entries `epic/*` / `batch/*` → live batches: reconcile against their tracking issue's checklist (which members sub-merged, which are in flight).
   - `git worktree list` entries on `issue/` branches whose issue is **not** parked (`status:needs-feedback` / `status:blocked` — a parked tree was kept on purpose, for its batch gate or because it holds unpushed or uncommitted work; leave it and list it in the digest) → leftovers from a previous session's workers, which you cannot re-enter and no new worker can be placed into. Adopt the work from the **branch and its PR**, not the directory: re-spawn with `base: <remote>/issue/<n>-<slug>`, then `git worktree remove -f -f` the orphan (a session killed mid-run leaves the lock behind, and plain `--force` refuses a locked tree), `git worktree prune`, and run the dead harness-branch sweep ([worktrees.md](worktrees.md#teardown-is-still-the-pms-job) — a previous session's notification is gone, so its `worktree-agent-<id>` name is too).
   - In-flight workers / open PRs (draft sub-PRs and open batch PRs) → resume at the right stage (sub-merge, batch gate, CI, integrate). A `worker launched: <agentId>` comment from an earlier session names a dead agent: never `SendMessage` it — re-spawn from the published branch and its PR.
   - Issues/tracking issues labeled **`status:awaiting-review`** → a previous session stopped holding a PR for a human. Re-check the PR: an approving review landed → resume at the merge it was waiting on; changes requested → route to a fix worker and re-request review; still waiting → carry it in the digest and leave it, don't re-request review on every session.
   - Recently merged batch PRs whose deployment hasn't been confirmed → resume Stage D.
   - Tracking issues labeled **`status:deploy-failed`** → a deployment failed and its fix
     never landed. Check for the hotfix issue: open and unworked → schedule it; never
     opened → open it now; merged and deployed green → clear the label and comment.
   - Issues `status:in-progress` with no worktree and no PR → resume or reset to `status:ready`.
