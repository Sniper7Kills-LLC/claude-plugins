#!/usr/bin/env python3
"""Tests for scripts/rule-coverage.py.

    python3 scripts/test-rule-coverage.py

The check guards the split of issue-flow/skills/issue-flow/SKILL.md into a short
dispatcher plus per-phase reference files. These tests run it against small
synthetic skill trees, against the 0.20.0 SKILL.md read from the git tag, and
against the skill tree in this checkout. The suite runs offline: the optional
LLM judge is a fake script, never the real `claude` binary.

The 0.20.0 tests need the tag `issue-flow--v0.20.0` in the local clone. CI
fetches tags (`fetch-depth: 0` in validate.yml). A missing tag is a failure,
not a skip, because a skipped baseline test reads like a green one.
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "rule-coverage.py")
spec = importlib.util.spec_from_file_location("rule_coverage", SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

BASELINE_REF = "issue-flow--v0.20.0"
BASELINE_PATH = "issue-flow/skills/issue-flow/SKILL.md"
INVENTORY = os.path.join(ROOT, "evals", "rule-inventory.json")

failures = []


def check(name, condition, detail=""):
    if condition:
        print("  ok: %s" % name)
    else:
        failures.append(name)
        print("  FAIL: %s%s" % (name, " — " + detail if detail else ""))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def run_check(inventory, skill_dir, **kwargs):
    """Return (exit code, printed report) of one coverage check."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = rc.run_check(inventory, skill_dir, **kwargs)
    return code, out.getvalue()


SYNTHETIC = """---
name: demo
description: A demo skill. It must never be read as a rule.
---

# Demo skill

The PM always reads the tracker first. This sentence states no rule at all.

## Core invariants

1. **Workers are decision-free.** A worker returns a question
   to the PM.
2. Labels are the state machine.

# Phase 0 — Preflight

Confirm the run configuration. You must confirm it with the user every session.

```bash
git fetch   # never treated as a rule: code fences are skipped
```

## Stage B — Form batches

- Dependency chains share a batch, never a chain of stacked PRs.
- Order the members by dependency.

### Sizing

Do not put more than three workers in flight.

## Stage C — Integrate

### C1 — Sub-merge gate

Only merge a member when its criteria are met.

## Stage D — Monitor deployment

A deploy is done only when browser-verified.

# Hard rules

- Every state change leaves a tracker trace.
- **The PM never implements inline.**
"""


def by_text(rules, fragment):
    matches = [rule for rule in rules if fragment in rule["text"]]
    return matches[0] if matches else None


def test_extract_synthetic():
    print("extract: synthetic baseline")
    rules = rc.extract_rules(SYNTHETIC)
    texts = [rule["text"] for rule in rules]

    rule = by_text(rules, "always reads the tracker")
    check("a keyword sentence before any phase is global",
          rule is not None and rule["phase"] == "global", repr(rule))
    check("a sentence with no keyword is not a rule",
          by_text(rules, "states no rule at all") is None, repr(texts))
    check("frontmatter is not read as a rule",
          by_text(rules, "must never be read") is None, repr(texts))
    check("code fences are not read as rules",
          by_text(rules, "git fetch") is None, repr(texts))

    rule = by_text(rules, "Workers are decision-free")
    check("a Core invariants item is one whole rule, keyword or not",
          rule is not None and "returns a question to the PM" in rule["text"]
          and rule["phase"] == "global", repr(rule))
    check("a Core invariants item with no keyword is still a rule",
          by_text(rules, "Labels are the state machine") is not None, repr(texts))

    rule = by_text(rules, "You must confirm it")
    check("a Phase 0 sentence gets phase 0",
          rule is not None and rule["phase"] == "0", repr(rule))
    check("the keyword sentence is split from its neighbour",
          rule is not None and "Confirm the run configuration" not in rule["text"],
          repr(rule))

    rule = by_text(rules, "Dependency chains share a batch")
    check("a Stage B bullet gets phase B",
          rule is not None and rule["phase"] == "B", repr(rule))
    check("a Stage B bullet with no keyword is not a rule",
          by_text(rules, "Order the members") is None, repr(texts))
    rule = by_text(rules, "Do not put more than three")
    check("a subheading inherits the phase of its stage",
          rule is not None and rule["phase"] == "B", repr(rule))
    rule = by_text(rules, "Only merge a member")
    check("a C1 heading gives phase C1",
          rule is not None and rule["phase"] == "C1", repr(rule))
    rule = by_text(rules, "Every state change leaves")
    check("a Hard rules bullet is a global rule, keyword or not",
          rule is not None and rule["phase"] == "global", repr(rule))

    rule = by_text(rules, "You must confirm it")
    lines = SYNTHETIC.splitlines()
    check("source_line points at the line the rule starts on",
          rule is not None and "You must confirm it" in lines[rule["source_line"] - 1],
          repr(rule))
    check("every rule has id, text, phase and source_line",
          all(set(r) >= {"id", "text", "phase", "source_line"} for r in rules))
    check("rule ids are unique",
          len({r["id"] for r in rules}) == len(rules))
    check("extraction is deterministic",
          rc.extract_rules(SYNTHETIC) == rules)


