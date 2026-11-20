#!/usr/bin/env python3
"""Tests that the event markers and commit trailers shown in the issue-flow docs use the
grammar that issue-flow/references/timeline.md documents.

The grammar is parsed from timeline.md, not copied here, so a change to the grammar breaks
this test until the docs follow. The test reads the PM docs (SKILL.md and its references),
the worker agent definition and the README. It checks every marker and trailer example, and
that every comment role the PM writes has a marker example."""

import os
import re
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "issue-flow")
failures = []


def check(cond, message):
    if not cond:
        failures.append(message)


def read(*parts):
    with open(os.path.join(PLUGIN, *parts), encoding="utf-8") as f:
        return f.read()


TIMELINE = read("references", "timeline.md")
SKILL_DIR = os.path.join(PLUGIN, "skills", "issue-flow")
PM_FILES = ["SKILL.md"] + [os.path.join("references", n) for n in sorted(os.listdir(os.path.join(SKILL_DIR, "references")))
                           if n.endswith(".md")]
PM_DOCS = {name: read("skills", "issue-flow", name) for name in PM_FILES}
WORKER = read("agents", "issue-worker.md")
README = read("README.md")

# ---------------------------------------------------------------- grammar from timeline.md
roles_line = re.search(r"^Roles: (.+?)\.\n\n", TIMELINE, re.M | re.S)
ROLES = set(re.findall(r"`([a-z.-]+)`", roles_line.group(1))) if roles_line else set()
check(len(ROLES) >= 9, f"timeline.md lists too few comment roles: {sorted(ROLES)}")

grammar = re.search(r"```\n(<!-- issue-flow:event [^\n]*-->)\n```", TIMELINE)
check(grammar is not None, "timeline.md has no marker grammar block")
GRAMMAR = grammar.group(1) if grammar else ""
ATTRS = re.findall(r"(\w+)=<\w+>", GRAMMAR)
REQUIRED = {a for a in ATTRS if re.search(rf"(?<!\[){a}=<", GRAMMAR)}
check({"role", "issue"} <= REQUIRED, f"marker grammar lost role or issue: {GRAMMAR}")

CHANGES = set()
TRAILER_KEYS = set()
for row in re.finditer(r"^\| `(\w+):` \| ([^|]+)\|", TIMELINE, re.M):
    TRAILER_KEYS.add(row.group(1))
    if row.group(1) == "Change":
        CHANGES = set(re.findall(r"`(\w+)`", row.group(2)))
check({"Change", "Why", "Fixes"} <= TRAILER_KEYS, f"timeline.md trailer table changed: {TRAILER_KEYS}")
check({"feature", "fix", "test", "docs", "refactor"} <= CHANGES, f"timeline.md Change values changed: {CHANGES}")

MARKER = re.compile(r"<!--\s*issue-flow:event\s+(.*?)\s*-->")
NUMBER = re.compile(r"\d+|<n>|<number>")
KEY = re.compile(r"[A-Za-z0-9_-]+|<k>")


def marker_errors(attrs_text):
    """Return the grammar violations of one marker's attribute text."""
    errors = []
    attrs = {}
    for pair in attrs_text.split():
        name, sep, value = pair.partition("=")
        if not sep or not value:
            errors.append(f"attribute without a value: {pair}")
            continue
        if name not in ATTRS:
            errors.append(f"unknown attribute {name}")
        attrs[name] = value
    for name in sorted(REQUIRED):
        if name not in attrs:
            errors.append(f"missing {name}")
    if attrs.get("role") and attrs["role"] not in ROLES:
        errors.append(f"unknown role {attrs['role']}")
    for name in ("issue", "pr"):
        if name in attrs and not NUMBER.fullmatch(attrs[name]):
            errors.append(f"{name} is not a number: {attrs[name]}")
    if "problem" in attrs and not KEY.fullmatch(attrs["problem"]):
        errors.append(f"bad problem key: {attrs['problem']}")
    return errors


