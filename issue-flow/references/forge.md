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
| `forge.issue.status.set` | `gh issue edit <n> --add-label "<new>" --remove-label "<old>"` | `tea issues edit <n> --add-labels "<new>" --remove-labels "<old>"` | `issue_write(method: "add_labels")` **then** `issue_write(method: "remove_label")` |
| `forge.issue.assign` | `gh issue edit <n> --add-assignee @me` | `tea issues edit <n> --set-assignees <me>` | `issue_write(method: "update", assignees)` |
| `forge.issue.comment` | `gh issue comment <n> --body-file <path>` | `tea api -X POST /repos/{owner}/{repo}/issues/<n>/comments --data @<json>` (`{"body": …}`) | `issue_write(method: "add_comment")` |
| `forge.issue.edit.body` | `gh issue edit <n> --body "<b>"` | `tea issues edit <n> --description "<b>"` | `issue_write(method: "update", body)` |
| `forge.issue.close` | `gh issue close <n>` | `tea issues close <n>` | `issue_write(method: "update", state: "closed")` |

**Every status change uses `forge.issue.status.set`, never a bare `forge.issue.label.add`.**
An issue carries at most one `status:` label ([labels.md](../skills/issue-flow/references/labels.md)),
so a transition is one operation with two halves — and on both CLIs it is a **single command**.
On the Gitea MCP interface it is **two calls that must not be separated**: issue the
`remove_label` immediately after the `add_labels`, with nothing between them, and confirm the
issue carries exactly one `status:` label before you do anything else. The measured failure is
the second half being dropped, and the MCP path is the one where dropping it is easy.
Reaching for `label.add` alone is the easy mistake and it leaves the issue in two states at
once, which silently poisons every later query that selects by status: a member sitting in
both `status:in-review` and `status:batched` still answers the search for work awaiting
review, forever. Measured twice in live runs, on every member of two separate batches.
Whenever you add a `status:` label, name the one you are removing in the same call.

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
| `forge.pr.create.draft` | `gh pr create --draft --base <base> --title "<t>" --body-file <path>` | `tea api -X POST /repos/{owner}/{repo}/pulls --data @<json>` (`{"title": "WIP: <t>", "head": …, "base": …, "body": …}`) | `pull_request_write(method: "create", draft: true)` |
| `forge.pr.create` | `gh pr create --base <base> --title "<t>" --body-file <path>` | `tea api -X POST /repos/{owner}/{repo}/pulls --data @<json>` (`{"title", "head", "base", "body"}`) | `pull_request_write(method: "create")` |
| `forge.pr.ready` | `gh pr ready <pr>` | `tea pr edit <pr> --ready` | `pull_request_write(method: "update", title)` — strip `WIP: ` |
| `forge.pr.view` | `gh pr view <pr> --json state,reviews,mergeable` | `tea api /repos/{owner}/{repo}/pulls/<pr>` (by number; by branch, see below) | `pull_request_read` |
| `forge.pr.diff` | `gh pr diff <pr>` | `tea api /repos/{owner}/{repo}/pulls/<pr>.diff` | `pull_request_read` |
| `forge.pr.reviewer.add` | `gh pr edit <pr> --add-reviewer <user>` | `tea pr edit <pr> --add-reviewers <user>` | `pull_request_write(method: "add_reviewers")` |
| `forge.pr.thread.resolve` | `gh api graphql` with the `resolveReviewThread` mutation | `tea pr resolve <comment-id>` | `pull_request_review_write(method: "resolve_thread")` |
| `forge.pr.merge.squash` | `gh pr merge <pr> --squash --delete-branch` | `tea pr merge <pr> --style squash`, then `forge.branch.delete` | `pull_request_write(method: "merge", merge_style: "squash", delete_branch: true)` |
| `forge.pr.merge.commit` | `gh pr merge <pr> --merge --delete-branch` | `tea pr merge <pr> --style merge`, then `forge.branch.delete` | `pull_request_write(method: "merge", merge_style: "merge", delete_branch: true)` |
| `forge.branch.delete` | folded into `--delete-branch` | `tea pr clean <pr>`, or `git push <remote> --delete <branch>` | `delete_branch` |