def synthetic_tree(root, dispatcher):
    skill = os.path.join(root, "skill")
    write(os.path.join(skill, "SKILL.md"), dispatcher)
    return skill


def split_dispatcher(stage_b_link, stage_d_link):
    """Return a dispatcher with the Stage B and Stage D rules moved out."""
    text = SYNTHETIC
    text = text.replace(
        "- Dependency chains share a batch, never a chain of stacked PRs.\n", "")
    text = text.replace("## Stage B — Form batches\n",
                        "## Stage B — Form batches\n\nRead %s.\n" % stage_b_link)
    text = text.replace("A deploy is done only when browser-verified.\n",
                        "Read %s.\n" % stage_d_link)
    return text


def test_check_synthetic():
    print("check: synthetic trees")
    rules = rc.extract_rules(SYNTHETIC)
    inventory = {"rules": rules}
    stage_b_rule = by_text(rules, "Dependency chains share a batch")
    work = tempfile.mkdtemp()
    try:
        skill = synthetic_tree(os.path.join(work, "same"), SYNTHETIC)
        code, report = run_check(inventory, skill)
        check("the baseline tree passes", code == 0, report)

        # A rule moved to the reference file its own phase links passes.
        skill = synthetic_tree(os.path.join(work, "moved-ok"), split_dispatcher(
            "[batching](references/batching.md)", "[deploy](references/deploy.md)"))
        write(os.path.join(skill, "references", "batching.md"),
              "# Batching\n\n- Dependency chains share a batch,\n"
              "  never a chain of **stacked** PRs.\n")
        write(os.path.join(skill, "references", "deploy.md"),
              "A deploy is done *only* when `browser-verified`.\n")
        code, report = run_check(inventory, skill)
        check("a rule moved to a reference linked at its phase passes",
              code == 0, report)

        # The same rule in a file the Stage B section never links fails.
        skill = synthetic_tree(os.path.join(work, "moved-bad"), split_dispatcher(
            "[deploy](references/deploy.md)", "[batching](references/batching.md)"))
        write(os.path.join(skill, "references", "batching.md"),
              "- Dependency chains share a batch, never a chain of stacked PRs.\n")
        write(os.path.join(skill, "references", "deploy.md"),
              "A deploy is done only when browser-verified.\n")
        code, report = run_check(inventory, skill)
        check("a rule in a reference not linked at its phase fails", code == 1, report)
        check("the failure names the rule's id",
              stage_b_rule["id"] in report, report)
        check("the failure says the rule is unreachable and where it is",
              "UNREACHABLE" in report and "references/batching.md" in report, report)

        # A rule removed from the tree fails and is named.
        removed = SYNTHETIC.replace("Only merge a member when its criteria are met.\n", "")
        skill = synthetic_tree(os.path.join(work, "removed"), removed)
        code, report = run_check(inventory, skill)
        missing = by_text(rules, "Only merge a member")
        check("a removed rule fails the check", code == 1, report)
        check("the failure names the removed rule",
              "MISSING" in report and missing["id"] in report
              and "Only merge a member" in report, report)

        # A global rule must stay in the dispatcher, even behind a link.
        moved_global = SYNTHETIC.replace(
            "- Every state change leaves a tracker trace.\n",
            "- See [rules](references/rules.md).\n")
        skill = synthetic_tree(os.path.join(work, "global"), moved_global)
        write(os.path.join(skill, "references", "rules.md"),
              "Every state change leaves a tracker trace.\n")
        code, report = run_check(inventory, skill)
        check("a global rule moved out of the dispatcher fails",
              code == 1 and by_text(rules, "Every state change")["id"] in report, report)

        # A C1 rule may sit in a file the parent Stage C section links.
        c1 = SYNTHETIC.replace(
            "## Stage C — Integrate\n",
            "## Stage C — Integrate\n\nRead [gates](references/gates.md).\n").replace(
            "Only merge a member when its criteria are met.\n", "")
        skill = synthetic_tree(os.path.join(work, "c1"), c1)
        write(os.path.join(skill, "references", "gates.md"),
              "Only merge a member when its criteria are met.\n")
        code, report = run_check(inventory, skill)
        check("a C1 rule linked from the Stage C section passes", code == 0, report)

        # An approved rewording recorded as an alias passes.
        reworded = SYNTHETIC.replace(
            "Only merge a member when its criteria are met.",
            "Merge a member only after every criterion is met.")
        skill = synthetic_tree(os.path.join(work, "alias"), reworded)
        code, report = run_check(inventory, skill)
        check("a reworded rule with no alias fails", code == 1, report)
        aliased = json.loads(json.dumps(inventory))
        target = by_text(aliased["rules"], "Only merge a member")
        target["aliases"] = ["Merge a member only after every criterion is met."]
        code, report = run_check(aliased, skill)
        check("a reworded rule that matches an alias passes", code == 0, report)
    finally:
        shutil.rmtree(work)


