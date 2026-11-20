#!/usr/bin/env python3
"""Tests for issue-flow/scripts/timeline.py (the `collect` subcommand).

Section 1 normalizes recorded GitHub and Gitea payloads of one story and requires
identical events. Section 2 checks the filters and the schema extras. Section 3 runs
the CLI as a subprocess: with --fixture, and live against a fake `gh`/`tea` that
serves the same payloads (including the Gitea API-error body that `tea api` prints
with exit 0). Later issues in the batch add their own sections below."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "scripts", "timeline.py")
spec = importlib.util.spec_from_file_location("timeline", SCRIPT)
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)

failures = []


def check(cond, message):
    if not cond:
        failures.append(message)


# ---------------------------------------------------------------- the story
# One run, rendered as raw GitHub payloads and as raw Gitea payloads.
T = lambda hms: f"2026-05-01T{hms}Z"  # noqa: E731
G = lambda hms: f"2026-05-01T{hms}+00:00"  # noqa: E731  Gitea's timestamp shape

CONVERGENCE_BODY = "Status block\n<!-- issue-flow:convergence --> gates: 12→3f/5c, 13→2f/–\n"


def issue_raw(number, title, labels, created, body="", user="alice", state="open"):
    return {"number": number, "title": title, "state": state, "body": body,
            "html_url": f"https://x/issues/{number}", "created_at": created,
            "labels": [{"name": n} for n in labels], "user": {"login": user}}


def pr_raw(number, title, head, base, created, merged=None, draft=False, merged_by=None):
    return {"number": number, "title": title, "state": "closed" if merged else "open",
            "html_url": f"https://x/pull/{number}", "created_at": created,
            "closed_at": merged, "merged_at": merged, "draft": draft,
            "head": {"ref": head}, "base": {"ref": base},
            "user": {"login": "bot"}, "merged_by": {"login": merged_by} if merged_by else None}


def gh_label(op, ts, name, actor="alice"):
    return {"event": op, "created_at": ts, "actor": {"login": actor}, "label": {"name": name}}


def gt_label(op, ts, name, actor="alice"):
    return {"type": "label", "created_at": ts, "user": {"login": actor},
            "label": {"name": name}, "body": "1" if op == "labeled" else ""}


def build(forge_type):
    gh = forge_type == "github"
    ts = T if gh else G
    label = gh_label if gh else gt_label
    comment = (lambda t, b: {"event": "commented", "created_at": t, "user": {"login": "pm"}, "body": b}) if gh else \
              (lambda t, b: {"type": "comment", "created_at": t, "user": {"login": "pm"}, "body": b})
    close = {"event": "closed", "created_at": ts("14:00:00"), "actor": {"login": "pm"}} if gh else \
            {"type": "close", "created_at": ts("14:00:00"), "user": {"login": "pm"}}
    ready = {"event": "ready_for_review", "created_at": ts("13:00:00"), "actor": {"login": "bot"}} if gh else \
            {"type": "change_title", "created_at": ts("13:00:00"), "user": {"login": "bot"},
             "old_title": "WIP: Add timeline", "new_title": "Add timeline"}
    # The remove and the add land in one second, in the order each forge happens to give.
    swap1 = [label("unlabeled", ts("11:00:00"), "status:ready"), label("labeled", ts("11:00:00"), "status:in-progress")]
    swap2 = [label("labeled", ts("12:00:00"), "status:in-review"), label("unlabeled", ts("12:00:00"), "status:in-progress")]
    issue51 = issue_raw(51, "Collector", ["status:in-review"], ts("10:00:00"))
    return {
        "forge": {"type": forge_type, "host": "h", "owner": "o", "repo": "r"},
        "issues": [
            {"issue": issue_raw(9, "flow status", ["flow:status"], ts("08:00:00"), CONVERGENCE_BODY), "timeline": []},
            {"issue": issue_raw(50, "Batch", ["type:batch"], ts("09:00:00"), "- [ ] #51\n- [ ] #52\n"), "timeline": []},
            {"issue": issue51, "timeline": [
                label("labeled", ts("10:00:05"), "status:ready"), label("labeled", ts("10:00:06"), "type:feature"),
                *swap1, comment(ts("11:30:00"), "checkpoint 1: finish tests, replacement spawned"),
                comment(ts("11:31:00"), "an ordinary comment"), *swap2, close]},
            {"issue": issue_raw(52, "Second member", ["status:ready"], ts("10:00:00")), "timeline": []},
        ],
        "pulls": [
            {"pr": pr_raw(60, "WIP: Add timeline" if not gh else "Add timeline", "issue/51-collector",
                          "batch/50-bar", ts("12:00:00"), merged=ts("13:30:00"), draft=False, merged_by="pm" if not gh else None),
             "timeline": [ready]},
            {"pr": pr_raw(61, "Batch 50", "batch/50-bar", "dev", ts("14:30:00"), merged=ts("15:00:00")),
             "timeline": []},
        ],
    }


gh_doc = timeline.normalize(build("github"))
gt_doc = timeline.normalize(build("gitea"))


def project(doc):
    return [(e["ts"], e["kind"], e["issue"], e["pr"], e["batch"], e["from"], e["to"]) for e in doc["events"]]


check(project(gh_doc) == project(gt_doc), f"GitHub and Gitea events differ:\n{project(gh_doc)}\n{project(gt_doc)}")
check(gh_doc["version"] == 1 and gt_doc["version"] == 1, "schema version must be 1")
check(gh_doc["forge"]["type"] == "github" and gt_doc["forge"]["type"] == "gitea", "forge block carries the type")

events = gh_doc["events"]
kinds = {e["kind"] for e in events}
for want in ("issue.opened", "issue.closed", "status", "checkpoint", "pr.opened", "pr.ready", "pr.merged", "batch.merged"):
    check(want in kinds, f"kind {want} is missing: {sorted(kinds)}")
check(all(set(e) >= {"ts", "kind", "issue", "pr", "batch", "actor", "from", "to", "detail"} for e in events),
      "every event carries the nine core keys")
check([e["ts"] for e in events] == sorted(e["ts"] for e in events), "events sort by ts")
check(all(e["ts"].endswith("Z") for e in events), "timestamps are UTC with a Z suffix")

# A label swap in one second is one transition pair; non-status labels are not events.
status51 = [(e["from"], e["to"]) for e in events if e["kind"] == "status" and e["issue"] == 51]
check(status51 == [(None, "ready"), ("ready", "in-progress"), ("in-progress", "in-review")],
      f"status transitions for #51: {status51}")

cp = [e for e in events if e["kind"] == "checkpoint"]
check(len(cp) == 1 and cp[0]["k"] == 1 and "finish tests" in cp[0]["detail"] and cp[0]["issue"] == 51,
      f"checkpoint event: {cp}")

opened = {e["pr"]: e for e in events if e["kind"] == "pr.opened"}
check(opened[60]["draft"] is True and opened[60]["base"] == "batch/50-bar" and opened[60]["issue"] == 51,
      f"pr.opened #60: {opened.get(60)}")
check(opened[61]["draft"] is False, f"pr.opened #61: {opened.get(61)}")
bm = [e for e in events if e["kind"] == "batch.merged"]
check(len(bm) == 1 and bm[0]["pr"] == 61 and bm[0]["batch"] == 50, f"batch.merged: {bm}")
check(all(e["batch"] == 50 for e in events if e["issue"] == 51), "member events carry the batch number")

check(gh_doc["batches"]["50"] == {"tracking": 50, "branch": "batch/50-bar", "members": [51, 52], "pr": 61,
                                  "prs": [60], "pr_repo": "o/r"}, f"batches: {gh_doc['batches']}")
check(gh_doc["convergence"] == [{"gate": 12, "filed": 3, "closed": 5}, {"gate": 13, "filed": 2, "closed": None}],
      f"convergence: {gh_doc['convergence']}")
check(gh_doc["issues"]["51"]["title"] == "Collector" and "status:in-review" in gh_doc["issues"]["51"]["labels"],
      "issues map carries title and labels")

# ---------------------------------------------------------------- filters
since, until = "2026-05-01T11:15:00Z", "2026-05-01T13:00:00Z"
doc = timeline.normalize(build("github"), since=since, until=until)
check(doc["events"] and all(since <= e["ts"] <= until for e in doc["events"]), f"since/until bound events: {project(doc)}")
check(doc["range"] == {"since": since, "until": until}, f"range block: {doc['range']}")
check(doc["issues"]["51"]["status_at_start"] == "in-progress", f"status_at_start: {doc['issues']['51']}")
check(gh_doc["issues"]["51"]["status_at_start"] is None, "no --since means no status_at_start")
only = timeline.normalize(build("gitea"), only_issue=51)
check(only["events"] and all(e["issue"] == 51 for e in only["events"]), "--issue keeps only that issue")
onlyb = timeline.normalize(build("gitea"), only_batch=50)
check(onlyb["events"] and all(e["batch"] == 50 for e in onlyb["events"]), "--batch keeps only that batch")

now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
check(timeline.parse_when("7d", now) == "2026-05-01T12:00:00Z", "relative 7d")
check(timeline.parse_when("24h", now) == "2026-05-07T12:00:00Z", "relative 24h")
check(timeline.parse_when("2026-05-02", now) == "2026-05-02T00:00:00Z", "ISO date")
check(timeline.parse_when("2026-05-02T10:00:00+02:00", now) == "2026-05-02T08:00:00Z", "offset converts to UTC")
try:
    timeline.parse_when("soon", now)
    check(False, "a bad --since must raise ValueError")
except ValueError:
    pass

with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["git", "init", "-q", tmp], check=True)
    sub = os.path.join(tmp, "a")
    os.mkdir(sub)
    got = timeline.common_dir_path("timeline.json", cwd=sub)
    check(got == os.path.realpath(os.path.join(tmp, ".git", "issue-flow", "timeline.json")) or
          os.path.realpath(got) == os.path.realpath(os.path.join(tmp, ".git", "issue-flow", "timeline.json")),
          f"common_dir_path: {got}")

# ---------------------------------------------------------------- CLI
# Hermetic runs: the hook writes a real events.jsonl in every developer's git common dir, so a
# test must never read the one of the checkout that holds it. `run` starts in a scratch repo
# and passes `--local-events off` unless the test names its own source.
HERMETIC = tempfile.mkdtemp(prefix="ifw-hermetic-")
subprocess.run(["git", "init", "-q", HERMETIC], check=True)


def run(args, env=None):
    if args[:1] in (["collect"], ["snapshot"], ["watch"]) and "--local-events" not in args:
        args = [*args, "--local-events", "off"]
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=HERMETIC,
                          env={**os.environ, **(env or {})})


with tempfile.TemporaryDirectory() as tmp:
    fx = os.path.join(tmp, "gh.json")
    with open(fx, "w") as handle:
        json.dump(build("github"), handle)
    proc = run(["collect", "--fixture", fx])
    check(proc.returncode == 0, f"--fixture exits 0, got {proc.returncode}: {proc.stderr}")
    check(json.loads(proc.stdout)["events"] == gh_doc["events"], "CLI --fixture matches normalize()")
    out = os.path.join(tmp, "out.json")
    proc = run(["collect", "--fixture", fx, "--out", out, "--since", "2026-05-01T11:15:00Z"])
    check(proc.returncode == 0 and proc.stdout == "" and os.path.exists(out), "--out writes a file, not stdout")
    check(run(["collect"]).returncode == 5, "collect without --fixture or --forge exits 5")
    check(run(["collect", "--forge", "github"]).returncode == 5, "--forge without --repo exits 5")
    check(run(["collect", "--fixture", fx, "--since", "soon"]).returncode == 5, "a bad --since exits 5")

    # Fake gh/tea: serves one page of canned data, then empty pages.
    fake = os.path.join(tmp, "fake.py")
    with open(fake, "w") as handle:
        handle.write(r'''
import json, os, re, sys
path = sys.argv[-1]
data = json.load(open(os.environ["FAKE_DATA"]))
if data.get("error"):
    print(json.dumps({"message": "token does not have permission"}))
    sys.exit(0)
page = re.search(r"[?&]page=(\d+)", path)
if page and int(page.group(1)) > 1:
    print('{"workflow_runs": []}' if "/actions/runs?" in path else "[]"); sys.exit(0)
pulls = {e["pr"]["number"]: e for e in data["pulls"]}
m = re.search(r"/pulls/(\d+)/(commits|reviews)(?:/(\d+)/comments)?\?", path)
if m:
    entry = pulls[int(m.group(1))]
    if m.group(3):
        print(json.dumps([c for c in entry.get("review_comments", []) if c["pull_request_review_id"] == int(m.group(3))]))
    elif m.group(2) == "commits":
        print(json.dumps([{k: v for k, v in c.items() if k not in ("diff",)} for c in entry.get("commits", [])]))
    else:
        print(json.dumps(entry.get("reviews", [])))
    sys.exit(0)
m = re.search(r"/pulls/(\d+)/comments\?", path)
if m:
    print(json.dumps(pulls[int(m.group(1))].get("review_comments", []))); sys.exit(0)
m = re.search(r"/(?:git/)?commits/([0-9a-f]+)(\.diff)?$", path)
if m:
    for entry in pulls.values():
        for c in entry.get("commits", []):
            if c["sha"] == m.group(1):
                print(c.get("diff", "") if m.group(2) else json.dumps(c)); sys.exit(0)
m = re.search(r"/actions/runs\?.*head_sha=(\w+)", path)
if m:
    rows = [r["run"] for e in pulls.values() for r in e.get("runs", []) if r["run"]["head_sha"] == m.group(1)]
    print(json.dumps({"workflow_runs": rows})); sys.exit(0)
m = re.search(r"/actions/runs/(\d+)/jobs", path)
if m:
    rows = [j for e in pulls.values() for r in e.get("runs", []) if r["run"]["id"] == int(m.group(1)) for j in r["jobs"]]
    print(json.dumps({"jobs": rows})); sys.exit(0)
m = re.search(r"/issues/(\d+)/timeline", path)
if m:
    n = int(m.group(1))
    for e in data["issues"] + data["pulls"]:
        if e.get("issue", e.get("pr"))["number"] == n:
            print(json.dumps(e["timeline"])); sys.exit(0)
    print("[]"); sys.exit(0)
if "/pulls" in path:
    print(json.dumps([e["pr"] for e in data["pulls"]])); sys.exit(0)
if "/issues" in path:
    label = re.search(r"labels=([^&]+)", path)
    rows = [e["issue"] for e in data["issues"]]
    if label:
        from urllib.parse import unquote
        want = unquote(label.group(1))
        rows = [r for r in rows if want in [l["name"] for l in r["labels"]]]
    print(json.dumps(rows)); sys.exit(0)
print(json.dumps({"message": "Not Found"}))
''')
    FAKE_SRC = open(fake).read()
    for forge_type, env_name in (("github", "IFW_GH"), ("gitea", "IFW_TEA")):
        wrapper = os.path.join(tmp, f"fake-{forge_type}")
        with open(wrapper, "w") as handle:
            handle.write(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        os.chmod(wrapper, 0o755)
        data = os.path.join(tmp, f"{forge_type}-data.json")
        with open(data, "w") as handle:
            json.dump(build(forge_type), handle)
        proc = run(["collect", "--forge", forge_type, "--repo", "o/r"], {env_name: wrapper, "FAKE_DATA": data})
        check(proc.returncode == 0, f"live {forge_type} exits 0, got {proc.returncode}: {proc.stderr}")
        if proc.returncode == 0:
            check(project(json.loads(proc.stdout)) == project(gh_doc), f"live {forge_type} events match the fixture run")

        # tea api / gh api can print an error object. That must fail, not read as an empty timeline.
        err = os.path.join(tmp, f"{forge_type}-error.json")
        with open(err, "w") as handle:
            json.dump({"error": True}, handle)
        proc = run(["collect", "--forge", forge_type, "--repo", "o/r"], {env_name: wrapper, "FAKE_DATA": err})
        check(proc.returncode == 2 and proc.stdout == "", f"{forge_type} API error body exits 2 with no stdout, got {proc.returncode}")
        check("permission" in proc.stderr, f"{forge_type} error names the API message: {proc.stderr!r}")

# ---------------------------------------------------------------- render
import copy
import re

DATA_TAG = re.compile(r'<script id="tl-data" type="application/json">(.*?)</script>', re.S)


def payload(html_text):
    found = DATA_TAG.search(html_text)
    return json.loads(found.group(1)) if found else None


def doc_urls(doc):
    return {i["url"] for i in doc["issues"].values()}


html_gh = timeline.render_html(gh_doc)
html_gt = timeline.render_html(gt_doc)

# Self-contained: no external fetch of any kind. Tracker URLs are the only URLs.
for name, page, source in (("github", html_gh, gh_doc), ("gitea", html_gt, gt_doc)):
    check(page.startswith("<!DOCTYPE html>"), f"{name} page is a full HTML document")
    for bad in ('<script src', '<link', '@import', '<img', '<iframe', 'url(', 'fetch(', 'XMLHttpRequest'):
        check(bad not in page, f"{name} page contains {bad!r}")
    found_urls = set(re.findall(r"https?://[^\s\"'\\<>]+", page))
    check(found_urls <= doc_urls(source), f"{name} page holds a URL that is not a tracker link: {found_urls - doc_urls(source)}")
    check(payload(page) is not None and payload(page)["events"] == source["events"], f"{name} embedded events round-trip")
    check('name="viewport"' in page and "prefers-color-scheme: dark" in page, f"{name} page has viewport and dark theme")
    for preset in ("24h", "7d", "30d", "all"):
        check(f'data-preset="{preset}"' in page, f"{name} page has the {preset} preset")
    check('<p id="empty" hidden>' in page, f"{name} page hides the empty state when events exist")

# The GitHub and Gitea runs of one story build the same lanes.
check(payload(html_gh)["groups"] == payload(html_gt)["groups"], "GitHub and Gitea render the same lanes")
groups = payload(html_gh)["groups"]
check([g["key"] for g in groups] == ["50", None], f"batch 50, then the unbatched flow:status issue: {[g['key'] for g in groups]}")
lane_ids = [lane["id"] for lane in groups[0]["lanes"]]
check(lane_ids[0] == "batch:50" and "issue:51" in lane_ids and "issue:50" in lane_ids,
      f"batch lane leads the group; the tracking issue joins it: {lane_ids}")
check("batch/50-bar" in groups[0]["label"] and groups[0]["lanes"][0]["kind"] == "batch", f"group label shows the branch: {groups[0]}")
issue_lane = next(lane for lane in groups[0]["lanes"] if lane["id"] == "issue:51")
check(issue_lane["url"] == "https://x/issues/51" and issue_lane["kind"] == "issue", f"issue lane: {issue_lane}")
check(len(issue_lane["events"]) > 3 and all(isinstance(i, int) for i in issue_lane["events"]), "lane lists event indexes")

# A branch of null still groups by the batch key.
nobranch = copy.deepcopy(gh_doc)
nobranch["batches"]["50"]["branch"] = None
check(payload(timeline.render_html(nobranch))["groups"][0]["label"].startswith("Batch #50"), "null branch still labels the batch")

# A dataset spanning several batches: groups sort by batch number, unbatched events go last.
multi = copy.deepcopy(gh_doc)
multi["issues"]["71"] = {"title": "Other batch member", "url": "https://x/issues/71", "state": "open",
                         "labels": [], "status_at_start": None}
multi["batches"]["70"] = {"tracking": 70, "branch": "epic/70-x", "members": [71], "pr": None, "prs": []}
extra = [{"ts": "2026-05-03T10:00:00Z", "kind": "status", "issue": 71, "pr": None, "batch": 70, "actor": "a",
          "from": None, "to": "in-progress", "detail": None},
         {"ts": "2026-05-03T11:00:00Z", "kind": "status", "issue": 71, "pr": None, "batch": 70, "actor": "a",
          "from": "in-progress", "to": "needs-feedback", "detail": None},
         {"ts": "2026-05-04T10:00:00Z", "kind": "issue.opened", "issue": 80, "pr": None, "batch": None, "actor": "a",
          "from": None, "to": None, "detail": "Loose issue"}]
multi["events"] = multi["events"] + extra
mg = payload(timeline.render_html(multi))["groups"]
check([g["key"] for g in mg] == ["50", "70", None], f"groups across batches: {[g['key'] for g in mg]}")
check(mg[2]["label"] == "No batch", f"unbatched group label: {mg[2]['label']}")

# Lane kinds are data. The table names issue, batch, worktree and agent; an unknown kind still renders.
kinds_doc = copy.deepcopy(gh_doc)
kinds_doc["events"] = kinds_doc["events"] + [
    {"ts": "2026-05-02T09:00:00Z", "kind": "agent.spawned", "issue": 51, "pr": None, "batch": 50, "actor": None,
     "from": None, "to": None, "detail": "worker", "agent": "issue-worker", "session": "s1"},
    {"ts": "2026-05-02T09:30:00Z", "kind": "worktree.created", "issue": 51, "pr": None, "batch": 50, "actor": None,
     "from": None, "to": None, "detail": None, "worktree": "wt-51"},
    {"ts": "2026-05-02T10:00:00Z", "kind": "zzz.thing", "issue": None, "pr": None, "batch": None, "actor": None,
     "from": None, "to": None, "detail": "odd", "extra_key": 1}]
kp = payload(timeline.render_html(kinds_doc))
lane_kinds = {lane["kind"] for g in kp["groups"] for lane in g["lanes"]}
check({"agent", "worktree", "zzz", "issue", "batch"} <= lane_kinds, f"lane kinds: {lane_kinds}")
check(kp["kinds"]["agent"]["label"] == "Agent" and kp["kinds"]["worktree"]["label"] == "Worktree", f"kind table: {kp['kinds']}")
check(kp["kinds"]["zzz"]["style"] == "marks" and kp["kinds"]["zzz"]["label"], f"unknown kind gets a generic row: {kp['kinds']['zzz']}")
check(set(timeline.LANE_KINDS) >= {"issue", "batch", "worktree", "agent"}, "LANE_KINDS names the documented kinds")

# Hostile text cannot close the data tag or break out of the page.
hostile = copy.deepcopy(gh_doc)
hostile["issues"]["51"]["title"] = 'x </script><b id="pwn">&'
hostile["events"][0]["detail"] = "<!-- </SCRIPT>"
hostile["forge"]["repo"] = "<i>r</i>"
page = timeline.render_html(hostile)
check(payload(page)["issues"]["51"]["title"] == hostile["issues"]["51"]["title"], "title with </script> round-trips")
check(payload(page)["events"][0]["detail"] == "<!-- </SCRIPT>", "detail with a comment opener round-trips")
check('<b id="pwn">' not in page and "<i>r</i>" not in page, "markup in data is escaped")
check(page.lower().count("</script>") == 3, "only the page's own three script tags close")

# Empty and filtered-empty states.
empty_page = timeline.render_html({"version": 1, "forge": {"type": "github", "owner": "o", "repo": "r"},
                                   "range": {"since": None, "until": None}, "issues": {}, "batches": {},
                                   "convergence": [], "events": []})
check('<p id="empty">No events in this range.</p>' in empty_page, "empty events show the empty state")
check(payload(empty_page)["groups"] == [], "empty events give no lanes")

# Convergence strip: shown only when the data has it.
check('class="convergence"' in html_gh and "Gate 12: 3 issues filed, 5 closed" in html_gh,
      "convergence strip lists gate 12 in plain words")
check("Gate 13: 2 issues filed, closed count not recorded" in html_gh, "a null count says it was not recorded")
check("Gate 14: 0 issues filed, none closed yet" in timeline._convergence_strip([{"gate": 14, "filed": 0, "closed": 0}]),
      "zero counts read as plain language")
check("1 issue filed" in timeline._convergence_strip([{"gate": 1, "filed": 1, "closed": 1}]), "one issue is singular")
check('class="convergence"' not in empty_page, "no convergence data, no strip")

# --refresh writes a meta refresh; default writes none.
check('http-equiv="refresh"' not in html_gh, "no refresh by default")
check('<meta http-equiv="refresh" content="5">' in timeline.render_html(gh_doc, refresh=5), "--refresh 5 writes the meta tag")

# Legend: it comes from the same table as the bar colors, so every drawable state is in it.
STATES_WANTED = ["ready", "in-progress", "in-review", "awaiting-review", "needs-feedback", "blocked", "batched",
                 "deploying", "deploy-failed", "merged", "closed"]
lp = payload(html_gh)
check([s["key"] for s in lp["states"]] == STATES_WANTED or set(STATES_WANTED) <= {s["key"] for s in lp["states"]},
      f"legend states: {[s['key'] for s in lp['states']]}")
check(set(lp["states"][0]) >= {"key", "label"} and {s["key"] for s in lp["states"]} == set(timeline.STATE_COLORS),
      "the page's state list is the STATE_COLORS table")
for key in timeline.STATE_COLORS:
    check(f"--st-{key}:" in html_gh, f"CSS defines the color of state {key}")
for theme in (0, 1):
    colors = [pair[theme] for pair in timeline.STATE_COLORS.values()]
    check(len(set(colors)) == len(colors), f"state colors are distinct (theme {theme}): {colors}")
    marks = [pair[theme] for pair in timeline.MARKER_COLORS.values()]
    check(len(set(marks)) == len(marks), f"marker colors are distinct (theme {theme}): {marks}")
check(timeline.STATE_COLORS["merged"] != timeline.STATE_COLORS["closed"], "merged and closed differ")
check({m["key"] for m in lp["markers"]} == set(timeline.MARKER_COLORS) and all(m["label"] for m in lp["markers"]),
      "the page's marker list is the MARKER_COLORS table")
for key in timeline.MARKER_COLORS:
    check(f"--mk-{key}:" in html_gh, f"CSS defines the color of marker {key}")
# A status the table does not know still gets a legend entry through the data, not a silent grey bar.
odd = copy.deepcopy(gh_doc)
odd["events"] = odd["events"] + [{"ts": "2026-05-02T09:00:00Z", "kind": "status", "issue": 51, "pr": None, "batch": 50,
                                  "actor": None, "from": None, "to": "mystery", "detail": None}]
check("mystery" in [s["key"] for s in payload(timeline.render_html(odd))["states"]], "an unknown status is added to the legend")

# CLI: render from a file, from stdin, and behind collect.
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "events.json")
    with open(src, "w") as handle:
        json.dump(gh_doc, handle)
    out = os.path.join(tmp, "timeline.html")
    proc = run(["render", "--in", src, "--out", out])
    check(proc.returncode == 0 and proc.stdout == "" and os.path.exists(out), f"render --out writes a file: {proc.stderr}")
    if os.path.exists(out):
        with open(out) as handle:
            check(payload(handle.read())["events"] == gh_doc["events"], "rendered file carries the events")
    with open(src) as handle:
        proc = subprocess.run([sys.executable, SCRIPT, "render", "--refresh", "9"], stdin=handle,
                              capture_output=True, text=True)
    check(proc.returncode == 0 and 'content="9"' in proc.stdout, f"render reads stdin and prints HTML: {proc.stderr}")
    fx2 = os.path.join(tmp, "gt.json")
    with open(fx2, "w") as handle:
        json.dump(build("gitea"), handle)
    collected = run(["collect", "--fixture", fx2])
    piped = subprocess.run([sys.executable, SCRIPT, "render"], input=collected.stdout, capture_output=True, text=True)
    check(piped.returncode == 0 and payload(piped.stdout)["events"] == gt_doc["events"], "collect | render works")
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w") as handle:
        handle.write("{not json")
    check(run(["render", "--in", bad]).returncode == 2, "unreadable JSON exits 2")
    check(run(["render", "--in", os.path.join(tmp, "missing.json")]).returncode == 2, "a missing --in file exits 2")
    check(run(["render", "--in", src, "--refresh", "0"]).returncode == 5, "--refresh 0 exits 5")

# ---------------------------------------------------------------- local events (record-event hook)
# `collect --local-events <file>` merges the hook's JSON lines into the document.
LOCAL = [
    {"ts": "2026-05-01T10:30:00Z", "kind": "agent.spawned", "session": "s1", "agent": "a1", "issue": 51,
     "worktree": None, "branch": None, "outcome": None},
    {"ts": "2026-05-01T11:45:00Z", "kind": "agent.stopped", "session": "s1", "agent": "a1", "issue": 51,
     "worktree": "/w/51", "branch": "issue/51-collector", "outcome": "ready-to-merge"},
    {"ts": "2026-05-01T10:31:00Z", "kind": "worktree.added", "session": "s1", "agent": None, "issue": None,
     "worktree": "/w/51", "branch": "issue/51-collector", "outcome": None},
    {"ts": "2026-05-02T10:31:00Z", "kind": "worktree.removed", "session": "s1", "agent": None, "issue": None,
     "worktree": "/w/51", "branch": None, "outcome": None},
]
JUNK = ["not json", "[1]", json.dumps({"ts": "bad", "kind": "agent.spawned"}),
        json.dumps({"ts": "2026-05-01T10:00:00Z", "kind": "issue.closed", "issue": 51})]
IS_LOCAL = ("agent.", "worktree.")

# ---------------------------------------------------------------- watch and path (the /timeline skill)
# A test-only override (IFW_WATCH_FAST=1) lets watch use an interval below its 60-second floor.
FAST = {"IFW_WATCH_FAST": "1"}
with tempfile.TemporaryDirectory() as tmp:
    fx = os.path.join(tmp, "gh.json")
    with open(fx, "w") as handle:
        json.dump(build("github"), handle)
    log = os.path.join(tmp, "events.jsonl")
    with open(log, "w") as handle:
        handle.write("\n".join([json.dumps(e) for e in LOCAL] + JUNK) + "\n")
    base_events = json.loads(run(["collect", "--fixture", fx]).stdout)["events"]
    proc = run(["collect", "--fixture", fx, "--local-events", log])
    check(proc.returncode == 0, f"--local-events exits 0: {proc.stderr}")
    merged = json.loads(proc.stdout)["events"] if proc.returncode == 0 else []
    local = [e for e in merged if e["kind"].startswith(IS_LOCAL)]
    check(len(local) == 4, f"four local events merged, junk lines skipped, got {len(local)}")
    check(len(merged) == len(base_events) + 4, "tracker events are unchanged")
    check([e["ts"] for e in merged] == sorted(e["ts"] for e in merged), "merged events stay sorted by ts")
    check(all(set(e) >= {"ts", "kind", "issue", "pr", "batch", "actor", "from", "to", "detail"} for e in local),
          "local events carry the nine schema keys")
    stopped = [e for e in local if e["kind"] == "agent.stopped"]
    check(stopped and stopped[0]["outcome"] == "ready-to-merge" and stopped[0]["batch"] == 50,
          f"a local event gets its batch from the issue: {stopped}")
    # Range and issue filters apply to local events too.
    ranged = json.loads(run(["collect", "--fixture", fx, "--local-events", log, "--since", "2026-05-02T00:00:00Z"]).stdout)
    check([e["kind"] for e in ranged["events"] if e["kind"].startswith("worktree.")] == ["worktree.removed"],
          "--since filters local events")
    only = json.loads(run(["collect", "--fixture", fx, "--local-events", log, "--issue", "51"]).stdout)
    check([e["kind"] for e in only["events"] if e["kind"].startswith("worktree.")] == ["worktree.added", "worktree.removed"],
          "a worktree event joins its issue by branch, and its remove by path")
    other = json.loads(run(["collect", "--fixture", fx, "--local-events", log, "--issue", "52"]).stdout)
    check(not [e for e in other["events"] if e["kind"].startswith(IS_LOCAL)], "--issue drops local events of other issues")
    # No log, or a missing explicit file, falls back to the tracker alone.
    none = run(["collect", "--fixture", fx, "--local-events", os.path.join(tmp, "none.jsonl")])
    check(none.returncode == 0 and json.loads(none.stdout)["events"] == base_events,
          "a missing log file gives the tracker-only document")
    off = json.loads(run(["collect", "--fixture", fx, "--local-events", "off"]).stdout)
    check(off["events"] == base_events, "--local-events off skips the log")
    check(json.loads(run(["collect", "--fixture", fx]).stdout)["events"] == base_events,
          "--fixture without --local-events reads no log")

    # With no path, the log comes from the repository's common dir.
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    os.makedirs(os.path.join(repo, ".git", "issue-flow"))
    with open(os.path.join(repo, ".git", "issue-flow", "events.jsonl"), "w") as handle:
        handle.write(json.dumps(LOCAL[0]) + "\n")
    cwd_run = subprocess.run([sys.executable, SCRIPT, "collect", "--fixture", fx, "--local-events"],
                             capture_output=True, text=True, cwd=repo)
    check(cwd_run.returncode == 0 and any(e["kind"] == "agent.spawned" for e in json.loads(cwd_run.stdout)["events"]),
          f"--local-events with no path reads the repository's log: {cwd_run.stderr}")

    # `watch` forwards --local-events (a path, or `off`) to collect.
    for flag, expect_local in ((log, True), ("off", False)):
        page = os.path.join(tmp, f"local-{expect_local}.html")
        proc = run(["watch", "--fixture", fx, "--local-events", flag, "--interval", "0.3",
                    "--max-minutes", "0.02", "--out", page], env=FAST)
        kinds = {e["kind"] for e in (payload(open(page).read())["events"] if os.path.exists(page) else [])}
        check(proc.returncode == 0 and (("agent.spawned" in kinds) == expect_local),
              f"watch --local-events {flag if flag == 'off' else '<path>'} passes through: {proc.stderr}")

    out = os.path.join(tmp, "live.html")
    proc = run(["watch", "--fixture", fx, "--interval", "0.3", "--max-minutes", "0.02", "--out", out], env=FAST)
    check(proc.returncode == 0, f"watch exits 0 at the maximum: {proc.stderr}")
    check(proc.stderr.count("wrote ") >= 2, f"watch writes the page more than once: {proc.stderr!r}")
    if os.path.exists(out):
        with open(out) as handle:
            page = handle.read()
        check('http-equiv="refresh"' in page, "watch renders a page that reloads itself")
        check(payload(page)["events"] == timeline.normalize(build("github"))["events"], "watch page carries the events")
    else:
        failures.append("watch wrote no page")
    check(not [n for n in os.listdir(tmp) if n.startswith(".") or n.endswith(".tmp")], "watch leaves no temp file")
    # Floor and bad input.
    check(run(["watch", "--fixture", fx, "--interval", "30", "--out", out]).returncode == 5,
          "watch refuses an interval below 60 seconds")
    check(run(["watch", "--fixture", fx, "--max-minutes", "0", "--out", out], env=FAST).returncode == 5,
          "watch refuses a maximum of zero")
    check(run(["watch", "--interval", "60"]).returncode == 5, "watch without --forge or --fixture exits 5")
    check(run(["collect", "--fixture", fx, "--bogus"]).returncode == 5, "collect still rejects an unknown flag")
    # SIGTERM stops the loop cleanly.
    import signal
    import time
    child = subprocess.Popen([sys.executable, SCRIPT, "watch", "--fixture", fx, "--interval", "0.3",
                              "--out", out], env={**os.environ, **FAST}, stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    child.send_signal(signal.SIGTERM)
    try:
        code = child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        code = None
    check(code == 0, f"SIGTERM stops watch with exit 0 (got {code})")

# Guard: a log planted in the scratch repo's common dir changes no fixture or live-fake result.
with tempfile.TemporaryDirectory() as tmp:
    planted = os.path.join(HERMETIC, ".git", "issue-flow")
    os.makedirs(planted, exist_ok=True)
    with open(os.path.join(planted, "events.jsonl"), "w") as handle:
        handle.write(json.dumps({"ts": "2026-05-01T10:00:00Z", "kind": "agent.spawned", "agent": "planted",
                                 "issue": 51}) + "\nnot json\n")
    try:
        fx = os.path.join(tmp, "gh.json")
        with open(fx, "w") as handle:
            json.dump(build("github"), handle)
        fixture_run = run(["collect", "--fixture", fx])
        check(fixture_run.returncode == 0
              and not any(e["kind"].startswith("agent.") for e in json.loads(fixture_run.stdout)["events"]),
              "a planted events.jsonl does not reach a fixture collect")
        fake = os.path.join(tmp, "fake-gh.py")
        with open(fake, "w") as handle:
            handle.write(FAKE_SRC)
        wrapper = os.path.join(tmp, "fake-gh")
        with open(wrapper, "w") as handle:
            handle.write(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        os.chmod(wrapper, 0o755)
        data = os.path.join(tmp, "data.json")
        with open(data, "w") as handle:
            json.dump(build("github"), handle)
        live_run = run(["collect", "--forge", "github", "--repo", "o/r"], {"IFW_GH": wrapper, "FAKE_DATA": data})
        check(live_run.returncode == 0
              and not any(e["kind"].startswith("agent.") for e in json.loads(live_run.stdout)["events"]),
              f"a planted events.jsonl does not reach a live-fake collect: {live_run.stderr[:200]}")
    finally:
        os.unlink(os.path.join(planted, "events.jsonl"))

# `snapshot` collects and renders in one process. Its exit code is the only failure signal the skill reads.
with tempfile.TemporaryDirectory() as tmp:
    fx = os.path.join(tmp, "gh.json")
    with open(fx, "w") as handle:
        json.dump(build("github"), handle)
    out = os.path.join(tmp, "page.html")
    proc = run(["snapshot", "--fixture", fx, "--out", out])
    check(proc.returncode == 0, f"snapshot exits 0: {proc.stderr}")
    check(os.path.exists(out) and "tl-data" in open(out).read(), "snapshot writes the page")
    check(proc.stdout == "", f"snapshot prints nothing on stdout: {proc.stdout[:80]!r}")
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    check(len(lines) == 1, f"snapshot prints one stderr line: {proc.stderr!r}")
    n_events = len(build_doc_events := timeline.normalize(build("github"))["events"])
    check(lines and out in lines[0] and f"{n_events} events" in lines[0] and "lanes" in lines[0]
          and "problems" in lines[0], f"the summary names path, events, lanes and problems: {lines}")
    check('http-equiv="refresh"' not in open(out).read(), "snapshot adds no reload tag")
    check(payload(open(out).read())["events"] == build_doc_events, "snapshot page carries the events")
    # Collect flags pass through: --issue narrows the document.
    one = run(["snapshot", "--fixture", fx, "--issue", "51", "--out", out])
    check(one.returncode == 0 and payload(open(out).read())["events"] != build_doc_events,
          f"snapshot forwards collect flags: {one.stderr}")
    # A failed collect exits non-zero and leaves no page.
    gone = os.path.join(tmp, "gone.html")
    for label, argv, want in (("an unreadable fixture", ["--fixture", os.path.join(tmp, "none.json")], 2),
                              ("a bad --since", ["--fixture", fx, "--since", "soon"], 5),
                              ("no forge and no fixture", [], 5)):
        bad = run(["snapshot", *argv, "--out", gone])
        check(bad.returncode == want, f"snapshot with {label} exits {want}, got {bad.returncode}: {bad.stderr}")
        check(not os.path.exists(gone), f"snapshot with {label} writes no page")
    # An unwritable output path is a failure too.
    bad = run(["snapshot", "--fixture", fx, "--out", os.path.join(tmp, "no-dir", "p.html")])
    check(bad.returncode == 2, f"snapshot with an unwritable --out exits 2, got {bad.returncode}")
    # The default page path is the common dir, as for watch.
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    dflt = subprocess.run([sys.executable, SCRIPT, "snapshot", "--fixture", fx], cwd=repo, capture_output=True, text=True)
    check(dflt.returncode == 0 and os.path.exists(os.path.join(repo, ".git", "issue-flow", "timeline.html")),
          f"snapshot defaults --out to the common dir: {dflt.stderr}")

# The default page path is the common dir of the repository, seen from a linked worktree.
with tempfile.TemporaryDirectory() as tmp:
    git = lambda *a, cwd=tmp: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)  # noqa: E731
    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    linked = os.path.join(tmp, "wt")
    git("worktree", "add", "-q", linked)
    expect = os.path.realpath(os.path.join(tmp, ".git", "issue-flow", "timeline.html"))
    proc = subprocess.run([sys.executable, SCRIPT, "path"], cwd=linked, capture_output=True, text=True)
    check(proc.returncode == 0 and os.path.realpath(proc.stdout.strip()) == expect, f"path from a linked worktree: {proc.stdout!r}")
    fx = os.path.join(tmp, "fx.json")
    with open(fx, "w") as handle:
        json.dump(build("github"), handle)
    proc = subprocess.run([sys.executable, SCRIPT, "watch", "--fixture", fx, "--interval", "0.2", "--max-minutes", "0.01"],
                          cwd=linked, capture_output=True, text=True, env={**os.environ, **FAST})
    check(proc.returncode == 0 and os.path.exists(expect), f"watch defaults --out to the common dir: {proc.stderr}")
    outside = tempfile.mkdtemp()
    proc = subprocess.run([sys.executable, SCRIPT, "path"], cwd=outside, capture_output=True, text=True)
    check(proc.returncode == 2, f"path outside a git repository exits 2 (got {proc.returncode})")

# The skill exists with the frontmatter the manifest validator needs.
SKILL = os.path.join(ROOT, "issue-flow", "skills", "timeline", "SKILL.md")
check(os.path.exists(SKILL), "skills/timeline/SKILL.md exists")
if os.path.exists(SKILL):
    with open(SKILL) as handle:
        head = handle.read().split("---")[1]
    check("name: timeline" in head and "description:" in head, "the timeline skill has name and description")
try:
    import yaml  # noqa: F401
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "validate-manifests.py")],
                          capture_output=True, text=True)
    check(proc.returncode == 0, f"validate-manifests passes with the skill: {proc.stdout}{proc.stderr}")
except ImportError:
    pass

# The renderer draws local events as worktree and agent lanes under their batch.
lane_doc = copy.deepcopy(gh_doc)
lane_doc["events"] = sorted(lane_doc["events"] + [
    dict(timeline._event(e["ts"], e["kind"], e["issue"], batch=50 if e["issue"] else None),
         **{k: e[k] for k in ("session", "agent", "worktree", "branch", "outcome")}) for e in LOCAL],
    key=lambda e: e["ts"])
lane_model, _ = timeline.build_model(lane_doc)
lane_ids = {lane["id"]: lane["kind"] for group in lane_model for lane in group["lanes"]}
check(lane_ids.get("agent:a1") == "agent", f"agent lane exists: {lane_ids}")
check(lane_ids.get("worktree:/w/51") == "worktree", f"worktree lane exists: {lane_ids}")
check(payload(timeline.render_html(lane_doc))["events"] == lane_doc["events"], "page embeds local events")

# One agent is one lane. Only agent.spawned names the issue (from the brief), so every later local event
# of the same agent id inherits that issue, and with it the batch. The fixture is a real dogfood log.
DOGFOOD = os.path.join(ROOT, "scripts", "fixtures", "dogfood-events.jsonl")
dog_doc = {"batches": {"78": {"members": [86], "branch": "batch/78-x"}}, "issues": {}, "events": []}
timeline.merge_local(dog_doc, timeline.read_local_events(DOGFOOD))
dog_agents = [e for e in dog_doc["events"] if e["kind"].startswith("agent.")]
check(dog_agents and all(e["issue"] == 86 and e["batch"] == 78 for e in dog_agents),
      f"every event of a spawned agent carries its issue and batch: {[(e['agent'], e['issue'], e['batch']) for e in dog_agents]}")
dog_model, _ = timeline.build_model(dog_doc)
dog_lanes = [(group["key"], lane["id"]) for group in dog_model for lane in group["lanes"] if lane["kind"] == "agent"]
check(len({lane_id for _key, lane_id in dog_lanes}) == len(dog_lanes) == 3 and {key for key, _ in dog_lanes} == {"78"},
      f"one lane per agent id, all under batch 78: {dog_lanes}")
dog_filtered = {"batches": dog_doc["batches"], "issues": {}, "events": []}
timeline.merge_local(dog_filtered, timeline.read_local_events(DOGFOOD), only_issue=86)
check(len([e for e in dog_filtered["events"] if e["kind"].startswith("agent.")]) == len(dog_agents),
      "--issue keeps the later events of a spawned agent")

# ---------------------------------------------------------------- granular data (#80)
# Commits with capped diffs, classified comments, reviews, CI runs and the why-links.
FIXTURE = os.path.join(ROOT, "scripts", "fixtures", "batch-78.json")
with open(FIXTURE) as handle:
    B78 = json.load(handle)


def gh_comment(c):
    return {"event": "commented", "id": c["id"], "html_url": c["html_url"], "created_at": c["created_at"],
            "actor": c["user"], "body": c["body"]}


def batch78_bundle():
    """The real batch #78 comments plus fork PR #9 (issue #75's renderer), as GitHub payloads."""
    issues = [{"issue": issue_raw(78, "Workflow timeline", ["type:batch"], "2026-11-19T22:00:00Z",
                                  "- [ ] #75\n"), "timeline": [gh_comment(c) for c in B78["comments"]["78"]]}]
    for n in ("74", "75", "76", "77"):
        issues.append({"issue": issue_raw(int(n), f"member {n}", ["status:merged"], "2026-11-19T22:00:00Z"),
                       "timeline": [gh_comment(c) for c in B78["comments"][n]]})
    pr = B78["pr9"]["pr"]
    pull = {"pr": dict(pr, user={"login": "Crank-Git"}), "timeline": [], "commits": B78["pr9"]["commits"]}
    return {"forge": {"type": "github", "host": None, "owner": "o", "repo": "r"}, "issues": issues, "pulls": [pull]}


