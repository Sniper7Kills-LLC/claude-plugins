#!/usr/bin/env python3
"""Check that a split of the issue-flow skill keeps every rule of the 0.20.0 SKILL.md.

    python3 scripts/rule-coverage.py extract [--baseline-ref REF] [--baseline-file PATH]
                                             [--out PATH]
    python3 scripts/rule-coverage.py check [--skill-dir DIR] [--inventory PATH]
                                           [--judge] [--claude-bin BIN]

`extract` reads the baseline SKILL.md and writes the rule inventory,
evals/rule-inventory.json. A rule is a sentence with must, never, always, only,
do not or don't, or one whole item of the Core invariants or the Hard rules. The
heading the rule sits under gives its phase: 0, A0, A, B, C, C1, C2, D, E, or
global. A new run keeps the `aliases` that a reviewer recorded for an id.

`check` reads a candidate skill directory. A rule passes when its text, or one of
its aliases, is in the dispatcher SKILL.md, or in a file that the dispatcher links
in a section of the rule's phase. A C1 or C2 rule may also sit in a file the
Stage C section links. A global rule must be in the dispatcher itself. The match
ignores case, whitespace, list markers, emphasis, backticks and link targets.

`--judge` asks `claude -p --model haiku` whether the text of the phase still
states each failed rule in other words. The report shows the answer. The answer
never changes the exit code, because a model's opinion is not a match.

Exit codes: 0 when every rule passes, 1 when a rule is missing or unreachable,
2 on a usage or input error.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REF = "issue-flow--v0.20.0"
DEFAULT_PATH = "issue-flow/skills/issue-flow/SKILL.md"
DEFAULT_INVENTORY = os.path.join(ROOT, "evals", "rule-inventory.json")
DEFAULT_SKILL_DIR = os.path.join(ROOT, "issue-flow", "skills", "issue-flow")

# A C1 or C2 rule is reachable from the Stage C section too, because that
# section is the one the reader is in when either gate applies.
PARENT_PHASE = {"C1": "C", "C2": "C"}
WHOLE_ITEM_SECTIONS = ("core invariants", "hard rules")

KEYWORD = re.compile(r"\b(must|never|always|only|do not|don't)\b", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
FENCE = re.compile(r"^\s*(```|~~~)")
RULE_LINE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*`\[(\"“])")
LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
MIN_WORDS = 3


def phase_of(title):
    """Return the phase a heading names, or None when it names no phase."""
    title = title.strip().strip("*").strip()
    match = re.match(r"(?:Phase\s+0\b)", title)
    if match:
        return "0"
    match = re.match(r"Stage\s+(A0|[A-E])\b", title)
    if match:
        return match.group(1)
    match = re.match(r"(C[12])\b", title)
    if match:
        return match.group(1)
    return None


def normalize(text):
    """Return the form of `text` that the match compares: plain, lowercase words."""
    text = LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"[*`]", "", text)
    text = text.replace("|", " ")
    text = text.replace("’", "'")
    return " ".join(text.split()).lower()


def strip_line(line):
    """Return a line without its list, quote or heading marker."""
    line = LIST_ITEM.sub("", line, count=1)
    line = re.sub(r"^\s*>\s?", "", line)
    return line.strip()


def walk(text):
    """Yield (line number, phase, section title, line) for each prose line.

    Frontmatter and fenced code are skipped. Headings update the phase and are
    not yielded.
    """
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                start = index + 1
                break
    stack = []  # (level, phase, title)
    in_fence = False
    for index in range(start, len(lines)):
        line = lines[index]
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else "global"
            stack.append((level, phase_of(title) or parent, title))
            continue
        phase = stack[-1][1] if stack else "global"
        section = stack[-1][2] if stack else ""
        yield index + 1, phase, section, line


def blocks(text):
    """Yield (first line number, phase, section, joined text) for each prose block.

    A block is a paragraph, a list item with its continuation lines, or a table
    row. A blank line, a heading, a fence or a horizontal rule ends a block.
    """
    current = None
    last_line = None
    for number, phase, section, line in walk(text):
        boundary = (last_line is not None and number != last_line + 1)
        last_line = number
        if boundary and current:
            yield current
            current = None
        if not line.strip() or RULE_LINE.match(line):
            if current:
                yield current
            current = None
            continue
        starts_new = LIST_ITEM.match(line) or line.lstrip().startswith("|")
        if current and starts_new:
            yield current
            current = None
        piece = strip_line(line)
        if current is None:
            current = [number, phase, section, piece]
        else:
            current[3] = current[3] + " " + piece
    if current:
        yield current


def rule_id(phase, text):
    digest = hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:8]
    return "rule-%s-%s" % (phase, digest)


def extract_rules(text):
    """Return the rule inventory entries of a baseline SKILL.md text."""
    rules = []
    seen = set()
    for number, phase, section, block in blocks(text):
        block = " ".join(block.split())
        if section.strip().lower() in WHOLE_ITEM_SECTIONS:
            candidates = [block]
        else:
            candidates = [s for s in SENTENCE_END.split(block) if KEYWORD.search(s)]
        for sentence in candidates:
            sentence = sentence.strip()
            if len(sentence.split()) < MIN_WORDS:
                continue
            key = (phase, normalize(sentence))
            if key in seen:
                continue
            seen.add(key)
            rules.append({
                "id": rule_id(phase, sentence),
                "text": sentence,
                "phase": phase,
                "source_line": source_line(text, number, sentence),
            })
    return rules


def source_line(text, block_line, sentence):
    """Return the line where `sentence` starts, searching from its block's first line."""
    lines = text.splitlines()
    first_words = normalize(sentence).split()[:3]
    probe = " ".join(first_words)
    for index in range(block_line - 1, len(lines)):
        if probe in normalize(strip_line(lines[index])):
            return index + 1
    return block_line


