#!/usr/bin/env python3
"""SessionStart hook: hand the issue-flow PM its mechanical preflight for free.

Wired by `issue-flow/hooks/hooks.json`. Phase 0 of the issue-flow skill opens with a
dozen steps, and several are pure bookkeeping — detect the remote, fetch it, list the
live integration branches, find leftover worktrees, compare the labels against the
standard set, list what is parked. Re-deriving those with tool calls costs inference
every session; a hook costs CPU once, before the first turn. The fetch it runs is also
the mechanical fix for the stale-ref failure class the skill documents (a locate pass
reading a checkout one merge behind the remote).

Scope, deliberately narrow:

* **Silent everywhere except an issue-flow project.** The hook emits nothing unless the
  repository root carries a committed `.issue-flow.json` — the file the run
  configuration writes. Every other repo with this plugin installed pays one stat call.
* **Read-only, plus one `git fetch --prune`.** Nothing is created, labeled, or pushed.
  Label *creation* stays in Phase 0 where the PM can act on it; the hook only reports
  what is missing.
* **Forge queries are best-effort and GitHub-only for now.** They run through `gh` and
  are skipped — with a note saying so — when the CLI is absent, unauthenticated, or
  slow. Gitea sessions get the git-side digest and run their own `tea` queries in
  Phase 0.
* **Fail open.** Any error, timeout, or unexpected payload exits 0, silently or with
  whatever partial digest was gathered. A convenience hook must never be the reason a
  session cannot start.

`ISSUE_FLOW_PREFLIGHT=off` disables it (set in `.claude/settings.json` `env`, like the
spawn guard's switch — the environment is read at session start).
"""

import json
import os
import subprocess
import sys

# Keep in sync with the bootstrap block in
# skills/issue-flow/references/labels.md — scripts/test-preflight.py asserts this.
STANDARD_LABELS = (
    "status:ready",
    "status:in-progress",
    "status:in-review",
    "status:batched",
    "status:deploying",
    "status:deploy-failed",
    "status:awaiting-review",
    "status:blocked",
    "status:needs-feedback",
    "type:epic",
    "type:batch",
    "type:hotfix",
    "type:spec-update",
    "review:finding",
    "flow:status",
    "priority:high",
    "priority:low",
)

PARKED_LABELS = ("status:awaiting-review", "status:deploy-failed", "flow:status")

FETCH_TIMEOUT = 20
FORGE_TIMEOUT = 10
LIST_CAP = 10


def off():
    setting = os.environ.get("ISSUE_FLOW_PREFLIGHT", "").strip().lower()
    return setting in {"off", "0", "false", "no"}


