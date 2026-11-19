#!/usr/bin/env python3
"""Rebuild what an issue-flow run did from the tracker alone, as normalized events.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/name --since 7d
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge gitea --repo owner/name --out timeline.json
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/issues-repo \\
        --pr-repo owner/pr-repo --batch 78
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --fixture bundle.json   # test/dry-run

Render the document as one HTML page (no server, no external requests):

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" render --in timeline.json --out timeline.html
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --fixture bundle.json | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" render --out timeline.html --refresh 30

`render` reads --in (default stdin) and writes --out (default stdout). --refresh <seconds>
adds a meta refresh, for a page that something rewrites while it is open. The page filters
by timeframe in the browser (24h, 7d, 30d, all, or a date range). Exit codes: 0 = page
written, 2 = unreadable or malformed input, 5 = malformed invocation. Lane kinds are
documented in references/timeline.md.

`collect` is read-only. It reads issues, issue timelines and pull requests (paged until
an empty page) and prints one JSON document, or writes it to --out. The schema is in
references/timeline.md; this script is its only producer.

`collect` also merges the local events that hooks/record-event.py writes (worker spawns,
verdicts, worktree changes): --local-events [PATH|off]. A live collect reads the default log
when it exists; a missing log gives the tracker-only document.

Commits, comment roles, reviews, CI runs and the links from a problem to its fix are in the
document too (references/timeline.md). A patch is cut at 400 lines or 32 KB per file, and the
patches of one document share --max-diff-bytes (default 4194304). A cut patch has `truncated`
set. --no-diffs leaves every patch out and keeps the per-file stats. A GitHub commit lists its
first 300 files. A Gitea commit takes its stats and patches from the `.diff` route.

Flags: --since/--until take an ISO date, an ISO timestamp, or a relative age (`7d`,
`24h`, `2w`). --issue and --batch keep one issue or one batch (a tracking-issue number).
--pr-repo reads pull requests from another repository on the same forge.

The fetch is kept apart from `normalize`, which is pure: it takes a bundle of raw
payloads and returns the document. A --fixture file is such a bundle:
{"forge": {"type", "host", "owner", "repo"}, "issues": [{"issue", "timeline"}],
"pulls": [{"pr", "timeline", "commits", "reviews", "review_comments", "runs"}]}, with the
payloads exactly as the forge returned them. A GitHub commit is the `commits/{sha}` payload
(it carries `files`). A Gitea commit is the `pulls/{n}/commits` item plus a `diff` key that holds
the `.diff` text. `runs` is a list of {"run", "jobs"}; `jobs` is filled for failed runs only.
`commits`, `reviews`, `review_comments` and `runs` may be absent.

Endpoints were checked against https://docs.github.com/en/rest/pulls/pulls,
https://docs.github.com/en/rest/pulls/reviews, https://docs.github.com/en/rest/pulls/comments,
https://docs.github.com/en/rest/commits/commits, https://docs.github.com/en/rest/actions/workflow-runs,
https://docs.github.com/en/rest/actions/workflow-jobs and the Gitea swagger file at
https://gitea.com/swagger.v1.json (retrieved 2026-11-19).

Keep a page current while a run is going (the /timeline skill starts this in the background):

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" watch --forge github --repo owner/name --interval 300 --max-minutes 240

`watch` runs `collect`, then `render --refresh <interval>`, then sleeps, until --max-minutes
passes or it gets SIGINT or SIGTERM (both exit 0). It takes every `collect` flag, and it
owns --out: the default is <git common dir>/issue-flow/timeline.html, which no commit
carries and every worktree shares. It replaces the page in one step, so a browser never
reads half a page. --interval is 60 seconds or more (default 300). --max-minutes defaults
to 240. A failed fetch keeps the last good page; three failed fetches in a row exit 2.
IFW_WATCH_FAST=1 lifts the 60-second floor, for tests only. `path` prints the default page
path (exit 2 outside a git repository).

Environment: IFW_GH and IFW_TEA replace the `gh` and `tea` commands (for tests).
`tea api` exits 0 on an API error and prints an error object, so every body is checked.

Exit codes: 0 = document written, 2 = a fetch failed or the forge returned an error
body (nothing is printed to stdout), 5 = the invocation is malformed.
"""

import argparse
import html
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

VERSION = 1
BATCH_BRANCH = re.compile(r"^(?:batch|epic)/(\d+)-")
ISSUE_BRANCH = re.compile(r"^issue/(\d+)-")
CHECKPOINT = re.compile(r"^\s*checkpoint\s+(\d+)\s*:\s*(.*)", re.I)
CONVERGENCE = re.compile(r"<!--\s*issue-flow:convergence\s*-->\s*gates:\s*([^\n]*)")
GATE = re.compile(r"(\d+)\s*(?:→|->)\s*(?:(\d+)f|[–—-])\s*/\s*(?:(\d+)c|[–—-])")
CHECKLIST_REF = re.compile(r"^\s*[-*]\s*\[.\].*?#(\d+)", re.M)
WIP = re.compile(r"^\s*(?:WIP:|\[WIP\])", re.I)
GROUP_SECONDS = 2
ROLES = ("plan", "cross-check", "plan-amendment", "gate.pass", "gate.rework", "sub-merge", "checkpoint",
         "worker.launched", "status.repair", "question", "other")
# The prose runs wrote before the event marker existed. Each row is matched at the start of the comment.
PROSE_ROLES = (
    (re.compile(r"##\s*Plan\s*\(PM\)", re.I), "plan"),
    (re.compile(r"\*\*Amendment\s*\(batch cross-check", re.I), "plan-amendment"),
    (re.compile(r"finding:\s*batch cross-check", re.I), "cross-check"),
    (re.compile(r"PM gate:\s*rework requested", re.I), "gate.rework"),
    (re.compile(r"PM gate:\s*pass", re.I), "gate.pass"),
    (re.compile(r"Sub-merged into", re.I), "sub-merge"),
    (re.compile(r"checkpoint\s+\d+\s*:", re.I), "checkpoint"),
    (re.compile(r"worker launched:", re.I), "worker.launched"),
    (re.compile(r"status repair:", re.I), "status.repair"),
)
MARKER = re.compile(r"<!--\s*issue-flow:event\s+([^>]*?)\s*-->")
MARKER_ATTR = re.compile(r"(\w+)=(\S+)")
TRAILER = re.compile(r"^(Change|Why|Fixes):[ \t]*(.*\S)[ \t]*$", re.I | re.M)
CHANGES = ("feature", "fix", "test", "docs", "refactor", "merge")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e)/|(^|/)test[-_][^/]*$|[-_.]tests?\.[^/]*$|\.spec\.[^/]*$", re.I)
DOCS_PATH = re.compile(r"(^|/)docs?/|\.(md|mdx|rst|txt)$", re.I)
EXCERPT_CHARS = 300
MAX_PATCH_LINES = 400
MAX_PATCH_BYTES = 32 * 1024
DEFAULT_MAX_DIFF_BYTES = 4 * 1024 * 1024
RUN_FAILED = ("failure", "timed_out", "startup_failure")
REVIEW_STATES = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested", "REQUEST_CHANGES": "changes_requested",
                 "COMMENT": "commented", "COMMENTED": "commented", "DISMISSED": "dismissed"}
FILE_STATUS = {"deleted": "removed"}


class FetchError(Exception):
    pass


# ------------------------------------------------------------------ small helpers

def common_dir_path(name, cwd=None):
    """Return <git common dir>/issue-flow/<name> as an absolute path.

    The common dir is shared by every worktree of one repository, so writers and
    readers in different worktrees agree on the path. Raises RuntimeError outside
    a git repository.
    """
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=cwd,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"not a git repository: {result.stderr.strip()}")
    base = os.path.join(cwd or ".", result.stdout.strip())
    return os.path.join(os.path.abspath(base), "issue-flow", name)


def _parse_dt(value):
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_utc(value):
    """Return an ISO-8601 UTC timestamp with a Z suffix, or None for an empty value."""
    if not value:
        return None
    return _parse_dt(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_when(text, now=None):
    """Turn a --since/--until value into a UTC timestamp. Raises ValueError when unreadable."""
    now = now or datetime.now(timezone.utc)
    rel = re.fullmatch(r"(\d+)([hdw])", text.strip())
    if rel:
        unit = {"h": "hours", "d": "days", "w": "weeks"}[rel.group(2)]
        return (now - timedelta(**{unit: int(rel.group(1))})).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        return to_utc(text)
    except ValueError:
        raise ValueError(f"cannot read {text!r}: use an ISO date, an ISO timestamp, or 7d / 24h / 2w")


def _login(raw, *keys):
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict) and value.get("login"):
            return value["login"]
    return None


def _label_names(raw):
    return [lab["name"] for lab in raw.get("labels") or []]


# ------------------------------------------------------------------ normalization

def _timeline_item(forge_type, raw):
    """Map one raw timeline entry to {ts, op, actor, name, body}; None when it is not one we use."""
    if forge_type == "github":
        op = {"labeled": "label_add", "unlabeled": "label_remove", "closed": "close",
              "reopened": "reopen", "commented": "comment", "ready_for_review": "ready"}.get(raw.get("event"))
    else:
        kind = raw.get("type")
        op = {"close": "close", "reopen": "reopen", "comment": "comment"}.get(kind)
        if kind == "label":
            op = "label_add" if raw.get("body") == "1" else "label_remove"
        elif kind == "change_title" and WIP.match(raw.get("old_title") or "") \
                and not WIP.match(raw.get("new_title") or ""):
            op = "ready"
    ts = to_utc(raw.get("created_at"))
    if not op or not ts:
        return None
    return {"ts": ts, "op": op, "actor": _login(raw, "actor", "user"),
            "name": (raw.get("label") or {}).get("name"), "body": raw.get("body") or "",
            "url": raw.get("html_url"), "id": raw.get("id")}


