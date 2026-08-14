#!/usr/bin/env python3
"""List peer sessions — the background agents `ListAgents` does not show.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/peers.py"
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/peers.py" --json

An agent spawned with `name:` and no `isolation:` becomes a peer session instead
of a background subagent. Measured on Claude Code 2.1.232 (issue #25): it never
fires a completion notification, it does not appear in either section of
`ListAgents`, and its plain-text answer reaches nobody — so a PM waiting on it
waits forever, with nothing to look at.

The data is on disk even though the listing omits it. This reads it:

  ~/.claude/teams/<session>/config.json   live members, one per peer, plus the
                                          lead; the directory disappears when the
                                          last member is reaped
  ~/.claude/teams/<session>/inboxes/*.json  mail not yet drained by that peer

What this cannot do is reap them. An in-process peer exits only when it accepts a
`shutdown_request` sent from inside the session that owns it, and a peer that has
never been messaged ignores the first one — wake it with an ordinary message,
send the shutdown, then run this again to confirm the roster shrank. Only the
`tmux` backend leaves an OS process, and this prints its pane when it finds one.

**A roster entry is not proof of life.** Team directories outlive the sessions
that made them: this machine had 72 entries from a session five days dead, and a
named grandchild was still listed half an hour after its parent exited while
`ListAgents` showed nothing.

So a team is judged by **its owning session**, not by the roster's own age: the
team directory is named for the session id, and a live session rewrites its
transcript constantly. A team whose session has been silent for `--stale-hours`
(24 by default) is counted, not listed. Deliberately not roster age — a PM that
has waited a day and a half on one silent helper is exactly the case this tool
exists for, and that team stays listed as long as the session it belongs to is
alive. `--all` lists the stale ones too; delete one by removing its directory.
"""

import argparse
import json
import os
import sys
import time

DEFAULT_TEAMS_DIR = os.path.expanduser("~/.claude/teams")
DEFAULT_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def read_team(directory):
    """Return (session, [member, ...]) for one team directory, or None."""
    config_path = os.path.join(directory, "config.json")
    try:
        with open(config_path) as handle:
            config = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    lead = config.get("leadAgentId")
    members = []
    for member in config.get("members", []):
        if member.get("agentId") == lead:
            continue
        name = member.get("name", "<unnamed>")
        inbox = os.path.join(directory, "inboxes", f"{name}.json")
        members.append(
            {
                "name": name,
                "agentId": member.get("agentId", ""),
                "backend": member.get("backendType", "unknown"),
                "pane": member.get("tmuxPaneId", ""),
                "cwd": member.get("cwd", ""),
                "joinedAt": member.get("joinedAt"),
                "pendingMail": count_mail(inbox),
            }
        )
    return {"session": config.get("name", os.path.basename(directory)), "members": members}


def count_mail(path):
    """Messages sitting in an inbox that the peer has not read yet.

    Records carry an explicit `"read"` flag and stay in the file after delivery,
    so counting records would report mail as undelivered forever and invite the
    PM to re-send what already arrived.
    """
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    if isinstance(data, dict):
        data = data.get("messages", [])
    if not isinstance(data, list):
        return 0
    return sum(
        1 for message in data if not (isinstance(message, dict) and message.get("read"))
    )


def session_last_active(session, projects_dir):
    """Newest mtime of the owning session's transcript, or None if not found.

    A team directory is named `session-<first 8 of the session id>` and the
    transcript is `<session id>.jsonl` under the project directory, so the team
    can be matched to the session that owns it. This is the liveness signal:
    a live session rewrites its transcript constantly, while the roster is
    written once at join and says nothing about whether anyone is still there.
    """
    prefix = session[len("session-"):] if session.startswith("session-") else session
    # The harness names a team for the first 8 characters of the session id.
    # Matching on anything shorter would collide with unrelated sessions and
    # borrow their liveness, so treat a short prefix as "cannot tell".
    if len(prefix) < 8 or not os.path.isdir(projects_dir):
        return None
    newest = None
    for project in os.listdir(projects_dir):
        directory = os.path.join(projects_dir, project)
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            if not (entry.startswith(prefix) and entry.endswith(".jsonl")):
                continue
            try:
                touched = os.path.getmtime(os.path.join(directory, entry))
            except OSError:
                continue
            newest = touched if newest is None else max(newest, touched)
    return newest


