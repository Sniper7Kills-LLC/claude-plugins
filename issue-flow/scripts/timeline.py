#!/usr/bin/env python3
"""Rebuild what an issue-flow run did from the tracker and the local-event log, as normalized events.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/name --since 7d
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge gitea --repo owner/name --out timeline.json
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/issues-repo \\
        --pr-repo owner/pr-repo --batch 78
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --fixture bundle.json   # test/dry-run

`collect` reads the tracker on `--jobs` threads (1 to 16, default 4; `snapshot` and `watch` pass it
on). The document is the same for every value. A rate-limit reply (GitHub secondary rate limit,
HTTP 429) is retried until 3 attempts have run, after `Retry-After` seconds or 2 s, 4 s. A
primary-limit reply (the quota is used up) waits for the quota's reset when it comes in 60 s or
less, else it stops the collect with exit 3. Any other failed read fails the whole collect.

The quota guard (GitHub): `collect` reads the quota with `gh api rate_limit`, which costs no
quota. Before each wave of reads it estimates the calls of that wave's uncached reads. If the
calls sent plus the estimate are more than --budget percent (default 50) of the quota at the
start, it stops with exit 3 and one stderr line: the estimate of the next wave, what remains,
the reset time and the options. Later waves are checked when they come, so the estimate is not
the cost of the whole run. Gitea has no rate-limit route, so the guard is off there.

Render the document as one HTML page (no server, no external requests):

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" render --in timeline.json --out timeline.html
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" snapshot --forge github --repo owner/name --out timeline.html
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --fixture bundle.json | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" render --out timeline.html --refresh 30

`render` reads --in (default stdin) and writes --out (default stdout). --refresh <seconds>
adds a meta refresh, for a page that something rewrites while it is open. The page filters
by timeframe in the browser (24h, 7d, 30d, all, or a date range). Exit codes: 0 = page
written, 2 = unreadable or malformed input, 5 = malformed invocation. Lane kinds are
documented in references/timeline.md.

`snapshot` runs `collect` and `render` in one process and takes every `collect` flag plus --out
(the page path; default `<git common dir>/issue-flow/timeline.html`). It exits 0 only when the
page is written, and prints one stderr line with the path, the event, lane and problem counts, and
`<n> API calls (<m> cached, <k> not modified), <q> remaining`. `<q>` is the quota read after the
run, and a forge with no quota to read leaves it out. `watch` runs the same code on a timer.

`collect` is read-only on the tracker. It reads issues, issue timelines and pull requests (paged until
an empty page) and prints one JSON document, or writes it to --out. The schema is in
references/timeline.md; this script is its only producer.

`collect` also merges the local events that hooks/record-event.py writes (worker spawns,
verdicts, worktree changes): --local-events [PATH|off]. A live collect reads the default log
when it exists; a missing log gives the tracker-only document.

Commits, comment roles, reviews, CI runs and the links from a problem to its fix are in the
document too (references/timeline.md). A patch is cut at 400 lines or 32 KB per file, and the
patches of one document share --max-diff-bytes (default 4194304). A cut patch has `truncated`
set. --no-diffs leaves every patch out and keeps the per-file stats. A GitHub commit lists its
first 300 files. A Gitea commit takes its stats and patches from the `.diff` route. A hunk
header keeps its line ranges and drops the section heading, which each source finds differently.

--diff-source git|api|auto (default auto) says where a commit's files and patches come from.
`auto` reads them from the --repo-path checkout and costs no API call. First it fetches, for
each PR repository, the heads of the PRs that hold a commit git lacks, with one `git fetch`
through the remote whose URL ends in that `owner/name`: refs/pull/<n>/head goes to
refs/issue-flow/pull/<owner>/<repo>/<n>, and no branch, tag, remote ref or work-tree file
changes. A fetch never prompts and stops after 60 s. A commit git still lacks is read through
the API, and one stderr line counts them and names git's first error line.
`git` fails on such a commit (exit 2); `api` reads every commit through the API. The "API cost"
section of references/timeline.md has the details and a measurement.

A read cache in <git common dir>/issue-flow/cache/ (or --cache-dir PATH) keeps every read but the
listings, keyed by the item's updated_at, a PR's head sha, a commit's sha, or a run's time. A
second collect with no changes reads only the listings, and on GitHub each unchanged listing
page gets a 304. The listing query sends --since rounded down to the start of its UTC hour, so
a relative --since gives the same query for an hour. The CI runs of an open PR, or of a PR
updated in the last hour, are read every time, and a run list that holds a run still going is
not kept. --no-cache neither reads nor writes the cache; --refresh-cache reads nothing from it
and writes every read. The document is the same with and without the cache.

`gc` removes the cache files that no collect touched for 30 days, the temporary files older than
1 hour, and the refs/issue-flow/pull/ refs of PRs that the cache shows closed for more than 30
days. `gc --all` removes the whole cache and every refs/issue-flow/ ref, so the next collect is
cold. gc prints one `ref <name>` line on stdout for each ref, first. `--dry-run` also prints one
`cache <path>` line for each file and removes nothing; `--verbose` prints the same lines in a
real run. The stderr summary names up to 3 refs. `snapshot` runs the same gc (never --all) after
it writes the page.

Flags: --since/--until take an ISO date, an ISO timestamp, or a relative age (`7d`,
`24h`, `2w`). --issue and --batch keep one issue or one batch (a tracking-issue number).
--pr-repo reads pull requests from another repository on the same forge. It repeats, and it
takes a comma list. The --repo repository is always read, and a PR found twice is kept once.
Each PR event carries `repo`. A PR whose head branch is `batch/<n>-*` or `epic/<n>-*` is the
batch PR of batch <n>, whichever repository holds it.

The fetch is kept apart from `normalize`, which is pure: it takes a bundle of raw
payloads and returns the document. A --fixture file is such a bundle:
{"forge": {"type", "host", "owner", "repo"}, "issues": [{"issue", "timeline"}],
"pulls": [{"repo", "pr", "timeline", "commits", "reviews", "review_comments", "runs"}]}, with the
payloads exactly as the forge returned them. A GitHub commit is the `commits/{sha}` payload
(it carries `files`). A Gitea commit is the `pulls/{n}/commits` item plus a `diff` key that holds
the `.diff` text. `runs` is a list of {"run", "jobs"}; `jobs` is filled for failed runs only.
`commits`, `reviews`, `review_comments` and `runs` may be absent. `repo` is `owner/name`; an
entry with none belongs to the issue repository.

Endpoints were checked against https://docs.github.com/en/rest/pulls/pulls,
https://docs.github.com/en/rest/pulls/reviews, https://docs.github.com/en/rest/pulls/comments,
https://docs.github.com/en/rest/commits/commits, https://docs.github.com/en/rest/actions/workflow-runs,
https://docs.github.com/en/rest/actions/workflow-jobs and the Gitea swagger file at
https://gitea.com/swagger.v1.json (retrieved 2026-11-19). The PR head refs were checked against
https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/checking-out-pull-requests-locally,
https://github.com/go-gitea/gitea/blob/main/modules/git/ref.go (`refs/pull/` + `/head`), the
Gitea `.diff` command at https://github.com/go-gitea/gitea/blob/main/modules/git/diff.go, and
https://git-scm.com/docs/git-fetch (retrieved 2026-11-19). The quota read and the primary-limit
reply were checked against https://docs.github.com/en/rest/rate-limit/rate-limit and
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api, and the absence
of a Gitea rate-limit route against https://gitea.com/swagger.v1.json (retrieved 2026-11-19).

Keep a page current while a run is going (the /timeline skill starts this in the background):

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" watch --forge github --repo owner/name --interval 300 --max-minutes 240

`watch` runs `collect`, then `render --refresh <interval>`, then sleeps, until --max-minutes
passes or it gets SIGINT or SIGTERM (both exit 0). A sleep ends at the deadline at the latest,
and watch then runs one last cycle. It prints one start line on stderr (the interval, the stop
time and the page path) and one end line (`watch: stopped after <n> cycles: <reason>`). It takes every `collect` flag, and it
owns --out: the default is <git common dir>/issue-flow/timeline.html, which no commit
carries and every worktree shares. It replaces the page in one step, so a browser never
reads half a page. --interval is 60 seconds or more (default 300). --max-minutes defaults
to 240. A failed fetch keeps the last good page; three failed fetches in a row exit 2. When the
quota guard stops a collect, `watch` skips the cycle, keeps the last page, prints one line that
names the next attempt, and goes on. The guard never ends a watch.
IFW_WATCH_FAST=1 lifts the 60-second floor, for tests only. `path` prints the default page
path (exit 2 outside a git repository).

Environment: IFW_GH and IFW_TEA replace the `gh` and `tea` commands (for tests).
`tea api` exits 0 on an API error and prints an error object, so every body is checked.

Exit codes: 0 = document written, 2 = a fetch failed or the forge returned an error
body (nothing is printed to stdout), 3 = the quota guard stopped the collect, or the quota is
used up until a reset more than 60 s away (nothing is printed to stdout), 5 = the invocation is
malformed.
"""

import argparse
import hashlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
# Four reads at once keep a collect well under GitHub's secondary (burst and concurrency) limits.
DEFAULT_JOBS = 4
MAX_JOBS = 16
RETRY_ATTEMPTS = 3
RETRY_AFTER_CAP = 60
RATE_LIMITED = re.compile(r"secondary rate limit|rate limit exceeded|abuse detection|\b429\b|too many requests", re.I)
RETRY_AFTER = re.compile(r"retry-after:?\s*(\d+)", re.I)
# GitHub's primary-limit reply is a 403 or 429 with x-ratelimit-remaining 0. `gh api` prints the
# message, not the headers, so the reset time comes from the reply only when it carries the header.
PRIMARY_LIMIT = re.compile(r"API rate limit exceeded|x-ratelimit-remaining:?\s*0\b", re.I)
RATELIMIT_RESET = re.compile(r"x-ratelimit-reset:?\s*(\d+)", re.I)
# GET /rate_limit costs no quota: https://docs.github.com/en/rest/rate-limit/rate-limit
RATE_LIMIT_PATH = "rate_limit"
DEFAULT_BUDGET = 50
# A paged read sends at least two requests: the page with the rows and the empty page that ends it.
PAGED_READ_CALLS = 2
RUN_FAILED = ("failure", "timed_out", "startup_failure")
REVIEW_STATES = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested", "REQUEST_CHANGES": "changes_requested",
                 "COMMENT": "commented", "COMMENTED": "commented", "DISMISSED": "dismissed"}
FILE_STATUS = {"deleted": "removed"}
FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# `git diff --name-status` letters, as the GitHub commit API names them.
GIT_STATUS = {"A": "added", "D": "removed", "M": "modified", "R": "renamed", "C": "copied", "T": "changed"}
MAX_COMMIT_FILES = 300
# The section heading after a hunk's `@@ … @@` comes from per-language rules. GitHub, Gitea and
# local git apply different rules (GitHub finds `def` lines in Python, git's default finds any
# line that starts with a letter), so the heading is dropped and a patch is the same from every source.
HUNK_HEADING = re.compile(r"^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@).*$", re.M)
DIFF_SOURCES = ("auto", "git", "api")
CACHE_VERSION = 1
# A closed PR updated this recently can still get a CI run that does not change its updated_at.
RUNS_STALE_AFTER = timedelta(hours=1)
# A fetch that hangs (a dead network, a server that never answers) must not stop the collect.
GIT_FETCH_TIMEOUT = 60
# git's error for a refspec whose source the remote does not have (a PR ref that is gone).
MISSING_REF = re.compile(r"couldn't find remote ref", re.I)
# gc: a cache file that no read touched for GC_MAX_AGE goes, and so does the ref of a PR that
# closed more than GC_MAX_AGE ago. A temporary file older than GC_TMP_AGE is a killed write.
GC_MAX_AGE = timedelta(days=30)
GC_TMP_AGE = timedelta(hours=1)
CACHE_DIR_NAME = re.compile(r"^[0-9a-f]{2}$")
CACHE_FILE_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
CACHE_TMP_NAME = re.compile(r"^\..*\.tmp$")
STATE_KEY = re.compile(r"^(?:github|gitea)/(.+)/pull/(\d+)/[^/]+/[^/]+/state$")
ENTRY_KEY = re.compile(r'"key":\s*"((?:[^"\\]|\\.)*)"')
PR_REF = re.compile(r"^refs/issue-flow/pull/(.+)/(\d+)$")


class FetchError(Exception):
    pass


class QuotaRefused(FetchError):
    """The quota guard stopped the collect. The message is the one line to print; the exit code is 3."""


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