def trailer_errors(block):
    """Return the grammar violations of one commit trailer block."""
    errors = []
    for line in block.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "Change" and value not in CHANGES:
            errors.append(f"unknown Change value: {value}")
        elif key == "Why" and not value:
            errors.append("empty Why")
        elif key == "Fixes" and not re.fullmatch(r"https?://\S+", value):
            errors.append(f"Fixes is not a URL: {value}")
    return errors


# ---------------------------------------------------------------- the checker checks itself
check(marker_errors("role=gate.rework issue=75 pr=9 problem=r1-2") == [], "a valid marker was rejected")
check(marker_errors("role=bogus issue=75") != [], "an unknown role was accepted")
check(marker_errors("role=plan") != [], "a marker with no issue was accepted")
check(marker_errors("role=plan issue=x") != [], "a non-numeric issue was accepted")
check(marker_errors("role=plan issue=1 color=red") != [], "an unknown attribute was accepted")
check(trailer_errors("Change: fix\nWhy: x\nFixes: https://e.test/1") == [], "a valid trailer block was rejected")
check(trailer_errors("Change: chore") != [], "an unknown Change value was accepted")
check(trailer_errors("Fixes: later") != [], "a Fixes value that is not a URL was accepted")

# ---------------------------------------------------------------- PM docs: every marker is valid
seen_roles = set()
for name, text in PM_DOCS.items():
    for found in MARKER.finditer(text):
        for error in marker_errors(found.group(1)):
            check(False, f"{name}: marker `{found.group(0)}`: {error}")
        role = re.search(r"role=(\S+)", found.group(1))
        if role:
            seen_roles.add(role.group(1))

REQUIRED_ROLES = {"plan", "cross-check", "plan-amendment", "gate.pass", "gate.rework", "sub-merge",
                  "checkpoint", "worker.launched", "status.repair"}
check(REQUIRED_ROLES <= ROLES, f"timeline.md dropped roles the PM writes: {REQUIRED_ROLES - ROLES}")
for role in sorted(REQUIRED_ROLES - seen_roles):
    check(False, f"SKILL.md and its references show no marker for role {role}")

# The visible prose prefixes stay, so older-style parsing keeps working.
all_pm = "\n".join(PM_DOCS.values())
for prefix in ("## Plan (PM)", "finding: batch cross-check", "PM gate: rework requested", "PM gate: pass",
               "Sub-merged into", "worker launched:", "checkpoint <k>:", "status repair:"):
    check(prefix in all_pm, f"the PM docs lost the prose prefix `{prefix}`")

# ---------------------------------------------------------------- worker: trailers with an example
check("Change:" in WORKER and "Why:" in WORKER and "Fixes:" in WORKER,
      "agents/issue-worker.md does not require the Change:, Why: and Fixes: trailers")
blocks = [textwrap.dedent(b) for b in re.findall(r"^[ ]*```[a-z]*\n(.*?)^[ ]*```", WORKER, re.S | re.M)]
examples = [b for b in blocks if re.search(r"^Change: ", b, re.M)]
check(len(examples) >= 1, "agents/issue-worker.md shows no commit message example with a Change: trailer")
for block in examples:
    paragraphs = block.strip().split("\n\n")
    check(len(paragraphs) >= 2, "the commit example has no trailer paragraph after the subject")
    trailers = paragraphs[-1]
    check(re.search(r"^Why: \S", trailers, re.M) is not None, "the commit example has no Why: trailer")
    for error in trailer_errors(trailers):
        check(False, f"agents/issue-worker.md commit example: {error}")
check(any(re.search(r"^Fixes: ", b, re.M) for b in examples), "no commit example shows a Fixes: trailer")

# ---------------------------------------------------------------- README: the fallback note
check(re.search(r"prose fallback", README, re.I) is not None,
      "README.md has no note that older runs classify through the prose fallback")

if failures:
    print(f"{len(failures)} failure(s):")
    for message in failures:
        print(f"  - {message}")
    sys.exit(1)
print("ok: event markers and trailers match the timeline.md grammar")
