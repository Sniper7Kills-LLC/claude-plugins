---
name: project-inherit
description: Bring an existing codebase into the issue-flow pipeline by reverse-engineering the full spec package from what is already built. Use when the user says "/project-inherit", "inherit this repo", "adopt this project", "plan an existing project", "onboard this codebase to issue-flow", or asks for issue-flow on a repo that has code but no docs/specs/. Reviews the entire repository with fanned-out read-only survey agents, harvests any existing documentation (README, docs/, ADRs, wikis, specs in other formats), interviews the user only about what code cannot answer, then writes the same approved spec package project-planner produces — built features marked built, gaps and roadmap marked planned — plus the project scaffold, ready for spec-to-issues and issue-flow.
---

# Project Inherit — the planner for code that already exists

You are the **inheritor**. `project-planner` starts from an idea and writes the spec
forward; you start from a repository and write the spec **backward** — the package that
planner *would* have produced if it had planned this project, reconciled with what was
actually built. Your deliverable is identical to the planner's: an approved
`docs/specs/` package plus a working scaffold. The pipeline downstream does not know or
care which skill produced it:

```
/project-inherit  →  docs/specs/ + CLAUDE.md + .claude/ + .gitignore  (review → approved)
/spec-to-issues   →  epics + sub-issues (planned features only)
/issue-flow       →  built, PR'd, merged, deployed
```

You never create tracker issues and you never change product code. **Every agent you
spawn in Phases 0–1 is read-only** — survey, never fix; a defect you find is spec
content and backlog material, not something to patch on the way through.

## Ground rules

All of `project-planner`'s ground rules apply verbatim — STE everywhere
([`../../references/ste.md`](../../references/ste.md), read before Phase 3), external
interfaces described from fetched docs only
([`../../references/external-apis.md`](../../references/external-apis.md)), one project
per repository, portable relative paths, no Artifacts, never overwrite existing
`CLAUDE.md` / `.claude/settings.json` / `.gitignore`. Plus three of your own:

- **The code is the senior witness.** Where existing documentation and the code
  disagree, the code is the fact and the document is a claim; record the conflict in
  `Risks & open questions`, never silently pick the document.
- **Describe, don't rename.** The `## Terms` vocabulary comes from the names the code
  already uses. A confusing domain name is recorded with its meaning and flagged as a
  candidate ADR — renaming a live domain is a tracked decision, not a spec edit.
- **Never fabricate history.** A feature you found built gets `status: built` and a
  spec written from its observed behaviour. If you cannot determine what a built
  feature does, that is an open question, not a guess.

## Wrong-entry checks

- `docs/specs/spec.md` already exists → this project is already in the pipeline. Point
  the user at `/spec-update` (change or refresh) or `project-planner`'s revision path,
  and stop.
- The repo is empty or has no meaningful code → this is a greenfield plan. Hand off to
  `/project-planner` and stop.

## Phase 0 — Harvest what is already written

Before reading code, collect every existing statement of intent:

- `README*`, `docs/**`, `CONTRIBUTING*`, `ARCHITECTURE*`, `docs/adr/**` or `adr/**`,
  `CHANGELOG*`, OpenAPI/GraphQL schemas, `.github/` templates, wiki exports, and any
  spec-shaped files in other formats (Notion/Confluence exports, `PRD*`, `SPEC*`,
  `*.rfc.md`).
- Existing `CLAUDE.md` / `AGENTS.md` / `.claude/` — these are prior operator decisions;
  the scaffold merges around them.
- Git archaeology, cheaply: default branch, tags/releases, the last ~50 commit
  subjects, open PRs and issues if a forge remote exists. This tells you what the team
  ships and what vocabulary they use.

Inventory what you found in one short table (source → what it claims to cover → date if
knowable). These documents are **input, not authority** — Phase 1 verifies every claim
against the code.

## Phase 1 — Review the entire repository

Fan out **read-only survey agents** (`Explore` agents; one per area, in parallel,
**spawned unnamed** — never pass `name:`, the spawn guard denies a named agent without
worktree isolation, and one denial here stalls the phase seven times over) and
have each return structured findings with `file:line` evidence. Size the fan-out to the
repo — a small repo may merge areas, a monorepo may need one pass per package — but
cover all of:

1. **Stack & layout** — languages, frameworks, package manifests, build tooling,
   directory conventions, entry points.
2. **Data model** — schemas, migrations, ORM models, stored file formats: every entity,
   field, constraint, relation, lifecycle, plus where writes happen.
3. **API & module surface** — routes/endpoints/handlers (method, path, request,
   response, status codes, error shapes) or the public module contracts of a library.
4. **UI screens & flows** — screens/pages/commands, navigation, the user flows the code
   actually implements, states handled (empty/loading/error) and states missing.
5. **Cross-cutting** — auth/authz as implemented, validation, error handling, logging,
   i18n, accessibility signals, performance-sensitive paths, security posture.
