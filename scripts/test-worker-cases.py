#!/usr/bin/env python3
"""Tests for the worker eval suite in evals/worker/ and its graders in run-evals.py.

    python3 scripts/test-worker-cases.py

The tests spend nothing and make no network call. A fake `claude` (set
through IFE_CLAUDE) stands in for the dispatcher and the worker. Every
sandbox is a temporary directory.
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
RUNNER = os.path.join(ROOT, "scripts", "run-evals.py")
EVALS = os.path.join(ROOT, "evals")
WORKER = os.path.join(EVALS, "worker")
FIXTURE = os.path.join(WORKER, "_fixture")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
TIERS = {"easy": 2, "medium": 3, "hard": 3}

failures = []


def fail(message):
    failures.append(message)


def check(condition, message):
    if not condition:
        fail(message)


def load_runner():
    spec = importlib.util.spec_from_file_location("run_evals", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV))


def unittest_run(cwd, pattern):
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", pattern],
                          cwd=cwd, capture_output=True, text=True, timeout=120)


def worker_cases():
    return load_runner().load_cases(EVALS, "worker")


def acceptance_criteria(case):
    with open(case["tracker_path"]) as handle:
        tracker = json.load(handle)
    return load_runner().acceptance_criteria(tracker, issue_of(case))


def issue_of(case):
    return next(g["issue"] for g in case["graders"] if "issue" in g)


def graders_of(case, kind):
    return [g for g in case["graders"] if g["type"] == kind]


def is_ambiguous(case):
    return any(g.get("outcome") == "needs-feedback" for g in graders_of(case, "verdict"))


# ---------------------------------------------------------------- suite shape


def test_suite_shape():
    cases = worker_cases()
    check(len(cases) == 8, f"the worker suite has 8 cases, got {len(cases)}")
    tiers = {tier: 0 for tier in TIERS}
    for case in cases:
        name = case["name"]
        check(case["suite"] == "worker", f"{name}: suite is worker")
        tags = case["tags"]
        check("worker" in tags, f"{name}: tagged worker, got {tags}")
        found = [tier for tier in TIERS if tier in tags]
        check(len(found) == 1, f"{name}: tagged with exactly one of easy, medium, hard, got {tags}")
        for tier in found:
            tiers[tier] += 1
        check(os.path.realpath(case["fixture_path"]) == os.path.realpath(FIXTURE),
              f"{name}: starts from evals/worker/_fixture")
        check(case.get("model") == "haiku", f"{name}: the dispatcher runs on haiku, got {case.get('model')}")
        prompt = case["prompt"]
        for needle in ("issue-flow:issue-worker", '"worktree"', "{plugin_dir}", ".eval/verdict.json",
                       f"#{issue_of(case)}", "ci:           skip", "batch:        standalone"):
            check(needle in prompt, f"{name}: the prompt holds {needle!r}")
        # A dispatcher that sleeps to hold its turn makes wall_s measure the sleep, not the worker.
        check("Do not run `sleep`" in prompt and "Do not end your turn" not in prompt,
              f"{name}: the prompt tells the dispatcher not to sleep or poll while it waits")
        check(acceptance_criteria(case), f"{name}: the tracker issue has acceptance criteria")
        check(graders_of(case, "verdict"), f"{name}: has a verdict grader")
        check(graders_of(case, "diff_scope"), f"{name}: has a diff_scope grader")
        hidden = os.path.join(case["dir"], "hidden")
        patch = os.path.join(case["dir"], "reference.patch")
        if is_ambiguous(case):
            check(not graders_of(case, "hidden_tests"), f"{name}: an ambiguous case has no hidden tests")
            check(any(g.get("no_commits") for g in graders_of(case, "diff_scope")),
                  f"{name}: an ambiguous case requires no commits")
            check(not os.path.exists(patch), f"{name}: an ambiguous case has no reference patch")
        else:
            check(graders_of(case, "hidden_tests"), f"{name}: has a hidden_tests grader")
            check(any(g.get("prepatch") for g in graders_of(case, "verdict")),
                  f"{name}: the verdict grader requires a pre-patch run")
            check(any(g.get("allow") for g in graders_of(case, "diff_scope")),
                  f"{name}: the diff_scope grader has an allowlist")
            check(os.path.isdir(hidden) and any(f.startswith("hidden_test") for f in os.listdir(hidden)),
                  f"{name}: hidden/ holds hidden_test*.py files")
            check(os.path.isfile(patch), f"{name}: has reference.patch")
    check(tiers == TIERS, f"2 easy, 3 medium and 3 hard cases, got {tiers}")
    check(sum(is_ambiguous(c) for c in cases) == 1, "exactly one case is the ambiguous one")
    check(not any(name.startswith("hidden_test") for _, _, names in os.walk(FIXTURE) for name in names),
          "the fixture holds no hidden test")


# ---------------------------------------------------------------- reference check


def fixture_copy():
    work = tempfile.mkdtemp(prefix="worker-ref-")
    repo = os.path.join(work, "repo")
    shutil.copytree(FIXTURE, repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
    return work, repo


def test_fixture_tests_pass():
    work, repo = fixture_copy()
    try:
        result = unittest_run(repo, "test*.py")
        check(result.returncode == 0, f"the fixture's own tests pass: {result.stderr[-800:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_reference_check():
    """Each hidden test fails on the untouched fixture and passes with reference.patch."""
    for case in worker_cases():
        if is_ambiguous(case):
            continue
        name = case["name"]
        work, repo = fixture_copy()
        try:
            hidden = os.path.join(case["dir"], "hidden")
            for entry in os.listdir(hidden):
                shutil.copy(os.path.join(hidden, entry), repo)
            before = unittest_run(repo, "hidden_test*.py")
            check(before.returncode != 0, f"{name}: the hidden tests fail on the untouched fixture")
            check(re.search(r"Ran [1-9]", before.stderr), f"{name}: the hidden tests ran on the fixture: {before.stderr[-400:]}")
            applied = git(repo, "apply", os.path.join(case["dir"], "reference.patch"))
            check(applied.returncode == 0, f"{name}: reference.patch applies: {applied.stderr}")
            after = unittest_run(repo, "hidden_test*.py")
            check(after.returncode == 0, f"{name}: the hidden tests pass with reference.patch: {after.stderr[-800:]}")
            own = unittest_run(repo, "test*.py")
            check(own.returncode == 0, f"{name}: the fixture's own tests pass with reference.patch: {own.stderr[-800:]}")
            changed = git(repo, "diff", "--name-only").stdout.split() + \
                git(repo, "ls-files", "--others", "--exclude-standard").stdout.split()
            changed = [p for p in changed if not os.path.basename(p).startswith("hidden_test")]
            allow = graders_of(case, "diff_scope")[0]["allow"]
            runner = load_runner()
            outside = [p for p in changed if not runner.path_allowed(p, allow)]
            check(not outside, f"{name}: reference.patch stays inside the allowlist, outside: {outside}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- graders


def sandbox_for(case):
    runner = load_runner()
    sandbox, repo = runner.build_sandbox(case)
    ctx = {"sandbox": sandbox, "repo": repo, "gh_log": [], "final": "", "case": case,
           "judge_env": runner.sandbox_env(sandbox)}
    return runner, sandbox, repo, ctx


def commit_patch(repo, branch, patch, extra=None):
    git(repo, "checkout", "-q", "-b", branch)
    if patch:
        result = git(repo, "apply", patch)
        check(result.returncode == 0, f"the patch applies in the sandbox: {result.stderr}")
    for path, text in (extra or {}).items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as handle:
            handle.write(text)
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "work")
    git(repo, "checkout", "-q", "main")


def write_verdict(repo, verdict):
    os.makedirs(os.path.join(repo, ".eval"), exist_ok=True)
    with open(os.path.join(repo, ".eval", "verdict.json"), "w") as handle:
        handle.write(verdict if isinstance(verdict, str) else json.dumps(verdict))


def golden_verdict(case):
    number = issue_of(case)
    if is_ambiguous(case):
        return {"issue": number, "branch": None, "worktree": "/tmp/wt", "prNumber": None,
                "outcome": "needs-feedback", "detail": "the issue does not name a tax rate",
                "question": "Which tax rate applies, and to which commands?",
                "criteria": [], "openThreads": 0, "notesForPM": None}
    return {"issue": number, "branch": f"issue/{number}-x", "worktree": "/tmp/wt", "prNumber": 99,
            "outcome": "ready-to-merge", "detail": "done",
            "localChecks": "python3 -m unittest: 12 passed; pre-patch: 2 new tests fail as expected",
            "criteria": [{"text": text, "met": True, "evidence": "tests/test_x.py::test_y passes"}
                         for text in acceptance_criteria(case)],
            "openThreads": 0, "notesForPM": None}


def run_grader(runner, grader, ctx):
    return runner.GRADERS[grader["type"]](grader, ctx)


def test_graders_on_a_clear_case():
    case = next(c for c in worker_cases() if not is_ambiguous(c))
    number = issue_of(case)
    runner, sandbox, repo, ctx = sandbox_for(case)
    try:
        hidden = graders_of(case, "hidden_tests")[0]
        scope = graders_of(case, "diff_scope")[0]
        verdict = graders_of(case, "verdict")[0]

        passed, detail, cost = run_grader(runner, hidden, ctx)
        check(passed is False and "no branch" in detail, f"hidden_tests fails without a branch, got {passed} {detail!r}")
        check(cost == 0.0, "a deterministic grader costs nothing")
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is False, f"diff_scope fails without a branch, got {detail!r}")
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "verdict.json" in detail, f"verdict fails without a file, got {detail!r}")

        commit_patch(repo, f"issue/{number}-wrong", None, {"NOTES.md": "nothing\n"})
        passed, detail, _ = run_grader(runner, hidden, ctx)
        check(passed is False, f"hidden_tests fails on a branch without the fix, got {detail!r}")
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is False and "NOTES.md" in detail, f"diff_scope names the path outside the allowlist, got {detail!r}")
        git(repo, "branch", "-q", "-D", f"issue/{number}-wrong")

        commit_patch(repo, f"issue/{number}-fix", os.path.join(case["dir"], "reference.patch"))
        passed, detail, _ = run_grader(runner, hidden, ctx)
        check(passed is True, f"hidden_tests passes on the reference fix, got {detail!r}")
        check(not os.path.exists(os.path.join(repo, os.listdir(os.path.join(case["dir"], "hidden"))[0])),
              "the hidden tests never reach the sandbox repo")
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is True, f"diff_scope passes on the reference fix, got {detail!r}")

        good = golden_verdict(case)
        write_verdict(repo, good)
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is True, f"verdict passes on a golden verdict, got {detail!r}")
        write_verdict(repo, "The worker said:\n```json\n" + json.dumps(good) + "\n```\n")
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is True, f"verdict reads JSON inside a code fence, got {detail!r}")
        write_verdict(repo, "not json")
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "JSON" in detail, f"verdict fails on text that is not JSON, got {detail!r}")
        write_verdict(repo, dict(good, criteria=good["criteria"][:-1]))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "criteria" in detail, f"verdict fails with a criterion missing, got {detail!r}")
        write_verdict(repo, dict(good, localChecks="python3 -m unittest: 12 passed"))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "pre-patch" in detail, f"verdict fails without a pre-patch run, got {detail!r}")
        write_verdict(repo, dict(good, criteria=[dict(c, evidence="") for c in good["criteria"]]))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "evidence" in detail, f"verdict fails on a criterion without evidence, got {detail!r}")
        write_verdict(repo, dict(good, criteria=[dict(c, met=False) for c in good["criteria"]]))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False, f"a ready-to-merge verdict fails with an unmet criterion, got {detail!r}")
        write_verdict(repo, dict(good, outcome="needs-feedback", question="?"))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "outcome" in detail, f"verdict fails on the wrong outcome, got {detail!r}")

        git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "on main")
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is False and "main" in detail, f"diff_scope fails when main moved, got {detail!r}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_graders_on_the_ambiguous_case():
    case = next(c for c in worker_cases() if is_ambiguous(c))
    number = issue_of(case)
    runner, sandbox, repo, ctx = sandbox_for(case)
    try:
        scope = graders_of(case, "diff_scope")[0]
        verdict = graders_of(case, "verdict")[0]
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is True, f"no_commits passes on an untouched sandbox, got {detail!r}")
        write_verdict(repo, golden_verdict(case))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is True, f"a needs-feedback verdict with a question passes, got {detail!r}")
        write_verdict(repo, dict(golden_verdict(case), question=""))
        passed, detail, _ = run_grader(runner, verdict, ctx)
        check(passed is False and "question" in detail, f"needs-feedback without a question fails, got {detail!r}")
        commit_patch(repo, f"issue/{number}-guess", None, {"inventory/tax.py": "RATE = 0.2\n"})
        passed, detail, _ = run_grader(runner, scope, ctx)
        check(passed is False and "commit" in detail, f"no_commits fails on a guessed commit, got {detail!r}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_path_allowed():
    runner = load_runner()
    check(runner.path_allowed("tests/test_items.py", ["tests/*"]), "a glob matches a path below it")
    check(not runner.path_allowed("inventory/cli.py", ["inventory/items.py"]), "an exact path matches only itself")


def test_acceptance_criteria_parser():
    runner = load_runner()
    tracker = {"issues": [{"number": 4, "body": "Text\n\n## Acceptance criteria\n- [ ] One\n- [x] Two\n\n## Notes\n- [ ] not a criterion\n"}]}
    check(runner.acceptance_criteria(tracker, 4) == ["One", "Two"],
          f"the parser reads only the acceptance-criteria list, got {runner.acceptance_criteria(tracker, 4)}")


def test_fake_gh_worker_subcommands():
    work = tempfile.mkdtemp(prefix="fakegh-worker-")
    try:
        state = {"repo": {"owner": "acme", "name": "inventory", "defaultBranch": "main"}, "user": "eval-bot",
                 "labels": [{"name": "status:in-review", "color": "", "description": ""}],
                 "issues": [], "prs": [{"number": 9, "title": "WIP", "body": "old", "state": "open",
                                        "isDraft": True, "headRefName": "issue/1-x", "baseRefName": "main",
                                        "labels": [], "comments": [],
                                        "reviews": [{"author": {"login": "bob"}, "state": "COMMENTED"}]}]}
        with open(os.path.join(work, "tracker.json"), "w") as handle:
            json.dump(state, handle)
        env = dict(os.environ, IFE_TRACKER=os.path.join(work, "tracker.json"),
                   IFE_GH_LOG=os.path.join(work, "gh-log.jsonl"))
        fake = os.path.join(EVALS, "fakegh", "gh")

        def gh(*args):
            return subprocess.run([sys.executable, fake, *args], capture_output=True, text=True, env=env, cwd=work)

        result = gh("pr", "edit", "9", "--title", "Fix it", "--body", "new", "--add-label", "status:in-review")
        check(result.returncode == 0, f"pr edit exits 0, got {result.returncode}: {result.stderr}")
        with open(os.path.join(work, "tracker.json")) as handle:
            pr = json.load(handle)["prs"][0]
        check((pr["title"], pr["body"], pr["labels"]) == ("Fix it", "new", ["status:in-review"]),
              f"pr edit changes the title, the body and the labels, got {pr}")
        result = gh("pr", "edit", "issue/1-x", "--body", "by branch")
        check(result.returncode == 0, f"pr edit finds a PR by its head branch, got {result.stderr}")
        branch_repo = os.path.join(work, "clone")
        os.makedirs(branch_repo)
        git(branch_repo, "init", "-q", "-b", "issue/1-x")
        git(branch_repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "work")
        viewed = subprocess.run([sys.executable, fake, "pr", "view", "--json", "number"], capture_output=True,
                                text=True, env=env, cwd=branch_repo)
        check(viewed.returncode == 0 and json.loads(viewed.stdout or "{}").get("number") == 9,
              f"pr view without a number shows the PR of the current branch, got {viewed.stdout!r} {viewed.stderr!r}")
        result = gh("api", "repos/{owner}/{repo}/pulls/9/comments")
        check(result.returncode == 0 and json.loads(result.stdout or "null") == [],
              f"api reads the review comments of a PR, got {result.stdout!r} {result.stderr!r}")
        result = gh("api", "repos/{owner}/{repo}/pulls/9/reviews")
        check(result.returncode == 0 and len(json.loads(result.stdout or "[]")) == 1,
              f"api reads the reviews of a PR, got {result.stdout!r} {result.stderr!r}")
        with open(os.path.join(work, "gh-log.jsonl")) as handle:
            log = [json.loads(line) for line in handle]
        check([e["kind"] for e in log] == ["write", "write", "read", "read", "read"],
              f"the log records pr edit as a write and the api reads as reads, got {log}")
        check(log[0].get("body") == "new", f"the log keeps the body of pr edit, got {log[0]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- end to end

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A stand-in for the dispatcher and its worker, driven by FAKE_WORKER."""
import json, os, subprocess, sys

