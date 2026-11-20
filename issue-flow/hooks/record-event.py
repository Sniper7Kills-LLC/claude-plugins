#!/usr/bin/env python3
"""PostToolUse and SubagentStop recorder: log local orchestration events for the timeline.

Wired by `issue-flow/hooks/hooks.json`. The tracker shows what the PM did to issues and
PRs. It does not show worker spawns, worker verdicts or worktree changes, which exist only
on this machine. This hook appends them as JSON lines to
`<git common dir>/issue-flow/events.jsonl`. Every linked worktree shares that file, and git
never tracks it. `timeline.py collect` merges the file into the timeline document.

Events (kinds are documented in references/timeline.md):

* `PostToolUse` on `Agent|Task` -> `agent.spawned`, when the brief carries an `issue: #<n>`
  line. Other spawns (research children, reviewers) are not worker events.
* `SubagentStop` -> `agent.stopped`, when the final message carries the verdict JSON, the
  agent type is an issue-worker, or the log already holds an `agent.spawned` line for the
  agent. The last case gives a crashed or fallback worker a lane end. `outcome`, `branch` and
  `worktree` come from the verdict. Without a verdict `worktree` is the `cwd` of the stop event,
  but only when its last path component is `agent-<agent_id>`, the harness's name for an agent
  worktree. A resumed agent's stop reports the parent session's directory, which is not its worktree.
* `PostToolUse` on `SendMessage` -> `agent.messaged`, when the PM messages an agent (a worker
  sent back for rework, for example). The line holds the recipient and the time. It never holds
  the message text, the summary or the reply.
* `PostToolUse` on `Bash` -> `worktree.added` / `worktree.removed`, for a `git worktree add`
  or `git worktree remove` command.

Each line holds only: ts, kind, session, agent, issue, worktree, branch, outcome. It never
holds a prompt, a brief body or tool output.

The hook observes. It never blocks, denies or changes a tool call: it always exits 0 and
prints nothing. Any error is swallowed. `ISSUE_FLOW_EVENTS=off` turns it off. Hooks read the
environment as it was at session start, so set the switch in the project's
`.claude/settings.json` and start a new session.

Hook events were checked against https://code.claude.com/docs/en/hooks. The `SendMessage` tool
is described at https://code.claude.com/docs/en/tools-reference and
https://code.claude.com/docs/en/cross-session-messaging. Those pages do not name the recipient
parameter, so `messaged` reads `to` first, then the other names a recipient could have. A call with
no single string recipient writes nothing. This hook does not
use `WorktreeCreate`: that event replaces the harness's worktree creation.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

SPAWN_TOOLS = ("Agent", "Task")
ISSUE_LINE = re.compile(r"^\s*issue:\s*#(\d+)", re.M)
KEYS = ("ts", "kind", "session", "agent", "issue", "worktree", "branch", "outcome")


def text(value):
    return value if isinstance(value, str) and value else None


def event(kind, payload, **fields):
    row = dict.fromkeys(KEYS)
    row["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["kind"] = kind
    row["session"] = text(payload.get("session_id"))
    row.update(fields)
    return row


def spawned(payload):
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    match = ISSUE_LINE.search(tool_input.get("prompt") or "")
    if not match:
        return None
    response = payload.get("tool_response")
    agent = text(response.get("agentId")) if isinstance(response, dict) else None
    return event("agent.spawned", payload, agent=agent, issue=int(match.group(1)))


RECIPIENT_KEYS = ("to", "recipient", "agent_id", "agentId", "target")


def messaged(payload):
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in RECIPIENT_KEYS:
        if text(tool_input.get(key)):
            return event("agent.messaged", payload, agent=tool_input[key])
    return None


def verdict_of(message):
    """Return the last JSON object in `message` that has an `outcome` key, or None."""
    decoder = json.JSONDecoder()
    found = None
    for start in (m.start() for m in re.finditer(r"\{", message)):
        try:
            value, _ = decoder.raw_decode(message[start:])
        except ValueError:
            continue
        if isinstance(value, dict) and "outcome" in value:
            found = value
    return found


SPAWN_SCAN_BYTES = 2 * 1024 * 1024


def was_spawned(path, agent):
    """Return True when the log's last 2 MiB hold an `agent.spawned` line for `agent`."""
    if not path or not agent:
        return False
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - SPAWN_SCAN_BYTES))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return False
    for line in data.splitlines():
        if "agent.spawned" not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "agent.spawned" and row.get("agent") == agent:
            return True
    return False


def agent_worktree(payload, agent):
    """Return the stop event's cwd when it is the agent's own worktree, else None."""
    cwd = text(payload.get("cwd"))
    if cwd and agent and os.path.basename(cwd.rstrip("/\\")) == f"agent-{agent}":
        return cwd
    return None


def stopped(payload, path=None):
    message = payload.get("last_assistant_message")
    verdict = verdict_of(message) if isinstance(message, str) else None
    agent_type = payload.get("agent_type")
    is_worker = isinstance(agent_type, str) and agent_type.endswith("issue-worker")
    agent = text(payload.get("agent_id"))
    if not verdict and not is_worker and not was_spawned(path, agent):
        return None
    verdict = verdict or {}
    issue = verdict.get("issue")
    return event("agent.stopped", payload, agent=agent,
                 issue=issue if isinstance(issue, int) else None,
                 worktree=text(verdict.get("worktree")) or agent_worktree(payload, agent), branch=text(verdict.get("branch")),
                 outcome=text(verdict.get("outcome")))


def worktree_change(payload):
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or "worktree" not in command:
        return None
    for part in re.split(r"&&|\|\||;|\n", command):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if words[:1] != ["git"]:
            continue
        words = words[1:]
        while words[:1] == ["-C"]:
            words = words[2:]
        if words[:2] not in (["worktree", "add"], ["worktree", "remove"]):
            continue
        kind = "worktree.added" if words[1] == "add" else "worktree.removed"
        branch, positional, skip = None, [], False
        rest = words[2:]
        for i, word in enumerate(rest):
            if skip:
                skip = False
            elif word in ("-b", "-B") and i + 1 < len(rest):
                branch, skip = rest[i + 1], True
            elif not word.startswith("-"):
                positional.append(word)
        if not positional:
            return None
        return event(kind, payload, worktree=positional[0], branch=branch)
    return None


def build(payload, path=None):
    name = payload.get("hook_event_name")
    if name == "SubagentStop":
        return stopped(payload, path)
    if name == "PostToolUse":
        tool = payload.get("tool_name")
        if tool in SPAWN_TOOLS:
            return spawned(payload)
        if tool == "SendMessage":
            return messaged(payload)
        if tool == "Bash":
            return worktree_change(payload)
    return None


def log_path(cwd):
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=cwd, capture_output=True,
                            text=True, timeout=5)
    if result.returncode != 0:
        return None
    base = os.path.join(cwd, result.stdout.strip())
    return os.path.join(os.path.abspath(base), "issue-flow", "events.jsonl")


def main():
    if os.environ.get("ISSUE_FLOW_EVENTS", "").strip().lower() == "off":
        return
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
        path = log_path(cwd)
        if not path:
            return
        row = build(payload, path)
        if not row:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # One write call per line keeps concurrent appends from interleaving.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # fail open: a recorder must never be the reason a tool call fails
        return


if __name__ == "__main__":
    main()
