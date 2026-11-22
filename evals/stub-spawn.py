#!/usr/bin/env python3
"""PreToolUse hook of an eval run: record each `Agent` call, then refuse it.

The runner adds this hook through `--settings` to a case with `stub_spawns: true`
(evals/README.md, "The spawn stub"). A PM case can then call `Agent` the way the
skill says, and the `spawns` grader reads what the PM asked for: the agent type,
the model, the brief. No agent runs, so the run costs nothing more and stays in
its sandbox.

Each call goes to `$IFE_SPAWN_LOG` as one JSON line: the `tool_input` fields,
plus `seq` and `tool_name`. The hook refuses the call even when it cannot write
the log, because a spawn that runs would leave the sandbox's limits.
"""

import json
import os
import sys

REASON = (
    "eval spawn stub: this test recorded the Agent call and did not run it. No agent "
    "runs in this test, and no completion notification will arrive. Treat the spawn as "
    "launched, do not call Agent again for the same purpose, and go on with the task."
)


def record(payload):
    path = os.environ.get("IFE_SPAWN_LOG")
    if not path:
        return
    tool_input = payload.get("tool_input")
    entry = dict(tool_input) if isinstance(tool_input, dict) else {"tool_input": tool_input}
    entry["tool_name"] = payload.get("tool_name")
    seq = 1
    if os.path.exists(path):
        with open(path) as handle:
            seq += sum(1 for line in handle if line.strip())
    entry["seq"] = seq
    with open(path, "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def main():
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            record(payload)
    except Exception as error:  # refuse the spawn anyway; see the module docstring
        print(f"stub-spawn: cannot record the call: {error!r}", file=sys.stderr)
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": REASON}}, sys.stdout)


if __name__ == "__main__":
    main()
