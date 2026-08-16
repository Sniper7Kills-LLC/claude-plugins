# Project scaffold format (project-planner reference)

How to generate `spec.html`, `CLAUDE.md`, the `.claude/` directory and `.gitignore`.
`SKILL.md` Phase 3 points here. Nothing in `.claude/` is written before the user
approves it in the Phase 4 review round.

### spec.html

A single self-contained HTML document at `docs/specs/spec.html` — the review artifact
the user actually reads. Same rules as the mockups (inline everything, responsive,
light + dark, no external requests). It must contain:

- Project name, status, date, and a one-screen executive summary.
- Goals / non-goals / personas, the feature map, architecture, data model, epics and
  milestones — the whole design, readable top to bottom, not a link farm.
- A section per feature set summarising its FRs and acceptance criteria, linking to
  its `features/NN-*.md`.
- Every mockup embedded **both ways**: an `<iframe src="mockups/NN-x.html">` *and* a
  visible `<a href="mockups/NN-x.html" target="_blank">Open in new tab</a>` next to it.
  Browsers treat local files as opaque origins and an iframe can silently render blank,
  so the link is the guaranteed path, not a decoration.
- Open questions and assumptions, called out visually.

All links relative. Regenerate `spec.html` at the end of **every** revision round and
set `html_generated` in the front-matter — a stale spec.html is worse than none.

### CLAUDE.md

Write `CLAUDE.md` at the project root, **under 200 lines** (Claude Code loads it into
every session; longer files reduce adherence). Write it in STE, using the spec's `Terms`
words. Spec-derived facts only:

- One-paragraph project summary and its current state.
- Stack, project layout, and where things belong.
- Build / run / test / lint commands.
- Conventions the spec fixes (naming, error handling, commit style).
- A **plain-text pointer** to the spec: "Design lives in `docs/specs/` — read
  `docs/specs/spec.md` and the relevant `docs/specs/features/*.md` before building a
  feature." Write it in backticks, **not** as an `@docs/specs/spec.md` import — an
  import loads the whole spec into every session's context.
- **The filing gate** — the five cases that earn a tracker issue, and the rule that every
  other finding is repaired by the change set that found it. It belongs here rather than in
  a `.claude/rules/` file: a path-scoped rule loads only while a matching file is open, and
  this gate binds the decision to *create* an issue, which happens with no file open at
  all. It also binds the human maintainer, who reads `CLAUDE.md`. The template and the
  routing are below, under "The filing gate".

If `CLAUDE.md` already exists, propose additions and merge on approval; never rewrite.
If the repo has `AGENTS.md` and no `CLAUDE.md`, create `CLAUDE.md` containing
`@AGENTS.md` plus the project-specific additions.

#### The filing gate

Copy the five cases and the repair routing from
[`../../../references/finding-policy.md`](../../../references/finding-policy.md) into
`CLAUDE.md`, and make the **routing** concrete for this project's branch model — the
routing is the half that makes the rule executable, so a template without it ships a gate
that forbids filing and never says where the repair goes:

```markdown
- **A finding earns a tracker issue in five cases, and never otherwise.** Behavior. A
  user-visible output. A guard that guards nothing. A blocked epic. A question the
  maintainer must rule. One carve-out: anything under `docs/specs/` that describes the
  wrong product earns a spec-update issue, because the spec decides what gets built.

  **Every other finding is repaired by the change set that found it, and no issue is
  filed.** A falsified sentence, a moved citation, a stale count, a missing term and prose
  drift each reach the repair. A repair that turns out to touch behavior stops and is
  filed as a behavior finding.

  Where the repair goes:
  - Found while building an issue → that issue's own PR, limited to files it already
    touches.
  - Found at review of <integration branch>  → a documentation commit on that branch,
    before the CI-trigger commit.
  - Found with nothing open → <where this project parks it: the status issue, a
    `docs/` TODO block, the next batch>.
```

Replace the two placeholders with this project's actual branch model and parking place.
Write the gate even when the project has no tracker yet. A repository that grows its own
rule requiring every change set to record a changelog row, or its own guard that files an
issue for each stale citation, regenerates backlog faster than it clears it — this rule is
what stops that before the first issue is filed.

**Bound the changelog obligation in the same block.** The documentation duty this plugin
ships attaches to a **decision at the batch gate** — never to a member, never to a change
set. Write that as a rule alongside the gate:

```markdown
- A changelog or spec-changelog entry records a **decision**, made at a batch gate. No
  rule, hook, CI check, or test may require a changelog row, a spec edit, or a
  docs-accuracy pass **per change set or per batch member** — a batch that shipped as
  planned earns no line. A test that asserts per-member bookkeeping agreement re-creates
  the churn the filing gate exists to prevent, and fails review here.
```

