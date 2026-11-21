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
| `evals/worker/<case>/case.json` | One worker case (Sniper7Kills-LLC/claude-plugins#103). The worker suite is below. |
| `evals/worker/<case>/tracker.json` | The tracker state for that case: one issue, with its acceptance criteria. |
| `evals/worker/<case>/hidden/` | The hidden tests of that case. The agent never sees them. |
| `evals/worker/<case>/reference.patch` | A reference solution. A test proves that the hidden tests pass with it. |
| `evals/worker/_fixture/` | The small project that the cases tagged `small` start from. |
| `evals/worker/_fixture_large/` | The large project that the cases tagged `large` start from (Sniper7Kills-LLC/claude-plugins#117). |
| `evals/results/` | Run results. Git ignores this directory. |
| `evals/baselines/<version>/` | Committed baseline run records and summaries, for example `0.20.0/pm/`. |
| `evals/candidates/<issue>-<slug>/<suite>/` | Committed results of a candidate change, measured against a baseline, for example `109-sonnet-worker/worker/`. |
| `evals/BASELINE.md` | The numbers of the current baseline, for both suites. |
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
| `--max-cost-usd X` | The cost ceiling of this one invocation, not of a suite or a day: each new invocation starts again at $0 (see "Cost"). |
| `--main-model <id>` | The model of the run. It overrides the model of the case. |
| `--effort low\|medium\|high\|xhigh\|max` | The effort level of each run. The default is `medium`, the effort of the 0.20.0 baselines. See "Effort". |
| `--keep` | Keep each sandbox, and write its path into the run record. |

To test an older version, make a worktree of its tag and point `--plugin-dir` at it:

```bash
git worktree add --detach /tmp/if-0.20.0 issue-flow--v0.20.0
python3 scripts/run-evals.py --plugin-dir /tmp/if-0.20.0/issue-flow --suite pm --runs 3
```

The runner runs one case at a time. The exit code is 0 when every run passes and the
ceiling did not stop the suite, 1 when not, and 2 for a bad flag or a bad case file.

## Quick start: a smoke test

A smoke test runs one case of each suite once. It shows that the runner, the sandbox and
the plugin work. It costs about $1 (the dogfood run of 2026-11-21 cost $0.77 for both).

Give the inner plugin directory, `<worktree>/issue-flow`, not the worktree root. A
directory without `.claude-plugin/plugin.json` stops the runner with exit 2.

```bash
git worktree add --detach /tmp/if-0.20.0 issue-flow--v0.20.0   # the plugin version to test
python3 scripts/run-evals.py --plugin-dir /tmp/if-0.20.0/issue-flow --suite pm \
    --case pm-triage --runs 1 --max-cost-usd 2 --out evals/results/smoke-pm
python3 scripts/run-evals.py --plugin-dir /tmp/if-0.20.0/issue-flow --suite worker \
    --case worker-easy-negative-qty --runs 1 --max-cost-usd 2 --out evals/results/smoke-worker
python3 scripts/eval-compare.py evals/baselines/0.20.0/pm evals/results/smoke-pm          # exits 5
python3 scripts/eval-compare.py evals/baselines/0.20.0/worker evals/results/smoke-worker  # exits 5
git worktree remove /tmp/if-0.20.0
```

`--out` may be any directory. `evals/results/` is the default place, and Git ignores it.

`--max-cost-usd` applies to each invocation by itself, so these two commands can spend up
to $4 together.

A smoke test is not a decision, so both `eval-compare.py` commands above exit 5 with
"smoke test only, not a decision". The script refuses a candidate that has fewer cases or
fewer runs per case than the baseline. One run of a case can differ from the 3-run mean by more than the
margins of the decision rules. In the dogfood run, one `pm-triage` run used 186,639 PM
tokens against a baseline mean of 244,830, with no change to the plugin.

## Safety: what a run can touch

Each run happens in a new temporary directory, the sandbox:

- `repo/` is a copy of the case fixture, as a git repo on `main`. Its only remote is
  `origin`, a local bare repo at `origin.git/` in the same sandbox.
- `tracker.json` is a copy of the case tracker. The fake `gh` reads and writes it.
- `evals/fakegh/` is first on `PATH`, so `gh` is the fake.
- `GH_TOKEN` and `GITHUB_TOKEN` are a fake token, and `GH_CONFIG_DIR` is an empty
  directory. If a run calls a real `gh` by its full path, the real `gh` cannot log in.
- Git reads no host config: `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`.
  Thus the run has no credential helper, no `url.*.insteadOf` and no signing key of the
  host. `GIT_SSH_COMMAND=false` makes every ssh remote fail, and `GIT_TERMINAL_PROMPT=0`
  stops git from asking for a password. The run commits as `eval <eval@example.invalid>`.
- `plugin/` is a copy of `--plugin-dir`, without `.git`. The run loads the copy, and
  `{plugin_dir}` in a prompt names the copy. A run in `bypassPermissions` mode can change
  its plugin files, but not the files of the plugin under test.

The runner also isolates the run from the host's Claude Code setup:

- `--settings` turns off the installed `issue-flow@sniper7kills`, so only `--plugin-dir`
  loads.
- `--setting-sources project,local` keeps the host's user settings out, with their hooks
  and plugins.
- `--strict-mcp-config` keeps every MCP server out.
- `--permission-mode bypassPermissions` lets the run work without a person. This is safe
  only because of the sandbox. Never point a run at a real checkout.

A live check on 2026-11-21 (Claude Code 2.1.330, one Haiku run for $0.002 with the runner's
own flags and environment) found that `--setting-sources project,local` keeps these out
of a run:

- The user's `~/.claude/CLAUDE.md`. The run found no CLAUDE.md or memory text in its
  context.
- The user agents in `~/.claude/agents/`. The `Agent` tool listed only the built-in agents
  and the agents of `--plugin-dir`.
- The user skills in `~/.claude/skills/`. The run listed only the built-in skills and the
  skills of `--plugin-dir`.

Auto-memory stays on: the system prompt names a memory directory for the sandbox project.
That directory is new and empty for each sandbox, so no host memory reaches the run. No
extra flag is necessary. If a later Claude Code version changes this, add `--bare` or a
`CLAUDE_CONFIG_DIR` of its own to the run, and check the login again.

The runner itself also runs commands in the sandbox, after the agent can change it: git to
build the sandbox and to grade, and the hidden tests. These get a minimal environment:
`PATH`, `TMPDIR`, `LANG`, a `HOME` in the sandbox, the git variables above and no host
secret. The runner's git also gets `-c core.hooksPath=/dev/null -c core.fsmonitor=false
-c core.pager=cat`, and `diff_scope` uses `git diff --no-ext-diff`. Thus a hook, an
fsmonitor or a diff driver that the agent writes into `.git/config` does not run on the
host. The hidden tests run the agent's code by design, but in a separate clone and with no
host secret.

After the run, the runner removes the sandbox, unless you give `--keep`.

### The environment of a run

The runner builds the environment of each run, and of each `llm` judge, from an
allowlist (`KEEP_ENV` and `KEEP_ENV_PREFIXES` in `scripts/run-evals.py`). A host
variable that is not on the allowlist does not get to the run. Thus the result of a run
does not change when you start the runner from inside a Claude Code session.

The run keeps these host variables:

| Group | Variables |
|---|---|
| System | `HOME`, `USER`, `LOGNAME`, `SHELL`, `PATH` (with the fake `gh` first), `TMPDIR`, `TERM`, `TZ`, `LANG`, `LC_*` |
| Proxy and certificates | `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` (and the lowercase forms), `NODE_EXTRA_CA_CERTS`, `CLAUDE_CODE_CERT_STORE`, `CLAUDE_CODE_CLIENT_CERT`, `CLAUDE_CODE_CLIENT_KEY`, `CLAUDE_CODE_CLIENT_KEY_PASSPHRASE` |
| Authentication | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_PROFILE`, `ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_WORKSPACE_ID`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_API_KEY_HELPER_TTL_MS`, `CLAUDE_CONFIG_DIR` (the credentials can be in it) |
| Cloud providers | `CLAUDE_CODE_USE_BEDROCK`, `_VERTEX`, `_FOUNDRY`, `_MANTLE`, `_ANTHROPIC_AWS`, `_ANTHROPIC_GOOGLE_CLOUD`, the matching `CLAUDE_CODE_SKIP_*_AUTH`, and `AWS_*`, `ANTHROPIC_BEDROCK_*`, `ANTHROPIC_VERTEX_*`, `ANTHROPIC_FOUNDRY_*`, `ANTHROPIC_AWS_*`, `ANTHROPIC_GOOGLE_CLOUD_*`, `VERTEX_REGION_*` |

The list comes from https://code.claude.com/docs/en/env-vars. If your login needs a
variable that is not in it, add the variable to `KEEP_ENV`. Google application-default
credentials (`GOOGLE_APPLICATION_CREDENTIALS`) are not in the list.

The runner then sets `IFE_TRACKER`, `IFE_GH_LOG`, `GH_TOKEN`, `GITHUB_TOKEN`,
`GH_CONFIG_DIR` and `GH_PROMPT_DISABLED` for the fake `gh`. It also sets
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM`, `GIT_SSH_COMMAND`, `GIT_TERMINAL_PROMPT` and
the `GIT_AUTHOR_*` and `GIT_COMMITTER_*` identity, as "Safety" above describes.

The run gets no other `CLAUDE*` or `ANTHROPIC_*` variable from the host. A Claude Code
session sets these for its Bash commands and hooks, and they are the reason for the
allowlist:

- `CLAUDE_CODE_SESSION_ID`: the session of the host.
- `CLAUDE_CODE_CHILD_NOTES_DIR`: the scratch directory of the host. In
  `worker-hard-ambiguous-tax` run 2 of the 0.20.0 baseline, the eval worker made its
  scratch directory there, outside the sandbox.
- `CLAUDE_CODE_MESSAGING_SOCKET` and `CLAUDE_CODE_MESSAGING_TOKEN`: the inbox of the host.
- `CLAUDE_EFFORT`: the effort of the host. The runner sets the effort with `--effort`.
- `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_SESSION_ATTENDED` and
  `CLAUDE_CODE_ENTRYPOINT`.

The run also gets no host setting that changes the run, for example `ANTHROPIC_MODEL` or
`CLAUDE_CODE_EFFORT_LEVEL`. `CLAUDE_CODE_EFFORT_LEVEL` would override `--effort`.

### Effort

The runner gives each run `--effort <level>` (https://code.claude.com/docs/en/cli-reference).
The default is `medium`. The run record and `results.json` keep the level as `effort`.
`scripts/eval-compare.py` refuses to compare two results with different efforts, or a
result with no effort, and exits 5.

`--effort` sets the effort of the main session: the PM, or the dispatcher of a worker
case. A subagent on another model does not take it
(https://code.claude.com/docs/en/model-config). Thus the worker of a worker case runs at
the `effort` of its agent definition or, when there is none, at the default of its model.
`claude-opus-5-5`, `claude-sonnet-5-5` and `claude-haiku-5-5` default to `medium`.
`agents/issue-worker.md` sets no `effort` at 0.20.0.

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
| `prompt` or `prompt_file` | yes | The prompt text, or a file in the case directory that holds it. The runner replaces each `{plugin_dir}` in it with the absolute `--plugin-dir` path. |
| `graders` | yes | One or more graders. Every case needs at least one deterministic grader: the runner refuses a case with only `llm` graders (exit 2). |
| `timeout_s` | no | The time limit of one run. The default is 900. |
| `runs` | no | Runs per case. The default is 1. |
| `fixture` | no | The directory to copy into `repo/`, relative to the case. Without it, `repo/` holds only a `README.md`. |
| `tracker` | no | The tracker file, relative to the case. The default is `tracker.json`. |
| `branches` | no | Branches to create at `main` and push to `origin`. |
| `disallowed_tools` | no | Tools the run cannot use, passed to `--disallowedTools`. |
| `model` | no | The model of the run, when `--main-model` is not given. |
| `docs_paths` | no | Globs of the documentation files that the brief's `docs` practice lets the worker change. `diff_scope` always allows them. |

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

**`llm`** (judged) asks a cheap judge: `claude -p` on Haiku, with no tools. The majority of
3 votes decides.

```json
{"type": "llm", "target": "gh_log", "rubric": "The comment on #2 asks specific questions."}
```

`target` is `final` (the last message of the run) or `gh_log` (every tracker write, with
its body). Each vote answers PASS or FAIL and gives one sentence of reason. A judge call
that fails is a FAIL vote. The judge stops when 2 votes agree, so a clear run costs 2 calls
and a split run costs 3. The `detail` gives the vote count and the reason of the first
vote on the winning side, for example `2/3 votes: PASS: ...`.

One vote was not enough. In the candidate of Sniper7Kills-LLC/claude-plugins#111, `pm-c1-no-prepatch` run 1 got a FAIL vote
whose reason said that the PM met the rubric. A new judgment of that run, with 3 votes, gave
`2/2 votes: PASS`. The cost of the votes is small: the judges of the 0.20.0 PM suite cost
$0.016 for 18 runs with one vote each, so 2 or 3 votes cost about $0.03 to $0.05 for the
suite. The judge cost still counts against `--max-cost-usd`, as before.

The worker suite adds three deterministic graders: `hidden_tests`, `verdict` and
`diff_scope`. The worker suite section below describes them. A suite adds a grader type to
the `GRADERS` table in `scripts/run-evals.py`.

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
- `events` is optional. Each event adds a comment to the tracker after a call, as a person
  would during a session. A later sweep can then find something new:

  ```json
  "events": [{"after": {"cmd": "pr merge", "args": "^53\\b"},
              "comment": {"issue": 63, "author": "maintainer", "body": "Answer: ..."}}]
  ```

  The event fires once, after the first successful call whose `cmd` is equal to `after.cmd`
  and whose arguments match the `after.args` regular expression. Without `args`, any
  arguments match. The comment updates the issue's `updatedAt`. The fake then sets
  `"fired": true` on the event. An event adds no line to the log. A refused call fires no
  event.

## The fake `gh`

The fake reads `$IFE_TRACKER` and appends to `$IFE_GH_LOG`. The runner sets both. It
supports these subcommands, with the `--json`, `--jq` and filter flags the PM uses:

- `issue list | view | edit | comment | create | close`
- `pr list | view | create | edit | ready | merge | comment`
- `label list | create`
- `auth status`, `repo view`
- `api` GET reads: `user`, `repos/{owner}/{repo}`, `.../issues`, `.../issues/<n>`,
  `.../issues/<n>/comments`, `.../issues/comments/<id>`, `.../pulls`, `.../pulls/<n>`,
  `.../pulls/<n>/comments`, `.../pulls/<n>/reviews`, `.../labels`

`pr edit` changes the title, the body, the base and the labels of a PR. The tracker holds no
line comments on a diff, so `pulls/<n>/comments` always returns `[]`.

`--jq` needs `jq` on `PATH`. `--search` is accepted but does not filter, so a search
returns more items than GitHub would, never fewer. Any other subcommand, and any `api`
call that writes, exits 1 with a message that names the fake.

After a successful call, the fake fires the tracker `events` that the call triggers (see
the schema above).

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

`results.json` is `{suite, variant, effort, started, finished, max_cost_usd,
total_cost_usd, partial, records}`. Each record:

| Key | Meaning |
|---|---|
| `case`, `run` | The case name and the run number, from 1. |
| `variant` | `{plugin_dir, git_sha, version}` of the plugin under test. |
| `effort` | The `--effort` level of the run. `results.json` also has it at the top level. |
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
| `wall_s` | The wall time of the `claude -p` process, in seconds. A worker case needs it, because `duration_ms` stops at the end of the main loop and does not include a background subagent. In the first live worker run, `duration_s` was 5.3 s and `duration_api_ms` was 84 s. |
| `session_id` | The session id. The transcript is `~/.claude/projects/<project>/<session_id>.jsonl`, and subagent transcripts are in `<session_id>/subagents/`. |
| `transcript` | The transcript path, when the runner found it. |
| `model` | The model asked for, or else the model with the highest cost in `modelUsage`. |
| `error` | `null`, or why the run failed: a timeout, output that is not a result, or an error result. A timeout or output that is not a result gives a run with no result: `session_id` is null and `cost_usd` is 0, which is not its real cost. |
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

`evals/BASELINE.md` gives the numbers of the current baseline, for both suites.

### Make a baseline

Run `scripts/eval-baseline.py` on the machine of the eval run. It needs the transcripts in
`~/.claude/projects/`, so run it soon after the eval run.

```bash
python3 scripts/eval-baseline.py evals/results/<stamp> --out evals/baselines/<version>/<suite> \
    --label "issue-flow (git worktree of tag issue-flow--v<version>)"
```

The script changes each record in these ways:

- `agents` is the output of `issue-flow/scripts/usage.py --json` for the session: the
  tokens, the turns and the estimated cost of each agent.
- `tokens` is `{main, subagents}`, the sum of the four token counts of the main agent and
  of every other agent. In a worker case, `subagents` is the worker.
- `variant.plugin_dir` becomes the `--label` text, and `transcript` and `sandbox` become
  null.

- A record with no `effort` takes the `effort` of `results.json`.

- `tracker_calls` is `{total, read, write, error}`, the count of the lines in the run's
  `gh-log.jsonl`. A run with no log gets `null`. The count is information only: no decision
  rule reads it. Every PM run starts with 3 reads from the SessionStart preflight hook
  (`label list`, `pr list` and `issue list`), so the lowest count is 3.

It also writes `summary.json`, with `mean_tracker_calls` for each case. The exit code is 1
when a run has no transcript. Then that record has `agents: null`.

Before it writes, the script scans the whole output for a local path: `/Users/`,
`/home/`, `/private/` or `/var/folders/`. When it finds one, it names the field, writes
nothing and exits 2. A grader `detail` can hold a sandbox path, for example.

### Compare a candidate with the baseline

1. Make a worktree of the candidate, or use this checkout.
2. Run the same suite on it, with the same number of runs as the baseline:

   ```bash
   python3 scripts/run-evals.py --plugin-dir issue-flow --suite worker --runs 3 --max-cost-usd 150
   ```

3. Compare the result with the baseline:

   ```bash
   python3 scripts/eval-compare.py evals/baselines/0.20.0/worker evals/results/<stamp>
   ```

`eval-compare.py` reads `suite` from both results and applies the decision rule of that
suite (Sniper7Kills-LLC/claude-plugins#106). Each threshold is inclusive.

| Rule | The verdict is `ship` only when |
|---|---|
| `pm` | No deterministic grader has a lower pass rate on any case than in the baseline. |
| | The mean score is not more than 0.05 below the baseline. A higher score passes. |
| | The mean PM tokens per run are at least 10% lower, on 3-run means. These are the four counts of `usage`, the main loop only. |
| `worker` | The hidden-test pass rate is not more than 5 points below the baseline. |
| | The mean `cost_usd` per run is at least 40% lower. |

The operator set the 10% margin on 2026-11-21. Before, "fewer tokens" was enough, and a
dogfood run gave `ship` for a candidate that was the baseline itself: one run of
186,639 PM tokens against a 3-run mean of 244,830. The PM rule needs at least 3 runs per
case on both sides.

The script refuses an incomplete candidate, and exits 5 with "smoke test only, not a
decision":

- The candidate did not run a case of the baseline.
- The candidate has `partial: true`: the cost ceiling stopped it.
- The candidate has fewer runs of a case than the baseline.

A case that only the candidate ran is left out, and the report names it.

A run with no result (a timeout, or output that is not a result object) counts as a
failed run in the hidden-test pass rate, even when its branch passes the hidden tests. Its
cost is not known, so the cost means leave it out. The report gives the count of these
runs for each side.

If the worker rule fails, the script gives the numbers per tier (`easy`, `medium`, `hard`).
It reads the tiers from the tags in `evals/worker/<case>/case.json`. When `--evals-dir`
has no `case.json` for a compared case, the script refuses and exits 5. Then it tries the
hybrid: the baseline runs on the cases tagged `hard`, and the candidate runs on the rest. The
verdict is `hybrid` only when the hybrid meets the same two checks. Otherwise it is
`do not ship`. The hidden-test pass rate counts only the runs that have a `hidden_tests`
grader, so `worker-hard-ambiguous-tax` is not in it.

Before the rules, the script compares the `effort` of the two results. When they differ,
or one result has no `effort`, it refuses and exits 5. Run the candidate with the
`--effort` of the baseline. The 0.20.0 baselines are `medium`.

The exit code is 0 for `ship`, 1 for `do not ship`, 3 for `hybrid`, 2 for bad input, and 5
for a refusal: a different or unknown effort, an incomplete candidate, or a missing case
file.
`--json` prints the full report. `--rule pm|worker` replaces the rule of the suite.

The report also flags each run whose `agents` cost is more than 10% from its `cost_usd`.
The `cost_usd` still governs.

At 3 runs per case, a drop under about 15 points cannot be detected. The report says so.
A `ship` verdict is not proof that no small regression exists.

### 0.20.0, PM suite

`evals/BASELINE.md` has the full 0.20.0 numbers for both suites. This section keeps the
PM run notes.

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

## Session-level PM cases

A single-decision PM case gives the PM one stage and stops. It cannot show what a change
does over a long session: fewer re-reads, a tracker read in one pass, or less context
growth from sweep to sweep. A session-level case runs one PM through several stages in
order, against one tracker, so those effects add up in one run
(Sniper7Kills-LLC/claude-plugins#116).

Use a session-level case when the change that you measure pays off across stages, for
example a change to how the PM reads the tracker, or to what it keeps in context between
sweeps. Use the single-decision cases to test one rule. A session-level run costs about
2.5 times as much as a single-decision run ($1.16 against $0.48 on 0.20.0), and its token count varies more from run to run.

```bash
python3 scripts/run-evals.py --plugin-dir issue-flow --suite pm --tag session --runs 3 --max-cost-usd 25
```

A session-level case carries the tag `session`. It is in the `pm` suite, so a full
`--suite pm` run includes it.

### `pm-session-batch`

The prompt is in `evals/pm/pm-session-batch/prompt.md`. The PM does four steps:

1. The Stage A0 sweep and the Stage A triage. The tracker holds 8 backlog issues: 3 clear
   issues, 2 unclear issues, an epic without sub-issues, an issue with two `status:`
   labels, and an issue that is already ready.
2. Stage B step 1 and the plan comments (step 3, plan only): the PM forms batches from the
   ready pool and plans each member. It claims and launches nothing.
3. The Stage C1 gate on two given verdicts of the in-flight batch #50: #51 is good and
   sub-merges, and #52 has a criterion with "implemented" as its evidence, so it goes back
   to the worker.
4. A second Stage A0 sweep and Stage A triage. The tracker `events` (see the schema
   above) add a maintainer's answer to #63 when the PM merges #51's PR. The second sweep
   must find that answer and make #63 ready.

The deterministic graders read the gh log. They check the triage labels and comments,
the epic decomposition, the split-status repair, a `type:batch` tracking issue, a plan
comment on each loose ready issue, the squash merge with `[skip ci]`, the `status:batched`
swap on #51, and #63 made ready after the merge. They forbid any claim, launch or merge
of #52. A `file` grader reads the send-back message for #52. The `llm` grader judges the
plans and the triage questions.

In this test, `gh pr merge` changes only the tracker, and the remote holds no commits of
the workers. The prompt tells the PM so, and tells it to skip the C1 step 4b check of the
landed message.

`evals/BASELINE.md` ("PM session case") gives the numbers on 0.20.0 and on the batch #115
head. Use `tracker_calls` (see "Make a baseline") for the tracker calls of a run. In
each of the 6 measured runs, Claude Code itself called `gh pr view <branch> --json url`
12 times, and the fake refused each call. These calls count as `error` lines.

## The worker suite

The worker suite measures the `issue-worker` agent on fixed issues. Each case gives one
worker one issue in a small project. After the run, hidden tests that the worker never saw
decide whether the issue is done.

```bash
python3 scripts/run-evals.py --plugin-dir issue-flow --suite worker --tag easy --runs 1 --max-cost-usd 5
```

`--tag small` runs the 8 cases of the 0.20.0 worker baseline, and `--tag large` runs the 3
cases on the large fixture. Without a tag, the suite runs all 11.

### How a worker case runs

1. The main session is a dispatcher on Haiku (`"model": "haiku"` in the case). It does no
   work on the issue.
2. The dispatcher spawns `issue-flow:issue-worker` with `isolation: "worktree"` and a
   standard handoff brief. The brief is standalone, with `ci: skip`, base `origin/main`,
   remote `origin`, `tdd: true`, and `pluginRoot: {plugin_dir}`.
3. The worker gets its model from `agents/issue-worker.md` of the plugin under test. Thus a
   change to the worker model is a change to the variant, not to the case.
4. The worker reads the issue through the fake `gh`, commits on `issue/<n>-<slug>`, pushes
   to the sandbox `origin`, and opens a draft PR in the fake tracker.
5. The dispatcher writes the worker's verdict to `.eval/verdict.json` in the sandbox repo.
   The prompt tells it not to run `sleep` and not to poll while it waits. In the 0.20.0
   baseline, one dispatcher ran `sleep 900`, and `wall_s` was 908 s for a 47 s worker.
6. The runner grades the run. Then it removes the sandbox, with the worker's worktree in it.

The record keeps `cost_usd`, which includes the worker. Use `wall_s` for the duration of a
worker case, not `duration_s`. `model_usage` gives the tokens and
the cost per model, so the worker's share is the entry of its model. `usage` counts only the
dispatcher.

### The fixtures

`evals/worker/_fixture/` is `inventory`, a small command-line tool on the Python standard
library. Its test command is `python3 -m unittest`, and its 11 tests pass. It has 4 source
files, so a worker finds the file of a bug at once.

`evals/worker/_fixture_large/` is `shop`, an order service on the Python standard library:
40 files. The `shop` package has 5 subpackages (`api`, `services`, `models`, `storage` and
`util`), and there are tests, sample data in `data/` and `docs/api.md`. A
request goes from `App.handle` through the router and a handler to a service, a repository
and a model, and the command line sends each command through `App.handle` too. Its test
command is `python3 -m unittest`, and its 27 tests pass. On this fixture, a change that
saves locating work can show a gain (Sniper7Kills-LLC/claude-plugins#117).

Each case starts from an untouched copy of its fixture. No fixture holds a hidden test.

### The cases

| Case | Tags | What the worker must do |
|---|---|---|
| `worker-easy-low-stock-boundary` | `small`, `easy` | A one-line fix: `low` leaves out an item at the threshold. |
| `worker-easy-negative-qty` | `small`, `easy` | A small validation: reject a negative quantity. |
| `worker-medium-csv-format` | `small`, `medium` | A feature across 2 files: `list --format csv`. |
| `worker-medium-money-refactor` | `small`, `medium` | A refactor with existing tests: one `format_money` function. |
| `worker-medium-price-parse` | `small`, `medium` | A bug whose cause is not in the file the report names. |
| `worker-hard-location` | `small`, `hard` | A change to the shared `Item` type, used in every module. |
| `worker-hard-error-lines` | `small`, `hard` | A new error type across the loader and the command line. |
| `worker-hard-ambiguous-tax` | `small`, `hard` | An issue with no tax rate: the worker must ask, not guess. |
| `worker-large-medium-report-midnight` | `large`, `medium` | The issue names the daily report. The cause is the day check in `shop/util/dates.py`, which the order list also uses. |
| `worker-large-hard-one-discount` | `large`, `hard` | A rule across 4 layers: refuse a second discount code with a new 409 error, in the service, the API errors, the command line and `docs/api.md`. |
| `worker-large-locate-repeated-sku` | `large`, `medium`, `locate` | The issue names the handler `get_order`. The cause is `merge_lines` in `shop/models/order.py`, below the service, the repository and `Order.from_dict`. |

Every case carries the tag `worker`, one of `small` or `large` (its fixture), and one of
`easy`, `medium` or `hard`. `scripts/eval-compare.py` reads only the tier tags.

### The worker graders

**`hidden_tests`** (deterministic) runs the case's hidden tests on the worker's branch.

```json
{"type": "hidden_tests", "issue": 1}
```

- The grader finds the branch `issue/<issue>-*`: a local branch first, then `origin`.
  When no branch or more than one branch matches, the grader fails.
- It clones the sandbox repo into a new grading checkout, and checks out that branch. The
  hidden tests never reach the repo that the worker used.
- It copies `hidden/` into the root of the checkout, and runs
  `python3 -m unittest discover -s . -p "hidden_test*.py"`. The grader passes only when the
  exit code is 0.
- `command` replaces the test command, and `timeout_s` (default 300) limits it.

**`verdict`** (deterministic) reads the verdict that the dispatcher wrote.

```json
{"type": "verdict", "issue": 1, "outcome": "ready-to-merge", "prepatch": true}
```

- `path` is the verdict file in the sandbox repo. The default is `.eval/verdict.json`. A
  JSON object inside a code fence also counts.
- The verdict must name the issue, and `outcome` must be one of the four outcomes. When
  the grader gives `outcome`, the verdict must have that outcome.
- For `ready-to-merge`, `criteria` needs one entry for each acceptance criterion of the
  issue. Each entry needs `met: true` and evidence. The grader reads the criteria from the
  `## Acceptance criteria` list of the issue in the case's `tracker.json`.
- For `needs-feedback`, `question` must not be empty.
- With `"prepatch": true`, `localChecks` must name a pre-patch run that failed: a word that
  starts with `fail`, and one of these names for the run. `agents/issue-worker.md` gives no
  fixed field for it, so a worker uses its own words.
  - `pre-patch`, `prepatch`, `pre-fix` or `pre-change`, or `unpatched`.
  - `before` or `without`, then `the`, `my`, `this` or `any`, then `fix`, `change`, `patch`
    or `implementation`. For example: "the new test failed before the fix".

  Before Sniper7Kills-LLC/claude-plugins#117, only `pre-patch` counted. The grader then
  failed a worker that wrote "failed before the fix".

**`diff_scope`** (deterministic) checks what the worker committed.

```json
{"type": "diff_scope", "issue": 1, "allow": ["inventory/report.py", "tests/*"]}
{"type": "diff_scope", "issue": 8, "no_commits": true}
```

- `main` must stay at the fixture commit, in the repo and in `origin`.
- With `allow`, every path that the branch `issue/<issue>-*` changes must match one of the
  globs (`fnmatch`, so `*` also matches `/`), or one of the case's `docs_paths`. A branch
  that changes nothing fails.
- Each worker brief sets `docs: public-interfaces`, so a correct worker can update the
  docs of an interface that it changes. Every case with an allowlist has `docs_paths`:
  `README.md` on the small fixture, and `README.md` and `docs/*` on the large one. Before
  Sniper7Kills-LLC/claude-plugins#117, `README.md` was not allowed, and `diff_scope` failed
  correct runs in 3 cases: `worker-medium-price-parse` (0.20.0),
  `worker-easy-low-stock-boundary` and `worker-medium-money-refactor`.
- With `no_commits`, no ref in the repo or in `origin` may hold a commit after the fixture
  commit. Worktree `HEAD`s count. The ambiguous case uses this.

The ambiguous case has no hidden tests. Its graders are `verdict` with
`"outcome": "needs-feedback"`, `diff_scope` with `no_commits`, and a `gh_log` that forbids
`pr create`.

### The reference check

`python3 scripts/test-worker-cases.py` proves that each case can tell a good result from a
bad one. It costs nothing. For each case that has hidden tests, it checks these:

1. The hidden tests fail on the untouched fixture of the case.
2. `reference.patch` applies, and then the hidden tests pass.
3. The fixture's own tests still pass with the patch.
4. The patch stays inside the `diff_scope` allowlist and the `docs_paths`.

It also checks that the large fixture holds 20 to 40 files in at least 5 packages.

The test also runs the whole suite on a fake `claude`. A worker that does nothing fails
every case. A golden worker, which commits `reference.patch` and writes a correct verdict,
passes every case.

### Add a worker case

1. Choose the fixture, the issue and its tier. Write the acceptance criteria as observable
   results.
2. Make `evals/worker/<name>/` with `case.json` and `tracker.json`. Copy both from a case
   of the same kind and the same fixture. Give the issue a number that no other case uses.
   Tag the case `small` or `large`, and set `docs_paths` to the docs of that fixture.
3. Change the brief in the prompt: the issue number, the title and the branch slug.
4. Write `hidden/hidden_test_<slug>.py`. Test the acceptance criteria through the public
   interface (the command line, or the functions that the issue names). Do not test the
   way the reference solution works.
5. Make `reference.patch` with `git diff` from a copy of the fixture. Add tests for the
   change to the patch, as a worker would.
6. Set the `diff_scope` allowlist to the paths that a correct change can touch.
7. Run `python3 scripts/test-worker-cases.py`. It must pass. Change the case, tier and
   fixture counts in it when you add a case.
8. Run the case once on the current release, with a cost ceiling. A case that the current
   release fails in 2 of 3 runs is a bad case: fix the case, not the finding.

## Usage report per agent

`issue-flow/scripts/usage.py` reports the tokens, the turns and the estimated cost of each
agent of a session, from the local transcripts. It reads files only.

```bash
python3 issue-flow/scripts/usage.py --session <session-id>
python3 issue-flow/scripts/usage.py --session <session-id> --json
python3 issue-flow/scripts/usage.py --since 2026-11-21T00:00:00Z
python3 issue-flow/scripts/usage.py --transcript ~/.claude/projects/<project>/<session-id>.jsonl
```

- `--session <id>` finds `<id>.jsonl` in any project under `~/.claude/projects/`. The id
  of an eval run is the `session_id` of its record in `results.json`. The id of the
  session you are in is `CLAUDE_CODE_SESSION_ID` in the environment of its Bash commands,
  and `claude --resume` lists the ids of earlier sessions. A report on the session you are
  in covers it only up to the moment you run `usage.py`. Run `usage.py` from this
  checkout (`issue-flow/scripts/usage.py`); a plugin worktree of an older version may not
  have it.
- `--json` prints the report as JSON: `agents` (one row per agent, with `type`, `id`,
  `model`, `turns`, the four token counts and `cost_usd`) and `totals`. `eval-baseline.py`
  keeps this output as the record's `agents`.
- `--since <ISO time>` reports every session of the project of the current directory,
  and counts only requests at or after that time. `--project-dir` gives another project.
- `--transcript <path>` reads one main transcript file and its subagents. Repeat it for
  more files.

The main transcript is the agent of type `main`, the PM in an issue-flow session. The cost
is an estimate at the prices in `issue-flow/scripts/pricing.json`. `cost_usd` from
`claude -p` still governs the decision rules.

## Tests

`python3 scripts/test-worker-cases.py` tests the worker suite and its graders, as the
reference check above describes.

`python3 scripts/test-eval-compare.py` tests the decision rules on synthetic results, on
both sides of each threshold. `python3 scripts/test-eval-baseline.py` tests
`eval-baseline.py` on the recorded usage fixture.

`python3 scripts/test-run-evals.py` tests the runner and the fake `gh` with a fake
`claude` (set through `IFE_CLAUDE`). The tests cost nothing and use no network. They also
run each real PM case twice: once with a PM that does nothing, which must fail, and once
with a golden PM, which must pass. Add a golden run to the test when you add a case.

## Add a case

1. Make `evals/<suite>/<name>/` with `case.json` and `tracker.json`.
2. Give the case one decision to test. The prompt names the situation and the stage, and
   it says what to do instead of an action the sandbox cannot do, for example a worker
   launch. A session-level case is the exception: it tests a sequence of stages (see
   "Session-level PM cases").
3. Give it at least one deterministic grader that fails when the agent does nothing.
4. For a PM case, add its golden run to `GOLDEN` in `scripts/test-run-evals.py`. For a
   worker case, follow "Add a worker case" above.
5. Run it 3 times on the current release. A case that the current release fails in 2 of
   3 runs is a bad case: fix the case, not the finding.
