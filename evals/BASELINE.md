# Eval baseline: issue-flow 0.20.0

This file records the numbers of issue-flow 0.20.0 on both eval suites. A later change is
compared with these numbers by `scripts/eval-compare.py` (Sniper7Kills-LLC/claude-plugins#106).
The PM run records are in `evals/baselines/0.20.0/pm/`. The worker suite was measured again after
Sniper7Kills-LLC/claude-plugins#124: its records are in `evals/baselines/0.20.0/worker/` and
`worker-large/` (Opus worker), and in `evals/baselines/0.21.0/worker/` and `worker-large/`
(Sonnet worker). See "Worker suite".
`evals/README.md` says how to compare a candidate.

## The setup

| Item | Value |
|---|---|
| Plugin | issue-flow 0.20.0, a git worktree of the tag `issue-flow--v0.20.0` (commit `c3974fe`) |
| Date | 2026-11-21 (UTC): PM suite 18:34–18:47. The worker suite was measured again on 2026-11-22 (see "Worker suite"). |
| Claude Code | 2.1.330 |
| Runner | `scripts/run-evals.py`, one run at a time |
| Runs | 3 per case |
| PM model | `claude-opus-5-5`, the default model |
| Worker dispatcher model | `claude-haiku-5-5` (`"model": "haiku"` in each worker case) |
| Worker model | `claude-opus-5-5` (`model: opus` in `agents/issue-worker.md` at 0.20.0) |
| Judge model | `claude-haiku-5-5`, for the `llm` graders of the PM suite |
| Effort | `medium`. The runner did not pass `--effort` yet. See "The runs inherited a Claude Code session". |

The PM suite has 6 cases (18 runs). The worker suite has 11 cases (33 runs per variant):
8 cases carry the tag `small` and 3 carry the tag `large`. Its numbers are in "Worker suite".

## Actual spend

| Suite | Kept runs | Superseded runs | Spent |
|---|---|---|---|
| PM | $8.66, judges $0.016 | $1.33, in `superseded-pm-triage.json` | $10.00 |
| Worker | replaced: see "Worker suite" | n/a | n/a |

The costs are `cost_usd`, the `total_cost_usd` of each `claude -p` result. This value
includes the subagents, and it governs the decision rules. The per-agent estimate of
`usage.py` agrees with it on every worker run: the largest gap is 8.0%, under the limit of
10%.

## PM suite

| Case | ok rate | Mean score (range) | Mean cost (range) | Main tokens | Subagent tokens | Turns | Duration (range) |
|---|---|---|---|---|---|---|---|
| `pm-a0-split-status` | 3/3 | 1.00 (1.00–1.00) | $0.42 ($0.42–$0.42) | 186k | 0 | 5.0 | 19.6 s (18–23) |
| `pm-batch-cross-check` | 3/3 | 1.00 (1.00–1.00) | $0.63 ($0.61–$0.64) | 484k | 5.1k | 9.0 | 59.6 s (49–68) |
| `pm-c1-no-prepatch` | 3/3 | 1.00 (1.00–1.00) | $0.47 ($0.45–$0.48) | 230k | 0 | 5.7 | 34.3 s (28–43) |
| `pm-c1-unevidenced` | 3/3 | 1.00 (1.00–1.00) | $0.45 ($0.44–$0.46) | 192k | 0 | 5.0 | 24.2 s (22–26) |
| `pm-semantic-conflict` | 3/3 | 1.00 (1.00–1.00) | $0.49 ($0.47–$0.51) | 229k | 4.3k | 5.3 | 32.5 s (28–39) |
| `pm-triage` | 3/3 | 1.00 (1.00–1.00) | $0.44 ($0.44–$0.44) | 245k | 0 | 6.0 | 20.3 s (18–22) |

For the suite: 18/18 runs ok, mean score 1.00, mean cost $0.48 per run, and 261k PM
tokens per run.

- A token count is the sum of input, output, cache-read and cache-write tokens.
- "Main tokens" is the record's `usage`: the PM loop only. The PM rule compares this
  number.
- "Subagent tokens" comes from `issue-flow/scripts/usage.py`, by `session_id` (`agents`
  and `tokens` in each record). Two cases spawned a Haiku cross-check subagent in some
  runs.
- `scripts/eval-baseline.py` added `agents`, `tokens`, the per-record `effort` and
  `suite_means` to the PM baseline on 2026-11-21, after the runs. All 18 transcripts were
  still on the machine of the runs. The per-agent cost agrees with `cost_usd` on every PM
  run. The mean cost in `summary.json` is `cost_usd` only; the judges are not in it.
- The PM records were made before `wall_s` existed, so the duration is `duration_s`. A PM
  case has no background agent, so `duration_s` is its full time.
- The first 3 runs of `pm-triage` failed because of a grader error. `evals/README.md`
  ("0.20.0, PM suite") gives the cause.

## Worker suite

The worker suite has 11 cases: 8 on the small fixture and 3 on the large fixture. It was
measured again on 2026-11-22 (UTC), after the brief fix of Sniper7Kills-LLC/claude-plugins#124.
The old runs, made with the contradictory brief, are no longer the baseline (see "The brief
fix in this baseline").

| Item | 0.20.0 | 0.21.0 |
|---|---|---|
| Plugin | a worktree of the tag `issue-flow--v0.20.0` (`c3974fe`) | `issue-flow/` of batch #125 (`7fa8a75`) |
| Worker model | `claude-opus-5-5` | `claude-sonnet-5-5` |
| Dispatcher model | `claude-haiku-5-5` | `claude-haiku-5-5` |
| Runs | 3 per case, 33 in total | 3 per case, 33 in total |
| Spent | $7.92 small + $3.57 large | $2.27 small + $1.35 large |
| Records | `evals/baselines/0.20.0/worker/`, `worker-large/` | `evals/baselines/0.21.0/worker/`, `worker-large/` |

Every ceiling was `--max-cost-usd 10` per invocation. The effort is `medium`. Every run on
both sides passed: 66/66 ok, mean score 1.00, hidden-test pass rate 100% on the cases that
have hidden tests. Each cell below reads "0.20.0 / 0.21.0".

| Case | Tier | Hidden tests | Mean cost | Worker tokens | Worker turns | Wall time |
|---|---|---|---|---|---|---|
| `worker-easy-low-stock-boundary` | easy | 3/3 / 3/3 | $0.26 / $0.10 | 375k / 296k | 12.0 / 9.7 | 75 s / 29 s |
| `worker-easy-negative-qty` | easy | 3/3 / 3/3 | $0.26 / $0.09 | 398k / 304k | 12.0 / 10.0 | 78 s / 32 s |
| `worker-medium-csv-format` | medium | 3/3 / 3/3 | $0.44 / $0.10 | 661k / 276k | 17.3 / 8.7 | 108 s / 35 s |
| `worker-medium-money-refactor` | medium | 3/3 / 3/3 | $0.35 / $0.08 | 533k / 217k | 15.0 / 7.3 | 90 s / 28 s |
| `worker-medium-price-parse` | medium | 3/3 / 3/3 | $0.28 / $0.11 | 412k / 363k | 12.7 / 11.7 | 80 s / 36 s |
| `worker-hard-location` | hard | 3/3 / 3/3 | $0.48 / $0.13 | 612k / 327k | 15.3 / 10.0 | 120 s / 44 s |
| `worker-hard-error-lines` | hard | 3/3 / 3/3 | $0.44 / $0.10 | 612k / 270k | 16.0 / 8.7 | 111 s / 39 s |
| `worker-hard-ambiguous-tax` | hard | n/a | $0.13 / $0.05 | 186k / 141k | 6.7 / 5.0 | 40 s / 16 s |
| `worker-large-medium-report-midnight` | medium | 3/3 / 3/3 | $0.38 / $0.10 | 641k / 328k | 17.7 / 10.3 | 110 s / 34 s |
| `worker-large-locate-repeated-sku` | medium, `locate` | 3/3 / 3/3 | $0.37 / $0.16 | 576k / 573k | 15.3 / 15.7 | 106 s / 48 s |
| `worker-large-hard-one-discount` | hard | 3/3 / 3/3 | $0.44 / $0.18 | 690k / 586k | 17.0 / 14.7 | 109 s / 53 s |

| Tier | Runs per variant | Hidden tests | Mean cost per run | Mean wall time |
|---|---|---|---|---|
| easy | 6 | 6/6 / 6/6 | $0.26 / $0.10 | 76 s / 30 s |
| medium | 15 | 15/15 / 15/15 | $0.36 / $0.11 | 99 s / 36 s |
| hard | 12 | 9/9 / 9/9 (the ambiguous case has none) | $0.37 / $0.12 | 95 s / 38 s |

| Fixture | Variant | Mean cost | Worker tokens | Worker turns | Wall time |
|---|---|---|---|---|---|
| small, 24 runs | 0.20.0 | $0.33 | 474k | 13.4 | 88 s |
| | 0.21.0 | $0.09 | 274k | 8.9 | 32 s |
| large, 9 runs | 0.20.0 | $0.40 | 636k | 16.7 | 108 s |
| | 0.21.0 | $0.15 | 496k | 13.6 | 45 s |

- The worker tokens and turns come from `issue-flow/scripts/usage.py`, by `session_id`
  (`agents` and `tokens` in each record). `summary.json` calls them `subagent` values.
- The duration is `wall_s`. `duration_s` and `turns` count only the dispatcher, because the
  worker runs in the background.
- A worker case does not load the PM skill, so the worker model and the worker agent file
  are the only differences between the two variants.
- A `ci: skip` worker now makes about 4.4 tracker calls per run on 0.21.0 against 8.8 on
  0.20.0.

### The brief fix in this baseline

Until Sniper7Kills-LLC/claude-plugins#124, each worker case sent `ci: skip` with
`batch: standalone`. The worker agent file defines `ci: skip` as a batch member that opens a
draft PR. The brief thus contradicted the contract of the worker, and a worker had to guess.
Each case now sends `batch: batch #<n>`, `members: 1` and `crossCheck: n/a — single-member
batch`, and its `tracker.json` holds the `type:batch` issue `<n>`. `scripts/test-worker-cases.py`
checks this (`test_briefs_are_consistent`).

The records of the first 0.20.0 baseline, made with the old brief, are replaced. The files
in `evals/candidates/*` are records of the old brief too. They stay as they are, and they
are not comparable with the numbers above. `scripts/eval-compare.py` runs on the new
baselines only.

## PM session case

`pm-session-batch` is a session-level PM case (Sniper7Kills-LLC/claude-plugins#116,
`evals/README.md`, "Session-level PM cases"). The run records are in
`evals/baselines/0.20.0/pm-session/`. The same case was run on the head of batch #115 for comparison.
That head had the split PM skill (#107). #107 is closed, so its run records are not kept.
The numbers are in the table below and on #107. These are new baselines, not a
ship decision.

| Item | 0.20.0 | Batch #115 head |
|---|---|---|
| Plugin | a git worktree of the tag `issue-flow--v0.20.0` (`c3974fe`) | a git worktree of `batch/115-token-speed` at `2ef9bd7` |
| Case, runner and fake `gh` | this repository at `0e4c65a` (case tree `c62b068`) | the same |
| Date | 2026-11-21 (UTC) | 2026-11-21 (UTC) |
| Runs | 3 | 3 |
| Spent, judges included | $3.48, under the ceiling of $25 | $3.94, under the ceiling of $25 |

The model is `claude-opus-5-5` and the effort is `medium` on both sides. The `llm` judge
takes the majority of votes (Sniper7Kills-LLC/claude-plugins#117) on both sides.

| Variant | ok | Mean score (range) | PM tokens | Tracker calls (range) | Turns | Mean cost (range) | Duration (range) |
|---|---|---|---|---|---|---|---|
| 0.20.0 | 3/3 | 0.92 (0.75–1.00) | 1,443k | 102.3 (96–114) | 21.3 | $1.16 ($1.07–$1.30) | 160 s (144–185) |
| batch head | 3/3 | 1.00 (1.00–1.00) | 1,692k | 77.3 (69–83) | 26.7 | $1.31 ($1.28–$1.33) | 204 s (194–224) |

- "PM tokens" is the sum of the four token counts of the main agent. No run spawned a
  subagent.
- "Tracker calls" is `tracker_calls.total`: every line of the run's gh log. It includes
  the 3 reads of the SessionStart preflight hook and 12 refused `gh pr view <branch>`
  calls from Claude Code itself in each run. Without them, the PM made 87.3 calls per
  run on 0.20.0 and 62.3 on the batch head.
- On this case, the batch head made 24% fewer tracker calls, but used 17% more PM tokens,
  5.3 more turns and 13% more cost than 0.20.0. The split PM skill
  (Sniper7Kills-LLC/claude-plugins#107) is the likely cause, because the session reads
  several stage files. This measurement does not test that. On this result the operator
  reverted #107 before batch #115 merged; the batch ships one `SKILL.md`, as in 0.20.0.
- The one score below 1.00 is 0.20.0 run 2: the judge found the questions on #63 too
  generic. The deterministic graders passed.
- The first version of the case gave #51 weak evidence for one criterion. The 0.20.0 PM
  correctly refused to merge #51 in 2 of 3 runs. The case was fixed, and all the runs above
  were made after the fix. The runs from before the fix are not kept.

## What the baseline can and cannot show

- **Sample size.** Each case has 3 runs. At this size, a drop under about 15 points cannot
  be detected. A `ship` verdict is not proof that no small regression exists.
- **No headroom on scores.** Every case of both suites passes 3/3 on 0.20.0. A candidate
  can only equal or lose to the baseline on scores, so the decision rules depend on the
  token and cost checks.
- **The PM token margin.** The PM rule needs mean PM tokens at least 10% lower, on 3-run
  means: at most 235k per run against the 261k of this baseline. The operator set the
  margin on 2026-11-21, because one run can differ from its case mean by more than 20%
  (`pm-triage`: 186,639 tokens in one dogfood run, 244,830 in the 3-run mean).
  `scripts/eval-compare.py` refuses a candidate with fewer cases or fewer runs per case
  than this baseline (exit 5).
- **The runs inherited a Claude Code session.** The worker suite started from a Claude
  Code session, and the runner passes the environment of that session to each run,
  `CLAUDE_EFFORT=medium` included. The effect on the results is not measured.
  The PM records do not say how their run started. Since
  Sniper7Kills-LLC/claude-plugins#113, the runner removes these variables from each run
  (`evals/README.md`, "The environment of a run"). A candidate run is then not affected
  by where it starts.
- **Effort.** `results.json` and `summary.json` of both suites now record
  `"effort": "medium"`. The records were made before the runner passed `--effort`, so this
  value is added afterwards. Each PM record also has `effort`, copied from the top level
  by `scripts/eval-baseline.py`; the worker records have it at the top level only. The evidence for `medium`: the host session had
  `CLAUDE_EFFORT=medium`, and `claude-opus-5-5` and `claude-haiku-5-5` default to `medium`
  (https://code.claude.com/docs/en/model-config). `--setting-sources project,local` kept
  the host's user settings out, and #113 found no `CLAUDE_CODE_EFFORT_LEVEL` in the host
  session.
  `scripts/eval-compare.py` refuses a candidate with another effort (exit 5). The default
  `--effort medium` of the runner matches the baseline.
- **Dispatcher wait.** In the first 0.20.0 worker baseline, one run of
  `worker-hard-ambiguous-tax` took 908 s, because a dispatcher ran `sleep`. The worker case
  prompts now tell the dispatcher not to run `sleep` and not to poll. The records in this
  file were made with that prompt, and they have no such outlier.
- **Costs are estimates.** `total_cost_usd` is a client-side estimate at list prices, not
  billing data.
