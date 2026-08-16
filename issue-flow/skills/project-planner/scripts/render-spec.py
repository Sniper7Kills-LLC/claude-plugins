#!/usr/bin/env python3
"""Render the spec package into a single browsable spec.html.

    python3 docs/specs/render-spec.py

The markdown package is the spec; this script is the website generator. It reads
spec.md and every feature file it lists, converts them with a small built-in
markdown renderer, and writes spec.html next to itself: the whole spec top to
bottom — never a summary that links out to the markdown.

Properties the output keeps:

- Self-contained. Inline CSS and JS only; no external requests. Mermaid diagrams
  render through assets/mermaid.min.js when that file exists (the planner vendors
  it once); without it each diagram degrades to its readable source.
- Complete. Every section of spec.md and every features/NN-*.md appears in full,
  in front-matter order. Each feature's mockups embed both ways: an <iframe> and
  an "Open in new tab" link (local iframes can silently render blank).
- Honest about freshness. On success the script stamps `html_generated` in
  spec.md's front-matter.

Standard library only — the copied script must run in any project.
"""

import datetime
import html
import os
import re
import sys

SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
MERMAID_ASSET = os.path.join(SPEC_DIR, "assets", "mermaid.min.js")
CALLOUT_HEADINGS = {"assumptions", "risks & open questions", "open questions"}


# --- front-matter -----------------------------------------------------------


def split_front_matter(text):
    """Return (dict, body). Minimal parser: `key: value` lines and one level of
    `- key: value` list items under a `key:` line. No yaml dependency."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw, body = text[3:end], text[end + 4 :]
    meta, current_list = {}, None
    for line in raw.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        item = re.match(r"\s+-\s*(.*)$", stripped)
        if item and current_list is not None:
            entry = item.group(1)
            kv = re.match(r"(\w[\w-]*):\s*(.*)$", entry)
            meta[current_list].append({kv.group(1): kv.group(2)} if kv else entry)
            continue
        cont = re.match(r"\s+(\w[\w-]*):\s*(.*)$", stripped)
        if cont and current_list is not None and meta[current_list]:
            last = meta[current_list][-1]
            if isinstance(last, dict):
                last[cont.group(1)] = cont.group(2)
            continue
        kv = re.match(r"(\w[\w-]*):\s*(.*)$", stripped)
        if kv:
            key, value = kv.group(1), kv.group(2)
            if value in ("", "[]"):
                meta[key] = []
                # only a bare `key:` opens a block list; `[]` is a closed empty list
                current_list = key if value == "" else None
            else:
                meta[key] = value.strip("\"'")
                current_list = None
    return meta, body


# --- markdown ---------------------------------------------------------------


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)),
        text,
    )
    return text


def split_cells(row):
    """Split a table row on `|`, ignoring pipes inside backtick code spans —
    `pending | running` in a cell is one cell, not two."""
    cells, current, in_code = [], [], False
    for ch in row.strip().strip("|"):
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    return cells


def md_to_html(body, demote=0, slug_prefix=""):
    """Convert the supported markdown subset. `demote` shifts heading levels so
    feature files nest under the page's own hierarchy."""
    out, lines, i = [], body.splitlines(), 0
    list_stack = []  # open list tags, innermost last
    li_open = []  # parallel: is an <li> awaiting its </li> at that level

    def close_lists(to_depth=0):
        while len(list_stack) > to_depth:
            if li_open[-1]:
                out.append("</li>")
            out.append("</%s>" % list_stack.pop())
            li_open.pop()

    while i < len(lines):
        line = lines[i]
        fence = re.match(r"```(\w*)\s*$", line)
        if fence:
            close_lists()
            lang, block = fence.group(1), []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(block)
            if lang == "mermaid":
                out.append(
                    '<div class="diagram"><pre class="mermaid">%s</pre></div>'
                    % html.escape(code, quote=False)
                )
            else:
                out.append(
                    '<pre class="code"><code>%s</code></pre>'
                    % html.escape(code, quote=False)
                )
            continue
        heading = re.match(r"(#{1,6})\s+(.*)$", line)
        if heading:
            close_lists()
            level = min(6, len(heading.group(1)) + demote)
            text = heading.group(2).strip()
            slug = slug_prefix + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            css = ' class="callout-h"' if text.lower() in CALLOUT_HEADINGS else ""
            out.append('<h%d id="%s"%s>%s</h%d>' % (level, slug, css, inline(text), level))
            i += 1
            continue
        if re.match(r"\s*([-*]|\d+\.)\s+", line):
            while i < len(lines):
                item = re.match(r"(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not item:
                    break
                # 2- and 4-space indents both mean "one level deeper": raw depth is
                # clamped to one step below the current level, so indent width never
                # fabricates an empty intermediate list.
                depth = min(len(item.group(1).expandtabs(4)) // 2 + 1, len(list_stack) + 1)
                tag = "ol" if item.group(2).rstrip(".").isdigit() else "ul"
                if depth > len(list_stack):
                    # nest inside the still-open parent <li>
                    list_stack.append(tag)
                    li_open.append(False)
                    out.append("<%s>" % tag)
                else:
                    close_lists(depth)
                    if li_open[-1]:
                        out.append("</li>")
                        li_open[-1] = False
                out.append("<li>%s" % inline(item.group(3)))
                li_open[-1] = True
                i += 1
            close_lists()
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(split_cells(lines[i]))
                i += 1
            rows = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            if rows:
                out.append('<div class="table-wrap"><table>')
                out.append(
                    "<tr>%s</tr>" % "".join("<th>%s</th>" % inline(c) for c in rows[0])
                )
                for r in rows[1:]:
                    out.append(
                        "<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r)
                    )
                out.append("</table></div>")
            continue
        if re.fullmatch(r"(-{3,}|\*{3,})", line.strip()):
            close_lists()
            out.append("<hr>")
            i += 1
            continue
        if line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> "))
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(quote)))
            continue
        if not line.strip():
            close_lists()
            i += 1
            continue
        para = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"(#{1,6}\s|```|\||>|\s*([-*]|\d+\.)\s)", lines[i]
        ):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(para)))
    close_lists()
    return "\n".join(out)


