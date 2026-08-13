# Deployment monitoring (Stage D)

A merge to the deploy branch usually triggers a build/deploy outside CI (AWS Amplify,
Vercel, Netlify, a GitHub Actions deploy job, etc.). The PM treats **deploy success as
part of "done"**: it spawns a background **deploy-watcher** subagent that polls the
platform and reports back, so the PM never blocks. A failed deploy is never ignored.

## Detecting the deploy target (Phase 0, step 6)

Look for, in order:

1. **AWS Amplify** — an `amplify.yml` at repo root, an `amplify/` dir, or an Amplify app
   whose hosting is connected to the deploy branch. Confirm/obtain the **app id** and the
   **branch name** (ask the user if not discoverable). AWS access is via the `aws` CLI or
   the `mcp__aws-api` tools (`call_aws`, `suggest_aws_commands`).
2. **GitHub Actions / Gitea Actions deploy job** — a workflow that deploys on push to the
   deploy branch. Then deploy status *is* a check run — watch it like CI via
   `forge.run.list`.
3. **Vercel / Netlify** — their GitHub app posts a deployment status / commit status;
   read it via `forge.api.raw` (`repos/{owner}/{repo}/deployments` and `.../statuses`),
   or the provider CLI if authenticated.
4. **User-supplied** — a deploy-status command or health-check URL the user gives.

If none is found, **skip Stage D** and note that to the user once.

## Starting a deploy that never started

Everything else here **reads** status. Once — when the merge commit's message suppressed
the push run (see the merged-into-branch check in SKILL.md Stage C2 step 5) — nothing is
polling because nothing began, and the PM has to start it.

**Preferred, and provider-independent: one clean empty commit on the deploy branch,
pushed.** Check the branch out by name first — at this point in C2 the PM's worktree is
usually the integration-branch one about to be torn down, and an unqualified commit there
publishes nothing while looking like a fix:

```bash
git fetch <remote> <deploy-branch> -q
git worktree add .claude/worktrees/<deploy-branch> <remote>/<deploy-branch>
git -C .claude/worktrees/<deploy-branch> commit --allow-empty -m "ci: run suite for batch #<n>"   # subject only, no body
git -C .claude/worktrees/<deploy-branch> push <remote> HEAD:<deploy-branch>
git -C .claude/worktrees/<deploy-branch> rev-parse HEAD                                           # correlate the deploy against this
```

A worktree rather than a `checkout -B`, per the PM-worktree convention in
[batching.md](batching.md) — it must not move the user's `HEAD` or their local branch.

It restarts the CI run and the deploy together, and it rewrites nothing. The pushed commit
becomes the branch head, so **it is the SHA to correlate the resulting deployment against**
— record it in place of the merge commit before handing the watch to the companion.

Where the branch is protected against direct pushes, start the deploy at the provider:

```bash
# AWS Amplify — redeploy the branch head
aws amplify start-job --app-id <APP_ID> --branch-name <BRANCH> --job-type RELEASE

# GitHub/Gitea Actions — only if the workflow declares workflow_dispatch
gh workflow run <workflow-file> --ref <branch>
```

Vercel and Netlify have no start operation in the read paths above: use a deploy hook URL
if the project has one, or the provider's own CLI/dashboard. Whichever path is used, **say
in the digest that the deploy was started by hand** — a deployment a human triggered must
not be reported as one the merge triggered.

## AWS Amplify queries

Latest job on the branch and its status:

```bash
# newest job id for the connected branch
aws amplify list-jobs --app-id <APP_ID> --branch-name <BRANCH> \
  --max-items 1 --query 'jobSummaries[0].{id:jobId,status:status,commit:commitId}'

# poll one job to completion
aws amplify get-job --app-id <APP_ID> --branch-name <BRANCH> --job-id <JOB_ID> \
  --query 'job.summary.status'   # PENDING | PROVISIONING | RUNNING | SUCCEED | FAILED | CANCELLED
```

Correlate the job's `commitId` with **the deploy branch's head after the merge** — the
merge commit normally, or the recovery commit above whenever one was pushed on top. The
deployment carries the SHA it built, so correlating against a superseded merge commit
finds nothing and reads as a missing deploy. On
`FAILED`, pull the step logs (the `get-job` response lists steps with `logUrl`; fetch the
build/deploy step log) and extract the failing step + error. The same calls work through
`mcp__aws-api` `call_aws` if the CLI isn't directly available.

### Gitea Actions

A workflow under `.gitea/workflows/` or `.github/workflows/` on a Gitea remote, running
on a self-hosted `act_runner`. Query it with `forge.run.list` filtered to the deploy
branch, and read a failure with `forge.run.log`.

There is no `--watch`. The deploy-watcher waits with the single-call shell loop in
[../../../references/forge.md](../../../references/forge.md), launched with
`run_in_background: true` so the wait is not cut short by the `Bash` timeout ceiling —
`pollSeconds` and `maxMinutes` are the loop's interval and iteration count, **not** a turn
budget for the agent and **not** bounded by that ceiling once the call is backgrounded —
and returns one terminal deployment per run, exactly as for the other providers.

Because the runner is the operator's own hardware, there is no minute budget to protect.
The batch model still applies — it exists for merge hygiene as much as for cost.