def test_judge_is_reported_never_passing():
    print("check: --judge")
    rules = rc.extract_rules(SYNTHETIC)
    work = tempfile.mkdtemp()
    try:
        argv_log = os.path.join(work, "argv.json")
        fake = os.path.join(work, "fake-claude")
        write(fake, "#!%s\nimport json, sys\n"
                    "json.dump({'argv': sys.argv[1:], 'stdin': sys.stdin.read()},"
                    " open(%r, 'w'))\n"
                    "print('YES')\nprint('The text says the same thing.')\n"
              % (sys.executable, argv_log))
        os.chmod(fake, 0o755)
        reworded = SYNTHETIC.replace(
            "Only merge a member when its criteria are met.",
            "Merge a member after every criterion is met, and not before.")
        skill = synthetic_tree(work, reworded)
        code, report = run_check({"rules": rules}, skill, judge=True, claude_bin=fake)
        check("a judge YES does not pass the check", code == 1, report)
        check("the report carries the judge's answer",
              "judge: YES" in report, report)
        with open(argv_log) as handle:
            call = json.load(handle)
        args = call["argv"]
        check("the judge runs claude -p on haiku with no tools",
              args[:5] == ["-p", "--model", "haiku", "--tools", ""], repr(args))
        settings = json.loads(args[args.index("--settings") + 1]) if "--settings" in args else {}
        check("the judge keeps the host's plugins, user settings and MCP servers out",
              "--strict-mcp-config" in args and "--no-session-persistence" in args
              and args[args.index("--setting-sources") + 1] == "project,local"
              and settings.get("enabledPlugins", {}).get("issue-flow@sniper7kills") is False
              if "--setting-sources" in args else False, repr(args))
        check("the judge prompt quotes the rule",
              "Only merge a member when its criteria are met." in args[-1], repr(args))
        check("the judge reads the phase's text on stdin",
              "not before" in call["stdin"], call["stdin"][:200])

        failing = os.path.join(work, "broken-claude")
        write(failing, "#!/bin/sh\necho 'no auth' >&2\nexit 3\n")
        os.chmod(failing, 0o755)
        code, report = run_check({"rules": rules}, skill, judge=True,
                                 claude_bin=failing)
        check("a judge that fails is reported, and the check still fails",
              code == 1 and "judge: error" in report, report)
    finally:
        shutil.rmtree(work)


