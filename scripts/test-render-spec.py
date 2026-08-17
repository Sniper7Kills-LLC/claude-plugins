#!/usr/bin/env python3
"""Tests for issue-flow/skills/project-planner/scripts/render-spec.py.

    python3 scripts/test-render-spec.py

Runs the renderer the way the planner tells the user to run it — copied into a
docs/specs/ directory and executed as a script — against a throwaway spec package.
The properties that matter: the render is a multi-page site (spec.html index plus one
html/ page per feature and per `pages:` entry, full content in place, never a link
farm), relative markdown links rewrite so they resolve from every generated page,
diagrams and mockups stay self-contained as a folder (mermaid referenced relatively
when vendored, readable source when not, iframe plus fallback link always), the
review-comments round-trip embeds and tolerates, stale html/ pages are deleted, and a
successful run stamps `html_generated`. The suite must pass offline.
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
pages:
  - id: plan
    file: pages/plan.md
---

# Widget Tracker

## Overview

The tracker records every widget the shop receives. Details in
[the intake feature](features/01-intake.md) and [the plan](pages/plan.md).

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

An operator registers an arriving widget in under a minute. See
[the index](../spec.md) and [the form mockup](mockups/01-intake.html).

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

PAGE_MD = """---
id: plan
title: Implementation plan
---

## Ordering

Intake ships first — see [the intake feature](../features/01-intake.md).
"""

MOCKUP_HTML = "<h1>Intake form mockup</h1>"

REVIEW_MD = """# Review comments

<!-- exported header -->

## [index] overview
status: open
date: 2026-08-16
Say which shop.

## [feature-intake] intake-purpose
status: resolved
date: 2026-08-16
Answered.

