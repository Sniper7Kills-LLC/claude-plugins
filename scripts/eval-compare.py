#!/usr/bin/env python3
"""Compare a candidate eval run with a baseline, and apply the decision rules.

    python3 scripts/eval-compare.py <baseline> <candidate> [--rule pm|worker] [--json]
        [--evals-dir <dir>]

<baseline> and <candidate> are results directories of scripts/run-evals.py, or the
results.json in one. The rule comes from the suite of the two results. The rules are
those of Sniper7Kills-LLC/claude-plugins#106:

- PM rule. Ship only when all three checks pass:
  1. No deterministic grader regresses on any case: its pass rate on the case is not
     lower than in the baseline.
  2. The mean score is not more than 0.05 below the baseline.
  3. The mean PM tokens per run are at least 10% lower. The PM tokens are the four
     token counts of the record's `usage`, which counts only the main loop. Both sides
     need at least 3 runs per case, because one run varies by more than 10%.
- Worker rule. Ship only when both checks pass:
  1. The hidden-test pass rate is not more than 5 points below the baseline.
  2. The mean cost per run (`cost_usd`) is at least 40% lower.
  A run with no result (a timeout, or output that is not a result object) counts as a
  failed run in the hidden-test pass rate. Its cost is not known, so the cost mean
  leaves it out.
  When the worker rule fails, the report gives the numbers per tier (easy, medium,
  hard). The hybrid keeps the baseline runs on the cases tagged `hard` and takes the
  candidate runs on the rest. The verdict is `hybrid` only when the hybrid meets the
  same two checks.

Each threshold is inclusive. The two results must name the same `effort`. A result that
names no effort, or names another effort, makes the script refuse: effort changes cost
and quality, so the rules would measure the effort and not the plugin.

The script also refuses an incomplete candidate: a case of the baseline that the
candidate did not run, `partial: true`, or fewer runs per case than the baseline. Such
a run is a smoke test only, not a decision. A case that only the candidate ran is left
out and named. The worker rule refuses when `--evals-dir` has no case.json for a case,
because the tiers come from its tags.
Exit codes: 0 ship, 1 do not ship, 3 hybrid, 2 bad input, 5 a refusal (different or
unknown effort, an incomplete candidate, or a missing case file).
"""

import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Floating-point sums of exact inputs must not fall on the wrong side of a threshold.
EPSILON = 1e-9
SCORE_DROP_MAX = 0.05
HIDDEN_DROP_MAX = 0.05
COST_CUT_MIN = 0.40
# The operator's margin of 2026-11-21: one PM run varies by more than 10% from the 3-run mean.
PM_TOKEN_CUT_MIN = 0.10
PM_MIN_RUNS = 3
SMOKE = "smoke test only, not a decision"
COST_DISAGREEMENT = 0.10
DETECTION_LIMIT = "15 points"
TIERS = ("easy", "medium", "hard")


class InputError(Exception):
    """A results file that cannot be compared."""


class RefusalError(Exception):
    """Two results that the rules must not decide on: exit 5."""


class EffortError(RefusalError):
    """Two results that ran at different efforts, or a result with no effort."""


class IncompleteError(RefusalError):
    """A candidate that does not cover the baseline, or too few runs for the rule."""


def check_effort(baseline, candidate):
    base, cand = baseline.get("effort"), candidate.get("effort")
    if base is None or cand is None or base != cand:
        raise EffortError(f"the efforts differ or are unknown: baseline {base!r}, candidate {cand!r}; "
                          "run the candidate with --effort set to the baseline's effort")


def load(path):
    """Return the results object at `path`, a results directory or its results.json."""
    if os.path.isdir(path):
        path = os.path.join(path, "results.json")
    if not os.path.isfile(path):
        raise InputError(f"{path}: no results.json")
    try:
        with open(path, encoding="utf-8") as handle:
            results = json.load(handle)
    except ValueError as error:
        raise InputError(f"{path}: not JSON: {error}")
    if not isinstance(results, dict) or not isinstance(results.get("records"), list):
        raise InputError(f"{path}: no records list")
    return results