def api_since(since):
    """Return `since` rounded down to the start of its UTC hour, or None for no `since`.

    A relative --since (`1d`) gives a new timestamp every second. The rounded value goes into the
    API query, and so into the listing's cache key, so collects in the same hour send the same
    query and get 304. The exact `since` still filters the results on this side.
    """
    if not since:
        return None
    return _parse_dt(since).replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if patch:
            patch = HUNK_HEADING.sub(r"\1", patch)
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
            review_of[(event.get("repo"), event["pr"], event.get("review_id"))] = entry
    for event in events:
        target = review_of.get((event.get("repo"), event["pr"], event.get("review_id"))) if event["kind"] == "review.comment" else None
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
    issue_repo = f"{forge.get('owner')}/{forge.get('repo')}"
    # A PR is known by repository and number: two repositories can both have a PR 8. An entry
    # with no `repo` belongs to the issue repository. A PR listed twice is kept once.
    pulls = {}
    for e in bundle.get("pulls", []):
        pulls.setdefault((e.get("repo") or issue_repo, e["pr"]["number"]), e)

    # Batches: a tracking issue, plus the branch and PRs found from branch names.
    batches = {}
    for pr in (e["pr"] for e in pulls.values()):
        m = BATCH_BRANCH.match(pr["head"]["ref"])
        if m:
            tracking.setdefault(int(m.group(1)), [])
    for n, members in tracking.items():
        entry = {"tracking": n, "branch": None, "members": members, "pr": None, "pr_repo": None, "prs": []}
        # The batch PR is found by its head branch. A member PR is found by its base branch.
        for (pr_repo, num), e in sorted(pulls.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            head, base = e["pr"]["head"]["ref"], e["pr"]["base"]["ref"]
            m, bm = BATCH_BRANCH.match(head), BATCH_BRANCH.match(base)
            if m and int(m.group(1)) == n:
                entry["branch"], entry["pr"], entry["pr_repo"] = head, num, pr_repo
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
            m = CHECKPOINT.match(MARKER.sub("", item["body"] or "", count=1).lstrip())
            if m:
                events.append(_event(item["ts"], "checkpoint", n, None, batch, item["actor"],
                                     detail=m.group(2).strip(), k=int(m.group(1))))
        if "flow:status" in _label_names(raw):
            for text in [raw.get("body")] + [i["body"] for i in sorted(items, key=lambda i: i["ts"]) if i["op"] == "comment"]:
                found = parse_convergence(text)
                if found:
                    convergence = found

    for (pr_repo, num), e in sorted(pulls.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        first_event = len(events)
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
        for event in events[first_event:]:
            event["repo"] = pr_repo

    events.sort(key=lambda e: (e["ts"], e["kind"], e["issue"] or 0, e["pr"] or 0, e.get("repo") or ""))
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
            "forge": {k: forge.get(k) for k in ("type", "host", "owner", "repo", "pr_repos") if forge.get(k)},
            "range": {"since": since, "until": until},
            "issues": issue_map,
            "batches": {k: v for k, v in batches.items() if not filtered or k in shown_batches},
            "convergence": convergence,
            "events": kept}


# ------------------------------------------------------------------ fetching

class Api:
    """Reads one forge through `gh api` or `tea api`; every body is checked for an error object.

    Calls are safe to make from many threads: an `Api` holds only its settings and a call count.
    `pmap` runs reads on a bounded pool and returns the results in the order of the inputs.
    `sent` counts the requests sent, and a retry is one more request. `not_modified` counts the
    sent requests that got a 304 (see `conditional`).
    """

    def __init__(self, forge_type, repo_path=".", jobs=DEFAULT_JOBS):
        self.forge_type = forge_type
        self.repo_path = repo_path
        self.jobs = jobs
        self.cmd = os.environ.get("IFW_GH", "gh") if forge_type == "github" else os.environ.get("IFW_TEA", "tea")
        self.limit = "per_page=100" if forge_type == "github" else "limit=50"
        self.sent = 0
        self.not_modified = 0
        self._sent_lock = threading.Lock()

    def path(self, repo, suffix):
        return f"{'repos' if self.forge_type == 'github' else '/repos'}/{repo}{suffix}"

    def pmap(self, fn, items):
        """Return [fn(item) for item in items], reading up to `jobs` at once.

        The result order never depends on which read ends first. The first failure, in input
        order, is raised, and reads that have not started are dropped.
        """
        items = list(items)
        if self.jobs <= 1 or len(items) <= 1:
            return [fn(item) for item in items]
        pool = ThreadPoolExecutor(max_workers=self.jobs)
        try:
            futures = [pool.submit(fn, item) for item in items]
            return [future.result() for future in futures]
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _run(self, path, count=True):
        """Run one `api` call and return its stdout. A failed call raises FetchError.

        A rate-limit reply is retried until RETRY_ATTEMPTS attempts have run. The wait is the
        `Retry-After` value when the reply has one, else 2, 4, then 8 seconds, and never
        more than RETRY_AFTER_CAP. Any other failure is raised at once.

        A primary-limit reply (the quota is used up) waits for the quota's reset only when the
        reset comes in RETRY_AFTER_CAP seconds or less. Otherwise it raises QuotaRefused at once,
        because a retry before the reset fails too. `count=False` keeps the call out of `sent`,
        for the quota reads, which cost no quota.
        """
        base = float(os.environ.get("IFW_RETRY_DELAY", "2"))
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            if count:
                with self._sent_lock:
                    self.sent += 1
            try:
                result = subprocess.run([self.cmd, "api", path], cwd=self.repo_path, capture_output=True, text=True)
            except OSError as exc:
                raise FetchError(f"cannot run {self.cmd}: {exc}")
            if result.returncode == 0:
                return result.stdout
            reason = (result.stderr or result.stdout).strip()
            if PRIMARY_LIMIT.search(reason) and path != RATE_LIMIT_PATH:
                reset = self._reset_of(reason)
                wait = None if reset is None else max(0.0, reset - time.time() + 1)
                if wait is None or wait > RETRY_AFTER_CAP or attempt == RETRY_ATTEMPTS:
                    raise QuotaRefused(f"collect: the API quota is used up. {reset_text(reset)} "
                                       "Use a shorter --since, or wait for the reset.")
                time.sleep(wait)
                continue
            limited = RATE_LIMITED.search(reason)
            if not limited or attempt == RETRY_ATTEMPTS:
                suffix = f" (rate limit; gave up after {attempt} attempts)" if limited else ""
                raise FetchError(f"{self.cmd} api {path} failed: {reason}{suffix}")
            wait = RETRY_AFTER.search(reason)
            time.sleep(min(float(wait.group(1)) if wait else base * 2 ** (attempt - 1), RETRY_AFTER_CAP))

    def _reset_of(self, reason):
        """Return the quota's reset as a Unix time: the reply's x-ratelimit-reset, else the quota read."""
        header = RATELIMIT_RESET.search(reason)
        if header:
            return int(header.group(1))
        quota = self.quota()
        return quota["reset"] if quota else None

    def quota(self):
        """Return the core REST quota as {"limit", "remaining", "reset"}, or None when there is none.

        The read is GET /rate_limit, which costs no quota, so it is not in `sent`. Gitea has no
        rate-limit route, and a GitHub server with rate limits off answers 404: both give None.
        """
        if self.forge_type != "github":
            return None
        try:
            out = self._run(RATE_LIMIT_PATH, count=False)
            body = json.loads(out)
        except FetchError as exc:
            if _not_found(exc):
                return None
            raise
        except ValueError:
            raise FetchError(f"{self.cmd} api {RATE_LIMIT_PATH} returned a body that is not JSON: {out[:200]!r}")
        core = ((body.get("resources") or {}).get("core") or {}) if isinstance(body, dict) else {}
        if isinstance(body, dict) and "message" in body and not core:
            if _not_found(FetchError(body["message"])):
                return None
            raise FetchError(f"{self.cmd} api {RATE_LIMIT_PATH} returned an error: {body['message']}")
        if not all(isinstance(core.get(k), int) for k in ("limit", "remaining", "reset")):
            raise FetchError(f"{self.cmd} api {RATE_LIMIT_PATH} has no resources.core quota: {out[:200]!r}")
        return {k: core[k] for k in ("limit", "remaining", "reset")}

    def get(self, path):
        out = self._run(path)
        try:
            body = json.loads(out)
        except ValueError:
            raise FetchError(f"{self.cmd} api {path} returned a body that is not JSON: {out[:200]!r}")
        if isinstance(body, dict) and "message" in body and "number" not in body:
            raise FetchError(f"{self.cmd} api {path} returned an error: {body['message']}")
        return body

    def text(self, path):
        """Read a plain-text body (a `.diff` route). An error object is JSON, so it is still caught."""
        out = self._run(path)
        if out.lstrip().startswith("{") and '"message"' in out[:200]:
            raise FetchError(f"{self.cmd} api {path} returned an error: {out[:200]!r}")
        return out

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

    def conditional(self, path, etag=None):
        """Read one GitHub page with `--include`, and send `If-None-Match: <etag>` when `etag` is set.

        Returns (304, etag, None) when the page did not change, else (200, etag or None, body).
        gh reports a 304 with exit 1 and `gh: HTTP 304`, so the status line decides, not the exit
        code. A 304 is never an error and is never retried. Any other failure gives the read to
        `get`, which retries it as `_run` does; that costs one more request, on a failure only.
        """
        args = [self.cmd, "api", "--include"] + (["-H", f"If-None-Match: {etag}"] if etag else []) + [path]
        with self._sent_lock:
            self.sent += 1
        try:
            result = subprocess.run(args, cwd=self.repo_path, capture_output=True, text=True)
        except OSError as exc:
            raise FetchError(f"cannot run {self.cmd}: {exc}")
        status, headers, out = parse_include(result.stdout)
        if status == 304:
            with self._sent_lock:
                self.not_modified += 1
            return 304, headers.get("etag") or etag, None
        if result.returncode != 0 or status is None or not 200 <= status < 300:
            return 200, None, self.get(path)
        try:
            body = json.loads(out)
        except ValueError:
            raise FetchError(f"{self.cmd} api {path} returned a body that is not JSON: {out[:200]!r}")
        if isinstance(body, dict) and "message" in body and "number" not in body:
            raise FetchError(f"{self.cmd} api {path} returned an error: {body['message']}")
        return 200, headers.get("etag"), body


def parse_include(text):
    """Split `gh api --include` output into (status, headers, body). No status line gives (None, {}, text).

    gh writes `HTTP/2.0 200 OK`, then one `Name: value` line per header, then an empty line.
    Header names are made lower case.
    """
    match = re.match(r"HTTP/\S+ (\d{3})[^\n]*\n", text)
    if not match:
        return None, {}, text
    parts = re.split(r"\r?\n\r?\n", text[match.end():], maxsplit=1)
    head, body = parts[0], parts[1] if len(parts) > 1 else ""
    headers = {}
    for line in head.splitlines():
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return int(match.group(1)), headers, body


def listing_key(forge_type, path):
    """Return the cache key of a listing's ETags and pages. `path` holds the query, not the page."""
    return f"etag/{forge_type}/{path}"


def read_listing(api, cache, path, keep_going=None):
    """Read every page of a GitHub listing, and send each page's stored ETag as `If-None-Match`.

    A 304 reuses the stored page. The stored pages are one snapshot, so when a page after the
    first changed, the walk starts again at page 1 with no ETags. A change on page 1 needs no
    restart, because no stored page was used yet. The new pages and ETags are stored under
    `listing_key` when every page has an ETag. The walk ends as `Api.paged` ends.

    A stored ETag is not a cached read: a request is still sent. So the entry is read without
    counting a hit, and `api.not_modified` counts the 304s.
    """
    key = listing_key(api.forge_type, path)
    entry = cache.peek(key)
    stored = entry.get("pages") if isinstance(entry, dict) else None
    if not isinstance(stored, list) or not all(isinstance(p, dict) and p.get("etag") and isinstance(p.get("body"), list)
                                               for p in stored):
        stored = []
    sep = "&" if "?" in path else "?"
    while True:
        pages, rows, restart = [], [], False
        for page in range(1, 1000):
            old = stored[page - 1] if page <= len(stored) else None
            status, etag, body = api.conditional(f"{path}{sep}{api.limit}&page={page}", old and old["etag"])
            if status == 304 and old is not None:
                etag, body = old["etag"], old["body"]
            elif old is not None:
                stored = []
                if page > 1:
                    restart = True
                    break
            if not isinstance(body, list):
                raise FetchError(f"api {path} page {page} is not a list: {str(body)[:200]}")
            pages.append({"etag": etag, "body": body})
            if not body:
                break
            rows.extend(body)
            if keep_going and not keep_going(body):
                break
        if not restart:
            break
    if all(p["etag"] for p in pages):
        cache.put(key, {"pages": pages})
    return rows


def _not_found(exc):
    return "Not Found" in str(exc) or "404" in str(exc)


def _error_line(stderr):
    """Return the first `fatal:` or `error:` line of git's stderr, else its first line."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in lines:
        if line.lower().startswith(("fatal:", "error:")):
            return line
    return lines[0] if lines else "git fetch failed with no message"


def _url_repo(url):
    """Return the `owner/name` at the end of a remote URL, in lower case, or None.

    The last two path parts name the repository in every URL form a forge gives:
    `https://host/o/r.git`, `git@host:o/r.git`, `ssh://git@host:22/o/r` and a local path.
    """
    url = (url or "").strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = [p for p in re.split(r"[/:]", url) if p]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else None


class GitDiffs:
    """Reads commit files and patches from the local repository, so they cost no API call.

    Commits are content-addressed: a commit git has is the commit the forge has, whichever
    remote it came from. `fetch` puts PR heads under refs/issue-flow/ only, so it never moves a
    branch, a tag, a remote-tracking ref or the work tree. A commit git cannot read returns
    None, and the caller reads it through the API instead.
    """

    def __init__(self, repo_path="."):
        self.repo_path = repo_path

    def _call(self, args, stdin=None, timeout=None, env=None):
        """Run one git command. Return (exit code, stdout, stderr).

        A git that cannot start, or that runs past `timeout` seconds, gives the exit code None
        and a stderr text that names the cause. With no `stdin`, git reads /dev/null, so a
        prompt can never wait for input.
        """
        streams = {"input": stdin.encode()} if stdin is not None else {"stdin": subprocess.DEVNULL}
        try:
            result = subprocess.run(["git", "-C", self.repo_path, "-c", "core.quotePath=false", *args],
                                    capture_output=True, timeout=timeout,
                                    env=env or {**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **streams)
        except subprocess.TimeoutExpired:
            return None, "", f"git {args[0]} timed out after {timeout:g} s"
        except OSError as exc:
            return None, "", f"cannot run git: {exc}"
        return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")

    def _git(self, *args, stdin=None):
        code, out, _ = self._call(args, stdin)
        return out if code == 0 else None

    def _fetch_env(self):
        """Return the environment of a fetch: no password prompt, and SSH in batch mode.

        BatchMode goes after the SSH command the user set (GIT_SSH_COMMAND, else core.sshCommand),
        so their key and options still apply. A GIT_SSH program takes no `-o`, so it is left alone.
        """
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        ssh = os.environ.get("GIT_SSH_COMMAND") or (self._git("config", "--get", "core.sshCommand") or "").strip()
        if ssh or not os.environ.get("GIT_SSH"):
            env["GIT_SSH_COMMAND"] = f"{ssh or 'ssh'} -o BatchMode=yes"
        return env

    def known(self, shas):
        """Return the set of `shas` that are commits in the local repository."""
        want = [s for s in shas if s and FULL_SHA.match(s)]
        if not want:
            return set()
        out = self._git("cat-file", "--batch-check=%(objectname) %(objecttype)", stdin="\n".join(want) + "\n")
        return {line.split()[0] for line in (out or "").splitlines() if line.endswith(" commit")}

    def remote_for(self, repo):
        """Return the name of the remote whose URL ends in `repo` (`owner/name`), or None.

        When several remotes match, the first name in sort order wins, so the choice is stable.
        """
        out = self._git("config", "--get-regexp", r"^remote\..*\.url$") or ""
        names = []
        for line in out.splitlines():
            key, _, url = line.partition(" ")
            if _url_repo(url) == repo.lower():
                names.append(key[len("remote."):-len(".url")])
        return min(names) if names else None

    def fetch(self, repo, numbers):
        """Fetch the head of each PR in `numbers` from `repo` into refs/issue-flow/pull/<repo>/<n>.

        One `git fetch` reads every head. A PR ref that is gone fails the whole call, so after a
        missing-ref error each head is fetched alone, and one bad ref costs only its own commits.
        Any other failure (no access, no network, a timeout) would fail each retry too, so it
        stops at once. Each fetch stops after GIT_FETCH_TIMEOUT seconds and never prompts.
        Return the first line of git's first error, or None when every head was fetched.
        """
        remote = self.remote_for(repo)
        if remote is None:
            return f"no remote URL of {self.repo_path} ends in {repo}"
        # --refmap= stops the configured refspecs from updating remote-tracking refs as a side effect.
        base = ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
                "--no-recurse-submodules", "--refmap=", remote]
        spec = lambda n: f"+refs/pull/{n}/head:refs/issue-flow/pull/{repo}/{n}"  # noqa: E731
        env = self._fetch_env()
        code, _, err = self._call([*base, *(spec(n) for n in numbers)], timeout=GIT_FETCH_TIMEOUT, env=env)
        if code == 0:
            return None
        if len(numbers) == 1 or not MISSING_REF.search(err):
            return _error_line(err)
        errors = []
        for n in numbers:
            code, _, err = self._call([*base, spec(n)], timeout=GIT_FETCH_TIMEOUT, env=env)
            if code != 0:
                errors.append(_error_line(err))
        return errors[0] if errors else None

    def _range(self, sha, parents):
        """Return the diff-tree arguments that compare `sha` with its first parent, as the forge does."""
        if parents is None:
            out = self._git("rev-list", "--parents", "-n", "1", sha)
            if out is None:
                return None
            parents = out.split()[1:]
        return [parents[0], sha] if parents else ["--root", sha]

    def diff(self, sha, parents=None):
        """Return the `git diff` text of one commit, the same text as Gitea's `.diff` route, or None."""
        span = self._range(sha, parents)
        return None if span is None else self._git("diff-tree", "-r", "-M", "-p", "--no-commit-id", *span)

    def files(self, sha, parents=None):
        """Return one commit's files in the shape of GitHub's `commits/{sha}` `files`, or None.

        Each file has `filename`, `status`, `additions`, `deletions`, and `patch` when the diff
        has a hunk. A renamed or copied file has `previous_filename`. A binary file counts 0 and
        0, as GitHub counts it. The list stops at MAX_COMMIT_FILES, like GitHub's first page.
        """
        span = self._range(sha, parents)
        if span is None:
            return None
        names = self._git("diff-tree", "-r", "-M", "-z", "--no-commit-id", "--name-status", *span)
        stats = self._git("diff-tree", "-r", "-M", "-z", "--no-commit-id", "--numstat", *span)
        # GitHub joins two hunks that one unchanged line keeps apart; git's default keeps them apart.
        # Measured against 185 files of this repository's own commits (references/timeline.md).
        text = self._git("diff-tree", "-r", "-M", "-p", "--inter-hunk-context=1", "--no-commit-id", *span)
        if names is None or stats is None or text is None:
            return None
        entries, tokens = [], names.split("\0")
        i = 0
        while i < len(tokens) and tokens[i]:
            code = tokens[i]
            if code[0] in "RC":
                entries.append((code[0], tokens[i + 1], tokens[i + 2]))
                i += 3
            else:
                entries.append((code[0], None, tokens[i + 1]))
                i += 2
        counts, tokens = [], stats.split("\0")
        i = 0
        while i < len(tokens) and tokens[i]:
            adds, dels, path = tokens[i].split("\t", 2)
            i += 3 if path == "" else 1
            counts.append((int(adds) if adds.isdigit() else 0, int(dels) if dels.isdigit() else 0))
        # Every file of the diff has one `diff --git` header, in the same order as the lists above.
        # A content line never starts with it: each one starts with "+", "-", " " or "\".
        chunks = re.split(r"^diff --git ", text, flags=re.M)[1:]
        if not len(entries) == len(counts) == len(chunks):
            return None
        files = []
        for (code, old, path), (adds, dels), chunk in list(zip(entries, counts, chunks))[:MAX_COMMIT_FILES]:
            entry = {"filename": path, "status": GIT_STATUS.get(code, "modified"), "additions": adds, "deletions": dels}
            if old is not None:
                entry["previous_filename"] = old
            hunk = re.search(r"^@@", chunk, re.M)
            if hunk:
                entry["patch"] = chunk[hunk.start():].rstrip("\n")
            files.append(entry)
        return files


class Cache:
    """Keeps the result of one read per key in a directory, as one JSON file per key.

    A file holds {"cache_version", "key", "value"}. `get` returns None for a miss: no file, a file
    that does not parse, another `cache_version`, or another key. A value is never None. Every
    write goes to a temporary file in the same directory and then replaces the entry in one
    rename. So two collects that write the same key leave one whole entry, the last one written.

    `read=False` makes every `get` and `has` a miss. `write=False` makes `put` do nothing.
    `Cache(None)` does neither. `hits` counts the `get` calls that found an entry.
    """

    def __init__(self, root, read=True, write=True):
        self.root = root
        self.read = read and root is not None
        self.write = write and root is not None
        self.hits = 0
        self._lock = threading.Lock()

    def path_of(self, key):
        """Return the file of `key`. The name is a hash, so any key is a safe file name."""
        digest = hashlib.sha256(key.encode()).hexdigest()
        return os.path.join(self.root, digest[:2], digest + ".json")

    def peek(self, key):
        """Return the value of `key`, or None for a miss. It does not count as a hit.

        A found entry gets its file time set to now, so `gc` removes only the entries that no
        collect used for GC_MAX_AGE.
        """
        if not self.read or key is None:
            return None
        path = self.path_of(key)
        try:
            with open(path, encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("cache_version") != CACHE_VERSION or entry.get("key") != key:
            return None
        try:
            os.utime(path)
        except OSError:
            pass
        return entry.get("value")

    def has(self, key):
        """Return True when `get(key)` would find an entry. It does not count as a hit."""
        return self.peek(key) is not None

    def get(self, key):
        value = self.peek(key)
        if value is not None:
            with self._lock:
                self.hits += 1
        return value

    def put(self, key, value):
        """Store `value` under `key`. A failed write leaves the old entry; the collect goes on."""
        if not self.write or key is None or value is None:
            return
        path = self.path_of(key)
        tmp = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"cache_version": CACHE_VERSION, "key": key, "value": value}, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    def cached(self, key, read, refresh=False, keep=None):
        """Return a callable that gives the entry of `key`, or runs `read` and stores its result.

        A `key` of None turns the cache off for this read. `refresh=True` runs `read` even when
        an entry exists, and stores the new result. `keep(value)`, when given, must be True for
        the result to be stored.
        """
        if key is None:
            return read

        def go():
            if not refresh:
                value = self.get(key)
                if value is not None:
                    return value
            value = read()
            if keep is None or keep(value):
                self.put(key, value)
            return value
        return go


# The cache keys. A read whose key holds no updated_at (or no sha) is never cached.
def issue_key(forge_type, repo, number, updated_at):
    return f"{forge_type}/{repo}/issue/{number}/{updated_at}" if updated_at else None


def pull_key(forge_type, repo, pr):
    updated, sha = pr.get("updated_at"), (pr.get("head") or {}).get("sha")
    return f"{forge_type}/{repo}/pull/{pr['number']}/{updated}/{sha}" if updated and sha else None


def commit_key(forge_type, sha):
    """A commit never changes, so its key holds only the sha. The forge sets the value's shape."""
    return f"{forge_type}/commit/{sha}" if sha else None


def jobs_key(forge_type, repo, run):
    updated = run.get("updated_at") or run.get("completed_at")
    return f"{forge_type}/{repo}/run/{run.get('id')}/{updated}/jobs" if updated and run.get("id") is not None else None


def runs_may_change(pr, now):
    """Return True when a PR's CI runs can change while its updated_at stays the same.

    A run on an open PR starts and ends without a PR event. A run that a merge or a close
    started can still be going, so a PR updated in the last RUNS_STALE_AFTER counts too.
    """
    if pr.get("state") == "open":
        return True
    if not pr.get("updated_at"):
        return True
    return _parse_dt(pr["updated_at"]) >= now - RUNS_STALE_AFTER


def runs_ended(runs):
    """Return True when every run in `runs` has ended, so the list can go into the cache.

    A run that is queued or in progress ends with no change to the PR's updated_at. A stored
    list that holds one would show it as running for as long as the entry lives. Both forges
    give an ended run the status `completed`.
    """
    return all(isinstance(run, dict) and run.get("status") == "completed" for run in runs or [])


def _cached_commit_reader(cache, forge_type, read_commit):
    """Return a `read_commit` that asks the cache first, then `read_commit` (or the API read)."""
    def read(commit, api_read):
        inner = (lambda: read_commit(commit, api_read)) if read_commit else api_read
        return cache.cached(commit_key(forge_type, commit.get("sha")), inner)()
    return read


def _pull_reads(api, repo, pr):
    """Return the first-wave reads of one PR, as {name: callable}. None depends on another read."""
    n = pr["number"]
    reads = {
        "timeline": lambda: api.paged(api.path(repo, f"/issues/{n}/timeline")),
        "reviews": lambda: api.paged(api.path(repo, f"/pulls/{n}/reviews")),
    }
    if api.forge_type == "github":
        reads["commits"] = lambda: api.paged(api.path(repo, f"/pulls/{n}/commits"))
        reads["review_comments"] = lambda: api.paged(api.path(repo, f"/pulls/{n}/comments"))
    else:
        reads["commits"] = lambda: api.paged(api.path(repo, f"/pulls/{n}/commits?verification=false"))
    sha = (pr.get("head") or {}).get("sha")
    if sha:
        def list_runs():
            try:
                return api.paged(api.path(repo, f"/actions/runs?head_sha={sha}"), key="workflow_runs")
            except FetchError as exc:
                # A repository with Actions off answers 404. Any other failure is a real one.
                if _not_found(exc):
                    return []
                raise
        reads["runs"] = list_runs
    return reads


def _pull_followups(api, repo, pr, first, read_commit=None, cache=None):
    """Return the second-wave reads of one PR, as a list of (apply, callable).

    These need the first wave: a commit's files or diff, a review's comments, a failed run's jobs.
    `apply(value)` stores the result into `first`, so a read's place in the document never
    depends on when it ends. `read_commit(commit, api_read)`, when given, reads a commit's files
    or diff and calls `api_read()` only for a commit it cannot read. `cache` keeps a review's
    comments under the PR's key and a run's jobs under the run's key.
    """
    n = pr["number"]
    github = api.forge_type == "github"
    cache = cache or Cache(None)
    base = pull_key(api.forge_type, repo, pr)
    reads = []
    for commit in first["commits"]:
        key = "files" if github else "diff"
        if github:
            read = lambda c=commit: api.get(api.path(repo, f"/commits/{c['sha']}")).get("files") or []
        else:
            read = lambda c=commit: api.text(api.path(repo, f"/git/commits/{c['sha']}.diff"))
        if read_commit is not None:
            read = lambda c=commit, api_read=read: read_commit(c, api_read)
        reads.append((lambda value, c=commit, k=key: c.__setitem__(k, value), read))
    if not github:
        first["review_comments"] = []
        for review in first["reviews"]:
            if review.get("comments_count"):
                read = lambda r=review: api.paged(api.path(repo, f"/pulls/{n}/reviews/{r['id']}/comments"))  # noqa: E731
                key = f"{base}/review/{review.get('id')}/comments" if base else None
                reads.append((first["review_comments"].extend, cache.cached(key, read)))
    limit = "?per_page=100" if github else "?limit=50"
    first["runs"] = [{"run": run, "jobs": []} for run in first.get("runs") or []]
    for entry in first["runs"]:
        if entry["run"].get("conclusion") in RUN_FAILED:
            def read_jobs(r=entry["run"]):
                try:
                    return api.get(api.path(repo, f"/actions/runs/{r['id']}/jobs") + limit).get("jobs") or []
                except FetchError as exc:
                    if _not_found(exc):
                        return []
                    raise
            reads.append((lambda value, e=entry: e.__setitem__("jobs", value),
                          cache.cached(jobs_key(api.forge_type, repo, entry["run"]), read_jobs)))
    return reads


def _git_commit_reader(git, prs, firsts, forge_type, mode, fallbacks):
    """Fetch the PR heads that hold a commit git lacks. Return (read_commit, local, error).

    One `git fetch` runs per PR repository, and only for the PRs that need it. `local` is the
    set of shas git has after the fetch, for the wave-3 estimate. `error` is the first line of
    the first failed fetch, or None. Each commit the reader cannot take from git is read through
    the API and its sha is added to `fallbacks`. With `mode` `git`, such a commit fails the
    collect instead.
    """
    shas = [c.get("sha") for first in firsts for c in first["commits"]]
    local = git.known(shas)
    need = {}
    for (pr_repo_name, pr), first in zip(prs, firsts):
        if any(c.get("sha") not in local for c in first["commits"]):
            need.setdefault(pr_repo_name, []).append(pr["number"])
    errors = [git.fetch(pr_repo_name, numbers) for pr_repo_name, numbers in need.items()]
    if need:
        local = git.known(shas)
    github = forge_type == "github"

    def read_commit(commit, api_read):
        sha = commit.get("sha")
        if sha in local:
            parents = [p.get("sha") for p in commit["parents"]] if isinstance(commit.get("parents"), list) else None
            value = git.files(sha, parents) if github else git.diff(sha, parents)
            if value is not None:
                return value
        if mode == "git":
            raise FetchError(f"commit {sha} is not in the local repository at {git.repo_path} (--diff-source git)")
        fallbacks.append(sha)
        return api_read()
    return read_commit, local, next((e for e in errors if e), None)


def pr_repo_list(repo, values):
    """Return the repositories to read PRs from: the issue repo first, then each --pr-repo value.

    A value may be a comma list. Repeats are dropped; order is kept.
    """
    repos = [repo]
    for value in values or []:
        for item in str(value).split(","):
            item = item.strip()
            if item and item not in repos:
                repos.append(item)
    return repos


def reset_text(reset):
    """Return the sentence that names the quota's reset time, from a Unix time or None."""
    if reset is None:
        return "The reset time is unknown."
    at = datetime.fromtimestamp(reset, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return f"The quota resets at {at} (in {max(0, int(-(-(reset - time.time()) // 60)))} min)."


class QuotaGuard:
    """Stops a collect before its reads take more than `budget` percent of the API quota.

    The quota is read once before the first read. Before each wave, `check(estimate)` adds the
    calls already sent to the wave's estimate, and raises QuotaRefused when the sum is more than
    the budget allows. A listing call counts in full even when the forge answers it with 304,
    because the answer is not known in advance. With no quota to read (Gitea, or a GitHub server
    with rate limits off) or no budget, the guard is off.
    """

    def __init__(self, api, budget):
        self.api = api
        self.budget = budget
        self.start = api.quota() if budget is not None else None
        self.sent_before = api.sent

    def check(self, estimate):
        if self.start is None:
            return
        spent = self.api.sent - self.sent_before
        allowed = int(self.start["remaining"] * self.budget / 100) - spent
        if estimate > allowed:
            raise QuotaRefused(
                f"collect: stopped before the next stage of reads: that stage needs about "
                f"{_plural(estimate, 'API call')}, and --budget {self.budget:g} allows {max(0, allowed)} of the "
                f"{max(0, self.start['remaining'] - spent)} that remain. The estimate covers the next stage only; "
                f"later stages are checked as they come. {reset_text(self.start['reset'])} Use a shorter --since, "
                "a higher --budget, or wait for the reset.")

    def remaining(self):
        """Return the quota left after the run (Q), or None when the guard is off or the read fails.

        The reads are done by then, so a failed quota read only leaves Q out of the summary.
        """
        if self.start is None:
            return None
        try:
            quota = self.api.quota()
        except FetchError:
            return None
        return quota["remaining"] if quota else None


def pull_read_estimate(api, forge_type, repo, issue_numbers, raws, prs, cache, now):
    """Return the calls that wave 2 sends: two per uncached paged read of an issue or a PR."""
    calls = sum(PAGED_READ_CALLS for n in issue_numbers
                if not cache.has(issue_key(forge_type, repo, n, raws[n].get("updated_at"))))
    for pr_repo_name, pr in prs:
        base = pull_key(forge_type, pr_repo_name, pr)
        for name in _pull_reads(api, pr_repo_name, pr):
            fresh = name == "runs" and runs_may_change(pr, now)
            if base is None or fresh or not cache.has(f"{base}/{name}"):
                calls += PAGED_READ_CALLS
    return calls


def followup_estimate(forge_type, prs, firsts, cache, diff_source, git_has=None):
    """Return the calls that wave 3 sends for the uncached reads.

    An uncached commit costs one call with `diff_source` `api`. With `auto` it costs one call
    when its sha is not in `git_has`, the shas git has after the fetch. With `git` it costs
    none: a commit git lacks fails the collect. A Gitea review's comments cost a paged read. A
    failed run's jobs cost one call. `firsts` holds the wave-2 results, with `runs` still the
    raw run list.
    """
    calls = 0
    git_has = git_has or set()
    for (pr_repo_name, pr), first in zip(prs, firsts):
        if diff_source != "git":
            calls += sum(1 for c in first.get("commits") or [] if not cache.has(commit_key(forge_type, c.get("sha")))
                         and (diff_source == "api" or c.get("sha") not in git_has))
        base = pull_key(forge_type, pr_repo_name, pr)
        if forge_type != "github":
            for review in first.get("reviews") or []:
                key = f"{base}/review/{review.get('id')}/comments" if base else None
                if review.get("comments_count") and not cache.has(key):
                    calls += PAGED_READ_CALLS
        for run in first.get("runs") or []:
            if run.get("conclusion") in RUN_FAILED and not cache.has(jobs_key(forge_type, pr_repo_name, run)):
                calls += 1
    return calls


def fetch_bundle(forge_type, repo, pr_repo=None, repo_path=".", since=None, only_issue=None, jobs=DEFAULT_JOBS,
                 diff_source="auto", cache=None, stats=None, budget=None):
    """Read the issues of `repo` and the PRs of `repo` plus every `pr_repo` (a string, or a list of strings).

    The reads go in waves: the listings, then each issue's and PR's own reads, then the reads that
    need those results (a commit's files, a review's comments, a failed run's jobs). A wave runs
    on up to `jobs` threads. Every result goes where its place in the plan says, so `jobs` never
    changes the document.

    `diff_source` says where a commit's files or diff come from: `api` reads each one through the
    forge; `git` reads each one from the checkout at `repo_path` and fails on a commit it does
    not have; `auto` reads from git and falls back to the API per commit.

    `cache` (a `Cache`) serves every read but the listings when its key matches. The commit cache
    comes before git, so the fetch runs only for PRs that hold an uncached commit. On GitHub each
    listing page is a conditional request (`read_listing`) when a cache is on. `stats`, a dict, gets
    `calls` (requests sent), `cached` (reads served by the cache) and `not_modified` (requests that
    got a 304).

    `budget`, a percent, turns the quota guard on (`QuotaGuard`): before each wave it estimates
    the uncached calls the wave sends and raises QuotaRefused when they do not fit. A listing page
    counts as one call even when it may get a 304. `stats` then also gets `remaining`, the quota
    read after the run.
    """
    api = Api(forge_type, repo_path, jobs)
    guard = QuotaGuard(api, budget)
    cache = cache or Cache(None)
    now = datetime.now(timezone.utc)
    owner, _, name = repo.partition("/")
    forge = {"type": forge_type, "host": None, "owner": owner, "repo": name}
    pr_repos = pr_repo_list(repo, [pr_repo] if isinstance(pr_repo, str) else pr_repo)
    if len(pr_repos) > 1:
        forge["pr_repos"] = pr_repos[1:]
    base_q = "?state=all" + ("" if forge_type == "github" else "&type=issues")

    def recent(page):
        return since is None or any((p.get("updated_at") or "") >= since for p in page)

    pr_query = "?state=all" + ("&sort=updated&direction=desc" if forge_type == "github" else "")
    keep_going = recent if forge_type == "github" else None

    def listing(path, keep_going=None):
        # Gitea's API sends no ETag, so its listings are read plain. With no cache, no If-None-Match.
        if forge_type != "github" or not (cache.read or cache.write):
            return api.paged(path, keep_going=keep_going)
        return read_listing(api, cache, path, keep_going)

    # Wave 1: every listing.
    listings = []
    if only_issue is not None:
        listings.append(lambda: api.get(api.path(repo, f"/issues/{only_issue}")))
    else:
        query = base_q + (f"&since={quote(api_since(since))}" if since else "")
        listings.append(lambda: listing(api.path(repo, "/issues" + query)))
        # Tracking and status issues carry batch membership and convergence data even
        # when they were not updated in the range.
        for label in ("type:batch", "type:epic", "flow:status"):
            listings.append(lambda label=label: listing(api.path(repo, "/issues" + base_q + f"&labels={quote(label)}")))
    for pr_repo_name in pr_repos:
        listings.append(lambda r=pr_repo_name: listing(api.path(r, "/pulls" + pr_query), keep_going=keep_going))
    guard.check(PAGED_READ_CALLS * len(listings) - (PAGED_READ_CALLS - 1 if only_issue is not None else 0))
    results = api.pmap(lambda read: read(), listings)
    raws = {}
    if only_issue is not None:
        raws[only_issue] = results[0]
        pr_results = results[1:]
    else:
        for i, rows in enumerate(results[:4]):
            for raw in rows:
                updated = to_utc(raw.get("updated_at"))
                # The range listing asked from the start of the hour; the exact --since filters here.
                if i == 0 and since and updated and updated < since:
                    continue
                if "pull_request" not in raw:
                    raws.setdefault(raw["number"], raw)
        pr_results = results[4:]
    issue_numbers = sorted(raws)
    prs = []
    for pr_repo_name, rows in zip(pr_repos, pr_results):
        for pr in rows:
            if since and to_utc(pr.get("updated_at")) and to_utc(pr["updated_at"]) < since:
                continue
            prs.append((pr_repo_name, pr))
    # gc reads these records to find the PRs that closed long ago, whose refs/issue-flow/ refs can go.
    for pr_repo_name, pr in prs:
        key = pull_key(forge_type, pr_repo_name, pr)
        if key and not cache.has(f"{key}/state"):
            cache.put(f"{key}/state", {k: pr.get(k) for k in ("state", "closed_at", "updated_at")})

    # Wave 2: each issue's timeline and each PR's own reads.
    guard.check(pull_read_estimate(api, forge_type, repo, issue_numbers, raws, prs, cache, now))
    tasks = [cache.cached(issue_key(forge_type, repo, n, raws[n].get("updated_at")),
                          lambda n=n: api.paged(api.path(repo, f"/issues/{n}/timeline"))) for n in issue_numbers]
    names = []
    for pr_repo_name, pr in prs:
        reads = _pull_reads(api, pr_repo_name, pr)
        base = pull_key(forge_type, pr_repo_name, pr)
        names.append(list(reads))
        # The runs entry is written again on each re-read, so a later hit holds the newest runs.
        # A list that holds a run still going is not written: no PR event says when it ends.
        tasks.extend(cache.cached(base and f"{base}/{name}", read, refresh=name == "runs" and runs_may_change(pr, now),
                                  keep=runs_ended if name == "runs" else None)
                     for name, read in reads.items())
    results = iter(api.pmap(lambda read: read(), tasks))
    timelines = [next(results) for _ in issue_numbers]
    firsts = [{key: next(results) for key in keys} for keys in names]

    # The fetch comes before the wave-3 check, so the estimate counts the commits git still lacks.
    read_commit, fallbacks, git_has, fetch_error = None, [], None, None
    if diff_source != "api":
        uncached = [{"commits": [c for c in first["commits"] if not cache.has(commit_key(forge_type, c.get("sha")))]}
                    for first in firsts]
        read_commit, git_has, fetch_error = _git_commit_reader(GitDiffs(repo_path), prs, uncached, forge_type,
                                                               diff_source, fallbacks)
    guard.check(followup_estimate(forge_type, prs, firsts, cache, diff_source, git_has))
    read_commit = _cached_commit_reader(cache, forge_type, read_commit)

    # Wave 3: the reads that need wave 2.
    followups = []
    for (pr_repo_name, pr), first in zip(prs, firsts):
        followups.extend(_pull_followups(api, pr_repo_name, pr, first, read_commit, cache))
    for (apply, _), value in zip(followups, api.pmap(lambda item: item[1](), followups)):
        apply(value)
    if fallbacks:
        print(f"collect: read {_plural(len(fallbacks), 'commit')} through the API: not in the local repository at "
              f"{repo_path}" + (f" (git fetch: {fetch_error})" if fetch_error else ""), file=sys.stderr)

    if stats is not None:
        stats.update(calls=api.sent, cached=cache.hits, not_modified=api.not_modified)
        stats["remaining"] = guard.remaining()
    issues = [{"issue": raws[n], "timeline": timeline} for n, timeline in zip(issue_numbers, timelines)]
    pulls = []
    for (pr_repo_name, pr), first in zip(prs, firsts):
        pulls.append({"repo": pr_repo_name, "pr": pr, "timeline": first["timeline"], "commits": first["commits"],
                      "reviews": first["reviews"], "review_comments": first.get("review_comments", []),
                      "runs": first.get("runs", [])})
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
    # An agent event inherits the issue of the agent's `agent.spawned`, the only event that reads the brief.
    # Without that, one agent would draw as one lane per issue value, in two batch groups.
    issue_of, agent_issue = {}, {}
    for row in sorted(rows, key=lambda r: r["ts"]):
        branch = ISSUE_BRANCH.match(row.get("branch") or "")
        if isinstance(row.get("issue"), int):
            row_issue = row["issue"]
        else:
            row_issue = int(branch.group(1)) if branch else issue_of.get(row.get("worktree"))
            if row_issue is None:
                row_issue = agent_issue.get(row.get("agent"))
        row["issue"] = row_issue
        if row.get("kind") == "agent.spawned" and row_issue is not None and row.get("agent"):
            agent_issue[row["agent"]] = row_issue
        if row_issue is not None and row.get("worktree"):
            issue_of[row["worktree"]] = row_issue
    for row in rows:
        if (since and row["ts"] < since) or (until and row["ts"] > until):
            continue
        issue = row["issue"]
        batch = member_of.get(issue)
        if (only_issue is not None and issue != only_issue) or (only_batch is not None and batch != only_batch):
            continue
        event = _event(row["ts"], row["kind"], issue=issue, batch=batch)
        event.update({key: row.get(key) for key in LOCAL_KEYS})
        doc["events"].append(event)
    doc["events"].sort(key=lambda e: (e["ts"], e["kind"], e["issue"] or 0, e["pr"] or 0, e.get("repo") or ""))
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


# The counts of the last collect in this process, for the summary line of `snapshot` and `watch`.
# `cache_root` and `repo_path` tell `snapshot` where its light gc runs (None: no cache was written).
LAST_COLLECT = {"calls": 0, "cached": 0, "not_modified": 0, "remaining": None, "cache_root": None, "repo_path": "."}


def open_cache(args):
    """Return the Cache this collect uses. --no-cache and --fixture use none.

    The default directory is <git common dir>/issue-flow/cache of --repo-path, which every
    worktree shares and no commit carries. Outside a git repository there is no default cache.
    """
    if args.fixture or args.no_cache:
        return Cache(None)
    root = args.cache_dir
    if not root:
        try:
            root = common_dir_path("cache", args.repo_path)
        except (RuntimeError, OSError):
            return Cache(None)
    return Cache(root, read=not args.refresh_cache)


def calls_summary():
    text = (f"{_plural(LAST_COLLECT['calls'], 'API call')} ({LAST_COLLECT['cached']} cached, "
            f"{LAST_COLLECT['not_modified']} not modified)")
    # Q, the quota left after the run (#93). A forge with no quota to read has none.
    if LAST_COLLECT.get("remaining") is not None:
        text += f", {LAST_COLLECT['remaining']} remaining"
    return text


def cmd_collect(args):
    LAST_COLLECT.update(calls=0, cached=0, not_modified=0, remaining=None, cache_root=None, repo_path=args.repo_path)
    try:
        since = parse_when(args.since) if args.since else None
        until = parse_when(args.until) if args.until else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    if args.max_diff_bytes < 0:
        print("error: --max-diff-bytes must be 0 or more", file=sys.stderr)
        return 5
    if not 1 <= args.jobs <= MAX_JOBS:
        print(f"error: --jobs takes a number from 1 to {MAX_JOBS}", file=sys.stderr)
        return 5
    if not 0 < args.budget <= 100:
        print("error: --budget takes a percent more than 0 and at most 100", file=sys.stderr)
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
            cache = open_cache(args)
            LAST_COLLECT["cache_root"] = cache.root if cache.write else None
            bundle = fetch_bundle(args.forge, args.repo, args.pr_repo, args.repo_path, since, args.issue, args.jobs,
                                  args.diff_source, cache, LAST_COLLECT, args.budget)
        doc = normalize(bundle, since, until, args.issue, args.batch, not args.no_diffs, args.max_diff_bytes)
        log = local_events_path(args)
        if log:
            merge_local(doc, read_local_events(log), since, until, args.issue, args.batch)
    except QuotaRefused as exc:
        print(exc, file=sys.stderr)
        return 3
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
# by adding a row. `bar` lanes draw a coloured status bar. `span` lanes draw one bar from the first event to the last stop. `marks` lanes draw one marker per
# event. A kind that has no row still renders, as a generic `marks` lane.
LANE_KINDS = {
    "issue": {"label": "Issue", "style": "bar"},
    "batch": {"label": "Batch", "style": "marks"},
    "worktree": {"label": "Worktree", "style": "marks"},
    "agent": {"label": "Agent", "style": "span"},
}
# A `span` lane colours its bar by the last `outcome` of its events, through a state colour.
# An outcome that is null or not listed here uses the neutral `ready` grey.
AGENT_OUTCOME_STATES = {"ready-to-merge": "merged", "checkpoint": "in-progress", "needs-feedback": "needs-feedback",
                        "blocked": "blocked"}
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


# Marker keys that show a problem. A lane with one of these in view is a "lane with problems".
PROBLEM_MARKS = ("rework", "review", "ci")
PROBLEM_TYPES = {"comment": "Rework requested", "review": "Changes requested", "ci.run": "CI failed"}
# How a status counts in the working-versus-waiting split. A status in no class (ready, batched,
# deploying) still shows in the per-status totals.
STATUS_CLASSES = {"working": ("in-progress",), "waiting": ("in-review", "awaiting-review", "needs-feedback"), "parked": ("blocked",)}


def _range_end(doc, now):
    """Return the moment open intervals and open problems run to: the range end, else `now`."""
    until = (doc.get("range") or {}).get("until")
    return _parse_dt(until) if until else (now or datetime.now(timezone.utc))


def problem_rows(doc, now=None):
    """Return one row per problem, open ones first (oldest first), then resolved ones (newest first).

    `seconds` is the age of an open problem, or the time from a resolved problem to its first fix commit.
    """
    end = _range_end(doc, now)
    commits = {e.get("sha"): e for e in doc.get("events") or [] if e.get("kind") == "commit" and e.get("sha")}
    rows = []
    for key, info in (doc.get("issues") or {}).items():
        for problem in info.get("problems") or []:
            opened = _parse_dt(problem["ts"])
            fixes = [{"sha": sha, "url": (commits.get(sha) or {}).get("url"), "subject": (commits.get(sha) or {}).get("subject")}
                     for sha in problem.get("resolved_by") or []]
            fixed_at = [_parse_dt(commits[s]["ts"]) for s in problem.get("resolved_by") or [] if s in commits]
            if problem.get("resolved"):
                seconds = max(0, int((min(fixed_at) - opened).total_seconds())) if fixed_at else None
            else:
                seconds = max(0, int((end - opened).total_seconds()))
            rows.append({"id": problem["id"], "issue": str(key), "lane": f"issue:{key}", "label": f"#{key}", "title": info.get("title"),
                         "url": problem.get("url"), "source": problem["source"], "type": PROBLEM_TYPES.get(problem["source"], "Problem"),
                         "text": (problem.get("text") or "").strip().split("\n")[0], "ts": problem["ts"],
                         "resolved": bool(problem.get("resolved")), "seconds": seconds, "fixes": fixes})
    open_rows = sorted((r for r in rows if not r["resolved"]), key=lambda r: r["ts"])
    resolved_rows = sorted((r for r in rows if r["resolved"]), key=lambda r: r["ts"], reverse=True)
    return open_rows + resolved_rows


def time_in_status(doc, now=None):
    """Return {issue: {by_status, by_class, open}} in whole seconds, summed from the status events.

    Each `status` event starts an interval that a later status, a merge, a close or a label removal ends.
    The last open interval runs to the range end, else to `now`. Merged and closed time is not a status.
    """
    end = _range_end(doc, now)
    steps, has_status = {}, set()
    for event in sorted(doc.get("events") or [], key=lambda e: e["ts"]):
        if event.get("issue") is None:
            continue
        kind = event.get("kind")
        state = event.get("to") if kind == "status" else None if kind in ("pr.merged", "issue.closed", "issue.reopened") else False
        if state is not False:
            steps.setdefault(str(event["issue"]), []).append((_parse_dt(event["ts"]), state))
            if kind == "status":
                has_status.add(str(event["issue"]))
    result = {}
    for key, rows in steps.items():
        if key not in has_status:
            continue
        by_status = {}
        for index, (start, state) in enumerate(rows):
            stop = min(rows[index + 1][0], end) if index + 1 < len(rows) else end
            if state and stop > start:
                by_status[state] = by_status.get(state, 0) + int((stop - start).total_seconds())
        by_class = {name: sum(by_status.get(s, 0) for s in states) for name, states in STATUS_CLASSES.items()}
        result[key] = {"by_status": by_status, "by_class": by_class, "open": rows[-1][1]}
    return result


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
            # The group heading already names the batch, so the lane names what it holds.
            lane_id, label, url, title = f"batch:{batch}", "Batch activity", None, None
        elif kind == "issue" and issue is not None:
            batch = batch or member_of.get(issue)
            info = issues.get(issue) or {}
            lane_id, label, url, title = f"issue:{issue}", f"#{issue}", info.get("url"), info.get("title")
        else:
            # A lane names its own key: agent.stopped carries a worktree too, and must stay on its agent's lane.
            keys = {"agent": ("agent", "session"), "worktree": ("worktree",)}.get(kind, ("worktree", "agent", "session"))
            ident = next((event[key] for key in keys if event.get(key)), "")
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


def _plural(count, word):
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _convergence_strip(rows):
    if not rows:
        return ""
    cells = []
    for row in rows:
        filed, closed = row.get("filed"), row.get("closed")
        filed_text = "filed count not recorded" if filed is None else _plural(filed, "issue") + " filed"
        closed_text = "closed count not recorded" if closed is None else "none closed yet" if closed == 0 else f"{closed} closed"
        cells.append(f'<li>Gate {html.escape(str(row.get("gate")))}: {html.escape(filed_text)}, {html.escape(closed_text)}</li>')
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
    "batched": ("#8c959f", "#6e7681"),
    "deploying": ("#1b7c83", "#39c5cf"),
    "deploy-failed": ("#8a1c2b", "#ff9492"),
    "merged": ("#1a7f37", "#3fb950"),
    "closed": ("#24292f", "#c9d1d9"),
}
# A marker is a glyph inside a 2px ring of this color. No ring color equals or sits near a state
# color (CIE76 distance of at least 20, a test enforces it), so a ring is never read as a bar state.
MARKER_COLORS = {
    "pr": ("#4e94da", "#1588c1"),
    "merge": ("#12a112", "#88f2bd"),
    "batch": ("#2d4a86", "#bd9cde"),
    "cp": ("#c9825e", "#f2bd88"),
    "park": ("#e65e19", "#c1154f"),
    "rework": ("#e17083", "#e619e5"),
    "fix": ("#1919e6", "#c3e619"),
    "ci": ("#e619e5", "#77862d"),
    "review": ("#a112a1", "#f2740d"),
    "note": ("#80931f", "#d9e892"),
    "msg": ("#b77dd4", "#1f936d"),
    "dot": ("#592d86", "#86592d"),
}
MARKER_LABELS = {"pr": "PR opened", "merge": "PR merged", "batch": "Batch merged", "cp": "Checkpoint",
                 "park": "Parked (needs feedback or blocked)", "rework": "Rework requested", "fix": "Fix commit",
                 "ci": "CI run failed", "review": "Changes requested in review", "note": "Cross-check or plan amendment",
                 "msg": "Agent messaged", "dot": "Other event"}
# The legend rows for markers. A marker in neither row would draw without a legend entry.
MARKER_GROUPS = {"events": ("pr", "merge", "batch", "cp", "msg", "note", "dot"),
                 "problems": ("park", "rework", "fix", "ci", "review")}
# Every text and surface color of the page, per theme. A test computes the WCAG contrast of each
# text pair from this table, so a color change that breaks 4.5:1 fails before it ships.
THEME_TOKENS = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "muted": "#59636e", "line": "#d0d7de", "panel": "#f6f8fa",
              "accent": "#0969da", "visited": "#6639ba", "add": "#1a7f37", "del": "#cf222e",
              "add-bg": "#dafbe1", "del-bg": "#ffebe9", "shadow": "rgba(31, 35, 40, .25)"},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "muted": "#9198a1", "line": "#30363d", "panel": "#161b22",
             "accent": "#58a6ff", "visited": "#bc8cff", "add": "#3fb950", "del": "#ff7b72",
             "add-bg": "#12261e", "del-bg": "#3c1618", "shadow": "rgba(1, 4, 9, .6)"},
}
DIFF_TOKENS = ("add", "del", "add-bg", "del-bg")