# --- page -------------------------------------------------------------------

CSS = """
:root { --bg:#ffffff; --fg:#1a1a24; --muted:#5a5a68; --line:#e3e3ea; --accent:#3452c8;
        --card:#f6f6f9; --warn-bg:#fff7e8; --warn-line:#e8b34a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14141b; --fg:#e8e8ee; --muted:#9a9aa8; --line:#2c2c38; --accent:#8ea6ff;
          --card:#1c1c26; --warn-bg:#2a2318; --warn-line:#a87c2a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:16px/1.6 system-ui,-apple-system,sans-serif; }
main { max-width:60rem; margin:0 auto; padding:2rem 1.5rem 6rem; }
h1,h2,h3,h4 { line-height:1.25; }
h1 { font-size:2rem; margin:.2rem 0; }
h2 { border-bottom:1px solid var(--line); padding-bottom:.3rem; margin-top:2.5rem; }
a { color:var(--accent); }
code { background:var(--card); padding:.1em .35em; border-radius:4px; font-size:.9em; }
pre.code { background:var(--card); border:1px solid var(--line); border-radius:8px;
           padding:1rem; overflow-x:auto; }
pre.code code { background:none; padding:0; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; margin:1rem 0; min-width:50%; }
th,td { border:1px solid var(--line); padding:.4rem .7rem; text-align:left; }
th { background:var(--card); }
blockquote { border-left:3px solid var(--accent); margin:1rem 0; padding:.2rem 1rem;
             color:var(--muted); }
.meta { color:var(--muted); margin-bottom:2rem; }
.badge { display:inline-block; padding:.1rem .6rem; border-radius:999px;
         border:1px solid var(--accent); color:var(--accent); font-size:.85rem; }
.diagram { background:var(--card); border:1px solid var(--line); border-radius:8px;
           padding:1rem; margin:1rem 0; overflow-x:auto; }
.diagram pre.mermaid { margin:0; }
.diagram.raw::before { content:"diagram source (assets/mermaid.min.js not vendored)";
                       display:block; color:var(--muted); font-size:.8rem;
                       margin-bottom:.5rem; }
.callout-h, .callout-h ~ p, .callout-h ~ ul { background:var(--warn-bg); }
.callout-h { border-left:4px solid var(--warn-line); padding:.3rem .8rem; }
nav.toc { background:var(--card); border:1px solid var(--line); border-radius:8px;
          padding:1rem 1.5rem; }
nav.toc ul { margin:.3rem 0; padding-left:1.2rem; }
.feature { border-top:2px solid var(--line); margin-top:3rem; padding-top:1rem; }
.mockup { margin:1.5rem 0; }
.mockup iframe { width:100%; height:32rem; border:1px solid var(--line);
                 border-radius:8px; background:#fff; }
"""


def mockup_html(path):
    esc = html.escape(path, quote=True)
    return (
        '<div class="mockup"><iframe src="%s" loading="lazy"></iframe>'
        '<p><a href="%s" target="_blank">Open %s in new tab</a></p></div>'
        % (esc, esc, esc)
    )


