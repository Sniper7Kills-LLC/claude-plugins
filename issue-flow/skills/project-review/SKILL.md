---
name: project-review
description: >
  User-viewpoint review of the running app in a sandbox — the QA/documentation pass
  after issue-flow ships work. Use when the user says "/project-review", "review the
  project", "user-test the app", "do a UX review", "build the user manual", or wants
  recent issue-flow work exercised end to end. The PM launches a sandbox, runs/creates
  E2E tests, fans out browser-driving ux-explorer agents that click through the app as
  end users (screenshots + manual-ready walkthroughs), and a code-auditor that sweeps
  for TODOs, stubs, and acceptance-criteria gaps. Nothing is fixed — the PM gathers
  every sub-agent report, files one tracker issue per finding, lands a manual +
  E2E-test PR via a review-scribe, then confirms launching issue-flow to work the new
  backlog.
---

# Project Review — use the app like a user, file what you find, fix nothing

This is the QA + documentation pass that follows building: exercise the project **as an
end user** in a **sandbox**, capture how it really behaves, and turn what you learn into
three deliverables:

1. **Tracker issues** — every bug, UX problem, gap, missing implementation, and code TODO
   found becomes an issue, filed **by the PM**, shaped for the issue-flow loop.
2. **A user manual** — per-flow walkthrough pages with screenshots, from the explorers'
   real sessions, landed via a docs PR.
3. **E2E smoke tests** — run the existing suite against the sandbox, or create one
   codifying the explored happy paths, landed in the same PR.

**The prime directive: find, never fix.** No product code changes happen during a
review — not by you, not by any sub-agent. Docs, screenshots, and tests are the only
things written. Fixing is issue-flow's job, and handing the new backlog to issue-flow is
how a review ends.

## Two roles

- **PM (the main thread).** Orchestrates: preps the sandbox, scopes the review, fans out
  reviewers, **gathers every report, dedups, and files all issues itself**, lands the
  deliverables PR, reports, and hands off to issue-flow. The PM never explores inline
  and never lets a sub-agent file issues.