def _theme_vars(theme):
    """Return the custom properties of one theme: surface tokens, bar colors and marker colors."""
    index = 0 if theme == "light" else 1
    pairs = [f"--{'diff-' if k in DIFF_TOKENS else ''}{k}:{v};" for k, v in THEME_TOKENS[theme].items()]
    pairs.append("--st-none:var(--line);")
    pairs += [f"--st-{k}:{v[index]};" for k, v in STATE_COLORS.items()]
    pairs += [f"--mk-{k}:{v[index]};" for k, v in MARKER_COLORS.items()]
    return " ".join(pairs)


# The only place a color value may appear. LAYOUT_CSS uses var(--token) and nothing else.
TOKEN_CSS = (":root { color-scheme:light; " + _theme_vars("light") + " }\n"
             "@media (prefers-color-scheme: dark) {\n"
             '  :root:not([data-theme="light"]) { color-scheme:dark; ' + _theme_vars("dark") + " } }\n"
             ':root[data-theme="light"] { color-scheme:light; }\n'
             ':root[data-theme="dark"] { color-scheme:dark; ' + _theme_vars("dark") + " }\n")

LAYOUT_CSS = r"""
* { box-sizing:border-box; }
body { margin:0; padding:1rem; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,sans-serif; }
a { color:var(--accent); } a:visited { color:var(--visited); } a:hover { text-decoration-thickness:2px; }
a:focus-visible, button:focus-visible, input:focus-visible, summary:focus-visible, #ptitle:focus-visible
  { outline:2px solid var(--accent); outline-offset:2px; }
#theme { white-space:nowrap; flex:none; }
.top { display:flex; justify-content:space-between; align-items:flex-start; gap:.75rem; }
h1 { font-size:1.25rem; margin:0 0 .25rem; } h2 { font-size:.95rem; margin:0 0 .25rem; }
.sub { color:var(--muted); margin:0 0 .75rem; }
button { font:inherit; color:var(--fg); background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:.25rem .6rem; cursor:pointer; }
button[aria-pressed="true"] { border-color:var(--accent); color:var(--accent); font-weight:600; }
.controls button[aria-pressed="true"]::before, .filters button[aria-pressed="true"]::before { content:"\2713\00a0"; }
.controls { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin-bottom:.75rem; }
.controls input { font:inherit; color:var(--fg); background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.25rem .6rem; }
#lanesearch { min-width:14rem; }
#problemsview { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.5rem .75rem; margin-bottom:.75rem; }
#problemsview > summary { cursor:pointer; font-weight:600; }
#pvcount { color:var(--muted); font-weight:400; }
#pvlist { list-style:none; margin:.5rem 0 0; padding:0; display:grid; gap:.25rem; max-height:15rem; overflow:auto; }
.pv-item { display:flex; flex-wrap:wrap; gap:.15rem .75rem; align-items:baseline; border-left:3px solid var(--diff-del); padding:.1rem .5rem; }
.pv-item.is-resolved { border-left-color:var(--diff-add); }
.pv-row { display:flex; flex-wrap:wrap; gap:.15rem .5rem; align-items:baseline; text-align:left; min-width:0; flex:1 1 24rem; border:0; background:transparent; padding:.15rem .25rem; }
.pv-row:hover { background:var(--bg); }
.pv-text { flex:1 1 14rem; min-width:0; max-height:2.9em; overflow:hidden; overflow-wrap:anywhere; }
.pv-time { color:var(--muted); white-space:nowrap; }
.pv-fixes { color:var(--muted); font-size:.85rem; }
#problems > li.hit { outline:2px solid var(--accent); outline-offset:-2px; }
.stackbar { display:flex; height:.6rem; border-radius:3px; overflow:hidden; border:1px solid var(--line); margin:.25rem 0; }
.stackbar span { display:block; height:100%; } .stackbar span + span { border-left:2px solid var(--bg); }
.sb-working { background:var(--st-in-progress); } .sb-waiting { background:var(--st-in-review); } .sb-parked { background:var(--st-blocked); }
.sblist { display:flex; flex-wrap:wrap; gap:.15rem .9rem; margin:0; padding:0; list-style:none; font-size:.85rem; }
.sblist i { display:inline-block; width:.8rem; height:.6rem; border-radius:2px; margin-right:.3rem; }
.convergence { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.5rem .75rem; margin-bottom:.75rem; }
.convergence ul { list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:.25rem 1.25rem; }
.legend { display:grid; gap:.3rem; color:var(--muted); font-size:.85rem; margin-bottom:.75rem; }
.legend-row { display:flex; flex-wrap:wrap; gap:.25rem 1rem; align-items:center; }
.legend-title { color:var(--fg); font-weight:600; min-width:4.5rem; }
.legend i { display:inline-block; width:.8rem; height:.6rem; border-radius:2px; margin-right:.3rem; vertical-align:baseline; }
#chart { overflow-x:auto; scrollbar-gutter:stable; padding-bottom:.75rem; }
.board { min-width:640px; padding-right:.75rem; }
.axis, .lane { display:grid; grid-template-columns:16rem 1fr; align-items:center; }
.axis .ticks { position:relative; height:1.2rem; color:var(--muted); font-size:.75rem; }
.axis .ticks span { position:absolute; transform:translateX(-50%); white-space:nowrap; }
.axis .ticks span:first-child { transform:none; } .axis .ticks span:last-child { transform:translateX(-100%); }
.group { margin-top:.75rem; font-weight:600; border-bottom:1px solid var(--line); padding-bottom:.15rem; }
.lane { border-bottom:1px solid var(--line); min-height:1.9rem; cursor:pointer; }
.lane .name { display:flex; gap:.4rem; align-items:center; min-width:0; padding:.15rem .5rem .15rem .6rem; }
.lane .name small { color:var(--muted); min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.lane .name a { text-decoration:none; font-weight:600; white-space:nowrap; } .lane .name a:hover { text-decoration:underline; }
.lane-open { padding:0 .45rem; font-size:.75rem; }
.track { position:relative; min-height:1.5rem; }
.seg { position:absolute; top:.55rem; height:.5rem; border-radius:3px; background:var(--st-none); opacity:.9; }
.seg-span { top:.6rem; height:.4rem; min-width:6px; }
.seg-batched { background:transparent; border:2px dashed var(--st-batched); opacity:1; }
.mk { position:absolute; top:.3rem; width:.95rem; height:.95rem; transform:translateX(-50%); border-radius:50%;
  border:2px solid var(--mk-dot); background:var(--bg); color:var(--fg); padding:0; display:inline-flex; align-items:center;
  justify-content:center; font:700 .55rem/1 system-ui,sans-serif; cursor:pointer; }
.mk::before { content:""; }
.mk-pr { border-color:var(--mk-pr); } .mk-pr::before { content:"+"; }
.mk-merge { border-color:var(--mk-merge); } .mk-merge::before { content:"\2713"; }
.mk-batch { border-color:var(--mk-batch); border-radius:3px; } .mk-batch::before { content:"\25C6"; }
.mk-cp { border-color:var(--mk-cp); border-radius:3px; } .mk-cp::before { content:"\25A0"; }
.mk-park { border-color:var(--mk-park); } .mk-park::before { content:"!"; }
.mk-rework { border-color:var(--mk-rework); } .mk-rework::before { content:"\25B2"; }
.mk-fix { border-color:var(--mk-fix); border-radius:3px; } .mk-fix::before { content:"\25CF"; }
.mk-ci { border-color:var(--mk-ci); } .mk-ci::before { content:"\00D7"; font-size:.8rem; }
.mk-review { border-color:var(--mk-review); } .mk-review::before { content:"\25BC"; }
.mk-note { border-color:var(--mk-note); border-radius:3px; } .mk-note::before { content:"i"; }
.mk-msg { border-color:var(--mk-msg); } .mk-msg::before { content:"\203A"; font-size:.8rem; }
.mk-dot { border-color:var(--mk-dot); width:.7rem; height:.7rem; }
.legend .mk { position:static; transform:none; margin:0 .35rem 0 0; cursor:default; vertical-align:middle; }
.lane:hover { background:var(--panel); }
.lane.sel { background:var(--panel); box-shadow:inset 3px 0 0 var(--accent); }
body.has-panel #layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(22rem,34rem); gap:1rem; align-items:start; }
#panel { background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:.75rem; position:sticky; top:.5rem;
  max-height:calc(100vh - 1rem); overflow:auto; min-width:0; }
#panel h3 { font-size:.85rem; margin:.75rem 0 .25rem; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); }
.phead { display:flex; justify-content:space-between; gap:.5rem; align-items:start; }
.phead button, .filters button { padding:.1rem .5rem; }
.filters { display:flex; flex-wrap:wrap; gap:.25rem; margin:.5rem 0; }
#psummary { display:grid; grid-template-columns:repeat(auto-fill,minmax(7rem,1fr)); gap:.35rem; margin:.5rem 0 0; }
#psummary div { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:.25rem .5rem; }
#psummary dt { color:var(--muted); font-size:.75rem; } #psummary dd { margin:0; font-weight:600; }
#psummary .alert { border-color:var(--diff-del); border-left-width:3px; } #psummary .alert dd { color:var(--diff-del); }
#problems, #notes, #log { list-style:none; margin:0; padding:0; }
#problems > li, #notes > li, #log > li { border-left:3px solid var(--line); padding:.25rem .5rem; margin:.35rem 0; overflow-wrap:anywhere; }
#problems > li { border-left-color:var(--mk-rework); }
#log > li.k-commits { border-left-color:var(--mk-fix); } #log > li.k-ci { border-left-color:var(--mk-ci); }
#log > li.k-reviews { border-left-color:var(--mk-review); } #log > li.k-agents { border-left-color:var(--mk-msg); }
.when { color:var(--muted); font-size:.75rem; }
.badge { display:inline-block; font-size:.7rem; line-height:1.3; padding:0 .4rem; border:1px solid var(--line); border-radius:999px; margin-right:.25rem; background:var(--panel); }
.b-fix, .b-open, .b-gate-rework, .b-changes-requested, .b-failure, .b-timed-out, .b-truncated { border-color:var(--diff-del); color:var(--diff-del); }
.b-approved, .b-success, .b-gate-pass, .b-resolved { border-color:var(--diff-add); color:var(--diff-add); }
.pre { white-space:pre-wrap; }
.why { color:var(--muted); font-style:italic; }
details.file { margin:.25rem 0; } details.file > summary { cursor:pointer; font-family:ui-monospace,monospace; font-size:.8rem; }
.add { color:var(--diff-add); } .del { color:var(--diff-del); }
.diff { font:12px/1.4 ui-monospace,monospace; margin:.25rem 0 0; overflow-x:auto; border:1px solid var(--line); border-radius:6px; }
.dl { display:block; white-space:pre; padding:0 .5rem; }
.dl-add { background:var(--diff-add-bg); } .dl-del { background:var(--diff-del-bg); }
.dl-hunk { background:var(--panel); color:var(--muted); } .dl-note { color:var(--muted); font-style:italic; }
@media (max-width:900px) { body.has-panel #layout { display:block; } #panel { position:static; max-height:none; margin-top:.75rem; } }
#tip { position:fixed; z-index:10; max-width:24rem; background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px;
  padding:.4rem .6rem; white-space:pre-line; pointer-events:none; box-shadow:0 2px 8px var(--shadow); }
#empty { color:var(--muted); padding:1rem; border:1px dashed var(--line); border-radius:6px; text-align:center; }
@media (max-width:640px) { .axis, .lane { grid-template-columns:8rem 1fr; } .board { min-width:520px; } .lane .name small { display:none; } }
"""
PAGE_CSS = TOKEN_CSS + LAYOUT_CSS

