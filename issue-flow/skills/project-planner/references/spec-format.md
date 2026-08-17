# Spec package format (project-planner reference)

The file templates and sizing rules for `docs/specs/`. `SKILL.md` Phase 2 points here.
Everything below is written to the STE standard in `../../../references/ste.md`.

### spec.md — the index

```markdown
---
name: <Project Name>
slug: <kebab-case-name>       # used for issue markers, not for directory nesting
repo: <owner/name | null>     # null until the remote exists
status: draft                 # draft → approved (only the user's approval flips this)
spec_version: 2
created: <YYYY-MM-DD>
approved: null                # date on approval
html_generated: null          # date spec.html was last regenerated
branch_model: dev-and-live    # dev-and-live | trunk  (the user's choice — see Phase 1)
features:                     # ordered, must match features/ exactly
  - id: foundation            # stable id, never renamed — dedup key for spec-to-issues
    file: features/00-foundation.md
  - id: <feature-id>
    file: features/01-<feature>.md
pages:                        # optional: supplement pages, rendered like features
  - id: implementation-plan   #   (own html/ page + sidebar entry) but never issued —
    file: pages/implementation-plan.md   # spec-to-issues reads features/ only
---

# <Project Name>

## Overview               — 2-3 paragraphs: what, for whom, why now
## Terms                  — the project's controlled vocabulary: every domain noun and
                            verb, its part of speech, its one meaning, and the synonyms
                            this project rejects. Table format in references/ste.md § 3.
                            Everything downstream writes from this list.
## Goals                  — measurable outcomes
## Non-goals              — explicitly out of v1 (as load-bearing as Goals)
## Users & personas       — who, what each needs, what each is allowed to do
## Feature map            — table: feature set → spec file → epic → mockups
## Architecture & stack   — opens with a mermaid flowchart of the components and the
                            data flow between them; then per component: hosting/deploy
                            target, key libraries, and why each choice beat the
                            alternative
## Data model             — opens with a mermaid erDiagram of the entities and their
                            relations; then per entity: fields, types, constraints,
                            lifecycle
## Cross-cutting concerns — auth/authz model, error handling, validation, logging,
                            accessibility, i18n, performance targets, security posture
## Environments & config  — envs, every environment variable and what it does, secrets
                            handling, seed/demo data
## Testing strategy       — unit/integration/E2E split, what "done" means for a change
## Epics                  — epic list: name, goal, the feature files it covers,
                            dependencies, exit criteria. NO sub-issue checklist —
                            spec-to-issues derives those from features/.
## Milestones             — epic ordering, and what "shippable" means at each
## Assumptions            — decisions you made that the user did not state
## Risks & open questions — anything unresolved (product-critical items block approval)
## Changelog              — one entry per review round
```

### features/NN-\<feature\>.md — the detail

One file per feature set. **This is what `spec-to-issues` reads to invent issues**, so
it must be richer than any issue list: complete enough that an engineer who reads only
this file can build the feature without asking a product question.

