# Stage D — Monitor deployment (post-merge)

Only if Phase 0 found a deploy target. The watch is **one background shell per merge**,
not an agent — the same pattern as every other long wait in this loop. When you merge a
PR into the deploy branch: label the tracking issue `status:deploying`, record **the SHA
that is head of the deploy branch after the merge** — normally the merge commit, but the
recovery commit instead whenever C2 step 5's head check made you push one on top (the
deployment carries the head SHA it built, not the merge commit you meant) — and launch
the watch loop from [references/deploy.md](deploy.md) with
`run_in_background: true`, anchored to that SHA. The shell exits with one verdict line;
between launch and exit, keep orchestrating — never poll it a turn at a time.

On the watch's verdict:

- **`succeeded`** → the build went green, but **green ≠ working**. Spawn the
  **deploy-verifier** (`Agent`, `subagent_type: "issue-flow:deploy-verifier"`) against the
  deployed URL captured in Phase 0. On its verdict:
  - **`verified`** → remove `status:deploying`, comment the confirmation with the
    screenshot ref on the batch PR / tracking issue. Digest + notify (Stage E). Done.
  - **`broken` / `unreachable`** → this is a failed deploy. Follow **Deploy failed**
    below, with cause `code-regression` unless the verifier's evidence points at
    config/secret/infra.
- **`failed`** / **`rolled-back`** → follow **Deploy failed** below. For a rollback,
  also capture what the platform did.
- **`timed-out`** → a deployment was observed but never went terminal inside the watch
  budget. Re-query once (run the status command directly). Still not terminal → surface
  to the user with the job id/link. Never record it as a success; leave
  `status:deploying` in place.
- **`no-deployment-observed`** → nothing ever built this SHA. Re-run C2 step 5's
  merged-head token check first — a suppressed head starts no deploy: push the clean
  recovery commit and watch the new SHA. A clean head with no deployment is a wiring
  problem (webhook, platform config) — surface it. Never a pass.
- **`watch-error`** → the status command itself failed (usually a permission refusal —
  the command must be in the committed allow-list). Fix the cause and relaunch the
  watch. Never a pass.

## Deploy failed

Run these steps in order.

1. **Label the failure.** On the tracking issue: `forge.issue.status.set <n>
   status:deploy-failed` (removes `status:deploying`). Comment the cause, the failing step, and the log excerpt.
   This label is the recovery signal — Phase 0 re-adopts a `status:deploy-failed` issue
   whose hotfix never landed.
2. **Read the deploy logs.** Delegate the read to a short-lived unnamed subagent; take
   back the failing step, a short excerpt, and a suspected cause
   (`code-regression | config | secret | quota | infra | unknown` —
   [references/deploy.md](deploy.md)), not the log.
3. **Route on the cause:**
   - **`code-regression`** → open a `priority:high` `type:hotfix` `status:ready`
     **hotfix issue** linking the failed deploy, the commit, and the cause. Hotfixes
     bypass batching — schedule a standalone worker immediately (`ci: run`, PR straight
     to dev).
   - **`config` / `secret` / `quota` / `infra`** (not a code fix) → this needs human
     input. **Park it as a comment, not a second label:** `status:deploy-failed` stays the
     issue's single `status:` label, and the request for human input is a comment that names
     the cause and what you need decided (`needs human input: <what>`). Surface it per the
     feedback policy — the surfacing is what reaches the user, not the label. Do not guess at
     infra or secret changes.

     Do **not** add `status:needs-feedback` or `status:blocked` here. An issue carries at most
     one `status:` label ([references/labels.md](labels.md)); adding a park beside
     `status:deploy-failed` builds exactly the split state Stage A0 step 3b then repairs, and
     3b keeps `deploy-failed` — so the park is destroyed and the outage loses its recovery
     signal either way it is ordered. Because the pair never legally exists, 3b needs no
     exception for it. The trade is deliberate and it has a cost: a deploy parked this way is
     **not** in the `status:needs-feedback` gather at Stage A step 4, so it reaches the user
     only through the feedback-policy surfacing above. Do that surfacing; nothing else will.
4. **Clear it when the fix deploys.** When the hotfix's own Stage D returns `verified`,
   remove `status:deploy-failed` from the tracking issue and comment the resolution.

A deploy is **done only when verified** (or parked for the user) — not when the build
merely succeeds.