THEME_KEY = "tl-theme"
# The head script applies a stored theme before first paint, so the page never flashes the wrong one.
# A missing or blocked store leaves the page on the browser's own preference ("auto").
THEME_HEAD_JS = ('(function(){try{var t=localStorage.getItem("' + THEME_KEY + '");'
                 'if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();')

# Pure helpers of the page script. They touch no DOM, so a test runs them alone in node.
VIEW_JS = r"""
  // ---- the shared view lives in the URL hash: lane=..&from=..&to=..&kinds=..&q=..&p=..
  function esc(text) { return encodeURIComponent(text).replace(/%3A/g, ":"); }
  function encodeView(v) {
    var parts = [];
    if (v.lane) { parts.push("lane=" + esc(v.lane)); }
    if (v.from) { parts.push("from=" + esc(v.from)); }
    if (v.to) { parts.push("to=" + esc(v.to)); }
    if (v.kinds) { parts.push("kinds=" + esc(v.kinds.join(","))); }
    if (v.q) { parts.push("q=" + esc(v.q)); }
    if (v.p) { parts.push("p=1"); }
    return parts.join("&");
  }
  function decodeView(text) {
    var view = {lane: null, from: null, to: null, kinds: null, q: "", p: false};
    String(text || "").replace(/^#/, "").split("&").forEach(function (pair) {
      var at = pair.indexOf("=");
      if (at < 1) { return; }
      var key = pair.slice(0, at), value;
      try { value = decodeURIComponent(pair.slice(at + 1)); } catch (e) { return; }
      if (key === "lane" && value) { view.lane = value; }
      else if ((key === "from" || key === "to") && !isNaN(Date.parse(value))) { view[key] = value; }
      else if (key === "kinds") { view.kinds = value === "" ? [] : value.split(","); }
      else if (key === "q") { view.q = value; }
      else if (key === "p" && value === "1") { view.p = true; }
    });
    return view;
  }
  // A search matches the lane label (for example #81) or its title. A leading # in the query is ignored.
  function laneMatches(lane, query) {
    var q = String(query || "").trim().toLowerCase().replace(/^#/, "");
    if (!q) { return true; }
    return ((lane.label || "") + " " + (lane.title || "")).toLowerCase().replace("#", "").indexOf(q) >= 0;
  }
  function fmtDuration(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    if (s === 0) { return "0m"; }
    if (s < 60) { return "<1m"; }
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) { return d + "d" + (h ? " " + h + "h" : ""); }
    if (h) { return h + "h" + (m ? " " + m + "m" : ""); }
    return m + "m";
  }
"""