def by_case(records):
    grouped = {}
    for record in records:
        grouped.setdefault(record["case"], []).append(record)
    return grouped


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def has_result(record):
    """False for a run whose `claude -p` gave no result object: a timeout or a parse failure.

    Such a run has an error and no session id. Its cost of 0 is not a real cost.
    """
    return not (record.get("error") and not record.get("session_id"))


def costs(records):
    return [r["cost_usd"] for r in records if has_result(r)]


def tokens(record):
    usage = record.get("usage") or {}
    return sum(usage.get(field) or 0 for field in ("input", "output", "cache_read", "cache_write"))


def hidden_runs(records):
    """Return one bool per run that has a hidden_tests grader; a run with no result fails."""
    return [grader["passed"] and has_result(record) for record in records
            for grader in record.get("graders", []) if grader["type"] == "hidden_tests"]


def deterministic_rates(records):
    """Return {grader index: (type, pass rate)} for the deterministic graders of one case."""
    counts = {}
    for record in records:
        for index, grader in enumerate(record.get("graders", [])):
            if grader["type"] == "llm":
                continue
            kind, passed, total = counts.get(index, (grader["type"], 0, 0))
            counts[index] = (kind, passed + bool(grader["passed"]), total + 1)
    return {index: (kind, passed / total) for index, (kind, passed, total) in counts.items()}


def spread(values):
    values = list(values)
    if not values:
        return None
    return {"min": round(min(values), 4), "max": round(max(values), 4),
            "stdev": round(statistics.pstdev(values), 4)}


def case_row(records):
    hidden = hidden_runs(records)
    return {
        "runs": len(records),
        "ok_rate": round(mean(r["ok"] for r in records), 4),
        "mean_score": round(mean(r["score"] for r in records), 4),
        "score_spread": spread(r["score"] for r in records),
        "hidden_pass_rate": round(mean(hidden), 4) if hidden else None,
        "mean_cost_usd": round(mean(costs(records)), 4) if costs(records) else None,
        "cost_spread": spread(costs(records)),
        "no_result_runs": sum(not has_result(r) for r in records),
        "mean_main_tokens": round(mean(tokens(r) for r in records)),
    }


def case_tags(evals_dir, suite, case):
    path = os.path.join(evals_dir, suite, case, "case.json")
    if not os.path.isfile(path):
        raise RefusalError(f"{path} does not exist; the worker rule reads the tiers from it. "
                           "Give --evals-dir the directory that holds the cases")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("tags") or []


def worker_numbers(groups, cases):
    records = [record for case in cases for record in groups[case]]
    hidden = hidden_runs(records)
    return {"hidden_pass_rate": mean(hidden) if hidden else None,
            "cost_usd": mean(costs(records)),
            "runs": len(records), "no_result_runs": sum(not has_result(r) for r in records)}


def worker_checks(base, cand):
    """Return the two checks of the worker rule for two {hidden_pass_rate, cost_usd} dicts."""
    hidden_ok = (base["hidden_pass_rate"] is not None and cand["hidden_pass_rate"] is not None
                 and cand["hidden_pass_rate"] >= base["hidden_pass_rate"] - HIDDEN_DROP_MAX - EPSILON)
    cost_ok = ((base["cost_usd"] or 0) > 0 and cand["cost_usd"] is not None
               and cand["cost_usd"] <= base["cost_usd"] * (1 - COST_CUT_MIN) + EPSILON)
    cut = 1 - cand["cost_usd"] / base["cost_usd"] if base["cost_usd"] and cand["cost_usd"] is not None else None
    return [
        {"name": "hidden_pass_within_5_points", "passed": hidden_ok,
         "baseline": base["hidden_pass_rate"], "candidate": cand["hidden_pass_rate"]},
        {"name": "cost_at_least_40pct_lower", "passed": cost_ok,
         "baseline": base["cost_usd"], "candidate": cand["cost_usd"],
         "cut": round(cut, 4) if cut is not None else None},
    ]


