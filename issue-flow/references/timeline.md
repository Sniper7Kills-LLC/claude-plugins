# The workflow timeline — document schema

`scripts/timeline.py collect` rebuilds what an issue-flow run did, from the tracker alone.
It prints one JSON document. The renderer, the `watch` command and the local-event hook
all read this schema, and `timeline.py` is its only producer. The tracker reads it uses are
in [forge.md](forge.md) (`forge.issue.timeline`, `forge.issue.list.all`, `forge.pr.list.all`).

The collector works because issue-flow leaves a tracker trace for every state change: a
label, a comment, a PR state.

## Document

```json
{
  "version": 1,
  "forge":   {"type": "github", "host": null, "owner": "o", "repo": "r", "pr_repo": "o/other"},
  "range":   {"since": "2026-05-01T00:00:00Z", "until": null},
  "issues":  {"51": {"title": "…", "url": "…", "state": "open", "labels": ["status:in-review"],
                      "status_at_start": "in-progress"}},
  "batches": {"50": {"tracking": 50, "branch": "batch/50-bar", "members": [51, 52], "pr": 61, "prs": [60]}},
  "convergence": [{"gate": 12, "filed": 3, "closed": 5}, {"gate": 13, "filed": 2, "closed": null}],
  "events":  [{"ts": "2026-05-01T11:00:00Z", "kind": "status", "issue": 51, "pr": null, "batch": 50,
               "actor": "alice", "from": "ready", "to": "in-progress", "detail": null}]
}
```

- **Timestamps** are ISO-8601 UTC with a `Z` suffix, at one-second precision. The collector
  converts every forge offset.
- **`forge.pr_repo`** is present only when the PRs come from another repository
  (`--pr-repo`).
- **`range`** repeats `--since` and `--until` after they are resolved to UTC. `null` means
  unbounded.
- **`issues`** is keyed by issue number (a string). It holds the issues that have an event in
  the result, or every fetched issue when no filter applies. `status_at_start` is the
  `status:*` value (without the prefix) the issue had at `range.since`, replayed from its full
  history. It is `null` when there is no `--since`, or when the issue had no status then.
- **`batches`** is keyed by the tracking-issue number. `tracking` is that number.
  `members` are the issue numbers in its checklist. `branch` is the integration branch,
  found from a PR whose head is `batch/<n>-…` or `epic/<n>-…`. `pr` is that PR (the batch PR).
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

**How the forges differ, and how `collect` hides it.** A draft on GitHub is a flag, so
`draft` also reads true when a `ready_for_review` event exists. A draft on Gitea is a
`WIP:` title prefix, so `pr.ready` comes from a title change that removes it. A Gitea label
entry is an add when its `body` is `"1"`. Both forges give the same events for the same
history.

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
| `issue` | `status`, `issue.*`, `pr.*`, `checkpoint` | `bar`: a bar coloured by status. Markers show PR open, PR merge, checkpoint and park (a `status` event whose `to` is `needs-feedback` or `blocked`). The bar at the left edge starts with the status the issue had at the range start (`status_at_start`, or the last earlier event). |
| `batch` | `batch.*` | `marks`: one marker per event. The lane leads its group and shows the batch merge. The tracking issue's own events sit in the same group. |
| `worktree` | `worktree.*` | `marks`: one lane per `worktree` key, one marker per event. |
| `agent` | `agent.*` | `marks`: one lane per `agent` key, or per `session` key when there is no `agent`. |
| any other prefix | the same prefix | `marks`: a generic lane, labelled with the prefix. |

Events with no batch go in a final group named "No batch". A batch with no `branch` shows
only its number. The page ignores event keys it does not know.

**Colors and legend.** `STATE_COLORS` (one row per status) and `MARKER_COLORS` in
`timeline.py` set every bar and marker color for both themes. The legend reads the same
tables, so a state cannot draw without a legend entry. A status that is not in the table
draws grey and gets its own legend entry.

**Page behavior.** The page embeds the document and draws it in the browser. It makes no
network request: the tracker links on issue lanes are the only URLs. The timeframe filter
(24h, 7d, 30d, all, or a UTC date range) re-draws without a new render. A range with no
events shows "No events in this range." `--refresh <seconds>` adds a meta refresh.
