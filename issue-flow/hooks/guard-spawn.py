#!/usr/bin/env python3
"""PreToolUse guard: refuse the two `Agent` spawn shapes issue-flow has seen go wrong.

Wired by `issue-flow/hooks/hooks.json`, which Claude Code loads automatically for
an installed plugin. It reads the PreToolUse payload on stdin and returns a deny
decision, with the fix in the reason, for an `Agent` (or legacy `Task`) call that

1. passes `name:` **without** `isolation:` (strands the caller — below), or
2. is a **fork** (`subagent_type: "fork"`) whose prompt is an issue-flow handoff
   brief (runs away — see "Fork dispatch" further down).

Why this is worth a hook rather than another paragraph of documentation
(measured on Claude Code 2.1.232, issue #25):

  unnamed, no isolation      -> background subagent, completion notification
  name + isolation           -> background subagent, completion notification
  name, no isolation         -> PEER SESSION: no completion notification ever,
                                invisible to ListAgents, and its plain-text
                                answer reaches nobody

The two success strings differ only in prose, so a caller cannot tell which of
the two it got. `issue-flow` never prescribes the bad shape, but the PM improvises
a `name:` at spawn sites that do not specify one, and then waits forever for a
helper that finished minutes ago.

Design choices, both deliberate:

* **Deny, not repair.** A hook may rewrite the call with `updatedInput`, but both
  repairs change behavior: adding `isolation` moves the agent into its own
  worktree cut from the default branch (it can no longer write the shared
  checkout, and `git -C` there is refused), while dropping `name` removes
  addressability the caller may have wanted. Denying returns the choice to the
  caller with both options spelled out.
* **Fail open.** Any unexpected payload, parse error, or internal fault exits 0
  and allows the call. A guard against a lost result must never become the reason
  work cannot start.

`ISSUE_FLOW_SPAWN_GUARD` selects the behavior: `deny` (default), `ask` (prompt
instead of refusing), or `off`.

**`ask` is for interactive sessions only.** Measured: a background agent whose
spawn hits an `ask` decision **hangs** — there is nobody to answer the prompt, so
the tool call never returns and the agent sits at its last line until it is
killed. Every issue-flow worker is a background agent, so `ask` would trade a
recoverable refusal for exactly the silent stall this guard exists to prevent.
That is why the default is `deny`, which returns an actionable error the agent can
act on by itself. A named peer session is a legitimate thing to
want — the harness offers it deliberately — and a plugin has no business refusing
it in a project that is not running issue-flow. Hooks read the environment as it
was when the session started, so `export` from a tool call does nothing: set it in
the project's `.claude/settings.json` instead and start a new session.

    { "env": { "ISSUE_FLOW_SPAWN_GUARD": "off" } }

For reference, `issue-flow` itself never trips this guard: its only named spawn is
`worker-<issue>`, which always carries `isolation: "worktree"`. What trips it is an
improvised `name:` at a spawn site that did not specify one — the failure in
issue #25.

This check can only fire when the `Agent` tool has a `name` parameter at all. Measured
on Claude Code 2.1.285 (issue #65): `name`, `team_name` and `mode` exist only with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; without the flag a caller cannot send the
shape, and issue-flow addresses workers by `agentId` instead. With the flag set, one
2.1.285 sample of the named-without-isolation shape came back as an ordinary background
subagent that delivered its result, so the peer session measured on 2.1.232 may be gone;
the guard stays as cheap insurance.

Fork dispatch (issue #50)
-------------------------
A fork inherits the caller's whole session. When the caller is the PM, that is the
full autonomous-loop text, and nothing in it tells the fork to stop after one
issue. Measured in a live run (PR #49): a fork dispatched for one issue kept
running the PM loop for 52 minutes — it claimed and built two more issues from
another epic and merged straight to `dev`. A runaway fork writes to the
repository, so the prose rule in `SKILL.md` Stage B step 5 is enforced here too.

Forks themselves are a legitimate harness feature, so only a **build-shaped**
fork is denied: one whose prompt carries at least `BRIEF_THRESHOLD` of the
handoff-brief field labels (`issue:`, `branch:`, `base:`, `ci:`, `batch:`,
`members:`, `crossCheck:`, `forge:`, `steRule:` — `references/issue-worker.md`)
at the start of a line, at least one of them issue-flow's own (`crossCheck:`,
`members:`, `forge:`, `steRule:`). The match tolerates the ways a model
re-lays the template out — list bullets and numbers, headings, bold, code,
table cells, pretty-printed JSON keys, `=` for `:`, `cross-check` — and a
fork that lists "Issue / Branch / CI" to describe a failing build still passes.

What it does not catch: a brief paraphrased into prose or squeezed onto one
line (single-line JSON). It is a tripwire for the template the PM is told to
copy, not a classifier. Known false positive: a fork asked to *discuss* the
brief, quoting its fields one per line, is denied — the reason says to reword
it. Only the literal `subagent_type: "fork"` is treated as a fork; omitting
the type starts a general-purpose agent (measured on Claude Code 2.1.284, where
the fork arrives at this hook as `tool_input.subagent_type == "fork"`).

`ISSUE_FLOW_FORK_GUARD` controls this check on its own, with the same values as
`ISSUE_FLOW_SPAWN_GUARD` (`deny` default, `ask`, `off`), so a project that turns
named peer sessions back on keeps the fork check. The same session-start rule
applies — set it in `.claude/settings.json`:

    { "env": { "ISSUE_FLOW_FORK_GUARD": "off" } }
"""