```markdown
---
id: <feature-id>              # stable, kebab-case, NEVER changed once issued —
                              #   spec-to-issues dedups on this, not on the title
feature: <Feature Name>       # display name; safe to rename
epic: <epic name from spec.md>
status: planned               # planned → issued → built (spec-to-issues and the PM
                              #   advance this; only `planned` gets issues created).
                              #   `retired` (set by /spec-update, from any state) also
                              #   gets no issues: the file stays as the record.
issues: []                    # filled in by spec-to-issues with the numbers it created;
                              #   leave empty — the planner never writes issue numbers
mockups: [mockups/03-<screen>.html]
---

## Purpose               — what this feature set is for, in user terms
## User stories          — "As a <persona>, I want <x>, so that <y>"
## Functional requirements
   FR-<feature>-1 … n    — numbered, individually testable statements. One requirement
                           per statement, active voice, present tense, no conjunction,
                           no "should be able to", no vague quantity. Patterns and
                           worked examples: references/ste.md § 4.
## User flows            — one mermaid diagram per flow (flowchart, or sequenceDiagram
                           when actors exchange messages): happy path plus each failure
                           branch, entry points included — followed by the numbered STE
                           steps. Diagram and steps must agree; the reviewer reads the
                           picture, the builder reads the steps.
## Screens & states      — per screen: purpose, regions, every state
                           (empty / loading / populated / error / permission-denied),
                           and its mockup link
## Behaviour rules       — validation, defaults, limits, ordering, pagination,
                           permissions per persona
## Data touched          — entities read/written, new fields, migrations
## Interfaces            — API endpoints or module contracts: method, path, request,
                           response, status codes, error shapes. For anything the project
                           does not own (cloud service, third-party API, provider CLI),
                           read the vendor's current docs and cite the URL + API/SDK
                           version beside the claim — never describe it from memory.
                           See ../../../references/external-apis.md.
## Edge cases & failures — what happens when it goes wrong, and what the user sees
## Acceptance criteria   — verifiable, per FR; this becomes issue "definition of done".
                           Each states one observable result, not an implementation and
                           not "works correctly" — see references/ste.md § 4.
## Out of scope          — what this feature explicitly does not do in v1
## Open questions        — blank if none
```

### pages/\<name\>.md — supplement pages (optional)

Free-form pages for material that is neither the index nor a feature: an
implementation plan (epic-by-epic work breakdown with its dependency graph),
infrastructure, repository layout, library choices. Front-matter is `id:` (stable,
kebab-case) and `title:`; the body is unconstrained but still STE. List each page in
`spec.md`'s `pages:` front-matter — unlisted files are not rendered. **Nothing
downstream reads them**: `spec-to-issues` derives work from `features/` only, so
anything that must become an issue belongs in a feature file, not a page.

### Epic 0 — Foundation (mandatory on a greenfield project)

**The first epic is always the foundation, and it is not optional.** Everything
downstream assumes it: issue-flow reads a test command out of it, its CI workflow is
what "one CI run per batch" runs, its deploy wiring is what Stage D monitors, and the
session's `practices` (TDD, coverage, E2E) are unenforceable until the harness exists.
A spec that jumps straight to features produces workers landing in an empty repo with
nothing to run.

Write it as `features/00-foundation.md` plus an `Epic 0: Foundation` entry, covering:

- Repo scaffold for the chosen stack — framework init, package manifest, directory
  layout as the spec's `Architecture & stack` describes it.
- Test harness (unit + integration) and the exact `test` command; lint, format,
  typecheck commands. These become `conventions` in every worker brief. The lint config
  enables the linter's **complexity and maintainability rules** (cyclomatic complexity,
  max nesting, unused/dead code — whatever the chosen linter offers), mirroring
  `.claude/rules/quality.md`: what a rule can catch mechanically should fail a check,
  not wait for a reviewer.
- CI workflow that runs those commands on pull requests.
- The **branch model the user chose** (below) — create `dev` when they picked
  dev-and-live.
- Deploy target wiring for the spec's hosting choice, and the seed/demo data script.
  When the hosting platform deploys outside the forge's own Actions, this includes a
  **deploy-status command** (for example `scripts/deploy-status.sh`) printing
  `<state> <jobId> <sha>` with a normalized state — issue-flow's Stage D watches
  deployments through it, and ships no provider integrations of its own.
- A first end-to-end smoke path (app builds, starts, serves one route) so epic 1 has
  something to build on.

Exit criteria: a fresh clone can install, test, lint, build and start the app with the
commands `CLAUDE.md` documents.

On an **existing codebase** with these already in place, say so and skip Epic 0 — but
check each item, and put the missing ones in a smaller foundation epic rather than
assuming.

### Epic sizing (shape the plan for issue-flow batching)

Epics live in `spec.md`; the work inside them is derived later from `features/`. Shape
epics so that:

- **One epic = one batch = one PR = one CI run.** Each must be independently mergeable
  and leave the app shippable (or at least green) when its batch lands.
