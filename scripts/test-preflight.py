#!/usr/bin/env python3
"""Tests for issue-flow/hooks/preflight.py.

    python3 scripts/test-preflight.py

Runs the hook as Claude Code runs it — a subprocess fed a SessionStart payload on
stdin — against throwaway git repositories. Three properties matter: the hook is
silent everywhere except a repo carrying `.issue-flow.json`, its digest reports the
facts Phase 0 would otherwise re-derive, and it fails open on anything it does not
understand. Forge (gh) queries are exercised only through their skip path — the
suite must pass offline and unauthenticated.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "issue-flow", "hooks", "preflight.py")
LABELS_MD = os.path.join(
    ROOT, "issue-flow", "skills", "issue-flow", "references", "labels.md"
)

failures = []


def run_hook(payload, cwd, env=None):
    """Return the digest text the hook emitted, or None when it stayed silent."""
    environment = dict(os.environ)
    environment.pop("ISSUE_FLOW_PREFLIGHT", None)
    environment.update(env or {})
    result = subprocess.run(
        [sys.executable, HOOK],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=environment,
        timeout=60,
    )
    if result.returncode != 0:
        failures.append(f"hook exited {result.returncode}: {result.stderr.strip()}")
        return None
    if not result.stdout.strip():
        return None
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        failures.append(f"hook emitted non-JSON: {result.stdout[:120]!r}")
        return None
    specific = output.get("hookSpecificOutput", {})
    if specific.get("hookEventName") != "SessionStart":
        failures.append(f"the output must name its hook event: {output}")
    return specific.get("additionalContext")


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", repo] + list(args), capture_output=True, text=True
    )
    if result.returncode != 0:
        failures.append(f"test setup: git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def make_project(base):
    """A repo with a local bare remote, an epic branch, and an issue-flow config."""
    remote = os.path.join(base, "remote.git")
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", remote], check=True)
    repo = os.path.join(base, "project")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "test")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("test\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "remote", "add", "origin", remote)
    git(repo, "push", "-q", "origin", "main")
    git(repo, "push", "-q", "origin", "main:refs/heads/dev")
    git(repo, "push", "-q", "origin", "main:refs/heads/epic/42-auth")
    # forge type gitea keeps the suite off the network: gh is never invoked
    with open(os.path.join(repo, ".issue-flow.json"), "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "forge": {"type": "gitea"}}, handle)
    return repo


payload = {"hook_event_name": "SessionStart", "source": "startup"}

with tempfile.TemporaryDirectory() as base:
    project = make_project(base)
    payload_here = dict(payload, cwd=project)

    # --- silence outside an issue-flow project --------------------------------
    plain = os.path.join(base, "plain")
    subprocess.run(["git", "init", "-q", "-b", "main", plain], check=True)
    if run_hook(dict(payload, cwd=plain), plain) is not None:
        failures.append("a repo without .issue-flow.json must get no digest")

    not_repo = os.path.join(base, "notrepo")
    os.makedirs(not_repo)
    if run_hook(dict(payload, cwd=not_repo), not_repo) is not None:
        failures.append("a directory that is not a git repo must get no digest")

    # --- the kill switch ------------------------------------------------------
    if run_hook(payload_here, project, env={"ISSUE_FLOW_PREFLIGHT": "off"}) is not None:
        failures.append("ISSUE_FLOW_PREFLIGHT=off must silence the hook")

    # --- the digest, in an issue-flow project ---------------------------------
    digest = run_hook(payload_here, project)
    if not digest:
        failures.append("an issue-flow project must get a digest")
        digest = ""
    for expected in (
        "issue-flow preflight",
        "remote: origin (fetched",
        "epic/42-auth",
        "dev branch: present",
        "leftover worktrees: none",
        "forge queries: skipped (forge is gitea",
    ):
        if expected not in digest:
            failures.append(f"digest should contain {expected!r}:\n{digest}")

    # --- plugin-version drift --------------------------------------------------
    with open(
        os.path.join(ROOT, "issue-flow", ".claude-plugin", "plugin.json"),
        encoding="utf-8",
    ) as handle:
        installed = json.load(handle)["version"]
    if "no pluginVersion recorded" not in digest:
        failures.append(f"a config without pluginVersion should say so:\n{digest}")

    def write_config(plugin_version):
        with open(
            os.path.join(project, ".issue-flow.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(
                {
                    "version": 1,
                    "forge": {"type": "gitea"},
                    "pluginVersion": plugin_version,
                },
                handle,
            )

    write_config(installed)
    digest = run_hook(payload_here, project) or ""
    if f"plugin version: {installed} (matches the last run)" not in digest:
        failures.append(f"a matching pluginVersion should be reported as such:\n{digest}")

    write_config("0.0.1")
    digest = run_hook(payload_here, project) or ""
    if f"plugin version: {installed}, project last ran 0.0.1" not in digest:
        failures.append(f"a stale pluginVersion should be flagged as drift:\n{digest}")
    write_config(installed)

    # --- spec front-matter feeds the branch-model line ------------------------
    specs = os.path.join(project, "docs", "specs")
    os.makedirs(specs)
    with open(os.path.join(specs, "spec.md"), "w", encoding="utf-8") as handle:
        handle.write("---\nbranch_model: dev-and-live\n---\n# Spec\n")
    digest = run_hook(payload_here, project) or ""
    if "spec branch_model: dev-and-live" not in digest:
        failures.append(f"digest should read branch_model from the spec:\n{digest}")

    # --- a leftover worker worktree is reported -------------------------------
    worktree = os.path.join(base, "leftover")
    git(project, "worktree", "add", "-q", "-b", "issue/7-fix", worktree)
    digest = run_hook(payload_here, project) or ""
    if "issue/7-fix" not in digest:
        failures.append(f"digest should report the leftover worktree:\n{digest}")
    git(project, "worktree", "remove", "--force", worktree)

    # --- fail open ------------------------------------------------------------
    if run_hook("{not json", project) is None:
        failures.append("malformed stdin must still produce a digest from cwd")
    if run_hook("", not_repo) is not None:
        failures.append("malformed stdin outside a project must stay silent")

    # a repo whose config is unreadable still gets the git-side digest
    with open(os.path.join(project, ".issue-flow.json"), "w", encoding="utf-8") as handle:
        handle.write("{broken json")
    digest = run_hook(payload_here, project) or ""
    if "issue-flow preflight" not in digest:
        failures.append("a malformed config must not silence the git-side digest")

# --- the label set stays in sync with labels.md --------------------------------
spec = importlib.util.spec_from_file_location("preflight", HOOK)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open(LABELS_MD, encoding="utf-8") as handle:
    documented = re.findall(r'^gh label create "([^"]+)"', handle.read(), re.MULTILINE)
if not documented:
    failures.append("could not parse the bootstrap labels out of labels.md")
elif set(documented) != set(module.STANDARD_LABELS):
    failures.append(
        "STANDARD_LABELS drifted from labels.md: "
        f"only in labels.md {sorted(set(documented) - set(module.STANDARD_LABELS))}, "
        f"only in the hook {sorted(set(module.STANDARD_LABELS) - set(documented))}"
    )
for parked in module.PARKED_LABELS:
    if parked not in module.STANDARD_LABELS:
        failures.append(f"PARKED_LABELS entry {parked!r} is not a standard label")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("preflight hook: all cases pass")
