# External APIs — read the documentation, never assume the shape

Whenever this plugin plans or builds against something it does not own — a cloud service,
a third-party API, a provider CLI, a library, an MCP server — **the interface is read from
that tool's own documentation, never recalled from memory and never inferred from the
name.**

This applies hardest to **AWS** and to any paid or stateful third-party service, where a
wrong assumption does not fail loudly at compile time. It costs money, mutates
infrastructure, or fails silently in production.

Code inside the repository is different: read the code. This file is about everything
outside it.

## Why this rule exists

A model's recollection of an API is a plausible reconstruction, not a fact. It goes wrong
in specific, repeatable ways:

- A parameter that was renamed, or that never existed.
- A required field remembered as optional.
- A response shape from a different API version.
- A CLI flag from a different subcommand.
- A limit, quota, or pagination rule invented to fill a gap.
- A permission or IAM action that does not cover the call being made.

Each one reads as confident and correct. None of them survive contact with the real
service.

## What counts as verifying

Use the most authoritative source available, in this order:

1. **The tool's own machine-readable interface.** `aws <service> <command> help`,
   `<cli> --help`, `forge.api.raw` (`gh api --help` / `tea api --help`), an OpenAPI or GraphQL schema, TypeScript types shipped
   with the package, the MCP server's own tool schema (load it with `ToolSearch` and read
   the parameters).
2. **The official documentation**, fetched now with `WebFetch` — the vendor's own docs
   site, the package's README or reference docs at the **version the project pins**.
3. **The vendor's own examples** in that documentation.

Not authoritative: a blog post, a forum answer, a model's recollection, another project's
code, or an older version's docs.

## Documentation MCP servers

A connected documentation MCP server is the best version of step 1 and step 2 above: it
serves current, version-correct reference material without a web round trip.

At preflight, the issue-flow PM works out which external services the project depends on,
searches the marketplaces the user has configured, and offers what it found. It may also
offer to **add a marketplace** when the server it needs lives in one the user has not
added yet. Three constraints on that offer:

- **Only offer what you actually found**, or what `claude mcp list` shows is already
  connected. A marketplace may be offered only from a **concrete source** — one the user
  supplied, or one named in the repository (README, `CLAUDE.md`, the spec,
  `.claude/settings.json`). Never invent a marketplace URL, a plugin name, a server name,
  or an install command, and never present a guess as though it were available.
- **The user runs the command, or explicitly approves it.** Adding a marketplace and
  installing an MCP server both run third-party code and change their configuration.
  Never do either silently.
- **Say that a restart is usually needed.** A marketplace added, a plugin installed, or a
  server connected mid-session generally does not appear until the session restarts;
  `/mcp` shows what is live now. Work continues on the `WebFetch` fallback until then —
  never hold the loop waiting for a restart.

A declined offer is recorded and not repeated. It changes nothing about the rule below —
without a doc server, verification happens through `WebFetch` against the vendor's own
documentation. Slower, still mandatory.

## The rule, per stage

- **project-planner.** Before writing an `Interfaces` section, an `Architecture & stack`
  choice, or an `Environments & config` entry that names a third-party service, read that
  service's docs. Record the **documentation URL and the API/SDK version** next to the
  claim in the spec. A capability you could not confirm goes in
  `Risks & open questions` — never into a requirement as though it were settled.
- **spec-to-issues.** Carry those documentation links into the issue body. A worker must
  not have to go and find them, and must not have to guess which version was meant.
- **issue-worker.** Before writing the first call against an external API, verify the
  exact shape: operation name, required parameters, response fields, error cases,
  pagination, rate limits, and the permissions the call needs. Cite the doc URL in the PR
  body. If the documentation contradicts the plan, that is a `needs-feedback` — not
  something to reconcile by guessing.
- **deploy-verifier, and the Stage D deploy watch.** Run the commands the brief or the
  `deploy` block gives you. If one is rejected as malformed, confirm the correct form
  from `help` output or the provider's docs before you retry — never permute flags until
  something runs.
- **PM.** When a plan or a verdict rests on an assumed external behaviour, send it back.
  "It should support that" is not a source.

## Evidence

A claim about an external interface is evidenced the same way any other claim is:

```
Verified against: https://docs.aws.amazon.com/... (Amplify API, retrieved 2026-08-01)
Verified against: `aws amplify list-jobs help` (aws-cli/2.15.4)
```

Put that line in the spec section, the issue body, the plan comment, or the PR body that
carries the claim.

## AWS in particular

- Confirm the **service, operation and parameter names** with `aws <service> help` and
  `aws <service> <operation> help`. Operation names differ from the console's wording.
- Confirm the **IAM actions** the call needs, and say so — a call that works for you may
  fail for the deploy role.
- Confirm **which region and account** the resource lives in before a command that names
  one.
- Anything that creates, deletes, or changes a resource is an **outward-facing action**:
  confirm with the user before running it, and never run one to "see what happens".
- Read-only discovery (`list-*`, `get-*`, `describe-*`) is the safe way to learn the shape
  of real resources. Prefer it over assuming.