**Bodies go through a file** — PR bodies and comments (`forge.pr.create*`,
`forge.issue.comment`). Write the text with the `Write` tool (a worker writes it in its own
scratch directory; a `cat > file <<EOF` heredoc carrying the body was refused by the
worktree guard in a dogfood run), then pass the path. Two reasons, both measured:

- Backticks inside a double-quoted `--body "…"` / `--description "…"` run as command
  substitution before the forge sees the text, so `` `tally --x` `` silently becomes
  nothing — or runs a command.
- In a worktree-isolated worker, the guard refused an inline multi-line body
  ("runs tea with the text … cannot be shown not to be git") and refuses any body built
  with `$(…)`. The file forms above were allowed on both forges (Claude Code 2.1.285).

On Gitea the JSON file holds the fields; build it with `jq -n --rawfile body <file>
'{title: "WIP: <t>", head: "<branch>", base: "<base>", body: $body}' > <json>` so quoting
cannot break it.

**Both merge rows are incomplete on purpose: always add the explicit message** (and check
the branch afterwards) — see *Never let a merge write its own commit message* below. The
default message decides whether the merged-into branch runs CI, and it differs per forge.

**`forge.pr.view` by *branch* needs a list, not a view — on both forges.** A replacement
worker adopting an already-open PR (the routine path after a `checkpoint`) looks the PR up
by its head ref. Measured on gh 2.88.1, Gitea 1.25.3 and tea 0.15.1:

- **GitHub:** `gh pr view <branch>` resolves a branch, but it returns a PR for that branch
  **whatever its state** — a closed or merged PR comes back with exit 0 — and it exits 1
  both for "no PR" and for a real failure. List open PRs by head instead; empty output is
  the "none" answer and a non-zero exit is an error:

  ```bash
  gh pr list --head "issue/<n>-<slug>" --state open --json number --jq '.[].number'
  ```

- **Gitea:** the list endpoint ignores `head=` and returns one page per call —
  `default_paging_num` 30, `limit` capped at `max_response_items` 50 (`/api/v1/settings/api`
  reports both). An unpaged call therefore misses every PR past the first page and reports
  "none" while one is open: measured with 55 open PRs, the oldest branch's PR was not found.
  `tea api` also exits 0 on an API error and prints the error object, so check the shape.
  Page until an empty page — a short page is not proof of the end, because the cap is
  per-server. This loop is for the PM. **A worker cannot run it:** in a worktree-isolated
  agent the guard refuses a `tea` call whose URL is built at runtime (`page=$page`) —
  measured on Claude Code 2.1.284. A worker makes one call per page with the number typed
  in (`agents/issue-worker.md`, runbook step 2). The PM's loop:

  ```bash
  page=1
  while :; do
    batch=$(tea api "/repos/{owner}/{repo}/pulls?state=open&limit=50&page=$page") || exit 1
    printf '%s' "$batch" | jq -e 'type == "array"' >/dev/null \
      || { printf '%s\n' "$batch" >&2; exit 1; }
    [ "$(printf '%s' "$batch" | jq length)" -eq 0 ] && break
    printf '%s' "$batch" | jq -r --arg b "issue/<n>-<slug>" '.[] | select(.head.ref == $b) | .number'
    page=$((page + 1))
  done
  ```

  `GET /repos/{owner}/{repo}/pulls/{base}/{head}` does exist and answers directly, but it
  needs the PR's base — and a replacement worker's brief carries `base:
  <remote>/issue/<n>-<slug>`, not the integration branch the PR targets.

Read the result the same way on both forges:

- **Empty output, exit 0:** no PR is open for that branch. Run the lookup once more before
  you act on it. Gitea pages by offset, so a PR closed on an earlier page during the scan
  shifts a later PR onto a page already read — measured: closing PR #50 between page 1
  and page 2 made page 2 skip PR #5.
- **A non-zero exit:** the lookup failed. It never means "none".
- **More than one line:** a bug worth stopping on, not a pick-the-first situation.

