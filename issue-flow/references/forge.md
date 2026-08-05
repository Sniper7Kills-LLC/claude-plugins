# The forge — how this plugin reaches the tracker

This plugin runs on **GitHub** or on **Gitea**. The loop, the batch model, the gates and
the labels are identical on both. Only the commands that reach the tracker differ, and
this file is the single place that records them.

**Supported versions.** GitHub through the `gh` CLI, any current version. Gitea **1.20 or
later** — 1.20 is the release that honors `[skip ci]` natively, which the batch model
depends on. Verified against Gitea **1.25.3**, `tea` **0.15.1**, and Gitea MCP server
**v1.6.0**.

## Detection

Work out the forge once, in Phase 0, and record it.

1. **A `forge` block in `.issue-flow.json` wins.** It is explicit, so never override it.
2. **Otherwise read the remote.** `git remote get-url <remote>`. A `github.com` host is
   GitHub. Any other host is a Gitea candidate. **GitHub Enterprise is GitHub**, even
   though its host is not `github.com` — the version probe and the ask below still catch
   a wrong guess.
3. **Confirm a Gitea candidate** before you rely on it: `curl -s <scheme>://<host>/api/v1/version`
   returns a version, or `tea logins list` shows a login for that host. **An SSH remote**
   (`git@host:owner/repo.git`) has no scheme and no port, so you cannot form the `curl`
   probe from it directly — use the `tea logins list` check instead.
4. **Ambiguous means ask.** A host you cannot confirm, or two plausible answers, is an
   `AskUserQuestion` — never a guess. Defaulting silently to GitHub against a Gitea
   remote produces a session of failing commands.

## Interfaces

Each forge has a command-line interface and an MCP server. **Prefer the CLI.**

| Forge | CLI (primary) | MCP (fallback) |
|---|---|---|
| GitHub | `gh` | GitHub MCP server |
| Gitea | `tea` | Gitea MCP server |

The CLI is primary on both, for one reason that matters: **`gh` and `tea` both infer the
owner and repository from `$PWD`, and the MCP servers do not.** Workers run inside git
worktrees, so `$PWD` inference works there and identifiers threaded through a handoff
brief would be one more thing to get wrong.

**One exception.** For **large Actions logs**, prefer the Gitea MCP's
`actions_run_read(method: "get_job_log_preview", max_bytes, tail_lines)`. Bounded reads
serve the context-discipline invariant better than a CLI that returns the whole log.

**Escape hatch.** `gh api` and `tea api` both make raw authenticated requests. Use either
for anything this table does not cover, and add the row when you do.

## Operation table

`<n>` is an issue number, `<pr>` a pull-request number, `<ts>` an ISO 8601 timestamp.

### Session and repository

| Operation | GitHub (`gh`) | Gitea (`tea`) | Gitea MCP |
|---|---|---|---|
| `forge.auth.check` | `gh auth status` | `tea logins list` | `get_me` |
| `forge.auth.login` | `gh auth login` | `tea logins add --name <n> --url <url> --token <t>` | n/a |
| `forge.user.login` | `gh api user --jq .login` | `tea whoami` | `get_me` |
| `forge.repo.view` | `gh repo view --json nameWithOwner,defaultBranchRef` | `tea api /repos/{owner}/{repo}` | `search_repos` |
| `forge.repo.create` | `gh repo create <name> --private --source=. --push` | `tea repos create --name <name> --private` | `create_repo` |
| `forge.api.raw` | `gh api <path>` | `tea api <path>` | n/a |

**A token on the command line lands in shell history and the process list.** Phase 0
already has the user run `forge.auth.login` themselves, through `!`, so this is a
documentation caution, not a plugin behavior.

**`tea repos list` has no default-branch field.** Its `--fields` option covers
`description,forks,id,name,owner,stars,ssh,updated,url,permission,type` only, and Phase 0
needs the default branch to work out `live` and to check whether `Closes #` auto-closes a
member issue at batch merge. Use `tea api /repos/{owner}/{repo}`, which returns the full
repository object including `default_branch`.

**`tea api` expands `{owner}` and `{repo}` from the current repository's remote, and only
inside a checkout.** Run it outside a checkout and the same command returns a 404. Phase 0
and workers both run inside a checkout, so `forge.repo.view`, `forge.pr.view` and
`forge.pr.diff` work as written. Outside a checkout, substitute the owner and repository
name literally.

### Labels

| Operation | GitHub (`gh`) | Gitea (`tea`) | Gitea MCP |
|---|---|---|---|
| `forge.label.list` | `gh label list` | `tea labels list --output json` | `label_read` |
| `forge.label.create` | `gh label create "<name>" --color <hex> --description "<d>"` | `tea labels create --name "<name>" --color "#<hex>" --description "<d>"` | `label_write(method: "create_repo_label")` |

