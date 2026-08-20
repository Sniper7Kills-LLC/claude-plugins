#!/usr/bin/env python3
"""Decide whether CI actually executed for a commit SHA — not just whether a
status exists.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge github --repo owner/name --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge gitea --gitea-url https://gitea.example \
        --owner o --repo r --gitea-token "$TOKEN" --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --fixture runs.json --sha SHA   # test/dry-run

A red or green check can be reported with zero jobs having ever run — a
disabled runner, an unparseable workflow (Gitea registers no run at all for
these), or a skip-ci token reintroduced by a squash-merge commit body nobody
typed (see references/forge.md's Actions and CI section). `decide_ran` is the
mechanized version of "does retrievable log output exist for this SHA,"
independent of whatever the status field claims — kept pure and
fixture-testable, separate from the forge-specific fetch below it.
"""

import argparse
import json
import subprocess
import sys
import urllib.request


def decide_ran(runs_for_sha):
    if not runs_for_sha:
        return {"ran": False, "reason": "no run recorded for this SHA"}
    with_logs = [r for r in runs_for_sha if r.get("log_bytes", 0) > 0]
    if not with_logs:
        return {"ran": False, "reason": "run(s) recorded but zero log bytes retrievable — status is not evidence"}
    return {"ran": True, "reason": f"{len(with_logs)} run(s) with retrievable logs", "runs": with_logs}


def fetch_github(repo, sha):
    result = subprocess.run(
        ["gh", "run", "list", "--repo", repo, "--commit", sha, "--json", "databaseId,status"],
        capture_output=True, text=True, check=True,
    )
    runs = json.loads(result.stdout)
    out = []
    for run in runs:
        log = subprocess.run(
            ["gh", "run", "view", str(run["databaseId"]), "--repo", repo, "--log"],
            capture_output=True, text=True,
        )
        out.append({"id": run["databaseId"], "status": run.get("status"), "log_bytes": len(log.stdout)})
    return out


def fetch_gitea(base_url, owner, repo, token, sha):
    req = urllib.request.Request(
        f"{base_url}/api/v1/repos/{owner}/{repo}/commits/{sha}/status",
        headers={"Authorization": f"token {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        payload = json.loads(resp.read())
    statuses = payload.get("statuses", payload if isinstance(payload, list) else [])
    out = []
    for status in statuses:
        target = status.get("target_url", "")
        log_bytes = 0
        if target:
            try:
                with urllib.request.urlopen(target) as log_resp:
                    log_bytes = len(log_resp.read())
            except Exception:
                log_bytes = 0
        out.append({"id": status.get("id"), "status": status.get("status"), "log_bytes": log_bytes})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--forge", choices=["github", "gitea"])
    parser.add_argument("--repo")
    parser.add_argument("--owner")
    parser.add_argument("--gitea-url")
    parser.add_argument("--gitea-token")
    parser.add_argument("--fixture")
    args = parser.parse_args()

    if args.fixture:
        with open(args.fixture) as handle:
            runs = json.load(handle)
    elif args.forge == "github":
        runs = fetch_github(args.repo, args.sha)
    elif args.forge == "gitea":
        runs = fetch_gitea(args.gitea_url, args.owner, args.repo, args.gitea_token, args.sha)
    else:
        print("error: specify --forge or --fixture", file=sys.stderr)
        return 2

    result = decide_ran(runs)
    print(json.dumps(result))
    return 0 if result["ran"] else 1


if __name__ == "__main__":
    sys.exit(main())
