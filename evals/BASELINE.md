# Eval baseline: issue-flow 0.20.0

This file records the numbers of issue-flow 0.20.0 on both eval suites. A later change is
compared with these numbers by `scripts/eval-compare.py` (Sniper7Kills-LLC/claude-plugins#106).
The run records are in `evals/baselines/0.20.0/pm/` and `evals/baselines/0.20.0/worker/`.
`evals/README.md` says how to compare a candidate.

## The setup

| Item | Value |
|---|---|
| Plugin | issue-flow 0.20.0, a git worktree of the tag `issue-flow--v0.20.0` (commit `c3974fe`) |
| Date | 2026-11-21 (UTC): PM suite 18:34–18:47, worker suite 19:09–20:04 |
| Claude Code | 2.1.330 |
| Runner | `scripts/run-evals.py`, one run at a time |
| Runs | 3 per case |
| PM model | `claude-opus-5-5`, the default model |
| Worker dispatcher model | `claude-haiku-5-5` (`"model": "haiku"` in each worker case) |
| Worker model | `claude-opus-5-5` (`model: opus` in `agents/issue-worker.md` at 0.20.0) |
| Judge model | `claude-haiku-5-5`, for the `llm` graders of the PM suite |
| Effort | `medium`. The runner did not pass `--effort` yet. See "The runs inherited a Claude Code session". |

The PM suite has 6 cases (18 runs) and the worker suite has 8 cases (24 runs). These 8
cases now carry the tag `small`. The 3 cases on the large fixture came later and have their
own baseline: see "Worker suite, large fixture".

## Actual spend

| Suite | Kept runs | Superseded runs | Spent |
|---|---|---|---|
| PM | $8.66, judges $0.016 | $1.33, in `superseded-pm-triage.json` | $10.00 |
| Worker | $8.38 | $1.04, in `superseded-worker-medium-price-parse.json` | $9.42, under the ceiling of $150 |

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

| Case | ok rate | Hidden tests | Mean score (range) | Mean cost (range) | Dispatcher tokens | Worker tokens | Worker turns | Wall time (range) |
|---|---|---|---|---|---|---|---|---|
| `worker-easy-low-stock-boundary` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.26 ($0.21–$0.35) | 112k | 323k | 10.3 | 69 s (62–79) |
| `worker-easy-negative-qty` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.26 ($0.24–$0.28) | 103k | 412k | 13.0 | 79 s (75–81) |
| `worker-medium-csv-format` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.39 ($0.28–$0.47) | 113k | 541k | 14.7 | 97 s (75–116) |
| `worker-medium-money-refactor` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.37 ($0.34–$0.40) | 113k | 537k | 15.0 | 92 s (89–94) |
| `worker-medium-price-parse` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.32 ($0.28–$0.36) | 121k | 445k | 13.0 | 85 s (77–90) |
| `worker-hard-location` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.53 ($0.47–$0.60) | 123k | 697k | 16.7 | 131 s (114–145) |
| `worker-hard-error-lines` | 3/3 | 3/3 | 1.00 (1.00–1.00) | $0.48 ($0.41–$0.54) | 147k | 637k | 16.3 | 125 s (114–137) |
| `worker-hard-ambiguous-tax` | 3/3 | n/a | 1.00 (1.00–1.00) | $0.18 ($0.14–$0.22) | 129k | 184k | 6.3 | 328 s (28–908) |

| Tier | Runs | Hidden tests | Mean cost per run |
|---|---|---|---|
| easy | 6 | 6/6 | $0.26 |
| medium | 9 | 9/9 | $0.36 |
| hard | 9 | 6/6 (the ambiguous case has none) | $0.40 |

For the suite: 24/24 runs ok, hidden-test pass rate 21/21 (100%), mean cost $0.35 per run,
120k dispatcher tokens and 472k worker tokens per run, and 13.2 worker turns per run.

- The worker tokens and turns come from `issue-flow/scripts/usage.py`, by `session_id`
  (`agents` and `tokens` in each record). No worker spawned a subagent of its own.
- The worker is `claude-opus-5-5` on all 24 runs, and the dispatcher is `claude-haiku-5-5`.
- The duration is `wall_s`. `duration_s` and `turns` count only the dispatcher, because the
  worker runs in the background.
- `worker-hard-ambiguous-tax` run 2 took 908 s, but its worker finished in 47 s. The Haiku
  dispatcher started `sleep 900` in the background to keep its turn open, and the
  `claude -p` process waited for it. The grade and the cost are not affected. Use the
  median, 47 s, for this case.

### The case fix in this baseline

The first 3 runs of `worker-medium-price-parse` failed only on `diff_scope`. Each worker
passed the hidden tests and wrote a correct verdict. Each also updated `README.md`, which
documents the price format of a stock file. The brief asks for docs on public interfaces,
so that change is correct. The allowlist of the case did not include `README.md`. The fix
adds it, and the case then ran 3 more times. `superseded-worker-medium-price-parse.json`
keeps the first 3 runs.

## Worker suite, large fixture

The 3 cases tagged `large` start from `evals/worker/_fixture_large/`, a 40-file order
service (Sniper7Kills-LLC/claude-plugins#117). The run records are in
`evals/baselines/0.20.0/worker-large/`. The same cases on the head of batch #115 are in
`evals/candidates/batch-115/worker-large/`, for comparison.

| Item | 0.20.0 | Batch #115 head |
|---|---|---|
| Plugin | a git worktree of the tag `issue-flow--v0.20.0` (`c3974fe`) | a git worktree of `batch/115-token-speed` at `b341a1d` |
| Worker model | `claude-opus-5-5` | `claude-sonnet-5-5` (Sniper7Kills-LLC/claude-plugins#109) |
| PM skill | one `SKILL.md` | the dispatcher plus per-stage files (Sniper7Kills-LLC/claude-plugins#107) |
| Date | 2026-11-21 (UTC) | 2026-11-21 (UTC) |
| Runs | 3 per case, 9 in total | 3 per case, 9 in total |
| Spent | $3.69, under the ceiling of $25 | $1.25, under the ceiling of $25 |

The dispatcher is `claude-haiku-5-5` and the effort is `medium` on both sides. The runner
and the graders are those of Sniper7Kills-LLC/claude-plugins#117 on both sides.

| Case | Tier | Variant | ok | Hidden tests | Mean cost (range) | Worker tokens | Worker turns | Wall time (range) |
|---|---|---|---|---|---|---|---|---|
| `worker-large-medium-report-midnight` | medium | 0.20.0 | 3/3 | 3/3 | $0.40 ($0.36–$0.46) | 650k | 17.0 | 88 s (84–97) |
| | | batch head | 3/3 | 3/3 | $0.11 ($0.10–$0.14) | 370k | 11.7 | 37 s (33–42) |
| `worker-large-locate-repeated-sku` | medium, `locate` | 0.20.0 | 3/3 | 3/3 | $0.40 ($0.39–$0.41) | 650k | 16.7 | 91 s (89–93) |
| | | batch head | 3/3 | 3/3 | $0.13 ($0.11–$0.15) | 384k | 11.0 | 39 s (38–42) |
| `worker-large-hard-one-discount` | hard | 0.20.0 | 3/3 | 3/3 | $0.43 ($0.34–$0.55) | 623k | 15.7 | 88 s (79–97) |
| | | batch head | 3/3 | 3/3 | $0.18 ($0.14–$0.24) | 485k | 13.3 | 49 s (44–57) |

For the 9 runs of 0.20.0: 9/9 ok, hidden-test pass rate 9/9, mean cost $0.41 per run, 641k
worker tokens and 16.4 worker turns per run, and 89 s of wall time. For the batch head:
9/9 ok, 9/9, $0.14, 413k, 12.0 turns and 42 s. `scripts/eval-compare.py` gives `ship` for
the batch head against this baseline: the same pass rate, and a cost 66% lower.

- On 0.20.0, a worker on the large fixture uses 641k tokens per run, against 472k on the
  small fixture. The cost per run is $0.41 against $0.35. The large fixture thus gives a
  worker more to read, but all 3 cases passed 9/9 on both sides. Like the small fixture,
  it has no headroom on scores.
- A worker case does not load the PM skill, so the Sonnet worker is the likely cause of
  the batch head's gain. This measurement does not test the two changes apart.

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
- **Dispatcher wait.** The 908 s run of `worker-hard-ambiguous-tax` (see "Worker suite")
  came from a dispatcher that ran `sleep`. The worker case prompts now tell the dispatcher
  not to run `sleep` and not to poll. A candidate run then has no such outlier, and its
  mean wall time for this case can be lower than the baseline's for that reason alone.
- **Costs are estimates.** `total_cost_usd` is a client-side estimate at list prices, not
  billing data.
