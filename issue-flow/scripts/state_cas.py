#!/usr/bin/env python3
"""Git-native compare-and-swap for small coordination state (issue claims,
batch-status swaps) — no external lock service, no server.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" get --repo . --remote origin --key batch-42
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key batch-42 \
        --expect '{"status":"open"}' --value '{"status":"merging"}'
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" set --repo . --remote origin --key issue-17 \
        --expect absent --value '{"owner":"worker-a"}'
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/state_cas.py" delete --repo . --remote origin --key issue-17 \
        --expect '{"owner":"worker-a"}'

State lives as an orphan commit chain under refs/issue-flow/state/<key> (one
commit per write, each holding a single state.json blob — no working-tree
checkout needed). git's own non-fast-forward push rejection is the race
arbiter across concurrent workers/machines: `set` fetches the current value,
refuses to proceed if it doesn't match --expect, and if the push is rejected
because someone else wrote first, that's a lost race, not a partial write.
Every push (`set` and `delete`) targets the remote ref directly by commit SHA
or --force-with-lease, never through a local ref of the same name — linked
worktrees (isolation: "worktree") share one ref store, and a local
update-ref + push-by-name lets a concurrent invocation for a different key
clobber the ref in between, defeating the race arbiter entirely.

Exit codes for `set`/`delete`: 0 = succeeded, 2 = stale (--expect didn't
match, incl. argparse errors — check for JSON on stdout, not just the code),
3 = race-lost (the push was rejected because the ref moved), 4 = the push or
fetch itself failed for another reason (network, auth) — retry, don't treat
as claimed.

This replaces the "re-read the issue's labels and assignees, hope nobody
raced you" pattern (references/parallelism.md's Claim race section) and the
self-checking batch-swap gate (SKILL.md's batch-swap section) with a check
whose correctness is a property of git's push return value, not an agent's
belief that it did the swap.
"""

import argparse
import json
import os
import subprocess
import sys


COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "issue-flow state_cas",
    "GIT_AUTHOR_EMAIL": "noreply@issue-flow.invalid",
    "GIT_COMMITTER_NAME": "issue-flow state_cas",
    "GIT_COMMITTER_EMAIL": "noreply@issue-flow.invalid",
}


def run(args, cwd, input_bytes=None, check=False, env=None):
    result = subprocess.run(args, cwd=cwd, input=input_bytes, capture_output=True, env=env)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.decode()}")
    return result


def ref_name(key):
    return f"refs/issue-flow/state/{key}"


def is_ref_rejection(stderr_text):
    """True when a push failed because the remote ref moved (someone else won the
    race), as opposed to a transport/auth/infra failure. Git's rejection message for
    both a plain non-fast-forward and a --force-with-lease mismatch always contains
    "[rejected]"; other failures (network, auth, unknown repo) don't."""
    return "[rejected]" in stderr_text


def local_fetch_ref(key):
    """A private, per-key landing ref for fetches — never FETCH_HEAD, which is one
    shared file per worktree and gets clobbered by a concurrent invocation's fetch
    for a different key before this one reads it."""
    return f"refs/issue-flow/fetched/{key}"