PAGE_JS = r"""
(function () {
  "use strict";
  var THEME_KEY = /*THEME_KEY*/;
  /*VIEW_JS*/
  var D =JSON.parse(document.getElementById("tl-data").textContent);
  var sel = null;
  var on = {};
  D.logGroups.forEach(function (g) { on[g.key] = true; });
  var times = D.events.map(function (e) { return Date.parse(e.ts); });
  var win = {lo: null, hi: null};
  var query = "", onlyProblems = false, restoring = false;
  var PM = {};
  D.problemMarks.forEach(function (k) { PM[k] = true; });
  var $ = function (id) { return document.getElementById(id); };

  // ---- theme: auto follows the browser, light and dark are explicit. The page works without storage.
  var MODES = ["auto", "light", "dark"];
  var mode = "auto";
  try { var saved = localStorage.getItem(THEME_KEY); if (MODES.indexOf(saved) > 0) { mode = saved; } } catch (e) { mode = "auto"; }
  function applyMode() {
    var root = document.documentElement, next = MODES[(MODES.indexOf(mode) + 1) % MODES.length], button = $("theme");
    if (mode === "auto") { root.removeAttribute("data-theme"); } else { root.setAttribute("data-theme", mode); }
    button.textContent = "Theme: " + mode;
    button.setAttribute("aria-label", "Theme: " + mode + ". Switch to " + next + ".");
  }
  $("theme").addEventListener("click", function () {
    mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* storage is blocked: the choice lasts for this page only */ }
    applyMode();
  });
  applyMode();

  function cls(text) { return String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-"); }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  // Log excerpts hold GitHub markdown. Show the words, not the markup.
  function plain(text) {
    return String(text).replace(/<[!]--[\s\S]*?-->/g, "").replace(/^\s{0,3}#{1,6}\s+/gm, "").replace(/\*\*|__/g, "").replace(/`+/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  }
  function stateAfter(e) {
    if (e.kind === "status") { return e.to || null; }
    if (e.kind === "pr.merged") { return "merged"; }
    if (e.kind === "issue.closed") { return "closed"; }
    if (e.kind === "issue.reopened") { return null; }
    return undefined;
  }
  var CLASS_NAMES = [["working", "Working"], ["waiting", "Waiting"], ["parked", "Parked"]];
  function statusOf(laneId) { return laneId.indexOf("issue:") === 0 ? (D.statusTime || {})[laneId.slice(6)] || null : null; }
  function statusLine(t) {
    return "Time in status: " + CLASS_NAMES.filter(function (c) { return c[0] !== "parked" || t.by_class.parked; })
      .map(function (c) { return c[1].toLowerCase() + " " + fmtDuration(t.by_class[c[0]]); }).join(", ");
  }
  function describe(e, lane) {
    var lines = [e.kind + "  " + e.ts];
    if (lane.title) { lines.push(lane.title); }
    if (e.from || e.to) { lines.push((e.from || "none") + " -> " + (e.to || "none")); }
    if (e.detail) { lines.push(e.detail); }
    if (e.pr) { lines.push("PR " + (e.repo && e.repo !== D.issueRepo ? e.repo : "") + "#" + e.pr); }
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
  // The date shows once, at the first tick and again where the day changes. Every other tick shows HH:MM.
  function tickLabel(ms, prevMs) {
    var full = stamp(ms);
    return prevMs === null || stamp(prevMs).slice(0, 10) !== full.slice(0, 10) ? full : full.slice(11);
  }
  // Draw as many time labels as fit in the axis. Start with seven and drop one until no two overlap.
  function axisTicks(ticks, b) {
    for (var steps = 6; steps >= 1; steps--) {
      ticks.textContent = "";
      var prev = null;
      for (var k = 0; k <= steps; k++) {
        var ms = b.lo + (b.hi - b.lo) * k / steps;
        var tick = el("span", null, tickLabel(ms, prev));
        prev = ms;
        tick.style.left = (100 * k / steps) + "%"; ticks.appendChild(tick);
      }
      var boxes = Array.prototype.map.call(ticks.children, function (n) { return n.getBoundingClientRect(); }), clash = false;
      for (var j = 1; j < boxes.length; j++) { if (boxes[j].left < boxes[j - 1].right + 8) { clash = true; } }
      if (!clash) { return; }
    }
  }
  // The axis is measured after the layout settles: the panel opening or closing changes the chart width.
  var drawn = {ticks: null, bounds: null, width: 0};
  function fitTicks() {
    var chart = $("chart");
    if (!drawn.ticks || !drawn.bounds || !drawn.ticks.isConnected) { return; }
    drawn.width = chart.clientWidth;
    axisTicks(drawn.ticks, drawn.bounds);
  }
  if (window.ResizeObserver) {
    new ResizeObserver(function () { if ($("chart").clientWidth !== drawn.width) { fitTicks(); } }).observe($("chart"));
  }
  var pending = false;
  window.addEventListener("resize", function () {
    if (pending) { return; }
    pending = true;
    window.requestAnimationFrame(function () { pending = false; draw(); });
  });
  function pos(ms, b) { return (100 * (ms - b.lo) / (b.hi - b.lo)).toFixed(3) + "%"; }

  // Markers closer than the gap move to the next row down, so none hides another.
  var MARK = {gap: 16, base: 5, step: 17, rows: 4, track: 26};
  function spread(board) {
    Array.prototype.forEach.call(board.querySelectorAll(".track"), function (track) {
      var marks = Array.prototype.map.call(track.querySelectorAll(".mk"), function (n) { return {n: n, x: n.offsetLeft}; });
      marks.sort(function (a, b) { return a.x - b.x; });
      var last = [], deepest = 0;
      marks.forEach(function (m) {
        var r = 0;
        while (r < MARK.rows - 1 && last[r] !== undefined && m.x - last[r] < MARK.gap) { r++; }
        last[r] = m.x; deepest = Math.max(deepest, r);
        m.n.style.top = (MARK.base + r * MARK.step) + "px";
      });
      track.style.minHeight = (MARK.track + deepest * MARK.step) + "px";
    });
  }

  function laneNode(lane, b, visible) {
    var kind = D.kinds[lane.kind];
    var row = el("div", "lane lane-" + cls(lane.kind) + (sel && sel.id === lane.id ? " sel" : ""));
    var full = lane.label + (lane.title ? " " + lane.title : "");
    row.addEventListener("click", function (ev) { if (!(ev.target.closest && ev.target.closest("a"))) { openLane(lane); } });
    var name = el("div", "name");
    name.setAttribute("data-tip", full);
    var open = el("button", "lane-open", "Details");
    open.type = "button";
    open.setAttribute("data-lane", lane.id);
    open.setAttribute("aria-label", "Details for " + full);
    open.setAttribute("aria-expanded", String(!!(sel && sel.id === lane.id)));
    name.appendChild(open);
    if (lane.url && /^https?:\/\//.test(lane.url)) {
      var link = el("a", null, lane.label);
      link.href = lane.url; link.rel = "noopener";
      name.appendChild(link);
    } else { name.appendChild(el("span", null, lane.label)); }
    if (lane.title) { name.appendChild(el("small", null, lane.title)); }
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
        var seg = el("div", "seg seg-" + cls(p.s));
        seg.style.left = pos(p.t, b); seg.style.width = (100 * (end - p.t) / (b.hi - b.lo)).toFixed(3) + "%";
        if (p.s !== "batched") { seg.style.background = "var(--st-" + cls(p.s) + ", var(--st-none))"; }
        var spent = statusOf(lane.id), tip = p.s + "\nfrom " + stamp(p.t) + " to " + stamp(end) + (lane.title ? "\n" + lane.title : "");
        if (spent) { tip += "\nTotal in " + p.s + ": " + fmtDuration(spent.by_status[p.s]) + "\n" + statusLine(spent); }
        seg.setAttribute("data-tip", tip);
        track.appendChild(seg);
      });
    }
    if (kind.style === "span" && visible.length) {
      var first = visible[0].t, last = first, outcome = null, stopped = false;
      visible.forEach(function (x) {
        var stop = x.e.kind === "agent.stopped";
        if (stop) { outcome = x.e.outcome || null; }
        if (stop || (!stopped && x.t > last)) { last = x.t; stopped = stopped || stop; }
      });
      var tone = (D.outcomeStates || {})[outcome] || "ready";
      var bar = el("div", "seg seg-span");
      bar.style.left = pos(first, b); bar.style.width = (100 * (last - first) / (b.hi - b.lo)).toFixed(3) + "%";
      bar.style.background = "var(--st-" + cls(tone) + ", var(--st-none))";
      bar.setAttribute("data-tip", (outcome || "no outcome recorded") + "\nfrom " + stamp(first) + " to " + stamp(last));
      track.appendChild(bar);
    }
    visible.forEach(function (x) {
      var mark = D.marks[x.i];
      if (!mark || (kind.style === "bar" && mark === "dot") || (kind.style === "span" && x.e.kind === "agent.spawned")) { return; }
      var text = describe(x.e, lane);
      var node = el("button", "mk mk-" + mark);
      node.type = "button";
      node.style.left = pos(x.t, b);
      node.setAttribute("data-tip", text);
      node.setAttribute("aria-label", text.replace(/\n+/g, "; "));
      track.appendChild(node);
    });
    row.appendChild(track);
    return track.children.length ? row : null;
  }

  function draw(keepPanel) {
    var chart = $("chart"), b = bounds();
    chart.textContent = "";
    var shown = 0, inRange = 0;
    drawn.ticks = null; drawn.bounds = null;
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
          if (!visible.length) { return; }
          inRange += visible.length;
          if (!laneMatches(lane, query)) { return; }
          if (onlyProblems && !visible.some(function (x) { return PM[D.marks[x.i]]; })) { return; }
          shown += visible.length;
          var row = laneNode(lane, b, visible);
          if (row) { rows.push(row); }
        });
        if (rows.length) {
          board.appendChild(el("div", "group", group.label));
          rows.forEach(function (r) { board.appendChild(r); });
        }
      });
      chart.appendChild(board);
      drawn.ticks = ticks; drawn.bounds = b;
      fitTicks();
      spread(board);
      window.requestAnimationFrame(fitTicks);
    }
    var empty = $("empty");
    empty.hidden = shown > 0;
    empty.textContent = inRange && !shown ? "No lanes match the search or the problems filter."
      : "No events in this range." + (b && !shown ? " Range: " + stamp(b.lo) + " to " + stamp(b.hi) + " UTC." : "");
    chart.hidden = shown === 0;
    if (!keepPanel) { renderPanel(); }
    problemsView();
    writeHash();
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

  // ---- the tooltip shows on hover and on keyboard focus, and never covers the open panel.
  var tip = $("tip");
  function intersects(a, b) { return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top; }
  function showTip(target, x, y, above) {
    tip.textContent = target.getAttribute("data-tip");
    tip.hidden = false;
    var panel = $("panel"), pr = panel.hidden ? null : panel.getBoundingClientRect();
    var w = tip.offsetWidth, h = tip.offsetHeight, limit = window.innerWidth - 8;
    var tries = [[x, y], [x, above - h - 6]];
    if (pr) { tries.splice(1, 0, [pr.left - w - 8, y], [pr.left - w - 8, above - h - 6]); }
    for (var i = 0; i < tries.length; i++) {
      var left = Math.max(8, Math.min(tries[i][0], limit - w)), top = Math.max(8, Math.min(tries[i][1], window.innerHeight - h - 8));
      tip.style.left = left + "px"; tip.style.top = top + "px";
      if (!pr || !intersects(tip.getBoundingClientRect(), pr)) { return; }
    }
  }
  function hideTip() { tip.hidden = true; }
  document.addEventListener("mousemove", function (ev) {
    var target = ev.target.closest ? ev.target.closest("[data-tip]") : null;
    if (!target) { hideTip(); return; }
    showTip(target, ev.clientX + 12, ev.clientY + 14, ev.clientY - 6);
  });
  document.addEventListener("focusin", function (ev) {
    var target = ev.target.closest ? ev.target.closest("[data-tip]") : null;
    if (!target) { hideTip(); return; }
    var box = target.getBoundingClientRect();
    showTip(target, box.left, box.bottom + 6, box.top);
  });
  document.addEventListener("focusout", hideTip);

  // ---- the drill-down panel. Every string from the data goes in as text, never as markup.
  var COMMITS = {};
  D.events.forEach(function (e) { if (e.kind === "commit" && e.sha) { COMMITS[e.sha] = e; } });
  var LABELS = [["commits", "Commits"], ["files", "Files"], ["additions", "Lines added"], ["deletions", "Lines removed"],
    ["rework_rounds", "Rework rounds"], ["review_rounds", "Review rounds"], ["ci_failures", "CI failures"]];
  var ALERT = {rework_rounds: true, review_rounds: true, ci_failures: true};
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
  // Focus moves into the panel when it opens and back to the lane's button when it closes.
  function openLane(lane, problemId) {
    sel = lane;
    document.body.classList.add("has-panel");
    draw();
    var hit = null;
    if (problemId) {
      Array.prototype.forEach.call($("problems").children, function (li) { if (li.getAttribute("data-problem") === problemId) { hit = li; } });
    }
    if (hit) { hit.classList.add("hit"); hit.setAttribute("tabindex", "-1"); hit.scrollIntoView({block: "start"}); hit.focus({preventScroll: true}); }
    else { $("ptitle").focus(); }
  }
  function findLane(id) {
    var found = null;
    D.groups.forEach(function (g) { g.lanes.forEach(function (l) { if (l.id === id) { found = l; } }); });
    return found;
  }
  function closePanel() {
    var lane = sel;
    sel = null;
    document.body.classList.remove("has-panel");
    draw();
    hideTip();
    Array.prototype.forEach.call(document.querySelectorAll(".lane-open"), function (button) {
      if (lane && button.getAttribute("data-lane") === lane.id) { button.focus(); }
    });
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
      add(li, el("div", "pre", plain(e.excerpt || e.detail || "")), safeUrl(e.url) ? link(e.url, "open") : null);
    } else if (e.kind === "review.comment") {
      add(li, el("div", null, (e.path || "") + (e.line ? ":" + e.line : "") + (e.resolved === true ? " (resolved)" : "")),
        el("div", "pre", plain(e.excerpt || "")), safeUrl(e.url) ? link(e.url, "open") : null);
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
    li.setAttribute("data-problem", p.id);
    var top = el("div", "when", stamp(Date.parse(p.ts)) + " · ");
    top.appendChild(badge(p.source === "ci.run" ? "ci" : p.source));
    if (p.state || p.role) { top.appendChild(badge(p.state || p.role)); }
    top.appendChild(p.resolved ? badge("resolved") : badge("open"));
    li.appendChild(top);
    add(li, el("div", "pre", plain(p.text || "")), safeUrl(p.url) ? link(p.url, "open the " + (p.source === "ci.run" ? "run" : p.source)) : null);
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
  // Time in status: a stacked bar for working, waiting and parked, and the same numbers as text.
  function stackBar(t) {
    var wrap = el("div"), total = 0, names = [];
    CLASS_NAMES.forEach(function (c) { total += t.by_class[c[0]]; });
    wrap.appendChild(el("h3", null, "Time in status"));
    var bar = el("div", "stackbar");
    bar.setAttribute("role", "img");
    CLASS_NAMES.forEach(function (c) {
      var secs = t.by_class[c[0]];
      names.push(c[1] + " " + fmtDuration(secs));
      if (!secs) { return; }
      var part = el("span", "sb-" + c[0]);
      part.style.width = (100 * secs / total).toFixed(2) + "%";
      part.setAttribute("data-tip", c[1] + ": " + fmtDuration(secs));
      bar.appendChild(part);
    });
    bar.setAttribute("aria-label", "Time in status: " + names.join(", "));
    if (total) { wrap.appendChild(bar); }
    var list = el("ul", "sblist");
    CLASS_NAMES.forEach(function (c) {
      var item = el("li", null, c[1] + " " + fmtDuration(t.by_class[c[0]])), swatch = el("i", "sb-" + c[0]);
      item.insertBefore(swatch, item.firstChild); list.appendChild(item);
    });
    wrap.appendChild(list);
    var each = Object.keys(t.by_status).map(function (k) { return k + " " + fmtDuration(t.by_status[k]); });
    if (each.length) { wrap.appendChild(el("p", "sub", "By status: " + each.join(" · "))); }
    if (!total) { wrap.appendChild(el("p", "sub", "No working or waiting time recorded in this range.")); }
    return wrap;
  }
  function renderPanel() {
    var panel = $("panel");
    if (!sel) { panel.hidden = true; return; }
    panel.hidden = false;
    $("ptitle").textContent = sel.label + (sel.title ? " " + sel.title : "");
    var sum = D.summaries[sel.id] || {}, dl = $("psummary");
    dl.textContent = "";
    LABELS.forEach(function (pair) {
      var cell = el("div", ALERT[pair[0]] && sum[pair[0]] ? "alert" : null);
      cell.appendChild(el("dt", null, pair[1])); cell.appendChild(el("dd", null, String(sum[pair[0]] || 0)));
      dl.appendChild(cell);
    });
    var spent = statusOf(sel.id), box = $("pstatus");
    box.textContent = "";
    box.hidden = !spent;
    if (spent) { box.appendChild(stackBar(spent)); }
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
      writeHash();
    });
  });
  $("pclose").addEventListener("click", closePanel);
  document.addEventListener("keydown", function (ev) {
    var t = ev.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    if (ev.key === "/" && !typing && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      ev.preventDefault();
      $("lanesearch").focus();
      return;
    }
    if (ev.key !== "Escape") { return; }
    if (t === $("lanesearch") && t.value) { t.value = ""; setQuery(""); return; }
    hideTip();
    if (sel) { closePanel(); }
  });

  // ---- lane search and the problems-only toggle
  function setQuery(text) { query = text; draw(true); }
  $("lanesearch").addEventListener("input", function () { setQuery($("lanesearch").value); });
  $("onlyprob").addEventListener("click", function () {
    onlyProblems = !onlyProblems;
    $("onlyprob").setAttribute("aria-pressed", String(onlyProblems));
    draw(true);
  });

  // ---- the problems view: every problem in the timeframe, open ones first. A click opens the lane at the problem.
  function openProblem(row) {
    var lane = findLane(row.lane);
    if (lane) { openLane(lane, row.id); }
  }
  function problemsView() {
    var b = bounds(), list = $("pvlist"), open = 0, done = 0;
    list.textContent = "";
    D.problemRows.forEach(function (row) {
      var t = Date.parse(row.ts);
      if (!b || t < b.lo || t > b.hi) { return; }
      if (row.resolved) { done++; } else { open++; }
      var li = el("li", "pv-item " + (row.resolved ? "is-resolved" : "is-open"));
      var main = el("button", "pv-row");
      main.type = "button";
      main.setAttribute("data-problem", row.id);
      add(main, el("strong", null, row.label), badge(row.type), badge(row.resolved ? "resolved" : "open"), el("span", "pv-text", row.text),
        el("span", "pv-time", row.resolved ? (row.seconds === null ? "fixed" : "fixed in " + fmtDuration(row.seconds)) : "open for " + fmtDuration(row.seconds)));
      main.addEventListener("click", function () { openProblem(row); });
      li.appendChild(main);
      if (row.fixes.length) {
        var fixes = el("span", "pv-fixes", "Fix: ");
        row.fixes.forEach(function (f, n) {
          if (n) { fixes.appendChild(document.createTextNode(", ")); }
          fixes.appendChild(link(f.url, String(f.sha).slice(0, 7)));
        });
        li.appendChild(fixes);
      }
      list.appendChild(li);
    });
    $("pvcount").textContent = "(" + open + " open, " + done + " resolved)";
    $("pvempty").hidden = open + done > 0;
  }

  // ---- the shared view: the hash holds the open lane, the timeframe, the log filters and the search.
  function currentView() {
    var all = D.logGroups.every(function (g) { return on[g.key]; });
    return {lane: sel ? sel.id : null, from: win.lo === null ? null : new Date(win.lo).toISOString(),
      to: win.hi === null ? null : new Date(win.hi).toISOString(),
      kinds: all ? null : D.logGroups.filter(function (g) { return on[g.key]; }).map(function (g) { return g.key; }),
      q: query, p: onlyProblems};
  }
  // replaceState keeps the back button free of view changes. The refresh reload of `watch` keeps the hash.
  function writeHash() {
    if (restoring) { return; }
    var text = encodeView(currentView());
    try { history.replaceState(null, "", text ? "#" + text : location.pathname + location.search); } catch (e) { /* a sandboxed page cannot rewrite its URL */ }
  }
  function applyView(v) {
    restoring = true;
    query = v.q; $("lanesearch").value = v.q;
    onlyProblems = v.p; $("onlyprob").setAttribute("aria-pressed", String(v.p));
    win.lo = v.from ? Date.parse(v.from) : null; win.hi = v.to ? Date.parse(v.to) : null;
    document.querySelectorAll("[data-preset]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.getAttribute("data-preset") === "all" && win.lo === null && win.hi === null));
    });
    D.logGroups.forEach(function (g) { on[g.key] = v.kinds === null || v.kinds.indexOf(g.key) >= 0; });
    document.querySelectorAll("[data-log]").forEach(function (button) { button.setAttribute("aria-pressed", String(on[button.getAttribute("data-log")])); });
    sel = v.lane ? findLane(v.lane) : null;
    document.body.classList.toggle("has-panel", !!sel);
    restoring = false;
  }
  window.addEventListener("hashchange", function () { applyView(decodeView(location.hash)); draw(); });

  // ---- the legend: States, Events and Problems, each its own labelled row.
  var legend = $("legend");
  function legendRow(title) {
    var row = el("div", "legend-row");
    row.setAttribute("role", "group"); row.setAttribute("aria-label", title);
    row.appendChild(el("span", "legend-title", title));
    legend.appendChild(row);
    return row;
  }
  var stateRow = legendRow("States");
  D.states.forEach(function (s) {
    var item = el("span", null, s.label), swatch = el("i", "seg-" + cls(s.key));
    if (s.key !== "batched") { swatch.style.background = "var(--st-" + cls(s.key) + ", var(--st-none))"; }
    item.insertBefore(swatch, item.firstChild); stateRow.appendChild(item);
  });
  var rows = {events: legendRow("Events"), problems: legendRow("Problems")};
  D.markers.forEach(function (m) {
    var item = el("span", null, m.label), swatch = el("span", "mk mk-" + m.key);
    swatch.setAttribute("aria-hidden", "true");
    item.insertBefore(swatch, item.firstChild); (rows[m.group] || rows.events).appendChild(item);
  });
  applyView(decodeView(location.hash));
  draw();
})();
"""