def kinds_of(doc, kind):
    return [e for e in doc["events"] if e["kind"] == kind]


# 1. Comments from this repo's own batch #78 classify by the prose fallback.
doc78 = timeline.normalize(batch78_bundle())
comments78 = kinds_of(doc78, "comment")
total78 = sum(len(rows) for key, rows in B78["comments"].items() if key in ("74", "75", "76", "77", "78"))
check(len(comments78) == total78, f"one comment event per comment: {len(comments78)} vs {total78}")
EXPECT = {"## Plan (PM)": "plan", "worker launched:": "worker.launched", "Sub-merged into": "sub-merge",
          "PM gate: rework requested": "gate.rework", "finding: batch cross-check": "cross-check"}
for key, rows in B78["comments"].items():
    if key == "80":
        continue
    for c in rows:
        want = next(role for prefix, role in EXPECT.items() if c["body"].startswith(prefix))
        got = [e for e in comments78 if e["url"] == c["html_url"]]
        check(len(got) == 1 and got[0]["role"] == want, f"comment {c['id']} should be {want}: {got and got[0]['role']}")
        if got:
            check(got[0]["excerpt"] == c["body"][:300] and got[0]["issue"] == int(key), f"excerpt/issue of {c['id']}")
check(not [e for e in comments78 if e["role"] == "other"], "no real batch comment falls to other")

