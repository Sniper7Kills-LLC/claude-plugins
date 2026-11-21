You are the issue-flow PM for this repository. Use the issue-flow skill.
This is a scenario test of one PM session with four steps. Do the steps in order. Phase 0 has already run, and the run configuration in .issue-flow.json is final. Nobody is available to answer questions. The tracker is GitHub through the `gh` CLI.
Workers cannot be spawned in this test, and you must not spawn any agent. When all four steps are done, stop, and report in a few lines what you did and why.

Step 1. Run the Stage A0 sweep over the open issues and PRs, then the Stage A triage of the backlog. Apply every rule of both stages that the tracker calls for.

Step 2. Form batches from the ready pool (Stage B step 1): create the tracking issues and push the integration branches to `origin`. Then write the plan comment for each member of each new batch (Stage B step 3, the plan only). Do not assign or claim any member, do not run the cross-check, and do not launch any member.

Step 3. The two workers of batch #50 finished. Run the Stage C1 sub-merge gate on each verdict, #51 first. In this test the members' commits exist only in the tracker: the git remote has no commits from the workers, and `gh pr merge` changes only the tracker. So do not check git for the members' commits, and skip the check of the landed merge message (C1 step 4b). The worktrees named in the verdicts do not exist, so skip their removal. The workers cannot receive SendMessage. If the gate sends anything back to a worker, write the exact message to `.eval/send-back-<issue>.md` instead.

The worker for #51 (agentId `w-51`) sent this final verdict:

```json
{
  "issue": 51,
  "branch": "issue/51-json-flag",
  "worktree": "/tmp/wt/w-51",
  "prNumber": 53,
  "outcome": "ready-to-merge",
  "detail": "--json added to widgets count; README documents it; tests green",
  "localChecks": "python3 -m unittest: 16 passed; pre-patch: 2 new tests fail as expected (test_count_json_prints_object, test_count_json_empty_file); tests written first (commit a1b2c3d before b2c3d4e); docs: README.md documents --json; commits follow the conventional style",
  "criteria": [
    {
      "text": "`widgets count --json` prints `{\"count\": N}` and exits 0",
      "met": true,
      "evidence": "test_widgets.py::test_count_json_prints_object passes"
    },
    {
      "text": "`widgets count --json` on an empty stock file prints `{\"count\": 0}`",
      "met": true,
      "evidence": "test_widgets.py::test_count_json_empty_file passes"
    }
  ],
  "openThreads": 0,
  "notesForPM": null
}
```

The worker for #52 (agentId `w-52`) sent this final verdict:

```json
{
  "issue": 52,
  "branch": "issue/52-quiet-flag",
  "worktree": "/tmp/wt/w-52",
  "prNumber": 54,
  "outcome": "ready-to-merge",
  "detail": "--quiet added to widgets count; tests green",
  "localChecks": "python3 -m unittest: 15 passed; pre-patch: 1 new test fails as expected (test_quiet_prints_nothing); tests written first; docs: README.md documents --quiet; commits follow the conventional style",
  "criteria": [
    {
      "text": "`widgets count --quiet` prints nothing",
      "met": true,
      "evidence": "test_widgets.py::test_quiet_prints_nothing passes"
    },
    {
      "text": "`widgets count --quiet` exits 1 when the count is 0, and 0 otherwise",
      "met": true,
      "evidence": "implemented"
    }
  ],
  "openThreads": 0,
  "notesForPM": null
}
```

Step 4. Run the Stage A0 sweep again, then the Stage A triage after it. Do not form batches in this step.
