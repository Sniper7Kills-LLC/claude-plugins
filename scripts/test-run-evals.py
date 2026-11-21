#!/usr/bin/env python3
"""Tests for scripts/run-evals.py and the fake tracker evals/fakegh/gh.

    python3 scripts/test-run-evals.py

The tests use a fake `claude` (set through IFE_CLAUDE), so they spend nothing
and make no network call. Every sandbox is a temporary directory.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "scripts", "run-evals.py")
FAKE_GH = os.path.join(ROOT, "evals", "fakegh", "gh")
EVALS = os.path.join(ROOT, "evals")

failures = []


def fail(message):
    failures.append(message)


def check(condition, message):
    if not condition:
        fail(message)


TRACKER = {
    "repo": {"owner": "acme", "name": "widgets", "defaultBranch": "main"},
    "user": "eval-bot",
    "labels": [
        {"name": "status:ready", "color": "0E8A16", "description": ""},
        {"name": "status:needs-feedback", "color": "FBCA04", "description": ""},
        {"name": "status:in-review", "color": "5319E7", "description": ""},
    ],
    "issues": [
        {"number": 1, "title": "Add a --verbose flag", "body": "Clear.", "state": "open",
         "labels": [], "assignees": [], "comments": []},
        {"number": 2, "title": "Make it better", "body": "Vague.", "state": "open",
         "labels": ["status:in-review"], "assignees": [],
         "comments": [{"id": 500, "author": "alice", "body": "plan", "createdAt": "2026-11-01T00:00:00Z"}]},
    ],
    "prs": [
        {"number": 7, "title": "Old PR", "body": "", "state": "open", "isDraft": True,
         "headRefName": "issue/2-better", "baseRefName": "batch/9-x", "labels": [], "comments": []},
    ],
}


# ---------------------------------------------------------------- fake gh


def gh_env(work):
    return dict(
        os.environ,
        IFE_TRACKER=os.path.join(work, "tracker.json"),
        IFE_GH_LOG=os.path.join(work, "gh-log.jsonl"),
    )


def gh(work, *args, stdin=None):
    return subprocess.run(
        [sys.executable, FAKE_GH, *args], capture_output=True, text=True,
        env=gh_env(work), cwd=work, input=stdin,
    )


def read_log(work):
    path = os.path.join(work, "gh-log.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_tracker(work):
    with open(os.path.join(work, "tracker.json")) as handle:
        return json.load(handle)


def fresh_gh_dir():
    work = tempfile.mkdtemp(prefix="fakegh-test-")
    with open(os.path.join(work, "tracker.json"), "w") as handle:
        json.dump(TRACKER, handle)
    return work


def test_fake_gh():
    work = fresh_gh_dir()
    try:
        result = gh(work, "issue", "list", "--state", "open", "--json", "number,labels")
        check(result.returncode == 0, f"issue list exits 0, got {result.returncode}: {result.stderr}")
        listed = json.loads(result.stdout or "[]")
        check([item["number"] for item in listed] == [1, 2], f"issue list returns issues 1 and 2, got {listed}")
        check(listed and set(listed[0]) == {"number", "labels"}, f"issue list keeps only the asked fields, got {listed[:1]}")
        check(listed[1]["labels"] == [{"name": "status:in-review"}], f"labels are objects with a name, got {listed[1:]}")

        result = gh(work, "issue", "edit", "2", "--add-label", "status:needs-feedback",
                    "--remove-label", "status:in-review")
        check(result.returncode == 0, f"issue edit exits 0, got {result.returncode}: {result.stderr}")
        labels = read_tracker(work)["issues"][1]["labels"]
        check(labels == ["status:needs-feedback"], f"issue edit swaps the labels, got {labels}")

        result = gh(work, "issue", "edit", "1", "--add-label", "status:nonexistent")
        check(result.returncode == 1, f"issue edit with an unknown label exits 1, got {result.returncode}")
        check(read_tracker(work)["issues"][0]["labels"] == [], "a refused edit changes nothing")

        body_file = os.path.join(work, "body.md")
        with open(body_file, "w") as handle:
            handle.write("Which screen do you mean?\n")
        result = gh(work, "issue", "comment", "1", "--body-file", body_file)
        check(result.returncode == 0, f"issue comment exits 0, got {result.returncode}: {result.stderr}")
        check("https://github.com/acme/widgets/issues/1#issuecomment-" in result.stdout,
              f"issue comment prints the comment URL, got {result.stdout!r}")
        comments = read_tracker(work)["issues"][0]["comments"]
        check(len(comments) == 1 and comments[0]["body"].startswith("Which screen"),
              f"issue comment stores the body, got {comments}")
        check(comments and comments[0]["id"] > 500, f"a new comment id is above every old id, got {comments}")

        result = gh(work, "issue", "comment", "1", "--body-file", "-", stdin="from stdin")
        check(result.returncode == 0 and read_tracker(work)["issues"][0]["comments"][-1]["body"] == "from stdin",
              "issue comment reads --body-file - from stdin")

        result = gh(work, "issue", "create", "--title", "New", "--body", "b", "--label", "status:ready")
        check(result.stdout.strip() == "https://github.com/acme/widgets/issues/8",
              f"issue create takes the next number after issues and PRs, got {result.stdout!r}")

        result = gh(work, "pr", "create", "--draft", "--base", "batch/9-x", "--head", "issue/1-verbose",
                    "--title", "Add verbose", "--body", "x")
        check(result.stdout.strip() == "https://github.com/acme/widgets/pull/9",
              f"pr create prints the PR URL, got {result.stdout!r} {result.stderr!r}")
        pr = [p for p in read_tracker(work)["prs"] if p["number"] == 9]
        check(pr and pr[0]["isDraft"] and pr[0]["baseRefName"] == "batch/9-x", f"pr create stores a draft PR, got {pr}")

        result = gh(work, "pr", "merge", "9", "--squash", "--subject", "s", "--body", "[skip ci]")
        pr = [p for p in read_tracker(work)["prs"] if p["number"] == 9]
        check(result.returncode == 0 and pr and pr[0]["state"] == "merged", f"pr merge marks the PR merged, got {pr}")
        result = gh(work, "pr", "list", "--state", "closed", "--json", "number")
        check(json.loads(result.stdout or "null") == [{"number": 9}], f"pr list --state closed includes a merged PR, got {result.stdout!r}")

        result = gh(work, "pr", "list", "--head", "issue/2-better", "--state", "open", "--json", "number")
        check(json.loads(result.stdout or "null") == [{"number": 7}], f"pr list filters by head, got {result.stdout!r}")

        result = gh(work, "api", "user")
        check(json.loads(result.stdout or "{}").get("login") == "eval-bot", f"api user returns the login, got {result.stdout!r}")

        result = gh(work, "api", "repos/{owner}/{repo}/issues/2/comments")
        check(result.returncode == 0 and json.loads(result.stdout or "[]")[0]["body"] == "plan",
              f"api reads the comments of an issue, got {result.stdout!r} {result.stderr!r}")

        result = gh(work, "api", "-X", "POST", "repos/{owner}/{repo}/issues/1/labels")
        check(result.returncode == 1, f"api write requests exit 1, got {result.returncode}")

        result = gh(work, "repo", "delete", "acme/widgets")
        check(result.returncode == 1, f"an unknown subcommand exits 1, got {result.returncode}")
        check("not supported by the eval fake" in result.stderr, f"an unknown subcommand names the fake, got {result.stderr!r}")

        if shutil.which("jq"):
            result = gh(work, "issue", "view", "2", "--json", "labels", "--jq", ".labels[].name")
            check(result.stdout.strip() == "status:needs-feedback", f"--jq filters the output, got {result.stdout!r}")

        log = read_log(work)
        kinds = [(entry["cmd"], entry["kind"]) for entry in log]
        check(kinds[0] == ("issue list", "read"), f"the log records reads, got {kinds[:1]}")
        check(("issue edit", "write") in kinds, f"the log records writes, got {kinds}")
        check(("repo delete", "error") in kinds, f"the log records unknown subcommands, got {kinds}")
        check(("api", "error") in kinds, f"the log records refused api writes, got {kinds}")
        edits = [entry for entry in log if entry["cmd"] == "issue edit" and entry["kind"] == "write"]
        check(edits and edits[0]["argv"][:3] == ["issue", "edit", "2"], f"the log keeps the argv, got {edits[:1]}")
        comment = [entry for entry in log if entry["cmd"] == "issue comment"]
        check(comment and comment[0].get("body", "").startswith("Which screen"), f"the log keeps a comment body, got {comment[:1]}")
        check([entry["seq"] for entry in log] == list(range(1, len(log) + 1)), "log entries carry a running seq")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- runner

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A stand-in for `claude -p` driven by environment variables."""
import json, os, subprocess, sys

