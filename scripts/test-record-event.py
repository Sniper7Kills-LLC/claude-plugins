#!/usr/bin/env python3
"""Tests for issue-flow/hooks/record-event.py.

    python3 scripts/test-record-event.py

Runs the hook as Claude Code runs it: a subprocess fed a hook payload on stdin. The
payload fields follow https://code.claude.com/docs/en/hooks (PostToolUse, SubagentStop).
Two properties matter as much as the log line: the hook never prints anything and never
exits non-zero, because it must not block or change a tool call.
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "issue-flow", "hooks", "record-event.py")

failures = []


def check(cond, message):
    if not cond:
        failures.append(message)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                   check=True, capture_output=True, text=True)


def run(payload, env=None):
    """Run the hook and require exit 0 with no stdout."""
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_EVENTS", None)
    environment.update(env or {})
    result = subprocess.run([sys.executable, HOOK],
                            input=payload if isinstance(payload, str) else json.dumps(payload),
                            capture_output=True, text=True, env=environment)
    check(result.returncode == 0, f"hook exited {result.returncode}: {result.stderr.strip()}")
    check(result.stdout == "", f"hook printed to stdout: {result.stdout[:120]!r}")
    return result


def events(repo):
    path = os.path.join(repo, ".git", "issue-flow", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        rows = []
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # a junk line is part of one test case
        return rows


BRIEF = "issue:        #77 — record events\nbranch: issue/77-x\nSECRET-PROMPT-TEXT do the work"


def spawn_payload(cwd, brief=BRIEF):
    return {"session_id": "sess-1", "cwd": cwd, "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "issue-flow:issue-worker", "isolation": "worktree",
                           "prompt": brief},
            "tool_response": {"status": "async_launched", "agentId": "a123",
                              "prompt": brief, "outputFile": "/x"}}


def stop_payload(cwd, message, agent_type="issue-flow:issue-worker", agent_id="a123"):
    return {"session_id": "sess-1", "cwd": cwd, "hook_event_name": "SubagentStop",
            "agent_id": agent_id, "agent_type": agent_type, "last_assistant_message": message}


def bash_payload(cwd, command):
    return {"session_id": "sess-1", "cwd": cwd, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": "SECRET-OUTPUT", "stderr": "", "interrupted": False}}


with tempfile.TemporaryDirectory() as tmp:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")

    # 1. A worker spawn writes one agent.spawned line.
    run(spawn_payload(repo))
    got = events(repo)
    check(len(got) == 1, f"spawn writes one line, got {len(got)}")
    if got:
        e = got[0]
        check(e["kind"] == "agent.spawned", f"spawn kind: {e}")
        check(e["issue"] == 77 and e["agent"] == "a123" and e["session"] == "sess-1", f"spawn fields: {e}")
        check(e["ts"].endswith("Z") and "T" in e["ts"], f"ts is ISO UTC: {e['ts']}")
        for key in ("worktree", "branch", "outcome"):
            check(key in e, f"spawn has key {key}")

    # 2. A spawn whose brief names no issue is not a worker spawn: nothing written.
    run(spawn_payload(repo, brief="research the codebase"))
    check(len(events(repo)) == 1, "a spawn without an issue line writes nothing")

    # 3. A stop with a verdict records outcome, branch and worktree.
    verdict = {"issue": 77, "branch": "issue/77-x", "worktree": "/w/agent-1", "outcome": "ready-to-merge"}
    run(stop_payload(repo, "all done\n```json\n" + json.dumps(verdict) + "\n```"))
    got = events(repo)
    check(len(got) == 2 and got[1]["kind"] == "agent.stopped", f"stop kind: {got}")
    if len(got) == 2:
        e = got[1]
        check(e["outcome"] == "ready-to-merge" and e["issue"] == 77, f"stop outcome/issue: {e}")
        check(e["branch"] == "issue/77-x" and e["worktree"] == "/w/agent-1", f"stop branch/worktree: {e}")
        check(e["agent"] == "a123", f"stop agent: {e}")

    # 4. A stop from an agent that is not a worker and has no verdict writes nothing.
    run(stop_payload(repo, "just a summary", agent_type="Explore", agent_id="a999"))
    check(len(events(repo)) == 2, "a non-worker stop of an unspawned agent writes nothing")

    # 5. Worktree add and remove from Bash.
    run(bash_payload(repo, "git worktree add -b issue/77-x /w/wt-77 origin/dev"))
    run(bash_payload(repo, "git worktree remove --force /w/wt-77"))
    run(bash_payload(repo, "git status"))
    got = events(repo)
    check([e["kind"] for e in got[2:]] == ["worktree.added", "worktree.removed"], f"worktree kinds: {got[2:]}")
    if len(got) == 4:
        check(got[2]["worktree"] == "/w/wt-77" and got[2]["branch"] == "issue/77-x", f"add fields: {got[2]}")
        check(got[3]["worktree"] == "/w/wt-77", f"remove fields: {got[3]}")

    # 6. No prompt text, brief body or tool output reaches the log.
    with open(os.path.join(repo, ".git", "issue-flow", "events.jsonl")) as handle:
        text = handle.read()
    check("SECRET-PROMPT-TEXT" not in text and "SECRET-OUTPUT" not in text and "do the work" not in text,
          "the log holds no prompt text or tool output")

    # 7. The kill switch writes nothing.
    before = len(events(repo))
    run(spawn_payload(repo), env={"ISSUE_FLOW_EVENTS": "off"})
    check(len(events(repo)) == before, "ISSUE_FLOW_EVENTS=off writes nothing")

    # 8. A hook run from a linked worktree writes to the common dir.
    linked = os.path.join(tmp, "linked")
    git(repo, "worktree", "add", "-q", "-b", "side", linked)
    before = len(events(repo))
    run(spawn_payload(linked))
    check(len(events(repo)) == before + 1, "a linked worktree writes to the shared common dir")
    check(not os.path.exists(os.path.join(linked, ".git", "issue-flow")), "no per-worktree log")

    # 9. Fail open: garbage, wrong shapes, no git dir, an unwritable path.
    run("not json at all")
    run("[1, 2]")
    run("")
    run({"tool_name": "Agent", "tool_input": "nope", "cwd": repo})
    run({"hook_event_name": "SubagentStop", "last_assistant_message": 5, "cwd": repo})
    nogit = os.path.join(tmp, "nogit")
    os.makedirs(nogit)
    run(spawn_payload(nogit))
    run(spawn_payload(os.path.join(tmp, "does-not-exist")))

    blocked = os.path.join(tmp, "blocked")
    os.makedirs(blocked)
    git(blocked, "init", "-q")
    with open(os.path.join(blocked, ".git", "issue-flow"), "w") as handle:
        handle.write("a file where the directory belongs")
    run(spawn_payload(blocked))

    # 10. SendMessage to an agent records the agent id and the time, never the message text.
    def send_payload(cwd, **tool_input):
        return {"session_id": "sess-1", "cwd": cwd, "hook_event_name": "PostToolUse", "tool_name": "SendMessage",
                "tool_input": tool_input, "tool_response": {"success": True, "SECRET-REPLY": "x"}}

    before = len(events(repo))
    run(send_payload(repo, to="a123", message="SECRET-MESSAGE-TEXT fix the legend", summary="SECRET-SUMMARY"))
    got = events(repo)
    check(len(got) == before + 1 and got[-1]["kind"] == "agent.messaged", f"SendMessage writes agent.messaged: {got[-1:]}")
    if len(got) == before + 1:
        e = got[-1]
        check(e["agent"] == "a123" and e["session"] == "sess-1" and e["ts"].endswith("Z"), f"messaged fields: {e}")
        for key in ("issue", "worktree", "branch", "outcome"):
            check(key in e and e[key] is None, f"messaged has null {key}")
    with open(os.path.join(repo, ".git", "issue-flow", "events.jsonl")) as handle:
        text = handle.read()
    check("SECRET-MESSAGE-TEXT" not in text and "SECRET-SUMMARY" not in text and "SECRET-REPLY" not in text and "fix the legend" not in text,
          "the log holds no message text, summary or reply")
    before = len(events(repo))
    run(send_payload(repo, message="no recipient"))
    run(send_payload(repo, to=["a", "b"], message="list recipient"))
    run({"hook_event_name": "PostToolUse", "tool_name": "SendMessage", "tool_input": "nope", "cwd": repo})
    check(len(events(repo)) == before, "a SendMessage with no single string recipient writes nothing")
    run(send_payload(repo, to="a123", message="x"), env={"ISSUE_FLOW_EVENTS": "off"})
    check(len(events(repo)) == before, "the kill switch covers agent.messaged")

    # 11. Fields from a real Claude Code 2.1.329 SubagentStop and PostToolUse(Agent) payload.
    live = os.path.join(tmp, "live")
    os.makedirs(live)
    git(live, "init", "-q")
    git(live, "commit", "-q", "--allow-empty", "-m", "init")
    agent_wt = os.path.join(tmp, "worktrees", "agent-ae15")
    git(live, "worktree", "add", "-q", "-b", "agent-branch", agent_wt)
    live_spawn = {"session_id": "s-live", "transcript_path": "/neutral/t.jsonl", "cwd": live,
                  "hook_event_name": "PostToolUse", "tool_name": "Agent",
                  "tool_input": {"description": "probe", "subagent_type": "general-purpose",
                                 "run_in_background": True, "isolation": "worktree",
                                 "prompt": "issue: #86 - probe\nDo the work."},
                  "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "ae15",
                                    "description": "probe", "outputFile": "/neutral/o"}}

    def live_stop(message, agent_id="ae15", cwd=agent_wt, agent_type="general-purpose"):
        return {"session_id": "s-live", "transcript_path": "/neutral/t.jsonl", "cwd": cwd,
                "hook_event_name": "SubagentStop", "stop_hook_active": False, "agent_id": agent_id,
                "agent_type": agent_type, "agent_transcript_path": "/neutral/a.jsonl",
                "last_assistant_message": message, "background_tasks": [], "session_crons": []}

    run(live_spawn)
    # A spawned agent that returns no verdict still gets a stop line, with outcome null and its own worktree.
    run(live_stop("I could not finish: the build failed."))
    got = events(live)
    check([e["kind"] for e in got] == ["agent.spawned", "agent.stopped"], f"spawned agent without verdict stops: {got}")
    if len(got) == 2:
        e = got[1]
        check(e["agent"] == "ae15" and e["outcome"] is None and e["issue"] is None, f"unverdicted stop fields: {e}")
        check(e["worktree"] == agent_wt, f"stop worktree falls back to the cwd named agent-<agent_id>: {e}")
    # A verdict's own worktree wins over the cwd.
    run(live_spawn | {"tool_response": {"agentId": "ae16", "status": "async_launched"}})
    run(live_stop(json.dumps({"issue": 86, "outcome": "blocked", "worktree": "/w/own"}), agent_id="ae16"))
    check(events(live)[-1]["worktree"] == "/w/own", f"verdict worktree beats cwd: {events(live)[-1:]}")
    # An agent the log never saw spawn, with no verdict and no worker type, still writes nothing.
    before = len(events(live))
    run(live_stop("summary", agent_id="unknown-agent"))
    check(len(events(live)) == before, "a stop with no spawn line, no verdict and no worker type writes nothing")
    # A worker stop without a verdict records the cwd as its worktree too.
    run(live_stop("crashed", agent_id="w9", agent_type="issue-flow:issue-worker"))
    check(events(live)[-1]["worktree"] is None,
          f"a cwd that is not agent-<agent_id> is not recorded as the worktree: {events(live)[-1:]}")
    # A resumed agent's stop reports the parent session's directory, which is not the agent's worktree.
    run(live_stop("resumed", agent_id="ae15", cwd=live))
    check(events(live)[-1]["worktree"] is None, f"a resumed stop with the parent cwd has no worktree: {events(live)[-1:]}")
    run(live_stop("done", agent_id="ae15"))
    check(events(live)[-1]["worktree"] == agent_wt, f"a cwd named agent-<agent_id> is the worktree: {events(live)[-1:]}")
    # An unreadable log line never stops the lookup.
    with open(os.path.join(live, ".git", "issue-flow", "events.jsonl"), "a") as handle:
        handle.write("garbage line\n")
    run(live_spawn | {"tool_response": {"agentId": "ae17", "status": "async_launched"}})
    run(live_stop("oops", agent_id="ae17"))
    check(events(live)[-1]["agent"] == "ae17" and events(live)[-1]["kind"] == "agent.stopped",
          "stop is recorded after a junk log line")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("record-event: all cases pass")
