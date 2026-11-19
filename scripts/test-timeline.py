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

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("timeline: all cases pass")