This bound exists because it was measured missing: a project hardened the plugin's
"cheap, always" changelog mechanism into a per-member two-file mandate held together by
1,500+ lines of agreement tests, and the two most-contended files in the repository
became a mandatory edit for every member. The obligation firing per member, not per
finding, regenerates the churn regardless of what the filing gate says.

### .claude/

```
.claude/
├── settings.json           # permissions + hooks (committed)
├── rules/
│   ├── <topic>.md          # unconditional: architecture invariants
│   └── <area>.md           # path-scoped via `paths:` frontmatter
├── skills/
│   └── <name>/SKILL.md     # the project's own repeatable procedures
└── commands/
    └── <name>.md           # optional: one-shot prompts too small for a skill
```

Everything here is committed and team-shared. Propose the full set — the permission
list, each hook, each skill — in the [review round](#phase-4--review-cycle-gate-user-approval)
**before** writing, the way `/init`'s interactive flow does. Scaffold only what the spec
justifies; an unused skill is context cost with no payoff.

#### settings.json

A **conservative** `permissions.allow` list derived from the chosen stack — the
project's own test, build, lint, typecheck and package-manager commands, nothing more —
plus any hooks (below).

**This list is what makes autonomous building possible.** issue-flow's workers run as
background subagents: they cannot surface a permission prompt to anyone, so a command
that isn't allow-listed stalls or fails the issue. Enumerate every command the project's
own workflow needs — install, test, lint, typecheck, build, start, migrate, seed — from
the spec's `Testing strategy` and Epic 0. Under-listing costs autonomy; over-listing
costs safety, and the rules below are the floor:

- Never `permissions.deny` bypasses, never `defaultMode: bypassPermissions`.
- Never blanket wildcards (`Bash(*)`, `Bash(rm:*)`, `Bash(curl:*)`) — enumerate the
  commands the project actually runs.
- Never secrets, tokens, or machine-specific absolute paths; this file is committed.
- Anything personal or machine-local belongs in `settings.local.json`, which is
  gitignored — don't write it.

#### rules/

Content too long or too situational for CLAUDE.md. Path-scoped rules use `paths:`
frontmatter (glob patterns) and load only when Claude reads a matching file, so detail
here is cheap.

**Always write `.claude/rules/ste.md`.** This is how the writing standard survives the
handoff: `spec-to-issues`, every issue-flow worker, and the `project-review` scribe run
in the *project*, not in this plugin, so they read the project's copy. Copy the substance
of [`../../../references/ste.md`](../../../references/ste.md) into it — the rules (§ 2), the
verbatim-exclusions (§ 1), the artifact patterns (§ 4, **including the code-comment
patterns**) and the checklist (§ 5) — and replace § 3 with a pointer to the spec's own
`## Terms` table, which is the live vocabulary.

**Scope it to prose *and* to source**, because code comments follow the standard too. A
rule scoped only to `docs/**` never loads while a worker edits a `.ts` file, and the
standard dies exactly where most of the writing happens. Use the stack's own source
globs:

```markdown
---
paths:
  - "docs/**/*.md"
  - "*.md"
  - ".claude/rules/*.md"
  - "src/**/*.{ts,tsx,js,jsx}"     # ← the project's actual source globs
  - "tests/**/*.{ts,tsx}"
---

# Writing standard (STE)

Every spec file, issue body, comment, manual page and **code comment** in this project
uses Simplified Technical English. The project's controlled vocabulary is the `## Terms`
table in `docs/specs/spec.md` — read it before you write a domain word.
…
```

**Always write `.claude/rules/external-apis.md` too**, whenever the project talks to any
service it does not own. Same reason as the STE rule: workers and reviewers run in the
project, not in this plugin, so the standard has to live in the repo. Copy the substance
of [`../../../references/external-apis.md`](../../../references/external-apis.md), then
make it concrete for **this** project — name the actual services, pin the API/SDK
versions the spec chose, and link their documentation:

```markdown
---
paths:
  - "src/**/*.{ts,tsx,js,jsx}"     # ← the project's actual source globs
  - "infra/**"
  - "docs/specs/**/*.md"
---

# External APIs — read the docs, never assume

Before you write a call against any service below, confirm the operation name, required
parameters, response shape, error cases and permissions from its documentation. Cite the
URL and version in the PR body.

| Service | Version pinned | Documentation |
|---|---|---|
| AWS Amplify Hosting | aws-cli 2.x | https://docs.aws.amazon.com/amplify/… |
| Stripe | API 2025-… | https://docs.stripe.com/api |

Anything that creates, deletes or changes a cloud resource is confirmed with the user
first. Read-only `list-*` / `get-*` / `describe-*` calls are the safe way to learn the
shape of a real resource.
```

Other path-scoped rules follow the same shape:

```markdown
---
paths:
  - "src/api/**/*.ts"
---

# API rules
- Every endpoint validates input with <the spec's chosen validator>.
- Errors use the shape defined in docs/specs/spec.md § Cross-cutting concerns.
```

#### skills/

A skill is a **procedure**, loaded on demand — the right home for multi-step workflows
that would otherwise bloat CLAUDE.md. Write one per repeatable project workflow the
spec implies. Typical set for a new project:

- `run` — how to start the app locally (deps, env, ports, seed data, how to tell it's
  up). Highest-value skill in a fresh repo; `issue-flow` workers and `project-review`
  both need it.
- `test` — how to run unit / integration / E2E suites, and which to run when.
- `deploy-check` — how to verify a deploy against the spec's deploy target.
- Domain procedures the spec calls out (e.g. `add-migration`, `add-endpoint`) where the
  steps are non-obvious and repeated.

Layout and frontmatter:

```markdown
---
name: run
description: Start the app locally for manual checks. Use when asked to run, launch, or
  screenshot the app.
argument-hint: "[port]"       # optional
allowed-tools: Bash, Read     # optional; pre-approves tools for the invoking turn only
---

# Run <Project>

1. …
```

- One directory per skill: `.claude/skills/<name>/SKILL.md`, plus any supporting files
  next to it (referenced via `${CLAUDE_SKILL_DIR}`).
- `description` decides when Claude auto-loads it — put the trigger case first.
- Keep `allowed-tools` minimal and never pre-approve destructive commands.
- Refer to project files by repo-relative path or `${CLAUDE_PROJECT_DIR}`, never
  absolute paths — the package must survive being unzipped elsewhere.

#### commands/

`.claude/commands/<name>.md` is the single-file form of the same mechanism and produces
the same `/name`. Use it only for a prompt with no supporting files and no frontmatter
worth setting; otherwise prefer a skill.

#### hooks

Hooks go in `settings.json` and run as shell commands at lifecycle events — they
execute regardless of what Claude decides, which makes them the right tool for
"always" rules that a CLAUDE.md line only *asks* for. Scaffold hooks the spec's
conventions justify, and **only** ones that are safe to run unattended:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "<formatter> --write \"$CLAUDE_FILE_PATHS\"" }
        ]
      }
    ]
  }
}
```

Rules for generated hooks:

- Format-on-edit and lint-on-edit are the safe, high-value defaults. A test-suite hook
  is usually too slow to run on every edit — put it in the `test` skill instead.
- Fast (sub-second where possible), idempotent, and non-interactive.
- Never destructive, never network-mutating, never `git push`/`git commit`, never
  anything that rewrites files outside the repo.
- Reference project scripts as `${CLAUDE_PROJECT_DIR}/…`, never absolute paths.
- Every hook must degrade quietly when its tool isn't installed yet (a fresh unzip has
  no `node_modules`) — guard with `command -v <tool> >/dev/null || exit 0`.