**Two differences.** `tea` wants a leading `#` on the color; `gh` does not. And the
**Gitea MCP takes numeric label IDs, never names** — resolve names through `label_read`
first. `tea` takes names, which is one more reason it is the primary interface.

**`tea labels create` is not idempotent.** `gh label create --force` updates an existing
label; `tea labels create` has no such flag, and running it twice with the same name
does not error — it silently creates a second label with that name, exit 0. Check
`tea labels list --output json` for the name first, and create only when it is missing
(see the bootstrap block in [labels.md](../skills/issue-flow/references/labels.md)).

### Issues

| Operation | GitHub (`gh`) | Gitea (`tea`) | Gitea MCP |
|---|---|---|---|
| `forge.issue.list` | `gh issue list --state open --json number,title,labels,assignees,updatedAt` | `tea issues list --state open --output json --fields index,title,labels,assignees,updated` | `list_issues(state: "open")` |
| `forge.issue.list.since` | `gh issue list --state all --search "updated:>=<ts>"` | `tea issues list --state all --from <ts> --output json` | `list_issues(since: "<ts>")` |
| `forge.pr.list.since` | `gh pr list --state open --search "updated:>=<ts>"` | `tea issues list --kind pulls --state open --from <ts> --output json` | `list_issues(type: "pulls", since: "<ts>")` |
| `forge.issue.view` | `gh issue view <n> --comments` | `tea api /repos/{owner}/{repo}/issues/<n>` plus `tea comments <n>` | `issue_read` |
| `forge.issue.create` | `gh issue create --title "<t>" --body "<b>" --label "<l>"` | `tea issues create --title "<t>" --description "<b>" --labels "<l>"` | `issue_write(method: "create")` |
| `forge.issue.label.add` | `gh issue edit <n> --add-label "<l>"` | `tea issues edit <n> --add-labels "<l>"` | `issue_write(method: "add_labels")` — **IDs** |
| `forge.issue.label.remove` | `gh issue edit <n> --remove-label "<l>"` | `tea issues edit <n> --remove-labels "<l>"` | `issue_write(method: "remove_label")` — **ID** |
| `forge.issue.assign` | `gh issue edit <n> --add-assignee @me` | `tea issues edit <n> --set-assignees <me>` | `issue_write(method: "update", assignees)` |
| `forge.issue.comment` | `gh issue comment <n> --body "<b>"` | `tea comments <n> "<b>"` | `issue_write(method: "add_comment")` |
| `forge.issue.edit.body` | `gh issue edit <n> --body "<b>"` | `tea issues edit <n> --description "<b>"` | `issue_write(method: "update", body)` |
| `forge.issue.close` | `gh issue close <n>` | `tea issues close <n>` | `issue_write(method: "update", state: "closed")` |

**`forge.issue.view` reads one issue, not the whole tracker.** `tea api
/repos/{owner}/{repo}/issues/<n>` returns one issue, and its cost does not grow with
backlog size. `tea issues <n>` does not exist.

**`tea` has no `@me`.** Resolve your own login with `forge.user.login` first and pass it
literally.

**`--add-assignees` does not work on Gitea.** The `tea issues edit --add-assignees` form makes
a POST to a nonexistent endpoint and fails with a 404; the assignment is silent. Use `--set-assignees`
instead. Note that `--set-assignees` replaces the entire assignee list, not append — this is
the correct behavior for a claim lock, but it will displace any pre-existing assignee.

### Pull requests

| Operation | GitHub (`gh`) | Gitea (`tea`) | Gitea MCP |
|---|---|---|---|
| `forge.pr.create.draft` | `gh pr create --draft --base <base> --title "<t>" --body "<b>"` | `tea pr create --draft --base <base> --title "<t>" --description "<b>"` | `pull_request_write(method: "create", draft: true)` |
| `forge.pr.create` | `gh pr create --base <base> --title "<t>" --body "<b>"` | `tea pr create --base <base> --title "<t>" --description "<b>"` | `pull_request_write(method: "create")` |
| `forge.pr.ready` | `gh pr ready <pr>` | `tea pr edit <pr> --ready` | `pull_request_write(method: "update", title)` — strip `WIP: ` |
| `forge.pr.view` | `gh pr view <pr> --json state,reviews,mergeable` | `tea pr list --output json`, or `tea api /repos/{owner}/{repo}/pulls/<pr>` | `pull_request_read` |
| `forge.pr.diff` | `gh pr diff <pr>` | `tea api /repos/{owner}/{repo}/pulls/<pr>.diff` | `pull_request_read` |
| `forge.pr.reviewer.add` | `gh pr edit <pr> --add-reviewer <user>` | `tea pr edit <pr> --add-reviewers <user>` | `pull_request_write(method: "add_reviewers")` |
| `forge.pr.thread.resolve` | `gh api graphql` with the `resolveReviewThread` mutation | `tea pr resolve <comment-id>` | `pull_request_review_write(method: "resolve_thread")` |
| `forge.pr.merge.squash` | `gh pr merge <pr> --squash --delete-branch` | `tea pr merge <pr> --style squash`, then `forge.branch.delete` | `pull_request_write(method: "merge", merge_style: "squash", delete_branch: true)` |
| `forge.pr.merge.commit` | `gh pr merge <pr> --merge --delete-branch` | `tea pr merge <pr> --style merge`, then `forge.branch.delete` | `pull_request_write(method: "merge", merge_style: "merge", delete_branch: true)` |
| `forge.branch.delete` | folded into `--delete-branch` | `tea pr clean <pr>`, or `git push <remote> --delete <branch>` | `delete_branch` |

