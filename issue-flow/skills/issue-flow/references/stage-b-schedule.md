# Stage B — Form batches & schedule work

1. **Form batches from the ready pool — after checking the review WIP cap.** Count the
   **batches with anything parked `status:awaiting-review`** — a batch counts when its
   tracking issue carries the label (the C2 hold under `batch-review`) **or any of its
   members does** (the C1 per-PR hold under `review-all` / `propose-only`; a batch with
   three held members is still one batch). At or above `reviewWipLimit`
   ([session-config.md](session-config.md), default 2), **form no new batch**:
   finished-but-unreviewed batches are inventory stacked in front of the pipeline's real
   bottleneck — the human reviewer — and another parallel batch adds inventory, not
   throughput, while its conflicts against the unreviewed work compound. Keep filling
   worker slots for batches already in flight, work the parked questions, and notify the
   operator **once per cap event** (digest + push) that reviews are the constraint —
   naming the setting and its value (`reviewWipLimit: 2`), since it is a default most
   operators never chose; an approval or merge frees the cap. The cap is soft and binds
   only under an authority setting that parks work for review — under `autonomous` it
   never triggers.
   (Skip this step entirely when `prGranularity` is
   `per-issue`: each ready issue goes straight to a worker on its own branch off dev with
   `ci: run`, and Stage C2 never runs.)
   - Every epic with ≥1 `status:ready` sub-issue → an **epic batch** on `epic/<n>-<slug>` (all its ready sub-issues are members).
   - Remaining loose `status:ready` issues → group into batches of ≤ `batchSize`, clustering by area/shared files/dependency chains (a dependency chain always lands in one batch, sequenced). Create a **tracking issue** per loose batch (`type:batch`, body = member checklist) and the integration branch `batch/<n>-<slug>`. A singleton batch is fine when nothing clusters.
   - `type:hotfix` / urgent `priority:high` singletons bypass batching → standalone worker, PR straight to dev with CI (`ci: run`). Batching is what they skip, not the merge gate: a standalone PR into dev takes the **batch-PR column** of `prAuthority` ([session-config.md](session-config.md)) — under the default `batch-review` it still needs a human approving review.
   - Branch creation and naming details: [references/batching.md](batching.md).