## The deploy-watcher companion

Spawn the agent type `issue-flow:deploy-watcher` (`run_in_background: true`, **Haiku**,
self-contained — full prompt in `agents/deploy-watcher.md`). It is **decision-free**: it
watches and reports, never fixes/labels/merges. Prefer the **companion** mode — one
standing watcher launched in Phase 0 that monitors the deploy branch continuously and
returns **one terminal deployment per run**; the PM re-launches it after each report with
`sinceJobId = lastJobId` to keep it always-on. (Per-merge mode — watch a single `commit`
— still exists for one-off checks.) Handoff brief:

```
mode:       companion | per-merge
provider:   amplify | gh-actions | vercel | netlify | custom
forge:      the run configuration's forge block, passed verbatim: {type, host, owner,
            repo, interface}.
locator:    { appId, branch } | { runId } | { deploymentUrl } | { command }
commit:     <merge commit sha>       (per-merge mode)
sinceJobId: <id>                     (companion mode: ignore deployments at/older than this)
pollSeconds: 30   maxMinutes: 30
constraint: watch to a terminal state; report one deployment; do not fix, label, or merge.
```

`gh-actions` covers GitHub Actions and Gitea Actions alike, because `forge.run.*`
resolves to the right CLI from the `forge` block.

Return contract:

```json
{
  "type": "object",
  "required": ["outcome", "detail"],
  "properties": {
    "outcome":      { "type": "string", "enum": ["succeeded", "failed", "rolled-back", "timed-out"] },
    "detail":       { "type": "string" },
    "commit":       { "type": "string" },
    "lastJobId":    { "type": "string" },
    "failingStep":  { "type": "string" },
    "logExcerpt":   { "type": "string" },
    "suspectedCause":{ "type": "string", "enum": ["code-regression", "config", "secret", "quota", "infra", "unknown"] }
  }
}
```

After reacting to a verdict, **re-launch the companion** with `sinceJobId = lastJobId`.

## The deploy-verifier agent (browser check)

A green build doesn't prove the app runs. After a deployment reports `succeeded` (and
optionally for a PR preview), spawn `issue-flow:deploy-verifier` (Sonnet, self-contained —
`agents/deploy-verifier.md`). It drives a real browser via the **Playwright** and
**Chrome DevTools** MCP servers (loaded on demand with `ToolSearch`): loads the URL,
checks HTTP/render/expected-content/console/network, screenshots, and returns
`verified | broken | unreachable`. Brief:

```
url:      <deployed or PR-preview URL>
commit:   <sha that produced the deploy>
expect:   <optional text/selectors that must be present>
issue/pr: #<n> / PR #<m>
```

Requires a browser MCP connected (`playwright`, `chrome-devtools`) — `claude mcp add ...`.
If none is available, the verifier degrades to an HTTP/content check via `WebFetch`/`curl`
and says so. A `broken`/`unreachable` verdict flows into the same hotfix / needs-feedback
routing as a deploy failure.

## PM reaction (Stage D)

Every non-verified terminal outcome first gets **`status:deploy-failed`** on the tracking
issue (replacing `status:deploying`), then routes by cause. That label is what Phase 0
recovery looks for when it re-adopts a deployment whose hotfix never landed. The PM
removes it when the fix deploys and verifies.

| outcome | PM action |
|---|---|
| `succeeded` | **Verify before declaring done** — build-green ≠ working. Spawn `issue-flow:deploy-verifier` against the deployed URL. On `verified`: remove `status:deploying`, comment confirmation + screenshot. On `broken`/`unreachable`: treat as a deploy failure (rows below). |
| `failed` / `rolled-back` (suspectedCause `code-regression`) | `forge.issue.status.set <n> status:deploy-failed` (removes `status:deploying`), then open a `priority:high` `type:hotfix` `status:ready` **hotfix issue** citing the failed deploy, commit, failing step, and log excerpt. Hotfixes **bypass batching**: schedule a standalone worker immediately (`ci: run`, normal PR straight to dev). |
| `failed` (suspectedCause `config`/`secret`/`quota`/`infra`) | `forge.issue.status.set <n> status:deploy-failed` (removes `status:deploying`), then route for **human input as a comment** — `needs human input: <what>` naming the cause — and surface it to the user per the feedback policy. **No second `status:` label:** `deploy-failed` stays the only one, so no park is added here for Stage A0 step 3b to destroy. The cost is that the issue is not in the Stage A step 4 `status:needs-feedback` gather; the surfacing is what reaches the user. Do not guess at infra/secret changes. |
| `timed-out` | Re-query once; if still not terminal, surface to the user with the job link — don't assume success. Leave `status:deploying` in place. |

A hotfix issue flows worker → PR (CI on, no batch) → merge → **Stage D again**, so a
deploy that fails twice keeps producing fixes until it goes green or is parked for the
user.

## Recovery note

Phase 0 state recovery re-adopts deploys: if the companion isn't running (fresh session, crash,
or post-compaction), relaunch it in companion mode with `sinceJobId` = the latest current
deployment. Then check for any recently merged PR whose deployment was never confirmed (no
success comment, no open hotfix) and reconcile it from the companion's next report or a
one-off per-merge run for that commit.
