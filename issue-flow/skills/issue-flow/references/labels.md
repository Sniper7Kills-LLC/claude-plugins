# Standard label taxonomy

Status labels form the state machine. An issue carries **at most one** `status:` label at a time, with **no exceptions** — including a failed deploy that also needs human input, where `status:deploy-failed` stays the single label and the request for input is a comment (Stage D in [../SKILL.md](../SKILL.md), [deploy.md](deploy.md)). Every transition **to** a status goes through `forge.issue.status.set`, which removes the old label and adds the new one as one operation; a bare `forge.issue.label.add` leaves the issue in two states at once and poisons every query that selects by status. Clearing **to no status at all** is the one case `status.set` cannot express — it needs a target label and there is none — so a bare `forge.issue.label.remove` is correct there, and only there: a verified deploy dropping `status:deploying`, a landed hotfix dropping `status:deploy-failed`, and the clearing of a lingering `status:` label when an issue closes (a member at batch merge, Stage C2 step 6, or a standalone/hotfix issue at its merge, Stage C1). Create missing labels with `forge.label.create`.

## Status (required set)

| Label | Color | Meaning |
|---|---|---|
| `status:ready` | `0E8A16` | Triaged, requirements clear, available to pick up |
| `status:in-progress` | `1D76DB` | A worker is actively building this issue |
| `status:in-review` | `5319E7` | Sub-PR open; self-review / threads in flight |
| `status:batched` | `BFD4F2` | Sub-merged into the batch's integration branch; awaiting the batch PR |
| `status:deploying` | `0052CC` | Batch merged; deployment in flight, being monitored (Stage D) |
| `status:deploy-failed` | `B60205` | Deployment failed or failed browser verification. Set by Stage D on the tracking issue in place of `status:deploying`, before routing to a hotfix issue or to human input. Cleared when the fix deploys and verifies. |
| `status:awaiting-review` | `FEF2C0` | PR open and green, waiting on a human approving review before the PM may merge (see `prAuthority`) |
| `status:blocked` | `D93F0B` | Cannot proceed; blocking condition (or open dependency) named in a comment |
| `status:needs-feedback` | `FBCA04` | Awaiting a user decision; questions posted in a comment |

## Type (optional, classifies the work)

| Label | Color | Meaning |
|---|---|---|
| `type:epic` | `5319E7` | A large item decomposed into sub-issues; not implemented directly. Blocked on its children; its sub-issues form an epic batch. |
| `type:batch` | `C5DEF5` | Tracking issue for a loose-issue batch: member checklist, integration branch pointer. Never worked directly. |
| `type:hotfix` | `B60205` | Fixes a failed deployment of an already-merged change. Bypasses batching — standalone CI-running PR to dev. |
| `review:finding` | `F9D0C4` | Filed by a `project-review` run (user-viewpoint QA pass); triaged and worked like any other issue. |
| `type:spec-update` | `D4C5F9` | The spec no longer describes the shipped product; a worker edits `docs/specs/` only. Filed by the PM at a batch gate — see [spec-maintenance.md](spec-maintenance.md). |

## Flow (bookkeeping, never scheduled)

| Label | Color | Meaning |
|---|---|---|
| `flow:status` | `EDEDED` | A session status issue, one per operator (`issue-flow: session status — @<login>`); that PM keeps its own marker block in the body updated with the digest. Triage skips it, and a PM never edits another operator's. |

## Priority (optional, used for pick order)

| Label | Color | Meaning |
|---|---|---|
| `priority:high` | `B60205` | Pick before anything else; urgent singletons may skip batching |
| `priority:low` | `C2E0C6` | Pick last |

No priority label = normal priority.

## State transitions

```
(untriaged) ──triage──> status:ready ──claim──> status:in-progress ──sub-PR opened──> status:in-review
                 │                        │                              │
                 │                        │                              ├──sub-merged──> status:batched ──batch PR merged──> closed
                 │                        │                              │                                   (status:deploying on the
                 │                        │                              │                                    tracking issue during Stage D)
                 │                        │                              │
                 │                        │                              └──prAuthority requires a human──> status:awaiting-review
                 │                        │                                     │                                  │
                 │                        │                                     │      approving review ───────────┘
                 │                        │                                     │      → back to the merge it was waiting on
                 │                        │                                     └──changes requested──> status:in-review
                 │                        │
                 │                        ├──question for user──> status:needs-feedback ──answered──> status:ready
                 │                        │
                 └────unclear────> status:needs-feedback
                                          │
                                          └──external blocker / open dependency──> status:blocked ──unblocked──> status:ready
```

`status:deploying` and `status:deploy-failed` sit on the **tracking issue** (epic or
`type:batch`) during Stage D, never on a member. `deploying` → `deploy-failed` on any
non-verified terminal deployment; `deploy-failed` is cleared when the hotfix deploys and
verifies. Phase 0 recovery re-adopts any issue left in `deploy-failed`.

`status:awaiting-review` sits on the **issue** when a sub-PR is held (`review-all` /
`propose-only`) and on the **tracking issue** when a batch PR is held (`batch-review` and
stricter). It is a waiting state, not a terminal one: a session may stop with issues in
it, and Phase 0 recovery re-adopts them.

Every transition gets a comment explaining it. Labels say *what* state; comments say *why*.

## Bootstrap command

**GitHub — `gh`:**

