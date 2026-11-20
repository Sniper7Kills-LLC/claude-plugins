---
name: timeline
description: Show what an issue-flow run did as one local HTML page - issue lanes, status changes, pull requests, checkpoints and batch merges - either as a snapshot or as a live page that refreshes while the run goes. Use when the user says "/timeline", "show the issue-flow timeline", "visualize the workflow", "show me what the run did", or "watch the run". Read-only on the tracker. Writes one HTML file and the PR head refs under refs/issue-flow/ on this machine and publishes nothing.
---

# Timeline — see what the run did

This skill draws the run's history as one HTML page. The page has one lane per issue,
a lane for each batch, and markers for status changes, pull requests, checkpoints and
merges. The page filters by timeframe in the browser.

The skill is **read-only**. It reads issues, issue timelines, pull requests (with their
comments, commits, reviews and CI runs) and the local-event log. It needs
no issue-flow run to be active, and it changes nothing on the tracker. To read commit diffs
without API calls, it fetches PR heads into `refs/issue-flow/` of the local repository. It
changes no branch, tag or work-tree file.

The output is a **local file only**. Never publish it, never upload it, and never create
an Artifact from it.

The tool is [`scripts/timeline.py`](../../scripts/timeline.py). The event schema and the
lane kinds are in [references/timeline.md](../../references/timeline.md).

## 1. Choose the mode and the timeframe

Ask the user with `AskUserQuestion` when the request does not say.

- **Snapshot** (default). Write the page once. Right for "what happened".
- **Live.** Rewrite the page on a timer, and the open page reloads itself. Right for
  "watch the run".

Timeframe: pass the user's words through as `--since`. The forms are a count and a unit
(`<n>h`, `<n>d` or `<n>w`, for example `24h`, `1d`, `7d`, `2w`), an ISO date (`2026-05-01`),
or an ISO timestamp (`2026-05-01T10:00:00Z`). No timeframe means the whole history. The page also filters in the browser, so a wide
collection is safe. To show one batch or one issue, pass `--batch <n>` or `--issue <n>`.

## 2. Find the forge and the repositories

Detect the forge as `references/forge.md` `## Detection` describes: a `forge` block in
`.issue-flow.json` wins, else read the git remote. Then pass these flags to `collect`:

- `--forge github` or `--forge gitea`
- `--repo <owner>/<repo>`: the repository that holds the **issues**.
- `--pr-repo <owner>/<repo>`: one flag for each other repository that holds pull requests.
  Repeat the flag. A fork workflow is the usual case: the member PRs are on the fork and the
  batch PR is on the issue repository. `collect` always reads the `--repo` repository too, so
  never repeat it. Find the other repositories from these, and drop repeats:
  1. The `prRepos` array of `.issue-flow.json`.
  2. The remotes of the checkout (`git remote -v`) that point at the same forge host, when the
     repository they name is not `--repo`. A remote with no PRs for this project gives an empty
     read, which costs nothing.
  3. The user's words, and the `notes` field of `.issue-flow.json`.
  Tell the user which repositories you read.

`collect` reads the tracker on 8 threads by default. `--jobs <n>` (1 to 16) changes that, and
the document is the same for every value. A tracker rate limit is retried 3 times, then the
run fails with exit 2. Pass `--jobs 1` only to debug one read at a time.

`collect` does not detect the forge by itself. It stops with exit 5 when `--forge` and
`--repo` are both missing.

## 3. Snapshot

Run `snapshot`. It collects and renders in one process:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" snapshot --forge <forge> --repo <owner/repo> \
    [--pr-repo <owner/repo> ...] [--since <age>] [--out <path>]
```

`snapshot` takes every `collect` flag and `--out`. `--out` is the page path. The default is
`<git common dir>/issue-flow/timeline.html`. No commit carries it, and every worktree of the
repository shares it. The user can ask for another path.

The exit code is the result. Do not pipe the tool, and do not read `PIPESTATUS`: it is empty
in zsh. Exit 0 means the page is written, and `snapshot` prints one line on stderr
(`wrote <path>: <n> events, <n> lanes, <n> problems`). A problem is a rework request, a
changes-requested review or a failed CI run. The page lists them in its Problems view. Any other exit code means no new page:
2 is a failed fetch or a failed render, 5 is a bad flag. Say so with the error text. Do not
write an empty page.

## 4. Live

Start `watch` as a **background shell**, with `run_in_background: true`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" watch --forge <forge> --repo <owner/repo> \
    [--pr-repo <owner/repo> ...] [--since <age>] [--interval 300] [--max-minutes 240] [--out <path>]
```

- `watch` collects, renders with `--refresh <interval>`, sleeps, and repeats. It is a
  sleep loop, not a busy loop. Do not add `/loop` or a cron job.
- `--interval` is 60 seconds or more. The default is 300. Take a shorter interval as 60.
- `--max-minutes` is the lifetime. The default is 240. The loop ends itself at that time.
- A failed fetch keeps the last good page. Three failures in a row end the loop with
  exit 2.

## 5. Show the result

1. Print the **absolute path** of the page.
2. Open it only when the session is interactive: run `open <path>` on macOS or
   `xdg-open <path>` on Linux. In a headless session (`claude -p`), do not open it. Print the
   path only, and say the user can open it in a browser. When you cannot tell whether the
   session is interactive, treat it as headless: print the path and do not run `open`.
3. Live mode: tell the user the page reloads every `<interval>` seconds, that the loop
   ends after `<max-minutes>` minutes, and how to stop it. When the user asks you to
   stop it, end the background shell (`TaskStop`, or `kill <pid>`). `SIGTERM` ends the
   loop cleanly and leaves the last page in place.

## Limits

- The page shows what the tracker records (issues, issue timelines, pull requests, comments,
  commits, reviews and CI runs) and the local-event log of agents and worktrees. Work that left
  no trace in either place does not appear.
- A snapshot is a copy of one moment. Run the skill again for a newer copy.
- The skill does not start or stop an issue-flow run.
