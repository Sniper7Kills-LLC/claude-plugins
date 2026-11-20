# Event markers on PM comments

The timeline ([timeline.md](../../../references/timeline.md)) reads each PM comment by its role.
A hidden marker on the first line gives the role without guessing. The visible text stays the
same, so a human reads no change. The grammar lives in `timeline.md`; this page only shows how
the PM writes it. `scripts/test-event-markers.py` checks every example here against that grammar.
Each marker is one line: the `role`, the `issue`, and the optional `pr` and `problem`.

| Comment | Role | Marker | Visible text starts with |
|---|---|---|---|
| Plan | `plan` | `<!-- issue-flow:event role=plan issue=<n> -->` | `## Plan (PM)` |
| Batch cross-check | `cross-check` | `<!-- issue-flow:event role=cross-check issue=<n> -->` | `finding: batch cross-check` |
| Plan amendment | `plan-amendment` | `<!-- issue-flow:event role=plan-amendment issue=<n> -->` | `**Amendment (batch cross-check` |
| Gate pass | `gate.pass` | `<!-- issue-flow:event role=gate.pass issue=<n> pr=<n> -->` | `PM gate: pass` |
| Rework request | `gate.rework` | `<!-- issue-flow:event role=gate.rework issue=<n> pr=<n> problem=<k> -->` | `PM gate: rework requested` |
| Sub-merge | `sub-merge` | `<!-- issue-flow:event role=sub-merge issue=<n> pr=<n> -->` | `Sub-merged into` |
| Checkpoint | `checkpoint` | `<!-- issue-flow:event role=checkpoint issue=<n> -->` | `checkpoint <k>:` |
| Worker launch | `worker.launched` | `<!-- issue-flow:event role=worker.launched issue=<n> -->` | `worker launched:` |
| Status repair | `status.repair` | `<!-- issue-flow:event role=status.repair issue=<n> -->` | `status repair:` |

`issue` is the issue the comment is about. For a cross-check on a tracking issue, it is the
tracking issue. Keep the prose prefix after the marker: a reader that does not know the marker
still classifies the comment by the prefix.

## A rework request

One comment, one marker, one problem. The numbered lines are for the human reader: the timeline does not split them. `problem=<k>` names the round
(`r1`, `r2`, …), so the problem id is `<issue>:<k>`. A worker cites the comment URL in the
`Fixes:` trailer of each commit that answers it ([issue-worker.md](issue-worker.md)).

```
<!-- issue-flow:event role=gate.rework issue=75 pr=9 problem=r1 -->
PM gate: rework requested
1. Criterion 2 has no evidence. Quote a test name or a command output.
2. The pre-patch check is missing from `localChecks`.
```
