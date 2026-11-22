#!/usr/bin/env python3
"""Turn a results directory of scripts/run-evals.py into a baseline that can be committed.

    python3 scripts/eval-baseline.py <results dir> --out evals/baselines/<version>/<suite>
        --label "issue-flow (git worktree of tag issue-flow--v<version>)" [--projects-root <dir>]

Run it on the same machine as the eval run, before Claude Code removes old transcripts.
The script does three things:

1. It adds the tokens of each agent to each record. It runs
   issue-flow/scripts/usage.py --json on the run's transcript, found by `session_id`,
   and keeps that report as `agents`. `tokens` is {main, subagents}: the sum of the four
   token counts of the main agent, and of every other agent.
2. It clears the local paths: `transcript` and `sandbox` become null, and
   `variant.plugin_dir` becomes the --label text. A record with no `effort` takes the
   `effort` of the results. Then it scans the whole output for a local path (`/Users/`,
   `/home/`, `/private/`, `/var/folders/`). When it finds one, it writes nothing and
   exits 2.
3. It writes summary.json, with the numbers per case and for the suite.
4. It counts the lines of each run's fake-gh log, `<case>/run-<k>/gh-log.jsonl`, as
   `tracker_calls` {total, read, write, error}. A run with no log gets null. The count
   is information only: no decision rule reads it.

The record's `cost_usd` stays the cost that governs. The `agents` cost is an estimate
from pricing.json, kept only for the per-agent breakdown.

Exit codes: 0 when every record has its transcript, 1 when one or more do not, 2 for
bad input or a local path in the output. The output is written only for 0 and 1.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USAGE = os.path.join(ROOT, "issue-flow", "scripts", "usage.py")
_spec = importlib.util.spec_from_file_location("usage", USAGE)
usage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage)
# A committed baseline must hold no path of the machine that made it.
LOCAL_PATH = re.compile(r"/(?:Users|home|private|var/folders)/")


def find_transcript(record, projects_root):
    """Return the transcript of the run: its recorded path, else a search by session id."""
    path = record.get("transcript")
    if path and os.path.isfile(path):
        return path
    session_id = record.get("session_id")
    if not session_id:
        return None
    found = usage.session_transcripts(session_id, projects_root)
    return found[0] if found else None


def local_paths(value, where="$"):
    """Yield (location, text) for each string in `value` that holds a local path."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from local_paths(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from local_paths(item, f"{where}[{index}]")
    elif isinstance(value, str) and LOCAL_PATH.search(value):
        yield where, value


