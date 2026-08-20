#!/usr/bin/env python3
"""Git-native compare-and-swap for small coordination state (issue claims,
batch-status swaps) — no external lock service, no server.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" get --repo . --remote origin --key batch-42
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key batch-42 \
        --expect '{"status":"open"}' --value '{"status":"merging"}'
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key issue-17 \
        --expect absent --value '{"owner":"worker-a"}'

State lives as an orphan commit chain under refs/issue-flow/state/<key> (one
commit per write, each holding a single state.json blob — no working-tree
checkout needed). git's own non-fast-forward push rejection is the race
arbiter across concurrent workers/machines: `set` fetches the current value,
refuses to proceed if it doesn't match --expect, and if the push is rejected
because someone else wrote first, that's a lost race, not a partial write.

This replaces the "re-read the issue's labels and assignees, hope nobody
raced you" pattern (references/parallelism.md's Claim race section) and the
self-checking batch-swap gate (SKILL.md's batch-swap section) with a check
whose correctness is a property of git's push return value, not an agent's
belief that it did the swap.
"""

import argparse
import json
import subprocess
import sys


def run(args, cwd, input_bytes=None, check=False):
    result = subprocess.run(args, cwd=cwd, input=input_bytes, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.decode()}")
    return result


def ref_name(key):
    return f"refs/issue-flow/state/{key}"


def fetch_current(repo, remote, key):
    """Returns (commit_sha_or_None, value_dict_or_None)."""
    result = run(["git", "fetch", remote, ref_name(key)], repo)
    if result.returncode != 0:
        stderr = result.stderr.decode()
        if "couldn't find remote ref" in stderr or "not found" in stderr:
            return None, None
        raise RuntimeError(f"fetch failed: {stderr.strip()}")
    fetch_head = run(["git", "rev-parse", "FETCH_HEAD"], repo, check=True)
    commit_sha = fetch_head.stdout.decode().strip()
    blob = run(["git", "cat-file", "-p", f"{commit_sha}:state.json"], repo, check=True)
    return commit_sha, json.loads(blob.stdout.decode())


def write_commit(repo, key, value, parent_sha):
    blob = run(["git", "hash-object", "-w", "--stdin"], repo, input_bytes=json.dumps(value).encode(), check=True)
    blob_sha = blob.stdout.decode().strip()
    tree_input = f"100644 blob {blob_sha}\tstate.json\n".encode()
    tree = run(["git", "mktree"], repo, input_bytes=tree_input, check=True)
    tree_sha = tree.stdout.decode().strip()
    commit_args = ["git", "commit-tree", tree_sha, "-m", f"state: {key}"]
    if parent_sha:
        commit_args += ["-p", parent_sha]
    commit = run(commit_args, repo, check=True)
    return commit.stdout.decode().strip()


def cmd_get(args):
    _, value = fetch_current(args.repo, args.remote, args.key)
    print(json.dumps({"key": args.key, "value": value}))
    return 0


def cmd_set(args):
    parent_sha, current = fetch_current(args.repo, args.remote, args.key)
    expect = None if args.expect == "absent" else json.loads(args.expect)
    if current != expect:
        print(json.dumps({"ok": False, "reason": "stale", "current": current}))
        return 2

    new_value = json.loads(args.value)
    new_commit = write_commit(args.repo, args.key, new_value, parent_sha)
    run(["git", "update-ref", ref_name(args.key), new_commit], args.repo, check=True)

    push_spec = f"{ref_name(args.key)}:{ref_name(args.key)}"
    push = run(["git", "push", args.remote, push_spec], args.repo)
    if push.returncode != 0:
        print(json.dumps({"ok": False, "reason": "race-lost", "detail": push.stderr.decode().strip()}))
        return 3

    print(json.dumps({"ok": True, "key": args.key, "value": new_value}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    sub = parser.add_subparsers(dest="command", required=True)

    get_p = sub.add_parser("get")
    get_p.add_argument("--key", required=True)

    set_p = sub.add_parser("set")
    set_p.add_argument("--key", required=True)
    set_p.add_argument("--expect", required=True, help="JSON of expected current value, or 'absent'")
    set_p.add_argument("--value", required=True, help="JSON of new value")

    args = parser.parse_args()
    try:
        return cmd_get(args) if args.command == "get" else cmd_set(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
