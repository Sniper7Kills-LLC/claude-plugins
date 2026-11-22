#!/usr/bin/env python3
"""Report tokens and estimated cost per agent from local Claude Code transcripts.

    python3 usage.py --session <session-id>            # one session, any project
    python3 usage.py --since 2026-11-01T00:00:00Z      # every session of this project since then
    python3 usage.py --transcript <path>.jsonl --json  # one transcript file

The script reads files only. It makes no network calls and no API calls.

Where the data comes from (https://code.claude.com/docs/en/sessions.md and
sub-agents.md): a session is ~/.claude/projects/<project>/<session-id>.jsonl, and
each subagent of that session is <session-id>/subagents/agent-<agentId>.jsonl. The
entry format is internal to Claude Code and can change, so the parser skips any
line or shape it does not know.

What real transcripts show (checked on 3295 assistant entries on 2026-11-21):

- One API response is written as one line per content block. Every line repeats the
  same `message.usage` and the same `requestId`. The script counts each `requestId`
  once, and falls back to `message.id` when `requestId` is absent.
- `usage.iterations[]` sums to the top-level usage on every entry. The script does
  not add it a second time.
- `<agentId>.meta.json` beside a subagent file names its `agentType`. The parent's
  `Agent` tool call is the fallback: its `subagent_type`, joined to the agent through
  the `agentId` of the tool result.

One turn is one API request. The main transcript is the agent of type `main`; in an
issue-flow session that is the PM. A cost is an estimate at the standard rates in
pricing.json. Fast mode, US-only routing and Batch API discounts are not applied.
"""

import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

PRICING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pricing.json")
PROJECTS_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def load_pricing(path=PRICING_PATH):
    """Return the parsed pricing table."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def project_dir_name(cwd):
    """Return the directory name Claude Code uses for the project at `cwd`."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def parse_time(text):
    """Return an aware datetime for an ISO 8601 string, or None when it does not parse."""
    if not isinstance(text, str):
        return None
    try:
        value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def read_jsonl(path):
    """Return the JSON objects in `path` and the count of lines that do not parse."""
    entries, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(entry, dict):
                entries.append(entry)
            else:
                skipped += 1
    return entries, skipped


def price_for(model, pricing):
    """Return the price entry for `model`, or None when the table has no price for it.

    A dated model ID such as `claude-haiku-4-5-20251001` takes the price of its base ID.
    """
    models = pricing.get("models", {})
    if model in models:
        return models[model]
    match = re.fullmatch(r"(.+)-\d{8}", model or "")
    if match and match.group(1) in models:
        return models[match.group(1)]
    return None


def split_cache_write(usage):
    """Return the 5-minute and 1-hour cache-write tokens of one request.

    Without the `cache_creation` split, every cache write counts as a 5-minute write.
    """
    total = usage.get("cache_creation_input_tokens") or 0
    split = usage.get("cache_creation")
    if not isinstance(split, dict):
        return total, 0
    one_hour = split.get("ephemeral_1h_input_tokens") or 0
    five_min = split.get("ephemeral_5m_input_tokens") or 0
    return five_min + max(total - five_min - one_hour, 0), one_hour


def request_cost(tokens, write_5m, write_1h, price):
    """Return the cost in USD of one request at `price`."""
    prompt = tokens["input"] + tokens["cache_read"] + tokens["cache_write"]
    rates = price
    long_prompt = price.get("long_prompt")
    if long_prompt and prompt > long_prompt.get("over_tokens", float("inf")):
        rates = long_prompt
    micro = (
        tokens["input"] * rates["input"]
        + tokens["output"] * rates["output"]
        + tokens["cache_read"] * rates["cache_read"]
        + write_5m * rates["cache_write_5m"]
        + write_1h * rates["cache_write_1h"]
    )
    return micro / 1_000_000


def session_files(transcript):
    """Return the main transcript and its subagent transcripts, with each agent's id and meta."""
    session_id = os.path.basename(transcript)[: -len(".jsonl")]
    files = [(transcript, session_id, {})]
    subagents = os.path.join(transcript[: -len(".jsonl")], "subagents")
    for path in sorted(glob.glob(os.path.join(subagents, "agent-*.jsonl"))):
        agent_id = os.path.basename(path)[len("agent-"): -len(".jsonl")]
        meta = {}
        meta_path = path[: -len(".jsonl")] + ".meta.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as handle:
                    meta = json.load(handle)
            except (OSError, ValueError):
                meta = {}
        files.append((path, agent_id, meta if isinstance(meta, dict) else {}))
    return files