**Draft pull requests on Gitea are a title prefix.** `tea pr create --draft` prepends
`WIP: `, and Gitea treats a WIP-prefixed pull request as a draft. `tea pr edit --draft`
adds the prefix idempotently and `--ready` strips a leading `WIP: ` or `[WIP]`. The
CI-free draft sub-pull-request model therefore works unchanged.

**`tea pr merge` cannot delete the branch.** There is no `--delete-branch` flag, so
teardown is a separate `forge.branch.delete` step. Do not skip it — an undeleted
integration branch is re-adopted as live work by Phase 0 state recovery.

**Never let a merge write its own commit message.** A merge commit's message decides
whether the branch you merged into runs CI, the default message differs per forge and per
repository setting, and the two forges fail in opposite directions. All of the following is
measured, GitHub against Actions and Gitea against a 1.25.3 instance with a live runner:

| | GitHub | Gitea 1.25.3 |
|---|---|---|
| Token matched in the **body**, not only the subject | yes — `total_count: 0` for the commit | yes — `total_count: 0` for the commit |
| **Default** squash message | pull request title **plus every commit message folded into the body** — so a member's `[skip ci]` comes along, and the commit registers **no run** | pull request title and `(#n)` **only** — the fold does not happen, no token survives, and the commit **registers a run** |
| **Explicit** message | `--subject`/`--body` honored | `--title`/`--message` honored (`tea` and the MCP both) |

So the fold is a GitHub default, not a law — and relying on it breaks in both directions.
On GitHub it silently *suppresses* what you wanted tested: squash a **batch** pull request
and every member's token lands on `dev`, the post-merge push registers no run at all, and
any push-triggered deploy never starts. An absent check reads like a pending one, never a
red one. On Gitea it silently *un-suppresses* what you wanted skipped: every sub-merge into
the integration branch starts a full run, so a batch of four members burns four runs and
the "one CI run per batch" invariant is gone — visible only as runs nobody explains.

State the message explicitly at every merge, with the token when the result must stay
CI-free (sub-merge) and without it when the result must be tested (batch merge):

| | CI-free result (sub-merge into the integration branch) | CI-visible result (batch merge into dev) |
|---|---|---|
| GitHub | `gh pr merge <pr> --squash --subject "<title> (#<pr>)" --body "[skip ci]" --delete-branch` | `gh pr merge <pr> --squash --subject "<title> (#<pr>)" --body "" --delete-branch` |
| Gitea | `tea pr merge <pr> --style squash --title "<title> (#<pr>)" --message "[skip ci]"`, then `forge.branch.delete` | same with `--message ""` |
| Gitea MCP | `pull_request_write(method: "merge", merge_style: "squash", title: "<title> (#<pr>)", message: "[skip ci]", delete_branch: true)` | same with `message: ""` |