2. **Fill the pipeline up to `concurrency` workers** (across all live batches). Prefer finishing an in-flight batch over opening a new one — fewer live integration branches means fewer batch-level conflicts. **Overlapping file sets are fine** — isolation comes from per-issue worktrees and PM conflict resolution at sub-merge.
3. **Plan + claim, per issue (PM):**
   - **Locate (read-only, parallel):** fan out `Agent` calls **unnamed** (a `name:` without `isolation:` is a peer session that never reports back — [worktrees.md](worktrees.md); the shipped guard denies it) (`Explore`) to map the files/call-sites the issue touches; take back a short summary. **Tell every locate agent the batch's base branch and require it to read that ref**, not the default branch: for an epic batch, earlier members are already sub-merged into the integration branch and exist *nowhere else*. An agent that greps `main` will truthfully report a helper "does not exist anywhere" when a sibling built it an hour ago, and the worker then rebuilds it. **`git fetch` before you locate**, and have the agents read the fetched remote ref rather than whatever the working tree happens to be on — naming the right branch is not enough if the checkout behind it is stale. Both failures were measured in live runs: one locate pass read `origin/main` and missed a UUIDv5 helper a merged sibling had added, which would have produced a second id scheme against a unique column; a later one read a working tree that was a single merge behind `origin/main` and reported its issue's whole premise as fiction. The symptom is identical either way — a truthful "this does not exist anywhere" about code that does — so treat any such report from a locate pass as suspect until the ref it read is confirmed current. Verify it rather than accept it: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo . --remote <remote> --ref <the exact ref the agent was told to read> --pattern <symbol> --json` — `--remote` fetches before resolving `--ref`, closing the same stale-checkout gap this check exists to catch, and the resolved SHA it grepped is reported back as `resolved_sha`. Don't also pass `--expect-sha` resolved by your own shell (e.g. `$(git rev-parse <remote>/<branch>)`): that command substitution runs before the script's fetch, so it captures the *pre-fetch* tracking ref — in the exact stale-checkout case this check exists for, the script then reports `stale: true` against its own fresh fetch and the grep never runs at all. `--remote` alone already pins the check to a SHA it resolved itself. A nonzero exit (bad ref, `stale: true`, or a fetch failure) means the claim's input was wrong — investigate before believing the "does not exist." The primary fix is **the fetch itself, done before the locate pass reads anything** — `verify_claim.py` is the mechanized backstop for when that discipline is skipped or forgotten under a `git fetch` you believed you already ran, not a replacement for it. The same exposure exists at every `isolation: "worktree"` spawn, one layer later: the harness branches the new worktree from whatever `origin/<default-branch>` the PM's local refs already had (`worktree.baseRef: fresh`, [worktrees.md](worktrees.md)), not a fresh fetch, so a worktree can be created from the same stale object store this section is about. The worker's own documented first action is `git fetch <remote>` before its `git checkout -B` ([worktrees.md](worktrees.md)), which corrects it — but that is the same kind of advisory instruction, unmechanized, as "`git fetch` before you locate" above.
   - **If the issue calls an external service, confirm the interface before you plan it.** Delegate a read of the vendor's current documentation (or the CLI's own `help`) and put the doc URL + pinned version in the plan. Never plan against a remembered API shape — see [references/external-apis.md](../../../references/external-apis.md). Cannot confirm it → `status:needs-feedback`, not a guess.
   - Comment a short plan on the issue: approach, files, **build order as vertical
     slices** (the thin end-to-end path first, then what deepens it — migration, real
     logic, error handling), the intended call stack for the main path, the tests the
     worker is expected to add, and out-of-scope. The slice order is what makes a
     mid-issue checkpoint hand over working behaviour instead of a half-built layer, and
     the named tests are what the C1 gate's pre-patch check verifies.
   - **Claim with compare-and-set:** immediately before claiming, re-read the issue's labels/assignees; if another worker already took it, abandon and pick the next. Else `forge.issue.assign`, then `forge.issue.status.set <n> status:in-progress`. **On a multi-member batch, assign every member here but hold the status swap** until each one actually launches (step 5) — step 4 forbids `status:in-progress` before the cross-check comment exists, and the assignee is what holds the claim in the meantime (assignee = lock; on Gitea resolve your login with `forge.user.login` first — `tea` has no `@me`).
   - If planning surfaces a user-only decision, don't guess: comment the question, `forge.issue.status.set <n> status:needs-feedback` (removes whichever single `status:` label the issue is carrying — `status:in-progress` if the claim already swapped it, none if it has not), drop the claim, pick different work.
4. **Cross-check the batch's plans — a gate, before the batch's first launch.**

   **The deliverable of this step is a comment, and the step is not done until it exists.**
   Post it on the batch's tracking issue, first line `finding: batch cross-check, <batch ref>`,
   listing the pairs found **or** stating the clean negative and naming what you compared.
   Then launch. A check with no artifact did not happen, whatever you concluded — and a check
   that only speaks when it finds something is one you cannot audit and will quietly stop
   running.

   This is the failure mode measured on its first outing: a four-member batch of unrelated
   issues went from its last plan to its first launch in **seven seconds** with no comment
   anywhere, so whether the check ran at all is unknowable. Nothing was wrong with the batch.
   That is exactly when the step evaporates — there are no pairs to act on, so there is
   nothing that forces you to show your work. Post the negative.

   Order: plan **every** member of the batch (step 3 for each of them), then run this
   check, then launch any of them (step 5). **No member goes `status:in-progress` before that
   comment is on the tracking issue.** Skip the step only when the batch has exactly **one
   member**, or for standalone/hotfix work. A batch with more than one member gets the check
   before its **first** launch even when `concurrency` is 1 or the members are sequenced —
   serializing the launches does not remove a plan collision, it only delays discovering it,
   and the worker blocks on a missing `crossCheck` regardless of how many siblings happen to be
   running beside it. **A member added to the batch after the check** gets an addendum comparing
   it against the members still live, and none if they have all sub-merged — the `crossCheck`
   URL you hand it must be a comparison that included it.

   Getting the order wrong wastes the check. Measured in a live run (a five-member epic batch):
   plan→claim→launch ran per issue, so one member was already building when the check found
   that its issue text was wrong about which fields are mergeable. The finding was correct and
   arrived too late to shape the work — it became a correction to push instead of a plan to
   fix. Plan the set, check the set, then start the set.

   Spawn **one**
   read-only agent (Haiku is enough; Sonnet if the plans are dense) and give it just the
   plan comments — not the code, not the diffs. Ask it for exactly this: which plans claim
   the **same file, function, interface, migration, config key or route**, and where one
   plan **assumes** a shape another plan is changing. It reports pairs with evidence and
   proposes an owner; it decides nothing.

   Then act on each pair, on the main thread:
   - **Same file, disjoint intent** → leave it. Worktrees isolate the edits and the PM
     resolves the conflict once at sub-merge; this is the design, not a problem.
   - **One plan changes what another consumes** → sequence them in this batch (the
     consumer forks the updated integration branch after the producer sub-merges), or
     narrow the consumer's plan to the current shape and file the follow-up.
   - **Both plans intend to own the same new thing** (two versions of one helper, two
     migrations for one table) → fix it now: pick the owner, edit the other plan's comment,
     say so on both issues.
   - **Genuine product disagreement about the same logic** → `status:needs-feedback` on
     both, per the feedback policy. Do not launch either.

   Why it earns its cost: the same collision found here costs one plan edit; found at C1 it
   costs two built branches and a semantic conflict. One small agent per batch, reading a
   few short comments, is the cheapest gate in the loop. Everything the check turns up that a
   worker would want goes in that same tracking-issue comment (see
   [references/batching.md](batching.md)), not only in your own context — including
   anything the locate passes surfaced, such as work an already-merged sibling has done.

4b. **Plan review — only when `planReview: true`**
   ([session-config.md](session-config.md)). After the cross-check comment
   exists and before any member launches, surface the batch's plans to the operator: one
   line per member (issue number, plan-comment link, one-sentence approach) plus the
   cross-check verdict, then `AskUserQuestion` — **approve the batch** / **revise named
   plans** (collect what to change, edit the plan comments, post a cross-check addendum
   for any plan that changed) / **proceed without review this batch**. This is the
   cheapest re-steer point in the loop: a correction here costs one comment edit, the
   same correction at the batch gate costs built branches — a bad line of a plan becomes
   hundreds of bad lines of code. This gate is the one place the loop *does* wait on the
   user, because they switched it on to be asked; under the default `planReview: false`
   skip it entirely — the plans are still on the issues for anyone watching.

5. **Hand off to a worker.** For a batch starting more than one member, the brief's
   **`crossCheck` field is the URL of step 4's comment**, and it is required — you cannot fill
   it in before the comment exists, which is the point. **The worker validates it and returns
   `blocked` without doing any work if it is missing, empty, "pending" or unresolvable**, so a
   launch that jumps the check now fails loudly and cheaply instead of quietly building against
   an unchecked plan. Do not launch with it blank, and do not write "pending": the ordering mistake it prevents is the one that cannot be repaired
   afterwards, because by the time the check reports, the plans it would have corrected are
   already being built. Measured failing in a live run even with an explicit
   "check the comment exists first" instruction here, which is why it is now a field rather
   than a reminder. Launch with `Agent`,
   `subagent_type: "issue-flow:issue-worker"`, **`isolation: "worktree"`** (the harness creates and pins the worker's worktree; a worker that makes its own with `EnterWorktree` drags the PM and every sibling into it), and **record the `agentId` the spawn result returns** — `SendMessage` to that id is how you reach the worker for rework, corrections and pushed findings. Pass `name: "worker-<issue>"` as well only when the `Agent` tool offers a `name` parameter: it exists only with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (measured on 2.1.285), so address by `agentId` either way. Pass only the handoff brief (issue number, branch, **base = the integration branch**, `ci: skip`, batch ref, **`members` = the batch's member count** — it is how the worker knows whether `crossCheck` may legally be `n/a` — remote, the plan you commented, conventions, the session's **`practices` block** — TDD/DDD/E2E/coverage/commit style/docs, which are part of the worker's definition of done — and **`steRule`**, the path to the writing standard the worker's comments, docstrings, test names and PR body must follow: `.claude/rules/ste.md` when the project has one, else this plugin's `references/ste.md`) — its runbook is self-contained. The brief format is in [references/issue-worker.md](issue-worker.md).

   **Never dispatch a build with `subagent_type: "fork"`.** A fork inherits this entire
   session verbatim — the full PM/autonomous-loop instructions, not just the one issue's
   brief — and nothing in that inherited text tells it to stop after one issue. Measured
   in a live run: a fork dispatched for a single issue kept running the PM loop on its
   own for 52 minutes, claiming and building two more issues from a different epic,
   running its own batch review, and merging everything straight to `dev` — all without
   the actual PM claiming, planning, cross-checking, or gating any of it. `issue-worker`
   is a fresh agent with no PM context; it only ever does what its brief says. The
   plugin's spawn guard (`hooks/guard-spawn.py`) denies a fork whose prompt is laid out
   as the handoff brief; this paragraph is why, the hook is what holds it.

   **As each member actually launches, complete its held claim**: `forge.issue.status.set <n> status:in-progress` (removes `status:ready`), and comment `worker launched: <agentId>` on the issue — the id is session-local, for this session's rework and for humans reading along; a later session never messages it (Phase 0 step 12). This is the swap step 3 deferred, and nothing else performs it. An issue left on `status:ready` while its worker runs re-enters the ready pool at the next triage and can be scheduled twice; the assignee alone does not stop *you*, because the claim CAS only abandons issues assigned to someone **else**.

   Sequenced members launch **after** their predecessor sub-merges (their branch then forks the updated integration branch). Return to orchestrating. (If the agent type can't be resolved, fall back to `general-purpose` and prepend the worker brief with: "You are a decision-free issue-worker; never merge; return the verdict JSON.")

   **The brief carries `pluginRoot`**, the plugin's absolute directory: two levels above
   this skill's base directory, which the harness shows when the skill loads. A worker
   is not told where its runbook lives, so its `../references/…` links and the Gitea
   CI-watch script (`scripts/gitea-ci-watch.sh`) resolve only through it (measured on
   Claude Code 2.1.285). Pass `steRule` as an absolute path for the same reason.