PAGE_JS = PAGE_JS.replace("/*VIEW_JS*/", VIEW_JS).replace("/*THEME_KEY*/", json.dumps(THEME_KEY))


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
    group_of = {k: name for name, keys in MARKER_GROUPS.items() for k in keys}
    markers = [{"key": k, "label": MARKER_LABELS[k], "group": group_of[k]} for k in MARKER_COLORS]
    problems = problem_rows(doc, now)
    summaries = {lane["id"]: lane_summary([events[i] for i in lane["events"]]) for group in groups for lane in group["lanes"]}
    notes = {}  # batch key -> indexes of its cross-check and plan-amendment comments, whichever issue holds them
    for index, event in enumerate(events):
        if event.get("kind") == "comment" and event.get("role") in NOTE_ROLES and event.get("batch") is not None:
            notes.setdefault(str(event["batch"]), []).append(index)
    data = {"issueRepo": "/".join(str(forge[k]) for k in ("owner", "repo") if forge.get(k)) or None, "states": states, "markers": markers, "events": events, "issues": doc.get("issues") or {}, "batches": doc.get("batches") or {},
            "range": rng, "groups": groups, "kinds": kinds, "marks": [marker_for(e) for e in events],
            "logs": [log_group_of(e) for e in events], "logGroups": [{"key": k, "label": label} for k, label, _ in LOG_GROUPS],
            "summaries": summaries, "notes": notes, "problemRows": problems, "problemMarks": list(PROBLEM_MARKS),
            "statusTime": time_in_status(doc, now), "outcomeStates": AGENT_OUTCOME_STATES}
    filters = "".join(f'<button type="button" data-log="{html.escape(k)}" aria-pressed="true">{html.escape(label)}</button>'
                      for k, label, _ in LOG_GROUPS)
    pv_open, pv_hidden = (" open", " hidden") if problems else ("", "")
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">\n' if refresh else ""
    empty = '<p id="empty">No events in this range.</p>' if not events else '<p id="empty" hidden>No events in this range.</p>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>Workflow timeline — {html.escape(where)}</title>
