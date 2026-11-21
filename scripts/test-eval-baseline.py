#!/usr/bin/env python3
"""Tests for scripts/eval-baseline.py.

    python3 scripts/test-eval-baseline.py

The tests give the script a synthetic results.json whose records point at the
recorded usage fixture in scripts/fixtures/usage/. They never read ~/.claude.
The token counts below are the fixture's hand count in scripts/test-usage.py.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "eval-baseline.py")
SESSION = "00000000-0000-4000-8000-000000000001"
FIXTURES = os.path.join(ROOT, "scripts", "fixtures", "usage")
TMP = tempfile.mkdtemp(prefix="test-eval-baseline-")

failures = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("       " + str(detail)[:1500])


def record(case, run, session_id, transcript=None, cost=0.25, hidden=True, wall=100.0):
    return {
        "case": case, "run": run,
        "variant": {"plugin_dir": "/Users/someone/wt/issue-flow", "git_sha": "abc", "version": "0.20.0"},
        "ok": hidden, "score": 1.0 if hidden else 0.5,
        "graders": [{"type": "hidden_tests", "passed": hidden, "detail": ""},
                    {"type": "verdict", "passed": True, "detail": ""}],
        "deterministic": {"passed": 1 + hidden, "total": 2}, "llm": {"passed": 0, "total": 0},
        "cost_usd": cost, "judge_cost_usd": 0.0,
        "usage": {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4},
        "model_usage": {"claude-haiku-5-5": {"costUSD": 0.01}, "claude-opus-5-5": {"costUSD": cost - 0.01}},
        "turns": 3, "duration_s": 5.0, "wall_s": wall, "session_id": session_id,
        "transcript": transcript, "model": "haiku", "error": None, "sandbox": "/private/tmp/ife-sandbox",
    }


def write_input(records):
    directory = os.path.join(TMP, "in")
    os.makedirs(directory, exist_ok=True)
    results = {"suite": "worker", "effort": "high",
               "variant": {"plugin_dir": "/Users/someone/wt/issue-flow", "git_sha": "abc", "version": "0.20.0"},
               "started": "20261121T000000Z", "finished": "20261121T010000Z", "max_cost_usd": 150.0,
               "total_cost_usd": sum(r["cost_usd"] for r in records), "partial": False, "records": records}
    with open(os.path.join(directory, "results.json"), "w") as handle:
        json.dump(results, handle)
    return directory


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


def load(path):
    with open(path) as handle:
        return json.load(handle)


def test_annotate_strip_and_summary():
    print("annotate, strip and summarize")
    # The projects root holds one project directory with the fixture session in it.
    projects = os.path.join(TMP, "projects")
    shutil.copytree(FIXTURES, os.path.join(projects, "-private-tmp-sandbox-repo"))
    records = [
        record("w-a", 1, SESSION),  # found by session id under the projects root
        record("w-a", 2, SESSION, transcript=os.path.join(FIXTURES, SESSION + ".jsonl"), cost=0.35,
               hidden=False, wall=300.0),
    ]
    out = os.path.join(TMP, "out")
    result = run(write_input(records), "--out", out, "--label", "issue-flow (tag issue-flow--v0.20.0)",
                 "--projects-root", projects)
    check("exits 0 when every transcript is found", result.returncode == 0, result.stdout + result.stderr)

    data = load(os.path.join(out, "results.json"))
    first = data["records"][0]
    check("a record gains the usage.py report", first.get("agents", {}).get("totals", {}).get("turns") == 8,
          first.get("agents"))
    check("main tokens are the main agent's four counts", first.get("tokens", {}).get("main") == 68000,
          first.get("tokens"))
    check("subagent tokens are every other agent's counts", first.get("tokens", {}).get("subagents") == 218030,
          first.get("tokens"))
    text = json.dumps(data)
    check("no local path stays in the results", "/Users/" not in text and "/private/" not in text
          and FIXTURES not in text, [line for line in text.split(",") if "/Users/" in line or "/private/" in line])
    check("the label replaces plugin_dir", data["variant"]["plugin_dir"] == "issue-flow (tag issue-flow--v0.20.0)"
          and first["variant"]["plugin_dir"] == data["variant"]["plugin_dir"], data["variant"])
    check("the session id stays", first["session_id"] == SESSION, first)

    summary = load(os.path.join(out, "summary.json"))
    case = summary["cases"]["w-a"]
    check("summary: ok rate", case["ok_rate"] == 0.5, case)
    check("summary: hidden-test pass rate", case["hidden_pass_rate"] == 0.5, case)
    check("summary: mean cost", abs(case["mean_cost_usd"] - 0.30) < 1e-9, case)
    check("summary: cost range", case["cost_range"] == [0.25, 0.35], case)
    check("summary: wall time is the duration of a run that has it", case["mean_duration_s"] == 200.0, case)
    check("summary: mean main and subagent tokens",
          case["mean_main_tokens"] == 68000 and case["mean_subagent_tokens"] == 218030, case)
    check("summary: mean subagent turns", case["mean_subagent_turns"] == 5, case)
    check("summary: the models of the suite", summary["models"] == ["claude-haiku-5-5", "claude-opus-5-5"],
          summary.get("models"))
    check("summary: the total cost", abs(summary["total_cost_usd"] - 0.60) < 1e-9, summary)
    check("summary: the effort of the run", summary.get("effort") == "high", summary.get("effort"))


def test_missing_transcript():
    print("missing transcript")
    out = os.path.join(TMP, "out-missing")
    result = run(write_input([record("w-b", 1, "no-such-session")]), "--out", out, "--label", "x",
                 "--projects-root", os.path.join(TMP, "empty"))
    check("exits 1 when a transcript is missing", result.returncode == 1, result.returncode)
    check("names the run whose transcript is missing", "w-b run 1" in result.stderr, result.stderr)
    data = load(os.path.join(out, "results.json"))
    check("the record keeps agents null", data["records"][0].get("agents") is None, data["records"][0])


try:
    test_annotate_strip_and_summary()
    test_missing_transcript()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

if failures:
    print("\n%d failure(s)" % len(failures))
    sys.exit(1)
print("\nall eval-baseline checks passed")
