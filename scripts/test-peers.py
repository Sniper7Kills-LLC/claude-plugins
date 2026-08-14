#!/usr/bin/env python3
"""Tests for issue-flow/scripts/peers.py.

    python3 scripts/test-peers.py

The fixtures below are the real shapes read off disk during issue #25: an
in-process peer under the default `auto` backend, a `tmux`-backed peer from a
pinned session, undrained mail, and the lead entry that must never be reported as
a peer. The detector runs against a temporary directory, never the real
~/.claude/teams.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "peers", os.path.join(ROOT, "issue-flow", "scripts", "peers.py")
)
peers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peers)

failures = []
NOW = 1786681000.0


def fail(message):
    failures.append(message)


def make_team(root, session, members, inboxes=None):
    directory = os.path.join(root, session)
    os.makedirs(os.path.join(directory, "inboxes"), exist_ok=True)
    with open(os.path.join(directory, "config.json"), "w") as handle:
        json.dump(
            {
                "name": session,
                "leadAgentId": f"team-lead@{session}",
                "members": members,
            },
            handle,
        )
    for name, mail in (inboxes or {}).items():
        with open(os.path.join(directory, "inboxes", f"{name}.json"), "w") as handle:
            json.dump(mail, handle)
    return directory


def lead_for(session):
    """The lead entry as the harness writes it — agentId matched to the session."""
    return dict(LEAD, agentId=f"team-lead@{session}")


LEAD = {
    "agentId": "team-lead@session-aaa",
    "name": "team-lead",
    "tmuxPaneId": "leader",
    "backendType": "in-process",
    "joinedAt": int((NOW - 3600) * 1000),
}
IN_PROCESS_PEER = {
    "agentId": "probe-b@session-aaa",
    "name": "probe-b",
    "tmuxPaneId": "in-process",
    "backendType": "in-process",
    "joinedAt": int((NOW - 120) * 1000),
}
TMUX_PEER = {
    "agentId": "worker-18@session-bbb",
    "name": "worker-18",
    "tmuxPaneId": "%7",
    "backendType": "tmux",
    "joinedAt": int((NOW - 7200) * 1000),
}

work = tempfile.mkdtemp(prefix="peers-test-")
try:
    # --- the lead is not a peer -----------------------------------------------
    make_team(work, "session-lead-only", [lead_for("session-lead-only")])
    if peers.collect(work):
        fail("a team with only its lead should report no peers")

    # --- one in-process peer, with undrained mail -----------------------------
    shutil.rmtree(work)
    os.makedirs(work)
    make_team(
        work,
        "session-aaa",
        [lead_for("session-aaa"), IN_PROCESS_PEER],
        inboxes={"probe-b": [{"msg": "one"}, {"msg": "two"}]},
    )
    teams = peers.collect(work)
    if len(teams) != 1 or len(teams[0]["members"]) != 1:
        fail(f"expected exactly one peer, got {teams}")
    else:
        member = teams[0]["members"][0]
        if member["name"] != "probe-b":
            fail(f"wrong peer name: {member['name']}")
        if member["backend"] != "in-process":
            fail(f"wrong backend: {member['backend']}")
        if member["pendingMail"] != 2:
            fail(f"undrained mail miscounted: {member['pendingMail']}")

    # --- a tmux peer keeps its pane, so it can be found in the OS -------------
    make_team(work, "session-bbb", [lead_for("session-bbb"), TMUX_PEER])
    teams = peers.collect(work)
    if len(teams) != 2:
        fail(f"expected two teams with peers, got {len(teams)}")
    tmux = [m for t in teams for m in t["members"] if m["backend"] == "tmux"]
    if len(tmux) != 1 or tmux[0]["pane"] != "%7":
        fail(f"tmux peer pane not surfaced: {tmux}")

    # --- ages read the way a human reads them ---------------------------------
    if peers.age(IN_PROCESS_PEER["joinedAt"], NOW) != "2m":
        fail(f"age of a 120s peer should be 2m, got {peers.age(IN_PROCESS_PEER['joinedAt'], NOW)}")
    if peers.age(TMUX_PEER["joinedAt"], NOW) != "2h":
        fail(f"age of a 7200s peer should be 2h, got {peers.age(TMUX_PEER['joinedAt'], NOW)}")
    if peers.age(None, NOW) != "?":
        fail("a peer with no joinedAt should render '?'")

    # --- a missing or malformed team directory is not a crash -----------------
    if peers.collect(os.path.join(work, "does-not-exist")) != []:
        fail("a missing teams directory should yield no peers")
    broken = os.path.join(work, "session-broken")
    os.makedirs(broken, exist_ok=True)
    with open(os.path.join(broken, "config.json"), "w") as handle:
        handle.write("{not json")
    if len(peers.collect(work)) != 2:
        fail("an unparseable team config should be skipped, not counted or raised")

    # --- the empty case says something useful ---------------------------------
    empty = peers.render([], NOW)
    if not empty or "No live peer sessions" not in empty[0]:
        fail(f"empty render should state there are none: {empty}")
    rendered = "\n".join(peers.render(peers.collect(work), NOW))
    for expected in ("probe-b", "worker-18", "shutdown_request"):
        if expected not in rendered:
            fail(f"rendered output is missing {expected!r}")

    # --- unread mail only, or a delivered message reads as undelivered forever -
    # Real records stay in the inbox with an explicit "read" flag.
    delivered = os.path.join(work, "session-aaa", "inboxes", "probe-b.json")
    with open(delivered, "w") as handle:
        json.dump(
            [
                {"from": "team-lead", "text": "one", "read": True},
                {"from": "team-lead", "text": "two", "read": True},
                {"from": "team-lead", "text": "three", "read": False},
            ],
            handle,
        )
    reread = peers.collect(work)
    counts = {m["name"]: m["pendingMail"] for t in reread for m in t["members"]}
    if counts.get("probe-b") != 1:
        fail(f"only unread mail is pending, expected 1, got {counts.get('probe-b')}")

    # --- a roster is not proof of life ----------------------------------------
    # 72 entries from a five-day-dead session were on the machine that filed
    # issue #25, and a named grandchild outlived its parent by half an hour with
    # ListAgents showing nothing. Old rosters must not read as work in flight.
    old = os.path.join(work, "session-aaa", "config.json")
    week_ago = NOW - 7 * 24 * 3600
    os.utime(old, (week_ago, week_ago))
    fresh = os.path.join(work, "session-bbb", "config.json")
    os.utime(fresh, (NOW - 60, NOW - 60))

    isolated_projects = os.path.join(work, "projects")
    os.makedirs(isolated_projects, exist_ok=True)
    live, stale = peers.collect(
        work, stale_hours=24, now=NOW, projects_dir=isolated_projects
    )
    if [t["session"] for t in live] != ["session-bbb"]:
        fail(f"only the freshly-touched team is live: {[t['session'] for t in live]}")
    if [t["session"] for t in stale] != ["session-aaa"]:
        fail(f"the week-old team should be stale: {[t['session'] for t in stale]}")
    if "path" not in (stale[0] if stale else {}):
        fail("a stale team must carry its path so it can be deleted")

    summary = "\n".join(peers.render(live, NOW, stale))
    if "1 stale team directory" not in summary:
        fail(f"stale teams should be summarized, not rolled out: {summary}")
    if "probe-b" in summary:
        fail("a stale team's members must not be listed as if they were live")

    # --- and --all-style inclusion still shows them ---------------------------
    everything = "\n".join(peers.render(live + stale, NOW, []))
    if "probe-b" not in everything or "worker-18" not in everything:
        fail("listing stale teams explicitly should include their members")

    # --- without stale_hours the call keeps its simple shape ------------------
    plain = peers.collect(work)
    if not isinstance(plain, list) or len(plain) != 2:
        fail(f"collect() without stale_hours should return a flat list of 2: {plain}")

    # --- liveness follows the owning SESSION, not the roster's age ------------
    # A PM waiting 36h on one silent helper is the case this tool exists for. Its
    # roster has not been rewritten since the peer joined, but its session is
    # alive and rewriting its transcript, so the team must stay listed.
    projects = os.path.join(work, "projects", "-some-repo")
    os.makedirs(projects, exist_ok=True)
    transcript = os.path.join(projects, "aaa11111-2222-3333-4444-555555555555.jsonl")
    with open(transcript, "w") as handle:
        handle.write("{}\n")
    os.utime(transcript, (NOW - 30, NOW - 30))          # session active 30s ago
    long_wait = make_team(
        work,
        "session-aaa11111",
        [lead_for("session-aaa11111"), dict(IN_PROCESS_PEER, name="patient-helper")],
    )
    ancient = NOW - 36 * 3600
    os.utime(os.path.join(long_wait, "config.json"), (ancient, ancient))

    live, stale = peers.collect(
        work, stale_hours=24, now=NOW, projects_dir=os.path.join(work, "projects")
    )
    if "session-aaa11111" not in [t["session"] for t in live]:
        fail("a 36h-old roster whose session is still active must stay listed")
    if "session-aaa11111" in [t["session"] for t in stale]:
        fail("an active session's team must not be filtered as stale")

    # A team whose owning session cannot be found is reported, but labelled: on a
    # real machine a peer dead for hours still looked "live" through the roster
    # fallback, and a detector that says "live" about a corpse is worse than none.
    orphan = make_team(
        work,
        "session-orphan1",
        [lead_for("session-orphan1"), dict(IN_PROCESS_PEER, name="lost-child")],
    )
    recent = NOW - 600
    os.utime(os.path.join(orphan, "config.json"), (recent, recent))
    live, stale = peers.collect(
        work, stale_hours=24, now=NOW, projects_dir=os.path.join(work, "projects")
    )
    orphan_team = next((t for t in live if t["session"] == "session-orphan1"), None)
    if not orphan_team:
        fail("a team with no matching transcript should still be reported")
    elif orphan_team.get("liveness") != "roster-only":
        fail(f"unverifiable liveness must be labelled: {orphan_team.get('liveness')}")
    if "roster only" not in "\n".join(peers.render(live, NOW, stale)):
        fail("the rendered output must carry the roster-only caveat")
    active = next((t for t in live if t["session"] == "session-aaa11111"), None)
    if active and active.get("liveness") != "session-active":
        fail(f"a team with a live transcript is session-active: {active.get('liveness')}")

    # ...and when that session goes quiet, the same team becomes cleanup.
    os.utime(transcript, (NOW - 48 * 3600, NOW - 48 * 3600))
    live, stale = peers.collect(
        work, stale_hours=24, now=NOW, projects_dir=os.path.join(work, "projects")
    )
    if "session-aaa11111" not in [t["session"] for t in stale]:
        fail("once the owning session goes quiet the team is stale")
finally:
    shutil.rmtree(work, ignore_errors=True)

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("peer detector: all cases pass")
