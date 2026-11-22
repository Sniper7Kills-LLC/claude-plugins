#!/usr/bin/env python3
"""Tests for issue-flow/hooks/guard-spawn.py.

    python3 scripts/test-guard-spawn.py

Runs the hook as Claude Code runs it — a subprocess fed a PreToolUse payload on
stdin — and checks the decision. The payload shapes are the ones measured in
issue #25. Two properties matter as much as the blocking itself: the guard must
never block a spawn that would have worked, and it must fail open on anything it
does not understand.
"""

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "issue-flow", "hooks", "guard-spawn.py")

failures = []


def run(payload, env=None):
    """Return the parsed hook output, or None when it allowed the call."""
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_SPAWN_GUARD", None)
    environment.pop("ISSUE_FLOW_FORK_GUARD", None)
    environment.pop("ISSUE_FLOW_MODEL_GUARD", None)
    environment.update(env or {})
    result = subprocess.run(
        [sys.executable, HOOK],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode != 0:
        failures.append(f"hook exited {result.returncode}: {result.stderr.strip()}")
        return None
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        failures.append(f"hook emitted non-JSON: {result.stdout[:120]!r}")
        return None


def denied(output):
    return bool(output) and output.get("hookSpecificOutput", {}).get(
        "permissionDecision"
    ) == "deny"


def expect(name, payload, should_deny, env=None):
    output = run(payload, env)
    if denied(output) != should_deny:
        verb = "deny" if should_deny else "allow"
        failures.append(f"{name}: expected the guard to {verb}, got {output}")
    return output


def spawn(**tool_input):
    return {"tool_name": "Agent", "tool_input": tool_input}


# --- the shape that strands the caller ---------------------------------------
output = expect(
    "named spawn without isolation is denied",
    spawn(name="reviewer", subagent_type="general-purpose", prompt="review the diff"),
    True,
)
if output:
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    for expected in ("Drop `name`", 'isolation: "worktree"', "ISSUE_FLOW_SPAWN_GUARD"):
        if expected not in reason:
            failures.append(f"the deny reason should offer {expected!r}: {reason[:200]}")
    if output["hookSpecificOutput"].get("hookEventName") != "PreToolUse":
        failures.append("the decision must name its hook event")

# --- shapes that must pass untouched -----------------------------------------
expect(
    "named spawn with isolation is allowed",
    spawn(name="worker-42", isolation="worktree", subagent_type="issue-flow:issue-worker"),
    False,
)
expect("unnamed spawn is allowed", spawn(subagent_type="Explore", prompt="locate"), False)
expect("unnamed isolated spawn is allowed", spawn(isolation="worktree", prompt="x"), False)
expect("an empty name is not a name", spawn(name="   ", prompt="x"), False)
expect(
    "a Bash call is none of the guard's business",
    {"tool_name": "Bash", "tool_input": {"command": "gh pr checks 21", "run_in_background": True}},
    False,
)
expect(
    "run_in_background alone is not blocked",
    spawn(subagent_type="general-purpose", run_in_background=True, prompt="x"),
    False,
)

# --- the legacy tool name is covered too --------------------------------------
expect(
    "a legacy Task spawn is guarded the same way",
    {"tool_name": "Task", "tool_input": {"name": "reviewer", "prompt": "x"}},
    True,
)

# --- review round 1: the message must not mislead -----------------------------
output = run(spawn(name="reviewer", prompt="x"))
reason = output["hookSpecificOutput"]["permissionDecisionReason"] if output else ""
if "Drop `name`" not in reason:
    failures.append("dropping the name must be the primary remedy, not the second option")
if "commits never reach your branch" not in reason:
    failures.append(
        "the message must warn that adding isolation to a worker's child strands its commits"
    )
if "settings.json" not in reason or "start a new session" not in reason:
    failures.append(
        "the escape must say it is read at session start, not exported from a tool call"
    )

# --- ask mode -----------------------------------------------------------------
asked = run(spawn(name="reviewer", prompt="x"), env={"ISSUE_FLOW_SPAWN_GUARD": "ask"})
if not asked or asked["hookSpecificOutput"].get("permissionDecision") != "ask":
    failures.append(f"ask mode should ask, not deny: {asked}")
denied_default = run(spawn(name="reviewer", prompt="x"))
if not denied(denied_default):
    failures.append("the default mode is deny")
for value in ("DENY", "  deny  ", "anything-else"):
    if not denied(run(spawn(name="r", prompt="x"), env={"ISSUE_FLOW_SPAWN_GUARD": value})):
        failures.append(f"an unrecognized setting {value!r} must fall back to deny")
for value in ("off", "OFF", "0", "false", "no"):
    if run(spawn(name="r", prompt="x"), env={"ISSUE_FLOW_SPAWN_GUARD": value}) is not None:
        failures.append(f"{value!r} should disable the guard")

# --- the escape hatch ---------------------------------------------------------
expect(
    "ISSUE_FLOW_SPAWN_GUARD=off disables the guard",
    spawn(name="reviewer", prompt="x"),
    False,
    env={"ISSUE_FLOW_SPAWN_GUARD": "off"},
)

# --- fail open: a guard must never be the reason work cannot start ------------
expect("malformed JSON allows the call", "{not json", False)
expect("an empty payload allows the call", {}, False)
expect("a null tool_input allows the call", {"tool_name": "Agent", "tool_input": None}, False)
expect("a string tool_input allows the call", {"tool_name": "Agent", "tool_input": "x"}, False)
expect("empty stdin allows the call", "", False)

# --- fork dispatch of a build (issue #50) -------------------------------------
TEMPLATE = os.path.join(
    ROOT, "issue-flow", "skills", "issue-flow", "references", "issue-worker.md"
)


def handoff_brief():
    """The real handoff-brief template, so a template change cannot blind the guard."""
    text = open(TEMPLATE, encoding="utf-8").read()
    start = text.index("## Handoff brief")
    block = text[text.index("```", start) + 3 :]
    return block[block.index("\n") + 1 : block.index("```")]


BRIEF = handoff_brief()
if "crossCheck:" not in BRIEF or "base:" not in BRIEF:
    failures.append(f"could not extract the handoff brief from {TEMPLATE}")


def fork(prompt, **extra):
    return spawn(subagent_type="fork", description="build #42", prompt=prompt, **extra)


output = expect("a fork carrying the handoff brief is denied", fork(BRIEF), True)
if output:
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    for expected in ("issue-flow:issue-worker", "ISSUE_FLOW_FORK_GUARD", "`crossCheck:`"):
        if expected not in reason:
            failures.append(f"the fork deny reason should mention {expected!r}: {reason[:200]}")
expect(
    "a hand-written brief with list and bold markup is denied",
    fork("Build issue #42.\n- **base:** origin/epic/7-x\n- **ci:** skip\n- **members:** 3\n"),
    True,
)
expect("the fork type is matched case-insensitively", spawn(subagent_type=" Fork ", prompt=BRIEF), True)
expect("a plain fork is allowed", fork("Summarize the diff and stop."), False)
expect(
    "a fork describing a failing build is not a brief",
    fork("Issue: #12 keeps failing.\nBranch: fix/x\nCI: red on lint\nFind out why."),
    False,
)
expect(
    "two issue-flow labels alone do not make a brief",
    fork("crossCheck: see above\nmembers: 2\nNow explain the batch model."),
    False,
)
expect(
    "the brief sent to issue-worker is the correct dispatch",
    spawn(
        subagent_type="issue-flow:issue-worker",
        name="worker-42",
        isolation="worktree",
        prompt=BRIEF,
    ),
    False,
)
expect(
    "the general-purpose fallback carrying the brief is allowed",
    spawn(subagent_type="general-purpose", prompt=BRIEF),
    False,
)
expect(
    "labels mid-line are not brief fields",
    fork("Note the issue: base: ci: members: are all mentioned inline here."),
    False,
)

# review round 1: the ways a model re-lays the template out are still a brief
for label, prompt in {
    "a numbered list": "1. issue: #42\n2. base: origin/dev\n3. crossCheck: n/a\n",
    "markdown headings": "## Issue: #42\n## Base: origin/dev\n## Members: 1\n",
    "a table": "| field | value |\n|---|---|\n| issue | #42 |\n| base | dev |\n| members | 1 |\n",
    "pretty-printed JSON": '{\n  "issue": 42,\n  "base": "origin/dev",\n  "crossCheck": "n/a"\n}',
    "= separators": "issue = #42\nbase = origin/dev\nsteRule = x\n",
    "a spelled-out cross-check": "Issue: #42\nBase: origin/dev\nCross-check: n/a\n",
    "a space before the colon": "issue : #42\nbase : dev\nforge : {type: gitea}\n",
    "capitalized labels only": "Issue: #42\nBase: dev\nCrossCheck: n/a\n",
}.items():
    expect(f"a brief laid out as {label} is denied", fork(prompt), True)

# a named, un-isolated fork trips both checks; the fork reason must win
output = run(fork(BRIEF, name="worker-42"))
reason = output["hookSpecificOutput"]["permissionDecisionReason"] if output else ""
if "issue-flow:issue-worker" not in reason:
    failures.append("a named build fork must be refused for the fork, not the name")

# known, documented false positive: a fork asked to discuss the brief's fields
expect(
    "a fork quoting the brief one field per line is denied (documented)",
    fork("Explain these fields:\n- issue:\n- base:\n- members:\n"),
    True,
)

# --- the two checks have separate switches ------------------------------------
expect(
    "ISSUE_FLOW_FORK_GUARD=off allows a build fork",
    fork(BRIEF),
    False,
    env={"ISSUE_FLOW_FORK_GUARD": "off"},
)
expect(
    "ISSUE_FLOW_FORK_GUARD=off allows a plain fork",
    fork("Summarize the diff."),
    False,
    env={"ISSUE_FLOW_FORK_GUARD": "off"},
)
expect(
    "turning off the name check leaves the fork check on",
    fork(BRIEF),
    True,
    env={"ISSUE_FLOW_SPAWN_GUARD": "off"},
)
expect(
    "turning off the fork check leaves the name check on",
    spawn(name="reviewer", prompt="x"),
    True,
    env={"ISSUE_FLOW_FORK_GUARD": "off"},
)
asked = run(fork(BRIEF), env={"ISSUE_FLOW_FORK_GUARD": "ask"})
if not asked or asked["hookSpecificOutput"].get("permissionDecision") != "ask":
    failures.append(f"fork ask mode should ask, not deny: {asked}")
elif "approve only if deliberate" not in asked["hookSpecificOutput"]["permissionDecisionReason"]:
    failures.append("the fork ask reason should say to approve only if deliberate")

# --- fork check fails open too ------------------------------------------------
expect("a fork with no prompt allows the call", spawn(subagent_type="fork"), False)
expect("a fork with a non-string prompt allows the call", spawn(subagent_type="fork", prompt=42), False)
expect("a non-string subagent_type allows the call", spawn(subagent_type=["fork"], prompt=BRIEF), False)

# --- a model override on an issue-flow agent (issue #120) --------------------
# SKILL.md: an issue-flow agent's tier is its definition's `model:` line. The one
# exception is an Opus escalation of a failed worker, whose brief carries `escalation:`.
NO_ESCALATION = re.sub(r"(?m)^escalation:.*\n(?:[ \t]+\S.*\n)*", "", BRIEF)
if NO_ESCALATION == BRIEF:
    failures.append(f"the handoff brief in {TEMPLATE} should document an `escalation:` field")
ESCALATED = NO_ESCALATION + (
    "escalation:   re-check failed after one rework round. Criterion 2 unmet:"
    " `widgets count --json --verbose` exits 0.\n"
)


def worker(model, prompt, **extra):
    return spawn(subagent_type="issue-flow:issue-worker", isolation="worktree",
                 description="escalate #42", model=model, prompt=prompt, **extra)


expect("an Opus worker with an escalation field is allowed", worker("opus", ESCALATED), False)
expect("the model value is matched case-insensitively", worker(" Opus ", ESCALATED), False)
expect(
    "an escalation field with list and bold markup counts",
    worker("opus", NO_ESCALATION + "- **escalation:** blocked: the suite cannot pass\n"),
    False,
)
output = expect("an Opus worker with no escalation field is denied", worker("opus", NO_ESCALATION), True)
if output:
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    for expected in ("SKILL.md", "`escalation:`", "ISSUE_FLOW_MODEL_GUARD", "issue-flow:issue-worker"):
        if expected not in reason:
            failures.append(f"the model deny reason should mention {expected!r}: {reason[:200]}")
expect(
    "an escalation label mid-line is not a field",
    worker("opus", NO_ESCALATION + "Note: no escalation: yet.\n"),
    True,
)
expect("an empty escalation field does not count", worker("opus", NO_ESCALATION + "escalation:\n"), True)
expect("an escalation goes to Opus only, not Sonnet", worker("sonnet", ESCALATED), True)
expect("an escalation goes to Opus only, not Haiku", worker("haiku", ESCALATED), True)
expect(
    "an Opus override on the criteria checker is denied, escalation or not",
    spawn(subagent_type="issue-flow:criteria-checker", model="opus", prompt=ESCALATED),
    True,
)
expect(
    "an override equal to the declared tier is still an override",
    spawn(subagent_type="issue-flow:deploy-verifier", model="sonnet", prompt="check the URL"),
    True,
)
expect("a worker with no model is allowed", worker(None, NO_ESCALATION), False)
expect(
    "a worker's general-purpose child on Sonnet is not the guard's business",
    spawn(subagent_type="general-purpose", model="sonnet", prompt="review the diff"),
    False,
)
expect("a child with no type on Sonnet is allowed", spawn(model="sonnet", prompt="x"), False)
expect(
    "another plugin's agent with a model is allowed",
    spawn(subagent_type="other-plugin:helper", model="opus", prompt="x"),
    False,
)
expect("a non-string model allows the call", worker(["opus"], NO_ESCALATION), False)
expect(
    "ISSUE_FLOW_MODEL_GUARD=off allows an override",
    worker("opus", NO_ESCALATION),
    False,
    env={"ISSUE_FLOW_MODEL_GUARD": "off"},
)
expect(
    "turning off the name check leaves the model check on",
    worker("opus", NO_ESCALATION),
    True,
    env={"ISSUE_FLOW_SPAWN_GUARD": "off"},
)
asked = run(worker("opus", NO_ESCALATION), env={"ISSUE_FLOW_MODEL_GUARD": "ask"})
if not asked or asked["hookSpecificOutput"].get("permissionDecision") != "ask":
    failures.append(f"model ask mode should ask, not deny: {asked}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("spawn guard: all cases pass")
