# issue-flow evals

The evals measure what the issue-flow plugin does, so that a change to the plugin can be
compared with the version before it. Each eval case gives the PM or a worker one concrete
situation. The runner runs the case on headless `claude -p`, and then grades what the
agent did.

The evals do not ship with the plugin. They live at the repository level, and
`scripts/run-evals.py` runs them.

## Why our own runner

`claude plugin eval` has no code graders and no commands after a run. Its case `env`
accepts only `EVAL_*` keys, so a fake `gh` cannot go first on `PATH`. It also gives no
token counts per run. The operator decided on 2026-11-21 to use our own runner on
`claude -p` instead (Sniper7Kills-LLC/claude-plugins#102).

## Directory layout

| Path | Content |
|---|---|
| `evals/fakegh/gh` | The fake `gh`. It serves the tracker of one run and logs every call. |
| `evals/pm/<case>/case.json` | One PM case (the case format is below). |
| `evals/pm/<case>/tracker.json` | The tracker state at the start of that case. |
| `evals/pm/_fixture/` | The git repo that the PM cases start from. A directory without `case.json` is not a case. |
| `evals/worker/<case>/` | The worker cases (Sniper7Kills-LLC/claude-plugins#103). |
| `evals/results/` | Run results. Git ignores this directory. |
| `evals/baselines/<version>/` | Committed baseline run records and summaries, for example `0.20.0/pm/`. |
| `evals/rule-inventory.json` | The rule inventory of the PM `SKILL.md` at 0.20.0. `python3 scripts/rule-coverage.py extract` makes it, and `check` verifies a skill tree against it (Sniper7Kills-LLC/claude-plugins#104). |

## How to run

```bash
python3 scripts/run-evals.py --plugin-dir issue-flow --suite pm
```

| Flag | Effect |
|---|---|
| `--plugin-dir <path>` | The plugin directory to test. It must contain `.claude-plugin/plugin.json`. |
| `--suite pm\|worker` | The suite: the directory under `evals/`. |
| `--case <glob>` | Run only the cases whose name matches the glob. |
| `--tag <tag>` | Run only the cases that carry the tag, for example `c1-gate`. |
| `--runs N` | Runs per case. Without it, each case uses its own `runs` value. |
| `--out <dir>` | The results directory. The default is `evals/results/<UTC time>/`. |
| `--max-cost-usd X` | The cost ceiling for the whole invocation (see "Cost"). |
| `--main-model <id>` | The model of the run. It overrides the model of the case. |
| `--keep` | Keep each sandbox, and write its path into the run record. |

To test an older version, make a worktree of its tag and point `--plugin-dir` at it:

```bash
git worktree add --detach /tmp/if-0.20.0 issue-flow--v0.20.0
python3 scripts/run-evals.py --plugin-dir /tmp/if-0.20.0/issue-flow --suite pm --runs 3
```

The runner runs one case at a time. The exit code is 0 when every run passes and the
ceiling did not stop the suite, 1 when not, and 2 for a bad flag or a bad case file.

## Safety: what a run can touch

Each run happens in a new temporary directory, the sandbox:

- `repo/` is a copy of the case fixture, as a git repo on `main`. Its only remote is
  `origin`, a local bare repo at `origin.git/` in the same sandbox.
- `tracker.json` is a copy of the case tracker. The fake `gh` reads and writes it.
- `evals/fakegh/` is first on `PATH`, so `gh` is the fake.
- `GH_TOKEN` and `GITHUB_TOKEN` are a fake token, and `GH_CONFIG_DIR` is an empty
  directory. If a run calls a real `gh` by its full path, the real `gh` cannot log in.

The runner also isolates the run from the host's Claude Code setup:

- `--settings` turns off the installed `issue-flow@sniper7kills`, so only `--plugin-dir`
  loads.
- `--setting-sources project,local` keeps the host's user settings out, with their hooks
  and plugins.
- `--strict-mcp-config` keeps every MCP server out.
- `--permission-mode bypassPermissions` lets the run work without a person. This is safe
  only because of the sandbox. Never point a run at a real checkout.

After the run, the runner removes the sandbox, unless you give `--keep`.

## The case format

`case.json`:

```json
{
  "name": "pm-triage",
  "suite": "pm",
  "tags": ["pm", "triage"],
  "prompt": "You are the issue-flow PM ...",
  "timeout_s": 900,
  "runs": 3,
  "fixture": "../_fixture",
  "tracker": "tracker.json",
  "branches": ["batch/10-cli-output"],
  "disallowed_tools": ["Agent"],
  "model": null,
  "graders": [ ... ]
}
```

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | The case name. Use the directory name. |
| `suite` | yes | `pm` or `worker`. |
| `tags` | yes | `pm` (or `worker`) and the stage, for example `triage`, `c1-gate`, `a0-sweep`. |
| `prompt` or `prompt_file` | yes | The prompt text, or a file in the case directory that holds it. |
| `graders` | yes | One or more graders. Every case needs at least one deterministic grader. |
| `timeout_s` | no | The time limit of one run. The default is 900. |
| `runs` | no | Runs per case. The default is 1. |
| `fixture` | no | The directory to copy into `repo/`, relative to the case. Without it, `repo/` holds only a `README.md`. |
| `tracker` | no | The tracker file, relative to the case. The default is `tracker.json`. |
| `branches` | no | Branches to create at `main` and push to `origin`. |
| `disallowed_tools` | no | Tools the run cannot use, passed to `--disallowedTools`. |
| `model` | no | The model of the run, when `--main-model` is not given. |

### Graders

The score of a run is the fraction of its graders that pass. A run is `ok` when it has no
error and every deterministic grader passes. The `llm` grader is a second score: it
changes `score`, not `ok`.

**`gh_log`** (deterministic) reads the log of the fake `gh`.

```json
{"type": "gh_log", "ordered": false,
 "expect": [{"cmd": "issue edit", "args": "^(\\d+ )*2\\b.*--add-label[ =]\\S*status:needs-feedback"},
            {"cmd": "issue comment", "args": "^2\\b", "body": "status repair: "}],
 "forbid": [{"cmd": "pr merge"}]}
```

- `cmd` is the subcommand, for example `issue edit` or `pr merge`. For `gh api` it is `api`.
- `args` is a regular expression. It is matched against the arguments after `cmd`, joined
  with spaces. `^` anchors only at the start of the arguments, never at a line of an inline
  body.
- `body` is a regular expression. It is matched against the comment or body text of the
  call, including text from `--body-file`. Here `^` anchors at the start of each line.
- Each `expect` entry must match a call that succeeded. With `"ordered": true`, the
  entries must match in the order given.
- No `forbid` entry may match any call. A call that the fake refused also counts, because
  it shows the decision.

**`file`** (deterministic) passes when the file exists in `repo/` and its text matches the
regular expression.

```json
{"type": "file", "path": ".eval/send-back-11.md", "regex": "cannot be combined"}
```

A case uses a `file` grader where the agent would do something the sandbox cannot show.
For example, a PM gate that sends work back to a worker writes the message to a file.

**`llm`** (judged) asks a cheap judge: `claude -p` on Haiku, with no tools, one vote.

```json
{"type": "llm", "target": "gh_log", "rubric": "The comment on #2 asks specific questions."}
```

`target` is `final` (the last message of the run) or `gh_log` (every tracker write, with
its body). The judge answers PASS or FAIL and gives one sentence of reason.

Other suites add grader types to the `GRADERS` table in `scripts/run-evals.py`.

## The `tracker.json` schema

```json
{
  "repo": {"owner": "acme", "name": "widgets", "defaultBranch": "main"},
  "user": "eval-bot",
  "labels": [{"name": "status:ready", "color": "0E8A16", "description": ""}],
  "issues": [
    {"number": 1, "title": "...", "body": "...", "state": "open",
     "labels": ["status:ready"], "assignees": ["eval-bot"], "author": "maintainer",
     "createdAt": "2026-11-20T09:00:00Z", "updatedAt": "2026-11-20T09:00:00Z",
     "comments": [{"id": 1101, "author": "eval-bot", "body": "...", "createdAt": "..."}]}
  ],
  "prs": [
    {"number": 12, "title": "...", "body": "...", "state": "open", "isDraft": true,
     "headRefName": "issue/11-json-flag", "baseRefName": "batch/10-cli-output",
     "labels": [], "assignees": [], "author": "eval-bot", "comments": [],
     "reviews": [], "mergeable": "MERGEABLE", "createdAt": "...", "updatedAt": "..."}
  ]
}
```

- `user` is the login that the fake gives for `gh api user` and `@me`.
- An issue `state` is `open` or `closed`. A PR `state` is `open`, `closed` or `merged`.
- Labels are names. An `issue edit` that adds a label not in `labels` fails, as on GitHub.
- Issues and PRs share one number sequence. A new item gets the highest number plus 1.
- A new comment gets the highest comment id plus 1.

## The fake `gh`

The fake reads `$IFE_TRACKER` and appends to `$IFE_GH_LOG`. The runner sets both. It
supports these subcommands, with the `--json`, `--jq` and filter flags the PM uses:

- `issue list | view | edit | comment | create | close`
- `pr list | view | create | ready | merge | comment`
- `label list | create`
- `auth status`, `repo view`
- `api` GET reads: `user`, `repos/{owner}/{repo}`, `.../issues`, `.../issues/<n>`,
  `.../issues/<n>/comments`, `.../issues/comments/<id>`, `.../pulls`, `.../pulls/<n>`,
  `.../labels`

`--jq` needs `jq` on `PATH`. `--search` is accepted but does not filter, so a search
returns more items than GitHub would, never fewer. Any other subcommand, and any `api`
call that writes, exits 1 with a message that names the fake.

Each call adds one line to `gh-log.jsonl`:

```json
{"seq": 3, "argv": ["issue", "edit", "21", "--remove-label", "status:in-review"],
 "cmd": "issue edit", "time": "2026-11-21T18:00:00Z", "kind": "write", "exit": 0}
```

- `kind` is `read`, `write` or `error`. An `error` line also has `error`, the message.
- A call that carries text (a comment, a body, `issue close --comment`) also has `body`.

## The run record

The runner writes `<out>/results.json` after each run, and one directory per run:
`<out>/<case>/run-<k>/` holds `record.json`, `claude-stdout.json`, `claude-stderr.txt`,
`gh-log.jsonl` and the final `tracker.json`.

`results.json` is `{suite, variant, started, finished, max_cost_usd, total_cost_usd,
partial, records}`. Each record:

| Key | Meaning |
|---|---|
| `case`, `run` | The case name and the run number, from 1. |
| `variant` | `{plugin_dir, git_sha, version}` of the plugin under test. |
| `ok` | No error, and every deterministic grader passed. |
| `score` | The fraction of graders that passed. |
| `graders` | `[{type, passed, detail}]`, in case order. |
| `deterministic`, `llm` | `{passed, total}` for each kind of grader. |
| `cost_usd` | `total_cost_usd` from the `claude -p` result. |
| `judge_cost_usd` | The cost of the `llm` judges of this run. |
| `usage` | `{input, output, cache_read, cache_write}` from the result's `usage`. |
| `model_usage` | The result's `modelUsage`, unchanged: tokens and cost per model. |
| `turns` | `num_turns` from the result. |
| `duration_s` | `duration_ms` from the result, in seconds. |
| `session_id` | The session id. The transcript is `~/.claude/projects/<project>/<session_id>.jsonl`, and subagent transcripts are in `<session_id>/subagents/`. |
| `transcript` | The transcript path, when the runner found it. |
| `model` | The model asked for, or else the model with the highest cost in `modelUsage`. |
| `error` | `null`, or why the run failed: a timeout, output that is not a result, or an error result. |
| `sandbox` | The kept sandbox path with `--keep`, else `null`. |

## Cost

`cost_usd` is the cost that governs the budgets and the decision rules. It is
`total_cost_usd` from the `claude -p --output-format json` result. That value
**includes subagents**. The `usage` field of the result does **not** include subagents:
it counts only the main agent loop. `modelUsage` includes subagents, per model. Source:
[Track cost and usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking.md),
retrieved 2026-11-21. Both cost figures are client-side estimates, not billing data.

A real run on Claude Code 2.1.330 (2026-11-21) confirmed the field names: `total_cost_usd`,
`usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`,
`usage.cache_creation_input_tokens`, `modelUsage`, `num_turns`, `duration_ms` and
`session_id`. The result also has `subagent_stats`, a count of the subagents the run
spawned. In the 0.20.0 baseline, two runs spawned a Haiku cross-check subagent. In both
runs, `total_cost_usd` was the sum of the `modelUsage` costs, with the Haiku cost included
(`pm-batch-cross-check` run 3: $0.63783 total = $0.63542 Opus + $0.00240 Haiku).

The ceiling works in two places:

1. Before each run, the runner adds the cost of every earlier run and its judges. When the
   sum reaches `--max-cost-usd`, the runner stops, sets `partial: true`, and starts no
   more runs.
2. Each run gets the rest of the budget as its own `--max-budget-usd`, so one run cannot
   go far past the ceiling.

## Baselines

`evals/baselines/<version>/<suite>/` keeps the results of a release, so that a later change
can be compared with it. Each directory holds:

- `results.json`, in the runner's format. Before commit, `variant.plugin_dir`, `transcript`
  and `sandbox` are cleared, because they hold local paths. `session_id` stays.
- `summary.json`, with the means per case.
- Any superseded runs, with the reason.

### 0.20.0, PM suite

The runs were on 2026-11-21, with Claude Code 2.1.330 and the default model
(claude-opus-5-5): 6 cases, 3 runs each, at concurrency 1. Total cost: $10.00, judges
included.

| Case | ok rate | Mean score | Mean cost | Mean turns | Mean duration |
|---|---|---|---|---|---|
| `pm-a0-split-status` | 3/3 | 1.00 | $0.42 | 5.0 | 19.6 s |
| `pm-batch-cross-check` | 3/3 | 1.00 | $0.63 | 9.0 | 59.6 s |
| `pm-c1-no-prepatch` | 3/3 | 1.00 | $0.47 | 5.7 | 34.3 s |
| `pm-c1-unevidenced` | 3/3 | 1.00 | $0.45 | 5.0 | 24.2 s |
| `pm-semantic-conflict` | 3/3 | 1.00 | $0.49 | 5.3 | 32.5 s |
| `pm-triage` | 3/3 | 1.00 | $0.44 | 6.0 | 20.3 s |

The first 3 runs of `pm-triage` failed because of a grader error, not a PM error. The
`args` regex was matched with `re.M`, so the forbid entry `^4\b` (issue #4) matched a line
`4. How often …` in the body of the comment on #2. The fix: `args` now anchors at the
start of the arguments only, and `body` still matches line by line. `pm-triage` then ran
3 more times. `superseded-pm-triage.json` keeps the first 3 runs.

## Tests

`python3 scripts/test-run-evals.py` tests the runner and the fake `gh` with a fake
`claude` (set through `IFE_CLAUDE`). The tests cost nothing and use no network. They also
run each real case twice: once with a PM that does nothing, which must fail, and once
with a golden PM, which must pass. Add a golden run to the test when you add a case.

## Add a case

1. Make `evals/<suite>/<name>/` with `case.json` and `tracker.json`.
2. Give the case one decision to test. The prompt names the situation and the stage, and
   it says what to do instead of an action the sandbox cannot do, for example a worker
   launch.
3. Give it at least one deterministic grader that fails when the agent does nothing.
4. Add its golden run to `GOLDEN` in `scripts/test-run-evals.py`.
5. Run it 3 times on the current release. A case that the current release fails in 2 of
   3 runs is a bad case: fix the case, not the finding.