C = timeline.classify_comment
check(C("**Amendment (batch cross-check addendum)**\ntext") == "plan-amendment", "amendment prefix")
check(C("status repair: relabeled #5") == "status.repair", "status repair prefix")
check(C("checkpoint 2: tests green") == "checkpoint", "checkpoint prefix")
check(C("PM gate: pass. All criteria met.") == "gate.pass", "gate pass prefix")
check(C("just chatting") == "other" and C("") == "other" and C(None) == "other", "other")
check(C("<!-- issue-flow:event role=question issue=5 -->\nWhich db?") == "question", "marker gives a role with no prose prefix")
check(C("<!-- issue-flow:event role=gate.pass issue=5 pr=7 -->\n## Plan (PM)") == "gate.pass", "marker wins over prose")
check(C("<!-- issue-flow:event role=bogus issue=5 -->\n## Plan (PM)") == "plan", "an unknown marker role falls back to prose")
check(timeline.parse_marker("x <!-- issue-flow:event role=gate.rework issue=5 pr=7 problem=legend -->") ==
      {"role": "gate.rework", "issue": 5, "pr": 7, "problem": "legend"}, "marker fields")
check(timeline.parse_marker("no marker") is None, "no marker")

# 2. Commits: per-file stats, capped patches, flags.
c1, c2 = (e for e in kinds_of(doc78, "commit"))
check(c1["sha"].startswith("14c46f1e") and c1["issue"] == 75 and c1["pr"] == 9 and c1["batch"] == 78, f"commit 1: {c1['sha'], c1['issue'], c1['pr'], c1['batch']}")
check(c1["subject"].startswith("issue-flow: render the timeline") and c1["author"] and c1["ts"] == "2026-11-19T22:34:54Z",
      f"commit 1 subject/author/ts: {c1['subject']!r} {c1['author']!r} {c1['ts']}")