def spawn_types(entries):
    """Return the `subagent_type` of each spawned agentId, read from the parent's Agent calls."""
    by_tool_use = {}
    agent_tool_use = {}
    for entry in entries:
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                kind = block["input"].get("subagent_type")
                if kind:
                    by_tool_use[block.get("id")] = kind
            result = entry.get("toolUseResult")
            if block.get("type") == "tool_result" and isinstance(result, dict) and result.get("agentId"):
                agent_tool_use[result["agentId"]] = block.get("tool_use_id")
    return {agent: by_tool_use[tool] for agent, tool in agent_tool_use.items() if tool in by_tool_use}


def collect_session(transcript, since):
    """Return the agents of one session in report order, and the count of skipped lines."""
    files = session_files(transcript)
    parsed, skipped = [], 0
    for path, agent_id, meta in files:
        entries, bad = read_jsonl(path)
        skipped += bad
        parsed.append((agent_id, meta, entries))
    spawned = spawn_types([entry for _, _, entries in parsed for entry in entries])

    agents = collections.OrderedDict()
    for index, (agent_id, meta, entries) in enumerate(parsed):
        for entry in entries:
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            model = message.get("model")
            # Claude Code writes `<synthetic>` entries for local notices; no API request made them.
            if not model or model == "<synthetic>":
                continue
            stamp = parse_time(entry.get("timestamp"))
            if since is not None and (stamp is None or stamp < since):
                continue
            owner = entry.get("agentId") or agent_id
            if owner not in agents:
                if index == 0 and owner == agent_id:
                    kind = "main"
                else:
                    kind = meta.get("agentType") if owner == agent_id else None
                    kind = kind or spawned.get(owner) or "unknown"
                agents[owner] = {"id": owner, "type": kind, "first": stamp, "requests": {}}
            key = entry.get("requestId") or message.get("id") or f"line-{id(entry)}"
            # The lines of one response carry the same usage; the last line wins.
            agents[owner]["requests"][key] = (model, message["usage"])
    ordered = list(agents.values())
    main = [agent for agent in ordered if agent["type"] == "main"]
    rest = sorted(
        (agent for agent in ordered if agent["type"] != "main"),
        key=lambda agent: agent["first"] or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc),
    )
    return main + rest, skipped


