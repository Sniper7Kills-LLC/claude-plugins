#!/usr/bin/env python3
"""Tests for issue-flow/skills/project-planner/scripts/render-spec.py.

    python3 scripts/test-render-spec.py

Runs the renderer the way the planner tells the user to run it — copied into a
docs/specs/ directory and executed as a script — against a throwaway spec package.
Three properties matter: spec.html carries the full markdown content in place (a
generated website, not a link farm), diagrams and mockups are embedded self-contained
(mermaid inlined when vendored, readable source when not, iframe plus fallback link
always), and a successful run stamps `html_generated`. The suite must pass offline.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(
    ROOT, "issue-flow", "skills", "project-planner", "scripts", "render-spec.py"
)

failures = []


def check(name, condition, detail=""):
    if condition:
        print("  ok: %s" % name)
    else:
        failures.append(name)
        print("  FAIL: %s%s" % (name, " — " + detail if detail else ""))


SPEC_MD = """---
name: Widget Tracker
slug: widget-tracker
status: draft
created: 2026-08-16
html_generated: null
branch_model: trunk
features:
  - id: intake
    file: features/01-intake.md
---

# Widget Tracker

## Overview

The tracker records every widget the shop receives.

## Architecture & stack

```mermaid
flowchart LR
  ui[Web UI] --> api[API] --> db[(Postgres)]
```

The API owns all database access.

## Assumptions

- The shop has one location.
"""

FEATURE_MD = """---
id: intake
feature: Widget Intake
epic: Epic 1
status: planned
issues: []
mockups: [mockups/01-intake.html]
---

## Purpose

An operator registers an arriving widget in under a minute.

## User flows

```mermaid
flowchart TD
  start[Scan widget] --> form[Intake form] --> save{Valid?}
  save -- yes --> done[Stored]
  save -- no --> form
```

1. The operator scans the widget barcode.
2. The form pre-fills the serial number.

## Behaviour rules

| Field | Rule |
| --- | --- |
| serial | unique, required |
| state | `pending | running | stored` |

- top rule
  - nested two-space rule
    - nested four-space child
- second top rule

A [strange link](x"onmouseover="alert1) survives escaping.
"""

MOCKUP_HTML = "<h1>Intake form mockup</h1>"


def make_spec_dir(with_mermaid_asset):
    root = tempfile.mkdtemp(prefix="render-spec-test-")
    spec = os.path.join(root, "docs", "specs")
    os.makedirs(os.path.join(spec, "features"))
    os.makedirs(os.path.join(spec, "mockups"))
    with open(os.path.join(spec, "spec.md"), "w") as f:
        f.write(SPEC_MD)
    with open(os.path.join(spec, "features", "01-intake.md"), "w") as f:
        f.write(FEATURE_MD)
    with open(os.path.join(spec, "mockups", "01-intake.html"), "w") as f:
        f.write(MOCKUP_HTML)
    if with_mermaid_asset:
        os.makedirs(os.path.join(spec, "assets"))
        with open(os.path.join(spec, "assets", "mermaid.min.js"), "w") as f:
            f.write("window.mermaid={initialize:function(){}};/*VENDORED-SENTINEL*/")
    shutil.copy(RENDERER, os.path.join(spec, "render-spec.py"))
    return root, spec


def render(spec):
    return subprocess.run(
        [sys.executable, os.path.join(spec, "render-spec.py")],
        capture_output=True,
        text=True,
    )


def main():
    print("with vendored mermaid asset:")
    root, spec = make_spec_dir(with_mermaid_asset=True)
    result = render(spec)
    check("exits 0", result.returncode == 0, result.stderr)
    html_path = os.path.join(spec, "spec.html")
    check("writes spec.html", os.path.exists(html_path))
    page = open(html_path).read() if os.path.exists(html_path) else ""

    check(
        "spec.md content rendered in place",
        "records every widget the shop receives" in page,
    )
    check(
        "feature content rendered in place, not linked",
        "registers an arriving widget in under a minute" in page,
    )
    check(
        "numbered steps rendered",
        "pre-fills the serial number" in page,
    )
    check("table rendered", "<th>Field</th>" in page and "<td>serial</td>" in page)
    check(
        "pipe inside code span stays one cell",
        "<td><code>pending | running | stored</code></td>" in page,
    )
    check(
        "nested list opens inside the parent <li>",
        "<li>top rule\n<ul>" in page,
    )
    check(
        "no <ul> directly inside <ul>",
        "<ul>\n<ul>" not in page and "<ul><ul>" not in page,
    )
    check(
        "4-space indent nests one level, not two",
        # toc + spec.md assumptions + feature outer + exactly 2 nested levels;
        # a fabricated empty intermediate list would make it 6
        page.count("<ul>") == 5,
        "found %d" % page.count("<ul>"),
    )
    check(
        "href quotes escaped",
        'onmouseover="alert1' not in page and "onmouseover" in page,
    )
    check(
        "both mermaid diagrams present",
        page.count('<pre class="mermaid">') == 2,
        "found %d" % page.count('<pre class="mermaid">'),
    )
    check("mermaid asset inlined", "VENDORED-SENTINEL" in page)
    check("mermaid initialized", "mermaid.initialize" in page)
    check(
        "mockup embedded both ways",
        'iframe src="mockups/01-intake.html"' in page
        and 'target="_blank">Open mockups/01-intake.html' in page,
    )
    check("assumptions called out", 'class="callout-h"' in page)
    check(
        "no external script/link requests",
        "<script src=" not in page and "<link " not in page,
    )
    check(
        "html_generated stamped",
        re.search(
            r"^html_generated: \d{4}-\d{2}-\d{2}$",
            open(os.path.join(spec, "spec.md")).read(),
            re.M,
        )
        is not None,
    )
    shutil.rmtree(root)

    print("without vendored mermaid asset:")
    root, spec = make_spec_dir(with_mermaid_asset=False)
    result = render(spec)
    check("exits 0", result.returncode == 0, result.stderr)
    page = open(os.path.join(spec, "spec.html")).read()
    check(
        "diagrams degrade to readable source",
        '<pre class="mermaid">' in page and "classList.add('raw')" in page,
    )
    check("reports the missing asset", "not vendored" in result.stdout)
    shutil.rmtree(root)

    print("missing html_generated key:")
    root, spec = make_spec_dir(with_mermaid_asset=False)
    spec_md = os.path.join(spec, "spec.md")
    with open(spec_md) as f:
        stripped_front_matter = f.read().replace("html_generated: null\n", "")
    with open(spec_md, "w") as f:
        f.write(stripped_front_matter)
    result = render(spec)
    check("exits 0", result.returncode == 0, result.stderr)
    check(
        "inserts the key instead of silently skipping",
        re.search(r"^html_generated: \d{4}-\d{2}-\d{2}$", open(spec_md).read(), re.M)
        is not None,
    )
    check("warns about the insertion", "was missing" in result.stderr)
    shutil.rmtree(root)

    print("missing spec.md:")
    root = tempfile.mkdtemp(prefix="render-spec-test-")
    shutil.copy(RENDERER, os.path.join(root, "render-spec.py"))
    result = subprocess.run(
        [sys.executable, os.path.join(root, "render-spec.py")],
        capture_output=True,
        text=True,
    )
    check("fails with a message", result.returncode == 1 and "no spec.md" in result.stderr)
    shutil.rmtree(root)

    print()
    if failures:
        print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("all render-spec checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