Then verify the **branch you merged into**, not the pull request, and do it after **every**
merge whatever the style — a repository can be configured to build its merge-commit message
from the pull request title and description too, which folds a batch pull request's body
(and this plugin's digests do discuss the token) onto `dev` through the plain `--merge`
path. The branch is the only place that shows what actually landed:

```bash
git fetch <remote> <base> -q
git log -1 --format='%s%n%b' <remote>/<base> \
  | grep -ciE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true
```

Read the count against what you intended: `0` after a batch merge means the head will be
tested; `0` after a sub-merge means CI just started on the integration branch. Non-zero is
the reverse. **Match the exact tokens here, not a bare `skip`** — this check reads
machine-generated history containing other people's subjects, and `fix: skip empty rows in
the parser` folded in from a member would otherwise declare a healthy `dev` suppressed and
send the PM into a remediation it does not need. The looser `grep -ciE 'skip|no ci'` stays
right for the pre-push check on a message you are about to write yourself, where a false
positive costs one reworded subject.

**The set is the five bracketed forms, and deliberately not GitHub's `skip-checks` trailer.**
GitHub does honor `skip-checks:true` / `skip-checks: true` (the space is optional), but only
as a git trailer — the docs require the trailers section to be **preceded by two empty
lines**, and measurement agrees: after one empty line the same text registers a run, after
two it registers none. That matters twice here. A line-based `grep -E` cannot see blank-line
context, so `^skip-checks: ?true$` would match the one-empty-line form that *does* run CI —
a false positive declaring a healthy `dev` suppressed, the exact class this exact-token
pattern exists to avoid. And the suppressing form cannot reach these commits anyway: `git
commit -m … -m …` collapses consecutive empty lines under the default
`--cleanup=whitespace`, and a squash fold joins commit messages with single blank lines. So
the trailer is documented here and left out of the regex on purpose. Gitea matches the
bracketed forms from `SKIP_WORKFLOW_STRINGS` and has no trailer form. If a forge adds a
token, this regex is the one place to widen — the loose pre-push pattern already catches
anything containing `skip`.

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
| `forge.pr.checks` | `gh pr checks <pr> --watch` | the commit-anchored shell loop below | `actions_run_read(method: "list_runs")` |

**The `tea actions …` rows need Gitea ≥ 1.26.0.** `tea` refuses the whole `actions`
family against an older server (`gitea server at <host> is older than 1.26.0`). The
underlying REST endpoints exist well before that, so on a 1.25.x server — including the
1.25.3 this file is verified against — reach them with
`tea api "/repos/{owner}/{repo}/actions/…"`, or use the Gitea MCP column.

**`forge.pr.checks` is one blocking call, never a turn per status check.** Every agent
turn re-reads the agent's whole context, so a 30-minute watch at one turn per check costs
60 full-context round trips instead of one. Resolve it through the abstraction like any
other operation — never hardcode `gh`. On GitHub it is already blocking and takes the PR
number the worker already has; on Gitea it resolves to the watch script below. Keep either in a
subagent so log volume never reaches the PM.

**Launch that call with `run_in_background: true`.** One blocking call is the right shape,
but a *foreground* one cannot outlast the harness's `Bash` ceiling: **120000 ms by default,
600000 ms maximum** (`BASH_DEFAULT_TIMEOUT_MS` / `BASH_MAX_TIMEOUT_MS`). Ten minutes is the
hard limit unless the operator raised it, and two is what an agent gets if it does not pass
`timeout` explicitly. CI runs routinely exceed both. When the call is killed the verdict is
**lost, not delayed** — the agent sees a timeout error instead of `success` / `failure` and
has to start the wait over, which is how a watch that never resolves becomes a merge on an
unread check.

A background shell keeps running across turns and re-invokes the agent when it exits, so
the ceiling stops applying and the cost is **one turn to launch, one to read the verdict,
regardless of how long CI takes**. That is strictly better than the foreground call on both
axes. Both waiting paths — this one and the Stage D deploy watch
([../skills/issue-flow/references/deploy.md](../skills/issue-flow/references/deploy.md)) —
use it, and they use it the same way:

1. Launch the watch (the `gh pr checks <pr> --watch` call, or the Gitea watch script
   below) with `run_in_background: true`. Do not pass a `timeout`; do not `sleep` in the
   foreground waiting on it. The tool result carries the **path to the shell's output
   file** — keep it; that file is where the verdict lands.
2. **Wait for that shell to exit.** Do other in-scope work if you have any; otherwise end
   your turn. The harness wakes you when the shell exits, and the notification carries
   the same output path.
3. Read the output file, take the one-line verdict, and only then act on it or return it.

**A subagent must not return its verdict before the shell exits.** This applies to the
worker watching its own CI. The PM's deploy watch runs on the main thread and returns no
verdict. A subagent's final text *is* its return, and a guess sent before the shell
exits is a verdict nobody tested. Measured on Claude Code 2.1.284: a subagent that ends its turn
while its background shell runs is woken when the shell exits, and its next reply
becomes its result. But the harness also notifies the caller at once, marked interim
("stopped with background work of its own still running"), carrying whatever the
subagent last said. So end that turn with a one-line status that cannot be read as a
verdict — `waiting on CI for PR #<n>` — and send the verdict only after reading the
finished shell's output. The PM ignores the interim notification (SKILL.md, *Reading a
worker notification*). A watch
you launched and walked away from is not a watch.