def sections_links(dispatcher_path):
    """Return {phase: [resolved paths]} of the markdown links in each phase's sections."""
    with open(dispatcher_path) as handle:
        text = handle.read()
    base = os.path.dirname(dispatcher_path)
    links = {}
    for _, phase, _, line in walk(text):
        for match in LINK.finditer(line):
            target = match.group(2).split("#", 1)[0]
            if not target or re.match(r"[a-z]+:", target):
                continue
            path = os.path.normpath(os.path.join(base, target))
            if os.path.isfile(path):
                links.setdefault(phase, [])
                if path not in links[phase]:
                    links[phase].append(path)
    return links


def read_normalized(path):
    with open(path) as handle:
        return normalize(" ".join(strip_line(l) for l in handle.read().splitlines()))


def rule_keys(rule):
    return [normalize(t) for t in [rule["text"]] + list(rule.get("aliases") or [])]


def judge_rule(rule, text, claude_bin):
    """Return the judge's one-line answer on whether `text` still states the rule."""
    prompt = (
        "The text on standard input is what an agent reads during phase %s of a "
        "skill. Does that text state the rule below, with the same meaning, even "
        "in other words? Answer YES or NO on the first line. Give one sentence of "
        "reason on the second line.\n\nRule: %s" % (rule["phase"], rule["text"]))
    try:
        result = subprocess.run(
            # The same isolation as the judge of scripts/run-evals.py: no host plugin,
            # user setting or MCP server reaches the answer.
            [claude_bin, "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
             "--settings", json.dumps({"enabledPlugins": {"issue-flow@sniper7kills": False}}),
             "--setting-sources", "project,local", "--strict-mcp-config", prompt],
            input=text, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "error %s" % error
    if result.returncode != 0:
        return "error exit %d: %s" % (result.returncode, result.stderr.strip()[:200])
    return " — ".join(l.strip() for l in result.stdout.strip().splitlines()[:2])


def run_check(inventory, skill_dir, judge=False, claude_bin="claude"):
    """Print a coverage report for `skill_dir` and return the exit code."""
    dispatcher = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(dispatcher):
        print("rule-coverage: no SKILL.md in %s" % skill_dir)
        return 2
    links = sections_links(dispatcher)
    cache = {}

    def text_of(path):
        if path not in cache:
            cache[path] = read_normalized(path)
        return cache[path]

    others = set(p for paths in links.values() for p in paths)
    for folder, _, names in os.walk(skill_dir):
        others.update(os.path.join(folder, n) for n in names if n.endswith(".md"))
    others.discard(dispatcher)

    rules = inventory["rules"]
    problems = 0
    for rule in rules:
        keys = rule_keys(rule)
        phase = rule["phase"]
        reachable = [dispatcher]
        if phase != "global":
            reachable += links.get(phase, [])
            reachable += links.get(PARENT_PHASE.get(phase), [])
        if any(k in text_of(p) for p in reachable for k in keys):
            continue
        problems += 1
        found = sorted(os.path.relpath(p, skill_dir) for p in others
                       if p not in reachable and any(k in text_of(p) for k in keys))
        where = "phase %s" % phase
        if found:
            line = "UNREACHABLE  %s  [%s]  in %s, which the dispatcher does not link at %s" % (
                rule["id"], where, ", ".join(found),
                "any phase section" if phase == "global" else "this phase")
            if phase == "global":
                line += " (a global rule must be in the dispatcher)"
        else:
            line = "MISSING  %s  [%s]" % (rule["id"], where)
        print(line)
        print("    %s" % rule["text"])
        if judge:
            raw = []
            for path in reachable:
                with open(path) as handle:
                    raw.append(handle.read())
            print("    judge: %s" % judge_rule(rule, "\n\n".join(raw), claude_bin))
    print("rule-coverage: %d rules, %d passed, %d failed" % (
        len(rules), len(rules) - problems, problems))
    return 1 if problems else 0


def load_baseline(args):
    if args.baseline_file:
        with open(args.baseline_file) as handle:
            return handle.read(), args.baseline_file
    result = subprocess.run(["git", "show", "%s:%s" % (args.baseline_ref, DEFAULT_PATH)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit("rule-coverage: cannot read the baseline: %s" % result.stderr.strip())
    return result.stdout, "%s:%s" % (args.baseline_ref, DEFAULT_PATH)


def command_extract(args):
    text, label = load_baseline(args)
    rules = extract_rules(text)
    if os.path.isfile(args.out):
        with open(args.out) as handle:
            previous = {r["id"]: r for r in json.load(handle).get("rules", [])}
        for rule in rules:
            aliases = previous.get(rule["id"], {}).get("aliases")
            if aliases:
                rule["aliases"] = aliases
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump({"baseline": label, "rules": rules}, handle, indent=2,
                  ensure_ascii=False)
        handle.write("\n")
    print("rule-coverage: wrote %d rules to %s" % (len(rules), args.out))
    return 0


def command_check(args):
    try:
        with open(args.inventory) as handle:
            inventory = json.load(handle)
    except (OSError, ValueError) as error:
        print("rule-coverage: cannot read the inventory: %s" % error)
        return 2
    return run_check(inventory, args.skill_dir, judge=args.judge,
                     claude_bin=args.claude_bin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="write the rule inventory from the baseline")
    extract.add_argument("--baseline-ref", default=DEFAULT_REF)
    extract.add_argument("--baseline-file", help="read this file instead of the git ref")
    extract.add_argument("--out", default=DEFAULT_INVENTORY)
    check = sub.add_parser("check", help="check a skill tree against the inventory")
    check.add_argument("--skill-dir", default=DEFAULT_SKILL_DIR)
    check.add_argument("--inventory", default=DEFAULT_INVENTORY)
    check.add_argument("--judge", action="store_true",
                       help="ask claude -p on haiku about each failed rule")
    check.add_argument("--claude-bin", default="claude")
    args = parser.parse_args(argv)
    if args.command == "extract":
        return command_extract(args)
    return command_check(args)


if __name__ == "__main__":
    sys.exit(main())
