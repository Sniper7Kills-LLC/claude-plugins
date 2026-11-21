#!/usr/bin/env python3
"""Tests for scripts/eval-compare.py.

    python3 scripts/test-eval-compare.py

Each test writes a synthetic baseline and a synthetic candidate in the format of
scripts/run-evals.py, runs the compare script on them, and reads its --json
verdict. The inputs sit on both sides of each threshold of the decision rules
(Sniper7Kills-LLC/claude-plugins#106). The tests use no network and no `claude`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "eval-compare.py")
TMP = tempfile.mkdtemp(prefix="test-eval-compare-")

failures = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name)
    if not condition:
        failures.append(name)
        if detail:
            print("       " + str(detail)[:1500])


def pm_record(case, run, score=1.0, det_pass=(True,), tokens=1000, cost=0.5):
    graders = [{"type": "gh_log", "passed": passed, "detail": ""} for passed in det_pass]
    graders.append({"type": "llm", "passed": score >= 1.0, "detail": ""})
    return {
        "case": case, "run": run, "ok": all(det_pass), "score": score, "graders": graders,
        "cost_usd": cost, "judge_cost_usd": 0.001, "turns": 5, "duration_s": 20.0,
        "usage": {"input": 10, "output": tokens - 10, "cache_read": 0, "cache_write": 0},
        "model": "claude-opus-5-5", "error": None,
    }


def worker_record(case, run, hidden=True, cost=1.0):
    graders = [{"type": "hidden_tests", "passed": hidden, "detail": ""},
               {"type": "verdict", "passed": True, "detail": ""}]
    return {
        "case": case, "run": run, "ok": hidden, "score": (hidden + 1) / 2, "graders": graders,
        "cost_usd": cost, "judge_cost_usd": 0.0, "turns": 3, "duration_s": 5.0, "wall_s": 200.0,
        "usage": {"input": 5, "output": 50, "cache_read": 0, "cache_write": 0},
        "model": "haiku", "error": None,
    }


def write_results(name, suite, records, effort="medium"):
    """Write a results directory in the runner's format; effort None leaves the key out."""
    directory = os.path.join(TMP, name)
    os.makedirs(directory, exist_ok=True)
    results = {"suite": suite, "variant": {"plugin_dir": None, "git_sha": "abc", "version": name},
               **({"effort": effort} if effort else {}),
               "started": "20261121T000000Z", "finished": "20261121T010000Z", "max_cost_usd": None,
               "total_cost_usd": sum(r["cost_usd"] for r in records), "partial": False,
               "records": records}
    with open(os.path.join(directory, "results.json"), "w") as handle:
        json.dump(results, handle)
    return directory


def make_evals_dir():
    """Write a case.json with tags for each synthetic worker case; the hybrid rule reads them."""
    directory = os.path.join(TMP, "evals")
    for case, tags in TIERS.items():
        os.makedirs(os.path.join(directory, "worker", case), exist_ok=True)
        with open(os.path.join(directory, "worker", case, "case.json"), "w") as handle:
            json.dump({"name": case, "suite": "worker", "tags": tags}, handle)
    return directory


def compare(baseline, candidate, *extra):
    result = subprocess.run([sys.executable, SCRIPT, baseline, candidate, "--json",
                             "--evals-dir", EVALS, *extra],
                            capture_output=True, text=True)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {"verdict": None, "raw": result.stdout + result.stderr}
    return result.returncode, report


