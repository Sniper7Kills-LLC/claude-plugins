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
                                  "prs": [60]}, f"batches: {gh_doc['batches']}")
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
def run(args, env=None):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
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
    print("[]"); sys.exit(0)
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
check(page.lower().count("</script>") == 2, "only the page's own two script tags close")

# Empty and filtered-empty states.
empty_page = timeline.render_html({"version": 1, "forge": {"type": "github", "owner": "o", "repo": "r"},
                                   "range": {"since": None, "until": None}, "issues": {}, "batches": {},
                                   "convergence": [], "events": []})
check('<p id="empty">No events in this range.</p>' in empty_page, "empty events show the empty state")
check(payload(empty_page)["groups"] == [], "empty events give no lanes")

# Convergence strip: shown only when the data has it.
check('class="convergence"' in html_gh and "Gate 12" in html_gh and "3 filed" in html_gh and "5 closed" in html_gh,
      "convergence strip lists gate 12")
check("Gate 13" in html_gh and "2 filed" in html_gh and "– closed" in html_gh, "a null count shows a dash")
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

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("timeline: all cases pass")