def _status_events(items):
    """Collapse status:* label operations into transitions.

    Operations by one actor within GROUP_SECONDS form one group, because a forge
    records a swap as a remove and an add in either order. One group is one
    transition, so the swap reads as a single from/to pair.
    """
    ops = [i for i in items if i["op"] in ("label_add", "label_remove") and (i["name"] or "").startswith("status:")]
    ops.sort(key=lambda i: i["ts"])
    groups = []
    for op in ops:
        if groups and groups[-1][0]["actor"] == op["actor"] and \
                (_parse_dt(op["ts"]) - _parse_dt(groups[-1][0]["ts"])).total_seconds() <= GROUP_SECONDS:
            groups[-1].append(op)
        else:
            groups.append([op])
    out, current = [], None
    for group in groups:
        removed = [g["name"][7:] for g in group if g["op"] == "label_remove"]
        added = [g["name"][7:] for g in group if g["op"] == "label_add"]
        before = removed[0] if removed else current
        if added:
            for name in added:
                out.append({"ts": group[0]["ts"], "actor": group[0]["actor"], "from": before, "to": name})
                before = name
            current = added[-1]
        else:
            out.append({"ts": group[0]["ts"], "actor": group[0]["actor"], "from": before, "to": None})
            current = None
    return out


def _event(ts, kind, issue=None, pr=None, batch=None, actor=None, frm=None, to=None, detail=None, **extra):
    event = {"ts": ts, "kind": kind, "issue": issue, "pr": pr, "batch": batch,
             "actor": actor, "from": frm, "to": to, "detail": detail}
    event.update(extra)
    return event


def parse_convergence(text):
    """Return the last convergence line in `text` as [{gate, filed, closed}]; [] when none."""
    found = CONVERGENCE.findall(text or "")
    if not found:
        return []
    return [{"gate": int(m.group(1)),
             "filed": int(m.group(2)) if m.group(2) else None,
             "closed": int(m.group(3)) if m.group(3) else None}
            for m in GATE.finditer(found[-1])]


def parse_marker(body):
    """Return {role, issue, pr, problem} from the first event marker in `body`, or None.

    The grammar is in references/timeline.md. A marker with no `role` is not a marker.
    """
    found = MARKER.search(body or "")
    if not found:
        return None
    attrs = dict(MARKER_ATTR.findall(found.group(1)))
    if not attrs.get("role"):
        return None
    number = lambda key: int(attrs[key]) if attrs.get(key, "").isdigit() else None  # noqa: E731
    return {"role": attrs["role"], "issue": number("issue"), "pr": number("pr"), "problem": attrs.get("problem")}


def classify_comment(body):
    """Return the role of a comment: a valid marker role first, then a prose prefix, else `other`."""
    marker = parse_marker(body)
    if marker and marker["role"] in ROLES:
        return marker["role"]
    text = MARKER.sub("", body or "", count=1).lstrip()
    for pattern, role in PROSE_ROLES:
        if pattern.match(text):
            return role
    return "other"


def parse_trailers(message):
    """Return {change, why, fixes} from the trailer block (the last paragraph) of a commit message.

    `change` is None unless it names one of CHANGES. `fixes` keeps every `Fixes:` value in order.
    """
    paragraphs = (message or "").strip().split("\n\n")
    block = paragraphs[-1] if len(paragraphs) > 1 else ""
    out = {"change": None, "why": None, "fixes": []}
    for key, value in TRAILER.findall(block):
        key = key.lower()
        if key == "fixes":
            out["fixes"].append(value)
        elif key == "change" and value.lower() in CHANGES and out["change"] is None:
            out["change"] = value.lower()
        elif key == "why" and out["why"] is None:
            out["why"] = value
    return out


def _excerpt(text):
    return (text or "")[:EXCERPT_CHARS]


def _full_text(text):
    """Return a source text for a problem: whole, without the issue-flow marker line."""
    return MARKER.sub("", text or "").strip()


GIT_TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]+\S")


def body_without_trailers(body):
    """Return a commit body without `Change:`, `Why:` and `Fixes:` lines and without a trailing trailer block."""
    kept = "\n".join(l for l in (body or "").split("\n") if not TRAILER.match(l)).strip()
    paragraphs = [p for p in re.split(r"\n\s*\n", kept) if p.strip()]
    while paragraphs and all(GIT_TRAILER.match(l) for l in paragraphs[-1].split("\n") if l.strip()):
        paragraphs.pop()
    return "\n\n".join(paragraphs).strip()


def _comment_event(item, issue, pr, batch):
    """One `comment` event for a timeline comment, with its role, excerpt, url and marker problem key."""
    marker = parse_marker(item["body"])
    return _event(item["ts"], "comment", issue, pr, batch, item["actor"], role=classify_comment(item["body"]),
                  excerpt=_excerpt(item["body"]), url=item["url"],
                  problem=marker["problem"] if marker else None, _full=_full_text(item["body"]))


def _parse_diff(text):
    """Split `git diff` text into {path: (patch, additions, deletions)}. The patch starts at the first hunk."""
    out = {}
    for chunk in re.split(r"^diff --git ", text or "", flags=re.M)[1:]:
        head, _, rest = chunk.partition("\n")
        names = re.match(r"a/(.+?) b/(.+)$", head)
        if not names:
            continue
        start = re.search(r"^@@", rest, re.M)
        patch = rest[start.start():].rstrip("\n") if start else ""
        lines = patch.split("\n") if patch else []
        out[names.group(2)] = (patch or None,
                               sum(1 for l in lines if l.startswith("+")), sum(1 for l in lines if l.startswith("-")))
    return out


def _cap_patch(patch, budget, diffs):
    """Return (patch, truncated). `budget` is a one-item list holding the bytes left for the document."""
    if not diffs or patch is None:
        return None, False
    truncated = False
    lines = patch.split("\n")
    if len(lines) > MAX_PATCH_LINES:
        patch, truncated = "\n".join(lines[:MAX_PATCH_LINES]), True
    data = patch.encode("utf-8")
    if len(data) > MAX_PATCH_BYTES:
        data, truncated = data[:MAX_PATCH_BYTES], True
    if len(data) > budget[0]:
        data, truncated = data[:max(budget[0], 0)], True
    budget[0] -= len(data)
    return data.decode("utf-8", "ignore"), truncated


def _change_from_files(paths):
    if paths and all(TEST_PATH.search(p) for p in paths):
        return "test"
    if paths and all(DOCS_PATH.search(p) for p in paths):
        return "docs"
    return "feature"


def _commit_event(raw, issue, pr, batch, diffs, budget):
    commit = raw.get("commit") or {}
    message = commit.get("message") or ""
    subject, _, rest = message.partition("\n")
    body = rest.strip()
    when = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
    author = (raw.get("author") or {}).get("login") or (commit.get("author") or {}).get("name")
    trailers = parse_trailers(message)
    from_diff = _parse_diff(raw.get("diff")) if raw.get("diff") is not None else {}
    files = []
    for f in raw.get("files") or []:
        path = f.get("filename")
        patch, adds, dels = f.get("patch"), f.get("additions"), f.get("deletions")
        if path in from_diff:
            patch, adds, dels = from_diff[path]
        patch, truncated = _cap_patch(patch, budget, diffs)
        files.append({"path": path, "status": FILE_STATUS.get(f.get("status"), f.get("status")),
                      "additions": adds, "deletions": dels, "patch": patch, "truncated": truncated})
    merge = len(raw.get("parents") or []) > 1
    change = trailers["change"] or ("merge" if merge else _change_from_files([f["path"] for f in files]))
    why_body = body_without_trailers(body)
    event = _event(to_utc(when), "commit", issue, pr, batch, author, detail=subject, sha=raw.get("sha"),
                   subject=subject, body=body, author=author, url=raw.get("html_url"), change=change,
                   why=trailers["why"] or why_body or None, addresses=[], files=files)
    event["_fixes"], event["_inferred"] = trailers["fixes"], trailers["change"] is None
    return event


def _review_events(raw_reviews, raw_comments, issue, pr, batch):
    out = []
    for raw in raw_reviews:
        state = REVIEW_STATES.get((raw.get("state") or "").upper())
        ts = to_utc(raw.get("submitted_at"))
        if state and ts:
            out.append(_event(ts, "review", issue, pr, batch, _login(raw, "user"), state=state,
                              excerpt=_excerpt(raw.get("body")), url=raw.get("html_url"), review_id=raw.get("id"),
                              _full=_full_text(raw.get("body"))))
    for raw in raw_comments:
        ts = to_utc(raw.get("created_at"))
        if not ts:
            continue
        resolved = bool(raw.get("resolver")) if "resolver" in raw else None
        out.append(_event(ts, "review.comment", issue, pr, batch, _login(raw, "user"), path=raw.get("path"),
                          line=raw.get("line") or raw.get("original_line") or raw.get("position"),
                          excerpt=_excerpt(raw.get("body")), url=raw.get("html_url"), resolved=resolved,
                          review_id=raw.get("pull_request_review_id")))
    return out


def _workflow_name(raw):
    if raw.get("name"):
        return raw["name"]
    base = os.path.basename((raw.get("path") or "").split("@")[0])
    return os.path.splitext(base)[0] or raw.get("display_title")


def _run_event(entry, issue, pr, batch):
    raw = entry["run"]
    started = to_utc(raw.get("run_started_at") or raw.get("started_at") or raw.get("created_at"))
    completed = to_utc(raw.get("completed_at") or (raw.get("updated_at") if raw.get("conclusion") else None))
    duration = int((_parse_dt(completed) - _parse_dt(started)).total_seconds()) if started and completed else None
    failed_job = failed_step = None
    if raw.get("conclusion") in RUN_FAILED:
        job = next((j for j in entry.get("jobs") or [] if j.get("conclusion") in RUN_FAILED), None)
        if job:
            failed_job = job.get("name")
            step = next((st for st in job.get("steps") or [] if st.get("conclusion") in RUN_FAILED), None)
            failed_step = step.get("name") if step else None
    ts = completed or started
    if not ts:
        return None
    return _event(ts, "ci.run", issue, pr, batch, None, workflow=_workflow_name(raw), conclusion=raw.get("conclusion"),
                  started=started, completed=completed, duration=duration, url=raw.get("html_url"),
                  sha=raw.get("head_sha"), failed_job=failed_job, failed_step=failed_step)


def _problem_source(event):
    """Return (source, role, state, text) when `event` opens a problem, else None."""
    kind = event["kind"]
    if kind == "comment" and event.get("role") == "gate.rework":
        return kind, "gate.rework", None, event.get("_full") or "Rework requested"
    if kind == "review" and event.get("state") == "changes_requested":
        return kind, None, "changes_requested", event.get("_full") or "Changes requested"
    if kind == "ci.run" and event.get("conclusion") in RUN_FAILED:
        where = "".join([f" in {event['failed_job']}" if event.get("failed_job") else "",
                         f" at {event['failed_step']}" if event.get("failed_step") else ""])
        return kind, None, event["conclusion"], f"{event.get('workflow') or 'CI'} {event['conclusion']}{where}"
    return None


