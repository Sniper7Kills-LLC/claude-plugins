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

The PM suite has 6 cases (18 runs) and the worker suite has 8 cases (24 runs).

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
