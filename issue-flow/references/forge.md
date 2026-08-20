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
| `forge.issue.comment` | `gh issue comment <n> --body "<b>"` | `tea comments <n> "<b>"` | `issue_write(method: "add_comment")` |
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

**Both merge rows are incomplete on purpose: always add the explicit message** (and check
the branch afterwards) — see *Never let a merge write its own commit message* below. The
default message decides whether the merged-into branch runs CI, and it differs per forge.

**`forge.pr.view` by *branch* is GitHub-only.** `gh pr view <branch>` resolves a branch
name as readily as a number; no `tea` command and no Gitea endpoint does. A replacement
worker adopting an already-open PR (the routine path after a `checkpoint`) therefore lists
and filters on the head ref:

```bash
tea api "/repos/{owner}/{repo}/pulls?state=open" \
  | jq -r '.[] | select(.head.ref == "issue/<n>-<slug>") | .number'
```

Empty output means no PR is open for that branch — open one. More than one line is a bug
worth stopping on, not a pick-the-first situation.

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
number the worker already has; on Gitea it resolves to the loop below. Keep either in a
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

1. Launch the watch (the `gh pr checks <pr> --watch` call, or the commit-anchored loop
   below) with `run_in_background: true`. Do not pass a `timeout`; do not `sleep` in the
   foreground waiting on it. The tool result carries the **path to the shell's output
   file** — keep it; that file is where the verdict lands.
2. **Stay alive until that shell exits.** Do other in-scope work if you have any;
   otherwise simply wait for the completion notification, which carries the same output
   path.
3. Read the output file, take the one-line verdict, and only then act on it or return it.

**Do not return before the shell exits.** Both waiting paths run inside subagents, and a
subagent's final text *is* its return — emitting it ends the agent, and an agent that has
ended is never re-invoked when its background shell finishes. The verdict is then lost in
exactly the way a killed foreground call loses it, so backgrounding buys nothing. Emit no
verdict, no partial verdict and no "watching…" progress note until you have read the
finished shell's output. A watch you launched and walked away from is not a watch.

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

```bash
# ONE tool call. Blocks until CI for THIS commit is terminal.
SHA=$(git rev-parse HEAD)
none=0
for _ in $(seq 1 60); do
  v=$(tea api "/repos/{owner}/{repo}/actions/runs?head_sha=$SHA" \
      | jq -r '(.workflow_runs // .runs // []) as $r
               | if   ($r|length) == 0                  then "pending:none"
                 elif any($r[]; .status != "completed") then "pending:running"
                 elif all($r[]; .conclusion == "success" or .conclusion == "skipped")
                                                        then "success"
                 else "failure" end')
  case "$v" in
    pending:none)                       # no run for this commit yet
      none=$((none+1))
      [ "$none" -ge 6 ] && { echo "no-run-registered"; exit 0; }   # [skip ci] — NOT success
      sleep 10 ;;
    pending:running) sleep 30 ;;
    ""|null) echo "watch-error"; exit 1 ;;   # request or jq failed — never a pass
    *) echo "$v"; exit 0 ;;
  esac
done
echo "timed-out"
```

Four properties worth keeping if you rewrite it:

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
only so a differently-shaped server does not silently produce an empty `$r`, which would
report `no-run-registered` on every commit forever.

**An empty request must not read as green.** The request's stderr is *not* suppressed: a
failed `tea api` call leaves `v` empty, and without the `""|null` arm the empty string
falls to `*)`, which ends the watch with exit 0 and a blank verdict. That is not
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
further 60-120 seconds; if a run has appeared, there was never anything to recover. Only a
second clean poll points at the runner or the workflow file (disabled runner, billing, a
workflow the provider cannot parse): a hard stop to resolve or substitute a local gate for,
not something a re-push fixes.

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
result, not as a tool error to retry. A background shell reports the same exit status when
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