def pm_rule(base_groups, cand_groups, cases):
    regressions = []
    for case in cases:
        base_rates = deterministic_rates(base_groups[case])
        cand_rates = deterministic_rates(cand_groups[case])
        for index, (kind, rate) in sorted(base_rates.items()):
            cand = cand_rates.get(index)
            if cand is None or cand[0] != kind:
                raise InputError(f"{case}: grader {index} ({kind}) differs between the two runs")
            if cand[1] < rate - EPSILON:
                regressions.append(f"{case}: grader {index} ({kind}) pass rate {rate:.2f} -> {cand[1]:.2f}")
    base_records = [r for case in cases for r in base_groups[case]]
    cand_records = [r for case in cases for r in cand_groups[case]]
    base_score, cand_score = mean(r["score"] for r in base_records), mean(r["score"] for r in cand_records)
    base_tokens, cand_tokens = mean(tokens(r) for r in base_records), mean(tokens(r) for r in cand_records)
    checks = [
        {"name": "no_deterministic_regression", "passed": not regressions, "regressions": len(regressions)},
        # A higher score passes: the rule guards against a drop.
        {"name": "mean_score_within_0.05", "passed": cand_score >= base_score - SCORE_DROP_MAX - EPSILON,
         "baseline": round(base_score, 4), "candidate": round(cand_score, 4)},
        {"name": "pm_tokens_at_least_10pct_lower",
         "passed": base_tokens > 0 and cand_tokens <= base_tokens * (1 - PM_TOKEN_CUT_MIN) + EPSILON,
         "baseline": round(base_tokens), "candidate": round(cand_tokens),
         "cut": round(1 - cand_tokens / base_tokens, 4) if base_tokens else None},
    ]
    verdict = "ship" if all(c["passed"] for c in checks) else "do not ship"
    return {"verdict": verdict, "checks": checks, "regressions": regressions}


def worker_rule(base_groups, cand_groups, cases, evals_dir):
    # Read the tags first, so a missing case file is refused whatever the verdict would be.
    tags = {case: case_tags(evals_dir, "worker", case) for case in cases}
    checks = worker_checks(worker_numbers(base_groups, cases), worker_numbers(cand_groups, cases))
    report = {"verdict": "ship", "checks": checks, "by_tier": None, "hybrid": None}
    if all(c["passed"] for c in checks):
        return report
    report["by_tier"] = {}
    for tier in TIERS:
        tier_cases = [case for case in cases if tier in tags[case]]
        if tier_cases:
            report["by_tier"][tier] = {"cases": len(tier_cases),
                                       "baseline": worker_numbers(base_groups, tier_cases),
                                       "candidate": worker_numbers(cand_groups, tier_cases)}
    hard = [case for case in cases if "hard" in tags[case]]
    if not hard or len(hard) == len(cases):
        report["verdict"] = "do not ship"
        return report
    # The hybrid runs the baseline model on hard cases and the candidate model on the rest.
    hybrid_groups = {case: (base_groups if case in hard else cand_groups)[case] for case in cases}
    hybrid = worker_numbers(hybrid_groups, cases)
    hybrid_checks = worker_checks(worker_numbers(base_groups, cases), hybrid)
    report["hybrid"] = {"hard_cases": hard, "candidate_hidden_pass_rate": hybrid["hidden_pass_rate"],
                        "candidate_cost_usd": hybrid["cost_usd"], "checks": hybrid_checks}
    report["verdict"] = "hybrid" if all(c["passed"] for c in hybrid_checks) else "do not ship"
    return report


def cost_disagreements(records):
    """Return one line per run whose per-agent cost is more than 10% off its run cost."""
    lines = []
    for record in records:
        agents = record.get("agents") or {}
        agent_cost = (agents.get("totals") or {}).get("cost_usd")
        if agent_cost is None or not record.get("cost_usd"):
            continue
        gap = abs(agent_cost - record["cost_usd"]) / record["cost_usd"]
        if gap > COST_DISAGREEMENT + EPSILON:
            lines.append(f"{record['case']} run {record['run']}: cost_usd ${record['cost_usd']:.4f}, "
                         f"per-agent sum ${agent_cost:.4f} ({gap:.0%} apart)")
    return lines


