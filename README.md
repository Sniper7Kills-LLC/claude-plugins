# Sniper7Kills LLC — Claude Code plugins

A Claude Code plugin marketplace. Add it once, then install any plugin it lists.

```bash
claude plugin marketplace add Sniper7Kills-LLC/claude-plugins
claude plugin install issue-flow@sniper7kills
```

Restart the session after you install a plugin, so its skills and agents load.

## Plugins

| Plugin | What it does |
|---|---|
| [**issue-flow**](issue-flow/) | Drives an autonomous development loop from GitHub Issues. It plans a spec, decomposes it into epics and sub-issues, and builds each one with a background worker agent on a batched integration branch. CI runs once per batch. A browser check verifies the deployment, and a user-viewpoint review files what it finds. |
| [**statusline-spark**](statusline-spark/) | Status line renderers that add history. The main line wraps `claude-powerline` and appends a context-window sparkline, whose sawtooth marks every auto-compact, and a cost burn-rate sparkline. The subagent panel gets a custom row: model, effort, context bar, token-rate sparkline, and elapsed time. |

## Develop a plugin in this repository

Work on `dev`. Merge `dev` into `main` to release.

```
feature branch → dev → main → tag
```

Validate a manifest before you commit it:

```bash
python3 scripts/validate-manifests.py    # every plugin: versions, frontmatter, links
claude plugin validate .                 # the marketplace manifest
claude plugin validate ./issue-flow      # one plugin and all its components
```

Check that a change to `issue-flow/skills/issue-flow/` keeps every rule of the 0.20.0
skill, at the phase where it applies:

```bash
python3 scripts/rule-coverage.py check           # every rule in evals/rule-inventory.json
python3 scripts/rule-coverage.py check --judge   # also ask Haiku about each failed rule
```

A reworded rule passes when the reworded text is in the rule's `aliases` list in
`evals/rule-inventory.json`. The judge only reports. It never makes a failed rule pass.

Test a change without publishing it. Add this checkout as a local marketplace:

```bash
claude plugin marketplace add ~/claude-plugins
```

## Release a version

1. Raise the version in the plugin's `plugin.json` **and** in its entry in
   `.claude-plugin/marketplace.json`. The two must match.
2. Open a pull request into `dev`. The **Validate** workflow checks the manifests, the
   skill and agent frontmatter, and every relative documentation link.
3. Merge `dev` into `main`.
4. The **Tag release** workflow reads each plugin's version from the marketplace and
   creates the matching `{name}--v{version}` tag.

The tag workflow is idempotent. It skips any version that is already tagged, so a merge
that changes no version creates no tag. It validates the tree before it tags, so a broken
manifest never gets a release.

To tag from your own machine instead, use the plugin CLI:

```bash
claude plugin tag ./issue-flow --push
```

## License

MIT — see [LICENSE](LICENSE).