def run(cmd, cwd, timeout=5):
    """Command stdout, or None on any failure — the hook never raises over a tool."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def repo_root(cwd):
    out = run(["git", "rev-parse", "--show-toplevel"], cwd)
    return out.strip() if out else None


def detect_remote(root):
    out = run(["git", "remote"], root)
    if not out:
        return None
    remotes = out.split()
    return "origin" if "origin" in remotes else (remotes[0] if remotes else None)


def default_branch(root, remote):
    out = run(["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], root)
    if out:
        return out.strip().removeprefix(f"{remote}/")
    return None


def remote_branches(root, remote, patterns):
    args = ["git", "branch", "-r", "--format=%(refname:short)", "--list"]
    args += [f"{remote}/{p}" for p in patterns]
    out = run(args, root)
    return [line.strip() for line in out.splitlines() if line.strip()] if out else []


def leftover_worktrees(root):
    """Worktrees on issue/* or worktree-agent-* branches — a previous session's."""
    out = run(["git", "worktree", "list", "--porcelain"], root)
    if not out:
        return []
    leftovers, path = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
            if branch.startswith("issue/") or branch.startswith("worktree-agent-"):
                leftovers.append(f"{path} ({branch})")
    return leftovers


def spec_branch_model(root):
    spec = os.path.join(root, "docs", "specs", "spec.md")
    try:
        with open(spec, encoding="utf-8") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    for line in head.split("\n---", 1)[0].splitlines():
        if line.strip().startswith("branch_model:"):
            return line.split(":", 1)[1].strip() or None
    return None


def plugin_version():
    """The installed plugin's version, from the manifest beside this hook."""
    manifest = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir,
        ".claude-plugin",
        "plugin.json",
    )
    try:
        with open(manifest, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
        return version if isinstance(version, str) and version else None
    except Exception:
        return None


def version_line(config):
    """Report plugin-version drift: the project's config remembers which plugin
    version last ran it (`pluginVersion`, stamped by Phase 0 step 11)."""
    current = plugin_version()
    if not current:
        return None
    last = config.get("pluginVersion")
    if not isinstance(last, str) or not last:
        return (
            f"- plugin version: {current} (no pluginVersion recorded in "
            ".issue-flow.json — first run, or a pre-tracking config; step 11 stamps it)"
        )
    if last == current:
        return f"- plugin version: {current} (matches the last run)"
    return (
        f"- plugin version: {current}, project last ran {last} — the plugin changed "
        "since this project's previous session. In step 11, walk the config against "
        "the current option tables: ask about any field the saved file lacks instead "
        "of silently defaulting it, then stamp the new version."
    )


def gh_json(root, args):
    out = run(["gh"] + args, root, timeout=FORGE_TIMEOUT)
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def forge_lines(root, config):
    """Best-effort tracker facts via gh. Returns display lines; says when skipped."""
    forge_type = (config.get("forge") or {}).get("type", "github")
    if forge_type != "github":
        return [f"- forge queries: skipped (forge is {forge_type} — run them in Phase 0)"]

    lines = []
    labels = gh_json(root, ["label", "list", "--limit", "200", "--json", "name"])
    if labels is None:
        return ["- forge queries: skipped (gh missing, unauthenticated, or slow — run them in Phase 0)"]
    have = {entry.get("name") for entry in labels}
    missing = [name for name in STANDARD_LABELS if name not in have]
    lines.append(
        "- missing standard labels: " + (", ".join(missing) if missing else "none")
    )

    prs = gh_json(
        root,
        ["pr", "list", "--state", "open", "--limit", "50",
         "--json", "number,title,isDraft,headRefName,baseRefName"],
    )
    if prs is not None:
        if prs:
            shown = [
                "#{number} {draft}{head} → {base}".format(
                    number=p.get("number"),
                    draft="(draft) " if p.get("isDraft") else "",
                    head=p.get("headRefName"),
                    base=p.get("baseRefName"),
                )
                for p in prs[:LIST_CAP]
            ]
            extra = f" (+{len(prs) - LIST_CAP} more)" if len(prs) > LIST_CAP else ""
            lines.append("- open PRs: " + "; ".join(shown) + extra)
        else:
            lines.append("- open PRs: none")

    issues = gh_json(
        root,
        ["issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title,labels"],
    )
    if issues is not None:
        parked = []
        for issue in issues:
            names = {label.get("name") for label in issue.get("labels", [])}
            hits = names.intersection(PARKED_LABELS)
            if hits:
                parked.append(f"#{issue.get('number')} [{', '.join(sorted(hits))}]")
        lines.append(
            "- parked/status issues: " + ("; ".join(parked[:LIST_CAP]) if parked else "none")
        )
    return lines


def digest(cwd):
    root = repo_root(cwd)
    if not root:
        return None
    config_path = os.path.join(root, ".issue-flow.json")
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}

    lines = ["issue-flow preflight (mechanical facts, gathered by the SessionStart hook):"]

    drift = version_line(config)
    if drift:
        lines.append(drift)

    remote = detect_remote(root)
    if remote:
        fetched = run(["git", "fetch", remote, "--prune", "--quiet"], root, FETCH_TIMEOUT)
        state = "fetched, refs current" if fetched is not None else "fetch FAILED — refs may be stale"
        lines.append(f"- remote: {remote} ({state})")
        branch = default_branch(root, remote)
        dev = bool(remote_branches(root, remote, ["dev", "develop", "development"]))
        model = spec_branch_model(root)
        lines.append(
            "- default branch: {d}; dev branch: {dev}; spec branch_model: {m}".format(
                d=branch or "unknown", dev="present" if dev else "absent", m=model or "none"
            )
        )
        integration = remote_branches(root, remote, ["epic/*", "batch/*"])
        lines.append(
            "- live integration branches: " + (", ".join(integration) if integration else "none")
        )
    else:
        lines.append("- remote: none detected (no fetch run)")

    leftovers = leftover_worktrees(root)
    lines.append(
        "- leftover worktrees: " + ("; ".join(leftovers) if leftovers else "none")
    )
    lines.extend(forge_lines(root, config))
    lines.append(
        "(read-only snapshot — verify anything surprising; the judgment steps of "
        "Phase 0 are still yours)"
    )
    return "\n".join(lines)


def main():
    if off():
        return
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        context = digest(cwd or os.getcwd())
    except Exception:  # fail open — see the module docstring
        return
    if not context:
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
