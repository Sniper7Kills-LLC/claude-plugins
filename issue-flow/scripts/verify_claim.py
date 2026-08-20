#!/usr/bin/env python3
"""Mechanically verify an existence claim about code at a git ref.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo . --ref origin/main --pattern "def foo"
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_claim.py" --repo . --ref origin/main --pattern "def foo" --expect-sha abc123 --json

A locate-pass or review agent's "this does not exist" is only as good as the
ref it read. Two measured failures (see SKILL.md's locate-pass section) were
both a truthful report against a stale or wrong ref: main instead of the
batch's integration branch, or a working tree one merge behind origin. This
does the same check with `git grep` against a pinned ref, so a claim about
existence/absence is checked against ground truth instead of trusted as
prose. Exit code is authoritative: 0 means the JSON result is trustworthy;
any nonzero means don't trust the claim without investigating.
"""

import argparse
import json
import subprocess
import sys


def run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def resolve_sha(repo, ref):
    result = run(["git", "rev-parse", "--verify", ref], repo)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def grep(repo, sha, pattern):
    result = run(["git", "grep", "-n", "-I", "-e", pattern, sha], repo)
    if result.returncode not in (0, 1):
        return None, result.stderr.strip()
    matches = [line for line in result.stdout.splitlines() if line.strip()]
    return matches, None


def check(repo, ref, pattern, expect_sha):
    sha = resolve_sha(repo, ref)
    if sha is None:
        return 2, {"error": f"cannot resolve ref {ref!r} in {repo!r}"}

    if expect_sha and sha != expect_sha:
        return 3, {"ref": ref, "resolved_sha": sha, "expected_sha": expect_sha, "stale": True}

    matches, err = grep(repo, sha, pattern)
    if err is not None:
        return 4, {"error": f"git grep failed: {err}"}

    return 0, {
        "ref": ref,
        "resolved_sha": sha,
        "pattern": pattern,
        "found": bool(matches),
        "matches": matches,
        "stale": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--expect-sha", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    code, payload = check(args.repo, args.ref, args.pattern, args.expect_sha)

    if args.json or "error" in payload:
        print(json.dumps(payload), file=sys.stderr if "error" in payload else sys.stdout)
    elif payload.get("stale"):
        print(f"STALE: {payload['ref']} resolved to {payload['resolved_sha']}, expected {payload['expected_sha']}")
    elif payload["found"]:
        print(f"FOUND at {payload['resolved_sha']}:")
        for line in payload["matches"]:
            print(f"  {line}")
    else:
        print(f"NOT FOUND at {payload['resolved_sha']} — claim of absence is supported")

    return code


if __name__ == "__main__":
    sys.exit(main())
