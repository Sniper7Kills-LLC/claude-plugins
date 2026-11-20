# The workflow timeline — document schema

`scripts/timeline.py collect` rebuilds what an issue-flow run did, from the tracker and the local-event log.
It prints one JSON document. The renderer and the `snapshot` and `watch` commands read this schema. The local-event hook writes its
own kinds into it, and `timeline.py` is its only producer. The tracker reads it uses are
in [forge.md](forge.md) (`forge.issue.timeline`, `forge.issue.list.all`, `forge.pr.list.all`).

The collector works because issue-flow leaves a tracker trace for every state change: a
label, a comment, a PR state. The local-event log adds what the tracker does not see: agents and worktrees.

## Document

```json
{
  "version": 1,
  "forge":   {"type": "github", "host": null, "owner": "o", "repo": "r", "pr_repos": ["o/other"]},
  "range":   {"since": "2026-05-01T00:00:00Z", "until": null},
  "issues":  {"51": {"title": "…", "url": "…", "state": "open", "labels": ["status:in-review"],
                      "status_at_start": "in-progress", "problems": []}},
  "batches": {"50": {"tracking": 50, "branch": "batch/50-bar", "members": [51, 52], "pr": 61, "pr_repo": "o/r", "prs": [60]}},
  "convergence": [{"gate": 12, "filed": 3, "closed": 5}, {"gate": 13, "filed": 2, "closed": null}],
  "events":  [{"ts": "2026-05-01T11:00:00Z", "kind": "status", "issue": 51, "pr": null, "batch": 50,
               "actor": "alice", "from": "ready", "to": "in-progress", "detail": null}]
}
```

- **Timestamps** are ISO-8601 UTC with a `Z` suffix, at one-second precision. The collector
  converts every forge offset.
- **`forge.pr_repos`** lists the repositories read for PRs besides the issue repository. It is
  present only when `--pr-repo` named one. See **Several PR repositories**.
- **`range`** repeats `--since` and `--until` after they are resolved to UTC. `null` means
  unbounded.
- **`issues`** is keyed by issue number (a string). It holds the issues that have an event in
  the result, or every fetched issue when no filter applies. `status_at_start` is the
  `status:*` value (without the prefix) the issue had at `range.since`, replayed from its full
  history. It is `null` when there is no `--since`, or when the issue had no status then.
  `problems` lists what needed fixing on the issue (see **Problems**). It is built from every
  event, so a filter does not shorten it.
- **`batches`** is keyed by the tracking-issue number. `tracking` is that number.
  `members` are the issue numbers in its checklist. `branch` is the integration branch,
  found from a PR whose head is `batch/<n>-…` or `epic/<n>-…`. `pr` is that PR (the batch PR) and
  `pr_repo` is the repository that holds it (`null` when none is found).
  `prs` are the member PRs, whose base is the integration branch. A tracking issue has the
  label `type:batch` or `type:epic`.
- **`convergence`** is the last `<!-- issue-flow:convergence --> gates: 12→3f/5c, 13→2f/–`
  line found in the body or the comments of an issue labelled `flow:status`. `closed` or
  `filed` is `null` for `–`. The list is empty when no line exists.
- **`events`** are sorted by `ts`, then `kind`, then issue and PR number.

## Event

Every event has these nine keys. A key that does not apply is `null`.

| Key | Meaning |
|---|---|
| `ts` | When it happened (UTC). |
| `kind` | What happened. An open string set — see the table below. |
| `issue` | The issue number. For a PR, the issue it implements (from the `issue/<n>-…` head branch), or the tracking issue for a batch PR. |
| `pr` | The PR number, when the event concerns a PR. |
| `repo` | `owner/name` of the repository that holds the PR. Every event that has a `pr` also has `repo`. Other events have no `repo` key. |
| `batch` | The tracking-issue number of the batch the event belongs to. |
| `actor` | The tracker login that caused it. `null` when the forge does not report one. |
| `from`, `to` | The old and new value, for a transition. |
| `detail` | One line of text: a title or a note. |

**Extension point.** An event may carry extra keys that belong to its kind. A consumer
ignores a key it does not know. Producers other than `collect` (the local-event hook) add
their own kinds with their own keys, for example `session`, `agent`, `worktree`, `branch`
and `outcome`. They use the kinds `agent.*` and `worktree.*`.

## Kinds from `collect`

| Kind | When | Fields |
|---|---|---|
| `issue.opened` | The issue was created. | `detail` = title. |
| `issue.closed` | The issue was closed. | |
| `issue.reopened` | The issue was reopened. | |
| `status` | A `status:*` label changed. | `from`, `to` = the value without the prefix. `null` = no status. A remove and an add by one actor within two seconds is one event, so a park (`to`: `needs-feedback` or `blocked`) shows as one transition. |
| `checkpoint` | A comment starting `checkpoint <k>: …`. | `k` = the number, `detail` = the text after the colon. |
| `pr.opened` | The PR was created. | `detail` = title; `draft` = whether it opened as a draft; `base`; `head`. |
| `pr.ready` | A draft PR became ready for review. | |
| `pr.merged` | The PR was merged. | `base`; `head`; `actor` = the merger when the forge reports it. |
| `pr.closed` | The PR was closed without a merge. | `base`; `head`. |
| `batch.merged` | The batch or epic PR (head `batch/<n>-…` or `epic/<n>-…`) was merged into its base. It comes with a `pr.merged` event at the same `ts`. | `base`; `head`. |
| `comment` | An issue comment or a PR conversation comment. A `checkpoint` comment gives this event and a `checkpoint` event. | `role`, `excerpt`, `url`, `problem` (see **Comment roles**). |
| `commit` | A commit on a PR, read from the PR commits endpoint. A squash-merged PR still lists its original commits. | See **Commit fields**. |
| `review` | A PR review was submitted. A pending review is not an event. | `state`, `excerpt`, `url`, `review_id`. |
| `review.comment` | An inline comment on a PR diff. | `path`, `line`, `excerpt`, `url`, `resolved`, `review_id`. |
| `ci.run` | A CI run on a PR head commit finished (or started, when it has not finished). | `workflow`, `conclusion`, `started`, `completed`, `duration`, `url`, `sha`, `failed_job`, `failed_step`. |

