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
import urllib.error
import urllib.request


def decide_ran(runs_for_sha):
    if not runs_for_sha:
        return {"ran": False, "reason": "no run recorded for this SHA"}
    with_logs = [r for r in runs_for_sha if r.get("log_bytes", 0) > 0]
    if with_logs:
        return {"ran": True, "reason": f"{len(with_logs)} run(s) with retrievable logs", "runs": with_logs}
    still_running = [r for r in runs_for_sha if r.get("status") in ("queued", "in_progress")]
    if still_running:
        return {
            "ran": False,
            "running": True,
            "reason": "run(s) still in progress — logs aren't retrievable yet, which is not evidence CI didn't run",
        }
    return {"ran": False, "reason": "run(s) recorded but zero log bytes retrievable — status is not evidence"}


def fetch_github(repo, sha):
    result = subprocess.run(
        ["gh", "run", "list", "--repo", repo, "--commit", sha, "--json", "databaseId,status"],
        capture_output=True, text=True, check=True,
    )
    runs = json.loads(result.stdout)
    out = []
    for run in runs:
        status = run.get("status")
        log_bytes = 0
        if status not in ("queued", "in_progress"):
            # `gh run view --log` refuses to return anything for a run still in
            # progress — that emptiness would otherwise be misread as "no logs, so
            # it didn't run" rather than "hasn't finished yet".
            log = subprocess.run(
                ["gh", "run", "view", str(run["databaseId"]), "--repo", repo, "--log"],
                capture_output=True, text=True,
            )
            log_bytes = len(log.stdout)
        out.append({"id": run["databaseId"], "status": status, "log_bytes": log_bytes})
    return out


def _gitea_api(base_url, path, token, timeout=10):
    req = urllib.request.Request(f"{base_url}{path}", headers={"Authorization": f"token {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_gitea(base_url, owner, repo, token, sha):
    # The commit-status API's `target_url` is the run's HTML web-UI page, which
    # returns bytes whether or not any job ever executed (the zero-jobs-run case —
    # disabled runner, unparseable workflow — this tool exists to catch) and, being
    # fetched unauthenticated, either 403s or serves a login page on a private
    # instance. Use the Actions runs API (references/forge.md's documented endpoint)
    # instead, and treat a run's dispatched jobs — not a scraped page — as evidence.
    payload = _gitea_api(base_url, f"/api/v1/repos/{owner}/{repo}/actions/runs?head_sha={sha}", token)
    runs = payload.get("workflow_runs") or payload.get("runs") or []
    out = []
    for run in runs:
        run_id = run.get("id")
        status = run.get("status")
        jobs_started = 0
        try:
            jobs_payload = _gitea_api(base_url, f"/api/v1/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", token)
            jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else jobs_payload
            # A job only counts as evidence once it has actually been dispatched —
            # still-queued/blocked jobs exist as objects without ever running.
            jobs_started = sum(1 for j in (jobs or []) if j.get("status") not in ("waiting", "blocked"))
        except Exception:
            jobs_started = 0
        out.append({"id": run_id, "status": status, "log_bytes": jobs_started})
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

    try:
        if args.fixture:
            with open(args.fixture) as handle:
                runs = json.load(handle)
        elif args.forge == "github":
            if not args.repo:
                print("error: --forge github requires --repo owner/name", file=sys.stderr)
                return 2
            runs = fetch_github(args.repo, args.sha)
        elif args.forge == "gitea":
            missing = [
                name for name, value in (
                    ("--gitea-url", args.gitea_url),
                    ("--owner", args.owner),
                    ("--repo", args.repo),
                    ("--gitea-token", args.gitea_token),
                ) if not value
            ]
            if missing:
                print(f"error: --forge gitea requires {', '.join(missing)}", file=sys.stderr)
                return 2
            runs = fetch_gitea(args.gitea_url, args.owner, args.repo, args.gitea_token, args.sha)
        else:
            print("error: specify --forge or --fixture", file=sys.stderr)
            return 2
    except (subprocess.CalledProcessError, urllib.error.URLError, OSError, ValueError) as exc:
        # An infra failure (auth, rate limit, network) must not read as "CI never
        # ran" — that's exit 1 and this is exit 2, same as the other infra-error case.
        print(json.dumps({"error": f"fetch failed: {exc}"}), file=sys.stderr)
        return 2

    result = decide_ran(runs)
    print(json.dumps(result))
    if result["ran"]:
        return 0
    if result.get("running"):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
