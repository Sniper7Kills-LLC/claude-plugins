# Stage E — Loop, report, stop

Each handled verdict frees a slot → return to Stage A/B and refill — **except
`checkpoint`, which does not free a slot**: the issue is still in flight and its
replacement worker occupies the same one. **Drop finished
issues and batches from working memory** (fully recorded on the tracker); keep only the running
session summary so context stays flat as the count grows. If context is compacted
mid-loop, re-run Phase 0 recovery and continue.

## Status reporting (three channels, low noise)

On every **milestone** — a batch merges, a deploy is verified or fails, the pipeline
starves, or user input becomes gating — and otherwise at most every ~30 minutes of
activity:

1. **Terminal digest** (≤10 lines): shipped since last digest, in flight (batch → members
   → stage), blocked/parked (one line each, why), open questions awaiting the user,
   anything awaiting a human review, the **convergence line** — `filed`/`closed` for
   the last three gates — and **pending repairs**: findings that failed the filing gate with
   no batch open to take them (one line each, cleared when a batch takes them). Both of
   those survive only because the digest carries them into the marker block; leave either
   out of a rewrite and it is gone, and by design nothing else holds it.
   The **first digest of the session** also states the
   active run configuration in one line (concurrency, run length, PR granularity,
   authority, practices).
2. **Status issue update:** rewrite **your marker block** in the body of your
   `issue-flow: session status — @<me>` issue with the same digest + timestamp,
   preserving everything outside the markers (re-read the body first — a human may have
   added notes). Don't spam comments; add one only for milestones worth a notification
   trail. Never touch another operator's status issue.
3. **Push notification** for milestones only (batch merged, deploy verified/failed,
   input needed, loop stopped): load `PushNotification` via
   `ToolSearch` (`select:PushNotification`) and send a one-liner. If the tool isn't
   available, skip silently — the other two channels carry it.

Between milestones, stay quiet. Never let reporting block scheduling.

## Project review

If the session's `review.when` trigger fires (after each batch, after every N batches, or
end of session), offer to run `/project-review` — it is a long, browser-driving pass, so
ask before launching unless the user already chose it for this session. Issues it files
land back in Stage A triage.

## Convergence check (at every merge gate)

A loop that files more than it closes never reaches "backlog empty" — it runs until the
budget does. So measure whether the loop is converging, and treat "not converging" as a
stop condition.

**What to count.** Two numbers per gate, both of which you already know without a tracker
call:

- **`filed`** — issues filed **from findings** since the last gate. Count what came through
  the filing gate ([stage-a-sweep-triage.md](stage-a-sweep-triage.md)), whether you filed it or a `/project-review` run in this session
  did: `review:finding` issues are exactly the growth this check exists to catch, and a
  session with `review.when` set will produce more of them than the PM does.
  **Epic decomposition and `type:batch` tracking issues
  are excluded** — those are planned work being made schedulable, not backlog growth, and
  counting them stops a healthy session for doing exactly what Stage A and Stage B require.
  **Increment the marker line when you file, not at the gate** — a compaction mid-batch
  otherwise loses the tally and the next gate under-reports.
- **`closed`** — issues closed by the merge at this gate. You know this number without a
  call: `Closes #` auto-closes when dev is the default branch, and you close members by
  hand at Stage C2 step 6 when it is not.

Do not use the raw open-issue count. It moves for reasons that have nothing to do with the
loop feeding itself.

**Where to keep it.** In your `flow:status` marker block, as one line the digest also
carries:

```markdown
<!-- issue-flow:convergence --> gates: 12→3f/5c, 13→4f/4c, 14→6f/2c, 15→2f/–
```

Working memory is not enough: the loop is designed to compact, this check exists for the
long sessions that compact most, and a window held only in context resets exactly when it
matters. The marker block is durable and Phase 0 recovery already re-reads it, so the
window survives a compaction. Keep the last three closed gates plus the one in progress —
`15→2f/–` above is the open gate, its `filed` rising as you file and its `closed` unknown
until the merge. Drop older entries.

**When to run it.** At every merge gate — the batch gate under the default granularity, and
each issue merge when `prGranularity` is `per-issue` (Stage C2 never runs there, so anchor
to the merge, not to the stage).

**If `filed >= closed` *and* `filed >= 3` at three consecutive gates, stop and report
it.** State the three pairs, the net, and name the issues that drove `filed`. This is a
stop, not a pause: ask whether to continue, tighten the filing gate, or end the session.
Never continue silently on the reasoning that the next gate will clear it.
The `filed >= 3` floor exists because a tie is not divergence: under `per-issue`
granularity a healthy gate closes exactly one issue, so a worker filing one genuine
behavior bug per issue reads `1f/1c` forever — net-zero backlog, every filing legitimate —
and a bare `filed >= closed` would stop that session for being healthy.

**With fewer than three closed gates the check is N/A, not "converging".** A short or
bursty run — a tracker created and drained inside a few days — may never evaluate it at
all; say "convergence: N/A (<n> gates)" in the digest rather than letting silence read as
a pass, and fall back to the close rate (closed/total) as the burst-run health signal.

## Stop

**Stop** when: the session's `runLength` limit is reached (N batches, N issues, backlog
empty, or the user says stop), the convergence check trips, no workable issues remain, everything left is
`status:blocked`/`status:needs-feedback`/`status:awaiting-review`, or the token budget
(if set) is spent. Reaching a limit **never abandons work in flight** — let running
workers finish and gate their results, then stop. On stop: `git worktree list --porcelain`
→ remove any tree on a merged or abandoned `issue/*` branch → `git worktree prune` → the
dead harness-branch sweep. A parked standalone tree kept at park time (it held unpushed or
uncommitted work) is neither: leave it, and list it in the final digest. Then
post a final digest on all three channels — shipped, deployed, blocked (why),
awaiting-feedback, awaiting human review, and anything skipped due to caps (never
truncate silently).
