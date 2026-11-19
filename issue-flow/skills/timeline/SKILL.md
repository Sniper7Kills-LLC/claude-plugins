---
name: timeline
description: Show what an issue-flow run did as one local HTML page - issue lanes, status changes, pull requests, checkpoints and batch merges - either as a snapshot or as a live page that refreshes while the run goes. Use when the user says "/timeline", "show the issue-flow timeline", "visualize the workflow", "show me what the run did", or "watch the run". Read-only. Writes one HTML file on this machine and publishes nothing.
---

# Timeline — see what the run did

This skill draws the tracker's history as one HTML page. The page has one lane per issue,
a lane for each batch, and markers for status changes, pull requests, checkpoints and
merges. The page filters by timeframe in the browser.

The skill is **read-only**. It reads issues, issue timelines and pull requests. It needs
no issue-flow run to be active, and it changes nothing on the tracker.

The output is a **local file only**. Never publish it, never upload it, and never create
an Artifact from it.

The tool is [`scripts/timeline.py`](../../scripts/timeline.py). The event schema and the
lane kinds are in [references/timeline.md](../../references/timeline.md).

## 1. Choose the mode and the timeframe

Ask the user with `AskUserQuestion` when the request does not say.

- **Snapshot** (default). Write the page once. Right for "what happened".
- **Live.** Rewrite the page on a timer, and the open page reloads itself. Right for
  "watch the run".

Timeframe: pass the user's words through as `--since` (`24h`, `7d`, `2w`, or a date). No
timeframe means the whole history. The page also filters in the browser, so a wide
collection is safe. To show one batch or one issue, pass `--batch <n>` or `--issue <n>`.

## 2. Find the forge and the repositories

Detect the forge as `references/forge.md` `## Detection` describes: a `forge` block in
`.issue-flow.json` wins, else read the git remote. Then pass these flags to `collect`:

- `--forge github` or `--forge gitea`
- `--repo <owner>/<repo>`: the repository that holds the **issues**.
- `--pr-repo <owner>/<repo>`: only when the pull requests live in a different repository.
  The `notes` field of `.issue-flow.json` or the user names it. A fork workflow is the
  usual case.

`collect` does not detect the forge by itself. It stops with exit 5 when `--forge` and
`--repo` are both missing.

## 3. Snapshot

Resolve the default page path, then collect and render in one pipe:

```bash
OUT="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" path)"
mkdir -p "$(dirname "$OUT")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge <forge> --repo <owner/repo> \
    [--pr-repo <owner/repo>] [--since <age>] \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" render --out "$OUT"
```

The default path is `<git common dir>/issue-flow/timeline.html`. No commit carries it,
and every worktree of the repository shares it. The user can ask for another path. Pass
that path to `--out` instead.

If `collect` exits with 2, the fetch failed. Say so with the error text. Do not write an
empty page.

## 4. Live

Start `watch` as a **background shell**, with `run_in_background: true`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" watch --forge <forge> --repo <owner/repo> \
    [--pr-repo <owner/repo>] [--since <age>] [--interval 300] [--max-minutes 240] [--out <path>]
```

- `watch` collects, renders with `--refresh <interval>`, sleeps, and repeats. It is a
  sleep loop, not a busy loop. Do not add `/loop` or a cron job.
- `--interval` is 60 seconds or more. The default is 300. Take a shorter interval as 60.
- `--max-minutes` is the lifetime. The default is 240. The loop ends itself at that time.
- A failed fetch keeps the last good page. Three failures in a row end the loop with
  exit 2.

## 5. Show the result

1. Print the **absolute path** of the page.
2. Open it when a desktop is present: `open <path>` on macOS, `xdg-open <path>` on Linux.
   If neither command exists, or the shell has no display, print the path only and say
   the user can open it in a browser.
3. Live mode: tell the user the page reloads every `<interval>` seconds, that the loop
   ends after `<max-minutes>` minutes, and how to stop it. When the user asks you to
   stop it, end the background shell (`TaskStop`, or `kill <pid>`). `SIGTERM` ends the
   loop cleanly and leaves the last page in place.

## Limits

- The page shows what the tracker records. Work that left no label, comment or pull
  request change does not appear.
- A snapshot is a copy of one moment. Run the skill again for a newer copy.
- The skill does not start or stop an issue-flow run.