def runs_per_case(groups, cases):
    counts = sorted({len(groups[case]) for case in cases})
    return counts[0] if len(counts) == 1 else counts


def check_complete(baseline, candidate, base_groups, cand_groups, rule):
    """Raise IncompleteError when the candidate cannot give a decision against the baseline."""
    problems = []
    absent = sorted(set(base_groups) - set(cand_groups))
    if absent:
        problems.append(f"the candidate did not run {len(absent)} baseline case(s): {', '.join(absent)}")
    if candidate.get("partial"):
        problems.append("the candidate is partial: the cost ceiling stopped it")
    fewer = sorted(case for case in base_groups if case in cand_groups
                   and len(cand_groups[case]) < len(base_groups[case]))
    for case in fewer:
        problems.append(f"{case}: {len(cand_groups[case])} candidate run(s), {len(base_groups[case])} in the baseline")
    if rule == "pm":
        short = sorted(case for case in set(base_groups) & set(cand_groups)
                       if min(len(base_groups[case]), len(cand_groups[case])) < PM_MIN_RUNS)
        if short:
            problems.append(f"the PM rule compares {PM_MIN_RUNS}-run means and needs at least {PM_MIN_RUNS} runs "
                            f"per case on both sides; too few in: {', '.join(short)}")
    if problems:
        raise IncompleteError(f"{SMOKE}: " + "; ".join(problems))


def compare(baseline, candidate, rule=None, evals_dir=None):
    suite = baseline.get("suite")
    if suite != candidate.get("suite"):
        raise InputError(f"the suites differ: baseline {suite!r}, candidate {candidate.get('suite')!r}")
    rule = rule or suite
    if rule not in ("pm", "worker"):
        raise InputError(f"no decision rule for suite {rule!r}; give --rule pm or --rule worker")
    base_groups, cand_groups = by_case(baseline["records"]), by_case(candidate["records"])
    cases = sorted(set(base_groups) & set(cand_groups))
    if not cases:
        raise InputError("the two results have no case in common")
    check_complete(baseline, candidate, base_groups, cand_groups, rule)
    # After check_complete, only the candidate can have a case that the other side lacks.
    missing = sorted(set(cand_groups) - set(base_groups))
    if rule == "pm":
        report = pm_rule(base_groups, cand_groups, cases)
    else:
        report = worker_rule(base_groups, cand_groups, cases, evals_dir or os.path.join(ROOT, "evals"))
    base_runs, cand_runs = runs_per_case(base_groups, cases), runs_per_case(cand_groups, cases)
    report.update({
        "rule": rule,
        "effort": baseline.get("effort"),
        "baseline_variant": baseline.get("variant"),
        "candidate_variant": candidate.get("variant"),
        "cases": {case: {"baseline": case_row(base_groups[case]), "candidate": case_row(cand_groups[case])}
                  for case in cases},
        "missing_cases": missing,
        "sample": {"cases": len(cases), "baseline_runs_per_case": base_runs,
                   "candidate_runs_per_case": cand_runs},
        "caveat": (f"At this sample size a drop under about {DETECTION_LIMIT} cannot be detected. "
                   "A pass here is not proof that no small regression exists."),
        "cost_disagreements": cost_disagreements(baseline["records"]) + cost_disagreements(candidate["records"]),
        "no_result_runs": {side: sum(not has_result(r) for case in cases for r in groups[case])
                           for side, groups in (("baseline", base_groups), ("candidate", cand_groups))},
    })
    report.setdefault("regressions", [])
    return report


def money(value):
    return "n/a" if value is None else f"${value:.2f}"


