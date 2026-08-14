#!/usr/bin/env python3
"""Validate every plugin in this marketplace.

Run it locally before you push, and let CI run it on every pull request:

    python3 scripts/validate-manifests.py

The checks exist because each failure below is silent at runtime. A skill whose
frontmatter does not parse loads with no name and no description, so Claude never
triggers it. A marketplace entry whose version disagrees with plugin.json blocks the
release tag.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+].+)?$")

errors = []
warnings = []


def error(message):
    errors.append(message)


def warn(message):
    warnings.append(message)


def load_json(path):
    """Return the parsed file, or None when it does not parse."""
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        error(f"{os.path.relpath(path, ROOT)}: file is missing")
    except json.JSONDecodeError as exc:
        error(f"{os.path.relpath(path, ROOT)}: JSON does not parse — {exc}")
    return None


def frontmatter(path):
    """Return the YAML frontmatter block of a markdown file, or None."""
    with open(path) as handle:
        text = handle.read()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return match.group(1) if match else None


def check_frontmatter(path, kind):
    """A skill or agent with unparseable frontmatter loads with empty metadata."""
    try:
        import yaml
    except ImportError:
        error(
            "PyYAML is not installed, so frontmatter cannot be checked — "
            "`pip install pyyaml` (CI installs it for you)"
        )
        return

    rel = os.path.relpath(path, ROOT)
    block = frontmatter(path)
    if block is None:
        error(f"{rel}: {kind} has no YAML frontmatter")
        return
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        first = str(exc).split("\n")[0]
        error(f"{rel}: frontmatter does not parse — {first}")
        return
    if not isinstance(data, dict):
        error(f"{rel}: frontmatter is not a mapping")
        return
    for field in ("name", "description"):
        if not data.get(field):
            error(f"{rel}: {kind} frontmatter is missing '{field}'")


def check_links(path):
    """A relative link that points at a missing file is dead documentation."""
    rel = os.path.relpath(path, ROOT)
    with open(path) as handle:
        text = handle.read()
    for match in re.finditer(r"\]\(([^)#\s]+\.md)\)", text):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
        if not os.path.exists(resolved):
            error(f"{rel}: link points at a missing file — {target}")


SPAWN_LINT_SUPPRESS = "<!-- spawn-lint: ok -->"

# A paragraph that names one of these is unambiguously describing an `Agent` spawn.
AGENT_MARKERS = ("agentType", "subagent_type", "Agent(")
# The same thing said in prose — "spawn the agent type `x`", "spawn a subagent".
AGENT_PROSE = re.compile(r"\b(with\s+`Agent`|spawn\w*\s+(the\s+|a\s+|an\s+)?(agent|subagent))", re.I)
# ...and one that names one of these is describing a `Bash` call, where
# `run_in_background` is correct and must not be flagged.
BASH_MARKERS = ("`Bash`", "shell loop", "blocking call", "`sleep`")
# The parameter, not the English word: "no isolation needed here" must not read as
# a paragraph that passes `isolation:`.
ISOLATION_PARAM = re.compile(r"`isolation[:`]|\bisolation:\s*[\"']")
# Clauses end at sentence punctuation followed by space. Version numbers (2.1.232)
# and `key: value` code spans survive that, which is why it is not a bare period.
CLAUSE_BREAK = re.compile(r"(?<=[.;!?])\s")


def clause_around(text, position):
    """The sentence-ish span containing `position` — the unit a parameter belongs to."""
    start = 0
    end = len(text)
    for match in CLAUSE_BREAK.finditer(text):
        if match.end() <= position:
            start = match.end()
        else:
            end = match.start()
            break
    return text[start:end]


def paragraphs(text):
    """Yield (first_line_number, paragraph) for each blank-line-separated block.

    Line numbers are counted, not derived from block lengths — a run of blank
    lines or a trailing space would otherwise drift the count, and a lint that
    reports the wrong line is a lint people learn to ignore.
    """
    block, start = [], 1
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            if not block:
                start = number
            block.append(line)
        elif block:
            yield start, "\n".join(block)
            block = []
    if block:
        yield start, "\n".join(block)


def spawn_lint(text):
    """Return [(line, message)] for documented `Agent` spawns that would break.

    Two failures, both measured against Claude Code 2.1.232 (issue #25):

    1. `name:` without `isolation:` does not produce a background subagent. It
       produces a peer session with no completion notification, invisible to
       `ListAgents`, whose plain-text answer reaches nobody. A PM that follows
       such a spec waits for a result that will never arrive.
    2. `run_in_background` on an `Agent` call is inert — present in the tool
       schema on some sessions, absent on others, and ineffective either way.
       On a `Bash` call the same parameter is correct and stays.

    Put `<!-- spawn-lint: ok -->` in a paragraph that discusses these shapes
    without prescribing them (the rule itself has to be written down somewhere).

    Rule 1 reads a **two-paragraph window**, not one paragraph. A spec that says
    "spawn `Agent` with `agentType: …`" and then names the agent in the next
    paragraph is the same instruction to a reader and the same trap to a PM, and a
    copyedit that reflows one paragraph into two must not turn CI green.
    """
    found = {}
    blocks = list(paragraphs(text))
    # Windows of one and two adjacent paragraphs. Two is deliberate: it catches a
    # reflowed spec while keeping the blast radius small, where a whole-section
    # window would pair an `agentType` mention with an unrelated `name:` far below.
    windows = [(i, i) for i in range(len(blocks))] + [
        (i, i + 1) for i in range(len(blocks) - 1)
    ]

    for first, last in windows:
        parts = blocks[first : last + 1]
        block = "\n\n".join(part for _, part in parts)
        if SPAWN_LINT_SUPPRESS in block:
            continue
        agent_site = any(marker in block for marker in AGENT_MARKERS) or bool(
            AGENT_PROSE.search(block)
        )

        def at(match, parts=parts):
            """Map an offset in the joined window back to a real file line.

            Each paragraph carries its own true start line, so the answer stays
            right however many blank lines separated them in the source — the
            joined text is not the file, and pretending otherwise drifts the
            number by one per extra blank line.
            """
            offset = match.start()
            for start_line, part in parts:
                if offset <= len(part):
                    return start_line + part[:offset].count("\n")
                offset -= len(part) + 2  # the "\n\n" this join inserted
            return parts[-1][0]

        # The parameter, backticked or bare: `name: "x"` and name: "x" are the same
        # instruction. Not a bare `name` — that word appears everywhere in prose.
        name_match = re.search(r"`name:|\bname:\s*[\"']", block)
        # Match the parameter, not the word. "no isolation needed for a read-only
        # helper" must still be caught — a writer who justifies omitting it is the
        # writer most likely to be wrong — and an incidental mention must not
        # silence the paragraph.
        if agent_site and name_match and not ISOLATION_PARAM.search(block):
            found[(at(name_match), "name")] = (
                at(name_match), "spawn spec passes `name:` with no `isolation:` — that "
                                "is a peer session with no completion notification "
                                "(issue #25)"
            )
        # Decide this one on the clause the parameter sits in, not the paragraph.
        # A paragraph may legitimately say "launch the poller with
        # `run_in_background: true` … then spawn a subagent to read it": the
        # parameter belongs to the Bash call there, and flagging it would block CI
        # over a correct line. Silence beats a false error here — an un-flagged
        # inert parameter is untidy, a blocked build over a right one is not.
        background_match = re.search(r"run_in_background", block)
        if background_match:
            clause = clause_around(block, background_match.start())
            agent_clause = (
                any(marker in clause for marker in AGENT_MARKERS)
                or bool(AGENT_PROSE.search(clause))
                or bool(ISOLATION_PARAM.search(clause))
            )
            if agent_clause and not any(marker in clause for marker in BASH_MARKERS):
                found[(at(background_match), "background")] = (
                    at(background_match), "`run_in_background` on an `Agent` spawn is "
                                          "inert — drop it (it stays on `Bash` calls)"
                )
    # Windows overlap, so the same line can be reached twice; report it once,
    # in file order.
    return [found[key] for key in sorted(found)]


def check_spawn_specs(path):
    """Documented spawn shapes that strand the PM are worse than no docs."""
    rel = os.path.relpath(path, ROOT)
    with open(path) as handle:
        text = handle.read()
    # Blank the frontmatter rather than removing it, so line numbers stay true.
    match = re.match(r"^---\n.*?\n---\n", text, re.S)
    if match:
        text = "\n" * match.group(0).count("\n") + text[match.end():]
    for line, message in spawn_lint(text):
        error(f"{rel}:{line}: {message}")


def main():
    marketplace_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    marketplace = load_json(marketplace_path)
    if marketplace is None:
        report()
        return

    entries = marketplace.get("plugins", [])
    if not entries:
        error(".claude-plugin/marketplace.json: lists no plugins")

    for entry in entries:
        name = entry.get("name", "<unnamed>")
        source = entry.get("source")
        if not source:
            error(f"marketplace entry '{name}': has no source")
            continue

        plugin_dir = os.path.normpath(os.path.join(ROOT, source))
        if not os.path.isdir(plugin_dir):
            error(f"marketplace entry '{name}': source directory is missing — {source}")
            continue

        manifest = load_json(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"))
        if manifest is None:
            continue

        # The release tag is built from these two versions, so they must agree.
        entry_version = entry.get("version")
        manifest_version = manifest.get("version")
        if not manifest_version:
            error(f"{name}: plugin.json has no version")
        elif not SEMVER.match(str(manifest_version)):
            error(f"{name}: plugin.json version is not semver — {manifest_version}")
        if entry_version and manifest_version and entry_version != manifest_version:
            error(
                f"{name}: marketplace entry says {entry_version}, "
                f"plugin.json says {manifest_version}"
            )
        if not entry_version:
            warn(f"{name}: marketplace entry has no version, so releases cannot be tagged")

        if manifest.get("name") != name:
            error(
                f"{name}: plugin.json name is '{manifest.get('name')}', "
                f"marketplace entry name is '{name}'"
            )

        for skill in sorted(glob_dir(plugin_dir, "skills", "SKILL.md")):
            check_frontmatter(skill, "skill")
        for agent in sorted(glob_dir(plugin_dir, "agents", None)):
            check_frontmatter(agent, "agent")

        for markdown in walk_markdown(plugin_dir):
            check_links(markdown)
            check_spawn_specs(markdown)

    for markdown in walk_markdown(ROOT, top_only=True):
        check_links(markdown)

    report()


def glob_dir(plugin_dir, subdir, filename):
    """Return skill or agent markdown files under one plugin directory."""
    base = os.path.join(plugin_dir, subdir)
    found = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if filename and name != filename:
                continue
            if not filename and not name.endswith(".md"):
                continue
            found.append(os.path.join(dirpath, name))
    return found


def walk_markdown(base, top_only=False):
    """Return markdown files under a directory, skipping .git."""
    if top_only:
        return [
            os.path.join(base, name)
            for name in os.listdir(base)
            if name.endswith(".md")
        ]
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        found.extend(
            os.path.join(dirpath, name) for name in filenames if name.endswith(".md")
        )
    return found


def report():
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors:
        print(f"\n{len(errors)} error(s)")
        sys.exit(1)
    print(f"validation passed ({len(warnings)} warning(s))")


if __name__ == "__main__":
    main()