The loop's own `sleep` interval and iteration count are unchanged — they bound the *watch*,
not the tool call, and `maxMinutes` may now exceed ten because nothing kills it at ten.
A verdict that never arrives is still not a pass: an elapsed budget returns `timed-out`.

**Do not build the Gitea watch on `tea actions runs list`.** Two measured reasons:

- **It is not the API object.** `tea` renders that command as a flattened *table*, so the
  JSON rows carry only `id`, `status`, `workflow`, `branch`, `event`, `started`,
  `duration` — every value a string, with **no `conclusion` and no `head_sha`**. Pass/fail
  is therefore absent: `status` only ever holds `queued`, `waiting`, `in_progress` or
  `completed`, while the `success`/`failure`/`cancelled`/`skipped` word lives in
  `conclusion`. And `.[0]` is not the newest run — rows sort descending by `id` compared
  *as a string*, so with runs 9 and 10 present, `.[0]` is run **9**. A loop keyed on
  `.[0]` reads a stale run; if that stale run is green it reports success for a commit
  that was never tested, which is the worst failure a merge gate can be fed.
- **It does not exist before Gitea 1.26.0** (see the version note above). Every call
  fails outright, so a loop built on it degrades to a silent timeout rather than an error.

Use **`tea api`** instead. It is an authenticated passthrough to the REST API, it is not
version-gated, it returns the real object (`status`, `conclusion`, `head_sha`), and it
substitutes `{owner}`/`{repo}` from the current checkout — which the worker always has.
The Actions endpoint filters by `head_sha` **server-side**, so anchor the watch to the
commit you just pushed rather than to "the latest run on the branch":

The loop ships as a script, `scripts/gitea-ci-watch.sh` in the plugin. Run it from the
checkout, with `run_in_background: true`, and type the full SHA in — get it first with
`git rev-parse HEAD` as a call of its own:

```bash
bash <pluginRoot>/scripts/gitea-ci-watch.sh <40-character sha of the commit you pushed>
```

It prints one verdict line: `success`, `failure`, `no-run-registered`, `timed-out`, or
`watch-error` (exit 1). Only `success` is a pass. `<pluginRoot>` is the plugin's directory:
a worker has it in its brief, and the PM knows it from the skill's base directory.

**A worker must use the script, not an inline loop.** In a worktree-isolated agent the
guard refuses `tea` inside `$(…)` and a URL built at runtime, which is what an inline loop
needs; it allows `bash <script>` (measured on Claude Code 2.1.284, and the script itself
run by an isolated agent on 2.1.285). `scripts/test-gitea-ci-watch.py` covers every
verdict against a stub `tea`.

The script keeps four properties. Keep them in any change to it:

- **The `head_sha` anchor** — a stale run can never be mistaken for yours.
- **`no-run-registered` distinct from `success`** — a `[skip ci]` commit was not tested.
- **The aggregate across *all* workflows for the commit** — one green workflow does not
  excuse a red sibling.
- **Success is proven, not assumed.** Listing the failing conclusions instead (`failure`,
  `cancelled`, …) is a blacklist: `timed_out`, `startup_failure`, `action_required` and a
  `null` conclusion all fall through to a pass and go straight into a merge gate. Only
  `success` and `skipped` count as green — a workflow skipped by its own `if:` condition
  is a pass, unlike the whole-commit `no-run-registered` beside it.

**The response is `{"total_count": n, "workflow_runs": [...]}`** — measured against 1.25.3,
which is also what GitHub returns for the same endpoint. The `// .runs` fallback is there
only so a differently-shaped server does not silently produce an empty run list, which
would report `no-run-registered` on every commit forever. For the same reason a response
with no run list at all — an API error such as `{"message": "not found"}` — is
`watch-error`. The earlier inline loop read it as an empty list: measured on 1.25.3, a
checkout whose remote named a missing repository got `no-run-registered` from it, and
the recovery for that verdict goes looking for a skip token that is not there.