def rate(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def span(values, unit=""):
    return "n/a" if values is None else f"{unit}{values['min']:.2f}-{unit}{values['max']:.2f}"


def render(report):
    """Return the report as text for a person."""
    lines = [f"rule: {report['rule']}", f"effort: {report.get('effort')}"]
    for side in ("baseline", "candidate"):
        variant = report.get(f"{side}_variant") or {}
        lines.append(f"{side}: version {variant.get('version')} at {str(variant.get('git_sha'))[:12]}")
    sample = report["sample"]
    lines.append(f"sample: {sample['cases']} cases; baseline {sample['baseline_runs_per_case']} runs per case, "
                 f"candidate {sample['candidate_runs_per_case']} runs per case")
    lines.append("")
    lines.append(f"{'case':34} {'ok b/c':>11} {'score b/c':>11} {'hidden b/c':>13} {'cost b/c':>15} "
                 f"{'main tokens b/c':>19}")
    for case, row in report["cases"].items():
        b, c = row["baseline"], row["candidate"]
        lines.append(
            f"{case:34} {b['ok_rate']:>5.2f}/{c['ok_rate']:<5.2f} {b['mean_score']:>5.2f}/{c['mean_score']:<5.2f} "
            f"{rate(b['hidden_pass_rate']):>6}/{rate(c['hidden_pass_rate']):<6} "
            f"{money(b['mean_cost_usd']):>7}/{money(c['mean_cost_usd']):<7} "
            f"{b['mean_main_tokens']:>9}/{c['mean_main_tokens']:<9}")
        lines.append(f"{'':34} run-to-run range: score b {span(b['score_spread'])}, c {span(c['score_spread'])}; "
                     f"cost b {span(b['cost_spread'], '$')}, c {span(c['cost_spread'], '$')}")
    lines.append("")
    for check in report["checks"]:
        values = {k: v for k, v in check.items() if k not in ("name", "passed")}
        lines.append(f"{'PASS' if check['passed'] else 'FAIL'}  {check['name']}  {values}")
    for line in report["regressions"]:
        lines.append(f"      regression: {line}")
    if report.get("by_tier"):
        lines.append("")
        lines.append("per tier (hidden-test pass rate, mean cost per run):")
        for tier, row in report["by_tier"].items():
            lines.append(f"  {tier:7} {row['cases']} cases  baseline {rate(row['baseline']['hidden_pass_rate'])} "
                         f"{money(row['baseline']['cost_usd'])}  candidate "
                         f"{rate(row['candidate']['hidden_pass_rate'])} {money(row['candidate']['cost_usd'])}")
    if report.get("hybrid"):
        hybrid = report["hybrid"]
        lines.append(f"hybrid (baseline on {', '.join(hybrid['hard_cases'])}): hidden "
                     f"{rate(hybrid['candidate_hidden_pass_rate'])}, cost {money(hybrid['candidate_cost_usd'])}")
        for check in hybrid["checks"]:
            lines.append(f"  {'PASS' if check['passed'] else 'FAIL'}  {check['name']}")
    if report["missing_cases"]:
        lines.append(f"left out (only the candidate ran them): {', '.join(report['missing_cases'])}")
    if any(report["no_result_runs"].values()):
        lines.append(f"runs with no result (failed runs, left out of the cost mean): "
                     f"baseline {report['no_result_runs']['baseline']}, "
                     f"candidate {report['no_result_runs']['candidate']}")
    for line in report["cost_disagreements"]:
        lines.append(f"cost check: {line}")
    lines.append("")
    lines.append(report["caveat"])
    lines.append(f"verdict: {report['verdict']}")
    return "\n".join(lines)


EXIT_CODES = {"ship": 0, "do not ship": 1, "hybrid": 3}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply the eval decision rules to a baseline and a candidate.")
    parser.add_argument("baseline", help="the baseline results directory or results.json")
    parser.add_argument("candidate", help="the candidate results directory or results.json")
    parser.add_argument("--rule", choices=("pm", "worker"), help="the rule to apply (default: the suite)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--evals-dir", default=os.path.join(ROOT, "evals"),
                        help="the cases directory, for the tier tags (default: evals/)")
    args = parser.parse_args(argv)
    try:
        baseline, candidate = load(args.baseline), load(args.candidate)
        check_effort(baseline, candidate)
        report = compare(baseline, candidate, args.rule, args.evals_dir)
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except RefusalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 5
    print(json.dumps(report, indent=2) if args.json else render(report))
    return EXIT_CODES[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
