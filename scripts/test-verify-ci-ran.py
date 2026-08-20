#!/usr/bin/env python3
"""Tests for issue-flow/scripts/verify_ci_ran.py's decide_ran (pure, no network)."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "verify_ci_ran", os.path.join(ROOT, "issue-flow", "scripts", "verify_ci_ran.py")
)
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

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("verify_ci_ran: all cases pass")
