#!/usr/bin/env python3
"""Decide whether CI actually executed for a commit SHA — not just whether a
status exists.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge github --repo owner/name --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --forge gitea --repo-path . --sha SHA
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify_ci_ran.py" --fixture runs.json --sha SHA   # test/dry-run

A red or green check can be reported with zero jobs having ever run — a
disabled runner, an unparseable workflow (Gitea registers no run at all for
these), or a skip-ci token reintroduced by a squash-merge commit body nobody
typed (see references/forge.md's Actions and CI section). `decide_ran` is the
mechanized version of "does retrievable log output exist for this SHA,"
independent of whatever the status field claims — kept pure and
fixture-testable, separate from the forge-specific fetch below it.

Exit codes: 0 = ran, 1 = never ran, 2 = still in progress or an infra error
fetching status (retry later), 5 = the invocation itself is malformed (missing
required flags for the chosen --forge) — kept apart from 2 so a misconfigured
command doesn't loop forever misread as "check back later".
"""

import argparse
import json
import subprocess
import sys


def decide_ran(runs_for_sha):
    if not runs_for_sha:
        return {"ran": False, "reason": "no run recorded for this SHA"}
    with_logs = [r for r in runs_for_sha if r.get("log_bytes", 0) > 0]
    if with_logs:
        return {"ran": True, "reason": f"{len(with_logs)} run(s) with retrievable logs", "runs": with_logs}
    # GitHub's non-terminal statuses aren't just queued/in_progress — an
    # approval-gated deployment sits in "waiting", and "requested"/"pending" occur
    # too. Gitea emits "waiting" for its own blocked state. None of these are
    # evidence CI didn't run; treating them as such is the false-never-ran case this
    # function exists to avoid.
    still_running = [
        r for r in runs_for_sha
        if r.get("status") in ("queued", "in_progress", "waiting", "requested", "pending")
    ]
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


def _tea_api(repo_path, path):
    # `tea api` (references/forge.md's documented Gitea client) resolves host and
    # credentials from the checkout's git remote — there is no other configuration
    # path in this plugin for a Gitea token, so a hand-rolled urllib client had
    # nowhere real to get one from. Routing through `tea api` inherits the auth the
    # rest of the plugin already uses instead of inventing a second one.
    result = subprocess.run(["tea", "api", path], cwd=repo_path, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"tea api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_gitea(repo_path, sha):
    # The commit-status API's `target_url` is the run's HTML web-UI page, which
    # returns bytes whether or not any job ever executed (the zero-jobs-run case —
    # disabled runner, unparseable workflow — this tool exists to catch). Use the
    # Actions runs API instead, and treat a run's dispatched jobs — not a scraped
    # page — as evidence. `{owner}`/`{repo}` are substituted by `tea api` itself from
    # the current checkout's remote (forge.md's own usage pattern).
    payload = _tea_api(repo_path, f"/repos/{{owner}}/{{repo}}/actions/runs?head_sha={sha}")
    runs = payload.get("workflow_runs") or payload.get("runs") or []
    out = []
    for run in runs:
        run_id = run.get("id")
        status = run.get("status")
        # Gitea serializes GitHub's status vocabulary over the wire, not its own
        # internal names: "waiting" means blocked (never dispatched), "queued" means
        # not yet dispatched, and a job only actually executed once it reaches
        # "completed" with a real conclusion — Gitea's own HasRun() predicate is
        # StatusSuccess || StatusFailure. cancelled/skipped are terminal but never ran.
        jobs_payload = _tea_api(repo_path, f"/repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs")
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else jobs_payload
        jobs_started = sum(
            1 for j in (jobs or [])
            if j.get("status") == "completed" and j.get("conclusion") in ("success", "failure")
        )
        out.append({"id": run_id, "status": status, "log_bytes": jobs_started})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--forge", choices=["github", "gitea"])
    parser.add_argument("--repo", help="github only: owner/name")
    parser.add_argument("--repo-path", default=".", help="gitea only: local checkout tea api resolves auth/owner/repo from")
    parser.add_argument("--fixture")
    args = parser.parse_args()

    try:
        if args.fixture:
            with open(args.fixture) as handle:
                runs = json.load(handle)
        elif args.forge == "github":
            if not args.repo:
                print("error: --forge github requires --repo owner/name", file=sys.stderr)
                return 5
            runs = fetch_github(args.repo, args.sha)
        elif args.forge == "gitea":
            runs = fetch_gitea(args.repo_path, args.sha)
        else:
            print("error: specify --forge or --fixture", file=sys.stderr)
            return 5
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError) as exc:
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