**An empty request must not read as green.** The request's stderr is *not* suppressed, and
a failed or empty `tea api` response is `watch-error` with exit 1 — never a blank verdict
with exit 0, which a loop that falls through to a catch-all arm produces. That is not
hypothetical — it is exactly what the login mismatch documented below produces. Measured
on a checkout whose remote carried an embedded token:

```
NOTE: no login matched this repository, falling back to login 'x' in non-interactive mode.
Error: request failed: Get "http://<other-host>/api/v1/repos///actions/runs?head_sha=…"
```

`/repos///` — `{owner}` and `{repo}` both empty, against the wrong server. With the arm in
place that is `watch-error` and exit 1; without it, a blank pass.

**The `pending:none` window is 60 seconds (6 × 10s) and fails toward "not tested".** A
self-hosted runner slow to register the run reports `no-run-registered` for a commit that
does get tested; the PM treats that as a gate to resolve, not a pass, so the cost is a
stall rather than an untested merge. Raise the count if a runner routinely takes longer to
pick up work — never lower it. A workflow file Gitea cannot parse registers **no run at
all** rather than a failed one, so a commit whose only workflow is malformed also lands
here — another reason this outcome must never be read as green.

**Recovering from `no-run-registered`.** The verdict is never a pass, but it is not always a
stall either — the caller reads the commit before deciding, because the two causes have
opposite remedies:

```bash
git fetch <remote> <branch> -q
git cat-file -e <sha>^{commit} || { echo "sha-not-local"; }   # do not diagnose without it
git log -1 --format='%s%n%b' <sha> \
  | grep -niE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true
```

The `|| true` is not decoration: `grep` exits 1 on zero matches, and **zero matches is the
branch the caller most needs to reach** — without it, a `set -e` script dies precisely when
the diagnosis is "not a token problem".

**Fetch first, and prove the commit is local, because `|| true` hides a missing one.** The
SHAs this runs on are usually forge-created merge commits that no local ref points at yet.
`git log` then exits 128, `grep` reads empty input and exits 1, and `|| true` swallows both
— indistinguishable from a clean message, so a genuinely suppressed commit gets diagnosed
as a runner problem. `sha-not-local` after a fetch is its own outcome: resolve it (fetch
the right remote or branch) before reading the count at all.

A hit means the commit is **suppressed**, usually by a token folded in from a squash body
rather than one anybody typed. Push one clean trigger — `git commit --allow-empty -m "<subject>"`,
one `-m`, subject only, no body — check the message you are about to push with the loose
pre-push grep (SKILL.md stage C2 step 1), then watch the new SHA.

No hit means nothing in the message suppressed anything. **Re-poll once before escalating.**
The `pending:none` window above is 60 seconds, and a busy self-hosted runner that registers
the run at 90 seconds produces exactly this clean-message `no-run-registered` — the most
common cause of it, not a broken runner. Poll `forge.run.list` for the SHA again after a
further 60-120 seconds; if a run has appeared, there was never anything to recover.

On GitHub the same outcome arrives as `gh pr checks` exiting 1 with a check count of `0`
(the exit-code note below); treat it exactly like `no-run-registered`, re-poll included,
since a run registered seconds after the push reads the same way.

**Still nothing → read the pull request before blaming the runner.** A `pull_request`
workflow starts only on a PR *event* (`opened`, `synchronize`, `reopened` by default), and
on GitHub only when a merge ref can be built. A state that blocks either produces no run
and no error — indistinguishable here from a runner problem, and a local-gate substitute
would merge on a weaker gate for no reason. Measured on github.com (September 2026), with a
workflow triggered on `pull_request` to `[dev, main]`:

| PR state | Runs registered |
|---|---|
| Opened while `CONFLICTING` | none (2.5 min) |
| Base changed after opening — an `edited` event, not a default trigger | none (2.5 min) |
| A conflict later cleared from the **base** side (now `MERGEABLE`) | none (4 min) — nothing fired an event |
| Closed and reopened | one, within seconds |
| A subject-only commit pushed to the head (`synchronize`) | one |

