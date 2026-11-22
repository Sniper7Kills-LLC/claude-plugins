#!/usr/bin/env python3
"""Tests for issue-flow/scripts/usage.py.

    python3 scripts/test-usage.py

The tests read only the recorded fixture under scripts/fixtures/usage/, never
~/.claude. The fixture copies the entry shapes of real transcripts, with every
prompt, path and name replaced. The expected numbers below are a hand count of
that fixture: each request's usage, de-duplicated by requestId, priced at the
rates in issue-flow/scripts/pricing.json.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "scripts", "usage.py")
FIXTURE_DIR = os.path.join(ROOT, "scripts", "fixtures", "usage")
SESSION = "00000000-0000-4000-8000-000000000001"
TRANSCRIPT = os.path.join(FIXTURE_DIR, SESSION + ".jsonl")

spec = importlib.util.spec_from_file_location("usage", SCRIPT)
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)

failures = []


def fail(message):
    failures.append(message)


def check(label, actual, expected):
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True,
    )


# The hand count. Prices per million tokens, from pricing.json:
#   opus-5-5   input 4, 5m write 5, 1h write 8, read 0.20, output 20
#   sonnet-5-5 input 2, 5m write 2.50, 1h write 4, read 0.10, output 10
#   haiku-5-5  input 0.10, 5m 0.125, 1h 0.20, read 0.01, output 0.50;
#              a prompt over 100000 tokens: 0.50, 0.625, 1, 0.05, 2.50
EXPECTED = {
    SESSION: {
        # req_main_1 appears on two lines and counts once.
        # 1000*4 + 2000*20 + 10000*0.2 + 1000*8          = 54000  -> 0.054
        # 500*4 + 1000*20 + 20000*0.2 + 2000*5            = 36000  -> 0.036
        # 100*4 + 400*20 + 30000*0.2                      = 14400  -> 0.0144
        "type": "main", "model": "claude-opus-5-5", "turns": 3,
        "input": 1600, "output": 3400, "cache_read": 60000, "cache_write": 3000,
        "cost_usd": 0.1044,
    },
    "a000000000000000a": {
        # Type from the parent's Agent call: no meta file exists.
        # req_a_1, two lines, prompt 10000: 2000*0.1 + 1000*0.5 + 8000*0.125 = 1700 -> 0.0017
        # req_a_2, prompt 101000 is over the threshold:
        #   1000*0.5 + 2000*2.5 + 100000*0.05                                  = 10500 -> 0.0105
        "type": "Explore", "model": "claude-haiku-5-5", "turns": 2,
        "input": 3000, "output": 3000, "cache_read": 100000, "cache_write": 8000,
        "cost_usd": 0.0122,
    },
    "a000000000000000b": {
        # The meta file wins over the parent's subagent_type.
        # 3000*2 + 5000*10 + 40000*0.1 + 4000*4 = 76000 -> 0.076
        # No 5m/1h split: the write counts as 5m.
        # 0 + 1000*10 + 50000*0.1 + 1000*2.5    = 17500 -> 0.0175
        "type": "issue-flow:issue-worker", "model": "claude-sonnet-5-5", "turns": 2,
        "input": 3000, "output": 6000, "cache_read": 90000, "cache_write": 5000,
        "cost_usd": 0.0935,
    },
    "a000000000000000c": {
        # No meta file and no parent call: the type is unknown.
        # The model is not in pricing.json: tokens, and no cost.
        "type": "unknown", "model": "claude-unknown-9", "turns": 1,
        "input": 10, "output": 20, "cache_read": 0, "cache_write": 0,
        "cost_usd": None,
    },
}

pricing = usage.load_pricing()
report = usage.build_report([TRANSCRIPT], pricing)

check("top-level keys", sorted(report), ["agents", "pricing_date", "totals"])
check("pricing_date matches pricing.json", report["pricing_date"], pricing["retrieved"])
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(report["pricing_date"])):
    fail(f"pricing_date must be YYYY-MM-DD, got {report['pricing_date']!r}")

agents = {agent["id"]: agent for agent in report["agents"]}
check("agent ids", sorted(agents), sorted(EXPECTED))
for agent_id, expected in EXPECTED.items():
    agent = agents.get(agent_id)
    if agent is None:
        continue
    check(f"{agent_id} keys", sorted(agent), sorted(
        ["type", "id", "model", "turns", "input", "output", "cache_read", "cache_write", "cost_usd"]
    ))
    for key, value in expected.items():
        check(f"{agent_id}.{key}", agent.get(key), value)

if report["agents"] and report["agents"][0]["id"] != SESSION:
    fail(f"the main agent must come first, got {report['agents'][0]['id']!r}")

totals = report["totals"]
check("totals.turns", totals["turns"], 8)
check("totals.input", totals["input"], 7610)
check("totals.output", totals["output"], 12420)
check("totals.cache_read", totals["cache_read"], 250000)
check("totals.cache_write", totals["cache_write"], 16000)
check("totals.cost_usd counts only priced models", totals["cost_usd"], 0.2101)
check("totals.unpriced_models", totals["unpriced_models"], ["claude-unknown-9"])
check("totals.skipped_lines counts the malformed lines", totals["skipped_lines"], 2)
check("totals.by_type keys", sorted(totals["by_type"]), sorted(
    ["main", "Explore", "issue-flow:issue-worker", "unknown"]
))
check("totals.by_type Explore turns", totals["by_type"].get("Explore", {}).get("turns"), 2)
check("totals.by_type main cost", totals["by_type"].get("main", {}).get("cost_usd"), 0.1044)

# --since drops the requests before the cutoff: req_main_1 is on 2026-11-01.
since = usage.parse_time("2026-11-02T00:00:00Z")
report = usage.build_report([TRANSCRIPT], pricing, since=since)
main = {a["id"]: a for a in report["agents"]}.get(SESSION, {})
check("--since main turns", main.get("turns"), 2)
check("--since main input", main.get("input"), 600)
check("--since main cost", main.get("cost_usd"), 0.0504)

# The CLI: --json prints the same report.
proc = run_cli("--transcript", TRANSCRIPT, "--json")
check("--json exit code", proc.returncode, 0)
try:
    cli_report = json.loads(proc.stdout)
    check("--json totals.cost_usd", cli_report["totals"]["cost_usd"], 0.2101)
    check("--json agent count", len(cli_report["agents"]), 4)
except (ValueError, KeyError) as error:
    fail(f"--json output does not parse as the report: {error}; stdout={proc.stdout[:300]!r}")

# The CLI: the table names every agent type and the totals.
proc = run_cli("--transcript", TRANSCRIPT)
check("table exit code", proc.returncode, 0)
for needle in ("issue-flow:issue-worker", "Explore", "unknown", "main", "TOTAL", "0.2101", "2026-"):
    if needle not in proc.stdout:
        fail(f"table must contain {needle!r}; stdout={proc.stdout!r}")
if "n/a" not in proc.stdout:
    fail("table must print n/a as the cost of an unpriced model")

# --session finds the transcript under the projects root.
with tempfile.TemporaryDirectory() as root:
    shutil.copytree(FIXTURE_DIR, os.path.join(root, "-fixture-project"))
    proc = run_cli("--session", SESSION, "--projects-root", root, "--json")
    check("--session exit code", proc.returncode, 0)
    if proc.returncode == 0:
        check("--session totals.turns", json.loads(proc.stdout)["totals"]["turns"], 8)

    # --since without a transcript reads every session in the project directory.
    proc = run_cli("--since", "2026-11-02T00:00:00Z", "--project-dir",
                   os.path.join(root, "-fixture-project"), "--json")
    check("--since exit code", proc.returncode, 0)
    if proc.returncode == 0:
        check("--since totals.turns", json.loads(proc.stdout)["totals"]["turns"], 7)

    proc = run_cli("--session", "no-such-session", "--projects-root", root)
    if proc.returncode == 0:
        fail("an unknown --session must exit non-zero")

# The project directory name is the cwd with every non-alphanumeric character
# replaced by "-" (https://code.claude.com/docs/en/sessions.md).
check("project_dir_name", usage.project_dir_name("/Users/a.b/my_repo"), "-Users-a-b-my-repo")

# Every priced model carries the date of its price.
for model, entry in pricing["models"].items():
    for key in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "date"):
        if key not in entry:
            fail(f"pricing.json model {model!r} lacks {key!r}")

# The script makes no network calls: it imports no network module.
with open(SCRIPT, encoding="utf-8") as handle:
    source = handle.read()
for module in ("urllib", "http", "socket", "requests", "ssl"):
    if re.search(rf"^\s*(import|from)\s+{module}\b", source, re.MULTILINE):
        fail(f"usage.py must not import {module}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("usage: all cases pass")