`actor` of a `comment`, `review` and `review.comment` is the author. `actor` of a `commit` is its
author login, or the git author name when the commit has no linked account. `actor` of a
`ci.run` is `null`. `issue` and `batch` come from the PR, as for `pr.opened`.

**`review` fields.** `state` is `approved`, `changes_requested`, `commented` or `dismissed`.
GitHub `CHANGES_REQUESTED` and Gitea `REQUEST_CHANGES` both give `changes_requested`. `excerpt`
is the first 300 characters of the body. `review_id` is the forge id of the review.

**`review.comment` fields.** `line` is the line the comment sits on (the Gitea `position` when
the forge gives no line). `resolved` is `true` or `false` on Gitea. GitHub does not report it
in the REST API, so it is `null` there. `review_id` is the review the comment belongs to.

**`ci.run` fields.** `workflow` is the workflow name (on Gitea, the workflow file name without
its extension). `conclusion` is the forge value (`success`, `failure`, `cancelled`, …) or `null`
while the run is in progress. `started` and `completed` are UTC timestamps. `duration` is whole
seconds, or `null`. `ts` is `completed`, or `started` for a run in progress. `failed_job` and
`failed_step` are the names of the first failed job and its first failed step. They are `null`
unless `conclusion` is `failure`, `timed_out` or `startup_failure`.

## Comment roles

Every issue comment and PR comment gives a `comment` event with a `role`. The role comes from
the **marker** when the comment has a valid one. Otherwise it comes from a **prose prefix**.
A comment that matches neither has the role `other`.

Roles: `plan`, `cross-check`, `plan-amendment`, `gate.pass`, `gate.rework`, `sub-merge`,
`checkpoint`, `worker.launched`, `status.repair`, `question`, `other`.

**Marker grammar.** One HTML comment, anywhere in the body (the writer puts it on the first line):

```
<!-- issue-flow:event role=<role> issue=<n> [pr=<n>] [problem=<k>] -->
```

- `role` is one of the roles above. A marker with an unknown role is ignored, and the prose
  prefix applies. A marker with no `role` is not a marker.
- `issue` is the issue the comment is about. `pr` is the PR, when there is one.
- `problem` is a key the writer chooses (letters, digits, `-`, `_`). It names the problem a
  `gate.rework` comment raises, so a commit can point at it. The problem id is `<issue>:<k>`.
- Attributes are `name=value` pairs with no spaces in the value. The order is free.

**Prose fallback.** A run made before the marker existed still classifies. The comment body
starts with the prefix, after any marker and leading whitespace. Matching ignores case.

| Prefix | Role |
|---|---|
| `## Plan (PM)` | `plan` |
| `**Amendment (batch cross-check` | `plan-amendment` |
| `finding: batch cross-check` | `cross-check` |
| `PM gate: rework requested` | `gate.rework` |
| `PM gate: pass` | `gate.pass` |
| `Sub-merged into` | `sub-merge` |
| `checkpoint <k>:` | `checkpoint` |
| `worker launched:` | `worker.launched` |
| `status repair:` | `status.repair` |

`question` has no prose prefix. Only the marker gives it. The `comment` event keeps
`excerpt` (the first 300 characters of the body), `url` and `problem` (the marker's key, or
`null`).

## Commit fields

A `commit` event has the nine standard keys and these keys. `ts` is the commit date.

| Key | Meaning |
|---|---|
| `sha`, `url` | The commit id and its page. |
| `subject`, `body` | The first line of the message, and the rest without surrounding blank lines. |
| `author` | The author login, or the git author name. |
| `change` | `feature`, `fix`, `test`, `docs`, `refactor` or `merge`. |
| `why` | The `Why:` trailer, else the body without its trailer lines, else `null`. Trailer lines are `Change:`, `Why:` and `Fixes:` lines, and a closing paragraph of `Token: value` lines (`Co-Authored-By:`, `Signed-off-by:`). |
| `addresses` | The ids of the problems the commit addresses. `[]` when none. |
| `files` | `[{path, status, additions, deletions, patch, truncated}]`. |

**Files.** `status` is `added`, `modified`, `removed`, `renamed`, `copied` or `changed`.
`patch` is the unified diff from the first hunk, `null` for a binary file, a file the forge gave
no patch for, or a run with `--no-diffs`. A patch is cut at 400 lines or 32 KB per file, and the
patches of one document share `--max-diff-bytes` (default 4194304; a file after the budget is
used up gets a cut or empty patch). A cut patch has `truncated: true`. `additions` and
`deletions` come from the forge, not from the cut patch. On Gitea they come from the commit
`.diff` text, because the commit file list has no counts. A GitHub commit lists its first 300 files.
A hunk header keeps only its line ranges (`@@ -1,7 +1,7 @@`). The section heading after the
ranges is dropped, because GitHub, Gitea and local git find different headings. A commit's files
have the same shape whether they come from git or from the API (see **API cost**).

**Trailer grammar.** A trailer is a line `Key: value` in the last paragraph of the commit
message. Keys are case-insensitive. The message needs a subject and at least one more
paragraph for a trailer block to exist.

| Trailer | Value | Rule |
|---|---|---|
| `Change:` | `feature`, `fix`, `test`, `docs`, `refactor` or `merge`. | Sets `change`. An unknown value is ignored. |
| `Why:` | One sentence. | Sets `why`. |
| `Fixes:` | One URL: an issue or PR comment, a review, a review comment or a CI run. | Sets `addresses`. Repeat the line for several URLs. |