<style>{PAGE_CSS}</style>
<script>{THEME_HEAD_JS}</script>
</head>
<body>
<header class="top">
<div>
<h1>Workflow timeline — {html.escape(where)}</h1>
<p class="sub">Data range: {html.escape(span)} · generated {html.escape(stamped)} · times are UTC</p>
</div>
<button type="button" id="theme">Theme: auto</button>
</header>
<div class="controls">
<button type="button" data-preset="24h" aria-pressed="false">24h</button>
<button type="button" data-preset="7d" aria-pressed="false">7d</button>
<button type="button" data-preset="30d" aria-pressed="false">30d</button>
<button type="button" data-preset="all" aria-pressed="true">all</button>
<label>From <input type="date" id="from"></label>
<label>To <input type="date" id="to"></label>
<label for="lanesearch">Search lanes</label>
<input type="search" id="lanesearch" placeholder="number or title, or press /" autocomplete="off" aria-keyshortcuts="/">
<button type="button" id="onlyprob" aria-pressed="false">Only lanes with problems</button>
</div>
<details id="problemsview"{pv_open}>
<summary>Problems <span id="pvcount"></span></summary>
<ul id="pvlist"></ul>
<p id="pvempty" class="sub"{pv_hidden}>No problems in this range.</p>
</details>
{_convergence_strip(doc.get("convergence"))}
<div class="legend" id="legend" role="group" aria-label="Legend"></div>
<div id="layout">
<main>
<div id="chart"></div>
{empty}
</main>
<aside id="panel" aria-label="Lane detail" hidden>
<div class="phead"><h2 id="ptitle" tabindex="-1"></h2><button type="button" id="pclose">Close</button></div>
<dl id="psummary"></dl>
<div id="pstatus" hidden></div>
<section id="pfix"><h3>What needed fixing</h3><ul id="problems"></ul><p id="pnone" class="sub">Nothing needed fixing.</p></section>
<section id="pnotes" hidden><h3>Cross-checks and amendments</h3><ul id="notes"></ul><p id="pnonotes" class="sub">None recorded.</p></section>
<h3>Log</h3>
<div class="filters" id="logfilter" role="group" aria-label="Filter log by kind">{filters}</div>
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