## Malformed heading block
must survive as an index comment
"""


def make_spec_dir(with_mermaid_asset):
    root = tempfile.mkdtemp(prefix="render-spec-test-")
    spec = os.path.join(root, "docs", "specs")
    os.makedirs(os.path.join(spec, "features"))
    os.makedirs(os.path.join(spec, "pages"))
    os.makedirs(os.path.join(spec, "mockups"))
    with open(os.path.join(spec, "spec.md"), "w") as f:
        f.write(SPEC_MD)
    with open(os.path.join(spec, "features", "01-intake.md"), "w") as f:
        f.write(FEATURE_MD)
    with open(os.path.join(spec, "pages", "plan.md"), "w") as f:
        f.write(PAGE_MD)
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


def read(path):
    return open(path).read() if os.path.exists(path) else ""


def main():
    print("with vendored mermaid asset:")
    root, spec = make_spec_dir(with_mermaid_asset=True)
    with open(os.path.join(spec, "review-comments.md"), "w") as f:
        f.write(REVIEW_MD)
    os.makedirs(os.path.join(spec, "html"))
    with open(os.path.join(spec, "html", "feature-old.html"), "w") as f:
        f.write("stale")
    result = render(spec)
    check("exits 0", result.returncode == 0, result.stderr)
    index = read(os.path.join(spec, "spec.html"))
    feature = read(os.path.join(spec, "html", "feature-intake.html"))
    page = read(os.path.join(spec, "html", "page-plan.html"))
    check("writes spec.html", index != "")
    check("writes html/feature-intake.html", feature != "")
    check("writes html/page-plan.html", page != "")
    check(
        "stale html/ page removed",
        not os.path.exists(os.path.join(spec, "html", "feature-old.html"))
        and "stale" in result.stderr,
    )

    check(
        "spec.md content rendered in place on the index",
        "records every widget the shop receives" in index,
    )
    check(
        "feature content rendered in full on its page",
        "registers an arriving widget in under a minute" in feature,
    )
    check(
        "index links to the feature page, not the markdown",
        'href="html/feature-intake.html"' in index
        and 'href="features/01-intake.md"' not in index,
    )
    check(
        "index links to the supplement page",
        'href="html/page-plan.html"' in index,
    )
    check(
        "supplement page rendered with its title and content",
        "Implementation plan" in page and "Intake ships first" in page,
    )
    check(
        "page link to a feature rewrites to the generated page",
        'href="feature-intake.html"' in page,
    )
    check(
        "feature link back to spec.md rewrites to the index",
        'href="../spec.html"' in feature,
    )
    check(
        "feature body link to a mockup re-bases from html/",
        'href="../mockups/01-intake.html"' in feature,
    )
    check(
        "index sidebar lists spec.md's own sections",
        'class="sub" href="#overview"' in index,
    )
    check(
        "pager threads index -> page -> feature",
        'href="../spec.html"' in page
        and 'href="feature-intake.html"' in page
        and 'href="page-plan.html"' in feature,
    )

    body = feature.split("</aside>")[-1]
    check(
        "numbered steps rendered",
        "pre-fills the serial number" in body,
    )
    check("table rendered", "<th>Field</th>" in body and "<td>serial</td>" in body)
    check(
        "pipe inside code span stays one cell",
        "<td><code>pending | running | stored</code></td>" in body,
    )
    check(
        "nested list opens inside the parent <li>",
        "<li>top rule\n<ul>" in body,
    )
    check(
        "no <ul> directly inside <ul>",
        "<ul>\n<ul>" not in body and "<ul><ul>" not in body,
    )
    check(
        "4-space indent nests one level, not two",
        # outer list + exactly 2 nested levels; a fabricated empty
        # intermediate list would make it 4
        body.count("<ul>") == 3,
        "found %d" % body.count("<ul>"),
    )
    check(
        "href quotes escaped",
        'onmouseover="alert1' not in body and "onmouseover" in body,
    )
    check(
        "one diagram per page",
        index.count('<pre class="mermaid">') == 1
        and feature.count('<pre class="mermaid">') == 1,
        "index %d feature %d"
        % (index.count('<pre class="mermaid">'), feature.count('<pre class="mermaid">')),
    )
    check(
        "mermaid referenced relatively, not inlined",
        '<script src="assets/mermaid.min.js">' in index
        and '<script src="../assets/mermaid.min.js">' in feature
        and "VENDORED-SENTINEL" not in index,
    )
    check(
        "mermaid initialized",
        "mermaid.initialize" in index and "mermaid.initialize" in feature,
    )
    check(
        "mockup embedded both ways with a re-based path",
        'iframe src="../mockups/01-intake.html"' in feature
        and 'target="_blank">Open ../mockups/01-intake.html' in feature,
    )
    check("assumptions called out", 'class="callout-h"' in index)
    check(
        "no external requests",
        'src="http' not in index and "<link " not in index
        and 'src="http' not in feature and "<link " not in feature,
    )
    check(
        "review layer present with a spec-keyed store",
        'id="rv-embedded"' in index and 'data-rvkey="widget-tracker"' in index,
    )
    check(
        "review comments embedded on every page",
        '"section": "overview"' in index and '"section": "overview"' in feature,
    )
    check(
        "malformed review block kept as an index comment",
        "must survive as an index comment" in index
        and '"page": "index"' in index,
    )
    check(
        "resolved status round-trips",
        '"status": "resolved"' in index,
    )
    check(
        "html_generated stamped",
        re.search(
            r"^html_generated: \d{4}-\d{2}-\d{2}$",
            read(os.path.join(spec, "spec.md")),
            re.M,
        )
        is not None,
    )
    shutil.rmtree(root)

    print("without vendored mermaid asset:")
    root, spec = make_spec_dir(with_mermaid_asset=False)
    result = render(spec)
    check("exits 0", result.returncode == 0, result.stderr)
    index = read(os.path.join(spec, "spec.html"))
    feature = read(os.path.join(spec, "html", "feature-intake.html"))
    check(
        "diagrams degrade to readable source",
        '<pre class="mermaid">' in index and "classList.add('raw')" in index
        and "classList.add('raw')" in feature,
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
        re.search(r"^html_generated: \d{4}-\d{2}-\d{2}$", read(spec_md), re.M)
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