def baseline_text():
    result = subprocess.run(
        ["git", "show", "%s:%s" % (BASELINE_REF, BASELINE_PATH)],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        check("the baseline tag %s is in this clone" % BASELINE_REF, False,
              result.stderr.strip() + " (run `git fetch --tags`)")
        return None
    return result.stdout


def single_line_rule(rules, lines, phase):
    """Return a rule of `phase` that fills one bullet line by itself."""
    for rule in rules:
        if rule["phase"] != phase:
            continue
        line = lines[rule["source_line"] - 1]
        stripped = line.lstrip()
        if not stripped.startswith("- "):
            continue
        if rc.normalize(stripped[2:]) == rc.normalize(rule["text"]):
            return rule, rule["source_line"] - 1
    return None, None


def test_baseline_tree():
    print("check: the 0.20.0 tree")
    text = baseline_text()
    if text is None:
        return
    with open(INVENTORY) as handle:
        inventory = json.load(handle)
    rules = inventory["rules"]

    fresh = rc.extract_rules(text)
    strip = lambda items: [{k: r[k] for k in ("id", "text", "phase", "source_line")}
                           for r in items]
    check("the checked-in inventory is what extract gives on 0.20.0",
          strip(rules) == strip(fresh),
          "regenerate with: python3 scripts/rule-coverage.py extract")
    check("the inventory names its baseline",
          inventory.get("baseline") == "%s:%s" % (BASELINE_REF, BASELINE_PATH),
          repr(inventory.get("baseline")))
    check("the inventory holds a real number of rules", len(rules) > 100, str(len(rules)))
    check("every rule has a known phase",
          all(r["phase"] in rc.PHASES for r in rules),
          repr(sorted({r["phase"] for r in rules})))
    check("every Stage of the loop has rules",
          {"0", "A0", "A", "B", "C", "C1", "C2", "D", "E", "global"}
          <= {r["phase"] for r in rules})

    work = tempfile.mkdtemp()
    try:
        skill = os.path.join(work, "skill")
        write(os.path.join(skill, "SKILL.md"), text)
        code, report = run_check(inventory, skill)
        check("every rule is present in the 0.20.0 tree", code == 0, report[-2000:])

        lines = text.splitlines(keepends=True)
        hard = [r for r in rules if r["phase"] == "global"
                and lines[r["source_line"] - 1].startswith("- **The PM never implements")]
        check("the Hard rules bullet on inline work is in the inventory", len(hard) == 1)
        if hard:
            removed = os.path.join(work, "removed")
            cut = lines[:hard[0]["source_line"] - 1] + lines[hard[0]["source_line"]:]
            write(os.path.join(removed, "SKILL.md"), "".join(cut))
            code, report = run_check(inventory, removed)
            check("0.20.0 with one rule removed fails", code == 1, report[-2000:])
            check("the failure names the removed rule",
                  hard[0]["id"] in report and "MISSING" in report, report[-2000:])

        rule, index = single_line_rule(rules, lines, "B")
        check("0.20.0 has a one-line Stage B rule to move", rule is not None)
        if rule:
            heading = next(i for i, line in enumerate(lines)
                           if line.startswith("## Stage D"))
            moved = lines[:index] + lines[index + 1:]
            heading -= 1 if heading > index else 0
            moved.insert(heading + 1, "\nRead [moved](references/moved.md).\n")
            bad = os.path.join(work, "moved")
            write(os.path.join(bad, "SKILL.md"), "".join(moved))
            write(os.path.join(bad, "references", "moved.md"), lines[index])
            code, report = run_check(inventory, bad)
            check("0.20.0 with a Stage B rule behind a Stage D link fails",
                  code == 1 and rule["id"] in report and "UNREACHABLE" in report,
                  report[-2000:])
    finally:
        shutil.rmtree(work)


def test_cli_on_this_checkout():
    print("cli: this checkout")
    result = subprocess.run([sys.executable, SCRIPT, "check"], cwd=ROOT,
                            capture_output=True, text=True)
    check("the skill tree in this checkout covers every rule",
          result.returncode == 0, (result.stdout + result.stderr)[-2000:])
    check("the CLI prints a summary line",
          "rule-coverage:" in result.stdout, result.stdout[-500:])


test_extract_synthetic()
test_check_synthetic()
test_judge_is_reported_never_passing()
test_baseline_tree()
test_cli_on_this_checkout()

if failures:
    print("\n%d failure(s)" % len(failures))
    sys.exit(1)
print("\nall rule-coverage checks passed")