def main():
    spec_path = os.path.join(SPEC_DIR, "spec.md")
    if not os.path.exists(spec_path):
        print("render-spec: no spec.md next to this script", file=sys.stderr)
        return 1
    with open(spec_path, encoding="utf-8") as f:
        meta, body = split_front_matter(f.read())

    features = []
    for entry in meta.get("features", []):
        rel = entry.get("file") if isinstance(entry, dict) else None
        if not rel:
            continue
        path = os.path.join(SPEC_DIR, rel)
        if not os.path.exists(path):
            print("render-spec: missing feature file %s" % rel, file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            fmeta, fbody = split_front_matter(f.read())
        features.append((rel, fmeta, fbody))

    mermaid_js = None
    if os.path.exists(MERMAID_ASSET):
        with open(MERMAID_ASSET, encoding="utf-8") as f:
            mermaid_js = f.read()

    name = meta.get("name", "Project spec")
    toc = ['<nav class="toc"><strong>Contents</strong><ul>']
    for match in re.finditer(r"^##\s+(.+)$", body, re.M):
        text = match.group(1).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        toc.append('<li><a href="#%s">%s</a></li>' % (slug, html.escape(text)))
    for rel, fmeta, _ in features:
        fid = fmeta.get("id", rel)
        toc.append(
            '<li><a href="#feature-%s">Feature: %s</a></li>'
            % (html.escape(fid, quote=True), html.escape(fmeta.get("feature", fid)))
        )
    toc.append("</ul></nav>")

    sections = [md_to_html(body)]
    for rel, fmeta, fbody in features:
        fid = html.escape(fmeta.get("id", rel), quote=True)
        sections.append('<section class="feature" id="feature-%s">' % fid)
        sections.append(
            "<h2>Feature: %s</h2>" % html.escape(fmeta.get("feature", fid))
        )
        sections.append(md_to_html(fbody, demote=1, slug_prefix=fid + "-"))
        mockups = fmeta.get("mockups", [])
        if isinstance(mockups, str):
            mockups = [m.strip() for m in mockups.strip("[]").split(",") if m.strip()]
        if mockups:
            sections.append("<h3>Mockups</h3>")
            for m in mockups:
                sections.append(mockup_html(m if isinstance(m, str) else str(m)))
        sections.append("</section>")

    if mermaid_js:
        mermaid_block = (
            "<script>%s</script>\n<script>mermaid.initialize({startOnLoad:true,"
            "theme:matchMedia('(prefers-color-scheme: dark)').matches?'dark':'default'});"
            "</script>" % mermaid_js
        )
    else:
        mermaid_block = (
            "<script>document.querySelectorAll('.diagram')"
            ".forEach(function(d){d.classList.add('raw')});</script>"
        )

    page = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<main>\n"
        "<h1>%s</h1>\n"
        '<p class="meta"><span class="badge">%s</span> &nbsp; created %s &nbsp; '
        "rendered %s</p>\n%s\n%s\n</main>\n%s\n</body>\n</html>\n"
        % (
            html.escape(name),
            CSS,
            html.escape(name),
            html.escape(meta.get("status", "draft")),
            html.escape(str(meta.get("created", "?"))),
            datetime.date.today().isoformat(),
            "\n".join(toc),
            "\n".join(sections),
            mermaid_block,
        )
    )

    out_path = os.path.join(SPEC_DIR, "spec.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

    today = datetime.date.today().isoformat()
    source = open(spec_path, encoding="utf-8").read()
    if re.search(r"^html_generated:", source, re.M):
        stamped = re.sub(
            r"^html_generated:.*$",
            "html_generated: %s" % today,
            source,
            count=1,
            flags=re.M,
        )
    elif source.startswith("---") and "\n---" in source[3:]:
        # key was hand-removed from the front-matter: insert it rather than
        # silently skipping the stamp — freshness tracking is the field's point
        stamped = source.replace("\n---", "\nhtml_generated: %s\n---" % today, 1)
        print("render-spec: html_generated was missing from spec.md — inserted",
              file=sys.stderr)
    else:
        stamped = source
        print("render-spec: spec.md has no front-matter — html_generated not stamped",
              file=sys.stderr)
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(stamped)

    print(
        "render-spec: wrote spec.html (%d feature file%s, mermaid %s)"
        % (
            len(features),
            "" if len(features) == 1 else "s",
            "inlined" if mermaid_js else "not vendored — diagrams show as source",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