# The runner gives a run only the variables it allows, so the test settings come from a file.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-env.json")) as handle:
    conf = json.load(handle)
argv = sys.argv[1:]
capture = conf.get("FAKE_CAPTURE")
if capture:
    with open(capture, "a") as handle:
        handle.write(json.dumps({"argv": argv}) + "\n")
plan = json.loads(conf.get("FAKE_WORKER", "{}"))
env = dict(os.environ, GIT_AUTHOR_NAME="w", GIT_AUTHOR_EMAIL="w@example.invalid",
           GIT_COMMITTER_NAME="w", GIT_COMMITTER_EMAIL="w@example.invalid")
if plan.get("branch"):
    subprocess.run(["git", "checkout", "-q", "-b", plan["branch"]], check=True, env=env)
    subprocess.run(["git", "apply", plan["patch"]], check=True, env=env)
    subprocess.run(["git", "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fix [skip ci]"], check=True, env=env)
    subprocess.run(["git", "push", "-q", "origin", plan["branch"]], check=True, env=env)
    subprocess.run(["git", "checkout", "-q", "main"], check=True, env=env)
if plan.get("verdict"):
    os.makedirs(".eval", exist_ok=True)
    with open(".eval/verdict.json", "w") as handle:
        json.dump(plan["verdict"], handle)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ready-to-merge",
                  "total_cost_usd": 0.25, "usage": {"input_tokens": 1, "output_tokens": 2},
                  "modelUsage": {"claude-haiku": {"costUSD": 0.01}, "claude-opus": {"costUSD": 0.24}},
                  "num_turns": 3, "duration_ms": 900, "session_id": "sess-w"}))
