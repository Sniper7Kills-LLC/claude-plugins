#!/usr/bin/env python3
"""Tests for issue-flow/scripts/verify_ci_ran.py: decide_ran (pure, no network) plus
an end-to-end check that malformed invocations exit 5, not the same code used for
"still running" / infra-retry."""

import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "issue-flow", "scripts", "verify_ci_ran.py")
spec = importlib.util.spec_from_file_location("verify_ci_ran", SCRIPT)
verify_ci_ran = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_ci_ran)

failures = []


def fail(message):
    failures.append(message)


result = verify_ci_ran.decide_ran([])
if result["ran"]:
    fail(f"no runs at all must be ran=False, got {result}")

result = verify_ci_ran.decide_ran([{"id": 1, "status": "completed", "log_bytes": 0}])
if result["ran"]:
    fail(f"a run with zero log bytes must be ran=False (status alone is not evidence), got {result}")

result = verify_ci_ran.decide_ran([{"id": 1, "status": "completed", "log_bytes": 5000}])
if not result["ran"]:
    fail(f"a run with real log bytes must be ran=True, got {result}")

result = verify_ci_ran.decide_ran([
    {"id": 1, "status": "completed", "log_bytes": 0},
    {"id": 2, "status": "completed", "log_bytes": 800},
])
if not result["ran"] or len(result.get("runs", [])) != 1:
    fail(f"mixed runs must report ran=True with only the log-bearing run counted, got {result}")

# still-running statuses beyond queued/in_progress must not read as "never ran" —
# an approval-gated GitHub run or a Gitea "waiting" run, not yet terminal.
for status in ("waiting", "requested", "pending"):
    result = verify_ci_ran.decide_ran([{"id": 1, "status": status, "log_bytes": 0}])
    if result["ran"] or not result.get("running"):
        fail(f"status={status!r} with zero log bytes must report running=True, not never-ran, got {result}")

# a malformed invocation (missing --repo for --forge github) must exit a code that
# isn't shared with "still running" / infra-retry (exit 2) — a caller polling on the
# bare exit code must not loop forever on a typo.
proc = subprocess.run(
    [sys.executable, SCRIPT, "--forge", "github", "--sha", "deadbeef"],
    capture_output=True, text=True,
)
if proc.returncode != 5:
    fail(f"expected exit 5 for --forge github without --repo, got {proc.returncode} (stderr={proc.stderr!r})")

proc = subprocess.run(
    [sys.executable, SCRIPT, "--sha", "deadbeef"],
    capture_output=True, text=True,
)
if proc.returncode != 5:
    fail(f"expected exit 5 for neither --forge nor --fixture given, got {proc.returncode} (stderr={proc.stderr!r})")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("verify_ci_ran: all cases pass")