def _same_url(a, b):
    return bool(a) and bool(b) and a.rstrip("/") == b.rstrip("/")


def link_problems(events):
    """Fill `change` and `addresses` on commit events and return {issue: [problem]}.

    A problem is a `gate.rework` comment, a changes-requested review or a failed CI run. A commit
    that carries `Fixes:` URLs addresses the problems at those URLs. Any other non-merge commit
    addresses the problems on its issue that opened before it and that no earlier commit resolved.
    A commit with no `Change:` trailer that addresses a problem is a `fix`.
    """
    problems, by_url, review_of, counters = {}, [], {}, {}
    for event in sorted(events, key=lambda e: e["ts"]):
        source = _problem_source(event)
        n = event["issue"]
        if not source or n is None:
            continue
        key = event.get("problem") if source[0] == "comment" else None
        pid = f"{n}:{key}" if key else None
        if not pid:
            counters[n] = counters.get(n, 0) + 1
            pid = f"{n}:auto-{counters[n]}"
        if any(p["id"] == pid for p in problems.get(n, [])):
            continue
        entry = {"id": pid, "source": source[0], "role": source[1], "state": source[2], "text": source[3],
                 "url": event.get("url"), "ts": event["ts"], "resolved_by": [], "resolved": False}
        problems.setdefault(n, []).append(entry)
        by_url.append((entry["url"], entry))
        if source[0] == "review":
            review_of[(event["pr"], event.get("review_id"))] = entry
    for event in events:
        target = review_of.get((event["pr"], event.get("review_id"))) if event["kind"] == "review.comment" else None
        if target:
            by_url.append((event.get("url"), target))
    for event in sorted((e for e in events if e["kind"] == "commit"), key=lambda e: e["ts"]):
        fixes, inferred = event.pop("_fixes"), event.pop("_inferred")
        found = []
        if fixes:
            for url in fixes:
                for known, entry in by_url:
                    if _same_url(known, url) and entry not in found:
                        found.append(entry)
        elif event["change"] != "merge":
            found = [p for p in problems.get(event["issue"], []) if not p["resolved_by"] and p["ts"] <= event["ts"]]
        for entry in found:
            entry["resolved_by"].append(event["sha"])
            entry["resolved"] = True
        event["addresses"] = [p["id"] for p in found]
        if found and inferred and event["change"] != "merge":
            event["change"] = "fix"
    for event in events:
        event.pop("_full", None)
    return problems


def normalize(bundle, since=None, until=None, only_issue=None, only_batch=None, diffs=True,
              max_diff_bytes=DEFAULT_MAX_DIFF_BYTES):
    """Turn a bundle of raw forge payloads into the timeline document (references/timeline.md).

    `since` and `until` are UTC timestamps that bound the events. They do not bound
    the replay: statuses are replayed from the start so `status_at_start` is right.
    `diffs=False` drops every patch. `max_diff_bytes` caps the patch text in the whole document.
    """
    budget = [max_diff_bytes]
    forge = bundle["forge"]
    ftype = forge["type"]
    issues = {}
    tracking = {}   # batch number -> member issue numbers
    timelines = {}
    for entry in bundle.get("issues", []):
        raw = entry["issue"]
        n = raw["number"]
        issues[n] = raw
        items = [i for i in (_timeline_item(ftype, t) for t in entry.get("timeline", [])) if i]
        timelines[n] = items
        if {"type:batch", "type:epic"} & set(_label_names(raw)):
            tracking[n] = list(dict.fromkeys(int(m) for m in CHECKLIST_REF.findall(raw.get("body") or "")))
    pulls = {e["pr"]["number"]: e for e in bundle.get("pulls", [])}

    # Batches: a tracking issue, plus the branch and PRs found from branch names.
    batches = {}
    for pr in (e["pr"] for e in pulls.values()):
        m = BATCH_BRANCH.match(pr["head"]["ref"])
        if m:
            tracking.setdefault(int(m.group(1)), [])
    for n, members in tracking.items():
        entry = {"tracking": n, "branch": None, "members": members, "pr": None, "prs": []}
        for num, e in sorted(pulls.items()):
            head, base = e["pr"]["head"]["ref"], e["pr"]["base"]["ref"]
            m, bm = BATCH_BRANCH.match(head), BATCH_BRANCH.match(base)
            if m and int(m.group(1)) == n:
                entry["branch"], entry["pr"] = head, num
            elif bm and int(bm.group(1)) == n:
                entry["prs"].append(num)
        batches[str(n)] = entry
    batch_of = {n: n for n in tracking}
    for n, members in tracking.items():
        for member in members:
            batch_of.setdefault(member, n)

    events, status_start_source = [], {}
    convergence = []
    for n, raw in issues.items():
        items = timelines[n]
        batch = batch_of.get(n)
        events.append(_event(to_utc(raw.get("created_at")), "issue.opened", n, None, batch,
                             _login(raw, "user"), detail=raw.get("title")))
        closes = [i for i in items if i["op"] == "close"]
        for item in closes:
            events.append(_event(item["ts"], "issue.closed", n, None, batch, item["actor"]))
        if not closes and raw.get("state") == "closed" and raw.get("closed_at"):
            events.append(_event(to_utc(raw["closed_at"]), "issue.closed", n, None, batch))
        for item in (i for i in items if i["op"] == "reopen"):
            events.append(_event(item["ts"], "issue.reopened", n, None, batch, item["actor"]))
        statuses = _status_events(items)
        status_start_source[n] = statuses
        for s in statuses:
            events.append(_event(s["ts"], "status", n, None, batch, s["actor"], s["from"], s["to"]))
        for item in (i for i in items if i["op"] == "comment"):
            events.append(_comment_event(item, n, None, batch))
            m = CHECKPOINT.match(item["body"])
            if m:
                events.append(_event(item["ts"], "checkpoint", n, None, batch, item["actor"],
                                     detail=m.group(2).strip(), k=int(m.group(1))))
        if "flow:status" in _label_names(raw):
            for text in [raw.get("body")] + [i["body"] for i in sorted(items, key=lambda i: i["ts"]) if i["op"] == "comment"]:
                found = parse_convergence(text)
                if found:
                    convergence = found

    for num, e in sorted(pulls.items()):
        pr, items = e["pr"], [i for i in (_timeline_item(ftype, t) for t in e.get("timeline", [])) if i]
        head, base = pr["head"]["ref"], pr["base"]["ref"]
        hm, im, bm = BATCH_BRANCH.match(head), ISSUE_BRANCH.match(head), BATCH_BRANCH.match(base)
        issue = int(hm.group(1)) if hm else int(im.group(1)) if im else None
        batch = int(hm.group(1)) if hm else int(bm.group(1)) if bm else batch_of.get(issue)
        ready = [i for i in items if i["op"] == "ready"]
        draft = bool(pr.get("draft")) or bool(WIP.match(pr.get("title") or "")) or bool(ready)
        actor = _login(pr, "user")
        events.append(_event(to_utc(pr.get("created_at")), "pr.opened", issue, num, batch, actor,
                             detail=pr.get("title"), draft=draft, base=base, head=head))
        for item in ready:
            events.append(_event(item["ts"], "pr.ready", issue, num, batch, item["actor"]))
        merged = to_utc(pr.get("merged_at"))
        if merged:
            events.append(_event(merged, "pr.merged", issue, num, batch, _login(pr, "merged_by"),
                                 detail=pr.get("title"), base=base, head=head))
            if hm:
                events.append(_event(merged, "batch.merged", issue, num, batch, _login(pr, "merged_by"),
                                     detail=pr.get("title"), base=base, head=head))
        elif pr.get("state") == "closed" and pr.get("closed_at"):
            events.append(_event(to_utc(pr["closed_at"]), "pr.closed", issue, num, batch, None, base=base, head=head))
        for item in (i for i in items if i["op"] == "comment"):
            events.append(_comment_event(item, issue, num, batch))
        for raw in e.get("commits") or []:
            events.append(_commit_event(raw, issue, num, batch, diffs, budget))
        events.extend(_review_events(e.get("reviews") or [], e.get("review_comments") or [], issue, num, batch))
        for entry in e.get("runs") or []:
            run_event = _run_event(entry, issue, num, batch)
            if run_event:
                events.append(run_event)

    events.sort(key=lambda e: (e["ts"], e["kind"], e["issue"] or 0, e["pr"] or 0))
    problems = link_problems(events)
    kept = [e for e in events
            if (not since or e["ts"] >= since) and (not until or e["ts"] <= until)
            and (only_issue is None or e["issue"] == only_issue)
            and (only_batch is None or e["batch"] == only_batch)]
    filtered = any(v is not None for v in (since, until, only_issue, only_batch))

    shown = {e["issue"] for e in kept if e["issue"] is not None}
    issue_map = {}
    for n, raw in sorted(issues.items()):
        if filtered and n not in shown:
            continue
        before = [s for s in status_start_source[n] if since and s["ts"] < since]
        issue_map[str(n)] = {"title": raw.get("title"), "url": raw.get("html_url"), "state": raw.get("state"),
                             "labels": _label_names(raw),
                             "status_at_start": before[-1]["to"] if before else None,
                             "problems": problems.get(n, [])}
    shown_batches = {str(e["batch"]) for e in kept if e["batch"] is not None}
    return {"version": VERSION,
            "forge": {k: forge.get(k) for k in ("type", "host", "owner", "repo", "pr_repo") if forge.get(k)},
            "range": {"since": since, "until": until},
            "issues": issue_map,
            "batches": {k: v for k, v in batches.items() if not filtered or k in shown_batches},
            "convergence": convergence,
            "events": kept}


# ------------------------------------------------------------------ fetching