'''


def run_suite(extra_env, *args):
    root = tempfile.mkdtemp(prefix="worker-e2e-")
    fake = os.path.join(root, "fake-claude")
    with open(fake, "w") as handle:
        handle.write(FAKE_CLAUDE)
    os.chmod(fake, 0o755)
    plugin = os.path.join(root, "plugin")
    os.makedirs(os.path.join(plugin, ".claude-plugin"))
    with open(os.path.join(plugin, ".claude-plugin", "plugin.json"), "w") as handle:
        json.dump({"name": "issue-flow", "version": "9.9.9"}, handle)
    capture = os.path.join(root, "capture.jsonl")
    with open(os.path.join(root, "fake-env.json"), "w") as handle:
        json.dump(dict(extra_env, FAKE_CAPTURE=capture), handle)
    env = dict(os.environ, IFE_CLAUDE=fake)
    out = os.path.join(root, "out")
    result = subprocess.run([sys.executable, RUNNER, "--plugin-dir", plugin, "--suite", "worker",
                             "--out", out, "--runs", "1", *args],
                            capture_output=True, text=True, env=env, cwd=root, timeout=600)
    results = None
    if os.path.exists(os.path.join(out, "results.json")):
        with open(os.path.join(out, "results.json")) as handle:
            results = json.load(handle)
    captures = []
    if os.path.exists(capture):
        with open(capture) as handle:
            captures = [json.loads(line) for line in handle if line.strip()]
    shutil.rmtree(root, ignore_errors=True)
    return result, results, captures, plugin


def test_do_nothing_worker_fails_every_case():
    result, results, captures, plugin = run_suite({}, "--tag", "worker")
    records = (results or {}).get("records", [])
    check(len(records) == 8, f"--tag worker runs all 8 cases, got {len(records)}: {result.stderr}")
    check(all(r["error"] is None for r in records), f"no case errors, got {[r['error'] for r in records]}")
    check(all(r["ok"] is False for r in records), "a worker that does nothing fails every case")
    check(result.returncode == 1, f"a failing suite exits 1, got {result.returncode}")
    # Each run loads a copy of the plugin, and the prompt names that copy.
    check(captures and all("{plugin_dir}" not in c["argv"][1]
                           and c["argv"][c["argv"].index("--plugin-dir") + 1] in c["argv"][1]
                           and c["argv"][c["argv"].index("--plugin-dir") + 1] != plugin for c in captures),
          "the runner puts the path of the plugin copy into the prompt in place of {plugin_dir}")


def test_golden_worker_passes_every_case():
    for case in worker_cases():
        number = issue_of(case)
        plan = {"verdict": golden_verdict(case)}
        if not is_ambiguous(case):
            plan.update(branch=f"issue/{number}-golden", patch=os.path.join(case["dir"], "reference.patch"))
        result, results, _, _ = run_suite({"FAKE_WORKER": json.dumps(plan)}, "--case", case["name"])
        records = (results or {}).get("records", [])
        check(records and records[0]["ok"] and records[0]["score"] == 1.0,
              f"{case['name']}: the golden run passes, got {records[0]['graders'] if records else result.stderr}")
        if records:
            check(records[0]["model_usage"] and records[0]["cost_usd"] == 0.25,
                  f"{case['name']}: the record keeps the cost and the per-model usage")
            check(isinstance(records[0].get("wall_s"), float) and records[0]["wall_s"] > 0,
                  f"{case['name']}: the record has the wall time, got {records[0].get('wall_s')}")


for test in (test_suite_shape, test_fixture_tests_pass, test_reference_check, test_path_allowed,
             test_acceptance_criteria_parser, test_graders_on_a_clear_case,
             test_graders_on_the_ambiguous_case, test_fake_gh_worker_subcommands,
             test_do_nothing_worker_fails_every_case,
             test_golden_worker_passes_every_case):
    try:
        test()
    except Exception as error:  # a crash in one test must not hide the others
        fail(f"{test.__name__} crashed: {error!r}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("worker cases: all checks pass")
