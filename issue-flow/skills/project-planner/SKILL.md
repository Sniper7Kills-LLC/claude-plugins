---
name: project-planner
description: Interactive project planning that produces a reviewable spec package plus a ready-to-work project scaffold. Use when the user says "/project-planner", "plan a project", "spec out <idea>", "design a project", or wants a new project/feature-set defined before any code. Interviews the user, writes a detailed multi-file spec (docs/specs/), self-contained HTML mockups, a browsable spec.html, CLAUDE.md, .claude/ (settings + permissions, hooks, rules, project skills/commands) and .gitignore, runs a user review cycle until the spec is approved — then hands off to spec-to-issues, which creates the tracker epics/sub-issues that issue-flow builds.
---

# Project Planner — spec first, scaffold second, issues later, code last

You are the **planner**. Your deliverable is an **approved spec package plus a working
project scaffold on disk** — not issues, not application code. The pipeline is:

```
/project-planner  →  docs/specs/ + CLAUDE.md + .claude/ + .gitignore  (review → approved)
/spec-to-issues   →  tracker epics + sub-issues
/issue-flow       →  built, PR'd, merged, deployed
```

You never create tracker issues (that's `spec-to-issues`) and you never implement the
product (that's `issue-flow`). Do not skip the user review cycle — an unapproved spec
never moves forward.

## Ground rules

- **Everything you write is Simplified Technical English.** The spec, every feature file,
  `spec.html` prose, `CLAUDE.md`, and every `.claude/rules/*.md` file follow the standard
  in [`../../references/ste.md`](../../references/ste.md). **Read that file before you
  write anything in Phase 2.** A spec is read by engineers, by agents, and by later
  planning waves — one sentence that reads two ways gets built two ways. The one thing
  you never rewrite is the user's own words: quote them verbatim, then restate in STE.
  You also carry the standard into the project itself (Phase 3, `.claude/rules/ste.md`),
  so `spec-to-issues`, workers and reviewers keep writing the same way.
- **Never assume an external interface — read its documentation.** Any cloud service,
  third-party API, provider CLI or library the spec names is described from that tool's
  own docs, fetched now, at the version the project pins. Record the doc URL and version
  beside the claim; a capability you could not confirm goes to `Risks & open questions`,
  not into a requirement. The standard, and the AWS-specific rules, are in
  [`../../references/external-apis.md`](../../references/external-apis.md).
- **One project per repository.** The project lives at the directory you were invoked
  in — the repo root. Do not create a per-project `<slug>/` subfolder under
  `docs/specs/`. If the user is genuinely planning several projects at once they will
  say so explicitly; only then ask how they want them separated.
- **The spec is the product brief, not a summary.** Detail is the point (see
  [Phase 2](#phase-2--author-the-spec-package)). A reader who has never seen the
  conversation must be able to review the design from the spec alone.
- **Everything is portable and relative.** This package must survive being zipped,
  downloaded, and unzipped somewhere else: relative links only, no absolute paths, no
  machine-specific values, no external asset requests. Assume the target may not be a
  git repo yet.
- **Never publish Artifacts.** Mockups and `spec.html` are files in the project. Review
  happens by opening `docs/specs/spec.html` locally (see
  [Phase 4](#phase-4--review-cycle-gate-user-approval)). Do not call the Artifact tool.
- **Never overwrite existing project files.** `CLAUDE.md`, `.claude/settings.json` and
  `.gitignore` are merged/appended and reported, never replaced.

## Phase 0 — Survey before asking

Before the first question, spend one round finding out what you can without the user:

- Is this a git repo (`git rev-parse --git-dir`)? Is there a remote? Is the tree empty
  or an existing codebase?
- Existing `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.gitignore`, `README`, package
  manifest, lockfile, CI config, `docs/specs/`?
- Existing codebase → delegate a survey (`Explore`, or `cavecrew-investigator` if
  present) for stack, layout, test command, conventions.
- A `docs/specs/spec.md` already present → this is a **revision**, not a new plan. Read
  it, and interview only about what's changing.

Report what you found in one short paragraph before Phase 1, so the user knows what you
already know.

## Phase 1 — Interview

Understand before writing. Use `AskUserQuestion` in **small batched rounds (≤4
questions each**, concrete options plus free text). **Loop as many rounds as the
project needs** — but stay at product altitude. Ask about a detail only when the spec
genuinely cannot be written without it; do not interrogate the user about anything you
can decide from convention, infer from their stack answer, or find out yourself.

Cover, roughly in this order:

1. **Goal & users** — what is it, who uses it, what problem dies.
2. **Scope line** — must-have vs nice-to-have vs explicit non-goals for v1.
3. **Stack & platform** — language/framework, hosting/deploy target (issue-flow will
   monitor it), greenfield or existing repo, data storage, auth.
4. **Branch model** — ask directly, because it decides where every batch lands and
   whether "production" is protected:
   - **dev-and-live** — a `dev` integration branch that batches merge into, promoted to
     the live branch as a separate approved step. Epic 0 creates `dev`.
   - **trunk** — one branch; every batch merges to the default branch and (if a deploy
     target exists) deploys straight away.
   Record the answer as `branch_model` in the spec front-matter; issue-flow reads it at
   preflight instead of guessing from what branches happen to exist.
5. **Constraints** — budget/CI limits, timeline, compliance, design/brand direction,
   third-party integrations.
6. **Per feature set, one round each** — for anything you cannot specify to the depth
   Phase 2 requires: the real workflow, the states, what happens when it goes wrong.

Between rounds do homework instead of asking: explore the repo, `WebSearch` comparable
products or candidate libraries, check what the chosen framework makes conventional.

**Homework includes reading the docs of every external service the answers commit you
to.** Once the user picks a host, a payment provider, an auth provider or a data store,
fetch that provider's current documentation before you write its `Interfaces`,
`Architecture & stack` or `Environments & config` entries. Do not describe an API from
memory — see [`../../references/external-apis.md`](../../references/external-apis.md).

Stop interviewing when you can write every section below **without inventing product
intent**. Anything still unknown that is not product-critical becomes a documented
assumption; anything product-critical that is still unknown goes to
`Risks & open questions` and blocks approval.

## Phase 2 — Author the spec package

**Read [`../../references/ste.md`](../../references/ste.md) now, before the first file.**
Every word below is written to that standard, and the `## Terms` list you build in
`spec.md` becomes the project's controlled vocabulary — `spec-to-issues` writes issue
bodies from it, workers build against it, and the user manual uses it. Add a term the
first time a domain noun or verb appears, and never rotate synonyms afterwards.

Write to `docs/specs/` at the project root:

```
docs/specs/
├── spec.md                 # the index: whole-project design
├── spec.html               # generated browsable overview (Phase 3)
├── features/
│   ├── 01-<feature>.md     # one file per feature set — the deep detail
│   └── ...
└── mockups/
    ├── 01-<screen>.html    # self-contained, one per key screen
    └── ...
```

Nothing here is gitignored: `spec-to-issues` puts repo-relative links to these files
into issue bodies, and issue-flow's workers run in **git worktrees, which contain
tracked files only**. An uncommitted spec is invisible to the engineers who build it.

### The files — templates, Epic 0, epic sizing, mockups

`spec.md`'s section-by-section template, the `features/NN-*.md` template, the mandatory
**Epic 0: Foundation**, the epic-sizing rules that shape issue-flow's batches, and the
mockup rules are all in **[references/spec-format.md](references/spec-format.md)**. Read
it before you write the first file — it is the format contract `spec-to-issues` reads
back.

## Phase 3 — Generate spec.html and the project scaffold

Generate the browsable `spec.html`, then `CLAUDE.md`, `.claude/` (settings, rules,
skills, commands, hooks) and the `.gitignore` block. The format and the rules for each —
including the permission floor, the hook safety rules, and the mandatory
`.claude/rules/ste.md` — are in **[references/scaffold.md](references/scaffold.md)**.

Two things gate this phase, so they stay here:

- **Nothing under `.claude/` is written until the user approves it** in the Phase 4
  review round. Propose the permission list, every hook, and every skill first.
- **Never overwrite an existing `CLAUDE.md`, `.claude/settings.json` or `.gitignore`.**
  Merge, append, and report what changed.


## Phase 4 — Review cycle (gate: user approval)

1. Print a **short digest** in the terminal: goals, epic list with sizes, stack, and the
   open questions — plus the scaffold **proposal**: the exact `permissions.allow`
   entries, each hook's exact command, and each skill/command with a one-line purpose.
   Permissions, hooks and skills are written only after the user approves them; the
   spec files and mockups may be written first so there is something to review.
2. **Open the spec locally** — `xdg-open docs/specs/spec.html` (Linux), `open` (macOS),
   `start ""` (Windows). If the command is unavailable or the session is
   non-interactive, print the absolute path instead and continue; never fail the phase
   over it. Do not publish an Artifact.
3. Ask via `AskUserQuestion`: **Approve** / **Revise** (collect what to change), plus
   the specific items from `Risks & open questions`.
4. On revise: edit the spec files and mockups, **regenerate `spec.html`**, re-open, and
   re-present. Loop until approved. Record every round in `## Changelog`.
5. On approval:
   - set `status: approved` and `approved: <date>` in `spec.md` front-matter;
   - if the project is a git repo, commit the spec package and the scaffold together;
   - if it is not a git repo yet, say so and offer `git init` — the package works
     unzipped and un-initialised, but nothing downstream does.

## Phase 5 — Handoff

`spec-to-issues` writes issue bodies that link to `docs/specs/...`, and issue-flow's
workers see only **tracked, pushed** files. So before handing off, state plainly:

> The spec must be committed **and pushed** before `/spec-to-issues`, or every spec link
> in every issue will be dead and the workers will build blind.

Then give the next commands:

```
/spec-to-issues        # create the tracker epics + sub-issues from docs/specs/
/issue-flow            # build it autonomously
```

Do not run them yourself unless the user asks.

## Output checklist

Before you declare the planner done, verify:

- [ ] **Every prose file passes the STE checklist** (`references/ste.md` § 5): sentences
      within length, one instruction per step, one concept one word, active voice,
      no metaphor, no `-ing` headings. User quotes and evidence left verbatim.
- [ ] **Every external interface in the spec cites its documentation** — URL plus the API
      or SDK version — and anything unconfirmed sits in `Risks & open questions` rather
      than in a requirement.
- [ ] `docs/specs/spec.md` — front-matter complete (including `branch_model` and the
      `features:` id/file list matching the directory), every section written, and a
      `## Terms` table covering every domain noun and verb the spec uses.
- [ ] **`Epic 0: Foundation` exists** with `features/00-foundation.md` — test harness,
      CI workflow, branch model, deploy wiring, seed data — or the spec states why an
      existing codebase doesn't need it.
- [ ] `docs/specs/features/*.md` — one per feature set, each with a stable `id:`,
      `status: planned` and an empty `issues: []`, every section written, every FR
      numbered, singular and testable, and covered by acceptance criteria.
- [ ] `docs/specs/mockups/*.html` — self-contained, each referenced by exactly one
      feature file, no orphans in either direction, no hand-edited mockup silently
      regenerated.
- [ ] `docs/specs/spec.html` — regenerated after the last edit, `html_generated` set,
      every mockup has both an iframe and a visible fallback link, all links relative.
- [ ] `CLAUDE.md` — under 200 lines, pointer to the spec is plain text not `@import`.
- [ ] `.claude/settings.json` — conservative permissions, no wildcards, no secrets;
      every hook approved by the user and guarded against a missing tool.
- [ ] `.claude/rules/ste.md` — written, scoped to the prose paths, pointing at the
      spec's `## Terms` table. Without it the standard dies at the handoff.
- [ ] `.claude/rules/*.md` — path-scoped where the content is area-specific.
- [ ] `.claude/skills/*/SKILL.md` — at minimum `run` and `test` for a fresh project;
      relative paths only, minimal `allowed-tools`.
- [ ] `.gitignore` — Claude Code block present, spec files not ignored.
- [ ] No absolute paths, no external asset requests, no Artifacts published.
- [ ] `status: approved` only if the user actually approved.
