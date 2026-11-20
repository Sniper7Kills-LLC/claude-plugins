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
| `agent.stopped` | A worker ended. | `agent`, `session`; `issue`, `outcome`, `branch` and `worktree` from its verdict. With no verdict, `outcome` and `issue` are null and `worktree` is the agent's own worktree (the `cwd` of the stop event). |
| `worktree.added` | `git worktree add` ran. | `worktree` = the path; `branch` when `-b` or `-B` names one. |
| `worktree.removed` | `git worktree remove` ran. | `worktree` = the path. |
| `agent.messaged` | The PM sent a message to an agent (`SendMessage`), for example a worker sent back for rework. | `agent` = the recipient exactly as the call named it, `session`. The line never holds the message text, the summary or the reply. |

`collect` fills `issue` for a worktree event from a branch named `issue/<n>-…`, and for a
later event on the same path. It fills `batch` from the issue. The range and issue filters
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

## Exit codes

`0` document written. `2` a fetch failed, or the forge returned an error body (`tea api`
exits 0 on an API error, so `collect` checks the body). Nothing goes to stdout. `5` the
invocation is malformed. `render` uses the same codes: `2` for unreadable or malformed
input.

## Lane kinds (the renderer)

`timeline.py render` draws one lane per issue, grouped under its batch. A lane kind is a row
in `LANE_KINDS` in `timeline.py`. The renderer reads the kind from the event `kind` prefix.
Another producer adds a lane type by adding a row and using that prefix.

| Lane kind | Events | Style |
|---|---|---|
| `issue` | `status`, `issue.*`, `pr.*`, `checkpoint`, and the `comment`, `commit`, `review` and `ci` events of the issue or its PR | `bar`: a bar coloured by status. Markers show PR open, PR merge, checkpoint and park (a `status` event whose `to` is `needs-feedback` or `blocked`). The bar at the left edge starts with the status the issue had at the range start (`status_at_start`, or the last earlier event). |
| `batch` | `batch.*` | `marks`: one marker per event. The lane leads its group and shows the batch merge. The tracking issue's own events sit in the same group. The detail events of a tracking issue (`comment`, `commit`, `review`, `ci`) go to its batch lane, not to an issue lane. |
| `worktree` | `worktree.*` | `marks`: one lane per `worktree` key, one marker per event. |
| `agent` | `agent.*` | `marks`: one lane per `agent` key, or per `session` key when there is no `agent`. `agent.stopped` carries a `worktree` too, but the lane key stays `agent`. |
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