- **Sub-agents (background, self-contained prompts).** `issue-flow:ux-explorer` walks
  one flow as a user via the browser MCP; `issue-flow:code-auditor` sweeps the code
  read-only; `issue-flow:review-scribe` writes the manual + E2E tests. All are
  decision-free and **none of them files issues or fixes anything**. Each declares its
  own model tier in its agent definition — spawn them without an `opts.model` override.
  (Fallback if an agent type won't resolve: spawn `general-purpose` on Sonnet and
  prepend its brief with the corresponding contract — "you are a decision-free
  <role>; never edit product code; never file issues; return the verdict JSON".)

This skill runs on **GitHub or Gitea**. All tracker interaction goes through the forge's
CLI — `gh` or `tea` — falling back to that forge's MCP server when the CLI is
unavailable. Every command is named as an abstract operation and resolved in
[../../references/forge.md](../../references/forge.md). Never hardcode `gh`.

**Every issue you file is Simplified Technical English** — the standard is
[`../../references/ste.md`](../../references/ste.md), and a planned project carries it at
`.claude/rules/ste.md` with its vocabulary in `docs/specs/spec.md` § Terms. You are
turning a sub-agent's report into a work item, so the split matters: **write** the title,
the context and the "what to build" in STE; **quote verbatim** the reproduction steps,
the expected-vs-actual, the console and server-log excerpts, the `path:line` references,
and any acceptance criterion the finding contradicts. Evidence you reword is evidence you
have damaged.

---

# Phase 0 — Preflight

1. **Repo check.** Same as issue-flow: `git rev-parse --is-inside-work-tree`,
   `forge.repo.view`, `forge.auth.check`. Detect the remote name and the dev-vs-live
   branch model (review branches fork off dev when it exists, else the default branch).
2. **Browser check.** `ToolSearch("playwright browser navigate")` /
   `ToolSearch("chrome devtools")`. No browser MCP → tell the user the interactive half
   degrades badly (explorers fall back to WebFetch-level checks) and suggest:
   `claude mcp add playwright -s user -- npx -y @playwright/mcp@latest`. Offer to
   continue degraded (code audit + HTTP checks + docs from static analysis) or stop.
3. **Sandbox up.** The review runs against a **sandbox, never production**. Resolve in
   order, asking the user only when ambiguous:
   - A URL the user supplied for this review.
   - An already-running local dev server (probe common ports the repo's config names).
   - Something launchable from the repo: `docker compose up`, `npm run dev`,
     `make dev`, a devcontainer — launch it with `run_in_background: true`, wait for
     the port to answer, and keep the handle so you can stop it at the end.
   - A deployed **dev/staging** URL (from issue-flow's deploy detection). Never the
     production URL — explorers submit test data.
   Capture: `SANDBOX_URL`, and a **`logCmd`** that prints/tails the server logs
   (`docker compose logs --tail 100`, the background task's output file, or the
   provider's log command) — explorers use it to correlate errors. Seed test data if
   the repo has a seed script; note any seeded credentials for the explorer briefs.
4. **Scope — what shipped recently.** The review focuses on recent issue-flow output,
   plus a first-run pass:
   - Find the last review marker: `forge.issue.list` filtered on `project-review run`
     and bodies containing `<!-- project-review:`. Recent = closed issues / merged batch
     PRs since then (else since the user-given range, else ~the last 30 days).
   - `forge.issue.list` with `state: closed` + merged `Epic/Batch` PRs → group into
     **user-facing flows** (auth, core object CRUD, settings, etc.). Pull each issue's
     **acceptance criteria** — they become explorer `expectations` and auditor
     `recentWork`.
   - Always add one **"first-run user"** flow (land cold, figure the app out, reach the
     core action) even if nothing recent maps to it.
   - Cap at `MAX_FLOWS` (default 6; user-tunable) — prefer flows touching the most
     recent work; list anything skipped in the digest, never skip silently.
5. **Labels.** Bootstrap issue-flow's standard labels (its `references/labels.md`
   block, which already includes `review:finding`), created with `forge.label.create`.
   On Gitea, `forge.label.create` is not idempotent on its own — check
   `forge.label.list` first and create only what is missing, exactly as `labels.md`
   documents.
6. **Review workspace.** `RUN_ID = <YYYY-MM-DD>-<short-slug>`. Create branch
   `review/<RUN_ID>` off dev in its own worktree at
   **`.claude/worktrees/review-<RUN_ID>`** — inside the checkout, already gitignored, and
   inside the project root so a sandboxed Bash tool can write there. A sibling directory
   (`../review-<RUN_ID>`) is outside the project and may be blocked. Also create a scratch
   dir for explorer output: `<scratch>/review-<RUN_ID>/{screenshots,notes}`.

# Phase 1 — E2E baseline (existing suite)

Detect an existing E2E suite (`playwright.config.*`, `cypress.config.*`, `e2e/`,
`tests/e2e/`, a `test:e2e` script). If one exists, delegate a Sonnet child to run it
**against `SANDBOX_URL`** and return a pass/fail summary with failing-test excerpts —
don't run it inline (token-heavy output). Each real failure is a **finding** for
Phase 3 (type `bug`, evidence = the test name + failure excerpt). If none exists, note
it — the review-scribe creates one in Phase 4.

# Phase 2 — Fan out the reviewers

**The browser is a singleton.** The browser MCP (Playwright / Chrome DevTools) is one
shared browser session — every agent's `browser_*` calls hit the same tabs. Two
browser-driving agents in flight at once stomp each other's navigation and screenshots.
Therefore: **ux-explorers run strictly one at a time, sequentially** — launch the next
only after the previous one's verdict returns. Parallelism comes from everything that
*doesn't* touch the browser: while an explorer runs, the code-auditor sweeps, the PM
dedups/files issues from already-returned verdicts, and the E2E baseline child (which
uses the repo's own Playwright install, not the MCP) can run.

- **One `issue-flow:ux-explorer` per flow** from Phase 0 scope — **sequential, one in
  flight at any moment**. Brief per flow:
  ```
  url:           <SANDBOX_URL>
  flow:          <flow goal, phrased as a user goal>
  persona:       new user, no development knowledge of this app, laptop browser
  expectations:  <acceptance criteria quoted from the flow's recent issues>
  screenshotDir: <scratch>/review-<RUN_ID>/screenshots/<flow-slug>/
  notesFile:     <scratch>/review-<RUN_ID>/notes/<flow-slug>.md
  logCmd:        <the sandbox log command>
  testData:      <seeded accounts/records, if any>
  ```
- **One `issue-flow:code-auditor`** over the checkout — launch it in parallel with the
  explorer chain (it never touches the browser). Brief: repo path, scope (default
  whole source tree; focus paths touched by recent PRs first), `recentWork` = the
  recent issues with acceptance criteria quoted, conventions (generated dirs to skip).

The PM does not sit and wait — between explorer completions, process verdicts already
in hand (Phase 3 dedup/filing can start immediately). An explorer returning `blocked`
(app down, login impossible) is itself a high-severity finding; check the sandbox is
still up before launching the next explorer.

# Phase 3 — Gather & file (PM only — this is the gate)

Sub-agents **report**; only the PM files. On all verdicts collected:

1. **Merge findings** from every explorer, the auditor, and the Phase 1 E2E failures.
2. **Dedup** — within the run (two explorers hitting the same broken API → one issue
   listing both flows) and against the tracker (`forge.issue.list` filtered on
   `<title keywords>`, plus bodies carrying `<!-- project-review:` from prior runs).
   Update/comment an existing issue instead of double-filing.
3. **File one issue per finding** (`forge.issue.create`):
   - **Title:** user-language, from the finding (`Sign-up form loses data on validation error`).
   - **Body:** what happened (steps to reproduce, expected vs actual), **evidence**
     (screenshot reference, console/server-log excerpt, `path:line` for code findings),
     source (`Found by project-review run <RUN_ID>, flow: <flow>` / `code audit`),
     related issue links (`Relates to #<n>` for acceptance-criteria gaps — the shipped
     issue it contradicts), and the marker `<!-- project-review:<RUN_ID> -->`.
   - **Labels:** `review:finding` + `status:ready` when clear and actionable;
     `status:needs-feedback` (+ the question as a comment) when it needs a product
     decision (e.g. "is this flow supposed to exist?"). High-severity breakage of a
     core flow → add `priority:high`. A sprawling UX overhaul → file as `type:epic`
     with the findings as its checklist, and let issue-flow decompose it.
   - Screenshots referenced in issue bodies must be durable: they land in the docs PR
     (Phase 4) — link the repo path; until that PR merges, attach the image to the
     issue (`forge.issue.create` body upload or a comment) so the evidence stands alone.
4. Keep a run ledger (finding → issue #) for the digest and the summary issue.

# Phase 4 — Deliverables PR (manual + E2E tests)

1. Spawn **`issue-flow:review-scribe`** on the review worktree. Brief: worktree,
   branch `review/<RUN_ID>`, the explorers' `walkthrough` files, `screenshotDir` =
   `<scratch>/review-<RUN_ID>/screenshots/` (the **root** — it holds one `<flow-slug>/`
   subdir per flow, and those subdirs are preserved in the manual so identically
   numbered screenshots from different flows can't overwrite each other), flow outcomes,
   the Phase 1 E2E detection (`framework/dir/runCmd`), `SANDBOX_URL`, `manualDir`
   (default `docs/manual`), repo conventions, and **`steRule:`** — the path to the
   project's `.claude/rules/ste.md` if it has one, else the plugin's
   `references/ste.md` — so the manual is written to the same standard as the spec.
2. On its verdict: check `notesForPM` (app changes a test needs — e.g. missing
   test-ids — become **filed issues**, marker included, not fixes), then push the
   branch and open **one PR `review/<RUN_ID>` → dev**: title
   `Project review <RUN_ID>: user manual + E2E smoke tests`, body listing manual pages,
   tests added, the test-run result, and the filed-issue ledger. Docs + tests only —
   this PR is standalone, so **CI runs normally** on it.
3. Gate it like any PM merge: CI green, threads resolved → merge. CI failure caused by
   the new tests → send it back to a scribe re-run; **never patch product code to make
   a review test pass.**

# Phase 5 — Report & hand off to issue-flow

1. **Review summary issue.** Create (or update) an issue titled
   `Project review <RUN_ID>`, labeled **`review:finding` only**. Do **not** label it
   `flow:status` — that label means "an operator's live issue-flow session", and
   issue-flow's co-operator check (`references/collaboration.md`) would read this record
   as another person running the loop.
   Body (bookkeeping, not a work item): sandbox used, flows walked with the outcome of
   each, findings filed as a table (issue # / severity / type), the manual + E2E PR link,
   and anything skipped (flow cap, degraded browser).
   Marker `<!-- project-review:<RUN_ID> -->` — the next run scopes from it.
   Close it once the handoff decision is made.
2. **Terminal digest** (≤10 lines): flows walked, issues filed by severity, PR link,
   skipped items, open questions.
3. **Hand off.** The review ends by pointing issue-flow at the new backlog. Ask
   (`AskUserQuestion`): **launch `/issue-flow` now** to work the filed findings, or
   stop here with the backlog triaged and ready? If the user already told you up front
   to "review then fix", skip the ask and invoke the `issue-flow:issue-flow` skill
   directly — the findings are labeled `status:ready`/`status:needs-feedback`, so its
   Stage A triage picks them straight up.
4. **Teardown.** Stop any sandbox you launched, remove the review worktree after the
   PR merges, `git worktree prune`.

---

# Hard rules

- **Find, never fix.** No product code changes during a review — by the PM or any
  sub-agent. The only writes are screenshots, walkthrough notes, manual pages, E2E
  tests, and tracker issues/comments. A broken sandbox or failing flow is a finding,
  not a repair task.
- **One browser-driving agent at a time.** The browser MCP is a single shared session;
  ux-explorers run sequentially, and no other browser user (e.g. a deploy-verifier)
  may be in flight while one runs. Parallelize only non-browser work (code-auditor,
  E2E via the repo's own runner, PM filing).
- **Only the PM files issues.** Explorers, the auditor, the scribe, and any fallback
  children report findings; the PM dedups and creates every issue. One finding, one
  issue, with evidence and the `<!-- project-review:<RUN_ID> -->` marker.
- **User viewpoint is the standard.** Explorers are briefed as non-developers judging
  what they can see and do; "works if you know the trick" is a UX finding. Logs and
  code are for *evidence*, never for excusing behavior a user would call broken.
- **Sandbox only.** Never explore production, never real credentials, never real
  personal data; test data is clearly fake. Destructive admin actions stop the flow
  (`partial`) rather than run.
- **Every finding is evidenced** — screenshot, console/log excerpt, or `path:line`.
  No evidence, no issue.
- **Issues and manual pages are STE** (`references/ste.md`). Titles and your own prose
  are written to the standard; steps, excerpts, paths and quoted criteria stay verbatim.
- **Deliverables land via PR** (docs + tests → one standalone CI-run PR to dev),
  through the normal merge gate. Never push directly to dev/live; never merge red.
- **Caps are loud.** Flow cap, degraded browser, skipped areas — always in the digest
  and summary issue, never silent.
- **The review ends with the handoff** — confirm (or, if pre-authorized, launch)
  issue-flow on the new findings. Filed-but-forgotten defeats the loop.