def empty_totals():
    return {"turns": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def summarize_agent(agent, pricing, unpriced):
    """Return the report row of one agent, and its priced cost in USD."""
    row = {"type": agent["type"], "id": agent["id"], **empty_totals()}
    models = collections.Counter()
    cost, priced_cost, complete = 0.0, 0.0, True
    for model, usage in agent["requests"].values():
        tokens = {
            "input": usage.get("input_tokens") or 0,
            "output": usage.get("output_tokens") or 0,
            "cache_read": usage.get("cache_read_input_tokens") or 0,
            "cache_write": usage.get("cache_creation_input_tokens") or 0,
        }
        row["turns"] += 1
        for field in TOKEN_FIELDS:
            row[field] += tokens[field]
        models[model] += 1
        price = price_for(model, pricing)
        if price is None:
            complete = False
            unpriced.add(model)
            continue
        write_5m, write_1h = split_cache_write(usage)
        value = request_cost(tokens, write_5m, write_1h, price)
        cost += value
        priced_cost += value
    row["model"] = ",".join(model for model, _ in models.most_common())
    row["cost_usd"] = round(cost, 6) if complete else None
    return row, priced_cost


def build_report(transcripts, pricing, since=None):
    """Return the usage report for the main transcripts in `transcripts`.

    The shape is the `--json` contract that eval reports consume:
    {agents: [{type, id, model, turns, input, output, cache_read, cache_write, cost_usd}],
     totals: {turns, input, output, cache_read, cache_write, cost_usd, unpriced_models,
              skipped_lines, by_type}, pricing_date}
    """
    rows, unpriced, skipped, priced_total = [], set(), 0, 0.0
    by_type = collections.OrderedDict()
    for transcript in transcripts:
        agents, bad = collect_session(transcript, since)
        skipped += bad
        for agent in agents:
            row, priced_cost = summarize_agent(agent, pricing, unpriced)
            rows.append(row)
            priced_total += priced_cost
            group = by_type.setdefault(row["type"], {**empty_totals(), "agents": 0, "cost": 0.0, "complete": True})
            group["agents"] += 1
            for field in ("turns",) + TOKEN_FIELDS:
                group[field] += row[field]
            if row["cost_usd"] is None:
                group["complete"] = False
            else:
                group["cost"] += row["cost_usd"]
    totals = empty_totals()
    for row in rows:
        for field in ("turns",) + TOKEN_FIELDS:
            totals[field] += row[field]
    totals["cost_usd"] = round(priced_total, 6)
    totals["unpriced_models"] = sorted(unpriced)
    totals["skipped_lines"] = skipped
    totals["by_type"] = {
        kind: {
            "agents": group["agents"],
            **{field: group[field] for field in ("turns",) + TOKEN_FIELDS},
            "cost_usd": round(group["cost"], 6) if group["complete"] else None,
        }
        for kind, group in by_type.items()
    }
    return {"agents": rows, "totals": totals, "pricing_date": pricing.get("retrieved")}


def format_cost(value):
    return "n/a" if value is None else f"{value:.4f}"


def format_table(report, pricing):
    """Return the report as a plain-text table."""
    headers = ["type", "id", "model", "turns", "input", "output", "cache_read", "cache_write", "cost_usd"]
    lines = [headers]
    for row in report["agents"]:
        lines.append([row[h] if h != "cost_usd" else format_cost(row[h]) for h in headers])
    lines.append([""] * len(headers))
    for kind, group in report["totals"]["by_type"].items():
        lines.append([kind, f"{group['agents']} agent(s)", ""]
                     + [group[f] for f in ("turns",) + TOKEN_FIELDS] + [format_cost(group["cost_usd"])])
    totals = report["totals"]
    lines.append(["TOTAL", "", ""] + [totals[f] for f in ("turns",) + TOKEN_FIELDS]
                 + [format_cost(totals["cost_usd"])])
    cells = [[str(cell) for cell in line] for line in lines]
    widths = [max(len(line[i]) for line in cells) for i in range(len(headers))]
    numeric = set(range(3, len(headers)))
    out = []
    for line in cells:
        out.append("  ".join(
            cell.rjust(widths[i]) if i in numeric else cell.ljust(widths[i])
            for i, cell in enumerate(line)
        ).rstrip())
    out.append("")
    out.append(f"Prices: {pricing.get('source')}, retrieved {report['pricing_date']}. "
               "The cost is an estimate at standard rates.")
    if totals["unpriced_models"]:
        out.append("No price for: " + ", ".join(totals["unpriced_models"])
                   + ". TOTAL cost leaves those requests out.")
    if totals["skipped_lines"]:
        out.append(f"Skipped {totals['skipped_lines']} line(s) that are not JSON.")
    return "\n".join(out)


class SelectionError(Exception):
    """The arguments select no transcript."""


def session_transcripts(session_id, projects_root=PROJECTS_ROOT):
    """Return the main transcripts of `session_id` in any project under `projects_root`, sorted."""
    return sorted(glob.glob(os.path.join(projects_root, "*", session_id + ".jsonl")))


def find_transcripts(args, since):
    """Return the main transcripts the arguments select; raise SelectionError when there are none."""
    if args.transcript:
        missing = [path for path in args.transcript if not os.path.isfile(path)]
        if missing:
            raise SelectionError(f"usage.py: no such transcript: {', '.join(missing)}")
        return args.transcript
    if args.session:
        found = session_transcripts(args.session, args.projects_root)
        if not found:
            raise SelectionError(f"usage.py: no transcript for session {args.session} under {args.projects_root}")
        return found
    if since is not None:
        project = args.project_dir or os.path.join(args.projects_root, project_dir_name(os.getcwd()))
        if not os.path.isdir(project):
            raise SelectionError(f"usage.py: no project directory {project}")
        selected = []
        cutoff = since.timestamp()
        for path in sorted(glob.glob(os.path.join(project, "*.jsonl"))):
            # A file not changed since the cutoff cannot hold a later entry.
            related = [path] + glob.glob(os.path.join(path[: -len(".jsonl")], "subagents", "*.jsonl"))
            if any(os.path.getmtime(item) >= cutoff for item in related):
                selected.append(path)
        return selected
    raise SelectionError("usage.py: give --transcript, --session, or --since")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report tokens and estimated cost per agent.")
    parser.add_argument("--transcript", action="append", help="a main session transcript (.jsonl); repeatable")
    parser.add_argument("--session", help="a session ID, searched under --projects-root")
    parser.add_argument("--since", help="count only requests at or after this ISO 8601 time")
    parser.add_argument("--project-dir", help="the project's transcript directory for --since "
                        "(default: the one for the current directory)")
    parser.add_argument("--projects-root", default=PROJECTS_ROOT, help="default: ~/.claude/projects")
    parser.add_argument("--pricing", default=PRICING_PATH, help="the pricing table (default: pricing.json here)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    since = None
    if args.since:
        since = parse_time(args.since)
        if since is None:
            parser.error(f"--since is not an ISO 8601 time: {args.since}")
    try:
        transcripts = find_transcripts(args, since)
    except SelectionError as error:
        print(error, file=sys.stderr)
        return 2
    pricing = load_pricing(args.pricing)
    report = build_report(transcripts, pricing, since=since)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_table(report, pricing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