class Api:
    """Reads one forge through `gh api` or `tea api`; every body is checked for an error object."""

    def __init__(self, forge_type, repo_path="."):
        self.forge_type = forge_type
        self.repo_path = repo_path
        self.cmd = os.environ.get("IFW_GH", "gh") if forge_type == "github" else os.environ.get("IFW_TEA", "tea")
        self.limit = "per_page=100" if forge_type == "github" else "limit=50"

    def path(self, repo, suffix):
        return f"{'repos' if self.forge_type == 'github' else '/repos'}/{repo}{suffix}"

    def get(self, path):
        try:
            result = subprocess.run([self.cmd, "api", path], cwd=self.repo_path, capture_output=True, text=True)
        except OSError as exc:
            raise FetchError(f"cannot run {self.cmd}: {exc}")
        if result.returncode != 0:
            raise FetchError(f"{self.cmd} api {path} failed: {(result.stderr or result.stdout).strip()}")
        try:
            body = json.loads(result.stdout)
        except ValueError:
            raise FetchError(f"{self.cmd} api {path} returned a body that is not JSON: {result.stdout[:200]!r}")
        if isinstance(body, dict) and "message" in body and "number" not in body:
            raise FetchError(f"{self.cmd} api {path} returned an error: {body['message']}")
        return body

    def text(self, path):
        """Read a plain-text body (a `.diff` route). An error object is JSON, so it is still caught."""
        try:
            result = subprocess.run([self.cmd, "api", path], cwd=self.repo_path, capture_output=True, text=True)
        except OSError as exc:
            raise FetchError(f"cannot run {self.cmd}: {exc}")
        if result.returncode != 0:
            raise FetchError(f"{self.cmd} api {path} failed: {(result.stderr or result.stdout).strip()}")
        if result.stdout.lstrip().startswith("{") and '"message"' in result.stdout[:200]:
            raise FetchError(f"{self.cmd} api {path} returned an error: {result.stdout[:200]!r}")
        return result.stdout

    def paged(self, path, keep_going=None, key=None):
        """Read every page. An empty page ends the walk; a short page does not (server caps vary).

        `key` names the list inside an object body (`workflow_runs`).
        """
        rows = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 1000):
            body = self.get(f"{path}{sep}{self.limit}&page={page}")
            if key and isinstance(body, dict):
                body = body.get(key) or []
            if not isinstance(body, list):
                raise FetchError(f"{self.cmd} api {path} page {page} is not a list: {str(body)[:200]}")
            if not body:
                break
            rows.extend(body)
            if keep_going and not keep_going(body):
                break
        return rows


def _fetch_pull_detail(api, forge_type, repo, pr):
    """Return the commit, review, review-comment and CI-run payloads of one PR, as the forge sent them."""
    n = pr["number"]
    if forge_type == "github":
        commits = [dict(c, files=api.get(api.path(repo, f"/commits/{c['sha']}")).get("files") or [])
                   for c in api.paged(api.path(repo, f"/pulls/{n}/commits"))]
        reviews = api.paged(api.path(repo, f"/pulls/{n}/reviews"))
        review_comments = api.paged(api.path(repo, f"/pulls/{n}/comments"))
    else:
        commits = [dict(c, diff=api.text(api.path(repo, f"/git/commits/{c['sha']}.diff")))
                   for c in api.paged(api.path(repo, f"/pulls/{n}/commits?verification=false"))]
        reviews = api.paged(api.path(repo, f"/pulls/{n}/reviews"))
        review_comments = []
        for review in reviews:
            if review.get("comments_count"):
                review_comments.extend(api.paged(api.path(repo, f"/pulls/{n}/reviews/{review['id']}/comments")))
    runs = []
    sha = (pr.get("head") or {}).get("sha")
    if sha:
        try:
            raw_runs = api.paged(api.path(repo, f"/actions/runs?head_sha={sha}"), key="workflow_runs")
            for run in raw_runs:
                jobs = []
                if run.get("conclusion") in RUN_FAILED:
                    jobs = api.get(api.path(repo, f"/actions/runs/{run['id']}/jobs") +
                                   ("?per_page=100" if forge_type == "github" else "?limit=50")).get("jobs") or []
                runs.append({"run": run, "jobs": jobs})
        except FetchError as exc:
            # A repository with Actions off answers 404. Any other failure is a real one.
            if "Not Found" not in str(exc) and "404" not in str(exc):
                raise
    return {"commits": commits, "reviews": reviews, "review_comments": review_comments, "runs": runs}


def fetch_bundle(forge_type, repo, pr_repo=None, repo_path=".", since=None, only_issue=None):
    api = Api(forge_type, repo_path)
    owner, _, name = repo.partition("/")
    forge = {"type": forge_type, "host": None, "owner": owner, "repo": name}
    if pr_repo and pr_repo != repo:
        forge["pr_repo"] = pr_repo
    base_q = "?state=all" + ("" if forge_type == "github" else "&type=issues")

    raws = {}
    if only_issue is not None:
        raws[only_issue] = api.get(api.path(repo, f"/issues/{only_issue}"))
    else:
        query = base_q + (f"&since={quote(since)}" if since else "")
        listings = [api.paged(api.path(repo, "/issues" + query))]
        # Tracking and status issues carry batch membership and convergence data even
        # when they were not updated in the range.
        for label in ("type:batch", "type:epic", "flow:status"):
            listings.append(api.paged(api.path(repo, "/issues" + base_q + f"&labels={quote(label)}")))
        for rows in listings:
            for raw in rows:
                if "pull_request" not in raw:
                    raws.setdefault(raw["number"], raw)
    issues = []
    for n, raw in sorted(raws.items()):
        issues.append({"issue": raw, "timeline": api.paged(api.path(repo, f"/issues/{n}/timeline"))})

    def recent(page):
        return since is None or any((p.get("updated_at") or "") >= since for p in page)

    pr_query = "?state=all" + ("&sort=updated&direction=desc" if forge_type == "github" else "")
    prs = api.paged(api.path(pr_repo or repo, "/pulls" + pr_query), keep_going=recent if forge_type == "github" else None)
    pulls = []
    for pr in prs:
        if since and to_utc(pr.get("updated_at")) and to_utc(pr["updated_at"]) < since:
            continue
        entry = {"pr": pr, "timeline": api.paged(api.path(pr_repo or repo, f"/issues/{pr['number']}/timeline"))}
        entry.update(_fetch_pull_detail(api, forge_type, pr_repo or repo, pr))
        pulls.append(entry)
    return {"forge": forge, "issues": issues, "pulls": pulls}


# ------------------------------------------------------------------ command line

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(5)


LOCAL_PREFIXES = ("agent.", "worktree.")
LOCAL_KEYS = ("session", "agent", "worktree", "branch", "outcome")


def read_local_events(path):
    """Return the rows of a local-event log. A missing file or an unreadable line gives nothing."""
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not str(row.get("kind", "")).startswith(LOCAL_PREFIXES):
                continue
            row["ts"] = to_utc(row.get("ts"))
        except (ValueError, AttributeError, TypeError):
            continue
        if row["ts"]:
            rows.append(row)
    return rows


def merge_local(doc, rows, since=None, until=None, only_issue=None, only_batch=None):
    """Add the local-event rows to `doc["events"]`, with the same filters `normalize` applied."""
    batches = doc.get("batches") or {}
    member_of = {m: int(key) for key, info in batches.items() for m in (info.get("members") or [])}
    member_of.update({int(key): int(key) for key in batches})
    # A worktree event has no issue of its own. The branch `issue/<n>-…` names it, and a later
    # event for the same worktree path (a remove has no branch) inherits it.
    issue_of = {}
    for row in sorted(rows, key=lambda r: r["ts"]):
        branch = ISSUE_BRANCH.match(row.get("branch") or "")
        if isinstance(row.get("issue"), int):
            row_issue = row["issue"]
        else:
            row_issue = int(branch.group(1)) if branch else issue_of.get(row.get("worktree"))
        row["issue"] = row_issue
        if row_issue is not None and row.get("worktree"):
            issue_of[row["worktree"]] = row_issue
    for row in rows:
        if (since and row["ts"] < since) or (until and row["ts"] > until):
            continue
        issue = row["issue"]
        batch = row.get("batch") if isinstance(row.get("batch"), int) else member_of.get(issue)
        if (only_issue is not None and issue != only_issue) or (only_batch is not None and batch != only_batch):
            continue
        event = _event(row["ts"], row["kind"], issue=issue, batch=batch)
        event.update({key: row.get(key) for key in LOCAL_KEYS})
        doc["events"].append(event)
    doc["events"].sort(key=lambda e: (e["ts"], e["kind"], e["issue"] or 0, e["pr"] or 0))
    return doc


def local_events_path(args):
    """The log to merge, or None. --local-events off disables it; no flag reads the default log
    for a live collect only, so a --fixture run stays reproducible."""
    choice = args.local_events
    if choice == "off" or (choice is None and args.fixture):
        return None
    if choice not in (None, ""):
        return choice
    try:
        return common_dir_path("events.jsonl", args.repo_path)
    except (RuntimeError, OSError):
        return None


