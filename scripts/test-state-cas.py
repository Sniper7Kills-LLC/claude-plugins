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

    # 6. shared-ref-store race: two linked worktrees off one clone (what
    # isolation: "worktree" actually gives every worker) claiming DIFFERENT keys at
    # the same moment. Before the push-by-SHA fix, a local `update-ref` + push-by-name
    # let worker B's update-ref for its own key clobber worker A's ref in between A's
    # update-ref and A's push, so A silently pushed B's commit. Interleave the two
    # calls' internal steps by hand to force that window regardless of scheduling.
    worktree_a = os.path.join(work, "wt-a")
    worktree_b = os.path.join(work, "wt-b")
    subprocess.run(["git", "worktree", "add", "-q", worktree_a], cwd=clone_a, check=True)
    subprocess.run(["git", "worktree", "add", "-q", worktree_b], cwd=clone_a, check=True)

    new_commit_a = state_cas.write_commit(worktree_a, "shared-key-a", {"owner": "worker-a"}, None)
    new_commit_b = state_cas.write_commit(worktree_b, "shared-key-b", {"owner": "worker-b"}, None)

    push_a = state_cas.run(
        ["git", "push", "origin", f"{new_commit_a}:{state_cas.ref_name('shared-key-a')}"], worktree_a
    )
    push_b = state_cas.run(
        ["git", "push", "origin", f"{new_commit_b}:{state_cas.ref_name('shared-key-b')}"], worktree_b
    )
    if push_a.returncode != 0 or push_b.returncode != 0:
        fail(f"expected both push-by-SHA writes to distinct keys to succeed, got a={push_a.returncode} b={push_b.returncode}")
    _, value_a = state_cas.fetch_current(clone_a, "origin", "shared-key-a")
    _, value_b = state_cas.fetch_current(clone_a, "origin", "shared-key-b")
    if value_a != {"owner": "worker-a"} or value_b != {"owner": "worker-b"}:
        fail(f"push-by-SHA cross-key clobbering: expected a=worker-a b=worker-b, got a={value_a} b={value_b}")

    # 7. the same race, but B genuinely contends A's key: B's push must be the one
    # git's non-fast-forward check rejects, not a coin flip decided by ref clobbering.
    new_commit_a2 = state_cas.write_commit(worktree_a, "contended-key", {"owner": "worker-a"}, None)
    new_commit_b2 = state_cas.write_commit(worktree_b, "contended-key", {"owner": "worker-b"}, None)
    push_a2 = state_cas.run(
        ["git", "push", "origin", f"{new_commit_a2}:{state_cas.ref_name('contended-key')}"], worktree_a
    )
    push_b2 = state_cas.run(
        ["git", "push", "origin", f"{new_commit_b2}:{state_cas.ref_name('contended-key')}"], worktree_b
    )
    if push_a2.returncode != 0:
        fail(f"expected worker-a's first write on contended-key to succeed, got rc={push_a2.returncode}")
    if push_b2.returncode == 0:
        fail("expected worker-b's contending write on contended-key to be rejected, not silently accepted")
    elif not state_cas.is_ref_rejection(push_b2.stderr.decode()):
        fail(f"expected a ref-rejection message, got: {push_b2.stderr.decode()!r}")
    _, contended_value = state_cas.fetch_current(clone_a, "origin", "contended-key")
    if contended_value != {"owner": "worker-a"}:
        fail(f"expected contended-key to hold worker-a's write, got {contended_value}")

    # 8. delete is CAS-guarded (--force-with-lease), not a blind unconditional push:
    # a delete based on a value that's since moved underneath must be rejected.
    args_del = Args()
    args_del.repo, args_del.remote, args_del.key = clone_a, "origin", "issue-17"
    args_del.expect = json.dumps({"owner": "worker-a"})
    code = state_cas.cmd_delete(args_del)
    if code != 0:
        fail(f"expected CAS-guarded delete of the current value to succeed, got code={code}")
    _, after_delete = state_cas.fetch_current(clone_a, "origin", "issue-17")
    if after_delete is not None:
        fail(f"expected issue-17 to be absent after delete, got {after_delete}")

    # 9. exercise the --force-with-lease mechanism itself, not just cmd_delete's
    # earlier re-fetch check: reproduce the TOCTOU window where the remote value
    # changes strictly *between* a caller's read and its delete push landing (a
    # takeover racing in after worker-a already decided to release). A blind
    # `git push <remote> :<ref>` (the pre-fix behavior) would succeed here and erase
    # worker-b's live claim; the lease must reject it.
    args_reclaim = Args()
    args_reclaim.repo, args_reclaim.remote, args_reclaim.key = clone_a, "origin", "issue-17"
    args_reclaim.expect, args_reclaim.value = "absent", json.dumps({"owner": "worker-a"})
    if state_cas.cmd_set(args_reclaim) != 0:
        fail("expected re-claim of issue-17 to succeed")
    parent_before_race, _ = state_cas.fetch_current(clone_a, "origin", "issue-17")

    # worker-b's takeover lands after worker-a already has parent_before_race in hand.
    args_takeover = Args()
    args_takeover.repo, args_takeover.remote, args_takeover.key = clone_b, "origin", "issue-17"
    args_takeover.expect, args_takeover.value = json.dumps({"owner": "worker-a"}), json.dumps({"owner": "worker-b"})
    if state_cas.cmd_set(args_takeover) != 0:
        fail("expected worker-b's takeover to succeed")

    # worker-a's delete push, using the value it read before the takeover landed.
    lease = f"--force-with-lease={state_cas.ref_name('issue-17')}:{parent_before_race}"
    stale_push = state_cas.run(["git", "push", lease, "origin", f":{state_cas.ref_name('issue-17')}"], clone_a)
    if stale_push.returncode == 0:
        fail("expected a stale --force-with-lease delete to be rejected, not silently erase worker-b's live claim")
    _, still_claimed = state_cas.fetch_current(clone_a, "origin", "issue-17")
    if still_claimed != {"owner": "worker-b"}:
        fail(f"expected worker-b's claim to survive worker-a's stale-lease delete, got {still_claimed}")
finally:
    subprocess.run(["rm", "-rf", work])

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("state_cas: all cases pass")