- **An epic decomposes cleanly into 3–6 sub-issues of ≤ ~1 focused day each.** If a
  feature set can't, split it across epics and say so in `Epics`.
- **Dependencies stay inside an epic** wherever possible; issue-flow sequences them on
  one integration branch. Cross-epic dependencies force epic ordering — record them as
  `Depends on: <epic>`.
- **Order by dependency, then user value.** Epic 0 is the foundation; epic 1 should
  produce something demoable.

### Diagrams

Anything the spec describes as a flow, a lifecycle, or a structure of connected parts
gets a picture, not only prose: a reviewer must be able to judge "is this how it should
work?" from the diagram alone, without decoding a page of text. Three are mandatory —
the architecture flowchart (`spec.md`), the data-model erDiagram (`spec.md`), and one
diagram per user flow (each `features/NN-*.md`). Add a `stateDiagram-v2` for any entity
or screen whose lifecycle has more than three states.

Rules:

- Fenced ` ```mermaid ` blocks in the markdown — GitHub and Gitea render them natively,
  so the spec is visual straight from the forge. The mermaid source in the `.md` files
  is the single source of truth for every diagram.
- Label nodes and edges with the spec's `## Terms` vocabulary — a diagram that invents
  its own names for things splits the controlled vocabulary.
- One diagram, one question. A flow diagram answers "what happens, in what order, and
  where can it fail"; it does not also carry the data model. Keep each diagram to one
  screen — split a sprawling flow at its natural handoff rather than shrinking the text.
- The prose next to a diagram states what the picture cannot: constraints, quantities,
  exact field names, error copy. Never let diagram and prose disagree — on a revision
  round, whichever one you edit, update the other.
- No semicolons in sequence-diagram message text — mermaid parses `;` as a statement
  separator and the diagram fails to render. Reword or use a comma.

### Review comments — the in-place feedback loop

Every page `render-spec.py` generates carries a review layer: the reviewer hovers a
heading to attach a comment, or types into the floating Review drawer (its section
target follows the scroll). Comments persist in the browser between sessions, and
**Export** downloads the full set as `review-comments.md`. The round-trip:

1. The reviewer reads the rendered spec, comments in place, exports, and saves the
   file as `docs/specs/review-comments.md`.
2. The reviser reads that file, acts on each `status: open` comment, flips it to
   `status: resolved` in place, and re-renders.
3. The next render embeds every comment under its section, so the reviewer sees each
   one answered in context and can reopen or delete from the page.

File format — one block per comment, body runs to the next `##`:

```markdown
## [<page-id>] <section-slug>
status: open | resolved
date: YYYY-MM-DD
<comment body>
```

`<page-id>` is `index`, `feature-<id>` or `page-<id>`; `<section-slug>` is the
heading's anchor id. Commit `review-comments.md` with the spec — it is the durable
record of the review round, and the parser keeps malformed blocks as page-level
comments rather than dropping them.

### Mockups

Each `mockups/*.html` is **fully self-contained**: inline CSS/JS, no external requests,
no CDN links, responsive, light + dark. Load the **`frontend-design`** skill before
writing them; load **`dataviz`** if any screen charts data. Mock realistic content, not
lorem ipsum. Name DOM regions and sections the way the spec names features.

**Mockups are guidance, not a contract.** They exist to convey the intent of a feature
and roughly what a user will see. An implementer may diverge — framework conventions, a
component library, accessibility, or a better idea all outrank the mockup — as long as
the feature's FRs and acceptance criteria are met. Nothing downstream diffs the built UI
against them, and "doesn't match the mockup" is not by itself a defect. Say this in the
mockup section of `spec.html` so reviewers read them the same way.

Every mockup must be referenced by exactly one feature file, and every mockup a feature
file references must exist. Verify both directions before Phase 3.

**On a revision round, never silently regenerate a mockup.** Check whether the file
changed since you wrote it (git status, or a diff against what the spec describes); a
hand-edited mockup is the user's design decision. Ask before replacing one, and prefer
editing the specific region that changed.