import json
import os
import re
import sys

SPAWN_TOOLS = {"Agent", "Task"}

# Handoff-brief field labels, matched at the start of a line with any list,
# heading, quote, bold, code, table or JSON markup around them
# (`- **base:**`, `2. ci:`, `| members |`, `"crossCheck": `, `cross-check =`).
BRIEF_FIELD = re.compile(
    r"^[\s>*_`#|\"+-]*(?:\d+[.)][ \t]*)?"
    r"(issue|branch|base|ci|batch|members|cross[-_ ]?check|forge|sterule)"
    r"[*_`\"]*[ \t]*[:=|]",
    re.IGNORECASE | re.MULTILINE,
)
BRIEF_THRESHOLD = 3
# Labels no ad-hoc prompt uses — "Issue / Branch / CI" alone is how anyone
# describes a failing build, so at least one of these must be present too.
ISSUE_FLOW_FIELDS = {"crosscheck", "members", "forge", "sterule"}

REASON = (
    "issue-flow spawn guard: this Agent call passes `name` with no `isolation`, which "
    "does not create a background subagent. It creates a peer session — no completion "
    "notification ever, invisible to ListAgents, and its final text delivered to "
    "nobody. You would wait for a result that cannot arrive.\n"
    "\n"
    "**Drop `name`.** An unnamed subagent always notifies, and it still runs in your "
    "worktree on your branch, so its work reaches you. This is the fix in almost every "
    "case — including a worker spawning its own review or fix children.\n"
    "\n"
    "Add `isolation: \"worktree\"` **only** if you also need the agent addressable by "
    "`SendMessage` later by name, as the PM does for `worker-<issue>` where names exist. "
    "Do not add it to a "
    "worker's own child: an isolated child builds in a separate worktree and its "
    "commits never reach your branch (agents/issue-worker.md).\n"
    "\n"
    "Deliberately want a peer session? This guard reads its setting from the "
    "environment at session start, so exporting a variable now will not change it — "
    "put this in the project's .claude/settings.json and start a new session:\n"
    '    "env": { "ISSUE_FLOW_SPAWN_GUARD": "off" }'
)

FORK_REASON = (
    "issue-flow spawn guard: this is a fork (`subagent_type: \"fork\"`) carrying an "
    "issue-flow handoff brief (FIELDS). A fork inherits this whole session — the PM "
    "loop included — and nothing in it says to stop after one issue. Measured: a fork "
    "dispatched for one issue ran the PM loop for 52 minutes, built two more issues and "
    "merged them to dev with no PM gate.\n"
    "\n"
    "**Dispatch the build to `issue-flow:issue-worker` instead**, with "
    "`isolation: \"worktree\"` (plus `name: \"worker-<issue>\"` where the tool offers it), "
    "passing the same brief "
    "(SKILL.md Stage B step 5). A worker starts with no PM context and does only what "
    "the brief says.\n"
    "\n"
    "Not a build? The prompt matched the handoff-brief layout. Reword it, or turn this "
    "check off: it reads its setting from the environment at session start, so put this "
    "in the project's .claude/settings.json and start a new session:\n"
    '    "env": { "ISSUE_FLOW_FORK_GUARD": "off" }'
)

APPROVE_PREFIX = "issue-flow spawn guard (approve only if deliberate):"


def mode(variable):
    """off | ask | deny — read once per call, from the session's environment."""
    setting = os.environ.get(variable, "").strip().lower()
    if setting in {"off", "0", "false", "no"}:
        return "off"
    if setting == "ask":
        return "ask"
    return "deny"


def verdict(guard, reason):
    if guard == "ask":
        return ("ask", reason.replace("issue-flow spawn guard:", APPROVE_PREFIX, 1))
    return ("deny", reason)


def brief_fields(prompt):
    """The distinct handoff-brief labels in `prompt`, as written, in order."""
    found = {}
    for match in BRIEF_FIELD.finditer(prompt):
        key = re.sub(r"[-_ ]", "", match.group(1).lower())
        found.setdefault(key, match.group(1))
    return found


def is_build_brief(fields):
    return len(fields) >= BRIEF_THRESHOLD and not ISSUE_FLOW_FIELDS.isdisjoint(fields)


def check_fork(tool_input):
    guard = mode("ISSUE_FLOW_FORK_GUARD")
    if guard == "off":
        return None
    kind = tool_input.get("subagent_type")
    if not isinstance(kind, str) or kind.strip().lower() != "fork":
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    fields = brief_fields(prompt)
    if not is_build_brief(fields):
        return None
    listed = ", ".join(f"`{field}:`" for field in fields.values())
    return verdict(guard, FORK_REASON.replace("FIELDS", listed, 1))


def check_name(tool_input):
    guard = mode("ISSUE_FLOW_SPAWN_GUARD")
    if guard == "off":
        return None
    name = tool_input.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if tool_input.get("isolation"):
        return None
    return verdict(guard, REASON)


def decide(payload):
    """Return (permissionDecision, reason), or None to stay out of the way."""
    if payload.get("tool_name") not in SPAWN_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    # The fork check runs first: a runaway fork writes to the repository, a
    # stranded peer only loses a result.
    return check_fork(tool_input) or check_name(tool_input)


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return
        decision = decide(payload)
    except Exception:  # fail open — see the module docstring
        return
    if not decision:
        return
    permission, reason = decision
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": permission,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