6. **Tests, CI & operations** — test frameworks and the real commands, coverage shape,
   CI workflows, deploy targets and scripts, environment variables (**names only**),
   seed/demo data.
7. **Conventions & quality** — naming, commit style in the log, lint/format configs,
   and the slop inventory: spawn the **`issue-flow:code-auditor`** agent for TODOs,
   stubs, dead code and placeholder content.

Reconcile the areas into one working map, then **diff it against the Phase 0 harvest**.
Three lists come out: *confirmed* (doc and code agree), *contradicted* (code wins; the
conflict goes to `Risks & open questions`), *undocumented* (code does something no
document mentions — usually most of the list).

## Phase 2 — Interview: only what the code cannot answer

Use `AskUserQuestion` in batched rounds (≤4 each), like the planner — but your
question budget is smaller, because the code answered the mechanical half. Ask about:

1. **Product intent** — what the project is for, who uses it, what problem dies. Code
   shows *what*; only the user knows *why* and *for whom*.
2. **Goals, non-goals, personas** — including which built behaviour is load-bearing
   versus vestigial ("is this admin page still used?").
3. **Contradictions and mysteries** — the Phase 1 contradicted list, and any built
   feature whose purpose you could not determine.
4. **The roadmap** — what should issue-flow build next: new features, known bugs, the
   debt worth paying down. This becomes the `planned` half of the spec.
5. **Branch model and deploy facts** you could not detect — asked exactly as
   `project-planner` Phase 1 asks them (`branch_model` lands in the front-matter).

Never ask what a survey agent, a document, or a convention can answer. Anything
non-critical still unknown becomes a documented assumption.

## Phase 3 — Author the spec package, backward

Write the same package to the same contract —
[`../project-planner/references/spec-format.md`](../project-planner/references/spec-format.md)
— with the inherit-specific rules:

- **`spec.md`** — every section, including the mandatory architecture flowchart and
  data-model erDiagram, drawn **from the measured reality**, not from aspiration. The
  `Assumptions` section carries your code-derived inferences; the contradicted list
  lands in `Risks & open questions`.
- **Built features** — one `features/NN-*.md` per existing feature set with
  `status: built`, `issues: []`, and every section written from observed behaviour
  (flows diagrammed, screens and states as they exist, interfaces as implemented, edge
  cases the code actually handles — and, called out plainly, the ones it does not).
  `spec-to-issues` creates nothing for `built`, so documenting reality is free of
  issue-noise. **No mockups for built features**: the app is its own mockup; `Screens
  & states` points at real routes/components instead.
- **Planned features** — the roadmap from Phase 2, written to full planner depth:
  `status: planned`, mockups, diagrams, FRs, acceptance criteria. This is what
  `spec-to-issues` will issue.
- **Foundation** — apply `spec-format.md`'s existing-codebase rule: audit every Epic 0
  item (test harness, CI, lint with complexity rules, branch model, deploy wiring,
  seed data, smoke path) and write a right-sized foundation epic for **only the
  missing items**. The code-auditor's findings that the user wants fixed become
  backlog entries here or in the planned features.
- **ADR backfill** — seed `docs/adr/` per
  [`../project-planner/references/scaffold.md`](../project-planner/references/scaffold.md),
  and additionally write one short ADR (`Status: accepted`) for each major
  already-made choice Phase 1 surfaced — stack, data store, auth approach — with the
  context you could reconstruct. Date them today and say they were reconstructed;
  an approximate record beats none.
- **`docs/external.md`** — seeded from the Phase 1 environment/CI inventory. Names and
  pointers only, never a value.

## Phase 4 — Scaffold, render, review

Identical to `project-planner` Phases 3–4, same reference
([`scaffold.md`](../project-planner/references/scaffold.md)): generate `spec.html` with
the copied `render-spec.py` (vendor the mermaid asset), propose — never impose — the
`.claude/` scaffold, **merge** with everything that already exists, derive
`.claude/rules/quality.md` from the conventions the code actually follows (plus the
default slop list), and run the review cycle to explicit approval. The digest for the
first review round leads with the three Phase 1 lists — confirmed / contradicted /
undocumented — because that is what the user cannot get anywhere else.

## Phase 5 — Handoff

Same as the planner: the spec must be committed **and pushed**, then

```
/spec-to-issues        # issues the planned features + foundation gaps only
/issue-flow            # build them
```

Say explicitly how many features went out `built` (documented, no issues) versus
`planned` (to be issued), so the user knows what the tracker is about to receive.

## Output checklist

Everything on `project-planner`'s output checklist applies. Additionally verify:

- [ ] Every `status: built` feature file describes observed behaviour with no invented
      intent, and no mockups.
- [ ] The confirmed / contradicted / undocumented reconciliation appeared in the review
      digest, and every contradiction is in `Risks & open questions`.
- [ ] Reconstructed ADRs say they were reconstructed.
- [ ] The foundation epic contains only the audited gaps, not a re-scaffold of what
      exists.
- [ ] No survey agent modified anything; `git status` shows only the spec package and
      the approved scaffold.
