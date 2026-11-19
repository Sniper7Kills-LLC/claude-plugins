#!/usr/bin/env python3
"""Rebuild what an issue-flow run did from the tracker alone, as normalized events.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/name --since 7d
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge gitea --repo owner/name --out timeline.json
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --forge github --repo owner/issues-repo \\
        --pr-repo owner/pr-repo --batch 78
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/timeline.py" collect --fixture bundle.json   # test/dry-run

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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