**Draft pull requests on Gitea are a title prefix.** `tea pr create --draft` prepends
`WIP: `, and Gitea treats a WIP-prefixed pull request as a draft. `tea pr edit --draft`
adds the prefix idempotently and `--ready` strips a leading `WIP: ` or `[WIP]`. The
CI-free draft sub-pull-request model therefore works unchanged.

**`tea pr merge` cannot delete the branch.** There is no `--delete-branch` flag, so
teardown is a separate `forge.branch.delete` step. Do not skip it — an undeleted
integration branch is re-adopted as live work by Phase 0 state recovery.

**`Closes #<n>` works differently on each forge.** On GitHub, it closes the linked
issue only when the pull request merges into the default branch. On Gitea, it closes
when the pull request merges into any branch. The plugin requires sub-pull-requests to
omit closing keywords because a sub-pull-request that closes an issue on Gitea closes
it before the batch lands. The worker enforces this rule for both forges.

### Actions and CI

| Operation | GitHub (`gh`) | Gitea (`tea`) | Gitea MCP |
|---|---|---|---|
| `forge.run.list` | `gh run list --branch <b> --json databaseId,status,conclusion` | `tea actions runs list --branch <b> --output json` | `actions_run_read(method: "list_runs")` |
| `forge.run.view` | `gh run view <id>` | `tea actions runs view <id>` | `actions_run_read(method: "get_run")` |
| `forge.run.log` | `gh run view <id> --log-failed` | `tea actions runs logs <id>` | `actions_run_read(method: "get_job_log_preview", max_bytes, tail_lines)` — **preferred** |
| `forge.pr.checks` | `gh pr checks <pr> --watch` | poll `forge.run.list` with `--branch <b>` set to the head branch | `actions_run_read(method: "list_runs")` |

**`tea actions runs list` filters by branch.** `--branch <b>` narrows the list to one
branch, and `--status`, `--event`, `--actor`, `--since` and `--until` narrow it further.

**Gitea has no `--watch`.** Poll `forge.run.list` on an interval instead of blocking, and
keep the polling in a subagent so the log volume never reaches the PM.

**`[skip ci]` is native on both.** Gitea Actions honors `[skip ci]`, `[ci skip]`,
`[no ci]`, `[skip actions]` and `[actions skip]` in the head commit message from 1.20
onward. The "CI runs once per batch" invariant needs no Gitea-specific workaround.

## Capability gaps

**Gitea has no sub-issue API.** Checked against 1.25.3: `/repos/{owner}/{repo}/issues/{index}`
exposes `dependencies` and `blocks`, but no `sub_issues`. Epic decomposition on Gitea
therefore uses the fallback this plugin already documents for GitHub — a `Part of #<n>`
line in each child body plus a task-list checkbox in the epic body. No new concept, and
no behavior change.

**Gitea has issue dependencies that GitHub does not.** `/issues/{index}/dependencies` and
`/issues/{index}/blocks` model the `Depends on #<n>` relationship natively. This plugin
does not use them yet. Recorded here as an opportunity, not a requirement — do not build
scheduling logic on an endpoint only one forge has.

## Safety — two Gitea calls that break the merge gate

The Gitea MCP's `pull_request_write` accepts two parameters with no `gh` equivalent, and
both defeat gates this plugin exists to enforce:

- **`force_merge: true`** merges with failing checks. The hard rule is *never merge with
  red checks*. Do not pass it. A red check is a fix worker, never a flag.
- **`merge_when_checks_succeed: true`** merges with no human at the gate. Under every
  `prAuthority` except `autonomous`, a human approving review is required, and this
  parameter removes the human. Do not pass it.

Neither is ever the answer to a blocked merge. If a merge will not proceed, the gate is
working — read why, and fix the cause.

Branch protection on the Gitea repository wins over `prAuthority`, exactly as it does on
GitHub. Never route around it, and never merge as an administrator to bypass it.