def collect(teams_dir, stale_hours=None, now=None, projects_dir=DEFAULT_PROJECTS_DIR):
    """Return teams that have peers. With stale_hours, split live from stale.

    "Stale" means the session that owns the roster is gone, judged by how long
    ago it last wrote its transcript — not by the roster's own age. A PM that has
    been waiting a day and a half on one silent helper is exactly who this tool is
    for, and filtering on roster age would hide that team as if it were litter.
    The roster mtime is the fallback when no transcript can be matched.
    """
    teams, stale = [], []
    if not os.path.isdir(teams_dir):
        return (teams, stale) if stale_hours is not None else teams
    now = now if now is not None else time.time()
    for name in sorted(os.listdir(teams_dir)):
        directory = os.path.join(teams_dir, name)
        team = read_team(directory)
        if not team or not team["members"]:
            continue
        if stale_hours is not None:
            touched = session_last_active(team["session"], projects_dir)
            # No transcript match means the owning session cannot be found at all —
            # true for a team created by a worktree-isolated agent, whose own
            # session leaves no top-level transcript. Fall back to the roster mtime,
            # but say the answer is unverified: measured on this machine, a peer
            # dead for hours still looked "live" that way, which is the one thing
            # this tool must not do quietly.
            team["liveness"] = "session-active" if touched is not None else "roster-only"
            if touched is None:
                try:
                    touched = os.path.getmtime(os.path.join(directory, "config.json"))
                except OSError:
                    touched = 0
            if now - touched > stale_hours * 3600:
                team["path"] = directory
                stale.append(team)
                continue
        teams.append(team)
    return (teams, stale) if stale_hours is not None else teams


def age(joined_at, now):
    """Human age of a peer, from the epoch-millis the team config records."""
    if not joined_at:
        return "?"
    seconds = max(0, int(now - joined_at / 1000))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def stale_summary(stale):
    """One line about leftovers — a roll call of dead rosters helps nobody."""
    if not stale:
        return []
    peers_count = sum(len(team["members"]) for team in stale)
    return [
        "",
        f"{len(stale)} stale team director{'y' if len(stale) == 1 else 'ies'} "
        f"({peers_count} roster entr{'y' if peers_count == 1 else 'ies'}) not touched "
        f"recently — rosters outlive their peers, so these are cleanup, not work in "
        f"flight. `--all` lists them; delete one by removing its directory.",
    ]


def render(teams, now, stale=()):
    lines = []
    if not teams:
        lines.append(
            "No live peer sessions. (Background subagents are not peers — they show in "
            "ListAgents.)"
        )
        return lines + stale_summary(stale)
    for team in teams:
        caveat = (
            "   (owning session not found — roster only, may already be dead)"
            if team.get("liveness") == "roster-only"
            else ""
        )
        lines.append(f"{team['session']}:{caveat}")
        for member in team["members"]:
            mail = f"  {member['pendingMail']} undrained" if member["pendingMail"] else ""
            pane = member["pane"]
            where = f"  pid/pane {pane}" if pane and pane != "in-process" else ""
            lines.append(
                f"  {member['name']:<24} {member['backend']:<12} "
                f"{age(member['joinedAt'], now):>5}{where}{mail}"
            )
    lines.append("")
    lines.append(
        "These are invisible to ListAgents and never send a completion notification. "
        "To reap one: message it, then send {\"type\": \"shutdown_request\"}, then re-run. "
        "A listed peer may already be dead — cross-check before you wait on it."
    )
    return lines + stale_summary(stale)


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--teams-dir", default=DEFAULT_TEAMS_DIR)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=24.0,
        help="a team whose config is older than this is summarized, not listed",
    )
    parser.add_argument("--all", action="store_true", help="list stale teams too")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit 1 when a live peer exists, for use in a check",
    )
    args = parser.parse_args()

    now = time.time()
    live, stale = collect(args.teams_dir, stale_hours=args.stale_hours, now=now)
    if args.json:
        print(json.dumps({"live": live, "stale": stale}, indent=2))
    elif args.all:
        # Listed together, but `live` still means live — see --exit-code below.
        print("\n".join(render(live + stale, now, [])))
    else:
        print("\n".join(render(live, now, stale)))
    if args.exit_code and live:
        sys.exit(1)


if __name__ == "__main__":
    main()