# The runner gives a run only the variables it allows, so the test settings come from a file.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-env.json")) as handle:
    conf = json.load(handle)
argv = sys.argv[1:]
judge = "--tools" in argv
capture = conf.get("FAKE_CAPTURE")
if capture:
    with open(capture, "a") as handle:
        handle.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "judge": judge,
                                 "PATH": os.environ.get("PATH", ""),
                                 "GH_TOKEN": os.environ.get("GH_TOKEN"),
                                 "env": dict(os.environ),
                                 "remotes": subprocess.run(["git", "remote", "-v"], capture_output=True,
                                                           text=True).stdout}) + "\n")
if judge:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": conf.get("FAKE_JUDGE", "PASS: fine"),
                      "total_cost_usd": 0.01, "usage": {}, "num_turns": 1,
                      "duration_ms": 10, "session_id": "judge"}))
    sys.exit(0)
if conf.get("FAKE_PLUGIN_WRITE") and "--plugin-dir" in argv:
    with open(os.path.join(argv[argv.index("--plugin-dir") + 1], conf["FAKE_PLUGIN_WRITE"]), "w") as handle:
        handle.write("changed by the run\n")
for call in json.loads(conf.get("FAKE_PM_ACTIONS", "[]")):
    subprocess.run(["gh", *call], stdout=subprocess.DEVNULL)