check({f["path"] for f in c1["files"]} >= {"issue-flow/scripts/timeline.py", "scripts/test-timeline.py"}, "commit files carry paths")
check(all(isinstance(f["additions"], int) and f["status"] == "modified" and f["patch"] for f in c1["files"]), "files carry stats, status, patch")


def gh_commit(sha, message, files, parents=1, ts="2026-06-01T09:00:00Z", login="dev"):
    return {"sha": sha, "html_url": f"https://x/commit/{sha}", "parents": [{"sha": f"p{i}"} for i in range(parents)],
            "commit": {"message": message, "author": {"name": "Dev", "date": ts}}, "author": {"login": login},
            "files": [dict({"filename": "src/a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -1 +1 @@\n+x"}, **f)
                      for f in files]}


big_lines = "@@ -1,500 +1,500 @@\n" + "\n".join(f"+line {i}" for i in range(500))
wide = "@@ -1 +1 @@\n+" + "y" * 40000
capped = timeline.normalize({"forge": {"type": "github", "owner": "o", "repo": "r"}, "issues": [],
    "pulls": [{"pr": pr_raw(7, "T", "issue/9-x", "dev", "2026-06-01T08:00:00Z"), "timeline": [],
               "commits": [gh_commit("aa11", "big", [{"filename": "big.py", "patch": big_lines}, {"filename": "wide.py", "patch": wide},
                                                      {"filename": "small.py"}, {"filename": "bin.png", "patch": None}])]}]})
files = {f["path"]: f for f in kinds_of(capped, "commit")[0]["files"]}
check(files["big.py"]["truncated"] and len(files["big.py"]["patch"].split("\n")) == 400, f"400-line cap: {len(files['big.py']['patch'].split(chr(10)))}")
check(files["wide.py"]["truncated"] and len(files["wide.py"]["patch"].encode()) == 32768, "32 KB cap")
check(files["small.py"]["truncated"] is False and files["small.py"]["patch"].startswith("@@"), "a small patch is kept whole")
check(files["bin.png"]["patch"] is None and files["bin.png"]["truncated"] is False, "a file with no patch stays null")
check(files["big.py"]["additions"] == 1, "stats come from the forge, not from the cut patch")
small_budget = timeline.normalize(capped_bundle := {"forge": {"type": "github", "owner": "o", "repo": "r"}, "issues": [],
    "pulls": [{"pr": pr_raw(7, "T", "issue/9-x", "dev", "2026-06-01T08:00:00Z"), "timeline": [],
               "commits": [gh_commit("aa11", "m", [{"filename": "a.py", "patch": "@@\n+" + "z" * 300}, {"filename": "b.py", "patch": "@@\n+" + "z" * 300}])]}]},
    max_diff_bytes=400)
sb = kinds_of(small_budget, "commit")[0]["files"]
check(len(sb[0]["patch"].encode()) == 304 and not sb[0]["truncated"], f"first file fits the document budget: {len(sb[0]['patch'])}")
check(len(sb[1]["patch"].encode()) == 96 and sb[1]["truncated"], f"second file is cut to what is left: {len(sb[1]['patch'])}")
nod = kinds_of(timeline.normalize(capped_bundle, diffs=False), "commit")[0]["files"]
check(all(f["patch"] is None and f["truncated"] is False for f in nod) and nod[0]["additions"] == 1, "--no-diffs omits patches, keeps stats")

# 3. Reviews, review comments, CI runs: both forges give the same events.
def story2(forge_type):
    gh = forge_type == "github"
    ts = lambda hms: f"2026-06-01T{hms}Z" if gh else f"2026-06-01T{hms}+00:00"  # noqa: E731
    commit = lambda sha, msg, files, **kw: gh_commit(sha, msg, files, ts=ts(kw.pop("at")), **kw)  # noqa: E731
    commits = [
        commit("c100", "feat: add the flag\n\nBody of the first commit.", [{"filename": "src/a.py"}, {"filename": "tests/test_a.py"}], at="09:00:00"),
        commit("c200", "fix the flag name", [{"filename": "src/a.py"}], at="10:00:00"),
        commit("c300", "rename\n\nChange: refactor\nWhy: One name for the flag.\nFixes: https://x/issues/51#issuecomment-9", [{"filename": "src/a.py"}], at="10:30:00"),
        commit("c400", "more tests", [{"filename": "tests/test_b.py"}, {"filename": "scripts/test-c.py"}], at="10:40:00"),
        commit("c500", "notes", [{"filename": "README.md"}, {"filename": "docs/x.md"}], at="10:45:00"),
        commit("c600", "Merge branch 'dev'", [{"filename": "src/z.py"}], parents=2, at="10:50:00"),
    ]
    if not gh:
        for c in commits:
            c["diff"] = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-old\n+new\n+newer\n"
            c["files"] = [{"filename": f["filename"], "status": "modified"} for f in c["files"]]
    review = {"id": 7, "state": "CHANGES_REQUESTED" if gh else "REQUEST_CHANGES", "body": "Rename the flag.",
              "user": {"login": "rev"}, "submitted_at": ts("09:30:00"), "html_url": "https://x/pull/60#pullrequestreview-7", "comments_count": 1}
    approve = dict(review, id=8, state="APPROVED", body="", submitted_at=ts("11:00:00"), html_url="https://x/pull/60#pullrequestreview-8")
    pending = dict(review, id=9, state="PENDING", submitted_at=None)
    rc = {"id": 70, "pull_request_review_id": 7, "path": "src/a.py", "body": "This name is unclear.", "user": {"login": "rev"},
          "created_at": ts("09:30:00"), "html_url": "https://x/pull/60#discussion_r70"}
    rc.update({"line": 12} if gh else {"position": 12, "resolver": {"login": "dev"}})
    fail_run = {"id": 900, "name": "ci", "conclusion": "failure", "status": "completed", "head_sha": "c100", "html_url": "https://x/runs/900"}
    ok_run = {"id": 901, "name": "ci", "conclusion": "success", "status": "completed", "head_sha": "c100", "html_url": "https://x/runs/901"}
    if gh:
        fail_run.update({"run_started_at": ts("09:40:00"), "updated_at": ts("09:45:30")})
        ok_run.update({"run_started_at": ts("10:55:00"), "updated_at": ts("10:56:00")})
    else:
        fail_run.update({"started_at": ts("09:40:00"), "completed_at": ts("09:45:30"), "path": ".gitea/workflows/ci.yaml"})
        fail_run.pop("name")
        ok_run.update({"started_at": ts("10:55:00"), "completed_at": ts("10:56:00"), "path": ".gitea/workflows/ci.yaml"})
        ok_run.pop("name")
    jobs = [{"name": "lint", "conclusion": "success", "steps": [{"name": "ruff", "conclusion": "success", "number": 1}]},
            {"name": "test", "conclusion": "failure", "steps": [{"name": "setup", "conclusion": "success", "number": 1},
                                                               {"name": "pytest", "conclusion": "failure", "number": 2}]}]
    marked = ("<!-- issue-flow:event role=gate.rework issue=51 pr=60 problem=legend -->\nPM gate: rework requested." if gh else
              "<!-- issue-flow:event role=gate.rework issue=51 pr=60 problem=legend -->\nPM gate: rework requested.")
    tl = [{"event": "commented", "id": 9, "html_url": "https://x/issues/51#issuecomment-9", "created_at": ts("10:20:00"), "actor": {"login": "pm"}, "body": marked}] if gh else \
         [{"type": "comment", "id": 9, "html_url": "https://x/issues/51#issuecomment-9", "created_at": ts("10:20:00"), "user": {"login": "pm"}, "body": marked}]
    return {"forge": {"type": forge_type, "host": "h", "owner": "o", "repo": "r"},
            "issues": [{"issue": issue_raw(50, "Batch", ["type:batch"], ts("08:00:00"), "- [ ] #51\n"), "timeline": []},
                       {"issue": issue_raw(51, "Flag", ["status:in-review"], ts("08:00:00")), "timeline": []}],
            "pulls": [{"pr": dict(pr_raw(60, "Add flag", "issue/51-flag", "batch/50-bar", ts("08:30:00")), head={"ref": "issue/51-flag", "sha": "c100"}), "timeline": tl,
                       "commits": commits, "reviews": [review, approve, pending], "review_comments": [rc],
                       "runs": [{"run": fail_run, "jobs": jobs}, {"run": ok_run, "jobs": []}]}]}


