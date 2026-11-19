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

Flags: --since/--until take an ISO date, an ISO timestamp, or a relative age (`7d`,
`24h`, `2w`). --issue and --batch keep one issue or one batch (a tracking-issue number).
--pr-repo reads pull requests from another repository on the same forge.

The fetch is kept apart from `normalize`, which is pure: it takes a bundle of raw
payloads and returns the document. A --fixture file is such a bundle:
{"forge": {"type", "host", "owner", "repo"}, "issues": [{"issue", "timeline"}],
"pulls": [{"pr", "timeline"}]}, with the payloads exactly as the forge returned them.

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
import subprocess
import sys
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
            "name": (raw.get("label") or {}).get("name"), "body": raw.get("body") or ""}


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


def normalize(bundle, since=None, until=None, only_issue=None, only_batch=None):
    """Turn a bundle of raw forge payloads into the timeline document (references/timeline.md).

    `since` and `until` are UTC timestamps that bound the events. They do not bound
    the replay: statuses are replayed from the start so `status_at_start` is right.
    """
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

    events.sort(key=lambda e: (e["ts"], e["kind"], e["issue"] or 0, e["pr"] or 0))
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
                             "status_at_start": before[-1]["to"] if before else None}
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

    def paged(self, path, keep_going=None):
        """Read every page. An empty page ends the walk; a short page does not (server caps vary)."""
        rows = []
        sep = "&" if "?" in path else "?"
        for page in range(1, 1000):
            body = self.get(f"{path}{sep}{self.limit}&page={page}")
            if not isinstance(body, list):
                raise FetchError(f"{self.cmd} api {path} page {page} is not a list: {str(body)[:200]}")
            if not body:
                break
            rows.extend(body)
            if keep_going and not keep_going(body):
                break
        return rows


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
        pulls.append({"pr": pr, "timeline": api.paged(api.path(pr_repo or repo, f"/issues/{pr['number']}/timeline"))})
    return {"forge": forge, "issues": issues, "pulls": pulls}


# ------------------------------------------------------------------ command line

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(5)


def cmd_collect(args):
    try:
        since = parse_when(args.since) if args.since else None
        until = parse_when(args.until) if args.until else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
        doc = normalize(bundle, since, until, args.issue, args.batch)
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
ISSUE_PREFIXES = {"status", "issue", "pr", "checkpoint"}


def lane_kind_of(event_kind):
    prefix = (event_kind or "").split(".")[0]
    if prefix in ISSUE_PREFIXES:
        return "issue"
    return prefix or "other"


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
    "dot": ("#656d76", "#6e7681"),
}
MARKER_LABELS = {"pr": "PR opened", "merge": "PR merged", "batch": "Batch merged", "cp": "Checkpoint",
                 "park": "Parked (needs feedback or blocked)", "dot": "Other event"}


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
.legend .mk { position:static; display:inline-block; vertical-align:middle; margin:0 .35rem 0 0; transform:rotate(45deg) scale(.8); }
.legend .mk-batch { width:.8rem; height:.8rem; }
#tip { position:fixed; z-index:10; max-width:24rem; background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:.4rem .6rem; white-space:pre-line; pointer-events:none; box-shadow:0 2px 8px rgba(0,0,0,.25); }
#empty { color:var(--muted); padding:1rem; border:1px dashed var(--line); border-radius:6px; text-align:center; }
@media (max-width:640px) { .axis, .lane { grid-template-columns:7rem 1fr; } .board { min-width:520px; } }
"""

PAGE_JS = r"""
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("tl-data").textContent);
  var MARK = {"pr.opened": "pr", "pr.merged": "merge", "batch.merged": "batch", "checkpoint": "cp"};
  var PARK = {"needs-feedback": 1, "blocked": 1};
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
  function pos(ms, b) { return (100 * (ms - b.lo) / (b.hi - b.lo)).toFixed(3) + "%"; }

  function laneNode(lane, b, visible) {
    var kind = D.kinds[lane.kind];
    var row = el("div", "lane lane-" + cls(lane.kind));
    var name = el("div", "name");
    if (lane.url && /^https?:\/\//.test(lane.url)) {
      var link = el("a", null, lane.label);
      link.href = lane.url; link.rel = "noopener"; link.title = lane.title || "";
      name.appendChild(link);
    } else { name.appendChild(document.createTextNode(lane.label)); }
    if (lane.title) { name.appendChild(el("small", null, " " + lane.title)); }
    row.appendChild(name);
    var track = el("div", "track");
    var evs = lane.events.map(function (i) { return {e: D.events[i], t: times[i]}; });
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
      var mark = null;
      if (kind.style === "bar") {
        mark = MARK[x.e.kind] || null;
        if (x.e.kind === "status" && PARK[x.e.to]) { mark = "park"; }
      } else { mark = MARK[x.e.kind] || "dot"; }
      if (!mark) { return; }
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
      for (var k = 0; k <= 4; k++) {
        var tick = el("span", null, stamp(b.lo + (b.hi - b.lo) * k / 4));
        tick.style.left = (k * 25) + "%"; ticks.appendChild(tick);
      }
      axis.appendChild(ticks);
      var board = el("div", "board"); board.appendChild(axis);
      D.groups.forEach(function (group) {
        var rows = [];
        group.lanes.forEach(function (lane) {
          var visible = lane.events.map(function (i) { return {e: D.events[i], t: times[i]}; })
            .filter(function (x) { return x.t >= b.lo && x.t <= b.hi; });
          if (visible.length) { shown += visible.length; rows.push(laneNode(lane, b, visible)); }
        });
        if (rows.length) {
          board.appendChild(el("div", "group", group.label));
          rows.forEach(function (r) { board.appendChild(r); });
        }
      });
      chart.appendChild(board);
    }
    var empty = $("empty");
    empty.hidden = shown > 0;
    empty.textContent = "No events in this range." + (b && !shown ? " Range: " + stamp(b.lo) + " to " + stamp(b.hi) + " UTC." : "");
    chart.hidden = shown === 0;
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
    data = {"states": states, "markers": markers, "events": events, "issues": doc.get("issues") or {}, "batches": doc.get("batches") or {},
            "range": rng, "groups": groups, "kinds": kinds}
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
<div id="chart"></div>
{empty}
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
    collect.add_argument("--out", help="write the document here instead of stdout")
    collect.set_defaults(func=cmd_collect)
    render = sub.add_parser("render", help="turn a timeline document into one self-contained HTML page")
    render.add_argument("--in", dest="infile", help="timeline JSON from `collect` (default: stdin)")
    render.add_argument("--out", help="write the page here instead of stdout")
    render.add_argument("--refresh", type=int, help="seconds between automatic page reloads (default: off)")
    render.set_defaults(func=cmd_render)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
