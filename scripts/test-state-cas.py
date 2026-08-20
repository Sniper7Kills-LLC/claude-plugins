#!/usr/bin/env python3
"""Tests for issue-flow/scripts/state_cas.py.

    python3 scripts/test-state-cas.py

Uses a local bare repo as "origin" (no real network) and a local clone as the
worker, to exercise: create-when-absent, CAS success, stale-expect rejection,
and a genuine lost-race (two clones racing the same key).
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "state_cas", os.path.join(ROOT, "issue-flow", "scripts", "state_cas.py")
)
state_cas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_cas)

failures = []


def fail(message):
    failures.append(message)


work = tempfile.mkdtemp(prefix="state-cas-test-")
try:
    bare = os.path.join(work, "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)

    clone_a = os.path.join(work, "a")
    clone_b = os.path.join(work, "b")
    subprocess.run(["git", "clone", "-q", bare, clone_a], check=True)
    subprocess.run(["git", "clone", "-q", bare, clone_b], check=True)

    # 1. get on an absent key returns null, no error.
    parent, current = state_cas.fetch_current(clone_a, "origin", "issue-17")
    if current is not None or parent is not None:
        fail(f"expected absent key to read as None, got parent={parent} current={current}")

    # 2. first set (expect absent) succeeds.
    class Args:
        pass

    args = Args()
    args.repo, args.remote, args.key = clone_a, "origin", "issue-17"
    args.expect, args.value = "absent", json.dumps({"owner": "worker-a"})
    code = state_cas.cmd_set(args)
    if code != 0:
        fail(f"expected first set to succeed, got code={code}")

    # 3. a stale expect on the same clone is rejected.
    args.expect, args.value = "absent", json.dumps({"owner": "worker-x"})
    code = state_cas.cmd_set(args)
    if code != 2:
        fail(f"expected stale-expect rejection, got code={code}")

    # 4. a genuine race: clone_b writes first, clone_a's push (based on a now-stale parent) loses.
    args_b = Args()
    args_b.repo, args_b.remote, args_b.key = clone_b, "origin", "race-key"
    args_b.expect, args_b.value = "absent", json.dumps({"owner": "worker-b"})
    if state_cas.cmd_set(args_b) != 0:
        fail("expected worker-b's first write on race-key to succeed")

    # clone_a still thinks race-key is absent (never fetched b's write) and tries the same create.
    args_a = Args()
    args_a.repo, args_a.remote, args_a.key = clone_a, "origin", "race-key"
    args_a.expect, args_a.value = "absent", json.dumps({"owner": "worker-a"})
    code = state_cas.cmd_set(args_a)
    if code != 2:
        fail(f"expected worker-a to see worker-b's value on refetch and reject as stale, got code={code}")

    # 5. correct CAS sequence (read-then-write with the real current value) succeeds.
    parent, current = state_cas.fetch_current(clone_a, "origin", "race-key")
    args_a.expect, args_a.value = json.dumps(current), json.dumps({"owner": "worker-a", "took_over": True})
    code = state_cas.cmd_set(args_a)
    if code != 0:
        fail(f"expected correctly-sequenced CAS to succeed, got code={code}")
finally:
    subprocess.run(["rm", "-rf", work])

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("state_cas: all cases pass")