def fetch_current(repo, remote, key):
    """Returns (commit_sha_or_None, value_dict_or_None)."""
    ref = ref_name(key)
    local_ref = local_fetch_ref(key)
    result = run(["git", "fetch", "--force", remote, f"{ref}:{local_ref}"], repo)
    if result.returncode != 0:
        # Don't infer "absent" from git's (locale-dependent, overly broad) stderr text.
        # `ls-remote --exit-code` has a defined, non-localized exit status: 2 means the
        # ref genuinely doesn't exist; anything else (network, auth, unknown repo) is a
        # real error the caller must not mistake for an unclaimed key.
        probe = run(["git", "ls-remote", "--exit-code", remote, ref], repo)
        if probe.returncode == 2:
            return None, None
        raise RuntimeError(f"fetch failed: {result.stderr.decode().strip()}")
    commit_sha = run(["git", "rev-parse", local_ref], repo, check=True).stdout.decode().strip()
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
    commit = run(commit_args, repo, check=True, env=dict(os.environ, **COMMIT_ENV))
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

    # Push the commit object directly by SHA, naming no local ref. Linked worktrees
    # (isolation: "worktree" gives every worker one) share a single ref store, so a
    # local `update-ref` + push-by-name lets a concurrent invocation for a different
    # key clobber this one's ref between the two steps — both callers then push
    # whatever the ref last pointed at, and git's non-fast-forward check never gets a
    # chance to arbitrate because both sides push the same object. Pushing the SHA
    # itself needs no local ref, so there is nothing to clobber: git still requires
    # the remote ref to be at parent_sha (or absent) for this to land as a
    # fast-forward, which is exactly the CAS condition.
    push_spec = f"{new_commit}:{ref_name(args.key)}"
    push = run(["git", "push", args.remote, push_spec], args.repo)
    if push.returncode != 0:
        stderr = push.stderr.decode().strip()
        if is_ref_rejection(stderr):
            print(json.dumps({"ok": False, "reason": "race-lost", "detail": stderr}))
            return 3
        print(json.dumps({"ok": False, "reason": "push-failed", "detail": stderr}))
        return 4

    print(json.dumps({"ok": True, "key": args.key, "value": new_value}))
    return 0


def cmd_delete(args):
    """CAS-guarded release: deletes the key's ref only if its current value still
    matches --expect, so a worker can only release the claim it actually holds (or,
    for takeover, only the claim whose value it just read)."""
    parent_sha, current = fetch_current(args.repo, args.remote, args.key)
    expect = None if args.expect == "absent" else json.loads(args.expect)
    if current != expect:
        print(json.dumps({"ok": False, "reason": "stale", "current": current}))
        return 2

    if parent_sha is None:
        print(json.dumps({"ok": True, "key": args.key, "value": None}))
        return 0

    # A plain `git push <remote> :<ref>` deletes unconditionally, regardless of what
    # the remote currently holds — the value check above runs against the value this
    # call fetched, but by the time the delete lands the remote may hold something
    # else (a takeover's `set` landed in between). --force-with-lease makes the
    # deletion itself conditional on the remote ref still being at parent_sha.
    lease = f"--force-with-lease={ref_name(args.key)}:{parent_sha}"
    push = run(["git", "push", lease, args.remote, f":{ref_name(args.key)}"], args.repo)
    if push.returncode != 0:
        stderr = push.stderr.decode().strip()
        if is_ref_rejection(stderr):
            print(json.dumps({"ok": False, "reason": "race-lost", "detail": stderr}))
            return 3
        print(json.dumps({"ok": False, "reason": "push-failed", "detail": stderr}))
        return 4

    print(json.dumps({"ok": True, "key": args.key, "value": None}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--remote", default="origin")
    sub = parser.add_subparsers(dest="command", required=True)

    # --repo/--remote are also accepted on each subparser (default=SUPPRESS, so an
    # omitted subcommand-level flag never clobbers a value already set at the top
    # level) so both `state_cas.py --repo . get --key k` and
    # `state_cas.py get --repo . --key k` parse — the module docstring and
    # parallelism.md both use the latter order, which the parent-only parser rejected.
    get_p = sub.add_parser("get")
    get_p.add_argument("--repo", default=argparse.SUPPRESS)
    get_p.add_argument("--remote", default=argparse.SUPPRESS)
    get_p.add_argument("--key", required=True)

    set_p = sub.add_parser("set")
    set_p.add_argument("--repo", default=argparse.SUPPRESS)
    set_p.add_argument("--remote", default=argparse.SUPPRESS)
    set_p.add_argument("--key", required=True)
    set_p.add_argument("--expect", required=True, help="JSON of expected current value, or 'absent'")
    set_p.add_argument("--value", required=True, help="JSON of new value")

    delete_p = sub.add_parser("delete")
    delete_p.add_argument("--repo", default=argparse.SUPPRESS)
    delete_p.add_argument("--remote", default=argparse.SUPPRESS)
    delete_p.add_argument("--key", required=True)
    delete_p.add_argument("--expect", required=True, help="JSON of expected current value, or 'absent'")

    args = parser.parse_args()
    try:
        if args.command == "get":
            return cmd_get(args)
        if args.command == "set":
            return cmd_set(args)
        return cmd_delete(args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
