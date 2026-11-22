#!/usr/bin/env python3
"""Tests for the criteria-checker agent, its eval suite in evals/checker/, and its graders.

    python3 scripts/test-checker-cases.py

The tests spend nothing and make no network call. A fake `claude` (set through
IFE_CLAUDE) stands in for the dispatcher and the checker. Every sandbox is a
temporary directory.
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
AGENT = os.path.join(ROOT, "issue-flow", "agents", "criteria-checker.md")
SKILL = os.path.join(ROOT, "issue-flow", "skills", "issue-flow", "SKILL.md")
WORKER_REF = os.path.join(ROOT, "issue-flow", "skills", "issue-flow", "references", "issue-worker.md")
PARTIAL_CASE = "checker-partial-hard-error-lines"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

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


def checker_cases():
    return load_runner().load_cases(EVALS, "checker")


def worker_cases_with_hidden_tests():
    return [case for case in load_runner().load_cases(EVALS, "worker")
            if any(g["type"] == "hidden_tests" for g in case["graders"])]


def source_case(case):
    """The worker case whose reference patch and tracker a checker case reuses."""
    return os.path.basename(os.path.dirname(os.path.realpath(case["tracker_path"])))


def criteria_of(case):
    with open(case["tracker_path"]) as handle:
        tracker = json.load(handle)
    return load_runner().acceptance_criteria(tracker, issue_of(case))


def issue_of(case):
    return next(g["issue"] for g in case["graders"] if g["type"] == "checker_verdict")


def checker_grader(case):
    return next(g for g in case["graders"] if g["type"] == "checker_verdict")


def diff_sections(patch_text):
    """Split a unified diff into {path: section text}, one section per file."""
    sections = {}
    for chunk in re.split(r"(?m)^(?=diff --git )", patch_text):
        found = re.match(r"diff --git a/(\S+) ", chunk)
        if found:
            sections[found.group(1)] = chunk
    return sections


# ---------------------------------------------------------------- the agent and the docs


def test_agent_definition():
    with open(AGENT) as handle:
        text = handle.read()
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    check(front, "criteria-checker.md has YAML frontmatter")
    block = front.group(1) if front else ""
    check(re.search(r"(?m)^name: criteria-checker$", block), "the agent is named criteria-checker")
    check(re.search(r"(?m)^model: sonnet$", block), "the checker runs on sonnet")
    tools = re.search(r"(?m)^tools: (.+)$", block)
    names = {name.strip() for name in tools.group(1).split(",")} if tools else set()
    check(tools and names, "the checker lists its tools")
    for forbidden in ("Edit", "Write", "NotebookEdit", "Agent", "Task", "Workflow"):
        check(forbidden not in names, f"the checker is read-only: no {forbidden} tool, got {sorted(names)}")
    check("description:" in block, "the frontmatter has a description")
    runner = load_runner()
    contract = re.search(r"## Return contract.*?```json\n(.*?)\n```", text, re.S)
    check(contract, "the agent definition has a JSON return contract")
    if contract:
        example = json.loads(contract.group(1))
        check(set(example) == set(runner.CHECKER_KEYS),
              f"the contract keys match the grader, got {sorted(example)} against {sorted(runner.CHECKER_KEYS)}")
        entry = (example.get("criteria") or [{}])[0]
        check(set(entry) == {"text", "status", "evidence"}, f"a criterion entry has text, status and evidence, got {entry}")
        check(set(re.split(r"\s*\|\s*", entry.get("status", ""))) == set(runner.CHECKER_STATUSES),
              f"the contract statuses match the grader, got {entry.get('status')!r}")
    # The checker judges the branch; the worker's own claims must not reach it.
    check(re.search(r"(?i)never .{0,80}worker's verdict", text), "the agent never reads the worker's verdict")


def test_gate_wiring():
    with open(SKILL) as handle:
        skill = handle.read()
    c1 = skill[skill.index("### C1 — Sub-merge gate"):skill.index("### C2 — Batch gate")]
    check("issue-flow:criteria-checker" in c1, "the C1 gate spawns issue-flow:criteria-checker")
    standalone = c1[c1.index("**Standalone/hotfix (`ci: run`) has no sub-merge**"):]
    standalone = standalone[:standalone.index("\n\n")]
    check("criteria check" in standalone.lower(), "the standalone gate runs the criteria check too")
    check(re.search(r"(?i)unmet.{0,40}unclear", c1), "the C1 gate names unmet and unclear criteria")
    with open(WORKER_REF) as handle:
        reference = handle.read()
    row = next((line for line in reference.splitlines() if line.startswith("| `ready-to-merge` |")), "")
    check("criteria-checker" in row, "the ready-to-merge row of issue-worker.md runs the criteria checker")
    with open(os.path.join(ROOT, "issue-flow", "README.md")) as handle:
        check("criteria-checker" in handle.read(), "the plugin README names the criteria-checker agent")


# ---------------------------------------------------------------- suite shape


def test_suite_shape():
    cases = checker_cases()
    by_name = {case["name"]: case for case in cases}
    check(PARTIAL_CASE in by_name, f"the checker suite has {PARTIAL_CASE}")
    full = [case for case in cases if "full" in case["tags"]]
    expected = sorted(case["name"] for case in worker_cases_with_hidden_tests())
    check(sorted(source_case(case) for case in full) == expected,
          f"one full-patch case for each worker case with hidden tests, got {sorted(source_case(c) for c in full)}")
    check(len(cases) == len(full) + 1, f"the suite is the full cases and the partial case, got {sorted(by_name)}")
    for case in cases:
        name = case["name"]
        source = os.path.join(WORKER, source_case(case))
        with open(os.path.join(source, "case.json")) as handle:
            worker = json.load(handle)
        check(case["suite"] == "checker" and "checker" in case["tags"], f"{name}: suite and tag are checker")
        check(case.get("model") == "haiku", f"{name}: the dispatcher runs on haiku")
        check(os.path.realpath(case["fixture_path"]) == os.path.realpath(os.path.join(source, worker["fixture"])),
              f"{name}: starts from the fixture of {source_case(case)}")
        branch = re.search(r"branch:\s+(\S+)", worker["prompt"]).group(1)
        check(case.get("patch_branch") == branch, f"{name}: the patch branch is {branch}")
        prompt = case["prompt"]
        for needle in ("issue-flow:criteria-checker", '"worktree"', "{head_sha}", "{plugin_dir}",
                       ".eval/checker-verdict.json", f"origin/{branch}", "origin/main",
                       "python3 -m unittest", "Do not run `sleep`"):
            check(needle in prompt, f"{name}: the prompt holds {needle!r}")
        # The brief quotes the criteria and nothing that the worker claimed.
        for number, text in enumerate(criteria_of(case), start=1):
            check(f"  {number}. {text}\n" in prompt, f"{name}: the prompt quotes criterion {number} verbatim")
        for leak in ("localChecks", "met: true", "ready-to-merge", "verdict.json\n"):
            check(leak not in prompt, f"{name}: the prompt carries no worker verdict ({leak!r})")
        types = [g["type"] for g in case["graders"]]
        check(types.count("checker_verdict") == 1 and "refs_unchanged" in types,
              f"{name}: has a checker_verdict and a refs_unchanged grader, got {types}")
        check(any(g["type"] == "gh_log" and {"kind": "write"} in g.get("forbid", []) for g in case["graders"]),
              f"{name}: a gh_log grader forbids every tracker write")
        if "full" in case["tags"]:
            check(case.get("runs") == 3, f"{name}: a full-patch case runs 3 times")
            check(os.path.realpath(os.path.join(case["dir"], case["patch"]))
                  == os.path.realpath(os.path.join(source, "reference.patch")),
                  f"{name}: applies the worker case's reference.patch")
            check(checker_grader(case).get("expect") == "all-met", f"{name}: expects every criterion met")
    partial = by_name.get(PARTIAL_CASE)
    if partial:
        check(partial.get("runs") == 5, "the partial case runs 5 times")
        check("partial" in partial["tags"], "the partial case is tagged partial")
        grader = checker_grader(partial)
        check(grader.get("expect") == "unmet" and grader.get("criteria") == [2],
              f"the partial case expects criterion 2 unmet, got {grader}")


def test_partial_patch_is_the_reference_without_cli():
    case = next((c for c in checker_cases() if c["name"] == PARTIAL_CASE), None)
    if case is None:
        return fail(f"{PARTIAL_CASE} is missing")
    with open(os.path.join(WORKER, "worker-hard-error-lines", "reference.patch")) as handle:
        reference = diff_sections(handle.read())
    with open(os.path.join(case["dir"], case["patch"])) as handle:
        partial = diff_sections(handle.read())
    check("inventory/cli.py" in reference, "the reference patch changes cli.py")
    expected = {path: text for path, text in reference.items() if path != "inventory/cli.py"}
    check(partial == expected, f"partial.patch is reference.patch without its cli.py section, got {sorted(partial)}")
    # The worker's own tests pass on the partial fix, and the hidden tests do not: the gap the checker must find.
    work = tempfile.mkdtemp(prefix="checker-partial-")
    try:
        repo = os.path.join(work, "repo")
        shutil.copytree(case["fixture_path"], repo)
        applied = subprocess.run(["git", "apply", os.path.join(case["dir"], case["patch"])], cwd=repo,
                                 capture_output=True, text=True)
        check(applied.returncode == 0, f"partial.patch applies to the fixture: {applied.stderr}")
        own = subprocess.run([sys.executable, "-m", "unittest"], cwd=repo, capture_output=True, text=True,
                             timeout=120)
        check(own.returncode == 0, f"the fixture tests pass with the partial fix: {own.stderr[-300:]}")
        hidden = os.path.join(WORKER, "worker-hard-error-lines", "hidden")
        for entry in os.listdir(hidden):
            shutil.copy(os.path.join(hidden, entry), repo)
        hid = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "hidden_test*.py"],
                             cwd=repo, capture_output=True, text=True, timeout=120)
        check(hid.returncode != 0, "the hidden tests fail on the partial fix")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- sandbox and graders


def golden_checker_verdict(case, head_sha, statuses=None):
    criteria = criteria_of(case)
    statuses = statuses or ["met"] * len(criteria)
    return {"issue": issue_of(case), "head": f"origin/{case['patch_branch']}", "headSha": head_sha,
            "criteria": [{"text": text, "status": status, "evidence": "tests/test_x.py::test_y passes"}
                         for text, status in zip(criteria, statuses)],
            "testsRun": "python3 -m unittest: 13 passed", "notes": None}


def write_checker_verdict(repo, verdict):
    os.makedirs(os.path.join(repo, ".eval"), exist_ok=True)
    with open(os.path.join(repo, ".eval", "checker-verdict.json"), "w") as handle:
        handle.write(verdict if isinstance(verdict, str) else json.dumps(verdict))


def test_sandbox_applies_the_patch():
    runner = load_runner()
    case = next(c for c in checker_cases() if c["name"] == PARTIAL_CASE)
    sandbox, repo = runner.build_sandbox(case)
    try:
        state = runner.start_state(case, sandbox, repo)
        origin = os.path.join(sandbox, "origin.git")
        branch = case["patch_branch"]
        sha = git(origin, "rev-parse", f"refs/heads/{branch}").stdout.strip()
        check(sha and state["head_sha"] == sha, f"the patch branch is pushed to origin at the head sha, got {state}")
        changed = git(repo, "diff", "--name-only", "main", sha).stdout.split()
        check(sorted(changed) == ["inventory/items.py", "tests/test_items.py"],
              f"the patch commit changes the patched paths, got {changed}")
        check(git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main",
              "the sandbox repo stays on main, so the checker starts where a PM starts")
        check(f"refs/heads/{branch}" in state["start_refs"] and "refs/heads/main" in state["start_refs"],
              f"start_state records the origin refs, got {state['start_refs']}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_a_patch_needs_a_branch():
    runner = load_runner()
    try:
        runner.validate_case({"name": "x", "suite": "checker", "tags": [], "prompt": "p", "patch": "a.patch",
                              "graders": [{"type": "refs_unchanged"}]}, "case.json")
        fail("a case with a patch and no patch_branch is refused")
    except runner.CaseError as error:
        check("patch_branch" in str(error), f"the refusal names patch_branch, got {error}")


def test_checker_graders():
    runner = load_runner()
    case = next(c for c in checker_cases() if c["name"] == PARTIAL_CASE)
    sandbox, repo = runner.build_sandbox(case)
    try:
        state = runner.start_state(case, sandbox, repo)
        ctx = {"sandbox": sandbox, "repo": repo, "gh_log": [], "final": "", "case": case, **state}
        sha = state["head_sha"]
        partial = checker_grader(case)
        all_met = dict(partial, expect="all-met")
        all_met.pop("criteria")
        grade = runner.GRADERS["checker_verdict"]

        passed, detail, cost = grade(partial, ctx)
        check(passed is False and "checker-verdict.json" in detail, f"fails without a verdict file, got {detail!r}")
        check(cost == 0.0, "a deterministic grader costs nothing")

        flagged = golden_checker_verdict(case, sha, ["met", "unmet", "unclear", "met"])
        write_checker_verdict(repo, flagged)
        passed, detail, _ = grade(partial, ctx)
        check(passed is True and "unmet" in detail, f"passes when criterion 2 is unmet, got {detail!r}")
        passed, detail, _ = grade(all_met, ctx)
        check(passed is False, f"all-met fails when a criterion is unmet, got {detail!r}")

        write_checker_verdict(repo, golden_checker_verdict(case, sha, ["met", "unclear", "met", "met"]))
        passed, detail, _ = grade(partial, ctx)
        check(passed is False and "criterion 2" in detail, f"unclear is not unmet, got {detail!r}")

        good = golden_checker_verdict(case, sha)
        write_checker_verdict(repo, "The checker said:\n```json\n" + json.dumps(good) + "\n```\n")
        passed, detail, _ = grade(all_met, ctx)
        check(passed is True, f"all-met passes on a golden verdict in a code fence, got {detail!r}")
        passed, detail, _ = grade(partial, ctx)
        check(passed is False and "criterion 2" in detail, f"the partial grader fails when every criterion is met, got {detail!r}")

        write_checker_verdict(repo, dict(good, headSha=sha[:10]))
        passed, detail, _ = grade(all_met, ctx)
        check(passed is True, f"a short head sha counts, got {detail!r}")

        bad = [
            (dict(good, criteria=good["criteria"][:-1]), "criteria"),
            (dict(good, criteria=[dict(c, status="done") for c in good["criteria"]]), "status"),
            (dict(good, criteria=[dict(c, evidence=" ") for c in good["criteria"]]), "evidence"),
            (dict(good, testsRun=""), "testsRun"),
            (dict(good, issue=99), "issue"),
            (dict(good, headSha="0000000"), "headSha"),
            (dict(good, headSha=None), "headSha"),
            ("not json", "JSON"),
        ]
        for verdict, word in bad:
            write_checker_verdict(repo, verdict)
            passed, detail, _ = grade(all_met, ctx)
            check(passed is False and word in detail, f"fails and names {word!r}, got {passed} {detail!r}")

        refs = runner.GRADERS["refs_unchanged"]
        passed, detail, _ = refs({"type": "refs_unchanged"}, ctx)
        check(passed is True, f"refs_unchanged passes on an untouched sandbox, got {detail!r}")
        # A harness worktree adds a local branch of its own; that is not a change to the work.
        git(repo, "branch", "worktree-agent-x", "main")
        passed, detail, _ = refs({"type": "refs_unchanged"}, ctx)
        check(passed is True, f"a new local branch is allowed, got {detail!r}")
        git(repo, "checkout", "-q", case["patch_branch"])
        git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "checker wrote")
        passed, detail, _ = refs({"type": "refs_unchanged"}, ctx)
        check(passed is False and case["patch_branch"] in detail, f"fails when the checker commits, got {detail!r}")
        git(repo, "push", "-q", "origin", case["patch_branch"])
        passed, detail, _ = refs({"type": "refs_unchanged"}, ctx)
        check(passed is False and "origin" in detail, f"fails when the checker pushes, got {detail!r}")

        gh = runner.GRADERS["gh_log"]
        forbid = {"type": "gh_log", "forbid": [{"kind": "write"}]}
        log = [{"seq": 1, "cmd": "issue view", "argv": ["issue", "view", "7"], "kind": "read"}]
        passed, _, _ = gh(forbid, dict(ctx, gh_log=log))
        check(passed is True, "a tracker read is allowed")
        log.append({"seq": 2, "cmd": "pr comment", "argv": ["pr", "comment", "9"], "kind": "write"})
        passed, detail, _ = gh(forbid, dict(ctx, gh_log=log))
        check(passed is False and "pr comment" in detail, f"a forbid entry with only a kind matches any write, got {detail!r}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------- end to end

FAKE_CLAUDE = r'''#!/usr/bin/env python3
"""A stand-in for the dispatcher and its checker, driven by FAKE_CHECKER."""
import json, os, re, sys

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-env.json")) as handle:
    conf = json.load(handle)
prompt = sys.argv[sys.argv.index("-p") + 1]
plans = json.loads(conf.get("FAKE_CHECKER", "{}"))
issue = int(re.search(r"issue:\s+#(\d+)", prompt).group(1))
plan = plans.get(str(issue))
if plan is not None:
    sha = re.search(r"headSha:\s+(\S+)", prompt).group(1)
    criteria = re.findall(r"(?m)^  \d+\. (.+)$", prompt)
    statuses = plan.get("statuses") or ["met"] * len(criteria)
    os.makedirs(".eval", exist_ok=True)
    with open(".eval/checker-verdict.json", "w") as handle:
        json.dump({"issue": issue, "head": "origin/x", "headSha": sha,
                   "criteria": [{"text": t, "status": s, "evidence": "ran it"} for t, s in zip(criteria, statuses)],
                   "testsRun": "python3 -m unittest: OK", "notes": None}, handle)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done",
                  "total_cost_usd": 0.05, "usage": {"input_tokens": 1, "output_tokens": 2},
                  "modelUsage": {"claude-haiku": {"costUSD": 0.01}, "claude-sonnet": {"costUSD": 0.04}},
                  "num_turns": 3, "duration_ms": 900, "session_id": "sess-c"}))
'''


def run_suite(plans, *args):
    root = tempfile.mkdtemp(prefix="checker-e2e-")
    fake = os.path.join(root, "fake-claude")
    with open(fake, "w") as handle:
        handle.write(FAKE_CLAUDE)
    os.chmod(fake, 0o755)
    plugin = os.path.join(root, "plugin")
    os.makedirs(os.path.join(plugin, ".claude-plugin"))
    with open(os.path.join(plugin, ".claude-plugin", "plugin.json"), "w") as handle:
        json.dump({"name": "issue-flow", "version": "9.9.9"}, handle)
    with open(os.path.join(root, "fake-env.json"), "w") as handle:
        json.dump({"FAKE_CHECKER": json.dumps(plans)}, handle)
    out = os.path.join(root, "out")
    result = subprocess.run([sys.executable, RUNNER, "--plugin-dir", plugin, "--suite", "checker",
                             "--out", out, "--runs", "1", *args],
                            capture_output=True, text=True, env=dict(os.environ, IFE_CLAUDE=fake), cwd=root,
                            timeout=600)
    results = None
    if os.path.exists(os.path.join(out, "results.json")):
        with open(os.path.join(out, "results.json")) as handle:
            results = json.load(handle)
    shutil.rmtree(root, ignore_errors=True)
    return result, {r["case"]: r for r in (results or {}).get("records", [])}


def test_do_nothing_checker_fails_every_case():
    cases = checker_cases()
    result, records = run_suite({})
    check(len(records) == len(cases), f"the suite runs every case, got {len(records)}: {result.stderr[-300:]}")
    check(records and not any(r["ok"] for r in records.values()), "a checker that writes no verdict fails every case")
    check(result.returncode == 1, f"a failing suite exits 1, got {result.returncode}")


def test_golden_checker_passes_every_case():
    cases = checker_cases()
    full = [c for c in cases if "full" in c["tags"]]
    # A full case and the partial case share issue #7, so each tag gets a suite run of its own.
    result, records = run_suite({str(issue_of(c)): {} for c in full}, "--tag", "full")
    for case in full:
        record = records.get(case["name"])
        check(record and record["ok"] and record["score"] == 1.0,
              f"{case['name']}: the golden run passes, got {record['graders'] if record else result.stderr[-300:]}")
        check(record and record["cost_usd"] == 0.05, f"{case['name']}: the record keeps the cost")
    partial = next(c for c in cases if c["name"] == PARTIAL_CASE)
    statuses = ["unmet" if n in checker_grader(partial)["criteria"] else "met"
                for n in range(1, len(criteria_of(partial)) + 1)]
    result, records = run_suite({"7": {"statuses": statuses}}, "--tag", "partial")
    record = records.get(PARTIAL_CASE)
    check(record and record["ok"], f"the partial case passes when the checker flags criterion 2, got "
                                   f"{record['graders'] if record else result.stderr[-300:]}")
    result, records = run_suite({"7": {}}, "--tag", "partial")
    record = records.get(PARTIAL_CASE)
    check(record and not record["ok"], "the partial case fails when the checker calls every criterion met")


for test in (test_agent_definition, test_gate_wiring, test_suite_shape, test_partial_patch_is_the_reference_without_cli,
             test_sandbox_applies_the_patch, test_a_patch_needs_a_branch, test_checker_graders,
             test_do_nothing_checker_fails_every_case, test_golden_checker_passes_every_case):
    try:
        test()
    except Exception as error:  # a crash in one test must not hide the others
        fail(f"{test.__name__} crashed: {error!r}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("checker cases: all checks pass")