def text_compare(baseline, candidate):
    result = subprocess.run([sys.executable, SCRIPT, baseline, candidate, "--evals-dir", EVALS],
                            capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def check_named(report, name):
    for item in report.get("checks", []):
        if item["name"] == name:
            return item["passed"]
    return None


# ---------------------------------------------------------------- PM rule


PM_CASES = ["pm-a", "pm-b"]


def pm_suite(name, score=1.0, tokens=1000, det_fail=None):
    """Two cases, three runs; `det_fail` names one (case, run) whose grader fails."""
    records = []
    for case in PM_CASES:
        for run in (1, 2, 3):
            failed = det_fail == (case, run)
            records.append(pm_record(case, run, score=score, det_pass=(not failed,), tokens=tokens))
    return write_results(name, "pm", records)


def test_pm_rule():
    print("PM rule")
    base = pm_suite("pm-base", score=1.0, tokens=1000)

    code, report = compare(base, pm_suite("pm-ship", score=1.0, tokens=999))
    check("PM: fewer tokens, same scores -> ship", report.get("verdict") == "ship", report)
    check("PM: ship exits 0", code == 0, code)

    code, report = compare(base, pm_suite("pm-same-tokens", score=1.0, tokens=1000))
    check("PM: equal tokens -> do not ship", report.get("verdict") == "do not ship", report)
    check("PM: the token check fails at equal tokens", check_named(report, "pm_tokens_lower") is False, report)
    check("PM: do not ship exits 1", code == 1, code)

    _, report = compare(base, pm_suite("pm-drop-005", score=0.95, tokens=900))
    check("PM: mean score 0.05 lower -> ship (the threshold is inclusive)",
          report.get("verdict") == "ship", report)

    _, report = compare(base, pm_suite("pm-drop-006", score=0.94, tokens=900))
    check("PM: mean score 0.06 lower -> do not ship", report.get("verdict") == "do not ship", report)
    check("PM: the score check names the failure", check_named(report, "mean_score_within_0.05") is False,
          report)

    _, report = compare(base, pm_suite("pm-higher-score", score=1.0, tokens=900))
    check("PM: a higher candidate score passes the score check",
          check_named(report, "mean_score_within_0.05") is True, report)

    _, report = compare(base, pm_suite("pm-det-regress", score=1.0, tokens=900, det_fail=("pm-b", 2)))
    check("PM: one deterministic grader fails once more than baseline -> do not ship",
          report.get("verdict") == "do not ship", report)
    check("PM: the regression check fails", check_named(report, "no_deterministic_regression") is False,
          report)
    check("PM: the regression names the case",
          any("pm-b" in line for line in report.get("regressions", [])), report)

    # A baseline that already failed once is not a regression when the candidate fails the same count.
    flaky = pm_suite("pm-base-flaky", score=1.0, tokens=1000, det_fail=("pm-a", 1))
    _, report = compare(flaky, pm_suite("pm-cand-flaky", score=1.0, tokens=900, det_fail=("pm-a", 3)))
    check("PM: the same deterministic pass rate is not a regression",
          check_named(report, "no_deterministic_regression") is True, report)


# ---------------------------------------------------------------- worker rule


TIERS = {"w-easy": ["worker", "easy"], "w-medium": ["worker", "medium"], "w-hard": ["worker", "hard"]}


def worker_suite(name, hidden_fail=None, cost=None, runs=20):
    """Three cases, one per tier. `hidden_fail` maps a case to its count of failed runs."""
    hidden_fail = hidden_fail or {}
    cost = cost or {}
    records = []
    for case in TIERS:
        for run in range(1, runs + 1):
            records.append(worker_record(case, run, hidden=run > hidden_fail.get(case, 0),
                                         cost=cost.get(case, 10.0)))
    return write_results(name, "worker", records)


def test_worker_rule():
    print("worker rule")
    # 60 runs at $10 each. One failed run in 60 is 1.67 points; three are exactly 5.
    base = worker_suite("w-base")

    _, report = compare(base, worker_suite("w-ship", hidden_fail={"w-easy": 3}, cost={c: 6.0 for c in TIERS}))
    check("worker: 5 points lower and 40% cheaper -> ship (both thresholds inclusive)",
          report.get("verdict") == "ship", report)

    _, report = compare(base, worker_suite("w-drop", hidden_fail={"w-easy": 4}, cost={c: 6.0 for c in TIERS}))
    check("worker: 6.7 points lower -> not ship", report.get("verdict") != "ship", report)
    check("worker: the pass-rate check fails", check_named(report, "hidden_pass_within_5_points") is False,
          report)
    check("worker: a failed adopt rule reports the per-tier breakdown",
          set(report.get("by_tier", {})) == {"easy", "medium", "hard"}, report)

    _, report = compare(base, worker_suite("w-cost39", cost={c: 6.1 for c in TIERS}))
    check("worker: 39% cheaper -> not ship", report.get("verdict") != "ship", report)
    check("worker: the cost check fails", check_named(report, "cost_at_least_40pct_lower") is False, report)

    # The candidate fails every hard run, but costs $1. The hybrid keeps the baseline on hard:
    # pass rate 0 points lower, cost (1 + 1 + 10) / 3 = 4, 60% lower.
    code, report = compare(base, worker_suite("w-hybrid", hidden_fail={"w-hard": 20},
                                              cost={c: 1.0 for c in TIERS}))
    check("worker: candidate fails hard, hybrid meets the rule -> hybrid",
          report.get("verdict") == "hybrid", report)
    check("worker: hybrid exits 3", code == 3, code)
    hybrid = report.get("hybrid") or {}
    check("worker: the hybrid cost is the baseline on hard plus the candidate elsewhere",
          abs(hybrid.get("candidate_cost_usd", 0) - 4.0) < 1e-9, hybrid)

    # The candidate is cheap but fails medium too, so the hybrid also loses points.
    _, report = compare(base, worker_suite("w-no-hybrid", hidden_fail={"w-hard": 20, "w-medium": 10},
                                           cost={c: 1.0 for c in TIERS}))
    check("worker: the hybrid fails the same rule -> do not ship",
          report.get("verdict") == "do not ship", report)

    # The hybrid meets the pass rate, but the hard runs keep it too expensive:
    # (5 + 5 + 10) / 3 = 6.67, 33% lower.
    _, report = compare(base, worker_suite("w-hybrid-cost", hidden_fail={"w-hard": 20},
                                           cost={c: 5.0 for c in TIERS}))
    check("worker: a hybrid under 40% cheaper -> do not ship",
          report.get("verdict") == "do not ship", report)


# ---------------------------------------------------------------- output and input


def test_output_and_input():
    print("output and input")
    base = pm_suite("o-base", tokens=1000)
    code, text = text_compare(base, pm_suite("o-cand", tokens=900))
    check("text: prints the verdict line", "verdict: ship" in text, text)
    check("text: states the sample size", "3 runs per case" in text, text)
    check("text: states the detection limit", "15 points" in text, text)
    check("text: prints a row for each case", all(case in text for case in PM_CASES), text)

    _, report = compare(base, pm_suite("o-cand2", tokens=900))
    check("json: the sample size is in the report", report.get("sample", {}).get("candidate_runs_per_case") == 3,
          report)

    worker = worker_suite("o-worker")
    code, report = compare(base, worker)
    check("two different suites exit 2", code == 2, (code, report))

    code, report = compare(base, os.path.join(TMP, "does-not-exist"))
    check("a missing results directory exits 2", code == 2, (code, report))

    # A results.json path works as well as its directory.
    code, report = compare(os.path.join(base, "results.json"), pm_suite("o-cand3", tokens=900))
    check("a results.json path is accepted", report.get("verdict") == "ship", report)

    # A case that only one side ran is left out, and the report says so.
    records = [pm_record("pm-a", run, tokens=900) for run in (1, 2, 3)]
    _, report = compare(base, write_results("o-partial", "pm", records))
    check("a case missing from the candidate is named", "pm-b" in report.get("missing_cases", []), report)

    # Decision 5: the run cost governs; a per-agent sum more than 10% off is flagged.
    records = []
    for case in PM_CASES:
        for run in (1, 2, 3):
            record = pm_record(case, run, tokens=900, cost=1.0)
            record["agents"] = {"totals": {"cost_usd": 1.2 if case == "pm-b" else 1.05}}
            records.append(record)
    _, report = compare(base, write_results("o-costcheck", "pm", records))
    flagged = report.get("cost_disagreements", [])
    check("a per-agent cost 20% off the run cost is flagged", any("pm-b" in line for line in flagged), report)
    check("a per-agent cost 5% off the run cost is not flagged",
          not any("pm-a" in line for line in flagged), report)


def test_effort():
    print("effort")
    base = pm_suite("e-base", tokens=1000)
    records = [pm_record(case, run, tokens=900) for case in PM_CASES for run in (1, 2, 3)]
    code, report = compare(base, write_results("e-high", "pm", records, effort="high"))
    check("a different effort exits 5", code == 5, (code, report))
    code, text = text_compare(base, write_results("e-high", "pm", records, effort="high"))
    check("the refusal names both efforts", "medium" in text and "high" in text, text)
    code, report = compare(base, write_results("e-none", "pm", records, effort=None))
    check("a candidate with no effort exits 5", code == 5, (code, report))
    code, report = compare(base, write_results("e-same", "pm", records))
    check("the same effort compares", code == 0 and report.get("verdict") == "ship", (code, report))
    # Both 0.20.0 baselines ran at medium, inherited from the host session.
    baselines = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals", "baselines", "0.20.0")
    for suite in ("pm", "worker"):
        for name in ("results.json", "summary.json"):
            with open(os.path.join(baselines, suite, name), encoding="utf-8") as handle:
                effort = json.load(handle).get("effort")
            check(f"the 0.20.0 {suite} {name} records effort medium", effort == "medium", effort)


EVALS = make_evals_dir()
try:
    test_pm_rule()
    test_worker_rule()
    test_effort()
    test_output_and_input()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

if failures:
    print("\n%d failure(s)" % len(failures))
    sys.exit(1)
print("\nall eval-compare checks passed")
