#!/usr/bin/env python3
"""Run the issue-flow eval cases on headless `claude -p` and grade each run.

    python3 scripts/run-evals.py --plugin-dir issue-flow --suite pm [--case <glob>]
        [--tag <tag>] [--runs N] [--out <dir>] [--max-cost-usd X] [--main-model <id>]
        [--effort low|medium|high|xhigh|max] [--keep] [--evals-dir <dir>]

Each run gets a temporary sandbox. The sandbox holds a git repo whose only
remote is a local bare repo, and a tracker.json that the fake `gh` in
evals/fakegh/ serves. The fake `gh` is first on PATH and GH_TOKEN is a fake
token, so no run can reach the real GitHub. The `claude` binary can be
replaced through IFE_CLAUDE; the tests use that to spend nothing.

The case format, the tracker.json schema, the run record and the cost source
are in evals/README.md. Other scripts read the run record, so keep it stable.
"""

import argparse
import datetime
import fnmatch
import glob
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_GH_DIR = os.path.join(ROOT, "evals", "fakegh")
INSTALLED_PLUGIN = "issue-flow@sniper7kills"
JUDGE_MODEL = "haiku"
DEFAULT_TIMEOUT_S = 900
FAKE_TOKEN = "ife-eval-fake-token-without-access"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# The effort of both 0.20.0 baselines; a run at another effort cannot be compared with them.
DEFAULT_EFFORT = "medium"
# The host variables a run takes: the system basics, and what `claude -p` reads to authenticate
# and reach the API (https://code.claude.com/docs/en/env-vars). A run gets no other host variable.
KEEP_ENV = frozenset({
    "HOME", "USER", "LOGNAME", "SHELL", "PATH", "TMPDIR", "TERM", "TZ", "LANG",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "NODE_EXTRA_CA_CERTS", "CLAUDE_CODE_CERT_STORE", "CLAUDE_CODE_CLIENT_CERT",
    "CLAUDE_CODE_CLIENT_KEY", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_WORKSPACE_ID", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
    "CLAUDE_CODE_SKIP_MANTLE_AUTH", "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",
    "CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH",
})
KEEP_ENV_PREFIXES = ("LC_", "AWS_", "ANTHROPIC_BEDROCK_", "ANTHROPIC_VERTEX_", "ANTHROPIC_FOUNDRY_",
                     "ANTHROPIC_AWS_", "ANTHROPIC_GOOGLE_CLOUD_", "VERTEX_REGION_")
SANDBOX_GIT = {"GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@example.invalid",
               "GIT_COMMITTER_NAME": "eval", "GIT_COMMITTER_EMAIL": "eval@example.invalid"}