```bash
gh label create "status:ready"          --color 0E8A16 --description "Triaged and available to work" --force
gh label create "status:in-progress"    --color 1D76DB --description "Agent actively working" --force
gh label create "status:in-review"      --color 5319E7 --description "Sub-PR open, review in flight" --force
gh label create "status:batched"        --color BFD4F2 --description "Sub-merged to integration branch, awaiting batch PR" --force
gh label create "status:deploying"      --color 0052CC --description "Merged; deployment being monitored" --force
gh label create "status:deploy-failed"  --color B60205 --description "Deployment failed; see comment" --force
gh label create "status:awaiting-review" --color FEF2C0 --description "Awaiting a human approving review before merge" --force
gh label create "status:blocked"        --color D93F0B --description "Blocked; see comment for blocker" --force
gh label create "status:needs-feedback" --color FBCA04 --description "Awaiting user decision" --force
gh label create "type:epic"             --color 5319E7 --description "Decomposed into sub-issues; epic batch" --force
gh label create "type:batch"            --color C5DEF5 --description "Loose-issue batch tracking issue" --force
gh label create "type:hotfix"           --color B60205 --description "Deploy hotfix; bypasses batching" --force
gh label create "type:spec-update"      --color D4C5F9 --description "Spec diverged from shipped behaviour; edits docs/specs only" --force
gh label create "review:finding"        --color F9D0C4 --description "Found by a project-review run" --force
gh label create "flow:status"           --color EDEDED --description "Session status issue; not schedulable" --force
gh label create "priority:high"         --color B60205 --description "Pick first" --force
gh label create "priority:low"          --color C2E0C6 --description "Pick last" --force
```

(`--force` makes the command idempotent — it updates color/description if the label exists.)

**Gitea — `tea`:**

`tea labels create` has no `--force` equivalent. Run it twice on the same name and it
does not update or error — it **silently creates a second label with that name**, and
the state machine then has two IDs for one status. The block below guards against this:
it reads the existing labels first and creates only the ones missing.

```bash
existing="$(tea labels list --output tsv | tail -n +2 | cut -f3)"

printf '%s\n' "$existing" | grep -qxF "status:ready" ||
tea labels create --name "status:ready"          --color "#0E8A16" --description "Triaged and available to work"
printf '%s\n' "$existing" | grep -qxF "status:in-progress" ||
tea labels create --name "status:in-progress"    --color "#1D76DB" --description "Agent actively working"
printf '%s\n' "$existing" | grep -qxF "status:in-review" ||
tea labels create --name "status:in-review"      --color "#5319E7" --description "Sub-PR open, review in flight"
printf '%s\n' "$existing" | grep -qxF "status:batched" ||
tea labels create --name "status:batched"        --color "#BFD4F2" --description "Sub-merged to integration branch, awaiting batch PR"
printf '%s\n' "$existing" | grep -qxF "status:deploying" ||
tea labels create --name "status:deploying"      --color "#0052CC" --description "Merged; deployment being monitored"
printf '%s\n' "$existing" | grep -qxF "status:deploy-failed" ||
tea labels create --name "status:deploy-failed"  --color "#B60205" --description "Deployment failed; see comment"
printf '%s\n' "$existing" | grep -qxF "status:awaiting-review" ||
tea labels create --name "status:awaiting-review" --color "#FEF2C0" --description "Awaiting a human approving review before merge"
printf '%s\n' "$existing" | grep -qxF "status:blocked" ||
tea labels create --name "status:blocked"        --color "#D93F0B" --description "Blocked; see comment for blocker"
printf '%s\n' "$existing" | grep -qxF "status:needs-feedback" ||
tea labels create --name "status:needs-feedback" --color "#FBCA04" --description "Awaiting user decision"
printf '%s\n' "$existing" | grep -qxF "type:epic" ||
tea labels create --name "type:epic"             --color "#5319E7" --description "Decomposed into sub-issues; epic batch"
printf '%s\n' "$existing" | grep -qxF "type:batch" ||
tea labels create --name "type:batch"            --color "#C5DEF5" --description "Loose-issue batch tracking issue"
printf '%s\n' "$existing" | grep -qxF "type:hotfix" ||
tea labels create --name "type:hotfix"           --color "#B60205" --description "Deploy hotfix; bypasses batching"
printf '%s\n' "$existing" | grep -qxF "type:spec-update" ||
tea labels create --name "type:spec-update"      --color "#D4C5F9" --description "Spec diverged from shipped behaviour; edits docs/specs only"
printf '%s\n' "$existing" | grep -qxF "review:finding" ||
tea labels create --name "review:finding"        --color "#F9D0C4" --description "Found by a project-review run"
printf '%s\n' "$existing" | grep -qxF "flow:status" ||
tea labels create --name "flow:status"           --color "#EDEDED" --description "Session status issue; not schedulable"
printf '%s\n' "$existing" | grep -qxF "priority:high" ||
tea labels create --name "priority:high"         --color "#B60205" --description "Pick first"
printf '%s\n' "$existing" | grep -qxF "priority:low" ||
tea labels create --name "priority:low"          --color "#C2E0C6" --description "Pick last"
```

**`--output tsv` needs no external interpreter.** The header row is
`Index<TAB>Color<TAB>Name<TAB>Description<TAB>Level`; `tail -n +2` drops it and `cut -f3`
takes the name column. `printf '%s\n' "$existing" | grep -qxF` avoids the `<<<` herestring,
which is a bash extension `sh` does not have. On a repository with no labels, `$existing`
is empty and every `grep` misses, so the block creates all seventeen labels — the same
outcome as before.
