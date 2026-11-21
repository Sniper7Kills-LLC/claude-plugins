# Stage C — Integrate (two gates)

## Reading a worker notification (every worker: C1 members, C2 fix workers)

A worker can notify more than once. Measured on Claude Code 2.1.284
([worktrees.md](worktrees.md)): a worker that ends its turn while its own
background shell runs — its CI watch — notifies you at once, **marked interim** ("stopped
with background work of its own still running"), and notifies again when the shell exits.
So read each notification in this order:

1. **For an issue you have already gated or freed** → ignore it, whatever it carries. A
   late reply to a harness wake-up is not new work.
2. **Marked interim** → not an outcome, whatever it carries. Take no action and keep the
   slot; the verdict follows.
3. **Final, with verdict JSON** → act on its `outcome` (C1 below, or the C2 step that
   spawned it).
4. **Final, with no verdict JSON** (an error, a status line with nothing left running) →
   `SendMessage` the worker once asking for its verdict JSON. If the reply still has none,
   treat it as `blocked` with what it did say. Never hold the slot waiting for a
   notification that nothing will send.

## C1 — Sub-merge gate (on each worker verdict)

Triggered by a worker's final notification with its verdict — read it as above. Act on its `outcome`:

- **`needs-feedback`** → `forge.issue.status.set <n> status:needs-feedback` (removes whichever single `status:` label the issue is carrying — the outcome arrives from either `status:in-progress` or `status:in-review`), post the worker's `question` as an issue comment, park per the feedback policy. **Standalone or hotfix issue → remove its worktree now**, here and on `blocked` below, when it holds nothing uncommitted or unpushed ([worktrees.md](worktrees.md#parked-standalone-worktrees)). Where the answer goes then depends on the tree: removed → a fresh worker; kept → `SendMessage` to this worker, the only one that can push that work. Free the slot → Stage A/B.
- **`blocked`** → **first, is this your own bookkeeping?** If the `blocker` names a missing,
  empty or unresolvable `crossCheck`, the issue is not blocked — you are. Post (or repair) the
  step-4 cross-check comment, then re-spawn the worker with the URL filled in, and do **not**
  label `status:blocked`: Stage A skips that label, so parking here would strand a perfectly
  workable issue on your own omission. Otherwise `forge.issue.status.set <n> status:blocked`
  (removes whichever single `status:` label the issue is carrying), comment naming the
  `blocker`. Free the slot.
- **`checkpoint`** → the worker hit its turn budget with work pushed and nothing wrong. **Re-spawn a fresh worker** (do *not* `SendMessage` — that reuses the context the checkpoint exists to discard) with the same brief, `base: <remote>/issue/<n>-<slug>`, and the verdict's `remaining` text appended to the plan. **Do not touch the status label** — leave it exactly as the checkpointed worker left it: `status:in-review` if it had already opened the PR (runbook step 2), `status:in-progress` if it checkpointed during implementation before that. Either is correct, the replacement adopts the open PR rather than re-opening one, and flipping the label buys nothing but thrash. **Post one terse comment** — `checkpoint <k>: <one-line remaining>, replacement spawned` — which is what invariant 6 requires of any state change, what the chain cap below counts, and what Phase 0 reconstructs an interrupted run from. No gate, no digest line, and **do not free the slot** — the issue is still in flight and its replacement occupies the same one. This is routine flow control, not an exception — a long issue is *expected* to take two or three workers. **Remove the checkpointed worker's worktree** (`git worktree remove -f -f <worktree>` then `git branch -D <worktreeBranch>`, as in step 6 below) — the replacement gets a fresh one from the harness and re-checks-out the published branch, so keeping the old tree only leaks it. The commits are safe on the remote; that is what makes the handoff free. **Cap the chain: after 3 consecutive checkpoints on one issue**, stop re-spawning — a worker that checkpoints without visible progress recycles forever and pays a fresh context ramp each time. `forge.issue.status.set <n> status:needs-feedback` (or `status:blocked` if the last `remaining` names a hard blocker) — it removes whichever single `status:` label the checkpointed worker left, `status:in-review` or `status:in-progress` — comment with the chain of `remaining` notes, free the slot, and park it per the feedback policy. Count the chain from those checkpoint comments, and reset it whenever a checkpoint's PR shows new commits.
- **`ready-to-merge`** → sub-merge below (never on the worker's word alone):
  1. Verify: all PR threads resolved, PR targets the **integration branch**, worker reported the **local checks green** (`localChecks` in its verdict), and the session's `practices` were met (tests-first evidence, E2E where required, coverage threshold, commit style, docs — see [session-config.md](session-config.md)). When the PR adds or changes tests, `localChecks` must also carry the **pre-patch check** — the new tests were run against the pre-patch code and failed (`agents/issue-worker.md`, Verify): a test that already passed before the change is evidence of nothing, and its green is permanent. Missing or unstated goes back to the worker like any other unevidenced practice. A practice missed without a stated reason goes back to the worker; it is not waived at the gate. No provider CI to wait for.
  1b. **Acceptance criteria gate.** The worker returns `criteria: [{text, met, evidence}]` — one entry per acceptance criterion in the issue body. Check the list is **complete** (every criterion in the issue appears) and that each `met: true` carries real evidence (a test name, a command output, a file:line). Any criterion `met: false`, missing, or evidenced only by "implemented" goes **back to the worker** with the specific criterion quoted; a criterion the worker argues is wrong or unbuildable is a product question → `forge.issue.status.set <n> status:needs-feedback` (removes `status:in-review`), not a waiver. Acceptance criteria are the definition of done that `spec-to-issues` wrote down — this is where they are enforced, not months later in `project-review`.
  2. **Conflict vs the integration branch** (a sibling just sub-merged): mechanical → resolve directly or via a short-lived worker; **semantic** (two intents on the same logic) → `forge.issue.status.set <n> status:needs-feedback` on both issues (each removes whichever single `status:` label it carries), park, do not guess.
  3. **Authority gate.** If `prAuthority` is `review-all` or `propose-only`, do **not** merge: ready the PR, request review, `forge.issue.status.set <member> status:awaiting-review` (removes `status:in-review` — a bare label add here leaves the same split status step 5 exists to prevent), notify once, and go schedule other work. Merge only after a human approving review lands (a reaction or a vague "looks good" is not one). Under `autonomous`/`batch-review`, sub-merges are yours.
  4. Sweep for new comments on the PR (Stage A0) — a "hold this" posted a minute ago outranks your gate — then merge the sub-PR: `forge.pr.ready` then `forge.pr.merge.squash` (on Gitea, follow with `forge.branch.delete` — `tea pr merge` does not remove the branch). **Write the squash message yourself, ending in `[skip ci]`** — `--subject "<title> (#<pr>)" --body "[skip ci]"` on `gh`, `--title`/`--message` on `tea`, `title`/`message` on the MCP. Do not let the merge compose it: the default message is a *different thing on each forge*, and both defaults are wrong here. GitHub folds the member's commits into the body, so the token arrives by luck — luck a repository setting can withdraw. Gitea (measured, 1.25.3) folds nothing: its default is the title and `(#n)` alone, no token survives, and **the sub-merge starts a full CI run** — one per member, which is the entire cost the batch model exists to avoid, and it shows up only as runs nobody explains.

  4b. **Check what actually landed, and re-trigger if the batch PR is already open.** The merged-into branch is the only place that shows the real message, so read it there:

     ```bash
     git fetch <remote> <integration-branch> -q
     git log -1 --format='%s%n%b' <remote>/<integration-branch> \
       | grep -ciE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true
     ```

     Here `0` is the failure: the head is CI-visible and a run has just started on the integration branch. Non-zero is the intended state. (The exact token set matters on this check — it reads a member's subjects, and a bare `skip` match would flag an honest `fix: skip empty rows in the parser`. See [forge.md](../../../references/forge.md).)

     Then, **if the batch PR for this integration branch is already open, the batch's CI trigger is now stale — record that and act on it once**. A member that sub-merges after the batch PR opened leaves the batch gated on a run for a commit that is no longer head; on GitHub it leaves no run at all. Two cases:

     - **More members still in flight** (any member of this batch is `status:in-progress`/`status:in-review`, or a worker is running): do **not** push a trigger commit yet. Each one starts a full run on the integration branch, and four late members would burn four runs — the exact cost the batch model exists to avoid. Note the branch as needing a re-trigger and leave it CI-free.
     - **This was the last member**: push a fresh trigger commit now — subject only, no body, verified with the pre-push check in C2 step 1 — and re-anchor the CI watch to the new head SHA.

     Either way C2 step 1 is the backstop: it re-triggers on whatever is head at gate time and anchors the watch there, so the batch can never merge on a run for a superseded commit. This step exists because the suppression happens *here*, in C1, while the rule about re-triggering lives in C2 step 1, and worker completions arrive asynchronously long after C2 step 1 was "done".
  5. **One transition, nothing else in this step** (one command on `gh`/`tea`; two back-to-back
     calls on the Gitea MCP interface, then verify — see [forge.md](../../../references/forge.md)):
     `forge.issue.status.set <member> status:batched` — adds `status:batched` and removes
     **whichever single `status:` label the issue is actually carrying**: `status:in-review`
     normally, `status:awaiting-review` when it came through the authority gate at step 3. Do
     this **before** the bookkeeping in step 6, not folded into it.

     This step is deliberately alone because folding it in is measured to fail. Across three
     live runs, **every** `→ status:batched` transition added without removing, while the
     `ready → in-progress` and `in-progress → in-review` swaps on the very same issues were
     correct every time. The difference is not the vocabulary — it is that those two are
     standalone instructions and this one used to be a clause inside a step that also merges,
     ticks, reaps a worktree and launches a successor. An issue carries **at most one**
     `status:` label ([labels.md](labels.md)); two at once poisons every later
     query that selects by status, and Stage A triage reads exactly those queries.
  6. **Then the bookkeeping.** Tick its checkbox
     on the epic/batch tracking issue (edit only your own marker block — see [collaboration.md](collaboration.md)), then remove the worktree from the worker's completion notification (`worktreePath`, or the `worktree` field of its verdict): `git worktree remove -f -f <worktree>` (`-f -f`: a lock can outlive its agent — [worktrees.md](worktrees.md#teardown-is-still-the-pms-job)) then `git branch -D <worktreeBranch>` — the harness branch, `worktree-agent-<id>`, named in the same notification (a worker's tree always holds commits, so the harness never auto-removes it, and removing the tree leaves its harness branch behind). Take the name from the notification: `git worktree list` never shows it, because the worker's first checkout moved the tree onto `issue/<n>-<slug>`. Every mention of `worktree-agent-<id>` in a per-worker step means this value. Launch any member that was sequenced behind it. Free the slot → Stage A/B.

  **Standalone/hotfix (`ci: run`) has no sub-merge** — its PR targets dev and runs provider CI. Gate it like a batch PR into dev: the batch-PR column of `prAuthority` (under the default `batch-review`, a human approving review), CI green for the head SHA, threads resolved, criteria evidenced. Merge with `--squash` (C2 step 5's message-and-check rules apply). Then **close the issue**: `Closes #` auto-closes only when dev is the default branch — otherwise close it manually with a comment linking the PR — and clear its lingering status label either way. Remove the worktree, and hand off to Stage D.

  **Anything that goes back to the worker** (an unevidenced criterion, a missed practice, a review comment, a mechanical conflict) goes back by **`SendMessage` to the worker's `agentId`** (recorded at launch, Stage B step 5) — it still holds its worktree and its branch, so nothing is re-pointed. Re-spawn only if it is no longer addressable, and then pass `base: <remote>/issue/<n>-<slug>`, never the integration branch: a fresh worker starts on the default branch, and pointing its branch at the integration branch would drop the PR's commits. Keep the worktree until the issue is `status:batched` or terminally parked — **except on `checkpoint`**, which reaps the worktree immediately (the status label is left untouched and the replacement worker gets a fresh tree from the harness; see Stage C1). See [references/issue-worker.md](issue-worker.md).

## C2 — Batch gate (when a batch completes)

A batch is complete when every member is `status:batched` **or** terminally parked
(`blocked`/`needs-feedback`). If some members are parked: **ship-partial decision** —
ship the sub-merged members now if they stand alone (comment the decision on the
tracking issue; move the parked members to a future batch), or hold the batch if the
parked work is entangled. That call is the PM's.

1. Open **one PR: integration branch → dev.** Title `Epic #<n>: <title>` / `Batch #<n>: <summary>`; body lists every member with `Closes #<m>` lines. Push an empty commit **without** the skip token to make CI run (`git commit --allow-empty -m "ci: run full suite for batch #<n>"` — one `-m`, subject only, **no body**).

   **Verify the trigger commit before you push it.** Both providers scan the *whole* commit message — subject **and body** — for the skip token, so a body that merely *explains* the token still suppresses the run. Measured in a live run: a trigger commit whose body contained a sentence describing that batch members carry the token registered no CI at all; the explanation performed the suppression. The check is one line, and it runs in the integration-branch worktree before the push:

   ```bash
   git log -1 --format='%s%n%b' | grep -ciE 'skip|no ci' || true    # must print 0
   ```

   Two details in that line are load-bearing. It covers the **whole token set** — GitHub honors `[skip ci]`, `[ci skip]`, `[no ci]`, `[skip actions]` and `[actions skip]`, so matching `skip` alone misses `[no ci]` and reports a clean `0` for a commit that will not run. (GitHub's `skip-checks: true` trailer is real but out of scope for the greps: it only suppresses when preceded by **two** empty lines, which a line-based `grep` cannot check and neither `-m`-built messages nor a squash fold can produce — see [../../references/forge.md](../../../references/forge.md).) And `grep -c` **exits 1 when it counts 0**, so the `|| true` keeps the passing case from aborting a `set -e` script — without it the check fails exactly when the commit is good.

   Non-zero → amend the message down to the bare subject and re-check. This failure is silent by construction: no run is created, so a PM that reads the check list once sees an *absent* check and can mistake it for a pending one. Poll to a terminal verdict (Stage `ci-watch` in [../../references/forge.md](../../../references/forge.md)) and never read its `no-run-registered` as green — merging a batch on an absent check merges it with zero CI.

   **The trigger holds only until the next push.** Anything that lands on the integration branch after this commit becomes the new head, and the usual next thing is a late member's sub-merge, whose squash body carries `[skip ci]` in from the member's own commits (Stage C1) — nobody typed it, and the run silently stops registering again. So the trigger is not a one-time step at the top of C2: **re-run it after every later push to the integration branch**, and gate the merge on a terminal verdict for the SHA that is head *now*, not for the SHA that was head when the PR opened. Anchor the watch to `git rev-parse HEAD` after the final push (the `head_sha` anchor in [../../references/forge.md](../../../references/forge.md) does this) so a green run for an earlier commit can never stand in for the one you are merging.

   **On `no-run-registered`, read the commit before reacting** — `git fetch <remote> <branch> -q; git cat-file -e <sha>^{commit} 2>/dev/null || echo sha-not-local; git log -1 --format='%s%n%b' <sha> | grep -niE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true` (the `|| true` matters: `grep` exits 1 on zero matches, which is the *no-token* branch — the one that must still be reachable inside a `set -e` script; the fetch and the existence check matter for the same reason in reverse — on a SHA no local ref covers, `git log` exits 128, the empty pipe reads exactly like a clean message, and `|| true` hides both — `sha-not-local` is its own outcome, so resolve it before reading the count at all). A hit is a suppressed commit: push one clean subject-only trigger and re-watch. No hit → **re-poll the run list for that SHA once, after another 60–120 seconds, before concluding anything**: the `pending:none` window is 60 seconds and a slow self-hosted runner registering late is the most common clean-message `no-run-registered`. Still nothing on the second poll → **read the PR before blaming the runner**: `forge.pr.view <pr>` for `mergeable`. On GitHub a `CONFLICTING` PR, a base changed after opening, or a conflict cleared from the base side all register no run and no error — conflicting → resolve it (step 4, now); mergeable → close and reopen the PR once ([forge.md](../../../references/forge.md), *Recovering from `no-run-registered`*). Only a PR still without a run after the reopen is a runner or workflow-file problem, which a re-push does not fix — resolve it or substitute the local gate at step 3 and say so on the PR. One clean re-trigger, not a loop (the re-poll is not a re-trigger).
2. **Batch review — optional, but the choice is recorded.** One reviewer pass over the whole batch diff (cheap — an **unnamed** subagent, no CI; naming it without `isolation:` strands the result) to catch cross-member integration issues the per-issue reviews couldn't see. Run it by default for any batch with more than one member; skipping is reasonable for a single member or a diff that is entirely one member's work. **Either way, say which on the batch PR** — the review's findings, or one line naming the skip and the reason. A skipped optional step and a step that found nothing leave the same trace, which is none, and the step then quietly stops happening: measured across two runs, where it ran and found two members' comments made untrue by a sibling, then did not run at all and nobody could tell. Include **prose one member's change made untrue** — a comment, docstring or test name in *another* member's files that describes behavior this batch just changed. This is the one class the Stage B cross-plan check cannot reach: the plans were genuinely disjoint and the check was right to clear them, and the drift only exists once the code lands. Measured in a live run: a fixture comment asserted a boot-sync behavior that is false for the case it annotates, and a live-test docstring still described a silent-join that a sibling member had just ended. Both are wrong in a way that misleads the next reader while every test passes.

   Include a **maintainability pass** against the project's quality rules
   (`.claude/rules/quality.md` when the planner scaffolded one, else the default slop
   list in [parallelism.md](parallelism.md)) — one verdict per rule, pass or
   fail, over the whole batch diff. This is the defect class a green suite never catches
   and per-member self-review systematically under-penalizes: try/catch that only
   rethrows, defensive casts against impossible states, abstractions with one caller,
   dead code. Enumerated rules turn "looks clean" into something auditable.

   **The review reports every class it was asked to cover, including the ones that came back clean** — name what you compared and state the negative, exactly as the Stage B cross-plan check does. A review that only mentions what it found cannot be told apart from one that never looked for it, and the classes that are usually clean are the ones that quietly stop being checked. This applies to the diff itself too: if a file appears in the PR's file list with a size change but no visible content — git treats it as binary, or it exceeds the host's diff cap — then it was **not reviewed**, whatever the lenses reported. Read both versions directly and say in the review that you did (see [parallelism.md](parallelism.md)).

   Include a **cross-batch check** when another integration branch is live: compare this batch's schema/migration files, seed data and shared config against the other live branches' — two batches each adding a migration are individually valid and collide on merge. A collision found here is resolved now (renumber/rebase the migration); a semantic one parks both.
3. **CI runs once** — or, when Phase 0 found **no CI in the repo**, an isolated subagent (`isolation: "worktree"`, `base: <remote>/<integration-branch>`) runs the project's full suite and returns a short pass/fail summary, and that is the gate. Say in the digest which of the two happened.

   Two cases sit between those, and both were measured in a live run:

   - **CI exists but produced no usable result** — the job never started (billing, a disabled runner, a workflow the provider cannot parse; on Gitea an unparseable workflow registers *no run at all*). The check may read **red without having executed anything**, which is not a code failure and must not be treated as one. Substitute the local gate above, and **say so on the PR**: name the reason CI did not run, and present the result as a local gate, never as "CI green". A human reading the PR must not have to infer that the red check is meaningless. Mechanize the "did it actually run" question rather than inferring it from prose:
     - GitHub: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge github --repo <owner/name> --sha <head_sha>`
     - Gitea: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge gitea --repo-path . --sha <head_sha>` (auth/owner/repo resolve from the checkout's remote via `tea api`, same as every other Gitea call in this skill — see [../../references/forge.md](../../../references/forge.md))

     Exit 0 only when retrievable log bytes exist for that SHA — a status with zero log bytes is treated the same as no run at all, regardless of what the status field says. Exit 2 means either the run is still in progress (logs aren't retrievable yet — that is not evidence it didn't run; re-check shortly) or the fetch itself hit an infra error (auth, rate limit, `tea`/`gh` unusable in that checkout) — also retry. Exit 5 means the invocation itself is malformed (`--forge github` without `--repo`, or neither `--forge` nor `--fixture` given) — fix the command, don't re-poll it. Only exit 1 means CI genuinely never executed.
   - **CI ran green but did not exercise the code** — a suite whose integration tests self-skip when their service is unreachable exits 0 and reports green (see the false-green trap in [agents/issue-worker.md](../../../agents/issue-worker.md)). Some projects point CI at an unreachable database deliberately. Where that is true, a green CI run is **weaker** than a local run against a live service, and the local gate is the real one. Confirm the tests that matter **executed** rather than skipped, wherever the gate ran, and state the counts.

   On failure either way: spawn a fix worker the same way (read its notifications per *Reading a worker notification* above) — `isolation: "worktree"` with the **integration branch** as its `base`, so its `git checkout -B <integration-branch> <remote>/<integration-branch>` lands it exactly there — fix, push (fix commits may `[skip ci]` until ready to re-run), re-trigger — the re-trigger commit gets the same subject-only message and the same one-line check as step 1.
4. **Conflict vs dev** (another batch landed first): resolve once, at this level — mechanical directly, semantic → park per policy.
4b. **Authority gate.** Under the default `batch-review` (and under `review-all` /
   `propose-only`), the batch PR needs a **human approving review** before it merges:
   request review, `forge.issue.status.set <tracking> status:awaiting-review` (removes
   whichever single `status:` label the tracking issue is carrying), notify once, and go
   do other work — never block on it. Requested changes → fix worker → re-request review.
   Only `autonomous` lets you merge a batch PR yourself. Branch protection always wins
   over this setting; never route around it and never admin-merge. Sweep for comments
   (Stage A0) immediately before merging.
5. Merge: `forge.pr.merge.commit` (plus `forge.branch.delete` on Gitea) for batch PRs (preserves the per-issue squashed commits; use `--squash` only if the project's visible style demands it). Standalone/hotfix PRs: `--squash`.

   **Write the merge message, and check `dev`'s head afterwards — every time, whatever the style.** Pass the subject and an **empty** body, keeping the merge style this step already chose — `gh pr merge <pr> --merge --subject "<title> (#<pr>)" --body ""` for a batch PR, and the same flags with `--squash` for a standalone or a project whose visible style demands it (`--title`/`--message` on `tea`; `title`/`message` on the MCP — see [../../references/forge.md](../../../references/forge.md)). **Do not copy a `--squash` example onto a batch PR**: it collapses the per-issue commits this step exists to preserve, and that is not undoable after the branch is deleted. The default squash message on GitHub folds in every commit on the branch, so a batch PR carries every member's `[skip ci]` onto `dev`, the post-merge push registers no run, and any push-triggered deploy never starts — Stage D then waits on a deployment nothing will begin. `forge.pr.merge.commit` does not fold *the branch's commits*, which is one reason it is the default for batch PRs, but it is not immune: a repository configured to build merge-commit messages from the pull request title and description folds the batch PR **body** in instead, and that body lists members and routinely discusses the token. So the check is unconditional, not squash-only:

   ```bash
   git fetch <remote> dev -q
   git log -1 --format='%s%n%b' <remote>/dev \
     | grep -ciE '\[(skip[ -]?ci|ci skip|no ci|skip actions|actions skip)\]' || true    # must print 0
   ```

   Non-zero means `dev`'s head is suppressed. **The remedy is one clean subject-only empty commit on `dev`, pushed** — and the push is the whole point, so name the branch explicitly rather than committing wherever the current worktree happens to sit (at this point in C2 that is usually the integration-branch worktree step 7 is about to delete, so an unqualified `git commit` publishes nothing and restarts nothing while reading as done):

   ```bash
   git fetch <remote> dev -q
   git worktree add .claude/worktrees/dev <remote>/dev
   git -C .claude/worktrees/dev commit --allow-empty -m "ci: run suite for batch #<n>"   # one -m, subject only, no body
   git -C .claude/worktrees/dev push <remote> HEAD:dev
   git -C .claude/worktrees/dev rev-parse HEAD                                           # the new correlation anchor
   ```

   A worktree, not a `checkout -B` in the primary checkout — same reason the PM's other sequential git work uses one ([references/batching.md](batching.md)): it names the branch and really pushes without moving the user's `HEAD` or force-moving their local `dev`.

   That restarts both the run and any push-triggered deploy. It is not a rewrite: the merge commit stays exactly as it is, and rewriting it is what you must not do. **Re-anchor after it**: the recovery commit is now `dev`'s head, so it — not the merge commit — is the SHA the CI watch polls and the SHA Stage D correlates the deployment's `commitId` against. Record it in place of the merge commit, or the deploy watch will hunt for a SHA no deployment carries and report `no-deployment-observed` for a deploy that ran fine. If `dev` is protected against direct pushes, start the deploy the project's own way (`gh workflow run <file> --ref <branch>` when the deploy workflow declares `workflow_dispatch`; the project's wired start command — `deploy.startCmd` — otherwise; see [references/deploy.md](deploy.md)) — and say in the digest which of the two happened, because a deploy that a human started by hand must not be reported as an automatic one.
6. **Close member issues.** If dev is the default branch, `Closes #` handles it; if not, close each member manually with a comment linking the batch PR. Close the batch tracking issue; the epic closes when its last child does. Clear lingering status labels.
7. Tear down: sweep for leftovers with `git worktree list --porcelain`, `git worktree remove -f -f` any entry on this batch's `issue/*` branches (`-f -f` because a leftover from a killed session is still locked; the notification paths cover the ones you tracked, the sweep catches the rest), `git worktree prune`, then run the **dead harness-branch sweep** from [worktrees.md](worktrees.md#teardown-is-still-the-pms-job) — a `worktree-agent-*` branch cannot be matched to a batch, but one that no worktree has checked out is dead; delete the integration branch (the merge did if `--delete-branch`).
8. **Keep the spec honest** (when the project has one — see [spec-maintenance.md](spec-maintenance.md)): append a dated line to `docs/specs/spec.md` § Changelog for every scope decision this batch involved (ship-partial, an answered product question, a hotfix that changed behaviour), advance any fully-closed feature to `status: built`, and file a `type:spec-update` issue when the **documented behaviour** anywhere under `docs/specs/` actually diverged from what shipped — a `features/*.md`, or `spec.md`'s Terms, data model or cross-cutting concerns, which every worker and `spec-to-issues` read the same way. **Record any decision with lasting technical rationale as an ADR** in `docs/adr/` — a gate dispute resolved one way over another, an approach chosen against a considered alternative, a worker finding flagged `adr-worthy` — per [spec-maintenance.md](spec-maintenance.md); a `Carried forward` comment dies with the batch, an ADR does not, and ADRs apply even on a project with no spec. What does **not** earn one: a routine implementation choice, a decision already in the spec, anything with no alternative that was seriously considered — an ADR per batch is the churn signal, not the goal. Commit it all with the batch.
9. Post a **status digest** (Stage E reporting). Hand off to **Stage D** if a deploy target exists.

**Promotion to live** (dev ≠ live): never automatic. On user request, open a `dev → live` PR through the same gates.