def write_snapshot(collect_args, out_path, refresh, scratch):
    """Collect and render one page at `out_path`. Return (exit code, document or None).

    `watch` and `snapshot` share this function. It leaves any page already at `out_path` alone
    when a step fails, and replaces the page in one rename when both steps succeed.
    """
    doc_path = os.path.join(scratch, "timeline.json")
    try:
        code = main(["collect", *collect_args, "--out", doc_path])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 5
    if code != 0:
        return code, None
    tmp_path = os.path.join(os.path.dirname(out_path), f".{os.path.basename(out_path)}.tmp")
    try:
        with open(doc_path) as handle:
            doc = json.load(handle)
        page = render_html(doc, refresh=refresh)
        with open(tmp_path, "w") as handle:
            handle.write(page)
        os.replace(tmp_path, out_path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": f"render failed: {exc}"}), file=sys.stderr)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 2, None
    return 0, doc


def _watch_once(collect_args, out_path, interval, scratch):
    """Collect and render one page. Return 0, or the exit code of the failed step."""
    return write_snapshot(collect_args, out_path, max(1, int(-(-interval // 1))), scratch)[0]


def cmd_snapshot(args):
    try:
        out_path = os.path.abspath(args.out) if args.out else common_dir_path("timeline.html")
        if not args.out:
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except (RuntimeError, OSError) as exc:
        print(json.dumps({"error": f"snapshot failed: {exc}"}), file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as scratch:
        code, doc = write_snapshot(args.collect_args, out_path, None, scratch)
    if code != 0:
        return code
    # A light gc: the age rules only, never --all. A gc failure never fails the snapshot.
    if LAST_COLLECT.get("cache_root"):
        try:
            run_gc(LAST_COLLECT["cache_root"], LAST_COLLECT.get("repo_path") or ".")
        except OSError:
            pass
    groups, _ = build_model(doc)
    lanes = sum(len(group["lanes"]) for group in groups)
    print(f"wrote {out_path}: {len(doc['events'])} events, {lanes} lanes, {len(problem_rows(doc))} problems; "
          f"{calls_summary()}", file=sys.stderr)
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

    caught = {"signal": "SIGINT"}

    def stop(signum, frame):
        caught["signal"] = signal.Signals(signum).name
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    deadline = time.monotonic() + args.max_minutes * 60
    stop_at = datetime.now(timezone.utc) + timedelta(minutes=args.max_minutes)
    # A clean stop and a crash both end the process. The end line tells them apart.
    print(f"watch: started; a refresh every {args.interval:g} s until "
          f"{stop_at.isoformat(timespec='seconds').replace('+00:00', 'Z')}; page {out_path}", file=sys.stderr, flush=True)
    cycles = 0

    def ended(reason, code):
        print(f"watch: stopped after {_plural(cycles, 'cycle')}: {reason}", file=sys.stderr, flush=True)
        return code

    failures_in_a_row = 0
    collect_args = args.collect_args
    try:
        with tempfile.TemporaryDirectory() as scratch:
            while True:
                code = _watch_once(collect_args, out_path, args.interval, scratch)
                cycles += 1
                # --refresh-cache renews the cache once. Later cycles read it, or each cycle reads everything.
                collect_args = [a for a in collect_args if a != "--refresh-cache"]
                if code == 5:
                    return ended("a bad flag (exit 5)", 5)
                remaining = deadline - time.monotonic()
                if code == 3:
                    # The quota guard refused: skip this cycle. It is not a failure, so watch never exits on it.
                    attempt = datetime.now(timezone.utc) + timedelta(seconds=min(args.interval, max(0, remaining)))
                    nxt = (f"next attempt at {attempt.isoformat(timespec='seconds').replace('+00:00', 'Z')}"
                           if remaining > 0 else "no next attempt: --max-minutes ends the watch")
                    print(f"watch: skipped this refresh to save the API quota; the last page stays; {nxt}",
                          file=sys.stderr, flush=True)
                    if remaining <= 0:
                        return ended("--max-minutes reached", 0)
                    time.sleep(min(args.interval, remaining))
                    continue
                failures_in_a_row = failures_in_a_row + 1 if code else 0
                if code:
                    print(f"watch: refresh failed (exit {code}); the last page stays", file=sys.stderr, flush=True)
                    if failures_in_a_row >= WATCH_FAILURE_LIMIT:
                        return ended(f"{WATCH_FAILURE_LIMIT} failed refreshes in a row (exit 2)", 2)
                else:
                    print(f"wrote {out_path} at {datetime.now(timezone.utc).isoformat(timespec='seconds')}; "
                          f"{calls_summary()}", file=sys.stderr, flush=True)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ended("--max-minutes reached", 0)
                # The sleep ends at the deadline at the latest, and the loop then writes one last cycle.
                time.sleep(min(args.interval, remaining))
    except KeyboardInterrupt:
        return ended(caught["signal"], 0)


# ------------------------------------------------------------------ gc

def _entry_key(path):
    """Return the key of a cache file from its first bytes, or None. `put` writes the key first."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    match = ENTRY_KEY.search(head)
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except ValueError:
        return None


def gc_plan(root, repo_path, everything=False, now=None):
    """Return (cache files, temporary files, refs) that `gc` removes.

    Only files in the cache layout count: `<root>/<2 hex>/<64 hex>.json` and the `.<name>.tmp`
    files that `Cache.put` writes beside them. Any other file under `root` stays. Without
    `everything`, a cache file goes when no read or write touched it for GC_MAX_AGE, a temporary
    file goes when it is older than GC_TMP_AGE, and a refs/issue-flow/pull/ ref goes when the
    newest state record of its PR shows it closed more than GC_MAX_AGE ago. With `everything`,
    every cache file, every temporary file and every refs/issue-flow/ ref goes.
    """
    now = now or datetime.now(timezone.utc)
    files, tmps, states = [], [], {}
    for name in sorted(os.listdir(root)) if root and os.path.isdir(root) else []:
        folder = os.path.join(root, name)
        if not CACHE_DIR_NAME.match(name) or not os.path.isdir(folder):
            continue
        for leaf in sorted(os.listdir(folder)):
            path = os.path.join(folder, leaf)
            try:
                age = now - datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc)
            except OSError:
                continue
            if CACHE_TMP_NAME.match(leaf):
                if everything or age > GC_TMP_AGE:
                    tmps.append(path)
                continue
            if not CACHE_FILE_NAME.match(leaf):
                continue
            if everything or age > GC_MAX_AGE:
                files.append(path)
            key = None if everything else _entry_key(path)
            found = STATE_KEY.match(key or "")
            if found:
                try:
                    with open(path, encoding="utf-8") as handle:
                        value = json.load(handle).get("value") or {}
                except (OSError, ValueError, AttributeError):
                    continue
                pr = (found.group(1), int(found.group(2)))
                if isinstance(value, dict) and (value.get("updated_at") or "") >= (states.get(pr, {}).get("updated_at") or ""):
                    states[pr] = value
    refs = []
    out = GitDiffs(repo_path)._git("for-each-ref", "--format=%(refname)", "refs/issue-flow/") or ""
    cutoff = (now - GC_MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ref in out.split():
        found = PR_REF.match(ref)
        record = states.get((found.group(1), int(found.group(2)))) if found else None
        closed = to_utc(record.get("closed_at")) if record and record.get("state") == "closed" else None
        if everything or (closed and closed < cutoff):
            refs.append(ref)
    return files, tmps, refs


def run_gc(root, repo_path, everything=False, dry_run=False):
    """Remove what `gc_plan` names, unless `dry_run`. Return (files, tmps, refs)."""
    files, tmps, refs = gc_plan(root, repo_path, everything)
    if dry_run:
        return files, tmps, refs
    for path in files + tmps:
        try:
            os.unlink(path)
        except OSError:
            pass
    for name in os.listdir(root) if root and os.path.isdir(root) else []:
        folder = os.path.join(root, name)
        if CACHE_DIR_NAME.match(name) and os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    if refs:
        GitDiffs(repo_path)._git("update-ref", "--stdin", stdin="".join(f"delete {ref}\n" for ref in refs))
    return files, tmps, refs


GC_REFS_NAMED = 3


def _gc_refs_text(refs):
    """Return the ref count of the gc summary, with the first GC_REFS_NAMED refs named."""
    text = _plural(len(refs), "ref")
    if not refs:
        return text
    named = ", ".join(refs[:GC_REFS_NAMED])
    if len(refs) > GC_REFS_NAMED:
        return f"{text} ({named} and {len(refs) - GC_REFS_NAMED} more: the `ref` lines on stdout name them all)"
    return f"{text} ({named})"


def cmd_gc(args):
    root = args.cache_dir
    if not root:
        try:
            root = common_dir_path("cache", args.repo_path)
        except (RuntimeError, OSError) as exc:
            print(json.dumps({"error": f"gc failed: {exc}; give --cache-dir"}), file=sys.stderr)
            return 2
    files, tmps, refs = run_gc(root, args.repo_path, args.all, args.dry_run)
    # The refs come first: they are few, and a display that cuts a long list keeps its head.
    for ref in refs:
        print(f"ref {ref}")
    listed = args.dry_run or args.verbose
    if listed:
        for path in files + tmps:
            print(f"cache {path}")
    verb = "would remove" if args.dry_run else "removed"
    hint = "; --verbose lists each file" if (files or tmps) and not listed else ""
    print(f"gc: {verb} {_plural(len(files), 'cache file')}, {_plural(len(tmps), 'temporary file')} and "
          f"{_gc_refs_text(refs)} ({root}){hint}", file=sys.stderr)
    return 0


def main(argv=None):
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    collect = sub.add_parser("collect", help="read the tracker and print the timeline document")
    collect.add_argument("--forge", choices=["github", "gitea"])
    collect.add_argument("--repo", help="owner/name of the repository that holds the issues")
    collect.add_argument("--pr-repo", action="append", metavar="OWNER/NAME[,OWNER/NAME]",
                         help="another repository that holds pull requests; repeat the flag or give a comma list "
                              "(the --repo repository is always read)")
    collect.add_argument("--repo-path", default=".",
                         help="local checkout: commit diffs are read from its git (--diff-source), the default cache "
                              "is in its git common dir, and `tea api` resolves its login from it (default: .)")
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
    collect.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                         help=f"reads to run at once, 1 to {MAX_JOBS} (default {DEFAULT_JOBS}; 1 reads one at a time)")
    collect.add_argument("--budget", type=float, default=DEFAULT_BUDGET, metavar="PERCENT",
                         help="stop with exit 3 when the reads need more than this percent of the API quota that "
                              f"remains (default {DEFAULT_BUDGET}; GitHub only, Gitea has no quota to read)")
    collect.add_argument("--diff-source", choices=DIFF_SOURCES, default="auto",
                         help="where commit files and patches come from: `git` (the --repo-path checkout), `api`, "
                              "or `auto`, git first and the API for a commit git lacks (default auto)")
    cache_mode = collect.add_mutually_exclusive_group()
    cache_mode.add_argument("--no-cache", action="store_true",
                            help="read nothing from the read cache and write nothing to it")
    cache_mode.add_argument("--refresh-cache", action="store_true",
                            help="read nothing from the read cache, and write every read to it")
    collect.add_argument("--cache-dir", metavar="PATH",
                         help="the read cache directory (default: <git common dir>/issue-flow/cache of --repo-path)")
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
    snapshot = sub.add_parser("snapshot", help="collect and render one page in one process",
                              description="Every other flag goes to `collect`. Exit 0 only when the page is written; "
                                          "any other exit code means no new page. One summary line goes to stderr.")
    snapshot.add_argument("--out", help="page path (default: <git common dir>/issue-flow/timeline.html)")
    snapshot.set_defaults(func=cmd_snapshot)
    path = sub.add_parser("path", help="print the default page path")
    path.set_defaults(func=cmd_path)
    gc = sub.add_parser("gc", help="remove old cache files and the refs/issue-flow/ refs of PRs closed long ago",
                        description="Removes cache files that no collect touched for 30 days, temporary files older "
                                    "than 1 hour, and the refs/issue-flow/pull/ refs of PRs that the cache shows "
                                    "closed for more than 30 days. `snapshot` runs the same gc at its end.")
    gc.add_argument("--repo-path", default=".", help="checkout whose refs/issue-flow/ refs and default cache gc cleans")
    gc.add_argument("--cache-dir", metavar="PATH", help="the read cache directory (default: <git common dir>/issue-flow/cache)")
    gc.add_argument("--all", action="store_true", help="remove the whole cache and every refs/issue-flow/ ref")
    gc.add_argument("--dry-run", action="store_true", help="print what gc would remove (one `cache` or `ref` line each) "
                                                            "and remove nothing")
    gc.add_argument("--verbose", action="store_true", help="also print one `cache` line for each file removed "
                                                            "(a `ref` line for each ref is always printed)")
    gc.set_defaults(func=cmd_gc)
    args, extra = parser.parse_known_args(argv)
    if args.command in ("watch", "snapshot"):
        args.collect_args = extra
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
