# Deployment monitoring (Stage D)

A merge to the deploy branch usually triggers a build/deploy outside CI. The PM treats
**deploy success as part of "done"**: after each merge to the deploy branch it launches
**one background shell watch**, anchored to the merged head SHA, and reacts when the
shell exits — so the PM never blocks. A failed deploy is never ignored.

## The platform is a project choice, not a plugin feature

This plugin ships **no provider integrations**. AWS Amplify, Vercel, Netlify, a
Kubernetes rollout — each is an architecture choice the *project* made, and the project
supplies the way to query it. The plugin knows exactly two generic paths:

1. **`mode: actions`** — the deploy runs as a workflow in the forge's own Actions
   (GitHub Actions or Gitea Actions). Deploy status *is* a run: Stage D watches it with
   the same commit-anchored background loop as CI
   ([../../../references/forge.md](../../../references/forge.md)), filtered to the
   deploy workflow, anchored to the merged head SHA. `no-run-registered` there maps to
   `no-deployment-observed` below.
2. **`mode: command`** — the project ships a **status command** that answers "what is
   the newest deployment of this branch". `project-planner`'s Epic 0 wires it
   (`scripts/deploy-status.sh`, or a `deploy-check` project skill); an existing project
   supplies it in `.issue-flow.json` or the user gives it once at preflight. The
   provider vocabulary lives inside that script — that is where "Amplify" belongs, if
   the project chose Amplify.

No status command, no deploy workflow, nothing from the user → **skip Stage D** and say
so once. Never guess a provider query.

## Detecting the deploy target (Phase 0, step 6)

In order — the first hit wins:

1. An explicit `deploy` block in `.issue-flow.json`.
2. A workflow in the forge's Actions that deploys on push to the deploy branch
   (`.github/workflows/` or `.gitea/workflows/`) → `mode: actions`, record the workflow
   file name.
3. A status command the repo ships (`scripts/deploy-status.sh`, a `deploy-check` /
   `deploy-status` project skill) → `mode: command`.
4. A status command or health URL the user supplies → `mode: command`.

Also capture the **deployed URL** (and the PR-preview URL pattern when the platform
builds previews) — Stage D cannot browser-verify without it. Ambiguity is an
`AskUserQuestion`, never a guess. Record everything in the run configuration's `deploy`
block ([session-config.md](session-config.md)).

## The status command contract (`mode: command`)

One-shot, read-only, exits fast. Prints **one line** describing the newest deployment of
the deploy branch:

```
<state> <jobId> <sha>
```

- `state` is normalized to `pending | running | succeeded | failed | rolled-back`. The
  mapping from the provider's own vocabulary (`SUCCEED`, `READY`, …) is written once,
  inside the project's script.
- `jobId` is the platform's deployment/job id — used in comments and links.
- `sha` is the commit the deployment built. The watch correlates on it.
- A query failure exits non-zero. The watch reports it as `watch-error` — never a pass.

The command must already be in the committed `.claude/settings.json` allow-list: the
watch runs in a background shell with nobody to answer a permission prompt.

## The watch — one background shell per merge

Launched by the PM at Stage D, after each merge to the deploy branch, with
`run_in_background: true` — one turn to launch, one to read the verdict, however long
the deploy takes (the same ceiling reasoning as the CI watch in
[../../../references/forge.md](../../../references/forge.md)). Anchor it to **the head
SHA of the deploy branch after the merge** — normally the merge commit, or the recovery
commit whenever one was pushed on top (SKILL.md Stage C2 step 5). The deployment carries
the SHA it built; correlating against a superseded commit finds nothing and reads as a
missing deploy.

```bash
# ONE Bash call, run_in_background: true. Watches the deployment of $SHA.
# Only the three <>-placeholders on the assignment lines are substituted; everything
# below them is literal shell, run as written.
SHA=<head of the deploy branch after the merge>
STATUS_CMD=<the deploy block's statusCmd, e.g. ./scripts/deploy-status.sh>
POLL_SECONDS=<pollSeconds from the deploy block, default 30>
MAX_MINUTES=<maxMinutes from the deploy block, default 30>
seen=0; newest=""
for _ in $(seq 1 $((MAX_MINUTES*60/POLL_SECONDS))); do
  line=$($STATUS_CMD) || { echo "watch-error: status command failed"; exit 1; }
  set -- $line; state=$1; job=$2; sha=$3; newest=$sha
  if [ "$sha" = "$SHA" ]; then
    case "$state" in
      succeeded|failed|rolled-back) echo "$state $job $sha"; exit 0 ;;
      pending|running) seen=1 ;;
    esac
  fi
  sleep "$POLL_SECONDS"
done
if [ "$seen" = 1 ]; then echo "timed-out $SHA"
elif [ -n "$newest" ] && [ "$newest" != "$SHA" ]; then echo "superseded $newest $SHA"
else echo "no-deployment-observed $SHA"
fi
```

**The parse contract** — the PM reads exactly one line, and the first
whitespace-separated token is the verdict:

| line shape | meaning |
|---|---|
| `succeeded\|failed\|rolled-back <jobId> <sha>` | terminal state observed for our SHA |
| `timed-out <sha>` | ours was seen `pending`/`running` but never terminal in budget |
| `superseded <newestSha> <sha>` | ours was never observed; the branch's newest deployment is a different commit |
| `no-deployment-observed <sha>` | nothing deployed at all during the watch |
| `watch-error: <text>` (exit 1) | the status query itself failed — never a verdict |

Five properties worth keeping if you rewrite it:

- **The SHA anchor** — a stale or unrelated deployment can never be mistaken for yours.
- **`no-deployment-observed` distinct from `timed-out`** — "nothing ever started" and
  "started but never finished" have opposite remedies (see the reaction table).
- **`superseded` distinct from `no-deployment-observed`** — the status command returns
  the branch's *newest* deployment, so a deploy replaced before the first poll (the
  hotfix-after-failed-batch shape) is invisible to the anchor. Without this verdict it
  reads as "nothing deployed", whose remedy pushes a recovery commit — an empty push
  per iteration against a pipeline that is running normally.
- **`watch-error` distinct from both** — a failed query is never a verdict.
- **Success is proven, not assumed** — only an observed terminal `succeeded` counts.

For `mode: actions`, the loop is the commit-anchored Actions watch in
[../../../references/forge.md](../../../references/forge.md), filtered to the deploy
workflow; its `no-run-registered` is this table's `no-deployment-observed`.

`pollSeconds` (default 30) and `maxMinutes` (default 30) come from the `deploy` block.

## PM reaction (Stage D)

Every non-verified terminal outcome first gets **`status:deploy-failed`** on the tracking
issue (replacing `status:deploying`), then routes by cause. That label is what Phase 0
recovery looks for when it re-adopts a deployment whose hotfix never landed. The PM
removes it when the fix deploys and verifies.

| verdict | PM action |
|---|---|
| `succeeded` | **Verify before declaring done** — build-green ≠ working. Spawn `issue-flow:deploy-verifier` against the deployed URL. On `verified`: remove `status:deploying`, comment confirmation + screenshot. On `broken`/`unreachable`: treat as a deploy failure (rows below). |
| `failed` / `rolled-back` (cause `code-regression`) | `forge.issue.status.set <n> status:deploy-failed` (removes `status:deploying`), then open a `priority:high` `type:hotfix` `status:ready` **hotfix issue** citing the failed deploy, commit, failing step, and log excerpt. Hotfixes **bypass batching**: schedule a standalone worker immediately (`ci: run`, normal PR straight to dev — the merge gate follows the batch-PR column of `prAuthority`, [session-config.md](session-config.md)). |
| `failed` (cause `config`/`secret`/`quota`/`infra`) | `forge.issue.status.set <n> status:deploy-failed` (removes `status:deploying`), then route for **human input as a comment** — `needs human input: <what>` naming the cause — and surface it to the user per the feedback policy. **No second `status:` label:** `deploy-failed` stays the only one, so no park is added here for Stage A0 step 3b to destroy. The cost is that the issue is not in the Stage A step 4 `status:needs-feedback` gather; the surfacing is what reaches the user. Do not guess at infra/secret changes. |
| `timed-out` | A deployment was observed but never reached a terminal state in the budget. Re-query once (run the status command directly). Still not terminal → surface to the user with the job id/link — never assume success. Leave `status:deploying` in place. |
| `superseded` | The branch's newest deployment is a different commit — ours was replaced before it was observed (typically a hotfix landing right behind the batch). Confirm the newer SHA contains ours (`git merge-base --is-ancestor $SHA <newestSha>`), then watch the **newest** SHA instead — its verdict covers both commits. If the newer SHA does *not* contain ours, the branch was force-moved: surface that to the user. Never push a recovery commit on this verdict. |
| `no-deployment-observed` | Nothing deployed at all during the watch. First run the status command **once, directly**: a newer deployment now in flight means this is really `superseded` — follow that row, push nothing. Then re-run the merged-head token check (SKILL.md Stage C2 step 5): a suppressed head starts no deploy — push the clean recovery commit and watch the new SHA, **once**; a second `no-deployment-observed` on the recovery SHA is a wiring problem (webhook, platform config) — surface it, do not push again. Never a pass. |
| `watch-error` | The status command itself failed — usually a permission refusal or an auth problem. Fix the cause (allow-list the exact command, re-authenticate) and relaunch the watch. Never a pass. |

**Classify the cause before routing a failure.** Delegate the log read to a short-lived
unnamed subagent; take back the failing step, a short excerpt, and one of:

- `code-regression` — build/test/runtime error traceable to the change.
- `config` — bad build settings, env/branch config, redirects.
- `secret` — missing/expired credential, auth failure.
- `quota` — limits, throttling, capacity.
- `infra` — platform/region outage, provisioning failure.
- `unknown` — the logs do not say.

The PM takes back the cause, not the log.

A hotfix issue flows worker → PR (CI on, no batch) → merge → **Stage D again**, so a
deploy that fails twice keeps producing fixes until it goes green or is parked for the
user.

## Starting a deploy that never started

Everything else here **reads** status. Once — when the merge commit's message suppressed
the push run (see the merged-into-branch check in SKILL.md Stage C2 step 5) — nothing is
building because nothing began, and the PM has to start it.

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
becomes the branch head, so **it is the SHA to watch** — record it in place of the merge
commit before launching the watch.

Where the branch is protected against direct pushes, start the deploy the project's own
way: `gh workflow run <workflow-file> --ref <branch>` when the deploy workflow declares
`workflow_dispatch`, the project's wired start command (`deploy.startCmd` when the config
carries one), or the platform's own redeploy control. Whichever path is used, **say in
the digest that the deploy was started by hand** — a deployment a human triggered must
not be reported as one the merge triggered.

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

## Recovery note

Phase 0 state recovery re-adopts deploys: check for any recently merged PR into the
deploy branch whose deployment was never confirmed (no success comment, no open hotfix).
For each, run the status command once (or list the deploy workflow's runs for that SHA):
already terminal → react to it now via the table above; still in flight → launch a watch
for that SHA and carry on.