s_gh, s_gt = timeline.normalize(story2("github")), timeline.normalize(story2("gitea"))
for label, sdoc in (("github", s_gh), ("gitea", s_gt)):
    revs = kinds_of(sdoc, "review")
    check([(r["state"], r["actor"], r["excerpt"]) for r in revs] == [("changes_requested", "rev", "Rename the flag."), ("approved", "rev", "")],
          f"{label} reviews (pending is skipped): {[(r['state'], r['excerpt']) for r in revs]}")
    check(revs and revs[0]["url"].endswith("pullrequestreview-7") and revs[0]["issue"] == 51 and revs[0]["pr"] == 60, f"{label} review keys")
    rcs = kinds_of(sdoc, "review.comment")
    check(len(rcs) == 1 and rcs[0]["path"] == "src/a.py" and rcs[0]["line"] == 12 and rcs[0]["excerpt"] == "This name is unclear."
          and rcs[0]["url"].endswith("discussion_r70"), f"{label} review comment: {rcs}")
    check(rcs and rcs[0]["resolved"] is (None if label == "github" else True), f"{label} resolved flag: {rcs and rcs[0]['resolved']}")
    runs = kinds_of(sdoc, "ci.run")
    check(len(runs) == 2 and runs[0]["workflow"] == "ci" and runs[0]["conclusion"] == "failure" and runs[0]["duration"] == 330
          and runs[0]["failed_job"] == "test" and runs[0]["failed_step"] == "pytest" and runs[0]["ts"] == "2026-06-01T09:45:30Z",
          f"{label} failing run: {runs[:1]}")
    check(runs[1]["conclusion"] == "success" and runs[1]["failed_job"] is None and runs[1]["failed_step"] is None, f"{label} passing run")
    check(runs[0]["started"] == "2026-06-01T09:40:00Z" and runs[0]["completed"] == "2026-06-01T09:45:30Z", f"{label} run times")

keyset = lambda d: [(e["ts"], e["kind"], e.get("role"), e.get("state"), e.get("change"), e.get("addresses"), e.get("sha")) for e in d["events"]]  # noqa: E731
check(keyset(s_gh) == keyset(s_gt), f"both forges give the same granular events:\n{keyset(s_gh)}\n{keyset(s_gt)}")
gt_files = [f for e in kinds_of(s_gt, "commit") for f in e["files"]][0]
check(gt_files["additions"] == 2 and gt_files["deletions"] == 1 and gt_files["patch"].startswith("@@ -1 +1,2 @@"),
      f"gitea stats and patch come from the .diff text: {gt_files}")

# 4. Why links: change, why, addresses, problems.
for label, sdoc in (("github", s_gh), ("gitea", s_gt)):
    cm = {e["sha"]: e for e in kinds_of(sdoc, "commit")}
    check({k: v["change"] for k, v in cm.items()} == {"c100": "feature", "c200": "fix", "c300": "refactor", "c400": "test", "c500": "docs", "c600": "merge"},
          f"{label} change: {({k: v['change'] for k, v in cm.items()})}")
    probs = sdoc["issues"]["51"]["problems"]
    ids = [p["id"] for p in probs]
    check(ids == ["51:auto-1", "51:auto-2", "51:legend"], f"{label} problem ids and order: {ids}")
    check(cm["c100"]["addresses"] == [], f"{label} a commit before any problem addresses nothing")
    check(cm["c200"]["addresses"] == ["51:auto-1", "51:auto-2"], f"{label} inferred addresses: {cm['c200']['addresses']}")
    check(cm["c300"]["addresses"] == ["51:legend"], f"{label} Fixes trailer wins: {cm['c300']['addresses']}")
    check(cm["c400"]["addresses"] == [] and cm["c500"]["addresses"] == [], f"{label} no open problem, no addresses")
    check(cm["c300"]["why"] == "One name for the flag.", f"{label} why from the trailer: {cm['c300']['why']!r}")
    check(cm["c100"]["why"] == "Body of the first commit." and cm["c100"]["body"] == "Body of the first commit.", f"{label} why falls back to the body")
    check(cm["c100"]["subject"] == "feat: add the flag", f"{label} subject")
    by = {p["id"]: p for p in probs}
    check(by["51:auto-1"]["source"] == "review" and by["51:auto-1"]["state"] == "changes_requested" and by["51:auto-1"]["role"] is None
          and by["51:auto-1"]["resolved_by"] == ["c200"] and by["51:auto-1"]["resolved"] is True and by["51:auto-1"]["text"] == "Rename the flag.",
          f"{label} review problem: {by['51:auto-1']}")
    check(by["51:auto-2"]["source"] == "ci.run" and by["51:auto-2"]["state"] == "failure" and "pytest" in by["51:auto-2"]["text"]
          and by["51:auto-2"]["url"] == "https://x/runs/900", f"{label} CI problem: {by['51:auto-2']}")
    check(by["51:legend"]["source"] == "comment" and by["51:legend"]["role"] == "gate.rework" and by["51:legend"]["resolved_by"] == ["c300"]
          and set(by["51:legend"]) >= {"id", "source", "role", "state", "text", "url", "ts", "resolved_by", "resolved"}, f"{label} comment problem: {by['51:legend']}")

# The real case: #75's legend rework, then the fix commit (fork PR #9).
rework = next(e for e in comments78 if e["role"] == "gate.rework")
probs75 = doc78["issues"]["75"]["problems"]
check(len(probs75) == 1 and probs75[0]["source"] == "comment" and probs75[0]["role"] == "gate.rework"
      and probs75[0]["url"] == rework["url"] and probs75[0]["text"].startswith("PM gate: rework requested"), f"#75 problem: {probs75}")
check(probs75 and probs75[0]["resolved_by"] == [c2["sha"]] and probs75[0]["resolved"], f"#75 problem is resolved by the legend fix: {probs75}")
check(c1["addresses"] == [] and c1["change"] == "feature", f"#75 first commit: {c1['change']} {c1['addresses']}")
check(c2["addresses"] == [probs75[0]["id"]] and c2["change"] == "fix", f"#75 legend fix commit: {c2['change']} {c2['addresses']}")
check(timeline.parse_trailers("s\n\nbody\n\nChange: Fix\nFixes: a\nFixes: b\nWhy: because") ==
      {"change": "fix", "why": "because", "fixes": ["a", "b"]}, "trailer grammar")
check(timeline.parse_trailers("s\n\nChange: nonsense\nunrelated: x") == {"change": None, "why": None, "fixes": []}, "a bad Change value is ignored")

# Filters keep the problem list whole; --issue keeps only that issue's events.
only = timeline.normalize(story2("github"), only_issue=51)
check(all(e["issue"] == 51 for e in only["events"]) and only["issues"]["51"]["problems"], "problems survive --issue")
check(all(e["kind"] != "commit" for e in timeline.normalize(story2("github"), since="2026-06-02T00:00:00Z")["events"]), "since bounds commits")