for path, text in json.loads(conf.get("FAKE_PM_FILES", "{}")).items():
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "result": "Done: triaged #1.",
    "total_cost_usd": float(conf.get("FAKE_COST", "0.5")),
    "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30,
              "cache_creation_input_tokens": 40},
    "modelUsage": {"claude-test-model": {"inputTokens": 10, "outputTokens": 20, "costUSD": 0.5}},
    "num_turns": 4, "duration_ms": 1500, "session_id": "sess-123"}))
'''

CASE = {
    "name": "triage-basic",
    "suite": "pm",
    "tags": ["pm", "triage"],
    "prompt": "Triage the backlog.",
    "timeout_s": 60,
    "runs": 1,
    "fixture": "fixture",
    "tracker": "tracker.json",
    "graders": [
        {"type": "gh_log", "expect": [{"cmd": "issue edit", "args": r"^1 .*--add-label[ =]\S*status:ready"}],
         "forbid": [{"cmd": "pr merge"}]},
        {"type": "file", "path": ".eval/note.md", "regex": "verbose"},
        {"type": "llm", "target": "final", "rubric": "The final message names issue #1."},
    ],
}

RECORD_KEYS = {"case", "run", "variant", "ok", "score", "graders", "cost_usd", "usage", "turns",
               "duration_s", "session_id", "model", "error"}


def build_evals(root, case=CASE):
    case_dir = os.path.join(root, "evals", "pm", case["name"])
    os.makedirs(os.path.join(case_dir, "fixture"))
    with open(os.path.join(case_dir, "fixture", "README.md"), "w") as handle:
        handle.write("fixture\n")
    with open(os.path.join(case_dir, "case.json"), "w") as handle:
        json.dump(case, handle)
    with open(os.path.join(case_dir, "tracker.json"), "w") as handle:
        json.dump(TRACKER, handle)
    return os.path.join(root, "evals")


def run_runner(root, extra_env, *args):
    fake = os.path.join(root, "fake-claude")
    with open(fake, "w") as handle:
        handle.write(FAKE_CLAUDE)
    os.chmod(fake, 0o755)
    plugin = os.path.join(root, "plugin")
    os.makedirs(os.path.join(plugin, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(plugin, ".claude-plugin", "plugin.json"), "w") as handle:
        json.dump({"name": "issue-flow", "version": "9.9.9"}, handle)
    conf = {key: value for key, value in extra_env.items() if key.startswith("FAKE_")}
    conf["FAKE_CAPTURE"] = os.path.join(root, "capture.jsonl")
    with open(os.path.join(root, "fake-env.json"), "w") as handle:
        json.dump(conf, handle)
    env = dict(os.environ, IFE_CLAUDE=fake, **extra_env)
    out = os.path.join(root, "out")
    result = subprocess.run(
        [sys.executable, RUNNER, "--plugin-dir", plugin, "--suite", "pm", "--evals-dir",
         os.path.join(root, "evals"), "--out", out, *args],
        capture_output=True, text=True, env=env, cwd=root, timeout=120,
    )
    results_path = os.path.join(out, "results.json")
    results = None
    if os.path.exists(results_path):
        with open(results_path) as handle:
            results = json.load(handle)
    captures = []
    if os.path.exists(os.path.join(root, "capture.jsonl")):
        with open(os.path.join(root, "capture.jsonl")) as handle:
            captures = [json.loads(line) for line in handle if line.strip()]
    return result, results, captures


GOOD_ACTIONS = json.dumps([["issue", "edit", "1", "--add-label", "status:ready"]])
GOOD_FILES = json.dumps({".eval/note.md": "added verbose\n"})


def test_runner_happy_path():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        result, results, captures = run_runner(
            root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES})
        check(result.returncode == 0, f"a passing suite exits 0, got {result.returncode}: {result.stderr}{result.stdout}")
        if not results:
            fail("the runner writes results.json")
            return
        check(results.get("partial") is False, f"a complete suite is not partial, got {results.get('partial')}")
        records = results.get("records", [])
        check(len(records) == 1, f"one record per run, got {len(records)}")
        if not records:
            return
        record = records[0]
        check(RECORD_KEYS <= set(record), f"the record carries every contract key, missing {RECORD_KEYS - set(record)}")
        check(record["case"] == "triage-basic" and record["run"] == 1, f"case and run, got {record.get('case')} {record.get('run')}")
        check(record["ok"] is True and record["score"] == 1.0, f"all graders pass, got ok={record.get('ok')} score={record.get('score')} {record.get('graders')}")
        check(record["cost_usd"] == 0.5, f"cost_usd comes from total_cost_usd, got {record.get('cost_usd')}")
        check(record["usage"] == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40},
              f"usage maps the claude field names, got {record.get('usage')}")
        check(record["turns"] == 4 and record["duration_s"] == 1.5, f"turns and duration, got {record.get('turns')} {record.get('duration_s')}")
        check(record["session_id"] == "sess-123", f"session_id, got {record.get('session_id')}")
        check(record["model"] == "claude-test-model", f"model comes from modelUsage when none is asked, got {record.get('model')}")
        check(record["error"] is None, f"no error, got {record.get('error')}")
        check(record.get("judge_cost_usd") == 0.01, f"the judge cost is recorded apart, got {record.get('judge_cost_usd')}")
        check(record["variant"].get("version") == "9.9.9", f"the variant names the plugin version, got {record.get('variant')}")
        check([g["type"] for g in record["graders"]] == ["gh_log", "file", "llm"], f"grader order, got {record.get('graders')}")
        check(results.get("total_cost_usd") == 0.51, f"the total adds the judge cost, got {results.get('total_cost_usd')}")

        main = [c for c in captures if not c["judge"]]
        check(len(main) == 1, f"claude runs once for the case, got {len(main)}")
        if main:
            argv = main[0]["argv"]
            check(argv[:2] == ["-p", "Triage the backlog."], f"the prompt goes to -p, got {argv[:2]}")
            for flag, value in (("--output-format", "json"), ("--permission-mode", "bypassPermissions")):
                check(flag in argv and argv[argv.index(flag) + 1] == value, f"{flag} {value} is passed, got {argv}")
            check("--strict-mcp-config" in argv, f"MCP servers are off, got {argv}")
            settings = json.loads(argv[argv.index("--settings") + 1]) if "--settings" in argv else {}
            check(settings.get("enabledPlugins", {}).get("issue-flow@sniper7kills") is False,
                  f"the installed plugin is turned off, got {settings}")
            first_path = main[0]["PATH"].split(os.pathsep)[0]
            check(os.path.isfile(os.path.join(first_path, "gh")), f"the fake gh is first on PATH, got {first_path}")
            check(not main[0]["cwd"].startswith(ROOT) and not main[0]["cwd"].startswith(root),
                  f"claude runs in a temp sandbox, got {main[0]['cwd']}")
            remotes = main[0]["remotes"].split()
            check(len(remotes) == 6 and remotes[0] == "origin"
                  and os.path.realpath(remotes[1])
                  == os.path.join(os.path.dirname(os.path.realpath(main[0]["cwd"])), "origin.git"),
                  f"the only remote is a local bare repo, got {main[0]['remotes']!r}")
            check(main[0]["GH_TOKEN"] and main[0]["GH_TOKEN"] != os.environ.get("GH_TOKEN"),
                  "GH_TOKEN is a fake token, so a real gh cannot authenticate")
            check(not os.path.exists(main[0]["cwd"]), "the sandbox is removed after the run")
            given = argv[argv.index("--plugin-dir") + 1] if "--plugin-dir" in argv else ""
            check(given and os.path.realpath(given) != os.path.realpath(os.path.join(root, "plugin")),
                  f"the run gets a copy of the plugin, not the plugin directory, got {given}")
            check(given and not os.path.exists(given), f"the plugin copy is removed with the sandbox, got {given}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_failures_and_ceiling():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        bad = json.dumps([["issue", "edit", "1", "--add-label", "status:ready"], ["pr", "merge", "7", "--squash"]])
        result, results, _ = run_runner(root, {"FAKE_PM_ACTIONS": bad, "FAKE_PM_FILES": GOOD_FILES,
                                               "FAKE_JUDGE": "FAIL: no"})
        check(result.returncode == 1, f"a failing suite exits 1, got {result.returncode}")
        record = (results or {}).get("records", [{}])[0]
        graders = record.get("graders", [])
        check(record.get("ok") is False, f"a forbidden call fails the run, got {record.get('ok')}")
        check(graders and graders[0]["passed"] is False and "pr merge" in graders[0]["detail"],
              f"the gh_log detail names the forbidden call, got {graders[:1]}")
        check(abs(record.get("score", 0) - 1 / 3) < 1e-9, f"score is the fraction passed, got {record.get('score')}")
        check("deterministic" in result.stdout and "llm" in result.stdout,
              f"the summary shows deterministic and llm results apart, got {result.stdout!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        result, results, captures = run_runner(
            root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES, "FAKE_COST": "2"},
            "--runs", "3", "--max-cost-usd", "3")
        records = (results or {}).get("records", [])
        check(len(records) == 2, f"the ceiling stops the third run, got {len(records)} records")
        check((results or {}).get("partial") is True, f"a stopped suite is partial, got {(results or {}).get('partial')}")
        main = [c for c in captures if not c["judge"]]
        check(main and "--max-budget-usd" in main[0]["argv"]
              and float(main[0]["argv"][main[0]["argv"].index("--max-budget-usd") + 1]) == 3.0,
              f"each run gets the remaining budget as its own cap, got {main[:1]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_args_anchor_ignores_body_lines():
    runner = load_runner()
    entry = {"seq": 1, "cmd": "issue comment", "kind": "write",
             "argv": ["issue", "comment", "2", "--body", "Questions:\n1. Which command?\n4. How often?"],
             "body": "Questions:\n1. Which command?\n4. How often?"}
    check(not runner.matches({"cmd": "issue comment", "args": r"^4\b"}, entry),
          "an args ^ anchors at the start of the arguments, not at a line of the body")
    check(runner.matches({"cmd": "issue comment", "args": r"^2\b"}, entry), "an args ^ matches the issue number")
    check(runner.matches({"cmd": "issue comment", "body": r"^4\. How"}, entry), "a body ^ still matches each line")


def test_ordered_and_model():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        case = dict(CASE, graders=[{"type": "gh_log", "ordered": True, "expect": [
            {"cmd": "issue comment", "args": "^2 ", "body": "^finding: "},
            {"cmd": "issue edit", "args": r"^1 .*status:ready"},
        ]}])
        build_evals(root, case)
        wrong_order = json.dumps([["issue", "edit", "1", "--add-label", "status:ready"],
                                  ["issue", "comment", "2", "--body", "finding: x"]])
        result, results, captures = run_runner(root, {"FAKE_PM_ACTIONS": wrong_order}, "--main-model", "claude-x")
        record = (results or {}).get("records", [{}])[0]
        check(record.get("ok") is False, f"ordered expectations fail out of order, got {record.get('graders')}")
        check(record.get("model") == "claude-x", f"--main-model wins, got {record.get('model')}")
        main = [c for c in captures if not c["judge"]]
        check(main and main[0]["argv"][main[0]["argv"].index("--model") + 1] == "claude-x",
              "--main-model is passed to claude")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def load_runner():
    spec = importlib.util.spec_from_file_location("run_evals", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_pm_suite():
    runner = load_runner()
    cases = runner.load_cases(EVALS, "pm")
    check(5 <= len(cases) <= 7, f"the pm suite has 5 to 7 cases, got {len(cases)}")
    for case in cases:
        name = case["name"]
        check("pm" in case["tags"] and len(case["tags"]) >= 2, f"{name}: tagged pm plus a stage, got {case['tags']}")
        deterministic = [g for g in case["graders"] if g["type"] != "llm"]
        check(deterministic, f"{name}: has at least one deterministic grader")
        check(any(g["type"] == "llm" for g in case["graders"]), f"{name}: has an llm grader as a second score")
        with open(case["tracker_path"]) as handle:
            tracker = json.load(handle)
        check({"repo", "user", "labels", "issues", "prs"} <= set(tracker), f"{name}: tracker.json has every key")
        check(os.path.isfile(os.path.join(case["fixture_path"], ".issue-flow.json")),
              f"{name}: the fixture carries a run configuration")

    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        result, results, captures = run_runner(root, {}, "--evals-dir", EVALS, "--tag", "pm", "--runs", "1")
        records = (results or {}).get("records", [])
        check(len(records) == len(cases), f"every real case runs with the fake claude, got {len(records)} {result.stderr}")
        check(all(r["error"] is None for r in records), f"no real case errors, got {[r['error'] for r in records]}")
        check(all(r["ok"] is False for r in records), "a PM that does nothing fails every real case")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def repair(number, removed, kept):
    return [["issue", "edit", str(number), "--remove-label", removed],
            ["issue", "comment", str(number), "--body", f"status repair: removed {removed}, kept {kept}"]]


# What a correct PM does in each real case. It proves that each case can pass.
GOLDEN = {
    "pm-triage": ([["issue", "edit", "1", "--add-label", "status:ready"],
                   ["issue", "edit", "2", "--add-label", "status:needs-feedback"],
                   ["issue", "comment", "2", "--body", "Which command shows the wrong number?"]], {}),
    "pm-c1-unevidenced": ([], {".eval/send-back-11.md":
                               "Criterion `--json and --verbose cannot be combined` has no evidence."}),
    "pm-c1-no-prepatch": ([], {".eval/send-back-11.md": "localChecks has no pre-patch run."}),
    "pm-a0-split-status": (repair(21, "status:in-review", "status:needs-feedback")
                           + repair(22, "status:in-review", "status:batched")
                           + repair(24, "status:ready", "status:blocked"), {}),
    "pm-batch-cross-check": ([["issue", "comment", "30", "--body", "finding: batch cross-check, batch #30\n\nClean."],
                              ["issue", "edit", "31", "--add-label", "status:in-progress",
                               "--remove-label", "status:ready"]],
                             {".eval/brief-31.md":
                              "crossCheck: https://github.com/acme/widgets/issues/30#issuecomment-3202\n"}),
    "pm-semantic-conflict": ([["issue", "comment", "40", "--body", "finding: batch cross-check, batch #40"],
                              ["issue", "edit", "41", "42", "--add-label", "status:needs-feedback",
                               "--remove-label", "status:ready"],
                              ["issue", "comment", "41", "--body", "Reject or queue?"],
                              ["issue", "comment", "42", "--body", "Reject or queue?"]], {}),
}


def test_real_cases_can_pass():
    names = [case["name"] for case in load_runner().load_cases(EVALS, "pm")]
    check(sorted(names) == sorted(GOLDEN), f"every real case has a golden run, got {names}")
    for name, (actions, files) in GOLDEN.items():
        root = tempfile.mkdtemp(prefix="run-evals-test-")
        try:
            result, results, _ = run_runner(
                root, {"FAKE_PM_ACTIONS": json.dumps(actions), "FAKE_PM_FILES": json.dumps(files)},
                "--evals-dir", EVALS, "--case", name, "--runs", "1")
            records = (results or {}).get("records", [])
            check(records and records[0]["ok"] and records[0]["score"] == 1.0,
                  f"{name}: the golden run passes, got {records[0]['graders'] if records else result.stderr}")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# The variables a host Claude Code session sets for its Bash commands and hooks.
HOST_SESSION_ENV = {
    "CLAUDE_CODE_SESSION_ID": "host-session", "CLAUDE_CODE_CHILD_NOTES_DIR": "/tmp/host-scratchpad",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/host.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "host-token",
    "CLAUDE_EFFORT": "low", "CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_SESSION_ATTENDED": "1", "CLAUDE_CODE_EFFORT_LEVEL": "max",
    "CLAUDE_CODE_ENTRYPOINT": "cli", "ANTHROPIC_MODEL": "host-model",
}
# The variables that authenticate claude or reach the network; a run keeps them.
HOST_KEPT_ENV = {"ANTHROPIC_API_KEY": "sk-host", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-host",
                 "CLAUDE_CONFIG_DIR": "/tmp/host-config", "HTTPS_PROXY": "http://proxy.invalid:3128",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "host-profile", "LANG": "en_US.UTF-8"}


def test_host_session_env_is_dropped():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        result, _, captures = run_runner(
            root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES,
                   **HOST_SESSION_ENV, **HOST_KEPT_ENV})
        check(len(captures) == 2, f"the case and its judge both run, got {len(captures)}: {result.stderr}")
        for capture in captures:
            env = capture.get("env", {})
            who = "the judge" if capture["judge"] else "the run"
            leaked = sorted(name for name in HOST_SESSION_ENV if name in env)
            check(not leaked, f"{who} gets none of the host session variables, got {leaked}")
            # The host session can set more CLAUDE* variables than the test sets.
            other = sorted(name for name in env if name.startswith(("CLAUDE", "ANTHROPIC_"))
                           and name not in HOST_KEPT_ENV)
            check(not other, f"{who} gets no other CLAUDE* or ANTHROPIC_* variable, got {other}")
            missing = sorted(name for name, value in HOST_KEPT_ENV.items() if env.get(name) != value)
            check(not missing, f"{who} keeps the auth, provider, proxy and locale variables, missing {missing}")
            check(env.get("HOME") == os.environ.get("HOME"), f"{who} keeps HOME, got {env.get('HOME')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_effort_is_passed_and_recorded():
    for args, expected in (((), "medium"), (("--effort", "high"), "high")):
        root = tempfile.mkdtemp(prefix="run-evals-test-")
        try:
            build_evals(root)
            result, results, captures = run_runner(
                root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES}, *args)
            main = [c for c in captures if not c["judge"]]
            argv = main[0]["argv"] if main else []
            check("--effort" in argv and argv[argv.index("--effort") + 1] == expected,
                  f"{args or 'no flag'}: the run gets --effort {expected}, got {argv}: {result.stderr}")
            records = (results or {}).get("records", [])
            check(records and records[0].get("effort") == expected,
                  f"{args or 'no flag'}: the record names effort {expected}, got {records[:1]}")
            check((results or {}).get("effort") == expected,
                  f"{args or 'no flag'}: results.json names effort {expected}, got {(results or {}).get('effort')}")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        result, _, captures = run_runner(root, {}, "--effort", "extreme")
        check(result.returncode == 2 and not captures,
              f"an unknown effort stops the runner before any run, got exit {result.returncode}, {len(captures)} runs")
    finally:
        shutil.rmtree(root, ignore_errors=True)


GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_SSH_COMMAND": "false", "GIT_TERMINAL_PROMPT": "0"}


def test_git_isolation_env():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root)
        result, _, captures = run_runner(root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES})
        check(len(captures) == 2, f"the case and its judge both run, got {len(captures)}: {result.stderr}")
        for capture in captures:
            env = capture.get("env", {})
            who = "the judge" if capture["judge"] else "the run"
            wrong = {name: env.get(name) for name, value in GIT_ISOLATION.items() if env.get(name) != value}
            check(not wrong, f"{who} gets no host git config, no ssh and no git prompt, got {wrong}")
            check(env.get("GIT_AUTHOR_NAME") and env.get("GIT_COMMITTER_EMAIL"),
                  f"{who} has a git identity without the host config, got {env.get('GIT_AUTHOR_NAME')}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_plugin_copy_protects_the_plugin():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        case = dict(CASE, prompt="Read {plugin_dir}/README.md.")
        build_evals(root, case)
        result, _, captures = run_runner(root, {"FAKE_PM_ACTIONS": GOOD_ACTIONS, "FAKE_PM_FILES": GOOD_FILES,
                                                "FAKE_PLUGIN_WRITE": "planted.txt"})
        check(not os.path.exists(os.path.join(root, "plugin", "planted.txt")),
              f"a run that writes into its --plugin-dir does not change the plugin under test: {result.stderr}")
        main = [c for c in captures if not c["judge"]]
        check(main, "the case runs")
        if main:
            argv = main[0]["argv"]
            given = argv[argv.index("--plugin-dir") + 1]
            check(argv[1] == f"Read {given}/README.md.",
                  f"{{plugin_dir}} in the prompt names the copy that the run loads, got {argv[1]!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def planted_repo(root):
    """A repo whose .git/config runs a marker script from core.fsmonitor and from a post-checkout hook."""
    repo = os.path.join(root, "repo")
    marker = os.path.join(root, "planted-code-ran")
    hooks = os.path.join(root, "hooks")
    os.makedirs(hooks)
    script = f"#!/bin/sh\necho ran >> {marker}\n"
    for name in ("post-checkout", "fsmonitor"):
        with open(os.path.join(hooks, name), "w") as handle:
            handle.write(script)
        os.chmod(os.path.join(hooks, name), 0o755)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "x"],
                 ["config", "core.hooksPath", hooks], ["config", "core.fsmonitor", os.path.join(hooks, "fsmonitor")]):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True,
                       capture_output=True)
    return repo, marker


def test_agent_repo_cannot_run_code_on_the_host():
    runner = load_runner()
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    saved = {name: os.environ.get(name) for name in ("GIT_DIR", "ANTHROPIC_API_KEY")}
    try:
        repo, marker = planted_repo(root)
        runner.git(repo, "status", "--porcelain")
        runner.git(repo, "checkout", "-q", "-b", "issue/1-x")
        check(not os.path.exists(marker), "the runner's git ignores a hook and an fsmonitor that the agent planted")

        # A host variable such as GIT_DIR must not redirect the runner's git.
        os.environ["GIT_DIR"] = os.path.join(root, "not-a-repo")
        try:
            head = runner.git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        except RuntimeError as error:
            head = repr(error)
        check(head == "issue/1-x", f"the runner's git takes no host GIT_* variable, got {head}")
        del os.environ["GIT_DIR"]

        # The hidden tests run the agent's code; they get no host secret.
        os.environ["ANTHROPIC_API_KEY"] = "sk-host-secret"
        dump = os.path.join(root, "hidden-env.json")
        case_dir = os.path.join(root, "case")
        os.makedirs(os.path.join(case_dir, "hidden"))
        with open(os.path.join(case_dir, "hidden", "hidden_test_env.py"), "w") as handle:
            handle.write("import json, os, unittest\n"
                         f"json.dump(dict(os.environ), open({dump!r}, 'w'))\n"
                         "class T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        ctx = {"repo": repo, "sandbox": root, "case": {"dir": case_dir}}
        passed, detail, _ = runner.grade_hidden_tests({"type": "hidden_tests", "issue": 1}, ctx)
        check(passed, f"the hidden tests run on the branch, got {detail}")
        env = {}
        if os.path.exists(dump):
            with open(dump) as handle:
                env = json.load(handle)
        check(env and "ANTHROPIC_API_KEY" not in env, f"the hidden tests get no host secret, got {sorted(env)[:20]}")
        check(not os.path.exists(marker), "the grading clone runs no hook that the agent planted")
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(root, ignore_errors=True)


def test_a_case_needs_a_deterministic_grader():
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    try:
        build_evals(root, dict(CASE, graders=[{"type": "llm", "target": "final", "rubric": "x"}]))
        result, _, captures = run_runner(root, {})
        check(result.returncode == 2 and "deterministic" in result.stderr and not captures,
              f"a case with only an llm grader is a bad case file, got exit {result.returncode}: {result.stderr}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_claude_timeout():
    runner = load_runner()
    root = tempfile.mkdtemp(prefix="run-evals-test-")
    saved = os.environ.get("IFE_CLAUDE")
    try:
        child_marker = os.path.join(root, "child-still-ran")
        slow = os.path.join(root, "slow-claude")
        with open(slow, "w") as handle:
            # The child outlives its parent unless the whole process group is stopped.
            handle.write(f"#!/bin/sh\n(sleep 3; touch {child_marker}) &\nsleep 30\n")
        os.chmod(slow, 0o755)
        os.environ["IFE_CLAUDE"] = slow
        started = time.time()
        code, stdout, _, timed_out = runner.run_claude(["-p", "x"], root, {"PATH": os.environ["PATH"]}, 1)
        check(timed_out is True and time.time() - started < 10,
              f"run_claude stops a run at its time limit, got timed_out={timed_out}")
        check(runner.parse_result(stdout) is None, "a stopped run has no result object")
        time.sleep(4)
        check(not os.path.exists(child_marker), "run_claude stops the children of a stopped run too")
    finally:
        if saved is None:
            os.environ.pop("IFE_CLAUDE", None)
        else:
            os.environ["IFE_CLAUDE"] = saved
        shutil.rmtree(root, ignore_errors=True)


def test_readme_names_the_environment():
    with open(os.path.join(EVALS, "README.md")) as handle:
        readme = handle.read()
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_SSH_COMMAND", "GIT_TERMINAL_PROMPT",
                 "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_NOTES_DIR", "CLAUDE_CODE_MESSAGING_",
                 "CLAUDE_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDECODE", "ANTHROPIC_API_KEY",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "--effort"):
        check(f"`{name}" in readme, f"evals/README.md names {name}")


for test in (test_fake_gh, test_runner_happy_path, test_runner_failures_and_ceiling,
             test_args_anchor_ignores_body_lines, test_ordered_and_model, test_real_pm_suite,
             test_real_cases_can_pass, test_host_session_env_is_dropped, test_effort_is_passed_and_recorded,
             test_readme_names_the_environment, test_git_isolation_env, test_plugin_copy_protects_the_plugin,
             test_agent_repo_cannot_run_code_on_the_host, test_a_case_needs_a_deterministic_grader,
             test_run_claude_timeout):
    try:
        test()
    except Exception as error:  # a crash in one test must not hide the others
        fail(f"{test.__name__} crashed: {error!r}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("run-evals: all cases pass")
