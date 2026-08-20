#!/usr/bin/env python3
"""Tests for issue-flow/scripts/verify_claim.py.

    python3 scripts/test-verify-claim.py

Builds a tiny real git repo with two commits (a symbol added in the second)
and checks: found, not-found, stale-ref detection, and bad-ref handling.
Never touches a real network or the working repo.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "verify_claim", os.path.join(ROOT, "issue-flow", "scripts", "verify_claim.py")
)
verify_claim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_claim)

failures = []


def fail(message):
    failures.append(message)


def git(repo, *args, env=None):
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout.strip()


def build_repo(root):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    with open(os.path.join(repo, "a.py"), "w") as handle:
        handle.write("print('hello')\n")
    git(repo, "add", "a.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first", env=env)
    first_sha = git(repo, "rev-parse", "HEAD")

    with open(os.path.join(repo, "b.py"), "w") as handle:
        handle.write("def new_helper():\n    return 1\n")
    git(repo, "add", "b.py")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "second", env=env)
    second_sha = git(repo, "rev-parse", "HEAD")
    return repo, first_sha, second_sha


work = tempfile.mkdtemp(prefix="verify-claim-test-")
try:
    repo, first_sha, second_sha = build_repo(work)

    code, payload = verify_claim.check(repo, "HEAD", "def new_helper", None)
    if code != 0 or not payload["found"]:
        fail(f"expected found=True at HEAD, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, first_sha, "def new_helper", None)
    if code != 0 or payload["found"]:
        fail(f"expected found=False at first commit, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, first_sha, "def new_helper", second_sha)
    if code != 3 or not payload.get("stale"):
        fail(f"expected stale detection when ref != expect-sha, got code={code} payload={payload}")

    code, payload = verify_claim.check(repo, "does-not-exist-ref", "def new_helper", None)
    if code != 2:
        fail(f"expected code=2 for unresolvable ref, got code={code} payload={payload}")
finally:
    subprocess.run(["rm", "-rf", work])

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("verify_claim: all cases pass")