# No host git config (so no credential helper, url rewrite or signing key), no ssh, and no
# prompt. A run that pushes can then reach only a remote that needs no login: the sandbox origin.
GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_SSH_COMMAND": "false", "GIT_TERMINAL_PROMPT": "0"}
# The agent can write the repo's .git/config. These settings stop the code it names from
# running when the runner itself calls git there.
GIT_SAFE_CONFIG = ("-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                   "-c", "core.pager=cat")
_usage_spec = importlib.util.spec_from_file_location("usage", os.path.join(ROOT, "issue-flow", "scripts", "usage.py"))
usage_report = importlib.util.module_from_spec(_usage_spec)
_usage_spec.loader.exec_module(usage_report)


class CaseError(Exception):
    """A case file that does not match the case format."""


# ---------------------------------------------------------------- cases


def load_cases(evals_dir, suite, case_glob=None, tag=None):
    """Return the valid cases of one suite, sorted by name; a bad case raises CaseError."""
    cases = []
    for path in sorted(glob.glob(os.path.join(evals_dir, suite, "*", "case.json"))):
        case_dir = os.path.dirname(path)
        with open(path) as handle:
            try:
                case = json.load(handle)
            except json.JSONDecodeError as error:
                raise CaseError(f"{path}: not JSON: {error}")
        validate_case(case, path)
        if case_glob and not fnmatch.fnmatch(case["name"], case_glob):
            continue
        if tag and tag not in case["tags"]:
            continue
        case["dir"] = case_dir
        if "prompt_file" in case:
            with open(os.path.join(case_dir, case["prompt_file"])) as handle:
                case["prompt"] = handle.read()
        case["tracker_path"] = os.path.join(case_dir, case.get("tracker", "tracker.json"))
        case["fixture_path"] = os.path.join(case_dir, case["fixture"]) if case.get("fixture") else None
        if not os.path.isfile(case["tracker_path"]):
            raise CaseError(f"{path}: tracker file {case['tracker_path']} is missing")
        if case["fixture_path"] and not os.path.isdir(case["fixture_path"]):
            raise CaseError(f"{path}: fixture directory {case['fixture_path']} is missing")
        cases.append(case)
    return cases


def validate_case(case, path):
    for key, kind in (("name", str), ("suite", str), ("tags", list), ("graders", list)):
        if not isinstance(case.get(key), kind):
            raise CaseError(f"{path}: '{key}' must be a {kind.__name__}")
    if not isinstance(case.get("prompt"), str) and not isinstance(case.get("prompt_file"), str):
        raise CaseError(f"{path}: give 'prompt' or 'prompt_file'")
    if not case["graders"]:
        raise CaseError(f"{path}: a case needs at least one grader")
    for grader in case["graders"]:
        if grader.get("type") not in GRADERS:
            raise CaseError(f"{path}: unknown grader type {grader.get('type')!r}")
    if all(grader["type"] == "llm" for grader in case["graders"]):
        raise CaseError(f"{path}: a case needs at least one deterministic grader; an llm grader is only a second score")


# ---------------------------------------------------------------- sandbox


def minimal_env(home):
    """The environment of a command the runner starts in the sandbox: no host secret, no host git setup."""
    env = {name: os.environ[name] for name in ("PATH", "TMPDIR", "LANG") if name in os.environ}
    env.update(HOME=home, XDG_CONFIG_HOME=os.devnull, **GIT_ISOLATION, **SANDBOX_GIT)
    return env


def git(cwd, *args):
    """Run git in a sandbox repo that the agent can change, so no code it planted runs."""
    result = subprocess.run(["git", *GIT_SAFE_CONFIG, *args], cwd=cwd, capture_output=True, text=True,
                            env=minimal_env(os.devnull))
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in the sandbox: {result.stderr.strip()}")
    return result.stdout.strip()


def build_sandbox(case):
    """Create the temp dir, the fixture repo with a local bare origin, and the tracker."""
    sandbox = tempfile.mkdtemp(prefix="ife-")
    repo = os.path.join(sandbox, "repo")
    if case["fixture_path"]:
        shutil.copytree(case["fixture_path"], repo)
    else:
        os.makedirs(repo)
        with open(os.path.join(repo, "README.md"), "w") as handle:
            handle.write(f"# {case['name']}\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
    origin = os.path.join(sandbox, "origin.git")
    git(sandbox, "init", "-q", "--bare", origin)
    git(repo, "remote", "add", "origin", origin)
    for branch in case.get("branches", []):
        git(repo, "branch", branch)
    git(repo, "push", "-q", "origin", "--all")
    git(repo, "fetch", "-q", "origin")
    git(repo, "remote", "set-head", "origin", "main")
    shutil.copyfile(case["tracker_path"], os.path.join(sandbox, "tracker.json"))
    os.makedirs(os.path.join(sandbox, "gh-config"))
    return sandbox, repo


def host_env_kept(name):
    """True when a run takes this host variable; evals/README.md lists the rules."""
    return name in KEEP_ENV or name.startswith(KEEP_ENV_PREFIXES)


def sandbox_env(sandbox):
    """The run's environment: the allowed host variables, the fake gh, and git with no host setup.

    The environment starts from an allowlist. A host Claude Code session sets CLAUDE_* variables
    for its commands, and a run that got them would write to the host session's paths.
    The fake gh is first on PATH, and GH_TOKEN and GITHUB_TOKEN are a fake token. Git reads no
    global or system config, so it has no credential helper; ssh for git fails, and git never
    prompts. The run can still start a program by its full path; the sandbox does not stop that.
    """
    env = {name: value for name, value in os.environ.items() if host_env_kept(name)}
    env.update(
        PATH=FAKE_GH_DIR + os.pathsep + env.get("PATH", ""),
        IFE_TRACKER=os.path.join(sandbox, "tracker.json"),
        IFE_GH_LOG=os.path.join(sandbox, "gh-log.jsonl"),
        GH_TOKEN=FAKE_TOKEN, GITHUB_TOKEN=FAKE_TOKEN,
        GH_CONFIG_DIR=os.path.join(sandbox, "gh-config"), GH_PROMPT_DISABLED="1",
        **GIT_ISOLATION, **SANDBOX_GIT,
    )
    return env


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------- claude


def claude_binary():
    return os.environ.get("IFE_CLAUDE", "claude")


def run_claude(args, cwd, env, timeout_s):
    """Run claude in its own process group; return (exit, stdout, stderr, timed_out)."""
    process = subprocess.Popen([claude_binary(), *args], cwd=cwd, env=env, text=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        # A killed run leaves no result; stop the whole tree so no child keeps working.
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr, True


def parse_result(stdout):
    """The `claude -p --output-format json` result object, or None."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, list):  # some versions print the whole message list
        data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), None)
    return data if isinstance(data, dict) else None


def isolation_args():
    """Flags that keep the host's plugins, hooks and MCP servers out of an eval run."""
    return ["--settings", json.dumps({"enabledPlugins": {INSTALLED_PLUGIN: False}}),
            "--setting-sources", "project,local", "--strict-mcp-config"]


# ---------------------------------------------------------------- graders


def log_text(entry):
    skip = 1 if entry.get("cmd") == "api" else len(entry.get("cmd", "").split())
    return " ".join(str(arg) for arg in entry.get("argv", [])[skip:])


def matches(spec, entry):
    if entry.get("cmd") != spec.get("cmd"):
        return False
    # No re.M here: the args text holds the body lines too, and `^` must anchor at the issue number.
    if "args" in spec and not re.search(spec["args"], log_text(entry)):
        return False
    if "body" in spec and not re.search(spec["body"], entry.get("body") or "", re.M):
        return False
    return True


def describe(spec):
    parts = [spec.get("cmd", "?")]
    if "args" in spec:
        parts.append(f"args /{spec['args']}/")
    if "body" in spec:
        parts.append(f"body /{spec['body']}/")
    return " ".join(parts)


def grade_gh_log(grader, ctx):
    """Every `expect` entry matches a successful call (in order when `ordered`); no call matches a `forbid` entry."""
    done = [entry for entry in ctx["gh_log"] if entry.get("kind") != "error"]
    problems = []
    last_seq = 0
    for spec in grader.get("expect", []):
        floor = last_seq if grader.get("ordered") else 0
        hit = next((e for e in done if e["seq"] > floor and matches(spec, e)), None)
        if hit is None:
            problems.append(f"missing {describe(spec)}" + (" (in order)" if grader.get("ordered") else ""))
        else:
            last_seq = hit["seq"]
    # A refused call still shows the decision, so forbid checks every call.
    for spec in grader.get("forbid", []):
        for entry in ctx["gh_log"]:
            if matches(spec, entry):
                problems.append(f"forbidden {spec['cmd']} (seq {entry['seq']}: {' '.join(map(str, entry['argv']))})")
    if problems:
        return False, "; ".join(problems), 0.0
    return True, f"{len(grader.get('expect', []))} expected, {len(grader.get('forbid', []))} forbidden", 0.0


def grade_file(grader, ctx):
    """The file exists in the sandbox repo and its text matches the regex."""
    path = os.path.join(ctx["repo"], grader["path"])
    if not os.path.isfile(path):
        return False, f"{grader['path']} does not exist", 0.0
    with open(path, errors="replace") as handle:
        text = handle.read()
    if re.search(grader.get("regex", ""), text, re.M):
        return True, f"{grader['path']} matches /{grader.get('regex', '')}/", 0.0
    return False, f"{grader['path']} does not match /{grader.get('regex', '')}/", 0.0


def render_writes(entries):
    lines = []
    for entry in entries:
        if entry.get("kind") == "read":
            continue
        lines.append(f"[{entry['seq']}] gh {' '.join(map(str, entry['argv']))}"
                     + (f" -> refused: {entry.get('error')}" if entry.get("kind") == "error" else ""))
        if entry.get("body"):
            lines.append("    body: " + entry["body"].replace("\n", "\n          "))
    return "\n".join(lines) or "(no tracker writes)"


def grade_llm(grader, ctx):
    """A cheap judge reads the target against the rubric and answers PASS or FAIL."""
    target = grader.get("target", "final")
    material = ctx["final"] if target == "final" else render_writes(ctx["gh_log"])
    prompt = (
        "You grade one run of an automated agent. Judge only the material below against the rubric.\n"
        "Answer with PASS or FAIL as the first word, then one sentence that gives the reason.\n\n"
        f"Rubric:\n{grader['rubric']}\n\nMaterial ({target}):\n{material}\n"
    )
    args = ["-p", prompt, "--model", grader.get("model", JUDGE_MODEL), "--tools", "",
            "--output-format", "json", "--no-session-persistence", *isolation_args()]
    code, stdout, stderr, timed_out = run_claude(args, ctx["sandbox"], ctx["judge_env"], 300)
    result = parse_result(stdout)
    if timed_out or result is None:
        return False, f"judge failed (exit {code}): {(stderr or stdout or '').strip()[:200]}", 0.0
    answer = (result.get("result") or "").strip()
    cost = float(result.get("total_cost_usd") or 0.0)
    return answer.upper().startswith("PASS"), answer[:300], cost


# ---------------------------------------------------------------- worker graders

VERDICT_PATH = ".eval/verdict.json"
OUTCOMES = {"ready-to-merge", "checkpoint", "needs-feedback", "blocked"}
HIDDEN_COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "hidden_test*.py"]


def acceptance_criteria(tracker, number):
    """Return the checklist items under the issue's `Acceptance criteria` heading, in order."""
    issue = next((item for item in tracker.get("issues", []) if item["number"] == number), None)
    found, inside = [], False
    for line in ((issue or {}).get("body") or "").splitlines():
        if line.startswith("#"):
            inside = line.lstrip("#").strip().lower() == "acceptance criteria"
            continue
        item = re.match(r"\s*[-*] \[[ xX]\] (.+)", line)
        if inside and item:
            found.append(item.group(1).strip())
    return found


def path_allowed(path, allow):
    return any(fnmatch.fnmatch(path, pattern) for pattern in allow)


def fixture_commit(repo):
    """The commit that build_sandbox made from the fixture: the root commit of main."""
    return git(repo, "rev-list", "--max-parents=0", "main").splitlines()[-1]


def issue_branch(repo, number):
    """Return ((ref, sha), None) for the worker's issue/<number>-* branch, or (None, reason).

    A local branch wins over origin, because a worker can commit after its last push.
    """
    for pattern in (f"refs/heads/issue/{number}-*", f"refs/remotes/origin/issue/{number}-*"):
        refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", pattern).splitlines()
        if len(refs) > 1:
            return None, f"more than one branch matches {pattern}: {', '.join(r.split()[0] for r in refs)}"
        if refs:
            return tuple(refs[0].split()), None
    return None, f"no branch issue/{number}-* in the sandbox repo or its origin"


def grade_hidden_tests(grader, ctx):
    """Run the case's hidden tests in a clean checkout of the worker's branch; pass on exit 0."""
    found, reason = issue_branch(ctx["repo"], grader["issue"])
    if found is None:
        return False, reason, 0.0
    ref, sha = found
    # A separate clone, so the hidden tests never reach the repo the agent worked in.
    checkout = os.path.join(ctx["sandbox"], "grading")
    shutil.rmtree(checkout, ignore_errors=True)
    try:
        git(ctx["sandbox"], "clone", "-q", "--no-checkout", ctx["repo"], checkout)
        git(checkout, "checkout", "-q", "--detach", sha)
        hidden = os.path.join(ctx["case"]["dir"], grader.get("dir", "hidden"))
        for entry in sorted(os.listdir(hidden)):
            shutil.copy(os.path.join(hidden, entry), checkout)
        try:
            # The tests run the agent's code, so they get no host variable but the basics.
            result = subprocess.run(grader.get("command") or HIDDEN_COMMAND, cwd=checkout,
                                    capture_output=True, text=True, timeout=grader.get("timeout_s", 300),
                                    env=minimal_env(ctx["sandbox"]))
        except subprocess.TimeoutExpired:
            return False, f"the hidden tests on {ref} ran past {grader.get('timeout_s', 300)}s", 0.0
    finally:
        shutil.rmtree(checkout, ignore_errors=True)
    output = (result.stderr + result.stdout).strip().splitlines()
    tail = " | ".join(line for line in output if re.match(r"(Ran \d+|OK|FAILED)", line)) or " | ".join(output[-3:])
    verdict = "pass" if result.returncode == 0 else f"fail (exit {result.returncode})"
    return result.returncode == 0, f"hidden tests on {ref} {sha[:8]}: {verdict}: {tail[:300]}", 0.0


def read_verdict(path):
    """Return (verdict, None), or (None, reason). A JSON object inside a code fence also counts."""
    if not os.path.isfile(path):
        return None, f"{os.path.basename(os.path.dirname(path))}/{os.path.basename(path)} does not exist"
    with open(path, errors="replace") as handle:
        text = handle.read().strip()
    for candidate in [text, *re.findall(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.S)]:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data, None
    return None, f"the verdict file holds no JSON object: {text[:120]!r}"


def grade_verdict(grader, ctx):
    """The verdict parses, has the expected outcome, and backs every acceptance criterion with evidence."""
    verdict, reason = read_verdict(os.path.join(ctx["repo"], grader.get("path", VERDICT_PATH)))
    if verdict is None:
        return False, reason, 0.0
    problems = []
    outcome = verdict.get("outcome")
    if outcome not in OUTCOMES:
        problems.append(f"outcome {outcome!r} is not one of {sorted(OUTCOMES)}")
    if grader.get("outcome") and outcome != grader["outcome"]:
        problems.append(f"outcome {outcome!r}, expected {grader['outcome']!r}")
    if str(verdict.get("issue", "")).lstrip("#") != str(grader["issue"]):
        problems.append(f"issue {verdict.get('issue')!r}, expected {grader['issue']}")
    if outcome == "needs-feedback" and not str(verdict.get("question") or "").strip():
        problems.append("needs-feedback without a question")
    if outcome == "ready-to-merge":
        with open(ctx["case"]["tracker_path"]) as handle:
            expected = acceptance_criteria(json.load(handle), grader["issue"])
        criteria = [c for c in verdict.get("criteria") or [] if isinstance(c, dict)]
        if len(criteria) < len(expected):
            problems.append(f"criteria has {len(criteria)} entries for {len(expected)} acceptance criteria")
        for index, criterion in enumerate(criteria, start=1):
            if criterion.get("met") is not True:
                problems.append(f"criterion {index} is not met")
            if not str(criterion.get("evidence") or "").strip():
                problems.append(f"criterion {index} has no evidence")
    if grader.get("prepatch"):
        checks = str(verdict.get("localChecks") or "")
        if not (re.search(r"(?i)pre-?patch", checks) and re.search(r"(?i)fail", checks)):
            problems.append(f"localChecks names no pre-patch failing run: {checks[:160]!r}")
    if problems:
        return False, "; ".join(problems), 0.0
    return True, f"outcome {outcome}, {len(verdict.get('criteria') or [])} criteria", 0.0


def grade_diff_scope(grader, ctx):
    """main does not move, and the worker's branch changes only allowed paths (or nothing is committed)."""
    repo = ctx["repo"]
    base = fixture_commit(repo)
    origin = os.path.join(ctx["sandbox"], "origin.git")
    problems = [f"{where} main moved off the fixture commit"
                for where, path in (("the local", repo), ("origin's", origin))
                if git(path, "rev-parse", "refs/heads/main") != base]
    if grader.get("no_commits"):
        # Every ref, worktree HEADs included, in the repo and in its origin.
        new = set()
        for path in (repo, origin):
            new.update(git(path, "rev-list", "--all", "--not", base).split())
        if new:
            problems.append(f"{len(new)} commit(s) beyond the fixture, expected none")
        detail = "no commits beyond the fixture"
    else:
        found, reason = issue_branch(repo, grader["issue"])
        if found is None:
            return False, reason, 0.0
        ref, sha = found
        changed = git(repo, "diff", "--no-ext-diff", "--name-only", base, sha).splitlines()
        outside = [path for path in changed if not path_allowed(path, grader.get("allow", []))]
        if outside:
            problems.append(f"{ref} changes paths outside the allowlist: {', '.join(outside)}")
        if not changed:
            problems.append(f"{ref} changes nothing")
        detail = f"{ref} changes {len(changed)} path(s), all allowed"
    if problems:
        return False, "; ".join(problems), 0.0
    return True, detail, 0.0


# Other suites add grader types here; each returns (passed, detail, cost_usd).
GRADERS = {"gh_log": grade_gh_log, "file": grade_file, "llm": grade_llm,
           "hidden_tests": grade_hidden_tests, "verdict": grade_verdict, "diff_scope": grade_diff_scope}


# ---------------------------------------------------------------- one run


def variant_of(plugin_dir):
    sha = subprocess.run(["git", "-C", plugin_dir, "rev-parse", "HEAD"], capture_output=True, text=True)
    version = None
    manifest = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
    if os.path.isfile(manifest):
        with open(manifest) as handle:
            version = json.load(handle).get("version")
    return {"plugin_dir": plugin_dir, "git_sha": sha.stdout.strip() if sha.returncode == 0 else None,
            "version": version}


def main_model(result, requested):
    if requested:
        return requested
    usage = (result or {}).get("modelUsage") or {}
    if not usage:
        return None
    return max(usage, key=lambda name: usage[name].get("costUSD") or usage[name].get("outputTokens") or 0)


def run_case(case, run_number, args, variant, budget_left, run_dir):
    """Run one case once and return its record."""
    sandbox, repo = build_sandbox(case)
    # A run in bypassPermissions mode can write its plugin files; it gets a copy of its own.
    plugin = os.path.join(sandbox, "plugin")
    shutil.copytree(args.plugin_dir, plugin, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    env = sandbox_env(sandbox)
    model = args.main_model or case.get("model")
    # A worker brief names the plugin under test as its pluginRoot.
    prompt = case["prompt"].replace("{plugin_dir}", plugin)
    claude_args = ["-p", prompt, "--plugin-dir", plugin, *isolation_args(),
                   "--permission-mode", "bypassPermissions", "--output-format", "json",
                   "--effort", args.effort]
    if model:
        claude_args += ["--model", model]
    if budget_left is not None:
        # A cap that rounds to 0.00 would stop the run before its first request.
        claude_args += ["--max-budget-usd", f"{max(budget_left, 0.01):.2f}"]
    if case.get("disallowed_tools"):
        claude_args += ["--disallowedTools", ",".join(case["disallowed_tools"])]

    started = time.time()
    code, stdout, stderr, timed_out = run_claude(claude_args, repo, env,
                                                 case.get("timeout_s", DEFAULT_TIMEOUT_S))
    wall = time.time() - started
    result = parse_result(stdout)
    error = None
    if timed_out:
        error = f"timeout after {case.get('timeout_s', DEFAULT_TIMEOUT_S)}s"
    elif result is None:
        error = f"claude exit {code}, output is not a result object: {(stdout or stderr or '').strip()[:300]}"
    elif result.get("is_error") or result.get("subtype") != "success":
        error = f"claude result {result.get('subtype')}: {str(result.get('result'))[:300]}"

    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "claude-stdout.json"), "w") as handle:
        handle.write(stdout or "")
    with open(os.path.join(run_dir, "claude-stderr.txt"), "w") as handle:
        handle.write(stderr or "")
    for name in ("gh-log.jsonl", "tracker.json"):
        if os.path.exists(os.path.join(sandbox, name)):
            shutil.copyfile(os.path.join(sandbox, name), os.path.join(run_dir, name))

    result = result or {}
    ctx = {"sandbox": sandbox, "repo": repo, "gh_log": read_jsonl(os.path.join(sandbox, "gh-log.jsonl")),
           "final": result.get("result") or "", "judge_env": sandbox_env(sandbox), "case": case}
    graders, judge_cost = [], 0.0
    for grader in case["graders"]:
        try:
            passed, detail, cost = GRADERS[grader["type"]](grader, ctx)
        except Exception as crash:  # a broken grader fails its own check, not the whole suite
            passed, detail, cost = False, f"grader crashed: {crash!r}", 0.0
        judge_cost += cost
        graders.append({"type": grader["type"], "passed": passed, "detail": detail})

    if args.keep:
        kept = sandbox
    else:
        shutil.rmtree(sandbox, ignore_errors=True)
        kept = None

    usage = result.get("usage") or {}
    deterministic = [g for g in graders if g["type"] != "llm"]
    judged = [g for g in graders if g["type"] == "llm"]
    record = {
        "case": case["name"], "run": run_number, "variant": variant, "effort": args.effort,
        "ok": error is None and all(g["passed"] for g in deterministic),
        "score": sum(g["passed"] for g in graders) / len(graders),
        "graders": graders,
        "deterministic": {"passed": sum(g["passed"] for g in deterministic), "total": len(deterministic)},
        "llm": {"passed": sum(g["passed"] for g in judged), "total": len(judged)},
        "cost_usd": float(result.get("total_cost_usd") or 0.0),
        "judge_cost_usd": round(judge_cost, 6),
        "usage": {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                  "cache_read": usage.get("cache_read_input_tokens", 0),
                  "cache_write": usage.get("cache_creation_input_tokens", 0)},
        "model_usage": result.get("modelUsage") or {},
        "turns": result.get("num_turns"),
        "duration_s": round(result["duration_ms"] / 1000, 3) if result.get("duration_ms") else round(wall, 3),
        # duration_ms stops when the main loop ends; a background subagent can run past it.
        "wall_s": round(wall, 3),
        "session_id": result.get("session_id"),
        "transcript": next(iter(usage_report.session_transcripts(result["session_id"])), None)
                      if result.get("session_id") else None,
        "model": main_model(result, model),
        "error": error,
        "sandbox": kept,
    }
    with open(os.path.join(run_dir, "record.json"), "w") as handle:
        json.dump(record, handle, indent=2)
    return record


# ---------------------------------------------------------------- main


def summary_line(record):
    verdict = "PASS" if record["ok"] else "FAIL"
    det, llm = record["deterministic"], record["llm"]
    line = (f"{record['case']} run {record['run']}: {verdict}  score {record['score']:.2f}  "
            f"deterministic {det['passed']}/{det['total']}  llm {llm['passed']}/{llm['total']}  "
            f"cost ${record['cost_usd'] + record['judge_cost_usd']:.2f}")
    if record["error"]:
        line += f"  error: {record['error']}"
    failed = [f"    {g['type']}: {g['detail']}" for g in record["graders"] if not g["passed"]]
    return "\n".join([line, *failed])


def write_results(out, results):
    temporary = os.path.join(out, "results.json.tmp")
    with open(temporary, "w") as handle:
        json.dump(results, handle, indent=2)
    os.replace(temporary, os.path.join(out, "results.json"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the issue-flow eval cases on claude -p.")
    parser.add_argument("--plugin-dir", required=True, help="the issue-flow plugin directory to test")
    parser.add_argument("--suite", required=True, help="pm or worker")
    parser.add_argument("--case", help="only the cases whose name matches this glob")
    parser.add_argument("--tag", help="only the cases that carry this tag")
    parser.add_argument("--runs", type=int, help="runs per case; overrides the case's own count")
    parser.add_argument("--out", help="the results directory (default evals/results/<UTC time>)")
    parser.add_argument("--max-cost-usd", type=float, help="stop before a run once the total cost reaches this")
    parser.add_argument("--main-model", help="the model of the run; overrides the case's own model")
    parser.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT,
                        help=f"the effort level of each run (default {DEFAULT_EFFORT}, the effort of the 0.20.0 baselines)")
    parser.add_argument("--keep", action="store_true", help="keep each sandbox for inspection")
    parser.add_argument("--evals-dir", default=os.path.join(ROOT, "evals"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    args.plugin_dir = os.path.abspath(args.plugin_dir)
    if not os.path.isfile(os.path.join(args.plugin_dir, ".claude-plugin", "plugin.json")):
        print(f"error: {args.plugin_dir} has no .claude-plugin/plugin.json", file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.evals_dir, args.suite, args.case, args.tag)
    except CaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not cases:
        print("error: no case matches", file=sys.stderr)
        return 2

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.abspath(args.out or os.path.join(args.evals_dir, "results", stamp))
    os.makedirs(out, exist_ok=True)
    variant = variant_of(args.plugin_dir)
    results = {"suite": args.suite, "variant": variant, "effort": args.effort,
               "started": stamp, "finished": None,
               "max_cost_usd": args.max_cost_usd, "total_cost_usd": 0.0, "partial": False,
               "records": []}
    plan = [(case, k) for case in cases for k in range(1, (args.runs or case.get("runs", 1)) + 1)]
    spent = 0.0
    for index, (case, run_number) in enumerate(plan):
        if args.max_cost_usd is not None and spent >= args.max_cost_usd:
            results["partial"] = True
            print(f"cost ceiling reached: ${spent:.2f} of ${args.max_cost_usd:.2f}; "
                  f"{len(plan) - index} run(s) not started")
            break
        budget_left = None if args.max_cost_usd is None else args.max_cost_usd - spent
        run_dir = os.path.join(out, case["name"], f"run-{run_number}")
        record = run_case(case, run_number, args, variant, budget_left, run_dir)
        spent = round(spent + record["cost_usd"] + record["judge_cost_usd"], 6)
        results["records"].append(record)
        results["total_cost_usd"] = spent
        write_results(out, results)
        print(summary_line(record), flush=True)

    results["finished"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_results(out, results)
    passed = sum(r["ok"] for r in results["records"])
    print(f"\n{passed}/{len(results['records'])} runs pass; total cost ${spent:.2f}; results in {out}")
    return 0 if passed == len(results["records"]) and not results["partial"] else 1


if __name__ == "__main__":
    sys.exit(main())