# 5. CLI: --no-diffs, --max-diff-bytes, and a live run against a fake gh/tea for both forges.
with tempfile.TemporaryDirectory() as tmp:
    for forge_type, env_name in (("github", "IFW_GH"), ("gitea", "IFW_TEA")):
        bundle_path = os.path.join(tmp, f"story-{forge_type}.json")
        with open(bundle_path, "w") as handle:
            json.dump(story2(forge_type), handle)
        proc = run(["collect", "--fixture", bundle_path, "--no-diffs"])
        got = json.loads(proc.stdout) if proc.returncode == 0 else {"events": []}
        check(proc.returncode == 0 and all(f["patch"] is None for e in kinds_of(got, "commit") for f in e["files"]), f"{forge_type} --no-diffs via CLI: {proc.stderr}")
        proc = run(["collect", "--fixture", bundle_path, "--max-diff-bytes", "5"])
        got = json.loads(proc.stdout) if proc.returncode == 0 else {"events": []}
        check(proc.returncode == 0 and any(f["truncated"] for e in kinds_of(got, "commit") for f in e["files"]), f"{forge_type} --max-diff-bytes via CLI")
        check(run(["collect", "--fixture", bundle_path, "--max-diff-bytes", "-1"]).returncode == 5, "a negative --max-diff-bytes exits 5")

    fake = os.path.join(tmp, "fake-live.py")
    with open(fake, "w") as handle:
        handle.write(FAKE_SRC)
    for forge_type, env_name in (("github", "IFW_GH"), ("gitea", "IFW_TEA")):
        wrapper = os.path.join(tmp, f"live-{forge_type}")
        with open(wrapper, "w") as handle:
            handle.write(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        os.chmod(wrapper, 0o755)
        data = os.path.join(tmp, f"story-{forge_type}.json")
        proc = run(["collect", "--forge", forge_type, "--repo", "o/r"], {env_name: wrapper, "FAKE_DATA": data})
        check(proc.returncode == 0, f"live {forge_type} granular collect exits 0: {proc.stderr[:300]}")
        if proc.returncode == 0:
            live = json.loads(proc.stdout)
            check(keyset(live) == keyset(timeline.normalize(story2(forge_type))), f"live {forge_type} granular events match the bundle run")
            check(live["issues"]["51"]["problems"] == timeline.normalize(story2(forge_type))["issues"]["51"]["problems"], f"live {forge_type} problems match")

# ---------------------------------------------------------------- drill-down (the renderer)
# The page draws the panel in the browser; these cases check the data and markup it draws from.
def lane_of(page_data, lane_id):
    return next(lane for g in page_data["groups"] for lane in g["lanes"] if lane["id"] == lane_id)


def with_agent_message(doc):
    doc = copy.deepcopy(doc)
    doc["events"].append({"ts": "2026-06-01T12:00:00Z", "kind": "agent.messaged", "issue": 51, "pr": None, "batch": 50, "actor": None,
                          "from": None, "to": None, "detail": "rework", "agent": "issue-worker", "session": "s1"})
    return doc


dd_doc = with_agent_message(s_gh)
dd_page = timeline.render_html(dd_doc)
dd = payload(dd_page)

# 1. Summary counts per lane, computed over every event of the lane.
sm = dd["summaries"]["issue:51"]
check(sm["commits"] == 6 and sm["files"] == 7 and sm["rework_rounds"] == 1 and sm["review_rounds"] == 1 and sm["ci_failures"] == 1,
      f"issue 51 summary: {sm}")
check(sm["additions"] == sum(f["additions"] for e in kinds_of(dd_doc, "commit") for f in e["files"])
      and sm["deletions"] == sum(f["deletions"] for e in kinds_of(dd_doc, "commit") for f in e["files"]), f"summary lines: {sm}")

# 2. What needed fixing: the problems and their fix commits reach the page unchanged.
check(dd["issues"]["51"]["problems"] == dd_doc["issues"]["51"]["problems"] and len(dd["issues"]["51"]["problems"]) == 3,
      "problems are embedded for the panel")
check(all(p["url"] and "resolved_by" in p for p in dd["issues"]["51"]["problems"]), "each problem has a link and resolved_by")

# 3. Commits keep their diffs for lazy rendering, with the truncated flag.
cap_page = payload(timeline.render_html(capped))
cap_commit = next(e for e in cap_page["events"] if e["kind"] == "commit")
check({f["path"]: f["truncated"] for f in cap_commit["files"]}["big.py"] is True, "a truncated patch keeps its flag in the page data")
for needle in ("truncated", "details", "toggle"):
    check(needle in timeline.PAGE_JS, f"page script handles {needle!r}")

# 4. Markers: each event kind maps to a marker, every marker is in the legend table.
mk = dict(zip((e["kind"] + ":" + str(e.get("role") or e.get("change") or e.get("state") or e.get("conclusion")) for e in dd_doc["events"]), dd["marks"]))
for key, want in (("comment:gate.rework", "rework"), ("commit:fix", "fix"), ("ci.run:failure", "ci"),
                  ("review:changes_requested", "review"), ("agent.messaged:None", "msg")):
    check(mk.get(key) == want, f"{key} draws marker {want}: {mk.get(key)}")
check(mk.get("ci.run:success") is None and mk.get("commit:feature") is None, "a passing run and a plain commit draw no issue-lane marker")
check(len(dd["marks"]) == len(dd_doc["events"]), "one marks entry per event")
check({m for m in dd["marks"] if m} <= set(timeline.MARKER_COLORS), "every marker in use is in MARKER_COLORS")
for key in ("rework", "fix", "ci", "review", "note", "msg"):
    check(key in timeline.MARKER_COLORS and key in timeline.MARKER_LABELS, f"marker {key} is in the colour and label tables")
    check({m["key"] for m in dd["markers"]} >= {key}, f"the legend lists marker {key}")
    check(f".mk-{key}" in timeline.PAGE_CSS, f"CSS styles marker {key}")

# 5. Kind filter: every log kind belongs to one group, the groups drive the filter buttons.
check([g["key"] for g in dd["logGroups"]][:5] == ["commits", "comments", "reviews", "ci", "agents"], f"log groups: {dd['logGroups']}")
lg = dict(zip((e["kind"] for e in dd_doc["events"]), dd["logs"]))
check(lg["commit"] == "commits" and lg["comment"] == "comments" and lg["review"] == "reviews" and lg["review.comment"] == "reviews"
      and lg["ci.run"] == "ci" and lg["agent.messaged"] == "agents", f"log group per kind: {lg}")
for g in dd["logGroups"]:
    check(f'data-log="{g["key"]}"' in dd_page, f"the panel has a {g['key']} filter button")
check('id="panel"' in dd_page and 'id="log"' in dd_page, "the page has the panel markup")

# 6. Batch lane drill-down: cross-check findings, amendments and the batch merge.
b78 = copy.deepcopy(doc78)
b78["events"].append({"ts": "2026-11-20T01:00:00Z", "kind": "batch.merged", "issue": 78, "pr": 8, "batch": 78, "actor": "pm",
                      "from": None, "to": None, "detail": None, "base": "dev", "head": "batch/78-x"})
b78["events"].append({"ts": "2026-11-20T01:00:00Z", "kind": "ci.run", "issue": 78, "pr": 8, "batch": 78, "actor": None, "from": None,
                      "to": None, "detail": None, "workflow": "validate", "conclusion": "failure", "started": None, "completed": None,
                      "duration": None, "url": "https://x/run/1", "sha": "abc", "failed_job": "t", "failed_step": "s"})
b78["events"].append({"ts": "2026-11-19T22:50:00Z", "kind": "comment", "issue": 75, "pr": None, "batch": 78, "actor": "pm", "from": None,
                      "to": None, "detail": None, "role": "plan-amendment", "excerpt": "**Amendment (batch cross-check addendum)**",
                      "url": "https://x/issues/75#issuecomment-1", "problem": None})
b78["events"].sort(key=lambda e: e["ts"])
b78_page = payload(timeline.render_html(b78))
bl = lane_of(b78_page, "batch:78")
roles = {b78_page["events"][i]["role"] for i in b78_page["notes"]["78"]}
check({"cross-check", "plan-amendment"} <= roles, f"the batch panel lists cross-check and amendment comments: {roles}")
check(any(b78_page["events"][i].get("role") == "cross-check" for i in bl["events"]), "the batch lane holds the cross-check comment")
check(any(b78_page["events"][i]["kind"] == "batch.merged" for i in bl["events"]), "the batch lane holds the batch merge")
check(any(b78_page["events"][i]["kind"] == "ci.run" for i in bl["events"]), "the batch lane holds batch CI runs")
check(b78_page["summaries"]["batch:78"]["ci_failures"] == 1, "the batch summary counts the failed batch run")
bmarks = {b78_page["marks"][i] for i in bl["events"]}
check({"note", "batch", "ci"} <= bmarks, f"batch lane markers: {bmarks}")
check(not [i for i in lane_of(b78_page, "issue:75")["events"] if b78_page["events"][i].get("role") == "cross-check"],
      "the cross-check comment stays off member lanes")

# 7. Escaping: hostile patch and comment text cannot break the page.
hostile2 = copy.deepcopy(s_gh)
evil = '</script><img src=x onerror=alert(1)> & "q"'
for e in hostile2["events"]:
    if e["kind"] == "commit":
        e["files"][0]["patch"] = "@@ -1 +1 @@\n+" + evil
        e["subject"] = evil
        break
for e in hostile2["events"]:
    if e["kind"] == "comment":
        e["excerpt"] = evil
        break
hp = timeline.render_html(hostile2)
check(hp.lower().count("</script>") == 3 and "<img" not in hp, "hostile patch and comment text cannot close the script or add markup")
check(any(evil in (f["patch"] or "") for e in payload(hp)["events"] if e["kind"] == "commit" for f in e["files"]), "hostile patch round-trips as data")
check(any(e.get("excerpt") == evil for e in payload(hp)["events"]), "hostile comment round-trips as data")
check("innerHTML" not in hp and "insertAdjacentHTML" not in hp and "document.write" not in hp, "the page never writes HTML from data")
for bad in ("<script src", "<link", "@import", "<iframe", "url(", "fetch(", "XMLHttpRequest"):
    check(bad not in hp, f"drill-down page contains {bad!r}")
allowed = {e["url"] for e in hostile2["events"] if e.get("url")} | doc_urls(hostile2) \
    | {p["url"] for i in hostile2["issues"].values() for p in i.get("problems", [])}
found = set(re.findall(r"https?://[^\s\"'\\<>]+", hp))
check(found <= allowed, f"the page holds only tracker URLs: {found - allowed}")

# 8. Themes and narrow widths.
check("@media (max-width" in timeline.PAGE_CSS and "#panel" in timeline.PAGE_CSS, "panel has a narrow layout")
check(timeline.PAGE_CSS.count("--mk-note:") == 3 and timeline.PAGE_CSS.count("--mk-msg:") == 3,
      "new markers set in the light, the dark and the auto-dark blocks")

# ---------------------------------------------------------------- rework r1 (#81)
# Fix 3: a trailer block is never a `why`.
def why_of(message):
    doc = timeline.normalize({"forge": {"type": "github", "owner": "o", "repo": "r"}, "issues": [],
        "pulls": [{"pr": pr_raw(7, "T", "issue/9-x", "dev", "2026-06-01T08:00:00Z"), "timeline": [],
                   "commits": [gh_commit("bb11", message, [{"filename": "src/a.py"}])]}]})
    return kinds_of(doc, "commit")[0]["why"]


check(why_of("subject\n\nCo-Authored-By: Claude <noreply@anthropic.com>") is None, "a lone Co-Authored-By trailer is not a why")
check(why_of("subject\n\nSigned-off-by: A <a@x>\nCo-Authored-By: B <b@x>") is None, "trailer lines are not a why")
check(why_of("subject\n\nChange: fix\nCo-Authored-By: B <b@x>") is None, "Change plus Co-Authored-By leave nothing")
check(why_of("subject\n\nBecause the flag was unclear.\n\nCo-Authored-By: B <b@x>") == "Because the flag was unclear.", "the body stays, the trailer block goes")
check(why_of("subject\n\nBecause X.\n\nWhy: Stated reason.\nCo-Authored-By: B <b@x>") == "Stated reason.", "a Why: trailer still wins")
check(why_of("subject\n\nNote: this line is prose.\nMore prose.") == "Note: this line is prose.\nMore prose.", "a mixed paragraph is not a trailer block")

# Fix 2: a problem carries the full source text; the log entry keeps the excerpt.
long_text = ("PM gate: rework requested. " + "word " * 120).strip()
rw = timeline.normalize({"forge": {"type": "github", "owner": "o", "repo": "r"},
    "issues": [{"issue": issue_raw(51, "Flag", ["status:in-review"], "2026-06-01T08:00:00Z"), "timeline": [
                   {"event": "commented", "id": 9, "html_url": "https://x/issues/51#issuecomment-9", "created_at": "2026-06-01T10:20:00Z",
                    "actor": {"login": "pm"}, "body": "<!-- issue-flow:event role=gate.rework issue=51 -->\n" + long_text}]}],
    "pulls": [{"pr": pr_raw(60, "Add flag", "issue/51-flag", "dev", "2026-06-01T08:30:00Z"), "timeline": [], "commits": [],
               "reviews": [{"id": 7, "state": "CHANGES_REQUESTED", "body": long_text, "user": {"login": "rev"},
                            "submitted_at": "2026-06-01T09:30:00Z", "html_url": "https://x/pull/60#pullrequestreview-7"}]}]})
by_source = {p["source"]: p for p in rw["issues"]["51"]["problems"]}
check(by_source["comment"]["text"] == long_text, f"a rework problem carries the full comment text: {by_source['comment']['text'][:40]!r}")
check(by_source["review"]["text"] == long_text, "a review problem carries the full review text")
check(all(len(e["excerpt"]) == 300 for e in rw["events"] if e["kind"] in ("comment", "review") and e.get("excerpt")),
      "log entries keep the 300-character excerpt")
check(not any(k.startswith("_") for e in rw["events"] for k in e), "no private key leaks into events")

# Fix 1: the time axis thins its labels to the width it has.
check("function axisTicks" in timeline.PAGE_JS and "resize" in timeline.PAGE_JS, "the axis re-thins on resize")

# ---------------------------------------------------------------- design pass (#83)
def _lin(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _rgb(color):
    color = color.lstrip("#")
    return [int(color[i:i + 2], 16) for i in (0, 2, 4)]


def luminance(color):
    r, g, b = (_lin(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def lab(color):
    r, g, b = (_lin(c) for c in _rgb(color))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a, b):
    """CIE76 colour distance. The test asks for 20 or more between a marker and a state."""
    return sum((p - q) ** 2 for p, q in zip(lab(a), lab(b))) ** 0.5


page83 = timeline.render_html(gh_doc)
head83, body83 = page83.split("<body>", 1)

# 1. Theme: three states, tokens in both themes, applied before first paint.
check(set(timeline.THEME_TOKENS) == {"light", "dark"}, "THEME_TOKENS holds a light and a dark table")
check(':root[data-theme="dark"]' in timeline.PAGE_CSS and ':root:not([data-theme="light"])' in timeline.PAGE_CSS
      and ':root[data-theme="light"]' in timeline.PAGE_CSS, "dark tokens sit under data-theme; the media block yields to an explicit light")
check(timeline.PAGE_CSS.count("color-scheme:") >= 3, "each theme sets color-scheme")
check(timeline.PAGE_CSS.count("--bg:") == 3 and timeline.PAGE_CSS.count("--accent:") == 3, "tokens are set in light, dark and auto-dark")
check('id="theme"' in body83, "the header has a theme button")
head_script = head83.split("</style>")[-1]
check("localStorage" in head_script and "data-theme" in head_script, "an inline head script applies the stored theme before first paint")
check(re.search(r"try\s*\{[^}]*localStorage", head_script) is not None, "the head script reads storage inside try")
check(re.search(r"try\s*\{[^}]*localStorage\.setItem", timeline.PAGE_JS) is not None, "the toggle writes storage inside try")
for word in ('"auto"', '"light"', '"dark"', "aria-label"):
    check(word in timeline.PAGE_JS, f"the toggle script handles {word}")

# 2. Contrast: every text pair in both themes is at least 4.5:1, computed from the token tables.
PAIRS = (("fg", "bg"), ("fg", "panel"), ("muted", "bg"), ("muted", "panel"), ("accent", "bg"), ("accent", "panel"),
         ("visited", "bg"), ("visited", "panel"), ("add", "bg"), ("add", "panel"), ("del", "bg"), ("del", "panel"),
         ("fg", "add-bg"), ("fg", "del-bg"))
for theme, table in timeline.THEME_TOKENS.items():
    for fg_key, bg_key in PAIRS:
        ratio = contrast(table[fg_key], table[bg_key])
        check(ratio >= 4.5, f"{theme}: {fg_key} on {bg_key} is {ratio:.2f}:1, need 4.5")

# 3. No colour outside the token block: the layout CSS and the script use var(--token) only.
check(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", timeline.LAYOUT_CSS) is None, "no hard-coded colour in the layout CSS")
check(re.search(r"#[0-9a-fA-F]{6}\b|rgba?\(", timeline.PAGE_JS) is None, "no hard-coded colour in the page script")
check(timeline.PAGE_CSS == timeline.TOKEN_CSS + timeline.LAYOUT_CSS, "the page CSS is the token block plus the layout block")

# 4. Markers: none equals or sits near a state colour in either theme (CIE76 distance of at least 20).
for index, theme in enumerate(("light", "dark")):
    for mkey, mpair in timeline.MARKER_COLORS.items():
        for skey, spair in timeline.STATE_COLORS.items():
            distance = delta_e(mpair[index], spair[index])
            check(distance >= 20, f"{theme}: marker {mkey} is only {distance:.1f} from state {skey}")
        check(contrast(mpair[index], timeline.THEME_TOKENS[theme]["bg"]) >= 3, f"{theme}: marker ring {mkey} shows on the page background")
check({m["group"] for m in payload(page83)["markers"]} == {"events", "problems"}, "markers fall into the Events and Problems legend rows")
check(set(timeline.MARKER_COLORS) == {k for keys in timeline.MARKER_GROUPS.values() for k in keys}, "every marker belongs to a legend group")
check("2px solid var(--mk-" in timeline.LAYOUT_CSS, "a marker is a glyph inside a 2px coloured ring")
for word in ("States", "Events", "Problems", '"group"'):
    check(word in timeline.PAGE_JS or word in body83, f"the legend names {word}")

# 5. Keyboard and screen reader.
check('"role", "button"' not in timeline.PAGE_JS and "lane-open" in timeline.PAGE_JS, "a lane has a real button, not a role=button wrapper")
check('el("button", "mk mk-' in timeline.PAGE_JS, "markers are buttons")
for word in ("focusin", "focusout", "aria-expanded", "Escape", "ptitle", ".focus("):
    check(word in timeline.PAGE_JS, f"the script handles {word}")
check('tabindex="-1"' in body83, "the panel title can take focus")
check('role="group"' in body83 or '"role", "group"' in timeline.PAGE_JS, "the legend has a role")
check('aria-label="Filter log' in body83, "the log filter is a labelled group")
check(":focus-visible" in timeline.LAYOUT_CSS, "focus has a visible style")
check("intersects" in timeline.PAGE_JS and "getBoundingClientRect" in timeline.PAGE_JS, "the tooltip is placed clear of the open panel")

# 6. Axis and layout.
for word in ("function tickLabel", "ResizeObserver", "function spread", "function plain"):
    check(word in timeline.PAGE_JS, f"the script defines {word.split()[-1]}")
check("scrollbar-gutter:stable" in timeline.LAYOUT_CSS.replace(" ", ""), "the chart reserves its scrollbar gutter")
check("2px dashed var(--st-batched)" in timeline.LAYOUT_CSS and "repeating-" not in timeline.LAYOUT_CSS, "batched draws as an outlined segment")
check('"seg seg-"' in timeline.PAGE_JS, "the script classes each segment by state")
check("alert" in timeline.PAGE_JS and ".alert" in timeline.LAYOUT_CSS, "non-zero rework, CI and review tiles get emphasis")

# 7. Minor items.
bl83 = next(lane for g in payload(html_gh)["groups"] for lane in g["lanes"] if lane["kind"] == "batch")
check(not bl83["label"].startswith("Batch #"), f"the batch lane does not repeat the group heading: {bl83['label']}")
check(".lane.sel" in timeline.LAYOUT_CSS and "3px" in timeline.LAYOUT_CSS.split(".lane.sel")[1].split("}")[0], "the selected lane has a 3px edge")
check("--diff-add-bg" in timeline.LAYOUT_CSS and "--diff-del-bg" in timeline.LAYOUT_CSS, "diff colours are theme tokens")
check('[aria-pressed="true"]::before' in timeline.LAYOUT_CSS, "a pressed filter shows a mark, not only a colour")
check("var(--visited)" in timeline.LAYOUT_CSS and "a:hover" in timeline.LAYOUT_CSS, "links have visited and hover states")

# ---------------------------------------------------------------- problems view, shared views, search, time in status (#84)
import shutil

NOW84 = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)

# 1. The problems view lists every problem of the data. Open problems come first, then resolved ones by time.
rows78 = timeline.problem_rows(doc78, NOW84)
check(len(rows78) == sum(len(i.get("problems") or []) for i in doc78["issues"].values()) == 1, f"one row per problem in the batch #78 fixture: {len(rows78)}")
r78 = rows78[0] if rows78 else {}
check(r78.get("issue") == "75" and r78.get("lane") == "issue:75" and r78.get("label") == "#75" and r78.get("resolved") is True, f"the #75 rework row is resolved: {r78}")
check(r78.get("type") == "Rework requested" and r78.get("text", "").startswith("PM gate: rework requested."), f"row type and first line: {r78.get('type')!r}")
check(r78.get("seconds") == 44, f"time to fix is first fix commit minus problem: {r78.get('seconds')}")
check(len(r78.get("fixes", [])) == 1 and r78["fixes"][0]["sha"].startswith("e733766") and r78["fixes"][0]["subject"] and "url" in r78["fixes"][0],
      "the row carries its fix commit with subject and url")
check(r78.get("url") and r78.get("id") == "75:auto-1", "the row carries the problem url and id")

pv_doc = copy.deepcopy(dd_doc)
pv_doc["issues"]["51"]["problems"][1].update(resolved=False, resolved_by=[])      # the CI failure stays open
pv_doc["issues"]["51"]["problems"].append(dict(pv_doc["issues"]["51"]["problems"][0], id="51:auto-9", ts="2026-06-01T12:00:00Z",
                                               resolved=False, resolved_by=[]))      # a newer open problem
pv_rows = timeline.problem_rows(pv_doc, NOW84)
check([r["id"] for r in pv_rows][:2] == ["51:auto-2", "51:auto-9"], f"open problems lead, oldest first: {[r['id'] for r in pv_rows]}")
check([r["resolved"] for r in pv_rows] == [False, False, True, True], f"open first, then resolved: {[r['resolved'] for r in pv_rows]}")
check([r["ts"] for r in pv_rows[2:]] == sorted((r["ts"] for r in pv_rows[2:]), reverse=True), "resolved rows sort newest first")
check(pv_rows[0]["seconds"] == int((NOW84 - datetime(2026, 6, 1, 9, 45, 30, tzinfo=timezone.utc)).total_seconds()), "an open problem shows its age")
check(pv_rows[0]["type"] == "CI failed" and {r["type"] for r in pv_rows} == {"CI failed", "Changes requested", "Rework requested"}, "row types by source")
check(timeline.problem_rows({"events": [], "issues": {}}, NOW84) == [], "no problems gives no rows")
check(timeline.problem_rows({"events": [], "issues": {"5": {"title": "x"}}}, NOW84) == [], "an issue without a problems key gives no rows")
pv_page = timeline.render_html(pv_doc, now=NOW84)
pv_data = payload(pv_page)
check(pv_data["problemRows"] == pv_rows, "the page embeds the rows")
body84 = pv_page.split("<body>", 1)[1]
check(body84.index('class="controls"') < body84.index('id="problemsview"') < body84.index('id="legend"'),
      "the problems section is the first section under the controls")
check(re.search(r'<details id="problemsview"[^>]* open', body84) is not None, "the problems section is open when a problem exists")
none_page = timeline.render_html({"events": [], "issues": {}}, now=NOW84)
check(re.search(r'<details id="problemsview"[^>]* open', none_page) is None and "No problems" in none_page, "the problems section is closed and says so when there are none")
for word in ("function problemsView", "openProblem", "data-problem", "scrollIntoView", ".pv-row"):
    check(word in timeline.PAGE_JS or word in timeline.LAYOUT_CSS, f"the problems view script handles {word}")
check("bounds()" in timeline.PAGE_JS.split("function problemsView")[-1].split("\n  function ")[0], "the problems view respects the timeframe")

# 2. Shared view: the hash holds lane, range, log filters, search and the problems toggle.
node = shutil.which("node")
HELPERS_OK = all(word in getattr(timeline, "VIEW_JS", "") for word in ("encodeView", "decodeView", "laneMatches", "fmtDuration"))
check(HELPERS_OK, "the page script defines the pure view helpers")
for word in ("replaceState", "hashchange", "location.hash"):
    check(word in timeline.PAGE_JS, f"the script uses {word}")
check("pushState" not in timeline.PAGE_JS, "writing the hash never adds history entries")


def js(expr):
    """Run `expr` against the pure view helpers in node and return its JSON value."""
    proc = subprocess.run([node, "-e", timeline.VIEW_JS + "\nconsole.log(JSON.stringify(" + expr + "));"],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return {"error": proc.stderr[-300:]}
    return json.loads(proc.stdout)


if not node:
    print("SKIP view-helper behaviour cases: node is not installed")
elif HELPERS_OK:
    view = {"lane": "issue:81", "from": "2026-11-19T22:00:00.000Z", "to": "2026-11-20T01:30:00.000Z", "kinds": ["commits", "ci"],
            "q": "hash & more=1", "p": True}
    enc = js("encodeView(" + json.dumps(view) + ")")
    check(isinstance(enc, str) and enc.startswith("lane=issue:81&from=2026-11-19T22:00") and "&p=1" in enc and "kinds=commits%2Cci" in enc,
          f"the hash writes the format lane=..&from=..&to=..&kinds=..&q=..&p=..: {enc}")
    check(isinstance(enc, str) and "&q=hash%20%26%20more%3D1" in enc, f"search text is escaped so & and = survive: {enc}")
    check(js("decodeView(encodeView(" + json.dumps(view) + "))") == view, "decode(encode(view)) returns the view")
    check(js("decodeView('#' + encodeView(" + json.dumps(view) + "))") == view, "decode accepts a leading #")
    empty = {"lane": None, "from": None, "to": None, "kinds": None, "q": "", "p": False}
    check(js("encodeView(" + json.dumps(empty) + ")") == "", "an empty view writes an empty hash")
    check(js("decodeView('')") == empty and js("decodeView('#')") == empty, "an empty hash decodes to the empty view")
    check(js("decodeView('lane=issue:81&bogus=1&from=not-a-date&p=7')") == dict(empty, lane="issue:81"), "unknown keys and bad values are ignored")
    check(js("decodeView('kinds=')")["kinds"] == [] and js("decodeView('')")["kinds"] is None, "an empty kinds list differs from an absent one")
    check(js("decodeView('q=%E0%A4%A')")["q"] == "", "a malformed escape does not throw")
    # 3. Lane search: number or title, case-insensitive, a leading # ignored.
    lane81 = {"label": "#81", "title": "Timeline drill-down"}
    for query, want in (("", True), ("81", True), ("#81", True), ("drill", True), ("DRILL-down", True), ("  drill ", True), ("82", False), ("nope", False)):
        check(js("laneMatches(" + json.dumps(lane81) + "," + json.dumps(query) + ")") is want, f"search {query!r} matches #81: {want}")
    check(js("laneMatches({label:'Batch activity',title:null},'batch')") is True, "a lane without a title still matches its label")
    # 4. Durations read as days, hours, minutes.
    for secs, want in ((0, "0m"), (45, "<1m"), (60, "1m"), (3600, "1h"), (5400, "1h 30m"), (90000, "1d 1h"), (None, "0m")):
        check(js(f"fmtDuration({json.dumps(secs)})") == want, f"fmtDuration({secs}) is {want}")
check('id="lanesearch"' in body84 and 'type="search"' in body84 and 'for="lanesearch"' in body84, "a labelled search box sits in the controls")
check('id="onlyprob"' in body84 and 'aria-pressed="false"' in body84.split('id="onlyprob"')[1].split(">")[0], "the problems-only toggle is a pressed-state button")
check(body84.index('id="lanesearch"') < body84.index('id="onlyprob"') < body84.index('id="problemsview"'), "the toggle sits next to the search, above the problems view")
for word in ('ev.key === "/"', "isContentEditable", "lanesearch", ".focus()", "preventDefault"):
    check(word in timeline.PAGE_JS, f"the / shortcut handles {word}")
check("problemMarks" in timeline.PAGE_JS and set(payload(html_gh)["problemMarks"]) == {"rework", "review", "ci"}, "a lane has a problem when it shows a rework, review or CI marker")

# 5. Time in status: totals equal the sums of the status intervals. The collector is unchanged.
tis = timeline.time_in_status(gh_doc, NOW84)
check("51" in tis, f"issues with status events get totals: {sorted(tis)}")
t51 = tis.get("51", {})
check(t51.get("by_status") == {"ready": 3595, "in-progress": 3600, "in-review": 5400}, f"#51 per-status seconds equal the intervals: {t51.get('by_status')}")
check(t51.get("by_class") == {"working": 3600, "waiting": 5400, "parked": 0}, f"#51 working vs waiting: {t51.get('by_class')}")
check(t51.get("open") is None, "a merged issue has no open interval")
park = copy.deepcopy(gh_doc)
park["events"] = [e for e in park["events"] if e.get("issue") != 51] + [
    {"ts": "2026-05-01T10:00:00Z", "kind": "status", "issue": 70, "pr": None, "batch": None, "actor": None, "from": None, "to": "in-progress", "detail": None},
    {"ts": "2026-05-01T10:10:00Z", "kind": "status", "issue": 70, "pr": None, "batch": None, "actor": None, "from": "in-progress", "to": "needs-feedback", "detail": None},
    {"ts": "2026-05-01T10:30:00Z", "kind": "status", "issue": 70, "pr": None, "batch": None, "actor": None, "from": "needs-feedback", "to": "blocked", "detail": None},
    {"ts": "2026-05-01T11:00:00Z", "kind": "status", "issue": 70, "pr": None, "batch": None, "actor": None, "from": "blocked", "to": "in-progress", "detail": None}]
park["events"].sort(key=lambda e: e["ts"])
pt = timeline.time_in_status(park, datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)).get("70", {})
check(pt.get("by_class") == {"working": 600 + 3600, "waiting": 1200, "parked": 1800}, f"needs-feedback waits, blocked parks, a reopened state adds up: {pt.get('by_class')}")
check(pt.get("open") == "in-progress", "the last status runs to the end of the range")
capped = timeline.time_in_status(park, datetime(2026, 5, 1, 10, 20, tzinfo=timezone.utc)).get("70", {"by_status": {}})
check(capped["by_status"].get("in-progress") == 600 and capped["by_status"].get("needs-feedback") == 600, f"the end bounds the open interval: {capped['by_status']}")
check(payload(timeline.render_html(gh_doc, now=NOW84)).get("statusTime") == tis, "the page embeds the totals")
for word in ("statusTime", "fmtDuration", "stackbar"):
    check(word in timeline.PAGE_JS or word in timeline.LAYOUT_CSS, f"the script shows time in status through {word}")

# 6. Marker colours: no two markers read as one (the dark "changes requested" ring was green like "PR merged").
for index, theme in enumerate(("light", "dark")):
    keys = list(timeline.MARKER_COLORS)
    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            distance = delta_e(timeline.MARKER_COLORS[keys[a]][index], timeline.MARKER_COLORS[keys[b]][index])
            check(distance >= 28, f"{theme}: marker {keys[a]} is only {distance:.1f} from marker {keys[b]}")
check(delta_e(timeline.MARKER_COLORS["review"][1], timeline.MARKER_COLORS["merge"][1]) >= 60, "dark: changes-requested does not read as merged")

# 7. Review fixes for batch #78: one lane per agent, and a marked checkpoint comment still parses.
agent_ids = [lane["id"] for group in lane_model for lane in group["lanes"] if lane["kind"] == "agent"]
check(agent_ids == ["agent:a1"], f"agent.spawned and agent.stopped share one lane: {agent_ids}")
worktree_ids = [lane["id"] for group in lane_model for lane in group["lanes"] if lane["kind"] == "worktree"]
check(worktree_ids == ["worktree:/w/51"], f"worktree events keep their own lane: {worktree_ids}")

marked_bundle = {
    "forge": {"type": "github", "host": None, "owner": "o", "repo": "r"},
    "issues": [{"issue": issue_raw(7, "Seven", ["status:in-progress"], "2026-05-01T10:00:00Z"),
                "timeline": [{"event": "commented", "created_at": "2026-05-01T11:00:00Z", "user": {"login": "w"},
                              "body": "<!-- issue-flow:event role=checkpoint issue=7 -->\ncheckpoint 2: tests green"}]}],
    "pulls": [],
}
marked = kinds_of(timeline.normalize(marked_bundle), "checkpoint")
check(len(marked) == 1 and marked[0]["k"] == 2 and marked[0]["detail"] == "tests green",
      f"a checkpoint comment with a marker gives a checkpoint event: {marked}")

# ---------------------------------------------------------------- several PR repositories (#86)
# Member PRs live on the fork (o/fork). The batch PR lives on the issue repo (o/r). Both use number 8.
def multi_bundle(forge_type):
    gh = forge_type == "github"
    ts = lambda hms: f"2026-07-01T{hms}Z" if gh else f"2026-07-01T{hms}+00:00"  # noqa: E731
    note = lambda t, who, body, cid: ({"event": "commented", "id": cid, "html_url": f"https://x/c/{cid}", "created_at": ts(t),  # noqa: E731
                                       "actor": {"login": who}, "body": body} if gh else
                                      {"type": "comment", "id": cid, "html_url": f"https://x/c/{cid}", "created_at": ts(t),
                                       "user": {"login": who}, "body": body})
    commit = gh_commit("f100", "issue-flow: fix the review findings", [{"filename": "src/a.py"}], ts=ts("12:00:00"))
    if not gh:
        commit["diff"] = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        commit["files"] = [{"filename": "src/a.py", "status": "modified"}]
    member = dict(pr_raw(8, "Member", "issue/51-flag", "batch/50-bar", ts("09:00:00"), merged=ts("10:00:00")), html_url="https://x/fork/pull/8")
    batch = dict(pr_raw(8, "Batch", "batch/50-bar", "dev", ts("11:00:00"), merged=ts("13:00:00"), merged_by="op"), html_url="https://x/r/pull/8")
    rework = "<!-- issue-flow:event role=gate.rework issue=50 problem=batch-r1 -->\nBatch review: rework requested."
    return {"forge": {"type": forge_type, "host": "h", "owner": "o", "repo": "r"},
            "issues": [{"issue": issue_raw(50, "Batch", ["type:batch"], ts("08:00:00"), "- [ ] #51\n"), "timeline": []},
                       {"issue": issue_raw(51, "Flag", ["status:merged"], ts("08:00:00")), "timeline": []}],
            "pulls": [{"repo": "o/fork", "pr": member, "timeline": [note("09:30:00", "pm", "Sub-merged into batch.", 1)]},
                      {"repo": "o/r", "pr": batch, "timeline": [note("11:30:00", "pm", rework, 2)], "commits": [commit]}]}


for forge_type in ("github", "gitea"):
    mdoc = timeline.normalize(multi_bundle(forge_type))
    opened = [(e["repo"], e["pr"], e["issue"], e["batch"]) for e in mdoc["events"] if e["kind"] == "pr.opened"]
    check(opened == [("o/fork", 8, 51, 50), ("o/r", 8, 50, 50)], f"{forge_type}: PRs of two repos stay apart, each with its repo: {opened}")
    pr_events = [e for e in mdoc["events"] if e["pr"] is not None]
    check(pr_events and all(e.get("repo") in ("o/fork", "o/r") for e in pr_events), f"{forge_type}: every PR event carries its repo")
    check(mdoc["batches"]["50"]["pr"] == 8 and mdoc["batches"]["50"]["pr_repo"] == "o/r"
          and mdoc["batches"]["50"]["branch"] == "batch/50-bar" and mdoc["batches"]["50"]["prs"] == [8],
          f"{forge_type}: the batch PR is found by its head branch: {mdoc['batches']['50']}")
    merged = [e for e in mdoc["events"] if e["kind"] == "batch.merged"]
    check(len(merged) == 1 and merged[0]["repo"] == "o/r" and merged[0]["actor"] == "op", f"{forge_type}: batch.merged: {merged}")
    check([e["repo"] for e in mdoc["events"] if e["kind"] == "pr.merged"] == ["o/fork", "o/r"], f"{forge_type}: pr.merged repos")
    rework_events = [e for e in mdoc["events"] if e["kind"] == "comment" and e["role"] == "gate.rework"]
    check(len(rework_events) == 1 and rework_events[0]["repo"] == "o/r" and rework_events[0]["issue"] == 50
          and rework_events[0]["batch"] == 50, f"{forge_type}: the rework comment sits on the batch lane: {rework_events}")
    problems = mdoc["issues"]["50"]["problems"]
    check([(p["id"], p["resolved"], p["resolved_by"]) for p in problems] == [("50:batch-r1", True, ["f100"])],
          f"{forge_type}: the batch rework is a problem on the tracking issue, fixed by the commit: {problems}")
    check(mdoc["issues"]["51"]["problems"] == [], f"{forge_type}: the member issue has no problem")
    # The same PR listed twice (one repo named twice) is one PR.
    twice = multi_bundle(forge_type)
    twice["pulls"].append(copy.deepcopy(twice["pulls"][0]))
    check(project(timeline.normalize(twice)) == project(mdoc), f"{forge_type}: a repeated PR is de-duplicated")

check(project(timeline.normalize(multi_bundle("github"))) == project(timeline.normalize(multi_bundle("gitea"))),
      "two-repo events match across the forges")
# A PR entry with no repo belongs to the issue repo.
plain = timeline.normalize(build("github"))
check(all(e.get("repo") == "o/r" for e in plain["events"] if e["pr"] is not None), "a PR with no repo key belongs to the issue repo")

# --pr-repo repeats, takes a comma list, and always keeps the issue repo.
check(timeline.pr_repo_list("o/r", None) == ["o/r"], "no --pr-repo reads the issue repo only")
check(timeline.pr_repo_list("o/r", ["o/fork"]) == ["o/r", "o/fork"], "the issue repo comes first")
check(timeline.pr_repo_list("o/r", ["o/fork,o/other", "o/fork", "o/r", " o/x "]) == ["o/r", "o/fork", "o/other", "o/x"],
      "a comma list and repeats are split and de-duplicated")

# Live: a fake forge serves each repository's own pull requests.
FAKE_MULTI = r"""
import json, os, re, sys
path = sys.argv[-1]
data = json.load(open(os.environ["FAKE_DATA"]))
page = re.search(r"[?&]page=(\d+)", path)
if page and int(page.group(1)) > 1:
    print('{"workflow_runs": []}' if "/actions/runs?" in path else "[]"); sys.exit(0)
repo = re.search(r"repos/([^/]+/[^/]+)/", path).group(1)
mine = [e for e in data["pulls"] if e["repo"] == repo]
if "/actions/runs" in path:
    print('{"workflow_runs": []}'); sys.exit(0)
m = re.search(r"/pulls/(\d+)/(commits|reviews)\?", path)
if m:
    entry = next(e for e in mine if e["pr"]["number"] == int(m.group(1)))
    rows = entry.get("commits", []) if m.group(2) == "commits" else []
    print(json.dumps([{k: v for k, v in c.items() if k != "diff"} for c in rows])); sys.exit(0)
if re.search(r"/pulls/\d+/comments\?", path):
    print("[]"); sys.exit(0)
m = re.search(r"/(?:git/)?commits/([0-9a-f]+)(\.diff)?$", path)
if m:
    for e in mine:
        for c in e.get("commits", []):
            if c["sha"] == m.group(1):
                print(c.get("diff", "") if m.group(2) else json.dumps(c)); sys.exit(0)
m = re.search(r"/issues/(\d+)$", path)
if m:
    print(json.dumps(next(e["issue"] for e in data["issues"] if e["issue"]["number"] == int(m.group(1))))); sys.exit(0)
m = re.search(r"/issues/(\d+)/timeline", path)
if m:
    n = int(m.group(1))
    rows = [e for e in data["issues"] if e["issue"]["number"] == n] if repo == data["issue_repo"] else []
    rows += [e for e in mine if e["pr"]["number"] == n]
    print(json.dumps(rows[0]["timeline"] if rows else [])); sys.exit(0)
if "/pulls" in path:
    print(json.dumps([e["pr"] for e in mine])); sys.exit(0)
if "/issues" in path:
    label = re.search(r"labels=([^&]+)", path)
    rows = [e["issue"] for e in data["issues"]] if repo == data["issue_repo"] else []
    if label:
        from urllib.parse import unquote
        rows = [r for r in rows if unquote(label.group(1)) in [l["name"] for l in r["labels"]]]
    print(json.dumps(rows)); sys.exit(0)
print(json.dumps({"message": "Not Found"}))
"""
with tempfile.TemporaryDirectory() as tmp:
    fake = os.path.join(tmp, "fake.py")
    with open(fake, "w") as handle:
        handle.write(FAKE_MULTI)
    for forge_type, env_name in (("github", "IFW_GH"), ("gitea", "IFW_TEA")):
        wrapper = os.path.join(tmp, f"fake-{forge_type}")
        with open(wrapper, "w") as handle:
            handle.write(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        os.chmod(wrapper, 0o755)
        data = os.path.join(tmp, f"multi-{forge_type}.json")
        with open(data, "w") as handle:
            json.dump(dict(multi_bundle(forge_type), issue_repo="o/r"), handle)
        env = {env_name: wrapper, "FAKE_DATA": data}
        expect = project(timeline.normalize(multi_bundle(forge_type)))
        for flags, label in ((["--pr-repo", "o/fork"], "one --pr-repo"),
                             (["--pr-repo", "o/fork", "--pr-repo", "o/r"], "a repeated --pr-repo"),
                             (["--pr-repo", "o/fork,o/r"], "a comma list")):
            proc = run(["collect", "--forge", forge_type, "--repo", "o/r", "--local-events", "off", *flags], env)
            check(proc.returncode == 0, f"live {forge_type} with {label} exits 0: {proc.stderr}")
            if proc.returncode == 0:
                live = json.loads(proc.stdout)
                check(project(live) == expect, f"live {forge_type} with {label} has both repos' events")
                check(live["forge"].get("pr_repos") == ["o/fork"], f"live {forge_type} with {label} records the extra repos: {live['forge']}")
        proc = run(["collect", "--forge", forge_type, "--repo", "o/r", "--local-events", "off", "--pr-repo", "o/fork", "--issue", "50"], env)
        check(proc.returncode == 0 and any(e["kind"] == "batch.merged" for e in json.loads(proc.stdout)["events"]),
              f"live {forge_type} --issue 50 keeps the batch PR events: {proc.stderr}")
        proc = run(["collect", "--forge", forge_type, "--repo", "o/r", "--local-events", "off"], env)
        check(proc.returncode == 0 and [e["repo"] for e in json.loads(proc.stdout)["events"] if e["kind"] == "pr.opened"] == ["o/r"],
              f"live {forge_type} with no --pr-repo reads the issue repo only")

# watch passes every --pr-repo to collect.
seen_args = []
real_main = timeline.main


def fake_main(argv=None):
    if argv and argv[0] == "collect":
        seen_args.append(list(argv))
        with open(argv[-1], "w") as handle:
            json.dump({"version": 1, "forge": {}, "issues": {}, "batches": {}, "events": []}, handle)
        return 0
    return real_main(argv)


timeline.main = fake_main
with tempfile.TemporaryDirectory() as tmp:
    timeline._watch_once(["--forge", "github", "--repo", "o/r", "--pr-repo", "o/fork", "--pr-repo", "o/other"], os.path.join(tmp, "t.html"), 60, tmp)
timeline.main = real_main
check(seen_args and seen_args[0][:9] == ["collect", "--forge", "github", "--repo", "o/r", "--pr-repo", "o/fork", "--pr-repo", "o/other"],
      f"watch passes every --pr-repo to collect: {seen_args}")

# The page names the repo on a PR from another repo only.
multi_page = payload(timeline.render_html(timeline.normalize(multi_bundle("github"))))
check(multi_page["issueRepo"] == "o/r", f"the page data names the issue repo: {multi_page.get('issueRepo')}")
check(multi_page["batches"]["50"]["pr_repo"] == "o/r", "the batch entry reaches the page")
check("issueRepo" in timeline.PAGE_JS and "e.repo" in timeline.PAGE_JS, "the detail text labels a PR from another repo")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("timeline: all cases pass")