def agent_report(transcript):
    result = subprocess.run([sys.executable, USAGE, "--transcript", transcript, "--json"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"usage.py failed on {transcript}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def token_split(report):
    main = sum(agent[field] for agent in report["agents"] if agent["type"] == "main" for field in usage.TOKEN_FIELDS)
    rest = sum(agent[field] for agent in report["agents"] if agent["type"] != "main" for field in usage.TOKEN_FIELDS)
    return {"main": main, "subagents": rest}


def mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 4) if values else None


def tracker_calls(results_dir, record):
    """Count the lines of a run's fake-gh log by kind, or return None when the run kept no log."""
    path = os.path.join(results_dir, record["case"], f"run-{record['run']}", "gh-log.jsonl")
    if not os.path.isfile(path):
        return None
    counts = {"total": 0, "read": 0, "write": 0, "error": 0}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                kind = json.loads(line).get("kind")
                counts["total"] += 1
                if kind in counts:
                    counts[kind] += 1
    return counts


def summarize(records):
    """Return the summary of one case's records."""
    hidden = [grader["passed"] for r in records for grader in r.get("graders", []) if grader["type"] == "hidden_tests"]
    costs = [r["cost_usd"] for r in records]
    scores = [r["score"] for r in records]
    with_tokens = [r for r in records if r.get("tokens")]
    return {
        "runs": len(records),
        "ok_rate": mean(r["ok"] for r in records),
        "mean_score": mean(scores),
        "score_range": [round(min(scores), 4), round(max(scores), 4)],
        "hidden_pass_rate": mean(hidden) if hidden else None,
        "mean_cost_usd": mean(costs),
        "cost_range": [round(min(costs), 4), round(max(costs), 4)],
        "mean_turns": mean(r.get("turns") for r in records),
        # wall_s includes a background worker; duration_s stops when the main loop ends.
        "mean_duration_s": mean(r.get("wall_s", r.get("duration_s")) for r in records),
        "duration_range": [round(min(r.get("wall_s", r.get("duration_s")) or 0 for r in records), 1),
                           round(max(r.get("wall_s", r.get("duration_s")) or 0 for r in records), 1)],
        "mean_main_tokens": mean(r["tokens"]["main"] for r in with_tokens),
        "mean_subagent_tokens": mean(r["tokens"]["subagents"] for r in with_tokens),
        "mean_subagent_turns": mean(sum(a["turns"] for a in r["agents"]["agents"] if a["type"] != "main")
                                    for r in with_tokens),
        "mean_agent_cost_usd": mean((r.get("agents") or {}).get("totals", {}).get("cost_usd") for r in records),
        # Information only: no decision rule reads it (Sniper7Kills-LLC/claude-plugins#108, #116).
        "mean_tracker_calls": mean((r.get("tracker_calls") or {}).get("total") for r in records),
        "errors": sum(1 for r in records if r.get("error")),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare an eval results directory as a committed baseline.")
    parser.add_argument("results", help="the results directory of run-evals.py")
    parser.add_argument("--out", required=True, help="the baseline directory to write")
    parser.add_argument("--label", required=True, help="the text that replaces variant.plugin_dir")
    parser.add_argument("--projects-root", default=usage.PROJECTS_ROOT, help="default: ~/.claude/projects")
    args = parser.parse_args(argv)

    path = os.path.join(args.results, "results.json")
    try:
        with open(path, encoding="utf-8") as handle:
            results = json.load(handle)
    except (OSError, ValueError) as error:
        print(f"error: {path}: {error}", file=sys.stderr)
        return 2

    missing = []
    for record in results["records"]:
        transcript = find_transcript(record, args.projects_root)
        if transcript is None:
            record["agents"], record["tokens"] = None, None
            missing.append(f"{record['case']} run {record['run']} (session {record.get('session_id')})")
        else:
            record["agents"] = agent_report(transcript)
            record["tokens"] = token_split(record["agents"])
        record["tracker_calls"] = tracker_calls(args.results, record)
        record["transcript"] = None
        record["sandbox"] = None
        # The records of 0.20.0 predate the per-record effort; the top-level value is theirs too.
        record.setdefault("effort", results.get("effort"))
        record["variant"] = {**record.get("variant", {}), "plugin_dir": args.label}
    results["variant"] = {**results.get("variant", {}), "plugin_dir": args.label}

    cases = {}
    for record in results["records"]:
        cases.setdefault(record["case"], []).append(record)
    models = sorted({model for r in results["records"] for model in (r.get("model_usage") or {})})
    summary = {
        "suite": results.get("suite"),
        "variant": results["variant"],
        "effort": results.get("effort"),
        "started": results.get("started"),
        "finished": results.get("finished"),
        "partial": results.get("partial"),
        "models": models,
        "total_cost_usd": round(results.get("total_cost_usd") or 0.0, 6),
        "runs": len(results["records"]),
        "suite_means": summarize(results["records"]),
        "cases": {case: summarize(records) for case, records in sorted(cases.items())},
    }

    found = list(local_paths({"results.json": results, "summary.json": summary}))
    if found:
        for where, text in found:
            print(f"error: local path in {where}: {text[:200]!r}", file=sys.stderr)
        print(f"error: {len(found)} local path(s) in the output; nothing written. Clear them in the "
              "results, or extend the script to clear that field.", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    for line in missing:
        print(f"no transcript: {line}", file=sys.stderr)
    print(f"{len(results['records'])} records, {len(missing)} without a transcript; wrote {args.out}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