So read `gh pr view <pr> --json mergeable`. `UNKNOWN` means GitHub has not computed it
yet — read it again after a few seconds.

- **`CONFLICTING`** → resolve the conflict by pushing to the PR's head branch (for a batch
  PR, that is the C2 conflict step, done now). The push is the event, so the run starts
  on its own; watch the new head SHA.
- **`MERGEABLE`** → close and reopen the PR once (`gh pr close <pr> && gh pr reopen <pr>`),
  then watch again. Do not try to tell *why* first: a base change shows only in the PR
  timeline, and a conflict cleared from the base side shows nowhere — the table's third
  row had nothing but its commits. Reopening is cheap and fires the missing event.
- **Still no run after the reopen** → only now the runner or the workflow file (disabled
  runner, billing, a workflow the provider cannot parse): a hard stop to resolve or
  substitute a local gate for, not something a re-push fixes.

The general rule: **a `pull_request` workflow needs both a merge ref and an event.**
Anything that removes either stops CI without producing a failure.

**Gitea differs** (measured on 1.25.3 with act_runner 0.4.0): a conflicting PR **does**
run, so the merge-ref cause does not apply (Gitea's `.mergeable` is a boolean, `false`
when conflicting). A base change registers no new run either, but the old run for the
same head SHA stays — so a watch anchored to that SHA reports the **old base's** verdict
instead of `no-run-registered`. After changing a PR's base on Gitea, close and reopen it
(`tea pr close <pr> && tea pr reopen <pr>`), and watch until a run newer than the base
change appears. Closing and reopening, and pushing to the head, both register a new
run.

**Cap the recovery at one clean re-trigger** — the re-poll is not a re-trigger and does not
count against the cap. If a commit that greps clean also registers no run across both polls,
the token was never the cause and pushing a third commit only hides that.

**`tea api` matches the login by git remote URL.** A remote with credentials embedded
(`https://<token>@host/...`) matches nothing, and `tea` then silently falls back to some
other configured login and resolves `{owner}`/`{repo}` to empty. Keep the remote clean and
authenticate with a credential helper.

The same rule holds on GitHub: `gh pr checks <pr> --watch` already blocks in one call.
Never wrap `forge.pr.checks` or `forge.run.list` in an agent-driven retry loop on either
forge — and launch the blocking call in the background on either forge, for the ceiling
reason above. **`gh pr checks` exits non-zero when checks fail (and `8` when they are still
pending).** That surfaces as a failed `Bash` call — treat the non-zero exit as the
result, not as a tool error to retry. **Exit 1 has two meanings, so check which.**
Measured on gh 2.88.1: a PR with no run at all exits 1 at once, `--watch` or not, and
prints `no checks reported on the '<branch>' branch` to **stderr**. That is GitHub's
`no-run-registered`, not a failure. Do not match the message — a watch that keeps only
stdout never sees it, and its wording can change between gh versions. Count the checks
instead: `gh pr view <pr> --json statusCheckRollup --jq '.statusCheckRollup | length'`
prints `0` for a PR with no run (exit 0) and the check count otherwise. `0` → recover it
as `no-run-registered` (above). A background shell reports the same exit status when
it finishes, so the meaning of the code does not change with the launch mode.

**`[skip ci]` is native on both.** Gitea Actions honors `[skip ci]`, `[ci skip]`,
`[no ci]`, `[skip actions]` and `[actions skip]` in the head commit message from 1.20
onward. The "CI runs once per batch" invariant needs no Gitea-specific workaround.

### Verifying CI actually executed

A green or red check can exist with zero jobs having run — see the measured
failure modes above. `issue-flow/scripts/verify_ci_ran.py` mechanizes the
check: it fetches runs for a SHA (via `gh` for GitHub, the Actions REST API
for Gitea) and requires retrievable log bytes, not just a status field,
before reporting `ran: true`. Use it at the point SKILL.md's CI section
requires it — before trusting any check as evidence the commit was tested.

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