def cmd_collect(args):
    try:
        since = parse_when(args.since) if args.since else None
        until = parse_when(args.until) if args.until else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    if args.max_diff_bytes < 0:
        print("error: --max-diff-bytes must be 0 or more", file=sys.stderr)
        return 5
    if not args.fixture and not args.forge:
        print("error: specify --forge (with --repo) or --fixture", file=sys.stderr)
        return 5
    if not args.fixture and not args.repo:
        print("error: --forge requires --repo owner/name", file=sys.stderr)
        return 5
    try:
        if args.fixture:
            with open(args.fixture) as handle:
                bundle = json.load(handle)
        else:
            bundle = fetch_bundle(args.forge, args.repo, args.pr_repo, args.repo_path, since, args.issue)
        doc = normalize(bundle, since, until, args.issue, args.batch, not args.no_diffs, args.max_diff_bytes)
        log = local_events_path(args)
        if log:
            merge_local(doc, read_local_events(log), since, until, args.issue, args.batch)
    except (FetchError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": f"collect failed: {exc}"}), file=sys.stderr)
        return 2
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


# ------------------------------------------------------------------ render

# Lane kinds are data. A row here sets how a lane looks; another producer adds a lane type
# by adding a row. `bar` lanes draw a coloured status bar. `marks` lanes draw one marker per
# event. A kind that has no row still renders, as a generic `marks` lane.
LANE_KINDS = {
    "issue": {"label": "Issue", "style": "bar"},
    "batch": {"label": "Batch", "style": "marks"},
    "worktree": {"label": "Worktree", "style": "marks"},
    "agent": {"label": "Agent", "style": "marks"},
}
# Event kinds from `collect` that belong on an issue lane. `batch.*` belongs on the batch lane.
ISSUE_PREFIXES = {"status", "issue", "pr", "checkpoint", "comment", "commit", "review", "ci"}
# The detail kinds fill the drill-down log. On a tracking issue they belong to the batch lane,
# which is where the cross-check findings, the plan amendments and the batch CI runs show.
DETAIL_PREFIXES = {"comment", "commit", "review", "ci"}
# The kind filter in the panel. A key matches an event kind exactly, or by prefix when it ends in ".".
LOG_GROUPS = (
    ("commits", "Commits", ("commit",)),
    ("comments", "Comments", ("comment",)),
    ("reviews", "Reviews", ("review", "review.comment")),
    ("ci", "CI", ("ci.run",)),
    ("agents", "Agents", ("agent.", "worktree.")),
    ("merges", "Merges", ("batch.merged", "pr.merged")),
)
PARK_STATES = ("needs-feedback", "blocked")
NOTE_ROLES = ("cross-check", "plan-amendment")
EVENT_MARKERS = {"pr.opened": "pr", "pr.merged": "merge", "batch.merged": "batch", "checkpoint": "cp", "agent.messaged": "msg"}


def lane_kind_of(event_kind):
    prefix = (event_kind or "").split(".")[0]
    if prefix in ISSUE_PREFIXES:
        return "issue"
    return prefix or "other"


def log_group_of(event):
    kind = event.get("kind") or ""
    for key, _label, matches in LOG_GROUPS:
        if any(kind == m or (m.endswith(".") and kind.startswith(m)) for m in matches):
            return key
    return None


def marker_for(event):
    """Return the marker key an event draws, "dot" for a plain event, or None for a log-only event."""
    kind = event.get("kind") or ""
    if kind in EVENT_MARKERS:
        return EVENT_MARKERS[kind]
    if kind == "status" and event.get("to") in PARK_STATES:
        return "park"
    if kind == "comment" and event.get("role") == "gate.rework":
        return "rework"
    if kind == "comment" and event.get("role") in NOTE_ROLES:
        return "note"
    if kind == "commit" and event.get("change") == "fix":
        return "fix"
    if kind == "ci.run" and event.get("conclusion") in RUN_FAILED:
        return "ci"
    if kind == "review" and event.get("state") == "changes_requested":
        return "review"
    return None if kind.split(".")[0] in DETAIL_PREFIXES else "dot"


def lane_summary(events):
    """Count what a lane's events show: commits, files, lines, rework and review rounds, CI failures."""
    paths = set()
    summary = {"commits": 0, "files": 0, "additions": 0, "deletions": 0,
               "rework_rounds": 0, "review_rounds": 0, "ci_failures": 0}
    for event in events:
        kind = event.get("kind")
        if kind == "commit":
            summary["commits"] += 1
            for entry in event.get("files") or []:
                paths.add(entry.get("path"))
                summary["additions"] += entry.get("additions") or 0
                summary["deletions"] += entry.get("deletions") or 0
        elif kind == "comment" and event.get("role") == "gate.rework":
            summary["rework_rounds"] += 1
        elif kind == "review" and event.get("state") == "changes_requested":
            summary["review_rounds"] += 1
        elif kind == "ci.run" and event.get("conclusion") in RUN_FAILED:
            summary["ci_failures"] += 1
    summary["files"] = len(paths)
    return summary


def build_model(doc):
    """Return the lane model for a document: groups of lanes, each lane listing event indexes.

    Issue lanes sit under their batch. The batch lane leads its group and holds the
    batch.merged marker. Events with no batch go to the "No batch" group, which sorts last.
    """
    issues = doc.get("issues") or {}
    batches = doc.get("batches") or {}
    member_of = {str(m): str(key) for key, info in batches.items() for m in (info.get("members") or [])}
    member_of.update({str(key): str(key) for key in batches})  # the tracking issue sits in its own batch
    groups = {}
    kinds = {}
    for index, event in enumerate(doc.get("events") or []):
        kind = lane_kind_of(event.get("kind"))
        if kind == "issue" and (event.get("kind") or "").split(".")[0] in DETAIL_PREFIXES and event.get("issue") is not None \
                and str(event["issue"]) in batches:
            kind = "batch"
        if kind not in kinds:
            kinds[kind] = LANE_KINDS.get(kind) or {"label": kind.capitalize(), "style": "marks"}
        issue = None if event.get("issue") is None else str(event["issue"])
        batch = None if event.get("batch") is None else str(event["batch"])
        if kind == "batch":
            batch = batch or issue
            lane_id, label, url, title = f"batch:{batch}", f"Batch #{batch}", None, None
        elif kind == "issue" and issue is not None:
            batch = batch or member_of.get(issue)
            info = issues.get(issue) or {}
            lane_id, label, url, title = f"issue:{issue}", f"#{issue}", info.get("url"), info.get("title")
        else:
            ident = event.get("worktree") or event.get("agent") or event.get("session") or ""
            batch = batch or (member_of.get(issue) if issue else None)
            lane_id, label, url, title = f"{kind}:{ident}", f"{kinds[kind]['label']} {ident}".strip(), None, None
        group = groups.setdefault(batch, {"key": batch, "lanes": {}})
        lane = group["lanes"].setdefault(lane_id, {"id": lane_id, "kind": kind, "label": label, "title": title,
                                                    "url": url, "events": []})
        lane["events"].append(index)
    ordered = sorted(groups.values(), key=lambda g: (g["key"] is None, int(g["key"]) if g["key"] else 0))
    result = []
    for group in ordered:
        key = group["key"]
        if key is None:
            label = "No batch"
        else:
            branch = (batches.get(key) or {}).get("branch")
            label = f"Batch #{key}" + (f" — {branch}" if branch else "")
        lanes = sorted(group["lanes"].values(), key=lambda lane: (lane["kind"] != "batch", lane["events"][0]))
        result.append({"key": key, "label": label, "lanes": lanes})
    return result, kinds


def _json_for_script(value):
    """Dump JSON that cannot close the surrounding <script> tag."""
    text = json.dumps(value, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _convergence_strip(rows):
    if not rows:
        return ""
    cells = []
    for row in rows:
        filed = "–" if row.get("filed") is None else row["filed"]
        closed = "–" if row.get("closed") is None else row["closed"]
        cells.append(f'<li><b>Gate {html.escape(str(row.get("gate")))}</b> {html.escape(str(filed))} filed, '
                     f'{html.escape(str(closed))} closed</li>')
    return '<section class="convergence" aria-label="Convergence"><h2>Convergence</h2><ul>' + "".join(cells) + "</ul></section>"


# One table drives the bar colors, the marker colors and the legend. Each value is
# (light, dark). Every color in a theme is distinct, and a test enforces it.
STATE_COLORS = {
    "ready": ("#57606a", "#8b949e"),
    "in-progress": ("#0969da", "#388bfd"),
    "in-review": ("#8250df", "#a371f7"),
    "awaiting-review": ("#bf3989", "#db61a2"),
    "needs-feedback": ("#bf8700", "#d29922"),
    "blocked": ("#cf222e", "#f85149"),
    "batched": ("#b6bec8", "#4a525c"),
    "deploying": ("#1b7c83", "#39c5cf"),
    "deploy-failed": ("#8a1c2b", "#ff9492"),
    "merged": ("#1a7f37", "#3fb950"),
    "closed": ("#24292f", "#c9d1d9"),
}
MARKER_COLORS = {
    "pr": ("#8250df", "#a371f7"),
    "merge": ("#1a7f37", "#3fb950"),
    "batch": ("#0550ae", "#79c0ff"),
    "cp": ("#bf8700", "#d29922"),
    "park": ("#cf222e", "#f85149"),
    "rework": ("#e16f24", "#f0883e"),
    "fix": ("#0e8a7d", "#2dd4bf"),
    "ci": ("#a40e26", "#ff7b72"),
    "review": ("#bf3989", "#db61a2"),
    "note": ("#0969da", "#58a6ff"),
    "msg": ("#1b7c83", "#39c5cf"),
    "dot": ("#656d76", "#6e7681"),
}
MARKER_LABELS = {"pr": "PR opened", "merge": "PR merged", "batch": "Batch merged", "cp": "Checkpoint",
                 "park": "Parked (needs feedback or blocked)", "rework": "Rework requested", "fix": "Fix commit",
                 "ci": "CI run failed", "review": "Changes requested in review", "note": "Cross-check or plan amendment",
                 "msg": "Agent messaged", "dot": "Other event"}


def _theme_vars(theme):
    pairs = [f"--st-{k}:{v[theme]};" for k, v in STATE_COLORS.items()]
    pairs += [f"--mk-{k}:{v[theme]};" for k, v in MARKER_COLORS.items()]
    return " ".join(pairs)


PAGE_CSS = """
:root { --bg:#fff; --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --panel:#f6f8fa; --accent:#0969da;
  --st-none:#d0d7de; """ + _theme_vars(0) + """ }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --panel:#161b22; --accent:#58a6ff;
    --st-none:#30363d; """ + _theme_vars(1) + """ } }
* { box-sizing:border-box; }
body { margin:0; padding:1rem; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,sans-serif; }
h1 { font-size:1.25rem; margin:0 0 .25rem; } h2 { font-size:.95rem; margin:0 0 .25rem; }
.sub { color:var(--muted); margin:0 0 .75rem; }
.controls { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin-bottom:.75rem; }
.controls button, .controls input { font:inherit; color:var(--fg); background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:.25rem .6rem; }
.controls button[aria-pressed="true"] { border-color:var(--accent); color:var(--accent); }
.convergence { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.5rem .75rem; margin-bottom:.75rem; }
.convergence ul { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:.25rem 1.25rem; }
.legend { display:flex; flex-wrap:wrap; gap:.25rem 1rem; color:var(--muted); font-size:.85rem; margin-bottom:.75rem; }
.legend i { display:inline-block; width:.8rem; height:.6rem; border-radius:2px; margin-right:.3rem; vertical-align:baseline; }
#chart { overflow-x:auto; }
.board { min-width:640px; }
.axis, .lane { display:grid; grid-template-columns:12rem 1fr; align-items:center; }
.axis .ticks { position:relative; height:1.2rem; color:var(--muted); font-size:.75rem; }
.axis .ticks span { position:absolute; transform:translateX(-50%); white-space:nowrap; }
.axis .ticks span:first-child { transform:none; } .axis .ticks span:last-child { transform:translateX(-100%); }
.group { margin-top:.75rem; font-weight:600; border-bottom:1px solid var(--line); padding-bottom:.15rem; }
.lane { border-bottom:1px solid var(--line); min-height:1.9rem; }
.lane .name { padding-right:.5rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.lane .name a { color:var(--accent); text-decoration:none; }
.lane .name small { color:var(--muted); }
.track { position:relative; height:1.5rem; }
.seg { position:absolute; top:.45rem; height:.6rem; border-radius:3px; background:var(--st-none); opacity:.9; }
.mk { position:absolute; top:.2rem; width:.7rem; height:.7rem; transform:translateX(-50%) rotate(45deg); background:var(--fg);
  border:2px solid var(--bg); cursor:default; }
.mk-pr { background:var(--mk-pr); } .mk-merge { background:var(--mk-merge); }
.mk-batch { background:var(--mk-batch); width:.9rem; height:.9rem; top:.1rem; }
.mk-cp { background:var(--mk-cp); border-radius:50%; } .mk-park { background:var(--mk-park); border-radius:50%; }
.mk-dot { background:var(--mk-dot); border-radius:50%; }
.mk-rework { background:var(--mk-rework); border:0; transform:translateX(-50%); clip-path:polygon(50% 0,100% 100%,0 100%); }
.mk-fix { background:var(--mk-fix); transform:translateX(-50%); border-radius:2px; }
.mk-ci { background:var(--mk-ci); border:0; transform:translateX(-50%); clip-path:polygon(0 0,100% 0,50% 100%); }
.mk-review { background:var(--mk-review); border:0; transform:translateX(-50%); clip-path:polygon(50% 0,100% 38%,82% 100%,18% 100%,0 38%); }
.mk-note { background:var(--mk-note); transform:translateX(-50%); width:.6rem; height:.6rem; border-radius:1px; }
.mk-msg { background:var(--mk-msg); transform:translateX(-50%); width:.5rem; height:.5rem; border-radius:50%; }
.legend .mk { position:static; display:inline-block; vertical-align:middle; margin:0 .35rem 0 0; transform:rotate(45deg) scale(.8); }
.legend .mk-rework, .legend .mk-fix, .legend .mk-ci, .legend .mk-review, .legend .mk-note, .legend .mk-msg { transform:scale(.8); }
.legend .mk-batch { width:.8rem; height:.8rem; }
.lane { cursor:pointer; }
.lane:hover, .lane.sel { background:var(--panel); }
.lane:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
body.has-panel #layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(22rem,34rem); gap:1rem; align-items:start; }
#panel { background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:.75rem; position:sticky; top:.5rem;
  max-height:calc(100vh - 1rem); overflow:auto; min-width:0; }
#panel h3 { font-size:.85rem; margin:.75rem 0 .25rem; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); }
.phead { display:flex; justify-content:space-between; gap:.5rem; align-items:start; }
.phead button, .filters button { font:inherit; color:var(--fg); background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.1rem .5rem; cursor:pointer; }
.filters { display:flex; flex-wrap:wrap; gap:.25rem; margin:.5rem 0; }
.filters button[aria-pressed="true"] { border-color:var(--accent); color:var(--accent); }
#psummary { display:grid; grid-template-columns:repeat(auto-fill,minmax(7rem,1fr)); gap:.35rem; margin:.5rem 0 0; }
#psummary div { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.25rem .5rem; }
#psummary dt { color:var(--muted); font-size:.75rem; } #psummary dd { margin:0; font-weight:600; }
#problems, #notes, #log { list-style:none; margin:0; padding:0; }
#problems > li, #notes > li, #log > li { border-left:3px solid var(--line); padding:.25rem .5rem; margin:.35rem 0; overflow-wrap:anywhere; }
#problems > li { border-left-color:var(--mk-rework); }
#log > li.k-commits { border-left-color:var(--mk-fix); } #log > li.k-ci { border-left-color:var(--mk-ci); }
#log > li.k-reviews { border-left-color:var(--mk-review); } #log > li.k-agents { border-left-color:var(--mk-msg); }
.when { color:var(--muted); font-size:.75rem; }
.badge { display:inline-block; font-size:.7rem; line-height:1.3; padding:0 .4rem; border:1px solid var(--line); border-radius:999px; margin-right:.25rem; background:var(--panel); }
.b-fix, .b-gate-rework, .b-changes-requested, .b-failure, .b-timed-out, .b-truncated { border-color:var(--mk-rework); color:var(--mk-rework); }
.b-approved, .b-success, .b-gate-pass, .b-resolved { border-color:var(--mk-merge); color:var(--mk-merge); }
.pre { white-space:pre-wrap; }
.why { color:var(--muted); font-style:italic; }
details.file { margin:.25rem 0; } details.file > summary { cursor:pointer; font-family:ui-monospace,monospace; font-size:.8rem; }
.add { color:var(--mk-merge); } .del { color:var(--mk-park); }
.diff { font:12px/1.4 ui-monospace,monospace; margin:.25rem 0 0; overflow-x:auto; border:1px solid var(--line); border-radius:6px; }
.dl { display:block; white-space:pre; padding:0 .5rem; }
.dl-add { background:rgba(46,160,67,.22); } .dl-del { background:rgba(248,81,73,.22); }
.dl-hunk { background:var(--panel); color:var(--muted); } .dl-note { color:var(--muted); font-style:italic; }
@media (max-width:900px) { body.has-panel #layout { display:block; } #panel { position:static; max-height:none; margin-top:.75rem; } }
#tip { position:fixed; z-index:10; max-width:24rem; background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:.4rem .6rem; white-space:pre-line; pointer-events:none; box-shadow:0 2px 8px rgba(0,0,0,.25); }
#empty { color:var(--muted); padding:1rem; border:1px dashed var(--line); border-radius:6px; text-align:center; }
@media (max-width:640px) { .axis, .lane { grid-template-columns:7rem 1fr; } .board { min-width:520px; } }
"""

PAGE_JS = r"""
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("tl-data").textContent);
  var sel = null;
  var on = {};
  D.logGroups.forEach(function (g) { on[g.key] = true; });
  var times = D.events.map(function (e) { return Date.parse(e.ts); });
  var win = {lo: null, hi: null};
  var $ = function (id) { return document.getElementById(id); };

  function cls(text) { return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-"); }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  function stateAfter(e) {
    if (e.kind === "status") { return e.to || null; }
    if (e.kind === "pr.merged") { return "merged"; }
    if (e.kind === "issue.closed") { return "closed"; }
    if (e.kind === "issue.reopened") { return null; }
    return undefined;
  }
  function describe(e, lane) {
    var lines = [e.kind + "  " + e.ts];
    if (lane.title) { lines.push(lane.title); }
    if (e.from || e.to) { lines.push((e.from || "none") + " -> " + (e.to || "none")); }
    if (e.detail) { lines.push(e.detail); }
    if (e.pr) { lines.push("PR #" + e.pr); }
    if (e.actor) { lines.push("by " + e.actor); }
    return lines.join("\n");
  }
  function bounds() {
    var lo = win.lo, hi = win.hi;
    if (times.length) {
      if (lo === null) { lo = Math.min.apply(null, times); }
      if (hi === null) { hi = Math.max.apply(null, times); }
    }
    if (lo === null || hi === null) { return null; }
    if (hi <= lo) { hi = lo + 3600000; }
    return {lo: lo, hi: hi};
  }
  // Draw as many time labels as fit in the axis. Start with five and drop one until no two overlap.
  function axisTicks(ticks, b) {
    for (var steps = 4; steps >= 1; steps--) {
      ticks.textContent = "";
      for (var k = 0; k <= steps; k++) {
        var tick = el("span", null, stamp(b.lo + (b.hi - b.lo) * k / steps));
        tick.style.left = (100 * k / steps) + "%"; ticks.appendChild(tick);
      }
      var boxes = Array.prototype.map.call(ticks.children, function (n) { return n.getBoundingClientRect(); }), clash = false;
      for (var j = 1; j < boxes.length; j++) { if (boxes[j].left < boxes[j - 1].right + 8) { clash = true; } }
      if (!clash) { return; }
    }
  }
  var pending = false;
  window.addEventListener("resize", function () {
    if (pending) { return; }
    pending = true;
    window.requestAnimationFrame(function () { pending = false; draw(); });
  });
  function pos(ms, b) { return (100 * (ms - b.lo) / (b.hi - b.lo)).toFixed(3) + "%"; }

  function laneNode(lane, b, visible) {
    var kind = D.kinds[lane.kind];
    var row = el("div", "lane lane-" + cls(lane.kind) + (sel && sel.id === lane.id ? " sel" : ""));
    row.tabIndex = 0; row.setAttribute("role", "button");
    row.setAttribute("aria-label", "Open details for " + lane.label);
    row.addEventListener("click", function (ev) { if (!(ev.target.closest && ev.target.closest("a"))) { openLane(lane); } });
    row.addEventListener("keydown", function (ev) {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target === row) { ev.preventDefault(); openLane(lane); }
    });
    var name = el("div", "name");
    if (lane.url && /^https?:\/\//.test(lane.url)) {
      var link = el("a", null, lane.label);
      link.href = lane.url; link.rel = "noopener"; link.title = lane.title || "";
      name.appendChild(link);
    } else { name.appendChild(document.createTextNode(lane.label)); }
    if (lane.title) { name.appendChild(el("small", null, " " + lane.title)); }
    row.appendChild(name);
    var track = el("div", "track");
    var evs = lane.events.map(function (i) { return {e: D.events[i], t: times[i], i: i}; });
    if (kind.style === "bar") {
      var current = null;
      evs.forEach(function (x) { if (x.t < b.lo) { var s = stateAfter(x.e); if (s !== undefined) { current = s; } } });
      var info = D.issues[lane.id.split(":")[1]] || {};
      if (current === null && !evs.some(function (x) { return x.t < b.lo; }) && info.status_at_start) { current = info.status_at_start; }
      var points = [{t: b.lo, s: current, e: null}];
      visible.forEach(function (x) { var s = stateAfter(x.e); if (s !== undefined) { points.push({t: x.t, s: s, e: x.e}); } });
      points.forEach(function (p, i) {
        if (!p.s) { return; }
        var end = i + 1 < points.length ? points[i + 1].t : b.hi;
        var seg = el("div", "seg");
        seg.style.left = pos(p.t, b); seg.style.width = (100 * (end - p.t) / (b.hi - b.lo)).toFixed(3) + "%";
        seg.style.background = "var(--st-" + cls(p.s) + ", var(--st-none))";
        seg.setAttribute("data-tip", p.s + "\nfrom " + stamp(p.t) + " to " + stamp(end) + (lane.title ? "\n" + lane.title : ""));
        track.appendChild(seg);
      });
    }
    visible.forEach(function (x) {
      var mark = D.marks[x.i];
      if (!mark || (kind.style === "bar" && mark === "dot")) { return; }
      var node = el("div", "mk mk-" + mark);
      node.style.left = pos(x.t, b);
      node.setAttribute("data-tip", describe(x.e, lane));
      track.appendChild(node);
    });
    row.appendChild(track);
    return row;
  }

  function draw() {
    var chart = $("chart"), b = bounds();
    chart.textContent = "";
    var shown = 0;
    if (b) {
      var axis = el("div", "axis"), ticks = el("div", "ticks");
      axis.appendChild(el("div"));
      axis.appendChild(ticks);
      var board = el("div", "board"); board.appendChild(axis);
      D.groups.forEach(function (group) {
        var rows = [];
        group.lanes.forEach(function (lane) {
          var visible = lane.events.map(function (i) { return {e: D.events[i], t: times[i], i: i}; })
            .filter(function (x) { return x.t >= b.lo && x.t <= b.hi; });
          if (visible.length) { shown += visible.length; rows.push(laneNode(lane, b, visible)); }
        });
        if (rows.length) {
          board.appendChild(el("div", "group", group.label));
          rows.forEach(function (r) { board.appendChild(r); });
        }
      });
      chart.appendChild(board);
      axisTicks(ticks, b);
    }
    var empty = $("empty");
    empty.hidden = shown > 0;
    empty.textContent = "No events in this range." + (b && !shown ? " Range: " + stamp(b.lo) + " to " + stamp(b.hi) + " UTC." : "");
    chart.hidden = shown === 0;
    renderPanel();
    if (b) { $("from").value = new Date(b.lo).toISOString().slice(0, 10); $("to").value = new Date(b.hi).toISOString().slice(0, 10); }
  }

  var PRESETS = {"24h": 86400000, "7d": 7 * 86400000, "30d": 30 * 86400000};
  document.querySelectorAll("[data-preset]").forEach(function (button) {
    button.addEventListener("click", function () {
      var name = button.getAttribute("data-preset");
      if (name === "all") { win.lo = null; win.hi = null; }
      else { win.hi = Date.now(); win.lo = win.hi - PRESETS[name]; }
      document.querySelectorAll("[data-preset]").forEach(function (other) { other.setAttribute("aria-pressed", String(other === button)); });
      draw();
    });
  });
  function fromInputs() {
    var from = Date.parse($("from").value + "T00:00:00Z"), to = Date.parse($("to").value + "T23:59:59Z");
    win.lo = isNaN(from) ? null : from; win.hi = isNaN(to) ? null : to;
    document.querySelectorAll("[data-preset]").forEach(function (other) { other.setAttribute("aria-pressed", "false"); });
    draw();
  }
  $("from").addEventListener("change", fromInputs);
  $("to").addEventListener("change", fromInputs);

  var tip = $("tip");
  document.addEventListener("mousemove", function (ev) {
    var target = ev.target.closest ? ev.target.closest("[data-tip]") : null;
    if (!target) { tip.hidden = true; return; }
    tip.textContent = target.getAttribute("data-tip");
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = (ev.clientY + 14) + "px";
  });

  // ---- the drill-down panel. Every string from the data goes in as text, never as markup.
  var COMMITS = {};
  D.events.forEach(function (e) { if (e.kind === "commit" && e.sha) { COMMITS[e.sha] = e; } });
  var LABELS = [["commits", "Commits"], ["files", "Files"], ["additions", "Lines added"], ["deletions", "Lines removed"],
    ["rework_rounds", "Rework rounds"], ["review_rounds", "Review rounds"], ["ci_failures", "CI failures"]];
  function safeUrl(url) { return typeof url === "string" && /^https?:\/\//.test(url); }
  function link(url, text) {
    if (!safeUrl(url)) { return el("span", null, text); }
    var a = el("a", null, text); a.href = url; a.rel = "noopener"; return a;
  }
  function badge(text) { return el("span", "badge b-" + cls(text), text); }
  function add(parent) {
    for (var k = 1; k < arguments.length; k++) {
      var part = arguments[k];
      if (part !== null && part !== undefined) { parent.appendChild(typeof part === "string" ? document.createTextNode(part) : part); }
    }
    return parent;
  }
  function openLane(lane) {
    sel = lane;
    document.body.classList.add("has-panel");
    draw();
  }
  function closePanel() {
    sel = null;
    document.body.classList.remove("has-panel");
    draw();
  }
  function fillDiff(box, file) {
    box.textContent = "";
    if (file.patch === null || file.patch === undefined) {
      box.appendChild(el("span", "dl dl-note", "No diff available (binary file, or diffs were not collected)."));
      return;
    }
    var pre = el("pre", "diff");
    file.patch.split("\n").forEach(function (line) {
      var c = line.charAt(0) === "+" ? "dl-add" : line.charAt(0) === "-" ? "dl-del" : line.charAt(0) === "@" ? "dl-hunk" : "";
      pre.appendChild(el("span", "dl " + c, line === "" ? " " : line));
    });
    if (file.truncated) { pre.appendChild(el("span", "dl dl-note", "… diff truncated: the collector kept only the first part of this patch.")); }
    box.appendChild(pre);
  }
  function fileNode(file) {
    var d = el("details", "file");
    var head = el("summary");
    add(head, file.path + " ", el("span", "add", "+" + (file.additions || 0)), " ", el("span", "del", "-" + (file.deletions || 0)), " ",
      file.status && file.status !== "modified" ? badge(file.status) : null, file.truncated ? badge("truncated") : null);
    d.appendChild(head);
    var box = el("div"), done = false;
    d.appendChild(box);
    d.addEventListener("toggle", function () { if (d.open && !done) { done = true; fillDiff(box, file); } });
    return d;
  }
  function logItem(e, group) {
    var li = el("li", "k-" + group);
    var top = el("div", "when", stamp(Date.parse(e.ts)) + " · ");
    top.appendChild(badge(e.kind));
    if (e.kind === "commit") { top.appendChild(badge(e.change || "feature")); }
    if (e.role) { top.appendChild(badge(e.role)); }
    if (e.state) { top.appendChild(badge(e.state)); }
    if (e.kind === "ci.run" && e.conclusion) { top.appendChild(badge(e.conclusion)); }
    if (e.actor || e.author) { top.appendChild(document.createTextNode(e.actor || e.author)); }
    li.appendChild(top);
    if (e.kind === "commit") {
      add(li, link(e.url, String(e.sha || "").slice(0, 7)), " ", e.subject || "");
      if (e.why) { li.appendChild(el("div", "why", "Why: " + e.why)); }
      (e.files || []).forEach(function (f) { li.appendChild(fileNode(f)); });
    } else if (e.kind === "comment" || e.kind === "review") {
      add(li, el("div", "pre", e.excerpt || e.detail || ""), safeUrl(e.url) ? link(e.url, "open") : null);
    } else if (e.kind === "review.comment") {
      add(li, el("div", null, (e.path || "") + (e.line ? ":" + e.line : "") + (e.resolved === true ? " (resolved)" : "")),
        el("div", "pre", e.excerpt || ""), safeUrl(e.url) ? link(e.url, "open") : null);
    } else if (e.kind === "ci.run") {
      var text = (e.workflow || "run") + (e.duration !== null && e.duration !== undefined ? ", " + e.duration + " s" : "") +
        (e.failed_job ? " — failed in " + e.failed_job + (e.failed_step ? " at " + e.failed_step : "") : "");
      add(li, text + " ", safeUrl(e.url) ? link(e.url, "open") : null);
    } else {
      var bits = [e.detail, e.outcome, e.base && e.head ? e.head + " into " + e.base : null].filter(Boolean);
      if (bits.length) { li.appendChild(el("div", "pre", bits.join(" · "))); }
    }
    return li;
  }
  function problemItem(p) {
    var li = el("li");
    var top = el("div", "when", stamp(Date.parse(p.ts)) + " · ");
    top.appendChild(badge(p.source === "ci.run" ? "ci" : p.source));
    if (p.state || p.role) { top.appendChild(badge(p.state || p.role)); }
    top.appendChild(p.resolved ? badge("resolved") : badge("open"));
    li.appendChild(top);
    add(li, el("div", "pre", p.text || ""), safeUrl(p.url) ? link(p.url, "open the " + (p.source === "ci.run" ? "run" : p.source)) : null);
    var fix = el("div", "why");
    if (p.resolved && p.resolved_by && p.resolved_by.length) {
      fix.appendChild(document.createTextNode("Fixed by "));
      p.resolved_by.forEach(function (sha, n) {
        if (n) { fix.appendChild(document.createTextNode(", ")); }
        var c = COMMITS[sha];
        fix.appendChild(c ? link(c.url, sha.slice(0, 7)) : el("span", null, sha.slice(0, 7)));
        if (c && c.subject) { fix.appendChild(document.createTextNode(" " + c.subject)); }
      });
    } else { fix.textContent = "No commit has fixed this yet."; }
    li.appendChild(fix);
    return li;
  }
  function renderPanel() {
    var panel = $("panel");
    if (!sel) { panel.hidden = true; return; }
    panel.hidden = false;
    $("ptitle").textContent = sel.label + (sel.title ? " " + sel.title : "");
    var sum = D.summaries[sel.id] || {}, dl = $("psummary");
    dl.textContent = "";
    LABELS.forEach(function (pair) {
      var cell = el("div"); cell.appendChild(el("dt", null, pair[1])); cell.appendChild(el("dd", null, String(sum[pair[0]] || 0)));
      dl.appendChild(cell);
    });
    var isIssue = sel.kind === "issue", problems = isIssue ? ((D.issues[sel.id.split(":")[1]] || {}).problems || []) : [];
    $("pfix").hidden = !isIssue;
    var list = $("problems"); list.textContent = "";
    problems.forEach(function (p) { list.appendChild(problemItem(p)); });
    $("pnone").hidden = problems.length > 0;
    var notes = ((D.notes || {})[sel.id.split(":")[1]] || []).map(function (i) { return D.events[i]; });
    $("pnotes").hidden = sel.kind !== "batch";
    var nlist = $("notes"); nlist.textContent = "";
    notes.forEach(function (e) { nlist.appendChild(logItem(e, "comments")); });
    $("pnonotes").hidden = notes.length > 0;
    var b = bounds(), log = $("log"), n = 0;
    log.textContent = "";
    sel.events.forEach(function (i) {
      var group = D.logs[i];
      if (!group || !on[group] || !b || times[i] < b.lo || times[i] > b.hi) { return; }
      log.appendChild(logItem(D.events[i], group)); n++;
    });
    $("lognone").hidden = n > 0;
  }
  document.querySelectorAll("[data-log]").forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-log");
      on[key] = !on[key];
      button.setAttribute("aria-pressed", String(on[key]));
      renderPanel();
    });
  });
  $("pclose").addEventListener("click", closePanel);
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && sel) { closePanel(); } });

  var legend = $("legend");
  D.states.forEach(function (s) {
    var item = el("span", null, s.label), swatch = el("i"); swatch.style.background = "var(--st-" + cls(s.key) + ", var(--st-none))";
    item.insertBefore(swatch, item.firstChild); legend.appendChild(item);
  });
  D.markers.forEach(function (m) {
    var item = el("span", null, m.label), swatch = el("span", "mk mk-" + m.key);
    item.insertBefore(swatch, item.firstChild); legend.appendChild(item);
  });
  draw();
})();
"""


def render_html(doc, refresh=None, now=None):
    """Return one self-contained HTML page for a timeline document.

    The page embeds the document and draws it with inline JavaScript, so the timeframe
    filter works offline. `refresh` (seconds) adds a meta refresh for a page that is
    rewritten while open.
    """
    groups, kinds = build_model(doc)
    events = doc.get("events") or []
    forge = doc.get("forge") or {}
    where = "/".join(str(forge[k]) for k in ("owner", "repo") if forge.get(k)) or "workflow"
    rng = doc.get("range") or {}
    span = f"{rng.get('since') or 'start'} to {rng.get('until') or 'now'}"
    stamped = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen = [k for k in STATE_COLORS]
    for event in events:  # a status the table does not know still gets a legend entry (grey bar)
        for value in (event.get("to"),) if event.get("kind") == "status" else ():
            if value and value not in seen:
                seen.append(value)
    states = [{"key": k, "label": k} for k in seen]
    markers = [{"key": k, "label": MARKER_LABELS[k]} for k in MARKER_COLORS]
    summaries = {lane["id"]: lane_summary([events[i] for i in lane["events"]]) for group in groups for lane in group["lanes"]}
    notes = {}  # batch key -> indexes of its cross-check and plan-amendment comments, whichever issue holds them
    for index, event in enumerate(events):
        if event.get("kind") == "comment" and event.get("role") in NOTE_ROLES and event.get("batch") is not None:
            notes.setdefault(str(event["batch"]), []).append(index)
    data = {"states": states, "markers": markers, "events": events, "issues": doc.get("issues") or {}, "batches": doc.get("batches") or {},
            "range": rng, "groups": groups, "kinds": kinds, "marks": [marker_for(e) for e in events],
            "logs": [log_group_of(e) for e in events], "logGroups": [{"key": k, "label": label} for k, label, _ in LOG_GROUPS],
            "summaries": summaries, "notes": notes}
    filters = "".join(f'<button type="button" data-log="{html.escape(k)}" aria-pressed="true">{html.escape(label)}</button>'
                      for k, label, _ in LOG_GROUPS)
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">\n' if refresh else ""
    empty = '<p id="empty">No events in this range.</p>' if not events else '<p id="empty" hidden>No events in this range.</p>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>Workflow timeline — {html.escape(where)}</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<h1>Workflow timeline — {html.escape(where)}</h1>
<p class="sub">Data range: {html.escape(span)} · generated {html.escape(stamped)} · times are UTC</p>
<div class="controls">
<button type="button" data-preset="24h" aria-pressed="false">24h</button>
<button type="button" data-preset="7d" aria-pressed="false">7d</button>
<button type="button" data-preset="30d" aria-pressed="false">30d</button>
<button type="button" data-preset="all" aria-pressed="true">all</button>
<label>From <input type="date" id="from"></label>
<label>To <input type="date" id="to"></label>
</div>
{_convergence_strip(doc.get("convergence"))}
<div class="legend" id="legend" aria-label="Status colors"></div>
<div id="layout">
<main>
<div id="chart"></div>
{empty}
</main>
<aside id="panel" aria-label="Lane detail" hidden>
<div class="phead"><h2 id="ptitle"></h2><button type="button" id="pclose">Close</button></div>
<dl id="psummary"></dl>
<section id="pfix"><h3>What needed fixing</h3><ul id="problems"></ul><p id="pnone" class="sub">Nothing needed fixing.</p></section>
<section id="pnotes" hidden><h3>Cross-checks and amendments</h3><ul id="notes"></ul><p id="pnonotes" class="sub">None recorded.</p></section>
<h3>Log</h3>
<div class="filters" id="logfilter">{filters}</div>
<ol id="log"></ol><p id="lognone" class="sub" hidden>No log entries in this range.</p>
</aside>
</div>
<div id="tip" hidden></div>
<script id="tl-data" type="application/json">{_json_for_script(data)}</script>
<script>{PAGE_JS}</script>
</body>
</html>
"""


def cmd_render(args):
    if args.refresh is not None and args.refresh < 1:
        print("error: --refresh takes a whole number of seconds, 1 or more", file=sys.stderr)
        return 5
    try:
        if args.infile and args.infile != "-":
            with open(args.infile) as handle:
                doc = json.load(handle)
        else:
            doc = json.load(sys.stdin)
        if not isinstance(doc, dict) or not isinstance(doc.get("events"), list):
            raise ValueError("not a timeline document: no events list")
        page = render_html(doc, refresh=args.refresh)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"render failed: {exc}"}), file=sys.stderr)
        return 2
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(page)
    else:
        sys.stdout.write(page)
    return 0


# ------------------------------------------------------------------ watch and path

WATCH_FAILURE_LIMIT = 3


def cmd_path(args):
    try:
        print(common_dir_path("timeline.html"))
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 0


def _watch_once(collect_args, out_path, interval, scratch):
    """Collect and render one page. Return 0, or the exit code of the failed step."""
    doc_path = os.path.join(scratch, "timeline.json")
    try:
        code = main(["collect", *collect_args, "--out", doc_path])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 5
    if code != 0:
        return code
    try:
        with open(doc_path) as handle:
            page = render_html(json.load(handle), refresh=max(1, int(-(-interval // 1))))
        tmp_path = os.path.join(os.path.dirname(out_path), f".{os.path.basename(out_path)}.tmp")
        with open(tmp_path, "w") as handle:
            handle.write(page)
        os.replace(tmp_path, out_path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"watch render failed: {exc}"}), file=sys.stderr)
        return 2
    return 0


def cmd_watch(args):
    fast = os.environ.get("IFW_WATCH_FAST") == "1"
    if args.interval < (0 if fast else 60) or args.interval <= 0:
        print("error: --interval takes 60 seconds or more", file=sys.stderr)
        return 5
    if args.max_minutes <= 0:
        print("error: --max-minutes must be more than 0", file=sys.stderr)
        return 5
    try:
        out_path = os.path.abspath(args.out) if args.out else common_dir_path("timeline.html")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"error": f"watch failed: {exc}"}), file=sys.stderr)
        return 2

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + args.max_minutes * 60
    failures_in_a_row = 0
    try:
        with tempfile.TemporaryDirectory() as scratch:
            while True:
                code = _watch_once(args.collect_args, out_path, args.interval, scratch)
                if code == 5:
                    return 5
                failures_in_a_row = failures_in_a_row + 1 if code else 0
                if code:
                    print(f"watch: refresh failed (exit {code}); the last page stays", file=sys.stderr, flush=True)
                    if failures_in_a_row >= WATCH_FAILURE_LIMIT:
                        return 2
                else:
                    print(f"wrote {out_path} at {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
                          file=sys.stderr, flush=True)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return 0
                time.sleep(min(args.interval, remaining))
    except KeyboardInterrupt:
        print("watch: stopped", file=sys.stderr, flush=True)
        return 0


def main(argv=None):
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    collect = sub.add_parser("collect", help="read the tracker and print the timeline document")
    collect.add_argument("--forge", choices=["github", "gitea"])
    collect.add_argument("--repo", help="owner/name of the repository that holds the issues")
    collect.add_argument("--pr-repo", help="owner/name of the repository that holds the pull requests (default: --repo)")
    collect.add_argument("--repo-path", default=".", help="checkout that `tea api` resolves its login from")
    collect.add_argument("--fixture", help="read a recorded bundle instead of the tracker")
    collect.add_argument("--since")
    collect.add_argument("--until")
    collect.add_argument("--issue", type=int)
    collect.add_argument("--batch", type=int, help="tracking-issue number of one batch or epic")
    collect.add_argument("--local-events", nargs="?", const="", metavar="PATH|off",
                         help="merge the hook's local events (default: <git common dir>/issue-flow/events.jsonl "
                              "for a live collect; `off` skips it; with --fixture, give the flag to read it)")
    collect.add_argument("--no-diffs", action="store_true", help="leave every patch out of the commit events")
    collect.add_argument("--max-diff-bytes", type=int, default=DEFAULT_MAX_DIFF_BYTES,
                         help="cap on the patch text in the whole document (default 4194304)")
    collect.add_argument("--out", help="write the document here instead of stdout")
    collect.set_defaults(func=cmd_collect)
    render = sub.add_parser("render", help="turn a timeline document into one self-contained HTML page")
    render.add_argument("--in", dest="infile", help="timeline JSON from `collect` (default: stdin)")
    render.add_argument("--out", help="write the page here instead of stdout")
    render.add_argument("--refresh", type=int, help="seconds between automatic page reloads (default: off)")
    render.set_defaults(func=cmd_render)
    watch = sub.add_parser("watch", help="rewrite the HTML page on a timer while a run is going",
                           description="Every other flag goes to `collect`.")
    watch.add_argument("--interval", type=float, default=300, help="seconds between refreshes (60 or more; default 300)")
    watch.add_argument("--max-minutes", type=float, default=240, help="stop after this long (default 240)")
    watch.add_argument("--out", help="page path (default: <git common dir>/issue-flow/timeline.html)")
    watch.set_defaults(func=cmd_watch)
    path = sub.add_parser("path", help="print the default page path")
    path.set_defaults(func=cmd_path)
    args, extra = parser.parse_known_args(argv)
    if args.command == "watch":
        args.collect_args = extra
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