**How `change` is set.** In this order: the `Change:` trailer. `merge` when the commit has two
or more parents. `fix` when the commit addresses a problem. `test` when every path is a test
path (a `test/`, `tests/`, `__tests__/`, `spec/` or `e2e/` directory, or a name that starts
`test-` or `test_`, or ends `.test.<ext>`, `_test.<ext>` or `.spec.<ext>`). `docs` when every
path is under `docs/` or ends `.md`, `.mdx`, `.rst` or `.txt`. Otherwise `feature`.

**How `addresses` is set.** A commit with `Fixes:` URLs addresses the problems whose URL
matches (a review comment URL matches its review's problem). A commit with none addresses
every problem on its issue that opened at or before the commit and that no earlier commit
resolved. A merge commit addresses nothing without a `Fixes:` trailer.

## Problems

`issues.<n>.problems` lists what needed fixing on issue `n`. A problem opens at:

- a `comment` event with the role `gate.rework`;
- a `review` event with the state `changes_requested`;
- a `ci.run` event whose conclusion is `failure`, `timed_out` or `startup_failure`.

| Field | Meaning |
|---|---|
| `id` | `<issue>:<k>` when the comment's marker has `problem=<k>`. Otherwise `<issue>:auto-<i>`, where `i` counts the unkeyed problems of the issue in time order from 1. |
| `source` | The event kind: `comment`, `review` or `ci.run`. |
| `role`, `state` | `role` is `gate.rework` for a comment, else `null`. `state` is the review state or the CI conclusion, else `null`. |
| `text` | The whole text of the comment or review, without the issue-flow marker line (the 300-character `excerpt` is for log entries only), or `<workflow> <conclusion> in <job> at <step>`. |
| `url` | The page of the source event. A `Fixes:` trailer cites this URL. |
| `ts` | When the problem opened. |
| `resolved_by` | The `sha` of every commit that addresses it. |
| `resolved` | `true` when `resolved_by` is not empty. |

A problem with a repeated id is listed once.

## Kinds from the local-event hook

`hooks/record-event.py` writes these to `<git common dir>/issue-flow/events.jsonl`.
`collect` merges them with `--local-events [PATH|off]`. A live `collect` reads the default log
when it exists. A `--fixture` run reads it only when the flag is given. A missing log gives
the tracker-only document. Each event has the nine standard keys plus `session`, `agent`,
`worktree`, `branch` and `outcome`. `actor`, `from`, `to` and `detail` are `null`.

| Kind | When | Fields |
|---|---|---|
| `agent.spawned` | A worker was spawned for an issue. | `issue`, `agent`, `session`. |
| `agent.stopped` | A worker ended. | `agent`, `session`; `issue`, `outcome`, `branch` and `worktree` from its verdict. With no verdict, `outcome` and `issue` are null. `worktree` is then the `cwd` of the stop event, but only when its last path component is `agent-<agent_id>`; a resumed agent's stop reports the parent session's directory, and `worktree` stays null. |
| `worktree.added` | `git worktree add` ran. | `worktree` = the path; `branch` when `-b` or `-B` names one. |
| `worktree.removed` | `git worktree remove` ran. | `worktree` = the path. |
| `agent.messaged` | The PM sent a message to an agent (`SendMessage`), for example a worker sent back for rework. | `agent` = the recipient exactly as the call named it, `session`. The line never holds the message text, the summary or the reply. |

`collect` fills `issue` for a worktree event from a branch named `issue/<n>-…`, and for a
later event on the same path. It gives every later event of an agent the `issue` of that
agent's `agent.spawned`, so one agent stays on one lane in one batch. It fills `batch` from the issue. The range and issue filters
apply to local events too.

**How the forges differ, and how `collect` hides it.** A draft on GitHub is a flag, so
`draft` also reads true when a `ready_for_review` event exists. A draft on Gitea is a
`WIP:` title prefix, so `pr.ready` comes from a title change that removes it. A Gitea label
entry is an add when its `body` is `"1"`. Both forges give the same events for the same
history.

## Several PR repositories

In a fork workflow the issues and the batch PR live in one repository, and the member PRs live
in another. `--pr-repo` names the other repositories. It repeats, and it takes a comma list:
`--pr-repo owner/fork --pr-repo owner/other` and `--pr-repo owner/fork,owner/other` are the same.
`collect` always reads the issue repository (`--repo`) too, and reads each repository once.

- A PR is known by `owner/name#number`. Two repositories may both have a PR 8, and a PR that
  two reads find is one PR. A fixture entry with no `repo` belongs to the issue repository.
- **The batch PR is found by its head branch.** A PR whose head is `batch/<n>-…` or `epic/<n>-…`
  is the batch PR of batch `n`, in whichever repository it is. It sets `batches[<n>].pr`,
  `.pr_repo` and `.branch`. It gives `batch.merged` when it is merged. Its comments, commits,
  reviews and CI runs have `issue` = the tracking issue and `batch` = `n`, so they sit on the
  batch lane. A member PR is found by its base branch instead.
- **Problems from the batch PR belong to the tracking issue.** A `gate.rework` comment, a
  changes-requested review or a failed CI run on the batch PR is in
  `issues[<n>].problems`. A later commit on that PR resolves it (see **Problems**).
- The same rules hold on Gitea, through `tea api`.
- The renderer writes the repository on a PR in the detail text only when it is not the issue
  repository.

Where the PR repositories come from. `collect` does not guess them. The `/timeline` skill
passes the issue repository, each remote of the checkout that holds PRs for this repository,
and the `prRepos` array of `.issue-flow.json` (see `session-config.md`).

## Filters

`--since` and `--until` bound `events` and nothing else: the replay behind
`status_at_start` always starts at the first event. `--issue <n>` keeps the events with
that `issue`. `--batch <n>` keeps the events with that `batch`.

## Parallel reads and rate limits

`collect` reads the tracker in three waves: the listings, then each issue's and PR's own reads,
then the reads that need those (a commit's files or diff, a review's comments, a failed run's
jobs). A wave runs on a bounded pool of threads. `--jobs <n>` sets the size: 1 to 16, default 4.
Four reads at once keep a collect well under GitHub's secondary (burst and concurrency) limits.
`--jobs 1` reads one at a time. `snapshot` and `watch` pass `--jobs` to `collect`.

- **The document does not depend on `--jobs`.** Each result goes where the plan puts it, never
  where the read ended. `--jobs 1` and `--jobs 8` give byte-identical documents.
- **A rate limit is retried, a bounded number of times.** A GitHub secondary rate limit, an HTTP
  429, or a "too many requests" reply makes the read run again, for 3 attempts in all. The wait
  is the `Retry-After` value when the reply has one, else 2 s, then 4 s, and never more than
  60 s. A read that is still limited after the third attempt fails with exit 2.
- **A primary limit waits for the reset, or stops.** GitHub answers a read with 403 or 429 when
  the quota is used up (`x-ratelimit-remaining` is `0`), and a retry before the reset fails too.
  The reset time is the reply's `x-ratelimit-reset` header when the reply has it. `gh api` prints
  only the message, so in practice `collect` reads the reset from `gh api rate_limit`. If the
  reset comes in 60 s or less, the read waits for it and runs again. Else the collect stops with
  exit 3 and one stderr line: `collect: the API quota is used up. The quota resets at <time> (in
  <n> min). Use a shorter --since, or wait for the reset.`
- **Every other error fails at once**, as it does with `--jobs 1`. One failed read fails the whole
  collect, and nothing is written.
- `IFW_RETRY_DELAY` sets the first wait in seconds, for tests only.

## API cost

The REST quota is 5,000 calls per hour on GitHub, and every agent and person on the token
shares it. This section names what each read costs and the flags that cut the cost.

**Commit diffs come from local git.** A commit's files and patches cost no API call when the
checkout has the commit. `--diff-source` chooses the source:

| Value | Behavior |
|---|---|
| `auto` (default) | Read each commit from git. If git does not have it, read it through the API. |
| `git` | Read each commit from git. If git does not have it, the collect fails with exit 2. |
| `api` | Read each commit through the API (`commits/{sha}` on GitHub, `git/commits/{sha}.diff` on Gitea). |

- **The fetch.** Before the commit reads, `collect` looks for the PRs that hold a commit git
  lacks. For each PR repository, one `git fetch` reads the heads of those PRs:
  `refs/pull/<n>/head` to `refs/issue-flow/pull/<owner>/<repo>/<n>`. The fetch takes no tags,
  writes no `FETCH_HEAD`, and updates no remote-tracking ref (`--refmap=`). It never changes a
  branch, a tag or the work tree. If the one fetch fails because a PR ref is gone (`couldn't
  find remote ref`), each PR head is fetched alone. Any other failure (no access, no network, a
  timeout) stops the fetch at once, because each retry would fail the same way.
- **The fetch never waits.** It reads no stdin, sets `GIT_TERMINAL_PROMPT=0`, and adds
  `-o BatchMode=yes` to the SSH command: after `GIT_SSH_COMMAND`, else after `core.sshCommand`,
  else after `ssh`. A `GIT_SSH` program with no `GIT_SSH_COMMAND` is left as it is. Each fetch
  stops after 60 s (`GIT_FETCH_TIMEOUT`). A failed fetch is not an error: its commits are read
  through the API.
- **The remote.** The fetch uses the remote of the `--repo-path` checkout whose URL ends in the
  PR repository's `owner/name`. If two remotes match, the first name in sort order wins. If no
  remote matches, there is no fetch, and the commits git lacks are read through the API.
- **The fallback.** A commit that git cannot read after the fetch (no matching remote, or a
  commit that a force-push removed) is read through the API, one commit at a time. One stderr
  line gives the count, and the first line of git's error when a fetch failed:
  `collect: read 3 commits through the API: not in the local repository at <path> (git fetch: fatal: Authentication failed for '<url>')`.
- **The diff.** A commit is compared with its first parent, as both forges do. A root commit
  is compared with the empty tree. Renames are found as `git diff -M` finds them. On GitHub the
  patch uses `--inter-hunk-context=1`: GitHub joins two hunks that one unchanged line keeps
  apart. Gitea's `.diff` route runs plain `git diff --find-renames`, so a Gitea patch uses
  git's default.
- **Measured.** Over a 1-day window of this repository (46 commits, 185 files, on
  2026-11-19), the git source made 0 commit calls against 73 for `--diff-source api`. The
  whole collect went from 221 calls to 148. The files built from git equal the API files.
- **What differs.** Git cannot give the section heading of a hunk header as each forge finds
  it, so both sources drop it (see **Commit fields**). GitHub documents `patch` as optional
  and leaves it out of a binary file; git gives no patch for a binary file either. If GitHub
  leaves out the patch of a text file, git still gives one, cut at 32 KB. The documentation
  does not say which parent GitHub compares a merge commit with. The measured window had 2
  merge commits, and the first parent gave the API files.

**The read cache keeps the reads of unchanged items.** The listings always run, so `collect`
knows each item's `updated_at`. An item whose key has an entry in the cache costs no call.

- **Where.** `<git common dir>/issue-flow/cache/` of the `--repo-path` checkout. No commit
  carries it, and every worktree of the repository shares it. `--cache-dir <path>` names another
  directory. Outside a git repository, and with `--fixture`, there is no cache.
- **Keys.** `forge` is `github` or `gitea`, and `repo` is the repository that holds the item.

  | Read | Key | Kept |
  |---|---|---|
  | An issue's timeline | `<forge>/<repo>/issue/<n>/<updated_at>` | until `updated_at` changes |
  | A PR's timeline, reviews, commits, review comments and CI runs | `<forge>/<repo>/pull/<n>/<updated_at>/<head sha>/<read>` | until `updated_at` or the head changes |
  | A review's comments (Gitea) | the PR key, then `/review/<id>/comments` | as the PR key |
  | A commit's files (GitHub) or `.diff` text (Gitea) | `<forge>/commit/<sha>` | always: a commit never changes |
  | A failed run's jobs | `<forge>/<repo>/run/<id>/<updated_at>/jobs` (Gitea: `completed_at`) | until the run changes |
  | A PR's state record, for `gc` (no read) | the PR key, then `/state` | as the PR key |

  An item that has no `updated_at`, a PR that has no head sha, and a run with no time are read
  every time. The listings are not served from the cache. On GitHub each listing page is a
  conditional request (see **Conditional listing reads**).
- **CI runs of an open PR.** A run starts and ends with no change to the PR's `updated_at`. So
  `collect` reads the runs again for each PR that is open, and for each PR updated in the last
  hour (`RUNS_STALE_AFTER`). The new runs replace the entry. The PR's other reads, and the jobs
  of a run that did not change, still come from the cache. A runs list that holds a run whose
  `status` is not `completed` (queued or in progress) is not stored: when that run ends, the
  PR's `updated_at` does not change, so a stored list would show the run as going forever.
- **Commits come before git.** `collect` looks in the cache first. The `git fetch` of
  `--diff-source auto` runs only for the PRs that hold an uncached commit. A commit has the same
  value whether git or the API gave it.
- **The file.** Each key has one JSON file, `<sha256 of the key>.json`, under a directory named
  for the first two hex digits. The file holds `{"cache_version", "key", "value"}`, and `value`
  is the forge payload. An entry with another `cache_version` is a miss, and the next write
  replaces it. A file that does not parse is a miss too.
- **Two collects at once.** Each write goes to a temporary file in the same directory, then one
  rename replaces the entry. Two writers of one key leave one whole entry: the last one written.
  A failed write leaves the old entry, and the collect goes on.
- **The document does not depend on the cache.** A warm collect gives a document byte-identical
  to `--no-cache` for the same tracker state.
- **The API.** `Cache(root, read=True, write=True)` in `timeline.py` has `get(key)` (the value,
  or None for a miss), `put(key, value)`, `peek(key)` (the value, with no hit counted) and
  `has(key)` (no hit is counted). A read sets the entry's file time, for `gc`. The key functions are
  `issue_key`, `pull_key`, `commit_key` and `jobs_key`. Another kind of entry uses its own key
  prefix: ETag entries use `etag/`.

| Flag | Reads the cache | Writes the cache |
|---|---|---|
| (none) | yes | yes |
| `--refresh-cache` | no | yes: every read |
| `--no-cache` | no | no, and no `If-None-Match` is sent |

`--no-cache` and `--refresh-cache` together exit 5. `snapshot` and `watch` pass all three
flags to `collect`.

**Conditional listing reads.** A listing is the issue listing of the range, the three label
listings (`type:batch`, `type:epic`, `flow:status`) and the PR listing of each PR repository.
On GitHub, `collect` keeps each listing page with its `ETag` and sends the `ETag` as
`If-None-Match` on the next read of that page. An unchanged page gets `304 Not Modified` with
no body, and `collect` uses the kept page.

- **The quota.** GitHub: "Making a conditional request does not count against your primary
  rate limit if a `304` response is returned and the request was made while correctly authorized
  with an `Authorization` header" ([Best practices for using the REST API](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api#use-conditional-requests-if-appropriate)).
  `gh` sends the `Authorization` header. Measured on 2026-11-20: a 304 left `core.used` unchanged.
- **How `gh api` reports a 304.** `gh api --include` prints the status line
  `HTTP/2.0 304 Not Modified` and the headers on stdout, prints `gh: HTTP 304` on stderr, and
  exits 1 (gh 2.88: any status above 299 is an error, see
  [`pkg/cmd/api/api.go`](https://github.com/cli/cli/blob/v2.88.1/pkg/cmd/api/api.go)). So
  `collect` reads the status line, not the exit code. A 304 is never an error and is never
  retried. Any other failure of a conditional read goes to the plain read, which retries a
  rate-limit reply.
- **One snapshot.** The kept pages of one listing are one snapshot. When page 1 changed,
  `collect` reads the rest of the listing with no `If-None-Match`. When a later page changed,
  `collect` reads the whole listing again from page 1, with no `If-None-Match`. The new pages
  replace the entry.
- **The entry.** The key is `etag/github/<path with its query, without the page>`. The value is
  `{"pages": [{"etag", "body"}, …]}`, in page order, up to the page that ended the walk. A
  listing with a page that has no `ETag` is not kept. A kept `ETag` is not a cached read: the
  request is still sent, so it counts in `C` and not in `M`. A 304 counts in `K`.
- **A relative `--since`.** `1d` gives a new timestamp every second, so the query of the issue
  listing, and its key, would change on every collect. `collect` sends `since` rounded down to
  the start of its UTC hour (`api_since`), so collects in the same hour send the same query and
  get 304. Then it drops the rows updated before the exact `--since`, and the document filters
  by the exact `--since`. The key changes once an hour.
- **The flags.** `--no-cache` sends no `If-None-Match` and keeps nothing. `--refresh-cache` sends
  no `If-None-Match` and keeps the new pages. `watch --refresh-cache` refreshes on its first
  cycle only; later cycles read the new cache.
- **Gitea.** The Gitea API documents no `ETag` and no `If-None-Match` for any route (the
  [swagger template](https://github.com/go-gitea/gitea/blob/main/templates/swagger/v1_json.tmpl)
  names neither), and its issue listing handlers set no `ETag`
  ([`routers/api/v1/repo/issue.go`](https://github.com/go-gitea/gitea/blob/main/routers/api/v1/repo/issue.go)).
  So on Gitea the listings are read in full on every collect, and `K` is 0.

**The counts.** The summary line of `snapshot` ends with the cost of the collect:
`wrote <path>: <E> events, <L> lanes, <P> problems; <C> API calls (<M> cached, <K> not modified), <Q> remaining`.
Each `watch` refresh line ends with the same counts. `watch` also prints one start line
(`watch: started; a refresh every <interval> s until <time>; page <path>`) and one end line
(`watch: stopped after <n> cycles: <reason>`). A sleep ends at the `--max-minutes` deadline at the
latest, and `watch` then runs one last cycle. A forge with no quota to read leaves out
`, <Q> remaining`.

| Count | Meaning |
|---|---|
| `C` | The HTTP requests that `collect` sent. A retry is one more request. A request answered with 304 counts. |
| `M` | The reads that the cache served in this run, with no request sent. One read is one row of the key table: a PR's reviews are one read, and its commits are another. A cold run can show `M` above 0: a read that this run wrote to the cache earlier is served from it. |
| `K` | The sent requests answered with 304. `K` is part of `C`. These requests do not count against the primary quota. |
| `Q` | `core.remaining` of the rate limit, read after the run. |

An estimate of a run's cost counts the reads that are not cached, so it leaves out `M`. It counts
each listing page as one possible call, because a 304 is not known before the request.

**Measured.** Two back-to-back collects of this repository, on the 1-day window
`--since 2026-11-19T00:00:00Z --until 2026-11-20T00:00:00Z`, with `--pr-repo` for the fork, on
2026-11-20: the first made 155 calls and the second made 12. The 12 are 11 listing pages and the
CI runs of the one open PR. The two documents are equal. With conditional listing reads, the
same check on 2026-11-20 made 162 calls cold and 13 warm: `13 API calls (174 cached, 11 not
modified)`. The 11 listing pages got 304, and `core.used` went up by 2, for the CI runs of the
two open PRs.

**Count the calls of a run.** `scripts/count-gh.py` wraps `gh` (or `tea`) through `IFW_GH`
(or `IFW_TEA`). The wrapper logs each call and then runs the real command:

```bash
W=$(python3 scripts/count-gh.py wrap /tmp/count)            # --target tea for Gitea
IFW_GH="$W" python3 issue-flow/scripts/timeline.py collect --forge github --repo owner/name --since 1d
python3 scripts/count-gh.py count /tmp/count                # every call
python3 scripts/count-gh.py count /tmp/count --match '/commits/[0-9a-f]{40}'
```

Tests import the same file and call `wrap(dir, target)` and `calls(dir, match)`.

**The quota guard stops a collect that would take too much of the quota.** On GitHub, `collect`
reads the quota with `gh api rate_limit` before the first read. That read costs no quota
([docs](https://docs.github.com/en/rest/rate-limit/rate-limit)), and it is not in `C`. Before
each of the three waves, `collect` estimates the calls of the wave's uncached reads:

| Wave | Estimate |
|---|---|
| The listings | 2 calls for each listing (a page with rows and the empty page that ends it), 1 for `--issue` |
| Each issue's and PR's own reads | 2 calls for each read with no cache entry: an issue's timeline, and a PR's timeline, reviews, commits, review comments and CI runs. The CI runs of an open PR, or of a PR updated in the last hour, always count. |
| The follow-up reads | 1 call for each uncached commit with `--diff-source api`, and with `auto` for each uncached commit that git still lacks after the fetch (`git` fails on such a commit instead), 1 for the jobs of each uncached failed run, 2 for the comments of each uncached Gitea review |

With `--diff-source auto` or `git`, the `git fetch` runs before the follow-up check, so the
estimate knows which commits git has. If the calls already sent plus the wave's estimate are
more than `--budget` percent of the quota at the start, `collect` stops with exit 3 and writes
nothing. The estimate covers the next wave only, not the whole run: a later wave is checked when
it comes. A paged read with more rows than one page costs more than its estimate. The next
wave's check counts the calls actually sent, so the error is not carried on.

- **`--budget <percent>`**, more than 0 and at most 100. The default is 50: one collect never
  takes more than half of the quota that remained when it started. `snapshot` and `watch` pass
  it to `collect`.
- **The refusal** is one stderr line, for example:
  `collect: stopped before the next stage of reads: that stage needs about 140 API calls, and --budget 50 allows 61 of the 122 that remain. The estimate covers the next stage only; later stages are checked as they come. The quota resets at 2026-11-20T10:15:00Z (in 23 min). Use a shorter --since, a higher --budget, or wait for the reset.`
  The first stage is the listings, so a refusal there names about 12 calls even when the whole
  run would need 190.
- **What to do when it stops.** Read the window down with a shorter `--since`, or pass `--batch`
  or `--issue`. Or wait for the reset time in the line. Raise `--budget` only when nothing else
  needs the quota until the reset: the issue-flow PM and its workers share it.
- **`watch` skips the cycle.** It keeps the last page and prints one line:
  `watch: skipped this refresh to save the API quota; the last page stays; next attempt at <time>`.
  A skip is not a failure, so it never counts toward the three failures that end a watch. The
  interval stays the same.
- **No quota to read.** Gitea has no rate-limit route (checked against
  `https://gitea.com/swagger.v1.json`, 2026-11-19). A GitHub server with rate limits off answers
  `rate_limit` with 404. In both cases the guard is off, and the summary line has no `Q`.
- **The summary line** of `snapshot` and `watch` adds `Q` after the counts:
  `<C> API calls (<M> cached, <K> not modified), <Q> remaining`. `Q` is read after the run. If
  that read fails, the line has no `Q` and the collect still succeeds.
- **`--budget` is measured against the quota at the start.** The limit is `--budget` percent of
  `core.remaining` read before the first call, less the calls this collect sent. The quota is
  shared with every other `gh` user of the token, so `Q` can fall faster than this run's calls.

**`gc` removes what the cache and the fetch leave behind.** Each `updated_at` change writes a
new cache file, each fetched PR adds a ref under `refs/issue-flow/pull/`, and a ref keeps the
objects of a force-pushed head. `timeline.py gc` removes:

| What | When |
|---|---|
| A cache file (`<2 hex>/<64 hex>.json`) | No collect read or wrote it for 30 days. Each read sets the file time. |
| A temporary file (`<2 hex>/.<name>.tmp`) | It is older than 1 hour: a write that a killed collect left. |
| A `refs/issue-flow/pull/<owner>/<repo>/<n>` ref | The newest state record of that PR in the cache shows it closed more than 30 days ago. |

- **State records.** A collect stores `{"state", "closed_at", "updated_at"}` of each PR it reads,
  under the PR key plus `/state`. `gc` reads these records before it removes any file, so the
  ref of a PR goes no later than its last record. A ref with no record stays.
- **`gc --all`** removes every cache file, every temporary file and every `refs/issue-flow/` ref.
  The next collect is cold, and costs as much quota as a first run: measured on 2026-11-20, a
  cold snapshot of this repository made 176 calls, and a warm `watch` cycle made 14.
- **The output.** `gc` prints one `ref <name>` line on stdout for each ref it removes, before
  any other line. `--dry-run` also prints one `cache <path>` line for each file that `gc` would
  remove, and removes nothing. `--verbose` prints the same `cache` lines in a real run. The
  stderr summary gives the counts and names up to 3 refs; the `ref` lines name them all. `gc`
  prints every line. When a display shows a list cut with `...`, the cut comes from the display:
  send stdout to a file and read the file.
- **Flags.** `--repo-path` (default `.`) names the checkout whose refs `gc` cleans and whose
  default cache it uses. `--cache-dir` names another cache directory. `gc` touches only files
  in the cache layout, so another file in that directory stays.
  Outside a git repository with no `--cache-dir`, `gc` exits 2.
- **Automatic.** `snapshot` runs the same gc (never `--all`) after it writes the page, against
  the cache of its collect. A gc failure never fails the snapshot. `--no-cache` and `--fixture`
  write no cache, so they run no gc. `watch` runs none: run `gc` or a `snapshot` from time to time.

## Exit codes

`0` document written. `2` a fetch failed, or the forge returned an error body (`tea api`
exits 0 on an API error, so `collect` checks the body). Nothing goes to stdout. `3` the quota
guard stopped the collect, or the quota is used up until a reset more than 60 s away (see **API
cost**). Nothing goes to stdout, and `snapshot` writes no page. `5` the invocation is malformed. `render` uses the same codes: `2` for unreadable or malformed
input. `gc` exits 0, or 2 when it finds no cache directory (outside a git repository with no
`--cache-dir`).

## Lane kinds (the renderer)

`timeline.py render` draws one lane per issue, grouped under its batch. A lane kind is a row
in `LANE_KINDS` in `timeline.py`. The renderer reads the kind from the event `kind` prefix.
Another producer adds a lane type by adding a row and using that prefix.

| Lane kind | Events | Style |
|---|---|---|
| `issue` | `status`, `issue.*`, `pr.*`, `checkpoint`, and the `comment`, `commit`, `review` and `ci` events of the issue or its PR | `bar`: a bar coloured by status. Markers show PR open, PR merge, checkpoint and park (a `status` event whose `to` is `needs-feedback` or `blocked`). The bar at the left edge starts with the status the issue had at the range start (`status_at_start`, or the last earlier event). |
| `batch` | `batch.*` | `marks`: one marker per event. The lane leads its group and shows the batch merge. The tracking issue's own events sit in the same group. The detail events of a tracking issue (`comment`, `commit`, `review`, `ci`) go to its batch lane, not to an issue lane. |
| `worktree` | `worktree.*` | `marks`: one lane per `worktree` key, one marker per event. |
| `agent` | `agent.*` | `span`: one lane per `agent` key, or per `session` key when there is no `agent`. One bar runs from `agent.spawned` to the last `agent.stopped`, coloured by the last `outcome` through `AGENT_OUTCOME_STATES` (a neutral grey when `outcome` is null). `agent.messaged` and each stop draw as markers on the bar. `agent.stopped` carries a `worktree` too, but the lane key stays `agent`. |
| any other prefix | the same prefix | `marks`: a generic lane, labelled with the prefix. |

Events with no batch go in a final group named "No batch". A batch with no `branch` shows
only its number. The page ignores event keys it does not know.

**Colors and legend.** `STATE_COLORS` (one row per status), `MARKER_COLORS` and `THEME_TOKENS`
in `timeline.py` set every color of the page for both themes. `TOKEN_CSS` is the only place a
color value appears; `LAYOUT_CSS` uses `var(--token)` and nothing else. The legend reads the same
tables, so a state cannot draw without a legend entry. It has three labelled rows: States, Events
and Problems (`MARKER_GROUPS`). A status that is not in the table draws grey and gets its own
legend entry. A bar state draws as a solid segment. The state `batched` draws as a dashed outline.

**Theme.** The header button cycles three modes: auto (the browser's `prefers-color-scheme`),
light and dark. Dark tokens sit under `:root[data-theme="dark"]` and, for auto, in a media block
that an explicit light mode overrides. Each theme sets `color-scheme`. The choice is stored in
`localStorage` under `tl-theme`, inside `try`, so the page works when storage is blocked. A small
script in `<head>` applies the stored theme before first paint, so the page does not flash.

**Contrast and marker colors.** The test `scripts/test-timeline.py` computes the WCAG contrast of
every text pair in `THEME_TOKENS` and requires 4.5:1 or more in both themes. A marker is a glyph
inside a 2px ring. Each ring color is at least 20 CIE76 units from every state color in both
themes, so a ring is never read as a bar state, and the test enforces that distance.

**Keyboard.** Every lane has one "Details" button, and a marker is a button with an `aria-label`
that holds the tooltip text. The tooltip shows on hover and on focus, and it moves clear of the
open panel. Opening the panel moves focus to its title. `Escape` or **Close** returns focus to the
lane's button. Markers that sit closer together than their own width move apart vertically.

**Page behavior.** The page embeds the document and draws it in the browser. It makes no
network request: the tracker links on issue lanes are the only URLs. The timeframe filter
(24h, 7d, 30d, all, or a UTC date range) re-draws without a new render. A range with no
events shows "No events in this range." `--refresh <seconds>` adds a meta refresh.

## Drill-down (the renderer)

A click on a lane opens a panel beside the chart. On a narrow window the panel sits below the
chart. A second click on another lane replaces it. `Escape` or **Close** shuts it. The panel
shows three parts.

1. **Summary.** Commits, files, lines added and removed, rework rounds (`comment` events with the
   role `gate.rework`), review rounds (`review` events with the state `changes_requested`) and CI
   failures (`ci.run` events that failed). `lane_summary` counts them over every event of the lane.
2. **What needed fixing.** One entry per item of `issues.<n>.problems`, on issue lanes. Each entry
   shows the whole text (wrapped), a link to the source, and the commits in `resolved_by` (sha, linked, with the
   subject). An open problem says so.
3. **Log.** The commits, comments, reviews, CI runs and agent events of the lane in time order, with
   a badge for `change` (commits), `role` (comments), `state` (reviews) and the conclusion (CI runs).
   The log follows the timeframe filter. The kind filter (`LOG_GROUPS`) hides one kind at a time.

**Diffs.** Each file of a commit is a `<details>` element. The page draws the diff from the embedded
`patch` only when the element opens. Added lines, removed lines and hunk headers have their own
colors. A file with `truncated: true` carries a `truncated` badge and a closing note. A file with a
`null` patch says that no diff is available. The panel sets all text with `textContent`, never as
markup, and the data tag escapes `<`, `>` and `&`.

**Problems view.** A collapsible section under the controls lists every problem of every issue. It is
open when any problem exists. `problem_rows` builds one row per problem: open rows first (oldest first),
then resolved rows (newest first). A row shows the issue, the type (`Rework requested`, `Changes requested`
or `CI failed`), the first line of the text, and `seconds`. For an open problem `seconds` is its age at the
range end, else at render time. For a resolved problem it is the time to the first commit in `resolved_by`.
The fix commits link to their pages. A click opens the issue's panel and scrolls to the problem. The list
follows the timeframe filter and shows "No problems in this range." when it is empty.

**Shared view.** The URL hash holds the view: `#lane=<lane id>&from=<iso>&to=<iso>&kinds=<csv>&q=<text>&p=<0|1>`.
`lane` is the open panel. `from` and `to` are the timeframe. `kinds` lists the log kinds that are on, and is
absent when all are on. `q` is the lane search. `p=1` turns on "Only lanes with problems". The page writes
the hash with `history.replaceState`, so the back button never steps through view changes. It reads the
hash on load and on `hashchange`, and it ignores unknown keys and bad values. The `--refresh` reload
keeps the hash, so `watch` keeps the open panel.

**Lane search.** The search box matches the lane label or title, ignoring case and a leading `#`. A lane
"has a problem" when it shows a `rework`, `review` or `ci` marker in the timeframe (`PROBLEM_MARKS`). `/`
focuses the search when no field has focus. `Escape` in the search clears it.

**Time in status.** `time_in_status` sums the intervals of each issue's `status` events. A status ends at
the next `status` event, a merge, a close or a label removal, and the last open status runs to the range
end, else to render time. `STATUS_CLASSES` groups the totals: `working` is `in-progress`; `waiting` is
`in-review`, `awaiting-review` and `needs-feedback`; `parked` is `blocked`. Other statuses (`ready`,
`batched`) appear in `by_status` only. The bar tooltip and a stacked bar in the panel summary show the
totals. The collector is unchanged: the totals come from the events already in the document.

**Batch lane.** Cross-check and plan-amendment comments (`NOTE_ROLES`) of the batch show in a
separate list on the batch lane, whichever issue holds the comment. Comments, commits, reviews and CI
runs of the tracking issue itself, and the batch merge, sit on the batch lane.

**Markers.** `marker_for` picks one marker per event. The marker table is `MARKER_COLORS`, and the
legend reads the same table.

| Marker | Event |
|---|---|
| `rework` | A `comment` with the role `gate.rework`. |
| `fix` | A `commit` with `change: fix`. |
| `ci` | A `ci.run` that failed. |
| `review` | A `review` with the state `changes_requested`. |
| `note` | A `comment` with the role `cross-check` or `plan-amendment`. |
| `msg` | An `agent.messaged` event, on the agent lane. |

A commit, comment, review or CI run that matches no row draws no marker and appears only in the log.

**Time axis.** The axis draws as many labels as fit in its width: seven at most, fewer until no two overlap. The first label shows the date and the time. The later labels show `HH:MM`, and a label shows the date again where the day changes. The axis is measured again after the layout settles, when the window resizes or the panel opens or closes.