- List every hook and its exact command in the review round. A hook the user didn't
  approve does not get written.

### .gitignore

Ensure the project root `.gitignore` contains this block, appended verbatim if the file
already exists (never rewrite an existing `.gitignore`; skip lines already present):

```gitignore
# Claude Code
.claude/settings.local.json
.claude/worktrees/
.claude/agent-memory-local/
CLAUDE.local.md
.claude.local.md
.issue-flow.local.json
```

Rationale, so you can explain it when asked:

- `settings.local.json` — Claude Code adds it to the machine-level
  `~/.config/git/ignore`, which does **not** travel with the repo; the team-shared rule
  has to live here.
- `.claude/worktrees/` — worktree checkouts, otherwise they appear as untracked files
  in the main checkout. issue-flow's per-issue worktrees live here too.
- `.issue-flow.local.json` — one operator's personal overrides of the committed
  `.issue-flow.json` run configuration, so concurrent sessions don't fight over it.
- `.claude/agent-memory-local/` — the `memory: local` target, deliberately non-shared.
- `CLAUDE.local.md` / `.claude.local.md` — personal per-project memory.

Add the project's own stack ignores (dependencies, build output, `.env*`, coverage,
OS/editor cruft) in a separate block. **Nothing under `docs/specs/` is ignored** — see
Phase 2.

If the project keeps gitignored files that every worktree needs (`.env`, local
secrets), also write a `.worktreeinclude` at the root listing them; it uses
`.gitignore` syntax and only copies files that are both matched and gitignored.
